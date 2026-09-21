"""aiogram wiring.

This is the only module that knows about Telegram updates; every decision
lives in ``router.py`` as a pure function.
"""

from __future__ import annotations

from aiogram import Dispatcher, F
from aiogram.types import CallbackQuery, Message

from nodeseek_bot.bot.router import BotDeps, route_callback, route_message


def build_dispatcher(deps: BotDeps) -> Dispatcher:
    """Register the two update handlers the bot needs."""
    dispatcher = Dispatcher()

    @dispatcher.message(F.text)
    async def on_message(message: Message) -> None:
        user = message.from_user
        if user is None:
            return
        result = route_message(deps, user.id, user.username, message.text or "")
        await message.answer(result.text, reply_markup=result.markup)

    @dispatcher.callback_query(F.data)
    async def on_callback(query: CallbackQuery) -> None:
        result = route_callback(deps, query.from_user.id, query.data or "")
        await query.answer()
        if result is None:
            return
        if isinstance(query.message, Message):
            await query.message.answer(result.text, reply_markup=result.markup)
        else:
            await query.bot.send_message(
                query.from_user.id, result.text, reply_markup=result.markup
            )

    return dispatcher
