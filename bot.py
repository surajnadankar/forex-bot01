import ccxt
import time
import pandas as pd
import threading
import requests
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

# ----------------- 2. WEB SERVER (FOR KEEP-ALIVE) -----------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Forex Master 4-Pair Structural Liquidity Bot is Running 24/5!"

def start_web_server():
    app.run(host='0.0.0.0', port=10000)

# ----------------- 3. EXCHANGE & PROP FIRM RISK SHIELD -----------------
exchange = ccxt.kraken({
    'enableRateLimit': True,
    'rateLimit': 2500
})
api_lock = threading.Lock()

ACCOUNT_SIZE_USD = 2500.0
RISK_PER_TRADE_USD = 25.0       # 1% Risk per trade ($25)
MAX_DAILY_LOSS_USD = 50.0       # 2 Losses = Stop for the day circuit breaker

risk_guard = {
    'current_date': "",
    'daily_loss_accumulated': 0.0,
    'is_frozen_today': False
}

FOREX_ASSETS = {
    'EURUSD': {
        'symbol': 'EUR/USD',
        'tag': '💶 EUR/USD',
        'pip_size': 0.0001,
        'sl_buffer': 0.0003,      # 3 pips buffer
        'min_allowed_sl': 0.0006, # 6 pips min safe SL
        'max_allowed_sl': 0.0025, # 25 pips max SL
        'min_gap': 0.0008,        # 8 pips min gap to activate gap-fill
        'min_swing_depth': 0.0030,# 30 pips swing depth
        'active_trade': None,
        'last_candle_time': None,
        'cached_pdh': None,
        'cached_pdl': None,
        'cached_prev_close': None,
        'true_swing_high': None,
        'true_swing_low': None,
        'buy_sweep_active': False,
        'buy_sweep_lowest': 0.0,
        'sell_sweep_active': False,
        'sell_sweep_highest': 0.0,
        'gap_trade_done': False,
        'last_daily_fetch': "",
        'last_4h_fetch': 0
    },
    'GBPUSD': {
        'symbol': 'GBP/USD',
        'tag': '💷 GBP/USD',
        'pip_size': 0.0001,
        'sl_buffer': 0.0004,      # 4 pips buffer
        'min_allowed_sl': 0.0008, # 8 pips min safe SL
        'max_allowed_sl': 0.0035, # 35 pips max SL
        'min_gap': 0.0010,        # 10 pips min gap
        'min_swing_depth': 0.0040,# 40 pips swing depth
        'active_trade': None,
        'last_candle_time': None,
        'cached_pdh': None,
        'cached_pdl': None,
        'cached_prev_close': None,
        'true_swing_high': None,
        'true_swing_low': None,
        'buy_sweep_active': False,
        'buy_sweep_lowest': 0.0,
        'sell_sweep_active': False,
        'sell_sweep_highest': 0.0,
        'gap_trade_done': False,
        'last_daily_fetch': "",
        'last_4h_fetch': 0
    },
    'USDJPY': {
        'symbol': 'USD/JPY',
        'tag': '💴 USD/JPY',
        'pip_size': 0.01,
        'sl_buffer': 0.04,        # 4 pips buffer
        'min_allowed_sl': 0.10,   # 10 pips min safe SL
        'max_allowed_sl': 0.45,   # 45 pips max SL
        'min_gap': 0.15,          # 15 pips min gap
        'min_swing_depth': 0.50,  # 50 pips swing depth
        'active_trade': None,
        'last_candle_time': None,
        'cached_pdh': None,
        'cached_pdl': None,
        'cached_prev_close': None,
        'true_swing_high': None,
        'true_swing_low': None,
        'buy_sweep_active': False,
        'buy_sweep_lowest': 0.0,
        'sell_sweep_active': False,
        'sell_sweep_highest': 0.0,
        'gap_trade_done': False,
        'last_daily_fetch': "",
        'last_4h_fetch': 0
    },
    'GBPJPY': {
        'symbol': 'GBP/JPY',
        'tag': '🐉 GBP/JPY',
        'pip_size': 0.01,
        'sl_buffer': 0.06,        # 6 pips buffer
        'min_allowed_sl': 0.15,   # 15 pips min safe SL
        'max_allowed_sl': 0.60,   # 60 pips max SL
        'min_gap': 0.20,          # 20 pips min gap
        'min_swing_depth': 0.65,  # 65 pips swing depth
        'active_trade': None,
        'last_candle_time': None,
        'cached_pdh': None,
        'cached_pdl': None,
        'cached_prev_close': None,
        'true_swing_high': None,
        'true_swing_low': None,
        'buy_sweep_active': False,
        'buy_sweep_lowest': 0.0,
        'sell_sweep_active': False,
        'sell_sweep_highest': 0.0,
        'gap_trade_done': False,
        'last_daily_fetch': "",
        'last_4h_fetch': 0
    }
}

performance = {
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

def register_trade_loss(loss_usd):
    global risk_guard
    risk_guard['daily_loss_accumulated'] += loss_usd
    if risk_guard['daily_loss_accumulated'] >= MAX_DAILY_LOSS_USD:
        risk_guard['is_frozen_today'] = True
        send_telegram_msg(
            "🚨🚨 *[FOREX CIRCUIT BREAKER ACTIVATED]* 🚨🚨\n"
            f"आज का अधिकतम नुकसान (-${risk_guard['daily_loss_accumulated']:.2f}) हिट हो गया!\n"
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
        risk_guard['daily_loss_accumulated'] = 0.0
        risk_guard['is_frozen_today'] = False
        for c in FOREX_ASSETS.values():
            c['gap_trade_done'] = False

    if now_ist.hour == 23 and now_ist.minute >= 30 and not performance['daily_summary_sent']:
        win_rate = (performance['today_wins'] / performance['today_trades'] * 100) if performance['today_trades'] > 0 else 0.0
        all_win_rate = (performance['alltime_wins'] / performance['alltime_trades'] * 100) if performance['alltime_trades'] > 0 else 0.0
        status_emoji = "🔥" if performance['today_pnl_usd'] > 0 else ("😐" if performance['today_pnl_usd'] == 0 else "🔻")

        summary_msg = (
            f"📋 *[FOREX END OF DAY REPORT]* {status_emoji}\n"
            f"📅 *Date:* {today_str}\n"
            f"-----------------------------------\n"
            f"🔢 *Total Forex Trades Today:* {performance['today_trades']}\n"
            f"✅ *Wins:* {performance['today_wins']} | ❌ *Losses:* {performance['today_losses']}\n"
            f"🎯 *Today's Win Rate:* {win_rate:.1f}%\n"
            f"💰 *Today's Net PnL:* *{performance['today_pnl_usd']:+.2f} USD*\n"
            f"-----------------------------------\n"
            f"🏛️ *THE5ERS $2,500 FOREX EVALUATION TRACKER:*\n"
            f"• All-Time Trades Logged: {performance['alltime_trades']}\n"
            f"• Cumulative Win Rate: {all_win_rate:.1f}%\n"
            f"• Total Evaluation PnL: *{performance['alltime_pnl_usd']:+.2f} USD*\n"
            f"• Circuit Breaker: {'🔴 FROZEN' if risk_guard['is_frozen_today'] else '🟢 ACTIVE'}\n"
            f"-----------------------------------"
        )
        send_telegram_msg(summary_msg)
        performance['daily_summary_sent'] = True

def get_candles(symbol, timeframe, limit=80):
    with api_lock:
        try:
            time.sleep(1.2)
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as e:
            print(f"Kraken fetch error ({symbol} - {timeframe}): {e}", flush=True)
            return None

def find_filtered_swings(df_4h, current_price, min_depth):
    try:
        if df_4h is None or len(df_4h) < 25:
            return None, None

        highs = [float(x) for x in df_4h['high'].tolist()]
        lows = [float(x) for x in df_4h['low'].tolist()]
        n = len(highs)

        valid_swing_lows = []
        valid_swing_highs = []

        for i in range(2, n - 3):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                left_low = min(lows[i-2], lows[i-1])
                right_low = min(lows[i+1], lows[i+2])
                if (highs[i] - left_low) >= min_depth and (highs[i] - right_low) >= min_depth:
                    valid_swing_highs.append(highs[i])

            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                left_high = max(highs[i-2], highs[i-1])
                right_high = max(highs[i+1], highs[i+2])
                if (left_high - lows[i]) >= min_depth and (right_high - lows[i]) >= min_depth:
                    valid_swing_lows.append(lows[i])

        below_current_lows = [l for l in valid_swing_lows if l < current_price]
        target_low = max(below_current_lows) if len(below_current_lows) > 0 else (min(valid_swing_lows) if len(valid_swing_lows) > 0 else min(lows[-30:-3]))

        above_current_highs = [h for h in valid_swing_highs if h > current_price]
        target_high = min(above_current_highs) if len(above_current_highs) > 0 else (max(valid_swing_highs) if len(valid_swing_highs) > 0 else max(highs[-30:-3]))

        return target_high, target_low
    except Exception as e:
        print(f"Swing calc error: {e}", flush=True)
        return None, None

def update_daily_and_swings(name, current_price):
    conf = FOREX_ASSETS[name]
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today_str = now_ist.strftime("%Y-%m-%d")

    if conf['last_daily_fetch'] != today_str or conf['cached_pdh'] is None:
        df_daily = get_candles(conf['symbol'], '1d', limit=10)
        if df_daily is not None and len(df_daily) >= 3:
            prev_day = df_daily.iloc[-2]
            conf['cached_pdh'] = float(prev_day['high'])
            conf['cached_pdl'] = float(prev_day['low'])
            conf['cached_prev_close'] = float(prev_day['close'])
            conf['last_daily_fetch'] = today_str
            print(f"[{name} DAILY CACHED] PDH: {conf['cached_pdh']:.4f} | PDL: {conf['cached_pdl']:.4f}", flush=True)

    now_ts = time.time()
    if (now_ts - conf['last_4h_fetch']) > 3600 or conf['true_swing_high'] is None:
        df_4h = get_candles(conf['symbol'], '4h', limit=80)
        s_high, s_low = find_filtered_swings(df_4h, current_price, conf['min_swing_depth'])
        if s_high is not None and s_low is not None:
            conf['true_swing_high'] = float(s_high)
            conf['true_swing_low'] = float(s_low)
            conf['last_4h_fetch'] = now_ts
            print(f"[{name} SWINGS] High: {conf['true_swing_high']:.4f} | Low: {conf['true_swing_low']:.4f}", flush=True)

def create_forex_split_trade(side, entry, calculated_sl, conf, trade_label="STRUCTURAL 2-LOT"):
    raw_distance = abs(entry - calculated_sl)
    if raw_distance <= 0:
        return None

    # Minimum Safe SL Floor
    effective_distance = max(raw_distance, conf['min_allowed_sl'])

    if side == 'BUY':
        actual_sl = entry - effective_distance
    else:
        actual_sl = entry + effective_distance

    # Max SL Cap
    if effective_distance > conf['max_allowed_sl']:
        send_telegram_msg(
            f"⚠️ *[{conf['tag']} TRADE SKIPPED - SL TOO LARGE]*\n"
            f"SL Distance: {effective_distance / conf['pip_size']:.1f} Pips (Max: {conf['max_allowed_sl'] / conf['pip_size']:.1f} Pips)"
        )
        return None

    # In Forex: 1 Standard Lot (100k) ≈ $10/pip.
    # Risk ($25) = Lots * (Pips * 10)  -->  Lots = 25 / (Pips * 10)
    pips = effective_distance / conf['pip_size']
    calculated_lots = round(RISK_PER_TRADE_USD / (pips * 10.0), 2)
    if calculated_lots < 0.02:
        calculated_lots = 0.02

    lot1 = round(calculated_lots / 2, 2)
    lot2 = round(calculated_lots - lot1, 2)

    if side == 'BUY':
        tp1 = entry + (effective_distance * 2.5) # 1:2.5 RR
        tp2 = entry + (effective_distance * 5.0) # 1:5.0 RR
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
        f"💼 *Total Lots ($2500 Acct):* {calculated_lots} Lots\n"
        f"   • *Lot 1:* {lot1} Lots (TP1 @ 1:2.5 RR)\n"
        f"   • *Lot 2:* {lot2} Lots (TP2 @ 1:5.0 RR)\n"
        f"🎯 *TP1 Target:* {tp1:.5f} (Locks +$31.25)\n"
        f"🏆 *TP2 Target:* {tp2:.5f} (Adds +$62.50)\n"
        f"🔒 *Max Risk:* $25.00 (1.0%)\n"
        f"-----------------------------"
    )
    return trade

def process_forex_asset(name):
    conf = FOREX_ASSETS[name]

    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    weekday = now_ist.weekday()
    # Weekend Check (Forex closed Sat 3:30 AM to Mon 3:30 AM IST)
    if weekday == 5 or (weekday == 6 and now_ist.hour < 23):
        return

    df_15m = get_candles(conf['symbol'], '15m', limit=25)
    if df_15m is None or len(df_15m) < 5:
        return

    last_closed = df_15m.iloc[-2]
    current_price = float(df_15m.iloc[-1]['close'])
    candle_time = int(last_closed['timestamp'])

    update_daily_and_swings(name, current_price)

    # 1. LIVE 2-LOT SL / TP / BE MONITORING
    trade = conf['active_trade']
    if trade is not None:
        side = trade['side']
        entry = trade['entry']
        sl = trade['sl']
        tp1 = trade['tp1']
        tp2 = trade['tp2']

        # Initial SL Hit
        if not trade['lot1_booked']:
            hit = (current_price <= sl) if side == 'BUY' else (current_price >= sl)
            if hit:
                performance['today_trades'] += 1
                performance['alltime_trades'] += 1
                performance['today_losses'] += 1
                performance['alltime_losses'] += 1
                performance['today_pnl_usd'] -= 25.0
                performance['alltime_pnl_usd'] -= 25.0
                register_trade_loss(25.0)

                send_telegram_msg(
                    f"🛑 *[{conf['tag']} FULL STOP LOSS HIT]* ❌\n"
                    f"-----------------------------\n"
                    f"Exit: {current_price:.5f} | Entry: {entry:.5f}\n"
                    f"Loss: -25.00 USD (-1.0%)\n"
                    f"Today PnL: {performance['today_pnl_usd']:+.2f} USD\n"
                    f"-----------------------------"
                )
                conf['active_trade'] = None
                return

        # TP1 Hit (1:2.5 RR)
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

                send_telegram_msg(
                    f"💰 *[{conf['tag']} TP1 HIT - 50% PARTIAL BOOKED]* 🎯\n"
                    f"-----------------------------\n"
                    f"✅ *Lot 1 Closed:* {trade['lot1_size']} Lots @ {current_price:.5f}\n"
                    f"💵 *Secured Profit:* +31.25 USD (+1.25R)\n"
                    f"🛡️ *SL Moved to Entry:* {entry:.5f} (RISK-FREE!)\n"
                    f"🏆 *Lot 2 Running for TP2:* {tp2:.5f} (+5.0R)\n"
                    f"-----------------------------"
                )

        # Lot 2 Monitoring
        if trade['lot1_booked']:
            tp2_hit = (current_price >= tp2) if side == 'BUY' else (current_price <= tp2)
            be_hit = (current_price <= entry) if side == 'BUY' else (current_price >= entry)

            if tp2_hit:
                performance['today_wins'] += 1
                performance['alltime_wins'] += 1
                performance['today_pnl_usd'] += 62.50
                performance['alltime_pnl_usd'] += 62.50

                send_telegram_msg(
                    f"🏆 *[{conf['tag']} TP2 HIT - FULL TARGET ACCOMPLISHED]* 🚀\n"
                    f"-----------------------------\n"
                    f"✅ *Lot 2 Closed:* {trade['lot2_size']} Lots @ {current_price:.5f}\n"
                    f"💵 *Lot 2 Profit:* +62.50 USD (+2.5R)\n"
                    f"💰 *Total Net Trade Profit:* *+93.75 USD (+3.75R / +3.75%)*\n"
                    f"📊 *Today's Score:* {performance['today_pnl_usd']:+.2f} USD\n"
                    f"-----------------------------"
                )
                conf['active_trade'] = None
                return

            elif be_hit:
                performance['today_wins'] += 1
                performance['alltime_wins'] += 1
                send_telegram_msg(
                    f"🛡️ *[{conf['tag']} RUNNER LOT 2 CLOSED AT BREAK-EVEN]*\n"
                    f"-----------------------------\n"
                    f"⚪ *Lot 2 Exit:* {entry:.5f} (P&L: $0.00)\n"
                    f"✅ *Final Profit Banked:* *+31.25 USD (+1.25R)*\n"
                    f"📉 Capital 100% Protected!\n"
                    f"-----------------------------"
                )
                conf['active_trade'] = None
                return

    # 2. 15M CANDLE CLOSE EXECUTION
    if candle_time != conf['last_candle_time']:
        conf['last_candle_time'] = candle_time
        c_open = float(last_closed['open'])
        c_high = float(last_closed['high'])
        c_low = float(last_closed['low'])
        c_close = float(last_closed['close'])

        if risk_guard['is_frozen_today'] or conf['active_trade'] is not None:
            return

        # --- A. 15M GAP-FILL STRATEGY ---
        prev_c = conf['cached_prev_close']
        if prev_c is not None and not conf['gap_trade_done']:
            day_open_price = float(df_15m.iloc[0]['open'])
            gap_size = abs(day_open_price - prev_c)

            if gap_size >= conf['min_gap']:
                # Gap Up Scenario -> Look for first 15M Bearish Red Candle
                if day_open_price > prev_c and c_close < c_open and c_close < prev_c + (gap_size * 0.9):
                    sl = c_high + conf['sl_buffer']
                    conf['active_trade'] = create_forex_split_trade('SELL', c_close, sl, conf, trade_label="15M GAP-FILL REVERSAL")
                    conf['gap_trade_done'] = True
                    return

                # Gap Down Scenario -> Look for first 15M Bullish Green Candle
                elif day_open_price < prev_c and c_close > c_open and c_close > prev_c - (gap_size * 0.9):
                    sl = c_low - conf['sl_buffer']
                    conf['active_trade'] = create_forex_split_trade('BUY', c_close, sl, conf, trade_label="15M GAP-FILL REVERSAL")
                    conf['gap_trade_done'] = True
                    return

        # --- B. PDH / PDL LIQUIDITY SWEEPS ---
        pdh = conf['cached_pdh']
        pdl = conf['cached_pdl']
        if pdh and pdl and conf['active_trade'] is None:
            if c_high > pdh and c_close < pdh and c_close < c_open:
                sl = c_high + conf['sl_buffer']
                conf['active_trade'] = create_forex_split_trade('SELL', c_close, sl, conf, trade_label="PDH SWEEP")
                return
            elif c_low < pdl and c_close > pdl and c_close > c_open:
                sl = c_low - conf['sl_buffer']
                conf['active_trade'] = create_forex_split_trade('BUY', c_close, sl, conf, trade_label="PDL SWEEP")
                return

        # --- C. 4H STRUCTURAL SWING SWEEPS ---
        s_low = conf['true_swing_low']
        s_high = conf['true_swing_high']
        if s_low and s_high:
            if c_low < s_low and not conf['buy_sweep_active']:
                conf['buy_sweep_active'] = True
                conf['buy_sweep_lowest'] = c_low
                send_telegram_msg(
                    f"⚠️ *[{conf['tag']} STRUCTURAL LOW SWEEP]*\n"
                    f"Target Low ({s_low:.5f}) swept!\n"
                    f"Lowest Wick: {conf['buy_sweep_lowest']:.5f}"
                )

            if conf['buy_sweep_active']:
                if c_low < conf['buy_sweep_lowest']:
                    conf['buy_sweep_lowest'] = c_low
                if c_close > c_open and conf['active_trade'] is None:
                    sl = conf['buy_sweep_lowest'] - conf['sl_buffer']
                    conf['active_trade'] = create_forex_split_trade('BUY', c_close, sl, conf, trade_label="STRUCTURAL V-BUY")
                    conf['buy_sweep_active'] = False
                    return

            if c_high > s_high and not conf['sell_sweep_active']:
                conf['sell_sweep_active'] = True
                conf['sell_sweep_highest'] = c_high
                send_telegram_msg(
                    f"⚠️ *[{conf['tag']} STRUCTURAL HIGH SWEEP]*\n"
                    f"Target High ({s_high:.5f}) swept!\n"
                    f"Highest Wick: {conf['sell_sweep_highest']:.5f}"
                )

            if conf['sell_sweep_active']:
                if c_high > conf['sell_sweep_highest']:
                    conf['sell_sweep_highest'] = c_high
                if c_close < c_open and conf['active_trade'] is None:
                    sl = conf['sell_sweep_highest'] + conf['sl_buffer']
                    conf['active_trade'] = create_forex_split_trade('SELL', c_close, sl, conf, trade_label="STRUCTURAL PEAK SELL")
                    conf['sell_sweep_active'] = False
                    return

def run_forex_bot():
    print("Forex Master 4-Pair Bot Active...", flush=True)
    send_telegram_msg(
        "🚀 *Forex Master 4-Pair Shield Bot Online!*\n"
        "• Assets: EUR/USD, GBP/USD, USD/JPY, GBP/JPY\n"
        "• Evaluation: The5ers $2500 Account\n"
        "• Split 2-Lot: TP1 @ 1:2.5 RR (+BE) | TP2 @ 1:5.0 RR\n"
        "• Strategies: 15M Gap-Fill + PDH/PDL Sweeps + 4H Structural Swings\n"
        "• Circuit Breaker: $50 Daily Max Loss Limit Active."
    )

    while True:
        try:
            check_and_send_daily_summary()

            for asset_name in ['EURUSD', 'GBPUSD', 'USDJPY', 'GBPJPY']:
                process_forex_asset(asset_name)
                time.sleep(3)
            time.sleep(25)
        except Exception as e:
            print(f"Forex master loop error: {e}", flush=True)
            time.sleep(15)

if __name__ == '__main__':
    t = threading.Thread(target=start_web_server)
    t.daemon = True
    t.start()
    run_forex_bot()
