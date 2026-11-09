"""个人中心领域逻辑：资料维护、密码修改、手机号 / 邮箱绑定、邮箱找回密码。

约定：
- 失败一律抛 `AccountError`（带 HTTP 状态、机器可读 code、可选 retry_after），
  由 `account_api.py` 统一转成 JSON；成功返回 dict。
- 验证码 / 重置 Token 在库里只存 SHA-256，明文只在内存里出现一次（发送时）。
- 所有写操作都以调用方传入的 `user_id`（来自 session）为准，不接受请求体指定用户。
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

import auth
import db
import models
import notify

# --- 规则常量 --------------------------------------------------------------
GENDERS = ("男", "女", "保密")
NICKNAME_MIN, NICKNAME_MAX = 2, 20
BIO_MAX = 200
PASSWORD_MIN, PASSWORD_MAX = 8, 16

CODE_TTL_SECONDS = 600          # 验证码 10 分钟
CODE_RESEND_SECONDS = 60        # 同账号同用途 60 秒冷却
CODE_HOURLY_LIMIT = 5           # 1 小时最多 5 条
CODE_MAX_ATTEMPTS = 5           # 单码最多尝试 5 次

RESET_TTL_MINUTES = 30          # 重置链接 30 分钟有效
RESET_RESEND_SECONDS = 60       # 同邮箱 60 秒冷却
RESET_WINDOW_MINUTES = 15       # 15 分钟内最多 3 次
RESET_WINDOW_LIMIT = 3

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

TS_FMT = "%Y-%m-%d %H:%M:%S"


class AccountError(Exception):
    """业务校验失败。status 用于 HTTP 状态码，extra 会合并进响应体。"""

    def __init__(self, message: str, code: str = "VALIDATION_ERROR",
                 status: int = 400, **extra) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.extra = extra


# --- 小工具 ----------------------------------------------------------------

def _now() -> datetime:
    return datetime.now()


def _fmt(moment: datetime) -> str:
    return moment.strftime(TS_FMT)


def _parse(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime((value or "").strip(), TS_FMT)
    except (TypeError, ValueError):
        return None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _code_hash(purpose: str, target: str, code: str) -> str:
    return _sha256(f"{purpose}:{target}:{code}")


def _conn():
    return models.get_conn()


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


# --- 脱敏 ------------------------------------------------------------------

def mask_phone(phone: Optional[str]) -> str:
    phone = (phone or "").strip()
    if not phone:
        return ""
    if len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-4:]}"


def mask_email(email: Optional[str]) -> str:
    """abc***@gmail.com —— 本地部分保留前 3 位（不足 3 位则全留）。"""
    email = (email or "").strip()
    if "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    head = local if len(local) <= 3 else local[:3]
    return f"{head}***@{domain}"


# --- 校验 ------------------------------------------------------------------

def validate_nickname(raw: str, username: str) -> str:
    """昵称留空时回退为用户名（用户名注册时已限制 2-20 位）。"""
    nickname = (raw or "").strip() or (username or "").strip()
    if len(nickname) < NICKNAME_MIN or len(nickname) > NICKNAME_MAX:
        raise AccountError(f"昵称长度需为 {NICKNAME_MIN}-{NICKNAME_MAX} 个字符")
    return nickname


def validate_gender(raw: str) -> str:
    gender = (raw or "").strip() or "保密"
    if gender not in GENDERS:
        raise AccountError("性别只能是「男」「女」或「保密」")
    return gender


def validate_bio(raw: str) -> str:
    bio = (raw or "").strip()
    if len(bio) > BIO_MAX:
        raise AccountError(f"个人简介最多 {BIO_MAX} 字，当前 {len(bio)} 字")
    return bio


def validate_phone(raw: str) -> str:
    phone = (raw or "").strip().replace(" ", "")
    if not PHONE_RE.match(phone):
        raise AccountError("请输入正确的 11 位手机号")
    return phone


def validate_email(raw: str) -> str:
    email = normalize_email(raw)
    if len(email) > 128 or not EMAIL_RE.match(email):
        raise AccountError("请输入正确的邮箱地址")
    return email


def validate_password(raw: str) -> str:
    """新密码规则：8-16 位、至少含字母和数字。"""
    password = raw or ""
    if len(password) < PASSWORD_MIN or len(password) > PASSWORD_MAX:
        raise AccountError(f"密码长度需为 {PASSWORD_MIN}-{PASSWORD_MAX} 位")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise AccountError("密码需同时包含字母和数字")
    if re.search(r"\s", password):
        raise AccountError("密码不能包含空格")
    return password


# --- 资料读取 --------------------------------------------------------------

def get_user(user_id: int) -> dict:
    row = models.get_user(int(user_id))
    if not row:
        raise AccountError("用户不存在或已被删除", code="USER_NOT_FOUND", status=404)
    return dict(row)


def profile_payload(user_id: int) -> dict:
    row = get_user(user_id)
    nickname = (row.get("nickname") or "").strip() or row["username"]
    phone = (row.get("phone") or "").strip()
    email = normalize_email(row.get("email") or "")
    return {
        "id": row["id"],
        "username": row["username"],
        "nickname": nickname,
        "nickname_editable": (row.get("nickname") or "").strip(),
        "gender": row.get("gender") or "保密",
        "bio": row.get("bio") or "",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
        "security": {
            "phone": mask_phone(phone),
            "phone_bound": bool(phone),
            "phone_verified": bool(row.get("phone_verified")),
            "email": mask_email(email),
            "email_bound": bool(email),
            "email_verified": bool(row.get("email_verified")),
        },
    }


# --- 资料更新 --------------------------------------------------------------

def update_profile(user_id: int, nickname: str, gender: str, bio: str) -> dict:
    row = get_user(user_id)
    clean_nickname = validate_nickname(nickname, row["username"])
    clean_gender = validate_gender(gender)
    clean_bio = validate_bio(bio)
    now = _fmt(_now())

    conn = _conn()
    try:
        conn.execute(
            "UPDATE users SET nickname = ?, gender = ?, bio = ?, updated_at = ? WHERE id = ?",
            (clean_nickname, clean_gender, clean_bio, now, int(user_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return profile_payload(user_id)


# --- 修改密码 --------------------------------------------------------------

def change_password(user_id: int, old_password: str, new_password: str,
                    confirm_password: str) -> dict:
    row = get_user(user_id)
    if not old_password:
        raise AccountError("请输入原密码")
    if not auth.verify_password(row["password_hash"], old_password):
        raise AccountError("原密码不正确", code="INVALID_CREDENTIALS")

    # 「与原密码相同」优先于强度校验：用户重复输入旧密码时给出更准确的提示
    if (new_password or "") == old_password:
        raise AccountError("新密码不能与原密码相同")
    password = validate_password(new_password)
    if password != (confirm_password or ""):
        raise AccountError("两次输入的新密码不一致", code="PASSWORD_MISMATCH")
    now = _fmt(_now())
    conn = _conn()
    try:
        conn.execute(
            "UPDATE users SET password_hash = ?, reset_token = '', "
            "reset_token_expire_at = '', updated_at = ? WHERE id = ?",
            (auth.hash_password(password), now, int(user_id)),
        )
        # 改密后，此前发出的重置链接一律失效
        conn.execute(
            "UPDATE email_tokens SET used = 1 WHERE user_id = ? AND purpose = 'reset_password'",
            (int(user_id),),
        )
        conn.commit()
    finally:
        conn.close()
    return {"relogin": True, "message": "密码修改成功，请重新登录"}


# --- 频率限制 --------------------------------------------------------------

def _check_rate(conn, table: str, where: str, params: tuple,
                cooldown: int, window_minutes: int, window_limit: int) -> None:
    """同一目标（账号 / 邮箱）发送频率控制。table / where 均为内部常量。"""
    now = _now()
    last = conn.execute(
        f"SELECT created_at FROM {table} WHERE {where} ORDER BY id DESC LIMIT 1",
        params,
    ).fetchone()
    if last and last["created_at"]:
        last_at = _parse(last["created_at"])
        if last_at:
            elapsed = (now - last_at).total_seconds()
            if elapsed < cooldown:
                retry = int(cooldown - elapsed) + 1
                raise AccountError(
                    f"操作过于频繁，请 {retry} 秒后再试",
                    code="RATE_LIMITED", status=429, retry_after=retry,
                )
    since = _fmt(now - timedelta(minutes=window_minutes))
    recent = conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE {where} AND created_at >= ?",
        params + (since,),
    ).fetchone()
    if int(recent["n"]) >= window_limit:
        raise AccountError(
            f"请求过于频繁，请 {window_minutes} 分钟后再试",
            code="RATE_LIMITED", status=429, retry_after=window_minutes * 60,
        )


def _purge_old(conn, table: str, user_id: int) -> None:
    cutoff = _fmt(_now() - timedelta(days=7))
    conn.execute(f"DELETE FROM {table} WHERE user_id = ? AND created_at < ?",
                 (int(user_id), cutoff))


# --- 手机号绑定 ------------------------------------------------------------

def send_phone_code(user_id: int, phone: str) -> dict:
    phone = validate_phone(phone)
    row = get_user(user_id)
    if (row.get("phone") or "") == phone and row.get("phone_verified"):
        raise AccountError("该手机号已绑定当前账号", code="PHONE_ALREADY_BOUND", status=409)

    conn = _conn()
    try:
        taken = conn.execute(
            "SELECT id FROM users WHERE phone = ? AND id <> ?", (phone, int(user_id))
        ).fetchone()
        if taken:
            raise AccountError("该手机号已被其他账号绑定", code="PHONE_TAKEN", status=409)

        _check_rate(conn, "sms_codes", "user_id = ? AND purpose = 'bind_phone'",
                    (int(user_id),), CODE_RESEND_SECONDS, 60, CODE_HOURLY_LIMIT)

        code = f"{secrets.randbelow(1000000):06d}"
        now = _now()
        conn.execute("UPDATE sms_codes SET used = 1 WHERE user_id = ? AND purpose = 'bind_phone'",
                     (int(user_id),))
        conn.execute(
            "INSERT INTO sms_codes (user_id, phone, purpose, code_hash, expires_at, "
            "attempts, used, created_at) VALUES (?, ?, 'bind_phone', ?, ?, 0, 0, ?)",
            (int(user_id), phone, _code_hash("bind_phone", phone, code),
             _fmt(now + timedelta(seconds=CODE_TTL_SECONDS)), _fmt(now)),
        )
        _purge_old(conn, "sms_codes", user_id)
        conn.commit()
    finally:
        conn.close()

    result = notify.send_sms(
        phone, f"【认证题库网】验证码 {code}，{CODE_TTL_SECONDS // 60} 分钟内有效，请勿泄露。"
    )
    if not result["ok"]:
        _invalidate_phone_codes(user_id)
        raise AccountError(result["message"], code="SMS_SEND_FAILED", status=502)

    payload = {
        "phone": mask_phone(phone),
        "cooldown": CODE_RESEND_SECONDS,
        "expires_in": CODE_TTL_SECONDS,
        "message": "验证码已发送，请注意查收",
    }
    if result["dev"] and notify.dev_mode():
        payload["debug_code"] = code
        payload["dev_mode"] = True
    return payload


def _invalidate_phone_codes(user_id: int) -> None:
    conn = _conn()
    try:
        conn.execute("UPDATE sms_codes SET used = 1 WHERE user_id = ? AND purpose = 'bind_phone'",
                     (int(user_id),))
        conn.commit()
    finally:
        conn.close()


def verify_phone(user_id: int, phone: str, code: str) -> dict:
    phone = validate_phone(phone)
    code = (code or "").strip()
    if not code:
        raise AccountError("请输入短信验证码")

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM sms_codes WHERE user_id = ? AND purpose = 'bind_phone' "
            "AND phone = ? AND used = 0 ORDER BY id DESC LIMIT 1",
            (int(user_id), phone),
        ).fetchone()
        _check_code(conn, "sms_codes", row, code, "短信验证码")

        taken = conn.execute(
            "SELECT id FROM users WHERE phone = ? AND id <> ?", (phone, int(user_id))
        ).fetchone()
        if taken:
            raise AccountError("该手机号已被其他账号绑定", code="PHONE_TAKEN", status=409)

        now = _fmt(_now())
        conn.execute("UPDATE sms_codes SET used = 1 WHERE id = ?", (row["id"],))
        try:
            conn.execute(
                "UPDATE users SET phone = ?, phone_verified = 1, updated_at = ? WHERE id = ?",
                (phone, now, int(user_id)),
            )
            conn.commit()
        except db.IntegrityError:
            conn.rollback()
            raise AccountError("该手机号已被其他账号绑定", code="PHONE_TAKEN", status=409)
    finally:
        conn.close()
    return {"phone": mask_phone(phone), "phone_verified": True, "message": "手机号绑定成功"}


def _check_code(conn, table: str, row, code: str, label: str) -> None:
    """校验验证码行：过期、错误次数、哈希比对。

    `table` 只接受本模块常量（sms_codes / email_tokens），不接收外部输入；
    校验失败会累加 attempts 并提交，成功则交给调用方在写业务数据时一并置 used。
    """
    if not row:
        raise AccountError(f"请先获取{label}", code="CODE_MISSING")
    if int(row["used"]):
        raise AccountError(f"{label}已使用，请重新获取", code="CODE_EXPIRED")
    expires = _parse(row["expires_at"])
    if not expires or expires < _now():
        raise AccountError(f"{label}已过期，请重新获取", code="CODE_EXPIRED")

    if int(row["attempts"]) >= CODE_MAX_ATTEMPTS:
        conn.execute(f"UPDATE {table} SET used = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        raise AccountError(f"{label}错误次数过多，请重新获取", code="CODE_EXPIRED")

    target = row["phone"] if "phone" in row.keys() else row["email"]
    if not secrets.compare_digest(row["code_hash"], _code_hash(row["purpose"], target, code)):
        conn.execute(f"UPDATE {table} SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        conn.commit()
        left = CODE_MAX_ATTEMPTS - int(row["attempts"]) - 1
        raise AccountError(f"{label}不正确，还可尝试 {max(left, 0)} 次", code="CODE_INVALID")


# --- 邮箱绑定 --------------------------------------------------------------

def send_email_code(user_id: int, email: str) -> dict:
    email = validate_email(email)
    row = get_user(user_id)
    if normalize_email(row.get("email") or "") == email and row.get("email_verified"):
        raise AccountError("该邮箱已绑定当前账号", code="EMAIL_ALREADY_BOUND", status=409)

    conn = _conn()
    try:
        taken = conn.execute(
            "SELECT id FROM users WHERE LOWER(email) = ? AND id <> ?", (email, int(user_id))
        ).fetchone()
        if taken:
            raise AccountError("该邮箱已被其他账号绑定", code="EMAIL_TAKEN", status=409)

        _check_rate(conn, "email_tokens", "user_id = ? AND purpose = 'bind_email'",
                    (int(user_id),), CODE_RESEND_SECONDS, 60, CODE_HOURLY_LIMIT)

        code = f"{secrets.randbelow(1000000):06d}"
        now = _now()
        conn.execute(
            "UPDATE email_tokens SET used = 1 WHERE user_id = ? AND purpose = 'bind_email'",
            (int(user_id),),
        )
        conn.execute(
            "INSERT INTO email_tokens (user_id, email, purpose, code_hash, token_hash, "
            "expires_at, attempts, used, created_at) VALUES (?, ?, 'bind_email', ?, '', ?, 0, 0, ?)",
            (int(user_id), email, _code_hash("bind_email", email, code),
             _fmt(now + timedelta(seconds=CODE_TTL_SECONDS)), _fmt(now)),
        )
        _purge_old(conn, "email_tokens", user_id)
        conn.commit()
    finally:
        conn.close()

    result = notify.send_mail(
        email,
        "【认证题库网】绑定邮箱验证码",
        f"您的邮箱验证码是 {code}，{CODE_TTL_SECONDS // 60} 分钟内有效。\n"
        f"如非本人操作，请忽略本邮件。",
    )
    if not result["ok"]:
        _invalidate_email_codes(user_id)
        raise AccountError(result["message"], code="MAIL_SEND_FAILED", status=502)

    payload = {
        "email": mask_email(email),
        "cooldown": CODE_RESEND_SECONDS,
        "expires_in": CODE_TTL_SECONDS,
        "message": "验证码已发送至邮箱，请注意查收",
    }
    if result["dev"] and notify.dev_mode():
        payload["debug_code"] = code
        payload["dev_mode"] = True
    return payload


def _invalidate_email_codes(user_id: int) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE email_tokens SET used = 1 WHERE user_id = ? AND purpose = 'bind_email'",
            (int(user_id),),
        )
        conn.commit()
    finally:
        conn.close()


def verify_email(user_id: int, email: str, code: str) -> dict:
    email = validate_email(email)
    code = (code or "").strip()
    if not code:
        raise AccountError("请输入邮箱验证码")

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM email_tokens WHERE user_id = ? AND purpose = 'bind_email' "
            "AND email = ? AND used = 0 ORDER BY id DESC LIMIT 1",
            (int(user_id), email),
        ).fetchone()
        _check_code(conn, "email_tokens", row, code, "邮箱验证码")

        taken = conn.execute(
            "SELECT id FROM users WHERE LOWER(email) = ? AND id <> ?", (email, int(user_id))
        ).fetchone()
        if taken:
            raise AccountError("该邮箱已被其他账号绑定", code="EMAIL_TAKEN", status=409)

        now = _fmt(_now())
        conn.execute("UPDATE email_tokens SET used = 1 WHERE id = ?", (row["id"],))
        try:
            conn.execute(
                "UPDATE users SET email = ?, email_verified = 1, updated_at = ? WHERE id = ?",
                (email, now, int(user_id)),
            )
            conn.commit()
        except db.IntegrityError:
            conn.rollback()
            raise AccountError("该邮箱已被其他账号绑定", code="EMAIL_TAKEN", status=409)
    finally:
        conn.close()
    return {"email": mask_email(email), "email_verified": True, "message": "邮箱绑定成功"}


# --- 邮箱找回密码 ----------------------------------------------------------

def request_password_reset(email: str, base_url: str) -> dict:
    email = validate_email(email)

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(email) = ? ORDER BY id LIMIT 1", (email,)
        ).fetchone()
        # 账号枚举防护：邮箱不存在 / 未验证时**也返回同样的成功文案**，不泄露
        # 「这个邮箱有没有注册过」。区别只体现在「不发信」，攻击者无法从响应判断。
        if not row or not int(row["email_verified"] or 0):
            return {
                "sent": True,
                "email": mask_email(email),
                "expires_in": RESET_TTL_MINUTES * 60,
                "message": (
                    f"如果 {mask_email(email)} 已注册并完成验证，"
                    f"重置邮件将在 {RESET_TTL_MINUTES} 分钟内送达"
                ),
            }

        _check_rate(conn, "email_tokens", "email = ? AND purpose = 'reset_password'",
                    (email,), RESET_RESEND_SECONDS, RESET_WINDOW_MINUTES, RESET_WINDOW_LIMIT)

        token = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(minutes=RESET_TTL_MINUTES)
        conn.execute(
            "UPDATE users SET reset_token = ?, reset_token_expire_at = ? WHERE id = ?",
            (_sha256(token), _fmt(expires), row["id"]),
        )
        conn.execute(
            "INSERT INTO email_tokens (user_id, email, purpose, code_hash, token_hash, "
            "expires_at, attempts, used, created_at) "
            "VALUES (?, ?, 'reset_password', '', ?, ?, 0, 0, ?)",
            (row["id"], email, _sha256(token), _fmt(expires), _fmt(now)),
        )
        _purge_old(conn, "email_tokens", row["id"])
        conn.commit()
    finally:
        conn.close()

    link = f"{base_url.rstrip('/')}/reset-password?token={token}"
    result = notify.send_mail(
        email,
        "【认证题库网】重置登录密码",
        f"我们收到了重置密码的请求，请在 {RESET_TTL_MINUTES} 分钟内打开以下链接设置新密码：\n\n"
        f"{link}\n\n"
        f"链接仅可使用一次。如非本人操作，请忽略本邮件，您的密码不会被修改。",
    )
    if not result["ok"]:
        _clear_reset_token(row["id"])
        raise AccountError(result["message"], code="MAIL_SEND_FAILED", status=502)

    payload = {
        "sent": True,
        "email": mask_email(email),
        "expires_in": RESET_TTL_MINUTES * 60,
        "message": f"重置邮件已发送至 {mask_email(email)}，{RESET_TTL_MINUTES} 分钟内有效",
    }
    if result["dev"] and notify.dev_mode():
        payload["debug_link"] = link
        payload["dev_mode"] = True
    return payload


def _clear_reset_token(user_id: int) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE users SET reset_token = '', reset_token_expire_at = '' WHERE id = ?",
            (int(user_id),),
        )
        conn.commit()
    finally:
        conn.close()


def inspect_reset_token(token: str) -> dict:
    """重置页首屏用：只判断链接是否可用，不改数据。"""
    token = (token or "").strip()
    if not token:
        return {"valid": False, "reason": "缺少重置凭证"}
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT email, reset_token_expire_at FROM users WHERE reset_token = ? LIMIT 1",
            (_sha256(token),),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"valid": False, "reason": "链接无效或已被使用"}
    expires = _parse(row["reset_token_expire_at"])
    if not expires or expires < _now():
        return {"valid": False, "reason": "链接已过期"}
    return {"valid": True, "email": mask_email(row["email"]), "expires_at": row["reset_token_expire_at"]}


def reset_password(token: str, password: str, confirm_password: str) -> dict:
    token = (token or "").strip()
    if not token:
        raise AccountError("重置链接缺少凭证，请重新申请", code="TOKEN_INVALID")

    token_hash = _sha256(token)
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE reset_token = ? LIMIT 1", (token_hash,)
        ).fetchone()
        if not row:
            raise AccountError("重置链接无效或已被使用，请重新申请", code="TOKEN_INVALID")

        expires = _parse(row["reset_token_expire_at"])
        if not expires or expires < _now():
            conn.execute(
                "UPDATE users SET reset_token = '', reset_token_expire_at = '' WHERE id = ?",
                (row["id"],),
            )
            conn.commit()
            raise AccountError("重置链接已过期，请重新申请", code="TOKEN_INVALID")

        new_password = validate_password(password)
        if new_password != (confirm_password or ""):
            raise AccountError("两次输入的新密码不一致", code="PASSWORD_MISMATCH")
        if auth.verify_password(row["password_hash"], new_password):
            raise AccountError("新密码不能与原密码相同")

        now = _fmt(_now())
        conn.execute(
            "UPDATE users SET password_hash = ?, reset_token = '', reset_token_expire_at = '', "
            "updated_at = ? WHERE id = ?",
            (auth.hash_password(new_password), now, row["id"]),
        )
        conn.execute(
            "UPDATE email_tokens SET used = 1 WHERE user_id = ? AND purpose = 'reset_password'",
            (row["id"],),
        )
        conn.commit()
    finally:
        conn.close()
    return {"reset": True, "message": "密码重置成功，请使用新密码登录"}
