"""回归测试：题目导入选择栏目 + 「阿里云认证」栏目页聚合 + 历史日期栏目下线。

全程使用独立临时数据库，不触碰正在运行的 data/quiz.db。

覆盖：
1. navigation 栏目树 / 路径 / 子孙聚合等复用接口；
2. 导入页渲染配置驱动的三级选择器；
3. 选择「阿里云认证 › 阿里云ACP › 云计算高级工程师」导入正确落库；
4. 空栏目 / 伪造 slug 被拒绝；配置树内任意合法节点允许导入；
5. 栏目页按「自身 + 子孙」聚合题目，兄弟栏目互不串题；
6. 首页 / 后台 / 导出携带栏目信息；
7. 编辑页可改栏目、留空保持原值、非法值整体拒绝；
8. 动态注册新 ACP 方向后选择器与栏目页自动生效，菜单节点移除后路径快照仍可读；
9. 历史日期栏目彻底下线（路由 404 / 导航与返回链接清理）；
10. 权限：普通用户不能导入，但可浏览栏目页。
"""
from __future__ import annotations

import io
import json
import shutil
import tempfile
from pathlib import Path

# 必须在 import app 之前重定向数据库：app.py 在模块加载时就会 init_db()
import models

_TMP_DIR = Path(tempfile.mkdtemp(prefix="daily_quiz_category_"))
models.DB_PATH = _TMP_DIR / "quiz.db"

from app import app  # noqa: E402
import auth  # noqa: E402
import navigation  # noqa: E402
import admin_logic  # noqa: E402

SRC = Path(__file__).resolve().parent / "data" / "ACP模拟题20道_2026-11-04.md"
MD_TEXT = SRC.read_text(encoding="utf-8")

D_CLOUD = "2026-11-10"
D_ACA = "2026-11-11"
D_BIGDATA = "2026-11-12"
CLOUD_SLUG = "acp-cloud-computing"


def login(client, username: str, password: str) -> None:
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code == 302, f"{username} 登录失败"


def upload(client, date_str: str, slug: str = CLOUD_SLUG, title: str = "测试题库"):
    """模拟管理员选择栏目 + 上传文件并提交。"""
    return client.post(
        "/import",
        data={
            "date": date_str,
            "title": title,
            "category_slug": slug,
            "md_file": (io.BytesIO(MD_TEXT.encode("utf-8")), f"ACP模拟题_{date_str}.md"),
        },
        content_type="multipart/form-data",
    )


def set_row(date_str: str):
    return models.get_daily_set_by_date(date_str)


def edit_form(date_str: str, title: str = "改后标题"):
    """由编辑页视图反向构造一份完整合法表单（逐题字段齐全）。"""
    view = admin_logic.build_set_editor(date_str)
    assert view is not None
    form = {"set_date": date_str, "set_title": title}
    for q in view["questions"]:
        qid = q["id"]
        form[f"q_{qid}_type"] = q["type"]
        form[f"q_{qid}_question"] = q["question"]
        for letter, text in q["options"].items():
            form[f"q_{qid}_option_{letter}"] = text
        form[f"q_{qid}_answer"] = "".join(q["answer"])
        form[f"q_{qid}_explanation"] = q["explanation"]
    return form


try:
    models.init_db()
    auth.create_default_users()
    admin = app.test_client()
    login(admin, "admin", "admin123")

    # 1) 配置树复用接口
    tree = navigation.category_tree_payload()
    assert tree[0]["slug"] == "aliyun-cert"
    acp = next(n for n in tree[0]["children"] if n["slug"] == "acp")
    assert acp["children"][0]["slug"] == CLOUD_SLUG
    assert navigation.category_path_label(CLOUD_SLUG) == "阿里云认证 › 阿里云ACP › 云计算高级工程师"
    assert navigation.category_label(CLOUD_SLUG) == "云计算高级工程师"
    assert navigation.category_label("") == "未分类"
    assert set(navigation.descendant_slugs("acp")) >= {"acp", CLOUD_SLUG}
    assert navigation.descendant_slugs("aliyun-cert") and not navigation.descendant_slugs("nope")
    assert navigation.is_valid_category(CLOUD_SLUG) and not navigation.is_valid_category("")
    assert not navigation.is_valid_category("../../etc")
    print("1) 配置树 / 路径快照 / 子孙聚合 / 合法性校验接口正确 ✅")

    # 2) 导入页渲染三级选择器
    body = admin.get("/import").get_data(as_text=True)
    assert "导入栏目" in body and "data-category-picker" in body
    assert 'name="category_slug"' in body and 'data-required="1"' in body
    assert CLOUD_SLUG in body, "选择器数据树应包含三级栏目"
    assert "/static/category_picker.js" in body
    assert body.count('data-cat-level=') == 3, "应有三级联动下拉"
    print("2) 导入页含配置驱动的三级栏目选择器 ✅")

    # 3) 选择 ACP › 云计算高级工程师 导入
    r = upload(admin, D_CLOUD)
    assert r.status_code == 200 and "导入结果" in r.get_data(as_text=True)
    row = set_row(D_CLOUD)
    assert row is not None and row["category_slug"] == CLOUD_SLUG
    assert row["category_path"] == "阿里云认证 › 阿里云ACP › 云计算高级工程师"
    assert len(models.get_all_questions_by_set(row["id"])) == 20
    assert "云计算高级工程师" in r.get_data(as_text=True)
    print("3) 选择三级栏目导入 20 题，slug 与路径快照正确落库 ✅")

    # 4) 空栏目 / 伪造 slug 被拒绝；树内中间节点合法
    r_empty = upload(admin, "2026-11-13", slug="")
    assert r_empty.status_code == 302, "空栏目应重定向回导入页"
    assert "请选择完整的导入栏目" in admin.get("/import").get_data(as_text=True)
    assert set_row("2026-11-13") is None
    r_bad = upload(admin, "2026-11-13", slug="not-a-real-slug")
    assert r_bad.status_code == 302
    assert "请选择完整的导入栏目" in admin.get("/import").get_data(as_text=True)
    assert set_row("2026-11-13") is None, "伪造 slug 不应落库"
    r_mid = upload(admin, "2026-11-14", slug="acp")
    assert r_mid.status_code == 200 and set_row("2026-11-14")["category_slug"] == "acp"
    print("4) 空栏目 / 伪造 slug 被拒绝且不落库；树内合法节点可导入 ✅")

    # 5) 栏目页聚合（父级含子级，兄弟栏目互不串题）
    upload(admin, D_ACA, slug="aca", title="ACA 入门题库")
    acp_page = admin.get("/certification/acp").get_data(as_text=True)
    assert D_CLOUD in acp_page and "测试题库" in acp_page, "ACP 页应聚合三级方向题库"
    assert D_ACA not in acp_page, "ACP 页不应出现 ACA 题库"
    root_page = admin.get("/certification").get_data(as_text=True)
    assert D_CLOUD in root_page and D_ACA in root_page, "总览应聚合全部认证题库"
    aca_page = admin.get("/certification/aca").get_data(as_text=True)
    assert D_ACA in aca_page and D_CLOUD not in aca_page
    leaf_page = admin.get(f"/certification/{CLOUD_SLUG}").get_data(as_text=True)
    assert D_CLOUD in leaf_page and "本栏目题库" in leaf_page
    print("5) 栏目页按自身 + 子孙聚合题目，兄弟栏目互不串题 ✅")

    # 6) 首页 / 后台 / 导出携带栏目
    assert "云计算高级工程师" in admin.get("/").get_data(as_text=True)
    assert "云计算高级工程师" in admin.get("/admin").get_data(as_text=True)
    exported = json.loads(admin.get(f"/export/questions/{D_CLOUD}").get_data(as_text=True))
    assert exported["category_slug"] == CLOUD_SLUG
    assert exported["category_path"].endswith("云计算高级工程师")
    print("6) 首页 / 后台列表 / 导出 JSON 均携带栏目信息 ✅")

    # 7) 编辑页：改栏目、留空保持、非法值拒绝
    form = edit_form(D_CLOUD)
    form["category_slug"] = "aca"
    r_edit = admin.post(f"/admin/set/{D_CLOUD}/edit", data=form, follow_redirects=True)
    assert r_edit.status_code == 200
    assert set_row(D_CLOUD)["category_slug"] == "aca", "编辑页应能改栏目"
    assert "阿里云ACA" in set_row(D_CLOUD)["category_path"], "路径快照应同步刷新"
    editor_body = admin.get(f"/admin/set/{D_CLOUD}/edit").get_data(as_text=True)
    assert "data-category-picker" in editor_body and 'data-required="0"' in editor_body
    assert 'value="aca"' in editor_body, "编辑页应回填当前栏目"

    form2 = edit_form(D_CLOUD)
    form2.pop("category_slug", None)
    admin.post(f"/admin/set/{D_CLOUD}/edit", data=form2, follow_redirects=True)
    assert set_row(D_CLOUD)["category_slug"] == "aca", "不提交栏目应保持原归属"

    form3 = edit_form(D_CLOUD)
    form3["category_slug"] = "nope"
    r_bad_edit = admin.post(f"/admin/set/{D_CLOUD}/edit", data=form3)
    assert "所属栏目不合法" in r_bad_edit.get_data(as_text=True)
    assert set_row(D_CLOUD)["category_slug"] == "aca", "非法栏目不应写入"
    print("7) 编辑页可改栏目 / 留空保持 / 非法值不落库 ✅")

    # 8) 动态扩展 ACP 方向：选择器与栏目页自动生效
    original_tree = navigation.CERT_NAV
    try:
        navigation.register_acp_direction("acp-bigdata", "大数据高级工程师", "扩展方向占位")
        assert "acp-bigdata" in admin.get("/import").get_data(as_text=True)
        r = upload(admin, D_BIGDATA, slug="acp-bigdata", title="大数据方向题库")
        assert r.status_code == 200 and set_row(D_BIGDATA)["category_slug"] == "acp-bigdata"
        assert D_BIGDATA in admin.get("/certification/acp").get_data(as_text=True)
        navigation.CERT_NAV = original_tree
        # 菜单节点被移除后，历史题库仍保留可读的路径快照
        assert "大数据高级工程师" in set_row(D_BIGDATA)["category_path"]
        assert "大数据高级工程师" in admin.get("/").get_data(as_text=True)
    finally:
        navigation.CERT_NAV = original_tree
    print("8) 动态注册方向自动生效；菜单变更后路径快照仍可读 ✅")

    # 9) 历史日期栏目彻底下线
    assert admin.get("/history").status_code == 404
    home = admin.get("/").get_data(as_text=True)
    assert "历史日期" not in home and "我的成绩与历史" not in home
    assert "返回历史" not in admin.get(f"/date/{D_CLOUD}").get_data(as_text=True)
    assert "返回首页" in admin.get(f"/date/{D_CLOUD}").get_data(as_text=True)
    print("9) /history 404，导航 / 首页卡片 / 返回历史链接均已清理 ✅")

    # 10) 权限：普通用户不能导入，但可浏览栏目页
    anon = app.test_client()
    assert anon.get("/import").status_code == 302
    normal = app.test_client()
    login(normal, "user", "user123")
    assert normal.get("/import").status_code == 302
    assert upload(normal, "2026-11-15").status_code == 302
    assert set_row("2026-11-15") is None
    assert normal.get(f"/certification/{CLOUD_SLUG}").status_code == 200
    assert "导入题目" not in normal.get(f"/certification/{CLOUD_SLUG}").get_data(as_text=True)
    print("10) 普通用户不能导入，可正常浏览栏目页且无导入入口 ✅")

    print("\n全部通过：导入栏目选择、栏目聚合展示与历史日期栏目下线验证完成。")
finally:
    shutil.rmtree(_TMP_DIR, ignore_errors=True)
