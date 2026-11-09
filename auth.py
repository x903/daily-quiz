"""认证模块：普通用户与管理员两套账号体系彻底隔离。

- 普通用户：users 表，入口 /login，session 键 user_id。
- 管理员：独立 admin 表，入口 /admin/login，session 键 admin_id。
- 两侧互不查对方的表：用管理员账号永远登不进用户端，反之亦然。

密码统一用 werkzeug pbkdf2:sha256 加盐哈希；admin.password 列的列名对齐
需求（id / username / password），但存的是哈希而非明文。

本模块只依赖 models，不依赖 Flask，方便脚本与测试复用。
"""
from __future__ import annotations

import os
import re
import secrets
from math import ceil
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

import captcha
import models
import runtime

# 默认账号（首次启动、对应表为空时创建）。
# 管理员密码可用环境变量覆盖，避免把真实密码写死在代码/镜像里。
# 生产环境（Docker/MySQL）在未配置 ADMIN_PASSWORD 时不再使用演示密码，而是随机生成
# 并打印一次到服务日志 —— 公网上的 admin/admin123 等同于没有密码。
DEFAULT_ADMIN_USERNAME = (os.environ.get("ADMIN_USERNAME") or "admin").strip() or "admin"
DEMO_ADMIN_PASSWORD = "admin123"
DEMO_USER_PASSWORD = "user123"
DEFAULT_USER = ("user", DEMO_USER_PASSWORD)


def _initial_admin_password() -> str:
    configured = os.environ.get("ADMIN_PASSWORD") or ""
    if configured:
        return configured
    if runtime.is_production():
        return secrets.token_urlsafe(12)
    return DEMO_ADMIN_PASSWORD


DEFAULT_ADMIN = (DEFAULT_ADMIN_USERNAME, _initial_admin_password())

# ---------------------------------------------------------------------------
# 用户名规则：注册 / 实时校验 / 登录归一化共用同一套，避免三处判断打架
# ---------------------------------------------------------------------------
USERNAME_MIN = 4
USERNAME_MAX = 20
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.]+$")
USERNAME_TAKEN_MESSAGE = "该用户名已被注册，请更换"
# 系统保留词：避免冒充官方，也避免与后台账号混淆
RESERVED_USERNAMES = frozenset({
    "admin", "administrator", "root", "system", "manager", "sysadmin",
    "superadmin", "superuser", "operator", "official", "staff", "support",
    "service", "helper", "moderator", "webmaster", "guest", "anonymous",
    "null", "undefined", "test", "demo", "api", "www", "mail", "postmaster",
})


class AccountValidationError(ValueError):
    """账号领域的可预期错误：带业务码与 HTTP 状态，接口层直接映射。"""

    def __init__(self, message: str, code: str = "INVALID", status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class LoginError(AccountValidationError):
    """登录失败：附带剩余尝试次数 / 锁定剩余秒数，便于前端提示。"""

    def __init__(self, message: str, code: str, status: int = 401,
                 attempts_left: Optional[int] = None, locked_seconds: int = 0):
        super().__init__(message, code=code, status=status)
        self.attempts_left = attempts_left
        self.locked_seconds = locked_seconds


def normalize_username(raw: str) -> str:
    """统一小写 + 去首尾空格，避免 User / user、' user ' 变成多个账号。"""
    return (raw or "").strip().lower()


def validate_username(raw: str) -> str:
    """校验用户名并返回归一化结果；不合法时抛 AccountValidationError。"""
    text = (raw or "").strip()
    if not text:
        raise AccountValidationError("请输入用户名", code="USERNAME_REQUIRED")
    if len(text) < USERNAME_MIN or len(text) > USERNAME_MAX:
        raise AccountValidationError(
            f"用户名长度需为 {USERNAME_MIN}-{USERNAME_MAX} 位", code="USERNAME_LENGTH"
        )
    if not USERNAME_PATTERN.match(text):
        raise AccountValidationError(
            "用户名只能包含字母、数字、下划线或点号", code="USERNAME_CHARS"
        )
    if text.isdigit():
        raise AccountValidationError("用户名不能为纯数字", code="USERNAME_ALL_DIGITS")
    if text.startswith(".") or text.endswith(".") or ".." in text:
        raise AccountValidationError(
            "用户名格式不正确，点号不能出现在首尾或连续出现", code="USERNAME_FORMAT"
        )
    name = text.lower()
    if name in RESERVED_USERNAMES:
        raise AccountValidationError("该用户名属于系统保留词，请更换", code="USERNAME_RESERVED")
    return name


def username_available(raw: str) -> dict:
    """实时校验用：返回 {available, username, code, message}，不抛异常。"""
    try:
        name = validate_username(raw)
    except AccountValidationError as exc:
        return {
            "available": False,
            "username": normalize_username(raw),
            "code": exc.code,
            "message": exc.message,
        }
    if models.username_taken(name):
        return {
            "available": False,
            "username": name,
            "code": "USERNAME_TAKEN",
            "message": USERNAME_TAKEN_MESSAGE,
        }
    return {"available": True, "username": name, "code": "OK", "message": "该用户名可以使用"}


def hash_password(password: str) -> str:
    """返回加盐后的密码哈希。"""
    return generate_password_hash(password, method="pbkdf2:sha256")


def verify_password(password_hash: str, password: str) -> bool:
    """校验明文密码与哈希是否匹配。"""
    return check_password_hash(password_hash, password)


def register_user(username: str, password: str, *, email: str = "", phone: str = "") -> dict:
    """注册普通用户（严格规则版）：校验 → 查重 → 落库，失败抛 AccountValidationError。

    - 用户名统一小写后入库，数据库唯一索引兜底并发；
    - email / phone 可选，给了就做格式与唯一性校验，落库后标记为「未验证」；
    - role 恒为 user，杜绝从注册口提权。
    """
    name = validate_username(username)
    password = password or ""
    if len(password) < 6:
        raise AccountValidationError("密码长度至少 6 位", code="PASSWORD_TOO_SHORT")
    if len(password) > 64:
        raise AccountValidationError("密码长度不能超过 64 位", code="PASSWORD_TOO_LONG")

    if models.username_taken(name):
        raise AccountValidationError(USERNAME_TAKEN_MESSAGE, code="USERNAME_TAKEN", status=409)

    email_value = None
    phone_value = None
    if (email or "").strip() or (phone or "").strip():
        import account as account_mod  # 延迟导入：account 依赖 auth，避免循环导入

        if (email or "").strip():
            email_value = account_mod.validate_email(email)
            if models.email_taken(email_value):
                raise AccountValidationError(
                    "该邮箱已被其他账号使用", code="EMAIL_TAKEN", status=409
                )
        if (phone or "").strip():
            phone_value = account_mod.validate_phone(phone)
            if models.phone_taken(phone_value):
                raise AccountValidationError(
                    "该手机号已被其他账号使用", code="PHONE_TAKEN", status=409
                )

    try:
        user_id = models.create_user(
            name, hash_password(password), "user", email=email_value, phone=phone_value
        )
    except Exception as exc:  # 并发注册时唯一索引会先于预查询命中
        if _is_unique_violation(exc):
            raise AccountValidationError(
                USERNAME_TAKEN_MESSAGE, code="USERNAME_TAKEN", status=409
            ) from exc
        raise

    return {
        "id": user_id,
        "username": name,
        "email": email_value or "",
        "phone": phone_value or "",
    }


def _is_unique_violation(exc: Exception) -> bool:
    """判断是否为唯一索引冲突（SQLite / MySQL 文案不同）。"""
    text = str(exc).lower()
    return isinstance(exc, models.db.IntegrityError) or "unique" in text or "duplicate" in text


def register(username: str, password: str, role: str = "user") -> tuple[bool, str]:
    """兼容旧调用：返回 (是否成功, 提示文案)。role 参数保留但一律忽略。"""
    try:
        register_user(username, password)
    except AccountValidationError as exc:
        return False, exc.message
    return True, "注册成功"


def register_admin(username: str, password: str) -> tuple[bool, str]:
    """创建后台管理员（只允许由已有管理员通过管理接口调用）。

    密码规则与自助改密保持一致（8-64 位、字母 + 数字），避免新建账号的
    初始口令比改密后的口令更宽松。
    """
    username = (username or "").strip()
    if not username:
        return False, "用户名不能为空"
    if len(username) < 2 or len(username) > 20:
        return False, "用户名长度需为 2-20 个字符"
    try:
        password = validate_admin_password(password)
    except AccountValidationError as exc:
        return False, exc.message
    if models.get_admin_by_username(username) or models.get_user_by_username(username):
        return False, "该用户名已被占用"
    models.create_admin(username, hash_password(password))
    return True, "管理员创建成功"


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """用户端登录校验：只查 users 表，且排除任何残留的 admin 角色行。

    旧库升级时，若管理员账号已有答题数据，users 中的历史行会被保留并降权为
    user（避免外键数据丢失）。这里额外拦截「与 admins 表同名」的账号，保证
    管理员凭据在用户端永远无法登录，两套入口彻底隔离。
    """
    username = normalize_username(username)
    if not username:
        return None
    if models.get_admin_by_username(username):
        return None
    user = models.get_user_by_username(username)
    if user and user["role"] != "admin" and verify_password(user["password_hash"], password):
        return dict(user)
    return None


def login_user(username: str, password: str, captcha_key: str, captcha_code: str) -> dict:
    """用户端登录（带图形验证码与失败锁定），返回用户 dict，失败抛 LoginError。

    校验顺序：必填 → 账号锁定 → 验证码（存在/过期/正确）→ 账号密码。
    验证码先于密码校验，是为了让「验证码错误」不泄露账号是否存在。
    """
    raw_username = (username or "").strip()
    password = password or ""
    if not raw_username or not password:
        raise LoginError("请输入用户名和密码", code="CREDENTIALS_REQUIRED", status=400)

    name = normalize_username(raw_username)
    locked = captcha.lock_remaining(name)
    if locked:
        raise LoginError(
            f"登录失败次数过多，请 {max(1, ceil(locked / 60))} 分钟后再试",
            code="ACCOUNT_LOCKED", status=423, locked_seconds=locked,
        )

    ok, reason = captcha.verify(captcha_key, captcha_code)
    if not ok:
        raise LoginError(
            "验证码错误或已过期" if reason != "empty" else "请输入验证码",
            code="CAPTCHA_INVALID", status=400,
        )

    user = authenticate_user(name, password)
    if not user:
        count, locked_seconds = captcha.register_failure(name)
        if locked_seconds:
            raise LoginError(
                f"登录失败次数过多，请 {max(1, ceil(locked_seconds / 60))} 分钟后再试",
                code="ACCOUNT_LOCKED", status=423, locked_seconds=locked_seconds,
            )
        left = max(0, captcha.LOGIN_MAX_FAILURES - count)
        raise LoginError(
            "用户名或密码错误", code="BAD_CREDENTIALS", status=401, attempts_left=left
        )

    captcha.clear_failures(name)
    return user


def authenticate_admin(username: str, password: str) -> Optional[dict]:
    """管理端登录校验：只查 admin 表，停用账号不允许登录。"""
    row = models.get_admin_by_username((username or "").strip())
    if row and row["status"] == "active" and verify_password(row["password"], password):
        return dict(row)
    return None


# ---------------------------------------------------------------------------
# 管理员登录（图形验证码 + 失败锁定）与改密
# ---------------------------------------------------------------------------
ADMIN_PASSWORD_MIN = 8
ADMIN_PASSWORD_MAX = 64


def validate_admin_password(raw: str) -> str:
    """管理员密码强度：8-64 位、字母 + 数字、不含空格。

    强制字母与数字组合，是因为管理员账号能改题库、看全站数据，弱口令风险远高于
    普通用户；同时保留 64 位上限，避免超长输入拖慢 PBKDF2 校验。
    """
    password = raw or ""
    if len(password) < ADMIN_PASSWORD_MIN or len(password) > ADMIN_PASSWORD_MAX:
        raise AccountValidationError(
            f"密码长度需为 {ADMIN_PASSWORD_MIN}-{ADMIN_PASSWORD_MAX} 位", code="PASSWORD_LENGTH"
        )
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise AccountValidationError("密码需同时包含字母和数字", code="PASSWORD_WEAK")
    if re.search(r"\s", password):
        raise AccountValidationError("密码不能包含空格", code="PASSWORD_SPACE")
    return password


def login_admin(username: str, password: str, captcha_key: str, captcha_code: str) -> dict:
    """管理端登录：与用户端同构的校验顺序，但验证码 / 失败计数走独立作用域。

    顺序：必填 → 账号锁定 → 验证码（存在/过期/正确）→ 账号密码。
    验证码先于密码，保证「验证码错误」不会泄露管理员账号是否存在；
    管理端与用户端的 captcha / fail / lock 键完全隔离，互不牵连。
    """
    raw_username = (username or "").strip()
    password = password or ""
    if not raw_username or not password:
        raise LoginError("请输入管理员账号和密码", code="CREDENTIALS_REQUIRED", status=400)

    lock_name = raw_username.lower()
    locked = captcha.lock_remaining(lock_name, scope="admin")
    if locked:
        raise LoginError(
            f"登录失败次数过多，请 {max(1, ceil(locked / 60))} 分钟后再试",
            code="ACCOUNT_LOCKED", status=423, locked_seconds=locked,
        )

    ok, reason = captcha.verify(captcha_key, captcha_code, scope="admin")
    if not ok:
        raise LoginError(
            "验证码错误或已过期" if reason != "empty" else "请输入验证码",
            code="CAPTCHA_INVALID", status=400,
        )

    admin = authenticate_admin(raw_username, password)
    if not admin:
        count, locked_seconds = captcha.register_failure(lock_name, scope="admin")
        if locked_seconds:
            raise LoginError(
                f"登录失败次数过多，请 {max(1, ceil(locked_seconds / 60))} 分钟后再试",
                code="ACCOUNT_LOCKED", status=423, locked_seconds=locked_seconds,
            )
        left = max(0, captcha.LOGIN_MAX_FAILURES - count)
        raise LoginError(
            "管理员账号或密码错误", code="BAD_CREDENTIALS", status=401, attempts_left=left
        )

    captcha.clear_failures(lock_name, scope="admin")
    return admin


def change_admin_password(admin_id: int, old_password: str, new_password: str,
                          confirm_password: str) -> dict:
    """管理员自助改密：校验原密码 → 强度 → 落库，成功后要求重新登录。

    原密码连续输错 5 次会锁 15 分钟（独立于登录锁定），防止会话被劫持后
    被用来反复试探原密码。
    """
    row = models.get_admin(int(admin_id))
    if not row:
        raise AccountValidationError("管理员账号不存在", code="ADMIN_NOT_FOUND", status=404)

    gate = f"admin:{int(admin_id)}"
    locked = captcha.lock_remaining(gate, scope="adminpwd")
    if locked:
        raise LoginError(
            f"原密码错误次数过多，请 {max(1, ceil(locked / 60))} 分钟后再试",
            code="ACCOUNT_LOCKED", status=423, locked_seconds=locked,
        )

    if not old_password:
        raise AccountValidationError("请输入原密码", code="OLD_PASSWORD_REQUIRED")
    if not verify_password(row["password"], old_password):
        count, locked_seconds = captcha.register_failure(gate, scope="adminpwd")
        if locked_seconds:
            raise LoginError(
                f"原密码错误次数过多，请 {max(1, ceil(locked_seconds / 60))} 分钟后再试",
                code="ACCOUNT_LOCKED", status=423, locked_seconds=locked_seconds,
            )
        left = max(0, captcha.LOGIN_MAX_FAILURES - count)
        # 用 400 而不是 401：管理端前端把 401 当作「会话失效」并跳登录页，
        # 这里的原密码错误属于表单校验失败，不能再触发一次强制登出。
        raise AccountValidationError(
            f"原密码不正确，还可尝试 {left} 次" if left else "原密码不正确",
            code="INVALID_CREDENTIALS", status=400,
        )

    if (new_password or "") == old_password:
        raise AccountValidationError("新密码不能与原密码相同", code="PASSWORD_SAME")
    password = validate_admin_password(new_password)
    if password != (confirm_password or ""):
        raise AccountValidationError("两次输入的新密码不一致", code="PASSWORD_MISMATCH")

    models.update_admin_password(int(admin_id), hash_password(password))
    captcha.clear_failures(gate, scope="adminpwd")
    # 改密后解除登录锁定，避免刚改完密码却因旧失败次数被挡在门外
    captcha.clear_failures(str(row["username"]).lower(), scope="admin")
    return {"relogin": True, "message": "密码已修改，请使用新密码重新登录"}


def reset_admin_password(operator: dict, target_id: int, new_password: str,
                         confirm_password: str, operator_password: str) -> dict:
    """由已登录管理员重置其他管理员的密码（需再次确认操作者本人的密码）。

    用于「另一位管理员忘记密码」的兜底；不允许用它改自己，避免绕过原密码校验。
    被重置账号的既有管理端会话会因为会话指纹不匹配而自动失效。
    """
    target = models.get_admin(int(target_id))
    if not target:
        raise AccountValidationError("管理员账号不存在", code="ADMIN_NOT_FOUND", status=404)
    if int(target["id"]) == int(operator["id"]):
        raise AccountValidationError(
            "修改自己的密码请使用「修改密码」", code="SELF_RESET_FORBIDDEN"
        )

    if not operator_password:
        raise AccountValidationError("请输入你的密码以确认操作", code="OPERATOR_PASSWORD_REQUIRED")
    current = models.get_admin(int(operator["id"]))
    if not current or not verify_password(current["password"], operator_password):
        raise LoginError("你的密码不正确，无法重置他人密码", code="INVALID_CREDENTIALS", status=403)

    password = validate_admin_password(new_password)
    if password != (confirm_password or ""):
        raise AccountValidationError("两次输入的新密码不一致", code="PASSWORD_MISMATCH")

    models.update_admin_password(int(target["id"]), hash_password(password))
    # 新密码立即可用，不继承目标账号之前的失败计数
    captcha.clear_failures(str(target["username"]).lower(), scope="admin")
    return {"message": f"已重置「{target['username']}」的密码，该账号需用新密码重新登录"}


def create_default_users() -> None:
    """分别播种默认普通用户与默认管理员（两张表各自独立判空）。

    生产环境下密码一律随机生成并**只打印一次**到服务日志：需要运维立即记录并改密。
    本地开发仍使用 user/user123、admin/admin123，方便调试与回归测试。
    """
    production = runtime.is_production()
    created: list[str] = []

    if models.count_users() == 0:
        name = DEFAULT_USER[0]
        password = secrets.token_urlsafe(12) if production else DEFAULT_USER[1]
        models.create_user(name, hash_password(password), "user")
        created.append(f"普通用户 {name}/{password}")

    if models.count_admins() == 0:
        name, _pw = DEFAULT_ADMIN
        password = _pw
        models.create_admin(name, hash_password(password))
        created.append(f"管理员 {name}/{password}")

    if not created:
        return
    if production:
        print(
            "[auth] 已创建默认账号（初始密码仅打印这一次，请立即记录并登录后台修改）：\n"
            + "\n".join(f"  - {item}" for item in created)
            + "\n[auth] 管理员入口 /admin/login\n",
            flush=True,
        )
    else:
        print("[auth] 已创建默认账号 -> " + "，".join(created))


def harden_default_accounts() -> None:
    """生产环境启动兜底：把仍在使用演示密码的账号改成随机强密码。

    旧库、示例数据、以及「先本地跑通再直接上线」的部署，很容易把
    admin/admin123 带到公网。这里在每次启动时检测一遍，命中就换掉并打印新密码，
    避免「部署完忘记改密」变成后台被接管。

    只在生产环境执行；本地 SQLite 调试与回归测试不受影响。
    """
    if not runtime.is_production():
        return

    targets = []
    conn = models.get_conn()
    try:
        admins = conn.execute("SELECT id, username, password FROM admins").fetchall()
        for row in admins:
            if check_password_hash(row["password"], DEMO_ADMIN_PASSWORD):
                targets.append(("管理员", int(row["id"]), row["username"], "admins"))
        users = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE role = 'user'"
        ).fetchall()
        for row in users:
            if check_password_hash(row["password_hash"], DEMO_USER_PASSWORD):
                targets.append(("用户", int(row["id"]), row["username"], "users"))
    finally:
        conn.close()

    if not targets:
        return

    lines = []
    for kind, row_id, username, table in targets:
        if table == "admins":
            # 优先使用 .env 里已声明的 ADMIN_PASSWORD，方便运维「设一次、处处一致」；
            # 没设置才随机生成并打印到日志。
            password = (os.environ.get("ADMIN_PASSWORD") or "").strip() or secrets.token_urlsafe(12)
            models.update_admin_password(row_id, hash_password(password))
        else:
            password = secrets.token_urlsafe(12)
            models.update_user_password(row_id, hash_password(password))
        lines.append(f"  - {kind} {username} 的新密码：{password}")
    print(
        "[security] 检测到演示密码（admin123 / user123），已自动替换为随机密码：\n"
        + "\n".join(lines)
        + "\n[security] 该信息只在本次启动日志中输出，请立即保存并登录后修改。\n",
        flush=True,
    )
