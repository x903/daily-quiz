"""「阿里云认证」栏目：可复用的树形菜单配置与页面元数据。

设计目的：
- 菜单（一级 / 二级 / 三级）与页面骨架共用**同一份配置**。后续要扩展其他 ACP
  方向，只需在 ``CERT_NAV`` 的 ACP 节点下加一个 ``NavNode``（或调用
  ``register_acp_direction``），菜单项、面包屑、子栏目卡片与 URL 会自动生成，
  不必再改模板或新增路由函数。
- 本模块只描述导航与占位文案，不依赖 Flask / 数据库，便于单独测试。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterator, Optional

ROOT_SLUG = "aliyun-cert"
ACP_SLUG = "acp"


@dataclass(frozen=True)
class NavNode:
    """一个菜单节点，同时也是一个介绍页所需的全部配置。"""

    label: str
    slug: str
    intro: str = ""
    children: tuple["NavNode", ...] = ()

    @property
    def has_children(self) -> bool:
        return bool(self.children)


@dataclass(frozen=True)
class CertPage:
    """视图渲染所需的页面上下文（由菜单树推导，不单独维护）。"""

    node: NavNode
    parent: Optional[NavNode]
    depth: int
    crumbs: tuple[NavNode, ...]

    @property
    def children(self) -> tuple[NavNode, ...]:
        return self.node.children


# 一级：阿里云认证 → 二级：ACA / ACP / ACE → 三级：ACP 下的具体方向
CERT_NAV: NavNode = NavNode(
    label="阿里云认证",
    slug=ROOT_SLUG,
    intro="阿里云认证体系介绍与备考入口，覆盖 ACA、ACP、ACE 三个级别。",
    children=(
        NavNode(
            label="阿里云ACA",
            slug="aca",
            intro="助理工程师认证（ACA）：面向入门学习者，考察云计算基础概念与阿里云核心产品认知。",
        ),
        NavNode(
            label="阿里云ACP",
            slug=ACP_SLUG,
            intro="专业工程师认证（ACP）：面向有实际项目经验的工程师，按技术方向细分考试。",
            children=(
                NavNode(
                    label="云计算高级工程师",
                    slug="acp-cloud-computing",
                    intro="ACP 云计算方向：覆盖 ECS、VPC、OSS、SLB、RDS 等核心服务的架构设计、部署与运维。",
                ),
            ),
        ),
        NavNode(
            label="阿里云ACE",
            slug="ace",
            intro="高级工程师 / 架构师认证（ACE）：面向复杂业务场景的架构设计与方案落地能力。",
        ),
    ),
)


def iter_pages(
    root: Optional[NavNode] = None,
    _parent: Optional[NavNode] = None,
    _depth: int = 1,
    _crumbs: tuple[NavNode, ...] = (),
) -> Iterator[CertPage]:
    """深度优先遍历菜单树，产出每个节点的页面上下文。

    Args:
        root: 起始节点，默认取模块级 ``CERT_NAV``（因此运行时替换配置会立即生效）。
        其余以下划线开头的参数仅供递归内部使用。

    Yields:
        CertPage，含节点、父节点、层级与从根到当前节点的面包屑。
    """
    node = root if root is not None else CERT_NAV
    crumbs = (*_crumbs, node)
    yield CertPage(node=node, parent=_parent, depth=_depth, crumbs=crumbs)
    for child in node.children:
        yield from iter_pages(child, node, _depth + 1, crumbs)


def find_page(slug: str) -> Optional[CertPage]:
    """按 slug 查找页面配置，未找到返回 None（视图据此返回 404）。"""
    return next((p for p in iter_pages() if p.node.slug == slug), None)


def all_pages() -> dict[str, CertPage]:
    """返回 slug → CertPage 索引，便于测试与后续批量生成路由。"""
    return {p.node.slug: p for p in iter_pages()}


def register_acp_direction(slug: str, label: str, intro: str = "") -> NavNode:
    """在「阿里云ACP」下追加一个方向，菜单与页面同时生效。

    常规扩展建议直接编辑 ``CERT_NAV`` 配置树；此函数用于运行时动态注册
    （例如插件式加载、测试模拟扩展）。重复 slug 会抛 ValueError。

    Args:
        slug: URL 片段，如 ``acp-bigdata``。
        label: 菜单显示名，如「大数据高级工程师」。
        intro: 占位简介。

    Returns:
        新注册的 NavNode。
    """
    global CERT_NAV
    if find_page(slug) is not None:
        raise ValueError(f"菜单 slug 已存在：{slug}")

    node = NavNode(label=label, slug=slug, intro=intro)

    def _append(current: NavNode) -> NavNode:
        if current.slug == ACP_SLUG:
            return replace(current, children=current.children + (node,))
        if current.children:
            return replace(
                current, children=tuple(_append(child) for child in current.children)
            )
        return current

    CERT_NAV = _append(CERT_NAV)
    if find_page(slug) is None:  # 配置树里没有 ACP 节点时不做静默失败
        raise ValueError("菜单树中未找到「阿里云ACP」节点，无法注册方向")
    return node


# ---------------------------------------------------------------------------
# 题库栏目：导入选择器、路径快照与按栏目聚合
# ---------------------------------------------------------------------------

def category_tree_payload() -> list[dict]:
    """导出可 JSON 序列化的栏目树，供导入页 / 编辑页的三级联动选择器使用。

    Returns:
        根节点列表（每个节点含 label / slug / children）。之所以返回列表而非
        单个根，是为了以后出现多个一级认证体系时选择器无需改动。
    """
    def to_dict(node: NavNode) -> dict:
        return {
            "label": node.label,
            "slug": node.slug,
            "children": [to_dict(child) for child in node.children],
        }

    return [to_dict(CERT_NAV)]


def category_path_of(slug: str) -> tuple[NavNode, ...]:
    """返回从一级栏目到该节点的路径；slug 不存在时返回空元组。"""
    page = find_page(slug)
    return page.crumbs if page is not None else ()


def category_path_label(slug: str, sep: str = " › ") -> str:
    """返回栏目的完整路径文案，用于导入时写入数据库快照。"""
    return sep.join(node.label for node in category_path_of(slug))


def category_label(slug: str) -> str:
    """返回栏目自身名称；未知或空 slug 返回「未分类」。"""
    page = find_page(slug) if slug else None
    return page.node.label if page is not None else "未分类"


def descendant_slugs(slug: str) -> tuple[str, ...]:
    """返回节点自身及全部后代的 slug，供栏目页聚合子栏目下的题库。

    Args:
        slug: 栏目节点 slug。

    Returns:
        slug 元组；未知节点返回空元组（调用方据此展示空列表而不是全库题目）。
    """
    page = find_page(slug)
    if page is None:
        return ()
    result: list[str] = []

    def walk(node: NavNode) -> None:
        result.append(node.slug)
        for child in node.children:
            walk(child)

    walk(page.node)
    return tuple(result)


def is_valid_category(slug: str) -> bool:
    """校验提交的栏目 slug 是否存在于当前配置树（防止伪造 / 失效值入库）。"""
    return bool(slug) and find_page(slug) is not None
