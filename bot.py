import ccxt
import time
import pandas as pd
import threading
import requests
import json
import os
import base64
from datetime import datetime, timezone, timedelta
from flask import Flask

# =====================================================================
# 1. API & CREDENTIALS CONFIGURATION
# =====================================================================
TELEGRAM_BOT_TOKEN = "8895341894:AAEE-p0_Ylj6RFmqr06nx5xNT7vzyBaBTqI"
TELEGRAM_CHAT_ID = "998154896"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GOOGLE_SHEET_WEBHOOK_URL = os.environ.get("GOOGLE_SHEET_URL", "")

def send_telegram_msg(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram send error: {e}", flush=True)

# =====================================================================
# 2. WEB SERVER (KEEPS RENDER ALIVE 24/7)
# =====================================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Master Institutional AI Sniper with Hardcoded Radars Active 24/7!"

def start_web_server():
    app.run(host='0.0.0.0', port=10000)

# =====================================================================
# 3. GLOBAL RISK ENGINE & PERMANENT RADAR LEVELS
# =====================================================================
exchange = ccxt.kraken({
    'enableRateLimit': True,
    'rateLimit': 2500
})
api_lock = threading.Lock()

ACCOUNT_SIZE_USD = 2500.0
RISK_PER_TRADE_USD = 25.0       # 1.0% Risk ($25.00)
MAX_DAILY_LOSS_USD = 100.0      # Strict -$100.00 Net Daily Circuit Breaker

MAX_CRYPTO_SLOTS = 2
MAX_FOREX_SLOTS = 2
MAX_TOTAL_SLOTS = 4

PERF_FILE = "master_performance.json"
MAP_FILE = "master_market_map.json"

# आपके द्वारा भेजे गए सभी सटीक की-लेवल्स और 4H टारगेट्स कोड में परमानेंट सेट हैं
DEFAULT_MARKET_MAP = {
    'BTC': {
        'key_levels': [
            61556.58, 73669.56, 75633.23, 76697.17, 78251.18, 79274.78, 
            83628.36, 85686.88, 87701.49, 88982.81, 91193.63, 91711.6, 
            92668.922, 93167.85, 96916.06, 98920.67, 101245.6, 102730.93, 
            106474.49, 117802.73, 126192.07
        ],
        'h4_highs': [87345.85, 90385.0],
        'h4_lows': [74988.74, 80240.59, 82679.42]
    },
    'GOLD': {
        'key_levels': [
            276.436, 2561.073, 2924.338, 3213.15, 3288.019, 3401.552, 
            3917.805, 4231.59, 4519.006, 4666.543, 4868.081, 5451.16, 5602.225
        ],
        'h4_highs': [4399.14],
        'h4_lows': [3959.981, 3996.199]
    },
    'EURUSD': {
        'key_levels': [0.5633, 0.82311, 0.96756, 1.1459, 1.22827, 1.4102, 1.51442, 1.6038],
        'h4_highs': [1.17075, 1.18448, 1.19227, 1.20728],
        'h4_lows': [1.01829, 1.07362, 1.10763]
    },
    'GBPUSD': {
        'key_levels': [
            1.0545, 1.1832, 1.21, 1.2709, 1.301, 1.314, 
            1.387, 1.593, 1.7191, 1.8331, 2.01, 2.1161, 2.446, 2.644
        ],
        'h4_highs': [1.3308, 1.3402, 1.3566, 1.3673],
        'h4_lows': [1.301, 1.3098, 1.314]
    },
    'USDJPY': {
        'key_levels': [76.161, 101.18, 127.22, 139.889, 146.478, 152.206, 160.201, 163.988],
        'h4_highs': [158.994, 160.329, 163.988],
        'h4_lows': [149.578, 152.206, 153.003, 156.498]
    },
    'USDCAD': {
        'key_levels': [
            0.90585, 0.94469, 1.06204, 1.2013, 1.29517, 1.34818, 
            1.37708, 1.38291, 1.39812, 1.41546, 1.42964, 1.43952, 1.44514, 1.4542, 1.47937
        ],
        'h4_highs': [1.42964, 1.43952, 1.44514],
        'h4_lows': [1.38974, 1.39812, 1.41546]
    }
}

def load_json(filepath, default):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Load error ({filepath}): {e}", flush=True)
    return default

def save_json(filepath, data):
    try:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Save error ({filepath}): {e}", flush=True)

def log_trade_to_google_sheet(trade, exit_price, final_pnl, outcome_label, ai_lesson="", delta_status="NEUTRAL"):
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    payload = {
        "timestamp_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "asset": trade["asset"],
        "side": trade["side"],
        "strategy_type": trade.get("label", "4H_SWEEP_15M_REVERSAL"),
        "entry_price": trade["entry"],
        "exit_price": exit_price,
        "stop_loss": trade["sl"],
        "tp": trade["tp2"],
        "delta_sentiment": delta_status,
        "pnl_usd": round(final_pnl, 2),
        "roi_pct": round((final_pnl / ACCOUNT_SIZE_USD) * 100, 2),
        "outcome": outcome_label,
        "ai_lesson": ai_lesson
    }
    if GOOGLE_SHEET_WEBHOOK_URL:
        try:
            requests.post(GOOGLE_SHEET_WEBHOOK_URL, json=payload, timeout=12)
        except Exception as e:
            print(f"Google Sheet logging error: {e}", flush=True)

def fetch_past_trades_memory_from_sheet():
    if not GOOGLE_SHEET_WEBHOOK_URL:
        return "No past sheet history available."
    try:
        res = requests.get(GOOGLE_SHEET_WEBHOOK_URL, timeout=10)
        if res.status_code == 200:
            return json.dumps(res.json(), indent=1)
    except Exception as e:
        print(f"Sheet memory fetch error: {e}", flush=True)
    return "No past sheet history available."

performance = load_json(PERF_FILE, {
    'today_date': "",
    'today_trades': 0,
    'today_wins': 0,
    'today_losses': 0,
    'today_pnl_usd': 0.0,
    'alltime_trades': 0,
    'alltime_wins': 0,
    'alltime_losses': 0,
    'alltime_pnl_usd': 0.0,
    'daily_summary_sent': False
})

market_map = load_json(MAP_FILE, DEFAULT_MARKET_MAP)

# सुनिश्चित करें कि हार्डकोडेड लेवल्स कभी खाली न हों
for k in DEFAULT_MARKET_MAP.keys():
    if k not in market_map or not market_map[k].get('key_levels'):
        market_map[k] = DEFAULT_MARKET_MAP[k]
save_json(MAP_FILE, market_map)

risk_guard = {
    'current_date': "",
    'is_frozen_today': False
}

# =====================================================================
# 4. ASSET SPECIFICATIONS (CRYPTO + FOREX)
# =====================================================================
ASSETS = {
    'BTC': {
        'category': 'CRYPTO',
        'symbol': 'BTC/USDT',
        'tag': '🟢 BTC/USDT',
        'is_forex': False,
        'sl_buffer': 150.0,
        'min_allowed_sl': 200.0,
        'max_allowed_sl': 1350.0,
        'trade_on_weekends': True,
        'active_trade': None,
        'last_candle_time': None
    },
    'GOLD': {
        'category': 'CRYPTO',
        'symbol': 'PAXG/USD',
        'tag': '🟡 GOLD (XAU/USD)',
        'is_forex': False,
        'sl_buffer': 3.0,
        'min_allowed_sl': 3.5,
        'max_allowed_sl': 15.0,
        'trade_on_weekends': False,
        'active_trade': None,
        'last_candle_time': None
    },
    'EURUSD': {
        'category': 'FOREX',
        'symbol': 'EUR/USD',
        'tag': '💶 EUR/USD',
        'is_forex': True,
        'pip_size': 0.0001,
        'sl_buffer': 0.0006,
        'min_allowed_sl': 0.0015,
        'max_allowed_sl': 0.0040,
        'trade_on_weekends': False,
        'active_trade': None,
        'last_candle_time': None
    },
    'GBPUSD': {
        'category': 'FOREX',
        'symbol': 'GBP/USD',
        'tag': '💷 GBP/USD',
        'is_forex': True,
        'pip_size': 0.0001,
        'sl_buffer': 0.0007,
        'min_allowed_sl': 0.0018,
        'max_allowed_sl': 0.0045,
        'trade_on_weekends': False,
        'active_trade': None,
        'last_candle_time': None
    },
    'USDJPY': {
        'category': 'FOREX',
        'symbol': 'USD/JPY',
        'tag': '💴 USD/JPY',
        'is_forex': True,
        'pip_size': 0.01,
        'sl_buffer': 0.08,
        'min_allowed_sl': 0.25,
        'max_allowed_sl': 0.85,
        'trade_on_weekends': False,
        'active_trade': None,
        'last_candle_time': None
    },
    'USDCAD': {
        'category': 'FOREX',
        'symbol': 'USD/CAD',
        'tag': '🍁 USD/CAD',
        'is_forex': True,
        'pip_size': 0.0001,
        'sl_buffer': 0.0006,
        'min_allowed_sl': 0.0015,
        'max_allowed_sl': 0.0040,
        'trade_on_weekends': False,
        'active_trade': None,
        'last_candle_time': None
    }
}

def get_slot_counts():
    crypto_count = sum(1 for k, v in ASSETS.items() if v['category'] == 'CRYPTO' and v['active_trade'] is not None)
    forex_count = sum(1 for k, v in ASSETS.items() if v['category'] == 'FOREX' and v['active_trade'] is not None)
    return crypto_count, forex_count, (crypto_count + forex_count)

def check_global_circuit_breaker():
    global performance, risk_guard
    if performance['today_pnl_usd'] <= -MAX_DAILY_LOSS_USD:
        risk_guard['is_frozen_today'] = True
        send_telegram_msg(
            "🚨🚨 *[GLOBAL MASTER CIRCUIT BREAKER ACTIVATED]* 🚨🚨\n"
            f"कुल संयुक्त दैनिक नुकसान (-${abs(performance['today_pnl_usd']):.2f}) -$100.00 सीमा पर पहुँच गया!\n"
            "🛡️ *ACCOUNT SAFEGUARD:* आज रात 12:00 AM IST तक पूरा बॉट फ़्रीज़ रहेगा।"
        )

# =====================================================================
# 5. MARKET DATA & VOLUME DELTA ENGINE (DATA SENTIMENT)
# =====================================================================
def get_candles(symbol, timeframe, limit=35):
    with api_lock:
        try:
            time.sleep(1.0)
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as e:
            print(f"Kraken fetch error ({symbol} - {timeframe}): {e}", flush=True)
            return None

def calculate_volume_delta_profile(df):
    try:
        if df is None or len(df) < 5:
            return 0.0, 1.0, "NEUTRAL"
        last = df.iloc[-2]
        c_open = float(last['open'])
        c_close = float(last['close'])
        c_high = float(last['high'])
        c_low = float(last['low'])
        c_vol = float(last['volume'])

        total_range = c_high - c_low
        if total_range <= 0:
            return 0.0, 1.0, "NEUTRAL"

        buy_weight = (c_close - c_low) / total_range
        sell_weight = (c_high - c_close) / total_range
        net_delta = c_vol * (buy_weight - sell_weight)

        avg_vol = df['volume'].iloc[-12:-2].mean()
        vol_ratio = round(c_vol / avg_vol, 2) if avg_vol > 0 else 1.0

        if net_delta > 0 and vol_ratio >= 1.2:
            vol_status = "POSITIVE (BULLISH DELTA)"
        elif net_delta < 0 and vol_ratio >= 1.2:
            vol_status = "NEGATIVE (SELLER ABSORPTION)"
        else:
            vol_status = "NEUTRAL / BALANCED"

        return round(net_delta, 2), vol_ratio, vol_status
    except Exception as e:
        print(f"Delta calculation error: {e}", flush=True)
        return 0.0, 1.0, "NEUTRAL"

# =====================================================================
# 6. GEMINI 3.8-FLASH MULTIMODAL & COGNITIVE REASONING ENGINE
# =====================================================================
def call_gemini(prompt_text, image_b64=None):
    if not GEMINI_API_KEY:
        return None

    model_name = "gemini-3.8-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}

    parts = []
    if image_b64:
        parts.append({
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": image_b64
            }
        })
    parts.append({"text": prompt_text})
    payload = {"contents": [{"parts": parts}]}

    for attempt in range(3):
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=40)
            if res.status_code == 200:
                data = res.json()
                return data['candidates'][0]['content']['parts'][0]['text']
            elif res.status_code in [503, 429]:
                time.sleep(3)
                continue
            else:
                return None
        except Exception:
            time.sleep(2)

    return None

# AI COGNITIVE REASONING AT DAILY/MONTHLY KEY-LEVEL
def ask_ai_to_evaluate_key_level_entry(asset_name, level, df_15m, delta_val, vol_ratio, vol_status):
    recent_candles = []
    for i in range(-5, -1):
        row = df_15m.iloc[i]
        c_type = "GREEN" if row['close'] >= row['open'] else "RED"
        recent_candles.append(f"O:{row['open']}, H:{row['high']}, L:{row['low']}, C:{row['close']} ({c_type})")

    candles_str = "\n".join(recent_candles)
    current_close = float(df_15m.iloc[-2]['close'])

    prompt = f"""
    You are an elite quantitative liquidity and smart money analyst for Suraj Nadankar's $2,500 The5ers prop account.
    Market has interacted with a major Daily/Monthly Key-Level:
    - Asset: {asset_name}
    - Daily/Monthly Key-Level: {level}
    - Current Candle Close: {current_close}
    - Volume Delta: {delta_val} | Volume Ratio: {vol_ratio}x ({vol_status})
    - Last 4 Closed 15M Candles:
    {candles_str}

    TASK:
    Analyze the liquidity behavior around this Daily/Monthly level {level}:
    1. Did price reject with long wicks (liquidity grab/fakeout), or is it slicing through with massive trend volume?
    2. Assess candle momentum, wick dynamics, and delta sentiment.
    3. Determine the SAFE trade:
       - DECISION: BUY (if strong bullish bounce/absorption off support)
       - DECISION: SELL (if strong bearish rejection/sweep of resistance)
       - DECISION: WAIT (if price is choppy, indecisive, or slicing through without rejection)

    Output format strictly:
    DECISION: [BUY, SELL, or WAIT]
    REASON: [Under 45 words in clean Hindi/Hinglish explaining smart money reaction at this Key-Level]
    """
    reply = call_gemini(prompt)
    return reply

def run_ai_trade_advisor(outcome, trade, current_price, delta_val, vol_ratio, vol_status):
    asset = trade['asset']
    side = trade['side']
    conf = ASSETS[asset]

    prompt = f"""
    You are an expert algorithmic prop trading coach for Suraj Nadankar's $2,500 The5ers account.
    A trade just closed:
    - Asset: {conf['tag']} | Side: {side} | Strategy: {trade.get('label')}
    - Entry: {trade['entry']} | Exit: {current_price} | Outcome: {outcome}
    - Volume Delta: {delta_val} | Delta Sentiment: {vol_status}
    
    Provide 3 concise bullet points in clean Hindi/Hinglish:
    1. Volume Delta aur absorption ke context me kya sahi tha ya kya trap tha?
    2. 4H Liquidity Sweep aur Macro S/R Shield ke context me trade ka analysis.
    3. Practical Lesson: Yeh mistake Sheet me note karke agle trade me kaise bachein?
    Keep it strictly under 80 words.
    """
    ai_resp = call_gemini(prompt)
    if ai_resp:
        send_telegram_msg(f"🧠 *[GEMINI AI RETROSPECTIVE COACH - {conf['tag']}]*\n{ai_resp.strip()}")
    return ai_resp or ""

def run_zero_trade_ai_audit():
    market_notes = []
    for asset, conf in ASSETS.items():
        data = market_map.get(asset, {})
        market_notes.append(f"{asset}: Key-Levels (D/M)={len(data.get('key_levels', []))} levels, 4H-Highs={data.get('h4_highs', [])}, 4H-Lows={data.get('h4_lows', [])}")

    prompt = f"""
    You are a professional prop firm risk analyst evaluating today's session for Suraj Nadankar's $2,500 The5ers account.
    Total trades taken today: 0 (No trade executed).
    Active Radar Levels tracked today:
    {market_notes}

    Analyze the day in clean Hindi/Hinglish (3 bullet points, under 85 words):
    1. Aaj trades na milne ka mukhya kaaran (Market chop tha ya 4H/Daily levels tak price nahi pahucha)?
    2. Daily/Monthly key levels ke context me price action ka flow kaisa tha?
    3. Kal ke session ke liye trader ke liye 1-2 practical tips.
    """
    ai_resp = call_gemini(prompt)
    if ai_resp:
        send_telegram_msg(f"🧠 *[GEMINI DAILY SESSION COACH & MARKET AUDIT]*\n{ai_resp.strip()}")

def analyze_user_chart_screenshot(file_id, user_caption=""):
    send_telegram_msg("🔍 *[GEMINI VISION ANALYZING YOUR SETUP...]*\nचार्ट, आपके इंटेंट (BUY या SELL) और Google Sheet हिस्ट्री को स्कैन किया जा रहा है...")
    try:
        f_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
        f_res = requests.get(f_url, timeout=20).json()
        if not f_res.get("ok"):
            send_telegram_msg("❌ Telegram से इमेज फ़ाइल लोड नहीं हो सकी, कृपया दोबारा भेजें।")
            return
        file_path = f_res["result"]["file_path"]

        dl_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        img_bytes = requests.get(dl_url, timeout=25).content
        img_b64 = base64.b64encode(img_bytes).decode('utf-8')

        sheet_memory = fetch_past_trades_memory_from_sheet()

        prompt = f"""
        You are an elite quantitative prop firm risk mentor for Suraj Nadankar's $2,500 The5ers account.
        User submitted a TradingView chart screenshot. User caption/notes: "{user_caption}".
        
        PAST GOOGLE SHEET TRADE MEMORY & LESSONS:
        {sheet_memory}

        CRITICAL DIRECTIONAL INSTRUCTION:
        Carefully inspect the chart visual markers and user caption to identify the trader's SPECIFIC INTENT (BUY or SELL).
        - If BUY/LONG setup: Focus EXCLUSIVELY on evaluating BUY setup. DO NOT mention SELL. If valid, give Entry, SL, TP, 1% Lot size and `/vet_trade <PAIR> BUY <ENTRY> <SL> <TP>`. If risky, state "⛔ VERDICT: BUY SETUP REJECTED / AVOID".
        - If SELL/SHORT setup: Focus EXCLUSIVELY on evaluating SELL setup. DO NOT mention BUY. If valid, give Entry, SL, TP, 1% Lot size and `/vet_trade <PAIR> SELL <ENTRY> <SL> <TP>`. If risky, state "⛔ VERDICT: SELL SETUP REJECTED / AVOID".
        Reference past sheet mistakes if a similar trap is forming on this chart. Keep analysis under 120 words in clean Hindi/Hinglish.
        """
        analysis = call_gemini(prompt, image_b64=img_b64)
        if analysis:
            send_telegram_msg(f"📊 *[GEMINI AI CHART SETUP REPORT]*\n\n{analysis.strip()}")
        else:
            send_telegram_msg("⚠️ AI चार्ट का विश्लेषण नहीं कर सका। कृपया दोबारा भेजें।")

    except Exception as e:
        print(f"Chart vision handling error: {e}", flush=True)
        send_telegram_msg(f"⚠️ चार्ट स्कैनिंग एरर: {e}")

# =====================================================================
# 7. TRADE SIZING & EXECUTION (1% RISK / 2-PART SPLIT)
# =====================================================================
def create_master_split_trade(asset_key, side, entry, calculated_sl, custom_tp=None, trade_label="4H_SWEEP_15M_REVERSAL"):
    conf = ASSETS[asset_key]
    crypto_c, forex_c, total_c = get_slot_counts()

    if total_c >= MAX_TOTAL_SLOTS:
        send_telegram_msg("⚠️ *[SLOTS FULL]* कुल 4 ट्रेड्स पहले से एक्टिव हैं!")
        return None
    if conf['category'] == 'CRYPTO' and crypto_c >= MAX_CRYPTO_SLOTS and forex_c < MAX_FOREX_SLOTS:
        if total_c >= 3:
            send_telegram_msg("⚠️ *[CRYPTO SLOTS FULL]* क्रिप्टो के स्लॉट्स भरे हुए हैं!")
            return None
    elif conf['category'] == 'FOREX' and forex_c >= MAX_FOREX_SLOTS:
        send_telegram_msg("⚠️ *[FOREX SLOTS FULL]* फॉरेक्स के स्लॉट्स भरे हुए हैं!")
        return None

    raw_distance = abs(entry - calculated_sl)
    if raw_distance <= 0: return None
    effective_distance = max(raw_distance, conf['min_allowed_sl'])

    if side == 'BUY': actual_sl = entry - effective_distance
    else: actual_sl = entry + effective_distance

    if effective_distance > conf['max_allowed_sl']:
        send_telegram_msg(f"⚠️ *[{conf['tag']} TRADE SKIPPED - SL TOO LARGE]*\nSL Dist: {effective_distance:.5f} (Max: {conf['max_allowed_sl']})")
        return None

    if conf['is_forex']:
        pips = effective_distance / conf['pip_size']
        calculated_lots = round(RISK_PER_TRADE_USD / (pips * 10.0), 2)
        if calculated_lots < 0.02: calculated_lots = 0.02
        lot1 = round(calculated_lots / 2, 2)
        lot2 = round(calculated_lots - lot1, 2)
        vol_unit_str = "Lots"
        risk_info = f"{pips:.1f} Pips"
    elif 'GOLD' in conf['tag']:
        calculated_lots = round(RISK_PER_TRADE_USD / effective_distance, 2)
        if calculated_lots < 0.02: calculated_lots = 0.02
        lot1 = round(calculated_lots / 2, 2)
        lot2 = round(calculated_lots - lot1, 2)
        vol_unit_str = "Lots"
        risk_info = f"${effective_distance:.2f}"
    else: # BTC
        calculated_lots = round(RISK_PER_TRADE_USD / effective_distance, 4)
        if calculated_lots < 0.0002: calculated_lots = 0.0002
        lot1 = round(calculated_lots / 2, 4)
        lot2 = round(calculated_lots - lot1, 4)
        vol_unit_str = "BTC"
        risk_info = f"${effective_distance:.2f}"

    if custom_tp is not None:
        tp1 = round(entry + ((custom_tp - entry) * 0.5), 5) if side == 'BUY' else round(entry - ((entry - custom_tp) * 0.5), 5)
        tp2 = custom_tp
    else:
        if side == 'BUY':
            tp1 = entry + (effective_distance * 2.5)
            tp2 = entry + (effective_distance * 5.0)
        else:
            tp1 = entry - (effective_distance * 2.5)
            tp2 = entry - (effective_distance * 5.0)

    trade = {
        'asset': asset_key,
        'side': side,
        'entry': entry,
        'sl': actual_sl,
        'tp1': tp1,
        'tp2': tp2,
        'total_volume': calculated_lots,
        'lot1_size': lot1,
        'lot2_size': lot2,
        'lot1_booked': False,
        'sl_at_be': False,
        'risk_distance': effective_distance,
        'vol_unit_str': vol_unit_str,
        'label': trade_label
    }

    send_telegram_msg(
        f"🚀 *[{conf['tag']} {trade_label} ENTRY (1% RISK)]*\n"
        f"-----------------------------\n"
        f"📈 *Direction:* {side}\n"
        f"💵 *Entry:* {entry} | *Safe SL:* {actual_sl} (Risk: {risk_info})\n"
        f"💼 *Total Volume:* {calculated_lots} {vol_unit_str}\n"
        f"   • *Lot 1:* {lot1} {vol_unit_str} (TP1 @ Half-Way: {tp1})\n"
        f"   • *Lot 2:* {lot2} {vol_unit_str} (TP2 @ Target: {tp2})\n"
        f"🎯 *TP1 Target:* {tp1}\n"
        f"🏆 *TP2 Target:* {tp2}\n"
        f"🔒 *Max Risk:* $25.00 (1.0%)\n"
        f"📊 *Global Active Slots:* {total_c + 1}/{MAX_TOTAL_SLOTS}\n"
        f"-----------------------------"
    )
    return trade

# =====================================================================
# 8. COMPLETE DUAL ENGINE: DAILY/MONTHLY AI ENTRIES + 4H SWEEPS
# =====================================================================
def process_single_asset(name):
    global performance, risk_guard, market_map
    conf = ASSETS[name]

    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    weekday = now_ist.weekday()

    if not conf['trade_on_weekends'] and (weekday == 5 or (weekday == 6 and now_ist.hour < 23)):
        return

    df_15m = get_candles(conf['symbol'], '15m', limit=35)
    if df_15m is None or len(df_15m) < 10: return

    last_closed_15m = df_15m.iloc[-2]
    prev_closed_15m = df_15m.iloc[-3]
    current_price = float(df_15m.iloc[-1]['close'])
    candle_time_15m = int(last_closed_15m['timestamp'])

    # 1. LIVE SL / TP / BE MONITORING
    trade = conf['active_trade']
    if trade is not None:
        side = trade['side']
        entry = trade['entry']
        sl = trade['sl']
        tp1 = trade['tp1']
        tp2 = trade['tp2']

        if not trade['lot1_booked']:
            hit = (current_price <= sl) if side == 'BUY' else (current_price >= sl)
            if hit:
                performance['today_trades'] += 1
                performance['alltime_trades'] += 1
                performance['today_losses'] += 1
                performance['alltime_losses'] += 1
                performance['today_pnl_usd'] -= 25.0
                performance['alltime_pnl_usd'] -= 25.0
                save_json(PERF_FILE, performance)
                check_global_circuit_breaker()

                send_telegram_msg(
                    f"🛑 *[{conf['tag']} FULL STOP LOSS HIT]* ❌\n"
                    f"Exit: {current_price} | Entry: {entry}\n"
                    f"Loss: -25.00 USD (-1.0%)\n"
                    f"Today Global Realized PnL: {performance['today_pnl_usd']:+.2f} USD"
                )
                delta_val, vol_ratio, vol_status = calculate_volume_delta_profile(df_15m)
                lesson = run_ai_trade_advisor("LOSS", trade, current_price, delta_val, vol_ratio, vol_status)
                log_trade_to_google_sheet(trade, current_price, -25.0, "FULL_STOP_LOSS", lesson, vol_status)
                conf['active_trade'] = None
                return

        if not trade['lot1_booked']:
            tp1_hit = (current_price >= tp1) if side == 'BUY' else (current_price <= tp1)
            if tp1_hit:
                trade['lot1_booked'] = True
                trade['sl'] = entry
                trade['sl_at_be'] = True
                performance['today_trades'] += 1
                performance['alltime_trades'] += 1
                performance['today_pnl_usd'] += 31.25
                performance['alltime_pnl_usd'] += 31.25
                save_json(PERF_FILE, performance)

                send_telegram_msg(
                    f"💰 *[{conf['tag']} TP1 HIT - 50% PARTIAL BOOKED]* 🎯\n"
                    f"✅ *Lot 1 Closed:* {trade['lot1_size']} {trade['vol_unit_str']} @ {current_price}\n"
                    f"💵 *Profit:* +31.25 USD\n"
                    f"🛡️ *SL Moved to Entry:* {entry} (Trade Risk-Free!)\n"
                    f"📊 *Global Net PnL:* {performance['today_pnl_usd']:+.2f} USD"
                )

        if trade['lot1_booked']:
            tp2_hit = (current_price >= tp2) if side == 'BUY' else (current_price <= tp2)
            be_hit = (current_price <= entry) if side == 'BUY' else (current_price >= entry)

            if tp2_hit:
                performance['today_wins'] += 1
                performance['alltime_wins'] += 1
                performance['today_pnl_usd'] += 62.50
                performance['alltime_pnl_usd'] += 62.50
                save_json(PERF_FILE, performance)

                send_telegram_msg(
                    f"🏆 *[{conf['tag']} TP2 HIT - FULL TARGET ACCOMPLISHED]* 🚀\n"
                    f"✅ *Lot 2 Closed:* {trade['lot2_size']} {trade['vol_unit_str']} @ {current_price}\n"
                    f"💵 *Total Trade Profit:* *+93.75 USD (+3.75% Net)*\n"
                    f"📊 *Global Net PnL:* {performance['today_pnl_usd']:+.2f} USD"
                )
                delta_val, vol_ratio, vol_status = calculate_volume_delta_profile(df_15m)
                lesson = run_ai_trade_advisor("TP2", trade, current_price, delta_val, vol_ratio, vol_status)
                log_trade_to_google_sheet(trade, current_price, +93.75, "FULL_TP2_HIT", lesson, vol_status)
                conf['active_trade'] = None
                return

            elif be_hit:
                performance['today_wins'] += 1
                performance['alltime_wins'] += 1
                save_json(PERF_FILE, performance)
                send_telegram_msg(
                    f"🛡️ *[{conf['tag']} RUNNER CLOSED AT BREAK-EVEN]*\n"
                    f"⚪ *Exit:* {entry} (P&L: $0.00) | Banked TP1: +$31.25"
                )
                log_trade_to_google_sheet(trade, entry, +31.25, "TP1_BANKED_BE_EXIT", "Runner secured at entry", "NEUTRAL")
                conf['active_trade'] = None
                return

    # 2. 15M CANDLE CLOSE EXECUTION
    if candle_time_15m != conf['last_candle_time']:
        conf['last_candle_time'] = candle_time_15m

        c_open_15m = float(last_closed_15m['open'])
        c_high_15m = float(last_closed_15m['high'])
        c_low_15m = float(last_closed_15m['low'])
        c_close_15m = float(last_closed_15m['close'])

        p_open_15m = float(prev_closed_15m['open'])
        p_high_15m = float(prev_closed_15m['high'])
        p_low_15m = float(prev_closed_15m['low'])
        p_close_15m = float(prev_closed_15m['close'])

        if risk_guard['is_frozen_today'] or conf['active_trade'] is not None:
            return

        # FEATURE A: FOREX MONDAY GAP-FILL SPECIAL TRIGGER
        if conf['is_forex'] and weekday == 0 and 3 <= now_ist.hour <= 7:
            df_daily = get_candles(conf['symbol'], '1d', limit=5)
            if df_daily is not None and len(df_daily) >= 3:
                fri_close = float(df_daily.iloc[-2]['close'])
                gap_distance = c_open_15m - fri_close
                if abs(gap_distance) >= (15 * conf['pip_size']):
                    if gap_distance > 0 and c_close_15m < c_open_15m:
                        sl = max(c_high_15m, p_high_15m) + conf['sl_buffer']
                        conf['active_trade'] = create_master_split_trade(name, 'SELL', c_close_15m, sl, trade_label="MONDAY_FOREX_GAP_FILL")
                        return
                    elif gap_distance < 0 and c_close_15m > c_open_15m:
                        sl = min(c_low_15m, p_low_15m) - conf['sl_buffer']
                        conf['active_trade'] = create_master_split_trade(name, 'BUY', c_close_15m, sl, trade_label="MONDAY_FOREX_GAP_FILL")
                        return

        # FEATURE B: 4H HIGHS/LOWS SWEEPS (LIQUIDITY HUNTS)
        h4_highs = list(market_map.get(name, {}).get('h4_highs', []))
        h4_lows = list(market_map.get(name, {}).get('h4_lows', []))

        df_4h = get_candles(conf['symbol'], '4h', limit=10)
        if df_4h is not None and len(df_4h) >= 4:
            last_4h = df_4h.iloc[-2]
            curr_4h = df_4h.iloc[-1]
            h4_cur_high = max(float(last_4h['high']), float(curr_4h['high']))
            h4_cur_low = min(float(last_4h['low']), float(curr_4h['low']))

            # 4H High Sweep -> Sell Setup
            for h_lvl in h4_highs:
                swept_4h = (h4_cur_high >= h_lvl)
                m15_c1_green = (p_high_15m >= h_lvl and p_close_15m > p_open_15m)
                m15_c2_red = (c_close_15m < h_lvl and c_close_15m < c_open_15m)

                if swept_4h and m15_c1_green and m15_c2_red and conf['active_trade'] is None:
                    sl = max(c_high_15m, p_high_15m) + conf['sl_buffer']
                    send_telegram_msg(f"🎯 *[4H LIQUIDITY SWEEP CONFIRMED - SELL]*\n4H High {h_lvl} Swept + 15M Red Confirmation!")
                    conf['active_trade'] = create_master_split_trade(name, 'SELL', c_close_15m, sl, trade_label="4H_SWEEP_15M_REVERSAL")
                    market_map[name]['h4_highs'].remove(h_lvl)
                    save_json(MAP_FILE, market_map)
                    return

            # 4H Low Sweep -> Buy Setup
            for l_lvl in h4_lows:
                swept_4h = (h4_cur_low <= l_lvl)
                m15_c1_red = (p_low_15m <= l_lvl and p_close_15m < p_open_15m)
                m15_c2_green = (c_close_15m > l_lvl and c_close_15m > c_open_15m)

                if swept_4h and m15_c1_red and m15_c2_green and conf['active_trade'] is None:
                    sl = min(c_low_15m, p_low_15m) - conf['sl_buffer']
                    send_telegram_msg(f"🎯 *[4H LIQUIDITY SWEEP CONFIRMED - BUY]*\n4H Low {l_lvl} Swept + 15M Green Confirmation!")
                    conf['active_trade'] = create_master_split_trade(name, 'BUY', c_close_15m, sl, trade_label="4H_SWEEP_15M_REVERSAL")
                    market_map[name]['h4_lows'].remove(l_lvl)
                    save_json(MAP_FILE, market_map)
                    return

        # FEATURE C: DAILY / MONTHLY KEY-LEVELS AI INDEPENDENT EXECUTION
        key_levels = list(market_map.get(name, {}).get('key_levels', []))
        for kl in key_levels:
            kl_touched = (c_low_15m <= kl <= c_high_15m) or (p_low_15m <= kl <= p_high_15m)
            if kl_touched and conf['active_trade'] is None:
                delta_val, vol_ratio, vol_status = calculate_volume_delta_profile(df_15m)
                ai_verdict = ask_ai_to_evaluate_key_level_entry(name, kl, df_15m, delta_val, vol_ratio, vol_status)

                if ai_verdict:
                    if "DECISION: BUY" in ai_verdict.upper():
                        sl = min(c_low_15m, p_low_15m) - conf['sl_buffer']
                        send_telegram_msg(f"🧠 *[AI APPROVED BUY AT DAILY/MONTHLY KEY-LEVEL {kl}]*\n{ai_verdict.strip()}")
                        conf['active_trade'] = create_master_split_trade(name, 'BUY', c_close_15m, sl, trade_label=f"KEY_LEVEL_AI_BUY ({kl})")
                        return
                    elif "DECISION: SELL" in ai_verdict.upper():
                        sl = max(c_high_15m, p_high_15m) + conf['sl_buffer']
                        send_telegram_msg(f"🧠 *[AI APPROVED SELL AT DAILY/MONTHLY KEY-LEVEL {kl}]*\n{ai_verdict.strip()}")
                        conf['active_trade'] = create_master_split_trade(name, 'SELL', c_close_15m, sl, trade_label=f"KEY_LEVEL_AI_SELL ({kl})")
                        return

# =====================================================================
# 9. TELEGRAM COMMAND & PHOTO LISTENER (WITH FULL AUDIT REPORT)
# =====================================================================
def handle_vet_trade(parts):
    if len(parts) < 6:
        send_telegram_msg(
            "ℹ️ *Format:* `/vet_trade <PAIR> <BUY/SELL> <ENTRY> <SL> <TP>`\n"
            "Example: `/vet_trade GOLD BUY 4192.00 4185.65 4211.98`"
        )
        return

    asset = parts[1].upper().replace("/", "")
    side = parts[2].upper()
    try:
        entry = float(parts[3])
        sl = float(parts[4])
        tp = float(parts[5])
    except ValueError:
        send_telegram_msg("❌ *Error:* Entry, SL, aur TP numbers me hone chahiye!")
        return

    if asset not in ASSETS:
        send_telegram_msg(f"❌ *Unknown Asset:* `{asset}`. Available: {list(ASSETS.keys())}")
        return

    conf = ASSETS[asset]
    if conf['active_trade'] is not None:
        send_telegram_msg(f"⚠️️ *[{conf['tag']}]* Pehle se ek active trade chal raha hai!")
        return

    send_telegram_msg(f"🔍 *[AI VETTING IN PROGRESS - {conf['tag']}]*\nGemini 3.8-Flash live volume delta aur Google Sheet history analyze kar raha hai...")
    df_15m = get_candles(conf['symbol'], '15m', limit=35)
    delta_val, vol_ratio, vol_status = calculate_volume_delta_profile(df_15m)

    sheet_memory = fetch_past_trades_memory_from_sheet()
    rr_ratio = round(abs(tp - entry) / abs(entry - sl), 2) if abs(entry - sl) > 0 else 0
    prompt = f"""
    You are an institutional risk & quantitative execution engine for a $2,500 The5ers prop account.
    A trader submitted a discretionary setup:
    - Asset: {conf['tag']} | Side: {side}
    - Entry: {entry} | SL: {sl} | TP: {tp} | RR: 1:{rr_ratio}
    - Volume Delta: {delta_val} | Delta Sentiment: {vol_status}
    - Macro Key-Levels: {market_map.get(asset, {}).get('key_levels', [])}
    
    PAST GOOGLE SHEET HISTORY:
    {sheet_memory}

    Evaluate with utmost risk caution:
    Output format strictly:
    DECISION: [APPROVED or REJECTED]
    CONFIDENCE: [Score from 1 to 10]
    REASONING: [Concise 2-bullet analysis in Hindi/Hinglish, under 60 words]
    """
    ai_verdict = call_gemini(prompt)
    if not ai_verdict:
        send_telegram_msg("⚠️ *AI Service Error:* Analysis nahi ho paya.")
        return

    is_approved = "DECISION: APPROVED" in ai_verdict.upper()

    if is_approved:
        send_telegram_msg(f"✅ *[AI VETTING: APPROVED & EXECUTED]* 🎯\n{ai_verdict.strip()}\n-----------------------------")
        conf['active_trade'] = create_master_split_trade(asset, side, entry, sl, custom_tp=tp, trade_label="USER_CHART_VISION_DISCRETIONARY")
    else:
        send_telegram_msg(f"❌ *[AI VETTING: REJECTED]* 🛡️\n{ai_verdict.strip()}\n🚫 *Action:* Risk control ke teht trade nahi liya gaya.")

def listen_telegram_commands_master():
    global market_map
    last_update_id = 0
    print("Master Unified Telegram Command & Vision Listener Active...", flush=True)

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=30"
            resp = requests.get(url, timeout=35).json()

            if "result" in resp:
                for item in resp["result"]:
                    last_update_id = item["update_id"]
                    msg = item.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    if chat_id != str(TELEGRAM_CHAT_ID):
                        continue

                    # 1. PHOTO HANDLER (DIRECT CHART SCREENSHOT)
                    if "photo" in msg:
                        photos = msg.get("photo", [])
                        best_photo = photos[-1]
                        caption = msg.get("caption", "").strip()
                        analyze_user_chart_screenshot(best_photo["file_id"], user_caption=caption)
                        continue

                    # 2. TEXT COMMAND HANDLER
                    text = msg.get("text", "").strip()
                    if not text.startswith("/"):
                        continue

                    parts = text.split()
                    cmd = parts[0].lower()

                    if cmd == "/levels":
                        crypto_c, forex_c, total_c = get_slot_counts()
                        msg_out = (
                            "📊 *[MASTER MULTI-TIER MARKET RADAR]*\n"
                            f"💼 *Active Trades:* {total_c}/{MAX_TOTAL_SLOTS} (Crypto: {crypto_c}/{MAX_CRYPTO_SLOTS} | Forex: {forex_c}/{MAX_FOREX_SLOTS})\n"
                            f"💰 *Today Net PnL:* {performance['today_pnl_usd']:+.2f} USD (Max Loss Cap: -$100.00)\n"
                            "-----------------------------\n"
                        )
                        for asset, data in market_map.items():
                            k_count = len(data.get('key_levels', []))
                            h_str = ", ".join([str(x) for x in data.get('h4_highs', [])]) or "None"
                            l_str = ", ".join([str(x) for x in data.get('h4_lows', [])]) or "None"
                            msg_out += f"*{asset}:*\n  🛡️ *Key-Levels (Daily/Monthly):* {k_count} levels active\n  🔺 *4H Highs (Sweep Targets):* `{h_str}`\n  🔻 *4H Lows (Sweep Targets):* `{l_str}`\n"
                        msg_out += (
                            "-----------------------------\n"
                            "ℹ️ *Commands:*\n"
                            "`/report` (Monthly/6-Month Google Sheet Live Audit)\n"
                            "`/set_key_levels BTC 85686, 87701`\n"
                            "`/set_4h_highs BTC 87345` | `/set_4h_lows BTC 82679`\n"
                            "`/reset_levels` (Hardcoded original radars par wapas set karein)\n"
                            "`/clear BTC` | `/vet_trade ...` | `/ask_ai ...`"
                        )
                        send_telegram_msg(msg_out)

                    # हार्डकोडेड ओरिजिनल लेवल्स रीस्टोर कमांड
                    elif cmd == "/reset_levels":
                        market_map = dict(DEFAULT_MARKET_MAP)
                        save_json(MAP_FILE, market_map)
                        send_telegram_msg("✅ *[RADAR RESET COMPLETED]*\nसभी 6 एसेट्स के ओरिजिनल हार्डकोडेड लेवल्स रीस्टोर हो गए!")

                    # GOOGLE SHEET से 6-महीने का लाइव ऑडिट
                    elif cmd == "/report":
                        sheet_memory = fetch_past_trades_memory_from_sheet()
                        if "No past" in sheet_memory:
                            send_telegram_msg("ℹ️ *[GOOGLE SHEET STATUS]*\nअभी कोई ट्रेड दर्ज नहीं है या GOOGLE_SHEET_URL कॉन्फ़िगर नहीं है।")
                        else:
                            try:
                                trades = json.loads(sheet_memory)
                                total_t = len(trades)
                                wins = sum(1 for t in trades if float(t.get("शुद्ध PnL ($)", 0)) > 0)
                                losses = sum(1 for t in trades if float(t.get("शुद्ध PnL ($)", 0)) < 0)
                                net_pnl = sum(float(t.get("शुद्ध PnL ($)", 0)) for t in trades)
                                overall_roi = (net_pnl / ACCOUNT_SIZE_USD) * 100
                                win_rate = (wins / total_t * 100) if total_t > 0 else 0

                                h4_trades = [t for t in trades if "4H_SWEEP" in str(t.get("रणनीति का प्रकार", ""))]
                                gap_trades = [t for t in trades if "GAP_FILL" in str(t.get("रणनीति का प्रकार", ""))]
                                user_trades = [t for t in trades if "USER_CHART" in str(t.get("रणनीति का प्रकार", ""))]

                                report_msg = (
                                    "📈 *[GOOGLE SHEET 6-MONTH INSTITUTIONAL AUDIT]*\n"
                                    "-----------------------------------\n"
                                    f"🔢 *Total Sheet Trades:* {total_t}\n"
                                    f"✅ *Wins:* {wins} | ❌ *Losses:* {losses}\n"
                                    f"🎯 *Win Rate:* {win_rate:.1f}%\n"
                                    f"💵 *Cumulative Net PnL:* *{net_pnl:+.2f} USD*\n"
                                    f"🚀 *Account Overall ROI:* *{overall_roi:+.2f}%*\n"
                                    "-----------------------------------\n"
                                    "📊 *STRATEGY BREAKDOWN:*\n"
                                    f"• *4H Liquidity Sweeps:* {len(h4_trades)} trades (Net: {sum(float(t.get('शुद्ध PnL ($)', 0)) for t in h4_trades):+.2f} USD)\n"
                                    f"• *Monday Gap-Fills:* {len(gap_trades)} trades (Net: {sum(float(t.get('शुद्ध PnL ($)', 0)) for t in gap_trades):+.2f} USD)\n"
                                    f"• *User Vision Chart Setups:* {len(user_trades)} trades (Net: {sum(float(t.get('शुद्ध PnL ($)', 0)) for t in user_trades):+.2f} USD)\n"
                                    "-----------------------------------\n"
                                    "🛡️ *डेटाबेस स्थिति:* Google Cloud Sheet में 100% परमानेंट लॉक।"
                                )
                                send_telegram_msg(report_msg)
                            except Exception as e:
                                send_telegram_msg(f"⚠️️ रिपोर्ट विश्लेषण एरर: {e}")

                    elif cmd == "/set_key_levels":
                        if len(parts) >= 3:
                            asset = parts[1].upper().replace("/", "")
                            if asset in market_map:
                                raw_vals = "".join(parts[2:]).split(",")
                                vals = [float(v.strip()) for v in raw_vals if v.strip()]
                                market_map[asset]['key_levels'] = sorted(vals)
                                save_json(MAP_FILE, market_map)
                                send_telegram_msg(f"✅ *[{asset} KEY-LEVELS (D/M) SET]*\nActive: `{market_map[asset]['key_levels']}`")

                    elif cmd == "/set_4h_highs":
                        if len(parts) >= 3:
                            asset = parts[1].upper().replace("/", "")
                            if asset in market_map:
                                raw_vals = "".join(parts[2:]).split(",")
                                vals = [float(v.strip()) for v in raw_vals if v.strip()]
                                market_map[asset]['h4_highs'] = sorted(vals)
                                save_json(MAP_FILE, market_map)
                                send_telegram_msg(f"✅ *[{asset} 4H HIGHS SET]*\nActive: `{market_map[asset]['h4_highs']}`")

                    elif cmd == "/set_4h_lows":
                        if len(parts) >= 3:
                            asset = parts[1].upper().replace("/", "")
                            if asset in market_map:
                                raw_vals = "".join(parts[2:]).split(",")
                                vals = [float(v.strip()) for v in raw_vals if v.strip()]
                                market_map[asset]['h4_lows'] = sorted(vals)
                                save_json(MAP_FILE, market_map)
                                send_telegram_msg(f"✅ *[{asset} 4H LOWS SET]*\nActive: `{market_map[asset]['h4_lows']}`")

                    elif cmd == "/clear":
                        if len(parts) >= 2:
                            asset = parts[1].upper().replace("/", "")
                            if asset in market_map:
                                market_map[asset] = {'key_levels': [], 'h4_highs': [], 'h4_lows': []}
                                save_json(MAP_FILE, market_map)
                                send_telegram_msg(f"🧹 *[{asset} RADAR COMPLETELY CLEARED]*")

                    elif cmd == "/ask_ai":
                        user_question = " ".join(parts[1:])
                        if user_question:
                            sheet_memory = fetch_past_trades_memory_from_sheet()
                            prompt = f"""
                            You are an expert algorithmic trading partner and mentor for Suraj Nadankar's $2,500 The5ers prop account.
                            Strategy: Daily/Monthly Key-Level Shields + 4H Liquidity Sweeps + 15M 2-Candle Confirmation + Volume Delta.
                            Recent Sheet Performance:
                            {sheet_memory}
                            
                            The user Suraj asks you: "{user_question}"
                            Respond in polite, direct Hindi/Hinglish (under 90 words), acknowledging their setup and giving clear risk advice.
                            """
                            ai_reply = call_gemini(prompt)
                            if ai_reply:
                                send_telegram_msg(f"🤖 *[GEMINI AI MENTOR]*\n{ai_reply.strip()}")
                        else:
                            send_telegram_msg("ℹ️️ Sawal puchne ke liye aise likhein: `/ask_ai GOLD me retracement par entry safe hai kya?`")

        except Exception as e:
            print(f"Telegram listener error: {e}", flush=True)
            time.sleep(5)
        time.sleep(1)

# =====================================================================
# 10. DAILY SUMMARY & ZERO-TRADE AI AUDIT (11:30 PM IST)
# =====================================================================
def check_and_send_daily_summary():
    global performance, risk_guard
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today_str = now_ist.strftime("%Y-%m-%d")

    if performance['today_date'] != today_str:
        performance['today_date'] = today_str
        performance['today_trades'] = 0
        performance['today_wins'] = 0
        performance['today_losses'] = 0
        performance['today_pnl_usd'] = 0.0
        performance['daily_summary_sent'] = False
        risk_guard['current_date'] = today_str
        risk_guard['is_frozen_today'] = False
        save_json(PERF_FILE, performance)

    if now_ist.hour == 23 and now_ist.minute >= 30 and not performance['daily_summary_sent']:
        win_rate = (performance['today_wins'] / performance['today_trades'] * 100) if performance['today_trades'] > 0 else 0.0
        summary_msg = (
            f"📋 *[GLOBAL MASTER END OF DAY REPORT]*\n"
            f"📅 *Date:* {today_str}\n"
            f"-----------------------------------\n"
            f"🔢 *Total Trades Today:* {performance['today_trades']}\n"
            f"✅ *Wins:* {performance['today_wins']} | ❌ *Losses:* {performance['today_losses']}\n"
            f"🎯 *Today Win Rate:* {win_rate:.1f}%\n"
            f"💰 *Today Net Realized PnL:* *{performance['today_pnl_usd']:+.2f} USD* (Max Loss Cap: -$100.00)\n"
            f"🏛️ *All-Time Evaluation Score:* *{performance['alltime_pnl_usd']:+.2f} USD*\n"
            "-----------------------------------\n"
            "ℹ️ *Tip:* Send `/report` to view full Google Sheet historical ROI audit."
        )
        send_telegram_msg(summary_msg)
        
        if performance['today_trades'] == 0:
            run_zero_trade_ai_audit()
            
        performance['daily_summary_sent'] = True
        save_json(PERF_FILE, performance)

def run_trading_bot():
    print("Master Algo Sniper Online with Hardcoded Radars...", flush=True)
    send_telegram_msg(
        "🚀 *Master Algo Institutional 3-Tier Sniper Online!* 🧠📊🎯\n"
        "• All 6 Asset Radars (Daily/Monthly + 4H Highs/Lows) are permanently hardcoded!\n"
        "• Daily/Monthly Key-Levels AI Execution Active (Gemini autonomously trades bounces/rejections)!\n"
        "• 4H Liquidity Sweeps + Monday Forex Gap-Fill + User Chart Vision Active!\n"
        "• Google Sheet Live Permanent Memory Active!\n"
        "• Send `/levels` or `/report` anytime!"
    )

    while True:
        try:
            check_and_send_daily_summary()
            for asset_name in ASSETS.keys():
                process_single_asset(asset_name)
                time.sleep(1.5)
            time.sleep(25)
        except Exception as e:
            print(f"Master loop error: {e}", flush=True)
            time.sleep(15)

if __name__ == '__main__':
    t_web = threading.Thread(target=start_web_server)
    t_web.daemon = True
    t_web.start()

    t_tg = threading.Thread(target=listen_telegram_commands_master)
    t_tg.daemon = True
    t_tg.start()

    run_trading_bot()
