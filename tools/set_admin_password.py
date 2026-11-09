"""重置管理员密码（不依赖 Web 服务，可在容器内直接执行）。

用途：上线前把演示密码换掉；或忘记密码、又被安全策略拦住时做应急恢复。

用法：
    # 宿主机（Docker 部署，MySQL 由 compose 注入连接信息）
    docker compose exec web python tools/set_admin_password.py -u admin

    # 本机 SQLite 开发库
    python tools/set_admin_password.py -u admin

脚本会交互式读取新密码（不回显、不落 shell 历史）；也可用 --password 便于自动化，
但会留在命令历史里，不推荐。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

import auth  # noqa: E402
import db  # noqa: E402
import models  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="重置管理员密码")
    parser.add_argument("-u", "--username", default="admin", help="管理员用户名，默认 admin")
    parser.add_argument("--password", default="", help="新密码（省略则交互输入）")
    args = parser.parse_args()

    username = (args.username or "").strip()
    row = models.get_admin_by_username(username)
    if not row:
        print(f"[x] 管理员「{username}」不存在。现有账号：")
        for item in models.list_admins():
            print(f"    - {item['username']}（{item['role']}，{item['status']}）")
        return 1

    password = args.password
    if not password:
        password = getpass.getpass("新密码：")
        confirm = getpass.getpass("再次输入：")
        if password != confirm:
            print("[x] 两次输入不一致")
            return 1
    try:
        password = auth.validate_admin_password(password)
    except auth.AccountValidationError as exc:
        print(f"[x] {exc.message}")
        return 1

    models.update_admin_password(int(row["id"]), auth.hash_password(password))
    # 清掉该账号的失败计数，避免刚改完密码仍处于锁定状态
    try:
        import captcha

        captcha.clear_failures(username.lower(), scope="admin")
    except Exception:  # noqa: BLE001 - Redis 不可用不影响改密
        pass

    print(f"[√] 已重置管理员「{username}」的密码（后端：{db.backend()}）")
    print("    旧会话会在下一次请求时因密码指纹不匹配自动失效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
