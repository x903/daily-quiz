"""统一数据库访问层：生产用 MySQL，本地离线 / 回归测试用 SQLite。

设计目标：**业务代码无需感知后端**。所有连接都暴露与 ``sqlite3.Connection``
兼容的最小接口，因此 `models` / `score_manager` / `wrong_book` / `quiz_logic`
里的 SQL 几乎可以原样复用：

    conn = models.get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    row["id"] / dict(row) / row.keys()      # 行对象统一为 dict-like
    cur.lastrowid / cur.rowcount
    with conn: ...                          # 正常提交、异常回滚（不关闭连接）
    conn.commit() / conn.rollback() / conn.close()
    conn.executescript(schema)              # MySQL 端自动拆分为多条语句

MySQL 适配要点：
- ``?`` 占位符 → ``%s``（跳过字符串字面量内的问号）；
- ``PRAGMA foreign_keys = ON`` → no-op（InnoDB 默认强制外键）；
- ``PRAGMA table_info(x)`` → ``information_schema.COLUMNS`` 查询；
- 驱动异常统一映射为 `DatabaseError` / `IntegrityError`；
- 启动时连接重试，配合 Docker Compose 等待 MySQL 就绪。

后端选择（按优先级）：
1. 显式 `DB_BACKEND=mysql|sqlite`；
2. 未指定时，若配置了 `DATABASE_URL` 或 `MYSQL_HOST` 则用 MySQL；
3. 否则回退 SQLite（保证没有 MySQL 服务时本地开发与既有测试仍可运行）。

推荐部署方式：``docker compose up -d --build``，Compose 会显式设置
`DB_BACKEND=mysql` 及全部 `MYSQL_*` 变量。
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = BASE_DIR / "data" / "quiz.db"

# 行对象类型：SQLite 为 sqlite3.Row，MySQL 为 RowDict，两者都是 dict-like。
Row = Any


class RowDict(dict):
    """同时支持 ``row['col']`` / ``dict(row)`` / ``row.keys()`` 与 ``row[0]``。

    PyMySQL 的 DictCursor 默认返回普通 dict，而旧代码（及测试）里存在
    ``fetchone()[0]`` 这类 sqlite3.Row 风格的位置索引。这里补上位置索引，
    使两种后端的行对象行为一致，业务代码无需为 MySQL 改写。
    """

    __slots__ = ("_keys",)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._keys = list(super().keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._keys[key])
        return super().__getitem__(key)


try:  # PyMySQL 只在 MySQL 后端才真正需要，SQLite 环境可以不装
    import pymysql
    from pymysql.cursors import DictCursor

    class CompatDictCursor(DictCursor):
        """DictCursor + RowDict：列名访问与位置索引都可用。"""

        dict_type = RowDict

    _HAS_PYMYSQL = True
except ImportError:  # pragma: no cover - 仅在未安装依赖时触发
    pymysql = None  # type: ignore[assignment]
    DictCursor = None  # type: ignore[assignment]
    CompatDictCursor = None  # type: ignore[assignment,misc]
    _HAS_PYMYSQL = False


class DatabaseError(Exception):
    """数据库访问异常（屏蔽 sqlite3 / pymysql 具体驱动类型）。"""


class IntegrityError(DatabaseError):
    """唯一键、外键、非空等约束冲突。"""


def _load_dotenv() -> None:
    """轻量加载根目录 `.env`（不引入额外依赖，已存在的环境变量优先）。"""
    path = BASE_DIR / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def backend() -> str:
    """返回当前生效的后端名称：``mysql`` 或 ``sqlite``（每次调用动态读取）。"""
    explicit = _env("DB_BACKEND").lower()
    if explicit in ("mysql", "sqlite"):
        return explicit
    if _env("DATABASE_URL") or _env("MYSQL_HOST"):
        return "mysql"
    return "sqlite"


def mysql_config() -> dict[str, Any]:
    """从 `DATABASE_URL` 或 `MYSQL_*` 环境变量解析连接参数。"""
    url = _env("DATABASE_URL")
    if url:
        parsed = urlparse(url)
        return {
            "host": parsed.hostname or "127.0.0.1",
            "port": parsed.port or 3306,
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "database": (parsed.path or "/").lstrip("/") or "daily_quiz",
        }
    return {
        "host": _env("MYSQL_HOST") or "127.0.0.1",
        "port": int(_env("MYSQL_PORT") or 3306),
        "user": _env("MYSQL_USER") or "quiz",
        "password": _env("MYSQL_PASSWORD") or _env("MYSQL_PWD") or "",
        "database": _env("MYSQL_DATABASE") or _env("MYSQL_DB") or "daily_quiz",
    }


def _map_exception(exc: BaseException) -> BaseException:
    """把驱动异常归一化为 DatabaseError / IntegrityError；其它异常原样抛出。"""
    if isinstance(exc, DatabaseError):
        return exc
    if isinstance(exc, sqlite3.IntegrityError):
        return IntegrityError(str(exc))
    if isinstance(exc, sqlite3.Error):
        return DatabaseError(str(exc))
    if _HAS_PYMYSQL:
        if isinstance(exc, pymysql.err.IntegrityError):
            return IntegrityError(str(exc))
        if isinstance(exc, pymysql.err.Error):
            return DatabaseError(str(exc))
    return exc


def _run_sql(conn: Any, cursor: Any, sql: str, args: Optional[Sequence[Any]]) -> Any:
    """执行单条 SQL 并归一化驱动异常（供两个后端共用）。"""
    try:
        cursor.execute(sql, args) if args is not None else cursor.execute(sql)
    except BaseException as exc:  # noqa: BLE001 - 需要同时覆盖两种驱动异常
        mapped = _map_exception(exc)
        if mapped is exc:
            raise
        raise mapped from exc
    return cursor


_PRAGMA_FK_RE = re.compile(r"^\s*PRAGMA\s+foreign_keys\s*=\s*ON\s*;?\s*$", re.I)
_PRAGMA_INFO_RE = re.compile(
    r"^\s*PRAGMA\s+table_info\s*\(\s*['\"]?(\w+)['\"]?\s*\)\s*;?\s*$", re.I
)


def _to_mysql_placeholders(sql: str) -> str:
    """把 SQL 中的 ``?`` 占位符替换为 ``%s``，跳过引号内的字面量。"""
    out: list[str] = []
    quote: Optional[str] = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(sql):
                out.append(sql[i + 1])
                i += 2
                continue
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:  # 转义的单引号 ''
                    out.append(sql[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _split_script(script: str) -> list[str]:
    """把 MySQL 建表脚本拆成单条语句（去掉 -- 行注释与 /* */ 块注释）。"""
    cleaned = re.sub(r"/\*.*?\*/", "", script, flags=re.S)
    lines = [
        line for line in cleaned.splitlines()
        if not line.strip().startswith(("--", "#"))
    ]
    text = "\n".join(lines)
    return [stmt.strip() for stmt in text.split(";") if stmt.strip()]


class _NullCursor:
    """不涉及真实查询的语句（如 PRAGMA foreign_keys）返回的空游标。"""

    lastrowid = 0
    rowcount = 0

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list:
        return []


class Connection:
    """两种后端共同暴露的连接接口（`close` / `with` 语义与 sqlite3 一致）。"""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self._cursors: list[Any] = []
        self._closed = False

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        raise NotImplementedError

    def executescript(self, script: str) -> None:
        raise NotImplementedError

    def commit(self) -> None:
        if self._closed:
            return
        try:
            self._raw.commit()
        except BaseException as exc:
            mapped = _map_exception(exc)
            if mapped is exc:
                raise
            raise mapped from exc

    def rollback(self) -> None:
        if self._closed:
            return
        try:
            self._raw.rollback()
        except BaseException as exc:
            mapped = _map_exception(exc)
            if mapped is exc:
                raise
            raise mapped from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for cursor in self._cursors:
            try:
                cursor.close()
            except Exception:  # noqa: BLE001 - 关闭失败不应影响主流程
                pass
        self._cursors.clear()
        self._raw.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False  # 异常继续向上抛出，与 sqlite3.Connection 一致

    def _new_cursor(self) -> Any:
        cursor = self._raw.cursor()
        self._cursors.append(cursor)
        return cursor


class SQLiteConnection(Connection):
    """SQLite 包装：SQL 原样透传，行对象为 sqlite3.Row（dict-like）。"""

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        args = tuple(params) if params else ()
        return _run_sql(self._raw, self._new_cursor(), sql, args)

    def executescript(self, script: str) -> None:
        try:
            self._raw.executescript(script)
        except BaseException as exc:
            mapped = _map_exception(exc)
            if mapped is exc:
                raise
            raise mapped from exc


class MySQLConnection(Connection):
    """MySQL 包装：占位符 / PRAGMA 转换，行对象为 dict（DictCursor）。"""

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        info = _PRAGMA_INFO_RE.match(sql)
        if info:
            sql = (
                "SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
            )
            args: Optional[Sequence[Any]] = (info.group(1),)
        elif _PRAGMA_FK_RE.match(sql):
            return _NullCursor()  # InnoDB 默认强制外键，无需 PRAGMA
        else:
            sql = _to_mysql_placeholders(sql)
            args = tuple(params) if params else None
        return _run_sql(self._raw, self._new_cursor(), sql, args)

    def executescript(self, script: str) -> None:
        for statement in _split_script(script):
            _run_sql(self._raw, self._new_cursor(), statement, None)


def _connect_mysql() -> Any:
    """建立 MySQL 连接，启动时按配置重试（Docker 里 MySQL 通常后于 Web 就绪）。"""
    if not _HAS_PYMYSQL:
        raise DatabaseError(
            "使用 MySQL 后端需要 PyMySQL：pip install PyMySQL cryptography"
        )
    cfg = mysql_config()
    retries = max(1, int(_env("DB_CONNECT_RETRIES") or 15))
    delay = max(0.0, float(_env("DB_CONNECT_RETRY_DELAY") or 2.0))
    last_exc: Optional[BaseException] = None
    for attempt in range(retries):
        try:
            return pymysql.connect(
                host=cfg["host"],
                port=cfg["port"],
                user=cfg["user"],
                password=cfg["password"],
                database=cfg["database"],
                charset="utf8mb4",
                cursorclass=CompatDictCursor,
                autocommit=False,
                connect_timeout=5,
                read_timeout=30,
                write_timeout=30,
            )
        except BaseException as exc:  # noqa: BLE001 - 连接阶段任何异常都需重试
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(delay)
    raise DatabaseError(
        f"连接 MySQL 失败（{cfg['host']}:{cfg['port']}/{cfg['database']}，"
        f"已重试 {retries} 次）：{last_exc}"
    ) from last_exc


def get_conn(sqlite_path: Optional[Path] = None) -> Connection:
    """返回一个统一接口的连接；调用方负责关闭。

    Args:
        sqlite_path: SQLite 文件路径；仅 SQLite 后端生效，由 `models.get_conn`
            动态传入 `models.DB_PATH`，因此既有测试仍可重定向到临时库。

    Returns:
        Connection：SQLiteConnection 或 MySQLConnection。
    """
    if backend() == "mysql":
        return MySQLConnection(_connect_mysql())

    path = Path(sqlite_path) if sqlite_path else DEFAULT_SQLITE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    conn = SQLiteConnection(raw)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
