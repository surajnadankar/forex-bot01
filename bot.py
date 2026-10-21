import os
import asyncio
import logging
import threading
from datetime import datetime, timezone

import yfinance as yf
from flask import Flask

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler, # नया जोड़ा गया है
    filters,        # नया जोड़ा गया है
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

# चैट आईडी को फाइल में सेव करने का फंक्शन ताकि रीस्टार्ट पर भी याद रहे
CHAT_ID_FILE = "chat_id.txt"

def load_chat_id():
    if os.path.exists(CHAT_ID_FILE):
        with open(CHAT_ID_FILE, "r") as f:
            return f.read().strip()
    return os.getenv("TELEGRAM_CHAT_ID", "").strip()

def save_chat_id(chat_id):
    with open(CHAT_ID_FILE, "w") as f:
        f.write(str(chat_id))

TELEGRAM_CHAT_ID = load_chat_id()

# =========================================================
# 2. TICKER MAP (Yahoo Finance Symbols)
# =========================================================
TICKER_MAP = {
    "XAUUSD": "GC=F", "GOLD": "GC=F",
    "XAGUSD": "SI=F", "SILVER": "SI=F",
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X", "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X", "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X", "US30": "^DJI",
    "NAS100": "^IXIC", "DAX40": "^GDAXI",
    "UK100": "^FTSE", "XTIUSD": "CL=F",
    "BTCUSD": "BTC-USD", "BTC": "BTC-USD",
    "ETHUSD": "ETH-USD", "ETH": "ETH-USD",
    "SOLUSD": "SOL-USD", "SOL": "SOL-USD",
}

# =========================================================
# 3. NORMAL KEY LEVELS
# =========================================================
USER_KEY_LEVELS = {
    "XAUUSD": [276.436, 2561.073, 2924.338, 3213.15, 3288.019, 3401.552, 3917.805, 3959.981, 3996.199, 4231.59, 4399.14, 4519.006, 4666.543, 4868.081, 5451.16, 5602.225],
    "XAGUSD": [],
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
    "XTIUSD": [],
    "BTCUSD": [61556.58, 73669.56, 74988.74, 75633.23, 76697.17, 78251.18, 79274.78, 80240.59, 82679.42, 83628.36, 85686.88, 87345.85, 87701.49, 88982.81, 90385.0, 91193.63, 91711.6, 92668.922, 93167.85, 96916.06, 98920.67, 101245.6, 102730.93, 106474.49, 117802.73, 126192.07],
    "ETHUSD": [1065.31, 1563.76, 1970.47, 2220.13, 2407.85, 2760.46, 3014.54, 3776.62, 4827.97],
    "SOLUSD": [73.39, 122.76, 182.99, 209.65, 253.09]
}

SPECIAL_LEVELS = {}
LEVEL_ALERT_COOLDOWN = {}
COOLDOWN_SECONDS = 3600  # 1 घंटा

# =========================================================
# 4. FLASK SERVER & KEEP-ALIVE
# =========================================================
web_app = Flask(__name__)

@web_app.route("/")
@web_app.route("/health")
def health():
    return "Level Alert Bot is Online", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# Render को जगाए रखने के लिए खुद को पिंग करने वाला फंक्शन
async def keep_alive_job():
    while True:
        try:
            import requests
            # अपनी Render सर्विस का URL यहाँ डालें
            requests.get("https://forex-bot01.onrender.com/health", timeout=10)
            logger.info("Keep-alive ping sent.")
        except Exception:
            pass
        await asyncio.sleep(600) # हर 10 मिनट में पिंग करेगा

# =========================================================
# 5. TELEGRAM ALERTS
# =========================================================
async def send_alert(context: ContextTypes.DEFAULT_TYPE, message: str):
    global TELEGRAM_CHAT_ID
    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID is not set. Alert skipped.")
        return
    try:
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Failed to send alert: {e}")

# =========================================================
# 6. LEVEL CHECKING JOB
# =========================================================
def get_live_price(symbol: str) -> float:
    try:
        yf_symbol = TICKER_MAP.get(symbol)
        if not yf_symbol:
            return None
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period="1d", interval="1m")
        if not df.empty:
            return float(df["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"Price fetch error for {symbol}: {e}")
    return None

def is_on_cooldown(level_key, now):
    if level_key in LEVEL_ALERT_COOLDOWN:
        time_passed = (now - LEVEL_ALERT_COOLDOWN[level_key]).total_seconds()
        if time_passed < COOLDOWN_SECONDS:
            return True
    return False

async def check_key_levels_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("=== Checking Key Levels ===")
    now = datetime.now(timezone.utc)
    active_symbols = set(list(USER_KEY_LEVELS.keys()) + list(SPECIAL_LEVELS.keys()))
    
    for symbol in active_symbols:
        if symbol not in TICKER_MAP:
            continue
            
        current_price = get_live_price(symbol)
        if current_price is None:
            await asyncio.sleep(2)
            continue
            
        # 🟢 नॉर्मल लेवल्स चेक
        if symbol in USER_KEY_LEVELS:
            for level in USER_KEY_LEVELS[symbol]:
                level_key = f"NORMAL_{symbol}_{level}"
                
                # 🎯 स्मार्ट टॉलरेंस: लेवल से 0.5% ऊपर या नीचे तक का दायरा
                tolerance = level * 0.005 
                if abs(current_price - level) <= tolerance:
                    if is_on_cooldown(level_key, now): continue
                    
                    direction = "⬆️ UPWARDS" if current_price >= level else "⬇️ DOWNWARDS"
                    msg = (
                        f"🟢 *NORMAL LEVEL CROSSED*\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f"📊 *Asset:* {symbol}\n"
                        f"🎯 *Level:* `{level}`\n"
                        f"💰 *Price:* `{current_price:.5f}`\n"
                        f"📉 *Direction:* {direction}\n"
                        f"⏳ _1 Hour Cooldown_"
                    )
                    await send_alert(context, msg)
                    LEVEL_ALERT_COOLDOWN[level_key] = now
        
        # 🚨 मेजर लेवल्स चेक
        if symbol in SPECIAL_LEVELS:
            for level in SPECIAL_LEVELS[symbol]:
                level_key = f"SPECIAL_{symbol}_{level}"
                tolerance = level * 0.005
                if abs(current_price - level) <= tolerance:
                    if is_on_cooldown(level_key, now): continue
                    
                    direction = "⬆️ UPWARDS" if current_price >= level else "⬇️ DOWNWARDS"
                    msg = (
                        f"🚨🚨 **SPECIAL MAJOR ALERT** 🚨🚨\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f"⚠️ *MAJOR LEVEL HAS BEEN CROSSED!*\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f"📊 *Asset:* **{symbol}**\n"
                        f"🎯 *Major Level:* `{level}`\n"
                        f"💰 *Current Price:* `{current_price:.5f}`\n"
                        f"📉 *Direction:* {direction}\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f"🔥 *ध्यान से ट्रेड करें!*\n"
                        f"⏳ _1 Hour Cooldown_"
                    )
                    await send_alert(context, msg)
                    LEVEL_ALERT_COOLDOWN[level_key] = now

        await asyncio.sleep(2)

# =========================================================
# 7. TELEGRAM COMMANDS
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TELEGRAM_CHAT_ID
    TELEGRAM_CHAT_ID = str(update.effective_chat.id)
    save_chat_id(TELEGRAM_CHAT_ID) # फाइल में सेव कर दिया
    
    msg = (
        "🚀 *Level Alert Bot Online*\n"
        "━━━━━━━━━━━━━━━━━\n"
        "✅ मैंने आपकी Chat ID सेव कर ली है। अब अलर्ट पक्का आएंगे।\n\n"
        "*कमांड्स:*\n"
        "👉 `/levels` (सारे लेवल्स देखें)\n"
        "👉 `/addlevel GOLD 4350` (नया नॉर्मल लेवल)\n"
        "👉 `/addmajor GOLD 4500` (नया मेजर लेवल)"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_symbols = set(list(USER_KEY_LEVELS.keys()) + list(SPECIAL_LEVELS.keys()))
    total_normal = sum(len(v) for v in USER_KEY_LEVELS.values())
    total_special = sum(len(v) for v in SPECIAL_LEVELS.values())
    
    await update.message.reply_text(
        f"⏳ *लेवल्स की लिस्ट आ रही है...*\n"
        f"🟢 नॉर्मल: {total_normal} | 🚨 मेजर: {total_special}", 
        parse_mode="Markdown"
    )

    for symbol in sorted(active_symbols):
        normal_lvls = USER_KEY_LEVELS.get(symbol, [])
        major_lvls = SPECIAL_LEVELS.get(symbol, [])
        if not normal_lvls and not major_lvls: continue
            
        text = f"📊 *Asset: {symbol}*\n"
        text += "━━━━━━━━━━━━━━━━━\n"
        if normal_lvls: text += f"🟢 *Normal:*\n`{', '.join(map(str, normal_lvls))}`\n\n"
        if major_lvls: text += f"🚨 *Major:*\n`{', '.join(map(str, major_lvls))}`\n"
            
        await update.message.reply_text(text, parse_mode="Markdown")
        await asyncio.sleep(0.5)

async def add_normal_level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) != 2: 
            return await update.message.reply_text("⚠️ फॉर्मेट: `/addlevel GOLD 4350`", parse_mode="Markdown")
        symbol, price = args[0].upper(), float(args[1])
        if symbol not in TICKER_MAP: 
            return await update.message.reply_text(f"❌ {symbol} सिंबल नहीं है।")
        if symbol not in USER_KEY_LEVELS: 
            USER_KEY_LEVELS[symbol] = []
        USER_KEY_LEVELS[symbol].append(price)
        await update.message.reply_text(f"✅ *{symbol}* में लेवल जुड़ गया: `{price}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ एरर: {str(e)}")

async def add_major_level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) != 2: 
            return await update.message.reply_text("⚠️ फॉर्मेट: `/addmajor GOLD 4500`", parse_mode="Markdown")
        symbol, price = args[0].upper(), float(args[1])
        if symbol not in TICKER_MAP: 
            return await update.message.reply_text(f"❌ {symbol} सिंबल नहीं है।")
        if symbol not in SPECIAL_LEVELS: 
            SPECIAL_LEVELS[symbol] = []
        SPECIAL_LEVELS[symbol].append(price)
        await update.message.reply_text(f"🚨 *{symbol}* में मेजर लेवल जुड़ गया: `{price}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ एरर: {str(e)}")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"🤖 *Bot Status:* Online\n"
        f"📊 *Assets:* {len(USER_KEY_LEVELS)} pairs\n"
        f"⏳ *Cooldowns:* {len(LEVEL_ALERT_COOLDOWN)} active\n"
        f"⏱️ *Interval:* 15 min"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# =========================================================
# 8. MAIN FUNCTION
# =========================================================
def main():
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN missing!")
        return

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(30).write_timeout(30).connect_timeout(15).pool_timeout(5)
        .get_updates_read_timeout(60).get_updates_connect_timeout(15).get_updates_pool_timeout(10)
        .build()
    )

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.warning(f"Update {update} caused error: {context.error}")

    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("levels", levels_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    
    # ✅ दोनों स्पेलिंग (/addlevel और /addleve) के लिए एक साथ हैंडलर
    app.add_handler(CommandHandler(["addlevel", "addleve"], add_normal_level_cmd))
    app.add_handler(CommandHandler("addmajor", add_major_level_cmd))
    
    # ✅ अगर कोई गलत कमांड टाइप करे तो जवाब दे
    async def unknown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("❌ यह कमांड मौजूद नहीं है। सही कमांड्स के लिए /start भेजें।")
    
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))

    if app.job_queue:
        # 15 मिनट पर लेवल चेक
        app.job_queue.run_repeating(check_key_levels_job, interval=15 * 60, first=30)
        # खुद को जगाए रखने वाला जॉब
        app.job_queue.run_once(lambda context: asyncio.create_task(keep_alive_job()), when=5)
        
        logger.info("JobQueue started.")

    logger.info("Bot running...")
    app.run_polling(drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    main()
