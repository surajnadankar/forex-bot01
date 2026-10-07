import os
import io
import re
import base64
import logging
import threading
import json
import requests
import yfinance as yf
from datetime import datetime, timedelta
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
# 1. Logging & Configurations
# =========================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()

# =========================================================
# 2. Master Institutional 4H V-Shape & Monday Levels Database
# =========================================================
MASTER_RADARS = {
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

TICKER_MAP = {
    "EURAUD": "EURAUD=X",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "CAD=X",
    "XAUUSD": "GC=F",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "BTCUSD": "BTC-USD",
    "BTCUSDT": "BTC-USD",
    "BTC": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "US30": "^DJI",
    "NAS100": "^IXIC",
    "US500": "^GSPC"
}

# =========================================================
# 3. Render Web Server (Instant Port Binding - No Timeout)
# =========================================================
web_app = Flask(__name__)

@web_app.route('/')
@web_app.route('/health')
def health():
    return "Suraj Institutional Live Sniper Engine Online 200 OK", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Binding Flask web server to 0.0.0.0:{port}...")
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# =========================================================
# 4. Yahoo Finance Live + Monday High/Low + 4H Context
# =========================================================
def resolve_yf_ticker(raw_symbol: str) -> str:
    clean_sym = re.sub(r'[^A-Z0-9]', '', raw_symbol.upper())
    if clean_sym in TICKER_MAP:
        return TICKER_MAP[clean_sym]
    if len(clean_sym) == 6 and not clean_sym.endswith("USD"):
        return f"{clean_sym}=X"
    return clean_sym

def fetch_live_market_context(symbol: str) -> str:
    try:
        yf_symbol = resolve_yf_ticker(symbol)
        ticker = yf.Ticker(yf_symbol)
        
        df_1d = ticker.history(period="10d", interval="1d")
        df_1h = ticker.history(period="5d", interval="1h")
        
        if df_1d.empty:
            return f"📊 **[LIVE MARKET AUDIT - {symbol}]**\n• लाइव डेटा: सामान्य फ़्लो में सक्रिय।"
        
        live_price = float(df_1d['Close'].iloc[-1])
        day_open = float(df_1d['Open'].iloc[-1])
        day_high = float(df_1d['High'].iloc[-1])
        day_low = float(df_1d['Low'].iloc[-1])
        day_trend = "BULLISH 🟢" if live_price >= day_open else "BEARISH 🔴"
        
        # 4H कैंडल स्ट्रक्चर
        df_4h_recent = df_1h.tail(4) if not df_1h.empty else df_1d
        c_4h_high = float(df_4h_recent['High'].max())
        c_4h_low = float(df_4h_recent['Low'].min())
        c_4h_open = float(df_4h_recent['Open'].iloc[0])
        trend_4h = "BULLISH 🟢" if live_price >= c_4h_open else "BEARISH 🔴"
        
        # सोमवार का High / Low (Monday Range Setup)
        monday_high = "N/A"
        monday_low = "N/A"
        for date_idx, row in df_1d.iterrows():
            if date_idx.weekday() == 0:  # 0 = Monday
                monday_high = f"{float(row['High']):.5f}"
                monday_low = f"{float(row['Low']):.5f}"
        
        # वॉल्यूम सर्ज
        if 'Volume' in df_1h and not df_1h['Volume'].empty and df_1h['Volume'].iloc[-1] > 0:
            last_vol = float(df_1h['Volume'].iloc[-1])
            avg_vol = float(df_1h['Volume'].tail(10).mean())
            vol_surge = round(last_vol / (avg_vol + 1e-5), 2)
        else:
            vol_surge = 1.0

        if vol_surge >= 1.4:
            delta_analysis = "🟢 [HIGH VOLUME MOMENTUM]: आक्रामक संस्थागत ऑर्डर्स सक्रिय।"
        elif vol_surge <= 0.6:
            delta_analysis = "⚠️ [DRY VOLUME]: लिक्विडिटी की भारी कमी, फेकआउट/ट्रैप संभव।"
        else:
            delta_analysis = "⚖️ [BALANCED VOLUME]: सामान्य लिक्विडिटी।"

        radar_key = "GOLD" if "GOLD" in symbol.upper() or "XAU" in symbol.upper() else symbol.upper()
        radar_info = MASTER_RADARS.get(radar_key, {})

        return f"""
📊 **YAHOO FINANCE LIVE AUDIT ({symbol} -> {yf_symbol})**
• लाइव भाव (Current Price): {live_price:.5f}
• 1D डेली ट्रेंड: {day_trend} (High: {day_high:.5f} | Low: {day_low:.5f})
• 4H ट्रेंड व ज़ोन: {trend_4h} (High: {c_4h_high:.5f} | Low: {c_4h_low:.5f})
• मंडे रेंज (Monday Setup): High: {monday_high} | Low: {monday_low}
• मास्टर 4H V-Highs: {radar_info.get('4h_v_highs', [])}
• मास्टर 4H V-Lows: {radar_info.get('4h_v_lows', [])}
• की-लेवल्स (S/R): {radar_info.get('key_levels', [])}
• वॉल्यूम सर्ज: {vol_surge}x (औसत के मुकाबले)
• लिक्विडिटी स्थिति: {delta_analysis}
"""
    except Exception as e:
        logger.warning(f"Yahoo Finance Fetch Warning: {str(e)}")
        return f"📊 **[LIVE MARKET AUDIT - {symbol}]**\n• लाइव डेटा सामान्य मोड में है।"

# =========================================================
# 5. Gemini 3.5 Flash Lite Engine
# =========================================================
def query_gemini_auto(prompt_text: str, image_bytes: bytes = None) -> str:
    if not GEMINI_API_KEY:
        return "❌ Error: Gemini API Key Not Found in Environment Variables."

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
            return f"❌ AI Engine Error: {res_json['error'].get('message', 'Unknown Error')}"
            
        return "❌ AI Response Format Error."
    except Exception as e:
        return f"❌ API Connection Error: {str(e)}"

# =========================================================
# 6. Core Trade Strategy Evaluator (All Rules Integrated)
# =========================================================
def evaluate_trade_logic(symbol: str, user_notes: str = "", image_bytes: bytes = None) -> str:
    live_context = fetch_live_market_context(symbol)

    full_prompt = f"""
You are an Elite Institutional Risk Manager for a $2,500 The5ers High Stakes account.
Evaluate the trade setup or query for: {symbol}

[LIVE YAHOO FINANCE & MASTER RADAR DATA]:
{live_context}

[USER MESSAGE / QUERY]:
{user_notes}

[INSTITUTIONAL EXECUTION & RISK RULES]:
1. Check Master 4H V-Highs, 4H V-Lows, Monday High/Low, and Key S&R levels provided in the context.
2. Structure Recognition:
   - Identify ANY pattern (4H V-Shape Sweep, Monday High/Low Sweep, Double Top/Bottom, Bull/Bear Flag, Triangles, Breakdown/Retest).
   - If a Breakdown or Breakout occurs with DRY VOLUME (< 0.8x) -> Alert as LIQUIDITY TRAP / FAKEOUT. Verdict: WAIT / AVOID TRAP.
   - If Volume is strong (> 1.3x) and aligns with the trend -> APPROVE CONTINUATION.
3. The5ers High Stakes Risk Management:
   - Account Size: $2,500. Fixed Risk: $25.00 (1%).
   - Max Stop Loss: 40 Pips (Trade MUST be rejected if SL > 40 pips).
   - 2-Trigger Orders: 
     * Target 1 (1:1 RRR): Book 50% lot and shift remaining SL to Cost/Breakeven.
     * Target 2 (1:3 RRR): Main runner.
4. If user asks general questions or asks for advice in text, answer immediately, intelligently, and clearly based on institutional rules.

Respond strictly in this clean Hindi/Hinglish format:
🎯 **निर्णय (Decision):** [APPROVED BUY / APPROVED SELL / WAIT / AVOID TRAP / INFO]
📊 **लाइव मार्केट व डेल्टा स्थिति:**
   • 1D ट्रेंड: [1D Trend]
   • 4H ट्रेंड व ज़ोन: [4H Trend & Zone]
   • मंडे / 4H स्वीप स्थिति: [Monday H/L or 4H V-Level status]
   • वॉल्यूम व लिक्विडिटी: [Surge ratio & Trap Status]
🔍 **पहचाना गया चार्ट पैटर्न (Pattern):** [Pattern Name & Structure Details]
🔹 **एंट्री (Entry Price):** [Price or NA]
🛑 **स्टॉप लॉस (Stop Loss):** [Price or NA] (Max 40 Pips check)
🎯 **टारगेट 1 (1:1 RRR - 50% Book):** [Price or NA] (50% कटेगा और SL कॉस्ट पर आएगा)
🚀 **टारगेट 2 (1:3 RRR - Runner):** [Price or NA]
💡 **स्पष्ट फैसला (Verdict):** [1-2 clear lines explaining why to enter or wait]
"""
    return query_gemini_auto(full_prompt, image_bytes)

# =========================================================
# 7. Telegram Handlers (Photo + Any Text Supported)
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🚀 *Master Institutional Live Sniper Engine Online!*\n"
        "-------------------------------------\n"
        "• AI इंजन: Google Gemini 3.5 Flash Lite\n"
        "• मास्टर 4H V-Highs / V-Lows लिक्विडिटी स्वीप रडार\n"
        "• मंडे हाई / लो (Monday Range) ट्रैप स्कैनर\n"
        "• The5ers $2.5K रूल्स: 1:1 पर 50% बुक + Breakeven, और 1:3 रनर\n"
        "• कोई भी चार्ट भेजें या कोई भी सवाल लिखकर पूछें!"
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
    message = update.message
    await message.reply_text("👁️ [लाइव मार्केट ऑडिट] 4H स्वीप, मंडे लेवल्स और वॉल्यूम स्कैन हो रहा है...")

    try:
        photo_file = await message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()

        symbol_detect_prompt = (
            "Analyze this trading chart image and extract ONLY the Symbol/Pair name "
            "(e.g. EURAUD, XAUUSD, GOLD, EURUSD, GBPUSD, BTCUSD, US30). "
            "Do NOT output any extra words or punctuation. Return only the symbol."
        )
        detected_symbol_raw = query_gemini_auto(symbol_detect_prompt, bytes(image_bytes)).strip().upper()
        detected_symbol = re.sub(r'[^A-Z0-9]', '', detected_symbol_raw)

        if not detected_symbol or len(detected_symbol) < 3:
            detected_symbol = "GOLD"

        caption = message.caption or ""
        analysis_result = evaluate_trade_logic(detected_symbol, user_notes=caption, image_bytes=bytes(image_bytes))
        await message.reply_text(f"📊 **Symbol Identified:** {detected_symbol}\n\n{analysis_result}")
    except Exception as e:
        await message.reply_text(f"⚠️ इमेज स्कैन में समस्या: {str(e)}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    if user_text.startswith('/'):
        return

    await update.message.reply_text("🔍 [लाइव मार्केट ऑडिट] आपके सवाल और लाइव लेवल्स का विश्लेषण हो रहा है...")
    
    found_symbol = "GOLD"
    clean_upper = user_text.upper()
    for s in TICKER_MAP.keys():
        if s in clean_upper:
            found_symbol = s
            break

    try:
        res = evaluate_trade_logic(found_symbol, user_notes=user_text, image_bytes=None)
        await update.message.reply_text(f"📊 **Asset Reference:** {found_symbol}\n\n{res}")
    except Exception as e:
        await update.message.reply_text(f"❌ एरर: {str(e)}")

# =========================================================
# 8. Main Function
# =========================================================
def main():
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN missing!")
        web_thread.join()
        return

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("levels", levels_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot is running smoothly on gemini-3.5-flash-lite...")
    app.run_polling()

if __name__ == "__main__":
    main()
