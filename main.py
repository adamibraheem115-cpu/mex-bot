import asyncio
import aiohttp
import json
import os
from datetime import datetime, timezone, timedelta
import pandas as pd
import pandas_ta as ta
from telegram import Bot

# ============ إعدادات عامة ============
BOT_TOKEN = "8097310973:AAE68aYlgPb1onGzvWDk4GbYWlPJBNQOzJI"
CHAT_ID = "8137529944"

BASE_URL     = "https://api.mexc.com/api/v3"
TIMEFRAME    = "4h"
VOLUME_LIMIT = 500_000          # حد أدنى للسيولة (Quote Volume)
MAX_SYMBOLS  = 300              # أعلى عدد عملات نفحصها
CHECK_GAP_S  = 3600             # كل كم ثانية نعيد التحليل الكامل (ساعة)
TARGET_PING_S= 300              # كل كم ثانية نراجع الأهداف (5 دقائق)
TARGETS      = [10, 30, 50, 100]# الأهداف المئوية

STATE_FILE   = "state.json"     # تخزين آخر إشارات لدراسة تحقيق الأهداف

bot = Bot(token=BOT_TOKEN)
sent_alerts = set()             # منع تكرار نفس التنبيه فورياً

# ============ أدوات مساعدة ============
def tz_now_str():
    # نحول لتوقيت عُمان تقريبياً (+4)
    now = datetime.now(timezone.utc) + timedelta(hours=4)
    return now.strftime("%d-%m-%Y %H:%M (%Z)")

def mexc_link(symbol: str) -> str:
    return f"https://www.mexc.com/exchange/{symbol.replace('USDT','_USDT')}"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"positions": {}}  # {symbol: {"entry": float, "time": iso, "hit": [10,30,...]}}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("❌ خطأ حفظ الحالة:", e)

async def send_message(text: str):
    try:
        await asyncio.sleep(0.4)
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)
    except Exception as e:
        print("❌ خطأ في إرسال الرسالة:", e)

# ============ التحليل الأساسي ============
async def analyze_symbol(session: aiohttp.ClientSession, symbol: str, state: dict):
    try:
        url = f"{BASE_URL}/klines?symbol={symbol}&interval={TIMEFRAME}&limit=200"
        async with session.get(url) as resp:
            kdata = await resp.json()

        if not isinstance(kdata, list) or len(kdata) < 50:
            return

        # أعمدة 8 أو 12
        cols8  = ['timestamp','open','high','low','close','volume','_','__']
        cols12 = ['timestamp','open','high','low','close','volume','_','__','___','____','_____','______']
        df = pd.DataFrame(kdata, columns=cols8 if len(kdata[0]) == 8 else cols12)

        df["close"] = df["close"].astype(float)
        df["EMA200"] = ta.ema(df["close"], length=200)
        df["RSI"]    = ta.rsi(df["close"],  length=14)

        last_close = float(df["close"].iloc[-1])
        ema200     = float(df["EMA200"].iloc[-1])
        rsi        = float(df["RSI"].iloc[-1])

        # منطق "سيولة المال الذكي"
        strength = None
        analysis = None

        if last_close > ema200 and rsi < 70:
            strength = "🚀 دخول سيولة مال ذكي"
            analysis = "💡 دخول مبكر من المال الذكي قبل الانفجار 🔥"
        elif last_close > ema200 and 70 <= rsi <= 80:
            strength = "⚡ سيولة قوية"
            analysis = "📊 استمرار دخول سيولة مرتفعة"
        elif last_close < ema200 and rsi < 30:
            strength = "📉 خروج سيولة"
            analysis = "⚠️ خروج محتمل من السيولة أو تصحيح"
        else:
            return  # لا تنبيه

        alert_key = f"{symbol}-{strength}"
        if alert_key in sent_alerts:
            return
        sent_alerts.add(alert_key)

        # خزّن “سعر الدخول” لمتابعة الأهداف
        pos = state["positions"].get(symbol)
        if pos is None or last_close > pos.get("entry", 0) * 1.02 or last_close < pos.get("entry", 0) * 0.98:
            state["positions"][symbol] = {
                "entry": last_close,
                "time":  datetime.utcnow().isoformat(),
                "hit":   []  # الأهداف التي حققها
            }
            save_state(state)

        msg = (
            f"{strength}\n"
            f"💎 <b>العملة:</b> {symbol}\n"
            f"💵 <b>السعر الحالي:</b> {last_close:.8f} USDT\n"
            f"📈 <b>RSI:</b> {rsi:.2f}\n"
            f"📏 <b>EMA200:</b> {ema200:.6f}\n"
            f"🕒 <b>الفريم:</b> {TIMEFRAME}\n"
            f"💹 <b>نوع التداول:</b> سبوت\n"
            f"⏰ <b>الوقت:</b> {tz_now_str()}\n\n"
            f"🧠 <b>تحليل:</b> {analysis}\n"
            f"🔗 <a href='{mexc_link(symbol)}'>فتح الشارت على MEXC</a>"
        )
        await send_message(msg)

    except Exception as e:
        print(f"⚠️ خطأ في تحليل {symbol}: {e}")

async def run_analysis():
    state = load_state()
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BASE_URL}/ticker/24hr") as resp:
            tickers = await resp.json()

        # فلترة USDT + سيولة قوية
        symbols = [
            t["symbol"] for t in tickers
            if t.get("symbol","").endswith("USDT")
            and float(t.get("quoteVolume", 0) or 0) >= VOLUME_LIMIT
        ]
        symbols = symbols[:MAX_SYMBOLS]
        print(f"🔍 يتم فحص {len(symbols)} عملة ذات سيولة قوية...")

        sem = asyncio.Semaphore(10)

        async def safe(sym):
            async with sem:
                await analyze_symbol(session, sym, state)

        await asyncio.gather(*[safe(s) for s in symbols])
        print("✅ التحليل اكتمل!")

# ============ متابعة الأهداف ============
async def check_targets():
    """يراجع الأهداف للرموز التي لديها دخول محفوظ"""
    state = load_state()
    positions = state.get("positions", {})
    if not positions:
        return

    symbols = list(positions.keys())
    async with aiohttp.ClientSession() as session:
        # نجيب أسعار حالية بسرعة
        # /ticker/price يُرجع lastPrice لرمز واحد؛ نستخدم /ticker/24hr لأننا نحتاج عدة رموز
        async with session.get(f"{BASE_URL}/ticker/24hr") as resp:
            all_ticks = await resp.json()

        price_map = {}
        for t in all_ticks:
            s = t.get("symbol")
            if s in symbols:
                try:
                    price_map[s] = float(t.get("lastPrice") or t.get("lastPrice", 0.0))
                except:
                    pass

        # راجع كل رمز
        updated = False
        for sym, info in positions.items():
            entry = float(info.get("entry", 0))
            hit   = info.get("hit", [])
            last  = price_map.get(sym)
            if entry <= 0 or last is None:
                continue

            change_pct = (last - entry) / entry * 100.0

            # ابحث عن أهداف لم تُحقق بعد
            for target in TARGETS:
                if target in hit:
                    continue
                if change_pct >= target:
                    hit.append(target)
                    updated = True
                    msg = (
                        f"🎯 <b>تحقيق هدف ربح +{target}%</b>\n"
                        f"💎 <b>العملة:</b> {sym}\n"
                        f"📊 <b>الربح منذ التنبيه الأول:</b> +{change_pct:.2f}%\n"
                        f"💵 <b>سعر الدخول:</b> {entry:.8f} • <b>السعر الحالي:</b> {last:.8f}\n"
                        f"🕒 <b>الفريم:</b> {TIMEFRAME}  |  💹 <b>النوع:</b> سبوت\n"
                        f"⏰ <b>الوقت:</b> {tz_now_str()}\n"
                        f"🔗 <a href='{mexc_link(sym)}'>فتح الشارت على MEXC</a>"
                    )
                    await send_message(msg)

            # خزّن الأهداف التي تحققت
            info["hit"] = sorted(list(set(hit)))
            positions[sym] = info

        if updated:
            state["positions"] = positions
            save_state(state)

# ============ الحلقة الرئيسية ============
async def main_loop():
    """يشغّل تحليل كامل كل ساعة، ويتحقق من الأهداف كل 5 دقائق."""
    # تشغيل آنيّ مرّة عند البدء
    await run_analysis()

    # مؤقّتات متداخلة
    last_full = 0.0
    while True:
        now = asyncio.get_event_loop().time()

        # تحليل كامل كل ساعة
        if now - last_full >= CHECK_GAP_S:
            try:
                await run_analysis()
            finally:
                last_full = now

        # مراجعة الأهداف كل 5 دقائق
        try:
            await check_targets()
        except Exception as e:
            print("⚠️ خطأ في مراجعة الأهداف:", e)

        await asyncio.sleep(TARGET_PING_S)

if __name__ == "__main__":
    asyncio.run(main_loop())
