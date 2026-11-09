# 认证题库网

面向**阿里云 / 华为 / AWS / 思科**认证考生的题库与每日刷题站点：
4 个厂商、**34 个三级认证**，每个认证都有独立的认证介绍页、题库、每日 10 题练习，
答题成绩自动进入日 / 周 / 月排行榜，并沉淀到个人中心。

- 用户端：服务端渲染（Jinja2），每个认证页有独立 `title / keywords / description` 与 canonical，
  同时输出 `sitemap.xml` / `robots.txt`，便于收录。
- 管理端：独立 URL（`/admin/login` → `/admin/dashboard`）与独立 `admins` 表，
  纯静态 SPA + `/api/admin/*` JSON，不渲染任何用户端导航。
- 数据层：生产用 **MySQL 8.0**，未配置 MySQL 时自动回退 SQLite，业务 SQL 共用一套
  （`?` 占位符由 `db.py` 适配为 `%s`）。

> 完整设计与取舍见 `docs/认证题库网-总体设计方案.md`。

## 一、信息架构与 URL 地图

```
/                                    首页：左侧分类入口 + 右侧排行榜（日榜）
/cert/aliyun                         阿里云频道：ACA / ACP（云·大模型·大数据）/ ACE
/cert/aliyun/acp                     ACP 方向页：云计算 / 大模型 / 大数据 三个认证
/cert/aliyun/acp/cloud               认证详情：介绍 + 模拟题库卡片 + 开始每日答题
/cert/huawei/datacom/hcip            华为数通 HCIP（其余方向同理）
/cert/aws/solutions-architect/associate
/cert/cisco/ccna                     单级方向直接是认证页（无第三段）
/daily-quiz                          每日答题：选认证 → 随机 10 题 → 判分与解析
/practice/<slug>                     模拟答题：整卷计时、逐题保存、交卷判分与解析（入口在各认证页）
/ranking                             排行榜：日 / 周 / 月 + 积分趋势
/login                               登录：用户名密码 + 图形验证码（失败 5 次锁 15 分钟）
/register                            注册：用户名 4-20 位、全局唯一、失焦实时校验
/profile                             个人中心（需登录）：基础信息 / 账号安全 / 学习数据 / 徽章 / 历史
/forgot-password                     找回密码：输入已绑定邮箱 → 查收邮件 → 设置新密码
/reset-password?token=...            重置密码（一次性链接，30 分钟内有效）
/honors                              旧地址，301 到 /profile
/admin/login  /admin/dashboard       管理端独立入口（图形验证码 + 失败 5 次锁 15 分钟）
```

**旧地址兼容**：`/certification`、`/certification/<slug>` 仍 **301** 到新地址，
历史外链和书签不会 404。独立题库目录页 `/practice` 已下线（题库改为内嵌在认证详情页），
访问旧地址会 301 回首页。

**认证目录**（`catalog.py`，全站唯一事实来源）

| 厂商 | 方向 | 认证数 |
|------|------|--------|
| 阿里云 | ACA、ACP（云计算 / 大模型 / 大数据）、ACE（云计算架构师） | 5 |
| 华为 | 数通 / 云计算 / 安全 / 存储 × HCIA / HCIP / HCIE | 12 |
| AWS | Cloud Practitioner、Solutions Architect（Associate / Professional）、Developer、SysOps、DevOps、Security、Machine Learning | 8 |
| 思科 | CCNA、CCNP（Enterprise / Security / Data Center / Service Provider）、CCIE（同四项） | 9 |

每个认证在 `catalog.py` 中定义：认证定位、适合人群、考试方向、难度、备考建议、SEO 文案。
`bank.sync_catalog()` 在 `init_db()` 里**幂等**写入 `cert_categories` + `cert_intros`：
结构性字段以代码为准，管理员编辑过的介绍与 SEO 字段永不被覆盖。

## 二、目录结构

```
daily-quiz/
├── app.py                  # Flask 入口：用户端页面、双登录入口、Blueprint 注册、遗留导入视图
├── web_site.py             # 用户端 Blueprint：/ /cert/* /daily-quiz /ranking /profile + 用户端 JSON API
├── account.py              # 个人中心领域逻辑：校验 / 脱敏 / 验证码 / 重置 Token / 资料与密码
├── account_api.py          # /api/user/*、/api/auth/* + /forgot-password、/reset-password 页面
├── auth_api.py             # 注册 / 登录 JSON 接口 + 用户名实时校验（/api/auth/*）
├── captcha.py              # 图形验证码（SVG）+ Redis/内存双后端 + 限流锁定；用户端 / 管理端作用域隔离
├── notify.py               # 邮件（SMTP）与短信（Webhook）通道；未配置时开发模式
├── catalog.py              # 认证目录（4 厂商 34 认证）：导航树、面包屑、五段式文案、SEO
├── seo.py                  # SEO 中枢：绝对地址、OG/Twitter、JSON-LD、robots 策略
├── bank.py                 # 认证题库数据访问：分类 / 介绍 / 题目 / 抽题 / 答题记录 / 榜单
├── quiz_service.py         # 每日答题业务：抽题（不下发答案）、服务端判分、落库
├── practice_bank.py        # 模拟题库业务：规则快照、组卷、开考/续答、判分、进度与错题
├── practice_api.py         # /api/practice/*：题库数据、场次恢复、逐题保存、交卷与解析
├── models.py               # 建表与迁移（24 表）、旧库升级、默认账号播种
├── db.py                   # MySQL / SQLite 双后端适配：占位符、字典行、异常归一化、连接重试
├── security.py             # 两套 session 与守卫：login_required / admin_required / api_*_required + 可信 IP
├── csrf.py                 # 全局 CSRF 防护：Origin / Referer / Sec-Fetch-Site + 403 响应
├── runtime.py              # 环境判定：生产 / 本地开发，驱动密码、调试回显等安全默认值
├── admin_api.py            # /api/admin/*：分类 / 题库 / 介绍 / 排行榜 / 导入 / 用户 / 记录
├── admin_service.py        # 导入服务（Markdown/JSON 读取、格式识别、覆盖确认）
├── admin_logic.py          # 遗留题库编辑：表单校验、单事务保存、删除预览与级联删除
├── navigation.py           # 旧版栏目树（供遗留导入页与旧导出接口使用）
├── auth.py                 # 用户 / 管理员两套认证；用户名规则、唯一性、验证码登录服务
├── parser.py               # Markdown / JSON 题目解析（含格式校验与失败原因）
├── quiz_logic.py           # 旧版按日期答题逻辑（遗留视图 / 兼容）
├── points.py / leaderboard.py / score_manager.py / wrong_book.py   # 旧版积分 / 榜单 / 成绩 / 错题
├── seed_bank.py            # 示例题库：4 个旗舰认证各 10 题（幂等）
├── seed_practice.py        # 示例模拟卷：ACP 全真 100 题 + ECS 30 题 + VPC 25 题（幂等）
├── schema_mysql.sql        # MySQL 权威 DDL
├── templates/              # 在用模板：base / home / cert（含题库卡片）/ daily_quiz / bank_quiz / ranking / profile / 404 等
├── static/
│   ├── site.css            # 用户端样式（首页、频道、认证详情、答题、榜单、个人中心）
│   ├── site.js             # 首页榜单切换、三级联动、答题与复盘、趋势图（内联 SVG）
│   ├── style.css           # 基础样式与表单（登录 / 注册 / 遗留页面）
│   ├── nav.js              # 三级下拉菜单交互（hover 桥接 + 260ms 延迟关闭）
│   ├── auth.js             # 登录验证码刷新 / fetch 提交 / 注册用户名实时校验
│   ├── account.css         # 个人中心 / 弹窗 / 找回密码样式
│   ├── practice.css        # 认证页题库卡片、答题页、答题卡、成绩环与逐题解析
│   ├── bank_quiz.js        # 模拟答题：计时、答题卡、逐题保存、交卷、成绩与错题筛选
│   ├── profile.js          # 资料编辑、修改密码、绑定手机与邮箱（前端校验 + 倒计时）
│   ├── recover.js          # 找回 / 重置密码：步骤条、强度条、提交
│   ├── favicon.svg        # 站点图标
│   ├── og-cover.png       # 社交分享封面（1200×630）
│   └── admin/              # 独立管理端 SPA（index.html / login.html / admin.css / admin.js）
├── docs/认证题库网-总体设计方案.md
├── docs/个人中心-设计方案.md      # 原型 / DDL / 接口列表 / 安全设计
├── docs/登录验证码与用户名唯一性-设计方案.md  # 流程图 / 接口 / Redis 结构 / 锁定策略
├── docs/SEO-全站优化说明.md   # 逐路由审计、结构化数据、抓取规则
├── docs/自由练习与模拟题库-设计方案.md  # 页面结构（认证页内嵌）/ 6 张表 DDL / 接口 / 判分与超时兜底
├── tools/nav_hover_check.py # CDP 驱动真实 Chrome 验证下拉菜单可选中
├── tools/shot_auth.py      # CDP 截图核对验证码、用户名提示与 DiceBear 头像
├── tools/shot_admin_auth.py # CDP 截图核对管理员验证码、改密与重置密码入口（探针账号，跑完即删）
├── tools/shot_practice.py   # CDP 截图核对认证页题库 / 答题 / 交卷 / 错题筛选 / 管理端组卷
├── tools/backup_mysql.sh   # 备份到 backups/（保留 7 天）
├── tools/restore_mysql.sh  # 从备份恢复（需 --yes 确认）
├── tools/og_cover.html     # 分享封面的源文件（改动后重新截图生成 PNG）
├── test_*.py               # 24 个回归脚本 + test_support.py 公共夹具（见第九节）
├── requirements.txt / Dockerfile / docker-compose.yml / .env.example
└── data/                   # SQLite 回退库与题目样例
```

## 三、题目格式

### 3.1 Markdown（ACP 风格，最常用）

```markdown
**1.** 阿里云中用于隔离云上网络的逻辑边界是？

- A. 安全组
- B. 专有网络 VPC
- C. 负载均衡 SLB
- D. 云监控

**答案：B**

**解析：**
- **考点**：VPC 提供逻辑隔离的私有网络，可自定义网段、交换机与路由。
```

也支持「第N套 / 单选题 / 多选题 / 判断题」分套格式与 `- A.` 项目符号；题型缺省为单选。
解析支持 `**解析**`、`**解析：**`、`解析：` 三种写法，只放行 `**加粗**` 与项目符号（防注入）。

### 3.2 JSON

```json
{
  "questions": [
    {
      "type": "single",
      "question": "OSPF 使用什么算法计算最短路径？",
      "options": {"A": "Dijkstra", "B": "RIP", "C": "BGP", "D": "STP"},
      "answer": "A",
      "explanation": "**考点**：SPF / Dijkstra"
    }
  ]
}
```

答案可写 `"B"`、`"A,B"` 或 `["A","B"]`；题型 `single / multiple / judge` 或中文。
导入时由 `parser.parse_auto()` 统一解析与校验：缺题干 / 缺选项 / 答案不在选项中 /
单选多答案 / 多选不足两个 / 判断题非 A·B 都会被标记失败，同批其余题目正常入库。

## 四、运行方式

### 4.1 Docker（推荐，MySQL）

```bash
cp .env.example .env           # 必填 SECRET_KEY（openssl rand -hex 32），建议同时设 ADMIN_PASSWORD
docker compose up -d --build
docker compose ps              # db / redis 需 healthy
docker compose logs -f web
```

访问 **http://localhost:5000**：

| 入口 | 地址 | 默认账号 |
|------|------|----------|
| 用户端 | `http://localhost:5000/` | 本地 SQLite 调试为 `user / user123`；Docker（生产）随机生成，见启动日志 |
| 管理端 | `http://localhost:5000/admin/login` | 账号 `admin`；密码由 `ADMIN_PASSWORD` 指定，未配置时随机生成 |

> **首次启动后请立刻执行 `docker compose logs web`**：如果库里还是演示密码
> （`admin123` / `user123`），启动时会自动替换成随机密码，并只在这次日志里打印。
> 也可以用 `docker compose exec web python tools/set_admin_password.py -u admin`
> 随时重置成自己记得住的密码。
>
> `SECRET_KEY` 未配置时 compose 会直接拒绝启动；这是刻意的——旧版本内置的固定密钥
> 一旦随代码公开，任何人都能伪造管理员会话。

首次启动会自动建表、播种 34 个分类与介绍、创建默认账号。示例题库需手动灌一次：

```bash
docker compose exec web python seed_bank.py      # 4 个旗舰认证各 10 题
docker compose exec web python seed_practice.py  # ACP 全真 100 题 + ECS 30 题 + VPC 25 题
```

- 数据卷固定命名为 `daily-quiz_mysql_data`（compose 里显式写了 `name:`）：
  `docker compose down` 只删容器、保留数据，换目录名或改项目名也不会挂到新的空卷。
  只有 `docker compose down -v` 或 `docker volume rm` 才会真正删库。
- 备份 / 恢复：
  ```bash
  bash tools/backup_mysql.sh                            # 导出到 backups/，默认保留 7 天
  bash tools/restore_mysql.sh backups/xxx.sql.gz --yes  # 覆盖恢复，务必先确认
  ```
  建议加定时任务：`0 3 * * * cd /srv/daily-quiz && bash tools/backup_mysql.sh`
- `web` 以非 root 运行 gunicorn（1 worker × 4 线程 + `--preload`），健康检查打到 `/login`。
- `redis` 存放登录验证码、失败计数与账号锁定：gunicorn 是多 worker 多进程，
  这些状态必须跨进程共享，否则会出现「A worker 生成、B worker 校验失败」。
  验证码是短生命周期数据，Redis 关闭了持久化。

### 4.2 本机运行

```bash
pip install -r requirements.txt
cp .env.example .env      # 填 MYSQL_*；不填则回退 SQLite
python app.py             # 默认 127.0.0.1:5000
```

### 4.3 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DB_BACKEND` | 配置了 MySQL 时 `mysql`，否则 `sqlite` | 后端选择 |
| `DATABASE_URL` | 空 | `mysql://user:pass@host:port/db`，优先于 `MYSQL_*` |
| `MYSQL_HOST` / `MYSQL_PORT` | `127.0.0.1` / `3306` | Compose 内为 `db` |
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | `quiz` / 空 / `daily_quiz` | 库与账号 |
| `DB_CONNECT_RETRIES` / `DB_CONNECT_RETRY_DELAY` | `15` / `2` | 启动等待 MySQL |
| `SECRET_KEY` | 无（compose 强制要求） | **会话签名密钥**，必须 ≥16 位随机值；缺失时生产环境每次启动随机生成（重启掉登录态） |
| `ADMIN_PASSWORD` | 空 | 管理员初始密码；为空时生产环境随机生成并打印一次到日志 |
| `WEB_PORT` | `5000` | Compose 宿主机端口 |
| `SITE_BASE_URL` | `http://localhost:5000` | 站点根地址：canonical / og:url / sitemap / 邮件链接都用它，域名部署必改 |
| `ACCOUNT_DEV_MODE` | 生产 `0` / 本地 SQLite 为开 | 为 `1` 时在接口回显验证码 / 重置链接。**生产必须为 0**，否则等于交出任意账号 |
| `COOKIE_SECURE` | `auto` | 会话 Cookie 是否只走 HTTPS；`auto` 按 `SITE_BASE_URL` 是否 https 判断 |
| `TRUST_PROXY_COUNT` | `0` | 受信反向代理层数。`0` 时完全忽略 `X-Forwarded-For`（防伪造 IP 绕过登录锁定） |
| `CSRF_TRUSTED_ORIGINS` | 空 | 额外可信来源（逗号分隔），仅在反代改写 Host 时需要 |
| `MAX_UPLOAD_MB` | `8` | 单次请求体上限，超出返回 413 |
| `MAIL_HOST` / `MAIL_PORT` / `MAIL_FROM` | 空 / `465` / 空 | SMTP 服务器；三项齐备才真实发信 |
| `MAIL_USER` / `MAIL_PASSWORD` / `MAIL_SSL` / `MAIL_STARTTLS` | 空 / 空 / `1` / `0` | SMTP 认证与加密方式 |
| `SMS_WEBHOOK_URL` / `SMS_WEBHOOK_TOKEN` | 空 | 短信网关（POST JSON `{phone, content}`） |
| `REDIS_URL` | 容器内 `redis://redis:6379/0` | 验证码 / 失败计数 / 锁定缓存；未配置时降级进程内缓存（仅单实例） |
| `CAPTCHA_TTL_SECONDS` | `300` | 验证码有效期 |
| `CAPTCHA_RATE_LIMIT` / `CAPTCHA_RATE_WINDOW` | `5` / `300` | 同 IP 取码限流阈值与窗口 |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCK_SECONDS` | `5` / `900` | 连续失败几次锁定、锁定多久 |

### 4.4 1 核 1G 服务器调优

MySQL 8 的默认配置会吃到 450MB 以上，1G 内存的机器必须先压。当前 compose 已内置低配参数：

| 容器 | 实测常态内存 | 上限 | 关键参数 |
|------|-------------|------|----------|
| `db` | ~113MB | `420m` | 关闭 `performance_schema` 与 `binlog`；buffer pool 64M；redo 32M；连接数 30；临时表内存 32M；X Plugin 关闭 |
| `web` | ~62MB | `256m` | gunicorn 1 worker × 4 线程（gthread）；`MALLOC_ARENA_MAX=2` 抑制 glibc 内存碎片 |
| `redis` | ~5MB | `96m` | `maxmemory 64mb`，不落盘 |

合计约 180MB，给系统和突发留出 600MB 以上余量。三个容器都加了 `mem_limit` / `cpus`
与 json-file 日志轮转（10MB × 3），避免日志把磁盘写满、峰值把整机拖死。

建议再补 1–2G swap 兜底（MySQL 峰值与 `docker build` 最容易触发 OOM）：

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile \
  && sudo mkswap /swapfile && sudo swapon /swapfile \
  && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

> 想再省内存可先 `docker stats` 看实际占用：题库几万条以内 `--innodb-buffer-pool-size=64M`
> 足够；不要盲目加大连接数，`max_connections` 已按 1 worker × 4 线程调到 30。

两个刻意做的取舍，按需改回：

- `--skip-log-bin`：关闭 binlog，省磁盘也省内存，代价是没有「按时间点恢复」能力，
  只剩每日全量备份。若以后做主从复制或需要 PITR，删掉这一行并重跑。
- `--innodb-flush-log-at-trx-commit=2`：每秒落盘一次而不是每次提交都 fsync，
  写入更快；极端断电可能丢最后 1 秒已提交事务。答题积分可接受，金融级数据请改回 `1`。

### 4.5 上线前安全检查

代码层已经把这些口子堵上，逐条对应到实现位置，方便复查：

| 风险 | 处理方式 | 位置 |
|------|----------|------|
| **CSRF** | 全局校验 `Origin` / `Referer` / `Sec-Fetch-Site`，跨站写请求直接 403；`SameSite=Lax` 兜底。表单与 fetch 走同一套，不需要逐个模板加 token | `csrf.py`、`app.py` |
| **会话伪造** | `SECRET_KEY` 不再有内置值；管理员会话必须带「密码指纹」，只有 `admin_id` 的伪造 Cookie 一律失效 | `app.py`、`security.py` |
| **默认口令** | 生产环境不再使用 `admin123` / `user123`；检测到旧库仍在使用时，启动即替换为随机密码并打印一次 | `auth.py` |
| **任意账号接管** | 找回密码接口不再回显重置链接（`ACCOUNT_DEV_MODE` 生产默认 0）；邮箱不存在与已存在返回同样文案，避免账号枚举 | `notify.py`、`account.py` |
| **暴力破解** | 登录验证码 + 失败锁定；**默认不信任 `X-Forwarded-For`**，避免伪造 IP 轮换绕过锁定 | `captcha.py`、`security.py` |
| **点击劫持** | `X-Frame-Options: SAMEORIGIN` + `CSP frame-ancestors 'self'` | `app.py` |
| **大包打爆容器** | `MAX_CONTENT_LENGTH=8MB`，超限返回 413；表单字段单独限制 2MB | `app.py` |
| **SQL 注入** | 全部查询参数化；动态列名 / 表名只来自代码内白名单，没有任何拼接用户输入 | `models.py`、`bank.py` |
| **XSS** | Jinja 自动转义；解析文本先 `html.escape` 再渲染；JSON-LD 切断 `</script>`；前端统一 `esc()` | `quiz_logic.py`、`seo.py` |
| **开放重定向** | `next` 只允许站内相对路径，拒绝 `//`、反斜杠与控制字符 | `app.py`、`auth_api.py` |
| **调试器 RCE** | `python app.py` 不再默认 `debug=True`，需显式 `FLASK_DEBUG=1` | `app.py` |
| **越权** | 成绩、错题、练习场次全部按 `user_id` 回查；管理端与用户端会话完全隔离 | `security.py`、`practice_bank.py` |

部署侧还要确认三件事：

1. **HTTPS + 反代**：Nginx 终止 TLS 后转发到 `127.0.0.1:5000`，并把
   `SITE_BASE_URL` 改成 `https://你的域名`（`COOKIE_SECURE=auto` 会自动给
   Cookie 加 `Secure`）。如果反代改写了 `Host`，把对外域名追加到
   `CSRF_TRUSTED_ORIGINS`，否则同源 POST 会被判成跨站。
2. **代理层数**：单层 Nginx 设 `TRUST_PROXY_COUNT=1`，并确保它用
   `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`；直连（如
   `docker compose` 直接暴露端口）保持 `0`。
3. **不要暴露 3306**：compose 默认不映射 MySQL 端口，仅容器内网可达；
   需要宿主机工具连库时临时开，用完关掉。

自查命令（容器已启动）：

```bash
curl -sI http://127.0.0.1:5000/ | grep -Ei "x-frame|content-security|nosniff"
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:5000/api/auth/login \
     -H "Origin: https://evil.example.com" -H "Content-Type: application/json" -d '{}'   # 期望 403
docker compose exec web python test_security_hardening.py                               # 25 项断言
```

已知取舍（按当前规模可接受，规模上来再补）：

- 登录失败按**账号**锁定 15 分钟，攻击者可用错误密码把某个账号锁住（骚扰型 DoS）；
  如需彻底避免，改成「IP + 账号」双维度计数。
- 普通用户改密后，其它设备上的旧会话不会立即失效（管理员端有指纹，普通用户没有）；
  当前会话是浏览器级 Cookie，关闭浏览器即失效，风险有限。
- 交卷时由服务端重新判分，但题目范围来自前端提交的 10 个 ID；理论上可以跨分类
  刷自己的分数（只影响本人榜单成绩，不影响他人数据）。若要彻底封死，需在
  `/api/quiz/start` 时把题号写进 session，交卷时比对。

## 五、用户端流程

1. **选认证**：首页左侧按厂商 → 方向 → 认证进入详情页；导航栏 4 个厂商均为三级下拉。
2. **看介绍**：认证页首屏是五段式介绍（认证定位 / 适合人群 / 考试方向 / 难度 / 备考建议），
   内容来自 `cert_intros`，管理员可改；下方「模拟题库」区块展示该认证下已发布的题库卡片。
3. **每日答题**：点「开始每日答题」进入 `/daily-quiz?category=<path>`，
   从该认证题库**随机抽 10 题**，每题 10 分，满分 100。
   - 该认证题量不足 10 时，按「本认证 → 同方向 → 同厂商 → 全站」逐级兜底，页面会标明题源。
   - 抽题与判分全部在服务端；HTTP 响应**不含** `answer` / `explanation`。
   - 交卷后展示得分、正确率、用时、逐题对错与解析。
4. **模拟题库**：直接挂在认证详情页（如 `/cert/aliyun/acp/cloud`）的「模拟题库」区块，
   不占用全站导航。管理员在后台发布后，题库立刻出现在对应认证页，其他认证看不到。
   - 卡片只展示名称、题量、题型比例、规则摘要、练习进度与入口，**认证页不出现题干**；
     顶部汇总「共 N 套 · M 道题」，与首屏「道题可练」数字一致。
   - 点「开始练习 / 继续练习」进入 `/practice/<slug>`，顶部固定「当前题库：X | 第 N/M 题」与倒计时，
     返回链接回到所属认证页。
   - 作答逐题保存（320ms 防抖 + 串行），刷新可续答；倒计时归零由服务端兜底自动交卷。
   - 抽题口径：从题库全部有效题中**随机取样**，再按「每卷题量」截断；题库扩容后
     新题也会被抽到（不是只考前 N 题）。同一用户单日对同一题库最多开考 20 次。
   - 交卷后展示得分、合格判定与逐题解析，可「查看错题」「重新练习」。
     **未作答的题不下发答案与解析**（防止「开考→交白卷→交卷」批量导出题库），
     已作答的题正常给出答案解析。
   - 积分：正确率 × 2 计入日 / 周 / 月榜（100% 正确 → 200 积分），同一题库每人每天
     只计一次，重复练习不叠加；完成率低于 60%（交白卷、只做几题）不计分。
   - 超时未交卷的场次由服务端兜底结算：除了用户再次进入该题库时触发，
     每个进程每小时还会做一次全局回收，关掉页面不再回来的场次不会永久堆积。
5. **记成绩**：一次交卷写 `quiz_records`（明细）+ `user_honors`（荣誉流水）+ 日 / 周 / 月榜；
   模拟考交卷写 `bank_practice_sessions` + `bank_exam_awards`（积分流水，同题库每日一次）+ 日 / 周 / 月榜。
   两条线的积分在排行榜与个人中心合并展示，`bank_exam_awards` 的唯一键保证并发交卷不会重复加分。
6. **看榜单 / 个人中心**：`/profile` 是个人数据的总入口，页头提供「继续答题 / 错题本 / 排行榜」
   三个按钮；`/ranking` 支持日 / 周 / 月切换与 7 天积分趋势；`/profile` 汇总
   账号信息、日 / 周 / 月排名、累计积分与最佳成绩、徽章（初次登场 / 满分王者 / 积分新星 /
   千分达人 / 坚持三天）与历史答题记录。

**浏览公开、答题才登录**：所有认证页（含题库卡片）、排行榜均可匿名浏览；
`/api/quiz/start`、`/api/quiz/submit`、`/api/practice/banks/<slug>/start`、`/profile`
需要登录，未登录返回 401 JSON 或 302 到登录页。
顶栏只放公共模块，不再单列「个人荣誉」「排行榜」「错题本」，点右上角用户名即可进入个人中心；
旧链接 `/honors` 会 301 过去。

7. **维护账号**：`/profile` 分三段——
   - **基础信息**：昵称（2-20 字）、性别（男 / 女 / 保密）、个人简介（≤200 字），原位展开编辑；
   - **账号安全**：手机号与邮箱只展示脱敏值（`138****1234`、`abc***@gmail.com`），
     支持修改密码（校验原密码，8-16 位且含字母数字，改后强制重新登录）与绑定 / 更换；
   - **学习数据**：累计答题次数、最高分、本月排行榜积分、平均正确率、日 / 周 / 月名次与最近答题。
   绑定走「发送验证码 → 校验 → 唯一性检查」，手机号与邮箱在 `users` 表中唯一；
   邮箱验证后可走 `/forgot-password` 收一次性重置链接（30 分钟、限频、用后即失效）。

## 六、管理端

`/admin/dashboard` 是独立静态 SPA，菜单只有管理功能：控制台概览、题库管理、题库导入、
模拟题库、认证介绍管理、认证分类、排行榜数据、用户与权限、答题记录。

1. **题库导入**：必须选择**三级认证分类**（选择器数据来自 `cert_categories`），
   上传 Markdown / JSON 或粘贴内容。导入会做两件事：
   - 自动生成一个**已发布的模拟题库**（以文件名命名，出现在该认证详情页的「模拟题库」区块，
     学员可直接针对性练习；同名文件重复导入会替换旧的自动题库，不会堆积）；
   - 同步写入旧题库表，兼容历史数据。
   **每日答题的题源就是模拟题库**：从该认证已发布的模拟题库里随机抽 10 题；
   若该认证还没建模拟题库，自动回退到旧题库（含同方向 / 同厂商 / 全站兜底）。
2. **认证分类**：查看 34 个分类的题量、难度、上架状态，编辑名称、考试代码、
   难度、排序、上下架与 **SEO 标题 / 关键词 / 描述**（直接决定该页 `<title>` 与 meta）。
3. **认证介绍管理**：编辑五段式介绍（定位 / 人群 / 方向 / 难度 / 建议）与资料来源、一句话摘要。
   认证定位、适合人群、考试方向、备考建议为必填，留空返回 400，避免把页面写空。
4. **题库管理**：按分类与关键词检索，新增 / 编辑 / 删除单题；答案必须出现在选项中。
5. **模拟题库**：按真实考试规则组卷并发布，发布后出现在对应认证详情页。
   - 新建时选「考试规则」（ACP 云计算 / ACP 大模型 / 自定义），题量、单选/多选、满分、
     合格分、时长自动回填并锁定，避免手填出 99 题或 60 分合格的卷子；
   - 发布前强制校验「实际题量 = 声明题量、实际单选 = 声明单选、实际多选 = 声明多选、
     规则快照未过期」，不满足只能存草稿；
   - 支持单题增改删与 Markdown / JSON 批量导入（只收单选与多选）；
   - 列表展示每个题库的累计练习人次、参与人数与平均分；可停用（前端隐藏、历史记录保留），
     有练习记录的题库禁止删除；
   - 「考试规则」弹层可改题量、比例、总分、合格分与时长，改完不影响已发布题库的快照。
6. **排行榜数据**：按日 / 周 / 月查看积分、答题次数、最高分。
7. **用户与权限**：普通用户（`users`）与管理员（`admins`）分表；可新增管理员、重置其他管理员的密码
   （需输入操作者本人的密码确认，被重置账号在其它设备上的登录立即失效）。
8. **修改密码**：校验原密码后改密，要求 8-64 位且同时包含字母和数字；成功后本人会话立即退出，
   其他设备上的管理端会话因密码指纹不匹配同步失效。
9. **发版与缓存**：`/admin/dashboard` 返回外壳时会自动给 `admin.css` / `admin.js` 追加
   `?v=<static 最新 mtime>`，URL 一变浏览器就必须重新拉取。历史上这两个文件被按 7 天强缓存，
   发版后旧脚本缺少新菜单的渲染分支，表现为「点菜单地址栏变了、右侧内容不动」；现在外壳与
   管理端静态资源都走 `no-cache` 协商缓存，旧副本打开一次页面就会自动换新。

> 旧书签 `/import`、`/admin/set/<date>/edit` 等仍可用（`admin_base.html` 布局，同样无用户导航），
> 走的是「按日期题库」老模型；从该入口导入的题目会自动挂到所选三级分类（`cert_category_id`），
> 因此同样能在认证页与新题库中看到。

## 七、数据库设计

权威 DDL：`schema_mysql.sql`（MySQL 8.0 / InnoDB / utf8mb4）。`models.SCHEMA` 是等价 SQLite DDL。
两套后端表名列名完全一致，行对象统一为 dict-like。

### 7.1 新版核心表

| 表 | 关键字段 | 说明 |
|----|----------|------|
| `users` | id, username(唯一), password_hash, nickname, **gender, bio, phone(唯一), phone_verified, email(唯一), email_verified, reset_token, reset_token_expire_at, updated_at**, role(恒 user), created_at | 普通用户 + 个人中心 |
| `admins` | id, username(唯一), password, role, display_name, status, last_login_at | 管理员（独立于 users） |
| `cert_categories` | id, vendor/direction/level_slug+name, **path_slug**(唯一), cert_name, exam_code, difficulty, sort_order, status, seo_title/keywords/description | 34 个认证分类，`sync_catalog` 幂等播种 |
| `cert_intros` | id, **category_id**(唯一, FK), positioning, audience, exam_topics, difficulty, advice, summary, source, updated_by | 五段式介绍，管理员可改 |
| `questions` | id, **cert_category_id**(FK), daily_set_id(可空), set_index, type, question, options(JSON), answer, explanation, difficulty, status, created_at | 认证题库题目 |
| `quiz_records` | id, user_id(FK), category_id(FK), cert_path, cert_name, question_ids(JSON), answers(JSON), total, correct, score, accuracy, duration, started_at, finished_at | 每次交卷一行 |
| `user_honors` | id, user_id(FK), category_id(FK), cert_name, score, correct, total, duration, quiz_date, created_at | 个人荣誉流水 |
| `rankings` | id, period_type(day/week/month), **period_key**, user_id, points, quiz_count, best_score, updated_at | 榜单聚合，唯一键 `(period_type, period_key, user_id)` |
| `sms_codes` | id, user_id(FK), phone, purpose, **code_hash**, expires_at, attempts, used, created_at | 短信验证码：10 分钟、单码 5 次尝试 |
| `email_tokens` | id, user_id(FK), email, purpose(bind_email/reset_password), code_hash, **token_hash**, expires_at, attempts, used, created_at | 邮箱验证码与一次性重置 Token |
| `exam_rules` | id, **code**(唯一), name, vendor_slug, cert_path, total_questions, single_count, multiple_count, score_per_question, total_score, pass_score, duration_minutes, is_preset, status | 考试规则模板：ACP 云计算 / ACP 大模型 / 自定义 |
| `question_banks` | id, **slug**(唯一), name, vendor/cert, category_id(FK), level_tag, difficulty, **rule_code, total_questions, single_count, multiple_count, total_score, pass_score, duration_minutes**, status, published_at | 题库 + 规则快照；发布时校验实际题量与比例 |
| `bank_questions` | id, **bank_id**(FK, CASCADE), type(single/multiple), question, options(JSON), answer(JSON), explanation, difficulty, status | 题库题目，答案不与题面一起下发 |
| `bank_practice_sessions` | id, **token**(唯一), bank_id, user_id, **question_ids(JSON 快照)**, answers(JSON), total/answered/correct, score, accuracy, status, deadline_at, duration, submit_reason | 一次练习一行；超时由服务端结算 |
| `bank_practice_answers` | id, session_id, bank_id, user_id, question_id, user_answer(JSON), is_correct, answered_at；唯一键 `(session_id, question_id)` | 逐题作答，交卷时结算对错 |
| `bank_practice_progress` | id, user_id, bank_id, answered_count, correct_count, wrong_count, best_score, last_score, last_session_id；唯一键 `(user_id, bank_id)` | 认证页卡片「已练 N/M」「错题 N」的数据源 |
| `bank_exam_awards` | id, user_id, bank_id, session_id, earned_date, score, points, created_at；唯一键 `(user_id, bank_id, earned_date)` | 模拟考积分流水：同题库每人每天只计一次，重复练习不叠加 |

`period_key` 形如 `2026-11-07` / `2026-W45` / `2026-11`。

### 7.2 旧版表（仍在使用）

`admin`（旧管理员表，启动时迁入 `admins`）、`daily_sets`、`daily_records`、
`wrong_questions`、`points_ledger`、`cert_intro`（旧版单段介绍，保留兼容）。
旧书签导入、按日期答题与错题本仍依赖这些表。

### 7.3 迁移要点（旧库升级）

- `users.nickname`、`questions.cert_category_id / difficulty / status` 按需补列。
- `questions.daily_set_id` 改为可空：SQLite 用「建新表 → 拷数据 → 改名」重建；
  MySQL 用 `MODIFY ... NULL`。认证题库的题目不挂日期题库。
- `questions.cert_category_id` 在 MySQL 下必须是 `INT UNSIGNED`（与 `cert_categories.id` 同类型），
  否则外键创建报 3780；迁移会先对齐类型再补 `fk_questions_category`（`ON DELETE CASCADE`）。
- 启动时清理「分类已不存在」的孤儿题目，避免污染全站兜底题池。
- 旧 `admin` 表账号迁入 `admins`，可沿用原密码哈希。
- 个人中心字段逐个补列；`phone / email` 先清洗空串为 `NULL`，再建 `uk_users_phone` /
  `uk_users_email` 唯一索引（SQL 唯一索引允许多个 `NULL`，未绑定不会互相冲突）。
- 验证码与重置 Token 只存 SHA-256（`code_hash` / `token_hash`），数据库泄露也无法直接使用。

## 八、API 设计

### 8.1 用户端（`web_site.py`）

| 方法与路径 | 说明 | 登录 |
|-----------|------|------|
| `GET /api/home` | 首页数据：分类树（含题量）、平台统计、日榜 | 否 |
| `GET /api/cert/tree` | 厂商 → 方向 → 认证三级树 + 每级题量 | 否 |
| `GET /api/cert/<path>/intro` | 某认证的介绍与题量，如 `/api/cert/aliyun/acp/cloud/intro` | 否 |
| `POST /api/quiz/start` | `{category: "aliyun/acp/cloud"}` → 10 题（无答案）、题源、开始时间 | 是 |
| `POST /api/quiz/submit` | `{category_id, question_ids, answers, duration, started_at}` → 分数、正确率、逐题复盘 | 是 |
| `GET /api/ranking?period=day&limit=20` | 榜单行、我的排名、7 天趋势 | 否 |
| `GET /api/profile/me` | 我的统计、徽章、日周月排名、历史记录（`/api/honors/me` 仍兼容） | 是 |
| `GET /sitemap.xml`、`/robots.txt` | 全站 URL 与抓取规则 | 否 |

### 8.2 注册与登录（`auth_api.py`）

| 方法与路径 | 入参 | 说明 | 登录 |
|-----------|------|------|------|
| `GET /api/auth/check-username` | `username` | 注册页实时校验：`{ok, available, username, code, message}`，同 IP 60 次/分钟 | 否 |
| `POST /api/auth/register` | `username, password, confirmPassword, email?, phone?, next?` | 用户名 4-20 位 / 非保留词 / 唯一；重名 **409** `USERNAME_TAKEN` | 否 |
| `GET /api/auth/captcha` | — | `{captchaKey, image(SVG data URI), expiresIn}`，同 IP 5 次/5 分钟 | 否 |
| `POST /api/auth/login` | `username, password, captchaKey, captchaCode, next?` | 先验验证码再验密码；验证码错误 400、密码错误 401、锁定 423 | 否 |

验证码有效期 5 分钟、校验即删除；失败 5 次锁定 15 分钟；验证码缓存优先 Redis
（`REDIS_URL`），不可用时降级进程内缓存。页面表单 `POST /login` 与 JSON 接口共用同一套校验，
关闭 JS 也无法绕过验证码。

### 8.3 个人中心与找回密码（`account_api.py`）

| 方法与路径 | 入参 | 说明 | 登录 |
|-----------|------|------|------|
| `GET /api/user/profile` | — | 资料 + 脱敏后的手机 / 邮箱 + 统计 + 日周月排名 | 是 |
| `PUT /api/user/profile` | `nickname, gender, bio` | 昵称 2-20、性别枚举、简介 ≤200 | 是 |
| `PUT /api/user/password` | `old_password, new_password, confirm_password` | 校验原密码与强度；成功后清 session | 是 |
| `POST /api/user/bind-phone` | `phone` | 发送短信验证码（60 秒冷却 / 1 小时 5 条） | 是 |
| `POST /api/user/verify-phone` | `phone, code` | 校验并写入 `phone_verified=1` | 是 |
| `POST /api/user/bind-email` | `email` | 发送邮箱验证码 | 是 |
| `POST /api/user/verify-email` | `email, code` | 校验并写入 `email_verified=1` | 是 |
| `POST /api/auth/forgot-password` | `email` | 生成一次性 Token（30 分钟）并发邮件 | 否 |
| `POST /api/auth/reset-password` | `token, password, confirm_password` | 校验 Token 并改密，用后立即失效 | 否 |

失败统一 `{ok: false, error, code}`：`VALIDATION_ERROR` / `INVALID_CREDENTIALS` /
`CODE_INVALID` / `TOKEN_INVALID` / `PHONE_TAKEN` / `EMAIL_TAKEN` / `RATE_LIMITED`(429)。
手机号与邮箱一律返回脱敏值；管理员 session 调这些接口一律 401。

### 8.4 管理端（`admin_api.py`，统一 `{ok, message, data}`）

| 方法与路径 | 说明 |
|-----------|------|
| `GET /api/admin/captcha` | 管理端登录验证码：`{captchaKey, image(SVG), expiresIn}`，`no-store`；同 IP 20 张/5 分钟 |
| `POST /api/admin/login` | 管理员登录：验证码 → 账号密码；验证码错误 400、密码错误 401、锁定 423 |
| `POST /api/admin/logout` / `GET /api/admin/session` | 独立管理员会话 |
| `POST /api/admin/password` | 修改本人密码：`old_password, new_password, confirm_password`；成功返回 `relogin + redirect` |
| `POST /api/admin/admins/<id>/password` | 重置其他管理员密码：`new_password, confirm_password, operator_password` |
| `GET /api/admin/meta`、`/overview` | 菜单字典、KPI 概览 |
| `GET /api/admin/cert-categories?q=&vendor=` | 分类列表（含题量、SEO 字段） |
| `POST /api/admin/cert-categories/<id>` | 改名称 / 考试代码 / 难度 / 排序 / 状态 / SEO |
| `GET /api/admin/intros?q=` | 五段式介绍列表与搜索 |
| `POST /api/admin/intros/<category_id>` | 保存介绍（四个核心字段必填） |
| `GET /api/admin/questions?category_id=&q=&page=` | 题目分页检索 |
| `POST /api/admin/questions` / `POST /api/admin/questions/<id>` / `.../delete` | 题目增改删 |
| `POST /api/admin/bank/import` | `{category_id, markdown, fmt}` 或上传文件，必须绑定三级分类 |
| `GET /api/admin/rankings?period=day` | 榜单数据 |
| `GET /api/admin/users` / `/records` / `/admins` | 用户、答题记录、新增管理员 |
| `GET /api/admin/practice-banks?q=&status=` | 模拟题库列表 + 汇总（题库数 / 已发布 / 累计人次 / 平均分）+ 规则选项 |
| `GET /api/admin/practice-banks/<id>` | 题库详情：题目、统计、`publish_errors` |
| `POST /api/admin/practice-banks` / `PUT .../<id>` | 新建 / 编辑题库（规则字段自动回填并锁定） |
| `POST /api/admin/practice-banks/<id>/status` | `{status: published\|disabled}`；发布前跑题量与比例校验 |
| `POST /api/admin/practice-banks/<id>/delete` | 删除；有练习记录返回 409，需先停用 |
| `POST /api/admin/practice-banks/<id>/questions`、`PUT/DELETE /api/admin/practice-questions/<id>` | 单题增改删 |
| `POST /api/admin/practice-banks/<id>/import` | 批量导入 Markdown / JSON（只收单选与多选） |
| `GET /api/admin/exam-rules`、`PUT /api/admin/exam-rules/<code>` | 考试规则列表与修改 |

鉴权：管理端接口只认管理端 session（`admin_id`），用户 session 一律 401；
用户端答题接口反之。两套登录态可同时存在但互不越权。

### 8.5 模拟题库（`practice_api.py`，前缀 `/api/practice`）

| 方法与路径 | 入参 | 说明 | 登录 |
|-----------|------|------|------|
| `GET /api/practice/banks` | `status=all/new/done/wrong`、`q`、`vendor`、`cert` | 题库列表 + 我的进度 + 未交卷场次 | 否 |
| `GET /api/practice/banks/<slug>` | — | 单题库详情 | 否 |
| `POST /api/practice/banks/<slug>/start` | `{restart?: bool}` | 开始 / 续答；`restart=true` 强制新场次 | 是 |
| `GET /api/practice/sessions/<token>` | — | 恢复场次（题目、已存答案、剩余秒数） | 是 |
| `POST /api/practice/sessions/<token>/answer` | `{question_id, answer: ["B"]}` | 保存单题；**不回对错、不下发答案** | 是 |
| `POST /api/practice/sessions/<token>/submit` | `{reason: "manual"\|"timeout"}` | 判分并返回成绩与逐题解析 | 是 |
| `GET /api/practice/sessions/<token>/result` | — | 已交卷场次的解析 | 是 |

失败统一 `{ok:false, error, code}`：`VALIDATION` / `NO_QUESTIONS` / `BANK_DISABLED` /
`NOT_FOUND` / `NOT_SUBMITTED`。场次按 `user_id + token` 双条件校验，换账号拿到 token 也读不到别人的卷子。

## 九、测试

```bash
# 全部 SQLite 回归（各自使用独立临时库，可与运行中的服务并存）
for f in test_*.py; do python "$f" || break; done

# MySQL 专项（容器内或 compose 网络内执行）
docker compose exec web python test_mysql_backend.py
```

| 脚本 | 覆盖 |
|------|------|
| `test_smoke.py` | 解析 / 导入 / 判分 / 错题 / 导出 / 用户隔离 |
| `test_permissions.py` | 游客浏览、答题需登录、越权拦截、成绩与错题归属 |
| `test_user_ui.py` | 用户端导航、管理端无用户菜单、日期探测 |
| `test_explanation.py` | 解析收集、安全渲染、四页面展示、防泄题 |
| `test_submit_fix.py` | 孤儿 session、外键约束、单事务回滚 |
| `test_admin_edit.py` | 题库编辑 / 删除、越权拦截、表单校验、级联删除 |
| `test_leaderboard.py` | 日 / 周 / 月榜边界、同分并列、回填、权限（旧积分口径） |
| `test_import_ui.py` | 导入页仅上传文件、旧方式失效、覆盖确认 |
| `test_cert_nav.py` | 34 认证目录、三级导航、面包屑、canonical、sitemap、旧地址 301 |
| `test_cert_intro.py` | 介绍 CMS、题库绑定、判分、荣誉与三榜、双端鉴权 |
| `test_category_import.py` | 旧导入挂接分类、编辑迁栏目、兄弟分类隔离 |
| `test_profile_security.py` | 资料校验、改密规则、手机 / 邮箱绑定、验证码限流、找回密码 Token |
| `test_username_captcha.py` | 用户名规则 / 归一化 / 三层唯一性、SVG 验证码一次性、登录失败锁定、取码限流、DiceBear 头像 |
| `test_seo.py` | 绝对 canonical / OG、JSON-LD 类型、robots 与 X-Robots-Tag、sitemap、404、gzip、静态缓存 |
| `test_guest_access.py` | 游客可访问的页面与接口、登录回跳、防开放重定向 |
| `test_admin_isolation.py` | 独立 admins 表、双入口、API 全链路、菜单隔离 |
| `test_admin_password.py` | 管理端验证码作用域、失败锁定、自助改密、他人密码重置、旧会话失效 |
| `test_admin_shell.py` | 管理端外壳静态资源带 `?v=` 指纹、协商缓存、未知模块兜底提示、渲染代次防覆盖 |
| `test_practice_bank.py` | 认证页题库卡片防泄题 / 不串认证、规则数值、100 题下发无答案、续答与进度、判分 / 错题归零 / 超时交卷、发布校验、停用与删除保护、SEO |
| `test_exam_audit.py` | 二次审计 B0：抽题覆盖全题库、交白卷不下发答案解析、模拟考积分与同题库每日幂等、重复结算不重复加分、超时场次全局回收 |
| `test_layout.py` | 全站容器不随内容收缩（flex + auto margin 陷阱）、答题区无窄宽度、面包屑与「已答 N/M」文案 |
| `test_security_hardening.py` | CSRF 跨站拦截 / 同源放行、管理端会话指纹、IP 不可伪造、生产环境关闭调试回显、默认凭据、开放重定向、安全响应头（25 项断言） |
| `test_mysql_backend.py` | MySQL 全部建表 / 外键 / 占位符 / 事务 / 个人中心端到端（未配置时 SKIP） |

导航下拉菜单属于纯前端交互，Python 测试覆盖不到，用真实浏览器跑：

```bash
python -m pip install websocket-client     # 仅首次
python tools/nav_hover_check.py            # 输出 RESULT: PASS/FAIL + 截图
```

脚本通过 CDP 驱动本机 Chrome，把鼠标从一级菜单逐像素移到二级、再横移到三级，
断言全程 `display` 不回落到 `none`，移开 550ms 后才收起。

登录验证码 / 注册页提示 / 头像同样用真实浏览器核对：

```bash
python tools/shot_auth.py     # 输出 RESULT: PASS/FAIL + 5 张截图
```

管理员登录验证码与改密 / 重置密码入口：

```bash
python tools/shot_admin_auth.py   # 用一次性探针管理员走完整改密流程，输出 6 张截图
```

模拟题库全链路（认证页题库卡片 → 模拟答题 → 交卷解析 → 错题筛选 → 管理端组卷）：

```bash
python tools/shot_practice.py     # 一次性探针用户，跑完级联删除；输出 10 张截图 + RESULT
```

在宿主机对容器 MySQL 跑 MySQL 专项（MySQL 未映射端口时）：

```bash
docker run --rm --network daily-quiz_default \
  -v "$PWD:/app" -w /app \
  -e DB_BACKEND=mysql -e MYSQL_HOST=db -e MYSQL_USER=quiz \
  -e MYSQL_PASSWORD=quizpass -e MYSQL_DATABASE=daily_quiz \
  --entrypoint python daily-quiz:latest test_mysql_backend.py
```

## 十、故障排查

**`BLOB, TEXT ... can't have a default value`（MySQL 1101）**
MySQL 的 TEXT 不能带字面量默认值。短文本列（`nickname`、`difficulty`、`status`）在 MySQL 迁移中
使用 `VARCHAR`，TEXT 列只允许无默认值。

**`Referencing column ... are incompatible`（MySQL 3780）**
外键两侧类型必须完全一致。`questions.cert_category_id` 需为 `INT UNSIGNED`；
迁移会自动 `MODIFY` 后再建 `fk_questions_category`。

**认证页题库为空，但后台显示已导入**
检查题目是否带 `cert_category_id`（新题库导入自带；旧 `/import` 入口会自动挂接）。
`SELECT COUNT(*) FROM questions WHERE cert_category_id IS NULL;` 为 0 即正常。

**启动报连接 MySQL 失败**
确认 `MYSQL_HOST` 在 Compose 内是服务名 `db`；库已创建；账号有建表与读写权限。
MySQL 8 的 `caching_sha2_password` 需要 `cryptography`（已在 `requirements.txt`）。

**提交答题 500 `FOREIGN KEY constraint failed`**
多为重建数据库后仍沿用旧 cookie（`user_id` 已不存在）。退出重登即可；
当前代码会回查用户表并清理失效 session，不再 500。

**`database is locked`（仅 SQLite）**
上一次异常请求残留写事务。重启服务即可；判分链路已是单事务 + `finally` 关闭连接。

**改了文案但页面没变**
模板与静态资源随镜像构建，需 `docker compose up -d --build`；
管理员在后台改的认证介绍、SEO 字段存数据库，保存即生效，无需重启。


## 十一、SEO 与抓取策略

全站 SEO 由 `seo.py` 统一产出，模板只消费 `seo` 字典。逐路由审计见
`docs/SEO-全站优化说明.md`。

- **绝对地址**：canonical、`og:url`、`og:image`、sitemap 全部用 `SITE_BASE_URL` 拼绝对地址；
  未配置时才退回请求 host。上线务必设置 `SITE_BASE_URL=https://你的域名`。
- **独立文案**：首页、4 个厂商、所有方向、34 个认证、每日答题、排行榜各自有
  `title` / `description` / `keywords`；认证页可被后台 SEO 字段覆盖，保存即生效。
- **结构化数据**（JSON-LD，单 `@graph` 输出）：
  - 首页：`Organization` + `WebSite`
  - 厂商 / 方向：`BreadcrumbList` + `CollectionPage`
  - 认证详情：`BreadcrumbList` + `LearningResource`（含题量、难度、厂商）
  - 认证页：`BreadcrumbList` + `LearningResource`（含认证名、题量、难度、厂商）
  - 排行榜：`WebPage`
- **抓取范围**：`robots.txt` 只放行内容页；`/admin`、`/api/`、`/profile`、`/quiz/`、
  `/result/`、`/wrong`、`/score/`、`/date/`、`/import`、`/export/`、`/leaderboard`、
  `/practice/<slug>`（答题页）、`/reset-password` 一律 `noindex`，页面 meta 与 `X-Robots-Tag` 响应头双保险。
- **重复内容与死链**：`/certification/*`、`/honors` 301 到新地址；
  认证页（含题库卡片）可索引，单份试卷 `/practice/<slug>` 为 `noindex,follow`；
  404 统一渲染站内导航 + `noindex`，且不输出 canonical。
- **性能**：HTML / JSON / XML 走 gzip（约 5:1）；CSS / JS 带 `?v=` 指纹并缓存 7 天；
  首屏头像固定 `width` / `height`，避免布局抖动。
- **上线动作**：在 Google Search Console / 百度站长提交 `https://你的域名/sitemap.xml`。

自检命令：

```bash
python test_seo.py                                  # 8 组 SEO 断言
curl -s https://你的域名/sitemap.xml | head -20
curl -sI https://你的域名/cert/aliyun/acp/cloud | grep -iE "canonical|x-robots|content-encoding"
```
