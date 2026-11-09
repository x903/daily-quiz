"""自由练习用户端接口（前缀 /api/practice）。

页面：
    GET  /practice                 题库目录（服务端渲染，见 web_site.py）
    GET  /practice/<slug>          模拟答题页

接口：
    GET  /api/practice/banks                       题库列表（公开，带当前用户进度）
    GET  /api/practice/banks/<slug>                题库详情（公开）
    POST /api/practice/banks/<slug>/start          开始 / 继续练习（登录）
    GET  /api/practice/sessions/<token>            恢复练习（登录）
    POST /api/practice/sessions/<token>/answer     保存单题作答（登录）
    POST /api/practice/sessions/<token>/submit     交卷判分（登录）
    GET  /api/practice/sessions/<token>/result     查看已交卷的解析（登录）

安全约束：
- 未交卷前任何接口都不返回正确答案与解析，只回题面、选项与已保存的作答；
- 场次 token 随机 24 字节，且每次访问都校验 user_id，不能越权看别人的卷子；
- 超时由服务端在 start / save / result 时兜底结算，不依赖前端倒计时。
"""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, request

import practice_bank
import security

bp = Blueprint("practice_api", __name__, url_prefix="/api/practice")


def api_action(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            data = view(*args, **kwargs)
            return jsonify({"ok": True, "data": data})
        except practice_bank.PracticeError as exc:
            payload = {"ok": False, "error": exc.message, "code": exc.code}
            payload.update(exc.extra)
            return jsonify(payload), exc.status

    return wrapper


def _user_id() -> int:
    user = security.resolve_current_user()
    return int(user["id"]) if user else 0


def _published(slug: str) -> dict:
    bank = practice_bank.get_bank(slug)
    if not bank or bank.get("status") == "draft":
        raise practice_bank.PracticeError("题库不存在或尚未发布", 404, "NOT_FOUND")
    return bank


# ---------------------------------------------------------------------------
# 题库目录
# ---------------------------------------------------------------------------
@bp.route("/banks", methods=["GET"])
@api_action
def list_banks():
    status = (request.args.get("status") or "all").strip().lower()
    if status not in ("all", "new", "done", "wrong"):
        status = "all"
    keyword = (request.args.get("q") or "").strip()
    vendor = (request.args.get("vendor") or "").strip()
    cert_path = (request.args.get("cert") or "").strip()
    items = practice_bank.list_banks(
        user_id=_user_id(), status=status, keyword=keyword,
        vendor=vendor, cert_path=cert_path,
    )
    return {
        "banks": items,
        "count": len(items),
        "filters": {"status": status, "q": keyword, "vendor": vendor, "cert": cert_path},
    }


@bp.route("/banks/<slug>", methods=["GET"])
@api_action
def bank_detail(slug: str):
    bank = _published(slug)
    return {"bank": practice_bank.user_bank_state(_user_id(), bank)}


# ---------------------------------------------------------------------------
# 练习场次
# ---------------------------------------------------------------------------
@bp.route("/banks/<slug>/start", methods=["POST"])
@security.api_user_required
@api_action
def start(slug: str):
    bank = _published(slug)
    payload = request.get_json(silent=True) or {}
    restart = bool(payload.get("restart"))
    return practice_bank.start_session(_user_id(), bank, restart=restart)


@bp.route("/sessions/<token>", methods=["GET"])
@security.api_user_required
@api_action
def session_detail(token: str):
    return practice_bank.get_session(_user_id(), token)


@bp.route("/sessions/<token>/answer", methods=["POST"])
@security.api_user_required
@api_action
def answer(token: str):
    payload = request.get_json(silent=True) or {}
    try:
        question_id = int(payload.get("question_id") or 0)
    except (TypeError, ValueError):
        question_id = 0
    if not question_id:
        raise practice_bank.PracticeError("缺少题目 ID", 400, "VALIDATION")
    return practice_bank.save_answer(
        _user_id(), token, question_id, payload.get("answer")
    )


@bp.route("/sessions/<token>/submit", methods=["POST"])
@security.api_user_required
@api_action
def submit(token: str):
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get("reason") or "manual").strip().lower()
    if reason not in ("manual", "timeout"):
        reason = "manual"
    return practice_bank.submit_session(_user_id(), token, reason=reason)


@bp.route("/sessions/<token>/result", methods=["GET"])
@security.api_user_required
@api_action
def result(token: str):
    session = practice_bank.get_session(_user_id(), token)
    if session.get("status") != "submitted":
        raise practice_bank.PracticeError("本场练习尚未交卷", 409, "NOT_SUBMITTED")
    return {"result": session.get("result"), "bank": session.get("bank")}
