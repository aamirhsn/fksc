"""Telegram listener using a user-account client (MTProto push, not bot notifications)."""
from __future__ import annotations

import asyncio
import logging
import time

from telethon import TelegramClient, events
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

from .parser import parse_message, should_buy

log = logging.getLogger("fksc.listener")


def message_urls(msg) -> list[str]:
    urls = []
    for ent in msg.entities or []:
        if isinstance(ent, MessageEntityTextUrl):
            urls.append(ent.url)
        elif isinstance(ent, MessageEntityUrl):
            urls.append(msg.raw_text[ent.offset: ent.offset + ent.length])
    if msg.reply_markup and getattr(msg.reply_markup, "rows", None):
        for row in msg.reply_markup.rows:
            for b in row.buttons:
                if getattr(b, "url", None):
                    urls.append(b.url)
    return urls


def attach(client: TelegramClient, cfg, buyer, state, notify):
    channels = cfg.get("telegram.channels", [])
    flt = cfg.get("filter", {}) or {}

    async def handle(event):
        t_recv = time.time()
        msg = event.message
        deal = parse_message(msg.raw_text or "", message_urls(msg))
        if not deal:
            return
        lag = t_recv - msg.date.timestamp()
        ok, why = should_buy(deal, flt)
        log.info("deal (tg lag %.2fs) price=%s 100%%=%s url=%s -> %s", lag, deal.price, deal.is_100, deal.url, why)
        if not ok or state.seen(deal.key):
            return
        state.mark_seen(deal.key)
        res = await buyer.buy(deal.url, deal.price)
        icon = "✅" if res.ok else "❌"
        await notify(f"{icon} {res.stage}: {res.message} ({res.elapsed:.1f}s) {deal.url}")

    # New posts and edits (channels often edit a post to add the link).
    client.add_event_handler(lambda e: asyncio.create_task(handle(e)), events.NewMessage(chats=channels))
    client.add_event_handler(lambda e: asyncio.create_task(handle(e)), events.MessageEdited(chats=channels))
