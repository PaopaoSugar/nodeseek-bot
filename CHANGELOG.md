# Changelog

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Fixed

- 配置报错会打印出错行的原文与 `^` 位置；BOM 与非 UTF-8 配置给出明确提示
- 数据目录/`bot.db` 不可写时（例如之前以 root 跑过 `--grant`），启动阶段就报错并给出 `chown` 修复命令，不再拖到推送时才静默失败
- 部署文档第 5 步补上 `chown root:nodeseek` + `chmod 640`：unit 以 `nodeseek` 运行，配置留在 `root:root 600` 会让服务读不到。顺手加了两条排查条目


### Changed

- 429 现在尊重 `Retry-After`（数值或 HTTP 日期都认）；无该头时，裸 429 的退避下限为 5 分钟，避免在被限流时继续轻微反复试探
- 限流告警会提示检查同机其他抓取进程

### Added

- `limits.invite_required`：关掉邀请制，任何人发 `/start` 即可使用
- `limits.invite_mode`：`fixed` 时每人一个可反复使用的固定邀请码（`InviteRepository.fixed`）

### Added

- `scripts/install.sh`：一键安装（系统依赖、系统用户、venv、配置、systemd、授权管理员），可重复执行
- `scripts/update.sh`：一键更新（停服务、备份数据库、拉代码、重装、重启），支持 `--check` 与 `--branch`
- `.gitattributes`：shell 脚本与 systemd unit 强制 LF
## [0.1.0] - 2026-09-22

### Added

- 监控 NodeSeek 新主题并按关键词匹配推送
- 每用户最多 3 个关键词，空格分隔表示 AND
- 同一用户的多条命中自动合并为一条消息
- 全局限速、429 退避、发送失败重试
- 邀请制注册与一次性邀请码
- 管理员指令：stats / top / users / ban / unban / health
- 轮询失败退避与窗口溢出告警
