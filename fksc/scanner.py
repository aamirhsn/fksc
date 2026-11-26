"""Polls Flipkart pages in the logged-in browser and finds SuperCoin-back offers.

Two signals per reload:
  1. DOM: every product link on the page and the text of its card.
  2. JSON: Flipkart's own API responses the page loads, searched for SuperCoin mentions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from urllib.parse import urljoin

from playwright.async_api import Page

from .detect import Offer, detect

log = logging.getLogger("fksc.scanner")

# Returns [{href, text}] for each product card (or the whole page if it is a product page).
CARDS_JS = r"""
() => {
  const out = [], seen = new Set();
  if (/\/p\/itm/i.test(location.pathname)) {
    out.push({href: location.href, text: (document.querySelector('main') || document.body).innerText.slice(0, 8000)});
  }
  for (const a of document.querySelectorAll('a[href*="/p/"]')) {
    const href = a.href.split('#')[0];
    const key = href.split('?')[0];
    if (seen.has(key)) continue;
    seen.add(key);
    // climb to the card: the largest ancestor that still holds only this product
    let el = a, best = a;
    for (let i = 0; i < 7 && el.parentElement; i++) {
      el = el.parentElement;
      const links = new Set([...el.querySelectorAll('a[href*="/p/"]')].map(x => x.href.split('?')[0]));
      if (links.size > 1 || el.innerText.length > 1500) break;
      best = el;
    }
    out.push({href, text: best.innerText});
  }
  return out;
}
"""


def _json_candidates(obj, base_url: str):
    """Yield (url, text) for JSON subtrees that mention SuperCoins and point at a product."""
    def strings(o, acc):
        if isinstance(o, str):
            acc.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                strings(v, acc)
        elif isinstance(o, list):
            for v in o:
                strings(v, acc)
        return acc

    def product_url(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str) and "/p/itm" in v:
                    return urljoin("https://www.flipkart.com", v)
                if k.lower() in ("pid", "productid") and isinstance(v, str) and len(v) >= 10:
                    return f"https://www.flipkart.com/product/p/itm?pid={v}"
            for v in o.values():
                u = product_url(v) if isinstance(v, dict) else None
                if u:
                    return u
        return None

    def walk(o, stack):
        if isinstance(o, dict):
            stack.append(o)
            for v in o.values():
                walk(v, stack)
            stack.pop()
        elif isinstance(o, list):
            for v in o:
                walk(v, stack)
        elif isinstance(o, str) and "supercoin" in o.lower().replace(" ", ""):
            # nearest enclosing dict that identifies a product
            for anc in reversed(stack[-6:]):
                u = product_url(anc)
                if u:
                    yield_list.append((u, "\n".join(strings(anc, []))[:4000]))
                    break

    yield_list: list = []
    walk(obj, [])
    return yield_list


class Scanner:
    def __init__(self, cfg, buyer, notify):
        self.cfg = cfg
        self.buyer = buyer
        self.notify = notify
        self.events: deque[dict] = deque(maxlen=300)
        self.last_bought: dict[str, float] = {}
        self.source_status: dict[str, dict] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ---- settings (live-editable from the dashboard) ----
    @property
    def min_ratio(self) -> float:
        return float(self.cfg.get("scanner.min_ratio", 0.5))

    def sources(self) -> list[str]:
        return list(self.cfg.get("scanner.sources", []) or []) + list(self.cfg.get("scanner.watchlist", []) or [])

    def sync_sources(self):
        """Start/stop one watcher tab per configured source."""
        want = set(self.sources())
        for src in list(self._tasks):
            if src not in want:
                self._tasks.pop(src).cancel()
                self.source_status.pop(src, None)
        for i, src in enumerate(sorted(want)):
            if src not in self._tasks:
                self._tasks[src] = asyncio.create_task(self._watch(src, delay=i * 0.7))

    # ---- core loop ----
    async def _watch(self, src: str, delay: float):
        await asyncio.sleep(delay)  # stagger tabs
        page = await self.buyer.ctx.new_page()
        bodies: list = []

        async def on_response(resp):
            try:
                if "flipkart" in resp.url and "json" in (resp.headers.get("content-type") or ""):
                    bodies.append(await resp.json())
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(on_response(r)))
        st = self.source_status.setdefault(src, {"scans": 0, "errors": 0, "last": None, "found": 0, "error": ""})
        try:
            while True:
                t0 = time.monotonic()
                bodies.clear()
                try:
                    offers = await self._scan(page, src, bodies)
                    st.update(scans=st["scans"] + 1, last=time.time(), found=len(offers), error="",
                              ms=int((time.monotonic() - t0) * 1000))
                    for off in offers:
                        await self._consider(off, src)
                except Exception as e:
                    st.update(errors=st["errors"] + 1, error=f"{type(e).__name__}: {str(e)[:150]}")
                    log.warning("scan %s failed: %s", src, e)
                interval = float(self.cfg.get("scanner.interval_s", 5))
                await asyncio.sleep(max(0.5, interval + random.uniform(-0.3, 0.3) * interval))
        except asyncio.CancelledError:
            await page.close()
            raise

    async def _scan(self, page: Page, src: str, bodies: list) -> list[Offer]:
        await page.goto(src, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(int(self.cfg.get("scanner.settle_ms", 800)))
        for _ in range(int(self.cfg.get("scanner.scroll_screens", 0))):
            await page.mouse.wheel(0, 1600)
            await page.wait_for_timeout(250)
        found: dict[str, Offer] = {}
        for card in await page.evaluate(CARDS_JS):
            off = detect(card["href"], card["text"])
            if off:
                found.setdefault(off.key, off)
        for body in list(bodies):
            for url, text in _json_candidates(body, src):
                off = detect(url, text)
                if off and off.key not in found:
                    found[off.key] = off
        return list(found.values())

    async def _consider(self, off: Offer, src: str):
        ratio = off.ratio
        ev = {
            "t": time.time(), "key": off.key, "url": off.url, "title": off.title, "price": off.price,
            "coins": off.coins, "pct": off.pct, "ratio": round(ratio, 3), "snippet": off.snippet,
            "source": src, "status": "seen",
        }
        # Update the existing row instead of spamming one row per reload
        prev = next((e for e in self.events if e["key"] == off.key), None)
        if prev:
            prev.update(t=ev["t"], price=off.price, coins=off.coins, pct=off.pct, ratio=ev["ratio"])
            ev = prev
        else:
            self.events.appendleft(ev)
            log.info("offer %.0f%% coins=%s price=%s %s", ratio * 100, off.coins, off.price, off.url)

        if ratio < self.min_ratio:
            return
        if ev["status"] in ("buying", "ordered", "dry_run", "needs_you"):
            return
        cooldown = float(self.cfg.get("scanner.rebuy_cooldown_h", 24)) * 3600
        if time.time() - self.last_bought.get(off.key, 0) < cooldown:
            return
        if not self.cfg.get("scanner.auto_buy", False):
            if ev["status"] != "alerted":
                ev["status"] = "alerted"
                await self.notify(f"{ratio*100:.0f}% SuperCoins: {off.title or off.url} ₹{off.price}", url=off.url)
            return
        await self.trigger_buy(off.url, ev)

    async def trigger_buy(self, url: str, ev: dict | None = None):
        from .detect import product_key
        key = product_key(url)
        if ev is None:
            ev = {"t": time.time(), "key": key, "url": url, "title": "(manual)", "price": None, "coins": None,
                  "pct": None, "ratio": None, "snippet": "", "source": "manual", "status": "seen"}
            self.events.appendleft(ev)
        self.last_bought[key] = time.time()
        ev["status"] = "buying"

        async def run():
            res = await self.buyer.buy(url, min_ratio=self.min_ratio if ev.get("source") != "manual" else None)
            ev.update(status=res.stage if res.ok else ("needs_you" if "needs you" in res.message else "failed"),
                      result=res.message, amount=res.amount, elapsed=round(res.elapsed, 2))
            if not res.ok and res.stage in ("product", "coins"):
                self.last_bought.pop(key, None)  # allow retry next time it shows up
            await self.notify(f"{'✅' if res.ok else '❌'} {res.stage}: {res.message} ({res.elapsed:.1f}s)", url=url)

        asyncio.create_task(run())
