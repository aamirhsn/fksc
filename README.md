# fksc — Flipkart 100% SuperCoin deal sniper

Watches Telegram deal channels (e.g. `Flipkart_scdeals_PranayCC`) and, as soon as a post with a
**100% SuperCoins** Flipkart link appears, drives your already-logged-in Flipkart session through
**Buy Now → address → order summary → payment** in a real browser.

## Why this is faster than your phone alert
- It listens as a **Telegram user client** (MTProto push via Telethon), not through phone
  notifications. Posts arrive in well under a second; each log line shows the measured lag.
- A Flipkart tab stays warm (cookies, TLS, DNS) so the product page opens immediately.
- No human in the loop until the bank OTP, and that can be automated too (below).

## Setup (on your own PC, in India)
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium        # or set buyer.browser_channel: chrome to use installed Chrome
cp config.example.yaml config.yaml
cp .env.example .env               # fill TG_API_ID / TG_API_HASH from https://my.telegram.org
python -m fksc login               # Telegram phone+code, then log in to Flipkart in the opened window
```
In that Flipkart window, **save your credit card on Flipkart** (Flipkart stores it tokenised). Put
its last 4 digits in `payment.card_last4` and the CVV in `.env` as `FK_CARD_CVV`. Card details
never go into `config.yaml` or the code, and `.env` is gitignored.

## Use
```bash
python -m fksc history -n 50       # replay recent channel posts through the filter (no buying)
python -m fksc buy <product-url>   # run the buy flow once on any item (dry_run stops before Pay)
python -m fksc run                 # watch the channel and buy
```
`buyer.dry_run: true` (the default) goes all the way to the payment page and stops before paying.
Watch it work on a few real deals, then set it to `false`.

## Payment options (`payment.method`)
| method | what happens | needs you? |
|---|---|---|
| `card` | picks saved card ending `card_last4`, fills CVV, clicks **Pay ₹X**, then fills the OTP | only for the OTP, unless the OTP webhook is on |
| `cod` | Cash on Delivery if offered: no card or OTP, so it's the quickest way to lock an order | sometimes a captcha |
| `upi_wait` | stops on the payment page and alerts you | yes |

Indian card payments always require a bank OTP (an RBI rule), so no tool can skip it.

### Hands-free OTP
Set `otp_webhook.enabled: true` and `OTP_WEBHOOK_TOKEN` in `.env`. Install any Android
"SMS to webhook" forwarder app on your phone, filter it to your bank's sender ID, and have it POST
the SMS text to `http://<your-PC-LAN-IP>:8765/otp?token=<OTP_WEBHOOK_TOKEN>`. The bot pulls the
OTP out of the SMS, types it into the bank page and submits it.

## Safety rails
- `buyer.max_order_amount`: read from the actual **Pay ₹X** button before clicking, so a wrong
  link or wrong price is never paid.
- `buyer.max_orders_per_day`, `buyer.max_spend_per_day`: kept in `state.json` across restarts.
- `filter.require_100_percent`, `filter.max_price`, `filter.exclude_keywords`.
- Each product (by `pid`) is bought at most once.
- If anything is unexpected, the tab is left open and you get a Telegram "Saved Messages" alert,
  so you can finish the purchase yourself.

## When a step stops working
Flipkart changes its pages often. Every step matches on visible button text
(`BUY_NOW_TEXT`, `CHECKOUT_ADVANCE_TEXT`, `PAY_BUTTON_TEXT` at the top of `fksc/buyer.py`), and with
`debug_screenshots: true` each step is saved to `./debug/`. Check the last screenshot and adjust
the matching pattern.

## Caveats
- Flipkart's terms prohibit automated purchasing. Flipkart has cancelled SuperCoin orders and
  restricted accounts it flagged as bots. You run this at your own risk.
- The checkout flow is tested against a local mock of Flipkart's steps, not the live site.
  Start with `dry_run` and `python -m fksc buy <url>`.
- A deal that lasts 2–3 seconds may still sell out between Buy Now and Pay. The bot reports that
  as `sold out / unavailable`.
