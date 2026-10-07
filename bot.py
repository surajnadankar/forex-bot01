import os
import io
import re
import base64
import logging
import threading
import json
import requests
import yfinance as yf
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

# Master 4H V-Shape & Key Support/Resistance Levels Database
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
# 2. Render Web Server (Port 10000 Instant Bind)
# =========================================================
web_app = Flask(__name__)

@web_app.route('/')
@web_app.route('/health')
def health():
    return "Suraj Institutional Live Sniper Engine Online 200 OK", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# =========================================================
# 3. Yahoo Finance Real-Time Live Stream
# =========================================================
def resolve_yf_ticker(raw_symbol: str) -> str:
    clean_sym = re.sub(r'[^A-Z0-9]', '', raw_symbol.upper())
    if clean_sym in TICKER_MAP:
        return TICKER_MAP[clean_sym]
    if len(clean_sym) == 6 and not clean_sym.endswith("USD"):
        return f"{clean_sym}=X"
    return clean_sym

def fetch_realtime_market_data(symbol: str) -> str:
    try:
        yf_symbol = resolve_yf_ticker(symbol)
        ticker = yf.Ticker(yf_symbol)
        
        fast = ticker.fast_info
        live_price = float(fast.last_price) if hasattr(fast, 'last_price') and fast.last_price else None
        
        df_5m = ticker.history(period="1d", interval="5m")
        df_1h = ticker.history(period="3d", interval="1h")
        df_1d = ticker.history(period="5d", interval="1d")
        
        if live_price is None:
            live_price = float(df_5m['Close'].iloc[-1]) if not df_5m.empty else float(df_1d['Close'].iloc[-1])
            
        day_open = float(df_1d['Open'].iloc[-1]) if not df_1d.empty else live_price
        day_trend = "BULLISH 🟢" if live_price >= day_open else "BEARISH 🔴"
        
        trend_4h = day_trend
        c_4h_high = live_price
        c_4h_low = live_price
        if not df_1h.empty and len(df_1h) >= 4:
            c_4h_open = float(df_1h['Open'].iloc[-4])
            trend_4h = "BULLISH 🟢" if live_price >= c_4h_open else "BEARISH 🔴"
            c_4h_high = float(df_1h['High'].tail(4).max())
            c_4h_low = float(df_1h['Low'].tail(4).min())

        vol_surge = 1.0
        delta_status = "⚖️ [BALANCED VOLUME]"
        
        if not df_5m.empty and 'Volume' in df_5m.columns:
            recent_vol = float(df_5m['Volume'].tail(3).sum())
            avg_vol = float(df_5m['Volume'].mean()) * 3
            if avg_vol > 0:
                vol_surge = round(recent_vol / avg_vol, 2)
            
            c_open = float(df_5m['Open'].iloc[-1])
            c_close = float(df_5m['Close'].iloc[-1])
            c_high = float(df_5m['High'].iloc[-1])
            c_low = float(df_5m['Low'].iloc[-1])
            candle_body = abs(c_close - c_open)
            candle_range = c_high - c_low if (c_high - c_low) > 0 else 0.001

            if vol_surge >= 1.3 and (candle_body / candle_range) < 0.35:
                delta_status = "🚨 [DELTA NEUTRAL ABSORPTION]: ऑर्डर्स एब्जॉर्ब हो रहे हैं। सपोर्ट/रेजिस्टेंस पर रिवर्सल बन सकता है!"
            elif vol_surge >= 1.4:
                delta_status = "✅ [HIGH VOLUME MOMENTUM]: आक्रामक ब्रेकआउट ऑर्डर्स सक्रिय हैं।"
            elif vol_surge <= 0.6:
                delta_status = "⚠️ [DRY VOLUME]: बाज़ार में कम लिक्विडिटी है, सीधे ब्रेकडाउन पर ट्रैप न हों।"

        radar_key = "GOLD" if "GOLD" in symbol.upper() or "XAU" in symbol.upper() else symbol.upper()
        radar_info = MASTER_RADARS.get(radar_key, {})

        return f"""
📊 **REAL-TIME LIVE STREAM ({symbol} -> {yf_symbol})**
• टिक-बाय-टिक लाइव भाव: {live_price:.2f}
• 1D ट्रेंड: {day_trend}
• 4H ट्रेंड व ज़ोन: {trend_4h} (High: {c_4h_high:.2f} | Low: {c_4h_low:.2f})
• वॉल्यूम सर्ज: {vol_surge}x (औसत के मुकाबले)
• डेल्टा स्थिति: {delta_status}
• मास्टर 4H V-Highs (Liquidity Sweeps): {radar_info.get('4h_v_highs', [])}
• मास्टर 4H V-Lows (Liquidity Sweeps): {radar_info.get('4h_v_lows', [])}
• की-लेवल्स (Support & Resistance): {radar_info.get('key_levels', [])}
"""
    except Exception as e:
        logger.warning(f"Live Stream Warning: {str(e)}")
        return f"📊 **[LIVE AUDIT - {symbol}]**\n• सामान्य डेटा मोड एक्टिव है।"

# =========================================================
# 4. Google Gemini 3.5 Flash Lite Engine
# =========================================================
def query_gemini_auto(prompt_text: str, image_bytes: bytes = None) -> str:
    if not GEMINI_API_KEY:
        return "❌ Error: Gemini API Key Not Found."

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
# 5. Core Trade Strategy Evaluator (Two-Way Actionable Plan)
# =========================================================
def evaluate_trade_logic(symbol: str, user_notes: str = "", image_bytes: bytes = None) -> str:
    realtime_context = fetch_realtime_market_data(symbol)

    full_prompt = f"""
You are an Elite Institutional Trading Engine and Risk Manager for a $2,500 The5ers High Stakes account.
Evaluate this setup or query for: {symbol}

[REAL-TIME DATA & STRATEGY DATABASE]:
{realtime_context}

[USER INPUT / IMAGE NOTES]:
{user_notes}

[CRITICAL INSTRUCTIONS - NEVER JUST SAY AVOID]:
1. If an image is provided, identify the visible market price from the price scale (or use the Real-time Tick price provided above).
2. React to the Strategy Levels:
   - **4H V-Highs / 4H V-Lows:** These are LIQUIDITY SWEEP levels. If price sweeps these levels and rejects, look for sharp reversals.
   - **Key-Levels:** These are MAJOR SUPPORT & RESISTANCE. Price will either bounce from them or break-and-retest.
3. **DO NOT JUST SAY 'AVOID' AND LEAVE WITH 'NA'. ALWAYS PROVIDE A CONCRETE TWO-WAY ACTION PLAN:**
   - State clearly what happens if price goes UP (Bullish Trigger, Entry, SL, TP1, TP2).
   - State clearly what happens if price goes DOWN (Bearish Trigger, Entry, SL, TP1, TP2).
4. **The5ers $2,500 Risk Rules:**
   - Fixed Risk: $25.00 (1%).
   - Max Stop Loss: 40 Pips (SL must be tight, under 40 pips).
   - 2-Trigger Orders: 
     * TP1 (1:1 RRR): Book 50% lot and move remaining SL to Cost/Breakeven.
     * TP2 (1:3 RRR): Main runner.

Respond strictly in this clean, powerful Hindi/Hinglish structured format:

🎯 **मार्केट स्ट्रक्चर स्थिति:** [4H V-Sweep Reversal / S&R Bounce / Breakout Retest]
📊 **लाइव डेटा:** 
   • वर्तमान भाव: [Current Price]
   • 1D / 4H ट्रेंड: [Trend status]
   • डेल्टा व वॉल्यूम: [Volume & Absorption analysis]

🟢 **BULLISH PLAN (अगर मार्केट ऊपर निकलता है):**
   • **एंट्री कंडीशन (Trigger):** [किस लेवल के ऊपर बाय एक्टिव होगा]
   • **एंट्री भाव (Buy Entry):** [Exact Price]
   • **स्टॉप लॉस (SL):** [Price] (Max 40 pips)
   • **टारगेट 1 (1:1 RRR - 50% Book):** [Price] (50% बुक + SL कॉस्ट पर)
   • **टारगेट 2 (1:3 RRR - Runner):** [Price]

🔴 **BEARISH PLAN (अगर मार्केट नीचे गिरता है):**
   • **एंट्री कंडीशन (Trigger):** [किस लेवल के नीचे सेल एक्टिव होगा]
   • **एंट्री भाव (Sell Entry):** [Exact Price]
   • **स्टॉप लॉस (SL):** [Price] (Max 40 pips)
   • **टारगेट 1 (1:1 RRR - 50% Book):** [Price] (50% बुक + SL कॉस्ट पर)
   • **टारगेट 2 (1:3 RRR - Runner):** [Price]

💡 **इंस्टीट्यूशनल फैसला (Master Verdict):** [सीधी 2 लाइनें: अभी करंट प्राइस पर क्या करना है और किस ट्रिगर का इंतज़ार करना है]
"""
    return query_gemini_auto(full_prompt, image_bytes)

# =========================================================
# 6. Telegram Handlers (Photo + Any Text Supported)
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🚀 *Master Institutional Actionable Sniper Engine Online!*\n"
        "-------------------------------------\n"
        "• विज़न + टिक-बाय-टिक लाइव मार्केट डेटा\n"
        "• 4H V-Shape लिक्विडिटी स्वीप + की-लेवल्स (S/R) एक्टिव\n"
        "• दो-तरफ़ा एक्शन प्लान (ऊपर और नीचे दोनों तरफ़ के ट्रिगर्स)\n"
        "• The5ers $2.5K रूल्स: 1:1 पर 50% बुक + Breakeven, और 1:3 रनर\n"
        "• कोई भी चार्ट भेजें या टेक्स्ट में पूछें, तुरंत पूरा एक्शन प्लान मिलेगा!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📡 *MASTER 4H V-SHAPE & KEY-LEVELS RADAR:*\n"]
    for asset, data in MASTER_RADARS.items():
        lines.append(f"🔹 *{asset}:*")
        lines.append(f"  🔺 4H V-Highs (Sweeps): {', '.join(map(str, data.get('4h_v_highs', [])))}")
        lines.append(f"  🔻 4H V-Lows (Sweeps): {', '.join(map(str, data.get('4h_v_lows', [])))}")
        lines.append(f"  🎯 Key-Levels (S/R): {', '.join(map(str, data.get('key_levels', [])))}\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    await message.reply_text("👁️ [लाइव मार्केट ऑडिट] चार्ट से 4H स्वीप, S&R लेवल्स और दोनों तरफ़ के एक्शन प्लान तैयार हो रहे हैं...")

    try:
        photo_file = await message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()

        symbol_detect_prompt = (
            "Analyze this trading chart image and extract ONLY the Symbol/Pair name "
            "(e.g. XAUUSD, GOLD, EURUSD, GBPUSD, BTCUSD). Return only the symbol name."
        )
        detected_symbol_raw = query_gemini_auto(symbol_detect_prompt, bytes(image_bytes)).strip().upper()
        detected_symbol = re.sub(r'[^A-Z0-9]', '', detected_symbol_raw)

        if not detected_symbol or len(detected_symbol) < 3:
            detected_symbol = "GOLD"

        caption = message.caption or ""
        analysis_result = evaluate_trade_logic(detected_symbol, user_notes=caption, image_bytes=bytes(image_bytes))
        await message.reply_text(f"📊 **Symbol Identified:** {detected_symbol}\n\n{analysis_result}")
    except Exception as e:
        await message.reply_text(f"⚠️ स्कैन में समस्या: {str(e)}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    if user_text.startswith('/'):
        return

    await update.message.reply_text("🔍 [लाइव मार्केट ऑडिट] लेवल्स चेक कर बुलिश व बेयरिश एक्शन प्लान तैयार हो रहा है...")
    
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
# 7. Main Function
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

    logger.info("Bot running with Actionable Dual-Trigger Engine...")
    app.run_polling()

if __name__ == "__main__":
    main()
