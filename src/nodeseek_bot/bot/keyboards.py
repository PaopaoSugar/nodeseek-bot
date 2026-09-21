"""Inline keyboards."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from nodeseek_bot.repository import RuleRow


def rule_list_keyboard(rules: list[RuleRow]) -> InlineKeyboardMarkup:
    """One delete button per rule, so users never have to type ids."""
    rows = [
        [InlineKeyboardButton(text=f"❌ {rule.keyword_raw}", callback_data=f"del:{rule.id}")]
        for rule in rules
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
