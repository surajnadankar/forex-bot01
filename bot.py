import os
import io
import re
import json
import base64
import logging
import threading
import time
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask
import yfinance as yf

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# =========================================================
# 1. CONFIG & LOGGING
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

CHAT_ID_FILE = "chat_id.txt"

def load_chat_id():
    if os.path.exists(CHAT_ID_FILE):
        try:
            with open(CHAT_ID_FILE, "r") as f:
                return f.read().strip()
        except Exception:
            pass
    return os.getenv("ALLOWED_CHAT_ID", "").strip()

def save_chat_id(cid):
    if cid:
        try:
            with open(CHAT_ID_FILE, "w") as f:
                f.write(str(cid))
        except Exception as e:
            logger.warning(f"Chat ID save error: {e}")

ACTIVE_CHAT_ID = load_chat_id()

# मेमोरी स्टेट
LAST_KNOWN_PRICE = {}
LEVEL_ALERT_COOLDOWN = {}
COOLDOWN_SECONDS = 7200  # 2 घंटे का कूलडाउन

# =========================================================
# 2. SYMBOL MAP & USER LEVELS
# =========================================================
TICKER_MAP = {
    "XAUUSD": "XAUUSD=X",
    "GOLD": "XAUUSD=X",
    "SILVER": "XAGUSD=X",
    "XAGUSD": "XAGUSD=X",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "CAD=X",
    "US30": "^DJI",
    "NAS100": "^IXIC",
    "DAX40": "^GDAXI",
    "UK100": "^FTSE",
    "BTCUSD": "BTC-USD",
    "BTC": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "ETH": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "SOL": "SOL-USD"
}

USER_KEY_LEVELS = {
    "XAUUSD": [276.436, 2561.073, 2924.338, 3213.15, 3288.019, 3401.552, 3917.805, 3959.981, 3996.199, 4231.59, 4399.14, 4519.006, 4666.543, 4868.081, 5451.16, 5602.225],
    "EURUSD": [0.5633, 0.82311, 0.96756, 1.01829, 1.07362, 1.10763, 1.1459, 1.17075, 1.18448, 1.19227, 1.20728, 1.22827, 1.4102, 1.51442, 1.6038],
    "GBPUSD": [1.0545, 1.1832, 1.21, 1.2709, 1.301, 1.3098, 1.314, 1.3308, 1.3402, 1.3566, 1.3673, 1.387, 1.593, 1.7191, 1.8331, 2.01, 2.1161, 2.446, 2.644],
    "USDJPY": [76.161, 101.18, 127.22, 139.889, 146.478, 149.578, 152.206, 153.003, 156.498, 158.994, 160.201, 160.329, 163.988],
    "EURJPY": [115.29, 134.034, 139.524, 148.617, 155.192, 167.779, 174.954, 180.965, 186.157],
    "GBPJPY": [128.659, 157.536, 179.781, 196.201, 207.605, 213.315, 239.86, 247.393],
    "AUDUSD": [0.49047, 0.64412, 0.68787, 0.7219, 0.80424, 0.94974, 1.10005, 1.189],
    "USDCAD": [0.90585, 0.94469, 1.06204, 1.2013, 1.29517, 1.34818, 1.37708, 1.38291, 1.389741, 1.39812, 1.41546, 1.42964, 1.43952, 1.44514, 1.4542, 1.47937],
    "US30": [21645.7, 28530.8, 33960.5, 36482.6, 40068.0, 44822.3, 49108.9, 50730.0, 53286.4],
    "NAS100": [16508.4, 22339.0, 26255.1, 30678.0, 108511.9],
    "DAX40": [8025.0, 12010.88, 13397.3, 16056.55, 21956.1, 26215.18],
    "UK100": [7787.2, 8398.4, 8847.3, 9587.2, 9880.5, 10191.8, 10461.2, 10863.4, 10941.1],
    "BTCUSD": [61556.58, 73669.56, 74988.74, 75633.23, 76697.17, 78251.18, 79274.78, 80240.59, 82679.42, 83628.36, 85686.88, 87345.85, 87701.49, 88982.81, 90385.0, 91193.63, 91711.6, 92668.922, 93167.85, 96916.06, 98920.67, 101245.6, 102730.93, 106474.49, 117802.73, 126192.07],
    "ETHUSD": [1065.31, 1563.76, 1970.47, 2220.13, 2407.85, 2760.46, 3014.54, 3776.62, 4827.97],
    "SOLUSD": [73.39, 122.76, 182.99, 209.65, 253.09]
}

SPECIAL_LEVELS = {
    "XAUUSD": [4030.0, 4062.0],
    "BTCUSD": [80750.0]
}

# =========================================================
# 3. RELIABLE MULTI-FEED TICK ENGINE
# =========================================================
def get_live_tick(symbol: str) -> float:
    sym = symbol.upper()
    
    # 1. क्रिप्टो के लिए Binance API
    if sym in ["BTCUSD", "BTC", "ETHUSD", "ETH", "SOLUSD", "SOL"]:
        pair = sym.replace("USD", "") + "USDT"
        try:
            r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={pair}", timeout=5)
            if r.status_code == 200:
                return float(r.json()["price"])
        except Exception:
            pass

    # 2. फॉरेक्स, मेटल्स और इंडाइसेस के लिए REST API
    ticker = TICKER_MAP.get(sym, sym)
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            data = res.json()
            meta = data["chart"]["result"][0]["meta"]
            if "regularMarketPrice" in meta and meta["regularMarketPrice"]:
                return float(meta["regularMarketPrice"])
    except Exception:
        pass

    try:
        t = yf.Ticker(ticker)
        p = t.fast_info.last_price
        if p:
            return float(p)
    except Exception:
        pass

    return 0.0

# =========================================================
# 4. FLASK WEB SERVER (PORT 10000)
# =========================================================
flask_app = Flask(__name__)

@flask_app.route("/")
@flask_app.route("/health")
def health():
    return "Suraj Sniper Multi-Asset Bot Online 200 OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# =========================================================
# 5. GEMINI AI VISION
# =========================================================
def query_gemini(prompt_text: str, image_bytes: bytes = None) -> str:
    if not GEMINI_API_KEY:
        return "Gemini API Key मौजूद नहीं है।"

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    parts = [{"text": prompt_text}]
    if image_bytes:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(image_bytes).decode("utf-8")
            }
        })

    payload = {"contents": [{"parts": parts}]}
    try:
        resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
        res_json = resp.json()
        if "candidates" in res_json and res_json["candidates"]:
            cand = res_json["candidates"][0]
            if "content" in cand and "parts" in cand["content"]:
                return cand["content"]["parts"][0]["text"].strip()
    except Exception as e:
        return f"AI विश्लेषण एरर: {e}"
    return "AI से उत्तर प्राप्त नहीं हुआ।"

# =========================================================
# 6. बैकग्राउंड लेवल ट्रैकर
# =========================================================
def background_price_watcher(bot_app):
    global LAST_KNOWN_PRICE, LEVEL_ALERT_COOLDOWN, ACTIVE_CHAT_ID
    time.sleep
