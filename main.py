import os
import sqlite3
import asyncio
import httpx
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- تنظیمات و متغیرهای سراسری ---
APP_VERSION = "v1.2.0"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
GATEIO_API_BASE = "https://api.gateio.ws/api/v4"
# مسیر دیتابیس برای رندر (اگر دیسک نباشد در لوکال ساخته می‌شود)
DB_PATH = "[/mnt/data/bot_data.db"](https://storage.gapgpt.app/media/code_interpreter/41f3cde9-4b72-49e4-8434-f3dc06b45508/bot_data.db%22) if os.path.exists("/mnt/data") else "bot_data.db"
DEFAULT_SYMBOL = "ETH_USDT"
AUTO_SEND_INTERVAL_MINUTES = 30
SUPPORTED_COINS = ["BTC", "SOL", "TON", "ARB", "POL", "BNB", "XRP", "ADA", "AVAX", "LINK"]

app = FastAPI()
scheduler = AsyncIOScheduler()

# --- مدیریت دیتابیس (SQLite) ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS active_chats (chat_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

def add_chat(chat_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO active_chats (chat_id) VALUES (?)", (chat_id,))

def remove_chat(chat_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM active_chats WHERE chat_id = ?", (chat_id,))

def get_all_chats() -> List[int]:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            return [row[0] for row in conn.execute("SELECT chat_id FROM active_chats").fetchall()]
    except Exception as e:
        print(f"DB Error: {e}")
        return []

# --- محاسبات تکنیکال ---
def calculate_ema(data: List[float], period: int) -> Optional[float]:
    if len(data) < period: return None
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_rsi(data: List[float], period: int = 14) -> Optional[float]:
    if len(data) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(data)):
        diff = data[i] - data[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0: return 100
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

async def fetch_candles(symbol: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            url = f"{GATEIO_API_BASE}/spot/candlesticks"
            params = {"currency_pair": symbol, "interval": "1h", "limit": "250"}
            resp = await client.get(url, params=params)
            return resp.json()
    except: return []

async def build_analysis_message(symbol: str) -> str:
    candles = await fetch_candles(symbol)
    if not candles or len(candles) < 200:
        return f"❌ خطا در دریافت داده‌های {symbol}"
    
    closes = [float(c[2]) for c in candles]
    current_price = closes[-1]
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)
    rsi = calculate_rsi(closes, 14)
    
    trend = "صعودی 🟢" if (ema20 and current_price > ema20 and ema20 > (ema50 or 0)) else "نزولی 🔴"
    rsi_val = f"{rsi:.1f}" if rsi else "N/A"

    text = (
        f"📊 <b>تحلیل {symbol.split('_')[0]}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💵 قیمت: <code>{current_price:,.2f}</code>\n"
        f"📈 روند: <b>{trend}</b>\n"
        f"🧭 شاخص RSI: <code>{rsi_val}</code>\n"
        f"📉 میانگین EMA20: <code>{ema20:,.1f}</code>\n"
        f"🕒 زمان: {datetime.now().strftime('%H:%M')}\n"
        f"━━━━━━━━━━━━━━"
    )
    return text

# --- کیبوردها ---
def main_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "📊 تحلیل لحظه‌ای ETH", "callback_data": "analyze_ETH_USDT"}],
            [{"text": "🪙 لیست ارزهای دیگر", "callback_data": "list_coins"}],
            [{"text": "⏹ توقف تحلیل خودکار", "callback_data": "stop"}, {"text": "▶️ شروع مجدد", "callback_data": "start"}]
        ]
    }

def coins_keyboard():
    buttons = []
    for i in range(0, len(SUPPORTED_COINS), 2):
        row = [{"text": SUPPORTED_COINS[i], "callback_data": f"analyze_{SUPPORTED_COINS[i]}_USDT"}]
        if i+1 < len(SUPPORTED_COINS):
            row.append({"text": SUPPORTED_COINS[i+1], "callback_data": f"analyze_{SUPPORTED_COINS[i+1]}_USDT"})
        buttons.append(row)
    buttons.append([{"text": "🔙 بازگشت", "callback_data": "main_menu"}])
    return {"inline_keyboard": buttons}

# --- تلگرام API ---
async def send_tg(chat_id, text, keyboard=None):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API_BASE}/sendMessage", 
                         json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard})

async def edit_tg(chat_id, msg_id, text, keyboard=None):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API_BASE}/editMessageText", 
                         json={"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard})

# --- وظایف دوره‌ای ---
async def handle_periodic_tasks():
    chats = get_all_chats()
    if not chats: return
    analysis = await build_analysis_message(DEFAULT_SYMBOL)
    message = f"⏰ <b>تحلیل خودکار اتریوم:</b>\n\n{analysis}"
    for cid in chats:
        try: await send_tg(cid, message, main_keyboard())
        except: pass

# --- مسیرهای FastAPI ---
@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "up", "version": APP_VERSION}

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    
    if "message" in data:
        msg = data["message"]
        chat_id = msg["chat"]["id"]
        if msg.get("text") == "/start":
            add_chat(chat_id)
            await send_tg(chat_id, f"بابی خوش آمدی! 🚀\nنسخه: {APP_VERSION}\nتحلیل خودکار ETH فعال شد.", main_keyboard())
            
    elif "callback_query" in data:
        cb = data["callback_query"]
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        cb_data = cb["data"]
        
        if cb_data == "start":
            add_chat(chat_id)
            await send_tg(chat_id, "✅ تحلیل خودکار فعال شد.")
        elif cb_data == "stop":
            remove_chat(chat_id)
            await send_tg(chat_id, "⏹ تحلیل خودکار متوقف شد.")
        elif cb_data == "list_coins":
            await edit_tg(chat_id, msg_id, "🪙 ارز مورد نظر را انتخاب کن:", coins_keyboard())
        elif cb_data == "main_menu":
            await edit_tg(chat_id, msg_id, "منوی اصلی:", main_keyboard())
        elif cb_data.startswith("analyze_"):
            symbol = cb_data.replace("analyze_", "")
            res = await build_analysis_message(symbol)
            await send_tg(chat_id, res, main_keyboard())
            
    return {"ok": True}

@app.on_event("startup")
async def startup_event():
    init_db()
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API_BASE}/setWebhook", json={"url": f"{RENDER_EXTERNAL_URL}/webhook"})
    
    if not scheduler.running:
        scheduler.add_job(handle_periodic_tasks, "interval", minutes=AUTO_SEND_INTERVAL_MINUTES)
        scheduler.start()
