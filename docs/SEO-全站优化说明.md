# 全站 SEO 优化说明

审计范围：`app.py` / `web_site.py` / `account_api.py` / `auth_api.py` 注册的全部路由，
以及 `templates/` 下的页面模板与 `static/` 资源。逐条确认「是否应被收录、canonical 指向哪里、
输出哪些结构化数据」。

## 一、审计结论（优化前）

| # | 问题 | 影响 |
|---|------|------|
| 1 | canonical 用 `request.path` 输出相对地址；`og:url` 直接取 `request.url`（带 `?period=` 等参数） | 同一内容可能出现多个 URL 版本，权重分散 |
| 2 | 只有基础 title/description，没有 `og:image`、`og:locale`、Twitter Card | 分享到微信/社交平台无摘要图 |
| 3 | 全站无 JSON-LD | 失去面包屑、题库实体的结构化理解 |
| 4 | 未注册 404 处理器，未知 URL 落到 Flask 默认页 | 裸错误页无导航、无 noindex，易被判软 404 |
| 5 | 个人中心、答题、成绩、错题等页面只有登录才可访问，但没有 `noindex` 响应头 | 若 cookie 被爬虫携带，可能收录隐私页 |
| 6 | `robots.txt` 只屏蔽 `/admin`、`/api/` | `/profile`、`/wrong`、`/import` 等仍可被抓取 |
| 7 | sitemap 用 `request.url_root` 拼域名，无 `lastmod` | 反向代理后可能输出内网地址；无更新信号 |
| 8 | 没有 favicon、没有分享封面 | 搜索结果与分享卡片缺品牌信息 |
| 9 | `SEND_FILE_MAX_AGE_DEFAULT = 0`，无 gzip | 静态资源每次重验证、HTML 未压缩，拖慢 LCP |
| 10 | 页脚只链 3 个入口 | 内链深度不足，34 个认证页离首页较远 |
| 11 | 首页 h1 为「选对认证，刷对题」，不含核心词 | 首屏主题信号弱 |

## 二、路由级收录策略

| 路由 | 状态 | 收录 | canonical | 结构化数据 |
|------|------|------|-----------|-----------|
| `/` | 200 | ✅ | `{base}/` | Organization + WebSite |
| `/cert/<vendor>` | 200 | ✅ | 自身 | BreadcrumbList + CollectionPage |
| `/cert/<vendor>/<dir>` | 200 | ✅ | 自身 | BreadcrumbList + CollectionPage |
| `/cert/<vendor>/<dir>/<level>` | 200 | ✅ | 自身（忽略 `?page=`） | BreadcrumbList + LearningResource |
| `/daily-quiz` | 200 | ✅ | `/daily-quiz` | WebPage |
| `/ranking` | 200 | ✅ | 日榜 `/ranking`；周/月带 `?period=` | WebPage |
| `/date/<date>` | 200 | ❌ noindex,nofollow | — | — |
| `/leaderboard` | 200 | ❌ noindex,nofollow（旧榜页，与 `/ranking` 重复） | — | — |
| `/login`、`/register`、`/forgot-password` | 200 | ❌ noindex,follow | 自身 | — |
| `/reset-password` | 200 | ❌ noindex,nofollow（含一次性 token） | — | — |
| `/profile`、`/quiz/*`、`/result/*`、`/score/*`、`/wrong*` | 302 / 200 | ❌ noindex,nofollow | — | — |
| `/import`、`/admin/*` | 302 / 200 | ❌ noindex,nofollow | — | — |
| `/api/*` | 200 | ❌ noindex,nofollow | — | — |
| `/certification/*`、`/practice`、`/honors` | 301 | — | 目标页 | — |
| `/no-such-page` | 404 | ❌ noindex,follow | 不输出 | — |

`noindex` 同时写在 `<meta name="robots">` 与 `X-Robots-Tag` 响应头里，
避免页面被缓存或非 HTML 响应时漏掉。

## 三、实现要点

### 1. `seo.py`：唯一的 URL 与元信息出口

```python
seo.page(title, description, path, keywords=..., robots=..., jsonld=...)
```

- `absolute()` 优先读 `SITE_BASE_URL`，没有再退回 `request.url_root`。
  反向代理、CDN、HTTPS 场景下都能输出稳定域名。
- `dump()` 序列化 JSON-LD 时把 `</` 转义为 `<\/`，防止提前闭合 `script`。
- `robots_for_path()` 是页面 meta 与响应头的共同规则源，二者不会打架。

### 2. 结构化数据

| 页面 | 节点 | 关键字段 |
|------|------|----------|
| 首页 | `Organization` | `name` / `url` / `logo` |
| 首页 | `WebSite` | `url` / `name` / `inLanguage` / `publisher` |
| 频道页 | `BreadcrumbList` | 逐级 `position` + 绝对 `item` |
| 频道页 | `CollectionPage` | `name` / `description` / `isPartOf` |
| 认证页 | `LearningResource` | `learningResourceType=认证题库`、`numberOfItems=题量`、`educationalLevel=难度`、`about=厂商` |
| 排行榜 | `WebPage` | `name` / `url` / `isPartOf` |

所有节点合成一个 `@graph`，每页只输出一个 `<script type="application/ld+json">`。

### 3. 抓取入口

`robots.txt`：

```
User-agent: *
Allow: /
Disallow: /admin
Disallow: /api/
Disallow: /profile
Disallow: /quiz/
Disallow: /result/
Disallow: /wrong
Disallow: /score/
Disallow: /date/
Disallow: /import
Disallow: /export/
Disallow: /leaderboard
Disallow: /login
Disallow: /register
Disallow: /forgot-password
Disallow: /reset-password

Sitemap: {base}/sitemap.xml
```

`sitemap.xml`：首页 + 每日答题 + 排行榜 + 4 厂商 + 全部方向 + 34 个认证，
`changefreq` 按内容更新频率区分，动态页带 `lastmod`。

### 4. 性能（同时服务 1 核 1G）

- `after_request` 对 `text/html`、`application/json`、`application/xml`、`text/css`、
  `application/javascript` 做 gzip（level 5，>860B 才压），实测认证页 34KB → 7KB。
- CSS/JS 引用带 `?v={static mtime}`，配合 `Cache-Control: max-age=604800`，
  发版后 URL 自动变化，不会看到旧样式。
- 首屏头像固定 `width/height`，避免 CLS；分享封面 1200×630、345KB。

## 四、验证

```bash
python test_seo.py
```

8 组断言：绝对 canonical / OG、公开页唯一 h1 与文案长度、JSON-LD 类型与字段、
noindex 双通道、sitemap 覆盖与 lastmod、robots 规则、404 + gzip、静态缓存头。

浏览器侧可再用：

```bash
curl -s http://127.0.0.1:5000/cert/aliyun/acp/cloud | grep -E "canonical|ld\\+json"
curl -sI http://127.0.0.1:5000/profile | grep -i x-robots
```

## 五、上线检查清单

1. `.env` 设置 `SITE_BASE_URL=https://你的域名`（决定 canonical / sitemap / 邮件链接）。
2. 确认全站 HTTPS，HTTP 301 到 HTTPS（在反向代理层做）。
3. Search Console / 百度站长提交 `https://你的域名/sitemap.xml`。
4. 用富媒体结果测试工具检查首页与一个认证页的 JSON-LD。
5. 后台「认证分类」里可逐条覆盖 SEO 标题 / 关键词 / 描述，保存即生效。
