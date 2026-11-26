"""Find SuperCoin-back offers in product text and compute coins / price.

1 SuperCoin is treated as ₹1. A "ratio" of 1.0 means 100% coin back.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

SC = r"super\s*-?\s*coins?"
# "100% SuperCoins", "75% coin back", "50% SC"
PCT_RE = re.compile(rf"(\d{{2,3}})\s*%\s*(?:{SC}|coin\s*-?\s*back|sc\b)(?:\s*back)?", re.I)
# "Earn 499 SuperCoins", "Get 499 SuperCoins back", "499 SuperCoins cashback", "SuperCoins worth ₹499"
COINS_RE = re.compile(
    rf"(?:earn|get|win|receive|back|upto|up\s*to)\D{{0,20}}?(\d[\d,]*)\s*{SC}"
    rf"|(\d[\d,]*)\s*{SC}\s*(?:back|cash\s*-?back|reward|bonus)"
    rf"|{SC}\s*(?:back\s*)?(?:worth|of)\s*₹?\s*(\d[\d,]*)",
    re.I,
)
# Offers about *spending* coins are not coin-back.
SPEND_CONTEXT = re.compile(r"(using|use|redeem|pay\s*with|spend|burn)\W+(\w+\W+){0,3}$", re.I)
PRICE_LINE_RE = re.compile(r"^\s*₹\s*([\d,]+(?:\.\d+)?)\s*$", re.M)
PRICE_RE = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


@dataclass
class Offer:
    url: str
    title: str
    price: float | None
    coins: float | None
    pct: float | None
    snippet: str

    @property
    def ratio(self) -> float:
        r = 0.0
        if self.pct:
            r = max(r, self.pct / 100)
        if self.coins and self.price:
            r = max(r, self.coins / self.price)
        return r

    @property
    def key(self) -> str:
        return product_key(self.url)


def product_key(url: str) -> str:
    u = urlparse(url)
    pid = parse_qs(u.query).get("pid")
    if pid:
        return pid[0].upper()
    m = re.search(r"/p/(itm[0-9a-z]+)", u.path, re.I)
    return m.group(1) if m else u.path.rstrip("/")


def extract_price(text: str) -> float | None:
    # On cards the selling price is the first "₹X" on its own line (MRP comes after, struck through).
    m = PRICE_LINE_RE.search(text)
    if m:
        return _num(m.group(1))
    nums = [_num(x) for x in PRICE_RE.findall(text)]
    nums = [n for n in nums if n >= 10]
    return min(nums) if nums else None


def detect(url: str, text: str) -> Offer | None:
    """Return an Offer if text mentions earning SuperCoins, else None."""
    if not re.search(SC + r"|coin\s*-?\s*back", text, re.I):
        return None
    pct = coins = None
    snippet = ""
    for m in PCT_RE.finditer(text):
        if SPEND_CONTEXT.search(text[max(0, m.start() - 40): m.start()]):
            continue
        v = _num(m.group(1))
        if v <= 100 and (pct is None or v > pct):
            pct, snippet = v, m.group(0)
    for m in COINS_RE.finditer(text):
        if SPEND_CONTEXT.search(text[max(0, m.start() - 40): m.start()]):
            continue
        v = _num(next(g for g in m.groups() if g))
        if coins is None or v > coins:
            coins = v
            snippet = snippet or m.group(0)
    if pct is None and coins is None:
        return None
    title = next((ln.strip() for ln in text.splitlines() if len(ln.strip()) > 12 and "₹" not in ln), "")[:120]
    return Offer(url=url, title=title, price=extract_price(text), coins=coins, pct=pct, snippet=snippet.strip())
