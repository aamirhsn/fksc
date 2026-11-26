# fksc — Flipkart SuperCoin-back deal watcher and auto-buyer

Runs on your own computer. It keeps a logged-in Flipkart browser open, reloads the pages you choose,
finds products whose SuperCoin offer is at least `min_ratio` of the price (default 50%), and
runs checkout immediately. A local dashboard controls everything.

> Status: scanner, checkout, OTP webhook and dashboard are tested end to end against a local mock
> of Flipkart. They have not been run against live Flipkart yet. A faster checkout that calls
> Flipkart's APIs directly is planned (see "Next").

## Setup
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium        # or keep buyer.browser_channel: chrome to use installed Chrome
cp config.example.yaml config.yaml
cp .env.example .env               # card CVV, OTP webhook token
python -m fksc login               # log in to Flipkart once in the window that opens
```

## Use
```bash
python -m fksc discover            # browse normally; every SuperCoin offer seen is logged to debug/discover.jsonl
python -m fksc buy <product-url>   # run the checkout once (dry_run stops before Pay)
python -m fksc run                 # watch pages + dashboard at http://127.0.0.1:8080
```
From the dashboard you can turn auto-buy and dry run on or off, set the minimum coin-back %, the
reload interval and spending caps, and add or remove watched pages and products. It also has a
manual Buy box, and it shows browser notifications with a sound.

## How detection works
For each watched page, on every reload:
1. Every product card on the page (each `/p/` link plus its card text) is checked for
   "Earn N SuperCoins", "N SuperCoins back", "N% SuperCoins" and similar wording. Coins ÷ price
   gives the coin-back ratio. Offers about *spending* coins ("using 50 SuperCoins") are ignored.
2. The JSON responses Flipkart's own page loads are searched the same way.

## Payment and OTP
`payment.method`: `card` (saved card matched by last 4 digits + CVV from `.env`), `cod`, or
`upi_wait` (stops on the payment page). Indian card payments always need a bank OTP. With
`otp_webhook.enabled`, the bank SMS can be forwarded to
`POST http://<pc>:8765/otp?token=<OTP_WEBHOOK_TOKEN>`. On iPhone, set up a Shortcuts automation
("Message contains OTP" → Get contents of URL) to do the forwarding.

## Safety rails
- `max_order_amount` is checked against the real "Pay ₹X" button before paying.
- Daily order and spend caps are kept in `state.json`.
- A product is not bought again within `rebuy_cooldown_h`.
- `dry_run` is on by default.
- The dashboard listens on 127.0.0.1 only, because anyone who can open it can trigger purchases.

## Next
Port the faster checkout used by the Tapworthy Android app: product-page banner check, then
`cart` → `checkout` → `paymentToken` → `fkpay paywithdetails` → bank OTP, all as direct API
calls from the logged-in page. Expect about 1s from detection to OTP.

## Caveats
Flipkart's terms prohibit automated purchasing, and Flipkart has cancelled SuperCoin orders and
restricted accounts it flagged as bots. You use this at your own risk.
