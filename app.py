import json
import os
import time

from flask import Flask, jsonify, send_from_directory

from live import get_live_signals
from attractiveness import get_attractiveness, debug_fetch_dividend

app = Flask(__name__, static_folder="static", static_url_path="")

CACHE_TTL = 900  # 15 minutes
_cache = {"data": None, "ts": 0}

ATTR_CACHE_TTL = 3600  # 1 hour -- KTB yield updates daily, dividend yield changes rarely
_attr_cache = {"data": None, "ts": 0}
ECOS_API_KEY = os.environ.get("ECOS_API_KEY", "")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/live")
def api_live():
    now = time.time()
    if _cache["data"] is None or now - _cache["ts"] > CACHE_TTL:
        try:
            result = get_live_signals()
            _cache["data"] = {"ok": True, **result}
        except Exception as e:
            _cache["data"] = {"ok": False, "error": str(e)}
        _cache["ts"] = now
    resp = dict(_cache["data"])
    resp["cache_age_sec"] = round(now - _cache["ts"])
    return jsonify(resp)


@app.route("/api/attractiveness")
def api_attractiveness():
    now = time.time()
    if _attr_cache["data"] is None or now - _attr_cache["ts"] > ATTR_CACHE_TTL:
        try:
            if not ECOS_API_KEY:
                raise RuntimeError("ECOS_API_KEY not configured on server")
            result = get_attractiveness(ECOS_API_KEY)
            _attr_cache["data"] = {"ok": True, **result}
        except Exception as e:
            _attr_cache["data"] = {"ok": False, "error": str(e)}
        _attr_cache["ts"] = now
    resp = dict(_attr_cache["data"])
    resp["cache_age_sec"] = round(now - _attr_cache["ts"])
    return jsonify(resp)


@app.route("/api/debug/dividend/<code>")
def api_debug_dividend(code):
    return jsonify(debug_fetch_dividend(code))


@app.route("/api/bundle")
def api_bundle():
    with open(os.path.join(DATA_DIR, "bundle.json"), encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
