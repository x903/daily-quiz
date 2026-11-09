"""自由练习：题库、考试规则、模拟答题与学习进度。

模块边界
--------
- **考试规则**（`exam_rules`）：阿里云 ACP 云计算 / ACP 大模型等真实考试约束，
  后台可改；题库创建时复制一份快照，之后改规则不会篡改历史题库。
- **题库**（`question_banks`）：前端只展示 `published`；`disabled` 仅隐藏入口，
  已产生的答题记录、成绩与解析全部保留。
- **题目**（`bank_questions`）：与每日答题的 `questions` 表完全独立，避免
  管理员编辑模拟卷时影响每日答题题池。
- **练习场次**（`bank_practice_sessions`）：开考瞬间冻结题目顺序与题量；
  交卷后写成绩；超时由服务端强制交卷，不依赖浏览器是否还开着。
- **逐题作答**（`bank_practice_answers`）：同场次每题保留最后一次作答。
- **学习进度**（`bank_practice_progress`）：按用户 × 题库聚合「已练 / 答对 /
  错题 / 最佳分」，列表页的「未做 / 已做 / 错题」筛选直接读这张表。

判分规则
--------
单选、多选均采用**完全匹配**：多选少选、多选、错选都算错，不给部分分，
与阿里云 ACP 真实机考一致。得分按 `正确题数 × 总分 / 总题数` 折算，
因此 100 题 / 100 分时每题 1 分，75 题 / 100 分时按比例折算。
"""
from __future__ import annotations

import json
import logging
import random
import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Iterable, Optional

import db
import models

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
LEVEL_TAGS = ("基础", "进阶", "冲刺", "错题本")
DIFFICULTIES = ("简单", "中等", "困难")
QUESTION_TYPES = ("single", "multiple")
TYPE_LABELS = {"single": "单选题", "multiple": "多选题"}

STATUS_LABELS = {"draft": "草稿", "published": "已发布", "disabled": "已停用"}
RULE_CODE_CUSTOM = "custom"
RULE_NAME_CUSTOM = "自定义规则"

# 模拟考积分：成绩 0~100 分按倍数折算；完成率不足 60% 视为无效练习（交白卷
# 不计分），同一题库同一自然日只计一次，避免「开考→交卷」刷分。
EXAM_POINTS_PER_SCORE = 2
EXAM_MIN_COMPLETION = 0.6

# 单用户单日对同一题库的开考场次上限：挡住脚本批量「开考→交白卷→换号再来」抓题。
MAX_DAILY_STARTS_PER_BANK = 20

# 超时场次全局回收：每个进程每小时最多触发一次（不依赖外部定时任务）。
SWEEP_INTERVAL_SECONDS = 3600
_last_sweep_at = 0.0

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")

# 真实考试规则（可在后台修改；这里是首次启动的预置值）。
# 说明：ACP 大模型官方只公布「75 题 / 100 分 / 80 分合格」，未公开单选多选
# 拆分；平台默认 50 单选 + 25 多选，并在描述里明确标注，管理员可自行调整。
PRESET_RULES = (
    {
        "code": "aliyun-acp-cloud",
        "name": "阿里云 ACP 云计算高级工程师",
        "vendor_slug": "aliyun",
        "cert_path": "aliyun/acp/cloud",
        "total_questions": 100,
        "single_count": 70,
        "multiple_count": 30,
        "total_score": 100,
        "pass_score": 80,
        "duration_minutes": 120,
        "description": "共 100 题（70 道单选 + 30 道多选），每题 1 分，满分 100 分，80 分及以上合格，考试时长 120 分钟，超时自动交卷。",
    },
    {
        "code": "aliyun-acp-llm",
        "name": "阿里云 ACP 大模型高级工程师",
        "vendor_slug": "aliyun",
        "cert_path": "aliyun/acp/llm",
        "total_questions": 75,
        "single_count": 50,
        "multiple_count": 25,
        "total_score": 100,
        "pass_score": 80,
        "duration_minutes": 120,
        "description": "共 75 题，满分 100 分，80 分及以上合格。官方未公开题型拆分，平台默认 50 单选 + 25 多选，可在后台调整。",
    },
)


class PracticeError(Exception):
    """业务异常：由接口层转换为统一的 JSON 错误响应。"""

    def __init__(self, message: str, status: int = 400, code: str = "",
                 extra: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.extra = extra or {}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _conn():
    return models.get_conn()


def _json_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _json_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_letters(raw) -> list[str]:
    """把 A,B / ["A","B"] / "AB" 统一为去重排序的大写字母数组。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = [str(v).strip().upper()[:1] for v in raw]
    else:
        text = re.sub(r"[\s,，、;；/|]", "", str(raw).upper())
        values = list(text)
    return sorted({v for v in values if v.isalpha()})


# ---------------------------------------------------------------------------
# 考试规则
# ---------------------------------------------------------------------------
def seed_rules(conn=None) -> None:
    """幂等写入预置考试规则；只补缺失项，不覆盖后台改过的值。"""
    own = conn is None
    conn = conn or _conn()
    try:
        for rule in PRESET_RULES:
            row = conn.execute(
                "SELECT id FROM exam_rules WHERE code = ?", (rule["code"],)
            ).fetchone()
            if row:
                continue
            now = _now()
            conn.execute(
                "INSERT INTO exam_rules (code, name, vendor_slug, cert_path, "
                "total_questions, single_count, multiple_count, score_per_question, "
                "total_score, pass_score, duration_minutes, description, is_preset, "
                "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?)",
                (
                    rule["code"], rule["name"], rule["vendor_slug"], rule["cert_path"],
                    rule["total_questions"], rule["single_count"], rule["multiple_count"],
                    round(rule["total_score"] / rule["total_questions"], 2),
                    rule["total_score"], rule["pass_score"], rule["duration_minutes"],
                    rule["description"], now, now,
                ),
            )
        conn.commit()
    finally:
        if own:
            conn.close()


def list_rules() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM exam_rules WHERE status = 'active' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_rule(code: str) -> Optional[dict]:
    code = (code or "").strip()
    if not code or code == RULE_CODE_CUSTOM:
        return None
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM exam_rules WHERE code = ? AND status = 'active'", (code,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_rule(code: str, payload: dict) -> dict:
    """后台修改 / 新建考试规则。"""
    code = (code or "").strip()
    if not SLUG_RE.match(code):
        raise PracticeError("规则代码只能是小写字母、数字和连字符，3-64 位", 400, "VALIDATION")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise PracticeError("规则名称不能为空", 400, "VALIDATION")
    total = _int(payload.get("total_questions"))
    single = _int(payload.get("single_count"))
    multiple = _int(payload.get("multiple_count"))
    total_score = _float(payload.get("total_score"), 100)
    pass_score = _float(payload.get("pass_score"), 80)
    duration = _int(payload.get("duration_minutes"), 120)
    if total <= 0 or single < 0 or multiple < 0 or single + multiple != total:
        raise PracticeError("题量必须满足：单选题数 + 多选题数 = 总题数", 400, "VALIDATION")
    if total_score <= 0 or not (0 <= pass_score <= total_score):
        raise PracticeError("及格分必须在 0 与总分之间", 400, "VALIDATION")
    if not (1 <= duration <= 600):
        raise PracticeError("考试时长需在 1-600 分钟之间", 400, "VALIDATION")
    now = _now()
    conn = _conn()
    try:
        row = conn.execute("SELECT id, is_preset FROM exam_rules WHERE code = ?", (code,)).fetchone()
        values = (
            name, str(payload.get("vendor_slug") or "").strip(),
            str(payload.get("cert_path") or "").strip(), total, single, multiple,
            round(total_score / total, 2), total_score, pass_score, duration,
            str(payload.get("description") or "").strip(), now,
        )
        if row:
            conn.execute(
                "UPDATE exam_rules SET name=?, vendor_slug=?, cert_path=?, total_questions=?, "
                "single_count=?, multiple_count=?, score_per_question=?, total_score=?, "
                "pass_score=?, duration_minutes=?, description=?, updated_at=? WHERE code=?",
                values + (code,),
            )
        else:
            conn.execute(
                "INSERT INTO exam_rules (name, vendor_slug, cert_path, total_questions, "
                "single_count, multiple_count, score_per_question, total_score, pass_score, "
                "duration_minutes, description, updated_at, code, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                values + (code, now),
            )
        conn.commit()
    finally:
        conn.close()
    return get_rule(code)


# ---------------------------------------------------------------------------
# 题库
# ---------------------------------------------------------------------------
def _category_by_path(conn, path: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM cert_categories WHERE path_slug = ?", (path,)
    ).fetchone()
    return dict(row) if row else None


def _question_counts(conn, bank_id: int) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN type = 'single' THEN 1 ELSE 0 END) AS single_count, "
        "SUM(CASE WHEN type = 'multiple' THEN 1 ELSE 0 END) AS multiple_count "
        "FROM bank_questions WHERE bank_id = ? AND status = 'active'",
        (int(bank_id),),
    ).fetchone()
    return {
        "question_count": int(row["total"] or 0) if row else 0,
        "actual_single": int(row["single_count"] or 0) if row else 0,
        "actual_multiple": int(row["multiple_count"] or 0) if row else 0,
    }


def _progress_map(conn, user_id: int, bank_ids: list[int]) -> dict[int, dict]:
    if not user_id or not bank_ids:
        return {}
    marks = ",".join("?" for _ in bank_ids)
    rows = conn.execute(
        f"SELECT * FROM bank_practice_progress WHERE user_id = ? AND bank_id IN ({marks})",
        (int(user_id), *[int(b) for b in bank_ids]),
    ).fetchall()
    return {int(r["bank_id"]): dict(r) for r in rows}


def _ongoing_map(conn, user_id: int) -> dict[int, dict]:
    """当前用户每本题库最近一场未交卷的练习。"""
    if not user_id:
        return {}
    rows = conn.execute(
        "SELECT * FROM bank_practice_sessions WHERE user_id = ? AND status = 'ongoing' "
        "ORDER BY id DESC",
        (int(user_id),),
    ).fetchall()
    out: dict[int, dict] = {}
    for row in rows:
        out.setdefault(int(row["bank_id"]), dict(row))
    return out


def _bank_payload(row: dict, *, progress: Optional[dict] = None,
                  ongoing: Optional[dict] = None) -> dict:
    data = dict(row)
    data["status_label"] = STATUS_LABELS.get(data.get("status"), data.get("status") or "")
    data["rule_label"] = data.get("rule_name") or RULE_NAME_CUSTOM
    data["is_mock"] = bool(data.get("rule_code")) and data.get("rule_code") != RULE_CODE_CUSTOM
    questions = _int(data.get("question_count"))
    if not questions:
        # 列表查询会带 question_count；单条查询时现算，避免调用方拿到 0。
        questions = _int(data.get("total_questions"))
    data["question_count"] = questions
    data["type_mix"] = f"{_int(data.get('single_count'))} 单选 · {_int(data.get('multiple_count'))} 多选"

    p = progress or {}
    answered = _int(p.get("answered_count"))
    data["progress"] = {
        "answered_count": answered,
        "correct_count": _int(p.get("correct_count")),
        "wrong_count": _int(p.get("wrong_count")),
        "best_score": round(_float(p.get("best_score")), 1),
        "last_score": round(_float(p.get("last_score")), 1),
        "last_practiced_at": p.get("last_practiced_at") or "",
        "percent": round(answered / questions * 100, 1) if questions else 0,
        "started": answered > 0,
        "has_wrong": _int(p.get("wrong_count")) > 0,
    }
    ongoing_data = None
    if ongoing:
        remaining = _remaining_seconds(ongoing)
        if ongoing.get("status") == "ongoing" and (not ongoing.get("deadline_at") or remaining > 0):
            ongoing_data = {
                "token": ongoing.get("token"),
                "answered": _int(ongoing.get("answered")),
                "total": _int(ongoing.get("total")),
                "remaining_seconds": remaining,
            }
    data["ongoing"] = ongoing_data
    return data


def list_banks(*, user_id: int = 0, status: str = "all", keyword: str = "",
               vendor: str = "", cert_path: str = "", public_only: bool = True,
               include_counts: bool = True) -> list[dict]:
    """题库列表：带实际题量、题型分布、当前用户进度与未交卷场次。"""
    where, args = [], []
    if public_only:
        where.append("b.status = 'published'")
    if vendor:
        where.append("b.vendor_slug = ?")
        args.append(vendor)
    if cert_path:
        where.append("b.cert_path = ?")
        args.append(cert_path)
    if keyword:
        like = f"%{keyword.strip()}%"
        where.append("(b.name LIKE ? OR b.cert_name LIKE ? OR b.description LIKE ?)")
        args += [like, like, like]
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    conn = _conn()
    try:
        rows = conn.execute(
            f"""
            SELECT b.*,
                   (SELECT COUNT(*) FROM bank_questions q
                     WHERE q.bank_id = b.id AND q.status = 'active') AS question_count,
                   (SELECT COUNT(*) FROM bank_questions q
                     WHERE q.bank_id = b.id AND q.status = 'active' AND q.type = 'single') AS actual_single,
                   (SELECT COUNT(*) FROM bank_questions q
                     WHERE q.bank_id = b.id AND q.status = 'active' AND q.type = 'multiple') AS actual_multiple
            FROM question_banks b
            {clause}
            ORDER BY b.sort_order, b.id
            """,
            tuple(args),
        ).fetchall()
        banks = [dict(r) for r in rows]
        ids = [int(b["id"]) for b in banks]
        progress = _progress_map(conn, user_id, ids)
        ongoing = _ongoing_map(conn, user_id)
        items = [
            _bank_payload(b, progress=progress.get(int(b["id"])),
                          ongoing=ongoing.get(int(b["id"])))
            for b in banks
        ]
    finally:
        conn.close()

    if status == "new":
        items = [b for b in items if not b["progress"]["started"]]
    elif status == "done":
        items = [b for b in items if b["progress"]["started"]]
    elif status == "wrong":
        items = [b for b in items if b["progress"]["has_wrong"]]
    return items


def get_bank(identifier, *, by_id: bool = False) -> Optional[dict]:
    conn = _conn()
    try:
        if by_id:
            row = conn.execute("SELECT * FROM question_banks WHERE id = ?", (int(identifier),)).fetchone()
        else:
            row = conn.execute("SELECT * FROM question_banks WHERE slug = ?", (str(identifier),)).fetchone()
        if not row:
            return None
        data = dict(row)
        data.update(_question_counts(conn, data["id"]))
        return data
    finally:
        conn.close()


def user_bank_state(user_id: int, bank: dict) -> dict:
    """单题库的当前用户进度 + 未交卷场次（列表页与详情页共用）。"""
    if not user_id:
        return _bank_payload(bank)
    conn = _conn()
    try:
        progress = _progress_map(conn, int(user_id), [int(bank["id"])]).get(int(bank["id"]))
        ongoing = _ongoing_map(conn, int(user_id)).get(int(bank["id"]))
        return _bank_payload(bank, progress=progress, ongoing=ongoing)
    finally:
        conn.close()


def _validate_bank_fields(payload: dict, existing: Optional[dict] = None) -> dict:
    name = str(payload.get("name", existing["name"] if existing else "") or "").strip()
    if not (2 <= len(name) <= 80):
        raise PracticeError("题库名称需为 2-80 个字符", 400, "VALIDATION")
    cert_path = str(payload.get("cert_path", existing["cert_path"] if existing else "") or "").strip()
    if not cert_path:
        raise PracticeError("请选择所属认证", 400, "VALIDATION")
    level_tag = str(payload.get("level_tag", existing["level_tag"] if existing else "基础") or "基础").strip()
    if level_tag not in LEVEL_TAGS:
        raise PracticeError("标签只能是：" + " / ".join(LEVEL_TAGS), 400, "VALIDATION")
    difficulty = str(payload.get("difficulty", existing["difficulty"] if existing else "中等") or "中等").strip()
    if difficulty not in DIFFICULTIES:
        raise PracticeError("难度只能是：" + " / ".join(DIFFICULTIES), 400, "VALIDATION")
    description = str(payload.get("description", existing["description"] if existing else "") or "").strip()
    if len(description) > 300:
        raise PracticeError("题库简介不能超过 300 字", 400, "VALIDATION")
    rule_code = str(payload.get("rule_code", existing["rule_code"] if existing else RULE_CODE_CUSTOM) or RULE_CODE_CUSTOM).strip()

    conn = _conn()
    try:
        category = _category_by_path(conn, cert_path)
    finally:
        conn.close()
    if not category:
        raise PracticeError("所属认证不存在，请刷新后重新选择", 400, "VALIDATION")

    rule = get_rule(rule_code)
    if rule:
        total = _int(rule["total_questions"])
        single = _int(rule["single_count"])
        multiple = _int(rule["multiple_count"])
        total_score = _float(rule["total_score"], 100)
        pass_score = _float(rule["pass_score"], 80)
        duration = _int(rule["duration_minutes"], 120)
        rule_name = rule["name"]
    else:
        rule_code = RULE_CODE_CUSTOM
        total = _int(payload.get("total_questions", existing["total_questions"] if existing else 0))
        single = _int(payload.get("single_count", existing["single_count"] if existing else 0))
        multiple = _int(payload.get("multiple_count", existing["multiple_count"] if existing else 0))
        total_score = _float(payload.get("total_score", existing["total_score"] if existing else 100), 100)
        pass_score = _float(payload.get("pass_score", existing["pass_score"] if existing else 80), 80)
        duration = _int(payload.get("duration_minutes", existing["duration_minutes"] if existing else 120), 120)
        rule_name = RULE_NAME_CUSTOM
        if total <= 0 or total > 300:
            raise PracticeError("总题量需在 1-300 之间", 400, "VALIDATION")
        if single < 0 or multiple < 0 or single + multiple != total:
            raise PracticeError("题量必须满足：单选题数 + 多选题数 = 总题数", 400, "VALIDATION")
        if total_score <= 0 or not (0 <= pass_score <= total_score):
            raise PracticeError("及格分必须在 0 与总分之间", 400, "VALIDATION")
        if not (1 <= duration <= 600):
            raise PracticeError("考试时长需在 1-600 分钟之间", 400, "VALIDATION")

    return {
        "name": name,
        "cert_path": cert_path,
        "vendor_slug": category["vendor_slug"],
        "vendor_name": category["vendor_name"],
        "cert_name": category["cert_name"],
        "category_id": category["id"],
        "level_tag": level_tag,
        "difficulty": difficulty,
        "description": description,
        "rule_code": rule_code,
        "rule_name": rule_name,
        "total_questions": total,
        "single_count": single,
        "multiple_count": multiple,
        "score_per_question": round(total_score / total, 2) if total else 0,
        "total_score": total_score,
        "pass_score": pass_score,
        "duration_minutes": duration,
    }


def create_bank(payload: dict, created_by: str = "") -> dict:
    fields = _validate_bank_fields(payload)
    slug = str(payload.get("slug") or "").strip().lower()
    if slug and not SLUG_RE.match(slug):
        raise PracticeError("URL 标识只能是小写字母、数字和连字符，3-64 位", 400, "VALIDATION")
    if not slug:
        slug = "bank-" + secrets.token_hex(4)
    now = _now()
    conn = _conn()
    try:
        if conn.execute("SELECT id FROM question_banks WHERE slug = ?", (slug,)).fetchone():
            raise PracticeError("URL 标识已被占用，请换一个", 409, "SLUG_TAKEN")
        cur = conn.execute(
            "INSERT INTO question_banks (slug, name, vendor_slug, vendor_name, cert_path, "
            "cert_name, category_id, level_tag, difficulty, description, rule_code, rule_name, "
            "total_questions, single_count, multiple_count, score_per_question, total_score, "
            "pass_score, duration_minutes, status, sort_order, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)",
            (
                slug, fields["name"], fields["vendor_slug"], fields["vendor_name"],
                fields["cert_path"], fields["cert_name"], fields["category_id"],
                fields["level_tag"], fields["difficulty"], fields["description"],
                fields["rule_code"], fields["rule_name"], fields["total_questions"],
                fields["single_count"], fields["multiple_count"], fields["score_per_question"],
                fields["total_score"], fields["pass_score"], fields["duration_minutes"],
                _int(payload.get("sort_order")), created_by, now, now,
            ),
        )
        bank_id = int(cur.lastrowid)
        conn.commit()
    except db.IntegrityError as exc:
        conn.rollback()
        raise PracticeError("题库标识或名称冲突：" + str(exc), 409, "DUPLICATE") from exc
    finally:
        conn.close()
    return get_bank(bank_id, by_id=True)


def update_bank(bank_id: int, payload: dict) -> dict:
    existing = get_bank(bank_id, by_id=True)
    if not existing:
        raise PracticeError("题库不存在", 404, "NOT_FOUND")
    fields = _validate_bank_fields(payload, existing)
    slug = str(payload.get("slug") or existing["slug"]).strip().lower()
    if not SLUG_RE.match(slug):
        raise PracticeError("URL 标识只能是小写字母、数字和连字符，3-64 位", 400, "VALIDATION")
    now = _now()
    conn = _conn()
    try:
        clash = conn.execute(
            "SELECT id FROM question_banks WHERE slug = ? AND id <> ?", (slug, int(bank_id))
        ).fetchone()
        if clash:
            raise PracticeError("URL 标识已被其它题库占用", 409, "SLUG_TAKEN")
        conn.execute(
            "UPDATE question_banks SET slug=?, name=?, vendor_slug=?, vendor_name=?, "
            "cert_path=?, cert_name=?, category_id=?, level_tag=?, difficulty=?, description=?, "
            "rule_code=?, rule_name=?, total_questions=?, single_count=?, multiple_count=?, "
            "score_per_question=?, total_score=?, pass_score=?, duration_minutes=?, "
            "sort_order=?, updated_at=? WHERE id=?",
            (
                slug, fields["name"], fields["vendor_slug"], fields["vendor_name"],
                fields["cert_path"], fields["cert_name"], fields["category_id"],
                fields["level_tag"], fields["difficulty"], fields["description"],
                fields["rule_code"], fields["rule_name"], fields["total_questions"],
                fields["single_count"], fields["multiple_count"], fields["score_per_question"],
                fields["total_score"], fields["pass_score"], fields["duration_minutes"],
                _int(payload.get("sort_order", existing.get("sort_order"))), now, int(bank_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_bank(bank_id, by_id=True)


def publish_check(bank: dict) -> list[str]:
    """发布前校验：题量、单选/多选比例、规则快照必须完全一致。"""
    errors: list[str] = []
    if bank["status"] == "published":
        return errors
    total = _int(bank["total_questions"])
    single = _int(bank["single_count"])
    multiple = _int(bank["multiple_count"])
    actual_total = _int(bank.get("question_count"))
    actual_single = _int(bank.get("actual_single"))
    actual_multiple = _int(bank.get("actual_multiple"))
    if total <= 0:
        errors.append("题库题量声明为 0，请先配置考试规则")
    if actual_total == 0:
        errors.append("题库内还没有题目，至少需要 " + str(total) + " 题")
    elif actual_total != total:
        errors.append(f"实际题目 {actual_total} 题，与声明的 {total} 题不一致")
    if actual_single != single:
        errors.append(f"单选题实际 {actual_single} 道，与要求的 {single} 道不一致")
    if actual_multiple != multiple:
        errors.append(f"多选题实际 {actual_multiple} 道，与要求的 {multiple} 道不一致")
    rule_code = bank.get("rule_code") or RULE_CODE_CUSTOM
    if rule_code != RULE_CODE_CUSTOM:
        rule = get_rule(rule_code)
        if not rule:
            errors.append("关联的考试规则已被删除或停用，请重新选择规则")
        else:
            snapshot = (
                _int(bank["total_questions"]), _int(bank["single_count"]),
                _int(bank["multiple_count"]), _float(bank["total_score"]),
                _float(bank["pass_score"]), _int(bank["duration_minutes"]),
            )
            current = (
                _int(rule["total_questions"]), _int(rule["single_count"]),
                _int(rule["multiple_count"]), _float(rule["total_score"]),
                _float(rule["pass_score"]), _int(rule["duration_minutes"]),
            )
            if snapshot != current:
                errors.append("关联的考试规则已更新，请编辑题库并重新保存以同步最新规则")
    return errors


def set_bank_status(bank_id: int, status: str) -> dict:
    if status not in ("draft", "published", "disabled"):
        raise PracticeError("状态只能是 draft / published / disabled", 400, "VALIDATION")
    bank = get_bank(bank_id, by_id=True)
    if not bank:
        raise PracticeError("题库不存在", 404, "NOT_FOUND")
    if status == "published":
        errors = publish_check(bank)
        if errors:
            raise PracticeError("；".join(errors), 409, "PUBLISH_CHECK_FAILED",
                                {"details": errors})
    now = _now()
    conn = _conn()
    try:
        conn.execute(
            "UPDATE question_banks SET status=?, updated_at=?, "
            "published_at = CASE WHEN ? = 'published' AND published_at = '' THEN ? ELSE published_at END "
            "WHERE id=?",
            (status, now, status, now, int(bank_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return get_bank(bank_id, by_id=True)


def delete_bank(bank_id: int) -> None:
    bank = get_bank(bank_id, by_id=True)
    if not bank:
        raise PracticeError("题库不存在", 404, "NOT_FOUND")
    conn = _conn()
    try:
        used = conn.execute(
            "SELECT COUNT(*) AS n FROM bank_practice_sessions WHERE bank_id = ?",
            (int(bank_id),),
        ).fetchone()
        if used and int(used["n"]) > 0:
            raise PracticeError("该题库已有答题记录，不能删除；请改为「停用」以保留历史成绩",
                                409, "HAS_RECORDS")
        conn.execute("DELETE FROM question_banks WHERE id = ?", (int(bank_id),))
        conn.commit()
    finally:
        conn.close()


def bank_stats(bank_id: int) -> dict:
    bank = get_bank(bank_id, by_id=True)
    if not bank:
        raise PracticeError("题库不存在", 404, "NOT_FOUND")
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS attempts, COUNT(DISTINCT user_id) AS participants, "
            "COALESCE(AVG(score), 0) AS avg_score, "
            "COALESCE(SUM(CASE WHEN score >= ? THEN 1 ELSE 0 END), 0) AS passed "
            "FROM bank_practice_sessions WHERE bank_id = ? AND status = 'submitted'",
            (_float(bank.get("pass_score")), int(bank_id)),
        ).fetchone()
        attempts = int(row["attempts"] or 0) if row else 0
        passed = int(row["passed"] or 0) if row else 0
        return {
            "attempts": attempts,
            "participants": int(row["participants"] or 0) if row else 0,
            "avg_score": round(_float(row["avg_score"]), 1) if row else 0,
            "pass_rate": round(passed / attempts * 100, 1) if attempts else 0,
        }
    finally:
        conn.close()


def admin_list(conn, status: str = "", keyword: str = "") -> list[dict]:
    where, args = [], []
    if status:
        where.append("b.status = ?")
        args.append(status)
    if keyword:
        like = f"%{keyword.strip()}%"
        where.append("(b.name LIKE ? OR b.cert_name LIKE ?)")
        args += [like, like]
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""
        SELECT b.*,
               (SELECT COUNT(*) FROM bank_questions q
                 WHERE q.bank_id = b.id AND q.status = 'active') AS question_count,
               (SELECT COUNT(*) FROM bank_questions q
                 WHERE q.bank_id = b.id AND q.status = 'active' AND q.type = 'single') AS actual_single,
               (SELECT COUNT(*) FROM bank_questions q
                 WHERE q.bank_id = b.id AND q.status = 'active' AND q.type = 'multiple') AS actual_multiple,
               (SELECT COUNT(*) FROM bank_practice_sessions s
                 WHERE s.bank_id = b.id AND s.status = 'submitted') AS attempts,
               (SELECT COUNT(DISTINCT s.user_id) FROM bank_practice_sessions s
                 WHERE s.bank_id = b.id AND s.status = 'submitted') AS participants,
               (SELECT COALESCE(AVG(s.score), 0) FROM bank_practice_sessions s
                 WHERE s.bank_id = b.id AND s.status = 'submitted') AS avg_score,
               (SELECT COUNT(*) FROM bank_practice_sessions s
                 WHERE s.bank_id = b.id AND s.status = 'submitted' AND s.score >= b.pass_score) AS passed
        FROM question_banks b
        {clause}
        ORDER BY b.sort_order, b.id DESC
        """,
        tuple(args),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["status_label"] = STATUS_LABELS.get(item["status"], item["status"])
        item["avg_score"] = round(_float(item["avg_score"]), 1)
        attempts = int(item["attempts"] or 0)
        item["pass_rate"] = round(
            (item.get("passed") or 0) / attempts * 100, 1
        ) if attempts else 0
        item["publish_errors"] = publish_check(item) if item["status"] != "published" else []
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 题库题目
# ---------------------------------------------------------------------------
def list_questions(bank_id: int, keyword: str = "") -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM bank_questions WHERE bank_id = ? AND status = 'active' "
            "ORDER BY sort_order, id",
            (int(bank_id),),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["options"] = _json_dict(item.get("options"))
            item["answer"] = _json_list(item.get("answer"))
            item["type_label"] = TYPE_LABELS.get(item["type"], item["type"])
            if keyword and keyword.strip().lower() not in (item["question"] + item.get("explanation", "")).lower():
                continue
            items.append(item)
        return items
    finally:
        conn.close()


def _validate_question(payload: dict, existing: Optional[dict] = None) -> dict:
    qtype = str(payload.get("type", existing["type"] if existing else "single") or "single").strip()
    if qtype not in QUESTION_TYPES:
        raise PracticeError("题型只能是单选题或多选题", 400, "VALIDATION")
    question = str(payload.get("question", existing["question"] if existing else "") or "").strip()
    if len(question) < 5:
        raise PracticeError("题干至少 5 个字符", 400, "VALIDATION")
    raw_options = payload.get("options", existing["options"] if existing else None)
    if isinstance(raw_options, str):
        raw_options = _json_dict(raw_options)
    if not isinstance(raw_options, dict):
        raise PracticeError("选项格式不正确", 400, "VALIDATION")
    options = {str(k).strip().upper(): str(v).strip() for k, v in raw_options.items() if str(v).strip()}
    if len(options) < 2:
        raise PracticeError("至少需要两个选项", 400, "VALIDATION")
    answer = _clean_letters(payload.get("answer", existing["answer"] if existing else None))
    if not answer:
        raise PracticeError("请设置正确答案", 400, "VALIDATION")
    if any(a not in options for a in answer):
        raise PracticeError("答案必须出现在选项中", 400, "VALIDATION")
    if qtype == "single" and len(answer) != 1:
        raise PracticeError("单选题只能有一个正确答案", 400, "VALIDATION")
    if qtype == "multiple" and len(answer) < 2:
        raise PracticeError("多选题至少需要两个正确答案", 400, "VALIDATION")
    difficulty = str(payload.get("difficulty", existing["difficulty"] if existing else "中等") or "中等").strip()
    if difficulty not in DIFFICULTIES:
        difficulty = "中等"
    explanation = str(payload.get("explanation", existing["explanation"] if existing else "") or "").strip()
    return {
        "type": qtype,
        "question": question,
        "options": options,
        "answer": answer,
        "explanation": explanation,
        "difficulty": difficulty,
    }


def add_question(bank_id: int, payload: dict) -> dict:
    bank = get_bank(bank_id, by_id=True)
    if not bank:
        raise PracticeError("题库不存在", 404, "NOT_FOUND")
    fields = _validate_question(payload)
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) AS n FROM bank_questions WHERE bank_id = ?",
            (int(bank_id),),
        ).fetchone()
        sort_order = _int(payload.get("sort_order"), _int(row["n"]) + 1 if row else 1)
        now = _now()
        cur = conn.execute(
            "INSERT INTO bank_questions (bank_id, type, question, options, answer, explanation, "
            "difficulty, sort_order, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (
                int(bank_id), fields["type"], fields["question"],
                json.dumps(fields["options"], ensure_ascii=False),
                json.dumps(fields["answer"], ensure_ascii=False),
                fields["explanation"], fields["difficulty"], sort_order, now, now,
            ),
        )
        qid = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return {"id": qid, "sort_order": sort_order}


def update_question(question_id: int, payload: dict) -> None:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM bank_questions WHERE id = ?", (int(question_id),)).fetchone()
        if not row:
            raise PracticeError("题目不存在", 404, "NOT_FOUND")
        existing = dict(row)
        existing["options"] = _json_dict(existing.get("options"))
        existing["answer"] = _json_list(existing.get("answer"))
        fields = _validate_question(payload, existing)
        conn.execute(
            "UPDATE bank_questions SET type=?, question=?, options=?, answer=?, "
            "explanation=?, difficulty=?, sort_order=?, updated_at=? WHERE id=?",
            (
                fields["type"], fields["question"],
                json.dumps(fields["options"], ensure_ascii=False),
                json.dumps(fields["answer"], ensure_ascii=False),
                fields["explanation"], fields["difficulty"],
                _int(payload.get("sort_order", existing.get("sort_order"))),
                _now(), int(question_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def delete_question(question_id: int) -> None:
    conn = _conn()
    try:
        row = conn.execute("SELECT id FROM bank_questions WHERE id = ?", (int(question_id),)).fetchone()
        if not row:
            raise PracticeError("题目不存在", 404, "NOT_FOUND")
        conn.execute("DELETE FROM bank_questions WHERE id = ?", (int(question_id),))
        conn.commit()
    finally:
        conn.close()


def import_questions(bank_id: int, text: str, fmt: str = "markdown") -> dict:
    """批量导入：复用每日答题的 Markdown / JSON 解析器，只接收单选与多选。"""
    import parser

    bank = get_bank(bank_id, by_id=True)
    if not bank:
        raise PracticeError("题库不存在", 404, "NOT_FOUND")
    text = (text or "").strip()
    if not text:
        raise PracticeError("请粘贴题目内容或上传文件", 400, "VALIDATION")
    result = parser.parse_auto(text, fmt)
    inserted, failed = 0, list(result.failed)
    for set_index in sorted(result.sets):
        for q in result.sets[set_index]:
            if q.q_type not in QUESTION_TYPES:
                failed.append({"question": q.question[:40], "reason": "模拟题库只支持单选题和多选题"})
                continue
            answer = _clean_letters(q.answer)
            if q.q_type == "single" and len(answer) != 1:
                failed.append({"question": q.question[:40], "reason": "单选题答案必须唯一"})
                continue
            if q.q_type == "multiple" and len(answer) < 2:
                failed.append({"question": q.question[:40], "reason": "多选题至少两个答案"})
                continue
            try:
                add_question(bank_id, {
                    "type": q.q_type, "question": q.question, "options": q.options,
                    "answer": answer, "explanation": q.explanation or "",
                })
                inserted += 1
            except PracticeError as exc:
                failed.append({"question": q.question[:40], "reason": exc.message})
    return {"inserted": inserted, "failed": failed}


# ---------------------------------------------------------------------------
# 每日答题题源：直接复用「模拟题库」（只取 published 状态）
# ---------------------------------------------------------------------------
IMPORT_BANK_CREATOR = "system:import"


def _published_question_ids(conn, category_id: int = 0) -> list[int]:
    """取已发布模拟题库中的可用题目 id（category_id=0 表示全站）。"""
    where = ["b.status = 'published'", "bq.status = 'active'"]
    args: list = []
    if category_id:
        where.append("b.category_id = ?")
        args.append(int(category_id))
    rows = conn.execute(
        "SELECT bq.id FROM bank_questions bq JOIN question_banks b ON b.id = bq.bank_id "
        "WHERE " + " AND ".join(where) + " ORDER BY bq.id",
        tuple(args),
    ).fetchall()
    return [int(r["id"]) for r in rows]


def count_bank_questions(category_id: int = 0) -> int:
    """统计已发布模拟题库的题目数（category_id=0 为全站）。

    Args:
        category_id: 认证分类 id；0 表示不按分类过滤。

    Returns:
        题目数量。
    """
    conn = _conn()
    try:
        return len(_published_question_ids(conn, category_id))
    finally:
        conn.close()


def _row_from_bank_question(row) -> dict:
    """把 bank_questions 行转成每日答题下发 / 判分所需的形状。

    与 `bank._question_row` 对齐：options 解析为 dict，answer 解析为
    answer_list（大写字母列表），这样 quiz_service 无需区分题目来源。
    """
    data = dict(row)
    data["options"] = _json_dict(data.get("options"))
    data["answer_list"] = _clean_letters(data.get("answer"))
    return data


def random_questions_for_category(category_id: int, limit: int = 10) -> tuple[list[dict], str]:
    """从模拟题库随机抽题；本认证不足时回退到全站已发布模拟题库。

    Args:
        category_id: 认证分类 id。
        limit: 需要抽取的题量。

    Returns:
        (题目列表, 题池说明)。题池取值 ``category``（本认证模拟题库）或
        ``global``（全站模拟题库）。
    """
    conn = _conn()
    try:
        local = _published_question_ids(conn, category_id)
        if len(local) >= limit:
            ids, pool = local, "category"
        else:
            ids, pool = _published_question_ids(conn, 0), "global"
        if not ids:
            return [], pool
        picked = random.sample(ids, min(limit, len(ids)))
        marks = ",".join("?" for _ in picked)
        rows = conn.execute(
            f"SELECT * FROM bank_questions WHERE id IN ({marks})", tuple(picked)
        ).fetchall()
        by_id = {int(r["id"]): r for r in rows}
        return [_row_from_bank_question(by_id[i]) for i in picked if i in by_id], pool
    finally:
        conn.close()


def questions_by_ids(ids: Iterable[int]) -> dict[int, dict]:
    """按 id 批量读取模拟题库题目，形状与 ``bank.questions_by_ids`` 一致。

    Args:
        ids: 题目 id 集合（允许字符串数字）。

    Returns:
        ``{题目 id: 题目字典}``，缺失的 id 不会出现在结果中。
    """
    clean: list[int] = []
    for raw in ids:
        try:
            clean.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not clean:
        return {}
    marks = ",".join("?" for _ in clean)
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM bank_questions WHERE id IN ({marks})", tuple(clean)
        ).fetchall()
        return {int(r["id"]): _row_from_bank_question(r) for r in rows}
    finally:
        conn.close()


def import_paper_as_bank(
    category: dict,
    text: str,
    fmt: str = "markdown",
    filename: str = "",
    created_by: str = IMPORT_BANK_CREATOR,
) -> dict:
    """把一份试卷导入为一个新的模拟题库，并直接发布。

    后台「题库导入」调用：一次上传 = 一个可练习的模拟卷，题目因此同时
    出现在认证详情页的模拟题库列表与每日答题的抽题池中。

    Args:
        category: `bank.get_category` 返回的认证分类字典。
        text: 试卷正文。
        fmt: markdown / json。
        filename: 原始文件名，用于生成题库名称。
        created_by: 创建者标识，自动导入统一为 ``system:import``。

    Returns:
        含 created / inserted / failed / bank / publish_errors 的字典。
    """
    import parser

    result = parser.parse_auto(text, fmt)
    valid: list[tuple] = []
    failed = list(result.failed)
    for set_index in sorted(result.sets):
        for q in result.sets[set_index]:
            if q.q_type not in QUESTION_TYPES:
                failed.append({"question": q.question[:40], "reason": "模拟题库只支持单选题和多选题"})
                continue
            answer = _clean_letters(q.answer)
            if q.q_type == "single" and len(answer) != 1:
                failed.append({"question": q.question[:40], "reason": "单选题答案必须唯一"})
                continue
            if q.q_type == "multiple" and len(answer) < 2:
                failed.append({"question": q.question[:40], "reason": "多选题至少两个答案"})
                continue
            valid.append((q, answer))
    if not valid:
        return {"created": False, "inserted": 0, "failed": failed, "bank": None,
                "publish_errors": [], "reason": "没有可导入的题目"}

    single = sum(1 for q, _ in valid if q.q_type == "single")
    multiple = len(valid) - single
    stem = ""
    if filename:
        stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if "." in stem:
            stem = stem.rsplit(".", 1)[0]
    name = (stem.strip() or f"{category['cert_name']} 模拟卷")[:80]
    # 同一份文件重复上传时替换旧的自动题库，避免模拟题库列表越堆越多。
    # 只处理 created_by='system:import' 的库，不会误删管理员手工建的题库。
    if filename:
        conn = _conn()
        try:
            dup = conn.execute(
                "SELECT id FROM question_banks WHERE category_id = ? AND name = ? AND created_by = ?",
                (int(category["id"]), name, IMPORT_BANK_CREATOR),
            ).fetchone()
        finally:
            conn.close()
        if dup:
            try:
                delete_bank(int(dup["id"]))
            except PracticeError:
                pass  # 已有练习记录时保留旧库，另建一份

    payload = {
        "name": name,
        "cert_path": category["path_slug"],
        "level_tag": "基础",
        "difficulty": "中等",
        # 自动建库不写「系统生成」类文案：它会原样渲染在前台认证页的题库卡片上，
        # 对用户没有信息量。留空即可，管理员可在后台「题库管理」补真正的简介。
        "description": "",
        "rule_code": RULE_CODE_CUSTOM,
        "total_questions": len(valid),
        "single_count": single,
        "multiple_count": multiple,
        "total_score": len(valid),
        "pass_score": int(round(len(valid) * 0.8)),
        "duration_minutes": max(10, min(600, int(round(len(valid) * 1.2)))),
    }
    created = create_bank(payload, created_by=created_by)
    for q, answer in valid:
        add_question(created["id"], {
            "type": q.q_type,
            "question": q.question,
            "options": q.options,
            "answer": answer,
            "explanation": q.explanation or "",
        })
    fresh = get_bank(created["id"], by_id=True)
    errors = publish_check(fresh)
    if not errors:
        set_bank_status(created["id"], "published")
        fresh = get_bank(created["id"], by_id=True)
    return {"created": True, "inserted": len(valid), "failed": failed,
            "bank": fresh, "publish_errors": errors}


def delete_auto_import_banks(category_id: int) -> int:
    """删除该认证下由「题库导入」自动创建的模拟题库（覆盖导入时清理旧卷）。

    Returns:
        实际删除的题库数量。
    """
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id FROM question_banks WHERE category_id = ? AND created_by = ?",
            (int(category_id), IMPORT_BANK_CREATOR),
        ).fetchall()
    finally:
        conn.close()
    removed = 0
    for row in rows:
        try:
            delete_bank(int(row["id"]))
            removed += 1
        except PracticeError:
            continue
    return removed


# ---------------------------------------------------------------------------
# 练习场次
# ---------------------------------------------------------------------------
def _remaining_seconds(session: dict) -> int:
    deadline = _parse_dt(session.get("deadline_at"))
    if not deadline:
        return 0
    delta = int((deadline - datetime.now()).total_seconds())
    return max(0, delta)


def _session_row(conn, token: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM bank_practice_sessions WHERE token = ?", (str(token or ""),)
    ).fetchone()
    return dict(row) if row else None


def _load_questions(conn, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM bank_questions WHERE id IN ({marks})", tuple(ids)
    ).fetchall()
    out = {}
    for row in rows:
        item = dict(row)
        item["options"] = _json_dict(item.get("options"))
        item["answer"] = _json_list(item.get("answer"))
        out[int(item["id"])] = item
    return out


def _check_daily_starts(conn, user_id: int, bank_id: int) -> None:
    """限制单用户单日对同一题库的开考场次，挡住批量开考抓题。"""
    since = _now()[:10] + " 00:00:00"
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM bank_practice_sessions "
        "WHERE user_id = ? AND bank_id = ? AND started_at >= ?",
        (int(user_id), int(bank_id), since),
    ).fetchone()
    used = _int(row["c"]) if row else 0
    if used >= MAX_DAILY_STARTS_PER_BANK:
        raise PracticeError(
            f"今日对本题库已开考 {used} 次，达到上限（{MAX_DAILY_STARTS_PER_BANK} 次），"
            "请明天再来或先练其他题库",
            429, "DAILY_LIMIT",
        )


def start_session(user_id: int, bank: dict, *, restart: bool = False) -> dict:
    """开始 / 继续练习。

    - 有未超时的进行中场次且未要求重新开始 → 直接续答；
    - 已超时 → 先按超时交卷，再视情况新建或返回结果；
    - 题库无题 → 400；当日开考次数超限 → 429。

    抽题口径：先从题库全部有效题中**随机取样**，再按 `total_questions`
    （每卷题量）截断。顺序不能反：先截断再洗牌只会打乱前 N 题的顺序，
    后面扩容进来的新题永远考不到。
    """
    if bank.get("status") == "disabled":
        raise PracticeError("该题库已停用，暂时不能开始新练习", 403, "BANK_DISABLED")
    _sweep_if_due()
    finalize_expired(user_id=user_id, bank_id=bank["id"])
    conn = _conn()
    try:
        if not restart:
            row = conn.execute(
                "SELECT * FROM bank_practice_sessions WHERE user_id = ? AND bank_id = ? "
                "AND status = 'ongoing' ORDER BY id DESC LIMIT 1",
                (int(user_id), int(bank["id"])),
            ).fetchone()
            if row:
                return _session_payload(conn, dict(row), bank)
        questions = conn.execute(
            "SELECT id FROM bank_questions WHERE bank_id = ? AND status = 'active' "
            "ORDER BY sort_order, id",
            (int(bank["id"]),),
        ).fetchall()
        ids = [int(r["id"]) for r in questions]
        if not ids:
            raise PracticeError("该题库还没有题目，请联系管理员补充后再练习", 400, "NO_QUESTIONS")
        random.SystemRandom().shuffle(ids)
        declared = _int(bank.get("total_questions"))
        if declared and len(ids) > declared:
            ids = ids[:declared]
        _check_daily_starts(conn, int(user_id), int(bank["id"]))
        token = secrets.token_urlsafe(24)
        now_dt = datetime.now()
        started = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        duration_min = _int(bank.get("duration_minutes"), 0)
        deadline = (now_dt + timedelta(minutes=duration_min)).strftime("%Y-%m-%d %H:%M:%S") if duration_min > 0 else ""
        cur = conn.execute(
            "INSERT INTO bank_practice_sessions (token, bank_id, user_id, question_ids, "
            "answers, total, answered, correct, score, accuracy, status, current_index, "
            "started_at, deadline_at, finished_at, duration, submit_reason, created_at) "
            "VALUES (?, ?, ?, ?, '{}', ?, 0, 0, 0, 0, 'ongoing', 0, ?, ?, '', 0, '', ?)",
            (token, int(bank["id"]), int(user_id),
             json.dumps(ids), len(ids), started, deadline, started),
        )
        session_id = int(cur.lastrowid)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM bank_practice_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return _session_payload(conn, dict(row), bank)
    finally:
        conn.close()


def _session_payload(conn, session: dict, bank: Optional[dict] = None) -> dict:
    if bank is None:
        row = conn.execute("SELECT * FROM question_banks WHERE id = ?", (session["bank_id"],)).fetchone()
        bank = dict(row) if row else {}
    ids = [int(x) for x in _json_list(session.get("question_ids"))]
    qmap = _load_questions(conn, ids)
    questions = []
    for index, qid in enumerate(ids):
        q = qmap.get(qid)
        if not q:
            continue
        questions.append({
            "id": qid,
            "index": index,
            "type": q["type"],
            "type_label": TYPE_LABELS.get(q["type"], q["type"]),
            "question": q["question"],
            "options": q["options"],
            "difficulty": q["difficulty"],
        })
    answers = _json_dict(session.get("answers"))
    payload = {
        "token": session["token"],
        "status": session["status"],
        "total": _int(session.get("total")),
        "answered": _int(session.get("answered")),
        "current_index": _int(session.get("current_index")),
        "started_at": session.get("started_at") or "",
        "deadline_at": session.get("deadline_at") or "",
        "remaining_seconds": _remaining_seconds(session) if session["status"] == "ongoing" else 0,
        "duration_minutes": _int(bank.get("duration_minutes")),
        "submit_reason": session.get("submit_reason") or "",
        "bank": {
            "id": bank.get("id"), "slug": bank.get("slug"), "name": bank.get("name"),
            "cert_name": bank.get("cert_name"), "level_tag": bank.get("level_tag"),
            "difficulty": bank.get("difficulty"), "rule_name": bank.get("rule_name"),
            "total_score": _float(bank.get("total_score")),
            "pass_score": _float(bank.get("pass_score")),
            "duration_minutes": _int(bank.get("duration_minutes")),
            "question_count": _int(bank.get("total_questions")),
        },
        "questions": questions,
        "answers": {str(k): _clean_letters(v) for k, v in answers.items()},
    }
    if session["status"] == "submitted":
        payload["result"] = _result_payload(conn, session, bank, qmap)
    return payload


def get_session(user_id: int, token: str) -> dict:
    conn = _conn()
    try:
        session = _session_row(conn, token)
        if not session or int(session["user_id"]) != int(user_id):
            raise PracticeError("练习记录不存在", 404, "NOT_FOUND")
        if session["status"] == "ongoing":
            finalize_expired(user_id=user_id, session_ids=[int(session["id"])])
            # 结束本连接开始读事务时的旧快照，否则 MySQL REPEATABLE READ 下
            # 重新 SELECT 仍会读到「未交卷」的旧行。
            conn.rollback()
            session = _session_row(conn, token)
        return _session_payload(conn, session)
    finally:
        conn.close()


def save_answer(user_id: int, token: str, question_id: int, raw_answer) -> dict:
    """保存一题的作答。考试中只回已答题数，绝不回对错，避免泄题。"""
    conn = _conn()
    try:
        session = _session_row(conn, token)
        if not session or int(session["user_id"]) != int(user_id):
            raise PracticeError("练习记录不存在", 404, "NOT_FOUND")
        if session["status"] != "ongoing":
            raise PracticeError("本场练习已交卷", 409, "SESSION_SUBMITTED")
        if _remaining_seconds(session) <= 0 and session.get("deadline_at"):
            finalize_expired(user_id=user_id, session_ids=[int(session["id"])])
            conn.rollback()  # 同 get_session：丢弃旧快照后再回读结算结果
            session = _session_row(conn, token)
            return _session_payload(conn, session)

        ids = [int(x) for x in _json_list(session.get("question_ids"))]
        qid = int(question_id)
        if qid not in ids:
            raise PracticeError("题目不属于本场练习", 400, "BAD_QUESTION")
        qrow = conn.execute(
            "SELECT * FROM bank_questions WHERE id = ?", (qid,)
        ).fetchone()
        if not qrow:
            raise PracticeError("题目不存在", 404, "NOT_FOUND")
        q = dict(qrow)
        options = _json_dict(q.get("options"))
        submitted = [a for a in _clean_letters(raw_answer) if a in options]

        now = _now()
        answers = _json_dict(session.get("answers"))
        answers[str(qid)] = submitted
        correct_answer = set(_json_list(q.get("answer")))
        is_correct = bool(submitted) and set(submitted) == correct_answer

        # 进度按「每题最后一次作答」增减，重做答对后错题数会自动减少。
        prev = conn.execute(
            "SELECT is_correct FROM bank_practice_answers WHERE user_id = ? AND bank_id = ? "
            "AND question_id = ? ORDER BY id DESC LIMIT 1",
            (int(user_id), int(session["bank_id"]), qid),
        ).fetchone()
        old = None if prev is None else int(prev["is_correct"])
        _touch_progress(conn, int(user_id), int(session["bank_id"]), old, is_correct, now)

        row = conn.execute(
            "SELECT id FROM bank_practice_answers WHERE session_id = ? AND question_id = ?",
            (int(session["id"]), qid),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE bank_practice_answers SET user_answer=?, is_correct=?, answered_at=? WHERE id=?",
                (json.dumps(submitted), 1 if is_correct else 0, now, int(row["id"])),
            )
        else:
            conn.execute(
                "INSERT INTO bank_practice_answers (session_id, bank_id, user_id, question_id, "
                "user_answer, is_correct, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(session["id"]), int(session["bank_id"]), int(user_id), qid,
                 json.dumps(submitted), 1 if is_correct else 0, now),
            )
        answered = sum(1 for v in answers.values() if v)
        conn.execute(
            "UPDATE bank_practice_sessions SET answers=?, answered=?, current_index=? WHERE id=?",
            (json.dumps(answers, ensure_ascii=False), answered, ids.index(qid), int(session["id"])),
        )
        conn.commit()
        return {
            "token": token,
            "answered": answered,
            "total": _int(session.get("total")),
            "saved_at": now,
        }
    finally:
        conn.close()


def _touch_progress(conn, user_id: int, bank_id: int, old_correct: Optional[int],
                    new_correct: bool, now: str) -> None:
    row = conn.execute(
        "SELECT * FROM bank_practice_progress WHERE user_id = ? AND bank_id = ?",
        (int(user_id), int(bank_id)),
    ).fetchone()
    if row:
        progress = dict(row)
        answered = _int(progress["answered_count"])
        correct = _int(progress["correct_count"])
        wrong = _int(progress["wrong_count"])
        if old_correct is None:
            answered += 1
            if new_correct:
                correct += 1
            else:
                wrong += 1
        elif old_correct == 1 and not new_correct:
            correct = max(0, correct - 1)
            wrong += 1
        elif old_correct == 0 and new_correct:
            wrong = max(0, wrong - 1)
            correct += 1
        conn.execute(
            "UPDATE bank_practice_progress SET answered_count=?, correct_count=?, "
            "wrong_count=?, last_practiced_at=?, updated_at=? WHERE id=?",
            (answered, correct, wrong, now, now, int(progress["id"])),
        )
        return
    conn.execute(
        "INSERT INTO bank_practice_progress (user_id, bank_id, answered_count, correct_count, "
        "wrong_count, best_score, last_score, last_session_id, last_practiced_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?, 0, 0, NULL, ?, ?)",
        (int(user_id), int(bank_id), 1 if new_correct else 0,
         0 if new_correct else 1, now, now),
    )


def _grade(session: dict, qmap: dict[int, dict]) -> dict:
    ids = [int(x) for x in _json_list(session.get("question_ids"))]
    answers = _json_dict(session.get("answers"))
    correct = 0
    for qid in ids:
        q = qmap.get(qid)
        if not q:
            continue
        submitted = set(_clean_letters(answers.get(str(qid))))
        if submitted and submitted == set(_json_list(q.get("answer"))):
            correct += 1
    total = len(ids)
    score = round(correct * _float(session.get("_total_score"), 100) / total, 1) if total else 0
    return {"correct": correct, "total": total, "score": score,
            "accuracy": round(correct / total * 100, 1) if total else 0}


def _result_payload(conn, session: dict, bank: dict, qmap: dict[int, dict]) -> dict:
    """交卷结果。

    安全约束：**未作答的题不下发答案与解析**。否则「开考 → 交白卷 → 立刻交卷」
    就能零成本把整套题库的答案导出（交卷没有最低完成度要求）。
    已作答的题正常给答案与解析，用户仍可逐题复盘。
    """
    ids = [int(x) for x in _json_list(session.get("question_ids"))]
    answers = _json_dict(session.get("answers"))
    items = []
    for index, qid in enumerate(ids):
        q = qmap.get(qid)
        if not q:
            continue
        submitted = _clean_letters(answers.get(str(qid)))
        answer_key = _json_list(q.get("answer"))
        revealed = bool(submitted)
        is_correct = revealed and set(submitted) == set(answer_key)
        items.append({
            "id": qid,
            "index": index,
            "type": q["type"],
            "type_label": TYPE_LABELS.get(q["type"], q["type"]),
            "question": q["question"],
            "options": _json_dict(q.get("options")),
            "your_answer": submitted,
            "revealed": revealed,
            "correct_answer": answer_key if revealed else [],
            "correct_text": _answer_text(_json_dict(q.get("options")), answer_key) if revealed else "",
            "is_correct": is_correct,
            "explanation_html": _explanation_html(q.get("explanation") or "") if revealed else "",
            "difficulty": q.get("difficulty"),
        })
    score = _float(session.get("score"))
    pass_score = _float(bank.get("pass_score"), 60)
    # 刚结算完的场次由 finalize 直接把积分带回来：MySQL 默认 REPEATABLE READ，
    # 交卷连接还停留在旧快照上，再查一次 bank_exam_awards 会看不到本事务刚提交的行。
    if "_points_awarded" in session:
        points_awarded = _int(session["_points_awarded"])
    else:
        award = conn.execute(
            "SELECT points FROM bank_exam_awards WHERE session_id = ?", (int(session["id"]),)
        ).fetchone()
        points_awarded = _int(award["points"]) if award else 0
    return {
        "session_token": session["token"],
        "score": score,
        "total_score": _float(bank.get("total_score"), 100),
        "pass_score": pass_score,
        "passed": score >= pass_score,
        "correct": _int(session.get("correct")),
        "total": _int(session.get("total")),
        "accuracy": _float(session.get("accuracy")),
        "duration": _int(session.get("duration")),
        "finished_at": session.get("finished_at") or "",
        "submit_reason": session.get("submit_reason") or "",
        "points_awarded": points_awarded,
        "wrong_count": sum(1 for item in items if not item["is_correct"]),
        "unanswered_count": sum(1 for item in items if not item["your_answer"]),
        "items": items,
    }


def _answer_text(options: dict, answer: list) -> str:
    parts = []
    for letter in answer:
        text = options.get(letter)
        parts.append(f"{letter}. {text}" if text else letter)
    return "；".join(parts)


def _explanation_html(text: str) -> str:
    import quiz_logic

    return quiz_logic.explanation_to_html(text) if text else ""


def _award_exam_points(conn, session: dict, stats: dict, finished_at: str) -> int:
    """把一场模拟考折算成积分并累加到日 / 周 / 月榜，返回本次积分。

    规则（与 README「积分规则」一致）：
    - 完成率 = 已作答题数 / 本卷总题数，低于 60% 视为无效练习，不计分；
    - 积分 = 正确率 × `EXAM_POINTS_PER_SCORE`（100% 正确 → 200 积分），
      用正确率而不是原始分，避免不同满分（30 / 75 / 100 分卷）之间不可比；
    - 同一用户 × 同一题库 × 同一自然日只计一次，重复练习不叠加。

    幂等：`finalize` 里的条件更新保证只有一次调用能走到这里，
    `bank_exam_awards` 的唯一键再兜一层并发。
    """
    total = _int(session.get("total"))
    if total <= 0:
        return 0
    answered = sum(1 for value in _json_dict(session.get("answers")).values() if value)
    if answered / total < EXAM_MIN_COMPLETION:
        return 0
    accuracy = _float(stats.get("accuracy"))
    points = int(round(accuracy * EXAM_POINTS_PER_SCORE))
    if points <= 0:
        return 0
    earned_date = (finished_at or _now())[:10]
    exists = conn.execute(
        "SELECT id FROM bank_exam_awards WHERE user_id = ? AND bank_id = ? AND earned_date = ?",
        (int(session["user_id"]), int(session["bank_id"]), earned_date),
    ).fetchone()
    if exists:
        return 0
    try:
        conn.execute(
            "INSERT INTO bank_exam_awards (user_id, bank_id, session_id, earned_date, "
            "score, points, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(session["user_id"]), int(session["bank_id"]), int(session["id"]),
             earned_date, _float(stats.get("score")), points, finished_at or _now()),
        )
    except db.IntegrityError:
        return 0
    # 与判分共用同一事务：积分要么和成绩一起落库，要么一起回滚。
    # 局部导入避免模块级循环依赖（bank 依赖 models，不反向依赖本模块）。
    import bank as bank_module

    for period_type in ("day", "week", "month"):
        bank_module.bump_ranking(period_type, int(session["user_id"]), accuracy,
                                 earned_date, points=points, conn=conn)
    logger.info("模拟考积分入账 user=%s bank=%s session=%s score=%s points=%s",
                session["user_id"], session["bank_id"], session["id"],
                stats.get("score"), points)
    return points


def finalize(session: dict, *, reason: str = "manual") -> dict:
    """判分并落库；重复调用幂等。返回更新后的 session。"""
    if session.get("status") == "submitted":
        return session
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM bank_practice_sessions WHERE id = ?", (int(session["id"]),)
        ).fetchone()
        if not row:
            raise PracticeError("练习记录不存在", 404, "NOT_FOUND")
        session = dict(row)
        bank_row = conn.execute(
            "SELECT * FROM question_banks WHERE id = ?", (int(session["bank_id"]),)
        ).fetchone()
        bank = dict(bank_row) if bank_row else {}
        ids = [int(x) for x in _json_list(session.get("question_ids"))]
        qmap = _load_questions(conn, ids)
        session["_total_score"] = _float(bank.get("total_score"), 100)
        stats = _grade(session, qmap)
        now_dt = datetime.now()
        started = _parse_dt(session.get("started_at")) or now_dt
        actual_seconds = max(0, int((now_dt - started).total_seconds()))
        limit_seconds = _int(bank.get("duration_minutes")) * 60
        if reason == "timeout" and limit_seconds:
            duration = limit_seconds
        else:
            duration = min(actual_seconds, limit_seconds) if limit_seconds else actual_seconds
        finished = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        # 只有「第一个把 ongoing 改成 submitted」的请求有资格写成绩与积分：
        # 用户双击交卷 / 交卷与超时回收同时触发时，后到的请求直接回读结果，
        # 不会重复加分，也不会把进度统计加两遍。
        changed = conn.execute(
            "UPDATE bank_practice_sessions SET status='submitted', correct=?, score=?, "
            "accuracy=?, duration=?, finished_at=?, submit_reason=? "
            "WHERE id=? AND status='ongoing'",
            (stats["correct"], stats["score"], stats["accuracy"], duration,
             finished, reason, int(session["id"])),
        )
        if not changed.rowcount:
            conn.rollback()
            row = conn.execute(
                "SELECT * FROM bank_practice_sessions WHERE id = ?", (int(session["id"]),)
            ).fetchone()
            return dict(row) if row else session
        progress = conn.execute(
            "SELECT * FROM bank_practice_progress WHERE user_id = ? AND bank_id = ?",
            (int(session["user_id"]), int(session["bank_id"])),
        ).fetchone()
        if progress:
            conn.execute(
                "UPDATE bank_practice_progress SET last_score=?, best_score=?, "
                "last_session_id=?, last_practiced_at=?, updated_at=? WHERE id=?",
                (stats["score"], max(_float(progress["best_score"]), stats["score"]),
                 int(session["id"]), finished, finished, int(progress["id"])),
            )
        else:
            conn.execute(
                "INSERT INTO bank_practice_progress (user_id, bank_id, answered_count, "
                "correct_count, wrong_count, best_score, last_score, last_session_id, "
                "last_practiced_at, updated_at) VALUES (?, ?, 0, 0, 0, ?, ?, ?, ?, ?)",
                (int(session["user_id"]), int(session["bank_id"]), stats["score"],
                 stats["score"], int(session["id"]), finished, finished),
            )
        awarded = _award_exam_points(conn, session, stats, finished)
        conn.commit()
        session.update({
            "status": "submitted", "correct": stats["correct"], "score": stats["score"],
            "accuracy": stats["accuracy"], "duration": duration,
            "finished_at": finished, "submit_reason": reason,
            "_points_awarded": awarded,
        })
        return session
    finally:
        conn.close()


def submit_session(user_id: int, token: str, reason: str = "manual") -> dict:
    conn = _conn()
    try:
        session = _session_row(conn, token)
        if not session or int(session["user_id"]) != int(user_id):
            raise PracticeError("练习记录不存在", 404, "NOT_FOUND")
        if session["status"] == "submitted":
            return _session_payload(conn, session)
        expired = bool(session.get("deadline_at")) and _remaining_seconds(session) <= 0
        session = finalize(session, reason="timeout" if expired else (reason or "manual"))
        # finalize 用的是另一条连接；本连接从开头 SELECT 起就持有旧快照，
        # 先结束它，_result_payload 才能读到刚写入的积分流水（MySQL RR）。
        conn.rollback()
        return _session_payload(conn, session)
    finally:
        conn.close()


def finalize_expired(user_id: int = 0, bank_id: int = 0,
                     session_ids: Optional[list[int]] = None) -> int:
    """把已超过截止时间的进行中练习按「超时交卷」结算。返回处理条数。"""
    conn = _conn()
    try:
        where = ["status = 'ongoing'", "deadline_at <> ''", "deadline_at < ?"]
        args: list = [_now()]
        if user_id:
            where.append("user_id = ?")
            args.append(int(user_id))
        if bank_id:
            where.append("bank_id = ?")
            args.append(int(bank_id))
        if session_ids:
            marks = ",".join("?" for _ in session_ids)
            where.append(f"id IN ({marks})")
            args += [int(x) for x in session_ids]
        rows = conn.execute(
            "SELECT * FROM bank_practice_sessions WHERE " + " AND ".join(where) + " LIMIT 50",
            tuple(args),
        ).fetchall()
        sessions = [dict(r) for r in rows]
    finally:
        conn.close()
    count = 0
    for session in sessions:
        try:
            finalize(session, reason="timeout")
            count += 1
        except PracticeError:
            continue
    return count


def sweep_expired(max_batches: int = 20) -> int:
    """全局回收超时场次（不限用户），返回结算条数。

    `finalize_expired` 单次只取 50 条，积压多时要循环调用；返回小于 50
    说明已扫完，最多扫 `max_batches` 轮，避免一次性长事务拖慢响应。
    """
    total = 0
    for _ in range(max_batches):
        count = finalize_expired()
        total += count
        if count < 50:
            break
    return total


def _sweep_if_due() -> None:
    """每个进程每小时最多兜底回收一次；失败只记日志，不影响用户开考。"""
    global _last_sweep_at
    now = time.monotonic()
    if now - _last_sweep_at < SWEEP_INTERVAL_SECONDS:
        return
    _last_sweep_at = now
    try:
        sweep_expired()
    except Exception:  # noqa: BLE001 - 后台兜底任务，不能让回收失败挡住开考
        logger.warning("超时场次回收失败", exc_info=True)


