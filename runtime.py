"""运行环境判定：区分「生产」与「本地开发 / 测试」。

上线前的安全默认值都依赖这个判断：

- 生产环境（Docker / MySQL）**不允许**回落到演示密码、调试验证码、公开的
  ``SECRET_KEY``；
- 本地 SQLite 开发与回归测试保留便利性（admin123 / 验证码回显），否则既有的
  测试脚本与本地验收流程会全部失效。

判定顺序：
1. 显式 ``APP_ENV`` / ``FLASK_ENV``：production / prod 为真，development /
   testing / test 为假；
2. 未显式声明时，``DB_BACKEND=mysql`` 视为生产（docker-compose 的默认形态），
   其余（SQLite）视为本地开发。

如需在本机用 MySQL 调试又不想触发生产策略，设置 ``APP_ENV=development`` 即可。
"""
from __future__ import annotations

import os

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def env_flag(name: str, default: bool = False) -> bool:
    """读取布尔型环境变量；未设置或无法识别时返回 ``default``。"""
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return default


def is_production() -> bool:
    """当前是否按生产环境策略运行（见模块文档）。"""
    env = (os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "").strip().lower()
    if env in ("production", "prod"):
        return True
    if env in ("development", "dev", "testing", "test"):
        return False
    return (os.environ.get("DB_BACKEND") or "").strip().lower() == "mysql"
