"""Runs ios/fksc-sniper.user.js in Chromium against mocked Flipkart endpoints.

Set CHROMIUM_PATH if Playwright's bundled browser isn't installed.
"""
import asyncio
import json
import os
import pathlib

import pytest

pw = pytest.importorskip("playwright.async_api")

SCRIPT = (pathlib.Path(__file__).parent.parent / "ios" / "fksc-sniper.user.js").read_text()
PID, LID = "ACCABC123XYZ9", "LSTACCABC123XYZ9QWERTY"
PRODUCT = f"https://www.flipkart.com/steel-bottle/p/itmabc?pid={PID}"


def product_html(coins, price=349, notify=False):
    action = "NOTIFY_ME" if notify else "BUY_NOW"
    return f"""<html><head><title>Steel Bottle 1L - Buy Steel Bottle Online</title>
<script type="application/ld+json">[{{"@type":"Product","name":"Steel Bottle","offers":{{"price":{price}}}}}]</script></head>
<body><a href="/steel-bottle/p/itmabc?pid={PID}">Steel Bottle 1L</a>
<div>{coins} SuperCoins cashback on this product</div>
<script>window.x={{"productActionButtonType":"{action}","listing":"{LID}"}}</script>Buy Now</body></html>"""


async def run(dry_run: bool):
    calls, state = [], {"coins": 10}
    cors = {"Access-Control-Allow-Origin": "https://www.flipkart.com", "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "content-type,x-user-agent,accept", "Access-Control-Allow-Methods": "GET,POST"}

    async def handle(route):
        req = route.request
        url = req.url
        if req.method == "OPTIONS":
            return await route.fulfill(status=204, headers=cors)
        calls.append((req.method, url, req.post_data))
        j = lambda body, status=200: route.fulfill(status=status, headers=cors, content_type="application/json", body=json.dumps(body))
        if "1.rome.api.flipkart.com" in url:
            return await j({"ERROR_CODE": 2000, "ERROR_MESSAGE": "DC Change", "RESPONSE": {"id": "2"}}, 406)
        if "2.rome.api.flipkart.com/api/5/cart" in url:
            return await j({"RESPONSE": {}, "ERROR_CODE": None})
        if "2.rome.api.flipkart.com/api/5/checkout" in url:
            return await j({"RESPONSE": {"ok": 1}})
        if "paymentToken" in url:
            return await j({"RESPONSE": {"getPaymentToken": {"token": "tok-1"}}})
        if "payments.flipkart.com/fkpay" in url:
            return await j({"response_status": "SUCCESS", "primary_action": {"target": "https://bank.example/acs", "http_method": "POST", "parameters": {"PaReq": "x"}}})
        if url.startswith("https://bank.example/acs"):
            return await route.fulfill(content_type="text/html", body="<html><body>Enter OTP</body></html>")
        if "/api/1/contacts" in url:
            return await j({"RESPONSE": [{"pincode": "560001", "isDefault": True}]})
        if "/p/itmabc" in url:
            return await route.fulfill(content_type="text/html", body=product_html(state["coins"]))
        return await route.fulfill(content_type="text/html", body="<html><body>home</body></html>")

    async with pw.async_playwright() as p:
        browser = await p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        page = await browser.new_page()
        await page.route("**/*", handle)
        await page.add_init_script("document.addEventListener('DOMContentLoaded',()=>{" + SCRIPT + "});")
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
        await page.goto(PRODUCT)
        await page.wait_for_function("window.__fksc")
        info = await page.evaluate(f"window.__fksc.analyze(document.documentElement.innerHTML, '{PID}')")
        assert info["coins"] == 10 and info["price"] == 349 and info["lid"] == LID and not info["notifyMe"]

        settings = {"autoBuy": True, "dryRun": dry_run, "minPct": 50, "intervalS": 0.3, "maxPrice": 2000, "maxOrdersPerDay": 5}
        await page.evaluate("""s => { localStorage.setItem('fksc_settings', JSON.stringify(s));
            localStorage.setItem('fksc_card', JSON.stringify({number:'4111111111111111', expiry:'12/29', cvv:'123'}));
            localStorage.setItem('fksc_watch', JSON.stringify([{pid:'%s', lid:'', url:'%s'}])); }""" % (PID, PRODUCT), settings)
        await page.reload()
        await page.wait_for_function("window.__fksc")
        await page.evaluate("window.__fksc.start()")
        await asyncio.sleep(1.5)
        assert not any("rome" in c[1] for c in calls), "must not buy below threshold"
        state["coins"] = 349  # deal goes live: 100%
        for _ in range(40):
            await asyncio.sleep(0.25)
            if dry_run and any("/api/5/checkout" in c[1] for c in calls):
                await asyncio.sleep(0.5)
                break
            if page.url.startswith("https://bank.example"):
                break
        final_url = page.url
        await browser.close()
    return calls, final_url


def test_live_flow_reaches_bank_otp():
    calls, final_url = asyncio.run(run(dry_run=False))
    paths = [c[1] for c in calls if "rome" in c[1] or "payments" in c[1]]
    assert final_url.startswith("https://bank.example/acs")
    assert paths[0].startswith("https://1.rome") and "2.rome.api.flipkart.com/api/5/cart" in paths[1]
    pay = next(c for c in calls if "fkpay" in c[1])
    body = json.loads(pay[2])
    assert pay[1].startswith("https://2.payments.flipkart.com") and body["card_number"] == "4111111111111111"
    assert body["expiry_month"] == "12" and body["expiry_year"] == "29" and body["token"] == "tok-1"
    cart = json.loads(next(c for c in calls if "2.rome.api.flipkart.com/api/5/cart" in c[1])[2])
    assert cart["cartContext"][LID]["productId"] == PID


def test_dry_run_stops_before_payment():
    calls, final_url = asyncio.run(run(dry_run=True))
    assert any("/api/5/checkout" in c[1] for c in calls)
    assert not any("paymentToken" in c[1] or "fkpay" in c[1] for c in calls)
    assert final_url.startswith("https://www.flipkart.com")
