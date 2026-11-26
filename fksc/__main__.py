"""CLI entry point: python -m fksc <command>"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import env, load_config
from .otp import OtpHub
from .parser import parse_message, should_buy
from .state import DailyState


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _tg_client(cfg):
    from telethon import TelegramClient
    return TelegramClient(
        str(cfg.base_dir / cfg.get("telegram.session_name", "fksc")),
        int(env("TG_API_ID", True)),
        env("TG_API_HASH", True),
    )


async def cmd_login(cfg):
    """Log in to Telegram (phone + code) and to Flipkart (in a real browser window)."""
    from .buyer import Buyer
    client = _tg_client(cfg)
    await client.start()
    me = await client.get_me()
    print(f"Telegram: logged in as {me.first_name}")
    for ch in cfg.get("telegram.channels", []):
        ent = await client.get_entity(ch)
        print(f"  watching: {getattr(ent, 'title', ch)}")
    await client.disconnect()

    buyer = Buyer(cfg, DailyState(cfg.path("state_file", "state.json")), OtpHub(), _print_notify)
    await buyer.start(headless=False)
    page = await buyer.ctx.new_page()
    await page.goto("https://www.flipkart.com/account/login")
    print("\nLog in to Flipkart in the browser window (and save your card there if needed).")
    input("Press Enter here once you're logged in... ")
    print("Flipkart logged in:", await buyer.is_logged_in())
    await buyer.stop()


async def _print_notify(text: str):
    print("\a" + text, flush=True)


async def cmd_run(cfg):
    from .buyer import Buyer
    from .listener import attach

    client = _tg_client(cfg)
    await client.start()
    state = DailyState(cfg.path("state_file", "state.json"))
    hub = OtpHub()

    async def notify(text: str):
        if cfg.get("notify.beep", True):
            print("\a", end="", flush=True)
        logging.getLogger("fksc").warning(text)
        if cfg.get("notify.telegram_saved_messages", True):
            try:
                await client.send_message("me", text, link_preview=False)
            except Exception:
                pass

    if cfg.get("otp_webhook.enabled"):
        await hub.serve(cfg.get("otp_webhook.host", "0.0.0.0"), int(cfg.get("otp_webhook.port", 8765)), env("OTP_WEBHOOK_TOKEN", True))

    buyer = Buyer(cfg, state, hub, notify)
    await buyer.start()
    if not await buyer.is_logged_in():
        print("Flipkart session is not logged in. Run: python -m fksc login")
        await buyer.stop()
        return
    attach(client, cfg, buyer, state, notify)
    mode = "DRY RUN" if cfg.get("buyer.dry_run", True) else "LIVE"
    print(f"[{mode}] Listening on {cfg.get('telegram.channels')} — Ctrl+C to stop")
    try:
        await client.run_until_disconnected()
    finally:
        await buyer.stop()


async def cmd_buy(cfg, url: str):
    """Run the purchase flow once on a URL (respects dry_run) to test selectors."""
    from .buyer import Buyer
    hub = OtpHub()
    if cfg.get("otp_webhook.enabled"):
        await hub.serve(cfg.get("otp_webhook.host", "0.0.0.0"), int(cfg.get("otp_webhook.port", 8765)), env("OTP_WEBHOOK_TOKEN", True))
    buyer = Buyer(cfg, DailyState(cfg.path("state_file", "state.json")), hub, _print_notify)
    await buyer.start()
    res = await buyer.buy(url)
    print(res)
    input("Press Enter to close the browser... ")
    await buyer.stop()


async def cmd_history(cfg, limit: int):
    """Replay the last N channel posts through the parser/filter (no buying)."""
    from .listener import message_urls
    client = _tg_client(cfg)
    await client.start()
    for ch in cfg.get("telegram.channels", []):
        async for msg in client.iter_messages(ch, limit=limit):
            deal = parse_message(msg.raw_text or "", message_urls(msg))
            if not deal:
                print(f"--- {msg.date:%d %b %H:%M:%S}  (no flipkart link)")
                continue
            ok, why = should_buy(deal, cfg.get("filter", {}) or {})
            print(f"--- {msg.date:%d %b %H:%M:%S}  {'BUY ' if ok else 'skip'} [{why}] price={deal.price} {deal.url}")
    await client.disconnect()


def main(argv=None):
    _setup_logging()
    ap = argparse.ArgumentParser(prog="fksc", description="Flipkart 100% SuperCoin deal sniper")
    ap.add_argument("-c", "--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="log in to Telegram and Flipkart (one time)")
    sub.add_parser("run", help="watch the channel and buy matching deals")
    b = sub.add_parser("buy", help="run the buy flow once on a product URL")
    b.add_argument("url")
    h = sub.add_parser("history", help="test the parser on recent channel posts")
    h.add_argument("-n", type=int, default=30)
    p = sub.add_parser("parse", help="test the parser on text from stdin")
    args = ap.parse_args(argv)

    if args.cmd == "parse":
        cfg_filter = {"require_100_percent": True}
        deal = parse_message(sys.stdin.read())
        print(deal, should_buy(deal, cfg_filter) if deal else None)
        return
    cfg = load_config(args.config)
    try:
        if args.cmd == "login":
            asyncio.run(cmd_login(cfg))
        elif args.cmd == "run":
            asyncio.run(cmd_run(cfg))
        elif args.cmd == "buy":
            asyncio.run(cmd_buy(cfg, args.url))
        elif args.cmd == "history":
            asyncio.run(cmd_history(cfg, args.n))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
