from fastapi import FastAPI, Form, Request, Header, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from jinja2 import Template
from datetime import datetime
from zoneinfo import ZoneInfo   # ✅ 時區
import uvicorn, threading, queue, time, random, os, sys

# === Dobot SDK（只用 Dashboard 29999） ===
try:
    from dobot_api import DobotApiDashboard
except Exception as _e:
    DobotApiDashboard = None  # 沒裝 SDK 也能啟動網站（僅不觸發手臂）

app = FastAPI()

# ─────────────────────────────────────────────
# 安全設定：Render 要設 ADMIN_PASSWORD；本機可設 DEBUG=1
# ─────────────────────────────────────────────
DEBUG = os.getenv("DEBUG", "0") == "1"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    if DEBUG:
        ADMIN_PASSWORD = "eggadmin"
        print("[DEBUG] 使用預設管理密碼：eggadmin")
    else:
        print("❌ ERROR: ADMIN_PASSWORD 未設定（請到 Render → Environment 新增）", file=sys.stderr)
        raise SystemExit(1)

# ✅ 時區（預設 Asia/Taipei，可改環境變數 APP_TZ）
APP_TZ = ZoneInfo(os.getenv("APP_TZ", "Asia/Taipei"))

# ✅ Dobot 連線設定（只觸發 Studio 腳本）
DOBOT_IP = os.getenv("DOBOT_IP", "192.168.5.1")
DOBOT_PORT = int(os.getenv("DOBOT_PORT", "29999"))
DOBOT_SCRIPT = os.getenv("DOBOT_SCRIPT", "RUN_ALL")   # ← 要與 Studio 腳本名稱一致
ROBOT_RUN_SECONDS = int(os.getenv("ROBOT_RUN_SECONDS", "20"))  # 用於前端進度顯示
ROBOT_API_KEY = os.getenv("ROBOT_API_KEY", ADMIN_PASSWORD)     # 簡單共用你的管理密碼

# ─────────────────────────────────────────────
# 訂單佇列與狀態
# ─────────────────────────────────────────────
orders = []
orders_lock = threading.Lock()
job_q = queue.Queue()
is_worker_running = threading.Event()
stop_event = threading.Event()

# 🔒 機器人觸發鎖（避免重入）
robot_lock = threading.Lock()

# ─────────────────────────────────────────────
# 共用樣式（手機優先）
# ─────────────────────────────────────────────
BASE_CSS = """
<style>
:root{
  --bg:#0b0f1a; --card:#121927; --muted:#8892a6;
  --text:#e6edf7; --accent:#38bdf8; --green:#22c55e; --red:#ef4444; --yellow:#f59e0b;
  --border:#1f2a3a;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Noto Sans,"PingFang TC","Microsoft JhengHei",sans-serif}
.container{max-width:680px;margin:24px auto;padding:20px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px;box-shadow:0 10px 20px rgba(0,0,0,.25)}
h1,h2{margin:0 0 8px}
h1{font-size:28px}
h2{font-size:22px;color:#d7e2f2}
p{color:var(--muted);margin:8px 0 0}
.label{display:block;margin-top:16px;margin-bottom:8px;color:#cbd5e1;font-size:14px}
select,input[type=number]{width:100%;padding:12px 14px;border:1px solid var(--border);background:#0e1522;color:var(--text);border-radius:12px}
.row{display:grid;grid-template-columns:1fr 120px;gap:12px}
button{width:100%;padding:14px 16px;margin-top:16px;border:1px solid rgba(56,189,248,.5);background:linear-gradient(180deg,#0ea5e9,#0284c7);
  color:#fff;font-weight:700;border-radius:12px;cursor:pointer;transition:.2s}
button:hover{filter:brightness(1.05)}
.btn-link{display:inline-block;margin-top:8px;color:var(--accent);text-decoration:none}
.footer{margin-top:16px;color:var(--muted);font-size:12px;text-align:center}
.kbd{font:12px/1 monospace;background:#0e1522;border:1px solid var(--border);border-radius:6px;padding:2px 6px;color:#cbd5e1}
.hr{height:1px;background:var(--border);margin:16px 0;border:none}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:#cbd5e1;background:#0e1522;border:1px solid var(--border);padding:4px 8px;border-radius:999px}
.table{width:100%;border-collapse:separate;border-spacing:0}
.th,.td{padding:10px 12px;border-bottom:1px solid var(--border);font-size:14px}
.th{color:#9fb2c8;text-align:left}
.id{color:#9bd2ff}
.status{font-weight:700}
.st-queued{color:var(--yellow)}
.st-processing{color:#60a5fa}
.st-done{color:var(--green)}
.bar{height:8px;background:#0e1522;border:1px solid var(--border);border-radius:999px;overflow:hidden}
.bar>span{display:block;height:100%;background:linear-gradient(90deg,#22c55e,#16a34a)}
.center{display:flex;justify-content:center}
.success{color:#b7ffc7}
.warn{color:#ffe9a6}
.error{color:#ffb4b4}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.logo .dot{width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 16px var(--accent)}
</style>
"""

# ─────────────────────────────────────────────
# HTML 模板
# ─────────────────────────────────────────────
INDEX_HTML = Template("""
<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>雞蛋糕點餐</title>
<link rel="icon" href="/favicon.svg">
{{ css|safe }}
<div class="container">
  <div class="logo"><span class="dot"></span><h1>雞蛋糕點餐</h1></div>
  <div class="card">
    <h2>選擇品項</h2>
    <p>請在下方選擇口味與數量，我們將為您現烤雞蛋糕 🍰</p>
    <form method="post" action="/order">
      <label class="label">口味</label>
      <select name="sku">
        <option value="classic">原味</option>
        <option value="choco">巧克力</option>
      </select>
      <div class="row">
        <div>
          <label class="label">數量</label>
          <input type="number" name="qty" min="1" value="1" required>
        </div>
      </div>
      <button type="submit">送出訂單</button>
  </div>
</div>
""")

THANKS_HTML = Template("""
<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>已收到訂單</title>
<link rel="icon" href="/favicon.svg">
{{ css|safe }}
<div class="container">
  <div class="logo"><span class="dot"></span><h1>訂單已成立</h1></div>
  <div class="card">
    {% if o %}
      <p class="success">✅ 已收到訂單 <span class="id">#{{ o["id"] }}</span></p>
      <div class="hr"></div>
      <p>口味：<b>{{ o["sku"] }}</b>　數量：<b>{{ o["qty"] }}</b></p>
      <p class="warn">請保持此頁面開啟，取餐時報訂單編號即可。</p>
    {% else %}
      <p class="error">找不到這筆訂單。</p>
    {% endif %}
    <a class="btn-link" href="/">〈 返回點餐</a>
  </div>
</div>
""")

ADMIN_HTML = Template("""
<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>管理</title>
<meta http-equiv="refresh" content="3">
<link rel="icon" href="/favicon.svg">
{{ css|safe }}
<div class="container">
  <div class="logo"><span class="dot"></span><h1>後台管理</h1></div>
  <div class="card">
    <div class="badge">背景工人：<b>{{ '製作中' if is_running else '待命' }}</b></div>
    <div class="hr"></div>
    <table class="table">
      <thead>
        <tr>
          <th class="th" style="width:90px">編號</th>
          <th class="th" style="width:120px">口味</th>
          <th class="th" style="width:90px">數量</th>
          <th class="th" style="width:120px">狀態</th>
          <th class="th">進度</th>
          <th class="th" style="width:150px">時間</th>
        </tr>
      </thead>
      <tbody>
      {% for o in orders %}
        <tr>
          <td class="td"><span class="id">#{{ o["id"] }}</span></td>
          <td class="td">{{ o["sku"] }}</td>
          <td class="td">{{ o["qty"] }}</td>
          <td class="td status"><span class="st-{{ o['status'] }}">{{ o["status"] }}</span></td>
          <td class="td">
            {% if o.get("progress") is not none %}
              <div class="bar"><span style="width:{{ o['progress'] }}%"></span></div>
            {% else %}
              <span class="muted">—</span>
            {% endif %}
          </td>
          <td class="td">{{ o["ts"] }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
</div>
""")

# ─────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────
def _now() -> str:
    """台北時間"""
    return datetime.now(APP_TZ).strftime("%H:%M:%S")

def _find(oid: int):
    with orders_lock:
        return next((o for o in orders if o["id"] == oid), None)

def _set(oid: int, **fields):
    with orders_lock:
        o = next((x for x in orders if x["id"] == oid), None)
        if o:
            o.update(fields)
            o["ts"] = _now()

# ─────────────────────────────────────────────
# Dobot 觸發（B 模式）
# ─────────────────────────────────────────────
def trigger_robot_script(script_name: str = DOBOT_SCRIPT):
    """只透過 29999 觸發 Studio 腳本；加鎖避免重入"""
    if DobotApiDashboard is None:
        raise RuntimeError("dobot_api 未安裝或匯入失敗")

    with robot_lock:
        dash = DobotApiDashboard(DOBOT_IP, DOBOT_PORT)
        # 建議流程：清錯誤 → 觸發（Enable 可在 Lua 內做）
        dash.ClearError()
        time.sleep(0.1)
        dash.RunScript(script_name)
        return f"Triggered {script_name}"

# 模擬/顯示進度：用 ROBOT_RUN_SECONDS 切成 100 份
def run_one_batch(order: dict):
    # 1) 先觸發手臂腳本
    msg = trigger_robot_script(DOBOT_SCRIPT)
    # 2) 以預估時間顯示進度（不連 30004；穩定後可改拉 DO / 回饋口）
    total = max(1, ROBOT_RUN_SECONDS)
    for i in range(total):
        time.sleep(1)
        prog = int((i + 1) / total * 100)
        _set(order["id"], progress=prog)
    _set(order["id"], status="done", progress=100)

# 背景工人：逐筆處理
def worker():
    while not stop_event.is_set():
        try:
            oid = job_q.get(timeout=0.3)
        except queue.Empty:
            is_worker_running.clear()
            continue
        is_worker_running.set()
        _set(oid, status="processing", progress=0)
        od = _find(oid)
        try:
            if od:
                run_one_batch(od)
        except Exception as e:
            _set(oid, status=f"error: {e}")
        finally:
            job_q.task_done()
    is_worker_running.clear()

threading.Thread(target=worker, daemon=True).start()

# ─────────────────────────────────────────────
# 路由（網站）
# ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML.render(css=BASE_CSS)

@app.post("/order")
def order(sku: str = Form(...), qty: int = Form(...)):
    with orders_lock:
        oid = (orders[0]["id"] + 1) if orders else 1
        orders.insert(0, {
            "id": oid, "sku": sku, "qty": int(qty),
            "ts": _now(), "status": "queued", "progress": None
        })
    job_q.put(oid)
    return RedirectResponse(url=f"/thanks?oid={oid}", status_code=303)

@app.get("/thanks", response_class=HTMLResponse)
def thanks(oid: int):
    o = _find(oid)
    return THANKS_HTML.render(o=o, css=BASE_CSS)

ADMIN_HEADERS = {"X-Robots-Tag": "noindex, nofollow, noarchive"}

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    pw = request.query_params.get("pw")
    if pw != ADMIN_PASSWORD:
        return HTMLResponse("<h3>Not Found</h3>", status_code=404)
    with orders_lock:
        snapshot = list(orders)
    html = ADMIN_HTML.render(orders=snapshot, is_running=is_worker_running.is_set(), css=BASE_CSS)
    return HTMLResponse(html, headers=ADMIN_HEADERS)

@app.get("/api/orders")
def api_orders():
    with orders_lock:
        return JSONResponse(list(orders))

@app.get("/favicon.svg")
def favicon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#38bdf8"/><stop offset="1" stop-color="#0284c7"/></linearGradient></defs>
    <rect width="64" height="64" rx="12" fill="#0b0f1a"/>
    <circle cx="20" cy="22" r="6" fill="url(#g)"/>
    <rect x="12" y="30" width="40" height="18" rx="9" fill="#121927" stroke="#1f2a3a"/>
    <circle cx="24" cy="39" r="5" fill="#22c55e"/>
    <circle cx="40" cy="39" r="5" fill="#f59e0b"/>
    </svg>"""
    return Response(content=svg, media_type="image/svg+xml")

@app.get("/healthz")
def healthz():
    return {"ok": True, "queue": job_q.qsize(), "running": is_worker_running.is_set()}

@app.on_event("shutdown")
def on_shutdown():
    stop_event.set()

# ─────────────────────────────────────────────
# 受保護的 Robot API（可手動觸發/暫停/停止）
# ─────────────────────────────────────────────
def _auth(x_api_key: str | None) -> None:
    if x_api_key != ROBOT_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/robot/start")
def robot_start(x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    msg = trigger_robot_script(DOBOT_SCRIPT)
    return {"ok": True, "msg": msg}

@app.post("/robot/pause")
def robot_pause(x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    if DobotApiDashboard is None:
        raise HTTPException(status_code=500, detail="dobot_api 未安裝")
    with robot_lock:
        dash = DobotApiDashboard(DOBOT_IP, DOBOT_PORT)
        dash.PauseScript()
    return {"ok": True, "msg": "Paused"}

@app.post("/robot/stop")
def robot_stop(x_api_key: str | None = Header(default=None)):
    _auth(x_api_key)
    if DobotApiDashboard is None:
        raise HTTPException(status_code=500, detail="dobot_api 未安裝")
    with robot_lock:
        dash = DobotApiDashboard(DOBOT_IP, DOBOT_PORT)
        dash.StopScript()
    return {"ok": True, "msg": "Stopped"}

# ─────────────────────────────────────────────
# 本機啟動
# ─────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
