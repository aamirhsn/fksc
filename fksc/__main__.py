"""CLI entry point: python -m fksc <command>"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from collections import deque

from .config import env, load_config
from .otp import OtpHub
from .state import DailyState


def _setup_logging():
    logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S",
                        format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s")


def _buyer(cfg, hub=None, notify=None):
    from .buyer import Buyer

    async def printer(text, url=None):
        print("\a" + text + (f"  {url}" if url else ""), flush=True)
    return Buyer(cfg, DailyState(cfg.path("state_file", "state.json")), hub or OtpHub(), notify or printer)


async def cmd_login(cfg):
    """Open a real browser window so you can log in to Flipkart once; the session is kept in browser_profile/."""
    buyer = _buyer(cfg)
    await buyer.start(headless=False)
    page = await buyer.ctx.new_page()
    await page.goto("https://www.flipkart.com/account/login")
    print("Log in to Flipkart in the browser window (phone + OTP). Save your credit card in your")
    print("Flipkart account too if it isn't saved yet.")
    await asyncio.to_thread(input, "Press Enter here once you're logged in... ")
    print("Flipkart logged in:", await buyer.is_logged_in())
    await buyer.stop()


async def cmd_discover(cfg, urls: list[str]):
    """Browse Flipkart normally; every SuperCoin mention on any page is recorded."""
    from .detect import detect
    from .scanner import CARDS_JS, _json_candidates

    buyer = _buyer(cfg)
    await buyer.start(headless=False)
    out_path = cfg.path("buyer.debug_dir", "debug") / "discover.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = open(out_path, "a", encoding="utf-8")
    pages_with_coins: dict[str, int] = {}

    def record(kind, page_url, data):
        out.write(json.dumps({"t": time.time(), "kind": kind, "page": page_url, **data}, ensure_ascii=False) + "\n")
        out.flush()

    async def on_response(page, resp):
        try:
            if "flipkart" not in resp.url or "json" not in (resp.headers.get("content-type") or ""):
                return
            body = await resp.text()
            if "supercoin" not in body.lower().replace(" ", ""):
                return
            record("api", page.url, {"api_url": resp.url[:300]})
            for u, text in _json_candidates(json.loads(body), page.url):
                off = detect(u, text)
                record("api_offer", page.url, {"url": u, "ratio": off.ratio if off else None, "text": text[:600]})
        except Exception:
            pass

    async def on_load(page):
        try:
            await page.wait_for_timeout(1500)
            n = 0
            for card in await page.evaluate(CARDS_JS):
                off = detect(card["href"], card["text"])
                if off:
                    n += 1
                    record("dom_offer", page.url, {"url": off.url, "ratio": round(off.ratio, 3), "price": off.price,
                                                   "coins": off.coins, "pct": off.pct, "text": card["text"][:600]})
            body = await page.locator("body").inner_text()
            for line in body.splitlines():
                if "supercoin" in line.lower().replace(" ", ""):
                    record("text", page.url, {"line": line.strip()[:300]})
            if n:
                pages_with_coins[page.url] = n
                print(f"  {n:3d} products with SuperCoin offers on {page.url}")
        except Exception:
            pass

    def hook(page):
        page.on("response", lambda r: asyncio.create_task(on_response(page, r)))
        page.on("load", lambda: asyncio.create_task(on_load(page)))

    buyer.ctx.on("page", hook)
    for p in buyer.ctx.pages:
        hook(p)
    for u in urls:
        p = await buyer.ctx.new_page()
        await p.goto(u)
    print("Browse Flipkart in the window: go wherever you normally see SuperCoin-back deals")
    print("(SuperCoin zone, offer banners, categories, product pages). Everything is logged to", out_path)
    await asyncio.to_thread(input, "Press Enter here to stop... ")
    out.close()
    print("\nPages where products with SuperCoin offers were found:")
    for u, n in sorted(pages_with_coins.items(), key=lambda x: -x[1]):
        print(f"  {n:3d}  {u}")
    print("\nAdd the useful ones as watched pages in the dashboard (or scanner.sources in config.yaml).")
    await buyer.stop()


async def cmd_run(cfg):
    from aiohttp import web

    from .scanner import Scanner
    from .web import make_app

    alerts: deque = deque(maxlen=100)
    hub = OtpHub()

    async def notify(text: str, url: str | None = None):
        alerts.appendleft({"t": time.time(), "text": text, "url": url})
        if cfg.get("notify.beep", True):
            print("\a", end="", flush=True)
        logging.getLogger("fksc").warning("%s %s", text, url or "")

    if cfg.get("otp_webhook.enabled"):
        await hub.serve(cfg.get("otp_webhook.host", "0.0.0.0"), int(cfg.get("otp_webhook.port", 8765)), env("OTP_WEBHOOK_TOKEN", True))

    buyer = _buyer(cfg, hub, notify)
    await buyer.start()
    if not await buyer.is_logged_in():
        print("Flipkart is not logged in in this browser profile. Run: python -m fksc login")
        await buyer.stop()
        return
    scanner = Scanner(cfg, buyer, notify)
    scanner.sync_sources()

    runner = web.AppRunner(make_app(cfg, scanner, buyer.state, alerts))
    await runner.setup()
    host, port = cfg.get("dashboard.host", "127.0.0.1"), int(cfg.get("dashboard.port", 8080))
    await web.TCPSite(runner, host, port).start()
    print(f"Dashboard: http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}   (Ctrl+C to stop)")
    try:
        await asyncio.Event().wait()
    finally:
        await buyer.stop()


async def cmd_buy(cfg, url: str):
    hub = OtpHub()
    if cfg.get("otp_webhook.enabled"):
        await hub.serve(cfg.get("otp_webhook.host", "0.0.0.0"), int(cfg.get("otp_webhook.port", 8765)), env("OTP_WEBHOOK_TOKEN", True))
    buyer = _buyer(cfg, hub)
    await buyer.start()
    print(await buyer.buy(url))
    await asyncio.to_thread(input, "Press Enter to close the browser... ")
    await buyer.stop()


def main(argv=None):
    _setup_logging()
    ap = argparse.ArgumentParser(prog="fksc", description="Flipkart SuperCoin-back deal watcher and auto-buyer")
    ap.add_argument("-c", "--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="log in to Flipkart once in a real browser window")
    d = sub.add_parser("discover", help="record every SuperCoin offer while you browse")
    d.add_argument("urls", nargs="*", default=["https://www.flipkart.com/"])
    sub.add_parser("run", help="watch pages, buy matching deals, serve the dashboard")
    b = sub.add_parser("buy", help="run the buy flow once on a product URL")
    b.add_argument("url")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    try:
        if args.cmd == "login":
            asyncio.run(cmd_login(cfg))
        elif args.cmd == "discover":
            asyncio.run(cmd_discover(cfg, args.urls))
        elif args.cmd == "run":
            asyncio.run(cmd_run(cfg))
        elif args.cmd == "buy":
            asyncio.run(cmd_buy(cfg, args.url))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
