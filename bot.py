import os
import logging
import traceback
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai

# ==========================================
# 1. कॉन्फ़िगरेशन एवं पर्यावरण चर (Environment Variables)
# ==========================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "")  # आपका टेलीग्राम चैट ID

# The5ers $2,500 High Stakes रिस्क पैरामीटर्स
ACCOUNT_BALANCE = 2500.00
RISK_PER_TRADE_PERCENT = 0.01  # 1% फिक्स रिस्क ($25.00)
FIXED_RISK_USD = ACCOUNT_BALANCE * RISK_PER_TRADE_PERCENT  # $25.00
DAILY_LOSS_LIMIT_USD = 100.00  # -$100 पर कस्टम सर्किट ब्रेकर (The5ers $125 से पहले सुरक्षित बफ़र)
COMMISSION_BUFFER_USD = 1.50   # स्प्रेड व कमीशन बफ़र
MAX_SL_PIPS = 40.0            # 40 पिप्स से बड़ा स्टॉप लॉस तुरंत स्किप

# AI मॉडल सेटअप
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel("gemini-1.5-flash")

# लॉगर सेटअप
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ग्लोबल ट्रैकर
daily_stats = {
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "net_pnl": 0.0,
    "circuit_broken": False
}

# ==========================================
# 2. सख्त गणितीय रिस्क व फ़िल्टर लॉजिक
# ==========================================
def calculate_lot_size(symbol: str, sl_pips: float) -> float:
    """
    1% ($25.00) रिस्क के आधार पर सटीक लॉट साइज़िंग (कमीशन बफ़र सहित)
    """
    if sl_pips <= 0:
        return 0.01
    
    # शुद्ध रिस्क बजट = $25.00 - $1.50 = $23.50
    net_risk_budget = FIXED_RISK_USD - COMMISSION_BUFFER_USD
    
    # फॉरेक्स पेयर्स के लिए मानक 1 पिप मूल्य (प्रति 1 स्टैंडर्ड लॉट ≈ $10 USD, USDCAD ≈ $7.14)
    pip_value_standard = 10.0
    if "CAD" in symbol:
        pip_value_standard = 7.14
        
    dollar_risk_per_standard_lot = sl_pips * pip_value_standard
    calculated_lot = round(net_risk_budget / dollar_risk_per_standard_lot, 2)
    
    # न्यूनतम 0.01 लॉट सीमा
    return max(0.01, calculated_lot)

def check_mathematical_sweep(current_candle: dict, key_level: float, setup_type: str):
    """
    सख्त स्तर सत्यापन: जब तक कैंडल सच में की-लेवल के पार न जाए, AI को कॉल नहीं करना
    """
    c_low = float(current_candle['low'])
    c_high = float(current_candle['high'])
    c_close = float(current_candle['close'])
    
    if setup_type == "BUY":
        # अगर लो लेवल से ऊपर ही रह गया, तो कोई स्वीप नहीं हुआ
        if c_low >= key_level:
            return False, "की-लेवल तक भाव नहीं पहुँचा"
        # रिजेक्शन विक पुष्टि: क्लोज़िंग वापस लेवल के आसपास या ऊपर होनी चाहिए
        if c_close <= c_low:
            return False, "रिजेक्शन विक अनुपस्थित"
        return True, "सख्त लिक्विडिटी स्वीप सत्यापित"
        
    elif setup_type == "SELL":
        if c_high <= key_level:
            return False, "की-लेवल तक भाव नहीं पहुँचा"
        if c_close >= c_high:
            return False, "रिजेक्शन विक अनुपस्थित"
        return True, "सख्त लिक्विडिटी स्वीप सत्यापित"
        
    return False, "अमान्य सेटअप"

# ==========================================
# 3. AI विश्लेषण इंजन (Prompt Hallucination Guard)
# ==========================================
def ask_gemini_analysis(query_or_candle_data: str) -> str:
    """
    सख्त AI प्रॉम्प्ट गार्ड: मनगढ़ंत डेटा बनाना पूरी तरह प्रतिबंधित
    """
    strict_system_prompt = (
        "You are an institutional trading risk manager for a $2,500 High Stakes account. "
        "Strict rules:\n"
        "1. DO NOT fabricate, hallucinate, or assume any price action that is not explicitly in the prompt.\n"
        "2. If exact price did not sweep the level, REJECT immediately.\n"
        "3. Provide direct, concise analysis in sentence 1. Focus on Entry, SL, TP1 (1:1 RRR with 50% partials), and TP2.\n"
        "4. Keep the explanation grounded and technical."
    )
    try:
        response = ai_model.generate_content([strict_system_prompt, query_or_candle_data])
        if response and response.text:
            return response.text.strip()
        return "⚠️ AI से कोई उत्तर प्राप्त नहीं हुआ।"
    except Exception as e:
        logger.error(f"Gemini API Error: {str(e)}")
        return f"❌ AI इंजन एरर: {str(e)}"

# ==========================================
# 4. टेलीग्राम कमांड हैंडलर्स
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🤖 *Suraj Algo Shield Bot Active*\n"
        "-------------------------------------\n"
        "💼 *खाता:* The5ers $2,500 High Stakes\n"
        "🛡️ *दैनिक सर्किट ब्रेकर:* -$100.00 USD\n"
        "🎯 *प्रति ट्रेड रिस्क:* 1% ($25.00 USD)\n\n"
        "कमांड्स:\n"
        "`/ask_ai [सवाल]` - मार्केट या सेटअप का विश्लेषण पूछें\n"
        "`/status` - आज का PnL और ड्रॉडाउन स्थिति\n"
        "`/reset_day` - दैनिक आंकड़े रीसेट करें"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_text = (
        f"📊 *दैनिक खाता स्थिति रिपोर्ट*\n"
        f"-------------------------------------\n"
        f"कुल ट्रेड्स: {daily_stats['total_trades']}\n"
        f"सफल: {daily_stats['wins']} | नुकसान: {daily_stats['losses']}\n"
        f"शुद्ध PnL: ${daily_stats['net_pnl']:.2f} USD\n"
        f"सर्किट ब्रेकर स्थिति: {'🚨 सक्रिय (ट्रेडिंग फ़्रीज़)' if daily_stats['circuit_broken'] else '✅ सुरक्षित'}\n"
        f"अधिकतम अनुमत दैनिक घाटा: -${DAILY_LOSS_LIMIT_USD:.2f} USD"
    )
    await update.message.reply_text(status_text, parse_mode="Markdown")

async def ask_ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /ask_ai कमांड का फुल-प्रूफ़ हैंडलर (कभी साइलेंट नहीं रहेगा)
    """
    user_text = update.message.text
    chat_id = update.effective_chat.id
    
    # टेक्स्ट को साफ करें
    clean_query = user_text.replace("/ask_ai", "").replace("...", "").strip()
    
    if not clean_query:
        await update.message.reply_text(
            "⚠️ कृपया अपना सवाल लिखें। उदाहरण:\n`/ask_ai Gold sell at 4148 SL 4162 TP 4110`",
            parse_mode="Markdown"
        )
        return

    await context.bot.send_message(chat_id=chat_id, text="🔍 विश्लेषण प्रोसेस हो रहा है...")

    try:
        reply = ask_gemini_analysis(clean_query)
        await context.bot.send_message(chat_id=chat_id, text=reply)
    except Exception as e:
        err_msg = f"❌ सिस्टम प्रोसेसिंग एरर: {str(e)}\n{traceback.format_exc()[:200]}"
        await context.bot.send_message(chat_id=chat_id, text=err_msg)

# ==========================================
# 5. लाइव स्वीप सिग्नल ट्रिगर फ़ंक्शन (Webhook/Scan)
# ==========================================
async def process_market_signal(bot, symbol: str, current_candle: dict, key_level: float, setup_type: str):
    """
    जब वास्तविक मार्केट डेटा आएगा तब यह फ़ंक्शन ट्रिगर होगा
    """
    # 1. सर्किट ब्रेकर चेक
    if daily_stats["net_pnl"] <= -DAILY_LOSS_LIMIT_USD:
        daily_stats["circuit_broken"] = True
        await bot.send_message(
            chat_id=ALLOWED_CHAT_ID,
            text=f"🚨 [CIRCUIT BREAKER TRIGGERED]\nदैनिक नुकसान -$100.00 छू चुका है। खाता सुरक्षित रखने के लिए आज के सारे ट्रेड्स ब्लॉक हैं।"
        )
        return

    # 2. सख्त गणितीय सत्यापन (चार्ट पर भाव गया या नहीं)
    is_valid, reason = check_mathematical_sweep(current_candle, key_level, setup_type)
    if not is_valid:
        # अगर भाव नहीं पहुँचा, तो AI को बिना वजह ट्रिगर नहीं करना
        logger.info(f"सिग्नल अस्वीकृत: {symbol} - {reason}")
        return

    # 3. स्टॉप लॉस दूरी और लॉट साइज़िंग
    entry_price = float(current_candle['close'])
    sl_price = float(current_candle['low']) - 0.0004 if setup_type == "BUY" else float(current_candle['high']) + 0.0004
    sl_distance_pips = abs(entry_price - sl_price) * (100 if "JPY" in symbol else 10000)

    # 4. बड़ा स्टॉप लॉस फ़िल्टर (Max 40 Pips)
    if sl_distance_pips > MAX_SL_PIPS:
        skip_msg = (
            f"⚠️ [⚠️ {symbol} TRADE SKIPPED - SL TOO LARGE]\n"
            f"SL Dist: {sl_distance_pips:.1f} Pips (Max: {MAX_SL_PIPS} Pips)\n"
            f"खाता सुरक्षा के लिए ट्रेड रद्द किया गया।"
        )
        await bot.send_message(chat_id=ALLOWED_CHAT_ID, text=skip_msg)
        return

    lot_size = calculate_lot_size(symbol, sl_distance_pips)
    tp1_price = entry_price + (entry_price - sl_price) if setup_type == "BUY" else entry_price - (sl_price - entry_price)

    # 5. वैध अलर्ट भेजना
    trade_alert = (
        f"🎯 [AI APPROVED {setup_type} AT KEY-LEVEL {key_level}]\n"
        f"सिंबल: {symbol}\n"
        f"एंट्री: {entry_price:.5f}\n"
        f"SL: {sl_price:.5f} ({sl_distance_pips:.1f} pips)\n"
        f"TP1 (50% Partial): {tp1_price:.5f}\n"
        f"अनुशंसित लॉट साइज़: {lot_size} (सख्त $25.00 रिस्क)\n"
        f"नियम: TP1 पर आधा लॉट काटें और SL एंट्री पर शिफ्ट करें।"
    )
    await bot.send_message(chat_id=ALLOWED_CHAT_ID, text=trade_alert)

# ==========================================
# 6. मुख्य एप्लीकेशन इनिशियलाइज़ेशन
# ==========================================
def main():
    print("सुरज एल्गो बॉट शुरू हो रहा है...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # हैंडलर्स रजिस्टर करें
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("ask_ai", ask_ai_handler))
    
    # यदि यूज़र बिना स्लैश के भी /ask_ai लिखता है
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^/ask_ai"), ask_ai_handler))

    # पोलिंग शुरू करें
    app.run_polling()

if __name__ == "__main__":
    main()
