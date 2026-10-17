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

COOLDOWN_SECONDS = 7200  # 2 घंटे का अलर्ट कूलडाउन

# =========================================================
# 2. सभी पेयर्स मौजूद हैं — सभी के लेवल्स पूरी तरह खाली ([])
# =========================================================
WATCH_ASSETS = {
    # Metals (शुद्ध Spot Ticker)
    "XAUUSD": {"ticker": "XAUUSD=X", "levels": []},
    "XAGUSD": {"ticker": "XAGUSD=X", "levels": []},
    
    # Forex Majors & Crosses
    "EURUSD": {"ticker": "EURUSD=X", "levels": []},
    "GBPUSD": {"ticker": "GBPUSD=X", "levels": []},
    "USDJPY": {"ticker": "JPY=X", "levels": []},
    "EURJPY": {"ticker": "EURJPY=X", "levels": []},
    "GBPJPY": {"ticker": "GBPJPY=X", "levels": []},
    "AUDUSD": {"ticker": "AUDUSD=X", "levels": []},
    "USDCAD": {"ticker": "CAD=X", "levels": []},
    
    # Indices
    "US30":   {"ticker": "^DJI", "levels": []},
    "NAS100": {"ticker": "^IXIC", "levels": []},
    "DAX40":  {"ticker": "^GDAXI", "levels": []},
    "UK100":  {"ticker": "^FTSE", "levels": []},
    
    # Energy
    "XTIUSD": {"ticker": "CL=F", "levels": []},
    
    # Crypto
    "BTCUSD": {"ticker": "BTC-USD", "levels": []},
    "ETHUSD": {"ticker": "ETH-USD", "levels": []},
    "SOLUSD": {"ticker": "SOL-USD", "levels": []}
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
    levels = sorted(WATCH_ASSETS.get(asset, {}).get("levels", []))
    resistance_highs = [lvl for lvl in levels if lvl > live_price]
    support_lows = [lvl for lvl in levels if lvl <= live_price]
    
    immediate_resistance = resistance_highs[0] if resistance_highs else "कोई नहीं"
    immediate_support = support_lows[-1] if support_lows else "कोई नहीं"
    
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
# 6. बैकग्राउंड प्राइस वॉचर (केवल यूज़र के सेट किए लेवल्स पर अलर्ट)
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
                levels = conf.get("levels", [])
                if not levels:
                    continue  # जब तक आप लेवल नहीं डालेंगे, कोई फालतू अलर्ट नहीं आएगा

                sym = conf["ticker"]
                current_price = get_live_tick(sym)
                if current_price <= 0.0:
                    continue

                prev_price = last_prices.get(asset, current_price)
                last_prices[asset] = current_price

                current_time = time.time()

                for lvl in levels:
                    alert_key = f"{asset}_{lvl}"
                    last_time = last_alert_times.get(alert_key, 0)

                    # 2 घंटे का कूलडाउन
                    if current_time - last_time < COOLDOWN_SECONDS:
                        continue

                    # नीचे से ऊपर ब्रेकआउट (Bullish Breakout)
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

                    # ऊपर से नीचे ब्रेकडाउन (Bearish Breakdown)
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
                            f"• एक्शन: सपोर्ट टूटने के बाद रीटेस्ट या बेयरिश शॉर्ट एंट्री देखें।"
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
        f"🚀 **मल्टी-एसेट स्नाइपर बॉट एक्टिव!**\n\n"
        f"• आपकी चैट ID: `{chat_id}`\n"
        f"• सभी फर्जी लेवल्स क्लियर कर दिए गए हैं।\n"
        f"• नए लेवल्स डालने के लिए लिखें: `/setlevels XAUUSD 4120,4138,4162`\n"
        f"• किसी पेयर के लेवल्स हटाने के लिए लिखें: `/clearlevels XAUUSD`\n"
        f"• सभी सेव लेवल्स देखने के लिए लिखें: `/levels`\n"
        f"• चार्ट की फोटो भेजें, जेमिनी तुरंत The5ers के नियमों पर एक्शन प्लान देगी।"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📡 **आपके द्वारा सेट किए गए लेवल्स:**\n"]
    for asset, conf in WATCH_ASSETS.items():
        lvls = conf.get('levels', [])
        status = ", ".join(map(str, lvls)) if lvls else "खाली (कोई लेवल नहीं)"
        lines.append(f"🔹 **{asset}:** {status}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def set_levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text("उपयोग उदाहरण: `/setlevels XAUUSD 4120,4138,4162`", parse_mode="Markdown")
            return
        asset = args[0].upper()
        lvls = [float(x.strip()) for x in args[1].split(",") if x.strip()]
        if asset in WATCH_ASSETS:
            WATCH_ASSETS[asset]["levels"] = sorted(lvls)
            await update.message.reply_text(f"✅ **{asset}** के नए लेवल्स सेट हो गए:\n`{WATCH_ASSETS[asset]['levels']}`", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"एसेट {asset} लिस्ट में नहीं मिला।")
    except Exception as e:
        await update.message.reply_text(f"एरर: {e}")

async def clear_levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) < 1:
            await update.message.reply_text("उपयोग उदाहरण: `/clearlevels XAUUSD`", parse_mode="Markdown")
            return
        asset = args[0].upper()
        if asset in WATCH_ASSETS:
            WATCH_ASSETS[asset]["levels"] = []
            await update.message.reply_text(f"🗑️ **{asset}** के सारे लेवल्स क्लियर (खाली) कर दिए गए हैं।")
        else:
            await update.message.reply_text(f"एसेट {asset} लिस्ट में नहीं मिला।")
    except Exception as e:
        await update.message.reply_text(f"एरर: {e}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👁️ चार्ट स्कैन हो रहा है, लाइव भाव के आधार पर एक्शन प्लान तैयार किया जा रहा है...")
    try:
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        caption = update.message.caption or ""

        prompt = f"""
आप $2,500 The5ers हाई स्टेक्स खाते के संस्थागत रिस्क मैनेजर हैं।
इस ट्रेडिंग चार्ट का विश्लेषण करें और कैप्शन ध्यान से देखें: {caption}

नियम:
1. चार्ट के दाहिने प्राइस स्केल (Right Axis) पर जो लाल या हरे बॉक्स में करंट मार्केट प्राइस दिख रहा है, उसी को 100% सही लाइव भाव मानें।
2. The5ers $2,500 अकाउंट: फिक्स रिस्क $25 (1%)।
3. मैक्सिमम स्टॉप लॉस 40 पिप्स (गोल्ड में $1 = 10 पिप्स, यानी अधिकतम $4 का SL)।
4. 2-ट्रिगर ऑर्डर: 1:1 RRR पर 50% लॉट बुक + SL कॉस्ट पर, और 1:3 RRR मुख्य रनर।
5. केवल अवॉइड न कहें, बुलिश और बेयरिश दोनों तरफ का एक्शन प्लान दें।

इस फॉर्मेट में हिंदी/हिंग्लिश में उत्तर दें:
🎯 **मार्केट स्ट्रक्चर स्थिति:** [सपोर्ट/रेजिस्टेंस व चार्ट स्ट्रक्चर]
📊 **पहचाना गया भाव व स्थिति:** [Current Visible Price from chart axis]

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

    await update.message.reply_text("🔍 जेमिनी से विश्लेषण हो रहा है...")
    prompt = f"""
आप The5ers $2,500 अकाउंट के रिस्क मैनेजर हैं। 
उपयोगकर्ता का सवाल: {user_text}

40 पिप्स मैक्स SL और 1:1 व 1:3 RRR के हिसाब से सटीक एक्शन प्लान दें।
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
    app.add_handler(CommandHandler("clearlevels", clear_levels_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    watcher_thread = threading.Thread(target=background_price_watcher, args=(app,), daemon=True)
    watcher_thread.start()

    logger.info("Multi-Asset Dynamic Sniper चालू है (सभी लेवल्स खाली)...")
    app.run_polling()

if __name__ == "__main__":
    main()
