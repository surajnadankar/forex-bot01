import os
import json
import logging
import threading
import base64
from datetime import datetime, time as dtime, timedelta, timezone
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
import requests
import yfinance as yf

# =========================================================
# 1. कॉन्फ़िगरेशन एवं पर्यावरण चर
# =========================================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()

IST = timezone(timedelta(hours=5, minutes=30))

ACCOUNT_BALANCE = 2500.00
FIXED_RISK_USD = 25.00
COMMISSION_BUFFER_USD = 1.50
MAX_SL_PIPS = 40.0
DAILY_CIRCUIT_BREAKER_USD = 100.00

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

YF_TICKERS = {
    "GOLD": "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "USDCAD": "CAD=X",
    "BTC": "BTC-USD"
}

DEFAULT_RADARS = {
    "GOLD": {
        "key_levels": [4148.0, 4149.0, 4138.0, 4120.0, 4110.0, 4162.0, 4166.7],
        "4h_v_highs": [4399.14, 4166.70],
        "4h_v_lows": [3959.98, 3996.20, 4110.00]
    },
    "EURUSD": {
        "key_levels": [1.0850, 1.0920, 1.1000, 1.1050, 1.1120],
        "4h_v_highs": [1.17075, 1.18448, 1.19227, 1.20728],
        "4h_v_lows": [1.01829, 1.07362, 1.10763]
    },
    "GBPUSD": {
        "key_levels": [1.3140, 1.3200, 1.3250, 1.3300],
        "4h_v_highs": [1.33080, 1.34020, 1.35660, 1.36730],
        "4h_v_lows": [1.30100, 1.30980, 1.31400]
    },
    "USDJPY": {
        "key_levels": [152.00, 153.50, 155.00, 156.50],
        "4h_v_highs": [158.994, 160.329, 163.988],
        "4h_v_lows": [149.578, 152.206, 153.003, 156.498]
    },
    "USDCAD": {
        "key_levels": [1.3950, 1.4020, 1.4150, 1.4257, 1.4295],
        "4h_v_highs": [1.43952, 1.44514],
        "4h_v_lows": [1.38974, 1.39812, 1.41546]
    },
    "BTC": {
        "key_levels": [85686.0, 87701.0, 89500.0],
        "4h_v_highs": [87345.0],
        "4h_v_lows": [82679.0]
    }
}

MASTER_RADARS = json.loads(json.dumps(DEFAULT_RADARS))

daily_stats = {
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "net_pnl": 0.0,
    "circuit_broken": False
}

# =========================================================
# 2. Render बैकग्राउंड वेब सर्वर
# =========================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Suraj Institutional Live Sniper Engine Online."

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# =========================================================
# 3. Yahoo Finance लाइव डेटा और डेल्टा न्यूट्रल स्कैनर
# =========================================================
def fetch_live_market_context(symbol: str = "GOLD") -> str:
    ticker_sym = YF_TICKERS.get(symbol.upper(), "GC=F")
    try:
        ticker = yf.Ticker(ticker_sym)
        df_1h = ticker.history(period="5d", interval="1h")
        df_1d = ticker.history(period="1mo", interval="1d")

        if df_1h.empty:
            return "⚠️ लाइव मार्केट डेटा अनुपलब्ध (मार्केट बंद या सिंबल लोड नहीं हुआ)।"

        live_price = float(df_1h['Close'].iloc[-1])
        last_vol = float(df_1h['Volume'].iloc[-1])
        avg_vol = float(df_1h['Volume'].tail(10).mean())
        
        vol_surge = round(last_vol / avg_vol, 2) if avg_vol > 0 else 1.0
        
        day_open = float(df_1d['Open'].iloc[-1])
        day_trend = "BULLISH 🟢" if live_price > day_open else "BEARISH 🔴"
        
        c_open = float(df_1h['Open'].iloc[-1])
        c_close = float(df_1h['Close'].iloc[-1])
        c_high = float(df_1h['High'].iloc[-1])
        c_low = float(df_1h['Low'].iloc[-1])
        
        candle_body = abs(c_close - c_open)
        candle_range = c_high - c_low if (c_high - c_low) > 0 else 0.001
        
        is_delta_neutral_trap = (vol_surge >= 1.3) and ((candle_body / candle_range) < 0.35)
        
        if is_delta_neutral_trap:
            delta_analysis = "🚨 [DELTA NEUTRAL TRAP ALERT]: भारी वॉल्यूम पर भी भाव अटका हुआ है। खरीदार/विक्रेता एब्जॉर्ब हो रहे हैं। सीधे ब्रेकडाउन/ब्रेकआउट पर एंट्री न लें!"
        elif vol_surge >= 1.5:
            delta_analysis = "✅ [HIGH VOLUME MOMENTUM]: आक्रामक ऑर्डर्स सक्रिय हैं।"
        else:
            delta_analysis = "⚠️ [DRY VOLUME]: बाज़ार में लिक्विडिटी कम है, फेकआउट संभव है।"

        return (
            f"📊 **[YAHOO FINANCE LIVE REAL-TIME AUDIT - {symbol}]**\n"
            f"• लाइव भाव (Current Price): {live_price:.2f}\n"
            f"• 1D डेली ट्रेंड: {day_trend}\n"
            f"• 1H वॉल्यूम सर्ज: {vol_surge}x (औसत के मुकाबले)\n"
            f"• डेल्टा स्थिति: {delta_analysis}\n"
        )
    except Exception as e:
        logger.error(f"Yahoo Finance Fetch Error: {str(e)}")
        return f"⚠️ लाइव डेटा फेच में समस्या: {str(e)}"

# =========================================================
# 4. Google Gemini 3.8 Flash Engine
# =========================================================
def query_gemini_auto(prompt_text: str, image_bytes: bytes = None) -> str:
    if not GEMINI_API_KEY:
        return "⚠️ Gemini API Key Render Environment में नहीं मिली।"

    # Google द्वारा निर्देशित आधिकारिक चालू मॉडल
    model_endpoint = "models/gemini-3.8-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_endpoint}:generateContent?key={GEMINI_API_KEY}"
    
    parts = [{"text": prompt_text}]
    if image_bytes:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(image_bytes).decode('utf-8')
            }
        })
    
    payload = {"contents": [{"parts": parts}]}
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        res_json = response.json()
        
        if "candidates" in res_json and len(res_json["candidates"]) > 0:
            return res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
        elif "error" in res_json:
            return f"❌ AI इंजन एरर: {res_json['error'].get('message', 'Unknown Error')}"
        return "⚠️️ AI से कोई विश्लेषण प्राप्त नहीं हुआ।"
    except Exception as e:
        return f"❌ नेटवर्क / API एरर: {str(e)}"

# =========================================================
# 5. ट्रेड ऑडिट और रिस्क इंजन (All Patterns + Volume/Delta)
# =========================================================
def evaluate_market_trade(text_query: str = "", image_bytes: bytes = None) -> str:
    detected_symbol = "GOLD"
    for s in ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "BTC"]:
        if s in text_query.upper():
            detected_symbol = s
            break

    live_context = fetch_live_market_context(detected_symbol)
    radar = MASTER_RADARS.get(detected_symbol, {})

    system_context = (
        "You are an Elite Institutional Risk Manager for a $2,500 The5ers High Stakes account.\n"
        "Evaluate the user's trading idea or chart image using the LIVE YAHOO FINANCE DATA provided below:\n\n"
        f"{live_context}\n"
        f"Master 4H V-Highs: {radar.get('4h_v_highs', [])}\n"
        f"Master 4H V-Lows: {radar.get('4h_v_lows', [])}\n"
        f"Key-Levels: {radar.get('key_levels', [])}\n\n"
        "Institutional Execution Rules:\n"
        "1. Identify ANY pattern in the chart (Double Tops/Bottoms, Bull/Bear Flags, Triangles, Head & Shoulders, Support Breakdown/Retests, 4H Sweeps).\n"
        "2. Cross-verify the pattern with the LIVE Yahoo Finance Volume & Delta Neutral data. DO NOT blindly parrot the user's notes.\n"
        "3. If Delta Neutral / Absorption Trap is detected or volume is dry, advise WAIT / AVOID TRAP.\n"
        "4. 2-Trigger Order Rules: Lot 1 takes TP1 at 1:1 (50% book, move remaining SL to Breakeven). Lot 2 runs for 1:3 RRR (TP2).\n"
        "5. Max SL 40 Pips rule. Account Risk is fixed at 1% ($25.00).\n\n"
        "Respond in this EXACT clean Hindi/Hinglish structured format:\n"
        "🎯 **निर्णय (Decision):** [APPROVED BUY / APPROVED SELL / WAIT / REJECT TRAP]\n"
        "📊 **लाइव मार्केट व डेल्टा स्थिति:** [1D ट्रेंड, वॉल्यूम सर्ज और डेल्टा न्यूट्रल ट्रैप स्थिति]\n"
        "🔍 **पहचाना गया चार्ट पैटर्न (Pattern):** [Flag / Double Bottom / Breakdown Retest / S&R Rejection]\n"
        "🔹 **एंट्री (Entry Price):** [Price]\n"
        "🛑 **स्टॉप लॉस (Stop Loss):** [Price] (Max 40 pips check)\n"
        "🎯 **टारगेट 1 (1:1 RRR - 50% Book):** [Price] (50% कटेगा और SL कॉस्ट पे आएगा)\n"
        "🚀 **टारगेट 2 (1:3 RRR - Runner):** [Price] (मुख्य रनर)\n"
        "💡 **सीधा फैसला (Clear Verdict):** [1-2 lines clearly stating whether to enter now or wait]"
    )
    full_prompt = f"{system_context}\n\nUser Message/Notes:\n{text_query}" if text_query else system_context
    return query_gemini_auto(full_prompt, image_bytes)

# =========================================================
# 6. टेलीग्राम कमांड व मैसेज हैंडलर्स
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🚀 *Master Algo Institutional Live Sniper Engine Live!*\n"
        "-------------------------------------\n"
        "• Google Gemini 3.8 Flash Engine एक्टिव\n"
        "• Yahoo Finance लाइव मार्केट डेटा (1D Trend + 1H Volume)\n"
        "• डेल्टा न्यूट्रल ट्रैप + सभी चार्ट पैटर्न्स (Flags, Double Top/Bottom, Sweeps)\n"
        "• 2-Trigger Orders: 1:1 पर 50% बुक + Breakeven, और 1:3 रनर\n"
        "• फोटो या टेक्स्ट कुछ भी भेजें, बॉट लाइव डेटा से क्रॉस-चेक करेगा!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📡 *MASTER 4H V-SHAPE & KEY-LEVELS RADAR:*\n"]
    for asset, data in MASTER_RADARS.items():
        lines.append(f"🔹 *{asset}:*")
        lines.append(f"  🔺 4H V-Highs: {', '.join(map(str, data.get('4h_v_highs', [])))}")
        lines.append(f"  🔻 4H V-Lows: {', '.join(map(str, data.get('4h_v_lows', [])))}")
        lines.append(f"  🎯 Key-Levels (S/R): {', '.join(map(str, data.get('key_levels', [])))}\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    caption = update.message.caption or ""
    await context.bot.send_message(
        chat_id=chat_id, 
        text="👁️️ [लाइव मार्केट ऑडिट] Yahoo Finance से 1D ट्रेंड, वॉल्यूम, डेल्टा न्यूट्रल और चार्ट पैटर्न स्कैन हो रहा है..."
    )
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        res = evaluate_market_trade(text_query=caption, image_bytes=bytes(photo_bytes))
        await context.bot.send_message(chat_id=chat_id, text=res)
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ स्कैन एरर: {str(e)}")

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    chat_id = update.effective_chat.id

    if user_text.startswith('/'):
        return

    await context.bot.send_message(chat_id=chat_id, text="🔍 लाइव डेटा फ़ेच कर डेल्टा न्यूट्रल और ट्रेड सेटअप का विश्लेषण हो रहा है...")
    try:
        res = evaluate_market_trade(text_query=user_text, image_bytes=None)
        await context.bot.send_message(chat_id=chat_id, text=res)
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ एरर: {str(e)}")

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.warning(f"अपडेट नोटिस: {context.error}")

# =========================================================
# 7. मुख्य निष्पादन लूप
# =========================================================
def main():
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("Flask वेब सर्वर पोर्ट 10000 पर सक्रिय हो चुका है।")

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN मिसिंग है!")
        web_thread.join()
        return

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_error_handler(global_error_handler)

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("levels", levels_cmd))
    
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("Master Live Sniper इंजन (Gemini 3.8 Flash) सक्रिय है...")
    application.run_polling()

if __name__ == "__main__":
    main()
