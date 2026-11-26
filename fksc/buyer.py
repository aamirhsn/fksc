"""Drives a logged-in Flipkart session through Buy Now -> checkout -> payment.

Flipkart changes its markup often, so every step matches on visible text with
several fallbacks instead of CSS class names. If a step stops matching, run with
debug_screenshots on, look at ./debug, and adjust the text lists at the top.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import BrowserContext, Frame, Page, async_playwright

from .config import Config, env
from .otp import OtpHub
from .state import DailyState

log = logging.getLogger("fksc.buyer")

SOLD_OUT_TEXT = re.compile(r"sold out|currently unavailable|coming soon|out of stock|notify me", re.I)
BUY_NOW_TEXT = re.compile(r"^\s*buy\s*now\s*$|^\s*buy\s*at\s*₹", re.I)
CHECKOUT_ADVANCE_TEXT = re.compile(
    r"^\s*(deliver here|continue|accept\s*&\s*continue|proceed to pay(ment)?|place order|confirm)\s*$", re.I
)
PAY_BUTTON_TEXT = re.compile(r"^\s*pay\s*₹?\s*[\d,]+|^\s*pay\s*now|^\s*confirm order", re.I)
SUCCESS_URL = re.compile(r"orderresponse|order-?confirm|ordersuccess|thankyou", re.I)
SUCCESS_TEXT = re.compile(r"order (placed|confirmed)|thank you for your order|your order has been placed", re.I)
AMOUNT_RE = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")


@dataclass
class BuyResult:
    ok: bool
    stage: str
    message: str
    amount: float | None = None
    elapsed: float = 0.0


class Buyer:
    def __init__(self, cfg: Config, state: DailyState, otp: OtpHub, notify):
        self.cfg = cfg
        self.state = state
        self.otp = otp
        self.notify = notify  # async callable(str)
        self.ctx: BrowserContext | None = None
        self._pw = None
        self._sem = asyncio.Semaphore(int(cfg.get("buyer.max_parallel", 2)))
        self._pay_lock = asyncio.Lock()  # one payment/OTP at a time
        self.timeout = int(cfg.get("buyer.step_timeout_ms", 8000))
        self.debug_dir = cfg.path("buyer.debug_dir", "debug")

    # ---------- lifecycle ----------
    async def start(self, headless: bool | None = None):
        self._pw = await async_playwright().start()
        if headless is None:
            headless = bool(self.cfg.get("buyer.headless", False))
        self.ctx = await self._pw.chromium.launch_persistent_context(
            str(self.cfg.path("buyer.profile_dir", "browser_profile")),
            headless=headless,
            # "chrome" uses your installed Google Chrome (looks less like a bot); None = bundled Chromium
            channel=self.cfg.get("buyer.browser_channel") or None,
            executable_path=self.cfg.get("buyer.browser_path") or None,
            viewport={"width": 1366, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Keep one tab warm on flipkart.com so DNS/TLS/cookies are hot when a deal lands.
        page = self.ctx.pages[0] if self.ctx.pages else await self.ctx.new_page()
        try:
            await page.goto("https://www.flipkart.com/", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.warning("warm-up load failed: %s", e)
        asyncio.create_task(self._keep_warm(page))

    async def _keep_warm(self, page: Page):
        while True:
            await asyncio.sleep(240)
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass

    async def stop(self):
        if self.ctx:
            await self.ctx.close()
        if self._pw:
            await self._pw.stop()

    async def is_logged_in(self) -> bool:
        page = await self.ctx.new_page()
        try:
            await page.goto("https://www.flipkart.com/account/?rd=0&link=home_account", wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
            return "login" not in page.url.lower()
        finally:
            await page.close()

    # ---------- helpers ----------
    async def _shot(self, page: Page, tag: str):
        if not self.cfg.get("buyer.debug_screenshots", True):
            return
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(self.debug_dir / f"{int(time.time()*1000)}_{tag}.png"))
        except Exception:
            pass

    async def _click_text(self, page: Page, pattern: re.Pattern, timeout: int) -> str | None:
        """Click the first visible button/link whose text matches. Returns the text clicked."""
        loc = (
            page.get_by_role("button", name=pattern)
            .or_(page.get_by_role("link", name=pattern))
            .or_(page.locator("button:visible, a:visible, div[role=button]:visible, span:visible").filter(has_text=pattern))
        )
        target = loc.first
        await target.wait_for(state="visible", timeout=timeout)
        text = (await target.inner_text()).strip()
        await target.click(timeout=timeout)
        return text

    def _frames(self, page: Page) -> list[Frame]:
        return [page.main_frame] + [f for f in page.frames if f != page.main_frame]

    # ---------- main flow ----------
    async def buy(self, url: str, min_ratio: float | None = None) -> BuyResult:
        async with self._sem:
            t0 = time.monotonic()
            page = await self.ctx.new_page()
            page.set_default_timeout(self.timeout)
            try:
                res = await self._buy(page, url, min_ratio)
            except Exception as e:
                await self._shot(page, "error")
                res = BuyResult(False, "exception", f"{type(e).__name__}: {e}")
            res.elapsed = time.monotonic() - t0
            log.info("result %s in %.2fs: %s / %s", "OK" if res.ok else "FAIL", res.elapsed, res.stage, res.message)
            # Leave the tab open if it's waiting on a human or failed, so you can take over.
            if res.ok and res.stage == "ordered":
                await page.close()
            return res

    async def _buy(self, page: Page, url: str, min_ratio: float | None = None) -> BuyResult:
        await page.goto(url, wait_until="domcontentloaded")
        await self._shot(page, "product")

        # 0. Optional: re-check the coin offer on the product page itself (adds ~0.1-0.5s)
        if min_ratio and self.cfg.get("buyer.verify_coins_on_product_page", False):
            from .detect import detect
            off = detect(url, await page.locator("body").inner_text())
            if not off or off.ratio < min_ratio:
                return BuyResult(False, "coins", f"coin offer not confirmed on product page ({off.ratio if off else 0:.0%})")

        # 1. Buy Now (race against "sold out")
        try:
            await self._click_text(page, BUY_NOW_TEXT, self.timeout)
        except Exception:
            body = await page.locator("body").inner_text()
            if SOLD_OUT_TEXT.search(body):
                return BuyResult(False, "product", "sold out / unavailable")
            return BuyResult(False, "product", f"Buy Now button not found on {page.url}")

        # 2. Walk the checkout steps until a payment page appears
        for step in range(8):
            if await self._on_payment_page(page):
                break
            if SUCCESS_URL.search(page.url):
                break
            try:
                clicked = await self._click_text(page, CHECKOUT_ADVANCE_TEXT, 4000)
                log.info("checkout: clicked %r", clicked)
                await page.wait_for_load_state("domcontentloaded")
            except Exception:
                await page.wait_for_timeout(300)
        await self._shot(page, "payment")
        if not await self._on_payment_page(page):
            body = await page.locator("body").inner_text()
            if SOLD_OUT_TEXT.search(body):
                return BuyResult(False, "checkout", "went out of stock during checkout")
            return BuyResult(False, "checkout", f"could not reach payment page ({page.url})")

        # 3. Payment
        async with self._pay_lock:
            method = self.cfg.get("payment.method", "card")
            if method == "cod":
                return await self._pay_cod(page)
            if method == "upi_wait":
                await self.notify(f"🟡 Deal at payment page, approve manually now: {url}")
                return await self._wait_for_success(page, self.cfg.get("payment.otp_timeout_s", 120), manual=True)
            return await self._pay_card(page)

    async def _on_payment_page(self, page: Page) -> bool:
        if re.search(r"payment|/pay\b|checkout.*pay", page.url, re.I):
            return True
        for f in self._frames(page):
            try:
                if await f.get_by_text(re.compile(r"credit\s*/\s*debit|net\s*banking|cash on delivery|payment options", re.I)).first.is_visible():
                    return True
            except Exception:
                pass
        return False

    async def _read_pay_amount(self, page: Page) -> tuple[object, float | None]:
        for f in self._frames(page):
            loc = f.get_by_role("button", name=PAY_BUTTON_TEXT).or_(
                f.locator("button").filter(has_text=PAY_BUTTON_TEXT)
            ).first
            try:
                await loc.wait_for(state="visible", timeout=2500)
            except Exception:
                continue
            text = await loc.inner_text()
            m = AMOUNT_RE.search(text) or AMOUNT_RE.search(await f.locator("body").inner_text())
            amount = float(m.group(1).replace(",", "")) if m else None
            return loc, amount
        return None, None

    def _check_caps(self, amount: float | None) -> str | None:
        if amount is None:
            return "could not read payable amount; refusing to pay blind"
        cap = float(self.cfg.get("buyer.max_order_amount", 0) or 0)
        if cap and amount > cap:
            return f"amount ₹{amount} > max_order_amount ₹{cap}"
        ok, why = self.state.can_spend(
            amount,
            int(self.cfg.get("buyer.max_orders_per_day", 0) or 0),
            float(self.cfg.get("buyer.max_spend_per_day", 0) or 0),
        )
        return None if ok else why

    async def _select_card(self, page: Page):
        last4 = str(self.cfg.get("payment.card_last4", "")).strip()
        # Open the card section
        for f in self._frames(page):
            try:
                await f.get_by_text(re.compile(r"credit\s*/\s*debit|^cards?$|saved cards", re.I)).first.click(timeout=2000)
                break
            except Exception:
                continue
        if self.cfg.get("payment.use_new_card"):
            await self._fill_new_card(page)
            return
        if last4:
            for f in self._frames(page):
                try:
                    await f.get_by_text(re.compile(rf"(\*|x|•|\s){{2,}}\s*{last4}\b|ending\s*(in\s*)?{last4}", re.I)).first.click(timeout=2500)
                    break
                except Exception:
                    continue
        cvv = env("FK_CARD_CVV")
        if cvv:
            await self._fill_any(page, ['input[name*="cvv" i]', 'input[id*="cvv" i]', 'input[placeholder*="cvv" i]'], cvv)

    async def _fill_new_card(self, page: Page):
        await self._fill_any(page, ['input[name*="cardNumber" i]', 'input[autocomplete="cc-number"]', 'input[placeholder*="card number" i]'], env("FK_CARD_NUMBER", True))
        exp = env("FK_CARD_EXPIRY", True)
        mm, yy = exp.split("/")
        if not await self._fill_any(page, ['input[autocomplete="cc-exp"]', 'input[placeholder*="MM / YY" i]', 'input[placeholder*="valid thru" i]'], exp):
            await self._fill_any(page, ['select[name*="month" i]', 'input[name*="month" i]'], mm)
            await self._fill_any(page, ['select[name*="year" i]', 'input[name*="year" i]'], yy)
        await self._fill_any(page, ['input[name*="cvv" i]', 'input[autocomplete="cc-csc"]'], env("FK_CARD_CVV", True))
        name = env("FK_CARD_NAME")
        if name:
            await self._fill_any(page, ['input[autocomplete="cc-name"]', 'input[name*="name" i]'], name)

    async def _fill_any(self, page: Page, selectors: list[str], value: str) -> bool:
        for f in self._frames(page):
            for sel in selectors:
                loc = f.locator(sel).first
                try:
                    if await loc.is_visible():
                        if await loc.evaluate("e => e.tagName") == "SELECT":
                            await loc.select_option(value)
                        else:
                            await loc.fill(value)
                        return True
                except Exception:
                    continue
        return False

    async def _pay_card(self, page: Page) -> BuyResult:
        await self._select_card(page)
        pay_btn, amount = await self._read_pay_amount(page)
        await self._shot(page, "before_pay")
        if pay_btn is None:
            await self.notify(f"🟡 Reached payment but no Pay button found — finish manually: {page.url}")
            return BuyResult(False, "payment", "pay button not found; tab left open")
        if why := self._check_caps(amount):
            return BuyResult(False, "payment", f"blocked by safety cap: {why}", amount)
        if self.cfg.get("buyer.dry_run", True):
            await self.notify(f"🧪 DRY RUN stopped before paying ₹{amount}. Tab left open.")
            return BuyResult(True, "dry_run", "stopped before pay (dry_run)", amount)

        self.otp.drain()
        await pay_btn.click()
        await self.notify(f"🔴 Paying ₹{amount} — OTP needed NOW (auto-fill {'on' if self.cfg.get('otp_webhook.enabled') else 'off'})")
        return await self._otp_and_confirm(page, amount)

    async def _pay_cod(self, page: Page) -> BuyResult:
        for f in self._frames(page):
            try:
                await f.get_by_text(re.compile(r"cash on delivery", re.I)).first.click(timeout=2500)
                break
            except Exception:
                continue
        else:
            return BuyResult(False, "payment", "Cash on Delivery not offered for this item")
        pay_btn, amount = await self._read_pay_amount(page)
        if pay_btn is None:
            return BuyResult(False, "payment", "COD confirm button not found")
        if why := self._check_caps(amount):
            return BuyResult(False, "payment", f"blocked by safety cap: {why}", amount)
        if self.cfg.get("buyer.dry_run", True):
            await self.notify(f"🧪 DRY RUN (COD) stopped before confirming ₹{amount}.")
            return BuyResult(True, "dry_run", "stopped before confirm (dry_run)", amount)
        await pay_btn.click()
        # Flipkart sometimes shows a captcha for COD; that needs you.
        return await self._wait_for_success(page, 60, amount=amount, manual=True)

    async def _otp_and_confirm(self, page: Page, amount: float | None) -> BuyResult:
        timeout = float(self.cfg.get("payment.otp_timeout_s", 120))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self._is_success(page):
                return self._ordered(amount)
            otp = await self.otp.wait(1.0)
            if otp:
                if await self._fill_any(page, [
                    'input[autocomplete="one-time-code"]', 'input[name*="otp" i]', 'input[id*="otp" i]',
                    'input[placeholder*="otp" i]', 'input[type="password"]', 'input[type="tel"]',
                ], otp):
                    await self._shot(page, "otp_filled")
                    for f in self._frames(page):
                        try:
                            await f.get_by_role("button", name=re.compile(r"submit|confirm|verify|pay|continue", re.I)).first.click(timeout=2000)
                            break
                        except Exception:
                            continue
                else:
                    await self.notify(f"OTP {otp} received but no OTP field found — type it in the browser")
        return await self._wait_for_success(page, 5, amount=amount)

    async def _is_success(self, page: Page) -> bool:
        if SUCCESS_URL.search(page.url):
            return True
        try:
            return await page.get_by_text(SUCCESS_TEXT).first.is_visible()
        except Exception:
            return False

    async def _wait_for_success(self, page: Page, seconds: float, amount=None, manual=False) -> BuyResult:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if await self._is_success(page):
                return self._ordered(amount)
            await asyncio.sleep(0.5)
        await self._shot(page, "no_confirmation")
        return BuyResult(False, "confirm", "no order confirmation seen" + (" (needs you in the browser)" if manual else ""), amount)

    def _ordered(self, amount) -> BuyResult:
        self.state.record_order(amount or 0)
        return BuyResult(True, "ordered", "order placed", amount)
