"""Public pages that need no login: the online catalogue link (/c/<code>).

Only what the owner chose to share is shown (items, and prices if switched on). An order becomes a Sale Order
in the owner's firm; prices always come from NEXORA, never from the page.
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from app import hop_db
from app.hop_schema import ensure_hop_schema

hop_public_bp = Blueprint("hop_public", __name__)


def _path() -> str:
    from app.routes.hop import _db_path as hop_path

    return hop_path()


def _err(msg: str, code: int = 400):
    return jsonify({"success": False, "error": {"message": msg}}), code


@hop_public_bp.route("/api/v1/public/catalog/<string:token>", methods=["GET"])
def public_catalog(token: str):
    from app import hop_catalog_link

    ensure_hop_schema(_path())
    with hop_db.connect(_path()) as conn:
        link = hop_catalog_link.by_token(conn, token)
        if not link:
            return _err("This catalogue link is not active", 404)
        data = hop_catalog_link.public_catalog(conn, link)
    resp = jsonify({"success": True, "data": data})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@hop_public_bp.route("/api/v1/public/catalog/<string:token>/order", methods=["POST"])
def public_order(token: str):
    from app import hop_catalog_link

    ensure_hop_schema(_path())
    body = request.get_json(silent=True) or {}
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
    try:
        with hop_db.connect(_path()) as conn:
            link = hop_catalog_link.by_token(conn, token)
            if not link:
                return _err("This catalogue link is not active", 404)
            res = hop_catalog_link.place_order(conn, link, body, ip)
    except ValueError as exc:
        return _err(str(exc))
    return jsonify({"success": True, "data": res})


@hop_public_bp.route("/api/v1/public/razorpay-webhook/<string:ws>", methods=["POST"])
def razorpay_webhook(ws: str):
    """The business's Razorpay → us when a payment link is paid. Checked with that business's webhook secret."""
    from app import hop_gov_payments

    ensure_hop_schema(_path())
    try:
        with hop_db.connect(_path()) as conn:
            res = hop_gov_payments.webhook(conn, ws, request.get_data() or b"", request.headers.get("X-Razorpay-Signature") or "")
    except PermissionError:
        return _err("bad signature", 400)
    except ValueError as exc:
        return _err(str(exc), 400)
    return jsonify({"success": True, "data": res})


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Catalogue</title>
<style>
:root{--bg:#f6f7fb;--card:#fff;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--acc:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#0b1220;--card:#111a2e;--ink:#e2e8f0;--muted:#94a3b8;--line:#1e293b;--acc:#60a5fa}}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:18px 16px 8px;max-width:820px;margin:auto}h1{margin:0;font-size:22px}.muted{color:var(--muted);font-size:13px}
main{max-width:820px;margin:auto;padding:0 16px 140px}input,textarea{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font-size:15px}
.cat{margin:18px 0 6px;font-weight:700;font-size:14px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.item{display:flex;gap:10px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:6px 0}
.item .n{flex:1;min-width:0}.item .n b{display:block;overflow-wrap:anywhere}.q{display:flex;align-items:center;gap:6px}
.q button{width:34px;height:34px;border-radius:50%;border:1px solid var(--line);background:var(--bg);color:var(--ink);font-size:18px}
.q span{min-width:26px;text-align:center;font-weight:600}
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:12px 16px}
.bar .in{max-width:820px;margin:auto;display:flex;gap:10px;align-items:center}
.btn{background:var(--acc);color:#fff;border:0;border-radius:10px;padding:12px 18px;font-size:15px;font-weight:600}
.btn:disabled{opacity:.5}#form{display:none;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:14px}
#form label{display:block;margin:8px 0 4px;font-size:13px;color:var(--muted)}.ok{padding:24px;text-align:center}
</style></head><body>
<header><h1 id="firm">Catalogue</h1><div class="muted" id="msg"></div></header>
<main><input id="q" placeholder="Search items…" oninput="draw()"><div id="list"><p class="muted">Loading…</p></div>
<div id="form"><b>Send your order</b>
<label>Your name</label><input id="name" maxlength="80">
<label>Mobile number</label><input id="phone" inputmode="tel" maxlength="14">
<label>Note (optional)</label><textarea id="note" rows="2" maxlength="500"></textarea>
<p class="muted">The shop will confirm prices and delivery with you.</p>
<button class="btn" id="send" onclick="send()">Send order</button> <span id="err" style="color:#dc2626"></span></div></main>
<div class="bar"><div class="in"><div style="flex:1" id="sum">No items picked</div>
<button class="btn" id="go" disabled onclick="document.getElementById('form').style.display='block';document.getElementById('name').focus()">Order</button></div></div>
<script>
const code=location.pathname.split('/').pop();let D=null;const Q={};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=v=>'\\u20B9'+Number(v).toLocaleString('en-IN',{maximumFractionDigits:2});
fetch('/api/v1/public/catalog/'+code).then(r=>r.json()).then(j=>{if(!j.success)throw new Error(j.error.message);D=j.data;
document.title=D.firm;document.getElementById('firm').textContent=D.firm;
document.getElementById('msg').textContent=[D.message,D.phone].filter(Boolean).join(' · ');draw();})
.catch(e=>{document.getElementById('list').innerHTML='<p class="ok">'+esc(e.message)+'</p>';document.querySelector('.bar').style.display='none';});
function draw(){if(!D)return;const q=document.getElementById('q').value.toLowerCase();let cat='',h='';
D.items.filter(i=>!q||(i.name+' '+i.code+' '+i.category).toLowerCase().includes(q)).forEach(i=>{
if(i.category!==cat){cat=i.category;h+='<div class="cat">'+esc(cat)+'</div>';}
h+='<div class="item"><div class="n"><b>'+esc(i.name)+'</b><span class="muted">'+esc([i.code,i.unit].filter(Boolean).join(' · '))+
(D.show_prices&&i.price?' · '+money(i.price)+(i.mrp&&i.mrp>i.price?' <s>'+money(i.mrp)+'</s>':''):'')+'</span></div>'+
'<div class="q"><button onclick="add('+i.id+',-1)">\\u2212</button><span>'+(Q[i.id]||0)+'</span><button onclick="add('+i.id+',1)">+</button></div></div>';});
document.getElementById('list').innerHTML=h||'<p class="muted">No items.</p>';sum();}
function add(id,d){Q[id]=Math.max(0,(Q[id]||0)+d);if(!Q[id])delete Q[id];draw();}
function sum(){const ids=Object.keys(Q);let t=0;ids.forEach(id=>{const i=D.items.find(x=>x.id==id);if(i&&i.price)t+=i.price*Q[id];});
document.getElementById('sum').textContent=ids.length?ids.length+' item(s)'+(D.show_prices&&t?' · '+money(t):''):'No items picked';
document.getElementById('go').disabled=!ids.length;}
function send(){const b=document.getElementById('send');b.disabled=true;document.getElementById('err').textContent='';
fetch('/api/v1/public/catalog/'+code+'/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
name:document.getElementById('name').value,phone:document.getElementById('phone').value,note:document.getElementById('note').value,
lines:Object.keys(Q).map(id=>({id:Number(id),qty:Q[id]}))})}).then(r=>r.json()).then(j=>{
if(!j.success)throw new Error(j.error.message);document.querySelector('main').innerHTML='<div class="ok"><h2>Thank you!</h2><p>Your order '+
esc(j.data.number)+' is sent to '+esc(D.firm)+'. They will contact you soon.</p></div>';document.querySelector('.bar').style.display='none';})
.catch(e=>{document.getElementById('err').textContent=e.message;b.disabled=false;});}
</script></body></html>"""


@hop_public_bp.route("/c/<string:token>", methods=["GET"])
def catalog_page(token: str):
    resp = Response(PAGE, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp
