"""认证目录：全站唯一的「厂商 → 方向 → 认证」事实来源。

本模块只放配置与纯函数，不依赖 Flask / 数据库，方便：
- 路由解析（/cert/<vendor>/<direction>/<level>）
- 导航渲染（三级菜单）
- SEO 文案生成（每个认证页独立的 title / keywords / description）
- 认证分类与介绍的初始化播种（models.init_db → bank.sync_catalog）

数据结构约定：
    vendor      {slug, name, short, tagline, intro, keywords}
    direction   {slug, name, summary, audience, topics, advice, difficulty,
                 cert, exam_code, levels[]}
    level       {slug, name, cert, exam_code, difficulty,
                 positioning / audience / topics / advice 可覆盖方向级默认值}

方向若没有 levels，则方向本身即为一个认证（如阿里云ACA、AWS Cloud Practitioner、思科CCNA），
URL 退化为两段 /cert/<vendor>/<direction>，页面与三级认证页完全一致。
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# 厂商与方向
# ---------------------------------------------------------------------------

VENDORS: tuple[dict, ...] = (
    {
        "slug": "aliyun",
        "name": "阿里云认证",
        "short": "阿里云",
        "tagline": "ACA / ACP / ACE 三级认证体系，云从业者的进阶路线",
        "intro": "阿里云认证覆盖 ACA（助理工程师）、ACP（专业工程师）、ACE（高级架构师）三个层级，"
                 "按云计算、大模型、大数据等方向细分，是国内云岗位招聘中最常见的加分项之一。",
        "keywords": "阿里云认证,阿里云题库,ACP题库,ACE题库,ACA题库",
        "directions": (
            {
                "slug": "aca",
                "name": "阿里云ACA",
                "cert": "阿里云ACA（助理工程师）",
                "exam_code": "ACA",
                "difficulty": "入门",
                "summary": "阿里云助理工程师认证，验证云计算基础概念与阿里云核心产品的使用能力。",
                "audience": "在校学生、云计算初学者、准备转行云计算的从业者。",
                "topics": "云计算基础、ECS 云服务器、OSS 对象存储、RDS 数据库、SLB 负载均衡、云安全基础。",
                "advice": "先建立云产品全景图，再逐项动手开通与配置；配套 300 道基础题即可覆盖大部分考点。",
            },
            {
                "slug": "acp",
                "name": "阿里云ACP",
                "cert": "阿里云ACP（专业工程师）",
                "difficulty": "进阶",
                "summary": "阿里云专业工程师认证，按技术方向细分，验证真实项目中的方案设计与运维能力。",
                "audience": "使用阿里云产品的架构、开发、运维人员，具备一年以上云上实践经验。",
                "topics": "ECS、VPC、SLB、OSS、RDS、云监控、云上安全与高可用架构。",
                "advice": "以官方考试大纲为纲，重点吃透 VPC 网络规划、高可用架构与故障排查；建议按方向刷题 500 题以上。",
                "levels": (
                    {
                        "slug": "cloud",
                        "name": "云计算高级工程师",
                        "cert": "阿里云ACP云计算高级工程师",
                        "exam_code": "ACP-Cloud",
                        "difficulty": "进阶",
                        "positioning": "面向使用阿里云云计算产品的架构、开发、运维人员，覆盖 ECS、VPC、SLB、"
                                       "OSS、RDS、云监控、云上安全与高可用架构等内容。",
                        "topics": "ECS 实例与弹性伸缩、VPC 网络规划、SLB 负载均衡、OSS 存储与 CDN、"
                                  "RDS 数据库、云监控与日志、云上安全与高可用架构。",
                    },
                    {
                        "slug": "llm",
                        "name": "大模型高级工程师",
                        "cert": "阿里云ACP大模型高级工程师",
                        "exam_code": "ACP-LLM",
                        "difficulty": "进阶",
                        "positioning": "面向大模型应用开发与落地人员，考察通义千问系列模型调用、提示词工程、"
                                       "RAG 检索增强、模型微调与 AI 应用工程化能力。",
                        "audience": "AI 应用开发者、算法工程师、希望把大模型接入业务的技术负责人。",
                        "topics": "大模型基础、提示词工程、RAG 与向量检索、模型微调、Agent 编排、"
                                  "百炼平台与 AI 应用工程化。",
                        "advice": "先用百炼跑通一条 RAG 链路，再补提示词与微调理论；重点练「场景选型 + 成本控制」类题目。",
                    },
                    {
                        "slug": "bigdata",
                        "name": "大数据高级工程师",
                        "cert": "阿里云ACP大数据高级工程师",
                        "exam_code": "ACP-BigData",
                        "difficulty": "进阶",
                        "positioning": "面向大数据平台建设与数据分析人员，考察 MaxCompute、DataWorks、"
                                       "实时计算与数据治理能力。",
                        "audience": "大数据开发工程师、数据仓库工程师、数据平台运维人员。",
                        "topics": "MaxCompute、DataWorks 数据集成与调度、Flink 实时计算、Hologres、"
                                  "数据建模与数据治理。",
                        "advice": "以一条「采集 → 入湖 → 建模 → 指标」的完整链路为主线练习，理解计算与存储分离的选型逻辑。",
                    },
                ),
            },
            {
                "slug": "ace",
                "name": "阿里云ACE",
                "cert": "阿里云ACE（高级架构师）",
                "difficulty": "专家",
                "summary": "阿里云高级架构师认证，面向复杂企业级场景的架构设计与治理能力。",
                "audience": "资深云架构师、解决方案架构师、技术负责人。",
                "topics": "企业级弹性架构、云原生、混合云、容灾、安全治理、成本优化。",
                "advice": "以真实企业架构案例为主线，训练方案取舍与演进设计能力；建议结合 ACE 实验与架构评审题练习。",
                "levels": (
                    {
                        "slug": "architect",
                        "name": "云计算架构师",
                        "cert": "阿里云ACE云计算架构师",
                        "exam_code": "ACE-Architect",
                        "difficulty": "专家",
                        "positioning": "面向资深云架构师和解决方案架构师，考察企业级弹性架构、云原生、混合云、"
                                       "容灾、安全治理等复杂场景设计能力。",
                        "topics": "企业级弹性架构、云原生与容器化、混合云与多活、容灾与备份、安全治理与合规、"
                                  "成本优化与架构演进。",
                        "advice": "按「业务目标 → 架构取舍 → 风险与成本」三段式训练答题结构，配合真实案例做架构评审。",
                    },
                ),
            },
        ),
    },
    {
        "slug": "huawei",
        "name": "华为认证",
        "short": "华为",
        "tagline": "HCIA / HCIP / HCIE 三级阶梯，覆盖数通、云、安全、存储",
        "intro": "华为认证按 HCIA（工程师）、HCIP（高级工程师）、HCIE（专家）三级递进，"
                 "覆盖数通、云计算、安全、存储等 ICT 核心方向，是网络与云基础设施岗位的主流认证。",
        "keywords": "华为认证,HCIA题库,HCIP题库,HCIE题库,华为数通题库",
        "directions": (
            {
                "slug": "datacom",
                "name": "数通 Datacom",
                "cert": "华为数通 Datacom",
                "summary": "华为数通（Datacom）方向，覆盖企业路由交换、园区网络与广域互联。",
                "audience": "企业网络运维、系统集成、网络规划工程师。",
                "topics": "路由交换、OSPF/BGP、MPLS VPN、VXLAN、SD-WAN、网络自动化与故障排查。",
                "advice": "先夯实 OSPF/BGP 与交换原理，再用 eNSP 复现实验拓扑；HCIP 起重点练故障排查与方案设计。",
                "levels": (
                    {
                        "slug": "hcia",
                        "name": "HCIA",
                        "cert": "华为HCIA-数通",
                        "exam_code": "H12-811",
                        "difficulty": "入门",
                        "positioning": "华为数通入门级认证，验证中小型网络的配置、调试与基础排障能力。",
                        "topics": "TCP/IP 基础、VLAN 与交换、静态路由、OSPF 基础、ACL 与 NAT、网络管理。",
                        "advice": "以 eNSP 实验为主，先把 VLAN 互通、静态路由、OSPF 单区域配熟，再刷 300 道基础题。",
                    },
                    {
                        "slug": "hcip",
                        "name": "HCIP",
                        "cert": "华为HCIP-数通",
                        "exam_code": "H12-821 / H12-831",
                        "difficulty": "进阶",
                        "positioning": "面向具备中小型企业网络规划与运维能力的工程师，重点考察路由交换、BGP、"
                                       "MPLS VPN、SD-WAN、故障排查等能力。",
                        "topics": "OSPF 多区域与高级特性、IS-IS、BGP 选路与策略、MPLS VPN、VXLAN、"
                                  "SD-WAN、网络割接与故障排查。",
                        "advice": "按「协议原理 → 拓扑实验 → 故障现象 → 定位思路」四步练；BGP 选路与 MPLS VPN 是高频难点。",
                    },
                    {
                        "slug": "hcie",
                        "name": "HCIE",
                        "cert": "华为HCIE-数通",
                        "exam_code": "H12-891",
                        "difficulty": "专家",
                        "positioning": "华为数通专家级认证，考察大型园区与骨干网络的设计、部署、优化与复杂故障处理能力。",
                        "topics": "大型网络架构设计、BGP/MPLS 高级特性、VXLAN EVPN、网络自动化、"
                                  "端到端故障定位、方案与实验考试。",
                        "advice": "以完整园区/骨干项目为主线做设计与排障演练，实验考试要练到「看到现象能列出排查树」。",
                    },
                ),
            },
            {
                "slug": "cloud",
                "name": "云计算",
                "cert": "华为云计算",
                "summary": "华为云计算方向，覆盖华为云平台、虚拟化、容器与云运维。",
                "audience": "云运维工程师、云解决方案工程师、数据中心运维人员。",
                "topics": "华为云平台部署、虚拟化、容器、云资源调度、云容灾备份。",
                "advice": "围绕 FusionCompute / 华为云 Stack 做实验，理解资源池化与容灾方案；HCIP 起注重方案设计题。",
                "levels": (
                    {
                        "slug": "hcia",
                        "name": "HCIA",
                        "cert": "华为HCIA-云计算",
                        "exam_code": "H13-511",
                        "difficulty": "入门",
                        "positioning": "华为云计算入门级认证，验证云平台基础操作与虚拟化概念。",
                        "topics": "云计算基础、虚拟化技术、FusionCompute 操作、云存储与云网络基础。",
                        "advice": "先把虚拟机创建、迁移、快照等基础操作练熟，再理解计算/存储/网络虚拟化的分层。",
                    },
                    {
                        "slug": "hcip",
                        "name": "HCIP",
                        "cert": "华为HCIP-云计算",
                        "exam_code": "H13-521 / H13-522",
                        "difficulty": "进阶",
                        "positioning": "面向云平台部署与运维工程师，考察华为云平台的规划、部署、资源调度与容灾能力。",
                        "topics": "云平台规划部署、虚拟化高级特性、容器与 Kubernetes、资源调度、"
                                  "备份容灾、云运维与监控。",
                        "advice": "按「部署 → 扩容 → 迁移 → 容灾」完整链路做实验，容器部分重点掌握 K8s 核心对象。",
                    },
                    {
                        "slug": "hcie",
                        "name": "HCIE",
                        "cert": "华为HCIE-云计算",
                        "exam_code": "H13-531",
                        "difficulty": "专家",
                        "positioning": "华为云计算专家级认证，考察大型云数据中心的设计、迁移与运维治理能力。",
                        "topics": "云数据中心架构、多集群管理、云原生平台、迁移方案、容灾双活、"
                                  "性能调优与故障处理。",
                        "advice": "以「业务上云迁移」为主线准备方案设计与面试答辩，重点训练取舍与风险控制表达。",
                    },
                ),
            },
            {
                "slug": "security",
                "name": "安全",
                "cert": "华为安全",
                "summary": "华为安全方向，覆盖防火墙、VPN、入侵防御与安全运维。",
                "audience": "网络安全工程师、等保建设与安全运维人员。",
                "topics": "防火墙高可靠性、VPN、入侵防御、终端与云安全、安全运维。",
                "advice": "以 USG 防火墙实验为主线，掌握策略、NAT、双机热备与 VPN 排障；结合等保要求理解安全架构。",
                "levels": (
                    {
                        "slug": "hcia",
                        "name": "HCIA",
                        "cert": "华为HCIA-安全",
                        "exam_code": "H12-711",
                        "difficulty": "入门",
                        "positioning": "华为安全入门级认证，验证网络安全基础与防火墙基本配置能力。",
                        "topics": "网络安全基础、防火墙策略、NAT、VPN 基础、终端安全基础。",
                        "advice": "先把安全区域、策略匹配、NAT 与基础 VPN 配通，再补常见攻击与防护原理。",
                    },
                    {
                        "slug": "hcip",
                        "name": "HCIP",
                        "cert": "华为HCIP-安全",
                        "exam_code": "H12-721 / H12-722",
                        "difficulty": "进阶",
                        "positioning": "面向具备网络安全基础的工程师，覆盖防火墙高可靠性、VPN、入侵防御、"
                                       "终端与云安全、安全运维等内容。",
                        "topics": "防火墙双机热备、IPsec/SSL VPN、入侵防御与反病毒、终端与云安全、"
                                  "安全运维与等保合规。",
                        "advice": "重点练双机热备切换与 IPsec VPN 排障；入侵防御部分理解签名匹配与响应动作。",
                    },
                    {
                        "slug": "hcie",
                        "name": "HCIE",
                        "cert": "华为HCIE-安全",
                        "exam_code": "H12-731",
                        "difficulty": "专家",
                        "positioning": "华为安全专家级认证，考察大型企业安全架构设计、威胁治理与应急响应能力。",
                        "topics": "企业安全架构、零信任、云安全治理、高级威胁防护、安全运营中心、应急响应。",
                        "advice": "按「资产 → 威胁 → 架构 → 运营」搭建知识体系，准备时多练安全方案设计与事件复盘。",
                    },
                ),
            },
            {
                "slug": "storage",
                "name": "存储",
                "cert": "华为存储",
                "summary": "华为存储方向，覆盖闪存、分布式存储与备份容灾。",
                "audience": "存储工程师、数据中心运维、容灾方案设计人员。",
                "topics": "RAID、SAN/NAS、闪存与分布式存储、性能调优、备份容灾。",
                "advice": "重点掌握 RAID 与 SAN/NAS 原理，配合 OceanStor 实验理解规划、扩容与故障处理。",
                "levels": (
                    {
                        "slug": "hcia",
                        "name": "HCIA",
                        "cert": "华为HCIA-存储",
                        "exam_code": "H13-611",
                        "difficulty": "入门",
                        "positioning": "华为存储入门级认证，验证存储基础概念与设备基本操作。",
                        "topics": "存储基础、RAID 技术、SAN/NAS 组网、OceanStor 基础配置。",
                        "advice": "先把 RAID 级别与容量计算练熟，再理解 SAN 与 NAS 的适用场景差异。",
                    },
                    {
                        "slug": "hcip",
                        "name": "HCIP",
                        "cert": "华为HCIP-存储",
                        "exam_code": "H13-621 / H13-622",
                        "difficulty": "进阶",
                        "positioning": "面向存储规划、部署与运维工程师，考察闪存与分布式存储的方案设计、"
                                       "性能调优与故障处理能力。",
                        "topics": "闪存存储、分布式存储、SAN/NAS 规划、双活与备份容灾、性能调优、故障处理。",
                        "advice": "以「容量规划 → 组网 → 双活 → 扩容」为主线做实验，重点理解性能瓶颈的定位方法。",
                    },
                    {
                        "slug": "hcie",
                        "name": "HCIE",
                        "cert": "华为HCIE-存储",
                        "exam_code": "H13-631",
                        "difficulty": "专家",
                        "positioning": "华为存储专家级认证，考察大型数据中心存储架构设计、容灾与数据治理能力。",
                        "topics": "数据中心存储架构、双活与两地三中心、分布式存储、数据保护、性能与容量治理。",
                        "advice": "围绕「两地三中心」做方案设计演练，重点训练 RPO/RTO 目标下的架构取舍。",
                    },
                ),
            },
        ),
    },
    {
        "slug": "aws",
        "name": "AWS 认证",
        "short": "AWS",
        "tagline": "Foundational → Associate → Professional → Specialty",
        "intro": "AWS 认证按基础级、助理级、专业级与专项级分层，覆盖架构、开发、运维、DevOps、安全与机器学习，"
                 "是全球认可度最高的公有云认证体系之一。",
        "keywords": "AWS认证,AWS题库,SAA题库,CLF题库,AWS架构师",
        "directions": (
            {
                "slug": "cloud-practitioner",
                "name": "Cloud Practitioner",
                "cert": "AWS Cloud Practitioner（云从业者）",
                "exam_code": "CLF-C02",
                "difficulty": "入门",
                "summary": "零基础入门的 AWS 云从业者认证，验证对云概念、核心服务、计费与安全的基础理解。",
                "audience": "云计算初学者、销售/售前/项目管理等非技术岗位、准备转行云职业的人。",
                "topics": "云概念、核心服务（EC2/S3/VPC/RDS）、安全与合规、计费与支持计划。",
                "advice": "先过一遍 AWS Cloud Practitioner Essentials，再用 300 道模拟题查漏补缺。",
            },
            {
                "slug": "solutions-architect",
                "name": "Solutions Architect",
                "cert": "AWS Solutions Architect",
                "summary": "AWS 架构师方向，基于 Well-Architected 框架设计安全、高可用、可扩展、成本优化的云架构。",
                "audience": "云架构师、解决方案顾问、后端与运维工程师。",
                "topics": "高可用与容错、网络与 VPC、存储与数据库选型、安全与合规、成本优化、迁移与现代化。",
                "advice": "六大支柱逐条对照真实场景练习；Associate 阶段重点在服务选型与架构取舍，Professional 阶段加入复杂迁移与多账号治理。",
                "levels": (
                    {
                        "slug": "associate",
                        "name": "Associate",
                        "cert": "AWS Solutions Architect – Associate",
                        "exam_code": "SAA-C03",
                        "difficulty": "进阶",
                        "positioning": "面向云架构师，考察如何基于 AWS 设计安全、高可用、可扩展、成本优化的云架构。",
                        "topics": "VPC 与混合网络、EC2/ECS/Lambda 选型、S3 与存储分层、RDS/Aurora/DynamoDB、"
                                  "ELB 与 Auto Scaling、IAM 与 KMS、成本优化与 Well-Architected 六支柱。",
                        "advice": "把「高可用、解耦、弹性、成本」四个关键词落到每道题的方案对比上；建议刷 600 题以上并整理错题。",
                    },
                    {
                        "slug": "professional",
                        "name": "Professional",
                        "cert": "AWS Solutions Architect – Professional",
                        "exam_code": "SAP-C02",
                        "difficulty": "高级",
                        "positioning": "AWS 架构师专家级认证，考察复杂企业架构、迁移、多账号治理与成本优化能力。",
                        "topics": "多账号与 Organizations、复杂网络互联、迁移策略、混合云、灾难恢复、"
                                  "安全治理、成本与性能优化。",
                        "advice": "以「大型企业上云」案例串起知识点，重点训练跨账号网络与迁移方案的权衡。",
                    },
                ),
            },
            {
                "slug": "developer",
                "name": "Developer",
                "cert": "AWS Developer – Associate",
                "exam_code": "DVA-C02",
                "difficulty": "进阶",
                "summary": "面向应用开发者，考察在 AWS 上开发、部署与调试云原生应用的能力。",
                "audience": "后端开发工程师、全栈工程师、DevOps 转型开发者。",
                "topics": "Lambda、API Gateway、DynamoDB、S3、SQS/SNS、CI/CD、IAM 与 Secrets Manager、"
                          "开发调试与可观测性。",
                "advice": "以「一个 Serverless 应用从开发到上线」为主线，重点掌握 Lambda 权限模型与异步调用。",
            },
            {
                "slug": "sysops",
                "name": "SysOps Administrator",
                "cert": "AWS SysOps Administrator – Associate",
                "exam_code": "SOA-C02",
                "difficulty": "进阶",
                "summary": "面向云运维人员，考察 AWS 环境的部署、监控、自动化与故障处理能力。",
                "audience": "云运维工程师、SRE、系统管理员。",
                "topics": "CloudWatch 监控告警、CloudFormation 自动化、备份与恢复、网络排障、"
                          "成本与合规、Systems Manager。",
                "advice": "重点练 CloudWatch 指标/日志/告警组合与自动化修复；排障题按「现象 → 指标 → 根因」作答。",
            },
            {
                "slug": "devops",
                "name": "DevOps Engineer",
                "cert": "AWS DevOps Engineer – Professional",
                "exam_code": "DOP-C02",
                "difficulty": "高级",
                "summary": "AWS 专业级 DevOps 认证，考察持续交付、自动化编排、监控告警与安全合规能力。",
                "audience": "DevOps 工程师、SRE、平台工程师。",
                "topics": "CodePipeline/CodeBuild/CodeDeploy、基础设施即代码、蓝绿与金丝雀发布、"
                          "监控告警、事件响应、安全合规自动化。",
                "advice": "把「发布流水线 + 回滚策略 + 监控闭环」做成一个可复述的方案模板，考试中直接套用。",
            },
            {
                "slug": "security",
                "name": "Security",
                "cert": "AWS Security – Specialty",
                "exam_code": "SCS-C02",
                "difficulty": "高级",
                "summary": "AWS 安全专项认证，考察身份权限、数据保护、检测响应与合规治理能力。",
                "audience": "安全工程师、云安全架构师、合规与审计人员。",
                "topics": "IAM 与权限边界、KMS 与加密、WAF/Shield、GuardDuty/Security Hub、"
                          "日志审计、事件响应与合规。",
                "advice": "把 IAM 策略评估逻辑（显式拒绝 > 允许）练到条件反射，再补检测与响应服务组合。",
            },
            {
                "slug": "machine-learning",
                "name": "Machine Learning",
                "cert": "AWS Machine Learning – Specialty",
                "exam_code": "MLA-C01",
                "difficulty": "高级",
                "summary": "AWS 机器学习专项认证，考察数据准备、模型训练、部署与 MLOps 能力。",
                "audience": "算法工程师、数据科学家、ML 平台工程师。",
                "topics": "SageMaker 全流程、特征工程、模型训练与调优、推理部署、"
                          "MLOps 流水线、数据治理与成本优化。",
                "advice": "围绕 SageMaker 把「数据 → 训练 → 部署 → 监控」跑通一遍，重点记服务选型边界。",
            },
        ),
    },
    {
        "slug": "cisco",
        "name": "思科认证",
        "short": "思科",
        "tagline": "CCNA → CCNP → CCIE，网络工程师的经典进阶路线",
        "intro": "思科认证是网络工程领域最经典的认证体系：CCNA 打基础，CCNP 深入企业网络设计与排障，"
                 "CCIE 以高难度实验考试著称，覆盖企业、安全、数据中心与服务提供商等方向。",
        "keywords": "思科认证,CCNA题库,CCNP题库,CCIE题库,思科刷题",
        "directions": (
            {
                "slug": "ccna",
                "name": "CCNA",
                "cert": "思科CCNA",
                "exam_code": "200-301",
                "difficulty": "入门",
                "summary": "思科初级网络工程师认证，验证网络基础、IP 连接、安全基础与自动化认知。",
                "audience": "初级网络工程师、网络运维、准备进入网络行业的转行者。",
                "topics": "网络基础、IP 连接与子网、VLAN 与交换、OSPF、安全基础、自动化与可编程性。",
                "advice": "先用 Packet Tracer 把 VLAN、静态路由、OSPF、ACL/NAT 配熟，再刷 400 道题巩固。",
            },
            {
                "slug": "ccnp",
                "name": "CCNP",
                "cert": "思科CCNP",
                "difficulty": "高级",
                "summary": "思科专业级认证，覆盖企业网络、安全、数据中心与服务提供商方向。",
                "audience": "高级网络工程师、网络架构师、系统集成工程师。",
                "topics": "企业网络设计、实施、优化与故障排除；SD-WAN、BGP、组播、QoS、自动化。",
                "advice": "以 ENCOR 为核心打底，再按方向选考；重点练「设计 + 排障」类场景题。",
                "levels": (
                    {
                        "slug": "enterprise",
                        "name": "Enterprise",
                        "cert": "思科CCNP Enterprise",
                        "exam_code": "350-401 ENCOR",
                        "difficulty": "高级",
                        "positioning": "面向高级网络工程师，考察企业网络的设计、实施、优化和故障排除能力。",
                        "topics": "双栈架构、虚拟化与 SD-WAN、BGP 与组播、QoS、网络保障与安全、自动化。",
                        "advice": "以企业园区 + 分支互联为主线，练熟 OSPF/BGP/组播与 SD-WAN 方案对比。",
                    },
                    {
                        "slug": "security",
                        "name": "Security",
                        "cert": "思科CCNP Security",
                        "exam_code": "350-701 SCOR",
                        "difficulty": "高级",
                        "positioning": "面向网络安全工程师，考察企业安全架构、防火墙、VPN、云安全与威胁防御能力。",
                        "topics": "网络安全架构、Firepower、VPN、身份与访问、云安全、端点与威胁防御。",
                        "advice": "以「边界 → 内网 → 云」三层防护为主线整理方案，重点记各安全组件的部署位置。",
                    },
                    {
                        "slug": "data-center",
                        "name": "Data Center",
                        "cert": "思科CCNP Data Center",
                        "exam_code": "350-601 DCCOR",
                        "difficulty": "高级",
                        "positioning": "面向数据中心网络工程师，考察 Nexus、ACI、VXLAN EVPN 与存储网络能力。",
                        "topics": "Nexus 架构、VXLAN EVPN、ACI、SAN/NAS、自动化与编排。",
                        "advice": "重点掌握 VXLAN EVPN 转发流程与 ACI 策略模型，配合模拟器做端到端打通实验。",
                    },
                    {
                        "slug": "service-provider",
                        "name": "Service Provider",
                        "cert": "思科CCNP Service Provider",
                        "exam_code": "350-501 SPCOR",
                        "difficulty": "高级",
                        "positioning": "面向运营商网络工程师，考察 MPLS、BGP 大规模路由与段路由能力。",
                        "topics": "IGP/BGP 大规模部署、MPLS L3VPN、Segment Routing、QoS、运营商自动化。",
                        "advice": "以「城域 + 骨干」拓扑练 BGP 选路与 MPLS VPN，理解 SR 与 LDP 的差异。",
                    },
                ),
            },
            {
                "slug": "ccie",
                "name": "CCIE",
                "cert": "思科CCIE",
                "difficulty": "专家",
                "summary": "思科专家级认证，笔试 + 8 小时实验考试，考察大规模复杂网络的设计、部署与排障。",
                "audience": "网络专家、架构师、大型项目技术负责人。",
                "topics": "复杂网络设计、部署、排障与优化；各方向专属技术栈。",
                "advice": "实验考试是核心：按「需求拆解 → 配置 → 验证 → 排障」反复演练，建议至少完成 10 套完整实验。",
                "levels": (
                    {
                        "slug": "enterprise",
                        "name": "Enterprise",
                        "cert": "思科CCIE Enterprise",
                        "exam_code": "400-007 + LAB",
                        "difficulty": "专家",
                        "positioning": "面向网络专家，考察大规模复杂企业网络的设计、部署、排障和优化能力。",
                        "topics": "企业架构设计、双栈与 SD-WAN、BGP/组播/QoS、网络保障、自动化、实验排障。",
                        "advice": "先过笔试建立设计视角，再把 8 小时实验拆成模块反复计时训练。",
                    },
                    {
                        "slug": "security",
                        "name": "Security",
                        "cert": "思科CCIE Security",
                        "exam_code": "400-251 + LAB",
                        "difficulty": "专家",
                        "positioning": "面向安全专家，考察大型企业安全架构的设计、部署与复杂事件处置能力。",
                        "topics": "安全架构、防火墙与 VPN、身份服务、云与数据中心安全、威胁防御、实验排障。",
                        "advice": "以攻防视角串联各安全组件，实验部分重点练策略冲突与故障定位。",
                    },
                    {
                        "slug": "data-center",
                        "name": "Data Center",
                        "cert": "思科CCIE Data Center",
                        "exam_code": "400-080 + LAB",
                        "difficulty": "专家",
                        "positioning": "面向数据中心专家，考察大规模数据中心网络与 ACI/VXLAN 的设计与运维能力。",
                        "topics": "数据中心架构、VXLAN EVPN、ACI、存储网络、自动化编排、实验排障。",
                        "advice": "把 ACI 与 EVPN 的转发与控制面流程画成图，实验按租户→策略→外部互联逐层搭。",
                    },
                    {
                        "slug": "service-provider",
                        "name": "Service Provider",
                        "cert": "思科CCIE Service Provider",
                        "exam_code": "400-201 + LAB",
                        "difficulty": "专家",
                        "positioning": "面向运营商专家，考察大规模 SP 网络的设计、部署、排障与优化能力。",
                        "topics": "大规模 IGP/BGP、MPLS、Segment Routing、组播 VPN、QoS、运营商自动化。",
                        "advice": "以运营商骨干拓扑为主线，重点练 BGP 策略与 MPLS 业务的端到端验证。",
                    },
                ),
            },
        ),
    },
)


# ---------------------------------------------------------------------------
# 派生：叶子（认证）列表
# ---------------------------------------------------------------------------

def _make_leaf(vendor: dict, direction: dict, level: Optional[dict]) -> dict:
    slug_parts = [vendor["slug"], direction["slug"]]
    if level:
        slug_parts.append(level["slug"])
    path = "/".join(slug_parts)

    cert_name = (level or direction).get("cert") or f"{vendor['name']}{direction['name']}"
    level_name = level["name"] if level else ""
    menu_name = level["name"] if level else direction["name"]

    difficulty = (level or direction).get("difficulty") or direction.get("difficulty", "进阶")
    exam_code = (level or direction).get("exam_code", "")

    summary = (level or {}).get("summary") or direction.get("summary", "")
    positioning = (level or {}).get("positioning") or summary
    audience = (level or {}).get("audience") or direction.get("audience", "")
    topics = (level or {}).get("topics") or direction.get("topics", "")
    advice = (level or {}).get("advice") or direction.get("advice", "")

    keywords = ",".join([
        f"{cert_name}题库",
        f"{cert_name}真题",
        f"{direction['name']}刷题",
        f"{vendor['name']}题库",
    ])

    return {
        "path": path,
        "url": f"/cert/{path}",
        "vendor_slug": vendor["slug"],
        "vendor_name": vendor["name"],
        "vendor_short": vendor["short"],
        "direction_slug": direction["slug"],
        "direction_name": direction["name"],
        "level_slug": level["slug"] if level else "",
        "level_name": level_name,
        "menu_name": menu_name,
        "name": cert_name,
        "cert_name": cert_name,
        "exam_code": exam_code,
        "difficulty": difficulty,
        "summary": summary,
        "positioning": positioning,
        "audience": audience,
        "topics": topics,
        "advice": advice,
        "keywords": keywords,
        "seo_title": f"{cert_name}题库 - 认证题库网",
        "seo_description": (summary or positioning)[:110],
        "source": "",
    }


def all_leaves() -> list[dict]:
    """展开全部认证（35 个），保持配置顺序。"""
    result: list[dict] = []
    for vendor in VENDORS:
        for direction in vendor["directions"]:
            levels = direction.get("levels") or ()
            if not levels:
                result.append(_make_leaf(vendor, direction, None))
            else:
                for level in levels:
                    result.append(_make_leaf(vendor, direction, level))
    return result


_LEAF_INDEX: Optional[dict[str, dict]] = None


def leaf_index() -> dict[str, dict]:
    global _LEAF_INDEX
    if _LEAF_INDEX is None:
        _LEAF_INDEX = {leaf["path"]: leaf for leaf in all_leaves()}
    return _LEAF_INDEX


def get_leaf(path: str) -> Optional[dict]:
    """按 path（如 aliyun/acp/cloud）取认证配置。"""
    return leaf_index().get((path or "").strip("/"))


def get_vendor(slug: str) -> Optional[dict]:
    return next((v for v in VENDORS if v["slug"] == slug), None)


def get_direction(vendor_slug: str, direction_slug: str) -> Optional[dict]:
    vendor = get_vendor(vendor_slug)
    if not vendor:
        return None
    return next((d for d in vendor["directions"] if d["slug"] == direction_slug), None)


def direction_leaves(vendor_slug: str, direction_slug: str) -> list[dict]:
    return [
        leaf for leaf in all_leaves()
        if leaf["vendor_slug"] == vendor_slug and leaf["direction_slug"] == direction_slug
    ]


def vendor_leaves(vendor_slug: str) -> list[dict]:
    return [leaf for leaf in all_leaves() if leaf["vendor_slug"] == vendor_slug]


def is_single_level(vendor_slug: str, direction_slug: str) -> bool:
    direction = get_direction(vendor_slug, direction_slug)
    return bool(direction) and not direction.get("levels")


# ---------------------------------------------------------------------------
# SEO
# ---------------------------------------------------------------------------

def home_seo() -> dict:
    return {
        "title": "认证题库网 - 阿里云、华为、AWS、思科认证题库与每日刷题",
        "keywords": "认证题库,阿里云题库,华为题库,AWS题库,思科题库,每日刷题,认证考试",
        "description": "认证题库网提供阿里云 ACA/ACP/ACE、华为 HCIA/HCIP/HCIE、AWS、思科 CCNA/CCNP/CCIE "
                       "认证介绍与题库练习，每日 10 题随机刷题，积分排行榜与个人备考记录。",
        "canonical": "/",
    }


def vendor_seo(vendor: dict) -> dict:
    return {
        "title": f"{vendor['name']}题库 - {vendor['tagline'][:18]} - 认证题库网",
        "keywords": vendor.get("keywords", ""),
        "description": vendor.get("intro", "")[:110],
        "canonical": f"/cert/{vendor['slug']}",
    }


def direction_seo(vendor: dict, direction: dict) -> dict:
    name = direction["name"]
    return {
        "title": f"{vendor['name']}{name}题库 - 认证考试真题与每日刷题 - 认证题库网",
        "keywords": f"{vendor['name']}{name}题库,{name}真题,{name}刷题,{vendor['name']}认证",
        "description": (direction.get("summary") or "")[:110],
        "canonical": f"/cert/{vendor['slug']}/{direction['slug']}",
    }


def nav_tree() -> list[dict]:
    """返回「厂商 → 方向 → 认证」三层树，叶子带 url。

    单级方向的 children 为空，前端按“可点击的二级项”渲染。
    """
    tree: list[dict] = []
    for vendor in VENDORS:
        directions = []
        for direction in vendor["directions"]:
            leaves = direction_leaves(vendor["slug"], direction["slug"])
            items = [
                {
                    "label": leaf["menu_name"] or leaf["name"],
                    "name": leaf["name"],
                    "url": leaf["url"],
                    "path": leaf["path"],
                }
                for leaf in leaves
            ]
            directions.append({
                "label": direction["name"],
                "slug": direction["slug"],
                "url": f"/cert/{vendor['slug']}/{direction['slug']}",
                "single": not direction.get("levels"),
                "summary": direction.get("summary", ""),
                "exam_code": direction.get("exam_code", ""),
                "children": [] if not direction.get("levels") else items,
            })
        tree.append({
            "label": vendor["name"],
            "short": vendor["short"],
            "slug": vendor["slug"],
            "url": f"/cert/{vendor['slug']}",
            "intro": vendor["intro"],
            "directions": directions,
        })
    return tree


def breadcrumbs_for_path(path: str) -> list[dict]:
    """根据 path 生成面包屑：[首页, 厂商, 方向?, 认证?]。"""
    parts = [p for p in (path or "").strip("/").split("/") if p]
    crumbs = [{"label": "首页", "url": "/"}]
    if not parts:
        return crumbs
    vendor = get_vendor(parts[0])
    if not vendor:
        return crumbs
    crumbs.append({"label": vendor["name"], "url": f"/cert/{vendor['slug']}"})
    if len(parts) < 2:
        return crumbs
    direction = get_direction(vendor["slug"], parts[1])
    if not direction:
        return crumbs
    leaf = get_leaf("/".join(parts))
    if leaf is None:  # 方向页
        crumbs.append({"label": direction["name"], "url": f"/cert/{vendor['slug']}/{direction['slug']}"})
        return crumbs
    crumbs.append({"label": direction["name"], "url": f"/cert/{vendor['slug']}/{direction['slug']}"})
    if leaf["level_slug"]:
        crumbs.append({"label": leaf["menu_name"], "url": leaf["url"]})
    return crumbs
