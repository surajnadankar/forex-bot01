import os
import re
import json
import base64
import logging
import threading
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
# 1. CONFIG
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
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SCAN_INTERVAL = 15 * 60  # 15 minutes
SCAN_ASSETS = ["GOLD", "BTC", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]

# =========================================================
# 2. TICKER & LEVEL MAPS
# =========================================================
TICKER_MAP = {
    "EURAUD": "EURAUD=X",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "NZDUSD": "NZDUSD=X",
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

FALLBACK_LEVELS = {
    "GOLD": [4100.0, 4120.0, 4148.0, 4166.7, 4200.0],
    "EURUSD": [1.0850, 1.0920, 1.1000, 1.1050, 1.1120],
    "GBPUSD": [1.3140, 1.3200, 1.3250, 1.3300],
    "USDJPY": [152.00, 153.50, 155.00, 156.50],
    "AUDUSD": [0.6850, 0.6920, 0.6980, 0.7050, 0.7120],
    "USDCAD": [1.3950, 1.4020, 1.4150, 1.4257],
    "BTC": [82000.0, 85000.0, 87700.0, 90000.0],
}

PIP_SIZE = {
    "GOLD": 0.1, "XAUUSD": 0.1,
    "BTC": 1.0, "BTCUSD": 1.0, "BTCUSDT": 1.0,
    "ETHUSD": 1.0,
    "USDJPY": 0.01,
    "EURAUD": 0.0001, "EURUSD": 0.0001, "GBPUSD": 0.0001,
    "AUDUSD": 0.0001, "USDCAD": 0.0001, "NZDUSD": 0.0001,
}

# =========================================================
# 3. FLASK HEALTH SERVER
# =========================================================
web_app = Flask(__name__)


@web_app.route("/")
@web_app.route("/health")
def health():
    return "V-Shape Tracker Online 200 OK", 200


def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# =========================================================
# 4. GOOGLE SHEET MANAGER
# =========================================================
_sheet_client = None


def get_sheet():
    global _sheet_client
    if _sheet_client is not None:
        return _sheet_client

    if not GOOGLE_CREDS_JSON:
        logger.warning("GOOGLE_CREDS_JSON not set - Sheet disabled")
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]

        creds_dict = json.loads(GOOGLE_CREDS_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        _sheet_client = client.open(GOOGLE_SHEET_NAME)
        logger.info(f"Google Sheet connected: {GOOGLE_SHEET_NAME}")
        return _sheet_client
    except Exception as e:
        logger.warning(f"Google Sheet init failed: {e}")
        return None


def get_or_create_worksheet(sheet, name, headers):
    try:
        try:
            ws = sheet.worksheet(name)
        except Exception:
            ws = sheet.add_worksheet(title=name, rows=1000, cols=len(headers))
            ws.append_row(headers)
        return ws
    except Exception as e:
        logger.warning(f"Worksheet error ({name}): {e}")
        return None


def load_all_vshapes():
    sheet = get_sheet()
    if sheet is None:
        return {}

    try:
        ws = get_or_create_worksheet(sheet, "VShapeHistory", [
            "ID", "Timestamp", "Asset", "Type", "Price", "DropPct",
            "RecoveryPct", "Status", "Swept_At", "Entry_Taken",
            "Entry_Price", "Result"
        ])
        if ws is None:
            return {}

        records = ws.get_all_records()
        return {str(r["ID"]): r for r in records if r.get("ID")}
    except Exception as e:
        logger.warning(f"load_all_vshapes error: {e}")
        return {}


def save_vshape(vshape: dict):
    sheet = get_sheet()
    if sheet is None:
        return False
    try:
        ws = get_or_create_worksheet(sheet, "VShapeHistory", [
            "ID", "Timestamp", "Asset", "Type", "Price", "DropPct",
            "RecoveryPct", "Status", "Swept_At", "Entry_Taken",
            "Entry_Price", "Result"
        ])
        if ws is None:
            return False

        ws.append_row([
            vshape.get("id", ""),
            vshape.get("timestamp", ""),
            vshape.get("asset", ""),
            vshape.get("type", ""),
            vshape.get("price", ""),
            vshape.get("drop_pct", vshape.get("pump_pct", "")),
            vshape.get("recovery_pct", vshape.get("rejection_pct", "")),
            vshape.get("status", "PENDING"),
            vshape.get("swept_at", ""),
            vshape.get("entry_taken", ""),
            vshape.get("entry_price", ""),
            vshape.get("result", ""),
        ])
        return True
    except Exception as e:
        logger.warning(f"save_vshape error: {e}")
        return False


def update_vshape_status(vshape_id: str, updates: dict):
    sheet = get_sheet()
    if sheet is None:
        return False
    try:
        ws = get_or_create_worksheet(sheet, "VShapeHistory", [
            "ID", "Timestamp", "Asset", "Type", "Price", "DropPct",
            "RecoveryPct", "Status", "Swept_At", "Entry_Taken",
            "Entry_Price", "Result"
        ])
        if ws is None:
            return False

        all_values = ws.get_all_values()
        if not all_values:
            return False

        header = all_values[0]
        for row_idx, row in enumerate(all_values[1:], start=2):
            if row and str(row[0]) == str(vshape_id):
                for col_name, value in updates.items():
                    if col_name in header:
                        col_idx = header.index(col_name) + 1
                        ws.update_cell(row_idx, col_idx, value)
                return True
        return False
    except Exception as e:
        logger.warning(f"update_vshape_status error: {e}")
        return False


def log_alert(asset: str, alert_type: str, message: str, score=0):
    sheet = get_sheet()
    if sheet is None:
        return
    try:
        ws = get_or_create_worksheet(sheet, "Alerts_Log", [
            "Timestamp", "Asset", "Alert_Type", "Message", "Score"
        ])
        if ws is None:
            return
        ws.append_row([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            asset,
            alert_type,
            message[:500],
            score,
        ])
    except Exception as e:
        logger.warning(f"log_alert error: {e}")


# =========================================================
# 5. SYMBOL HELPERS
# =========================================================
def resolve_yf_ticker(raw_symbol: str) -> str:
    clean = re.sub(r"[^A-Z0-9]", "", raw_symbol.upper())
    if clean in TICKER_MAP:
        return TICKER_MAP[clean]
    if len(clean) == 6:
        return f"{clean}=X"
    return clean


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
# 6. DATA FETCHING
# =========================================================
def fetch_4h_data(symbol: str, period: str = "1mo"):
    try:
        yf_symbol = resolve_yf_ticker(symbol)
        ticker = yf.Ticker(yf_symbol)
        df_1h = ticker.history(period=period, interval="1h")

        if df_1h.empty:
            return pd.DataFrame()

        for freq in ["4h", "4H", "240min", "240T"]:
            try:
                df_4h = df_1h.resample(freq).agg({
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum",
                }).dropna()
                if not df_4h.empty:
                    return df_4h
            except Exception:
                continue

        return pd.DataFrame()
    except Exception as e:
        logger.warning(f"fetch_4h_data({symbol}) error: {e}")
        return pd.DataFrame()


def fetch_15m_data(symbol: str):
    try:
        yf_symbol = resolve_yf_ticker(symbol)
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period="5d", interval="15m")
        return df if not df.empty else pd.DataFrame()
    except Exception as e:
        logger.warning(f"fetch_15m_data({symbol}) error: {e}")
        return pd.DataFrame()


# =========================================================
# 7. SWING & V-SHAPE DETECTION
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
            swing_highs.append((i, float(highs[i]), df.index[i]))
        if lows[i] == window_l.min():
            swing_lows.append((i, float(lows[i]), df.index[i]))

    return swing_highs, swing_lows


def detect_all_vshapes(df: pd.DataFrame, lookback: int = 5,
                       min_move_pct: float = 0.25,
                       recovery_ratio: float = 0.55,
                       max_bars: int = 6):
    vshapes = []
    if df.empty or len(df) < (lookback * 2 + max_bars + 2):
        return vshapes

    swing_highs, swing_lows = find_swing_points(df, lookback)

    # BULLISH V-SHAPES from swing lows
    for idx, low_price, low_time in swing_lows:
        if idx + max_bars >= len(df):
            continue

        left_high = float(df["High"].iloc[:idx].tail(20).max()) if idx > 0 else low_price
        if left_high <= low_price:
            continue

        drop_pct = ((left_high - low_price) / left_high) * 100
        if drop_pct < min_move_pct:
            continue

        future = df.iloc[idx + 1: idx + 1 + max_bars]
        if future.empty:
            continue

        max_recovery = float(future["High"].max())
        recovery_pct = ((max_recovery - low_price) / (left_high - low_price)) * 100

        if recovery_pct < recovery_ratio * 100:
            continue

        c = df.iloc[idx]
        o, cl, l = float(c["Open"]), float(c["Close"]), float(c["Low"])
        body = abs(cl - o)
        lower_wick = min(o, cl) - l

        if body > 0 and lower_wick <= body * 1.2:
            continue

        vshapes.append({
            "timestamp": str(low_time),
            "type": "BULLISH",
            "price": round(low_price, 5),
            "left_high": round(left_high, 5),
            "drop_pct": round(drop_pct, 2),
            "recovery_pct": round(recovery_pct, 1),
            "status": "PENDING",
            "swing_idx": idx,
            "swing_time": low_time,
        })

    # BEARISH V-SHAPES from swing highs
    for idx, high_price, high_time in swing_highs:
        if idx + max_bars >= len(df):
            continue

        left_low = float(df["Low"].iloc[:idx].tail(20).min()) if idx > 0 else high_price
        if left_low >= high_price:
            continue

        pump_pct = ((high_price - left_low) / left_low) * 100
        if pump_pct < min_move_pct:
            continue

        future = df.iloc[idx + 1: idx + 1 + max_bars]
        if future.empty:
            continue

        max_rejection = float(future["Low"].min())
        rejection_pct = ((high_price - max_rejection) / (high_price - left_low)) * 100

        if rejection_pct < recovery_ratio * 100:
            continue

        c = df.iloc[idx]
        o, cl, h = float(c["Open"]), float(c["Close"]), float(c["High"])
        body = abs(cl - o)
        upper_wick = h - max(o, cl)

        if body > 0 and upper_wick <= body * 1.2:
            continue

        vshapes.append({
            "timestamp": str(high_time),
            "type": "BEARISH",
            "price": round(high_price, 5),
            "left_low": round(left_low, 5),
            "pump_pct": round(pump_pct, 2),
            "rejection_pct": round(rejection_pct, 1),
            "status": "PENDING",
            "swing_idx": idx,
            "swing_time": high_time,
        })

    return vshapes


def check_if_swept(vshape: dict, df_4h: pd.DataFrame) -> bool:
    if df_4h.empty:
        return False

    swing_idx = vshape.get("swing_idx")
    if swing_idx is None:
        return False

    level = vshape["price"]
    future = df_4h.iloc[swing_idx + 1:]

    if future.empty:
        return False

    if vshape["type"] == "BULLISH":
        for _, c in future.iterrows():
            if float(c["Low"]) < level and float(c["Close"]) > level:
                return True
    else:
        for _, c in future.iterrows():
            if float(c["High"]) > level and float(c["Close"]) < level:
                return True

    return False


# =========================================================
# 8. 15M ENTRY TRIGGER
# =========================================================
def check_15m_entry_trigger(symbol: str, vshape: dict, df_15m: pd.DataFrame):
    if df_15m.empty or len(df_15m) < 3:
        return None

    last = df_15m.iloc[-1]
    o = float(last["Open"])
    cl = float(last["Close"])

    if vshape["type"] == "BULLISH":
        if cl > o:
            entry = cl
            sl = vshape["price"] - get_pip_size(symbol) * 5
            risk = entry - sl
            if risk <= 0:
                return None
            return {
                "action": "BUY",
                "entry": round(entry, 5),
                "sl": round(sl, 5),
                "tp1": round(entry + risk * 1.0, 5),
                "tp2": round(entry + risk * 3.0, 5),
            }
    else:
        if cl < o:
            entry = cl
            sl = vshape["price"] + get_pip_size(symbol) * 5
            risk = sl - entry
            if risk <= 0:
                return None
            return {
                "action": "SELL",
                "entry": round(entry, 5),
                "sl": round(sl, 5),
                "tp1": round(entry - risk * 1.0, 5),
                "tp2": round(entry - risk * 3.0, 5),
            }

    return None


# =========================================================
# 9. QUALITY SCORE
# =========================================================
def score_vshape(vshape: dict, vol_surge: float = 1.0):
    score = 0
    reasons = []

    if vshape["type"] == "BULLISH":
        pct = vshape.get("recovery_pct", 0)
    else:
        pct = vshape.get("rejection_pct", 0)

    if pct >= 80:
        score += 30
        reasons.append("Strong recovery")
    elif pct >= 60:
        score += 20
        reasons.append("Good recovery")
    else:
        score += 10
        reasons.append("Weak recovery")

    move = vshape.get("drop_pct") or vshape.get("pump_pct", 0)
    if move >= 0.5:
        score += 20
        reasons.append("Strong move")
    elif move >= 0.3:
        score += 12
        reasons.append("Moderate move")

    if vol_surge >= 1.3:
        score += 20
        reasons.append(f"Vol {vol_surge}x")

    score += 20
    reasons.append("V-Shape detected")
    score += 10

    if score >= 75:
        verdict = "HIGH QUALITY"
    elif score >= 55:
        verdict = "MEDIUM"
    else:
        verdict = "LOW"

    return {"score": min(score, 100), "verdict": verdict, "reasons": reasons}


# =========================================================
# 10. TELEGRAM ALERT SENDER
# =========================================================
async def send_alert(context: ContextTypes.DEFAULT_TYPE, message: str):
    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set - alert skipped")
        return
    try:
        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning(f"send_alert error: {e}")


# =========================================================
# 11. AUTO SCAN JOB
# =========================================================
async def auto_scan_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("=== Auto-scan starting ===")
    existing = load_all_vshapes()

    for asset in SCAN_ASSETS:
        try:
            await scan_asset(context, asset, existing)
        except Exception as e:
            logger.warning(f"scan_asset({asset}) error: {e}")

    logger.info("=== Auto-scan complete ===")


async def scan_asset(context: ContextTypes.DEFAULT_TYPE, asset: str, existing: dict):
    df_4h = fetch_4h_data(asset, period="1mo")
    if df_4h.empty:
        return

    vol_surge = 1.0
    if "Volume" in df_4h.columns and df_4h["Volume"].sum() > 0:
        recent_vol = float(df_4h["Volume"].tail(3).sum())
        avg_vol = float(df_4h["Volume"].mean()) * 3
        if avg_vol > 0:
            vol_surge = round(recent_vol / avg_vol, 2)

    vshapes = detect_all_vshapes(df_4h)

    for vs in vshapes:
        vs["asset"] = asset
        vs["id"] = f"VS_{normalize_symbol(asset)}_{vs['swing_time'].strftime('%Y%m%d_%H%M')}"

        if vs["id"] not in existing:
            score = score_vshape(vs, vol_surge)
            vs["score"] = score["score"]

            save_vshape(vs)
            log_alert(asset, "V_SHAPE_NEW", f"{vs['type']} V @ {vs['price']}", score["score"])

            msg = (
                f"🔍 *नई V-Shape मिली!*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"📊 *{asset}*\n"
                f"🎯 Type: *{vs['type']} V*\n"
                f"💰 Level: `{vs['price']}`\n"
                f"📉 Move: {vs.get('drop_pct') or vs.get('pump_pct')}%\n"
                f"📈 Recovery: {vs.get('recovery_pct') or vs.get('rejection_pct')}%\n"
                f"⭐ Score: *{score['score']}/100 - {score['verdict']}*\n"
                f"⏰ {vs['timestamp']}\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"⏳ Sweep का इंतज़ार...\n"
                f"_(Price वापस आकर इस level को sweep करेगी तो 15M entry alert आएगा)_"
            )
            await send_alert(context, msg)
            continue

        existing_vs = existing[vs["id"]]
        if existing_vs.get("Status") != "SWEPT":
            is_swept = check_if_swept(vs, df_4h)
            if is_swept:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                update_vshape_status(vs["id"], {"Status": "SWEPT", "Swept_At": now})
                log_alert(asset, "SWEEP_CONFIRMED", f"Sweep @ {vs['price']}", 0)

                msg = (
                    f"🎯 *SWEEP CONFIRMED!*\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"📊 *{asset}*\n"
                    f"💰 Swept Level: `{vs['price']}`\n"
                    f"🎯 Type: {vs['type']} Setup\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"⏳ *15M entry trigger का इंतज़ार...*\n"
                    f"_(जैसे ही 15M पर favorable candle close होगी, entry alert आएगा)_"
                )
                await send_alert(context, msg)

                await check_and_send_15m_entry(context, asset, vs)


async def check_and_send_15m_entry(context: ContextTypes.DEFAULT_TYPE, asset: str, vshape: dict):
    df_15m = fetch_15m_data(asset)
    if df_15m.empty:
        return

    entry = check_15m_entry_trigger(asset, vshape, df_15m)
    if entry:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        update_vshape_status(vshape["id"], {
            "Entry_Taken": "YES",
            "Entry_Price": entry["entry"],
        })
        log_alert(asset, "ENTRY_TRIGGER", f"{entry['action']} @ {entry['entry']}", 0)

        msg = (
            f"✅ *15M ENTRY TRIGGER CONFIRMED!*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📊 *{asset}*\n"
            f"🎯 Action: *{entry['action']}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"💰 *Entry:* `{entry['entry']}`\n"
            f"🔴 *SL:* `{entry['sl']}`\n"
            f"🎯 *TP1 (1:1):* `{entry['tp1']}` (50% बुक + SL to BE)\n"
            f"🎯 *TP2 (1:3):* `{entry['tp2']}`\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"⏰ {now} UTC"
        )
        await send_alert(context, msg)


# =========================================================
# 12. GEMINI (3.0 MODELS)
# =========================================================
def query_gemini_auto(prompt_text: str, image_bytes: bytes = None) -> str:
    if not GEMINI_API_KEY:
        return "Error: Gemini API Key missing."

    model_candidates = [
        "models/gemini-3.0-flash-lite",
        "models/gemini-3.1-flash-lite",
        "models/gemini-3.5-flash-lite",
        "models/gemini-3.5-flash",
    ]

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
    last_error = "Unknown"

    for model_endpoint in model_candidates:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"{model_endpoint}:generateContent?key={GEMINI_API_KEY}"
        )
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=45)
            j = r.json()

            if "candidates" in j and j["candidates"]:
                cand = j["candidates"][0]
                if "content" in cand and "parts" in cand["content"]:
                    return cand["content"]["parts"][0]["text"].strip()

            if "error" in j:
                err_msg = j["error"].get("message", "Unknown")
                last_error = err_msg
                if any(x in err_msg.lower() for x in ["not found", "no longer", "not supported", "deprecated"]):
                    continue
                return f"AI Error: {err_msg}"
        except Exception as e:
            last_error = str(e)
            continue

    return f"AI Error: All models failed. Last: {last_error}"


# =========================================================
# 13. TRADE EVALUATOR
# =========================================================
def evaluate_trade_logic(symbol: str, user_notes: str = "", image_bytes: bytes = None):
    df_4h = fetch_4h_data(symbol, period="1mo")
    df_15m = fetch_15m_data(symbol)

    if df_4h.empty:
        return "Data unavailable", {"ok": False}

    live_price = float(df_4h["Close"].iloc[-1])
    vshapes = detect_all_vshapes(df_4h)
    recent = [v for v in vshapes if v["swing_idx"] > len(df_4h) - 30][-5:]

    vshapes_txt = "कोई हाल की V-Shape नहीं मिली"
    if recent:
        lines = []
        for v in recent:
            lines.append(
                f"- {v['type']} V @ {v['price']} | "
                f"move {v.get('drop_pct') or v.get('pump_pct')}% | "
                f"recov {v.get('recovery_pct') or v.get('rejection_pct')}%"
            )
        vshapes_txt = "\n".join(lines)

    context = (
        f"Symbol: {symbol}\n"
        f"Live Price: {live_price}\n"
        f"4H Recent V-Shapes:\n{vshapes_txt}"
    )

    full_prompt = f"""
You are an Elite Institutional Trading Engine for a $2,500 The5ers account.
Evaluate this setup for {symbol}.

[REAL-TIME DATA]:
{context}

[USER NOTES]:
{user_notes}

[INSTRUCTIONS]:
1. Focus on 4H V-Shape + Sweep + 15M entry strategy.
2. BULLISH V = LONG bias. BEARISH V = SHORT bias.
3. ALWAYS give TWO-WAY plan (Bullish + Bearish).
4. The5ers rules: Risk $25 (1%), Max SL 40 pips, TP1 = 1:1 (50% book + SL to BE), TP2 = 1:3.

Respond in Hindi/Hinglish with this format:

MARKET STRUCTURE: [V-Shape Setup / Sweep Reversal]
LIVE DATA:
   - Price / Trend / Recent V-Shapes

BULLISH PLAN:
   - Trigger / Entry / SL / TP1 / TP2

BEARISH PLAN:
   - Trigger / Entry / SL / TP1 / TP2

MASTER VERDICT: [2 lines]
"""
    ai_reply = query_gemini_auto(full_prompt, image_bytes)
    return ai_reply, {"ok": True, "price": live_price}


# =========================================================
# 14. TELEGRAM HANDLERS
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TELEGRAM_CHAT_ID
    if not TELEGRAM_CHAT_ID:
        TELEGRAM_CHAT_ID = str(update.effective_chat.id)
        logger.info(f"Auto-set TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID}")

    msg = (
        "🚀 *V-Shape Tracker Online*\n"
        "━━━━━━━━━━━━━━━━━\n"
        "• 1-Month V-Shape Detection (4H)\n"
        "• Auto-scan every 15 minutes\n"
        "• Google Sheet backup\n"
        "• 15M Entry Trigger alerts\n"
        "• 3-Stage Alerts:\n"
        "  1️⃣ नई V-Shape मिली\n"
        "  2️⃣ Sweep Confirmed\n"
        "  3️⃣ Entry Trigger\n"
        "━━━━━━━━━━━━━━━━━\n"
        "*Commands:*\n"
        "/start - ये message\n"
        "/scan - Manual scan (सभी assets)\n"
        "/check SYMBOL - Full analysis\n"
        "/vshapes - Sheet से सारी V-Shapes\n"
        "/status - Bot status\n"
        "\n_या seedha चार्ट भेजें!_"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Manual scan शुरू... 1 month data check कर रहे हैं।")
    existing = load_all_vshapes()

    total_new = 0
    total_swept = 0

    for asset in SCAN_ASSETS:
        df_4h = fetch_4h_data(asset, period="1mo")
        if df_4h.empty:
            continue

        vshapes = detect_all_vshapes(df_4h)
        for vs in vshapes:
            vs["asset"] = asset
            vs["id"] = f"VS_{normalize_symbol(asset)}_{vs['swing_time'].strftime('%Y%m%d_%H%M')}"

            if vs["id"] not in existing:
                save_vshape(vs)
                total_new += 1
            elif existing[vs["id"]].get("Status") != "SWEPT":
                if check_if_swept(vs, df_4h):
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
                    update_vshape_status(vs["id"], {"Status": "SWEPT", "Swept_At": now})
                    total_swept += 1

    await update.message.reply_text(
        f"✅ Scan complete!\n"
        f"🆕 New V-Shapes: {total_new}\n"
        f"🎯 Swept: {total_swept}"
    )


async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    symbol = args[0].upper() if args else "GOLD"
    await update.message.reply_text(f"🔍 {symbol} की analysis बन रही है...")
    res
