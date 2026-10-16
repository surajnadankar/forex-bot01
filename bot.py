import os
import io
import re
import base64
import logging
import threading
import time
import requests
import yfinance as yf
from datetime import datetime, timezone, timedelta
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes
)

# =========================================================
# 1. लॉगिंग और सेटिंग्स
# =========================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()

COOLDOWN_SECONDS = 7200  # 2 घंटे का कूलडाउन (120 मिनट)

# =========================================================
# 2. सभी सिंबल्स व शुद्ध स्पॉट टिकर मैपिंग (Zero Gap)
# =========================================================
WATCH_ASSETS = {
    # Metals (शुद्ध Spot Tickers - MT5/TradingView से 100% मैच)
    "XAUUSD": {"ticker": "XAUUSD=X", "levels": [4110.0, 4120.0, 4138.0, 4148.0, 4162.0, 4166.7, 4399.14]},
    "GOLD":   {"ticker": "XAUUSD=X", "levels": [4110.0, 4120.0, 4138.0, 4148.0, 4162.0, 4166.7, 4399.14]},
    "XAGUSD": {"ticker": "XAGUSD=X", "levels": [31.50, 32.20, 33.00, 34.50]},
    
    # Forex Majors & Crosses
    "EURUSD": {"ticker": "EURUSD=X", "levels": [1.0850, 1.0920, 1.1000, 1.1050, 1.1120]},
    "GBPUSD": {"ticker": "GBPUSD=X", "levels": [1.3010, 1.3140, 1.3200, 1.3250, 1.3300]},
    "USDJPY": {"ticker": "JPY=X", "levels": [149.50, 152.00, 153.50, 155.00, 156.50]},
    "EURJPY": {"ticker": "EURJPY=X", "levels": [162.00, 163.50, 164.80, 166.00]},
    "GBPJPY": {"ticker": "GBPJPY=X", "levels": [194.00, 195.50, 197.00, 198.50]},
    "AUDUSD": {"ticker": "AUDUSD=X", "levels": [0.6550, 0.6620, 0.6700, 0.6780]},
    "USDCAD": {"ticker": "CAD=X", "levels": [1.3850, 1.3950, 1.4050, 1.4150]},
    
    # Indices
    "US30":   {"ticker": "^DJI", "levels": [42000.0, 42500.0, 42850.0, 43300.0]},
    "NAS100": {"ticker": "^IXIC", "levels": [18000.0, 18400.0, 18750.0, 19100.0]},
    "DAX40":  {"ticker": "^GDAXI", "levels": [19000.0, 19300.0, 19600.0]},
    "UK100":  {"ticker": "^FTSE", "levels": [8150.0, 8250.0, 8350.0]},
    
    # Energy
    "XTIUSD": {"ticker": "CL=F", "levels": [68.50, 70.20, 72.00, 74.50]},
    
    # Crypto
    "BTCUSD": {"ticker": "BTC-USD", "levels": [82679.0, 83100.0, 84200.0, 85686.0, 87345.0]},
    "BTC":    {"ticker": "BTC-USD", "levels": [82679.0, 83100.0, 84200.0, 85686.0, 87345.0]},
    "ETHUSD": {"ticker": "ETH-USD", "levels": [2450.0, 2550.0, 2680.0, 2750.0]},
    "SOLUSD": {"ticker": "SOL-USD", "levels": [145.0, 155.0, 168.0, 180.0]}
}

last_prices = {}
last_alert_times = {}

# =========================================================
# 3. रेंडर वेब सर्वर (Port 10000 Instant Bind)
# =========================================================
web_app = Flask(__name__)

@web_app.route('/')
@web_app.route('/health')
def health():
    return "Suraj Institutional Multi-Asset Sniper Online 200 OK", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# =========================================================
# 4. लाइव टिक और डायनामिक हाई/लो क्लासिफायर
# =========================================================
def get_live_tick(ticker_sym: str) -> float:
    try:
        t = yf.Ticker(ticker_sym)
        fast = t.fast_info
        if hasattr(fast, 'last_price') and fast.last_price:
            return float(fast.last_price)
        df = t.history(period="1d", interval="1m")
        if not df.empty:
            return float(df['Close'].iloc[-1])
    except Exception as e:
        logger.warning(f"Tick Fetch Error {ticker_sym}: {e}")
    return 0.0

def classify_market_levels(asset: str, live_price: float):
    """मार्केट कहाँ पर है उसके आधार पर ऊपर वाले को High/Resistance और नीचे वाले को Low/Support बनाएगा"""
    levels = sorted(WATCH_ASSETS.get(asset, {}).get("levels", []))
    resistance_highs = [lvl for lvl in levels if lvl > live_price]
    support_lows = [lvl for lvl in levels if lvl <= live_price]
    
    immediate_resistance = resistance_highs[0] if resistance_highs else "None"
    immediate_support = support_lows[-1] if support_lows else "None"
    
    return {
        "resistance_highs": resistance_highs,
        "support_lows": support_lows,
        "immediate_high": immediate_resistance,
        "immediate_low": immediate_support
    }

# =========================================================
# 5. जेमिनी 3.5 फ़्लैश लाइट इंजन
# =========================================================
def query_gemini_auto(prompt_text: str, image_bytes: bytes = None) -> str:
    if not GEMINI_API_KEY:
        return "Gemini API Key नहीं मिली।"

    model_endpoint = "models/gemini-3.5-flash-lite"
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_endpoint}:generateContent?key={GEMINI_API_KEY}"

    parts = [{"text": prompt_text}]
    if image_bytes:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(image_bytes).decode("utf-8")
            }
        })

    payload = {"contents": [{"parts": parts}]}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        res_json = response.json()
        if "candidates" in res_json and len(res_json["candidates"]) > 0:
            candidate = res_json["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"]:
                return candidate["content"]["parts"][0]["text"].strip()
        if "error" in res_json:
            return f"AI इंजन एरर: {res_json['error'].get('message', 'Unknown')}"
    except Exception as e:
        return f"कनेक्शन एरर: {str(e)}"
    return "AI से उत्तर प्राप्त नहीं हुआ।"

# =========================================================
# 6. बैकग्राउंड प्राइस वॉचर (डायनामिक अलर्ट्स + 2H कूलडाउन)
# =========================================================
def background_price_watcher(bot_app):
    global last_prices, last_alert_times
    time.sleep(10)
    
    while True:
        try:
            target_chat = ALLOWED_CHAT_ID
            if not target_chat:
                time.sleep(15)
                continue

            for asset, conf in WATCH_ASSETS.items():
                if asset in ["GOLD", "BTC"]:  # डुप्लीकेट सिंबल बाईपास
                    continue

                sym = conf["ticker"]
                current_price = get_live_tick(sym)
                if current_price <= 0.0:
                    continue

                prev_price = last_prices.get(asset, current_price)
                last_prices[asset] = current_price

                levels = conf.get("levels", [])
                current_time = time.time()

                for lvl in levels:
                    alert_key = f"{asset}_{lvl}"
                    last_time = last_alert_times.get(alert_key, 0)

                    # 2 घंटे का कूलडाउन चेक
                    if current_time - last_time < COOLDOWN_SECONDS:
                        continue

                    # नीचे से ऊपर ब्रेक (Bullish Breakout)
                    if prev_price < lvl and current_price >= lvl:
                        cls = classify_market_levels(asset, current_price)
                        msg = (
                            f"🟢 **[BULLISH BREAKOUT ALERT]**\n\n"
                            f"• एसेट: **{asset}**\n"
                            f"• टूटा हुआ लेवल: **{lvl}**\n"
                            f"• लाइव भाव: **{current_price}**\n"
                            f"• स्टेटस: मार्केट ने लेवल को नीचे से ऊपर तोड़ा है।\n"
                            f"• अगला हाई/रेजिस्टेंस ज़ोन: **{cls['immediate_high']}**\n"
                            f"• नया सपोर्ट बना: **{lvl}**\n"
                            f"• एक्शन: 15M कैंडल क्लोज और वॉल्यूम कन्फर्मेशन पर बाय देखें।"
                        )
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": target_chat, "text": msg, "parse_mode": "Markdown"}
                        )
                        last_alert_times[alert_key] = current_time

                    # ऊपर से नीचे ब्रेक (Bearish Breakdown)
                    elif prev_price > lvl and current_price <= lvl:
                        cls = classify_market_levels(asset, current_price)
                        msg = (
                            f"🔴 **[BEARISH BREAKDOWN ALERT]**\n\n"
                            f"• एसेट: **{asset}**\n"
                            f"• टूटा हुआ लेवल: **{lvl}**\n"
                            f"• लाइव भाव: **{current_price}**\n"
                            f"• स्टेटस: मार्केट ने लेवल को ऊपर से नीचे तोड़ा है।\n"
                            f"• अगला लो/सपोर्ट ज़ोन: **{cls['immediate_low']}**\n"
                            f"• नया रेजिस्टेंस बना: **{lvl}**\n"
                            f"• एक्शन: सपोर्ट टूटने के बाद रीटेस्ट या बेयरिश एंट्री देखें।"
                        )
                        requests.post(
                            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                            json={"chat_id": target_chat, "text": msg, "parse_mode": "Markdown"}
                        )
                        last_alert_times[alert_key] = current_time

        except Exception as e:
            logger.error(f"वॉचर लूप एरर: {e}")

        time.sleep(20)

# =========================================================
# 7. टेलीग्राम कमांड्स और मैसेज हैंडलर्स
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = (
        f"🚀 **मल्टी-एसेट डायनामिक स्नाइपर बॉट एक्टिव!**\n\n"
        f"• आपकी चैट ID: `{chat_id}`\n"
        f"• शुद्ध स्पॉट टिकर एक्टिव (MT5 / TradingView से 100% मैच)\n"
        f"• शामिल सिंबल्स: Metals (Gold, Silver), Forex (EURUSD, GBPUSD, USDJPY, EURJPY, GBPJPY, AUDUSD, USDCAD), Indices (US30, NAS100, DAX40, UK100), Energy (Crude), Crypto (BTC, ETH, SOL).\n"
        f"• बॉट मार्केट के मौजूदा भाव के हिसाब से खुद हाई और लो तय करेगा।\n"
        f"• कोई भी चार्ट भेजें, जेमिनी तुरंत The5ers $2,500 के नियमों पर दो-तरफा प्लान देगी।"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📡 **एक्टिव सिंबल्स और डायनामिक लेवल्स:**\n"]
    for asset, conf in WATCH_ASSETS.items():
        if asset in ["GOLD", "BTC"]:
            continue
        lines.append(f"🔹 **{asset}:** {conf.get('levels')}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def set_levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # उपयोग: /setlevels XAUUSD 4120,4138,4162,4196
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text("उपयोग: `/setlevels XAUUSD 4120,4138,4162`", parse_mode="Markdown")
            return
        asset = args[0].upper()
        lvls = [float(x.strip()) for x in args[1].split(",") if x.strip()]
        if asset in WATCH_ASSETS:
            WATCH_ASSETS[asset]["levels"] = sorted(lvls)
            await update.message.reply_text(f"✅ {asset} के नए लेवल्स सेट हो गए: {WATCH_ASSETS[asset]['levels']}")
        else:
            await update.message.reply_text(f"एसेट {asset} लिस्ट में नहीं है।")
    except Exception as e:
        await update.message.reply_text(f"एरर: {e}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👁️ चार्ट स्कैन हो रहा है, डायनामिक हाई/लो और एक्शन प्लान तैयार किया जा रहा है...")
    try:
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        caption = update.message.caption or ""

        prompt = f"""
आप $2,500 The5ers हाई स्टेक्स खाते के संस्थागत रिस्क मैनेजर हैं।
इस ट्रेडिंग चार्ट का विश्लेषण करें और कैप्शन ध्यान से देखें: {caption}

नियम:
1. चार्ट के दाहिने प्राइस स्केल (Right Axis) पर जो लाल या हरे बॉक्स में करंट मार्केट प्राइस दिख रहा है, उसी को 100% सही लाइव भाव मानें।
2. भाव से ऊपर वाले लेवल्स को रेजिस्टेंस/हाई और नीचे वाले को सपोर्ट/लो के रूप में डायनामिकली देखें।
3. The5ers $2,500 अकाउंट: फिक्स रिस्क $25 (1%)।
4. मैक्सिमम स्टॉप लॉस 40 पिप्स (गोल्ड में $1 = 10 पिप्स, यानी अधिकतम $4 का SL)।
5. 2-ट्रिगर ऑर्डर: 1:1 RRR पर 50% लॉट बुक + SL कॉस्ट पर, और 1:3 RRR मुख्य रनर।
6. केवल अवॉइड न कहें, बुलिश और बेयरिश दोनों तरफ का एक्शन प्लान दें।

इस फॉर्मेट में हिंदी/हिंग्लिश में उत्तर दें:
🎯 **मार्केट स्ट्रक्चर स्थिति:** [डायनामिक सपोर्ट/रेजिस्टेंस व स्वीप ज़ोन]
📊 **पहचाना गया भाव व स्थिति:** [Current Visible Price from chart axis & Position between High/Low]

🟢 **बुलिश प्लान (ऊपर निकलने पर):**
• ट्रिगर: [लेवल]
• बाय एंट्री: [भाव]
• स्टॉप लॉस: [भाव] (मैक्स 40 पिप्स)
• टारगेट 1 (1:1 RRR): [भाव] (50% बुक + SL कॉस्ट पर)
• टारगेट 2 (1:3 RRR): [भाव]

🔴 **बेयरिश प्लान (नीचे गिरने पर):**
• ट्रिगर: [लेवल]
• सेल एंट्री: [भाव]
• स्टॉप लॉस: [भाव] (मैक्स 40 पिप्स)
• टारगेट 1 (1:1 RRR): [भाव] (50% बुक + SL कॉस्ट पर)
• टारगेट 2 (1:3 RRR): [भाव]

💡 **मास्टर फैसला:** [अभी करंट प्राइस पर क्या एक्शन लेना है]
"""
        res = query_gemini_auto(prompt, bytes(image_bytes))
        await update.message.reply_text(res)
    except Exception as e:
        await update.message.reply_text(f"इमेज स्कैन एरर: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    if user_text.startswith('/'):
        return

    await update.message.reply_text("🔍 जेमिनी से लेवल्स और सेटअप का विश्लेषण हो रहा है...")
    prompt = f"""
आप The5ers $2,500 अकाउंट के रिस्क मैनेजर हैं। 
उपयोगकर्ता का सवाल: {user_text}

सिंबल्स डेटाबेस (शुद्ध स्पॉट टिकर्स):
Metals: XAUUSD, XAGUSD
Forex: EURUSD, GBPUSD, USDJPY, EURJPY, GBPJPY, AUDUSD, USDCAD
Indices: US30, NAS100, DAX40, UK100
Energy: XTIUSD (Crude)
Crypto: BTCUSD, ETHUSD, SOLUSD

मार्केट के लाइव भाव के आधार पर ऊपर वाले को हाई और नीचे वाले को लो मानते हुए 40 पिप्स मैक्स SL और 1:1 व 1:3 RRR के हिसाब से सटीक एक्शन प्लान दें।
"""
    res = query_gemini_auto(prompt, None)
    await update.message.reply_text(res)

# =========================================================
# 8. मुख्य फ़ंक्शन
# =========================================================
def main():
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN नहीं मिला!")
        web_thread.join()
        return

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("levels", levels_cmd))
    app.add_handler(CommandHandler("setlevels", set_levels_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    watcher_thread = threading.Thread(target=background_price_watcher, args=(app,), daemon=True)
    watcher_thread.start()

    logger.info("Multi-Asset Dynamic Sniper Engine चालू है...")
    app.run_polling()

if __name__ == "__main__":
    main()
