
import asyncio, aiohttp, pandas as pd, pandas_ta as ta, nest_asyncio
from telegram import Bot
from datetime import datetime
import pytz

nest_asyncio.apply()

# ======================
# 🔧 إعدادات البوت
# ======================
BOT_TOKEN = "8097310973:AAE68aYlgPb1onGzvWDk4GbYWlPJBNQOzJI"
CHAT_ID = "8137529944"
bot = Bot(token=BOT_TOKEN)
tz = pytz.timezone("Asia/Riyadh")

# ======================
# ⚙️ إعدادات التحليل
# ======================
VOLUME_THRESHOLD = 300_000
EMA_PERIOD = 200
RSI_PERIOD = 14
CHANGE_THRESHOLD = 1.0
COOLDOWN_HOURS = 8
MIN_PRICE = 0.005
BASE_URL = "https://api.mexc.com/api/v3"

TARGET_LEVELS = [10, 20, 30, 40, 50, 75, 100, 150, 200, 300, 500]
DROP_ALERT = -10  # 🔻 الهبوط النسبي من آخر قمة
last_alert = {}

# ======================
# 🕐 جلب بيانات الشموع
# ======================
async def get_klines(session, symbol):
    url = f"{BASE_URL}/klines?symbol={symbol}&interval=4h&limit=300"
    try:
        async with session.get(url, timeout=10) as resp:
            data = await resp.json()
            if not isinstance(data, list):
                return None
            df = pd.DataFrame(data, columns=['t','o','h','l','c','v','x','y','z','a','b','ignore'])
            df['c'] = df['c'].astype(float)
            df['v'] = df['v'].astype(float)
            df['ema200'] = ta.ema(df['c'], length=EMA_PERIOD)
            df['rsi'] = ta.rsi(df['c'], length=RSI_PERIOD)
            return df
    except:
        return None

# ======================
# 💎 تنبيه دخول
# ======================
async def send_entry_alert(symbol, price, ema200, rsi, volume, change):
    url = f"https://www.mexc.com/exchange/{symbol.replace('USDT','_USDT')}"
    if volume > 10_000_000 and change > 5:
        strength = "🚀 سيولة قوية جدًا — دخول ذكي"
    elif volume > 3_000_000:
        strength = "🟢 سيولة متوسطة — دخول جيد"
    else:
        strength = "🟡 سيولة منخفضة — تحتاج متابعة"

    text = f"""
💎 <b>دخول سيولة مال ذكي</b>

📊 <b>العملة:</b> {symbol}
💵 <b>السعر الحالي:</b> {price:.6f} USDT
📈 <b>الارتداد من القاع:</b> +{change:.2f} %
📊 <b>RSI:</b> {rsi:.1f}
📉 <b>EMA200:</b> {ema200:.3f}
💰 <b>حجم التداول آخر 24 ساعة:</b> ${volume:,.0f}
🏦 <b>السيولة:</b> {strength}
💹 <b>نوع التداول:</b> سبوت
📍 <b>المنصة:</b> MEXC
🧠 <b>تحليل:</b> دخول مبكر من المال الذكي قبل الانفجار
🔗 <a href="{url}">رابط الشارت على MEXC</a>

⏰ {datetime.now(tz).strftime('%Y-%m-%d %H:%M')}
"""
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)

# ======================
# 🎯 تنبيه تحقيق هدف
# ======================
async def send_target_alert(symbol, price, entry, gain):
    url = f"https://www.mexc.com/exchange/{symbol.replace('USDT','_USDT')}"
    text = f"""
🎯 <b>{symbol}</b> حققت هدف جديد!

💵 <b>سعر الدخول:</b> {entry:.6f} USDT
🚀 <b>السعر الحالي:</b> {price:.6f} USDT
📈 <b>الربح:</b> +{gain:.2f} %
💎 <b>العملة وصلت مستوى {int(gain)} %</b>
💹 <b>نوع التداول:</b> سبوت
🔗 <a href="{url}">رابط الشارت</a>
"""
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)

# ======================
# 🔻 تنبيه الهبوط (التصحيح)
# ======================
async def send_drop_alert(symbol, price, peak, drop_percent):
    url = f"https://www.mexc.com/exchange/{symbol.replace('USDT','_USDT')}"
    text = f"""
⚠️ <b>تنبيه تصحيح للسعر</b>

📊 <b>العملة:</b> {symbol}
💎 <b>من آخر قمة:</b> {peak:.6f} → {price:.6f}
📉 <b>نسبة الهبوط:</b> {drop_percent:.2f} %
💹 <b>نوع التداول:</b> سبوت
🔍 <b>قد يكون تصحيح لجني أرباح أو بداية ضعف</b>
🔗 <a href="{url}">رابط الشارت</a>
"""
    await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)

# ======================
# 🔍 تحليل عملة واحدة
# ======================
async def analyze_symbol(session, symbol):
    try:
        df = await get_klines(session, symbol)
        if df is None or len(df) < EMA_PERIOD:
            return

        last = df.iloc[-1]
        price, ema200, rsi = last['c'], last['ema200'], last['rsi']
        if price < MIN_PRICE:
            return

        async with session.get(f"{BASE_URL}/ticker/24hr?symbol={symbol}") as resp:
            t = await resp.json()
        if not isinstance(t, dict):
            return

        volume = float(t.get('quoteVolume', 0))
        change = float(t.get('priceChangePercent', 0))

        # دخول
        if (
            volume >= VOLUME_THRESHOLD
            and price > ema200
            and 45 <= rsi <= 70
            and change >= CHANGE_THRESHOLD
        ):
            if symbol not in last_alert or (
                datetime.now() - last_alert[symbol]["time"]
            ).total_seconds() > COOLDOWN_HOURS * 3600:
                await send_entry_alert(symbol, price, ema200, rsi, volume, change)
                last_alert[symbol] = {
                    "entry": price,
                    "time": datetime.now(),
                    "targets": set(),
                    "peak": price
                }

        # أهداف ورصد هبوط
        if symbol in last_alert:
            entry = last_alert[symbol]["entry"]
            gain = ((price - entry)/entry)*100
            last_alert[symbol]["peak"] = max(price, last_alert[symbol]["peak"])
            for target in TARGET_LEVELS:
                if gain >= target and target not in last_alert[symbol]["targets"]:
                    await send_target_alert(symbol, price, entry, gain)
                    last_alert[symbol]["targets"].add(target)

            peak = last_alert[symbol]["peak"]
            drop_percent = ((price - peak)/peak)*100
            if drop_percent <= DROP_ALERT:
                await send_drop_alert(symbol, price, peak, drop_percent)
                last_alert[symbol]["peak"] = price

    except Exception as e:
        print(f"⚠️ خطأ في {symbol}: {e}")

# ======================
# 🧠 تحليل السوق كامل
# ======================
async def run_analysis():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/ticker/24hr") as resp:
            tickers = await resp.json()

        symbols = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT')]

        print(f"🔍 فحص {len(symbols)} عملة...")
        tasks = [analyze_symbol(session, s) for s in symbols[:400]]
        await asyncio.gather(*tasks)
        print("✅ التحليل اكتمل!")

# ======================
# ⏳ تشغيل تلقائي كل ساعة
# ======================
async def main():
    while True:
        await run_analysis()
        print("⏳ تحديث بعد ساعة...")
        await asyncio.sleep(3600)

import asyncio
asyncio.run(main_loop())


