#!/bin/sh
# NodeSeek 关键词监控 bot —— 一键安装脚本
#
#   sh install.sh                                            交互式，从 GitHub 拉最新代码
#   sh install.sh --token 123456:AA-xxxxxxxx --admin 123456789   全自动
#   sh install.sh --source /root/nodeseek-bot-0.1.0.tar.gz   用离线包（本地目录也行）
#
# 支持 Debian 12 / Ubuntu 22.04 及以上，需要 root。
# 幂等：重复执行不会覆盖已有配置，也不会动数据库。
set -eu

REPO_URL="${REPO_URL:-https://github.com/PaopaoSugar/nodeseek-bot.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/nodeseek-bot}"
CONFIG_DIR="${CONFIG_DIR:-/etc/nodeseek-bot}"
DATA_DIR="${DATA_DIR:-/var/lib/nodeseek-bot}"
SERVICE_USER="${SERVICE_USER:-nodeseek}"
BOT_TOKEN="${BOT_TOKEN:-}"
ADMIN_IDS="${ADMIN_IDS:-}"
SOURCE="${SOURCE:-}"

log() { printf '==> %s\n' "$*"; }
warn() { printf '警告：%s\n' "$*" >&2; }
die() { printf '错误：%s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
用法：sh install.sh [选项]

  --token TOKEN     Telegram bot token（@BotFather 给的），不给就交互式询问
  --admin IDS       管理员 Telegram user id（@userinfobot 查），多个用逗号分隔
  --source PATH     离线安装：本地目录或 .tar.gz 包（默认从 GitHub 克隆）
  --branch NAME     分支名，默认 main
  --dir PATH        安装目录，默认 /opt/nodeseek-bot
  -h, --help        显示这段帮助

等价的环境变量：BOT_TOKEN / ADMIN_IDS / SOURCE / BRANCH / REPO_URL / INSTALL_DIR。
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --token) BOT_TOKEN="${2:-}"; shift 2 ;;
        --admin) ADMIN_IDS="${2:-}"; shift 2 ;;
        --source) SOURCE="${2:-}"; shift 2 ;;
        --branch) BRANCH="${2:-}"; shift 2 ;;
        --dir) INSTALL_DIR="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数：$1（用 -h 看帮助）" ;;
    esac
done

[ "$(id -u)" = "0" ] || die "请用 root 运行：sudo sh install.sh"
command -v apt-get >/dev/null 2>&1 || die "只支持 Debian/Ubuntu 系（找不到 apt-get）"

ask() {
    # ask "<提示语>" 变量名 —— 从 /dev/tty 读，所以 curl | sh 也能用
    local prompt="$1" name="$2" value=""
    if [ -r /dev/tty ]; then
        printf '%s ' "$prompt" > /dev/tty
        read -r value < /dev/tty || value=""
    fi
    eval "$name=\$value"
}

# --- 1. 系统依赖 -------------------------------------------------------------
missing=0
command -v python3 >/dev/null 2>&1 || missing=1
command -v git >/dev/null 2>&1 || missing=1
if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import ensurepip' >/dev/null 2>&1 || missing=1
fi
if [ "$missing" = 1 ]; then
    log "安装系统依赖：python3 python3-venv git"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv git
fi

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "需要 Python 3.11 及以上，当前是 $(python3 -V 2>&1)"

# --- 2. 系统用户 -------------------------------------------------------------
if id "$SERVICE_USER" >/dev/null 2>&1; then
    log "系统用户 $SERVICE_USER 已存在"
else
    log "创建系统用户 $SERVICE_USER"
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# --- 3. 取代码 ---------------------------------------------------------------
if [ -n "$SOURCE" ]; then
    case "$SOURCE" in
        *.tar.gz|*.tgz)
            [ -f "$SOURCE" ] || die "找不到文件：$SOURCE"
            log "从压缩包解包到 $INSTALL_DIR"
            mkdir -p "$INSTALL_DIR"
            tar -xzf "$SOURCE" -C "$INSTALL_DIR" --strip-components=1
            ;;
        *)
            [ -d "$SOURCE" ] || die "找不到目录或压缩包：$SOURCE"
            log "从本地目录复制到 $INSTALL_DIR"
            mkdir -p "$INSTALL_DIR"
            tar -C "$SOURCE" --exclude=.git --exclude=.venv --exclude=.deps \
                --exclude=dist --exclude=__pycache__ -cf - . \
                | tar -C "$INSTALL_DIR" -xf -
            ;;
    esac
elif [ -d "$INSTALL_DIR/.git" ]; then
    log "更新已有仓库 $INSTALL_DIR（分支 $BRANCH）"
    git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard FETCH_HEAD
else
    if [ -e "$INSTALL_DIR" ]; then
        die "$INSTALL_DIR 已存在且不是 git 仓库；请先移走，或用 --source 安装"
    fi
    log "克隆 $REPO_URL（分支 $BRANCH）"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# --- 4. 虚拟环境 -------------------------------------------------------------
log "创建虚拟环境并安装依赖"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet "$INSTALL_DIR"
"$INSTALL_DIR/.venv/bin/nodeseek-bot" --help >/dev/null \
    || die "安装校验失败：nodeseek-bot --help 没有正常输出"

# --- 5. 配置文件 -------------------------------------------------------------
mkdir -p "$CONFIG_DIR"
CONFIG="$CONFIG_DIR/config.toml"
if [ -f "$CONFIG" ]; then
    log "配置已存在，保持原样：$CONFIG"
else
    if [ -z "$BOT_TOKEN" ]; then
        ask "Telegram bot token（@BotFather，形如 123456:AA...）：" BOT_TOKEN
    fi
    [ -n "$BOT_TOKEN" ] || die "必须提供 bot_token"
    if [ -z "$ADMIN_IDS" ]; then
        ask "管理员 Telegram user id（@userinfobot 查，多个用逗号分隔）：" ADMIN_IDS
    fi
    [ -n "$ADMIN_IDS" ] || die "必须提供至少一个管理员 user id"

    log "写入配置 $CONFIG"
    cp "$INSTALL_DIR/config.example.toml" "$CONFIG"
    "$INSTALL_DIR/.venv/bin/python" - "$CONFIG" "$BOT_TOKEN" "$ADMIN_IDS" "$DATA_DIR/bot.db" <<'PY'
import pathlib, re, sys

path, token, admins, db_path = sys.argv[1:5]
try:
    ids = [int(part) for part in re.split(r"[,\s]+", admins) if part]
except ValueError:
    raise SystemExit("管理员 user id 只能是数字，多个用逗号分隔") from None
if not ids:
    raise SystemExit("管理员 user id 不能为空")

text = pathlib.Path(path).read_text(encoding="utf-8")
text = re.sub(r"^bot_token = .*$", 'bot_token = "%s"' % token, text, count=1, flags=re.M)
text = re.sub(
    r"^admin_user_ids = .*$",
    "admin_user_ids = [%s]" % ", ".join(str(i) for i in ids),
    text,
    count=1,
    flags=re.M,
)
text = re.sub(r"^db_path = .*$", 'db_path = "%s"' % db_path, text, count=1, flags=re.M)
pathlib.Path(path).write_text(text, encoding="utf-8", newline="\n")
PY
fi
# 服务以 $SERVICE_USER 运行，配置必须让它读得到
chown root:"$SERVICE_USER" "$CONFIG"
chmod 640 "$CONFIG"

# --- 6. 数据目录 -------------------------------------------------------------
log "准备数据目录 $DATA_DIR"
mkdir -p "$DATA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

# --- 7. systemd --------------------------------------------------------------
log "安装 systemd unit"
UNIT=/etc/systemd/system/nodeseek-bot.service
sed -e "s|^User=.*|User=$SERVICE_USER|" \
    -e "s|^Group=.*|Group=$SERVICE_USER|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=$INSTALL_DIR|" \
    -e "s|^ExecStart=.*|ExecStart=$INSTALL_DIR/.venv/bin/nodeseek-bot --config $CONFIG|" \
    -e "s|^ReadWritePaths=.*|ReadWritePaths=$DATA_DIR|" \
    "$INSTALL_DIR/deploy/nodeseek-bot.service" > "$UNIT"
chmod 644 "$UNIT"
systemctl daemon-reload
systemctl enable --now nodeseek-bot

# --- 8. 把自己变成用户（邀请制的引导）----------------------------------------
admins=$("$INSTALL_DIR/.venv/bin/python" - "$CONFIG" <<'PY'
import pathlib, re, sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r"^admin_user_ids = \[(.*)\]$", text, re.M)
print(" ".join(match.group(1).replace(",", " ").split()) if match else "")
PY
)
for admin in $admins; do
    log "授权管理员 $admin"
    su -s /bin/sh -c "$INSTALL_DIR/.venv/bin/nodeseek-bot --config $CONFIG --grant $admin" \
        "$SERVICE_USER" || warn "授权 $admin 失败，稍后可手动执行 --grant"
done

# --- 9. 收尾 -----------------------------------------------------------------
sleep 2
if systemctl is-active --quiet nodeseek-bot; then
    log "服务已启动"
else
    warn "服务没有跑起来，看这里：journalctl -u nodeseek-bot -n 40 --no-pager"
fi

cat <<EOF

安装完成。
  代码目录   $INSTALL_DIR
  配置文件   $CONFIG
  数据库     $DATA_DIR/bot.db
  查看状态   systemctl status nodeseek-bot --no-pager
  实时日志   journalctl -u nodeseek-bot -f
  更新       sh $INSTALL_DIR/scripts/update.sh

接着在 Telegram 里给 bot 发 /start，再 /add 你的关键词。
EOF
