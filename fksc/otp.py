"""Optional OTP intake: a small HTTP endpoint your phone's SMS forwarder posts to."""
from __future__ import annotations

import asyncio
import hmac
import logging
import re

from aiohttp import web

log = logging.getLogger("fksc.otp")
OTP_RE = re.compile(r"(?:otp|one[\s-]*time[\s-]*password|code)\D{0,60}?(\d{4,8})\b|(\d{6})\D{0,30}(?:is your|otp)", re.I)


def extract_otp(sms: str) -> str | None:
    m = OTP_RE.search(sms or "")
    if not m:
        return None
    return m.group(1) or m.group(2)


class OtpHub:
    def __init__(self):
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    def drain(self):
        while not self.queue.empty():
            self.queue.get_nowait()

    async def wait(self, timeout: float) -> str | None:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def serve(self, host: str, port: int, token: str):
        async def handle(req: web.Request):
            got = req.headers.get("X-Token") or req.query.get("token", "")
            if not token or not hmac.compare_digest(got, token):
                return web.Response(status=403, text="bad token")
            body = await req.text()
            try:
                data = await req.json()
                body = data.get("text") or data.get("message") or data.get("body") or body
            except Exception:
                pass
            otp = extract_otp(body)
            if otp:
                log.info("OTP received via webhook")
                self.queue.put_nowait(otp)
                return web.Response(text="ok")
            return web.Response(status=422, text="no otp found")

        app = web.Application()
        app.router.add_post("/otp", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, host, port).start()
        log.info("OTP webhook listening on http://%s:%s/otp", host, port)
