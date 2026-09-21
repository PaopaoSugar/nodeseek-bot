"""Turn incoming Telegram commands into handler results.

Everything here is a pure function over repositories, so the whole command
surface can be tested without a bot, a network, or an event loop.
"""

from __future__ import annotations

from dataclasses import dataclass

from nodeseek_bot.bot.admin_handlers import AdminDeps, handle_admin
from nodeseek_bot.bot.user_handlers import (
    NEED_INVITE_TEXT,
    HandleResult,
    handle_add,
    handle_clear,
    handle_delete,
    handle_help,
    handle_invite,
    handle_list,
    handle_start,
    handle_test,
)
from nodeseek_bot.config import Config, LimitsConfig
from nodeseek_bot.db import Database
from nodeseek_bot.repository import (
    DeliveryRepository,
    InviteRepository,
    ItemRepository,
    MetaRepository,
    RuleRepository,
    UserRepository,
)

UNKNOWN_COMMAND = "🤔 不认识这条指令。\n\n"

PLAIN_TEXT = (
    "直接发消息不会生效。\n\n用 /add 关键词 添加监控词，例如 /add 日本 vps；/help 可以看全部指令。"
)

DELETE_USAGE = "用法：/del 编号，编号见 /list。"


@dataclass(slots=True)
class BotDeps:
    """Everything the command layer needs, wired once at startup."""

    db: Database
    users: UserRepository
    rules: RuleRepository
    items: ItemRepository
    invites: InviteRepository
    deliveries: DeliveryRepository
    meta: MetaRepository
    limits: LimitsConfig
    admins: list[int]

    def admin_deps(self) -> AdminDeps:
        return AdminDeps(
            db=self.db,
            users=self.users,
            rules=self.rules,
            deliveries=self.deliveries,
            items=self.items,
            meta=self.meta,
            admins=self.admins,
        )


def build_bot_deps(db: Database, config: Config) -> BotDeps:
    return BotDeps(
        db=db,
        users=UserRepository(db),
        rules=RuleRepository(db),
        items=ItemRepository(db),
        invites=InviteRepository(db),
        deliveries=DeliveryRepository(db),
        meta=MetaRepository(db),
        limits=config.limits,
        admins=config.telegram.admin_user_ids,
    )


def parse_command(text: str) -> tuple[str, list[str]]:
    """Split ``/add@bot 日本 vps`` into ``("/add", ["日本", "vps"])``."""
    parts = text.strip().split()
    if not parts:
        return "", []
    return parts[0].split("@", 1)[0].lower(), parts[1:]


def route_message(
    deps: BotDeps,
    tg_user_id: int,
    username: str | None,
    text: str,
) -> HandleResult:
    command, args = parse_command(text)

    if command == "/start":
        return handle_start(
            deps.users,
            deps.invites,
            deps.limits,
            tg_user_id,
            username,
            args[0] if args else None,
        )
    if not command.startswith("/"):
        return HandleResult(PLAIN_TEXT)
    if command == "/help":
        return handle_help()

    is_admin = tg_user_id in deps.admins
    if deps.limits.invite_required:
        joined = deps.users.find_by_tg_id(tg_user_id) is not None
        if not joined and not (is_admin and command == "/admin"):
            return HandleResult(NEED_INVITE_TEXT)

    if command == "/add":
        return handle_add(deps.users, deps.rules, deps.limits, tg_user_id, username, " ".join(args))
    if command == "/list":
        return handle_list(deps.users, deps.rules, tg_user_id)
    if command == "/del":
        return _delete(deps, tg_user_id, args)
    if command == "/clear":
        return handle_clear(deps.users, deps.rules, tg_user_id)
    if command == "/test":
        return handle_test(deps.items, tg_user_id, " ".join(args))
    if command == "/invite":
        return handle_invite(deps.users, deps.invites, deps.limits, tg_user_id, username)
    if command == "/admin":
        return handle_admin(deps.admin_deps(), tg_user_id, text)
    return HandleResult(UNKNOWN_COMMAND + handle_help().text)


def _delete(deps: BotDeps, tg_user_id: int, args: list[str]) -> HandleResult:
    if not args or not args[0].isdigit():
        return HandleResult(DELETE_USAGE)
    user_id = deps.users.ensure(tg_user_id=tg_user_id, username=None)
    rows = deps.rules.list_for_user(user_id)
    index = int(args[0])
    if index < 1 or index > len(rows):
        return HandleResult("⚠️ 这条规则不存在，发送 /list 看看编号。")
    return handle_delete(deps.users, deps.rules, tg_user_id, rows[index - 1].id)


def route_callback(deps: BotDeps, tg_user_id: int, data: str) -> HandleResult | None:
    """Handle inline button presses. Returns None when the payload is unknown."""
    if not data.startswith("del:"):
        return None
    raw_id = data.partition(":")[2]
    if not raw_id.isdigit():
        return None
    return handle_delete(deps.users, deps.rules, tg_user_id, int(raw_id))
