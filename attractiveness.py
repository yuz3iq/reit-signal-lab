"""
REIT attractiveness ("매력도") index: dividend yield vs. 10-year Korean
Treasury Bond (KTB) yield spread.

Idea: a REIT is a bond-like, income-oriented asset. The classic way to judge
whether it's "cheap" relative to risk-free alternatives is to compare its
dividend yield against the long-term government bond yield. A wide positive
spread (dividend yield well above the 10Y KTB) suggests the REIT is pricing
in more risk/discount than a bond would; a negative or thin spread suggests
investors are already paying a premium for the REIT over just holding bonds.

Two independent live data sources, each fetched defensively (a failure in
one never breaks the other):

  1. 10Y KTB yield  -- Bank of Korea ECOS Open API (StatisticSearch),
     stat table 817Y002 ("시장금리(일별)"), item 010210000 ("국고채(10년)").
     Requires a free ECOS API key (ECOS_API_KEY env var).

  2. Per-stock dividend yield ("배당수익률") -- scraped from Naver Finance's
     per-stock info page (item/coinfo.naver), since neither ECOS nor any
     free API we have exposes company-level dividend yield. This is a
     best-effort HTML scrape (label-anchored, not positional, to survive
     minor markup changes) and can fail; when it does, that stock's spread
     is reported as unavailable rather than guessed.

In keeping with this project's data-honesty rule: every number here is
either REAL (fetched live, with its source) or explicitly marked
unavailable. Nothing is estimated or backfilled.
"""
import re
from datetime import datetime, timedelta

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}

TICKERS = {
    "448730": "삼성FN리츠",
    "451800": "한화리츠",
    "417310": "코람코더원리츠",
    "338100": "NH프라임리츠",
    "293940": "신한알파리츠",
}

ECOS_STAT_CODE = "817Y002"   # 시장금리(일별)
ECOS_ITEM_CODE = "010210000"  # 국고채(10년)


def fetch_10y_ktb_yield(ecos_key, lookback_days=20, timeout=10):
    """Latest available 10Y Korean treasury bond yield (%) from ECOS."""
    end = datetime.utcnow() + timedelta(hours=9)  # KST
    start = end - timedelta(days=lookback_days)
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{ecos_key}/json/kr/1/50/"
        f"{ECOS_STAT_CODE}/D/{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}/{ECOS_ITEM_CODE}/"
    )
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    rows = data.get("StatisticSearch", {}).get("row")
    if not rows:
        err = data.get("RESULT", {}) or data
        raise ValueError(f"ECOS 응답에 데이터 없음: {err}")
    rows_sorted = sorted(rows, key=lambda x: x["TIME"])
    last = rows_sorted[-1]
    return {
        "value_pct": float(last["DATA_VALUE"]),
        "date": last["TIME"],
        "item_name": last.get("ITEM_NAME1", "국고채(10년)"),
    }


def _extract_pct_after_label(html, label, window=400):
    idx = html.find(label)
    if idx == -1:
        return None
    chunk = re.sub(r"<[^>]+>", " ", html[idx: idx + window])
    m = re.search(r"([0-9]+\.[0-9]+)\s*%", chunk)
    return float(m.group(1)) if m else None


def fetch_dividend_yield(code, timeout=10):
    """Best-effort scrape of current dividend yield (%) for a stock from Naver Finance.
    Returns (value_or_None, source_url_or_None)."""
    urls = [
        f"https://finance.naver.com/item/coinfo.naver?code={code}",
        f"https://finance.naver.com/item/main.naver?code={code}",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.encoding = "euc-kr"
            html = r.text
            for label in ("배당수익률", "시가배당율"):
                val = _extract_pct_after_label(html, label)
                if val is not None:
                    return val, url
        except Exception:
            continue
    return None, None


def get_attractiveness(ecos_key):
    result = {"tickers": {}, "warnings": []}

    try:
        ktb = fetch_10y_ktb_yield(ecos_key)
        result["ktb_10y"] = ktb
    except Exception as e:
        result["ktb_10y"] = None
        result["warnings"].append(f"국고채(10년) 금리 조회 실패: {e}")

    for code, name in TICKERS.items():
        entry = {"name": name}
        try:
            div_yield, src = fetch_dividend_yield(code)
            if div_yield is None:
                entry["available"] = False
                entry["dividend_yield_pct"] = None
                entry["error"] = "배당수익률 조회 실패"
            else:
                entry["available"] = True
                entry["dividend_yield_pct"] = div_yield
                entry["source"] = src
                if result["ktb_10y"]:
                    entry["spread_pct"] = round(div_yield - result["ktb_10y"]["value_pct"], 2)
        except Exception as e:
            entry["available"] = False
            entry["error"] = str(e)
        result["tickers"][code] = entry

    result["as_of"] = (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")
    return result


if __name__ == "__main__":
    import json
    import os
    print(json.dumps(get_attractiveness(os.environ.get("ECOS_API_KEY", "")), ensure_ascii=False, indent=2))
