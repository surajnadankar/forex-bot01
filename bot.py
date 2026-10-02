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
    return "Master Algo Sniper with Gemini Vision Chart Analyst Active 24/7!"

def start_web_server():
    app.run(host='0.0.0.0', port=10000)

# =====================================================================
# 3. GLOBAL RISK ENGINE & ACCOUNT LIMITS ($2500 THE5ERS ACCOUNT)
# =====================================================================
exchange = ccxt.kraken({
    'enableRateLimit': True,
    'rateLimit': 2500
})
api_lock = threading.Lock()

ACCOUNT_SIZE_USD = 2500.0
RISK_PER_TRADE_USD = 25.0       # 1.0% Risk ($25.00)
MAX_DAILY_LOSS_USD = 100.0      # Strict -$100.00 Net Daily Circuit Breaker (Max 4 losses)

MAX_CRYPTO_SLOTS = 2
MAX_FOREX_SLOTS = 2
MAX_TOTAL_SLOTS = 4

PERF_FILE = "master_performance.json"
LEVELS_FILE = "master_levels.json"

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
            json.dump(data, f)
    except Exception as e:
        print(f"Save error ({filepath}): {e}", flush=True)

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

user_levels = load_json(LEVELS_FILE, {
    'BTC': {'highs': [87130.0], 'lows': [82900.0, 80150.0, 75560.0]},
    'GOLD': {'highs': [], 'lows': []},
    'EURUSD': {'highs': [], 'lows': []},
    'GBPUSD': {'highs': [], 'lows': []},
    'USDJPY': {'highs': [], 'lows': []},
    'USDCAD': {'highs': [], 'lows': []}
})

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
# 5. MARKET DATA & VOLUME DELTA ENGINE
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
            return 0.0, 1.0, "NORMAL"
        last = df.iloc[-2]
        c_open = float(last['open'])
        c_close = float(last['close'])
        c_high = float(last['high'])
        c_low = float(last['low'])
        c_vol = float(last['volume'])

        total_range = c_high - c_low
        if total_range <= 0:
            return 0.0, 1.0, "FLAT"

        buy_weight = (c_close - c_low) / total_range
        sell_weight = (c_high - c_close) / total_range
        net_delta = c_vol * (buy_weight - sell_weight)

        avg_vol = df['volume'].iloc[-12:-2].mean()
        vol_ratio = round(c_vol / avg_vol, 2) if avg_vol > 0 else 1.0

        if vol_ratio >= 1.8:
            vol_status = "HEAVY ABSORPTION / CLIMAX"
        elif vol_ratio >= 1.2:
            vol_status = "ABOVE AVERAGE EXPANSION"
        else:
            vol_status = "NORMAL / LOW LIQUIDITY"

        return round(net_delta, 2), vol_ratio, vol_status
    except Exception as e:
        print(f"Delta calculation error: {e}", flush=True)
        return 0.0, 1.0, "NORMAL"

# =====================================================================
# 6. GEMINI MULTIMODAL VISION & POST-TRADE AI ENGINE
# =====================================================================
def call_gemini(prompt_text, image_b64=None):
    if not GEMINI_API_KEY:
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key={GEMINI_API_KEY}"
    
     headers = {"Content-Type": "application/json"}
        parts = []
        if image_b64:
            parts.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": image_b64
                }
            })
        parts.append({"text": prompt_text})
        payload = {"contents": [{"parts": parts}]}
        res = requests.post(url, headers=headers, json=payload, timeout=35)
        if res.status_code == 200:
            data = res.json()
            return data['candidates'][0]['content']['parts'][0]['text']
        else:
            print(f"Gemini API error: {res.status_code} - {res.text}", flush=True)
            return None
    except Exception as e:
        print(f"Gemini connection error: {e}", flush=True)
        return None

def run_ai_trade_advisor(outcome, trade, current_price, delta_val, vol_ratio, vol_status):
    asset = trade['asset']
    side = trade['side']
    conf = ASSETS[asset]

    prompt = f"""
    You are an expert algorithmic prop trading coach for a $2,500 The5ers account.
    A trade just closed:
    - Asset: {conf['tag']}
    - Side: {side} | Entry: {trade['entry']} | Exit: {current_price}
    - Outcome: {outcome}
    - Volume Delta: {delta_val}
    - Volume Ratio: {vol_ratio}x ({vol_status})
    
    Provide 3 concise bullet points in clean Hindi/Hinglish:
    1. Volume Delta aur absorption ke context me kya entry sahi thi?
    2. Trade ka mukhya kaaran (Liquidity grab/sweep context).
    3. Ek practical lesson/tip taaki agla trade aur behtar ho sake.
    Keep it strictly under 75 words.
    """
    ai_resp = call_gemini(prompt)
    if ai_resp:
        send_telegram_msg(f"🧠 *[GEMINI AI RETROSPECTIVE ADVICE - {conf['tag']}]*\n{ai_resp.strip()}")

def run_zero_trade_ai_audit():
    market_notes = []
    for asset, conf in ASSETS.items():
        highs = user_levels.get(asset, {}).get('highs', [])
        lows = user_levels.get(asset, {}).get('lows', [])
        market_notes.append(f"{asset}: Target Highs={highs}, Target Lows={lows}")

    prompt = f"""
    You are a professional prop firm risk analyst evaluating today's session for a $2,500 account.
    Total trades taken today: 0 (No trade executed).
    Active Radar Levels tracked today:
    {market_notes}

    Analyze the day in clean Hindi/Hinglish (3 bullet points, under 85 words):
    1. Aaj trades na milne ka mukhya kaaran (Kya market rangebound/chop tha ya price levels se door raha)?
    2. Missing Opportunity check: Kya koi standard 15M sweep bana jahan level thoda peeche tha?
    3. Kal ke session ke liye trader ke liye 1-2 practical tips (Levels kaise adjust karein).
    """
    ai_resp = call_gemini(prompt)
    if ai_resp:
        send_telegram_msg(f"🧠 *[GEMINI DAILY SESSION COACH & MARKET AUDIT]*\n{ai_resp.strip()}")

def analyze_user_chart_screenshot(file_id, user_caption=""):
    send_telegram_msg("🔍 *[GEMINI VISION ANALYZING YOUR CHART...]*\nचार्ट का स्ट्रक्चर, ट्रेंडलाइन्स, सपोर्ट/रेजिस्टेंस और दोतरफ़ा (BUY/SELL) ब्रेकआउट पाथवे स्कैन हो रहे हैं...")
    try:
        f_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
        f_res = requests.get(f_url, timeout=15).json()
        if not f_res.get("ok"):
            send_telegram_msg("❌ फ़ोटो डाउनलोड करने में समस्या आई, कृपया दोबारा भेजें।")
            return
        file_path = f_res["result"]["file_path"]

        dl_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        img_bytes = requests.get(dl_url, timeout=20).content
        img_b64 = base64.b64encode(img_bytes).decode('utf-8')

        prompt = f"""
        You are a quantitative institutional trader and technical chart mentor for a $2,500 The5ers prop account.
        User submitted an image of their TradingView chart. User notes/caption: "{user_caption}"

        Carefully analyze the chart image:
        1. Identify the asset name (e.g. GOLD / XAUUSD, BTC, EURUSD, etc.) and timeframe shown.
        2. Identify the pattern structure (Ascending Channel, Bear/Bull Flag, Wedge, S/R Range).
        3. Formulate BOTH SCENARIOS with realistic price values directly read from the chart axes:
           - 🟢 SCENARIO 1 (BULLISH / BOUNCE or BREAKOUT):
             • Condition: (e.g. bounce from lower trendline/support)
             • Exact Entry, Stop Loss, Take Profit
             • 1% Risk ($25.00) calculated Lot size
           - 🔴 SCENARIO 2 (BEARISH / BREAKDOWN & RETEST):
             • Condition: (e.g. breakdown below lower trendline & failed retest)
             • Exact Entry, Stop Loss, Take Profit
             • 1% Risk ($25.00) calculated Lot size
        4. Give direct ready-to-copy commands for BOTH scenarios so user can easily execute whichever triggers!

        Format strictly in polite, clear Hindi/Hinglish with bold formatting. Keep analysis extremely structured and under 150 words.
        """
        analysis = call_gemini(prompt, image_b64=img_b64)
        if analysis:
            send_telegram_msg(f"📊 *[GEMINI AI CHART VISION REPORT]*\n\n{analysis.strip()}")
        else:
            send_telegram_msg("⚠️ AI चार्ट का विश्लेषण नहीं कर सका, कृपया स्पष्ट स्क्रीनशॉट भेजें।")

    except Exception as e:
        print(f"Chart vision handling error: {e}", flush=True)
        send_telegram_msg(f"⚠️ चार्ट स्कैनिंग एरर: {e}")

# =====================================================================
# 7. TRADE SIZING & EXECUTION (DISCRETIONARY & AUTO-SWEEP)
# =====================================================================
def create_master_split_trade(asset_key, side, entry, calculated_sl, custom_tp=None, trade_label="KEY-LEVEL SWEEP"):
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
# 8. ASSET MONITORING & EXECUTION LOOP
# =====================================================================
def process_single_asset(name):
    global performance, risk_guard, user_levels
    conf = ASSETS[name]

    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    weekday = now_ist.weekday()

    if not conf['trade_on_weekends'] and (weekday == 5 or (weekday == 6 and now_ist.hour < 23)):
        return

    df_15m = get_candles(conf['symbol'], '15m', limit=35)
    if df_15m is None or len(df_15m) < 10: return

    last_closed = df_15m.iloc[-2]
    prev_closed = df_15m.iloc[-3]
    current_price = float(df_15m.iloc[-1]['close'])
    candle_time = int(last_closed['timestamp'])

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
                run_ai_trade_advisor("LOSS", trade, current_price, delta_val, vol_ratio, vol_status)
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
                run_ai_trade_advisor("TP2", trade, current_price, delta_val, vol_ratio, vol_status)
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
                conf['active_trade'] = None
                return

    # 2. 15M CANDLE CLOSE EXECUTION (USER TARGET LEVELS ONLY)
    if candle_time != conf['last_candle_time']:
        conf['last_candle_time'] = candle_time
        c_open = float(last_closed['open'])
        c_high = float(last_closed['high'])
        c_low = float(last_closed['low'])
        c_close = float(last_closed['close'])

        p_low = float(prev_closed['low'])
        p_high = float(prev_closed['high'])

        if risk_guard['is_frozen_today'] or conf['active_trade'] is not None:
            return

        # A. FOREX MONDAY GAP-FILL SPECIAL TRIGGER
        if conf['is_forex'] and weekday == 0 and 3 <= now_ist.hour <= 7:
            df_daily = get_candles(conf['symbol'], '1d', limit=5)
            if df_daily is not None and len(df_daily) >= 3:
                fri_close = float(df_daily.iloc[-2]['close'])
                gap_distance = c_open - fri_close
                if abs(gap_distance) >= (15 * conf['pip_size']):
                    if gap_distance > 0 and c_close < c_open:
                        sl = max(c_high, p_high) + conf['sl_buffer']
                        conf['active_trade'] = create_master_split_trade(name, 'SELL', c_close, sl, trade_label="MONDAY GAP-FILL")
                        return
                    elif gap_distance < 0 and c_close > c_open:
                        sl = min(c_low, p_low) - conf['sl_buffer']
                        conf['active_trade'] = create_master_split_trade(name, 'BUY', c_close, sl, trade_label="MONDAY GAP-FILL")
                        return

        # B. USER TARGET LEVELS EXECUTION
        target_highs = list(user_levels.get(name, {}).get('highs', []))
        target_lows = list(user_levels.get(name, {}).get('lows', []))

        for t_low in target_lows:
            if c_low < t_low and c_close > t_low and c_close > c_open and conf['active_trade'] is None:
                sl = min(c_low, p_low) - conf['sl_buffer']
                conf['active_trade'] = create_master_split_trade(name, 'BUY', c_close, sl, trade_label=f"KEY-LOW SWEEP ({t_low})")
                user_levels[name]['lows'].remove(t_low)
                save_json(LEVELS_FILE, user_levels)
                send_telegram_msg(f"🎯 *[LEVEL MITIGATED]* {name} Key-Low {t_low} hit and removed from active radar!")
                return

        for t_high in target_highs:
            if c_high > t_high and c_close < t_high and c_close < c_open and conf['active_trade'] is None:
                sl = max(c_high, p_high) + conf['sl_buffer']
                conf['active_trade'] = create_master_split_trade(name, 'SELL', c_close, sl, trade_label=f"KEY-HIGH SWEEP ({t_high})")
                user_levels[name]['highs'].remove(t_high)
                save_json(LEVELS_FILE, user_levels)
                send_telegram_msg(f"🎯 *[LEVEL MITIGATED]* {name} Key-High {t_high} hit and removed from active radar!")
                return

# =====================================================================
# 9. UNIFIED TELEGRAM COMMAND & PHOTO LISTENER
# =====================================================================
def handle_vet_trade(parts):
    if len(parts) < 6:
        send_telegram_msg(
            "ℹ️ *Format:* `/vet_trade <PAIR> <BUY/SELL> <ENTRY> <SL> <TP>`"
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

    send_telegram_msg(f"🔍 *[AI VETTING IN PROGRESS - {conf['tag']}]*\nGemini 2.5 Flash live volume delta aur context analyze kar raha hai...")
    df_15m = get_candles(conf['symbol'], '15m', limit=35)
    delta_val, vol_ratio, vol_status = calculate_volume_delta_profile(df_15m)

    rr_ratio = round(abs(tp - entry) / abs(entry - sl), 2) if abs(entry - sl) > 0 else 0
    prompt = f"""
    You are an institutional risk & quantitative execution engine for a $2,500 The5ers prop account.
    A trader submitted a discretionary setup:
    - Asset: {conf['tag']}
    - Side: {side}
    - Entry: {entry} | SL: {sl} | TP: {tp} | RR: 1:{rr_ratio}
    - Market Volume Delta: {delta_val} | Volume Ratio: {vol_ratio}x ({vol_status})

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
        conf['active_trade'] = create_master_split_trade(asset, side, entry, sl, custom_tp=tp, trade_label="AI-VETTED ENTRY")
    else:
        send_telegram_msg(f"❌ *[AI VETTING: REJECTED]* 🛡️️\n{ai_verdict.strip()}\n🚫 *Action:* Risk control ke teht trade nahi liya gaya.")

def listen_telegram_commands_master():
    global user_levels
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

                    # 1. PHOTO HANDLER (DIRECT CHART SCREENSHOT RECOGNITION)
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
                            "📊 *[UNIFIED MASTER RADAR & KEY-LEVELS]*\n"
                            f"💼 *Active Trades:* {total_c}/{MAX_TOTAL_SLOTS} (Crypto: {crypto_c}/{MAX_CRYPTO_SLOTS} | Forex: {forex_c}/{MAX_FOREX_SLOTS})\n"
                            f"💰 *Today Net PnL:* {performance['today_pnl_usd']:+.2f} USD (Max Loss Cap: -$100.00)\n"
                            "-----------------------------\n"
                        )
                        for asset, data in user_levels.items():
                            h_str = ", ".join([f"{x}" for x in data.get('highs', [])]) or "None"
                            l_str = ", ".join([f"{x}" for x in data.get('lows', [])]) or "None"
                            msg_out += f"*{asset}:*\n  🔺 *Highs:* {h_str}\n  🔻 *Lows:* {l_str}\n"
                        msg_out += (
                            "-----------------------------\n📸 *Send ANY Chart Photo directly!* (Gemini Vision auto-scans)\n\n"
                            "ℹ️ *Commands:*\n"
                            "`/vet_trade GOLD BUY 4189.50 4183.95 4205.78`\n"
                            "`/set_highs BTC 87130`\n"
                            "`/set_lows BTC 82900, 80150, 75560`\n"
                            "`/add_high GOLD 2685` | `/clear USDCAD`\n"
                            "`/ask_ai <apka sawal>`"
                        )
                        send_telegram_msg(msg_out)

                    elif cmd == "/vet_trade":
                        handle_vet_trade(parts)

                    elif cmd in ["/set_highs", "/set_lows"]:
                        if len(parts) >= 3:
                            asset = parts[1].upper().replace("/", "")
                            if asset in user_levels:
                                raw_vals = "".join(parts[2:]).split(",")
                                vals = [float(v.strip()) for v in raw_vals if v.strip()]
                                key = "highs" if cmd == "/set_highs" else "lows"
                                user_levels[asset][key] = sorted(vals)
                                save_json(LEVELS_FILE, user_levels)
                                send_telegram_msg(f"✅ *[{asset} {key.upper()} UPDATED]*\nActive: {user_levels[asset][key]}")
                            else:
                                send_telegram_msg(f"❌ Unknown asset `{asset}`. Available: {list(user_levels.keys())}")

                    elif cmd in ["/add_high", "/add_low"]:
                        if len(parts) >= 3:
                            asset = parts[1].upper().replace("/", "")
                            if asset in user_levels:
                                val = float(parts[2].replace(",", "").strip())
                                key = "highs" if cmd == "/add_high" else "lows"
                                if val not in user_levels[asset][key]:
                                    user_levels[asset][key].append(val)
                                    user_levels[asset][key].sort()
                                    save_json(LEVELS_FILE, user_levels)
                                send_telegram_msg(f"✅ *[{asset} {key.upper()} ADDED]*\nLevel: {val}\nCurrent: {user_levels[asset][key]}")

                    elif cmd == "/clear":
                        if len(parts) >= 2:
                            asset = parts[1].upper().replace("/", "")
                            if asset in user_levels:
                                user_levels[asset]['highs'] = []
                                user_levels[asset]['lows'] = []
                                save_json(LEVELS_FILE, user_levels)
                                send_telegram_msg(f"🧹 *[{asset} RADAR CLEARED]*")

                    elif cmd == "/ask_ai":
                        user_question = " ".join(parts[1:])
                        if user_question:
                            prompt = f"""
                            You are a trading partner and mentor for a $2,500 The5ers prop account.
                            Strategy: 15M Key Level Sweeps, Volume Delta, Liquidity Grabs.
                            The user Suraj asks you: "{user_question}"
                            
                            Respond in polite, direct Hindi/Hinglish (under 90 words).
                            """
                            ai_reply = call_gemini(prompt)
                            if ai_reply:
                                send_telegram_msg(f"🤖 *[GEMINI AI MENTOR]*\n{ai_reply.strip()}")
                        else:
                            send_telegram_msg("ℹ️ Sawal puchne ke liye aise likhein: `/ask_ai BTC me entry lene ka sahi time kya tha?`")

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
            f"-----------------------------------"
        )
        send_telegram_msg(summary_msg)
        
        if performance['today_trades'] == 0:
            run_zero_trade_ai_audit()
            
        performance['daily_summary_sent'] = True
        save_json(PERF_FILE, performance)

def run_trading_bot():
    print("Master Algo Sniper with Gemini Vision Active...", flush=True)
    send_telegram_msg(
        "🚀 *Master Algo Unified Sniper + Gemini Vision AI Online!* 👁️🧠\n"
        "• Assets: BTC, GOLD, EURUSD, GBPUSD, USDJPY, USDCAD\n"
        "• Daily Circuit Breaker: -$100.00 Net (Max 4 Trades)\n"
        "• Slots: Max 4 Concurrent (2 Crypto/Gold + 2 Forex)\n"
        "• 📸 *Direct Chart Vision:* Just upload ANY chart photo directly to Telegram!\n"
        "• Send `/levels` anytime to check radar!"
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
