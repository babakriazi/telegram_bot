import os
import requests
from fastapi import FastAPI, Request

app = FastAPI()

# خواندن مقادیر از تنظیمات رندر (Environment Variables)
TOKEN = os.environ.get("BOT_TOKEN")
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL") # رندر خودش اینو میده

@app.on_event("startup")
def startup_event():
    """این بخش وقتی اپلیکیشن بالا میاد، وبهوک رو به تلگرام ست می‌کنه"""
    if TOKEN and RENDER_URL:
        webhook_url = f"{RENDER_URL}/webhook"
        url = f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={webhook_url}"
        res = requests.get(url)
        print(f"Setting webhook to {webhook_url}: {res.json()}")

@app.get("/")
def read_root():
    return {"status": "Bot is running!"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """اینجا جاییه که تلگرام پیام‌ها رو می‌فرسته"""
    data = await request.json()
    
    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        # منطق ساده ربات
        if text == "/start":
            reply = "سلام Bobby! این ربات با FastAPI روی Render ران شده. ✅"
        else:
            reply = f"پیام تو دریافت شد: {text}"

        # ارسال پاسخ به تلگرام
        send_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": reply}
        requests.post(send_url, json=payload)

    return {"ok": True}
