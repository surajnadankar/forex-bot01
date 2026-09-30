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
    return "Forex Hybrid Sniper Bot Active 24/5!"

def start_web_server():
    app.run(host='0.0.0.0', port=10000)

# ----------------- 3. EXCHANGE & RISK SHIELD -----------------
exchange = ccxt.kraken({
    'enableRateLimit': True,
    'rateLimit': 2500
})
api_lock = threading.Lock()

ACCOUNT_SIZE_USD = 2500.0
RISK_PER_TRADE_USD = 25.0       # 1% Risk per trade ($25)
MAX_DAILY_LOSS_USD = 50.0       # $50 Max Net Realized Daily Loss Circuit Breaker
MAX_CONCURRENT_TRADES = 2

DATA_FILE = "forex_performance.json"
LEVELS_FILE = "forex_levels.json"

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

performance = load_json(DATA_FILE, {
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
    'EURUSD': {'highs': [], 'lows': []},
    'GBPUSD': {'highs': [], 'lows': []},
    'USDJPY': {'highs': [], 'lows': []},
    'USDCAD': {'highs': [], 'lows': []}
})

risk_guard = {
    'current_date': "",
    'is_frozen_today': False
}

FOREX_ASSETS = {
    'EURUSD': {
        'symbol': 'EUR/USD',
        'tag': '💶 EUR/USD',
        'pip_size': 0.0001,
        'sl_buffer': 0.0006,
        'min_allowed_sl': 0.0015,
        'max_allowed_sl': 0.0040,
        'active_trade': None,
        'last_candle_time': None
    },
    'GBPUSD': {
        'symbol': 'GBP/USD',
        'tag': '💷 GBP/USD',
        'pip_size': 0.0001,
        'sl_buffer': 0.0007,
        'min_allowed_sl': 0.0018,
        'max_allowed_sl': 0.0045,
        'active_trade': None,
        'last_candle_time': None
    },
    'USDJPY': {
        'symbol': 'USD/JPY',
        'tag': '💴 USD/JPY',
        'pip_size': 0.01,
        'sl_buffer': 0.08,
        'min_allowed_sl': 0.25,
        'max_allowed_sl': 0.65,
        'active_trade': None,
        'last_candle_time': None
    },
    'USDCAD': {
        'symbol': 'USD/CAD',
        'tag': '🍁 USD/CAD',
        'pip_size': 0.0001,
        'sl_buffer': 0.0006,
        'min_allowed_sl': 0.0015,
        'max_allowed_sl': 0.0040,
        'active_trade': None,
        'last_candle_time': None
    }
}

def get_current_open_trades_count():
    count = 0
    for c in FOREX_ASSETS.values():
        if c['active_trade'] is not None:
            count += 1
    return count

def check_net_circuit_breaker():
    global performance, risk_guard
    if performance['today_pnl_usd'] <= -MAX_DAILY_LOSS_USD:
        risk_guard['is_frozen_today'] = True
        send_telegram_msg(
            "🚨🚨 *[FOREX CIRCUIT BREAKER ACTIVATED]* 🚨🚨\n"
            f"आज का शुद्ध दैनिक नुकसान (-${abs(performance['today_pnl_usd']):.2f}) -$50.00 तक पहुँच गया!\n"
            "🛡️ *ACCOUNT SAFEGUARD:* ड्रॉडाउन रोकने के लिए बॉट आज रात 12:00 AM तक पूरी तरह FREEZE रहेगा।"
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
        save_json(DATA_FILE, performance)

    if now_ist.hour == 23 and now_ist.minute >= 30 and not performance['daily_summary_sent']:
        win_rate = (performance['today_wins'] / performance['today_trades'] * 100) if performance['today_trades'] > 0 else 0.0
        summary_msg = (
            f"📋 *[FOREX END OF DAY REPORT]*\n"
            f"📅 *Date:* {today_str}\n"
            f"-----------------------------------\n"
            f"🔢 *Total Trades Today:* {performance['today_trades']}\n"
            f"✅ *Wins:* {performance['today_wins']} | ❌ *Losses:* {performance['today_losses']}\n"
            f"🎯 *Today's Win Rate:* {win_rate:.1f}%\n"
            f"💰 *Today's Net Realized PnL:* *{performance['today_pnl_usd']:+.2f} USD*\n"
            f"🏛️ *Evaluation PnL:* *{performance['alltime_pnl_usd']:+.2f} USD*\n"
            f"-----------------------------------"
        )
        send_telegram_msg(summary_msg)
        performance['daily_summary_sent'] = True
        save_json(DATA_FILE, performance)

def get_candles(symbol, timeframe, limit=35):
    with api_lock:
        try:
            time.sleep(1.2)
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as e:
            print(f"Kraken fetch error ({symbol} - {timeframe}): {e}", flush=True)
            return None

def create_forex_split_trade(side, entry, calculated_sl, conf, trade_label="KEY-LEVEL SWEEP"):
    if get_current_open_trades_count() >= MAX_CONCURRENT_TRADES:
        return None
    raw_distance = abs(entry - calculated_sl)
    if raw_distance <= 0: return None
    effective_distance = max(raw_distance, conf['min_allowed_sl'])

    if side == 'BUY': actual_sl = entry - effective_distance
    else: actual_sl = entry + effective_distance

    if effective_distance > conf['max_allowed_sl']:
        send_telegram_msg(
            f"⚠️️ *[{conf['tag']} TRADE SKIPPED - SL TOO LARGE]*\n"
            f"SL Distance: {effective_distance / conf['pip_size']:.1f} Pips"
        )
        return None

    pips = effective_distance / conf['pip_size']
    calculated_lots = round(RISK_PER_TRADE_USD / (pips * 10.0), 2)
    if calculated_lots < 0.02: calculated_lots = 0.02
    lot1 = round(calculated_lots / 2, 2)
    lot2 = round(calculated_lots - lot1, 2)

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
        f"💼 *Total Lots:* {calculated_lots} Lots (Lot1: {lot1} | Lot2: {lot2})\n"
        f"🎯 *TP1 Target:* {tp1:.5f}\n"
        f"🏆 *TP2 Target:* {tp2:.5f}\n"
        f"🔒 *Max Risk:* $25.00 (1.0%)\n"
        f"📊 *Open Trades:* {get_current_open_trades_count() + 1}/{MAX_CONCURRENT_TRADES}\n"
        f"-----------------------------"
    )
    return trade

def process_forex_asset(name):
    global performance, risk_guard, user_levels
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
                save_json(DATA_FILE, performance)
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
                save_json(DATA_FILE, performance)

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
                save_json(DATA_FILE, performance)

                send_telegram_msg(
                    f"🏆 *[{conf['tag']} TP2 HIT - FULL TARGET ACCOMPLISHED]* 🚀\n"
                    f"✅ *Lot 2 Closed:* {trade['lot2_size']} Lots @ {current_price:.5f}\n"
                    f"💵 *Total Profit:* *+93.75 USD (+3.75R / +3.75%)*\n"
                    f"📊 *Today Net Realized PnL:* {performance['today_pnl_usd']:+.2f} USD"
                )
                conf['active_trade'] = None
                return

            elif be_hit:
                performance['today_wins'] += 1
                performance['alltime_wins'] += 1
                save_json(DATA_FILE, performance)
                send_telegram_msg(
                    f"🛡️ *[{conf['tag']} RUNNER LOT 2 CLOSED AT BREAK-EVEN]*\n"
                    f"⚪ *Lot 2 Exit:* {entry:.5f} (P&L: $0.00)\n"
                    f"✅ *Final Profit Banked:* *+31.25 USD (+1.25R)*"
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
        if get_current_open_trades_count() >= MAX_CONCURRENT_TRADES:
            return

        target_highs = list(user_levels.get(name, {}).get('highs', []))
        target_lows = list(user_levels.get(name, {}).get('lows', []))

        # USER KEY-LOW SWEEP (BUY)
        for t_low in target_lows:
            if c_low < t_low and c_close > t_low and c_close > c_open and conf['active_trade'] is None:
                sl = min(c_low, p_low) - conf['sl_buffer']
                conf['active_trade'] = create_forex_split_trade('BUY', c_close, sl, conf, trade_label=f"KEY-LOW SWEEP ({t_low:.5f})")
                user_levels[name]['lows'].remove(t_low)
                save_json(LEVELS_FILE, user_levels)
                send_telegram_msg(f"🎯 *[LEVEL MITIGATED]* {name} Key-Low {t_low:.5f} tested and removed from active list!")
                return

        # USER KEY-HIGH SWEEP (SELL)
        for t_high in target_highs:
            if c_high > t_high and c_close < t_high and c_close < c_open and conf['active_trade'] is None:
                sl = max(c_high, p_high) + conf['sl_buffer']
                conf['active_trade'] = create_forex_split_trade('SELL', c_close, sl, conf, trade_label=f"KEY-HIGH SWEEP ({t_high:.5f})")
                user_levels[name]['highs'].remove(t_high)
                save_json(LEVELS_FILE, user_levels)
                send_telegram_msg(f"🎯 *[LEVEL MITIGATED]* {name} Key-High {t_high:.5f} tested and removed from active list!")
                return

# ----------------- TELEGRAM COMMAND LISTENER (FOREX) -----------------
def listen_telegram_commands_forex():
    global user_levels
    last_update_id = 0
    print("Forex Telegram Command Listener Started...", flush=True)

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=30"
            resp = requests.get(url, timeout=35).json()

            if "result" in resp:
                for item in resp["result"]:
                    last_update_id = item["update_id"]
                    msg = item.get("message", {})
                    text = msg.get("text", "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    if chat_id != str(TELEGRAM_CHAT_ID) or not text.startswith("/"):
                        continue

                    parts = text.split()
                    cmd = parts[0].lower()

                    if cmd == "/f_levels":
                        msg_out = "📋 *[LIVE ACTIVE KEY-LEVELS (FOREX)]*\n-----------------------------\n"
                        for asset, data in user_levels.items():
                            h_str = ", ".join([f"{x:.5f}" for x in data.get('highs', [])]) or "None"
                            l_str = ", ".join([f"{x:.5f}" for x in data.get('lows', [])]) or "None"
                            msg_out += f"*{asset}:*\n  🔺 *Highs:* {h_str}\n  🔻 *Lows:* {l_str}\n"
                        msg_out += "-----------------------------\nℹ️ *Commands:*\n`/f_set_highs GBPUSD 1.3320`\n`/f_set_lows GBPUSD 1.3209, 1.3175`\n`/f_clear GBPUSD`"
                        send_telegram_msg(msg_out)

                    elif cmd in ["/f_set_highs", "/f_set_lows"]:
                        if len(parts) >= 3:
                            asset = parts[1].upper().replace("/", "")
                            if asset in user_levels:
                                raw_vals = "".join(parts[2:]).split(",")
                                vals = [float(v.strip()) for v in raw_vals if v.strip()]
                                key = "highs" if cmd == "/f_set_highs" else "lows"
                                user_levels[asset][key] = sorted(vals)
                                save_json(LEVELS_FILE, user_levels)
                                send_telegram_msg(f"✅ *[{asset} {key.upper()} UPDATED]*\nNew active levels: {user_levels[asset][key]}")
                            else:
                                send_telegram_msg(f"❌ Unknown asset `{asset}`. Use EURUSD, GBPUSD, USDJPY, USDCAD.")

                    elif cmd in ["/f_add_high", "/f_add_low"]:
                        if len(parts) >= 3:
                            asset = parts[1].upper().replace("/", "")
                            if asset in user_levels:
                                val = float(parts[2].replace(",", "").strip())
                                key = "highs" if cmd == "/f_add_high" else "lows"
                                if val not in user_levels[asset][key]:
                                    user_levels[asset][key].append(val)
                                    user_levels[asset][key].sort()
                                    save_json(LEVELS_FILE, user_levels)
                                send_telegram_msg(f"✅ *[{asset} {key.upper()} ADDED]*\nLevel: {val:.5f}\nCurrent: {user_levels[asset][key]}")

                    elif cmd == "/f_clear":
                        if len(parts) >= 2:
                            asset = parts[1].upper().replace("/", "")
                            if asset in user_levels:
                                user_levels[asset]['highs'] = []
                                user_levels[asset]['lows'] = []
                                save_json(LEVELS_FILE, user_levels)
                                send_telegram_msg(f"🧹 *[{asset} LEVELS CLEARED]*")

        except Exception as e:
            print(f"Telegram listener error: {e}", flush=True)
            time.sleep(5)
        time.sleep(1)

def run_forex_bot():
    print("Forex Hybrid Sniper Bot Active...", flush=True)
    send_telegram_msg(
        "🚀 *Forex Hybrid Sniper Bot Online (Telegram Controlled)!*\n"
        "• Check levels anytime: Send `/f_levels`\n"
        "• Max 2 Open Trades\n"
        "• Circuit Breaker: Strict Net -$50.00 Limit\n"
        "• Update anytime via chat!"
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
    t_web = threading.Thread(target=start_web_server)
    t_web.daemon = True
    t_web.start()

    t_tg = threading.Thread(target=listen_telegram_commands_forex)
    t_tg.daemon = True
    t_tg.start()

    run_forex_bot()
