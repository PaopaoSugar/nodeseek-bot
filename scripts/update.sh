#!/bin/sh
# NodeSeek 关键词监控 bot —— 更新脚本
#
#   sh update.sh              拉最新代码、重装、重启（默认分支 main）
#   sh update.sh --branch dev 换分支
#   sh update.sh --check      只看有没有新版本，不动任何东西
#
# 需要 root。更新前会自动停服务并备份数据库。
set -eu

REPO_URL="${REPO_URL:-https://github.com/PaopaoSugar/nodeseek-bot.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/nodeseek-bot}"
CONFIG_DIR="${CONFIG_DIR:-/etc/nodeseek-bot}"
DATA_DIR="${DATA_DIR:-/var/lib/nodeseek-bot}"
CHECK_ONLY=0

log() { printf '==> %s\n' "$*"; }
warn() { printf '警告：%s\n' "$*" >&2; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --branch) BRANCH="${2:-}"; shift 2 ;;
        --check) CHECK_ONLY=1; shift ;;
        -h|--help)
            sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) die "未知参数：$1（用 -h 看帮助）" ;;
    esac
done
[ "$(id -u)" = "0" ] || die "请用 root 运行：sudo sh update.sh"
[ -d "$INSTALL_DIR" ] || die "找不到 $INSTALL_DIR，先跑 scripts/install.sh"

CONFIG="$CONFIG_DIR/config.toml"
[ -f "$CONFIG" ] || die "找不到配置文件 $CONFIG"

if [ ! -d "$INSTALL_DIR/.git" ]; then
    die "$INSTALL_DIR 不是 git 仓库（当初可能是用 --source 装的）。
请重新执行安装脚本并指定同一个 --source，或改用 git 方式部署。"
fi

log "检查 $BRANCH 分支的更新"
git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
current=$(git -C "$INSTALL_DIR" rev-parse HEAD)
latest=$(git -C "$INSTALL_DIR" rev-parse FETCH_HEAD)

if [ "$current" = "$latest" ]; then
    log "已经是最新：$(git -C "$INSTALL_DIR" log -1 --format='%h %s')"
    if [ "$CHECK_ONLY" = 1 ]; then
        exit 0
    fi
else
    log "当前：$(git -C "$INSTALL_DIR" log -1 --format='%h %s')"
    log "最新：$(git -C "$INSTALL_DIR" log -1 --format='%h %s' FETCH_HEAD)"
    if [ "$CHECK_ONLY" = 1 ]; then
        exit 0
    fi
fi

# 1. 先停服务：WAL 会在干净退出时合并回主库，这样备份出来的才是完整数据
if systemctl is-active --quiet nodeseek-bot; then
    log "停止服务（顺便让数据库落盘）"
    systemctl stop nodeseek-bot
fi

# 2. 备份数据库
if [ -f "$DATA_DIR/bot.db" ]; then
    stamp=$(date +%Y%m%d-%H%M%S)
    mkdir -p "$DATA_DIR/backups"
    cp "$DATA_DIR/bot.db" "$DATA_DIR/backups/bot-$stamp.db"
    log "数据库已备份：$DATA_DIR/backups/bot-$stamp.db"
fi

# 3. 换代码
log "切换到最新提交"
git -C "$INSTALL_DIR" reset --hard FETCH_HEAD

# 4. 重装并重启
log "重新安装"
"$INSTALL_DIR/.venv/bin/pip" install --quiet "$INSTALL_DIR"
"$INSTALL_DIR/.venv/bin/nodeseek-bot" --help >/dev/null || die "新代码装不上，已停在当前版本"
systemctl start nodeseek-bot
sleep 2

if systemctl is-active --quiet nodeseek-bot; then
    log "更新完成，服务已启动"
    journalctl -u nodeseek-bot -n 10 --no-pager || true
else
    warn "服务没起来：journalctl -u nodeseek-bot -n 40 --no-pager"
    warn "要回滚到更新前："
    warn "  cd $INSTALL_DIR && git reset --hard $current"
    warn "  .venv/bin/pip install . && systemctl start nodeseek-bot"
    exit 1
fi