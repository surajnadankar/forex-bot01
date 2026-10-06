import os
import io
import re
import base64
import logging
import requests
import yfinance as yf
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config & Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID")

# Standard Ticker Mapping for Yahoo Finance
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
    "ETHUSD": "ETH-USD",
    "US30": "^DJI",
    "NAS100": "^IXIC",
    "US500": "^GSPC",
    "CRUDEOIL": "CL=F",
    "NGAS": "NG=F"
}

def resolve_yf_ticker(raw_symbol: str) -> str:
    """Symbol ko Yahoo Finance ticker mein map karta hai."""
    clean_sym = re.sub(r'[^A-Z0-9]', '', raw_symbol.upper())
    if clean_sym in TICKER_MAP:
        return TICKER_MAP[clean_sym]
    if len(clean_sym) == 6 and not clean_sym.endswith("USD"):
        return f"{clean_sym}=X"
    return clean_sym

def fetch_live_market_context(symbol: str) -> str:
    """Yahoo Finance se exact live data aur 4H/1D levels fetch karta hai."""
    try:
        yf_symbol = resolve_yf_ticker(symbol)
        ticker = yf.Ticker(yf_symbol)
        
        df_1d = ticker.history(period="5d", interval="1d")
        df_4h = ticker.history(period="5d", interval="1h")
        
        if df_1d.empty:
            return f"⚠️ {symbol} ({yf_symbol}) ka live data prapt nahi ho saka."
        
        live_price = df_1d['Close'].iloc[-1]
        day_high = df_1d['High'].iloc[-1]
        day_low = df_1d['Low'].iloc[-1]
        
        c_4h_high = df_4h['High'].max() if not df_4h.empty else day_high
        c_4h_low = df_4h['Low'].min() if not df_4h.empty else day_low
        vol_surge = df_1d['Volume'].iloc[-1] / (df_1d['Volume'].mean() + 1e-5) if 'Volume' in df_1d else 1.0
        
        delta_analysis = "🟢 [HIGH VOLUME MOMENTUM]" if vol_surge > 1.2 else "⚠️ [DRY VOLUME - Potential Trap]"

        return f"""
📊 **YAHOO FINANCE LIVE AUDIT ({symbol} -> {yf_symbol})**
- **Current Price:** {live_price:.5f}
- **1D Range:** High: {day_high:.5f} | Low: {day_low:.5f}
- **4H Key Zone:** High: {c_4h_high:.5f} | Low: {c_4h_low:.5f}
- **Volume Surge Ratio:** {vol_surge:.2f}x
- **Volume Status:** {delta_analysis}
"""
    except Exception as e:
        logger.warning(f"Yahoo Finance Fetch Warning: {str(e)}")
        return f"⚠️ Live Market Fetch Warning for {symbol}: Could not resolve exact prices."

def query_gemini_auto(prompt_text: str, image_bytes: bytes = None) -> str:
    """Gemini API Call using gemini-3.5-flash-lite"""
    if not GEMINI_API_KEY:
        return "❌ Error: Gemini API Key Not Found in Environment Variables."

    # Using exact model: gemini-3.5-flash-lite
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
                return candidate["content"]["parts"][0]["text"]
                
        if "error" in res_json:
            return f"❌ AI Engine Error: {res_json['error'].get('message', 'Unknown Error')}"
            
        return "❌ AI Response Format Error."
    except Exception as e:
        return f"❌ API Connection Error: {str(e)}"

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram photo message handler"""
    message = update.message
    await message.reply_text("🔍 [लाइव मार्केट ऑडिट] इमेज और लाइव डेटा स्कैन हो रहा है...")

    photo_file = await message.photo[-1].get_file()
    image_bytes = await photo_file.download_as_bytearray()

    # Step 1: Detect Symbol via Gemini
    symbol_detect_prompt = """
    Analyze this chart image and extract ONLY the Symbol/Pair name (e.g. EURAUD, XAUUSD, BTCUSD, US30). 
    Do NOT output any extra words or analysis. Just return the Symbol name.
    """
    detected_symbol_raw = query_gemini_auto(symbol_detect_prompt, bytes(image_bytes)).strip().upper()
    detected_symbol = re.sub(r'[^A-Z0-9]', '', detected_symbol_raw)

    if not detected_symbol or len(detected_symbol) < 3:
        detected_symbol = "EURAUD"

    # Step 2: Fetch Live Data for detected symbol
    live_context = fetch_live_market_context(detected_symbol)

    # Step 3: Full Strategy Analysis
    full_prompt = f"""
    You are an expert Institutional Smart Money & Liquidity Trading AI Bot.
    
    [DETECTED PAIR]: {detected_symbol}
    [LIVE MARKET DATA FROM API]:
    {live_context}

    [INSTRUCTIONS FOR ANALYSIS]:
    1. Verify if the market is at Key Levels (Support/Resistance, Monday High/Low, or 4H Sweep Zone).
    2. Do NOT rely ONLY on V-Shape Liquidity Sweeps.
       - If V-Shape Sweep exists with Dry Volume -> Mark as AVOID TRAP / REVERSAL TRAP.
       - If Breakout / Trend Continuation exists with High Volume -> Provide Continuation Signal.
    3. Strictly use the price levels provided in the LIVE MARKET DATA above for Entry, SL, and TP. Do NOT hallucinate prices.
    4. Provide output in clear Hindi/Hinglish format with:
       - **निर्णय (Decision):** (TAKE TRADE / AVOID TRAP)
       - **लाइव स्थिति:** (Trend & Volume)
       - **चार्ट पैटर्न:** (Sweep / Breakout / Retest)
       - **एंट्री विवरण (2 Lots):** Entry Price, SL, TP1 (50% Book), TP2 (Runner)
       - **स्पष्ट फैसला (Verdict):** Brief explanation.
    """

    analysis_result = query_gemini_auto(full_prompt, bytes(image_bytes))
    await message.reply_text(f"📊 **Symbol Identified:** {detected_symbol}\n\n{analysis_result}")

def main():
    if not TELEGRAM_BOT_TOKEN:
        print("Telegram Bot Token Not Found!")
        return

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    print("Bot is running smoothly on gemini-3.5-flash-lite...")
    app.run_polling()

if __name__ == "__main__":
    main()
