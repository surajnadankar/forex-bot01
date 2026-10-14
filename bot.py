import os
import re
import base64
import logging
import threading
import json
from datetime import datetime, timezone

import requests
import pandas as pd
import yfinance as yf
from flask import Flask

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# =========================================================
# 1. Logging & Config
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "TradingSignals").strip()
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON", "").strip()

# =========================================================
# 2. Ticker Map & Fallback Levels
# =========================================================
TICKER_MAP = {
    "EURAUD": "EURAUD=X",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "XAUUSD": "GC=F",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "BTCUSD": "BTC-USD",
    "BTCUSDT": "BTC-USD",
    "BTC": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "US30": "^DJI",
    "NAS100": "^IXIC",
    "US500": "^GSPC",
}

PIP_SIZE = {
    "GOLD": 0.1, "XAUUSD": 0.1,
    "BTC": 1.0, "BTCUSD": 1.0, "BTCUSDT": 1.0,
    "ETHUSD": 1.0,
    "USDJPY": 0.01,
    "EURAUD": 0.0001, "EURUSD": 0.0001, "GBPUSD": 0.0001,
    "AUDUSD": 0.0001, "USDCAD": 0.0001,
}

FALLBACK_LEVELS = {
    "GOLD": [4100.0, 4120.0, 4148.0, 4166.7, 4200.0],
    "EURUSD": [1.0850, 1.0920, 1.1000, 1.1050, 1.1120],
    "GBPUSD": [1.3140, 1.3200, 1.3250, 1.3300],
    "USDJPY": [152.00, 153.50, 155.00, 156.50],
    "USDCAD": [1.3950, 1.4020, 1.4150, 1.4257],
    "BTC": [82000.0, 85000.0, 87700.0, 90000.0],
}

# =========================================================
# 3. Flask Web Server (health check for Render)
# =========================================================
web_app = Flask(__name__)


@web_app.route("/")
@web_app.route("/health")
def health():
    return "Sniper Engine Online 200 OK", 200


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# =========================================================
# 4. Symbol Helpers
# =========================================================
def resolve_yf_ticker(raw_symbol: str) -> str:
    clean_sym = re.sub(r"[^A-Z0-9]", "", raw_symbol.upper())
    if clean_sym in TICKER_MAP:
        return TICKER_MAP[clean_sym]
    if len(clean_sym) == 6:
        return f"{clean_sym}=X"
    return clean_sym


def normalize_symbol(raw_symbol: str) -> str:
    clean = re.sub(r"[^A-Z0-9]", "", raw_symbol.upper())
    if "XAU" in clean or clean == "GOLD":
        return "GOLD"
    if "BTC" in clean:
        return "BTC"
    return clean


def get_pip_size(symbol: str) -> float:
    return PIP_SIZE.get(normalize_symbol(symbol), 0.0001)


# =========================================================
# 5. Swing Point Detection
# =========================================================
def find_swing_points(df: pd.DataFrame, lookback: int = 5):
    swing_highs, swing_lows = [], []
    if df.empty or len(df) < lookback * 2 + 1:
        return swing_highs, swing_lows

    highs = df["High"].values
    lows = df["Low"].values

    for i in range(lookback, len(df) - lookback):
        window_h = highs[i - lookback:i + lookback + 1]
        window_l = lows[i - lookback:i + lookback + 1]
        if highs[i] == window_h.max():
            swing_highs.append((i, float(highs[i])))
        if lows[i] == window_l.min():
            swing_lows.append((i, float(lows[i])))

    return swing_highs, swing_lows


# =========================================================
# 6. Liquidity Sweep Detection
# =========================================================
def detect_liquidity_sweep(
    df: pd.DataFrame,
    lookback: int = 5,
    wick_ratio: float = 1.3,
    recent_bars: int = 3,
    max_age_bars: int = 40,
):
    sweeps = []
    if df.empty or len(df) < (lookback * 2 + recent_bars + 1):
        return sweeps

    swing_highs, swing_lows = find_swing_points(df, lookback)

    for idx in range(len(df) - recent_bars, len(df)):
        c = df.iloc[idx]
        o = float(c["Open"])
        h = float(c["High"])
        l = float(c["Low"])
        cl = float(c["Close"])

        rng = h - l
        if rng <= 0:
            continue

        body = abs(cl - o)
        upper_wick = h - max(o, cl)
        lower_wick = min(o, cl) - l

        # BEARISH SWEEP — buy-side liquidity grab
        for sh_idx, sh_price in swing_highs:
            if sh_idx >= idx or (idx - sh_idx) > max_age_bars:
                continue
            if h > sh_price and cl < sh_price:
                if body == 0 or upper_wick > body * wick_ratio:
                    sweeps.append({
                        "type": "BEARISH_SWEEP",
                        "level": round(sh_price, 5),
                        "swept_to": round(h, 5),
                        "closed_at": round(cl, 5),
                        "bars_ago": len(df) - 1 - idx,
                    })
                    break

        # BULLISH SWEEP — sell-side liquidity grab
        for sl_idx, sl_price in swing_lows:
            if sl_idx >= idx or (idx - sl_idx) > max_age_bars:
                continue
            if l < sl_price and cl > sl_price:
                if body == 0 or lower_wick > body * wick_ratio:
                    sweeps.append({
                        "type": "BULLISH_SWEEP",
                        "level": round(sl_price, 5),
                        "swept_to": round(l, 5),
                        "closed_at": round(cl, 5),
                        "bars_ago": len(df) - 1 - idx,
                    })
                    break

    return sweeps


# =========================================================
# 7. V-Shape Detection
# =========================================================
def detect_v_shape(
    df: pd.DataFrame,
    min_move_pct: float = 0.3,
    max_bars: int = 6,
    recovery_ratio: float = 0.5,
):
    result = {"bullish_v": None, "bearish_v": None}
    if df.empty or len(df) < max_bars + 3:
        return result

    recent = df.tail(max_bars + 3).reset_index()

    # BULLISH V — sharp drop + strong recovery
    for i in range(2, len(recent) - 1):
        low_i = float(recent["Low"].iloc[i])
        left_high = float(recent["High"].iloc[:i].max())
        if left_high <= 0:
            continue

        drop_pct = ((left_high - low_i) / left_high) * 100
        if (left_high - low_i) <= 0:
            continue

        right_close = float(recent["Close"].iloc[i + 1])
        recovery_pct = ((right_close - low_i) / (left_high - low_i)) * 100

        c = recent.iloc[i]
        o = float(c["Open"])
        cl = float(c["Close"])
        l = float(c["Low"])
        body = abs(cl - o)
        lower_wick = min(o, cl) - l

        if (
            drop_pct >= min_move_pct
            and recovery_pct >= recovery_ratio * 100
            and (body == 0 or lower_wick > body * 1.5)
        ):
            result["bullish_v"] = {
                "low": round(low_i, 5),
                "left_high": round(left_high, 5),
                "drop_pct": round(drop_pct, 2),
                "recovery_pct": round(recovery_pct, 1),
            }
            break

    # BEARISH V — sharp pump + rejection
    for i in range(2, len(recent) - 1):
        high_i = float(recent["High"].iloc[i])
        left_low = float(recent["Low"].iloc[:i].min())
        if left_low <= 0:
            continue

        pump_pct = ((high_i - left_low) / left_low) * 100
        if (high_i - left_low) <= 0:
            continue

        right_close = float(recent["Close"].iloc[i + 1])
        rejection_pct = ((high_i - right_close) / (high_i - left_low)) * 100

        c = recent.iloc[i]
        o = float(c["Open"])
        cl = float(c["Close"])
        h = float(c["High"])
        body = abs(cl - o)
        upper_wick = h - max(o, cl)

        if (
            pump_pct >= min_move_pct
            and rejection_pct >= recovery_ratio * 100
            and (body == 0 or upper_wick > body * 1.5)
        ):
            result["bearish_v"] = {
                "high": round(high_i, 5),
                "left_low": round(left_low, 5),
                "pump_pct": round(pump_pct, 2),
                "rejection_pct": round(rejection_pct, 1),
            }
            break

    return result


# =========================================================
# 8. Equal Highs / Equal Lows
# =========================================================
def detect_equal_levels(
    df: pd.DataFrame, tolerance_pct: float = 0.0008, lookback: int = 5
):
    eq_highs, eq_lows = [], []
    swing_highs, swing_lows = find_swing_points(df, lookback)

    for i in range(len(swing_highs)):
        for j in range(i + 1, len(swing_highs)):
            p1, p2 = swing_highs[i][1], swing_highs[j][1]
            if p1 > 0 and abs(p1 - p2) / p1 <= tolerance_pct:
                eq_highs.append(round((p1 + p2) / 2, 5))
                break

    for i in range(len(swing_lows)):
        for j in range(i + 1, len(swing_lows)):
            p1, p2 = swing_lows[i][1], swing_lows[j][1]
            if p1 > 0 and abs(p1 - p2) / p1 <= tolerance_pct:
                eq_lows.append(round((p1 + p2) / 2, 5))
                break

    return sorted(set(eq_highs)), sorted(set(eq_lows))


# =========================================================
# 9. Session Levels
# =========================================================
def get_session_levels(df_5m: pd.DataFrame):
    sessions = {"ASIAN": (0, 8), "LONDON": (7, 16), "NEWYORK": (12, 21)}
    out = {}
    if df_5m.empty:
        return out

    idx = df_5m.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")

    for name, (sh, eh) in sessions.items():
        mask = [(sh <= t.hour < eh) for t in idx]
        sub = df_5m[mask]
        if not sub.empty:
            out[name] = {
                "high": round(float(sub["High"].max()), 5),
                "low": round(float(sub["Low"].min()), 5),
            }
    return out


# =========================================================
# 10. Setup Quality Score
# =========================================================
def score_setup_quality(sweeps, v_shape, trend_4h, vol_surge, rr_ratio=2.0):
    score = 0
    reasons = []

    if sweeps:
        score += 40
        reasons.append(f"{len(sweeps)} sweep(s) detected")
    else:
        reasons.append("No sweep - low probability")

    has_v = v_shape.get("bullish_v") or v_shape.get("bearish_v")
    if has_v:
        score += 20
        reasons.append("V-Shape confirmed")
    else:
        reasons.append("No V-shape")

    if "BULLISH" in trend_4h or "BEARISH" in trend_4h:
        score += 15
        reasons.append("4H trend aligned")
    else:
        reasons.append("4H trend unclear")

    if vol_surge >= 1.3:
        score += 15
        reasons.append(f"Volume surge {vol_surge}x")
    else:
        reasons.append(f"Low volume {vol_surge}x")

    if rr_ratio >= 2.0:
        score += 10
        reasons.append(f"RRR 1:{rr_ratio}")
    else:
        reasons.append(f"RRR only 1:{rr_ratio}")

    if score >= 75:
        verdict = "HIGH QUALITY - COPY KAREIN"
    elif score >= 55:
        verdict = "MEDIUM - 50% size"
    else:
        verdict = "SKIP - setup kamzor"

    return {"score": score, "verdict": verdict, "reasons": reasons}


# =========================================================
# 11. Google Sheet Logging (safe - never crashes bot)
# =========================================================
_sheet_client = None


def get_sheet():
    global _sheet_client
    if _sheet_client is not None:
        return _sheet_client

    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]

        if GOOGLE_CREDS_JSON:
            creds_dict = json.loads(GOOGLE_CREDS_JSON)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name(
                "google_creds.json", scope
            )

        client = gspread.authorize(creds)
        _sheet_client = client.open(GOOGLE_SHEET_NAME).sheet1
        return _sheet_client
    except Exception as e:
        logger.warning(f"Google Sheet init skipped: {e}")
        return None


def log_to_sheet(data: dict) -> bool:
    sheet = get_sheet()
    if sheet is None:
        return False
    try:
        sheet.append_row([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            data.get("symbol", ""),
            data.get("price", ""),
            data.get("verdict", ""),
            data.get("score", ""),
            data.get("sweep_type", "NONE"),
            data.get("v_shape", "NONE"),
            data.get("trend_4h", ""),
            data.get("vol_surge", ""),
            data.get("entry", ""),
            data.get("sl", ""),
            data.get("tp1", ""),
            data.get("tp2", ""),
            " | ".join(data.get("reasons", [])),
        ])
        return True
    except Exception as e:
        logger.warning(f"Sheet log error: {e}")
        return False


# =========================================================
# 12. Live Market Data Builder
# =========================================================
def fetch_realtime_market_data(symbol: str) -> dict:
    result = {"symbol": symbol, "ok": False}

    try:
        yf_symbol = resolve_yf_ticker(symbol)
        ticker = yf.Ticker(yf_symbol)

        df_5m = ticker.history(period="2d", interval="5m")
        df_1h = ticker.history(period="10d", interval="1h")
        df_1d = ticker.history(period="10d", interval="1d")

        if df_5m.empty and df_1d.empty:
            result["error"] = "No data available"
            return result

        if not df_5m.empty:
            live_price = float(df_5m["Close"].iloc[-1])
        else:
            live_price = float(df_1d["Close"].iloc[-1])

        day_open = float(df_1d["Open"].iloc[-1]) if not df_1d.empty else live_price
        day_trend = "BULLISH" if live_price >= day_open else "BEARISH"

        trend_4h = day_trend
        c_4h_high = c_4h_low = live_price

        if not df_1h.empty and len(df_1h) >= 8:
            df_4h = df_1h.resample("4H").agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
            }).dropna()

            if not df_4h.empty:
                c_4h_open = float(df_4h["Open"].iloc[-1])
                c_4h_high = float(df_4h["High"].iloc[-1])
                c_4h_low = float(df_4h["Low"].iloc[-1])
                trend_4h = "BULLISH" if live_price >= c_4h_open else "BEARISH"

        vol_surge = 1.0
        delta_status = "[BALANCED VOLUME]"

        if not df_5m.empty and "Volume" in df_5m.columns and df_5m["Volume"].sum() > 0:
            recent_vol = float(df_5m["Volume"].tail(3).sum())
            avg_vol = float(df_5m["Volume"].mean()) * 3

            if avg_vol > 0:
                vol_surge = round(recent_vol / avg_vol, 2)

            c = df_5m.iloc[-1]
            c_o = float(c["Open"])
            c_c = float(c["Close"])
            c_h = float(c["High"])
            c_l = float(c["Low"])
            body = abs(c_c - c_o)
            rng = (c_h - c_l) if (c_h - c_l) > 0 else 0.001

            if vol_surge >= 1.3 and (body / rng) < 0.35:
                delta_status = "[ABSORPTION] Orders absorbed - reversal possible"
            elif vol_surge >= 1.4:
                delta_status = "[MOMENTUM] Aggressive breakout orders"
            elif vol_surge <= 0.6:
                delta_status = "[DRY VOLUME] Low liquidity - trap risk"

        sweeps_5m = detect_liquidity_sweep(df_5m) if not df_5m.empty else []
        sweeps_1h = detect_liquidity_sweep(df_1h) if not df_1h.empty else []

        v_shape_5m = detect_v_shape(df_5m) if not df_5m.empty else {"bullish_v": None, "bearish_v": None}
        v_shape_1h = detect_v_shape(df_1h) if not df_1h.empty else {"bullish_v": None, "bearish_v": None}

        eq_highs, eq_lows = ([], [])
        if not df_1h.empty:
            eq_highs, eq_lows = detect_equal_levels(df_1h)

        sessions = get_session_levels(df_5m) if not df_5m.empty else {}

        quality = score_setup_quality(sweeps_5m, v_shape_5m, trend_4h, vol_surge)

        norm = normalize_symbol(symbol)
        key_levels = FALLBACK_LEVELS.get(norm, [])

        result.update({
            "ok": True,
            "yf_symbol": yf_symbol,
            "price": round(live_price, 5),
            "day_trend": day_trend,
            "trend_4h": trend_4h,
            "c_4h_high": round(c_4h_high, 5),
            "c_4h_low": round(c_4h_low, 5),
            "vol_surge": vol_surge,
            "delta_status": delta_status,
            "sweeps_5m": sweeps_5m,
            "sweeps_1h": sweeps_1h,
            "v_shape_5m": v_shape_5m,
            "v_shape_1h": v_shape_1h,
            "eq_highs": eq_highs,
            "eq_lows": eq_lows,
            "sessions": sessions,
            "key_levels": key_levels,
            "quality": quality,
        })
        return result

    except Exception as e:
        logger.warning(f"fetch_realtime_market_data error: {e}")
        result["error"] = str(e)
        return result


# =========================================================
# 13. Format Context for AI
# =========================================================
def format_market_context(d: dict) -> str:
    if not d.get("ok"):
        return f"Data unavailable for {d.get('symbol')}: {d.get('error')}"

    def fmt_sweeps(sw_list):
        if not sw_list:
            return "  No recent sweeps"
        lines = []
        for s in sw_list[:5]:
            lines.append(
                f"  - {s['type']} @ {s['level']} | swept {s['swept_to']} | "
                f"close {s['closed_at']} | {s['bars_ago']} bars ago"
            )
        return "\n".join(lines)

    def fmt_v(v):
        if v.get("bullish_v"):
            bv = v["bullish_v"]
            return f"BULLISH V (low {bv['low']}, drop {bv['drop_pct']}%, recovery {bv['recovery_pct']}%)"
        if v.get("bearish_v"):
            bv = v["bearish_v"]
            return f"BEARISH V (high {bv['high']}, pump {bv['pump_pct']}%, reject {bv['rejection_pct']}%)"
        return "No V-shape"

    sessions_str = "N/A"
    if d.get("sessions"):
        sessions_str = " | ".join(
            [f"{k}: H={v['high']} L={v['low']}" for k, v in d["sessions"].items()]
        )

    q = d["quality"]

    return (
        f"LIVE DATA ({d['symbol']} -> {d['yf_symbol']})\n"
        f"- Price: {d['price']}\n"
        f"- 1D Trend: {d['day_trend']}\n"
        f"- 4H Trend: {d['trend_4h']} (High: {d['c_4h_high']} | Low: {d['c_4h_low']})\n"
        f"- Vol Surge: {d['vol_surge']}x\n"
        f"- Delta: {d['delta_status']}\n\n"
        f"SWEEPS (5M):\n{fmt_sweeps(d['sweeps_5m'])}\n\n"
        f"SWEEPS (1H):\n{fmt_sweeps(d['sweeps_1h'])}\n\n"
        f"V-SHAPE (5M): {fmt_v(d['v_shape_5m'])}\n"
        f"V-SHAPE (1H): {fmt_v(d['v_shape_1h'])}\n\n"
        f"Equal Highs: {d['eq_highs'] if d['eq_highs'] else 'None'}\n"
        f"Equal Lows: {d['eq_lows'] if d['eq_lows'] else 'None'}\n\n"
        f"Sessions: {sessions_str}\n"
        f"Key S/R: {d['key_levels']}\n\n"
        f"SETUP SCORE: {q['score']}/100 -> {q['verdict']}\n"
        f"Reasons: {q['reasons']}"
    )


# =========================================================
# 14. Gemini Engine
# =========================================================
def query_gemini_auto(prompt_text: str, image_bytes: bytes = None) -> str:
    if not GEMINI_API_KEY:
        return "Error: Gemini API Key missing."

    model_endpoint = "models/gemini-2.0-flash"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"{model_endpoint}:generateContent?key={GEMINI_API_KEY}"
    )

    parts = [{"text": prompt_text}]
    if image_bytes:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
            }
        })

    payload = {"contents": [{"parts": parts}]}
    headers = {"Content-Type": "application/json"}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        j = r.json()

        if "candidates" in j and j["candidates"]:
            cand = j["candidates"][0]
            if "content" in cand and "parts" in cand["content"]:
                return cand["content"]["parts"][0]["text"].strip()

        if "error" in j:
            return f"AI Error: {j['error'].get('message', 'Unknown')}"

        return "AI Response Format Error."
    except Exception as e:
        return f"API Error: {str(e)}"


# =========================================================
# 15. Trade Evaluator
# =========================================================
def evaluate_trade_logic(symbol: str, user_notes: str = "", image_bytes: bytes = None):
    data = fetch_realtime_market_data(symbol)
    context = format_market_context(data)

    if not data.get("ok"):
        return context, data

    full_prompt = f"""
You are an Elite Institutional Trading Engine for a $2,500 The5ers account.
Evaluate this setup for {symbol}.

[REAL-TIME DATA WITH DETECTED SWEEPS & V-SHAPE]:
{context}

[USER NOTES]:
{user_notes}

[INSTRUCTIONS]:
1. Use detected SWEEPS + V-SHAPES + SETUP SCORE as primary signals.
2. BULLISH_SWEEP / BULLISH V = LONG bias.
3. BEARISH_SWEEP / BEARISH V = SHORT bias.
4. ALWAYS give TWO-WAY plan (Bullish + Bearish), never just "AVOID with NA".
5. The5ers rules: Risk $25 (1%), Max SL 40 pips, TP1 = 1:1 (book 50% + SL to BE), TP2 = 1:3.

Format:

MARKET STRUCTURE: [Sweep Reversal / S&R Bounce / V-Shape Setup]
LIVE DATA:
   - Price: [price]
   - 1D / 4H Trend: [trend]
   - Sweep / V-Shape: [summary]
   - Volume/Delta: [analysis]
   - Setup Score: [score/100]

BULLISH PLAN:
   - Trigger: [level]
   - Entry: [price]
   - SL: [price] (max 40 pips)
   - TP1 (1:1): [price]
   - TP2 (1:3): [price]

BEARISH PLAN:
   - Trigger: [level]
   - Entry: [price]
   - SL: [price] (max 40 pips)
   - TP1 (1:1): [price]
   - TP2 (1:3): [price]

MASTER VERDICT: [2 lines in Hindi/Hinglish]
"""
    ai_reply = query_gemini_auto(full_prompt, image_bytes)
    return ai_reply, data


# =========================================================
# 16. Telegram Handlers
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "Institutional Sniper Engine - Copy Trading Mode\n"
        "-------------------------------------\n"
        "- Real Liquidity Sweep Detection (5M + 1H)\n"
        "- V-Shape Detection (dynamic)\n"
        "- Equal Highs/Lows + Session Levels\n"
        "- Setup Quality Score (0-100)\n"
        "- Google Sheet Logging\n"
        "- Dual-Trigger Plan (Bullish + Bearish)\n\n"
        "Commands:\n"
        "/start - Ye message\n"
        "/levels - Live sweep + V-shape scan\n"
        "/check SYMBOL - Full plan for asset\n"
        "/sheet - Last 10 signals\n\n"
        "Ya seedha chart bhejein / text me poochein."
    )
    await update.message.reply_text(msg)


async def levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scanning all assets for sweeps + V-shape...")

    lines = []
    for asset in ["GOLD", "EURUSD", "GBPUSD", "USDJPY", "BTC"]:
        d = fetch_realtime_market_data(asset)
        if d.get("ok"):
            q = d["quality"]
            v = d["v_shape_5m"]
            v_txt = "V-OK" if (v.get("bullish_v") or v.get("bearish_v")) else "V-NO"
            sw_txt = f"SW{len(d['sweeps_5m'])}" if d["sweeps_5m"] else "SW0"
            lines.append(
                f"- {asset}: {d['price']} | {d['trend_4h']} | "
                f"{sw_txt} {v_txt} | Score {q['score']} | {q['verdict']}"
            )
        else:
            lines.append(f"- {asset}: data error")

    await update.message.reply_text("\n".join(lines))


async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    symbol = args[0].upper() if args else "GOLD"
    await update.message.reply_text(f"Building full plan for {symbol}...")

    res, data = evaluate_trade_logic(symbol)

    if data.get("ok"):
        try:
            log_to_sheet({
                "symbol": symbol,
                "price": data["price"],
                "verdict": data["quality"]["verdict"],
                "score": data["quality"]["score"],
                "sweep_type": data["sweeps_5m"][0]["type"] if data["sweeps_5m"] else "NONE",
                "v_shape": "YES" if (data["v_shape_5m"].get("bullish_v") or data["v_shape_5m"].get("bearish_v")) else "NO",
                "trend_4h": data["trend_4h"],
                "vol_surge": data["vol_surge"],
                "reasons": data["quality"]["reasons"],
            })
        except Exception as e:
            logger.warning(f"Sheet log failed silently: {e}")

    await update.message.reply_text(res)


async def sheet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sheet = get_sheet()
    if sheet is None:
        await update.message.reply_text("Google Sheet connect nahi hai.")
        return

    try:
        rows = sheet.get_all_values()
        if len(rows) <= 1:
            await update.message.reply_text("Sheet khali hai.")
            return

        last = rows[-10:]
        txt = ["Last 10 signals:\n"]
        for r in last:
            txt.append(" | ".join(r[:6]))

        await update.message.reply_text("\n".join(txt)[:4000])
    except Exception as e:
        await update.message.reply_text(f"Sheet error: {e}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    await message.reply_text("Analyzing chart: sweeps, V-shape, S&R...")

    try:
        photo_file = await message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()

        detect_prompt = (
            "Analyze this trading chart. Extract ONLY the Symbol/Pair name "
            "(XAUUSD, GOLD, EURUSD, GBPUSD, BTCUSD, USDJPY, USDCAD, etc). "
            "Return only the symbol name on first line."
        )
        detect_result = query_gemini_auto(detect_prompt, bytes(image_bytes))
        first_line = detect_result.split("\n")[0].strip().upper()
        detected_symbol = re.sub(r"[^A-Z0-9]", "", first_line)

        if not detected_symbol or len(detected_symbol) < 3:
            detected_symbol = "GOLD"

        caption = message.caption or ""

        analysis_result, data = evaluate_trade_logic(
            detected_symbol,
            user_notes=caption,
            image_bytes=bytes(image_bytes),
        )

        if data.get("ok"):
            try:
                log_to_sheet({
                    "symbol": detected_symbol,
                    "price": data["price"],
                    "verdict": data["quality"]["verdict"],
                    "score": data["quality"]["score"],
                    "sweep_type": data["sweeps_5m"][0
