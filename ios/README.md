# iPhone: SuperCoin Sniper for Safari

`fksc-sniper.user.js` runs inside Safari on your iPhone, in your logged-in Flipkart tab. It does not
need a computer or any server.

## What it does
1. **Watches** the products on your watchlist by fetching each product page from your own
   Flipkart session every few seconds.
2. **Detects** Flipkart's banner "N SuperCoins cashback on this product" (or "…cashback with
   Plus"). It compares N with the selling price from the page's product data, and treats the deal
   as live when N ÷ price ≥ your minimum % and the button isn't "Notify Me". This is the same
   check the Tapworthy app uses, and the offer differs by account, so it checks yours.
3. **Buys** by calling Flipkart's own APIs directly, without clicking through pages:
   `cart` → `checkout` → `paymentToken` → `fkpay paywithdetails` (credit card) → your bank's OTP
   page. If Flipkart answers "DC Change", it switches to the data centre Flipkart names.
4. **OTP:** iOS shows the code from Messages above the keyboard; tap it and submit.

## Install (once)
1. Install **Userscripts** from the App Store (free, open source).
2. Open the app and choose a folder for scripts, e.g. iCloud Drive › Userscripts.
3. iPhone **Settings › Apps › Safari › Extensions › Userscripts**: turn it on and allow it on
   `flipkart.com`. On older iOS this is Settings › Safari › Extensions.
4. Put `fksc-sniper.user.js` in that folder. Download it from the repo on GitHub, then use
   Files › Save to the Userscripts folder.
5. In **Safari** (not the Flipkart app), log in at flipkart.com and make sure you have a default
   delivery address.

## Use
1. Open any flipkart.com page and tap the blue **⚡** button.
2. Build the watchlist:
   - on a product page: **+ Watch this product**
   - on a search, category or offer page: **+ Watch all on this page**
3. Under **Credit card**, save the card number, expiry and CVV. They're kept in the Userscripts
   extension's storage on this phone, which Flipkart's page scripts can't read, and sent only to
   Flipkart's payment API.
4. Set **Min SuperCoin %** (50–100), **Max price** and **Max orders / day**. Turn **Auto-buy** on.
   Leave **Dry run** on at first: it adds to cart and creates the checkout, then stops before
   paying.
5. Tap **Start**. The ⚡ turns green, and the watchlist shows each product's coins, price and %.
   When a deal hits you'll hear a beep and the panel opens.
6. Once a dry run has worked on a real deal, turn Dry run off.

## Keeping it running
iOS only runs the script while that Safari tab is **on screen**:
- keep the phone plugged in, with Safari open on the Flipkart tab
- the script asks iOS to keep the screen on (iOS 16.4+). If the screen still locks, set
  Settings › Display & Brightness › Auto-Lock › Never while sniping
- turn Low Power Mode off
- use Wi-Fi: each check downloads a full product page (roughly 0.3–1 MB), so 10 products every 4s
  is several GB an hour

Only one tab runs the watcher at a time. If Flipkart returns 429/403, the script pauses for
60 seconds.

## Limits
- It only watches products you add. Tapworthy's server watches a large catalogue for you, and this
  script has no such feed.
- The Flipkart endpoints come from Tapworthy's app (v0.2.21) and were tested against mocks, not
  live Flipkart. Try a dry run first.
- The script runs as a Safari *content script*, so its requests to `rome.api.flipkart.com` and
  `payments.flipkart.com` depend on Flipkart allowing calls from www.flipkart.com, as its own site
  does. If you see "add to cart failed (HTTP 0)", change `@inject-into content` to
  `@inject-into page`. That works, but the card then sits in the page's localStorage, which
  Flipkart's scripts can read.
- Flipkart's terms prohibit automated purchasing, and accounts flagged as bots have had
  SuperCoin orders cancelled. You use this at your own risk.
