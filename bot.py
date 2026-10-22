import os
import asyncio
import logging
import threading
from datetime import datetime, timezone

import yfinance as yf
from flask import Flask, request

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
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "https://forex-bot01.onrender.com").strip()

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
# 2. TICKER MAP
# =========================================================
TICKER_MAP = {
    "XAUUSD": "GC=F", "GOLD": "GC=F", "XAGUSD": "SI=F", "SILVER": "SI=F",
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X", "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X", "AUDUSD": "AUDUSD=X", "USDCAD": "USDCAD=X", "US30": "^DJI",
    "NAS100": "^IXIC", "DAX40": "^GDAXI", "UK100": "^FTSE", "XTIUSD": "CL=F",
    "BTCUSD": "BTC-USD", "BTC": "BTC-USD", "ETHUSD": "ETH-USD", "ETH": "ETH-USD",
    "SOLUSD": "SOL-USD", "SOL": "SOL-USD",
}

# =========================================================
# 3. KEY LEVELS
# =========================================================
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

SPECIAL_LEVELS = {}
LEVEL_ALERT_COOLDOWN = {}
COOLDOWN_SECONDS = 3600

# =========================================================
# 4. FLASK APP
# =========================================================
flask_app = Flask(__name__)
telegram_app = None

@flask_app.route("/")
@flask_app.route("/health")
def health():
    return "Webhook Bot is Online", 200

@flask_app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
async def webhook():
    if telegram_app:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        await telegram_app.update_queue.put(update)
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# =========================================================
# 5. HELPER FUNCTIONS
# =========================================================
async def send_alert(context: ContextTypes.DEFAULT_TYPE, message: str):
    global TELEGRAM_CHAT_ID
    if not TELEGRAM_CHAT_ID:
        return
    try:
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Failed to send alert: {e}")

def get_live_price_and_range(symbol: str) -> dict:
    try:
        yf_symbol = TICKER_MAP.get(symbol)
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period="1d", interval="1m")
        if not df.empty:
            recent = df.tail(2)
            return {
                "current": float(recent["Close"].iloc[-1]),
                "high": float(recent["High"].max()),
                "low": float(recent["Low"].min())
            }
    except Exception:
        pass
    return None

def get_prev_day_high_low(symbol: str) -> dict:
    try:
        yf_symbol = TICKER_MAP.get(symbol)
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period="5d", interval="1d")
        if len(df) >= 2:
            prev_day = df.iloc[-2]
            return {
                "pdh": float(prev_day["High"]),
                "pdl": float(prev_day["Low"])
            }
    except Exception:
        pass
    return None

def is_on_cooldown(level_key, now):
    if level_key in LEVEL_ALERT_COOLDOWN:
        time_passed = (now - LEVEL_ALERT_COOLDOWN[level_key]).total_seconds()
        if time_passed < COOLDOWN_SECONDS:
            return True
    return False

# =========================================================
# 6. LEVEL CHECKING JOB
# =========================================================
async def check_key_levels_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("=== Checking Key Levels (Including PDH/PDL) ===")
    now = datetime.now(timezone.utc)
    active_symbols = set(list(USER_KEY_LEVELS.keys()) + list(SPECIAL_LEVELS.keys()))
    
    for symbol in active_symbols:
        if symbol not in TICKER_MAP: continue
            
        price_data = get_live_price_and_range(symbol)
        if price_data is None:
            await asyncio.sleep(2)
            continue
            
        current_price = price_data["current"]
        recent_high = price_data["high"]
        recent_low = price_data["low"]
        
        # Previous Day High & Low चेक
        prev_day = get_prev_day_high_low(symbol)
        if prev_day:
            pdh = prev_day["pdh"]
            pdl = prev_day["pdl"]
            
            pdh_key = f"PDH_{symbol}"
            if (recent_high >= pdh) or (abs(current_price - pdh) <= pdh * 0.001):
                if not is_on_cooldown(pdh_key, now):
                    msg = f"🔴 *PREVIOUS DAY HIGH (PDH) CROSSED!*\n📊 *Asset:* {symbol}\n🎯 *PDH Level:* `{pdh:.5f}`\n💰 *Current Price:* `{current_price:.5f}`"
                    await send_alert(context, msg)
                    LEVEL_ALERT_COOLDOWN[pdh_key] = now

            pdl_key = f"PDL_{symbol}"
            if (recent_low <= pdl) or (abs(current_price - pdl) <= pdl * 0.001):
                if not is_on_cooldown(pdl_key, now):
                    msg = f"🟢 *PREVIOUS DAY LOW (PDL) CROSSED!*\n📊 *Asset:* {symbol}\n🎯 *PDL Level:* `{pdl:.5f}`\n💰 *Current Price:* `{current_price:.5f}`"
                    await send_alert(context, msg)
                    LEVEL_ALERT_COOLDOWN[pdl_key] = now

        # Normal लेवल्स चेक
        if symbol in USER_KEY_LEVELS:
            for level in USER_KEY_LEVELS[symbol]:
                level_key = f"NORMAL_{symbol}_{level}"
                is_crossed_up = (recent_high >= level) and (current_price >= level * 0.99)
                is_crossed_down = (recent_low <= level) and (current_price <= level * 1.01)
                tolerance = level * 0.001
                is_near = abs(current_price - level) <= tolerance

                if is_crossed_up or is_crossed_down or is_near:
                    if is_on_cooldown(level_key, now): continue
                    direction = "⬆️ UPWARDS" if current_price >= level else "⬇️ DOWNWARDS"
                    msg = f"🟢 *NORMAL LEVEL CROSSED*\n📊 *Asset:* {symbol}\n🎯 *Level:* `{level}`\n💰 *Price:* `{current_price:.5f}`\n📉 *Direction:* {direction}"
                    await send_alert(context, msg)
                    LEVEL_ALERT_COOLDOWN[level_key] = now
        
        # Major लेवल्स चेक
        if symbol in SPECIAL_LEVELS:
            for level in SPECIAL_LEVELS[symbol]:
                level_key = f"SPECIAL_{symbol}_{level}"
                is_crossed_up = (recent_high >= level) and (current_price >= level * 0.99)
                is_crossed_down = (recent_low <= level) and (current_price <= level * 1.01)
                tolerance = level * 0.001
                is_near = abs(current_price - level) <= tolerance

                if is_crossed_up or is_crossed_down or is_near:
                    if is_on_cooldown(level_key, now): continue
                    direction = "⬆️ UPWARDS" if current_price >= level else "⬇️ DOWNWARDS"
                    msg = f"🚨 *MAJOR LEVEL CROSSED!*\n📊 *Asset:* {symbol}\n🎯 *Level:* `{level}`\n💰 *Price:* `{current_price:.5f}`\n📉 *Direction:* {direction}"
                    await send_alert(context, msg)
                    LEVEL_ALERT_COOLDOWN[level_key] = now

        await asyncio.sleep(2)

# =========================================================
# 7. TELEGRAM COMMANDS
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TELEGRAM_CHAT_ID
    TELEGRAM_CHAT_ID = str(update.effective_chat.id)
    save_chat_id(TELEGRAM_CHAT_ID)
    await update.message.reply_text("🚀 *Bot Online (Webhook)*\n✅ PDH/PDL Auto-Tracking Active\nChat ID सेव हो गई है।", parse_mode="Markdown")

async def add_normal_level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        symbol, price = args[0].upper(), float(args[1])
        if symbol not in USER_KEY_LEVELS: USER_KEY_LEVELS[symbol] = []
        USER_KEY_LEVELS[symbol].append(price)
        await update.message.reply_text(f"✅ *{symbol}* में लेवल जुड़ गया: `{price}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ एरर: {str(e)}")

async def add_major_level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        symbol, price = args[0].upper(), float(args[1])
        if symbol not in SPECIAL_LEVELS: SPECIAL_LEVELS[symbol] = []
        SPECIAL_LEVELS[symbol].append(price)
        await update.message.reply_text(f"🚨 *{symbol}* में मेजर लेवल जुड़ गया: `{price}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ एरर: {str(e)}")

# =========================================================
# 8. MAIN
# =========================================================
def main():
    global telegram_app
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN missing!")
        return

    telegram_app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(30).write_timeout(30).connect_timeout(15).pool_timeout(5)
        .build()
    )

    telegram_app.add_handler(CommandHandler("start", start_cmd))
    telegram_app.add_handler(CommandHandler(["addlevel", "addleve"], add_normal_level_cmd))
    telegram_app.add_handler(CommandHandler("addmajor", add_major_level_cmd))
    
    if telegram_app.job_queue:
        telegram_app.job_queue.run_repeating(check_key_levels_job, interval=2 * 60, first=10)

    async def post_init(application):
        webhook_url = f"{RENDER_EXTERNAL_URL}/{TELEGRAM_BOT_TOKEN}"
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")

    telegram_app.post_init = post_init
    
    logger.info("Bot running in Webhook mode with PDH/PDL Tracking...")
    telegram_app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=f"{RENDER_EXTERNAL_URL}/{TELEGRAM_BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
