// ==UserScript==
// @name         FK SuperCoin Sniper
// @namespace    fksc
// @version      0.1.1
// @description  Watches Flipkart products for high SuperCoin cashback and checks out through Flipkart's own APIs, up to the bank OTP.
// @match        https://www.flipkart.com/*
// @grant        GM.getValue
// @grant        GM.setValue
// @inject-into  content
// @run-at       document-end
// @noframes
// ==/UserScript==

(async function () {
  'use strict';
  if (window.__fkscLoaded) return;
  window.__fkscLoaded = true;

  // ---------------------------------------------------------------- storage
  const GM_ = typeof GM !== 'undefined' ? GM : {
    // Fallback for testing outside a userscript manager (NOT private: page scripts can read it).
    getValue: async (k, d) => { try { const v = localStorage.getItem('fksc_' + k); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
    setValue: async (k, v) => { localStorage.setItem('fksc_' + k, JSON.stringify(v)); },
  };
  const DEFAULTS = {
    minPct: 50,          // buy when SuperCoins / price * 100 >= this
    intervalS: 4,        // seconds between checks of the same product
    parallel: 3,         // product pages fetched at once
    autoBuy: false,
    dryRun: true,        // stop after checkout is created, before paying
    maxPrice: 2000,      // never pay more than this per order
    maxOrdersPerDay: 5,
  };
  const S = Object.assign({}, DEFAULTS, await GM_.getValue('settings', {}));
  let watch = await GM_.getValue('watch', []);           // [{pid, lid, url, title, last}]
  let card = await GM_.getValue('card', null);           // {number, expiry, cvv}
  let day = await GM_.getValue('day', { date: '', orders: 0, bought: {} });
  const saveSettings = () => GM_.setValue('settings', S);
  const saveWatch = () => GM_.setValue('watch', watch);
  const today = () => new Date().toISOString().slice(0, 10);
  function rollDay() { if (day.date !== today()) day = { date: today(), orders: 0, bought: {} }; }

  // ---------------------------------------------------------------- detection
  // Product-page banner Flipkart shows for SuperCoin cashback offers on the logged-in account.
  const BANNER_RES = [
    /(\d[\d,]*)\s*SuperCoins?\s+cashback\s+on\s+this\s+product/i,
    /(\d[\d,]*)\s*SuperCoins?\s+cashback\s+with(?:\s+Plus)?/i,
  ];
  const PRICE_RES = [
    /"@type"\s*:\s*"Product"[\s\S]{0,8000}?"price"\s*:\s*"?(\d+(?:\.\d+)?)"?/i,
    /"ppd"\s*:\s*\{[^}]{0,240}?"finalPrice"\s*:\s*(\d+)/,
  ];
  const ACTION_RE = /"productActionButtonType"\s*:\s*"([^"]+)"/g;
  const num = (s) => Number(String(s || '').replace(/,/g, ''));

  function analyze(html, pid) {
    let coins = 0;
    for (const re of BANNER_RES) { const m = html.match(re); if (m) { coins = num(m[1]); break; } }
    let price = 0;
    for (const re of PRICE_RES) { const m = html.match(re); if (m) { price = Number(m[1]); break; } }
    let notifyMe = false;
    for (const m of html.matchAll(ACTION_RE)) if (m[1].startsWith('NOTIFY_ME')) notifyMe = true;
    if (!notifyMe && /Notify\s*Me/i.test(html) && !/Buy\s*Now/i.test(html)) notifyMe = true;
    const t = html.match(/<title>([^<]{0,200})/i);
    if (!pid) pid = pidFromHtml(html);
    const lidM = pid && html.match(new RegExp('LST' + pid + '[A-Z0-9]+', 'i'));
    return {
      pid,
      coins, price, notifyMe,
      pct: coins && price ? Math.round((coins / price) * 1000) / 10 : 0,
      title: t ? t[1].replace(/\s*[-|:]\s*Buy.*$/i, '').trim() : '',
      lid: lidM ? lidM[0].toUpperCase() : '',
    };
  }

  // Product id (pid) is in the URL most of the time, but links from Google, shares and some
  // mobile pages only have the /p/itm… path, so fall back to the page's own data.
  function pidFromHtml(html) {
    const res = [
      /<link[^>]+rel=["']canonical["'][^>]+[?&]pid=([A-Z0-9]{10,20})/i,
      /<meta[^>]+og:url[^>]+[?&]pid=([A-Z0-9]{10,20})/i,
      /"productId"\s*:\s*"([A-Z0-9]{10,20})"/,
      /"pid"\s*:\s*"([A-Z0-9]{10,20})"/,
      /[?&]pid=([A-Z0-9]{10,20})/,
    ];
    for (const re of res) { const m = String(html || '').match(re); if (m) return m[1].toUpperCase(); }
    return '';
  }

  function parseProductUrl(href) {
    try {
      const u = new URL(href, location.href);
      if (!/(^|\.)flipkart\.com$/i.test(u.hostname)) return null;
      const itmM = u.pathname.match(/\/p\/(itm[0-9a-z]+)/i);
      const pid = (u.searchParams.get('pid') || '').toUpperCase();
      if (!pid && !itmM) return null;
      const lid = (u.searchParams.get('lid') || '').toUpperCase();
      const path = itmM ? u.pathname : '/product/p/itm';
      const qs = [pid && 'pid=' + pid, lid && 'lid=' + lid].filter(Boolean).join('&');
      return { pid, lid, itm: itmM ? itmM[1].toLowerCase() : '', url: 'https://www.flipkart.com' + path + (qs ? '?' + qs : '') };
    } catch (_) { return null; }
  }
  const keyOf = (w) => w.pid || w.itm;

  function productFromThisPage() {
    const html = document.documentElement.innerHTML;
    let p = parseProductUrl(location.href);
    if (!p || !p.pid) {
      const canon = document.querySelector('link[rel="canonical"]');
      const fromCanon = canon && parseProductUrl(canon.href);
      p = p || fromCanon;
      if (!p) {
        // not a /p/ URL at all, but the page may still be a product page
        const pid = pidFromHtml(html);
        if (!pid || !/Buy\s*Now|Add\s*to\s*cart|Notify\s*Me/i.test(document.body.innerText)) return null;
        p = { pid: '', lid: '', itm: '', url: '' };
      }
      p.pid = p.pid || (fromCanon && fromCanon.pid) || pidFromHtml(html);
      if (!p.pid) return null;
      p.url = 'https://www.flipkart.com' + (p.itm ? new URL(p.url || location.href).pathname : '/product/p/itm') + '?pid=' + p.pid;
    }
    if (!p.lid) p.lid = analyze(html, p.pid).lid;
    if (p.lid && !/[?&]lid=/.test(p.url)) p.url += '&lid=' + p.lid;
    return p;
  }

  // ---------------------------------------------------------------- Flipkart APIs
  let dc = '1';
  const fkUa = () => (/FKUA\//.test(navigator.userAgent) ? navigator.userAgent : navigator.userAgent + ' FKUA/msite/0.0.3/msite/Mobile');
  function dcFromPage() {
    const m = document.documentElement.innerHTML.match(/"dc(?:Id)?"\s*:\s*"(\d{1,3})"/);
    return m ? m[1] : '';
  }
  async function rome(method, path, body) {
    dc = dcFromPage() || dc;
    let last = { ok: false, status: 0, text: '' };
    for (let hop = 0; hop < 3; hop++) {
      const r = await fetch('https://' + dc + '.rome.api.flipkart.com' + path, {
        method, credentials: 'include', cache: 'no-store',
        headers: { Accept: '*/*', 'Content-Type': 'application/json', 'X-User-Agent': fkUa() },
        body: body == null ? undefined : JSON.stringify(body),
      });
      const text = await r.text();
      last = { ok: r.ok, status: r.status, text };
      if (r.ok) break;
      // 406 "DC Change": retry on the data centre Flipkart names
      let next = '';
      try {
        const j = JSON.parse(text);
        if (r.status === 406 && (String(j.ERROR_CODE) === '2000' || /DC Change/i.test(j.ERROR_MESSAGE || ''))) {
          next = String((j.RESPONSE && j.RESPONSE.id) || (j.META_INFO && j.META_INFO.dcInfo && j.META_INFO.dcInfo.id) || '');
        }
      } catch (_) {}
      if (!/^[A-Za-z0-9-]{1,16}$/.test(next) || next === dc) break;
      dc = next;
      log('Flipkart DC → ' + dc);
    }
    return last;
  }
  const hasError = (text) => /"ERROR_CODE"\s*:\s*(?!null)/.test(text || '');

  async function hasAddress() {
    const r = await fetch('/api/1/contacts', { credentials: 'include', cache: 'no-store', headers: { 'X-User-Agent': fkUa() } });
    if (r.status === 401 || r.status === 403) return 'Not logged in to Flipkart';
    const t = await r.text();
    return /"pin(?:c|C)ode"\s*:\s*"?\d{6}/.test(t) ? '' : 'No saved delivery address on Flipkart';
  }

  function submitAction(action) {
    const form = document.createElement('form');
    form.method = String(action.http_method || action.httpMethod || 'POST').toUpperCase() === 'GET' ? 'GET' : 'POST';
    form.action = action.target;
    form.style.display = 'none';
    for (const [k, v] of Object.entries(action.parameters || {})) {
      const i = document.createElement('input');
      i.type = 'hidden'; i.name = k; i.value = v == null ? '' : String(v);
      form.appendChild(i);
    }
    document.body.appendChild(form);
    form.submit();
  }

  async function payWithCard() {
    const tok = await rome('GET', '/api/3/checkout/paymentToken');
    let token = '';
    try { token = JSON.parse(tok.text).RESPONSE.getPaymentToken.token; } catch (_) {}
    if (!token) return { ok: false, error: 'payment token missing (HTTP ' + tok.status + ')' };
    const d = String(card.expiry).replace(/\D/g, '');
    const body = {
      payment_instrument: 'CREDIT', token,
      card_number: String(card.number).replace(/\D/g, ''),
      expiry_month: d.slice(0, 2), expiry_year: d.slice(-2),
      cvv: String(card.cvv).replace(/\D/g, ''),
      device_information: {
        colorDepth: screen.colorDepth || 24, javaEnabled: false, javaScriptEnabled: true,
        language: navigator.language || 'en-IN', screenHeight: screen.height, screenWidth: screen.width,
        timeDifference: -new Date().getTimezoneOffset(),
      },
      device_capabilities: { read_sms: false, phonepe_sdk: false, juspay_sdk: false, nda_enabled: false, upi_enabled: false },
      is_diff_shown_to_user: false,
    };
    let lastErr = '';
    for (let attempt = 0; attempt < 3; attempt++) {
      const r = await fetch('https://' + dc + '.payments.flipkart.com/fkpay/api/v3/payments/paywithdetails?instrument=CREDIT', {
        method: 'POST', credentials: 'include', cache: 'no-store',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const text = await r.text();
      let j = null; try { j = JSON.parse(text); } catch (_) {}
      if (j && j.primary_action && j.primary_action.target) return { ok: true, action: j.primary_action };
      lastErr = (j && j.messages && j.messages[0] && (j.messages[0].message || j.messages[0].status_code)) || text.slice(0, 160) || 'HTTP ' + r.status;
      await sleep(220 * (attempt + 1));
    }
    return { ok: false, error: String(lastErr) };
  }

  async function checkout(w, info) {
    const t0 = performance.now();
    const step = (m) => { setStatus(m); log(m); };
    rollDay();
    if (day.orders >= S.maxOrdersPerDay) return fail('daily order cap reached');
    if (info.price > S.maxPrice) return fail('price ₹' + info.price + ' > max ₹' + S.maxPrice);
    if (!card && !S.dryRun) return fail('no card saved');
    const lid = w.lid || info.lid;
    if (!w.pid) return fail('product id not found for ' + (w.title || w.url));
    if (!lid) return fail('listing id (lid) not found for ' + w.pid);

    step('Adding to cart…');
    const ctx = { [lid]: { productId: w.pid, quantity: 1, cashifyDiscountApplied: false, vulcanDiscountApplied: false, payWithEMISelected: false } };
    const cart = await rome('POST', '/api/5/cart', { cartContext: ctx, pageType: 'ProductPage' });
    if (!cart.ok) return fail('add to cart failed (HTTP ' + cart.status + ')');

    step('Creating checkout…');
    let co = await rome('POST', '/api/5/checkout?loginFlow=false&view=FLIPKART', {
      checkoutType: 'PHYSICAL', cartRequest: { cartContext: { [lid]: { quantity: 1, payWithEMISelected: false } } },
    });
    if (!co.ok || hasError(co.text)) {
      co = await rome('POST', '/api/5/checkout?infoLevel=order_summary', {
        checkoutType: 'PHYSICAL', cartRequest: { pageType: 'ProductPage', cartContext: ctx },
      });
    }
    if (!co.ok || hasError(co.text)) return fail('checkout failed (HTTP ' + co.status + ')');
    const ms = Math.round(performance.now() - t0);

    if (S.dryRun) {
      alertUser('DRY RUN: checkout created for ' + (w.title || w.pid) + ' in ' + ms + 'ms. Stopped before paying.');
      return { ok: true, dry: true };
    }
    step('Paying ₹' + info.price + ' with card …' + String(card.number).slice(-4));
    const paid = await payWithCard();
    if (paid.ok) {
      day.orders += 1; day.bought[keyOf(w)] = Date.now(); await GM_.setValue('day', day);
      alertUser('Bank OTP page opening (' + (Math.round(performance.now() - t0)) + 'ms). Use the SMS code suggestion above the keyboard.');
      await stopRunning();
      submitAction(paid.action);
      return { ok: true };
    }
    alertUser('Card API failed (' + paid.error + '). Opening Flipkart checkout so you can pay manually.');
    await stopRunning();
    location.href = 'https://www.flipkart.com/viewcheckout?loginFlow=false&checkoutInitiated=true';
    return { ok: false };

    function fail(msg) { alertUser('Not bought: ' + msg); return { ok: false, error: msg }; }
  }

  // ---------------------------------------------------------------- watcher loop
  let running = false, busy = false, wakeLock = null, audio = null, checks = 0;
  const TAB_ID = Math.random().toString(36).slice(2);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function checkOne(w) {
    const r = await fetch(w.url, { credentials: 'include', cache: 'no-store', headers: { Accept: 'text/html' } });
    if (r.status === 429 || r.status === 403) throw Object.assign(new Error('rate limited (' + r.status + ')'), { backoff: true });
    const html = await r.text();
    const info = analyze(html, w.pid);
    if (!w.pid && info.pid) { w.pid = info.pid; saveWatch(); }
    checks++;
    w.last = { t: Date.now(), pct: info.pct, coins: info.coins, price: info.price, notifyMe: info.notifyMe };
    if (!w.title && info.title) { w.title = info.title; saveWatch(); }
    if (!w.lid && info.lid) { w.lid = info.lid; saveWatch(); }
    return info;
  }

  async function loop() {
    let i = 0;
    while (running) {
      await GM_.setValue('lock', { tab: TAB_ID, t: Date.now() });
      if (!watch.length) { setStatus('Watchlist is empty'); await sleep(1000); continue; }
      const due = watch.filter((w) => !w.last || Date.now() - w.last.t >= S.intervalS * 1000);
      const batch = [];
      for (let k = 0; k < Math.min(S.parallel, due.length); k++) batch.push(due[(i + k) % due.length]);
      i += batch.length;
      if (!batch.length) { await sleep(200); continue; }
      const results = await Promise.allSettled(batch.map(checkOne));
      for (let k = 0; k < batch.length; k++) {
        const res = results[k];
        if (res.status === 'rejected') {
          log('Error: ' + res.reason.message);
          if (res.reason.backoff) { setStatus('Flipkart is rate-limiting: pausing 60s'); await sleep(60000); }
          continue;
        }
        const info = res.value, w = batch[k];
        const hit = info.pct >= S.minPct && !info.notifyMe;
        if (!hit || !running || busy) continue;
        rollDay();
        if (day.bought[keyOf(w)]) continue;
        log('HIT ' + info.pct + '% (' + info.coins + ' SC / ₹' + info.price + ') ' + (w.title || w.pid));
        beep();
        if (!S.autoBuy) { alertUser(info.pct + '% SuperCoins live: ' + (w.title || w.pid)); day.bought[keyOf(w)] = Date.now(); continue; }
        busy = true;
        try { await checkout(w, info); } catch (e) { alertUser('Checkout error: ' + e.message); }
        busy = false;
      }
      render();
    }
  }

  async function start() {
    const lock = await GM_.getValue('lock', null);
    if (lock && lock.tab !== TAB_ID && Date.now() - lock.t < 5000) { alertUser('Already running in another tab'); return; }
    const addrErr = await hasAddress().catch(() => '');
    if (addrErr) { alertUser(addrErr); return; }
    if (S.autoBuy && !S.dryRun && !card) { alertUser('Save a card first, or turn on dry run'); return; }
    running = true;
    try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume(); } catch (_) {}
    try { wakeLock = await navigator.wakeLock.request('screen'); } catch (_) {}
    setStatus('Running');
    render();
    loop();
  }
  async function stopRunning() {
    running = false;
    try { wakeLock && (await wakeLock.release()); } catch (_) {}
    wakeLock = null;
    await GM_.setValue('lock', null);
    setStatus('Stopped');
    render();
  }
  document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState === 'visible' && running && !wakeLock) {
      try { wakeLock = await navigator.wakeLock.request('screen'); } catch (_) {}
    }
  });

  function beep() {
    try {
      const o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = 880; g.gain.value = 0.3; o.connect(g); g.connect(audio.destination);
      o.start(); o.stop(audio.currentTime + 0.4);
    } catch (_) {}
  }

  // ---------------------------------------------------------------- UI (shadow DOM, isolated from Flipkart CSS)
  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;z-index:2147483647;right:12px;bottom:76px;';
  document.documentElement.appendChild(host);
  const root = host.attachShadow({ mode: 'closed' });
  root.innerHTML = `
<style>
  :host{all:initial}
  *{box-sizing:border-box;font:14px/1.4 -apple-system,system-ui,sans-serif}
  .fab{width:52px;height:52px;border-radius:26px;border:0;background:#2563eb;color:#fff;font-size:22px;box-shadow:0 4px 14px rgba(0,0,0,.3)}
  .fab.on{background:#16a34a}
  .panel{display:none;position:fixed;inset:auto 8px 8px 8px;max-height:82vh;overflow:auto;background:#fff;color:#111;border-radius:14px;
         box-shadow:0 10px 40px rgba(0,0,0,.35);padding:14px}
  @media (prefers-color-scheme:dark){.panel{background:#1c1c1e;color:#f2f2f7}.row input{background:#2c2c2e;color:#f2f2f7;border-color:#3a3a3c}.w{border-color:#3a3a3c}}
  .panel.open{display:block}
  h3{margin:0 0 6px;font-size:16px}
  .status{font-weight:600;margin:4px 0 10px}
  .row{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:6px 0}
  .row input[type=number],.row input[type=text],.row input[type=password]{width:110px;padding:6px 8px;border:1px solid #d1d5db;border-radius:8px}
  .card input{width:100% !important}
  button.b{border:0;border-radius:9px;padding:9px 12px;background:#2563eb;color:#fff;font-weight:600}
  button.g{background:transparent;color:#6b7280;border:1px solid #d1d5db}
  button.r{background:#dc2626}
  .btns{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
  .w{border-top:1px solid #e5e7eb;padding:7px 0;display:flex;gap:8px;justify-content:space-between;font-size:13px}
  .w .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:62vw}
  .mut{color:#6b7280;font-size:12px}.hit{color:#16a34a;font-weight:700}
  .log{font:11px/1.35 ui-monospace,monospace;white-space:pre-wrap;max-height:140px;overflow:auto;color:#6b7280}
  details{margin:8px 0}summary{font-weight:600}
</style>
<button class="fab" id="fab">⚡</button>
<div class="panel" id="panel">
  <div class="row"><h3>SuperCoin Sniper</h3><button class="b g" id="close">✕</button></div>
  <div class="status" id="status">Stopped</div>
  <div class="btns">
    <button class="b" id="toggle">Start</button>
    <button class="b g" id="addThis">+ Watch this product</button>
    <button class="b g" id="addPage">+ Watch all on this page</button>
  </div>
  <div class="row"><span>Auto-buy</span><input type="checkbox" id="autoBuy"></div>
  <div class="row"><span>Dry run (stop before paying)</span><input type="checkbox" id="dryRun"></div>
  <div class="row"><span>Min SuperCoin %</span><input type="number" id="minPct" min="1" max="100"></div>
  <div class="row"><span>Check each product every (s)</span><input type="number" id="intervalS" min="1" step="0.5"></div>
  <div class="row"><span>Max price ₹</span><input type="number" id="maxPrice"></div>
  <div class="row"><span>Max orders / day</span><input type="number" id="maxOrdersPerDay"></div>
  <details><summary>Credit card <span class="mut" id="cardInfo"></span></summary>
    <div class="card">
      <div class="row"><input type="text" id="cNum" inputmode="numeric" autocomplete="off" placeholder="Card number"></div>
      <div class="row"><input type="text" id="cExp" inputmode="numeric" autocomplete="off" placeholder="MM/YY"><input type="password" id="cCvv" inputmode="numeric" autocomplete="off" placeholder="CVV"></div>
      <div class="btns"><button class="b" id="saveCard">Save card</button><button class="b g" id="delCard">Remove</button></div>
      <div class="mut">Stored in the Userscripts extension's storage on this iPhone only. Sent only to Flipkart's payment API.</div>
    </div>
  </details>
  <details open><summary>Watchlist (<span id="wcount">0</span>)</summary><div id="list"></div></details>
  <details><summary>Log</summary><div class="log" id="log"></div></details>
</div>`;
  const $ = (id) => root.getElementById(id);
  const logLines = [];
  function log(m) {
    logLines.unshift(new Date().toLocaleTimeString() + ' ' + m);
    logLines.length = Math.min(logLines.length, 80);
    $('log').textContent = logLines.join('\n');
  }
  function setStatus(m) { $('status').textContent = m; }
  function alertUser(m) {
    log(m); setStatus(m); beep();
    $('panel').classList.add('open');
    document.title = '⚡ ' + m.slice(0, 40);
  }

  function render() {
    $('fab').classList.toggle('on', running);
    $('toggle').textContent = running ? 'Stop' : 'Start';
    $('toggle').className = 'b' + (running ? ' r' : '');
    for (const k of ['autoBuy', 'dryRun']) $(k).checked = !!S[k];
    for (const k of ['minPct', 'intervalS', 'maxPrice', 'maxOrdersPerDay']) if (root.activeElement !== $(k)) $(k).value = S[k];
    $('cardInfo').textContent = card ? '•••• ' + String(card.number).slice(-4) : '(none)';
    $('wcount').textContent = watch.length;
    const list = $('list');
    list.replaceChildren();
    for (const w of watch) {
      const row = document.createElement('div'); row.className = 'w';
      const left = document.createElement('div');
      const t = document.createElement('div'); t.className = 't'; t.textContent = w.title || w.pid || w.itm;
      const s = document.createElement('div');
      if (w.last) {
        s.className = w.last.pct >= S.minPct ? 'hit' : 'mut';
        s.textContent = (w.last.coins ? w.last.coins + ' SC · ' : 'no SC banner · ') + (w.last.price ? '₹' + w.last.price : '') +
          (w.last.pct ? ' · ' + w.last.pct + '%' : '') + (w.last.notifyMe ? ' · notify-me' : '') +
          ' · ' + Math.round((Date.now() - w.last.t) / 1000) + 's ago';
      } else { s.className = 'mut'; s.textContent = 'not checked yet'; }
      left.append(t, s);
      const del = document.createElement('button'); del.className = 'b g'; del.textContent = '✕';
      del.onclick = () => { watch = watch.filter((x) => keyOf(x) !== keyOf(w)); saveWatch(); render(); };
      row.append(left, del); list.append(row);
    }
    if (running) setStatus('Running · ' + watch.length + ' products · ' + checks + ' checks' + (S.autoBuy ? (S.dryRun ? ' · auto-buy (dry run)' : ' · AUTO-BUY LIVE') : ' · alert only'));
  }

  function addProduct(p, title) {
    if (!p || watch.some((w) => (p.pid && w.pid === p.pid) || (p.itm && w.itm === p.itm))) return false;
    watch.push({ pid: p.pid, lid: p.lid, itm: p.itm, url: p.url, title: title || '' });
    return true;
  }

  $('fab').onclick = () => { $('panel').classList.toggle('open'); render(); };
  $('close').onclick = () => $('panel').classList.remove('open');
  $('toggle').onclick = () => (running ? stopRunning() : start());
  $('addThis').onclick = () => {
    const p = productFromThisPage();
    if (!p) return alertUser('Couldn\u2019t find a product id on this page (' + location.pathname.slice(0, 60) + '). Scroll until the price and Buy Now button show, then try again.');
    log(addProduct(p, document.title.replace(/\s*[-|:]\s*Buy.*$/i, '')) ? 'Added ' + p.pid + (p.lid ? '' : ' (listing id will be read on first check)') : 'Already watching');
    saveWatch(); render();
  };
  $('addPage').onclick = () => {
    let n = 0;
    for (const a of document.querySelectorAll('a[href*="/p/"]')) {
      if (addProduct(parseProductUrl(a.href), (a.innerText || '').split('\n')[0].slice(0, 100))) n++;
    }
    log('Added ' + n + ' products from this page'); saveWatch(); render();
  };
  for (const k of ['autoBuy', 'dryRun']) $(k).onchange = () => {
    if (k === 'dryRun' && !$(k).checked && !confirm('Turn off dry run? Deals will be paid for with your card.')) { $(k).checked = true; return; }
    S[k] = $(k).checked; saveSettings(); render();
  };
  for (const k of ['minPct', 'intervalS', 'maxPrice', 'maxOrdersPerDay']) $(k).onchange = () => {
    const v = Number($(k).value); if (v > 0) { S[k] = v; saveSettings(); } render();
  };
  $('saveCard').onclick = async () => {
    const number = $('cNum').value.replace(/\D/g, ''), expiry = $('cExp').value.trim(), cvv = $('cCvv').value.replace(/\D/g, '');
    if (number.length < 13 || !/^\d{2}\s*\/?\s*\d{2,4}$/.test(expiry) || cvv.length < 3) return alertUser('Check card number, MM/YY and CVV');
    card = { number, expiry, cvv };
    await GM_.setValue('card', card);
    $('cNum').value = $('cExp').value = $('cCvv').value = '';
    log('Card saved'); render();
  };
  $('delCard').onclick = async () => { card = null; await GM_.setValue('card', null); render(); };

  setInterval(() => { if (running) render(); }, 2000);
  render();

  // test hook (only used by the automated tests)
  window.__fksc = { analyze, parseProductUrl, productFromThisPage, start, stopRunning, get watch() { return watch; }, S };
})();
