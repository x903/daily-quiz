"""认证栏目导航树、三级联动与预置百科介绍配置。

设计目的：
- 菜单（一级 / 二级 / 三级）、栏目详情页与题库导入选择器共用**同一份配置**。
  新增厂商 / 方向 / 等级只需在 ``CERT_NAV_ROOTS`` 里加一个 ``NavNode``，
  菜单项、面包屑、子栏目卡片、URL、导入选择器与每日答题联动会自动生成。
- 别名 ``CERT_NAV`` 保留「阿里云认证」单树，供 ``all_pages()`` 与
  ``register_acp_direction()`` 等既有调用与回归脚本使用。
- 本模块只描述导航、文案与预置资料，不依赖 Flask / 数据库，便于单独测试。

三级语义（厂商 → 方向 → 等级）：
    华为认证 / AWS 认证 / 思科认证 / 阿里云认证（一级厂商）
        → 数通、云计算、安全/存储、架构设计……（二级方向）
            → HCIP-数通、CCNA、SAA、CLF……（三级具体认证 / 等级）

预置百科介绍（``INTRO_PRESETS``）按三级叶子 slug 索引，由
``models.seed_cert_intros()`` 幂等写入 ``cert_intro`` 表；管理员在后台
「认证介绍管理」里修改的是数据库副本，配置里的文本只作为兜底。
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


# ---------------------------------------------------------------------------
# 一级：阿里云认证（既有三级树，保持不变）
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 新增一级厂商：华为 / AWS / 思科（均为「厂商 → 方向 → 等级」三级树）
# ---------------------------------------------------------------------------

HUAWEI_CERT: NavNode = NavNode(
    label="华为认证",
    slug="huawei-cert",
    intro="华为认证覆盖数通、云计算、安全、存储等 ICT 技术方向，分为 HCIA / HCIP / HCIE 三个等级。",
    children=(
        NavNode(
            label="数通",
            slug="huawei-datacom",
            intro="数通（Datacom）方向：企业级路由交换、BGP/OSPF/VXLAN、网络自动化与故障排查。",
            children=(
                NavNode(
                    label="HCIP-数通",
                    slug="hcip-datacom",
                    intro="华为HCIP数通属于中级专业认证。它代表具备独立规划、调试中小型企业网络及处理真实场景问题的能力，是企业网络运维、系统集成等岗位的核心敲门砖，也是通往高级专家HCIE的重要跳板。",
                ),
            ),
        ),
        NavNode(
            label="云计算",
            slug="huawei-cloud",
            intro="云计算方向：华为云平台部署、虚拟化、容器、云资源调度与云容灾备份。",
            children=(
                NavNode(
                    label="HCIP-云计算",
                    slug="hcip-cloud",
                    intro="聚焦华为云平台部署、虚拟化技术、容器技术、云资源调度与云容灾备份等核心内容。随着企业全面上云，它是云计算运维工程师、云解决方案工程师的高薪核心证书。",
                ),
            ),
        ),
        NavNode(
            label="安全 / 存储方向",
            slug="huawei-security-storage",
            intro="安全方向聚焦网络安全架构与防护运维；存储方向聚焦企业级存储系统的设计、部署与容灾。",
            children=(
                NavNode(
                    label="HCIP-安全",
                    slug="hcip-security",
                    intro="华为 HCIP-Security 是安全方向的中级认证，面向具备网络安全基础的工程师，覆盖防火墙高可靠性、VPN、入侵防御、终端与云安全、安全运维等内容，证明持证人具备大中型企业网络安全架构的设计、部署与运维能力，是安全工程师与等保相关岗位的重要资质。",
                ),
                NavNode(
                    label="HCIP-存储",
                    slug="hcip-storage",
                    intro="华为 HCIP-Storage 是存储方向的中级认证，考核闪存与分布式存储系统的规划设计、部署实施、性能调优、日常运维与故障处理，覆盖 RAID、SAN/NAS、备份容灾等核心技术，适合存储工程师与数据中心运维岗位。",
                ),
            ),
        ),
    ),
)


AWS_CERT: NavNode = NavNode(
    label="AWS 认证",
    slug="aws-cert",
    intro="亚马逊云科技认证按基础级（Foundational）、助理级（Associate）、专业级（Professional）与专项级（Specialty）分层。",
    children=(
        NavNode(
            label="架构设计",
            slug="aws-architecture",
            intro="架构设计方向：基于 AWS Well-Architected Framework 设计安全、弹性、高性能且成本优化的云架构。",
            children=(
                NavNode(
                    label="Solutions Architect (解决方案架构师)",
                    slug="aws-solutions-architect",
                    intro="主要考核如何基于 AWS Well-Architected Framework，设计安全、弹性、高性能且成本优化的云架构。是亚马逊云科技认可度极高、全球通用的黄金架构师证书。",
                ),
            ),
        ),
        NavNode(
            label="云基础",
            slug="aws-cloud-foundation",
            intro="云基础方向：面向零基础学习者，验证对 AWS 云服务、核心架构与高阶术语的整体理解。",
            children=(
                NavNode(
                    label="Cloud Practitioner (云从业者)",
                    slug="aws-cloud-practitioner",
                    intro="零基础入云的入门级认证。不要求深入的技术背景，重点验证对AWS云服务、核心架构和高阶术语的基础理解，是转行云职业的最佳起点。",
                ),
            ),
        ),
        NavNode(
            label="DevOps",
            slug="aws-devops",
            intro="DevOps 方向：在 AWS 上预置、运行和管理分布式应用系统，覆盖持续交付、自动化与监控。",
            children=(
                NavNode(
                    label="DevOps Engineer (运维/DevOps工程师)",
                    slug="aws-devops-engineer",
                    intro="AWS Certified DevOps Engineer - Professional（DOP-C02）是专业级认证，面向具有两年以上 AWS 经验的 DevOps 工程师，考核在 AWS 上预置、运行和管理分布式应用系统的能力，涵盖持续交付、自动化编排、监控告警、安全合规与高可用架构，是云运维 / DevOps 路线的进阶证书。",
                ),
            ),
        ),
    ),
)


CISCO_CERT: NavNode = NavNode(
    label="思科认证",
    slug="cisco-cert",
    intro="思科认证按助理级（Associate / CCNA）、专业级（Professional / CCNP）与专家级（Expert / CCIE）分层。",
    children=(
        NavNode(
            label="网络基础",
            slug="cisco-network-fundamentals",
            intro="网络基础方向：网络基础知识、IP 连接与服务、安全基础、自动化与可编程性。",
            children=(
                NavNode(
                    label="CCNA (网络工程师)",
                    slug="ccna",
                    intro="IT网络行业的基石认证。全面考察网络基础、IP连接性、IP服务、安全基础和自动化编程，证明持证人具备配置、运维中小型网络的能力。",
                ),
            ),
        ),
        NavNode(
            label="企业网络",
            slug="cisco-enterprise",
            intro="企业网络方向：企业级复杂网络的核心架构、虚拟化、保障、安全与自动化，外加一门专项选考。",
            children=(
                NavNode(
                    label="CCNP (高级网络工程师)",
                    slug="ccnp",
                    intro="面向有经验的技术人员，深入考察企业级复杂网络的设计、实施和故障排除，是企业内部高级网络架构师的进阶必备证书。",
                ),
            ),
        ),
    ),
)


def nav_roots() -> tuple[NavNode, ...]:
    """返回全部一级厂商菜单（动态读取 ``CERT_NAV``，运行时扩展立即生效）。

    顺序上阿里云认证恒为第一个，既有导入选择器与回归脚本依赖这一顺序。
    """
    return (CERT_NAV, HUAWEI_CERT, AWS_CERT, CISCO_CERT)


# ---------------------------------------------------------------------------
# 预置百科介绍：与三级叶子一一对应，由 models.seed_cert_intros 幂等写库
# ---------------------------------------------------------------------------

INTRO_PRESETS: dict[str, dict[str, str]] = {
    "hcip-datacom": {
        "cert_name": "HCIP-数通", "vendor": "华为认证", "direction": "数通", "level": "HCIP",
        "intro": "华为HCIP数通属于中级专业认证。它代表具备独立规划、调试中小型企业网络及处理真实场景问题的能力，是企业网络运维、系统集成等岗位的核心敲门砖，也是通往高级专家HCIE的重要跳板。",
        "source": "华为人才在线 · HCIP-Datacom（H12-821 / H12-831）",
    },
    "hcip-cloud": {
        "cert_name": "HCIP-云计算", "vendor": "华为认证", "direction": "云计算", "level": "HCIP",
        "intro": "聚焦华为云平台部署、虚拟化技术、容器技术、云资源调度与云容灾备份等核心内容。随着企业全面上云，它是云计算运维工程师、云解决方案工程师的高薪核心证书。",
        "source": "华为人才在线 · HCIP-Cloud Computing",
    },
    "hcip-security": {
        "cert_name": "HCIP-安全", "vendor": "华为认证", "direction": "安全 / 存储方向", "level": "HCIP",
        "intro": "华为 HCIP-Security 是安全方向的中级认证，面向具备网络安全基础的工程师，覆盖防火墙高可靠性、VPN、入侵防御、终端与云安全、安全运维等内容，证明持证人具备大中型企业网络安全架构的设计、部署与运维能力，是安全工程师与等保相关岗位的重要资质。",
        "source": "华为人才在线 · HCIP-Security / 华为认证方向百科",
    },
    "hcip-storage": {
        "cert_name": "HCIP-存储", "vendor": "华为认证", "direction": "安全 / 存储方向", "level": "HCIP",
        "intro": "华为 HCIP-Storage 是存储方向的中级认证，考核闪存与分布式存储系统的规划设计、部署实施、性能调优、日常运维与故障处理，覆盖 RAID、SAN/NAS、备份容灾等核心技术，适合存储工程师与数据中心运维岗位。",
        "source": "华为企业业务 · HCIP-Storage V5.5（e.huawei.com）",
    },
    "aws-solutions-architect": {
        "cert_name": "Solutions Architect (解决方案架构师)", "vendor": "AWS 认证", "direction": "架构设计", "level": "Associate / SAA-C03",
        "intro": "主要考核如何基于 AWS Well-Architected Framework，设计安全、弹性、高性能且成本优化的云架构。是亚马逊云科技认可度极高、全球通用的黄金架构师证书。",
        "source": "AWS 官方考试指南 · Solutions Architect - Associate (SAA-C03)",
    },
    "aws-cloud-practitioner": {
        "cert_name": "Cloud Practitioner (云从业者)", "vendor": "AWS 认证", "direction": "云基础", "level": "Foundational / CLF-C02",
        "intro": "零基础入云的入门级认证。不要求深入的技术背景，重点验证对AWS云服务、核心架构和高阶术语的基础理解，是转行云职业的最佳起点。",
        "source": "AWS 官方考试指南 · Cloud Practitioner (CLF-C02)",
    },
    "aws-devops-engineer": {
        "cert_name": "DevOps Engineer (运维/DevOps工程师)", "vendor": "AWS 认证", "direction": "DevOps", "level": "Professional / DOP-C02",
        "intro": "AWS Certified DevOps Engineer - Professional（DOP-C02）是专业级认证，面向具有两年以上 AWS 经验的 DevOps 工程师，考核在 AWS 上预置、运行和管理分布式应用系统的能力，涵盖持续交付、自动化编排、监控告警、安全合规与高可用架构，是云运维 / DevOps 路线的进阶证书。",
        "source": "AWS 官方考试指南 · DevOps Engineer - Professional (DOP-C02)",
    },
    "ccna": {
        "cert_name": "CCNA (网络工程师)", "vendor": "思科认证", "direction": "网络基础", "level": "Associate / 200-301",
        "intro": "IT网络行业的基石认证。全面考察网络基础、IP连接性、IP服务、安全基础和自动化编程，证明持证人具备配置、运维中小型网络的能力。",
        "source": "Cisco U. · CCNA (200-301)",
    },
    "ccnp": {
        "cert_name": "CCNP (高级网络工程师)", "vendor": "思科认证", "direction": "企业网络", "level": "Professional / CCNP Enterprise",
        "intro": "面向有经验的技术人员，深入考察企业级复杂网络的设计、实施和故障排除，是企业内部高级网络架构师的进阶必备证书。",
        "source": "Cisco U. · CCNP Enterprise (ENCORE 350-401)",
    },
}


def intro_preset_slugs() -> tuple[str, ...]:
    """返回所有预置介绍的三级叶子 slug。"""
    return tuple(INTRO_PRESETS)


# ---------------------------------------------------------------------------
# 页面遍历与查找
# ---------------------------------------------------------------------------

def iter_pages(
    root: Optional[NavNode] = None,
    _parent: Optional[NavNode] = None,
    _depth: int = 1,
    _crumbs: tuple[NavNode, ...] = (),
) -> Iterator[CertPage]:
    """深度优先遍历菜单树，产出每个节点的页面上下文。

    Args:
        root: 起始节点，默认取 ``CERT_NAV``（保持 ``all_pages()`` 只索引阿里云
              栏目页的既有语义）；需要遍历全部厂商请用 ``iter_all_pages()``。
        其余以下划线开头的参数仅供递归内部使用。

    Yields:
        CertPage，含节点、父节点、层级与从根到当前节点的面包屑。
    """
    node = root if root is not None else CERT_NAV
    crumbs = (*_crumbs, node)
    yield CertPage(node=node, parent=_parent, depth=_depth, crumbs=crumbs)
    for child in node.children:
        yield from iter_pages(child, node, _depth + 1, crumbs)


def iter_all_pages() -> Iterator[CertPage]:
    """遍历全部一级厂商下的所有页面（含新增的华为 / AWS / 思科）。"""
    for root in nav_roots():
        yield from iter_pages(root)


def find_page(slug: str) -> Optional[CertPage]:
    """按 slug 在**全部厂商**中查找页面配置，未找到返回 None。"""
    return next((p for p in iter_all_pages() if p.node.slug == slug), None)


def all_pages() -> dict[str, CertPage]:
    """返回「阿里云认证」栏目页的 slug → CertPage 索引（既有兼容接口）。"""
    return {p.node.slug: p for p in iter_pages()}


def category_triplet(slug: str) -> dict[str, str]:
    """把栏目路径归一为「厂商 / 方向 / 等级」三段，供每日答题联动回显。"""
    page = find_page(slug)
    if page is None:
        return {"vendor": "", "direction": "", "level": "", "path": ""}
    crumbs = page.crumbs
    labels = [c.label for c in crumbs]
    return {
        "vendor": labels[0] if labels else "",
        "direction": labels[1] if len(labels) > 1 else "",
        "level": labels[2] if len(labels) > 2 else labels[-1],
        "path": " › ".join(labels),
    }


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
    """导出可 JSON 序列化的**全部厂商**栏目树，供导入页 / 编辑页 / 每日答题使用。

    Returns:
        根节点列表（每个节点含 label / slug / children）。返回列表而非单个根，
        是为了支持多个一级认证体系时选择器无需改动；阿里云认证恒为第一个。
    """
    def to_dict(node: NavNode) -> dict:
        return {
            "label": node.label,
            "slug": node.slug,
            "children": [to_dict(child) for child in node.children],
        }

    return [to_dict(root) for root in nav_roots()]


def practice_tree_payload() -> list[dict]:
    """每日答题三级联动（厂商 → 方向 → 等级）的配置树。"""
    return category_tree_payload()


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
