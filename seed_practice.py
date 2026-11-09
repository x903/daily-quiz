"""播种自由练习示例题库（幂等）。

内容来源：
- 70 道单选题来自 `data/practice_bank_singles.json`（由站内 ACP 模拟题整理）；
- 30 道多选题在本文件内维护，覆盖 ECS / VPC / OSS / 负载均衡 / 数据库 /
  安全 / 可观测 / 成本与高可用，满足「70 单选 + 30 多选 = 100 题」。

执行：
    python seed_practice.py
    docker compose exec web python seed_practice.py

已存在的 slug 会跳过，不会覆盖管理员后续编辑。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import models
import practice_bank

BASE_DIR = Path(__file__).resolve().parent
SINGLES_FILE = BASE_DIR / "data" / "practice_bank_singles.json"

# (题干, 选项, 正确答案, 解析)
MULTIPLE_QUESTIONS = [
    (
        "关于 ECS 突发性能实例（t5 / t6），下列说法正确的有？",
        {"A": "通过 CPU 积分机制提供基准性能之上的突发能力",
         "B": "CPU 积分耗尽后实例会自动关机",
         "C": "可开启无性能约束模式，超出基准的部分按量计费",
         "D": "适合 CPU 使用率长期处于低位的轻负载业务"},
        "A,C,D",
        "积分耗尽后实例不会被关机，而是被限制在基准性能；无性能约束模式允许透支积分并产生额外费用；突发性能实例适合低负载、偶发突发的场景。",
    ),
    (
        "关于 ESSD 云盘的性能级别（PL0–PL3）与 ESSD AutoPL，说法正确的有？",
        {"A": "同一容量下，PL 级别越高，IOPS 与吞吐上限越高",
         "B": "ESSD AutoPL 可按实际 IOPS 弹性计费",
         "C": "ESSD 的 IOPS 上限只由容量决定，与 PL 级别无关",
         "D": "提升 PL 级别通常是原地变配，无需迁移数据或停机"},
        "A,B,D",
        "ESSD 的 IOPS/吞吐上限由容量与性能级别共同决定，C 错误；PL 变配为原地操作，不迁移数据。",
    ),
    (
        "关于 ECS 快照，下列说法正确的有？",
        {"A": "快照用于备份云盘数据，可用于创建新云盘或回滚",
         "B": "快照一致性组可保证多块云盘在同一时间点的一致性",
         "C": "自动快照策略可按周期为指定云盘创建快照",
         "D": "快照必须先下载到本地，才能恢复数据"},
        "A,B,C",
        "快照可直接用于创建云盘或回滚，无需下载；一致性组用于数据库等多盘协同场景。",
    ),
    (
        "关于弹性伸缩（ESS），下列说法正确的有？",
        {"A": "伸缩组需要关联伸缩配置或启动模板",
         "B": "冷却时间用于避免短时间内反复扩缩容",
         "C": "生命周期挂钩可在实例加入或移出伸缩组时执行自定义操作",
         "D": "伸缩组只能按固定数量扩容，不支持报警触发"},
        "A,B,C",
        "弹性伸缩支持定时、报警、动态规则等多种触发方式，D 错误。",
    ),
    (
        "关于 ECS 部署集，下列说法正确的有？",
        {"A": "分散分布可降低多台实例落在同一宿主机的风险",
         "B": "紧凑分布适合对网络延迟敏感的业务",
         "C": "严格分布对实例规格与数量有更严格的限制",
         "D": "部署集会自动为实例创建快照"},
        "A,B,C",
        "部署集只控制实例的物理分布，不承担备份职责，D 错误。",
    ),
    (
        "关于 VPC 路由，下列说法正确的有？",
        {"A": "VPC 创建后会自动生成系统路由表",
         "B": "自定义路由表可绑定到交换机，实现子网级流量控制",
         "C": "路由条目由目标网段与下一跳类型组成",
         "D": "删除 VPC 后，路由表仍会保留并继续转发流量"},
        "A,B,C",
        "VPC 删除后其路由表随之失效，不会继续转发，D 错误。",
    ),
    (
        "关于安全组，下列说法正确的有？",
        {"A": "安全组是有状态的，返回流量会自动放行",
         "B": "默认拒绝所有入方向流量，需要显式放行",
         "C": "安全组规则支持按 IP、端口与协议配置",
         "D": "安全组作用于交换机层级，不能绑定实例"},
        "A,B,C",
        "安全组绑定实例或弹性网卡；作用于交换机层级的是网络 ACL。",
    ),
    (
        "关于 NAT 网关，下列说法正确的有？",
        {"A": "SNAT 使 VPC 内无公网 IP 的实例访问公网",
         "B": "DNAT 可将公网请求映射到内网实例",
         "C": "多个 EIP 可绑定同一 NAT 网关，提升带宽与连接能力",
         "D": "NAT 网关会自动为 ECS 分配公网 IP"},
        "A,B,C",
        "NAT 网关只做地址转换，不会改变 ECS 是否拥有公网 IP。",
    ),
    (
        "关于 VPC 与本地 IDC 互联的高可用，下列说法正确的有？",
        {"A": "IPsec-VPN 建议配置双隧道",
         "B": "专线可结合 BGP 实现主备或负载分担",
         "C": "可同时使用专线与 VPN 做冗余",
         "D": "单条专线天然具备运营商级冗余，无需备份"},
        "A,B,C",
        "单条专线仍可能因施工、光缆故障中断，生产环境应设计冗余链路。",
    ),
    (
        "关于 VPC 流日志，下列说法正确的有？",
        {"A": "可记录弹性网卡级别的出入方向流量",
         "B": "可投递到日志服务 SLS 进行查询与告警",
         "C": "适用于排查异常连接与满足合规留存",
         "D": "开启后会自动阻断攻击流量"},
        "A,B,C",
        "流日志只负责记录，不负责阻断；阻断需要安全组、网络 ACL、WAF 或高防等。",
    ),
    (
        "关于 CLB、ALB、NLB 的选型，下列说法正确的有？",
        {"A": "CLB 同时支持四层与七层负载均衡",
         "B": "ALB 面向七层，支持基于域名与路径的转发",
         "C": "NLB 面向四层，适合超高性能与长连接场景",
         "D": "ALB 只能按 IP 转发，不支持域名"},
        "A,B,C",
        "ALB 支持域名、路径、Header 等七层转发条件，D 错误。",
    ),
    (
        "关于 ALB 健康检查，下列说法正确的有？",
        {"A": "健康检查路径需要返回配置的期望状态码",
         "B": "检查间隔、超时时间与健康/不健康阈值均可配置",
         "C": "后端全部不健康时，ALB 会向客户端返回 5xx",
         "D": "健康检查失败后，ALB 会自动删除该 ECS 实例"},
        "A,B,C",
        "健康检查失败只会摘除后端，不会删除实例，D 错误。",
    ),
    (
        "关于 CDN 缓存，下列说法正确的有？",
        {"A": "缓存键决定哪些请求参数参与缓存区分",
         "B": "忽略全部 URL 参数可能让不同内容命中同一缓存",
         "C": "预热可提前把静态资源推送到边缘节点",
         "D": "刷新缓存会同时删除源站文件"},
        "A,B,C",
        "刷新只清理边缘节点缓存，不会影响源站文件。",
    ),
    (
        "关于 OSS 访问控制，下列说法正确的有？",
        {"A": "Bucket ACL 可设置为私有、公共读、公共读写",
         "B": "RAM Policy 可精细控制到 Bucket、对象前缀与操作",
         "C": "STS 可颁发有时效的临时访问凭证",
         "D": "开启公共读写后，任何访问都不再产生费用"},
        "A,B,C",
        "公共读写只改变访问权限，不改变计费；且会带来严重的安全风险。",
    ),
    (
        "关于 OSS 数据保护，下列说法正确的有？",
        {"A": "版本控制可保留对象的历史版本",
         "B": "合规保留策略（WORM）可在保留期内禁止修改与删除",
         "C": "跨区域复制可将数据异步复制到另一地域",
         "D": "生命周期规则只能删除对象，不能转换存储类型"},
        "A,B,C",
        "生命周期支持转低频、归档、冷归档以及删除，D 错误。",
    ),
    (
        "关于 OSS 大文件上传，下列说法正确的有？",
        {"A": "分片上传支持断点续传",
         "B": "传输加速可优化跨地域、跨国上传体验",
         "C": "签名 URL 可让客户端直传而无需暴露 AccessKey",
         "D": "分片越大，失败重传的成本越低"},
        "A,B,C",
        "分片越大，单次失败需要重传的数据越多，重传成本越高；分片大小需要权衡。",
    ),
    (
        "关于 RDS MySQL 高可用，下列说法正确的有？",
        {"A": "主备实例可自动故障切换",
         "B": "只读实例可分担读流量",
         "C": "跨地域灾备可提升地域级容灾能力",
         "D": "主备切换后原主库数据一定全部丢失"},
        "A,B,C",
        "主备切换基于数据复制，通常不会丢失已同步数据，D 错误。",
    ),
    (
        "关于 RDS 备份与恢复，下列说法正确的有？",
        {"A": "自动备份结合日志备份可支持按时间点恢复",
         "B": "备份文件可下载或跨地域备份",
         "C": "恢复数据通常创建新实例，避免覆盖生产实例",
         "D": "开启备份会显著增加 CPU 并导致业务中断"},
        "A,B,C",
        "备份为在线操作，通常不影响业务连续性，D 错误。",
    ),
    (
        "关于 PolarDB MySQL，下列说法正确的有？",
        {"A": "采用存储与计算分离架构",
         "B": "支持一写多读，读节点可快速扩展",
         "C": "支持按需秒级弹性",
         "D": "数据只能存放在本地盘，无法共享存储"},
        "A,B,C",
        "PolarDB 使用共享分布式存储，D 错误。",
    ),
    (
        "关于云数据库 Redis，下列说法正确的有？",
        {"A": "集群版通过分片提升容量与吞吐",
         "B": "读写分离版可分担读请求",
         "C": "大 Key 可能导致单分片内存与网络瓶颈",
         "D": "热 Key 问题只能通过升级实例规格解决"},
        "A,B,C",
        "热 Key 可通过本地缓存、读写分离、Key 拆分、集群多分片等组合手段治理，D 过于绝对。",
    ),
    (
        "关于 RAM 身份体系，下列说法正确的有？",
        {"A": "主账号拥有全部权限，应妥善保管",
         "B": "RAM 用户是账号内的独立身份，可分配权限",
         "C": "RAM 角色可被云服务或可信实体扮演",
         "D": "STS 临时凭证的有效期可以无限延长"},
        "A,B,C",
        "STS 临时凭证有明确的有效期，不能无限延长，D 错误。",
    ),
    (
        "关于 RAM 权限策略，下列说法正确的有？",
        {"A": "显式 Deny 优先于 Allow",
         "B": "可通过 Condition 限制源 IP、MFA、时间等条件",
         "C": "授权范围应遵循最小权限原则",
         "D": "给 RAM 用户授予 AdministratorAccess 是最安全的做法"},
        "A,B,C",
        "授予管理员权限违背最小权限原则，风险极高，D 错误。",
    ),
    (
        "关于云安全中心，下列说法正确的有？",
        {"A": "可发现漏洞、基线风险与异常登录",
         "B": "支持防勒索与主动防御能力",
         "C": "可对多台 ECS 统一管理安全告警",
         "D": "只能防护阿里云外的 IDC 主机"},
        "A,B,C",
        "云安全中心主要面向阿里云资产，也支持部分混合云接入，D 错误。",
    ),
    (
        "关于 Web 应用防火墙（WAF），下列说法正确的有？",
        {"A": "可防护 SQL 注入、XSS 等应用层攻击",
         "B": "支持 CC 防护与自定义访问控制规则",
         "C": "接入后需要把域名解析指向 WAF 提供的地址",
         "D": "WAF 可以替代安全组实现四层端口封禁"},
        "A,B,C",
        "WAF 主要防护七层流量，四层端口控制仍应使用安全组或网络 ACL。",
    ),
    (
        "关于 DDoS 防护，下列说法正确的有？",
        {"A": "基础防护提供一定阈值内的免费清洗",
         "B": "高防实例可提供更大的清洗能力与清洗中心",
         "C": "黑洞是攻击超过阈值时云平台采取的一种限流保护",
         "D": "部署 WAF 后就不会再遭受 DDoS 攻击"},
        "A,B,C",
        "WAF 与 DDoS 防护层面不同，不能互相替代，D 错误。",
    ),
    (
        "关于日志服务 SLS，下列说法正确的有？",
        {"A": "支持采集 ECS、容器与应用日志",
         "B": "支持类 SQL 查询与实时分析",
         "C": "可配置告警并推送到通知渠道",
         "D": "日志只能保存 7 天，无法调整"},
        "A,B,C",
        "日志留存时间可按存储类型与配置调整，D 错误。",
    ),
    (
        "关于云监控，下列说法正确的有？",
        {"A": "可查看 ECS、RDS、SLB 等云产品指标",
         "B": "支持自定义监控与报警规则",
         "C": "可配置报警联系人组与通知方式",
         "D": "只能监控 CPU，不能监控磁盘与网络"},
        "A,B,C",
        "云监控覆盖 CPU、磁盘、网络等多类指标，D 错误。",
    ),
    (
        "关于云上成本优化，下列说法正确的有？",
        {"A": "长期稳定负载适合包年包月或节省计划",
         "B": "波动负载适合按量付费",
         "C": "可中断的离线任务适合抢占式实例",
         "D": "所有实例都应选择最高规格以保证性能"},
        "A,B,C",
        "规格应按实际负载选型，过度配置会造成浪费，D 错误。",
    ),
    (
        "关于多可用区高可用架构，下列说法正确的有？",
        {"A": "应用实例分散到多个可用区",
         "B": "使用 SLB 或 ALB 将流量分发到多可用区",
         "C": "数据库采用主备或集群版",
         "D": "所有实例部署在同一台物理机上即可实现高可用"},
        "A,B,C",
        "单机部署是典型的单点故障，D 错误。",
    ),
    (
        "关于容灾设计，下列说法正确的有？",
        {"A": "RTO 指业务恢复所需的时间",
         "B": "RPO 指可容忍的数据丢失量",
         "C": "同城双活可降低单可用区故障的影响",
         "D": "做了备份就一定不需要容灾演练"},
        "A,B,C",
        "备份不等于可恢复，必须通过定期演练验证恢复流程与 RTO/RPO，D 错误。",
    ),
]


def _load_singles(limit: int = 70) -> list[dict]:
    data = json.loads(SINGLES_FILE.read_text(encoding="utf-8"))
    items = []
    for raw in data[:limit]:
        items.append({
            "type": "single",
            "question": raw["question"],
            "options": raw["options"],
            "answer": raw["answer"],
            "explanation": raw.get("explanation", ""),
        })
    return items


def _multiples() -> list[dict]:
    items = []
    for question, options, answer, explanation in MULTIPLE_QUESTIONS:
        items.append({
            "type": "multiple",
            "question": question,
            "options": options,
            "answer": [x.strip() for x in answer.split(",")],
            "explanation": explanation,
        })
    return items


def _pick(pool: list[dict], pattern: str, count: int) -> list[dict]:
    """优先挑选命中主题的题，不够时用同题型其它题补齐，保证题量准确。"""
    regex = re.compile(pattern)
    chosen, rest = [], []
    for item in pool:
        (chosen if regex.search(item["question"]) else rest).append(item)
    return (chosen + rest)[:count]


def _create(slug: str, payload: dict, questions: list[dict]) -> None:
    if practice_bank.get_bank(slug):
        print(f"[seed] 跳过已存在题库 {slug}")
        return
    bank = practice_bank.create_bank(dict(payload, slug=slug), created_by="seed")
    for question in questions:
        practice_bank.add_question(bank["id"], question)
    bank = practice_bank.get_bank(bank["id"], by_id=True)
    errors = practice_bank.publish_check(bank)
    if errors:
        print(f"[seed] {slug} 发布校验失败：{'；'.join(errors)}")
        return
    practice_bank.set_bank_status(bank["id"], "published")
    print(f"[seed] 已发布 {bank['name']}：{bank['question_count']} 题 "
          f"（{bank['single_count']} 单选 + {bank['multiple_count']} 多选）")


def main() -> None:
    models.init_db()
    singles = _load_singles(70)
    multiples = _multiples()
    if len(singles) != 70 or len(multiples) != 30:
        raise SystemExit(f"种子题量不正确：{len(singles)} 单选 + {len(multiples)} 多选")

    # 1) 阿里云 ACP 云计算全真模拟：严格按 100 题（70 单选 + 30 多选）
    _create(
        "aliyun-acp-cloud-mock",
        {
            "name": "阿里云 ACP 云计算高级工程师 · 全真模拟卷",
            "cert_path": "aliyun/acp/cloud",
            "level_tag": "冲刺",
            "difficulty": "中等",
            "rule_code": "aliyun-acp-cloud",
            "description": "严格按 ACP 云计算真实考试规则组卷：100 题（70 单选 + 30 多选），120 分钟，80 分合格，超时自动交卷。",
            "sort_order": 1,
        },
        singles + multiples,
    )

    # 2) ECS 专项：30 题（20 单选 + 10 多选），自定义规则
    _create(
        "ecs-essentials",
        {
            "name": "ECS 弹性计算专项题库",
            "cert_path": "aliyun/acp/cloud",
            "level_tag": "基础",
            "difficulty": "简单",
            "rule_code": "custom",
            "total_questions": 30,
            "single_count": 20,
            "multiple_count": 10,
            "total_score": 30,
            "pass_score": 24,
            "duration_minutes": 45,
            "description": "聚焦 ECS 实例、云盘、快照、部署集与弹性伸缩，适合打牢计算底座。",
            "sort_order": 2,
        },
        _pick(singles, r"ECS|云盘|快照|镜像|实例|弹性伸缩|部署集|DDH|抢占式|用户数据", 20)
        + _pick(multiples, r"ECS|云盘|快照|部署集|弹性伸缩|突发性能|ESSD", 10),
    )

    # 3) VPC 专项：25 题（18 单选 + 7 多选），自定义规则
    _create(
        "vpc-architecture",
        {
            "name": "VPC 网络架构模拟卷",
            "cert_path": "aliyun/acp/cloud",
            "level_tag": "进阶",
            "difficulty": "中等",
            "rule_code": "custom",
            "total_questions": 25,
            "single_count": 18,
            "multiple_count": 7,
            "total_score": 25,
            "pass_score": 20,
            "duration_minutes": 30,
            "description": "覆盖 VPC、交换机、路由、安全组、NAT、专线与混合云互联，练网络架构判断。",
            "sort_order": 3,
        },
        _pick(singles, r"VPC|交换机|路由|EIP|NAT|VPN|专线|IPv6|网络 ACL|对等连接|CEN", 18)
        + _pick(multiples, r"VPC|NAT|安全组|专线|VPN|路由|流日志", 7),
    )


if __name__ == "__main__":
    main()
