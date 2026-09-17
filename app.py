import json
import os
import time

from flask import Flask, jsonify, send_from_directory

from live import get_live_signals
from attractiveness import get_attractiveness, debug_fetch_dividend
from dart_dividend import CORP_CODES as DART_CORP_CODES, fetch_dart_dividend_yield

app = Flask(__name__, static_folder="static", static_url_path="")

CACHE_TTL = 900  # 15 minutes
_cache = {"data": None, "ts": 0}

ATTR_CACHE_TTL = 3600  # 1 hour -- KTB yield updates daily, dividend yield changes rarely
_attr_cache = {"data": None, "ts": 0}
ECOS_API_KEY = os.environ.get("ECOS_API_KEY", "")

# DART 공시(alotMatter)는 분기 단위로만 갱신되므로 12시간 캐시 -- WiseReport 스크래핑과
# 별도 캐시로 관리해, DART 조회가 실패해도 매력도 지수 본체(배당수익률-금리스프레드)는
# 영향받지 않도록 분리함.
DART_CACHE_TTL = 43200  # 12 hours
_dart_cache = {"data": None, "ts": 0}
DART_API_KEY = os.environ.get("DART_API_KEY", "")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _get_dart_dividends():
    now = time.time()
    if _dart_cache["data"] is None or now - _dart_cache["ts"] > DART_CACHE_TTL:
        data = {}
        if DART_API_KEY:
            for code in DART_CORP_CODES:
                try:
                    data[code] = fetch_dart_dividend_yield(code, DART_API_KEY)
                except Exception as e:
                    data[code] = {"error": str(e)}
        _dart_cache["data"] = data
        _dart_cache["ts"] = now
    return _dart_cache["data"]


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

    if resp.get("ok") and DART_API_KEY and "tickers" in resp:
        dart_data = _get_dart_dividends()
        for code, entry in resp["tickers"].items():
            entry["dart"] = dart_data.get(code)
    elif "tickers" in resp and not DART_API_KEY:
        for entry in resp["tickers"].values():
            entry["dart"] = None

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
