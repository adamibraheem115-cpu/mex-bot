import asyncio
import aiohttp
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone, timedelta
from telegram import Bot

# ==========================
# إعدادات البوت
# ==========================
BOT_TOKEN = "8097310973:AAE68aYlgPb1onGzvWDk4GbYWlPJBNQOzJI"
CHAT_ID = "8137529944"

bot = Bot(token=BOT_TOKEN)

# ==========================
# إعدادات السوق
# ==========================
BASE_URL       = "https://api.mexc.com/api/v3"
TIMEFRAME      = "4h"          # فريم 4 ساعات
VOLUME_LIMIT   = 500_000       # حد أدنى للسيولة لتمرير الرمز للتصفية الأولية
MAX_SYMBOLS    = 300           # أقصى عدد عملات نفحصها في الدورة
LOOKBACK_LOW   = 90            # عدد الشموع (≈ 15 يوم) لحساب "القاع"
RSI_LO, RSI_HI = 45, 70        # شروط RSI لسيولة المال الذكي

# لتفادي تكرار الإشعارات
sent_alerts = set()

# ==========================
# أدوات مساعدة
# ==========================
def now_oman():
    return (datetime.now(timezone.utc) + timedelta(hours=4)).strftime("%d-%m-%Y %H:%M")

def mexc_link(symbol: str) -> str:
    return f"https://www.mexc.com/exchange/{symbol.replace('USDT','_USDT')}"

async def send_message(text: str):
    try:
        # تأخير بسيط لتفادي ضغط اتصال تيليجرام
        await asyncio.sleep(0.4)
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)
    except Exception as e:
        print("❌ خطأ في إرسال الرسالة:", e)

# ==========================
# تحليل وإرسال “سيولة المال الذكي”
# ==========================
async def analyze_symbol(session: aiohttp.ClientSession, symbol: str):
    try:
        # شموع 4h
        k_url = f"{BASE_URL}/klines?symbol={symbol}&interval={TIMEFRAME}&limit=200"
        async with session.get(k_url, timeout=12) as r:
            kdata = await r.json()

        if not isinstance(kdata, list) or len(kdata) < max(LOOKBACK_LOW, 200//2):
            return

        # أعمدة (8 أو 12)
        cols8  = ['t','o','h','l','c','v','_','__']
        cols12 = ['t','o','h','l','c','v','_','__','___','____','_____','______']
        df = pd.DataFrame(kdata, columns=cols8 if len(kdata[0]) == 8 else cols12)

        # تحويل أرقام
        df["c"] = df["c"].astype(float)
        df["l"] = df["l"].astype(float)

        # مؤشرات
        df["ema200"] = ta.ema(df["c"], length=200)
        df["rsi"]    = ta.rsi(df["c"], length=14)

        last_close = float(df["c"].iloc[-1])
        ema200     = float(df["ema200"].iloc[-1])
        rsi        = float(df["rsi"].iloc[-1])

        # قاع آخر 15 يوم تقريبًا (90 شمعة 4h)
        recent = df.tail(LOOKBACK_LOW)
        recent_low = float(recent["l"].min())
        if recent_low <= 0:
            return
        rebound_pct = (last_close - recent_low) / recent_low * 100.0

        # بيانات 24 ساعة (للحجم)
        t_url = f"{BASE_URL}/ticker/24hr?symbol={symbol}"
        async with session.get(t_url, timeout=10) as r2:
            t = await r2.json()
        if not isinstance(t, dict):
            return

        quote_volume = float(t.get("quoteVolume", 0.0) or 0.0)  # USDT تقريبًا
        # تصنيف السيولة الداخلة
        if quote_volume >= 5_000_000:
            liq_tag = "💧 سيولة قوية"
        elif quote_volume >= 1_500_000:
            liq_tag = "💧 سيولة متوسطة"
        else:
            liq_tag = "💧 سيولة منخفضة"

        # منطق “سيولة المال الذكي”: اتجاه صاعد + RSI صحي
        if not (last_close > ema200 and RSI_LO <= rsi <= RSI_HI):
            return

        # مفتاح لمنع التكرار
        alert_key = f"{symbol}-smartinflow"
        if alert_key in sent_alerts:
            return
        sent_alerts.add(alert_key)

        # رسالة منسّقة
        msg = (
            "🚀 <b>دخول سيولة مال ذكي</b>\n\n"
            f"💎 <b>العملة:</b> {symbol}\n"
            f"💵 <b>السعر الحالي:</b> {last_close:.8f} USDT\n"
            f"📉 <b>EMA200:</b> {ema200:.6f}\n"
            f"📈 <b>RSI:</b> {rsi:.2f}\n"
            f"📊 <b>الارتداد من القاع:</b> +{rebound_pct:.2f}% (آخر ~15 يوم)\n"
            f"💸 <b>حجم التداول 24 ساعة:</b> {quote_volume:,.0f} USDT\n"
            f"{liq_tag}\n"
            "💹 <b>نوع التداول:</b> سبوت\n"
            f"⏰ <b>الوقت:</b> {now_oman()}\n\n"
            f"🔗 <a href='{mexc_link(symbol)}'>فتح الشارت على MEXC</a>"
        )
        await send_message(msg)

    except Exception as e:
        print(f"⚠️ خطأ في تحليل {symbol}: {e}")

# ==========================
# دورة التحليل الكاملة
# ==========================
async def run_analysis():
    async with aiohttp.ClientSession() as session:
        # قائمة السوق 24 ساعة
        async with session.get(f"{BASE_URL}/ticker/24hr", timeout=15) as resp:
            tickers = await resp.json()

        # نختار فقط أزواج USDT ذات سيولة كافية
        symbols = [
            t["symbol"] for t in tickers
            if t.get("symbol","").endswith("USDT")
            and float(t.get("quoteVolume", 0) or 0) >= VOLUME_LIMIT
        ]
        symbols = symbols[:MAX_SYMBOLS]

        print(f"🔍 يتم فحص {len(symbols)} عملة ذات سيولة كافية...")
        sem = asyncio.Semaphore(10)  # تحديد المهام المتزامنة لتفادي ضغط الشبكة

        async def safe(sym):
            async with sem:
                await analyze_symbol(session, sym)

        await asyncio.gather(*[safe(s) for s in symbols])
        print("✅ التحليل اكتمل!")

# ==========================
# حلقة التشغيل كل ساعة
# ==========================
async def main_loop():
    while True:
        await run_analysis()
        print("⏳ سيتم التحديث بعد ساعة...")
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main_loop())
