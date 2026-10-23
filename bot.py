import os
import io
import re
import json
import base64
import logging
import threading
import time
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask
import yfinance as yf

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# =========================================================
# 1. CONFIG & LOGGING
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

CHAT_ID_FILE = "chat_id.txt"

def load_chat_id():
    if os.path.exists(CHAT_ID_FILE):
        try:
            with open(CHAT_ID_FILE, "r") as f:
                return f.read().strip()
        except Exception:
            pass
    return os.getenv("ALLOWED_CHAT_ID", "").strip()

def save_chat_id(cid):
    if cid:
        try:
            with open(CHAT_ID_FILE, "w") as f:
                f.write(str(cid))
        except Exception as e:
            logger.warning(f"Chat ID save error: {e}")

ACTIVE_CHAT_ID = load_chat_id()

# मेमोरी स्टेट
LAST_KNOWN_PRICE = {}
LEVEL_ALERT_COOLDOWN = {}
COOLDOWN_SECONDS = 7200  # 2 घंटे का कूलडाउन

# =========================================================
# 2. SYMBOL MAP & USER LEVELS
# =========================================================
TICKER_MAP = {
    "XAUUSD": "XAUUSD=X",
    "GOLD": "XAUUSD=X",
    "SILVER": "XAGUSD=X",
    "XAGUSD": "XAGUSD=X",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "CAD=X",
    "US30": "^DJI",
    "NAS100": "^IXIC",
    "DAX40": "^GDAXI",
    "UK100": "^FTSE",
    "BTCUSD": "BTC-USD",
    "BTC": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "ETH": "ETH-USD",
    "SOLUSD": "SOL-USD",
    "SOL": "SOL-USD"
}

USER_KEY_LEVELS = {
    "XAUUSD": [276.436, 2561.073, 2924.338, 3213.15, 3288.019, 3401.552, 3917.805, 3959.981, 3996.199, 4231.59, 4399.14, 4519.006, 4666.543, 4868.081, 5451.16, 5602.225],
    "EURUSD": [0.5633, 0.82311, 0.96756, 1.01829, 1.07362, 1.10763, 1.1459, 1.17075, 1.18448, 1.19227, 1.20728, 1.22827, 1.4102, 1.51442, 1.6038],
    "GBPUSD": [1.0545, 1.1832, 1.21, 1.2709, 1.301, 1.3098, 1.314, 1.3308, 1.3402, 1.3566, 1.3673, 1.387, 1.593, 1.7191, 1.8331, 2.01, 2.1161, 2.446, 2.644],
    "USDJPY": [76.161, 101.18, 127.22, 139.889, 146.478, 149.578, 152.206, 153.003, 156.498, 158.994, 160.201, 160.329, 163.988],
    "EURJPY": [115.29, 134.034, 139.524, 148.617, 155.192, 167.779, 174.954, 180.965, 186.157],
    "
