# 部署

本文档面向 Debian 12 / Ubuntu 22.04+ 自托管场景，命令可直接复制执行。

**先试一键脚本**：在服务器上以 root 执行
`sh scripts/install.sh`（或 `curl -fsSL https://raw.githubusercontent.com/PaopaoSugar/nodeseek-bot/main/scripts/install.sh | sh`）。
它会做完下面第 1 到第 7 步的全部事情。以后更新用 `sh scripts/update.sh`。
本文档余下的部分就是脚本逐步在做的事，出问题时用来对照排查。

## 参考环境（一台实测机的值，不是硬性要求）

| 项目 | 实测值 |
| --- | --- |
| 系统 | Debian GNU/Linux 12 (bookworm), aarch64 |
| CPU / 内存 | 4 核 / 23 GiB，**无 swap** |
| 磁盘 | 177 GB，已用 6.1 GB |
| Python | **3.11.2，且是唯一版本**（`/usr/bin/python3`） |
| SQLite | 3.40.1（WAL 与 upsert 可用） |
| systemd | 252 |
| 时区 | Asia/Shanghai (+0800) |
| 已安装 | git、curl、pip3、ufw、nft、iptables |
| 未安装 | python3-venv（需补装）、fail2ban、nginx |
| 出网延迟 | Telegram 约 0.53 s，NodeSeek 约 0.23 s |

标准库（`venv` / `sqlite3` / `tomllib` / `unicodedata` / `email.utils` /
`ElementTree`）全部满足，**不需要安装任何其他系统依赖**。

## 0. 先确认机器

    uname -m                 # 期望 aarch64
    python3 -V               # 期望 Python 3.11.2
    cat /etc/debian_version  # 期望 12.x

## 1. 补装 venv 支持

Debian 12 默认不带 `ensurepip`，这是唯一需要补装的包：

    apt update && apt install -y python3-venv

## 2. 建系统用户

    useradd --system --home /opt/nodeseek-bot --shell /usr/sbin/nologin nodeseek

## 3. 取代码

仓库还没推到 GitHub 时，本地打一个包上传（`dist/nodeseek-bot-0.1.0.tar.gz`），
上传到 `/root/` 后：

    tar xzf /root/nodeseek-bot-0.1.0.tar.gz -C /opt

已经推到 GitHub 之后：

    git clone <你的仓库地址> /opt/nodeseek-bot

## 4. 建 venv 并安装

    python3 -m venv /opt/nodeseek-bot/.venv
    /opt/nodeseek-bot/.venv/bin/pip install --upgrade pip
    /opt/nodeseek-bot/.venv/bin/pip install /opt/nodeseek-bot

若报 `ensurepip is not available`，说明第 1 步没生效：重跑第 1 步，
删掉 `.venv` 目录再来一遍。

装完顺手确认两件事（`schema.sql` 是数据文件，必须跟代码一起装进去）：

    /opt/nodeseek-bot/.venv/bin/nodeseek-bot --help
    ls /opt/nodeseek-bot/.venv/lib/python3.11/site-packages/nodeseek_bot/schema.sql

第一条应该打印用法，第二条应该列出文件。少了 `schema.sql` 说明安装不完整，
重新执行 `pip install /opt/nodeseek-bot`。

## 5. 建配置

    mkdir -p /etc/nodeseek-bot
    cp /opt/nodeseek-bot/config.example.toml /etc/nodeseek-bot/config.toml
    nano /etc/nodeseek-bot/config.toml
    chown root:nodeseek /etc/nodeseek-bot/config.toml
    chmod 640 /etc/nodeseek-bot/config.toml

至少要填 `[telegram].bot_token`（从 @BotFather 申请）和
`admin_user_ids`（用 @userinfobot 查自己的 user id）。

`chown`/`chmod` 这两行不能省：unit 里 `User=nodeseek`，配置若留在
`root:root 600`，服务会读不到它，表现就是机器人在 Telegram 里一声不吭。

常见的行为调整都在 `[limits]` 里：

    [limits]
    max_rules_per_user = 3     # 每人最多几个关键词
    invite_required = true     # false = 开放注册，任何人发 /start 就能用
    invite_mode = "one_time"   # "fixed" = 每人一个可反复使用的固定邀请码
    invite_quota_per_user = 3  # 仅 one_time 模式生效

这些只在启动时读一次，改完要 `systemctl restart nodeseek-bot`。邀请码/关键词本身存在 `bot.db` 里，改配置不会动已有数据。

## 6. 建数据目录

    mkdir -p /var/lib/nodeseek-bot
    chown nodeseek:nodeseek /var/lib/nodeseek-bot

并把配置里的路径改成数据目录，与代码分离，便于单独备份：

    [storage]
    db_path = "/var/lib/nodeseek-bot/bot.db"

## 7. 先把自己加进去（邀请制的引导）

这个 Bot 是邀请制的：**没有加入过的用户，除了 `/start` 之外发什么都会被挡掉**。
而 `/invite` 又要求你先是个用户——所以第一次必须从命令行把自己加上：

    sudo -u nodeseek /opt/nodeseek-bot/.venv/bin/nodeseek-bot \
        --config /etc/nodeseek-bot/config.toml --grant <你的 user id>

用 `sudo -u nodeseek` 而不是直接 root 跑：数据目录和 `bot.db` 都属于 `nodeseek`，
以 root 身份操作会把 `bot.db-wal` 之类的文件留成 root 所有，
之后服务就写不进去了。

user id 用 [@userinfobot](https://t.me/userinfobot) 查。看到
`已授权 Telegram 用户 ...` 就成了。它可以在服务运行期间随时执行，不会互相干扰
（SQLite 是 WAL 模式）。

之后所有事情都在 Telegram 里做：

    /start            # 应该看到帮助
    /add 关键词       # 添加监控词
    /list             # 查看 / 点按钮删除
    /test 关键词      # 用最近 24 小时的帖子试一下
    /invite           # 生成一次性邀请码，发给朋友

给朋友发码，朋友那边 `/start <邀请码>` 就能加入。

## 8. 装 unit 并启动

    cp /opt/nodeseek-bot/deploy/nodeseek-bot.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now nodeseek-bot

## 9. 观察

    systemctl status nodeseek-bot
    journalctl -u nodeseek-bot -f

启动后给 Bot 发 `/start` 应该立刻有回应。正常工作时日志里每 30 秒最多一行
`poll ok, N new item(s)`，没有新帖时不打印任何东西。

## 10. 每日清理过期数据

`items` 只保留 14 天，用 cron 每天跑一次维护：

    crontab -e
    # 追加：
    0 4 * * * sudo -u nodeseek /opt/nodeseek-bot/.venv/bin/nodeseek-bot --config /etc/nodeseek-bot/config.toml --maintenance

## 11. 升级

    cd /opt/nodeseek-bot && git pull
    .venv/bin/pip install .
    systemctl restart nodeseek-bot

## 12. 明确不要做的事

- **不要把 `interval_seconds` 调到 20 以下。** NodeSeek 的 RSS 不做 CDN 缓存
  （`cf-cache-status: DYNAMIC`），每次请求都会打到源站。30 秒已经是每天 2,880 次请求。
- **不要改成 webhook 模式。** 本机只有内网地址（Oracle NAT），
  webhook 需要额外的端口、域名与 TLS 证书，收益为零。
- **不要把 `config.toml` 提交进仓库。** 它已经被 `.gitignore` 忽略，请保持这一点。
- **不要在同一台机器上再跑别的抓取脚本。** 限流看的是整个 IP 的请求总量，同机多一个客户端，轮询间隔再保守也会被 429。
  实测过一次：一个没有间隔的探测脚本把整个 IP 打进了 `429`/`503` 交替状态，同机那个 30 秒轮询的 bot 一起被殃及。
  同理，请把这个 IP 当作敏感基础设施：轮询频率是安全垫，不是可以随便花的预算。

## 13. 故障排查

| 现象 | 处理 |
| --- | --- |
| `ensurepip is not available` | 回到第 1 步补装 `python3-venv`，删掉 `.venv` 重建 |
| `invalid TOML in ...` | 报错下面会连出错行的原文和 `^` 一起打出来，`^` 指向的字符就是问题所在；顺手确认第 1 行还是 `#` 开头的注释 |
| `attempt to write a readonly database` | `bot.db` 属于 root（多半是最初以 root 跑过 `--grant`），而服务和它不同用户。`systemctl stop nodeseek-bot && chown -R nodeseek:nodeseek /var/lib/nodeseek-bot && systemctl start nodeseek-bot`。新版本会在启动阶段就报出这条并附上命令 |
| `/start` 完全没反应 | 先 `systemctl status nodeseek-bot --no-pager`。服务没在跑就不会有任何回复；再看 `journalctl -u nodeseek-bot -n 40 --no-pager`。最常见的原因是配置权限：`ls -l /etc/nodeseek-bot/config.toml` 应为 `root:nodeseek 640` |
| 日志里 `409 Conflict` | 同一个 token 有两个实例在抢 `getUpdates`，`ps aux | grep [n]odeseek-bot` 确认只有一个 |
| `config file not found` | 检查 `--config` 路径与 unit 里的 `ExecStart` 是否一致 |
| `bot_token does not look like a bot token` | token 里必须有 `:`，且形如 `123456:AA...` |
| `429 Too Many Requests` | 先确认加速度：`ps aux | grep -E "[s]oak|[n]odeseek|curl"`，同一台机器上多个进程抓同一个 feed 是最常见的原因。确认只有一个 bot 实例后，把 `interval_seconds` 临时调到 60，并等它自己退避（最长 10 分钟一次） |
| 日志出现 `poll failed (3 in a row)` | 检查出网；连续失败 3 次会给管理员发私聊告警 |
| 收不到通知 | 用 `/test 关键词` 看最近 24 小时会不会命中；确认自己已经加入（见第 7 步） |
| 给 Bot 发 `/add` 没反应 | 你还没加入。跑一次 `--grant <你的 user id>`（见第 7 步） |
| 日志里 `getUpdates` 报 409 | 这个 token 还挂着 webhook。程序启动时会自动清掉；若反复出现，检查是不是有两个实例在跑 |
| rss.nodeseek.com 挂掉 | 备用端点 `https://www.nodeseek.com/rss.xml`（来自 feed 里的 `atom:link`） |
