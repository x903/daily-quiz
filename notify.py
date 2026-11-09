"""通知渠道：邮件与短信。

设计目标：本地开发 / 验收环境没有 SMTP 与短信网关也能跑通全流程，同时不让
"把验证码返回给前端" 成为生产默认行为。

- `MAIL_HOST` 配置齐全 → 真实发信（SMTP，支持 SSL / STARTTLS）；
- `SMS_WEBHOOK_URL` 配置 → 以 JSON POST 方式投递给网关；
- 两者缺失时进入**开发模式**：内容打到容器日志（`docker compose logs -f web`），
  并在接口响应里附带一次性调试码 / 调试链接，方便测试。
  显式设置 `ACCOUNT_DEV_MODE=0` 可关闭调试码，此时缺少网关会直接报错。

所有函数只返回普通 dict / bool，不抛异常，调用方不需要 try/except。
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

import runtime

TIMEOUT = 8


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def mail_configured() -> bool:
    return bool(_env("MAIL_HOST") and _env("MAIL_FROM"))


def sms_configured() -> bool:
    return bool(_env("SMS_WEBHOOK_URL"))


def dev_mode() -> bool:
    """是否允许把验证码 / 重置链接回显到接口响应。

    安全默认：**生产环境一律关闭**，只有显式 ``ACCOUNT_DEV_MODE=1`` 才打开。

    历史行为是「未配置邮件网关就自动回显」，这在公网部署上是致命的：
    任何人只要提交一个已知邮箱，就能直接从 ``/api/auth/forgot-password``
    的响应里拿到重置链接，等于任意账号接管。现在生产环境没配网关时只会退化为
    「发不出去」，并在日志里提示管理员配置 SMTP，而不是把凭据交给调用方。

    本地 SQLite 开发仍保持便利（未配置网关时回显），并可用 ACCOUNT_DEV_MODE=0 关闭。
    """
    flag = _env("ACCOUNT_DEV_MODE").lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    return not runtime.is_production()


def _log_preview(kind: str, target: str, subject: str, body: str) -> None:
    """开发模式：把内容写进服务日志，方便 docker compose logs 直接看到。"""
    print(f"[{kind}:dev] to={target} subject={subject}\n{body}\n", flush=True)


def send_mail(to: str, subject: str, body: str) -> dict:
    """发送纯文本邮件。返回 {ok, dev, message}。"""
    if not mail_configured():
        _log_preview("mail", to, subject, body)
        if not dev_mode():
            return {"ok": False, "dev": False, "message": "邮件服务未配置，请联系管理员"}
        return {"ok": True, "dev": True, "message": "开发模式：邮件内容已输出到服务日志"}

    host = _env("MAIL_HOST")
    port = int(_env("MAIL_PORT", "465") or 465)
    user = _env("MAIL_USER")
    password = _env("MAIL_PASSWORD")
    sender = _env("MAIL_FROM")
    use_ssl = _env("MAIL_SSL", "1") not in ("0", "false", "no")
    use_tls = _env("MAIL_STARTTLS", "0") in ("1", "true", "yes")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("认证题库网", sender))
    msg["To"] = to
    msg.set_content(body)

    try:
        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=TIMEOUT) as smtp:
                if use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
    except Exception as exc:  # 网络 / 认证失败都不应把 500 抛给用户
        print(f"[mail:error] to={to} error={exc}", flush=True)
        return {"ok": False, "dev": False, "message": "邮件发送失败，请稍后重试"}
    return {"ok": True, "dev": False, "message": "邮件已发送"}


def send_sms(phone: str, body: str) -> dict:
    """发送短信。未配置网关时按开发模式处理（日志输出 + 调试码）。"""
    if not sms_configured():
        _log_preview("sms", phone, "验证码", body)
        if not dev_mode():
            return {"ok": False, "dev": False, "message": "短信服务未配置，请联系管理员"}
        return {"ok": True, "dev": True, "message": "开发模式：短信内容已输出到服务日志"}

    payload = json.dumps({"phone": phone, "content": body}).encode("utf-8")
    req = urllib.request.Request(
        _env("SMS_WEBHOOK_URL"),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    token = _env("SMS_WEBHOOK_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"HTTP {resp.status}")
    except Exception as exc:
        print(f"[sms:error] to={phone} error={exc}", flush=True)
        return {"ok": False, "dev": False, "message": "短信发送失败，请稍后重试"}
    return {"ok": True, "dev": False, "message": "短信已发送"}
