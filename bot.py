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
    "SOL": "SOL-USD",
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
    "SOLUSD": [73.39, 122.76, 182.99, 209.65, 253.09],
}

SPECIAL_LEVELS = {
    "XAUUSD": [4030.0, 4062.0],
    "BTCUSD": [80750.0],
}

# =========================================================
# 3. RELIABLE MULTI-FEED TICK ENGINE
# =========================================================
def get_live_tick(symbol: str) -> float:
    sym = symbol.upper()
    
    if sym in ["BTCUSD", "BTC", "ETHUSD", "ETH", "SOLUSD", "SOL"]:
        pair = sym.replace("USD", "") + "USDT"
        try:
            r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={pair}", timeout=5)
            if r.status_code == 200:
                return float(r.json().get("price", 0.0))
        except Exception:
            pass

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
                "data": base64.b64encode(image_bytes).decode("utf-8"),
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
def background_price_watcher():
    global LAST_KNOWN_PRICE, LEVEL_ALERT_COOLDOWN, ACTIVE_CHAT_ID
    time.sleep(15)
    logger.info("=== बैकग्राउंड लेवल ट्रैकर चालू है ===")

    while True:
        try:
            target_chat = ACTIVE_CHAT_ID
            if not target_chat:
                time.sleep(10)
                continue

            all_symbols = set(list(USER_KEY_LEVELS.keys()) + list(SPECIAL_LEVELS.keys()))

            for sym in all_symbols:
                current_price = get_live_tick(sym)
                if current_price <= 0.0:
                    continue

                old_price = LAST_KNOWN_PRICE.get(sym)
                if old_price is None or old_price <= 0.0:
                    LAST_KNOWN_PRICE[sym] = current_price
                    continue

                now = time.time()

                for lvl in SPECIAL_LEVELS.get(sym, []):
                    alert_key = f"SPECIAL_{sym}_{lvl}"
                    if now - LEVEL_ALERT_COOLDOWN.get(alert_key, 0) < COOLDOWN_SECONDS:
                        continue

                    if old_price < lvl and current_price >= lvl:
                        msg = (
                            f"🚨 *[MAJOR LEVEL BULLISH BREAKOUT]*\n\n"
                            f"• एसेट: *{sym}*\n"
                            f"• लेवल: `{lvl}` (मेजर स्तर)\n"
                            f"• लाइव भाव: `{current_price}`\n"
                            f"• एक्शन: प्राइस ने मेजर लेवल को नीचे से ऊपर तोड़ा है।"
                        )
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": target_chat, "text": msg, "parse_mode": "Markdown"},
                        )
                        LEVEL_ALERT_COOLDOWN[alert_key] = now

                    elif old_price > lvl and current_price <= lvl:
                        msg = (
                            f"🚨 *[MAJOR LEVEL BEARISH BREAKDOWN]*\n\n"
                            f"• एसेट: *{sym}*\n"
                            f"• लेवल: `{lvl}` (मेजर स्तर)\n"
                            f"• लाइव भाव: `{current_price}`\n"
                            f"• एक्शन: प्राइस ने मेजर लेवल को ऊपर से नीचे तोड़ा है।"
                        )
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": target_chat, "text": msg, "parse_mode": "Markdown"},
                        )
                        LEVEL_ALERT_COOLDOWN[alert_key] = now

                for lvl in USER_KEY_LEVELS.get(sym, []):
                    alert_key = f"NORMAL_{sym}_{lvl}"
                    if now - LEVEL_ALERT_COOLDOWN.get(alert_key, 0) < COOLDOWN_SECONDS:
                        continue

                    if old_price < lvl and current_price >= lvl:
                        msg = (
                            f"🟢 *[KEY LEVEL BULLISH CROSS]*\n\n"
                            f"• एसेट: *{sym}*\n"
                            f"• लेवल: `{lvl}`\n"
                            f"• लाइव भाव: `{current_price}`\n"
                            f"• स्थिति: नीचे से ऊपर क्रॉसिंग हुई।"
                        )
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": target_chat, "text": msg, "parse_mode": "Markdown"},
                        )
                        LEVEL_ALERT_COOLDOWN[alert_key] = now

                    elif old_price > lvl and current_price <= lvl:
                        msg = (
                            f"🔴 *[KEY LEVEL BEARISH CROSS]*\n\n"
                            f"• एसेट: *{sym}*\n"
                            f"• लेवल: `{lvl}`\n"
                            f"• लाइव भाव: `{current_price}`\n"
                            f"• स्थिति: ऊपर से नीचे क्रॉसिंग हुई।"
                        )
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": target_chat, "text": msg, "parse_mode": "Markdown"},
                        )
                        LEVEL_ALERT_COOLDOWN[alert_key] = now

                LAST_KNOWN_PRICE[sym] = current_price
                time.sleep(1)

        except Exception as e:
            logger.error(f"वॉचर लूप एरर: {e}")

        time.sleep(15)

# =========================================================
# 7. टेलीग्राम कमांड्स और मैसेज हैंडलर
# =========================================================
def sync_chat_id(update: Update):
    global ACTIVE_CHAT_ID
    if update.effective_chat:
        ACTIVE_CHAT_ID = str(update.effective_chat.id)
        save_chat_id(ACTIVE_CHAT_ID)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sync_chat_id(update)
    msg = (
        f"🚀 *सूरज स्नाइपर लेवल ट्रैकर ऑनलाइन!*\n\n"
        f"• आपकी चैट ID लिंक हो गई: `{ACTIVE_CHAT_ID}`\n"
        f"• सभी लेवल्स (Normal + Major) एक्टिव हैं।\n"
        f"• 2 घंटे का स्मार्ट कूलडाउन एक्टिव है।\n\n"
        f"📌 *कमांड्स:*\n"
        f"• `/levels` : सभी एक्टिव लेवल्स देखें\n"
        f"• `/viewlevels XAUUSD` : किसी पेयर के पूरे लेवल्स देखें\n"
        f"• `/addlevel XAUUSD 4150.5` : नॉर्मल लेवल जोड़ें\n"
        f"• `/addmajor XAUUSD 4200.0` : मेजर लेवल जोड़ें\n"
        f"• `/clearlevels XAUUSD` : लेवल्स खाली करें\n"
        f"• चार्ट की फोटो भेजें, जेमिनी पूरा एक्शन प्लान देगी।"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sync_chat_id(update)
    lines = ["📡 *एक्टिव मॉनिटर लेवल्स:*\n"]
    for sym, lvls in USER_KEY_LEVELS.items():
        majors = SPECIAL_LEVELS.get(sym, [])
        m_txt = f" | ⭐ मेजर: {majors}" if majors else ""
        lines.append(f"🔹 *{sym}* (कुल {len(lvls)} लेवल्स){m_txt}")
    lines.append("\n_डिटेल्स देखने के लिए `/viewlevels XAUUSD` लिखें।_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def view_levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sync_chat_id(update)
    try:
        sym = context.args[0].upper()
        if sym in ["GOLD"]:
            sym = "XAUUSD"
        lvls = USER_KEY_LEVELS.get(sym, [])
        majors = SPECIAL_LEVELS.get(sym, [])
        await update.message.reply_text(
            f"📊 *{sym} लेवल्स:*\n\n• नॉर्मल: `{lvls}`\n• मेजर: `{majors}`",
            parse_mode="Markdown",
        )
    except Exception:
        await update.message.reply_text("उपयोग: `/viewlevels XAUUSD`", parse_mode="Markdown")

async def add_normal_level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sync_chat_id(update)
    try:
        args = context.args
        sym = re.sub(r"[^A-Z0-9]", "", args[0].upper())
        if sym in ["GOLD"]:
            sym = "XAUUSD"
        raw_text = "".join(args[1:])
        new_lvls = [float(x.strip()) for x in raw_text.split(",") if x.strip()]
        
        current = USER_KEY_LEVELS.get(sym, [])
        combined = sorted(list(set(current + new_lvls)))
        USER_KEY_LEVELS[sym] = combined
        await update.message.reply_text(f"✅ *{sym}* में लेवल्स जुड़ गए (कुल {len(combined)}):\n`{new_lvls}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"एरर: {e}")

async def add_major_level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sync_chat_id(update)
    try:
        args = context.args
        sym = re.sub(r"[^A-Z0-9]", "", args[0].upper())
        if sym in ["GOLD"]:
            sym = "XAUUSD"
        raw_text = "".join(args[1:])
        new_lvls = [float(x.strip()) for x in raw_text.split(",") if x.strip()]
        
        current = SPECIAL_LEVELS.get(sym, [])
        combined = sorted(list(set(current + new_lvls)))
        SPECIAL_LEVELS[sym] = combined
        await update.message.reply_text(f"🚨 *{sym}* में ⭐ **मेजर लेवल्स** जुड़ गए:\n`{combined}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"एरर: {e}")

async def clear_levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sync_chat_id(update)
    try:
        sym = re.sub(r"[^A-Z0-9]", "", context.args[0].upper())
        if sym in ["GOLD"]:
            sym = "XAUUSD"
        USER_KEY_LEVELS[sym] = []
        SPECIAL_LEVELS[sym] = []
        await update.message.reply_text(f"🗑️ *{sym}* के सभी लेवल्स क्लियर कर दिए गए हैं।", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"एरर: {e}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sync_chat_id(update)
    await update.message.reply_text("👁️ चार्ट स्कैन हो रहा है, जेमिनी एक्शन प्लान बना रही है...")
    try:
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        caption = update.message.caption or ""

        prompt = (
            "आप $2,500 The5ers हाई स्टेक्स खाते के संस्थागत रिस्क मैनेजर हैं।\n"
            f"इस ट्रेडिंग चार्ट का विश्लेषण करें: {caption}\n\n"
            "नियम:\n"
            "1. चार्ट के दाहिने प्राइस स्केल पर जो लाइव भाव दिख रहा है, उसी को आधार मानें।\n"
            "2. The5ers $2,500 अकाउंट: फिक्स रिस्क $25 (1%)।\n"
            "3. मैक्सिमम स्टॉप लॉस 40 पिप्स (गोल्ड में $1 = 10 पिप्स, यानी अधिकतम $4 का SL)।\n"
            "4. 2-ट्रिगर ऑर्डर: 1:1 RRR पर 50% लॉट बुक + SL कॉस्ट पर, और 1:3 RRR मुख्य रनर।\n"
            "5. केवल अवॉइड न कहें, बुलिश और बेयरिश दोनों तरफ का एक्शन प्लान दें।\n\n"
            "इस फॉर्मेट में उत्तर दें:\n"
            "🎯 मार्केट स्ट्रक्चर: [सपोर्ट/रेजिस्टेंस व ट्रेंड]\n"
            "📊 पहचाना गया भाव: [Live Price]\n\n"
            "🟢 बुलिश प्लान:\n"
            "• एंट्री: [भाव]\n• स्टॉप लॉस: [भाव]\n• टारगेट 1: [भाव]\n• टारगेट 2: [भाव]\n\n"
            "🔴 बेयरिश प्लान:\n"
            "• एंट्री: [भाव]\n• स्टॉप लॉस: [भाव]\n• टारगेट 1: [भाव]\n• टारगेट 2: [भाव]\n\n"
            "💡 मास्टर फैसला: [तुरंत क्या करना है]"
        )
        res = query_gemini(prompt, bytes(image_bytes))
        await update.message.reply_text(res)
    except Exception as e:
        await update.message.reply_text(f"इमेज स्कैन एरर: {e}")

# =========================================================
# 8. TELEGRAM BOT THREAD RUNNER
# =========================================================
def run_telegram_polling():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN नहीं मिला!")
        return

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("levels", levels_cmd))
    app.add_handler(CommandHandler("viewlevels", view_levels_cmd))
    app.add_handler(CommandHandler(["addlevel", "addlevels"], add_normal_level_cmd))
    app.add_handler(CommandHandler(["addmajor", "addmajorlevel"], add_major_level_cmd))
    app.add_handler(CommandHandler("clearlevels", clear_levels_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("=== Telegram Polling चालू हो रहा है ===")
    app.run_polling(drop_pending_updates=True)

# =========================================================
# 9. मुख्य प्रोसेस
# =========================================================
def main():
    t_bot = threading.Thread(target=run_telegram_polling, daemon=True)
    t_bot.start()

    t_watcher = threading.Thread(target=background_price_watcher, daemon=True)
    t_watcher.start()

    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Flask Web Server Port {port} पर चल रहा है...")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
