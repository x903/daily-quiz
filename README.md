# 每日答题系统（Flask 版）

基于 Flask + Jinja2 + **MySQL 8.0** + 原生前端的「每日答题系统」，支持 Docker 一键部署
（`Dockerfile` + `docker-compose.yml`）。每天导入约 20 套 Markdown 题目，导入时按
「阿里云认证 › 阿里云ACP › 云计算高级工程师」等三级栏目归类，自动判分、记录成绩、
沉淀错题与积分，支持数据导出、按日 / 周 / 月统计的排行榜，以及配置驱动的认证栏目页
（栏目页可直接浏览并答题）。原「历史日期」栏目已取消，题目改由首页最近列表与栏目页进入。

> 数据层见 `db.py`：生产 / Docker 使用 MySQL（`schema_mysql.sql` 建表），未配置 MySQL 时
> 自动回退 SQLite，保证本地离线开发与既有回归测试不依赖数据库服务。详见
> [第三节运行方式](#三运行方式) 与 [第六节数据库设计](#六数据库设计mysql--sqlite)。

## 一、目录结构

```
daily-quiz/
├── app.py                  # Flask 入口：路由、权限装饰器、登录态、排行榜、导出、后台
├── db.py                   # 【新增】统一数据访问层：MySQL/SQLite 双后端、SQL 与异常适配、启动重试
├── schema_mysql.sql        # 【新增】MySQL 8.0 建表脚本（6 表 / InnoDB / utf8mb4 / 8 个外键，幂等）
├── models.py               # 数据模型：按后端建表、6 张表 CRUD、字段迁移、历史成绩积分回填
├── parser.py               # 题目解析：Markdown → 结构化题目，含格式校验与失败标记
├── auth.py                 # 认证与权限：密码哈希、注册、登录校验、默认账号播种
├── quiz_logic.py           # 每日答题逻辑：导入、出题、判分（同事务记积分）、错题重练
├── admin_logic.py          # 管理员题库维护：编辑表单校验、单事务保存、删除预览与级联删除
├── points.py               # 积分规则：每答对 1 题得 1 分，写 points_ledger 流水
├── leaderboard.py          # 排行榜：日 / 周 / 月周期计算、聚合口径与并列排名
├── navigation.py           # 认证菜单树 / 页面元数据 / 栏目树 JSON / 路径与子孙聚合 / ACP 方向扩展
├── score_manager.py        # 成绩管理：daily_records 写入与查询（按用户）
├── wrong_book.py           # 错题管理：wrong_questions 写入、查询、清空（按用户）
├── templates/              # Jinja2 模板（见下）
├── static/
│   ├── style.css           # 页面样式（含三级下拉菜单、栏目选择器）
│   ├── app.js              # 计时、进度条、当前题指示、未答提交保护
│   ├── nav.js              # 三级菜单触屏 / 键盘点击展开
│   └── category_picker.js  # 配置驱动的三级栏目联动（导入页 / 编辑页共用）
├── sample_questions.md     # 示例题目（6 套，覆盖单选/多选/判断）
├── sample_with_errors.md   # 含格式错误的示例（用于演示导入失败提示）
├── test_smoke.py           # 冒烟测试（解析 / 导入 / 判分 / 错题 / 导出 / 用户隔离）
├── test_permissions.py     # 权限与角色测试（未登录拦截 / 管理员后台 / 用户隔离）
├── test_user_ui.py         # 用户界面验证（普通用户无导入入口 / 管理员保留 / 日期自动探测）
├── test_explanation.py     # 解析全链路验证（解析收集 / 安全渲染 / 四页面展示 / 防泄题 / 导出）
├── test_submit_fix.py      # 提交答题修复验证（孤儿 session / 外键约束 / 单事务回滚，独立临时库）
├── test_admin_edit.py      # 题库编辑与删除验证（越权拦截 / 表单校验 / 事务 / 级联删除，独立临时库）
├── test_leaderboard.py     # 排行榜验证（自动记分 / 周期边界 / 跨周月聚合 / 回填 / 权限，独立临时库）
├── test_import_ui.py       # 导入方式验证（仅上传文件 / 旧方式失效 / 覆盖确认 / 权限，独立临时库）
├── test_cert_nav.py        # 认证三级菜单 / 页面骨架 / 配置扩展 / 无回归（独立临时库）
├── test_category_import.py # 导入栏目 / 栏目页聚合 / 编辑改栏目 / 历史栏目下线（独立临时库）
├── test_mysql_backend.py   # 【新增】MySQL 专项：DDL/字符集/外键、占位符、异常、事务、端到端
├── requirements.txt        # 【新增】运行依赖：Flask / PyMySQL / cryptography / gunicorn
├── Dockerfile              # 【新增】python:3.12-slim + gunicorn，非 root 运行，含健康检查
├── docker-compose.yml      # 【新增】mysql:8.0 + web 两服务、数据卷、初始化脚本、健康依赖
├── .dockerignore           # 【新增】排除 .git/.venv/.env/缓存/测试脚本/本地数据库
├── .gitignore              # 【新增】忽略虚拟环境、缓存、.env、*.db、IDE 文件
├── .env.example            # 【新增】环境变量模板（复制为 .env 后修改）
├── data/                   # SQLite 回退库目录（quiz.db 已忽略）；可存放题目 MD 样例
└── README.md
```

### 模板一览

`base`（含按角色动态导航与认证三级下拉）· `certification`（认证栏目通用骨架页）·
`_nav`（递归渲染认证子菜单的宏）· `_category_picker`（三级栏目选择器宏，导入页 / 编辑页共用）·
`index` · `login` · `register` · `import` · `import_result` ·
`quiz` · `result` · `date_detail` · `score_detail` · `wrong` · `wrong_practice` ·
`admin`（后台首页/题库管理）· `admin_users`（用户管理）· `admin_records`（全部答题记录）·
`admin_set_edit`（题库编辑：日期/标题/栏目/逐题内容）· `admin_set_delete`（删除确认）·
`leaderboard`（日 / 周 / 月榜、我的排名、积分规则说明）·
`_macros`（共用宏 `explanation_block`，在答案下方渲染解析）

> 原 `history` 模板与 `/history` 路由已删除，「历史日期」栏目不再提供。

## 二、题目输入格式

每套以 `第N套` 开头，套内题目依次排列；题型支持「单选题 / 多选题 / 判断题」：

```markdown
第1套
单选题
Python 中用于声明不可变序列的内置类型是？
A. list
B. tuple
C. dict
D. set
答案：B

第2套
多选题
以下哪些是 Python 的内置数据类型？
A. int
B. str
C. float
D. decimal
答案：ABC

第3套
判断题
Flask 是异步优先的 Web 框架。
A. 正确
B. 错误
答案：B
```

### 格式二：ACP 风格（编号题，每日 N 道）

适用于「每日模拟题（N 道）」这类格式：题目用 `**1.**` 编号、选项带 `- A.` 项目符号、
答案加粗为 `**答案：C**`，题型由文件头部的 `**题型**：` 提示推断（缺省视为单选）。
每道编号题会被当作「一套」（第 1 套 … 第 N 套）。

```markdown
# 阿里云 ACP 云计算 · 每日模拟题（20 道）

> **题型**：单项选择题（每题仅 1 个正确答案）

## 一、ECS 云服务器（第 1–3 题）

**1.** 某运维工程师在 ECS 实例内部执行命令获取实例 ID。关于元数据服务，下列说法正确的是？

- A. 必须为实例绑定公网 IP 后才能访问该地址
- B. 公网任意主机均可通过该地址读取实例信息
- C. 该地址为链路本地地址，只能在实例内部访问
- D. 实例停机后，其他主机仍可通过该地址访问其元数据

**答案：C**

**2.** 某金融客户要求 3 台 ECS 尽量分散到不同物理宿主机，最合适的配置是？

- A. 将 3 台实例加入同一部署集，策略选择“高可用策略”
- B. 将 3 台实例加入同一部署集，策略选择“紧凑型策略”
- C. 将 3 台实例放入同一安全组
- D. 将 3 台实例放在同一可用区的不同交换机下

**答案：A**
```

> 说明：两种格式可混用，解析器会自动识别 `第N套` / `单选题` 与 `**N.**` / `- A.` / `**答案：C**`。
> 若文件含多选/判断，建议在每题上方标注 `多选题` / `判断题`；否则将沿用文件头 `题型` 提示
> （缺省为单选）。`sample_questions.md` 为格式一示例，`sample_with_errors.md` 演示失败提示。

### 题目解析（可选）

答案之后可紧跟「解析」块，系统会把解析与题目一起入库，并在**所有显示答案的页面**展示：
题目回顾页、答题结果页、成绩详情页、错题本，以及题目/错题 JSON 导出。

兼容三种写法：`**解析**`、`**解析：**`、`解析：`（不加粗亦可）。解析内容用 `- ` 项目符号，
支持缩进子项与 `**加粗**`：

```markdown
**5.** 某 VPC 内的 ECS 均无公网 IP，需要以内网方式访问同地域 OSS……？

- A. 为 ECS 绑定 EIP
- B. 创建 OSS 网关终端节点（Gateway Endpoint）
- C. 通过 NAT 网关做 SNAT
- D. 使用 CDN 回源 OSS

**答案：B**

**解析：**
- **考点**：网关终端节点用于 VPC 私网直接访问 OSS。
- **易错点**：
  - ① 用 NAT 网关会产生实例费与流量处理费；
  - ② 绑定 EIP 走公网会产生流量费。
- **延伸**：私网路径有三种。
```

> 解析块的收集在遇到下一道题、章节标题、表格或分隔线时自动结束，因此文末的
> 「答案速查表」「本期模块分布」「作答建议」等汇总内容不会被误当成解析。
> 无解析的题目（如格式一示例）不显示解析块，完全向后兼容。
> 解析文本渲染前会做 HTML 转义，仅放行 `**加粗**` 与项目符号，防止题库内容注入脚本。

### 格式校验

解析阶段会对每道题做完整性校验，以下情况会被标记为「导入失败」并列举原因：
- 缺少题干 / 缺少选项 / 缺少答案；
- 答案选项在题目中不存在（如答案选了 `D` 但题目只有 A~C）；
- 单选题答案不唯一、多选题答案不足两个；
- 判断题答案不是 `A` 或 `B`。

失败的题不会进入题库，但同批次其他正确题目仍会正常导入。

## 三、运行方式

### 3.1 Docker 一键部署（推荐，自动使用 MySQL）

```bash
cp .env.example .env          # 可选：修改密码 / SECRET_KEY / 端口
docker compose up -d --build  # 同时拉起 mysql:8.0 与 Flask/Gunicorn
docker compose ps             # 查看状态（db 需 healthy）
docker compose logs -f web    # 查看应用日志
```

访问 **http://localhost:5000**（可用 `WEB_PORT` 改宿主机端口），默认账号见 5.1 节。

- `db` 服务用命名卷 `mysql_data` 持久化数据，首次启动自动执行
  `/docker-entrypoint-initdb.d/01-schema.sql`（即 `schema_mysql.sql`）；
  `web` 通过 `depends_on: service_healthy` 等待 MySQL 就绪，`db.py` 另有连接重试兜底。
- `web` 镜像以非 root 用户运行 Gunicorn（2 worker + `--preload`，避免并发建表 / 重复播种账号），
  并带 `/login` 健康检查。
- 停止：`docker compose down`（保留数据）；`docker compose down -v`（连数据卷一起删除，不可恢复）。

### 3.2 本机直接运行（连外部 MySQL）

```bash
pip install -r requirements.txt
cp .env.example .env          # 填写 MYSQL_* / SECRET_KEY
python app.py
```

应用启动时会读取 `.env`（`db.py` 内置轻量解析，无需额外依赖）并执行
`models.init_db()` 幂等建表 / 迁移 / 历史成绩积分回填。若 MySQL 尚未建库，
先在 MySQL 里执行 `schema_mysql.sql`（脚本头部注释含 `CREATE DATABASE` 示例）。
`app.py` 默认监听 `127.0.0.1:5000`，可用 `FLASK_RUN_HOST` / `FLASK_RUN_PORT` 调整。

### 3.3 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DB_BACKEND` | `mysql`（配置过 `MYSQL_HOST`/`DATABASE_URL` 时） | `mysql` / `sqlite`；未配置 MySQL 环境时自动回退 `sqlite` |
| `DATABASE_URL` | 空 | `mysql://user:pass@host:port/db`，优先于 `MYSQL_*` |
| `MYSQL_HOST` / `MYSQL_PORT` | `127.0.0.1` / `3306` | MySQL 地址（Compose 内为 `db`） |
| `MYSQL_USER` / `MYSQL_PASSWORD` | `quiz` / 空 | 应用账号 |
| `MYSQL_DATABASE` | `daily_quiz` | 库名（也可用 `MYSQL_DB`） |
| `DB_CONNECT_RETRIES` / `DB_CONNECT_RETRY_DELAY` | `15` / `2` | 启动等待 MySQL 的重试次数与间隔（秒） |
| `SECRET_KEY` | 固定开发值 | **生产务必改成随机长字符串**，否则 session 不安全 |
| `WEB_PORT` | `5000` | Compose 映射到宿主机的端口 |

> **回退说明**：SQLite 只作为「没有 MySQL 服务时」的本地开发与回归测试便利通道
> （数据写入 `data/quiz.db`，已被 `.gitignore` 忽略）。生产与 Docker 部署一律走 MySQL；
> 两套后端共用同一套业务 SQL，行为差异由 `db.py` 抹平。

## 四、使用流程

1. **导入每日题目**（仅管理员）
   - 使用管理员账号登录后，打开「导入题目」页（或访问 `/import`）。
   - **仅支持一种导入方式：上传 Markdown 文件**（UTF-8 编码的 `.md` / `.markdown` / `.txt`）。
     已取消原「选择 data/ 下已有文件」与「直接粘贴文本」两种方式：界面无入口，
     后端也不再接收 `markdown` 文本字段或 `/import/api/load` 读取接口。
   - **必须选择导入栏目**：三级联动下拉「一级 › 二级 › 三级」，例如
     `阿里云认证 › 阿里云ACP › 云计算高级工程师`。选择器由 `navigation.category_tree_payload()`
     驱动，新增认证方向后自动出现，无需改模板。后端用 `is_valid_category()` 校验 slug，
     空值或伪造值一律拒绝导入；合法值写入 `daily_sets.category_slug`，同时把完整路径文案
     存为 `category_path` 快照。导入成功页会显示栏目并提供栏目页链接。
   - **日期自动识别**：文件名或标题中含 `YYYY-MM-DD`（如 `ACP模拟题20道_2026-11-04.md`）时，
     上传后会自动填入导入日期（仅当用户未手动改动默认日期时覆盖）；正文头部若含
     「生成日期：2026-11-04」也会识别。手动填写的日期优先级最高，标题留空则取文件名。
   - 同一日期已存在题库时，会提示是否「覆盖」（覆盖将删除原题目、成绩与错题）。
     确认页已暂存本次上传的文件内容，**无需重新选择文件**即可确认覆盖。
   - 导入成功页展示：套数、题目总数、题型分布（单选/多选/判断）、以及失败题目列表与原因。

2. **开始答题**
   - 首页「今日答题」、首页「最近题目」列表或认证栏目页的「本栏目题库」进入 `/quiz/<日期>`。
   - 按「第 1 套 … 第 N 套」分组，单选题用单选按钮、多选题用复选框、判断题用单选按钮。
   - 顶部吸顶栏显示：当前套号、答题进度（已答/总数）、用时计时器。
   - 提交时若仍有未答题会二次确认；判分后自动记录成绩并刷新错题本。

3. **查看成绩与错题**
   - 答题后跳转结果页，显示总分、正确率、用时与错题列表（你的答案 vs 正确答案 **+ 题目解析**）。
   - 凡显示答案的位置都会一并显示解析；答题页与错题重练页不显示答案与解析（防泄题）。
   - 「错题本」按日期聚合错题，支持「重练该日错题」（答对即从错题本移除）与「清空错题本」。
   - 首页最近题目列表与认证栏目页均可查看各日期的完成状态、补做未完成题目。

4. **数据导出**
   - 导出某日题目：`/export/questions/<日期>` → `questions_<日期>.json`
   - 导出某日成绩：`/export/score/<日期>` → `score_<日期>.json`
   - 导出全部错题：`/export/wrong` → `wrong_questions.json`

5. **修改 / 删除题库**（仅管理员，后台「题库管理」表格）
   - 点「编辑」进入 `/admin/set/<日期>/edit`：可改**日期、标题、所属栏目**，以及每道题的题型、题干、选项、正确答案与解析；勾选「删除本题」可移除单题，保存时自动重算题数。栏目留空表示保持原归属，填写非法值会整份拒绝。
   - 校验规则与导入一致（题干/选项/答案必填、答案字母必须存在、单选唯一、多选至少 2 个、判断为 A/B）；日期不能与其他题库重复，题库至少保留 1 道题。任一题校验失败则**整份修改不落库**。
   - 修改题目**不会重算历史成绩**（成绩是当时的作答快照），编辑页会提示当前成绩/错题数量；删除单题会连带清除该题的错题。
   - 点「删除题库」进入确认页，展示将删除的题目数、成绩数、错题数，**必须手工输入该日期**才能执行删除；删除后不可恢复。

6. **查看排行榜**（所有登录用户）
   - 顶部导航「排行榜」或首页卡片进入 `/leaderboard`，可切换日榜 / 周榜 / 月榜，并用「上一期 / 下一期 / 回到本期」查看往期。
   - 榜单展示排名、用户名、积分、答对题数、活跃天数、单日最佳；前三名与「我」高亮，页面顶部单独显示「我的本期排名」（即使不在前 50 名）。
   - `/leaderboard/api?period=week&date=2026-11-06` 返回同生源 JSON，便于后续做前端异步刷新。
   - 积分规则与统计口径见 [5.5 节](#55-排行榜能力与积分规则)。

7. **浏览阿里云认证栏目**（所有登录用户）
   - 顶部导航新增一级菜单「阿里云认证」，悬停 / 键盘聚焦 / 点击箭头展开二级「阿里云ACA / 阿里云ACP / 阿里云ACE」；
     「阿里云ACP」下再向右展开三级「云计算高级工程师」。点击菜单文字跳转到对应介绍页
     （当前为标题 + 简介占位骨架，内容后续补充）。
   - 入口：`/certification`（总览）、`/certification/aca`、`/certification/acp`、
     `/certification/ace`、`/certification/acp-cloud-computing`；未知 slug 返回 404。
   - 每个栏目页新增「本栏目题库」表格：按 `category_slug` 匹配**自身 + 全部子孙节点**，
     因此 ACP 页能看到三级方向下的题目、总览能看到全部认证题目，兄弟栏目互不串题；
     可直接点「答题 / 查看题目 / 成绩」。未分类的历史题库不进栏目页，只在首页最近列表显示。
   - **配置驱动，便于扩展其他 ACP 方向**：菜单树与页面元数据集中在 `navigation.py` 的 `CERT_NAV`，
     页面由 `/certification/<slug>` 通用视图渲染，面包屑、子栏目卡片与高亮均从配置推导。
     新增方向只需二选一：① 在 ACP 节点的 `children` 里加一个 `NavNode(label, slug, intro)`；
     ② 运行时调用 `navigation.register_acp_direction("acp-bigdata", "大数据高级工程师", "简介")`。
     **无需改动模板、路由函数或 CSS**；重复 slug 会被拒绝。

## 五、用户与权限设计

系统内置**两类角色**，通过 `users.role` 区分：`user`（普通用户）与 `admin`（管理员）。

### 5.1 默认账号

首次运行 `python app.py` 且数据库为空时，自动播种两个演示账号（生产环境请尽快改密）：

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 管理员 | `admin` | `admin123` |
| 普通用户 | `user` | `user123` |

> 普通用户需自行在 `/register` 注册（注册即 `user` 角色，无法自助成为管理员）。

### 5.2 权限规则

| 能力 | 未登录 | 普通用户 | 管理员 |
|------|--------|----------|--------|
| 访问登录页 / 注册页 | ✅ | ✅ | ✅ |
| 答题、查看个人成绩与错题 | ❌ | ✅（仅本人） | ✅ |
| 导出个人数据 | ❌ | ✅（仅本人） | ✅ |
| 按认证栏目浏览题库、首页最近题目 | ❌ | ✅ | ✅ |
| 查看日 / 周 / 月排行榜（全员排名） | ❌ | ✅ | ✅ |
| 浏览阿里云认证栏目（三级菜单、介绍页与题库） | ❌ | ✅ | ✅ |
| 进入后台（导入 / 编辑 / 删除题库 / 用户与答题统计） | ❌ | ❌ | ✅ |

- **未登录**：除 `/login`、`/register` 外所有页面均重定向到登录页（含 `/leaderboard`、`/certification`）。
- **普通用户**：所有成绩、错题、导出均按 `user_id` 过滤，只能看到自己的数据；访问后台、导入页、题库编辑/删除页会被拦截（302 → 首页）。
  - **界面纯净**：普通用户登录后首屏仍是「纯粹答题区域」——「今日答题」「错题本」「排行榜」「最近题目」均为答题相关功能，顶部导航与首页**无任何「导入」按钮或入口**；认证栏目页也不出现导入字样。
- **管理员**：在普通用户能力之上，可进入 `/admin` 后台导入题库（**仅上传 Markdown 文件，且必须选择栏目**）、编辑题库（含标题、日期、栏目与逐题内容）、删除题库（二次确认后级联删除）、查看全部用户的答题记录与用户统计。

### 5.3 关键路由与守卫

- 守卫装饰器：`@login_required`（需登录）、`@admin_required`（需管理员）。
- **登录态强校验**：每次请求都会用 session 里的 `user_id` 回查 `users` 表（结果按请求缓存在 `flask.g`）。
  账号已不存在（如数据库被重建、账号被删）时立即 `session.clear()` 并跳转登录页；
  角色以数据库为准，不信任 cookie 中的 `role`。这样既避免用孤儿 `user_id` 写库触发外键错误，也防越权。
- 结果页 `/result/<id>` 额外校验「记录归属本人或管理员」，防止越权查看他人成绩。
- 密码使用 `werkzeug` 的 `pbkdf2:sha256` **加盐哈希**存储，不保存明文。

### 5.4 后台能力

- `/admin`：概览 KPI（用户数 / 题目日期数 / 答题记录数 / 错题总数）+ 题库管理表格（日期、标题、**栏目**、题数、答题记录数、错题数，以及「编辑 / 查看题目 / 删除题库」入口）。
- `/admin/set/<日期>/edit`：编辑题库基本信息（含栏目）与逐题内容；`admin_logic.save_edit` 把「删单题 → 更新题目 → 重算题数 → 改日期/标题/栏目」放在**同一事务**内，校验失败整体不写入。
- `/admin/set/<日期>/delete`：GET 展示影响范围，POST 需提交与日期完全一致的 `confirm_text` 才执行 `delete_set_cascade`（删除题目、成绩、错题、题目集，单事务回滚保护）。
- `/admin/users`：所有用户及其答题次数、平均正确率、错题数。
- `/admin/records`：全部用户的每日成绩明细（含用户名）。

### 5.5 排行榜能力与积分规则

**积分规则**（`points.py`）
- 每答对 1 题得 1 分（改 `POINTS_PER_CORRECT` 一个常量即可调整分值）。
- 只有**正式答题**（`/quiz/<日期>` 提交）计分；错题重练不计分，避免制造错题反复刷分。
- 每次提交写一条 `points_ledger` 流水（0 分也留痕），由 `grade_and_save` 在**同一事务**内写入，与成绩、错题一起提交或回滚。

**统计口径**（`leaderboard.py`）
- 统计日期按**答题发生日** `earned_date`，补做历史日期的题目计入补做当天；`set_date` 仅用于追溯。
- 每个用户每个自然日只取**当日最佳一次**得分 `MAX(points)`，周榜 / 月榜为这些每日得分之和。
  现有系统允许无限重答，直接累加流水会被刷榜；取每日最佳既保证公平，又完整保留流水供审计 / 换口径。
- 日榜 = 当天；周榜 = 周一至周日；月榜 = 自然月 1 日至月末；积分相同用竞争排名（1, 2, 2, 4 …），
  同分按答对题数、活跃天数、用户名排序展示。
- 只统计积分 > 0 的用户；已删除的用户（`JOIN users`）与已删除题库的流水都不会进榜。

**升级兼容**：`models.init_db()` 会把尚无流水的历史 `daily_records` **幂等回填**为
`source='backfill'` 的积分流水（按 `record_id` 判重），升级前的成绩也能进榜。
`?date=YYYY-MM-DD` 可查看任意往期榜单，`?period=day|week|month` 切换维度。

### 5.6 栏目归属与「历史日期」栏目下线

**数据存储**（`daily_sets` 新增两列）
- `category_slug`：导入时选中的栏目节点 slug，供栏目页按「自身 + 子孙」聚合题目。
- `category_path`：完整路径文案快照（如「阿里云认证 › 阿里云ACP › 云计算高级工程师」），
  用于首页 / 后台 / 栏目页展示。存快照是因为菜单以后可能改名或删除节点，历史题库
  仍需显示可读归属；查询一律用 slug，不依赖中文文案。
- 旧库首次启动由 `_migrate()` 自动 `ALTER` 补两列，旧题库为空值，显示「未分类」，
  不出现在任何栏目页，只在首页最近列表可见。

**选择器组件**：`templates/_category_picker.html` + `static/category_picker.js` 由导入页与
编辑页共用。导入页 `required=1`（未选到末级会阻止提交并提示），编辑页 `required=0`
（留空表示不修改归属）。数据来自 `navigation.category_tree_payload()`，新增认证方向零改动。

**当前限制**：`daily_sets.date` 仍是唯一键，同一天只能存在一套题库；栏目是题库的归属标签，
暂不能「同一天给 ACA 和 ACP 各导入一套」。若需支持，要把题库标识改为 `(date, category)`
复合键，并同步调整 `/quiz/<日期>`、`/date/<日期>`、`/score/<日期>` 等 URL 与积分追溯字段。

**「历史日期」栏目已取消**：顶部导航、首页「我的成绩与历史」卡片、`/history` 路由、
`templates/history.html` 模板及所有「返回历史」链接均已移除，错误回退统一改为首页；
题目改由首页「最近题目」列表与各认证栏目页进入。

## 六、数据库设计（MySQL / SQLite）

**权威 DDL 为 `schema_mysql.sql`**（MySQL 8.0、InnoDB、`utf8mb4_unicode_ci`、8 个外键、
索引内联在 `CREATE TABLE` 中因此可重复执行）。`models.SCHEMA` 是等价的 SQLite DDL，
仅用于本地回退后端。两种后端表名 / 列名 / 索引完全一致，业务 SQL 保持 SQLite 风格的
`?` 占位符，由 `db.MySQLConnection` 自动转换为 `%s`；`PRAGMA table_info` 会映射为
`information_schema.COLUMNS` 查询，行对象统一为 dict-like（同时支持 `row["x"]` 与 `row[0]`）。

| 表 | 关键字段 |
|----|----------|
| `users` | id, username(唯一), password_hash, role(user/admin), created_at |
| `daily_sets` | id, date(唯一), title, total_sets, status(pending/completed), created_at, **category_slug**, **category_path** |
| `questions` | id, daily_set_id, set_index, type, question, options(JSON), answer(JSON), **explanation**(TEXT), created_at |
| `daily_records` | id, daily_set_id, **user_id**, total, correct, wrong, accuracy, duration, finished_at |
| `wrong_questions` | id, daily_set_id, question_id, **user_id**, user_answer(JSON), correct_answer(JSON), created_at |
| `points_ledger` | id, **user_id**, daily_set_id, record_id, **earned_date**(统计发生日), set_date, points, correct, total, source(quiz/backfill), created_at |

`options` 与 `answer` 以 JSON 存储；`explanation` 存解析原始 Markdown 文本（旧库首次启动自动 `ALTER` 补列，旧题为空、不显示解析块）；判断题选项固定为 `A. 正确 / B. 错误`。
`daily_sets.category_slug / category_path` 的迁移与聚合口径见 [5.6 节](#56-栏目归属与历史日期栏目下线)；导出题目 / 成绩 JSON 也会带上这两个字段。
`daily_records` 与 `wrong_questions` 均绑定 `user_id`：成绩为「每用户每日期一条」（再次答题覆盖更新），
错题按「用户 + 题目」唯一确定，保证多人数据相互隔离。旧库首次启动会自动 `ALTER` 补加 `user_id` 字段。

**积分流水 `points_ledger`**：成绩表会被重答**覆盖**，无法作为积分历史来源，因此每次正式答题单独写一条流水。
`earned_date` 是统计周期使用的「答题发生日」，`set_date` 是被答题库的日期（补做时两者不同）；
`source` 区分实时答题与历史成绩回填。建有 `(user_id, earned_date)` 与 `(earned_date)` 两个索引，
榜单按日期范围聚合时可直接命中；`user_id` / `daily_set_id` 均为 `ON DELETE CASCADE` 外键，
删除用户或题库（含 `delete_set_cascade` 中的显式清理）不会留下脏积分。
升级时 `init_db()` 自动把旧 `daily_records` 回填为 `backfill` 流水，按 `record_id` 判重、可重复执行。

**外键与事务**：`get_conn()` 在 SQLite 下开启 `PRAGMA foreign_keys = ON`（MySQL/InnoDB
默认强制外键），成绩/错题/积分表均有 `FOREIGN KEY (user_id) REFERENCES users(id)
ON DELETE CASCADE`（另有题目集、题目外键）。因此写入前必须保证 `user_id` 真实存在。
判分（`grade_and_save`）与错题重练（`practice_wrong`）把「删旧错题 → 写新错题 → 写成绩 →
**写积分流水** → 更新状态」放在**同一个连接、同一个事务**内（`with conn:`），任一环节失败
整体回滚，且 `finally` 关闭连接。驱动异常统一映射为 `db.DatabaseError` / `db.IntegrityError`，
所以路由层的友好错误提示在两种后端下行为一致。

**MySQL 类型映射**：`id` 用 `INT UNSIGNED AUTO_INCREMENT`；`date` / `set_date` /
`earned_date` / `created_at` / `finished_at` 统一用 `VARCHAR` 存字符串（不用 DATE/DATETIME，
避免 Python 侧收到 `datetime.date` 造成类型不一致）；`username` / `title` / `category_slug`
等短文本用带默认值的 `VARCHAR`，题面 / 选项 / 答案 / 解析用 `TEXT`；`accuracy` 用 `DOUBLE`。
MySQL 的 `TEXT` 不支持字面量默认值，因此 `questions.explanation` 在 MySQL DDL 中不带 DEFAULT
（代码写入时始终显式提供，默认空串）；`options` / `answer` 仍以 JSON 文本存储。
`init_db()` 在 MySQL 下会执行 `schema_mysql.sql`，并对旧库做幂等字段补列与积分回填。

## 七、测试

```bash
python test_smoke.py        # 解析 / 导入 / 判分 / 错题 / 导出 / 用户隔离
python test_permissions.py  # 未登录拦截 / 管理员后台 / 普通用户越权拦截 / 成绩与错题归属隔离
python test_user_ui.py      # 普通用户无导入入口 / 管理员保留入口 / 文件名日期自动探测
python test_explanation.py  # 解析收集 / 安全渲染 / 四页面展示 / 防泄题 / 导出携带解析
python test_submit_fix.py   # 提交答题：孤儿 session / 外键约束 / 单事务回滚（独立临时库，可安全在服务运行时执行）
python test_admin_edit.py   # 题库编辑/删除：越权拦截 / 表单校验 / 事务 / 级联删除（独立临时库）
python test_leaderboard.py  # 排行榜：自动记分 / 周期边界 / 跨周月聚合 / 回填 / 权限（独立临时库）
python test_import_ui.py    # 导入页：仅上传文件 / 旧方式失效 / 覆盖确认 / 权限（独立临时库）
python test_cert_nav.py     # 认证三级菜单 / 5 个页面骨架 / 配置扩展 / 无回归（独立临时库）
python test_category_import.py  # 导入栏目 / 栏目页聚合 / 编辑改栏目 / 历史栏目下线（独立临时库）
python test_mysql_backend.py    # 需 DB_BACKEND=mysql；未配置时自动 SKIP，不影响 SQLite 回归
```

**在 MySQL 上跑全套回归**（先启动 MySQL，例如 `docker compose up -d db` 并映射端口）：

```bash
DB_BACKEND=mysql MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 \
MYSQL_USER=quiz MYSQL_PASSWORD=quizpass MYSQL_DATABASE=daily_quiz \
python test_mysql_backend.py
```

全部既有测试脚本都可通过同样的环境变量切换到 MySQL 后端；由于它们改用同一个库，
建议每个脚本运行前重建测试库（或顺序执行并清理），避免日期 / 用户数据互相干扰。

覆盖：
- 正常样本解析（6 套全过）、异常样本解析（4 题失败并给出原因）、导入、判分、成绩落库、错题沉淀、JSON 导出、重复导入覆盖保护；
- 角色权限：未登录仅能访问登录/注册页；管理员可进入后台；普通用户访问后台被拦截（302）；
  成绩与错题均按 `user_id` 隔离，用户之间互不可见。
- 解析：三种「解析」写法均可收集，文末汇总不误收；`**加粗**` 与项目符号安全渲染；
  四个显示答案的页面均展示解析，答题页与重练页不泄露；导出 JSON 携带解析。
- 提交健壮性：含错题提交成功且归属正确；陈旧 session 被自动清理并引导重新登录；
  孤儿 `user_id` 提前拦截为可读异常；写库中途失败时成绩与错题整体回滚；满分提交与错题重练正常。
- 题库维护：越权拦截；标题/日期/逐题内容修改；7 类表单校验失败均整体不落库；
  修改日期与单题删除后的题数重算、错题级联清理、成绩保留；删除确认页与整库级联删除。
- 排行榜：正式答题自动记分且流水与成绩一致；重答取当日最佳、重练不计分；
  日/周/月边界与周期平移（含月末夹紧）；上周同月、上月分数不串榜；同分并列；
  历史成绩幂等回填、删除题库级联清理积分；页面 / JSON 接口 / 入口与权限。
- 导入方式：页面无方式二/三 UI 与脚本；`/import/api/load` 已下线；仅提交粘贴文本被拒绝；
  上传文件可导入且日期/标题自动识别；覆盖确认页暂存内容无需重选文件；非 UTF-8 文件不 500；越权拦截。
- 认证栏目：菜单树 1/2/3 级结构与面包屑；5 个页面骨架与未知 slug 404；三级下拉渲染且原有菜单完整；
  普通用户无导入入口、未登录拦截、既有页面无回归；动态注册 ACP 方向后菜单与页面自动生效、重复 slug 被拒。
- 导入栏目：配置树 / 路径 / 子孙聚合接口；三级选择器渲染；选到「云计算高级工程师」导入正确落库；
  空栏目与伪造 slug 被拒；栏目页父级聚合子级且兄弟栏目不串题；首页 / 后台 / 导出携带栏目；
  编辑页改栏目、留空保持、非法值不落库；动态注册方向后选择器与栏目页自动生效；
  `/history` 404 与导航 / 返回链接清理；普通用户不能导入但可浏览栏目页。
- MySQL 后端：DDL 幂等 / InnoDB / utf8mb4 / 8 个外键；`?→%s`、`PRAGMA` 映射、
  位置索引行对象；`db.IntegrityError` 归一化；单事务回滚；导入 → 判分 → 积分 →
  榜单 → 后台 → 级联删除端到端。

> `test_submit_fix.py`、`test_admin_edit.py`、`test_leaderboard.py`、`test_import_ui.py`、
> `test_cert_nav.py`、`test_category_import.py` 使用独立临时库；
> 其余测试脚本会写入 `data/quiz.db`，建议在服务停止或备份后运行，避免与运行中的服务争用写锁。
> 上述「写入真实库」的脚本也可以先在副本上运行：
> `models.DB_PATH = 副本路径` 后 `runpy.run_path(脚本, run_name="__main__")`。

## 八、故障排查

### 启动报 `连接 MySQL 失败（... 已重试 N 次）`

- 确认 `DB_BACKEND=mysql` 且 `MYSQL_HOST/PORT/USER/PASSWORD/MYSQL_DATABASE` 正确；
  Compose 内部 `MYSQL_HOST` 必须是服务名 `db`，不能写 `127.0.0.1`。
- 确认库已创建（Compose 由 `MYSQL_DATABASE` 自动建库；外部 MySQL 需手工 `CREATE DATABASE`）。
- 确认账号有该库的建表 / 读写权限；MySQL 8 默认 `caching_sha2_password` 认证需要
  `cryptography`（已在 `requirements.txt` 中）。
- `docker compose ps` 看 `db` 是否 healthy；`docker compose logs db` 看初始化是否报错。
- 只想本地快速跑、暂时没有 MySQL：设 `DB_BACKEND=sqlite`，数据写到 `data/quiz.db`。

### 提交答题报 500：`FOREIGN KEY constraint failed`

**现象**：`POST /quiz/<日期>` 返回 500，堆栈停在 `wrong_book.add_wrong`。

**原因**：浏览器 session cookie 里的 `user_id` 在 `users` 表中已不存在
（常见于**重建数据库 / 切换后端之后仍沿用旧登录态**）。由于 `secret_key` 固定，旧 cookie
依旧通过签名校验；若登录守卫不回查数据库，就会用孤儿 `user_id` 去写带外键的错题/成绩表，
被数据库拒绝（SQLite 报 `sqlite3.IntegrityError`，MySQL 报 `pymysql.err.IntegrityError`，
两者都会被 `db.py` 归一化为 `db.IntegrityError`）。

**处理**：
1. **退出登录并重新登录**一次（或清理浏览器该站点的 Cookie）；
2. **重启 Flask 服务**，让代码改动生效并释放可能残留的写事务锁；
3. 当前代码已加入登录态强校验，遇到失效登录态会自动 `session.clear()` 并跳转登录页，不再 500。

> 注意：删除或替换 `data/quiz.db`、或 `docker compose down -v` 重建 MySQL 数据卷，
> 都会让用户 id 重新分配，**已登录的浏览器必须重新登录**。
> 日常使用请勿直接删库；如需重置，先备份，并在重置后通知所有使用者重新登录。

### `database is locked`（仅 SQLite 回退后端）

多为上一次异常请求留下的未提交事务，或调试模式下的残留连接持有写锁。
重启服务即可释放；当前判分链路已改为单事务 + `finally` 关闭连接，正常路径不会残留锁。
