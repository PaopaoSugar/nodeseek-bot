# NodeSeek 关键词监控 Telegram Bot — 设计文档

- 日期：2026-09-22
- 状态：待评审
- 范围：NodeSeek 单站新主题关键词监控

## 1. 目标

用户在 NodeSeek 有人发布新主题时，凡是订阅了匹配关键词的用户，都能尽快收到 Telegram
私聊通知。这是一个**自己运营的小众工具**，邀请制，不做公开增长。

成功标准：

- 一个用户在 NodeSeek 发新帖后，命中关键词的订阅者在几十秒内收到通知。
- 1000 名订阅用户同时在线时，投递不被 Telegram 限流打断。
- 数据源侧不因高频轮询被限流或封禁。

## 2. 非目标（明确不做）

以下都是经过讨论后**主动排除**的，不要顺手实现：

- 不监控 linux.do。该站 RSS 被 Cloudflare Managed Challenge 拦截
  （实测 4 种 TLS 指纹、3 个路径全部 403 `cf-mitigated: challenge`），
  已放弃。
- 不监控回复/评论，只监控新主题。
- 不做繁体/简体转换，只做简体。
- 不做网页后台。管理界面就是 Bot 指令。
- 不做静音时段。
- 不匹配正文，v1 只匹配标题。
- 不做全文检索/倒排索引，规模不需要。

## 3. 数据源

### 3.1 端点

    GET https://rss.nodeseek.com/

请求要求：

- 浏览器级 `User-Agent`，不要用默认的 python-requests / curl UA。
- `Accept: application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8`
- 不带 cookie、不带认证。
- 单源串行，任何时刻只允许一个在途请求。

### 3.2 实测结论

| 项目 | 实测值 |
| --- | --- |
| HTTP 状态 | 200 稳定，30 秒轮询未触发限流 |
| CDN 缓存 | `cf-cache-status: DYNAMIC`，**CF 不缓存，每次都打源站** |
| 条件请求 | 无 `ETag`、无 `Last-Modified`、无 `Cache-Control`，**无法用 304 省流量** |
| 单条响应体积 | 约 9~10 KB |
| 窗口大小 | **20 条** |
| 刷新机制 | 按请求实时生成（`lastBuildDate` 与最新帖 `pubDate` 几乎同秒） |

### 3.3 item 结构

    <item>
      <title><![CDATA[抽个美国家宽Nat小鸡]]></title>
      <description><![CDATA[想买个号来绑定gpt……]]></description>   <!-- 可选 -->
      <link>https://www.nodeseek.com/post-941369-1</link>
      <guid isPermaLink="false">941369</guid>
      <category><![CDATA[daily]]></category>
      <dc:creator><![CDATA[7li7li]]></dc:creator>
      <pubDate>Mon, 21 Sep 2026 17:19:26 GMT</pubDate>
    </item>

解析时**必须注意**：

- `guid` 是纯数字帖子 ID，直接用作去重主键。
- **`description` 可选**。实测 20 条里有 3 条没有。解析器必须容错，
  缺字段时置空，绝不抛异常。
- `category` 和 `dc:creator` 每条都有。v1 不用，但**要存进库**，
  因为「按板块订阅」和「关注作者」是后面几乎免费的扩展。
- `dc:creator` 解析时带命名空间
  （`{http://purl.org/dc/elements/1.1/}creator`）。
- 整个 XML 挤在一行，日志和调试时不要按行切分。

### 3.4 延迟预算

端到端延迟 = feed 新鲜度 + 轮询平均等待（15 秒）+ 解析匹配（< 1 秒）+ TG 投递（1~2 秒）。

feed 新鲜度下限尚在实测中。这个数字决定了我们能不能宣称「30 秒内」。

## 4. 架构

单进程 asyncio，四个互不阻塞的 task，用内存队列和数据库解耦。

    ┌──────────┐   新 item    ┌──────────┐   deliveries   ┌──────────┐
    │  poller  │ ──────────▶ │ matcher  │ ─────────────▶ │ notifier │
    └──────────┘             └──────────┘                └──────────┘
          │                        │                            │
          └────────────┬───────────┴────────────────────────────┘
                       ▼
                  SQLite (WAL)
                       ▲
                       │
                  ┌──────────┐
                  │   bot    │  aiogram，处理用户与管理员指令
                  └──────────┘

**为什么是单进程**：1000 用户规模下，多进程引入的 IPC、SQLite 写锁竞争、
部署复杂度，收益远小于成本。四个 task 隔离已经足够避免互相阻塞。

**为什么不用 Redis / 消息队列**：数据库表本身就是队列。
`deliveries` 表的状态机提供了持久化和崩溃恢复，
引入外部中间件只是多一个会挂的东西。

### 4.1 组件职责

- **poller** — 定时拉 RSS，解析，归一化，`INSERT OR IGNORE` 写 `items`，
  把新 item 推进内存队列，维护轮询健康状态。
- **matcher** — 从队列取新 item，对所有启用规则做匹配，
  命中的写入 `deliveries`（靠唯一约束防重）。
- **notifier** — 扫描待发投递，按用户聚合与限速，发送，更新状态。
- **bot** — aiogram，处理用户指令、邀请、管理员指令、告警推送。

### 4.2 数据流

1. poller 每 30 秒拉一次 feed。
2. 对每条 item，按 `guid` 尝试插入。插入成功 = 新帖，推进队列；
   已存在 = 跳过。
3. matcher 对每条新帖，遍历所有用户的启用规则做匹配。
4. 命中则插入 `deliveries(item_guid, rule_id, user_id, status='pending')`。
5. notifier 发现某用户有 pending，聚合后经令牌桶发送。
6. 发送成功更新为 `sent`，失败按退避重试，超次数置 `failed` 并告警。

## 5. 数据模型

    PRAGMA journal_mode = WAL;
    PRAGMA foreign_keys = ON;

    CREATE TABLE users (
        id             INTEGER PRIMARY KEY,
        tg_user_id     INTEGER NOT NULL UNIQUE,
        username       TEXT,
        created_at     TEXT NOT NULL,
        is_banned      INTEGER NOT NULL DEFAULT 0,
        invited_by     INTEGER REFERENCES users(id),
        notify_enabled INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE invites (
        code       TEXT PRIMARY KEY,
        created_by INTEGER NOT NULL REFERENCES users(id),
        max_uses   INTEGER NOT NULL DEFAULT 1,
        used_count INTEGER NOT NULL DEFAULT 0,
        expires_at TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE rules (
        id           INTEGER PRIMARY KEY,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        keyword_raw  TEXT NOT NULL,
        keyword_norm TEXT NOT NULL,
        enabled      INTEGER NOT NULL DEFAULT 1,
        created_at   TEXT NOT NULL
    );
    CREATE INDEX idx_rules_user ON rules(user_id);

    CREATE TABLE items (
        guid         TEXT PRIMARY KEY,
        title        TEXT NOT NULL,
        title_norm   TEXT NOT NULL,
        link         TEXT NOT NULL,
        author       TEXT,
        category     TEXT,
        published_at TEXT NOT NULL,
        fetched_at   TEXT NOT NULL,
        excerpt      TEXT
    );
    CREATE INDEX idx_items_published ON items(published_at);

    CREATE TABLE deliveries (
        id         INTEGER PRIMARY KEY,
        item_guid  TEXT NOT NULL REFERENCES items(guid) ON DELETE CASCADE,
        rule_id    INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        status     TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        sent_at    TEXT,
        attempts   INTEGER NOT NULL DEFAULT 0,
        error      TEXT,
        UNIQUE(item_guid, rule_id)
    );
    CREATE INDEX idx_deliveries_pending ON deliveries(status, user_id);

    CREATE TABLE stats_daily (
        day   TEXT NOT NULL,
        key   TEXT NOT NULL,
        value INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, key)
    );

    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

说明：

- `items` 以 `guid` 为主键，去重完全交给数据库，不需要额外判重逻辑。
- `deliveries` 的 `UNIQUE(item_guid, rule_id)` 保证进程重启、重放都不会重复推送。
- `items` 保留 14 天后清理，`deliveries` 随级联删除。
  需要长期保留的统计进 `stats_daily`。
- 所有时间戳用 ISO 8601 UTC 字符串存储，展示时再换时区。

## 6. 匹配引擎

### 6.1 归一化

    def normalize(text: str) -> str:
        text = unicodedata.normalize("NFKC", text)   # 全角转半角
        return text.lower()

NFKC 同时处理全角/半角、兼容字符。不做简繁转换。

### 6.2 规则语义

- 每个用户最多 **3 条规则**（可配置）。
- 单条规则内用空格分隔多个词，表示 **AND**。
  例：规则「日本 VPS」命中标题「有日本三网优化大宽带吗？不要搬瓦工」为假，
  命中「日本 VPS 补货了」为真。
- 匹配方式是**纯子串包含**，不是分词、不是正则。
- 规则存两份：`keyword_raw`（用户原始输入，用于展示）
  和 `keyword_norm`（归一化后，用于匹配）。

    def match(title_norm: str, keyword_norm: str) -> bool:
        return all(term in title_norm for term in keyword_norm.split())

### 6.3 性能

1000 用户 × 3 规则 = 3000 次子串比较，每条新帖。每 30 秒只有个位数新帖。
单核轻松，**不要提前上 Aho-Corasick**，留个接口即可。

### 6.4 关键词测试

`/test 关键词` 查最近 24 小时的 `items`，返回会命中的条数和 3 个标题样例。
这个功能能消掉绝大部分「怎么没通知我」和「怎么全是垃圾」的反馈，
优先级高于任何优化。

规则变更**不补推历史**，只影响之后的新帖。

## 7. 通知与限速

这是 1000 用户规模下唯一真正的压力点。

### 7.1 Telegram 约束

- 全局上限约 30 条/秒。
- 单聊约 1 条/秒。
- 单条消息上限 4096 字符。

### 7.2 每用户聚合发送

notifier 为每个用户维护一个发送状态机：

- **空闲 + 有 pending** → 立即取出该用户**全部** pending，合并成一条发送。
  单条命中的场景**零额外延迟**。
- 发送后进入**合并窗口**（默认 20 秒）。窗口内到达的新命中累积不发。
- 窗口结束：有累积则合并成一条发送后重置窗口；无累积则回到空闲。

效果：

- 只有 1 条命中 → 立刻收到，不等待。
- 同一瞬间 5 条命中 → 合并成 1 条，而不是 5 条。
- 某关键词持续爆量 → 最多每 20 秒收到 1 条汇总。

**为什么必须做**：若某热门词瞬间命中 200 个用户，不合并就是 200 条消息，
按 30 条/秒要排 6~7 秒，期间**所有其他用户的通知都被堵在后面**。
合并既是防刷屏，也是保整体吞吐。

### 7.3 全局限速

- 令牌桶，默认 25 条/秒（留出余量）。
- 超长合并消息按 3500 字符分批，仍逐条占用令牌。

### 7.4 错误处理

- 收到 429：读 `retry_after`，挂起该用户队列并退避，投递退回 pending。
- 单条投递失败重试上限 3 次，超限置 `failed` 并计入告警。
- 用户拉黑 Bot / 已注销：标记 `notify_enabled = 0`，不再投递，不计入失败率。

### 7.5 消息格式

    🔍 命中「VPS」的新帖 3 条

    1. 法国Anthony大内存VPS有货 2v8G 3,49€/m 起
       https://www.nodeseek.com/post-941353-1
    2. 有日本三网优化大宽带吗？不要搬瓦工
       https://www.nodeseek.com/post-941358-1

- HTML 模式，**必须** `disable_web_page_preview=True`，否则每个链接都展开预览。
- 标题中的 HTML 特殊字符要转义。
- 附 inline 按钮：`不再提示此关键词` / `屏蔽此帖`。

## 8. 用户交互

- `/start` — 欢迎与引导；带参数时按邀请码激活。
- `/add <关键词>` — 添加规则，超过 3 条时提示删除。
- `/list` — 列出规则与编号。
- `/del <编号>` / `/clear`
- `/test <关键词>` — 预览最近 24 小时命中情况。
- `/invite` — 生成邀请码。
- `/help`

增删词优先用 inline keyboard 按钮，比让用户手打指令体验好得多。

## 9. 邀请机制

- 邀请制，不开放注册。
- 每个用户配额 3 个一次性邀请码。
- 新用户 `/start <code>` 激活，记录 `invited_by`。
- 邀请链可溯源：出滥用时按链连坐封禁。

成熟后如果要做公开放号，再考虑 Bot 内「申请」按钮 + 人工审核，
**不要写网页前端**。

## 10. 管理员

通过 `admin_user_ids` 白名单控制，全部走 Bot 指令，不做网页后台：

- `/admin stats` — 用户数、今日活跃、24h 命中/发送、队列积压、轮询成功率
- `/admin top` — 关键词订阅排行（「某个关键词有多少人在监控」）
- `/admin users` — 用户列表，带邀请链
- `/admin ban <tg_user_id>` — 连同邀请链封禁
- `/admin invite [n]` — 手动发码
- `/admin health` — 最后一次成功轮询时间、响应耗时、近 24h 异常计数

「某个关键词有多少人监控」本质就是：

    SELECT keyword_norm, COUNT(*) FROM rules
    WHERE enabled = 1 GROUP BY keyword_norm ORDER BY 2 DESC LIMIT 20;

## 11. 可靠性与告警

### 11.1 poller 健康

- 连续失败则指数退避：间隔 × 2^n，上限 10 分钟；成功后立即恢复。
- 请求超时 20 秒，单源串行。

### 11.2 窗口溢出检测

feed 窗口只有 20 条。若单次拉取的新增条数 ≥ `overflow_threshold`
（默认 15），说明发布速度可能超过轮询速度，**存在丢帖风险**，立即告警。
这个数字同时要记进日志，用于将来决定是否降低轮询间隔。

### 11.3 告警清单（私聊推给管理员）

- 轮询连续失败 ≥ 3 次
- 单次新增条数 ≥ 阈值（可能丢帖）
- 近 24 小时出现 429
- pending 投递积压超过阈值
- 进程异常退出（systemd `Restart=always` 兜底 + 启动时上报）

注意：**不要**用「最新帖延迟过大」做告警。凌晨没人发帖时这个值会自然增长，
会产生大量误报（这一点已被实测数据证实）。

## 12. 配置

    [telegram]
    bot_token       = ""            # 必填
    admin_user_ids  = []

    [poller]
    interval_seconds        = 30
    request_timeout_seconds = 20
    max_backoff_seconds     = 600
    overflow_threshold      = 15

    [notifier]
    global_rate_per_second = 25
    max_attempts           = 3
    coalesce_window_seconds = 20
    max_message_chars      = 3500

    [limits]
    max_rules_per_user       = 3
    invite_quota_per_user    = 3

    [storage]
    db_path        = "/var/lib/nodeseek-bot/bot.db"
    retention_days = 14

配置文件含 Bot token，权限设 600。

## 13. 项目结构

    nodeseek-bot/
      pyproject.toml
      config.example.toml
      deploy/
        nodeseek-bot.service
      src/nodeseek_bot/
        __init__.py
        config.py
        db.py
        schema.sql
        normalize.py
        matcher.py
        poller.py
        notifier.py
        alerts.py
        runtime.py
        sources/
          __init__.py        # Source 协议 + RawItem
          nodeseek.py        # NodeSeek 实现
        bot/
          __init__.py
          keyboards.py
          user_handlers.py
          admin_handlers.py
      tests/
        fixtures/nodeseek_sample.xml
        test_normalize.py
        test_matcher.py
        test_parser.py
        test_coalesce.py

数据源做成可插拔的 `Source` 协议（`fetch() -> list[RawItem]`）。
当前只有 NodeSeek 一个实现。这样将来加别的站只需要新增一个文件，
不必改动主链路。成本几乎为零，值得保留。

## 14. 技术选型

- Python 3.11（服务器现状）
- aiogram 3.x — 异步、成熟、限流异常类型清晰
- httpx — 异步 HTTP
- RSS 解析用标准库 `xml.etree.ElementTree`，不引 feedparser。
  结构固定且简单，自写解析器能精确容错 `description` 缺失。
- SQLite（标准库 `sqlite3`），WAL 模式
- 无 Redis、无 Postgres、无容器编排、无 Web 框架

## 15. 部署

- 代码放 `/opt/nodeseek-bot/`，venv 隔离。
- systemd unit，`Restart=always`，日志走 journald。
- 数据库放 `/var/lib/nodeseek-bot/`，与代码分离，便于备份。
- 备用端点：feed 的 `atom:link` 指向 `https://www.nodeseek.com/rss.xml`，
  若 rss.nodeseek.com 故障可临时切换验证。

### 15.1 已核对的运行环境（实测）

| 项目 | 实测值 |
| --- | --- |
| 系统 | Debian GNU/Linux 12 (bookworm), aarch64 |
| 内核 | 7.1.0-joeyblog-bbrv3 |
| CPU / 内存 | 4 核 / 23 GiB，**无 swap** |
| 磁盘 | 177 GB，已用 6.1 GB |
| Python | **3.11.2，且是唯一版本**（/usr/bin/python3） |
| SQLite | 3.40.1（WAL 与 upsert 均可用） |
| systemd | 252 |
| 时区 | Asia/Shanghai (+0800) |
| 已安装 | git、curl、pip3、ufw、nft、iptables、Docker（无运行容器） |
| 未安装 | fail2ban、nginx |
| 出网延迟 | Telegram 约 0.53 s，NodeSeek 约 0.23 s |
| 标准库 | venv / sqlite3 / tomllib / unicodedata / email.utils / ElementTree 全部可用 |

由此推导出的**硬约束**：

1. **选择 long polling，不用 webhook。** 网卡只有内网地址（如 Oracle 免费实例的 NAT），对外暴露入站端口还需要域名和 TLS 证书。
   long polling 两样都不需要，代价只是一点位置精度。
2. **无需安装任何 Python 系统依赖**，标准库已满足全部需求。
   仅需补装 python3-venv（Debian 12 默认不带 ensurepip）。
3. **把服务器 IP 当作敏感基础设施。** 轮询频率不是可以随便调的参数：
   IP 一旦被目标站限流或标记，影响的不只是本项目，当自己也不能立刻恢复。
   README 与部署文档都必须写明这一点。
4. Python 3.11.2 是本项目的下限版本，代码不得使用 3.12 才有的语法。

### 15.2 源站限流的实测修正

上线前的一次压力实测中，短时间内的连续请求超过 18,000 次，**全程返回 200**，据此曾判断源站容忍度有很大余量。
**这个结论是错的。** 上线后不久，正常的 30 秒轮询就收到了 `429 Too Many Requests`。

原因是同一台机器上当时还有另一个进程在抓同一个 feed：
限流看的是**整个 IP 的请求总量**，不是单个客户端的行为。
这条结论直接影响了实现：

- 只有一个抓取进程时，30 秒（每天 2,880 次）确实留有余量，但余量是安全垫，不是预算。
- 收到 429 必须按服务端给的 `Retry-After` 退避，而不是按自己的曲线硬撞（`poller.py` 已实现）。
- 限流期间 feed 只有 20 条窗口，持续十几分钟就会真的丢帖，所以它只能当异常处理。

## 16. 测试策略

- **单元测试**
  - 归一化：全角、大小写、混合中英
  - 匹配：AND 语义、子串命中、空规则、特殊字符
  - 解析：完整 item、缺 `description` 的 item、CDATA 转义、命名空间 `dc:creator`
  - 溢出检测阈值
  - 合并分批：超过 `max_message_chars` 时正确切分
- **集成测试**
  - poller 用本地 fixture XML 驱动，不打真实站点
  - 用假 Telegram API 验证限速、429 退避、聚合行为
- **手工验证**
  - `/test` 命令对真实数据的召回情况
  - 真实 Telegram 环境下的合并效果

## 17. 开源与仓库规范

本项目计划开源，因此以下约束**反向影响实现**，不是事后补的文档工作。

### 18.1 自托管友好

- 任何人都应能 clone 后用**自己的 Bot token** 跑起来。配置全部外置，
  不允许硬编码 token、管理员 ID、文件路径。
- 默认值必须**对 NodeSeek 礼貌**。项目被 fork 出去几十份就是几十倍的请求量，
  所以轮询间隔、串行限制、User-Agent 都放进配置，并在注释里说明取舍理由。
- 测试与 CI **绝不访问真实站点**，全部使用本地 fixture。

### 18.2 许可证

默认 **MIT**。如果希望衍生作品也必须开源（例如防止有人闭源后拿去做收费服务），
改用 **AGPL-3.0**。这是本项目唯一需要拍板的法律性决定。

### 18.3 仓库结构

    nodeseek-bot/
      .github/
        workflows/ci.yml
        ISSUE_TEMPLATE/bug_report.yml
        ISSUE_TEMPLATE/feature_request.yml
        PULL_REQUEST_TEMPLATE.md
      deploy/
        nodeseek-bot.service
      docs/
        deployment.md
        superpowers/specs/
        superpowers/plans/
      src/nodeseek_bot/
      tests/
      .editorconfig
      .gitignore
      .python-version
      CHANGELOG.md
      CONTRIBUTING.md
      LICENSE
      README.md
      README.en.md
      SECURITY.md
      config.example.toml
      pyproject.toml

设计文档与实施计划一并入库（`docs/superpowers/`）。开源项目的设计过程公开是加分项。

### 18.4 密钥与隐私

- `config.example.toml` 入库，`config.toml` 进 `.gitignore`，**永不提交**。
- 数据库文件、日志、`data/` 目录一并忽略。
- `SECURITY.md` 说明漏洞上报渠道与 Bot token 泄露的处置流程。
- 测试 fixture 只用公开的 RSS 样本，不含任何用户数据。

### 18.5 代码约定

- 标识符、注释、docstring 用**英文**，便于国际贡献者阅读。
- 面向用户的消息文本、README、文档用**中文**。
- 格式化与 lint 统一用 ruff，行宽 100。
- 全量类型注解。
- 提交信息遵循 Conventional Commits（feat / fix / docs / refactor / test / chore）。
- 语义化版本，从 0.1.0 开始。

### 18.6 CI

`.github/workflows/ci.yml` 在 push 与 PR 上运行，Python 3.11 与 3.12 矩阵：

- `ruff check` 与 `ruff format --check`
- `pytest`

CI 不需要任何密钥，因为所有外部依赖都有测试替身。

### 18.7 文档

- `README.md` 中文为主并含英文简介段；`README.en.md` 为完整英文版，互相链接。
- README 必须包含：一句话简介、功能、工作原理、快速开始（BotFather 建 bot →
  配置 → 运行）、配置项说明、部署、常见问题、免责声明、许可证。
- **免责声明**必须写清：只读取公开 RSS、与 NodeSeek 官方无关、
  内容版权归原作者、提醒自托管者保持礼貌的轮询频率。
- `docs/deployment.md` 给出 systemd 完整步骤。
- `CHANGELOG.md` 采用 Keep a Changelog 格式。
- `CONTRIBUTING.md` 给出开发环境、跑测试、提交规范。

### 18.8 暂不做

Docker 镜像、PyPI 发布、多语言界面、Web 后台。仓库里保留位置，v1 不实现。
## 18. 开放问题

1. **feed 新鲜度下限未知** — 实测中，feed 里最新一条的延迟在 40 秒到 7 分钟之间波动，
   这是 feed 自身的刷新间隔，不是本项目引入的延迟。
2. **30 秒轮询的限流风险** — 已在生产环境碰到 429，见第 15.2 节；已按 `Retry-After` 退避。
3. **NodeSeek 是否存在更大窗口的 feed** — 如分类 feed 或分页，
   若溢出风险高可作为补充源。
4. Bot token、服务器部署路径等交付细节待定。
