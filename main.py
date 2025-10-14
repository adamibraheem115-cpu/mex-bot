import asyncio
import aiohttp
import pandas as pd
import pandas_ta as ta
from telegram import Bot

# ==========================
# إعدادات البوت والتوكن
# ==========================
BOT_TOKEN = "8097310973:AAE68aYlgPb1onGzvWDk4GbYWlPJBNQOzJI"
CHAT_ID = "8137529944"

bot = Bot(token=BOT_TOKEN)

# ==========================
# إعدادات MEXC API
# ==========================
BASE_URL = "https://api.mexc.com/api/v3"
TIMEFRAME = "4h"  # فريم 4 ساعات

# ==========================
# دالة إرسال تنبيهات تليجرام
# ==========================
async def send_telegram_message(message):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="HTML")
    except Exception as e:
        print("❌ خطأ في إرسال الرسالة:", e)

# ==========================
# تحليل العملة
# ==========================
async def analyze_symbol(session, symbol):
    try:
        url = f"{BASE_URL}/klines?symbol={symbol}&interval={TIMEFRAME}&limit=200"
        async with session.get(url) as resp:
            data = await resp.json()

        if not isinstance(data, list) or len(data) < 50:
            return

        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', '_', '__', '___', '____', '_____', '______'
        ])
        df["close"] = df["close"].astype(float)

        # حساب EMA200 و RSI
        df["EMA200"] = ta.ema(df["close"], length=200)
        df["RSI"] = ta.rsi(df["close"], length=14)

        last_close = df["close"].iloc[-1]
        last_ema = df["EMA200"].iloc[-1]
        last_rsi = df["RSI"].iloc[-1]

        # تحديد قوة الفرصة
        strength = ""
        if last_close > last_ema and last_rsi < 70:
            strength = "🚀 قوية"
        elif last_close > last_ema and 70 <= last_rsi <= 80:
            strength = "⚡ متوسطة"
        elif last_close < last_ema and last_rsi < 30:
            strength = "📉 ضعيفة"
        else:
            return

        # رابط العملة على منصة MEXC
        coin_link = f"https://www.mexc.com/exchange/{symbol.replace('USDT','_USDT')}"

        message = (
            f"📊 <b>فرصة تداول جديدة</b>\n"
            f"💰 العملة: <b>{symbol}</b>\n"
            f"🕒 الإطار الزمني: {TIMEFRAME}\n"
            f"📈 القوة: {strength}\n"
            f"💵 السعر الحالي: {last_close:.6f}\n"
            f"🔗 <a href='{coin_link}'>رابط العملة على MEXC</a>\n"
            f"💹 نوع التداول: سبوت"
        )

        await send_telegram_message(message)

    except Exception as e:
        print(f"⚠️ خطأ في تحليل {symbol}:", e)

# ==========================
# تشغيل التحليل العام
# ==========================
async def run_analysis():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/ticker/24hr") as resp:
            tickers = await resp.json()

        # فحص فقط أزواج USDT ذات حجم تداول كبير
        symbols = [
            t["symbol"] for t in tickers
            if t["symbol"].endswith("USDT") and float(t["quoteVolume"]) > 500000
        ]

        print(f"🔍 يتم فحص {len(symbols)} عملة...")
        tasks = [analyze_symbol(session, s) for s in symbols[:400]]
        await asyncio.gather(*tasks)
        print("✅ التحليل اكتمل!")

# ==========================
# التشغيل التلقائي كل ساعة
# ==========================
async def main_loop():
    while True:
        await run_analysis()
        print("⏳ سيتم التحديث بعد ساعة...")
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main_loop())
