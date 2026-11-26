"""Parse deal posts from Telegram into structured deals."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Flipkart product links and the shortlink domains deal channels commonly use.
URL_RE = re.compile(
    r"https?://(?:"
    r"(?:www\.|dl\.)?flipkart\.com/[^\s<>\"')\]]+"
    r"|fkrt\.(?:it|cc|co|to)/[^\s<>\"')\]]+"
    r"|fktr\.in/[^\s<>\"')\]]+"
    r"|fkrt\.site/[^\s<>\"')\]]+"
    r"|bitli\.in/[^\s<>\"')\]]+"
    r"|bit\.ly/[^\s<>\"')\]]+"
    r"|ekaro\.in/[^\s<>\"')\]]+"
    r"|extp\.in/[^\s<>\"')\]]+"
    r")",
    re.IGNORECASE,
)

HUNDRED_PCT_RE = re.compile(
    r"100\s*%\s*(?:off\s*)?(?:super\s*-?\s*coins?|sc\b|coin\s*-?\s*back|coins?\b|cash\s*-?\s*back)"
    r"|(?:full|100\s*%)\s*coin\s*-?\s*back"
    r"|(?:super\s*coins?|sc)\s*[:\-]?\s*100\s*%",
    re.IGNORECASE,
)

PRICE_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
PID_RE = re.compile(r"[?&]pid=([A-Z0-9]{10,20})", re.IGNORECASE)


@dataclass
class Deal:
    text: str
    urls: list[str]
    is_100: bool
    prices: list[float] = field(default_factory=list)

    @property
    def price(self) -> float | None:
        # Deal posts usually list "MRP ₹X  Deal ₹Y"; the lowest number is the deal price.
        return min(self.prices) if self.prices else None

    @property
    def url(self) -> str | None:
        # Prefer a direct flipkart.com link (no redirect hop) over a shortlink.
        direct = [u for u in self.urls if "flipkart.com" in u.lower()]
        return (direct or self.urls or [None])[0]

    @property
    def key(self) -> str:
        """Dedupe key: product id if visible, else the URL."""
        u = self.url or ""
        m = PID_RE.search(u)
        return m.group(1).upper() if m else u.split("?")[0].rstrip("/")


def _clean_url(u: str) -> str:
    return u.rstrip(".,;:!*_~")


def parse_message(text: str, entity_urls: list[str] | None = None) -> Deal | None:
    """Return a Deal if the message contains a Flipkart link, else None.

    entity_urls: URLs hidden behind hyperlink text (Telegram MessageEntityTextUrl)
    or inline buttons, which don't appear in the plain text.
    """
    text = text or ""
    found = [_clean_url(m.group(0)) for m in URL_RE.finditer(text)]
    for u in entity_urls or []:
        if URL_RE.match(u):
            found.append(_clean_url(u))
    urls = list(dict.fromkeys(found))  # dedupe, keep order
    if not urls:
        return None
    prices = []
    for m in PRICE_RE.finditer(text):
        try:
            prices.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return Deal(text=text, urls=urls, is_100=bool(HUNDRED_PCT_RE.search(text)), prices=prices)


def should_buy(deal: Deal, cfg_filter: dict) -> tuple[bool, str]:
    low = deal.text.lower()
    for word in cfg_filter.get("exclude_keywords", []) or []:
        if re.search(r"\b" + re.escape(word.lower()) + r"\b", low):
            return False, f"excluded keyword '{word}'"
    if cfg_filter.get("require_100_percent", True) and not deal.is_100:
        return False, "not a 100% SuperCoin deal"
    max_price = float(cfg_filter.get("max_price", 0) or 0)
    if max_price and deal.price is not None and deal.price > max_price:
        return False, f"price {deal.price} > max_price {max_price}"
    return True, "ok"
