import os
import sqlite3
import httpx
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request, Response
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- تنظیمات ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
GATEIO_API_BASE = "https://api.gateio.ws/api/v4"
DB_PATH = "[/mnt/data/bot_data.db"](https://storage.gapgpt.app/media/code_interpreter/41f3cde9-4b72-49e4-8434-f3dc06b45508/bot_data.db%22) if os.path.exists("/mnt/data") else "bot_data.db"
AUTO_SEND_INTERVAL_MINUTES = 30

app = FastAPI()
scheduler = AsyncIOScheduler()

# --- مدیریت دیتابیس SQLite ---
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
    with sqlite3.connect(DB_PATH) as conn:
        return [row[0] for row in conn.execute("SELECT chat_id FROM active_chats").fetchall()]

# --- توابع تحلیل تکنیکال (نسخه بهینه شده بابی) ---
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

async def fetch_candles(symbol: str, interval: str = "1h", limit: int = 200):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            url = f"{GATEIO_API_BASE}/spot/candlesticks"
            params = {"currency_pair": symbol, "interval": interval, "limit": limit}
            resp = await client.get(url, params=params)
            return resp.json()
    except: return []

async def analyze_coin(symbol: str) -> str:
    candles = await fetch_candles(symbol)
    if not candles or len(candles) < 50: return f"❌ خطا در دریافت داده برای {symbol}"
    
    closes = [float(c[2]) for c in candles]
    current_price = closes[-1]
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    rsi = calculate_rsi(closes, 14)
    
    trend = "صعودی 🟢" if ema20 and ema20 > ema50 else "نزولی 🔴"
    rsi_status = "اشباع خرید ⚠️" if rsi and rsi > 70 else "اشباع فروش ✅" if rsi and rsi < 30 else "معمولی"

    text = (
        f"📊 <b>تحلیل لحظه‌ای {symbol}</b>\n\n"
        f"💵 قیمت: <code>{current_price:,}</code>\n"
        f"📈 روند: {trend}\n"
        f"indicator RSI: <code>{rsi:.2f}</code> ({rsi_status})\n"
        f"🕒 زمان: {datetime.now().strftime('%H:%M')}\n\n"
        f"📌 <i>تحلیل دوره‌ای فقط برای ETH ارسال می‌شود.</i>"
    )
    return text

# --- تلگرام ---
def get_main_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🔄 انتخاب ارزهای دیگر", "callback_data": "list_coins"}],
            [{"text": "📊 تحلیل الان ETH", "callback_data": "analyze_ETH_USDT"}],
            [{"text": "❌ توقف ربات", "callback_data": "stop_bot"}]
        ]
    }

def get_coins_keyboard():
    coins = ["BTC", "SOL", "TON", "ARB", "POL", "BNB", "XRP", "ADA", "AVAX", "LINK"]
    buttons = []
    for i in range(0, len(coins), 2):
        row = [{"text": coins[i], "callback_data": f"analyze_{coins[i]}_USDT"}]
        if i+1 < len(coins): row.append({"text": coins[i+1], "callback_data": f"analyze_{coins[i+1]}_USDT"})
        buttons.append(row)
    buttons.append([{"text": "🔙 بازگشت", "callback_data": "main_menu"}])
    return {"inline_keyboard": buttons}

async def send_tg(chat_id, text, keyboard=None):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API_BASE}/sendMessage", 
                         json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard})

async def edit_tg(chat_id, msg_id, text, keyboard=None):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API_BASE}/editMessageText", 
                         json={"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard})

# --- تسک دوره ای ---
async def send_periodic_analysis():
    chats = get_all_chats()
    if not chats: return
    analysis = await analyze_coin("ETH_USDT")
    for cid in chats:
        try: await send_tg(cid, "⏰ <b>تحلیل خودکار ۳۰ دقیقه‌ای:</b>\n\n" + analysis, get_main_keyboard())
        except: pass

# --- مسیرها (Endpoints) ---
@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    
    if "message" in data:
        msg = data["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "")
        
        if text == "/start":
            add_chat(chat_id)
            await send_tg(chat_id, "سلام بابی! ربات تحلیلگر فعال شد. اتریوم هر ۳۰ دقیقه تحلیل می‌شود.", get_main_keyboard())
            
    elif "callback_query" in data:
        cb = data["callback_query"]
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]
        cb_data = cb["data"]
        
        if cb_data == "list_coins":
            await edit_tg(chat_id, msg_id, "ارز مورد نظر را برای تحلیل انتخاب کنید:", get_coins_keyboard())
        elif cb_data == "main_menu":
            await edit_tg(chat_id, msg_id, "منوی اصلی:", get_main_keyboard())
        elif cb_data.startswith("analyze_"):
            symbol = cb_data.replace("analyze_", "")
            res = await analyze_coin(symbol)
            await send_tg(chat_id, res, get_main_keyboard())
        elif cb_data == "stop_bot":
            remove_chat(chat_id)
            await edit_tg(chat_id, msg_id, "ربات برای شما متوقف شد. برای فعال‌سازی مجدد /start را بزنید.")
            
    return {"ok": True}

@app.on_event("startup")
async def startup_event():
    init_db()
    # تنظیم وبهوک
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API_BASE}/setWebhook", json={"url": f"{RENDER_EXTERNAL_URL}/webhook"})
    # استارت اسکولر
    if not scheduler.running:
        scheduler.add_job(send_periodic_analysis, "interval", minutes=AUTO_SEND_INTERVAL_MINUTES)
        scheduler.start()
