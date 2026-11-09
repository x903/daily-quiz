"""初始化示例题库：为 4 个旗舰认证各写入 10 道题（幂等，已有题目则跳过）。

用法：
    python seed_bank.py          # 本地 SQLite / 环境变量指定的 MySQL
    docker compose exec web python seed_bank.py
"""
from __future__ import annotations

import bank

QUESTIONS = {
    "aliyun/acp/cloud": [
        ("single", "阿里云中用于隔离云上网络的逻辑边界是？",
         {"A": "安全组", "B": "专有网络 VPC", "C": "负载均衡 SLB", "D": "云监控"}, "B",
         "VPC 提供逻辑隔离的私有网络，可自定义网段、交换机与路由；安全组是实例级访问控制。"),
        ("single", "ECS 实例的弹性公网 IP（EIP）解绑后，以下说法正确的是？",
         {"A": "EIP 随实例一起释放", "B": "EIP 保留在账号下，可绑定到其它实例",
          "C": "EIP 自动转为私网地址", "D": "EIP 立即失效"}, "B",
         "EIP 是独立资源，解绑后保留在账号中，可重新绑定到同地域其它实例。"),
        ("single", "跨可用区部署 SLB 后端服务器的主要目的是？",
         {"A": "降低带宽成本", "B": "提升可用性，单可用区故障时仍可服务",
          "C": "减少安全组规则", "D": "提高磁盘 IOPS"}, "B",
         "多可用区部署可避免单可用区故障导致整体不可用，是典型的高可用设计。"),
        ("single", "OSS 中用于控制对象访问权限的机制不包括？",
         {"A": "Bucket ACL", "B": "RAM Policy", "C": "STS 临时凭证", "D": "安全组规则"}, "D",
         "安全组作用于 ECS 网络层，不用于 OSS 的对象权限控制。"),
        ("multiple", "以下哪些属于阿里云 RDS 提供的高可用能力？",
         {"A": "主备实例自动切换", "B": "只读实例分流", "C": "跨地域备份", "D": "自动扩容磁盘"}, "A,B,C",
         "RDS 支持主备高可用、只读实例与备份恢复；磁盘扩容需手动或按策略触发，不属于高可用切换能力。"),
        ("single", "云监控中配置报警规则时，最适合用于触发扩容的指标是？",
         {"A": "CPU 使用率持续高于 70%", "B": "磁盘剩余空间 90%",
          "C": "实例重启次数", "D": "安全组规则数量"}, "A",
         "CPU/内存等负载指标是弹性伸缩的常用触发条件。"),
        ("judge", "阿里云 SLB 支持四层（TCP/UDP）与七层（HTTP/HTTPS）负载均衡。",
         {"A": "正确", "B": "错误"}, "A",
         "SLB 提供四层与七层能力，七层支持基于域名/路径的转发。"),
        ("single", "VPC 中交换机（vSwitch）的作用是？",
         {"A": "划分可用区内的子网", "B": "提供公网 IP", "C": "做应用层路由", "D": "存储对象数据"}, "A",
         "vSwitch 是 VPC 内的子网划分，且必须属于某一个可用区。"),
        ("single", "为降低 OSS 公网流量成本，最合理的做法是？",
         {"A": "全部使用公网访问", "B": "通过 CDN 回源并使用内网 Endpoint",
          "C": "关闭 Bucket 日志", "D": "把对象改为低频存储即可"}, "B",
         "同地域内网 Endpoint 免公网流量费，CDN 可降低回源压力与跨地域流量。"),
        ("single", "关于安全组与网络 ACL，说法正确的是？",
         {"A": "安全组是有状态的，网络 ACL 是无状态的", "B": "两者都是有状态",
          "C": "网络 ACL 作用于实例网卡", "D": "安全组只能拒绝流量"}, "A",
         "安全组有状态（返回流量自动放行），网络 ACL 无状态，需要分别配置进出方向。"),
    ],
    "huawei/datacom/hcip": [
        ("single", "OSPF 中 Router-ID 的选举规则是？",
         {"A": "最小接口 IP", "B": "最大 Loopback 地址，其次最大物理接口地址",
          "C": "随机生成", "D": "必须手工配置"}, "B",
         "优先使用手工配置的 Router-ID；未配置时选最大的 Loopback 地址，再选最大物理接口地址。"),
        ("single", "BGP 中用于避免路由环路的属性是？",
         {"A": "AS_Path", "B": "Local_Preference", "C": "MED", "D": "Next_Hop"}, "A",
         "AS_Path 记录经过的 AS 号，收到含本 AS 号的路由会被丢弃，从而防止环路。"),
        ("single", "BGP 选路中，以下哪项优先级最高？",
         {"A": "Local_Preference 较大", "B": "AS_Path 较短", "C": "MED 较小", "D": "Router-ID 较小"}, "A",
         "华为设备 BGP 选路顺序中，Local_Preference 先于 AS_Path 比较。"),
        ("single", "MPLS VPN 中用于区分不同客户路由的标识是？",
         {"A": "RD", "B": "RT", "C": "Label", "D": "VLAN ID"}, "A",
         "RD 用于在地址空间中区分不同 VPN 实例的前缀，RT 控制路由导入导出。"),
        ("multiple", "VXLAN 相比传统 VLAN 的优势包括？",
         {"A": "支持 16M 租户标识", "B": "可跨三层网络扩展二层域",
          "C": "完全不需要 Underlay 网络", "D": "支持 ECMP 负载分担"}, "A,B,D",
         "VXLAN 仍依赖 Underlay IP 网络提供可达性，不能脱离底层网络。"),
        ("single", "交换机上配置 Eth-Trunk 的主要目的是？",
         {"A": "增加带宽并提供链路冗余", "B": "隔离广播域", "C": "实现路由汇总", "D": "做 NAT 转换"}, "A",
         "链路聚合将多条物理链路捆绑为一条逻辑链路，提升带宽与可靠性。"),
        ("single", "STP 中根桥的选举依据是？",
         {"A": "最小 Bridge ID", "B": "最大端口速率", "C": "最小 MAC 地址", "D": "最大优先级"}, "A",
         "Bridge ID = 优先级 + MAC，数值最小者成为根桥，默认优先级 32768。"),
        ("judge", "OSPF 的 Stub 区域不允许 Type 5 LSA 进入。",
         {"A": "正确", "B": "错误"}, "A",
         "Stub 区域通过 Type 3 默认路由访问外部，禁止 Type 5 外部 LSA。"),
        ("single", "网络割接前最重要的准备是？",
         {"A": "准备好回滚方案与验证步骤", "B": "关闭所有日志", "C": "直接下发配置",
          "D": "先重启所有设备"}, "A",
         "割接必须有明确的回滚方案、窗口与验证清单，控制变更风险。"),
        ("single", "SD-WAN 相比传统专线的主要优势是？",
         {"A": "基于应用策略选路，降低成本并提升灵活性", "B": "完全不需要物理链路",
          "C": "只能用于数据中心内部", "D": "不支持加密"}, "A",
         "SD-WAN 通过集中控制器与应用识别实现智能选路，可混合使用多种链路。"),
    ],
    "aws/solutions-architect/associate": [
        ("single", "需要为跨多个可用区的高可用应用选择数据库，最合适的托管方案是？",
         {"A": "单可用区 RDS", "B": "Aurora Multi-AZ", "C": "EC2 自建单机 MySQL",
          "D": "DynamoDB 本地部署"}, "B",
         "Aurora Multi-AZ 提供跨 AZ 的存储副本与故障转移，是高可用托管数据库的首选。"),
        ("single", "S3 中用于防止对象被意外删除的机制是？",
         {"A": "版本控制 + MFA Delete", "B": "存储桶 ACL", "C": "生命周期规则", "D": "跨区域复制"}, "A",
         "版本控制保留历史版本，MFA Delete 为删除操作增加二次认证。"),
        ("single", "以下哪种方案最适合将静态网站托管成本降到最低？",
         {"A": "EC2 + EBS", "B": "S3 静态网站托管 + CloudFront", "C": "ECS Fargate",
          "D": "Lambda 每次渲染"}, "B",
         "S3 + CloudFront 无需服务器，按存储与请求计费，适合静态内容。"),
        ("single", "VPC 中私有子网访问互联网的正确做法是？",
         {"A": "给实例绑定公网 IP", "B": "通过 NAT Gateway 出网",
          "C": "配置 Internet Gateway 直连", "D": "使用 VPC Peering"}, "B",
         "私有子网没有公网路由，需通过 NAT Gateway 访问互联网，入站不可达。"),
        ("multiple", "IAM 策略中显式 Deny 的特点包括？",
         {"A": "优先级高于 Allow", "B": "可覆盖资源策略的允许",
          "C": "只对根账号生效", "D": "需要与 Allow 同时存在才生效"}, "A,B",
         "显式 Deny 在策略评估中始终优先，可覆盖身份策略与资源策略的 Allow。"),
        ("single", "ELB 健康检查失败时，默认行为是？",
         {"A": "继续转发流量", "B": "将实例从目标组摘除，不再转发",
          "C": "自动重启实例", "D": "降低实例权重到 0 但保持连接"}, "B",
         "不健康目标会被摘除，恢复健康后重新加入。"),
        ("judge", "AWS Well-Architected Framework 包含安全性、可靠性、性能效率、成本优化、卓越运营和可持续性六大支柱。",
         {"A": "正确", "B": "错误"}, "A",
         "六大支柱即安全、可靠、性能、成本、运营与可持续性。"),
        ("single", "需要解耦订单服务与库存服务，最合适的托管消息服务是？",
         {"A": "SQS", "B": "CloudFront", "C": "Route 53", "D": "EBS"}, "A",
         "SQS 提供托管队列，实现异步解耦与削峰填谷。"),
        ("single", "跨区域灾备中，RTO 最小时通常选择？",
         {"A": "备份恢复到新区域", "B": "热备（Active-Active）多区域部署",
          "C": "冷备", "D": "仅做快照"}, "B",
         "双活多区域可做到分钟级甚至近实时切换，RTO 最小，但成本最高。"),
        ("single", "Lambda 函数访问 VPC 内 RDS 时，需要配置？",
         {"A": "VPC 子网与安全组", "B": "仅 IAM 角色", "C": "公网网关", "D": "NAT 实例即可"}, "A",
         "Lambda 需加入目标 VPC 的子网并配置安全组，同时具备相应 IAM 权限。"),
    ],
    "cisco/ccna": [
        ("single", "IP 地址 192.168.10.130/26 所在子网的网络地址是？",
         {"A": "192.168.10.0", "B": "192.168.10.64", "C": "192.168.10.128", "D": "192.168.10.192"}, "C",
         "/26 掩码为 255.255.255.192，块大小 64，128 是 130 所在子网的网络地址。"),
        ("single", "VLAN 的主要作用是？",
         {"A": "扩大广播域", "B": "划分广播域", "C": "加密数据", "D": "分配公网 IP"}, "B",
         "VLAN 在二层划分广播域，减少广播风暴并提升安全隔离。"),
        ("single", "两台交换机之间需要承载多个 VLAN，应配置？",
         {"A": "Access 口", "B": "Trunk 口", "C": "Loopback 口", "D": "三层路由口"}, "B",
         "Trunk 通过 802.1Q 标签在一条链路上承载多个 VLAN。"),
        ("single", "OSPF 中 DR/BDR 选举发生在？",
         {"A": "任意两台路由器之间", "B": "广播或 NBMA 网络中", "C": "点到点网络", "D": "所有网络类型"}, "B",
         "点到点网络不需要 DR/BDR，广播与 NBMA 网络才进行选举。"),
        ("single", "标准 ACL 的匹配依据是？",
         {"A": "源 IP 地址", "B": "目的 IP 地址", "C": "源端口", "D": "协议类型"}, "A",
         "标准 ACL（1-99）只能匹配源 IP；扩展 ACL 可匹配源/目的 IP、端口与协议。"),
        ("single", "默认路由在路由表中的表示是？",
         {"A": "0.0.0.0/0", "B": "255.255.255.255/32", "C": "127.0.0.0/8", "D": "224.0.0.0/4"}, "A",
         "0.0.0.0/0 匹配所有目的地址，作为最后兜底路由。"),
        ("judge", "交换机收到未知单播帧时会泛洪到同一 VLAN 的其它端口。",
         {"A": "正确", "B": "错误"}, "A",
         "MAC 地址表中没有对应条目时，交换机会在 VLAN 内泛洪。"),
        ("single", "SSH 相比 Telnet 的核心优势是？",
         {"A": "速度更快", "B": "传输加密与身份认证", "C": "不需要 IP 地址", "D": "支持更多命令"}, "B",
         "SSH 对会话加密并提供设备认证，Telnet 明文传输不安全。"),
        ("single", "查看 Cisco 设备接口 IP 与状态的命令是？",
         {"A": "show ip interface brief", "B": "show running-config", "C": "show version",
          "D": "show mac address-table"}, "A",
         "show ip interface brief 以一屏表格显示接口、IP、状态与协议。"),
        ("single", "生成树协议中，非根桥到根桥开销最小的端口称为？",
         {"A": "根端口", "B": "指定端口", "C": "阻塞端口", "D": "边缘端口"}, "A",
         "每台非根桥选举一个根端口，到根桥路径开销最小。"),
    ],
}


def run() -> None:
    total = 0
    for path, items in QUESTIONS.items():
        category = bank.get_category_by_path(path)
        if not category:
            print(f"[skip] 分类不存在：{path}")
            continue
        if bank.count_questions(category["id"]) > 0:
            print(f"[skip] 已有题目：{category['cert_name']}")
            continue
        for qtype, stem, options, answer, explanation in items:
            bank.create_question(
                {
                    "type": qtype,
                    "question": stem,
                    "options": options,
                    "answer": answer,
                    "explanation": explanation,
                },
                category["id"],
            )
            total += 1
        print(f"[ok] {category['cert_name']} +{len(items)} 题")
    print(f"完成，新增 {total} 道示例题。")


if __name__ == "__main__":
    run()
