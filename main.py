import os
import html
import math
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request, Response

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
GATEIO_API_BASE = "https://api.gateio.ws/api/v4"

SYMBOL = "ETH_USDT"

TIMEFRAMES = {
    "15m": "۱۵ دقیقه",
    "1h": "۱ ساعت",
    "1d": "۱ روز",
    "1w": "۱ هفته",
}

CANDLE_LIMIT = 260
AUTO_SEND_INTERVAL_MINUTES = 30

active_chats: set[int] = set()

app = FastAPI(title="ETH Analysis Telegram Bot")
scheduler = AsyncIOScheduler()


def now_utc_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def format_price(value: Optional[float]) -> str:
    if value is None:
        return "نامشخص"
    if value >= 100:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:,.8f}"


def format_percent(value: Optional[float]) -> str:
    if value is None:
        return "نامشخص"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def calculate_ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period

    for price in values[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def calculate_rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None

    gains: List[float] = []
    losses: List[float] = []

    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))

        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period

    if average_loss == 0:
        return 100.0

    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def calculate_macd(values: List[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(values) < 35:
        return None, None, None

    macd_line_values: List[float] = []

    for end_index in range(26, len(values) + 1):
        subset = values[:end_index]
        ema_12 = calculate_ema(subset, 12)
        ema_26 = calculate_ema(subset, 26)

        if ema_12 is not None and ema_26 is not None:
            macd_line_values.append(ema_12 - ema_26)

    if len(macd_line_values) < 9:
        return None, None, None

    macd_line = macd_line_values[-1]
    signal_line = calculate_ema(macd_line_values, 9)

    if signal_line is None:
        return macd_line, None, None

    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_support_resistance(highs: List[float], lows: List[float], closes: List[float]) -> Tuple[Optional[float], Optional[float]]:
    if len(closes) < 30:
        return None, None

    recent_highs = highs[-50:] if len(highs) >= 50 else highs
    recent_lows = lows[-50:] if len(lows) >= 50 else lows

    support = min(recent_lows)
    resistance = max(recent_highs)

    return support, resistance


def analyze_volume(volumes: List[float]) -> str:
    if len(volumes) < 21:
        return "نامشخص"

    current_volume = volumes[-1]
    average_volume = statistics.mean(volumes[-21:-1])

    if average_volume <= 0:
        return "نامشخص"

    ratio = current_volume / average_volume

    if ratio >= 1.5:
        return "بالا"
    if ratio <= 0.7:
        return "پایین"
    return "نرمال"


def get_trend_score(
    close_price: float,
    ema_20: Optional[float],
    ema_50: Optional[float],
    ema_200: Optional[float],
    rsi: Optional[float],
    macd_histogram: Optional[float],
    volume_status: str,
) -> Tuple[int, List[str]]:
    score = 0
    reasons: List[str] = []

    if ema_20 is not None and ema_50 is not None:
        if ema_20 > ema_50:
            score += 1
            reasons.append("EMA20 بالاتر از EMA50")
        elif ema_20 < ema_50:
            score -= 1
            reasons.append("EMA20 پایین‌تر از EMA50")

    if ema_50 is not None and ema_200 is not None:
        if ema_50 > ema_200:
            score += 1
            reasons.append("EMA50 بالاتر از EMA200")
        elif ema_50 < ema_200:
            score -= 1
            reasons.append("EMA50 پایین‌تر از EMA200")

    if ema_200 is not None:
        if close_price > ema_200:
            score += 1
            reasons.append("قیمت بالاتر از EMA200")
        elif close_price < ema_200:
            score -= 1
            reasons.append("قیمت پایین‌تر از EMA200")

    if rsi is not None:
        if 50 <= rsi <= 70:
            score += 1
            reasons.append("RSI در محدوده مثبت")
        elif 30 <= rsi < 45:
            score -= 1
            reasons.append("RSI در محدوده ضعیف")
        elif rsi > 75:
            score -= 1
            reasons.append("RSI اشباع خرید")
        elif rsi < 25:
            score += 1
            reasons.append("RSI اشباع فروش احتمالی")

    if macd_histogram is not None:
        if macd_histogram > 0:
            score += 1
            reasons.append("MACD مثبت")
        elif macd_histogram < 0:
            score -= 1
            reasons.append("MACD منفی")

    if volume_status == "بالا":
        if score > 0:
            score += 1
            reasons.append("حجم بالا در جهت مثبت")
        elif score < 0:
            score -= 1
            reasons.append("حجم بالا در جهت منفی")

    return score, reasons


def signal_from_score(score: int) -> Tuple[str, str]:
    if score >= 3:
        return "صعودی", "🟢"
    if score <= -3:
        return "نزولی", "🔴"
    return "خنثی", "🟡"


async def fetch_gateio_candles(interval: str) -> List[Dict[str, float]]:
    url = f"{GATEIO_API_BASE}/spot/candlesticks"
    params = {
        "currency_pair": SYMBOL,
        "interval": interval,
        "limit": CANDLE_LIMIT,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        raw_candles = response.json()

    candles: List[Dict[str, float]] = []

    for item in raw_candles:
        if not isinstance(item, list) or len(item) < 6:
            continue

        candle = {
            "timestamp": safe_float(item[0]),
            "volume": safe_float(item[1]),
            "close": safe_float(item[2]),
            "high": safe_float(item[3]),
            "low": safe_float(item[4]),
            "open": safe_float(item[5]),
        }
        candles.append(candle)

    candles.sort(key=lambda candle: candle["timestamp"])
    return candles


async def analyze_timeframe(interval: str, label: str) -> Dict[str, Any]:
    candles = await fetch_gateio_candles(interval)

    if len(candles) < 60:
        return {
            "interval": interval,
            "label": label,
            "error": "داده کافی از Gate.io دریافت نشد.",
        }

    closes = [candle["close"] for candle in candles]
    highs = [candle["high"] for candle in candles]
    lows = [candle["low"] for candle in candles]
    volumes = [candle["volume"] for candle in candles]

    close_price = closes[-1]
    previous_close = closes[-2] if len(closes) >= 2 else close_price
    price_change = ((close_price - previous_close) / previous_close) * 100 if previous_close else 0

    ema_20 = calculate_ema(closes, 20)
    ema_50 = calculate_ema(closes, 50)
    ema_200 = calculate_ema(closes, 200)
    rsi = calculate_rsi(closes, 14)
    macd_line, signal_line, macd_histogram = calculate_macd(closes)
    support, resistance = calculate_support_resistance(highs, lows, closes)
    volume_status = analyze_volume(volumes)

    score, reasons = get_trend_score(
        close_price=close_price,
        ema_20=ema_20,
        ema_50=ema_50,
        ema_200=ema_200,
        rsi=rsi,
        macd_histogram=macd_histogram,
        volume_status=volume_status,
    )

    signal, emoji = signal_from_score(score)

    return {
        "interval": interval,
        "label": label,
        "close_price": close_price,
        "price_change": price_change,
        "ema_20": ema_20,
        "ema_50": ema_50,
        "ema_200": ema_200,
        "rsi": rsi,
        "macd_line": macd_line,
        "signal_line": signal_line,
        "macd_histogram": macd_histogram,
        "support": support,
        "resistance": resistance,
        "volume_status": volume_status,
        "score": score,
        "signal": signal,
        "emoji": emoji,
        "reasons": reasons[:3],
        "error": None,
    }


def build_timeframe_section(result: Dict[str, Any]) -> str:
    label = html.escape(result["label"])
    interval = html.escape(result["interval"])

    if result.get("error"):
        return (
            f"⏱ <b>{label} ({interval})</b>\n"
            f"⚠️ {html.escape(result['error'])}\n"
        )

    reasons = result.get("reasons") or []
    reasons_text = "، ".join(reasons) if reasons else "بدون دلیل قوی"

    return (
        f"⏱ <b>{label} ({interval})</b>\n"
        f"{result['emoji']} سیگنال: <b>{html.escape(result['signal'])}</b> | امتیاز: <b>{result['score']}</b>\n"
        f"💰 قیمت: <code>{format_price(result['close_price'])}</code> USDT | تغییر کندل آخر: <code>{format_percent(result['price_change'])}</code>\n"
        f"📈 EMA20: <code>{format_price(result['ema_20'])}</code> | EMA50: <code>{format_price(result['ema_50'])}</code> | EMA200: <code>{format_price(result['ema_200'])}</code>\n"
        f"🧭 RSI: <code>{result['rsi']:.2f}</code> | MACD Hist: <code>{result['macd_histogram']:.4f}</code>\n"
        f"🧱 حمایت: <code>{format_price(result['support'])}</code> | مقاومت: <code>{format_price(result['resistance'])}</code>\n"
        f"📊 حجم: <b>{html.escape(result['volume_status'])}</b>\n"
        f"🔎 نکته: {html.escape(reasons_text)}\n"
    )


def build_overall_summary(results: List[Dict[str, Any]]) -> str:
    valid_results = [result for result in results if not result.get("error")]

    if not valid_results:
        return "⚠️ جمع‌بندی کلی: داده کافی برای تحلیل وجود ندارد."

    total_score = sum(result["score"] for result in valid_results)

    bullish_count = sum(1 for result in valid_results if result["signal"] == "صعودی")
    bearish_count = sum(1 for result in valid_results if result["signal"] == "نزولی")
    neutral_count = sum(1 for result in valid_results if result["signal"] == "خنثی")

    if total_score >= 6:
        overall_signal = "صعودی قوی"
        emoji = "🟢"
    elif total_score >= 3:
        overall_signal = "مایل به صعود"
        emoji = "🟢"
    elif total_score <= -6:
        overall_signal = "نزولی قوی"
        emoji = "🔴"
    elif total_score <= -3:
        overall_signal = "مایل به نزول"
        emoji = "🔴"
    else:
        overall_signal = "خنثی / بدون برتری واضح"
        emoji = "🟡"

    return (
        f"{emoji} <b>جمع‌بندی کلی:</b> {overall_signal}\n"
        f"امتیاز کل: <b>{total_score}</b>\n"
        f"صعودی: <b>{bullish_count}</b> | نزولی: <b>{bearish_count}</b> | خنثی: <b>{neutral_count}</b>"
    )


async def build_analysis_message() -> str:
    results: List[Dict[str, Any]] = []

    for interval, label in TIMEFRAMES.items():
        try:
            result = await analyze_timeframe(interval, label)
        except Exception as error:
            result = {
                "interval": interval,
                "label": label,
                "error": f"خطا در دریافت یا تحلیل داده: {error}",
            }

        results.append(result)

    sections = [build_timeframe_section(result) for result in results]
    overall_summary = build_overall_summary(results)

    disclaimer = (
        "⚠️ <b>هشدار:</b> این تحلیل فقط آموزشی و الگوریتمی است و توصیه مالی محسوب نمی‌شود."
    )

    return (
        f"🤖 <b>تحلیل خودکار اتریوم</b>\n"
        f"نماد: <b>{SYMBOL}</b>\n"
        f"منبع داده: <b>Gate.io</b>\n"
        f"زمان: <code>{now_utc_text()}</code>\n\n"
        + "\n".join(sections)
        + f"\n{overall_summary}\n\n"
        + disclaimer
    )


def main_keyboard() -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "▶️ Start", "callback_data": "start"},
                {"text": "⏹ Stop", "callback_data": "stop"},
            ],
            [
                {"text": "📊 تحلیل الان", "callback_data": "analysis_now"},
                {"text": "ℹ️ Help", "callback_data": "help"},
            ],
        ]
    }


async def telegram_request(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{TELEGRAM_API_BASE}/{method}"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


async def send_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    await telegram_request("sendMessage", payload)


async def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    await telegram_request(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": False,
        },
    )


async def set_webhook() -> None:
    if not BOT_TOKEN:
        print("BOT_TOKEN is missing.")
        return

    if not RENDER_EXTERNAL_URL:
        print("RENDER_EXTERNAL_URL is missing.")
        return

    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{TELEGRAM_API_BASE}/setWebhook",
            json={
                "url": webhook_url,
                "drop_pending_updates": True,
            },
        )
        print("setWebhook:", response.status_code, response.text)


async def send_periodic_analysis() -> None:
    if not active_chats:
        print("No active chats for periodic analysis.")
        return

    print(f"Sending periodic analysis to {len(active_chats)} chats.")

    message = await build_analysis_message()
    failed_chat_ids: List[int] = []

    for chat_id in list(active_chats):
        try:
            await send_message(chat_id, message, main_keyboard())
        except Exception as error:
            print(f"Failed to send message to chat {chat_id}: {error}")
            failed_chat_ids.append(chat_id)

    for chat_id in failed_chat_ids:
        active_chats.discard(chat_id)


async def handle_start(chat_id: int) -> None:
    active_chats.add(chat_id)

    text = (
        "سلام Bobby یا دوست عزیز 👋\n\n"
        "✅ دریافت تحلیل خودکار اتریوم برای این چت فعال شد.\n"
        f"از این به بعد هر <b>{AUTO_SEND_INTERVAL_MINUTES} دقیقه</b> یک تحلیل کامل برای تایم‌فریم‌های "
        "<b>15m، 1h، 1d، 1w</b> ارسال می‌شود.\n\n"
        "برای دریافت تحلیل فوری، روی دکمه <b>تحلیل الان</b> بزن."
    )

    await send_message(chat_id, text, main_keyboard())

    try:
        analysis_message = await build_analysis_message()
        await send_message(chat_id, analysis_message, main_keyboard())
    except Exception as error:
        await send_message(chat_id, f"⚠️ خطا در ساخت تحلیل اولیه: <code>{html.escape(str(error))}</code>", main_keyboard())


async def handle_stop(chat_id: int) -> None:
    active_chats.discard(chat_id)

    text = (
        "⏹ ارسال خودکار تحلیل برای این چت متوقف شد.\n\n"
        "هر زمان خواستی دوباره فعال شود، دستور /start را بفرست یا روی دکمه Start بزن."
    )

    await send_message(chat_id, text, main_keyboard())


async def handle_help(chat_id: int) -> None:
    text = (
        "ℹ️ <b>راهنمای ربات تحلیل ETH</b>\n\n"
        "این ربات اتریوم را روی جفت‌ارز <b>ETH_USDT</b> از Gate.io تحلیل می‌کند.\n\n"
        "دستورها:\n"
        "▶️ /start — شروع دریافت تحلیل خودکار هر ۳۰ دقیقه\n"
        "⏹ /stop — توقف دریافت تحلیل خودکار\n"
        "ℹ️ /help — نمایش راهنما\n\n"
        "اندیکاتورها:\n"
        "• EMA 20/50/200\n"
        "• RSI\n"
        "• MACD\n"
        "• حمایت و مقاومت ساده\n"
        "• وضعیت حجم معاملات\n\n"
        "⚠️ این تحلیل توصیه مالی نیست و فقط خروجی الگوریتمی آموزشی است."
    )

    await send_message(chat_id, text, main_keyboard())


async def handle_analysis_now(chat_id: int) -> None:
    await send_message(chat_id, "⏳ در حال دریافت داده از Gate.io و تحلیل ETH...", main_keyboard())

    try:
        message = await build_analysis_message()
        await send_message(chat_id, message, main_keyboard())
    except Exception as error:
        await send_message(chat_id, f"⚠️ خطا در تحلیل: <code>{html.escape(str(error))}</code>", main_keyboard())


async def handle_text_message(message: Dict[str, Any]) -> None:
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if chat_id is None:
        return

    if text.startswith("/start"):
        await handle_start(chat_id)
    elif text.startswith("/stop"):
        await handle_stop(chat_id)
    elif text.startswith("/help"):
        await handle_help(chat_id)
    elif text.startswith("/analysis") or text in {"تحلیل", "تحلیل الان"}:
        await handle_analysis_now(chat_id)
    else:
        await send_message(
            chat_id,
            "برای شروع تحلیل خودکار /start را بفرست.\nبرای راهنما /help را بفرست.",
            main_keyboard(),
        )


async def handle_callback_query(callback_query: Dict[str, Any]) -> None:
    callback_query_id = callback_query.get("id")
    data = callback_query.get("data")
    message = callback_query.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    if callback_query_id:
        await answer_callback_query(callback_query_id, "درخواست دریافت شد.")

    if chat_id is None:
        return

    if data == "start":
        await handle_start(chat_id)
    elif data == "stop":
        await handle_stop(chat_id)
    elif data == "help":
        await handle_help(chat_id)
    elif data == "analysis_now":
        await handle_analysis_now(chat_id)
    else:
        await send_message(chat_id, "درخواست نامعتبر است.", main_keyboard())


@app.on_event("startup")
async def startup_event() -> None:
    await set_webhook()

    if not scheduler.running:
        scheduler.add_job(
            send_periodic_analysis,
            "interval",
            minutes=AUTO_SEND_INTERVAL_MINUTES,
            id="periodic_eth_analysis",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        print("Scheduler started.")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if scheduler.running:
        scheduler.shutdown()
        print("Scheduler stopped.")


# ==================== UptimeRobot Support ====================

@app.get("/")
@app.head("/")
async def root() -> Dict[str, Any]:
    return {
        "status": "up",
        "bot": "ETH Gate.io Analysis Bot",
        "symbol": SYMBOL,
        "active_chats": len(active_chats),
        "time": now_utc_text(),
    }


@app.get("/health")
@app.head("/health")
async def health():
    """پاسخ مناسب برای UptimeRobot (HEAD + GET)"""
    return Response(
        content="OK",
        status_code=200,
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


@app.get("/ping")
@app.head("/ping")
async def ping():
    """مسیر خیلی سبک"""
    return Response(status_code=200)


# =====================================================================

@app.post("/webhook")
async def telegram_webhook(request: Request) -> Dict[str, bool]:
    update = await request.json()

    try:
        if "message" in update:
            await handle_text_message(update["message"])
        elif "callback_query" in update:
            await handle_callback_query(update["callback_query"])
    except Exception as error:
        print(f"Webhook handling error: {error}")

    return {"ok": True}
