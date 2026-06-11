import os
import sqlite3
import httpx
from fastapi import FastAPI, Request
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from typing import Dict, Any, List

# تنظیمات اولیه
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
DB_PATH = "bot_data.db"
AUTO_SEND_INTERVAL_MINUTES = 30

app = FastAPI(title="Crypto Analysis Bot")
scheduler = AsyncIOScheduler()

# --- دیتابیس ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS active_chats (chat_id INTEGER PRIMARY KEY)")
        conn.commit()

def add_chat(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO active_chats (chat_id) VALUES (?)", (chat_id,))

def remove_chat(chat_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM active_chats WHERE chat_id = ?", (chat_id,))

def get_all_chats():
    with sqlite3.connect(DB_PATH) as conn:
        return [row[0] for row in conn.execute("SELECT chat_id FROM active_chats").fetchall()]

# --- توابع کیبورد ---
def main_keyboard():
    # لیست ارزهای دیگر
    coins = ["BTC", "SOL", "TON", "ARB", "POL", "BNB", "XRP", "ADA", "AVAX", "LINK"]
    inline_keyboard = [
        [{"text": "📊 تحلیل لحظه‌ای ETH", "callback_data": "analysis_ETH_USDT"}],
        [{"text": "🔄 ارزهای دیگر", "callback_data": "show_coins"}]
    ]
    return {"inline_keyboard": inline_keyboard}

def coins_keyboard():
    coins = ["BTC", "SOL", "TON", "ARB", "POL", "BNB", "XRP", "ADA", "AVAX", "LINK"]
    buttons = []
    # چیدمان ۲ تایی برای تمیزتر شدن
    for i in range(0, len(coins), 2):
        row = [{"text": coins[i], "callback_data": f"analysis_{coins[i]}_USDT"}]
        if i + 1 < len(coins):
            row.append({"text": coins[i+1], "callback_data": f"analysis_{coins[i+1]}_USDT"})
        buttons.append(row)
    buttons.append([{"text": "🔙 بازگشت", "callback_data": "back_to_main"}])
    return {"inline_keyboard": buttons}

# --- منطق ارسال پیام ---
async def send_message(chat_id, text, reply_markup=None):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API_BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text, "reply_markup": reply_markup, "parse_mode": "HTML"}
        )

# --- منطق اصلی تحلیل (جایگزین تابع اصلی در کد قبلی) ---
async def perform_analysis(symbol: str):
    # اینجا همان منطق fetch و محاسبات قبلی شما قرار می‌گیرد
    # این تابع باید بر اساس نماد (symbol) دیتای Gate.io را بگیرد
    return f"تحلیل تکنیکال {symbol} انجام شد. (در اینجا دیتای واقعی قرار می‌گیرد)"

# --- هندلرها ---
async def handle_callback_query(callback_query: Dict[str, Any]):
    chat_id = callback_query["message"]["chat"]["id"]
    data = callback_query["data"]
    
    if data == "show_coins":
        # ویرایش پیام قبلی با کیبورد ارزها
        await edit_message(chat_id, callback_query["message"]["message_id"], "یک ارز را برای تحلیل انتخاب کن:", coins_keyboard())
    
    elif data == "back_to_main":
        await edit_message(chat_id, callback_query["message"]["message_id"], "ربات تحلیلگر در خدمت شماست:", main_keyboard())
        
    elif data.startswith("analysis_"):
        symbol = data.split("_")[1] + "_" + data.split("_")[2]
        analysis_text = await perform_analysis(symbol)
        await send_message(chat_id, analysis_text)

async def edit_message(chat_id, message_id, text, reply_markup):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API_BASE}/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": "HTML"
            }
        )

# --- تسک خودکار ---
async def send_periodic_analysis():
    chats = get_all_chats()
    if not chats: return
    message = await perform_analysis("ETH_USDT")
    for chat_id in chats:
        try:
            await send_message(chat_id, message)
        except:
            pass

# --- استارت‌آپ ---
@app.on_event("startup")
async def startup():
    init_db()
    if not scheduler.running:
        scheduler.add_job(send_periodic_analysis, "interval", minutes=AUTO_SEND_INTERVAL_MINUTES)
        scheduler.start()

@app.post("/webhook")
async def webhook(request: Request):
    update = await request.json()
    if "callback_query" in update:
        await handle_callback_query(update["callback_query"])
    elif "message" in update and "text" in update["message"]:
        text = update["message"]["text"]
        chat_id = update["message"]["chat"]["id"]
        if text == "/start":
            add_chat(chat_id)
            await send_message(chat_id, "خوش آمدید! تحلیل‌های ETH هر ۳۰ دقیقه ارسال می‌شود.", main_keyboard())
        elif text == "/stop":
            remove_chat(chat_id)
            await send_message(chat_id, "ارسال تحلیل متوقف شد.")
    return {"ok": True}
