"""Admin command handlers."""

from __future__ import annotations

from dataclasses import dataclass

from nodeseek_bot.bot.user_handlers import HandleResult
from nodeseek_bot.db import Database
from nodeseek_bot.repository import (
    DeliveryRepository,
    ItemRepository,
    MetaRepository,
    RuleRepository,
    UserRepository,
)

TOP_LIMIT = 20
USER_LIST_LIMIT = 50
HELP = (
    "可用指令：\n"
    "/admin stats — 总览\n"
    "/admin top — 关键词订阅排行\n"
    "/admin users — 最近用户\n"
    "/admin ban <tg_user_id>\n"
    "/admin unban <tg_user_id>\n"
    "/admin health — 轮询健康状态"
)


@dataclass(slots=True)
class AdminDeps:
    db: Database
    users: UserRepository
    rules: RuleRepository
    deliveries: DeliveryRepository
    items: ItemRepository
    meta: MetaRepository
    admins: list[int]


def handle_admin(deps: AdminDeps, tg_user_id: int, text: str) -> HandleResult:
    if tg_user_id not in deps.admins:
        return HandleResult("你没有权限执行该指令。")
    parts = text.strip().split()
    command = parts[1].lower() if len(parts) > 1 else ""
    args = parts[2:]

    if command == "stats":
        return HandleResult(_stats(deps))
    if command == "top":
        return HandleResult(_top(deps))
    if command == "users":
        return HandleResult(_users(deps))
    if command == "health":
        return HandleResult(_health(deps))
    if command == "ban" and args:
        return HandleResult(_set_ban(deps, args[0], banned=True))
    if command == "unban" and args:
        return HandleResult(_set_ban(deps, args[0], banned=False))
    return HandleResult(HELP)


def _stats(deps: AdminDeps) -> str:
    with deps.db.tx() as conn:

        def count(sql: str, params: tuple[object, ...] = ()) -> int:
            return int(conn.execute(sql, params).fetchone()[0])

        users = count("SELECT COUNT(*) FROM users")
        banned = count("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        rules = count("SELECT COUNT(*) FROM rules WHERE enabled = 1")
        items = count("SELECT COUNT(*) FROM items")
        pending = count("SELECT COUNT(*) FROM deliveries WHERE status = 'pending'")
        sent = count("SELECT COUNT(*) FROM deliveries WHERE status = 'sent'")
        failed = count("SELECT COUNT(*) FROM deliveries WHERE status = 'failed'")

    return (
        f"用户 {users}（封禁 {banned}）\n"
        f"关键词 {rules}\n"
        f"已抓取帖子 {items}\n"
        f"投递：待发 {pending} / 已发 {sent} / 失败 {failed}"
    )


def _top(deps: AdminDeps) -> str:
    rows = deps.rules.top_keywords(limit=TOP_LIMIT)
    if not rows:
        return "还没有任何关键词。"
    lines = [f"{index}. {word} — {count} 人" for index, (word, count) in enumerate(rows, 1)]
    return "关键词订阅排行：\n" + "\n".join(lines)


def _users(deps: AdminDeps) -> str:
    with deps.db.tx() as conn:
        rows = conn.execute(
            """
            SELECT u.tg_user_id AS tg_user_id, u.username AS username,
                   u.is_banned AS is_banned, p.tg_user_id AS inviter,
                   (SELECT COUNT(*) FROM rules r WHERE r.user_id = u.id) AS rules
            FROM users AS u
            LEFT JOIN users AS p ON p.id = u.invited_by
            ORDER BY u.id DESC
            LIMIT ?
            """,
            (USER_LIST_LIMIT,),
        ).fetchall()
    if not rows:
        return "还没有用户。"
    lines = [
        f"{row['tg_user_id']} @{row['username'] or '-'} 规则{row['rules']}"
        f"{' 已封禁' if row['is_banned'] else ''}"
        f" 邀请人{row['inviter'] or '-'}"
        for row in rows
    ]
    return f"最近 {USER_LIST_LIMIT} 个用户：\n" + "\n".join(lines)


def _health(deps: AdminDeps) -> str:
    last_success = deps.meta.get("last_success_at") or "从未成功"
    last_error = deps.meta.get("last_error") or "无"
    return f"最后一次成功轮询：{last_success}\n最近错误：{last_error}"


def _set_ban(deps: AdminDeps, raw_id: str, *, banned: bool) -> str:
    try:
        tg_user_id = int(raw_id)
    except ValueError:
        return "请提供数字 user id。"
    if not deps.users.set_banned(tg_user_id, banned):
        return "找不到该用户。"
    return f"已{'封禁' if banned else '解封'} {tg_user_id}。"
