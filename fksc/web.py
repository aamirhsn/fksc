"""Local dashboard: http://127.0.0.1:8080 (only reachable from your PC by default)."""
from __future__ import annotations

import time

from aiohttp import web

SETTINGS = {  # dashboard field -> config key, type
    "auto_buy": ("scanner.auto_buy", bool),
    "dry_run": ("buyer.dry_run", bool),
    "min_ratio": ("scanner.min_ratio", float),
    "interval_s": ("scanner.interval_s", float),
    "max_order_amount": ("buyer.max_order_amount", float),
    "max_orders_per_day": ("buyer.max_orders_per_day", int),
    "max_spend_per_day": ("buyer.max_spend_per_day", float),
    "payment_method": ("payment.method", str),
}


def make_app(cfg, scanner, state, alerts) -> web.Application:
    async def index(_):
        return web.Response(text=PAGE, content_type="text/html")

    async def api_state(_):
        return web.json_response({
            "now": time.time(),
            "settings": {k: cfg.get(key) for k, (key, _) in SETTINGS.items()},
            "sources": cfg.get("scanner.sources", []) or [],
            "watchlist": cfg.get("scanner.watchlist", []) or [],
            "source_status": scanner.source_status,
            "events": list(scanner.events)[:150],
            "alerts": list(alerts)[:50],
            "today": {"orders": state.data["orders"], "spend": state.data["spend"]},
        })

    async def api_settings(req):
        data = await req.json()
        for k, v in data.items():
            if k in SETTINGS:
                key, typ = SETTINGS[k]
                cfg.set(key, typ(v))
        return web.json_response({"ok": True})

    async def api_sources(req):
        data = await req.json()
        kind = "scanner.watchlist" if data.get("kind") == "watchlist" else "scanner.sources"
        cur = list(cfg.get(kind, []) or [])
        url = (data.get("url") or "").strip()
        if data.get("action") == "add" and url.startswith("https://") and "flipkart.com" in url and url not in cur:
            cur.append(url)
        elif data.get("action") == "remove" and url in cur:
            cur.remove(url)
        cfg.set(kind, cur)
        scanner.sync_sources()
        return web.json_response({"ok": True})

    async def api_buy(req):
        url = (await req.json()).get("url", "").strip()
        if not (url.startswith("https://") and "flipkart.com" in url):
            return web.json_response({"ok": False, "error": "not a flipkart.com URL"}, status=400)
        await scanner.trigger_buy(url)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/settings", api_settings)
    app.router.add_post("/api/sources", api_sources)
    app.router.add_post("/api/buy", api_buy)
    return app


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SuperCoin Sniper</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#16181d;--mut:#6b7280;--line:#e5e7eb;--acc:#2563eb;--ok:#15803d;--bad:#b91c1c;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e8eaee;--mut:#9aa1ad;--line:#2a2f39;--acc:#60a5fa;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24}}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;gap:12px;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
h1{font-size:17px;margin:0}.pill{padding:3px 10px;border-radius:99px;font-weight:600;font-size:12px}
.live{background:var(--bad);color:#fff}.dry{background:var(--warn);color:#000}.off{background:var(--line)}
main{display:grid;grid-template-columns:320px 1fr;gap:16px;padding:16px;max-width:1400px;margin:auto}
@media (max-width:860px){main{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:16px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin:0 0 10px}
label{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:7px 0}
input[type=number],input[type=text],select{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;width:120px}
input[type=text]{width:100%}button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:7px 12px;cursor:pointer;font-weight:600}
button.ghost{background:transparent;color:var(--mut);border:1px solid var(--line);padding:2px 8px}
.row{display:flex;gap:6px;margin-top:6px}.src{display:flex;justify-content:space-between;gap:6px;font-size:12px;padding:6px 0;border-top:1px solid var(--line);word-break:break-all}
.mut{color:var(--mut);font-size:12px}table{width:100%;border-collapse:collapse}td,th{padding:7px 6px;border-top:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:12px;color:var(--mut);border-top:0}.r{text-align:right;font-variant-numeric:tabular-nums}
.big{font-weight:700}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}a{color:var(--acc)}
.tablewrap{overflow-x:auto}
</style></head><body>
<header><h1>SuperCoin Sniper</h1><span id="mode" class="pill off">…</span><span class="mut" id="today"></span>
<span style="flex:1"></span><button id="notif" class="ghost">Enable alerts</button></header>
<main><div>
<section><h2>Controls</h2>
<label>Auto-buy <input type="checkbox" data-k="auto_buy"></label>
<label>Dry run (stop before Pay) <input type="checkbox" data-k="dry_run"></label>
<label>Min coin-back % <input type="number" data-k="min_ratio" data-pct step="5" min="1" max="100"></label>
<label>Reload every (s) <input type="number" data-k="interval_s" step="0.5" min="1"></label>
<label>Max per order ₹ <input type="number" data-k="max_order_amount"></label>
<label>Max orders/day <input type="number" data-k="max_orders_per_day"></label>
<label>Max spend/day ₹ <input type="number" data-k="max_spend_per_day"></label>
<label>Payment <select data-k="payment_method"><option value="card">Saved card</option><option value="cod">Cash on Delivery</option><option value="upi_wait">Stop at payment</option></select></label>
</section>
<section><h2>Pages being watched</h2><div id="sources"></div>
<div class="row"><input type="text" id="newsrc" placeholder="https://www.flipkart.com/…"></div>
<div class="row"><button data-add="sources">Add page</button><button data-add="watchlist" class="ghost">Add as product</button></div></section>
<section><h2>Buy now (manual)</h2><div class="row"><input type="text" id="buyurl" placeholder="Product URL"></div>
<div class="row"><button id="buybtn">Buy</button></div></section>
</div><div>
<section><h2>Alerts</h2><div id="alerts" class="mut">None yet</div></section>
<section><h2>SuperCoin offers found</h2><div class="tablewrap"><table><thead><tr><th>Seen</th><th>Product</th><th class="r">Price</th><th class="r">Coins</th><th class="r">Back</th><th>Status</th></tr></thead><tbody id="events"></tbody></table></div>
<p class="mut" id="empty">Nothing yet. Offers appear here as soon as a watched page shows SuperCoins.</p></section>
</div></main>
<script>
const $=s=>document.querySelector(s), el=(t,c,txt)=>{const e=document.createElement(t);if(c)e.className=c;if(txt!=null)e.textContent=txt;return e};
const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
const ago=t=>{const s=Math.round(Date.now()/1000-t);return s<60?s+'s':s<3600?Math.round(s/60)+'m':Math.round(s/3600)+'h'};
let editing=null, lastAlert=0, ctx;
function beep(){try{ctx=ctx||new AudioContext();const o=ctx.createOscillator();o.frequency.value=880;o.connect(ctx.destination);o.start();o.stop(ctx.currentTime+.25)}catch(e){}}
document.querySelectorAll('[data-k]').forEach(i=>{
  i.addEventListener('focus',()=>editing=i);i.addEventListener('blur',()=>editing=null);
  i.addEventListener('change',()=>{let v=i.type=='checkbox'?i.checked:i.value;if(i.dataset.pct!=null)v=v/100;
    if(i.dataset.k=='dry_run'&&!i.checked&&!confirm('Turn off dry run? Real orders will be paid.')){i.checked=true;return}
    post('/api/settings',{[i.dataset.k]:v}).then(refresh)})});
document.querySelectorAll('[data-add]').forEach(b=>b.onclick=()=>post('/api/sources',{action:'add',kind:b.dataset.add,url:$('#newsrc').value}).then(()=>{$('#newsrc').value='';refresh()}));
$('#buybtn').onclick=()=>post('/api/buy',{url:$('#buyurl').value}).then(r=>r.json()).then(j=>{if(!j.ok)alert(j.error);else $('#buyurl').value='';refresh()});
$('#notif').onclick=()=>Notification.requestPermission().then(()=>{beep();$('#notif').textContent='Alerts on'});
function render(s){
  const st=s.settings;
  document.querySelectorAll('[data-k]').forEach(i=>{if(i===editing)return;let v=st[i.dataset.k];if(i.dataset.pct!=null)v=Math.round(v*100);if(i.type=='checkbox')i.checked=!!v;else i.value=v??''});
  const m=$('#mode');m.className='pill '+(!st.auto_buy?'off':st.dry_run?'dry':'live');m.textContent=!st.auto_buy?'Watching only':st.dry_run?'Auto-buy (dry run)':'AUTO-BUY LIVE';
  $('#today').textContent=`Today: ${s.today.orders} orders · ₹${s.today.spend}`;
  const src=$('#sources');src.replaceChildren();
  for(const [kind,list] of [['sources',s.sources],['watchlist',s.watchlist]]) for(const u of list){
    const ss=s.source_status[u]||{};const d=el('div','src');const l=el('div');l.append(el('div',null,(kind=='watchlist'?'📦 ':'📄 ')+u));
    l.append(el('div',ss.error?'bad':'mut',ss.error||(ss.last?`${ss.scans} scans · last ${ago(ss.last)} ago · ${ss.ms}ms · ${ss.found} with coins`:'starting…')));
    const b=el('button','ghost','✕');b.onclick=()=>post('/api/sources',{action:'remove',kind,url:u}).then(refresh);d.append(l,b);src.append(d)}
  const tb=$('#events');tb.replaceChildren();$('#empty').hidden=s.events.length>0;
  for(const e of s.events){const tr=el('tr');tr.append(el('td','mut',ago(e.t)));
    const td=el('td');const a=el('a',null,e.title||e.key);a.href=e.url;a.target='_blank';td.append(a);if(e.snippet)td.append(el('div','mut',e.snippet));tr.append(td);
    tr.append(el('td','r',e.price!=null?'₹'+e.price:'–'),el('td','r',e.coins??(e.pct?e.pct+'%':'–')));
    tr.append(el('td','r big '+(e.ratio>=st.min_ratio?'ok':''),e.ratio!=null?Math.round(e.ratio*100)+'%':'–'));
    const cls={ordered:'ok',dry_run:'warn',buying:'warn',needs_you:'bad',failed:'bad',alerted:'warn'}[e.status]||'mut';
    const sc=el('td',cls,e.status);if(e.result)sc.append(el('div','mut',e.result+(e.elapsed?` (${e.elapsed}s)`:'')));tr.append(sc);tb.append(tr)}
  const al=$('#alerts');if(s.alerts.length){al.replaceChildren();al.className='';for(const a of s.alerts.slice(0,8)){const d=el('div','src');d.append(el('span',null,a.text));if(a.url){const l=el('a',null,'open');l.href=a.url;l.target='_blank';d.append(l)}d.append(el('span','mut',ago(a.t)));al.append(d)}}
  const newest=s.alerts[0];if(newest&&newest.t>lastAlert){if(lastAlert){beep();if(window.Notification&&Notification.permission=='granted')new Notification('SuperCoin Sniper',{body:newest.text})}lastAlert=newest.t}
}
function refresh(){fetch('/api/state').then(r=>r.json()).then(render).catch(()=>{$('#mode').textContent='disconnected';$('#mode').className='pill off'})}
refresh();setInterval(refresh,1000);
</script></body></html>"""
