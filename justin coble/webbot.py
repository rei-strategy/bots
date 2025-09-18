#!/usr/bin/env python3
# Local/Container web UI for your preforeclosure-bot
# - Shows only 8 sources
# - Runs: python main.py <CLI_KEY>  (keys match your main.py)
# - Works on your Mac (with .venv) or inside Docker (no venv required)
# - Reliable stop; logs via SSE; status polling every 2s

import os, shlex, signal, subprocess, threading, time, queue, traceback
from pathlib import Path
from flask import Flask, Response, request, redirect, url_for, jsonify

# ===== Paths (auto-detect) =====
APP_ROOT = Path(__file__).resolve().parent
CANDIDATES = [
    Path(os.environ.get("BOT_DIR")) if os.environ.get("BOT_DIR") else None,
    Path("/app"),  # Docker default
    Path("/Users/georgesmacbook/Documents/CTO/bots/justins bots/preforeclosure-bot"),  # Mac path
    APP_ROOT,
]
BOT_DIR = next((p for p in CANDIDATES if p and p.exists()), APP_ROOT)

VENV_BIN = BOT_DIR / ".venv" / "bin"
ENTRY    = "main.py"
TITLE    = "Bot Launcher"

# ===== Rows: (Display Name, FILEBASE, CLI_KEY) =====
# FILEBASE must match operators/<FILEBASE>_ops.py
# CLI_KEY must match what main.py expects (from your working reference)
ROWS = [
    ("Reuben Lublin",       "alaw_net",            "reuben_lublin"),
    ("Brock & Scott",       "brockandscott",       "brock_and_scott"),
    ("Aldridge Pites",      "aldridgepite",        "aldridge_pites"),
    ("Foreclosure Hotline", "foreclosurehotline",  "foreclosure_hotline"),
    ("ServiceLink Auction", "servicelink_auction", "servicelink_auction"),
    ("Auction.com",         "auction_com",         "auction_com"),
    ("Xome.com",            "zome_com",            "zome_com"),
    ("Logs PowerBI Report", "logs_powerbi",        "logs_powerbi"),
]

DISPLAY_ORDER = [r[0] for r in ROWS]
DISPLAY_TO_FILEBASE = {d:f for (d,f,k) in ROWS}
DISPLAY_TO_CLIKEY   = {d:k for (d,f,k) in ROWS}

# ===== State =====
app = Flask(__name__)
_proc = None
_current_display = None
_running_lock = threading.Lock()
_log_q = queue.Queue()
_last_lines = []
MAX_LAST = 1000

# Per-row status
OPER_STATE = {d: {"running": False, "last_ok": None, "last_duration": None, "last_ts": None, "available": False}
              for d in DISPLAY_ORDER}

# ===== Helpers =====
def env_ok():
    # In Docker we don't have a venv; just verify code exists
    return BOT_DIR.is_dir() and (BOT_DIR / ENTRY).exists()

def operator_file_exists(display: str) -> bool:
    base = DISPLAY_TO_FILEBASE[display]
    return (BOT_DIR / "operators" / f"{base}_ops.py").is_file()

def refresh_availability():
    for d in DISPLAY_ORDER:
        OPER_STATE[d]["available"] = operator_file_exists(d)

def _python_exe() -> str:
    vpy = VENV_BIN / "python"
    return str(vpy) if vpy.exists() else "python"

def make_command(cli_key: str) -> list:
    # cd -> optional venv -> system or venv python
    inner = f'cd {shlex.quote(str(BOT_DIR))} && '
    if (VENV_BIN / "activate").exists():
        inner += f'source {shlex.quote(str(VENV_BIN / "activate"))} && '
    inner += f'{shlex.quote(_python_exe())} -u {shlex.quote(ENTRY)} {shlex.quote(cli_key)}'
    return ["/bin/bash", "-lc", inner]

def _push(line: str):
    global _last_lines
    try: _log_q.put_nowait(line)
    except queue.Full: pass
    _last_lines = (_last_lines + [line])[-MAX_LAST:]

def _reader_thread(proc: subprocess.Popen, display: str, started_at: float):
    try:
        for raw in iter(proc.stdout.readline, b""):
            if not raw: break
            s = raw.decode(errors="replace")
            _push(s.rstrip("\n"))
        rc = proc.wait()
        dur = time.time() - started_at
        st = OPER_STATE.get(display)
        if st:
            st["running"] = False
            st["last_ok"] = (rc == 0)
            st["last_duration"] = dur
            st["last_ts"] = time.time()
        _push(f"--- {display} {'OK' if rc==0 else 'FAILED'} (rc={rc}) in {dur:.1f}s ---")
    except Exception:
        _push("ERROR in reader_thread:\n" + traceback.format_exc())

def _kill_process_group(p: subprocess.Popen, timeout=8):
    # SIGINT -> wait -> SIGTERM -> wait -> SIGKILL
    try:
        pgid = os.getpgid(p.pid)
    except Exception:
        try: p.send_signal(signal.SIGINT)
        except Exception: pass
        return
    try:
        os.killpg(pgid, signal.SIGINT)
    except ProcessLookupError:
        return
    for _ in range(timeout):
        if p.poll() is not None: return
        time.sleep(1)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(6):
        if p.poll() is not None: return
        time.sleep(0.5)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass

def _row_html(display: str, ready: bool) -> str:
    st = OPER_STATE[display]
    running   = st["running"]
    available = st["available"]
    last_ok   = st["last_ok"]
    last_dur  = st["last_duration"]
    last_ts   = st["last_ts"]

    if running:
        status = "running"
    elif not available:
        status = "unavailable"
    elif last_ok is True:
        status = "ok"
    elif last_ok is False:
        status = "failed"
    else:
        status = "never"

    dur_s = f"{last_dur:.1f}s" if last_dur else "—"
    when  = time.strftime("%Y-%m-%d %H:%M", time.localtime(last_ts)) if last_ts else "—"
    disabled = ("disabled" if running or not ready or not available else "")

    return (
        '<tr data-display="' + display + '">'
        '<td class="td">' + display + '</td>'
        '<td class="td status"><span class="badge ' + status + '">' + status + '</span></td>'
        '<td class="td dur">' + dur_s + '</td>'
        '<td class="td when">' + when + '</td>'
        '<td class="td">'
        '<form method="POST" action="/run" style="display:inline">'
        '<input type="hidden" name="display" value="' + display + '"/>'
        '<button class="btn primary runbtn" ' + disabled + '>Run</button>'
        '</form>'
        '</td>'
        '</tr>'
    )

# ===== Routes =====
@app.route("/health")
def health():
    return {"ok": True, "env_ok": env_ok()}

@app.route("/", methods=["GET"])
def index():
    try:
        ready = env_ok()
        refresh_availability()
        rows = "".join(_row_html(d, ready) for d in DISPLAY_ORDER)

        html = (
            "<!doctype html><html><head>"
            '<meta charset="utf-8"/><title>' + TITLE + '</title>'
            '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
            "<style>"
            "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#f8fafc;color:#0f172a;margin:0}"
            ".wrap{max-width:1200px;margin:24px auto;padding:0 16px}"
            ".card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;box-shadow:0 1px 2px rgba(0,0,0,.04)}"
            ".pad{padding:16px 18px}"
            ".row{display:grid;grid-template-columns:1.2fr 2fr;gap:16px}"
            "h1{margin:0 0 12px 2px}"
            ".subtle{font-size:12px;color:#64748b}"
            ".btn{appearance:none;border:0;border-radius:10px;padding:9px 13px;font-weight:600;cursor:pointer}"
            ".primary{background:#4f46e5;color:#fff}.primary:hover{background:#4338ca}"
            ".ghost{background:#fff;border:1px solid #cbd5e1}.ghost:hover{background:#f1f5f9}"
            ".log{font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace;background:#0b1021;color:#e5e7eb;border-radius:10px;border:1px solid #1f2937;padding:12px;height:66vh;overflow:auto;white-space:pre-wrap}"
            "table{width:100%;border-collapse:collapse;font-size:14px}"
            "th,.td{padding:10px;border-bottom:1px solid #e2e8f0;text-align:left}"
            ".badge{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid #cbd5e1;background:#fff;display:inline-block;text-transform:uppercase}"
            ".badge.ok{color:#16a34a;border-color:#86efac;background:#f0fdf4}"
            ".badge.failed{color:#dc2626;border-color:#fecaca;background:#fef2f2}"
            ".badge.running{color:#2563eb;border-color:#bfdbfe;background:#eff6ff}"
            ".badge.never{color:#475569}"
            ".badge.unavailable{color:#9ca3af;background:#f3f4f6;border-color:#e5e7eb}"
            ".kv{font-size:12px;color:#475569}"
            "</style>"
            "</head><body>"
            '<div class="wrap"><h1>⚙️ ' + TITLE + '</h1>'
            '<div class="row">'
            '<div class="card pad">'
            '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">'
            '<div><div style="font-weight:600">Operators</div>'
            '<div class="subtle">' + ('Ready' if ready else 'Config problem: fix paths or venv') + '</div></div>'
            '<span class="badge ' + ('ok' if ready else 'failed') + '">● ' + ('Ready' if ready else 'Needs config') + '</span>'
            '</div>'
            '<table id="optable"><thead>'
            '<tr><th>Operator</th><th>Status</th><th>Last Duration</th><th>Last Run</th><th>Action</th></tr>'
            '</thead><tbody>' + rows + '</tbody></table>'
            '<form method="POST" action="/stop" style="margin-top:14px"><button class="btn ghost">Stop Current</button></form>'
            '<div style="margin-top:10px" class="kv">'
            '<div><b>Bot dir:</b> ' + str(BOT_DIR) + '</div>'
            '<div><b>Venv bin:</b> ' + str(VENV_BIN) + '</div>'
            '<div><b>Entry:</b> ' + ENTRY + '</div>'
            '</div></div>'
            '<div class="card pad"><div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">'
            '<div style="font-weight:600">Live Log</div><div id="status" class="badge">idle</div></div>'
            '<div id="log" class="log"></div></div>'
            '</div></div>'
            "<script>"
            "var es=new EventSource('/stream');"
            "var logEl=document.getElementById('log');"
            "var statusEl=document.getElementById('status');"
            "es.onopen=function(){statusEl.textContent='connected';};"
            "es.onerror=function(){statusEl.textContent='reconnecting…';};"
            "es.onmessage=function(e){logEl.textContent+=(e.data||'')+'\\n';logEl.scrollTop=logEl.scrollHeight;};"
            "function poll(){fetch('/status').then(r=>r.json()).then(d=>{(d.items||[]).forEach(function(item){"
            "var row=document.querySelector('tr[data-display=\"'+item.display+'\"]'); if(!row) return;"
            "var st=row.querySelector('.status'); var dr=row.querySelector('.dur'); var wh=row.querySelector('.when'); var btn=row.querySelector('.runbtn');"
            "if(st){ st.innerHTML='<span class=\"badge '+item.status+'\">'+item.status+'</span>'; }"
            "if(dr){ dr.textContent=item.duration || '—'; }"
            "if(wh){ wh.textContent=item.when || '—'; }"
            "if(btn){ btn.disabled=!!item.disable_run; }"
            "});}).catch(function(_){}); setTimeout(poll,2000);} poll();"
            "</script>"
            "</body></html>"
        )
        return html
    except Exception:
        _push("ERROR in index():\n" + traceback.format_exc())
        return ("Internal Server Error", 500)

@app.route("/run", methods=["POST"])
def run_row():
    global _proc, _current_display
    display = request.form.get("display")
    if display not in DISPLAY_ORDER:
        _push("ERROR: Unknown display '"+str(display)+"'."); return redirect(url_for("index"))
    if not env_ok():
        _push("ERROR: Environment not configured. Check BOT_DIR/ENTRY."); return redirect(url_for("index"))
    if not operator_file_exists(display):
        _push("ERROR: operators/"+DISPLAY_TO_FILEBASE[display]+"_ops.py not found."); return redirect(url_for("index"))

    cli_key = DISPLAY_TO_CLIKEY[display]
    with _running_lock:
        if _proc and _proc.poll() is None:
            _push("INFO: A run is already in progress. Stop it first."); return redirect(url_for("index"))

        OPER_STATE[display]["running"] = True
        _current_display = display

        cmd = make_command(cli_key)
        pretty = " ".join(shlex.quote(c) for c in cmd)
        start_ts = time.time()
        _push("--- START "+display+" ---\n$ "+pretty)

        try:
            _proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=False,
                preexec_fn=os.setsid  # separate process group
            )
        except FileNotFoundError as e:
            _push("ERROR: "+str(e))
            OPER_STATE[display]["running"] = False
            _current_display = None
            return redirect(url_for("index"))

        t = threading.Thread(target=_reader_thread, args=(_proc, display, start_ts), daemon=True)
        t.start()

    return redirect(url_for("index"))

@app.route("/stop", methods=["POST"])
def stop():
    global _proc, _current_display
    with _running_lock:
        if _proc and _proc.poll() is None:
            _push("… stopping (SIGINT → SIGTERM → SIGKILL if needed)")
            _kill_process_group(_proc)
        else:
            _push("INFO: No running process.")
        if _current_display and _current_display in OPER_STATE:
            OPER_STATE[_current_display]["running"] = False
        _current_display = None
    return redirect(url_for("index"))

@app.route("/stream")
def stream():
    def gen():
        for line in _last_lines[-120:]:
            yield "data: " + line + "\n\n"
        while True:
            try:
                line = _log_q.get(timeout=15)
                yield "data: " + line + "\n\n"
            except queue.Empty:
                yield "data: \n\n"
    return Response(gen(), mimetype="text/event-stream")

@app.route("/status")
def status():
    refresh_availability()
    items = []
    for d in DISPLAY_ORDER:
        st = OPER_STATE[d]
        running   = st["running"]
        available = st["available"]
        last_ok   = st["last_ok"]
        last_dur  = st["last_duration"]
        last_ts   = st["last_ts"]

        if running:            s = "running"
        elif not available:    s = "unavailable"
        elif last_ok is True:  s = "ok"
        elif last_ok is False: s = "failed"
        else:                  s = "never"

        items.append({
            "display": d,
            "status": s,
            "duration": (f"{last_dur:.1f}s" if last_dur else None),
            "when": (time.strftime("%Y-%m-%d %H:%M", time.localtime(last_ts)) if last_ts else None),
            "disable_run": (running or not available or not env_ok()),
        })
    return jsonify({"items": items, "ts": time.time()})

if __name__ == "__main__":
    print("Open http://127.0.0.1:5000")
    if not env_ok():
        print("! BOT_DIR/ENTRY not found. BOT_DIR=", BOT_DIR)
    refresh_availability()
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)
