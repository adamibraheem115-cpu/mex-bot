# -- coding: utf-8 --
import asyncio, requests, pandas as pd, os, pytz, nest_asyncio, time, json, logging, math
from datetime import datetime
from telegram import Bot

# ================== إعدادات عامة ==================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# --- تليجرام (تركي كما هو حسب طلبك) ---
BOT_TOKEN = "8097310973:AAE68aYlgPb1onGzvWDk4GbYWlPJBNQOzJI"
CHAT_ID   = "8137529944"

# --- مراقبة السوق ---
TZ                     = pytz.timezone("Asia/Amman")
CHECK_INTERVAL         = 90               # ثواني بين كل فحص
CSV_FILE               = "mexc_liquidity_log.csv"
STATE_FILE             = "positions.json"

# --- عتبات الاكتشاف ---
PRICE_FROM_BOTTOM      = 10               # % ارتداد من القاع لاعتبار الحركة قوية
VOLUME_ABS_THRESHOLD   = 200_000          # حد مطلق لحجم الكوت فوليوم (24h) للعملات الصغيرة/المتوسطة
MIN_PAIR_QUOTE_VOL     = 50_000           # فلترة أولية: تجاهل الأزواج ذات السيولة الضعيفة
REL_VOL_FACTOR_ENTRY   = 1.30             # تضاعف نسبي بالسيولة مقابل آخر دورة => 1.3x
REL_VOL_FACTOR_RE_ALERT= 1.50             # تضاعف نسبي أشد لتكرار التنبيه

# --- إعادة التنبيه على نفس العملة ---
RE_ALERT_MOVE          = 10               # % زيادة بالسعر منذ آخر تنبيه
RE_ALERT_MIN_MINS      = 20               # أقل زمن بين تنبيهين لنفس العملة (دقائق)

# --- أهداف ربح متعددة ---
TARGETS                = [10, 20, 30, 50] # %

# --- استثناء أزواج الرافعة *_UPUSDT / *_DOWNUSDT (اختياري لكنه موصى به) ---
SKIP_LEVERAGED_TOKENS  = True

# ================== تهيئة ==================
bot = Bot(token=BOT_TOKEN)

if not os.path.exists(CSV_FILE):
    pd.DataFrame(columns=["Time","Symbol","Event","Volume","Change(%)","Price"]).to_csv(CSV_FILE, index=False)

# بنية الحالة:
# active_positions["BTCUSDT"] = {
#   "alert_price": 68000,
#   "alert_vol": 1200000,
#   "last_alert_ts": 1720...,        # time.time()
#   "entry_price": 68000,
#   "hit_targets": [10, 20],         # نخزنها قائمة ثم نحوّلها set في الذاكرة
#   "last_vol": 1180000              # لتقدير الزيادة النسبية في السيولة
# }
active_positions = {}

def load_state():
    global active_positions
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # تحويل hit_targets لقائمة إلى set داخل الذاكرة
            for sym, s in data.items():
                s["hit_targets"] = set(s.get("hit_targets", []))
            active_positions = data
            logging.info(f"تم تحميل الحالة من {STATE_FILE} – {len(active_positions)} رمز.")
        except Exception as e:
            logging.error(f"فشل تحميل الحالة: {e}")

def save_state():
    try:
        serializable = {}
        for sym, s in active_positions.items():
            ss = dict(s)
            # تحويل set إلى قائمة للتخزين
            if isinstance(ss.get("hit_targets"), set):
                ss["hit_targets"] = sorted(list(ss["hit_targets"]))
            serializable[sym] = ss
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"فشل حفظ الحالة: {e}")

async def send_alert(text):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)
    except Exception as e:
        logging.error(f"Telegram Error: {e}")

def log_event(symbol, event, volume, change, price):
    try:
        pd.DataFrame(
            [[datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), symbol, event, volume, change, price]],
            columns=["Time","Symbol","Event","Volume","Change(%)","Price"]
        ).to_csv(CSV_FILE, mode='a', header=False, index=False)
    except Exception as e:
        logging.error(f"CSV log error: {e}")

def get_tickers():
    try:
        r = requests.get("https://api.mexc.com/api/v3/ticker/24hr", timeout=15)
        data = r.json() if r.status_code == 200 else []
        return data if isinstance(data, list) else []
    except requests.exceptions.Timeout:
        logging.warning("⏳ Timeout أثناء جلب بيانات MEXC")
        return []
    except Exception as e:
        logging.error(f"get_tickers error: {e}")
        return []

def classify_strength(quote_vol, rise_from_bottom):
    # تصنيف بسيط قابل للتعديل
    if quote_vol > 1_000_000 and rise_from_bottom > 40:
        return "💎 سيولة عالية جدًا 🔥", "🧠 دخول قوي من المال الذكي"
    elif quote_vol > 500_000 and rise_from_bottom > 25:
        return "⚡ سيولة متوسطة 🚀", "🧠 دخول متزن من المال الذكي"
    elif quote_vol > VOLUME_ABS_THRESHOLD and rise_from_bottom > PRICE_FROM_BOTTOM:
        return "📈 سيولة ملحوظة", "🧠 بداية دخول سيولة"
    else:
        return None, None

def leveraged_token(symbol: str) -> bool:
    if not SKIP_LEVERAGED_TOKENS:
        return False
    # أمثلة شائعة: BTCUPUSDT / BTCDOWNUSDT
    return symbol.endswith("UPUSDT") or symbol.endswith("DOWNUSDT")

def should_realert(symbol, last_price, quote_vol, rel_vol_factor):
    s = active_positions.get(symbol)
    if not s:
        return True
    mins_since = (time.time() - s["last_alert_ts"]) / 60.0
    if mins_since < RE_ALERT_MIN_MINS:
        return False
    move_since_last = ((last_price - s["alert_price"]) / (s["alert_price"] or 1e-9)) * 100.0
    # إعادة تنبيه إذا تحرك السعر بقوة أو السيولة قفزت نسبيًا
    vol_jump = rel_vol_factor >= REL_VOL_FACTOR_RE_ALERT or (quote_vol >= (s.get("alert_vol", 0) * REL_VOL_FACTOR_RE_ALERT))
    return (move_since_last >= RE_ALERT_MOVE) or vol_jump

def check_targets(symbol, last_price):
    """يعيد قائمة الأهداف التي تم الوصول إليها ولم تُرسل بعد"""
    s = active_positions.get(symbol)
    if not s or "entry_price" not in s or not s["entry_price"]:
        return []
    entry = s["entry_price"]
    if entry <= 0:
        return []
    gain = ((last_price - entry) / entry) * 100.0
    hit = []
    already = s.get("hit_targets", set())
    for t in TARGETS:
        if gain >= t and t not in already:
            hit.append((t, gain))
    return hit  # [(target, current_gain), ...]

async def analyze_market():
    tickers = get_tickers()
    if not tickers:
        return

    # فلترة أولية: USDT فقط + حجم معقول + استثناء أزواج الرافعة
    filtered = []
    for t in tickers:
        try:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if leveraged_token(symbol):
                continue
            quote_vol = float(t.get("quoteVolume", 0.0))
            if quote_vol < MIN_PAIR_QUOTE_VOL:
                continue
            filtered.append(t)
        except Exception:
            continue

    for t in filtered:
        try:
            symbol      = t["symbol"]
            last_price  = float(t["lastPrice"])
            low_price   = float(t["lowPrice"])
            quote_vol   = float(t["quoteVolume"])
            change_24h  = float(t.get("priceChangePercent", 0.0))  # تغيير 24 ساعة من نفس الإندبوينت

            if low_price <= 0 or last_price <= 0:
                continue

            rise_from_bottom = ((last_price - low_price) / low_price) * 100.0

            # --- نسبية السيولة مقارنةً بآخر دورة ---
            prev_vol = active_positions.get(symbol, {}).get("last_vol", 0.0)
            rel_vol_factor = (quote_vol / prev_vol) if prev_vol > 0 else 1.0

            # 1) تحقق أهداف الربح في مراكز تم تنبيهها سابقًا
            if symbol in active_positions:
                hit_list = check_targets(symbol, last_price)
                for target, current_gain in hit_list:
                    msg = (
                        f"🎯 <b>تحقيق هدف ربح +{target:.0f}%</b>\n\n"
                        f"💠 <b>العملة:</b> {symbol}\n"
                        f"💵 <b>السعر الحالي:</b> {last_price:.6f} USDT\n"
                        f"📈 <b>الربح منذ التنبيه الأول:</b> +{current_gain:.2f}%\n"
                        f"📊 <b>تغير 24h:</b> {change_24h:.2f}%\n"
                        f"💰 <b>حجم 24h:</b> ${quote_vol:,.0f}\n"
                        f"🏦 <b>المنصة:</b> MEXC\n"
                        f"⏰ <b>الوقت:</b> {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} (عمّان)\n\n"
                        f"🔗 <a href='https://www.mexc.com/exchange/{symbol.replace('USDT','_USDT')}'>عرض الشارت على MEXC</a>"
                    )
                    await send_alert(msg)
                    log_event(symbol, f"Target {target}%", quote_vol, current_gain, last_price)
                    s = active_positions[symbol]
                    s.setdefault("hit_targets", set()).add(target)
                    s["alert_price"]   = last_price
                    s["alert_vol"]     = quote_vol
                    s["last_alert_ts"] = time.time()
                    s["last_vol"]      = quote_vol
                    save_state()

            # 2) دخول جديد / إعادة تنبيه
            # الشروط: ارتداد مناسب + سيولة مطلقة كافية + قفزة نسبية بالسيولة
            entry_cond = (
                rise_from_bottom >= PRICE_FROM_BOTTOM and
                quote_vol >= VOLUME_ABS_THRESHOLD and
                rel_vol_factor >= REL_VOL_FACTOR_ENTRY
            )

            if entry_cond:
                strength, analysis = classify_strength(quote_vol, rise_from_bottom)
                if not strength:
                    # حتى لو التصنيف ضعيف، لا زال الشرط العام تحقق؛ لكن نحافظ على الجودة
                    strength, analysis = "📈 سيولة ملحوظة", "🧠 بداية دخول سيولة"
                # منع السبام على نفس الزوج
                if symbol in active_positions and not should_realert(symbol, last_price, quote_vol, rel_vol_factor):
                    # تحديث last_vol حتى لو لم نرسل تنبيه
                    active_positions[symbol]["last_vol"] = quote_vol
                    continue

                msg = (
                    f"🚀 <b>ارتداد قوي مع ضخ سيولة</b>\n\n"
                    f"💠 <b>العملة:</b> {symbol}\n"
                    f"💵 <b>السعر الحالي:</b> {last_price:.6f} USDT\n"
                    f"📈 <b>الارتداد من القاع:</b> +{rise_from_bottom:.2f}%\n"
                    f"📊 <b>تغير 24h:</b> {change_24h:.2f}%\n"
                    f"💰 <b>حجم 24h:</b> ${quote_vol:,.0f}\n"
                    f"📈 <b>زيادة السيولة منذ آخر دورة:</b> {rel_vol_factor:.2f}x\n"
                    f"{strength}\n"
                    f"🏦 <b>المنصة:</b> MEXC\n"
                    f"⏰ <b>الوقت:</b> {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')} (عمّان)\n\n"
                    f"{analysis}\n"
                    f"🔗 <a href='https://www.mexc.com/exchange/{symbol.replace('USDT','_USDT')}'>فتح الشارت على MEXC</a>"
                )
                await send_alert(msg)
                log_event(symbol, "Entry" if symbol not in active_positions else "Re-Alert",
                          quote_vol, rise_from_bottom, last_price)

                if symbol not in active_positions:
                    active_positions[symbol] = {
                        "alert_price": last_price,
                        "alert_vol":   quote_vol,
                        "last_alert_ts": time.time(),
                        "entry_price": last_price,       # أول دخول يعتبر سعر دخول
                        "hit_targets": set(),
                        "last_vol":    quote_vol
                    }
                else:
                    s = active_positions[symbol]
                    s["alert_price"]   = last_price
                    s["alert_vol"]     = quote_vol
                    s["last_alert_ts"] = time.time()
                    s["last_vol"]      = quote_vol
                    # نحافظ على entry_price القديم
                save_state()
            else:
                # حتى لو ما توفر شرط دخول، حدث last_vol للقياس النسبي في الدورة القادمة
                if symbol in active_positions:
                    active_positions[symbol]["last_vol"] = quote_vol

        except Exception as e:
            logging.error(f"Error in analyze_market loop for {t.get('symbol')}: {e}")
            continue

async def run_bot():
    load_state()
    logging.info("🚀 البوت يعمل الآن – يراقب السيولة الذكية على أزواج USDT ...")
    while True:
        await analyze_market()
        await asyncio.sleep(CHECK_INTERVAL)

if _name_ == "_main_":
    nest_asyncio.apply()
    asyncio.get_event_loop().run_until_complete(run_bot())
