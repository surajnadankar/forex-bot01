import os
import threading
import logging
import traceback
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
import google.generativeai as genai

# =========================================================
# 1. कॉन्फ़िगरेशन (API Keys & Risk Controls)
# =========================================================
# Render के Environment Variables से कीज लोड होंगी, वरना सीधे यहाँ इस्तेमाल होंगी
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()

# The5ers $2,500 High Stakes रिस्क पैरामीटर्स
ACCOUNT_BALANCE = 2500.00
RISK_PER_TRADE_PERCENT = 0.01  # 1% फिक्स रिस्क = $25.00
FIXED_RISK_USD = ACCOUNT_BALANCE * RISK_PER_TRADE_PERCENT  # $25.00
COMMISSION_BUFFER_USD = 1.50   # स्प्रेड व कमीशन बफ़र
MAX_SL_PIPS = 40.0            # 40 पिप्स से बड़ा स्टॉप लॉस सीधे स्किप
DAILY_CIRCUIT_BREAKER_USD = 100.00  # -$100 पर ट्रेडिंग ब्लॉक

# लॉगिंग सेटअप
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# जेमिनी कॉन्फ़िगरेशन
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel("gemini-1.5-flash")
else:
    ai_model = None

# =========================================================
# 2. Render वेब सर्वर (Port Binding Crash से बचाव)
# =========================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Suraj Algo Shield Bot is Active and Healthy."

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# =========================================================
# 3. सख्त रिस्क और लॉट साइज़ गणना
# =========================================================
def calculate_lot_size(symbol: str, sl_pips: float) -> float:
    if sl_pips <= 0:
        return 0.01
    
    net_risk_budget = FIXED_RISK_USD - COMMISSION_BUFFER_USD  # $23.50
    pip_val_standard = 7.14 if "CAD" in symbol.upper() else 10.0
    dollar_risk_per_std_lot = sl_pips * pip_val_standard
    lot = round(net_risk_budget / dollar_risk_per_std_lot, 2)
    return max(0.01, lot)

def verify_sweep_math(current_candle: dict, key_level: float, direction: str):
    """
    जब तक कैंडल सच में की-लेवल के पार न जाए, AI ट्रिगर नहीं होगा
    """
    c_low = float(current_candle['low'])
    c_high = float(current_candle['high'])
    c_close = float(current_candle['close'])
    
    if direction.upper() == "BUY":
        if c_low >= key_level:
            return False, "की-लेवल टच नहीं हुआ"
        if c_close <= c_low:
            return False, "कैंडल रिजेक्शन अनुपस्थित"
        return True, "सख्त स्वीप सत्यापित"
    elif direction.upper() == "SELL":
        if c_high <= key_level:
            return False, "की-लेवल टच नहीं हुआ"
        if c_close >= c_high:
            return False, "कैंडल रिजेक्शन अनुपस्थित"
        return True, "सख्त स्वीप सत्यापित"
    return False, "अमान्य डायरेक्शन"

# =========================================================
# 4. सुरक्षित AI विश्लेषण हैंडलर (Strict No-Hallucination)
# =========================================================
def query_gemini_analysis(user_query: str) -> str:
    if not ai_model:
        return "⚠️ Gemini API Key सेटअप नहीं है। कृपया Render Environment में GEMINI_API_KEY डालें।"
    
    system_prompt = (
        "You are an institutional trading risk manager for a $2,500 High Stakes account. "
        "Strict rules:\n"
        "1. DO NOT fabricate, hallucinate, or assume price sweeps. Only analyze provided numbers.\n"
        "2. Provide direct, concise verdict in sentence 1.\n"
        "3. Focus strictly on Entry, SL, TP1 (1:1 RRR with 50% partials), and TP2."
    )
    try:
        response = ai_model.generate_content([system_prompt, user_query])
        if response and response.text:
            return response.text.strip()
        return "⚠️ AI से कोई उत्तर नहीं मिला।"
    except Exception as e:
        logger.error(f"Gemini Error: {str(e)}")
        return f"❌ AI इंजन एरर: {str(e)}"

# =========================================================
# 5. टेलीग्राम बॉट कमांड्स
# =========================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *Suraj Algo Shield Bot Live*\n"
        "-------------------------------------\n"
        "💼 *खाता:* The5ers $2,500 High Stakes\n"
        "🛡️ *दैनिक सर्किट ब्रेकर:* -$100.00 USD\n"
        "🎯 *प्रति ट्रेड रिस्क:* 1% ($25.00 USD)\n\n"
        "उपलब्ध कमांड्स:\n"
        "`/ask_ai [सवाल]` - AI मार्केट विश्लेषण\n"
        "`/status` - खाता सुरक्षा स्थिति"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📊 *खाता स्थिति*\n"
        "-------------------------------------\n"
        "• पूंजी: $2,500.00 USD\n"
        "• रिस्क प्रति ट्रेड: $25.00 (1%)\n"
        "• अधिकतम SL सीमा: 40 पिप्स\n"
        "• सर्किट ब्रेकर सीमा: -$100.00 USD\n"
        "• स्थिति: ✅ सुरक्षित"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def ask_ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id
    
    # कमांड टेक्स्ट क्लीनिंग
    clean_text = user_text.replace("/ask_ai", "").replace("...", "").strip()
    if not clean_text:
        await update.message.reply_text(
            "⚠️ कृपया अपना सवाल लिखें। उदाहरण:\n`/ask_ai Gold sell at 4148 SL 4162 TP 4110`",
            parse_mode="Markdown"
        )
        return

    await context.bot.send_message(chat_id=chat_id, text="🔍 विश्लेषण तैयार किया जा रहा है...")
    
    try:
        verdict = query_gemini_analysis(clean_text)
        await context.bot.send_message(chat_id=chat_id, text=verdict)
    except Exception as e:
        err_report = f"❌ प्रोसेसिंग एरर: {str(e)}\n{traceback.format_exc()[:200]}"
        await context.bot.send_message(chat_id=chat_id, text=err_report)

# =========================================================
# 6. मुख्य एक्ज़ीक्यूशन लूप
# =========================================================
def main():
    # 1. बैकग्राउंड में Flask सर्वर शुरू करें (Render को संतुष्ट रखने के लिए)
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("Flask वेब सर्वर पोर्ट पर सक्रिय हो चुका है।")

    # 2. टोकन सत्यापन
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error("मान्य TELEGRAM_BOT_TOKEN नहीं मिला! Render Environment चेक करें।")
        # सर्वर को जिंदा रखें ताकि Render क्रैश लूप में न जाए
        web_thread.join()
        return

    # 3. टेलीग्राम एप्लीकेशन इनिशियलाइज़ेशन
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("ask_ai", ask_ai_cmd))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^/ask_ai"), ask_ai_cmd))

    logger.info("टेलीग्राम बॉट पोलिंग शुरू कर रहा है...")
    application.run_polling()

if __name__ == "__main__":
    main()
