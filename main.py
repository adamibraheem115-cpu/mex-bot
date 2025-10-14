import asyncio
import aiohttp
import pandas as pd
import pandas_ta as ta
from telegram import Bot

# ==========================
# إعدادات البوت
# ==========================
BOT_TOKEN = "8097310973:AAE68aYlgPb1onGzvWDk4GbYWlPJBNQOzJI"
CHAT_ID = "8137529944"

bot = Bot(token=BOT_TOKEN)

# ==========================
# إعدادات منصة MEXC
# ==========================
BASE_URL = "https://api.mexc.com/api/v3"
TIMEFRAME = "4h"
VOLUME_LIMIT = 500000  # حد أدنى للسيولة

# حفظ العملات اللي تم تنبيهها (لتجنب التكرار)
sent_alerts = set()

# ==========================
# إرسال رسالة تليجرام
# ==========================
async def send_telegram_message(message):
    try:
        await asyncio.sleep(0.5)  # تأخير بسيط بين الرسائل لتجنب الحظر
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")
    except Exception as e:
        print("❌ خطأ في إرسال الرسالة:", e)

# ==========================
# تحليل عملة معينة
# ==========================
async def analyze_symbol(session, symbol):
    try:
        url = f"{BASE_URL}/klines?symbol={symbol}&interval={TIMEFRAME}&limit=200"
        async with session.get(url) as resp:
            data = await resp.json()

        if not isinstance(data, list) or len(data) < 50:
            return

        # ✅ معالجة اختلاف الأعمدة (8 أو 12)
        columns_8 = ['timestamp', 'open', 'high', 'low', 'close', 'volume', '_', '__']
        columns_12 = ['timestamp', 'open', 'high', 'low', 'close', 'volume', '_', '__', '___', '____', '_____', '______']
        df = pd.DataFrame(data, columns=columns_8 if len(data[0]) == 8 else columns_12)

        df["close"] = df["close"].astype(float)
        df["EMA200"] = ta.ema(df["close"], length=200)
        df["RSI"] = ta.rsi(df["close"], length=14)

        last_close = df["close"].iloc[-1]
        last_ema = df["EMA200"].iloc[-1]
        last_rsi = df["RSI"].iloc[-1]

        # ==========================
        # تحديد نوع الفرصة
        # ==========================
        strength = None
        if last_close > last_ema and last_rsi < 70:
            strength = "🚀 قوية"
        elif last_close > last_ema and 70 <= last_rsi <= 80:
            strength = "⚡ متوسطة"
        elif last_close < last_ema and last_rsi < 30:
            strength = "📉 ضعيفة"

        if not strength:
            return

        # لو تم تنبيه العملة سابقاً بنفس القوة → لا تعيد التنبيه
        alert_key = f"{symbol}-{strength}"
        if alert_key in sent_alerts:
            return
        sent_alerts.add(alert_key)

        # رابط منصة MEXC
        coin_link = f"https://www.mexc.com/exchange/{symbol.replace('USDT', '_USDT')}"

        # ==========================
        # إنشاء الرسالة
        # ==========================
        message = (
            f"📊 <b>فرصة تداول جديدة</b>\n"
            f"💰 <b>العملة:</b> {symbol}\n"
            f"🕒 <b>الفريم:</b> {TIMEFRAME}\n"
            f"📈 <b>القوة:</b> {strength}\n"
            f"💵 <b>السعر الحالي:</b> {last_close:.6f}\n"
            f"📊 <b>RSI:</b> {last_rsi:.2f}\n"
            f"📏 <b>EMA200:</b> {last_ema:.6f}\n"
            f"🔗 <a href='{coin_link}'>رابط العملة على MEXC</a>\n"
            f"💹 <b>نوع التداول:</b> سبوت"
        )

        await send_telegram_message(message)

    except Exception as e:
        print(f"⚠️ خطأ في تحليل {symbol}: {e}")

# ==========================
# تشغيل التحليل العام
# ==========================
async def run_analysis():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/ticker/24hr") as resp:
            tickers = await resp.json()

        symbols = [
            t["symbol"]
            for t in tickers
            if t["symbol"].endswith("USDT") and float(t["quoteVolume"]) > VOLUME_LIMIT
        ]

        print(f"🔍 يتم فحص {len(symbols)} عملة ذات سيولة قوية...")
        semaphore = asyncio.Semaphore(10)  # لتحديد عدد المهام المتزامنة
        async def safe_analyze(symbol):
            async with semaphore:
                await analyze_symbol(session, symbol)

        tasks = [safe_analyze(s) for s in symbols[:400]]
        await asyncio.gather(*tasks)
        print("✅ التحليل اكتمل!")

# ==========================
# التشغيل كل ساعة
# ==========================
async def main_loop():
    while True:
        await run_analysis()
        print("⏳ سيتم التحديث بعد ساعة...")
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main_loop())
