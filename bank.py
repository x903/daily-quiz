"""认证题库网数据访问层：分类、五段式介绍、题库、答题记录、荣誉与排行榜。

与 `models.py` 的分工：
- `models.py` 负责存量「按日期题库」域（daily_sets / daily_records / 错题本 / 积分流水）；
- `bank.py` 负责 v2「认证题库」域，表结构见 SCHEMA 与 schema_mysql.sql 末尾。

所有 SQL 使用 `?` 占位符，由 db.MySQLConnection 自动转换为 `%s`；
随机函数、自增主键等少量方言差异集中在本模块内部处理。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

import catalog
import db
import models
import parser


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rand_func() -> str:
    return "RAND()" if db.backend() == "mysql" else "RANDOM()"


def _no_set() -> None:
    """认证题库题目不挂 daily_set，统一写入 NULL（两套后端都已允许为空）。"""
    return None


def _conn():
    return models.get_conn()


# ---------------------------------------------------------------------------
# 初始化：按 catalog.py 幂等同步分类与介绍
# ---------------------------------------------------------------------------

def sync_catalog(conn) -> dict:
    """把 catalog.py 的认证树写入 cert_categories，并为缺失项补默认介绍。

    - 分类：按 path_slug 幂等 upsert（结构性字段以代码为准；SEO 字段保留后台改动）；
    - 介绍：只在缺失时插入，**绝不覆盖**管理员已编辑的正文。
    """
    now = _now()
    created = updated = 0
    for order, leaf in enumerate(catalog.all_leaves()):
        row = conn.execute(
            "SELECT id FROM cert_categories WHERE path_slug = ?", (leaf["path"],)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE cert_categories SET vendor_name = ?, direction_name = ?, "
                "level_name = ?, cert_name = ?, exam_code = ?, difficulty = ?, "
                "sort_order = ?, updated_at = ? WHERE id = ?",
                (
                    leaf["vendor_name"], leaf["direction_name"], leaf["level_name"],
                    leaf["cert_name"], leaf["exam_code"], leaf["difficulty"],
                    order, now, row["id"],
                ),
            )
            category_id = row["id"]
            updated += 1
        else:
            cur = conn.execute(
                "INSERT INTO cert_categories (vendor_slug, vendor_name, direction_slug, "
                "direction_name, level_slug, level_name, path_slug, cert_name, exam_code, "
                "difficulty, sort_order, status, seo_title, seo_keywords, seo_description, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
                (
                    leaf["vendor_slug"], leaf["vendor_name"], leaf["direction_slug"],
                    leaf["direction_name"], leaf["level_slug"], leaf["level_name"],
                    leaf["path"], leaf["cert_name"], leaf["exam_code"], leaf["difficulty"],
                    order, leaf["seo_title"], leaf["keywords"], leaf["seo_description"],
                    now, now,
                ),
            )
            category_id = cur.lastrowid
            created += 1

        intro = conn.execute(
            "SELECT id FROM cert_intros WHERE category_id = ?", (category_id,)
        ).fetchone()
        if not intro:
            conn.execute(
                "INSERT INTO cert_intros (category_id, positioning, audience, exam_topics, "
                "difficulty, advice, summary, source, updated_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)",
                (
                    category_id, leaf["positioning"], leaf["audience"], leaf["topics"],
                    leaf["difficulty"], leaf["advice"], leaf["summary"][:500],
                    leaf.get("source", ""), now, now,
                ),
            )
    return {"created": created, "updated": updated}


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------

_CAT_COLUMNS = (
    "id, vendor_slug, vendor_name, direction_slug, direction_name, level_slug, "
    "level_name, path_slug, cert_name, exam_code, difficulty, sort_order, status, "
    "seo_title, seo_keywords, seo_description, created_at, updated_at"
)


def list_categories(keyword: str = "", status: str = "", vendor: str = "") -> list[dict]:
    """分类列表，附带题量与是否已配置介绍。"""
    where, args = [], []
    if keyword:
        like = f"%{keyword}%"
        where.append("(c.cert_name LIKE ? OR c.path_slug LIKE ? OR c.exam_code LIKE ?)")
        args += [like, like, like]
    if status:
        where.append("c.status = ?")
        args.append(status)
    if vendor:
        where.append("c.vendor_slug = ?")
        args.append(vendor)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    conn = _conn()
    try:
        rows = conn.execute(
            f"""
            SELECT c.*,
                   (SELECT COUNT(*) FROM questions q
                     WHERE q.cert_category_id = c.id AND q.status = 'active') AS question_count,
                   (SELECT COUNT(*) FROM cert_intros i WHERE i.category_id = c.id) AS has_intro
            FROM cert_categories c
            {clause}
            ORDER BY c.sort_order, c.id
            """,
            tuple(args),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_category(category_id: int) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM cert_categories WHERE id = ?", (int(category_id),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_category_by_path(path: str) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM cert_categories WHERE path_slug = ?",
            ((path or "").strip("/"),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_category(category_id: int, fields: dict) -> bool:
    allowed = {"cert_name", "difficulty", "status", "sort_order",
               "seo_title", "seo_keywords", "seo_description", "exam_code"}
    sets, args = [], []
    for key in allowed:
        if key in fields and fields[key] is not None:
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if not sets:
        return False
    sets.append("updated_at = ?")
    args.append(_now())
    args.append(int(category_id))
    conn = _conn()
    try:
        cur = conn.execute(
            f"UPDATE cert_categories SET {', '.join(sets)} WHERE id = ?", tuple(args)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def category_stats() -> dict:
    conn = _conn()
    try:
        c = conn.execute("SELECT COUNT(*) AS c FROM cert_categories WHERE status='active'").fetchone()["c"]
        q = conn.execute("SELECT COUNT(*) AS c FROM questions WHERE cert_category_id IS NOT NULL AND status='active'").fetchone()["c"]
        return {"categories": int(c), "questions": int(q)}
    finally:
        conn.close()


def counts_by_path() -> dict[str, int]:
    """path_slug -> 题量，用于首页/厂商页直接展示。

    口径与每日答题抽题源保持一致：该认证有**已发布模拟题库**时按模拟题库计数，
    否则回退统计旧题库 (questions)。这样后台导入双写不会把同一道题算两遍，
    重复导入也不会让数字持续膨胀，同时兼容只有旧题库的历史分类。
    """
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT cert_category_id, COUNT(*) AS c FROM questions "
            "WHERE cert_category_id IS NOT NULL AND status = 'active' "
            "GROUP BY cert_category_id"
        ).fetchall()
        mapping = {}
        for row in rows:
            mapping[int(row["cert_category_id"])] = int(row["c"])
        # 模拟题库：通过 question_banks.cert_path 归集到对应认证方向
        brows = conn.execute(
            "SELECT b.cert_path, COUNT(*) AS c FROM bank_questions bq "
            "JOIN question_banks b ON bq.bank_id = b.id "
            "WHERE bq.status = 'active' AND b.status = 'published' GROUP BY b.cert_path"
        ).fetchall()
        bank_map = {row["cert_path"]: int(row["c"]) for row in brows}
        cats = conn.execute("SELECT id, path_slug FROM cert_categories").fetchall()
        return {
            row["path_slug"]: bank_map.get(row["path_slug"], 0)
            or mapping.get(int(row["id"]), 0)
            for row in cats
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 认证介绍
# ---------------------------------------------------------------------------

def get_intro(category_id: int) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM cert_intros WHERE category_id = ?", (int(category_id),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_intros(keyword: str = "") -> list[dict]:
    where, args = "", []
    if keyword:
        like = f"%{keyword}%"
        where = "WHERE c.cert_name LIKE ? OR c.path_slug LIKE ? OR c.vendor_name LIKE ?"
        args = [like, like, like]
    conn = _conn()
    try:
        rows = conn.execute(
            f"""
            SELECT c.id AS category_id, c.path_slug, c.cert_name, c.vendor_name,
                   c.direction_name, c.level_name, c.exam_code, c.difficulty AS cat_difficulty,
                   i.id AS intro_id, i.positioning, i.audience, i.exam_topics,
                   i.difficulty, i.advice, i.summary, i.source, i.updated_by, i.updated_at
            FROM cert_categories c
            LEFT JOIN cert_intros i ON i.category_id = c.id
            {where}
            ORDER BY c.sort_order, c.id
            """,
            tuple(args),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_intro(category_id: int, fields: dict, updated_by: str = "") -> bool:
    """保存五段式介绍；不存在则创建（管理员可先于播种编辑）。"""
    if not get_category(category_id):
        return False
    now = _now()
    data = {
        "positioning": (fields.get("positioning") or "").strip(),
        "audience": (fields.get("audience") or "").strip(),
        "exam_topics": (fields.get("exam_topics") or "").strip(),
        "difficulty": (fields.get("difficulty") or "").strip(),
        "advice": (fields.get("advice") or "").strip(),
        "summary": (fields.get("summary") or "").strip()[:500],
        "source": (fields.get("source") or "").strip()[:255],
    }
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id FROM cert_intros WHERE category_id = ?", (int(category_id),)
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE cert_intros SET positioning = ?, audience = ?, exam_topics = ?, "
                "difficulty = ?, advice = ?, summary = ?, source = ?, updated_by = ?, "
                "updated_at = ? WHERE category_id = ?",
                (data["positioning"], data["audience"], data["exam_topics"],
                 data["difficulty"], data["advice"], data["summary"], data["source"],
                 updated_by, now, int(category_id)),
            )
        else:
            conn.execute(
                "INSERT INTO cert_intros (category_id, positioning, audience, exam_topics, "
                "difficulty, advice, summary, source, updated_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(category_id), data["positioning"], data["audience"], data["exam_topics"],
                 data["difficulty"], data["advice"], data["summary"], data["source"],
                 updated_by, now, now),
            )
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 题库
# ---------------------------------------------------------------------------

def _question_row(row) -> dict:
    data = dict(row)
    try:
        data["options"] = json.loads(data.get("options") or "{}")
    except (ValueError, TypeError):
        data["options"] = {}
    data["answer_list"] = [a.strip() for a in str(data.get("answer") or "").replace("，", ",").split(",") if a.strip()]
    return data


def count_questions(category_id: int, include_descendants: bool = False, status: str = "active") -> int:
    conn = _conn()
    try:
        if include_descendants:
            cat = get_category(category_id)
            if not cat:
                return 0
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM questions q JOIN cert_categories c "
                "ON c.id = q.cert_category_id "
                "WHERE c.vendor_slug = ? AND c.direction_slug = ? AND q.status = ?",
                (cat["vendor_slug"], cat["direction_slug"], status),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE cert_category_id = ? AND status = ?",
                (int(category_id), status),
            ).fetchone()
        return int(row["c"])
    finally:
        conn.close()


def list_questions(
    category_id: Optional[int] = None,
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
    status: str = "",
) -> dict:
    where, args = ["q.cert_category_id IS NOT NULL"], []
    if category_id:
        where.append("q.cert_category_id = ?")
        args.append(int(category_id))
    if keyword:
        like = f"%{keyword}%"
        where.append("(q.question LIKE ? OR q.explanation LIKE ?)")
        args += [like, like]
    if status:
        where.append("q.status = ?")
        args.append(status)
    clause = "WHERE " + " AND ".join(where)
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    conn = _conn()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM questions q {clause}", tuple(args)
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT q.*, c.cert_name, c.path_slug
            FROM questions q LEFT JOIN cert_categories c ON c.id = q.cert_category_id
            {clause}
            ORDER BY q.id DESC LIMIT ? OFFSET ?
            """,
            tuple(args) + (page_size, (page - 1) * page_size),
        ).fetchall()
        return {
            "questions": [_question_row(r) for r in rows],
            "total": int(total),
            "page": page,
            "page_size": page_size,
            "pages": max(1, (int(total) + page_size - 1) // page_size),
        }
    finally:
        conn.close()


def get_question(question_id: int) -> Optional[dict]:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM questions WHERE id = ?", (int(question_id),)).fetchone()
        return _question_row(row) if row else None
    finally:
        conn.close()


def create_question(fields: dict, category_id: int, created_at: str = "") -> int:
    answer = fields.get("answer")
    if isinstance(answer, (list, tuple)):
        answer = ",".join(str(a).strip().upper() for a in answer)
    options = fields.get("options")
    if isinstance(options, (dict, list)):
        options = json.dumps(options, ensure_ascii=False)
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO questions (daily_set_id, cert_category_id, set_index, type, question, "
            "options, answer, explanation, difficulty, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _no_set(), int(category_id), int(fields.get("set_index") or 0),
                fields.get("type") or "single", fields.get("question") or "",
                options or "{}", (answer or "").upper(),
                fields.get("explanation") or "", fields.get("difficulty") or "中",
                fields.get("status") or "active", created_at or _now(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_question(question_id: int, fields: dict) -> bool:
    allowed = {"question", "type", "options", "answer", "explanation", "difficulty", "status"}
    sets, args = [], []
    for key in allowed:
        if key not in fields:
            continue
        value = fields[key]
        if key == "options" and isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if key == "answer":
            if isinstance(value, (list, tuple)):
                value = ",".join(str(a).strip().upper() for a in value)
            value = str(value or "").upper()
        sets.append(f"{key} = ?")
        args.append(value)
    if not sets:
        return False
    args.append(int(question_id))
    conn = _conn()
    try:
        cur = conn.execute(
            f"UPDATE questions SET {', '.join(sets)} WHERE id = ?", tuple(args)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_question(question_id: int) -> bool:
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM questions WHERE id = ?", (int(question_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _random_from(pool_ids: Iterable[int], limit: int, exclude: set[int]) -> list[dict]:
    ids = [int(i) for i in pool_ids if int(i) not in exclude]
    if not ids or limit <= 0:
        return []
    placeholders = ", ".join("?" for _ in ids)
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM questions WHERE id IN ({placeholders}) AND status = 'active' "
            f"ORDER BY {_rand_func()} LIMIT ?",
            tuple(ids) + (int(limit),),
        ).fetchall()
        return [_question_row(r) for r in rows]
    finally:
        conn.close()


def random_questions(category_id: int, limit: int = 10) -> tuple[list[dict], str]:
    """随机抽题；题量不足时逐级回退。

    优先级：本认证 → 同方向其它等级 → 同厂商其它方向 → 全站题库。
    Returns:
        (题目列表, 实际使用的题池说明)：pool 取值 category / direction / vendor / global
    """
    cat = get_category(category_id)
    if not cat:
        return [], "none"
    conn = _conn()
    try:
        def ids_of(where: str, args: tuple) -> list[int]:
            rows = conn.execute(
                f"SELECT id FROM questions WHERE status='active' AND cert_category_id IS NOT NULL {where}",
                args,
            ).fetchall()
            return [int(r["id"]) for r in rows]

        pools = [
            ("category", ids_of("AND cert_category_id = ?", (int(category_id),))),
            ("direction", ids_of(
                "AND cert_category_id IN (SELECT id FROM cert_categories "
                "WHERE vendor_slug = ? AND direction_slug = ?)",
                (cat["vendor_slug"], cat["direction_slug"]),
            )),
            ("vendor", ids_of(
                "AND cert_category_id IN (SELECT id FROM cert_categories WHERE vendor_slug = ?)",
                (cat["vendor_slug"],),
            )),
            ("global", ids_of("", ())),
        ]
    finally:
        conn.close()

    picked: list[dict] = []
    seen: set[int] = set()
    used_pool = "category"
    for name, ids in pools:
        if len(picked) >= limit:
            break
        rows = _random_from(ids, limit - len(picked), seen)
        if rows and not picked:
            used_pool = name
        for row in rows:
            if int(row["id"]) in seen:
                continue
            seen.add(int(row["id"]))
            picked.append(row)
    return picked[:limit], used_pool


def questions_by_ids(ids: Iterable[int]) -> dict[int, dict]:
    clean = []
    for raw in ids:
        try:
            clean.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not clean:
        return {}
    placeholders = ", ".join("?" for _ in clean)
    conn = _conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM questions WHERE id IN ({placeholders})", tuple(clean)
        ).fetchall()
        return {int(r["id"]): _question_row(r) for r in rows}
    finally:
        conn.close()


def import_questions(text: str, category_id: int, fmt: str = "markdown", overwrite: bool = False) -> dict:
    """把 Markdown / JSON 题目导入指定认证分类。"""
    cat = get_category(category_id)
    if not cat:
        raise ValueError("认证分类不存在")
    result = parser.parse_auto(text, fmt)
    questions = [q for qs in result.sets.values() for q in qs]
    if not questions:
        return {"imported": 0, "failed": len(result.failed), "errors": result.failed[:20],
                "category": cat["cert_name"]}
    removed = 0
    if overwrite:
        conn = _conn()
        try:
            cur = conn.execute(
                "DELETE FROM questions WHERE cert_category_id = ?", (int(category_id),)
            )
            removed = cur.rowcount
            conn.commit()
        finally:
            conn.close()
    imported = 0
    for index, q in enumerate(questions, start=1):
        create_question(
            {
                "type": q.q_type,
                "question": q.question,
                "options": q.options,
                "answer": q.answer,
                "explanation": q.explanation,
                "set_index": index,
            },
            category_id,
        )
        imported += 1
    return {
        "imported": imported,
        "removed": removed,
        "failed": len(result.failed),
        "errors": result.failed[:20],
        "category": cat["cert_name"],
    }


# ---------------------------------------------------------------------------
# 答题记录 / 荣誉
# ---------------------------------------------------------------------------

def save_quiz_record(
    user_id: int,
    category: dict,
    question_ids: list[int],
    answers: dict,
    total: int,
    correct: int,
    score: int,
    accuracy: float,
    duration: int,
    started_at: str,
    finished_at: str,
) -> int:
    now = finished_at or _now()
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO quiz_records (user_id, category_id, cert_path, cert_name, "
            "question_ids, answers, total, correct, score, accuracy, duration, "
            "started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(user_id), int(category["id"]), category["path_slug"], category["cert_name"],
                json.dumps([int(i) for i in question_ids]),
                json.dumps({str(k): v for k, v in answers.items()}, ensure_ascii=False),
                int(total), int(correct), int(score), float(accuracy), int(duration),
                started_at or "", now,
            ),
        )
        record_id = cur.lastrowid
        conn.execute(
            "INSERT INTO user_honors (user_id, category_id, cert_name, score, correct, "
            "total, duration, quiz_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(user_id), int(category["id"]), category["cert_name"], int(score),
             int(correct), int(total), int(duration), now[:10], now),
        )
        conn.commit()
    finally:
        conn.close()

    for period_type in ("day", "week", "month"):
        bump_ranking(period_type, user_id, score, now[:10])
    return record_id


def period_key(period_type: str, when: Optional[date] = None) -> str:
    d = when or date.today()
    if period_type == "day":
        return d.isoformat()
    if period_type == "week":
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period_type == "month":
        return f"{d.year}-{d.month:02d}"
    raise ValueError("period_type 必须是 day / week / month")


def bump_ranking(period_type: str, user_id: int, score: int, quiz_date: str, *,
                 points: Optional[int] = None, conn=None) -> None:
    """把一次成绩累加到日 / 周 / 月榜（读改写，SQLite / MySQL 通用）。

    Args:
        score: 本次成绩，用于刷新榜单「最高分」。
        points: 计入榜单的积分，默认等于成绩（每日答题按成绩 1:1 计分，
            模拟考按成绩 ×2 折算，需显式传入）。
        conn: 调用方事务连接；传入时不提交也不关闭，用于与判分同事务落库。

    `conn` 由调用方传入时复用其事务（模拟考积分与判分同事务提交）。
    """
    gained = int(score if points is None else points)
    when = date.fromisoformat(str(quiz_date)[:10])
    key = period_key(period_type, when)
    now = _now()
    owns = conn is None
    conn = conn or _conn()
    try:
        row = conn.execute(
            "SELECT id, points, quiz_count, best_score FROM rankings "
            "WHERE period_type = ? AND period_key = ? AND user_id = ?",
            (period_type, key, int(user_id)),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE rankings SET points = ?, quiz_count = ?, best_score = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    int(row["points"]) + gained, int(row["quiz_count"]) + 1,
                    max(int(row["best_score"]), int(score)), now, int(row["id"]),
                ),
            )
        else:
            conn.execute(
                "INSERT INTO rankings (period_type, period_key, user_id, points, "
                "quiz_count, best_score, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (period_type, key, int(user_id), gained, int(score), now),
            )
        if owns:
            conn.commit()
    finally:
        if owns:
            conn.close()


def top_ranking(period_type: str, limit: int = 10, when: Optional[date] = None) -> list[dict]:
    key = period_key(period_type, when)
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT r.user_id, r.points, r.quiz_count, r.best_score, r.updated_at,
                   u.username, u.nickname
            FROM rankings r JOIN users u ON u.id = r.user_id
            WHERE r.period_type = ? AND r.period_key = ?
            ORDER BY r.points DESC, r.best_score DESC, r.updated_at ASC
            LIMIT ?
            """,
            (period_type, key, int(limit)),
        ).fetchall()
        result = []
        for index, row in enumerate(rows, start=1):
            item = dict(row)
            item["rank"] = index
            item["display_name"] = item.get("nickname") or item.get("username") or "匿名用户"
            result.append(item)
        return result
    finally:
        conn.close()


def user_rank(period_type: str, user_id: int, when: Optional[date] = None) -> Optional[dict]:
    key = period_key(period_type, when)
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT points, quiz_count, best_score FROM rankings "
            "WHERE period_type = ? AND period_key = ? AND user_id = ?",
            (period_type, key, int(user_id)),
        ).fetchone()
        if not row:
            return None
        better = conn.execute(
            "SELECT COUNT(*) AS c FROM rankings WHERE period_type = ? AND period_key = ? "
            "AND (points > ? OR (points = ? AND best_score > ?))",
            (period_type, key, row["points"], row["points"], row["best_score"]),
        ).fetchone()["c"]
        return {
            "rank": int(better) + 1,
            "points": int(row["points"]),
            "quiz_count": int(row["quiz_count"]),
            "best_score": int(row["best_score"]),
        }
    finally:
        conn.close()


def user_trend(user_id: int, days: int = 7) -> list[dict]:
    """最近 N 天每日积分（每日答题成绩 + 模拟考积分），用于榜单趋势图。"""
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT d, SUM(p) AS p FROM ("
            "  SELECT substr(finished_at, 1, 10) AS d, score AS p FROM quiz_records "
            "  WHERE user_id = ? AND substr(finished_at, 1, 10) >= ? "
            "  UNION ALL "
            "  SELECT earned_date AS d, points AS p FROM bank_exam_awards "
            "  WHERE user_id = ? AND earned_date >= ?"
            ") t GROUP BY d",
            (int(user_id), start, int(user_id), start),
        ).fetchall()
        mapping = {str(r["d"]): int(r["p"]) for r in rows}
    finally:
        conn.close()
    out = []
    for offset in range(days):
        d = date.today() - timedelta(days=days - 1 - offset)
        out.append({"date": d.isoformat(), "points": mapping.get(d.isoformat(), 0)})
    return out


def list_user_records(user_id: int, limit: int = 50) -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT id, category_id, cert_name, score, correct, total, duration, finished_at "
            "FROM quiz_records WHERE user_id = ? ORDER BY finished_at DESC, id DESC LIMIT ?",
            (int(user_id), int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def user_stats(user_id: int) -> dict:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS times, COALESCE(SUM(score), 0) AS points, "
            "COALESCE(MAX(score), 0) AS best, COALESCE(AVG(accuracy), 0) AS avg_acc, "
            "COALESCE(SUM(correct), 0) AS correct, COALESCE(SUM(total), 0) AS total "
            "FROM quiz_records WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        # 模拟考积分单独记账（每题库每日一次），与每日答题的成绩分相加即为总积分，
        # 与排行榜 rankings.points 的口径保持一致。
        exam = conn.execute(
            "SELECT COUNT(*) AS times, COALESCE(SUM(points), 0) AS points, "
            "COALESCE(MAX(score), 0) AS best FROM bank_exam_awards WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        days = conn.execute(
            "SELECT COUNT(DISTINCT quiz_date) AS d FROM user_honors WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()["d"]
        cats = conn.execute(
            "SELECT COUNT(DISTINCT cert_name) AS c FROM quiz_records WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()["c"]
        return {
            "times": int(row["times"]),
            "points": int(row["points"]) + int(exam["points"]),
            "best": int(row["best"]),
            "avg_accuracy": float(row["avg_acc"] or 0),
            "correct": int(row["correct"]),
            "total": int(row["total"]),
            "active_days": int(days),
            "categories": int(cats),
            "exam_times": int(exam["times"]),
            "exam_best": int(exam["best"]),
        }
    finally:
        conn.close()


def badges_for(user_id: int) -> list[dict]:
    """根据答题记录推导荣誉徽章（无需额外表，规则集中在此）。"""
    stats = user_stats(user_id)
    conn = _conn()
    try:
        perfect = conn.execute(
            "SELECT COUNT(*) AS c FROM quiz_records WHERE user_id = ? AND score >= 100",
            (int(user_id),),
        ).fetchone()["c"]
    finally:
        conn.close()
    defs = [
        ("first_quiz", "初次登场", "完成第一次每日答题", stats["times"] >= 1),
        ("perfect", "满分王者", "单次答题拿到 100 分", int(perfect) >= 1),
        ("points_500", "积分新星", "累计积分达到 500", stats["points"] >= 500),
        ("points_1000", "千分达人", "累计积分达到 1000", stats["points"] >= 1000),
        ("days_3", "坚持三天", "累计 3 天参与答题", stats["active_days"] >= 3),
    ]
    return [
        {"code": code, "name": name, "desc": desc, "earned": bool(ok)}
        for code, name, desc, ok in defs
    ]


def my_honors(user_id: int, limit: int = 30) -> dict:
    return {
        "stats": user_stats(user_id),
        "badges": badges_for(user_id),
        "records": list_user_records(user_id, limit),
        "recent": list_user_records(user_id, 5),
    }


def ranking_payload(period_type: str, limit: int = 20, user_id: Optional[int] = None) -> dict:
    rows = top_ranking(period_type, limit)
    data = {
        "period": period_type,
        "period_key": period_key(period_type),
        "rows": rows,
        "me": user_rank(period_type, user_id) if user_id else None,
        "trend": user_trend(user_id) if user_id else [],
    }
    return data
