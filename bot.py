import ccxt
import time
import pandas as pd
import threading
import requests
import json
import os
from datetime import datetime, timezone, timedelta
from flask import Flask

# ----------------- 1. TELEGRAM CONFIG -----------------
TELEGRAM_BOT_TOKEN = "8895341894:AAEE-p0_Ylj6RFmqr06nx5xNT7vzyBaBTqI"
TELEGRAM_CHAT_ID = "998154896"

def send_telegram_msg(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

# ----------------- 2. WEB SERVER -----------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Forex Pure 4H V-Shape Sniper Active 24/5!"

def start_web_server():
    app.run(host='0.0.0.0', port=10000)

# ----------------- 3. EXCHANGE & RISK CONFIG -----------------
exchange = ccxt.kraken({
    'enableRateLimit': True,
    'rateLimit': 2500
})
api_lock = threading.Lock()

ACCOUNT_SIZE_USD = 2500.0
RISK_PER_TRADE_USD = 25.0       # 1% Risk ($25)
MAX_DAILY_LOSS_USD = 50.0       # $50 Max Net Realized Loss
MAX_CONCURRENT_TRADES = 2       # अधिकतम 2 ट्रेड्स एक साथ

DATA_FILE = "forex_performance.json"

def load_performance():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
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
    }

def save_performance(data):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"File save error: {e}", flush=True)

performance = load_performance()

risk_guard = {
    'current_date': "",
    'is_frozen_today': False
}

FOREX_ASSETS = {
    'EURUSD': {
        'symbol': 'EUR/USD',
        'tag': '💶 EUR/USD',
        'pip_size': 0.0001,
        'sl_buffer': 0.0006,      # 6 pips buffer
        'min_allowed_sl': 0.0015, # 15 pips safe SL floor
        'max_allowed_sl': 0.0035, # 35 pips max SL
        'min_v_depth': 0.0040,    # 40 pips 4H V-depth
        'proximity_limit': 0.0150,# 150 pips proximity
        'min_gap': 0.0012,        # 12 pips gap
        'active_trade': None,
        'last_candle_time': None,
        'v_4h_highs': [],
        'v_4h_lows': [],
        'cached_prev_close': None,
        'monday_open_price': None,
        'gap_trade_done': False,
        'last_4h_fetch': 0
    },
    'GBPUSD': {
        'symbol': 'GBP/USD',
        'tag': '💷 GBP/USD',
        'pip_size': 0.0001,
        'sl_buffer': 0.0007,      # 7 pips buffer
        'min_allowed_sl': 0.0018, # 18 pips safe SL floor
        'max_allowed_sl': 0.0040, # 40 pips max SL
        'min_v_depth': 0.0050,    # 50 pips 4H V-depth
        'proximity_limit': 0.0200,# 200 pips proximity
        'min_gap': 0.0015,        # 15 pips gap
        'active_trade': None,
        'last_candle_time': None,
        'v_4h_highs': [],
        'v_4h_lows': [],
        'cached_prev_close': None,
        'monday_open_price': None,
        'gap_trade_done': False,
        'last_4h_fetch': 0
    },
    'USDJPY': {
        'symbol': 'USD/JPY',
        'tag': '💴 USD/JPY',
        'pip_size': 0.01,
        'sl_buffer': 0.08,        # 8 pips buffer
        'min_allowed_sl': 0.25,   # 25 pips safe SL floor
        'max_allowed_sl': 0.55,   # 55 pips max SL
        'min_v_depth': 0.60,      # 60 pips 4H V-depth
        'proximity_limit': 2.50,  # 250 pips proximity
        'min_gap': 0.20,          # 20 pips gap
        'active_trade': None,
        'last_candle_time': None,
        'v_4h_highs': [],
        'v_4h_lows': [],
        'cached_prev_close': None,
        'monday_open_price': None,
        'gap_trade_done': False,
        'last_4h_fetch': 0
    },
    'USDCAD': {
        'symbol': 'USD/CAD',
        'tag': '🍁 USD/CAD',
        'pip_size': 0.0001,
        'sl_buffer': 0.0006,      # 6 pips buffer
        'min_allowed_sl': 0.0015, # 15 pips safe SL floor
        'max_allowed_sl': 0.0035, # 35 pips max SL
        'min_v_depth': 0.0040,    # 40 pips 4H V-depth
        'proximity_limit': 0.0150,# 150 pips proximity
        'min_gap': 0.0012,        # 12 pips gap
        'active_trade': None,
        'last_candle_time': None,
        'v_4h_highs': [],
        'v_4h_lows': [],
        'cached_prev_close': None,
        'monday_open_price': None,
        'gap_trade_done': False,
        'last_4h_fetch': 0
    }
}

def get_current_open_trades_count():
    count = 0
    for c in FOREX_ASSETS.values():
        if c['active_trade'] is not None: count += 1
    return count

def check_net_circuit_breaker():
    global performance, risk_guard
    if performance['today_pnl_usd'] <= -MAX_DAILY_LOSS_USD:
        risk_guard['is_frozen_today'] = True
        send_telegram_msg(
            "🚨🚨 *[FOREX CIRCUIT BREAKER ACTIVATED]* 🚨🚨\n"
            f"आज का शुद्ध दैनिक नुकसान (-${abs(performance['today_pnl_usd']):.2f}) -$50.00 तक पहुँच गया!\n"
            "🛡️ *ACCOUNT SAFEGUARD:* बॉट आज रात 12:00 AM तक पूरी तरह FREEZE रहेगा।"
        )

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
        for c in FOREX_ASSETS.values():
            c['gap_trade_done'] = False
        save_performance(performance)

    # रात 11:30 PM IST के बाद दैनिक रिपोर्ट
    if now_ist.hour == 23 and now_ist.minute >= 30 and not performance['daily_summary_sent']:
        win_rate = (performance['today_wins'] / performance['today_trades'] * 100) if performance['today_trades'] > 0 else 0.0
        all_win_rate = (performance['alltime_wins'] / performance['alltime_trades'] * 100) if performance['alltime_trades'] > 0 else 0.0
        status_emoji = "🔥" if performance['today_pnl_usd'] > 0 else ("😐" if performance['today_pnl_usd'] == 0 else "🔻")

        summary_msg = (
            f"📋 *[FOREX DAILY PERFORMANCE REPORT]* {status_emoji}\n"
            f"📅 *Date:* {today_str}\n"
            f"-----------------------------------\n"
            f"🔢 *Total Forex Trades Today:* {performance['today_trades']}\n"
            f"✅ *Wins:* {performance['today_wins']} | ❌ *Losses:* {performance['today_losses']}\n"
            f"🎯 *Today Win Rate:* {win_rate:.1f}%\n"
            f"💰 *Today Net Realized PnL:* *{performance['today_pnl_usd']:+.2f} USD*\n"
            f"-----------------------------------\n"
            f"🏛️ *THE5ERS $2,500 EVALUATION TRACKER:*\n"
            f"• All-Time Trades: {performance['alltime_trades']}\n"
            f"• Cumulative Win Rate: {all_win_rate:.1f}%\n"
            f"• Total Evaluation PnL: *{performance['alltime_pnl_usd']:+.2f} USD*\n"
            f"• Circuit Status: {'🔴 FROZEN' if risk_guard['is_frozen_today'] else '🟢 ACTIVE'}\n"
            f"-----------------------------------"
        )
        send_telegram_msg(summary_msg)
        performance['daily_summary_sent'] = True
        save_performance(performance)

def get_candles(symbol, timeframe, limit=80):
    with api_lock:
        try:
            time.sleep(1.2)
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as e:
            print(f"Kraken fetch error ({symbol} - {timeframe}): {e}", flush=True)
            return None

def extract_true_4h_v_pivots(df_4h, min_depth, current_price, proximity_limit):
    try:
        if df_4h is None or len(df_4h) < 25:
            return [], []

        lows = [float(x) for x in df_4h['low'].tolist()]
        highs = [float(x) for x in df_4h['high'].tolist()]
        n = len(lows)

        v_bottoms = []
        v_tops = []

        for i in range(3, n - 3):
            cur_low = lows[i]
            left_drop = max(highs[i-3:i]) - cur_low
            right_rally = max(highs[i+1:i+4]) - cur_low

            if left_drop >= min_depth and right_rally >= min_depth:
                if cur_low == min(lows[i-3:i+4]):
                    if abs(current_price - cur_low) <= proximity_limit:
                        v_bottoms.append(cur_low)

            cur_high = highs[i]
            left_rally = cur_high - min(lows[i-3:i])
            right_drop = cur_high - min(lows[i+1:i+4])

            if left_rally >= min_depth and right_drop >= min_depth:
                if cur_high == max(highs[i-3:i+4]):
                    if abs(current_price - cur_high) <= proximity_limit:
                        v_tops.append(cur_high)

        return list(set(v_tops)), list(set(v_bottoms))
    except Exception as e:
        print(f"Forex 4H V-Pivot calc error: {e}", flush=True)
        return [], []

def update_4h_v_levels(name, current_price):
    conf = FOREX_ASSETS[name]
    now_ts = time.time()
    if (now_ts - conf['last_4h_fetch']) > 1800 or len(conf['v_4h_lows']) == 0:
        # 200 कैंडल्स = 33 दिन का इतिहास
        df_4h = get_candles(conf['symbol'], '4h', limit=200)
        v_tops, v_bottoms = extract_true_4h_v_pivots(df_4h, conf['min_v_depth'], current_price, conf['proximity_limit'])
        conf['v_4h_highs'] = v_tops
        conf['v_4h_lows'] = v_bottoms
        conf['last_4h_fetch'] = now_ts
        print(f"[{name} 4H V-RADAR (33 DAYS)] Active Lows: {len(conf['v_4h_lows'])} | Active Highs: {len(conf['v_4h_highs'])}", flush=True)

def verify_volume_and_delta(df_15m, side):
    try:
        last_c = df_15m.iloc[-2]
        vol_window = df_15m['volume'].iloc[-12:-2]
        avg_vol = vol_window.mean() if len(vol_window) > 0 else 1.0
        c_vol = float(last_c['volume'])

        # 1. Volume Spike Check (1.4x)
        if c_vol < (avg_vol * 1.4):
            return False, 0.0

        c_range = float(last_c['high']) - float(last_c['low'])
        if c_range <= 0:
            return False, 0.0

        delta_ratio = (float(last_c['close']) - float(last_c['low'])) / c_range

        if side == 'BUY':
            is_valid = (delta_ratio >= 0.55) and (float(last_c['close']) > float(last_c['open']))
            return is_valid, delta_ratio
        else:
            is_valid = (delta_ratio <= 0.45) and (float(last_c['close']) < float(last_c['open']))
            return is_valid, delta_ratio
    except Exception as e:
        print(f"Forex Volume-delta check error: {e}", flush=True)
        return False, 0.0

def create_forex_split_trade(side, entry, calculated_sl, conf, trade_label="4H V-LIQUIDITY SWEEP", delta_val=0.0):
    if get_current_open_trades_count() >= MAX_CONCURRENT_TRADES:
        return None

    raw_distance = abs(entry - calculated_sl)
    if raw_distance <= 0: return None

    effective_distance = max(raw_distance, conf['min_allowed_sl'])

    if side == 'BUY': actual_sl = entry - effective_distance
    else: actual_sl = entry + effective_distance

    if effective_distance > conf['max_allowed_sl']:
        send_telegram_msg(
            f"⚠️ *[{conf['tag']} TRADE SKIPPED - SL TOO LARGE]*\n"
            f"SL Distance: {effective_distance / conf['pip_size']:.1f} Pips (Max: {conf['max_allowed_sl'] / conf['pip_size']:.1f} Pips)"
        )
        return None

    pips = effective_distance / conf['pip_size']
    calculated_lots = round(RISK_PER_TRADE_USD / (pips * 10.0), 2)
    if calculated_lots < 0.02: calculated_lots = 0.02

    lot1 = round(calculated_lots / 2, 2)
    lot2 = round(calculated_lots - lot1, 2)

    if "GAP-FILL" in trade_label and conf['cached_prev_close'] is not None:
        tp1 = conf['cached_prev_close']
        tp2 = entry + (effective_distance * 5.0) if side == 'BUY' else entry - (effective_distance * 5.0)
    else:
        if side == 'BUY':
            tp1 = entry + (effective_distance * 2.5)
            tp2 = entry + (effective_distance * 5.0)
        else:
            tp1 = entry - (effective_distance * 2.5)
            tp2 = entry - (effective_distance * 5.0)

    trade = {
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
        'trade_label': trade_label,
        'pips_risk': pips
    }

    send_telegram_msg(
        f"🚀 *[{conf['tag']} {trade_label} ENTRY (1% RISK)]*\n"
        f"-----------------------------\n"
        f"📈 *Direction:* {side}\n"
        f"💵 *Entry:* {entry:.5f} | *Safe SL:* {actual_sl:.5f} ({pips:.1f} Pips)\n"
        f"📊 *Institutional Order-Flow:* Volume Spike (1.4x+) Confirmed\n"
        f"🔋 *Delta Ratio:* {delta_val:.2f} (Absorption Validated)\n"
        f"💼 *Total Lots ($2500 Acct):* {calculated_lots} Lots\n"
        f"   • *Lot 1:* {lot1} Lots (TP1 Partial + BE)\n"
        f"   • *Lot 2:* {lot2} Lots (TP2 Runner Target)\n"
        f"🎯 *TP1 Target:* {tp1:.5f}\n"
        f"🏆 *TP2 Target:* {tp2:.5f}\n"
        f"🔒 *Max Risk:* $25.00 (1.0%)\n"
        f"📊 *Open Trades:* {get_current_open_trades_count() + 1}/{MAX_CONCURRENT_TRADES}\n"
        f"-----------------------------"
    )
    return trade

def process_forex_asset(name):
    global performance, risk_guard
    conf = FOREX_ASSETS[name]

    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    weekday = now_ist.weekday()
    if weekday == 5 or (weekday == 6 and now_ist.hour < 23):
        return

    df_15m = get_candles(conf['symbol'], '15m', limit=35)
    if df_15m is None or len(df_15m) < 10:
        return

    last_closed = df_15m.iloc[-2]
    prev_closed = df_15m.iloc[-3]
    current_price = float(df_15m.iloc[-1]['close'])
    candle_time = int(last_closed['timestamp'])

    update_4h_v_levels(name, current_price)

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
                save_performance(performance)
                check_net_circuit_breaker()

                send_telegram_msg(
                    f"🛑 *[{conf['tag']} FULL STOP LOSS HIT]* ❌\n"
                    f"Exit: {current_price:.5f} | Entry: {entry:.5f}\n"
                    f"Loss: -25.00 USD (-1.0%)\n"
                    f"Today Net Realized PnL: {performance['today_pnl_usd']:+.2f} USD"
                )
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
                save_performance(performance)

                send_telegram_msg(
                    f"💰 *[{conf['tag']} TP1 HIT - 50% PARTIAL BOOKED]* 🎯\n"
                    f"✅ *Lot 1 Closed:* {trade['lot1_size']} Lots @ {current_price:.5f}\n"
                    f"💵 *Secured Profit:* +31.25 USD (+1.25R)\n"
                    f"🛡️ *SL Moved to Entry:* {entry:.5f} (RISK-FREE!)\n"
                    f"📊 *Today Net Realized PnL:* {performance['today_pnl_usd']:+.2f} USD"
                )

        if trade['lot1_booked']:
            tp2_hit = (current_price >= tp2) if side == 'BUY' else (current_price <= tp2)
            be_hit = (current_price <= entry) if side == 'BUY' else (current_price >= entry)

            if tp2_hit:
                performance['today_wins'] += 1
                performance['alltime_wins'] += 1
                performance['today_pnl_usd'] += 62.50
                performance['alltime_pnl_usd'] += 62.50
                save_performance(performance)

                send_telegram_msg(
                    f"🏆 *[{conf['tag']} TP2 HIT - FULL TARGET ACCOMPLISHED]* 🚀\n"
                    f"✅ *Lot 2 Closed:* {trade['lot2_size']} Lots @ {current_price:.5f}\n"
                    f"💵 *Lot 2 Profit:* +62.50 USD (+2.5R)\n"
                    f"💰 *Total Net Trade Profit:* *+93.75 USD (+3.75R / +3.75%)*\n"
                    f"📊 *Today Net Realized PnL:* {performance['today_pnl_usd']:+.2f} USD"
                )
                conf['active_trade'] = None
                return

            elif be_hit:
                performance['today_wins'] += 1
                performance['alltime_wins'] += 1
                save_performance(performance)
                send_telegram_msg(
                    f"🛡️ *[{conf['tag']} RUNNER LOT 2 CLOSED AT BREAK-EVEN]*\n"
                    f"⚪ *Lot 2 Exit:* {entry:.5f} (P&L: $0.00)\n"
                    f"✅ *Final Profit Banked:* *+31.25 USD (+1.25R)*"
                )
                conf['active_trade'] = None
                return

    # 2. STRICT 15M CANDLE CLOSE EXECUTION
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
        if get_current_open_trades_count() >= MAX_CONCURRENT_TRADES:
            return

        # A. 15M MONDAY GAP-FILL (सोमवार 3:30 AM से 7:30 AM IST)
        if weekday == 0 and now_ist.hour in [3, 4, 5, 6, 7] and not conf['gap_trade_done']:
            if conf['cached_prev_close'] is None:
                df_daily = get_candles(conf['symbol'], '1d', limit=5)
                if df_daily is not None and len(df_daily) >= 3:
                    conf['cached_prev_close'] = float(df_daily.iloc[-3]['close'])

            prev_c = conf['cached_prev_close']
            if prev_c is not None:
                if conf['monday_open_price'] is None:
                    conf['monday_open_price'] = float(df_15m.iloc[-10]['open'])
                gap_size = abs(conf['monday_open_price'] - prev_c)
                if gap_size >= conf['min_gap']:
                    if conf['monday_open_price'] < prev_c and c_close > c_open:
                        safe_sl = min(c_low, p_low) - conf['sl_buffer']
                        conf['active_trade'] = create_forex_split_trade('BUY', c_close, safe_sl, conf, trade_label="15M MONDAY GAP-FILL")
                        conf['gap_trade_done'] = True
                        return
                    elif conf['monday_open_price'] > prev_c and c_close < c_open:
                        safe_sl = max(c_high, p_high) + conf['sl_buffer']
                        conf['active_trade'] = create_forex_split_trade('SELL', c_close, safe_sl, conf, trade_label="15M MONDAY GAP-FILL")
                        conf['gap_trade_done'] = True
                        return

        # B. 4H V-SHAPE SWEEPS + VOLUME/DELTA ABSORPTION
        # 1. 4H V-Bottom Swept (BUY)
        for v_low in list(conf['v_4h_lows']):
            if c_low < v_low and c_close > v_low and conf['active_trade'] is None and get_current_open_trades_count() < MAX_CONCURRENT_TRADES:
                is_valid_flow, delta_val = verify_volume_and_delta(df_15m, 'BUY')
                if is_valid_flow:
                    safe_sl = min(c_low, p_low) - conf['sl_buffer']
                    conf['active_trade'] = create_forex_split_trade('BUY', c_close, safe_sl, conf, trade_label=f"4H V-LOW SWEEP ({v_low:.5f})", delta_val=delta_val)
                    conf['v_4h_lows'].remove(v_low)
                    return

        # 2. 4H V-Top Swept (SELL)
        for v_high in list(conf['v_4h_highs']):
            if c_high > v_high and c_close < v_high and conf['active_trade'] is None and get_current_open_trades_count() < MAX_CONCURRENT_TRADES:
                is_valid_flow, delta_val = verify_volume_and_delta(df_15m, 'SELL')
                if is_valid_flow:
                    safe_sl = max(c_high, p_high) + conf['sl_buffer']
                    conf['active_trade'] = create_forex_split_trade('SELL', c_close, safe_sl, conf, trade_label=f"4H V-HIGH SWEEP ({v_high:.5f})", delta_val=delta_val)
                    conf['v_4h_highs'].remove(v_high)
                    return

# ----------------- 8. MAIN LOOP -----------------
def run_forex_bot():
    print("Forex Pure 4H V-Shape Sniper Bot Active...", flush=True)
    send_telegram_msg(
        "🚀 *Forex Pure 4H V-Shape Sniper Bot Online!*\n"
        "• Tracking: EUR/USD, GBP/USD, USD/JPY, USD/CAD\n"
        "• History Radar: 200 Candles / 33 Days Multi-Day Pivots\n"
        "• Order Flow: Volume Spike (1.4x) + Delta Absorption Filter ACTIVE\n"
        "• Noise: PDH/PDL 100% REMOVED\n"
        "• Monday Gap-Fill: Mon 3:30 AM - 7:30 AM IST\n"
        "• Concurrency: Max 2 Active Trades Across Account\n"
        "• Circuit Breaker: Strict Net Realized -$50.00 Limit\n"
        "• Daily EOD Report: Scheduled at 11:30 PM IST\n"
        "• Storage: Persistent Disk Logging Active."
    )

    while True:
        try:
            check_and_send_daily_summary()
            for asset_name in ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCAD']:
                process_forex_asset(asset_name)
                time.sleep(2)
            time.sleep(25)
        except Exception as e:
            print(f"Forex master loop error: {e}", flush=True)
            time.sleep(15)

if __name__ == '__main__':
    t = threading.Thread(target=start_web_server)
    t.daemon = True
    t.start()
    run_forex_bot()
