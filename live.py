"""
Live data fetch + walk-forward signal computation for Ritz Signal Lab.

Fetches recent daily prices for the 5 office REITs + KOSPI from Naver
Finance's public JSON endpoint (no API key), rebuilds the same feature
set used in offline training (see build_features.py), retrains an
RBF-kernel SVR on the trailing 90 trading days per stock (using the
hyperparameters already tuned offline), and predicts tomorrow's return
sign as today's live signal.

Every external call is defensive: if a source fails, that piece is
marked unavailable rather than crashing the whole response, in keeping
with the "don't invent numbers" principle used across this project.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR

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

FEAT_COLS = [
    "own_ret_lag1", "own_ret_lag2", "own_ret_lag3", "own_vol5", "own_vol20",
    "own_mom5", "kospi_ret_lag1", "fx_ret_lag1", "kospi_vol5", "vol_chg",
]

WINDOW = 90
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

with open(os.path.join(DATA_DIR, "best_params.json"), encoding="utf-8") as f:
    BEST_PARAMS = json.load(f)


def _fetch_sise_json(symbol, start, end, timeout=10):
    """Fetch daily OHLCV from Naver's siseJson endpoint for a stock or index symbol."""
    url = (
        "https://api.finance.naver.com/siseJson.naver"
        f"?symbol={symbol}&requestType=1&startTime={start}&endTime={end}&timeframe=day"
    )
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    text = r.text.strip()
    # Response is a JS array literal with single quotes; convert to JSON.
    text = text.replace("'", '"')
    rows = json.loads(text)
    if not rows or len(rows) < 2:
        raise ValueError(f"empty response for {symbol}")
    header = [h.strip() for h in rows[0]]
    df = pd.DataFrame(rows[1:], columns=header)
    df = df.rename(columns={"날짜": "date", "종가": "close", "시가": "open",
                             "고가": "high", "저가": "low", "거래량": "volume"})
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]]


def _date_range(days_back=220):
    end = datetime.utcnow() + timedelta(hours=9)  # KST
    start = end - timedelta(days=days_back)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fetch_all_prices():
    """Fetch trailing price history for all 5 REITs + KOSPI. Returns (dict[code->df], warnings list)."""
    start, end = _date_range(220)
    out = {}
    warnings = []
    for code in TICKERS:
        try:
            out[code] = _fetch_sise_json(code, start, end)
        except Exception as e:
            warnings.append(f"{code} 시세 조회 실패: {e}")
    try:
        out["KOSPI"] = _fetch_sise_json("KOSPI", start, end)
    except Exception as e:
        warnings.append(f"코스피 조회 실패: {e}")
    return out, warnings


def build_live_features(price_df, kospi_df):
    m = price_df.copy().sort_values("date").reset_index(drop=True)
    m["ret"] = m["close"].pct_change()

    k = kospi_df.copy().sort_values("date").reset_index(drop=True)
    k["kospi_ret"] = k["close"].pct_change()
    k["kospi_vol5"] = k["kospi_ret"].rolling(5).std()

    merged = pd.merge(m[["date", "close", "volume", "ret"]],
                       k[["date", "kospi_ret", "kospi_vol5"]], on="date", how="inner")
    merged = merged.sort_values("date").reset_index(drop=True)

    merged["own_ret_lag1"] = merged["ret"]
    merged["own_ret_lag2"] = merged["ret"].shift(1)
    merged["own_ret_lag3"] = merged["ret"].shift(2)
    merged["own_vol5"] = merged["ret"].rolling(5).std()
    merged["own_vol20"] = merged["ret"].rolling(20).std()
    merged["own_mom5"] = merged["close"].pct_change(5)
    merged["kospi_ret_lag1"] = merged["kospi_ret"]
    merged["fx_ret_lag1"] = 0.0  # FX source not wired up live; neutral placeholder
    merged["vol_chg"] = merged["volume"].pct_change()

    merged[FEAT_COLS] = merged[FEAT_COLS].replace([np.inf, -np.inf], np.nan)
    merged = merged.dropna(subset=FEAT_COLS).reset_index(drop=True)
    return merged


def predict_live_signal(code, merged):
    if len(merged) < WINDOW + 1:
        raise ValueError(f"insufficient history ({len(merged)} rows) for {code}")
    train = merged.iloc[-WINDOW:]
    X_train = train[FEAT_COLS].values
    y_train = train["ret"].shift(-1).values  # next-day return target
    # last row has no realized next-day return yet; drop it from training, use it to predict
    X_fit, y_fit = X_train[:-1], y_train[:-1]
    x_today = X_train[-1:]

    scaler = StandardScaler().fit(X_fit)
    model = SVR(kernel="rbf", **BEST_PARAMS[code])
    model.fit(scaler.transform(X_fit), y_fit)
    pred = float(model.predict(scaler.transform(x_today))[0])
    return pred


def get_live_signals():
    prices, warnings = fetch_all_prices()
    result = {
        "as_of": (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST"),
        "warnings": warnings,
        "signals": {},
    }
    kospi_df = prices.get("KOSPI")
    for code, name in TICKERS.items():
        entry = {"name": name}
        df = prices.get(code)
        if df is None or kospi_df is None or len(df) < 5:
            entry["available"] = False
            result["signals"][code] = entry
            continue
        try:
            last_row = df.iloc[-1]
            prev_row = df.iloc[-2]
            entry["last_close"] = float(last_row["close"])
            entry["last_date"] = last_row["date"].strftime("%Y-%m-%d")
            entry["day_change_pct"] = round(float(last_row["close"] / prev_row["close"] - 1) * 100, 2)

            merged = build_live_features(df, kospi_df)
            pred = predict_live_signal(code, merged)
            entry["predicted_next_ret_pct"] = round(pred * 100, 3)
            entry["signal"] = "매수" if pred > 0 else "현금"
            entry["available"] = True
        except Exception as e:
            entry["available"] = False
            entry["error"] = str(e)
        result["signals"][code] = entry
    return result


if __name__ == "__main__":
    print(json.dumps(get_live_signals(), ensure_ascii=False, indent=2))
