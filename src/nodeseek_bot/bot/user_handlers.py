"""Pure command handlers.

They deliberately avoid aiogram's dispatcher so every branch can be tested
without a network or a running bot.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardMarkup

from nodeseek_bot.bot.keyboards import rule_list_keyboard
from nodeseek_bot.config import LimitsConfig
from nodeseek_bot.matcher import RuleLimitExceeded, expand_rules, matches
from nodeseek_bot.repository import (
    InviteQuotaExceeded,
    InviteRepository,
    ItemRepository,
    RuleRepository,
    UserRepository,
)

NEED_INVITE_TEXT = (
    "👋 这是一个邀请制的小工具，需要一个邀请码才能开始。\n"
    "如果你有邀请码，发送 /start 邀请码 即可加入；"
    "没有的话，可以找已经在用的人要一个。"
)

WELCOME_TEXT = "🎉 欢迎加入！\n\n"

HELP_TEXT = (
    "🤖 NodeSeek 关键词监控\n"
    "\n"
    "/add 关键词 —— 新增规则，例如 /add 日本 vps\n"
    "/list —— 查看已有规则（点按钮即可删除）\n"
    "/clear —— 清空全部规则\n"
    "/test 关键词 —— 用最近 24 小时的帖子试一下会命中多少条\n"
    "/invite —— 生成邀请码\n"
    "/help —— 显示这条帮助\n"
    "\n"
    "只匹配标题；多个词用空格分隔，表示这些词要同时出现。"
)


@dataclass(frozen=True, slots=True)
class HandleResult:
    text: str
    markup: InlineKeyboardMarkup | None = None


def handle_add(
    users: UserRepository,
    rules: RuleRepository,
    limits: LimitsConfig,
    tg_user_id: int,
    username: str | None,
    keyword: str,
) -> HandleResult:
    if expand_rules(keyword) is None:
        return HandleResult("⚠️ 关键词不能为空，且长度不能超过 64 个字符。")
    user_id = users.ensure(tg_user_id=tg_user_id, username=username)
    try:
        rules.add(user_id, keyword, limit=limits.max_rules_per_user)
    except RuleLimitExceeded:
        return HandleResult(
            f"⚠️ 关键词数量已达上限（最多 {limits.max_rules_per_user} 个），"
            "请先删除一个再添加。\n发送 /list 查看当前规则。"
        )
    except ValueError:
        return HandleResult(f"⚠️ 关键词「{keyword.strip()}」已经存在了。")
    return HandleResult(
        f"✅ 已添加关键词「{keyword.strip()}」。", rule_list_keyboard(rules.list_for_user(user_id))
    )


def handle_list(users: UserRepository, rules: RuleRepository, tg_user_id: int) -> HandleResult:
    user_id = users.ensure(tg_user_id=tg_user_id, username=None)
    rows = rules.list_for_user(user_id)
    if not rows:
        return HandleResult("你还没有设置任何关键词。发送 /add 关键词 试试。")
    lines = [f"{index}. {rule.keyword_raw}" for index, rule in enumerate(rows, start=1)]
    return HandleResult(
        "🔑 当前关键词：\n" + "\n".join(lines) + "\n\n点下面的按钮可以删除。",
        rule_list_keyboard(rows),
    )


def handle_delete(
    users: UserRepository, rules: RuleRepository, tg_user_id: int, rule_id: int
) -> HandleResult:
    user_id = users.ensure(tg_user_id=tg_user_id, username=None)
    if not rules.delete(user_id, rule_id):
        return HandleResult("⚠️ 这条规则不存在，可能已经被删掉了。")
    return HandleResult("✅ 已删除该关键词。", rule_list_keyboard(rules.list_for_user(user_id)))


def handle_clear(users: UserRepository, rules: RuleRepository, tg_user_id: int) -> HandleResult:
    user_id = users.ensure(tg_user_id=tg_user_id, username=None)
    removed = rules.clear(user_id)
    if removed == 0:
        return HandleResult("你还没有设置任何关键词。")
    return HandleResult(f"✅ 已清空 {removed} 个关键词。")


def handle_test(items: ItemRepository, tg_user_id: int, keyword: str) -> HandleResult:
    rule = expand_rules(keyword)
    if rule is None:
        return HandleResult("⚠️ 关键词不能为空，且长度不能超过 64 个字符。")
    titles = [title for title, title_norm in items.recent_titles(24) if matches(title_norm, rule)]
    if not titles:
        return HandleResult(f"最近 24 小时没有标题命中「{keyword.strip()}」。")
    sample = "\n".join(f"· {title}" for title in titles[:3])
    return HandleResult(
        f"🔍 最近 24 小时命中 {len(titles)} 条：\n{sample}\n\n"
        "规则变更只影响之后的新帖，不会补推历史。"
    )


def handle_help() -> HandleResult:
    return HandleResult(HELP_TEXT)


def handle_invite(
    users: UserRepository,
    invites: InviteRepository,
    limits: LimitsConfig,
    tg_user_id: int,
    username: str | None = None,
) -> HandleResult:
    user_id = users.ensure(tg_user_id=tg_user_id, username=username)
    if not limits.invite_required:
        return HandleResult("这个 bot 现在不需要邀请码：直接让对方发送 /start 就能加入。")
    if limits.invite_mode == "fixed":
        code = invites.fixed(user_id)
        return HandleResult(
            f"🎟 你的邀请码：{code}\n\n"
            "这是你的固定邀请码，可以反复使用：让朋友发送 "
            f"/start {code} 即可加入。"
        )
    try:
        (code,) = invites.issue(created_by=user_id, quota=limits.invite_quota_per_user)
    except InviteQuotaExceeded:
        return HandleResult(
            f"⚠️ 你的邀请码已经用完了：未使用的码最多同时持有 {limits.invite_quota_per_user} 个。"
        )
    return HandleResult(
        f"🎟 你的邀请码：{code}\n\n把它发给朋友，让对方发送 /start {code} 即可加入。"
    )


def handle_start(
    users: UserRepository,
    invites: InviteRepository,
    limits: LimitsConfig,
    tg_user_id: int,
    username: str | None = None,
    payload: str | None = None,
) -> HandleResult:
    if payload and limits.invite_required:
        inviter_id = invites.redeem(payload)
        if inviter_id is None:
            return HandleResult("⚠️ 这个邀请码无效，或者已经被用过了。")
        user_id = users.ensure(tg_user_id=tg_user_id, username=username)
        users.set_inviter(user_id, inviter_id)
        return HandleResult(WELCOME_TEXT + HELP_TEXT)
    if users.find_by_tg_id(tg_user_id) is not None:
        return handle_help()
    if limits.invite_required:
        return HandleResult(NEED_INVITE_TEXT)
    users.ensure(tg_user_id=tg_user_id, username=username)
    return HandleResult(WELCOME_TEXT + HELP_TEXT)
