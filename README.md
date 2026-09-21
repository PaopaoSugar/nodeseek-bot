# NodeSeek 关键词监控 Bot

监控 [NodeSeek](https://www.nodeseek.com/) 的新主题，命中你设置的关键词时，
通过 Telegram 私聊把标题和链接推给你。

[![CI](https://github.com/PaopaoSugar/nodeseek-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/PaopaoSugar/nodeseek-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English](README.en.md) | 简体中文

## 功能

- **只监控新主题**，不监控回复。命中就推，不命中就安静。
- 每人最多 **3 个关键词**（可配置）。纯标题子串匹配，不做分词、不做正则。
- 一条规则里用**空格分隔**多个词表示 **AND**，例如 `日本 vps`
  只会命中同时包含「日本」和「vps」的标题。
- 全角/半角与大小写自动归一化，`ＶＰＳ` 和 `vps` 是同一条规则。
- 同一用户短时间内的多条命中**自动合并成一条消息**，不会刷屏。
- **邀请制**（可关闭）：`/invite` 生成一次性邀请码，别人用 `/start <code>` 加入；
  也可以改成每人一个固定邀请码，或直接开放注册。
- 管理员指令：`stats` / `top` / `users` / `ban` / `unban` / `health`。
- 单进程、只有两个运行期依赖（aiogram + httpx），SQLite 存全部状态。
- 长时间运行**不需要**公开任何入站端口（long polling）。

## 工作原理

    ┌──────────┐   新帖    ┌──────────┐  deliveries  ┌──────────┐
    │  poller  │ ────────▶ │ matcher  │ ───────────▶ │ notifier │
    └──────────┘           └──────────┘              └──────────┘
          │                     │                          │
          └───────────┬─────────┴──────────────────────────┘
                      ▼
                 SQLite (WAL)
                      ▲
                      │
                 ┌──────────┐
                 │   bot    │  aiogram，处理用户与管理员指令
                 └──────────┘

- 每 30 秒拉一次 NodeSeek 的公开 RSS（约 20 条窗口），`INSERT OR IGNORE` 入库去重，
  只有真正的新帖会进入匹配。
- 匹配就是子串比较：1000 用户 × 3 规则 = 3000 次比较，每条新帖不到 1 毫秒。
- 命中的结果写进 `deliveries` 表再发送，进程重启、重复抓取都不会重复推送。
- **只读取公开 RSS，不使用任何账号、cookie 或凭据。**

## 部署到服务器（一键脚本）

Debian 12 / Ubuntu 22.04 及以上，用 root 执行：

    curl -fsSL https://raw.githubusercontent.com/PaopaoSugar/nodeseek-bot/main/scripts/install.sh | sh

脚本会：装系统依赖 → 建 `nodeseek` 系统用户 → 拉代码 → 建虚拟环境 →
生成 `/etc/nodeseek-bot/config.toml`（会提示你填 token 和管理员 id）→
建数据目录 → 装 systemd unit 并启动 → 把管理员加进用户表。
重复执行是安全的：已有配置和数据库都不会被动。

之后更新只要一行：

    sh /opt/nodeseek-bot/scripts/update.sh

它会先停服务、备份数据库，再拉最新代码、重装、重启；
`--check` 只查看有没有新版本，`--branch` 可以切分支。

脚本以外的细节（目录布局、故障排查、升级、卸载）见 [docs/deployment.md](docs/deployment.md)。

## 手动运行（本地或开发环境）

1. 找 [@BotFather](https://t.me/BotFather) 建一个 bot，拿到 token。
2. 找 [@userinfobot](https://t.me/userinfobot) 查自己的 Telegram user id。
3. 装好并配置：

       python3 -m venv .venv
       .venv/bin/pip install .
       cp config.example.toml config.toml
       nano config.toml      # 填 bot_token 和 admin_user_ids

4. 把自己加进去（邀请制，第一次必须从命令行来）：

       .venv/bin/nodeseek-bot --config config.toml --grant <你的 user id>

5. 运行：

       .venv/bin/nodeseek-bot --config config.toml

6. 在 Telegram 里给 bot 发 `/start`，然后：

       /add 日本 vps      # 添加关键词
       /list              # 查看（点按钮即可删除）
       /test 日本 vps     # 用最近 24 小时的帖子试一下会命中几条
       /invite            # 生成邀请码发给朋友

长期运行请用 systemd，见 [部署文档](docs/deployment.md)。

命令行参数：`--config PATH`、`--maintenance`（清理过期数据）、`--grant TG_USER_ID`（把自己或某人直接加入）、`--log-level`。

## 配置

所有配置项都在 [`config.example.toml`](config.example.toml) 里，逐项对应如下：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `telegram.bot_token` | 无 | **必填**，BotFather 给的 token |
| `telegram.admin_user_ids` | `[]` | 接收运维告警的管理员 user id，可多个 |
| `poller.interval_seconds` | `30` | 轮询间隔。**不要低于 20** |
| `poller.request_timeout_seconds` | `20` | 单次请求超时 |
| `poller.max_backoff_seconds` | `600` | 连续失败时的退避上限 |
| `poller.overflow_threshold` | `15` | 单次新增超过该值即告警（feed 窗口只有 20 条） |
| `poller.feed_url` | `https://rss.nodeseek.com/` | 数据源 |
| `notifier.global_rate_per_second` | `25` | 全局限速，Telegram 上限约 30/秒 |
| `notifier.max_attempts` | `3` | 发送失败重试次数 |
| `notifier.coalesce_window_seconds` | `20` | 同一用户多条命中的合并窗口 |
| `notifier.max_message_chars` | `3500` | 单条消息长度上限，超出会拆成多条 |
| `limits.max_rules_per_user` | `3` | 每人最多几个关键词 |
| `limits.invite_required` | `true` | `false` = 开放注册，任何人发 `/start` 即可使用 |
| `limits.invite_mode` | `one_time` | `one_time` 一次性码 / `fixed` 每人一个可反复使用的固定码 |
| `limits.invite_quota_per_user` | `3` | 仅 `one_time` 模式：每人最多同时持有几个未使用的码 |
| `storage.db_path` | `data/bot.db` | SQLite 路径，部署时建议放到 `/var/lib/` |
| `storage.retention_days` | `14` | 帖子保留天数，由 `--maintenance` 清理 |

**为什么默认是 30 秒**：NodeSeek 的 RSS 走 Cloudflare 但
`cf-cache-status: DYNAMIC`，也就是**不缓存，每次请求都打到源站**。
30 秒已经是每天 2,880 次请求，再快没有意义，只会给源站添麻烦。
另外这个 feed 没有 `ETag` / `Last-Modified`，也无法用条件请求省流量。

## 部署

一句话：Debian 12 上 `apt install python3-venv` → venv 安装 → systemd unit →
cron 每天跑一次 `--maintenance`。完整命令见 [docs/deployment.md](docs/deployment.md)。

## 常见问题

**延迟大概是多少？**
端到端延迟 = feed 自身的新鲜度 + 轮询平均等待（15 秒）+ 匹配（< 1 秒）+ 投递（1~2 秒）。
feed 的新鲜度不由我们决定：实测最快的一次，是新帖**已经发出 11.9 秒**才出现在 RSS 里。
所以最好情况在 **25~30 秒**，和「30 秒」的目标同量级。
但这是观察到的下限而非承诺——源站限流退避、feed 抖动、TG 投递都会让它更长。

**为什么只支持 NodeSeek？**
linux.do 的 RSS 在 Cloudflare Managed Challenge 后面，实测 4 种 TLS 指纹 ×
3 个路径（`/latest.rss`、`/latest.json`、首页）全部返回 `403 cf-mitigated: challenge`。
用浏览器指纹强行绕过属于对抗行为，也随时会失效，所以这里直接放弃。
`Source` 协议是开放的，想接别的站点只需要再实现一个解析器。

**为什么只匹配标题，不做全文匹配？**
RSS 的 `description` 是**可选字段**（实测 20 条里有 3 条没有），
而且经常只是「RT」「顶」这类噪声。全文匹配会带来大量误报，
让用户最后干脆把 bot 静音。标题够用。

**收不到通知怎么办？**
先发 `/test 你的关键词`，它会告诉你最近 24 小时会不会命中：
不命中说明是你关键词太窄，命中却收不到才是 bug。
另外确认自己没被 `/admin ban`，并且规则没有超过上限（`/list` 一眼可见）。

**会把我 ban 吗？我该做点什么？**
保持默认的 30 秒即可。不要部署多份、不要把间隔调到 20 秒以下，
也不要在这台机器上同时跑别的高频出网脚本。

## 免责声明

- 本项目只读取 NodeSeek **公开的 RSS**，不使用任何账号凭据、cookie 或登录态。
- 本项目与 NodeSeek 官方**没有任何关系**，也未获得其背书。
- 帖子内容版权归原发布者所有；本项目只转发**标题**与**原始链接**，不做全文转载。
- 请保持礼貌的轮询频率。默认值已按此设定，**不建议调低**。
- 使用者需自行遵守目标站点的服务条款，并自行承担使用风险。

## 许可证

[MIT](LICENSE)
