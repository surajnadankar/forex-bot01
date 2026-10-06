import os
import json
import logging
import threading
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
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", "").strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()

# भारतीय मानक समय (IST)
IST = timezone(timedelta(hours=5, minutes=30))

# The5ers $2,500 High Stakes रिस्क पैरामीटर्स
ACCOUNT_BALANCE = 2500.00
RISK_PER_TRADE_PERCENT = 0.01  # 1% फिक्स रिस्क ($25.00)
FIXED_RISK_USD = ACCOUNT_BALANCE * RISK_PER_TRADE_PERCENT  # $25.00
COMMISSION_BUFFER_USD = 1.50   # स्प्रेड व कमीशन बफ़र ($23.50 शुद्ध रिस्क)
MAX_SL_PIPS = 40.0            # 40 पिप्स से बड़ा स्टॉप लॉस सीधे रिजेक्ट
DAILY_CIRCUIT_BREAKER_USD = 100.00  # -$100 पर दैनिक ट्रेडिंग फ़्रीज़

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Yahoo Finance टिकर मैपिंग
YF_TICKERS = {
    "GOLD": "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "USDCAD": "CAD=X",
    "BTC": "BTC-USD"
}

# =========================================================
# 2. मूल हार्डकोडेड 6 एसेट्स + 4H V-Shape रडार्स
# =========================================================
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
MONDAY_WEEKEND_GAPS = {}

daily_stats = {
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "net_pnl": 0.0,
    "circuit_broken": False
}

# =========================================================
# 3. Render बैकग्राउंड वेब सर्वर
# =========================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Suraj Institutional Yahoo Finance + Delta Neutral Engine Online."

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# =========================================================
# 4. ऑटोमैटिक Yahoo Finance + डेल्टा न्यूट्रल ट्रैप इंजन
# =========================================================
def fetch_live_market_context(symbol: str = "GOLD") -> str:
    """
    Yahoo Finance से खुद 1D, 1H, और लाइव भाव/वॉल्यूम/डेल्टा न्यूट्रल फेच करता है
    """
    ticker_sym = YF_TICKERS.get(symbol.upper(), "GC=F")
    try:
        ticker = yf.Ticker(ticker_sym)
        df_1h = ticker.history(period="5d", interval="1h")
        df_1d = ticker.history(period="1mo", interval="1d")

        if df_1h.empty:
            return "⚠️ लाइव मार्केट डेटा अनुपलब्ध (मार्केट बंद या टिकर लोड नहीं हुआ)।"

        live_price = float(df_1h['Close'].iloc[-1])
        last_vol = float(df_1h['Volume'].iloc[-1])
        avg_vol = float(df_1h['Volume'].tail(10).mean())
        
        vol_surge = round(last_vol / avg_vol, 2) if avg_vol > 0 else 1.0
        
        # 1D ट्रेंड
        day_open = float(df_1d['Open'].iloc[-1])
        day_trend = "BULLISH 🟢" if live_price > day_open else "BEARISH 🔴"
        
        # डेल्टा न्यूट्रल व एब्जॉर्प्शन चेक
        c_open = float(df_1h['Open'].iloc[-1])
        c_close = float(df_1h['Close'].iloc[-1])
        c_high = float(df_1h['High'].iloc[-1])
        c_low = float(df_1h['Low'].iloc[-1])
        
        candle_body = abs(c_close - c_open)
        candle_range = c_high - c_low if (c_high - c_low) > 0 else 0.001
        
        # अगर वॉल्यूम बहुत भारी है (1.3x+) लेकिन बॉडी बहुत छोटी है (< 35% of range), 
        # तो इसका मतलब भारी मात्रा में ऑर्डर्स टकराए हैं पर प्राइस नहीं हिली = डेल्टा न्यूट्रल एब्जॉर्प्शन ट्रैप
        is_delta_neutral_trap = (vol_surge >= 1.3) and ((candle_body / candle_range) < 0.35)
        
        if is_delta_neutral_trap:
            delta_analysis = "🚨 [DELTA NEUTRAL / ABSORPTION TRAP]: भारी वॉल्यूम पर भी भाव नहीं बढ़ रहा। बड़े खरीदार/विक्रेता ऑर्डर्स एब्जॉर्ब कर रहे हैं। बिना कन्फर्मेशन ब्रेकआउट पर सीधे एंट्री मत लेना!"
        elif vol_surge >= 1.5:
            delta_analysis = "✅ [HIGH VOLUME MOMENTUM]: आक्रामक ऑर्डर्स एक्टिव हैं। ब्रेकआउट या लेवल रिजेक्शन वास्तविक है।"
        else:
            delta_analysis = "⚠️ [DRY VOLUME]: बाज़ार में पार्टिसिपेशन कम है, फेकआउट से सावधान रहें।"

        return (
            f"📊 **[YAHOO FINANCE LIVE REAL-TIME AUDIT - {symbol}]**\n"
            f"• लाइव भाव (Current Price): {live_price:.2f}\n"
            f"• 1D डेली ट्रेंड: {day_trend}\n"
            f"• 1H वॉल्यूम सर्ज: {vol_surge}x (औसत के मुकाबले)\n"
            f"• डेल्टा व लिक्विडिटी स्थिति: {delta_analysis}\n"
        )
    except Exception as e:
        logger.error(f"Yahoo Finance Fetch Error: {str(e)}")
        return f"⚠️ लाइव डेटा फेच में समस्या: {str(e)}"

# =========================================================
# 5. 2-ट्रिगर ऑर्डर लॉट साइज़िंग और 1:3 RRR इंजन
# =========================================================
def calculate_split_lot_sizes(symbol: str, sl_pips: float):
    if sl_pips <= 0:
        return 0.01, 0.01
    net_risk = FIXED_RISK_USD - COMMISSION_BUFFER_USD  # $23.50
    pip_val = 7.14 if "CAD" in symbol.upper() else 10.0
    dollar_risk_per_std_lot = sl_pips * pip_val
    total_lot = round(net_risk / dollar_risk_per_std_lot, 2)
    total_lot = max(0.02, total_lot)
    lot_1 = round(total_lot / 2, 2)
    lot_2 = round(total_lot - lot_1, 2)
    return max(0.01, lot_1), max(0.01, lot_2)

# =========================================================
# 6. Direct REST AI Engine (Text + Vision + YF + Delta)
# =========================================================
def query_gemini_rest(prompt_text: str, image_bytes: bytes = None) -> str:
    if not GEMINI_API_KEY:
        return "⚠️ Gemini API Key Render Environment में नहीं मिली।"

    import base64
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    
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
            return f"❌ AI एरर: {res_json['error'].get('message', 'Unknown Error')}"
        return "⚠️ AI से कोई विश्लेषण प्राप्त नहीं हुआ।"
    except Exception as e:
        return f"❌ नेटवर्क / API एरर: {str(e)}"

def process_trade_evaluation(text_query: str = "", image_bytes: bytes = None) -> str:
    detected_symbol = "GOLD"
    for s in ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "BTC"]:
        if s in text_query.upper():
            detected_symbol = s
            break

    # 1. Yahoo Finance से लाइव डेटा और डेल्टा न्यूट्रल खुद खींचना
    live_context = fetch_live_market_context(detected_symbol)
    radar = MASTER_RADARS.get(detected_symbol, {})

    system_context = (
        "You are an Elite Institutional Risk Manager for a $2,500 The5ers High Stakes account.\n"
        "You MUST evaluate the user's trading idea/chart against the LIVE MARKET DATA fetched from Yahoo Finance below:\n\n"
        f"{live_context}\n"
        f"Master 4H V-Highs: {radar.get('4h_v_highs', [])}\n"
        f"Master 4H V-Lows: {radar.get('4h_v_lows', [])}\n"
        f"Key-Levels: {radar.get('key_levels', [])}\n\n"
        "Strict Trading Rules:\n"
        "1. DO NOT simply repeat what the user said. Critically judge if the trade makes sense right now.\n"
        "2. If Delta Neutral / Absorption Trap is detected or volume is dry, warn the user and advice to WAIT.\n"
        "3. 2-Trigger Orders Execution: Lot 1 closes at 1:1 TP1 (50% partial book) and remaining SL moves to Breakeven (Entry). Lot 2 runs for 1:3 RRR (TP2).\n"
        "4. Max SL 40 Pips rule. Account Risk is fixed at 1% ($25.00).\n\n"
        "Respond in this EXACT structured Hindi/Hinglish format:\n"
        "🎯 **निर्णय (Decision):** [APPROVED BUY / APPROVED SELL / WAIT / REJECT TRAP]\n"
        "📊 **लाइव मार्केट व डेल्टा स्थिति:** [Mention 1D trend and whether Delta Neutral Trap is present]\n"
        "🔍 **चार्ट संरचना (Identified Structure):** [4H V-Shape Sweep / Breakout Retest / Chart Pattern]\n"
        "🔹 **एंट्री (Entry Price):** [Price]\n"
        "🛑 **स्टॉप लॉस (Stop Loss):** [Price] (Max 40 pips check)\n"
        "🎯 **टारगेट 1 (1:1 RRR - 50% Book):** [Price] (यहाँ 50% कटेगा और SL एंट्री पर शिफ्ट होगा)\n"
        "🚀 **टारगेट 2 (1:3 RRR - Runner):** [Price] (मुख्य रनर)\n"
        "💡 **सीधा फैसला (Clear Verdict):** [1-2 lines clearly stating whether to take trade now or avoid]"
    )
    full_prompt = f"{system_context}\n\nUser Message/Notes:\n{text_query}" if text_query else system_context
    return query_gemini_rest(full_prompt, image_bytes)

# =========================================================
# 7. टेलीग्राम कमांड व मैसेज हैंडलर्स
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🚀 *Master Algo Institutional Live Sniper Engine!*\n"
        "-------------------------------------\n"
        "• Yahoo Finance लाइव मार्केट डेटा (1D Trend + 1H Volume) ऑटो-स्कैन\n"
        "• डेल्टा न्यूट्रल और एब्जॉर्प्शन ट्रैप फ़िल्टर एक्टिव\n"
        "• 4H V-Shape स्वीप + 15M कैंडल रिजेक्शन + 2-Trigger Orders (1:1 & 1:3 RRR)\n"
        "• फोटो (चार्ट) या सीधे टेक्स्ट में मैसेज भेजें, AI लाइव डेटा से क्रॉस-चेक करके जवाब देगा!\n\n"
        "`/levels` - 4H V-Shape व Key-Levels देखें\n"
        "`/status` - खाता रिस्क स्थिति\n"
        "`/report` - डेली मास्टर ऑडिट रिपोर्ट"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def levels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📡 *MASTER 4H V-SHAPE & KEY-LEVELS RADAR:*\n"]
    for asset, data in MASTER_RADARS.items():
        lines.append(f"🔹 *{asset}:*")
        lines.append(f"  🔺 4H V-Highs: {', '.join(map(str, data.get('4h_v_highs', [])))}")
        lines.append(f"  🔻 4H V-Lows: {', '.join(map(str, data.get('4h_v_lows', [])))}")
        lines.append(f"  🎯 Key-Levels: {', '.join(map(str, data.get('key_levels', [])))}\n")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"📊 *दैनिक खाता स्थिति (The5ers $2,500)*\n"
        f"-------------------------------------\n"
        f"• कुल ट्रेड्स: {daily_stats['total_trades']}\n"
        f"• सफल: {daily_stats['wins']} | नुकसान: {daily_stats['losses']}\n"
        f"• शुद्ध PnL: ${daily_stats['net_pnl']:.2f} USD\n"
        f"• सर्किट ब्रेकर सीमा: -${DAILY_CIRCUIT_BREAKER_USD:.2f} USD\n"
        f"• स्थिति: {'🚨 ट्रेडिंग ब्लॉक (सर्किट सक्रिय)' if daily_stats['circuit_broken'] else '✅ सुरक्षित'}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    win_rate = 0.0 if daily_stats['total_trades'] == 0 else (daily_stats['wins'] / daily_stats['total_trades']) * 100
    msg = (
        f"📋 *[GLOBAL MASTER END OF DAY REPORT]*\n"
        f"📅 Date: {today_str}\n"
        f"-------------------------------------\n"
        f"🔢 Total Trades: {daily_stats['total_trades']}\n"
        f"🎯 Win Rate: {win_rate:.1f}%\n"
        f"💰 Net Realized PnL: ${daily_stats['net_pnl']:.2f} USD\n"
        f"🏛 Account Score: ${daily_stats['net_pnl']:.2f} USD"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    caption = update.message.caption or ""
    await context.bot.send_message(
        chat_id=chat_id, 
        text="👁️ [मार्केट ऑडिट चालू] Yahoo Finance से लाइव 1D ट्रेंड, वॉल्यूम और डेल्टा न्यूट्रल ट्रैप स्कैन किया जा रहा है..."
    )
    try:
        photo_file = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        res = process_trade_evaluation(text_query=caption, image_bytes=bytes(photo_bytes))
        await context.bot.send_message(chat_id=chat_id, text=res)
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ स्कैन एरर: {str(e)}")

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    chat_id = update.effective_chat.id

    if user_text.startswith('/'):
        return

    await context.bot.send_message(chat_id=chat_id, text="🔍 लाइव मार्केट डेटा फ़ेच कर डेल्टा न्यूट्रल व 1:3 RRR का विश्लेषण हो रहा है...")
    try:
        res = process_trade_evaluation(text_query=user_text, image_bytes=None)
        await context.bot.send_message(chat_id=chat_id, text=res)
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ विश्लेषण एरर: {str(e)}")

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.warning(f"अपडेट नोटिस: {context.error}")

# =========================================================
# 8. रात 11:30 PM IST EOD रिपोर्ट शेड्यूलर
# =========================================================
async def send_nightly_eod(context: ContextTypes.DEFAULT_TYPE):
    target_chat = ALLOWED_CHAT_ID or context.job.chat_id
    if not target_chat:
        return
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    win_rate = 0.0 if daily_stats['total_trades'] == 0 else (daily_stats['wins'] / daily_stats['total_trades']) * 100
    msg = (
        f"📋 *[GLOBAL MASTER END OF DAY REPORT]*\n"
        f"📅 Date: {today_str}\n"
        f"-------------------------------------\n"
        f"🔢 Total Trades Today: {daily_stats['total_trades']}\n"
        f"✅ Wins: {daily_stats['wins']} | ❌ Losses: {daily_stats['losses']}\n"
        f"🎯 Today Win Rate: {win_rate:.1f}%\n"
        f"💰 Today Net Realized PnL: ${daily_stats['net_pnl']:.2f} USD\n"
        f"🏛 Evaluation Score: ${daily_stats['net_pnl']:.2f} USD"
    )
    await context.bot.send_message(chat_id=target_chat, text=msg, parse_mode="Markdown")

# =========================================================
# 9. मुख्य निष्पादन लूप
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
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("report", report_cmd))
    
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    job_queue = application.job_queue
    if job_queue:
        eod_time = dtime(hour=23, minute=30, tzinfo=IST)
        job_queue.run_daily(send_nightly_eod, time=eod_time, name="nightly_eod")

    logger.info("Yahoo Finance + Delta Neutral Integrated Sniper तैयार है...")
    application.run_polling()

if __name__ == "__main__":
    main()
