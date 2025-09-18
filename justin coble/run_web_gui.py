# run_web_gui.py

import os
import json
import subprocess
from datetime import datetime
from flask import Flask, render_template_string, request, Response, stream_with_context

app = Flask(__name__)

HISTORY_FILE = os.path.join(os.getcwd(), "run_history.json")

SOURCES = [
    {"key": "reuben_lublin",        "label": "Reuben Lublin"},
    {"key": "brock_and_scott",      "label": "Brock & Scott"},
    {"key": "aldridge_pites",       "label": "Aldridge Pites"},
    {"key": "foreclosure_hotline",  "label": "Foreclosure Hotline"},
    {"key": "servicelink_auction",  "label": "ServiceLink Auction"},
    {"key": "auction_com",          "label": "Auction.com"},
    {"key": "zome_com",             "label": "Xome.com"},
    {"key": "logs_powerbi",         "label": "Logs PowerBI Report"},
]

INDEX_HTML = """
<!doctype html>
<title>Lead Bot Controller</title>
<style>
  body { font-family: sans-serif; padding: 20px; }
  label { display: block; margin-bottom: 8px; }
  .history { color: #555; margin-left: 6px; font-size: .9em; }
  .status.complete { color: green; }
  .status.partial { color: orange; }
</style>
<h1>Lead Bot Interface</h1>
<form method="post" action="/run">
  {% for s in sources %}
    <label>
      <input type="checkbox" name="source" value="{{ s.key }}"> {{ s.label }}
      {% if history.get(s.key) %}
        <span class="history">
          Last run: {{ history[s.key].time }}, 
          <span class="status {{ history[s.key].status|lower }}">{{ history[s.key].status }}</span>
        </span>
      {% endif %}
    </label>
  {% endfor %}
  <button type="submit">Run Bot</button>
</form>
"""

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            return json.load(open(HISTORY_FILE))
        except:
            return {}
    return {}

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

@app.route("/", methods=["GET"])
def index():
    history = load_history()
    return render_template_string(INDEX_HTML, sources=SOURCES, history=history)

@app.route("/run", methods=["POST"])
def run():
    selected = request.form.getlist("source")
    if not selected:
        return "<p style='color:red'>No source selected. <a href='/'>Go back</a>.</p>", 400

    def generate():
        yield """<!doctype html>
<html><head><title>Running Bot…</title></head><body>
<h1>Running Bot…</h1><pre id="output">
"""
        project_dir = os.getcwd()
        python_bin  = os.path.join(project_dir, ".venv", "bin", "python")
        main_py     = os.path.join(project_dir, "main.py")
        cmd = [python_bin, "-u", main_py] + selected

        yield f"🔧 Executing: {' '.join(cmd)}\n\n"
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        for line in proc.stdout:
            safe = (line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            yield safe

        proc.wait()
        code = proc.returncode
        yield f"\n✅ Finished with code {code}\n"
        yield "</pre>\n<p><a href='/'>Run another source</a></p>\n"
        yield """
<script>
  const pre = document.getElementById('output');
  new MutationObserver(() => window.scrollTo(0, document.body.scrollHeight))
    .observe(pre, { childList: true, subtree: true });
</script>
"""

        status = "Complete" if code == 0 else "Partial"
        now    = datetime.now().strftime("%m-%d-%Y %I:%M %p")
        hist   = load_history()
        for key in selected:
            hist[key] = {"time": now, "status": status}
        save_history(hist)

        yield "</body></html>"

    return Response(stream_with_context(generate()), mimetype="text/html")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, threaded=True)