"""登录图形验证码 + 登录限流 / 账号临时锁定。

设计要点
--------
- 验证码是纯 SVG（不依赖 Pillow），字符随机旋转、随机颜色，叠加干扰线与噪点。
- 明文只存服务端：Redis 键 `login:captcha:{key}`，TTL 300 秒；读取即删除，
  所以同一验证码无论校验成功还是失败都只能用一次。
- 存储优先 Redis（多实例部署校验一致），未配置 `REDIS_URL` 或连接失败时
  降级到进程内内存，保证单机 / 本地开发也能跑。
- 同一 IP 5 分钟内最多取 5 张验证码；账号连续失败 5 次锁 15 分钟。
- 字符池剔除 0/O、1/I/l 等易混字符，校验时统一转大写并忽略首尾空格。

作用域（scope）
--------------
用户端与管理端共用本模块，但键必须隔离：否则一张用户端验证码可以拿去登管理端，
用户端的失败计数也会把管理员一起锁住。默认 scope="user" 保持原有关键字不变：

    scope="user"       login:captcha:{key} / login:fail:{name} / ...
    scope="admin"      admin:login:captcha:{key} / admin:login:fail:{name} / ...
    scope="adminpwd"   adminpwd:login:fail:{name}（改密时校验原密码的失败计数）
"""
from __future__ import annotations

import base64
import os
import secrets
import threading
import time
from typing import Optional, Tuple

import db

# .env 可能由 db 模块在导入时加载；这里显式触发一次，保证 REDIS_URL 等配置已就位
db._load_dotenv()

CAPTCHA_TTL_SECONDS = int(os.environ.get("CAPTCHA_TTL_SECONDS", "300"))
CAPTCHA_LENGTH = 4                 # 4-6 位，取 4 位兼顾识别率
CAPTCHA_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉 I O 0 1 l
CAPTCHA_RATE_LIMIT = int(os.environ.get("CAPTCHA_RATE_LIMIT", "5"))
CAPTCHA_RATE_WINDOW = int(os.environ.get("CAPTCHA_RATE_WINDOW", "300"))
LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCK_SECONDS = int(os.environ.get("LOGIN_LOCK_SECONDS", "900"))

REDIS_URL = os.environ.get("REDIS_URL", "").strip()
_KEY_PREFIX = "login:captcha:"
_FAIL_PREFIX = "login:fail:"
_LOCK_PREFIX = "login:lock:"
_RATE_PREFIX = "login:captcha:rate:"

# 作用域 → 键前缀。用户端保持历史键名不变，管理端统一加 admin: 前缀。
_SCOPE_PREFIX = {"user": "", "admin": "admin:", "adminpwd": "adminpwd:"}


def _prefix(scope: str) -> str:
    """返回作用域键前缀；未知作用域按名字隔离，避免误共享状态。"""
    name = (scope or "user").strip().lower()
    if name in _SCOPE_PREFIX:
        return _SCOPE_PREFIX[name]
    return f"{name}:" if name else ""


def _key(base: str, value: str, scope: str) -> str:
    return f"{_prefix(scope)}{base}{value}"


# ---------------------------------------------------------------------------
# 存储后端
# ---------------------------------------------------------------------------
class _MemoryBackend:
    """进程内 KV：单实例 / 本地开发 / Redis 不可用时使用。"""

    name = "memory"

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}
        self._lock = threading.RLock()

    def _purge(self) -> None:
        now = time.time()
        for key in [k for k, (_, exp) in self._data.items() if exp <= now]:
            self._data.pop(key, None)

    def setex(self, key: str, ttl: int, value: str) -> None:
        with self._lock:
            self._data[key] = (str(value), time.time() + max(1, int(ttl)))

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            if item[1] <= time.time():
                self._data.pop(key, None)
                return None
            return item[0]

    def getdel(self, key: str) -> Optional[str]:
        with self._lock:
            value = self.get(key)
            self._data.pop(key, None)
            return value

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def incr(self, key: str, ttl: int) -> int:
        with self._lock:
            self._purge()
            item = self._data.get(key)
            if item and item[1] > time.time():
                value = int(item[0]) + 1
                self._data[key] = (str(value), item[1])
                return value
            self._data[key] = ("1", time.time() + max(1, int(ttl)))
            return 1

    def ttl(self, key: str) -> int:
        with self._lock:
            item = self._data.get(key)
            if not item or item[1] <= time.time():
                self._data.pop(key, None)
                return -2
            return int(item[1] - time.time())

    def ping(self) -> bool:
        return True


class _RedisBackend:
    """Redis 后端：多实例共享验证码、失败计数与锁定状态。"""

    name = "redis"

    # 计数首次写入时设置过期，避免 INCR 与 EXPIRE 之间的竞态
    _INCR_LUA = """
    local v = redis.call('INCR', KEYS[1])
    if v == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
    return v
    """

    def __init__(self, client) -> None:
        self._client = client
        self._incr = client.register_script(self._INCR_LUA)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._client.setex(key, max(1, int(ttl)), str(value))

    def get(self, key: str) -> Optional[str]:
        return self._client.get(key)

    def getdel(self, key: str) -> Optional[str]:
        pipe = self._client.pipeline(transaction=True)
        pipe.get(key)
        pipe.delete(key)
        value, _ = pipe.execute()
        return value

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def incr(self, key: str, ttl: int) -> int:
        return int(self._incr(keys=[key], args=[max(1, int(ttl))]))

    def ttl(self, key: str) -> int:
        return int(self._client.ttl(key))

    def ping(self) -> bool:
        return bool(self._client.ping())


_backend = None
_backend_lock = threading.Lock()


def backend():
    """返回存储后端；Redis 配置了但连不上时自动降级并在日志里说明。"""
    global _backend
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        if REDIS_URL:
            try:
                import redis  # 延迟导入：本地开发未装也能跑内存模式

                client = redis.Redis.from_url(
                    REDIS_URL,
                    decode_responses=True,
                    socket_connect_timeout=1.5,
                    socket_timeout=1.5,
                )
                client.ping()
                _backend = _RedisBackend(client)
                print(f"[captcha] 已连接 Redis：{REDIS_URL}")
                return _backend
            except Exception as exc:  # noqa: BLE001 - 任何连接问题都降级
                print(f"[captcha] Redis 不可用（{exc}），降级为进程内缓存（仅适合单实例）")
        else:
            print("[captcha] 未配置 REDIS_URL，使用进程内缓存（仅适合单实例）")
        _backend = _MemoryBackend()
        return _backend


def random_code(length: int = CAPTCHA_LENGTH) -> str:
    return "".join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(length))


def render_svg(code: str) -> str:
    """把验证码画成 SVG：随机旋转 / 颜色 + 干扰线 + 噪点，降低 OCR 命中率。"""
    rnd = secrets.SystemRandom()
    width, height = 120, 40
    palette = ["#243b6b", "#1f4e79", "#3b2f63", "#175b4a", "#6b2f3a", "#2f4f2f"]

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="图形验证码">',
        f'<rect width="{width}" height="{height}" rx="6" fill="#f2f5fb"/>',
    ]
    # 干扰线
    for _ in range(5):
        parts.append(
            '<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c}" '
            'stroke-opacity="0.32" stroke-width="{w}"/>'.format(
                x1=rnd.randint(0, width), y1=rnd.randint(0, height),
                x2=rnd.randint(0, width), y2=rnd.randint(0, height),
                c=rnd.choice(palette), w=rnd.choice([1, 1, 2]),
            )
        )
    # 噪点
    for _ in range(30):
        parts.append(
            '<circle cx="{cx}" cy="{cy}" r="{r}" fill="{c}" fill-opacity="0.35"/>'.format(
                cx=rnd.randint(0, width), cy=rnd.randint(0, height),
                r=rnd.choice([0.8, 1.0, 1.3]), c=rnd.choice(palette),
            )
        )
    # 字符：等距铺开，每个字单独旋转与抖动
    step = (width - 24) / max(1, len(code))
    for index, char in enumerate(code):
        x = 14 + index * step + rnd.uniform(-1.5, 1.5)
        y = 27.5 + rnd.uniform(-3.5, 3.5)
        rotate = rnd.uniform(-18, 18)
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" '
            f'transform="rotate({rotate:.1f} {x:.1f} {y:.1f})" '
            f'font-family="DejaVu Sans, Arial, Helvetica, sans-serif" '
            f'font-size="{rnd.choice([23, 25, 27])}" font-weight="700" '
            f'fill="{rnd.choice(palette)}">{char}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def svg_data_uri(code: str) -> str:
    raw = render_svg(code).encode("utf-8")
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")


def issue(scope: str = "user") -> Tuple[str, str, str]:
    """签发一张验证码，返回 (captchaKey, 明文, SVG data URI)。明文只留在服务端。"""
    key = secrets.token_urlsafe(18)
    code = random_code()
    store = backend()
    store.setex(_key(_KEY_PREFIX, key, scope), CAPTCHA_TTL_SECONDS, code)
    return key, code, svg_data_uri(code)


def consume(captcha_key: str, scope: str = "user") -> Optional[str]:
    """取出并删除验证码（一次性）。不存在或已过期返回 None。"""
    key = (captcha_key or "").strip()
    if not key:
        return None
    return backend().getdel(_key(_KEY_PREFIX, key, scope))


def peek(captcha_key: str, scope: str = "user") -> Optional[str]:
    """只读验证码，仅供自动化测试与排障使用，接口层不得暴露。"""
    key = (captcha_key or "").strip()
    return backend().get(_key(_KEY_PREFIX, key, scope)) if key else None


def verify(captcha_key: str, captcha_code: str, scope: str = "user") -> Tuple[bool, str]:
    """校验验证码。返回 (是否通过, 失败原因)。

    无论成功失败都删除该 Key：失败后前端必须换一张，避免穷举同一张图。
    key 也带作用域，所以用户端验证码无法用于管理端登录。
    """
    expected = consume(captcha_key, scope)
    if not expected:
        return False, "expired"
    submitted = (captcha_code or "").strip().upper()
    if not submitted:
        return False, "empty"
    if not secrets.compare_digest(expected, submitted):
        return False, "mismatch"
    return True, "ok"


# ---------------------------------------------------------------------------
# 限流 / 锁定
# ---------------------------------------------------------------------------
def hit_captcha_rate(ip: str, scope: str = "user",
                     limit: int = CAPTCHA_RATE_LIMIT,
                     window: int = CAPTCHA_RATE_WINDOW) -> Tuple[bool, int]:
    """取码限流：默认同一 IP 5 分钟最多 5 张。返回 (是否被限流, 剩余秒数)。

    管理端登录页是静态页，每次打开都会取一张码，所以调用方可以把 limit 放宽；
    用户端与管理员各自计数，互不挤占额度。
    """
    key = _key(_RATE_PREFIX, ip or "unknown", scope)
    count = backend().incr(key, window)
    remaining = max(1, backend().ttl(key))
    return count > limit, remaining


def hit_check_rate(ip: str, limit: int = 60, window: int = 60) -> Tuple[bool, int]:
    """用户名实时校验限流：默认同一 IP 每分钟 60 次，防批量枚举账号。"""
    key = "auth:check:" + (ip or "unknown")
    count = backend().incr(key, window)
    return count > limit, max(1, backend().ttl(key))


def hit_action_rate(ip: str, action: str, limit: int, window: int) -> Tuple[bool, int]:
    """通用动作限流：注册、找回密码等公开写接口共用。

    这些接口不需要登录，如果不按 IP 限流，脚本可以无限注册垃圾账号、
    或遍历邮箱批量触发重置邮件（顺带把 SMTP 配额打光）。
    """
    key = f"auth:action:{action}:" + (ip or "unknown")
    count = backend().incr(key, window)
    return count > limit, max(1, backend().ttl(key))


def lock_remaining(username: str, scope: str = "user") -> int:
    """账号剩余锁定秒数，0 表示未锁定。"""
    ttl = backend().ttl(_key(_LOCK_PREFIX, (username or "").strip().lower(), scope))
    return ttl if ttl > 0 else 0


def register_failure(username: str, scope: str = "user") -> Tuple[int, int]:
    """记一次登录失败。返回 (连续失败次数, 剩余锁定秒数)。"""
    name = (username or "").strip().lower()
    count = backend().incr(_key(_FAIL_PREFIX, name, scope), LOGIN_LOCK_SECONDS)
    if count >= LOGIN_MAX_FAILURES:
        backend().setex(_key(_LOCK_PREFIX, name, scope), LOGIN_LOCK_SECONDS, str(count))
        backend().delete(_key(_FAIL_PREFIX, name, scope))
        return count, LOGIN_LOCK_SECONDS
    return count, 0


def clear_failures(username: str, scope: str = "user") -> None:
    """登录成功后清空失败计数与锁定标记。"""
    name = (username or "").strip().lower()
    backend().delete(_key(_FAIL_PREFIX, name, scope))
    backend().delete(_key(_LOCK_PREFIX, name, scope))


def reset_for_tests() -> None:
    """测试用：清空进程内后端，避免用例之间互相污染。"""
    global _backend
    with _backend_lock:
        _backend = _MemoryBackend()
