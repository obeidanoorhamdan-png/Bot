"""config.py — الإعدادات، الثوابت، الأزرار، الترجمة، أدوات مساعدة عامة"""
import html
import logging
import os
import time
from contextvars import ContextVar
from datetime import datetime, timedelta

START_TIME = time.time()

# ─────────── البيئة ───────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
SUPER_ADMIN_ID = int(os.environ.get("SUPER_ADMIN_ID", "6207431030") or 0)
ADMIN_IDS = {SUPER_ADMIN_ID} | {
    int(x) for x in os.environ.get("ADMIN_IDS", "6207431030").replace(" ", "").split(",") if x.isdigit()}
ADMIN_IDS.discard(0)

API_URL = os.environ.get("API_URL", "https://w0rmgpt.lovable.app/api/public/subscriptions")
SITE_URL = os.environ.get("SITE_URL", "https://w0rmgpt.lovable.app/")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "engtopwln").lstrip("@")   # قناة افتراضية تُزرع عند أول تشغيل
INITIAL_API_KEY = os.environ.get("INITIAL_API_KEY", "wsk_0c4d9994396f4b27a464dfc1c43e244be0fb58c6").strip()
INITIAL_API_KEY_LABEL = os.environ.get("INITIAL_API_KEY_LABEL", "المفتاح الأساسي")

POINTS_INVITE = int(os.environ.get("POINTS_INVITE", "1"))
POINTS_DAILY = int(os.environ.get("POINTS_DAILY", "1"))
MIN_TRANSFER = int(os.environ.get("MIN_TRANSFER", "1"))
MAX_QTY = int(os.environ.get("MAX_QTY", "50"))
API_CONCURRENCY = int(os.environ.get("API_CONCURRENCY", "3"))
DROPS_REFRESH_INTERVAL = int(os.environ.get("DROPS_REFRESH_INTERVAL", "900"))
JOIN_CACHE_SECONDS = 3600
NOTIFY_MAX = int(os.environ.get("NOTIFY_MAX", "5000"))

MARKET_DEFAULT_COMMISSION = int(os.environ.get("MARKET_COMMISSION", "10"))
MARKET_MIN_PRICE = 1
MARKET_MAX_PRICE = int(os.environ.get("MARKET_MAX_PRICE", "1000"))
MARKET_MAX_LISTINGS = int(os.environ.get("MARKET_MAX_LISTINGS", "10"))
COMPLAINT_HOURS = int(os.environ.get("COMPLAINT_HOURS", "48"))

DB_PATH = "/data/bot_data.db" if os.path.exists("/data") else os.environ.get("DB_PATH", "bot_data.db")
BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "7"))
PAGE_SIZE = 5

SUBSCRIPTION_TYPES = {
    "plus": {"name": "بلس", "emoji": "⭐", "api_plan": "plus",
             "description": "اشتراك أساسي — مناسب للاستخدام الشخصي",
             "prices": {7: 5, 30: 15, 90: 40, 365: 120}},
    "pro": {"name": "برو", "emoji": "💎", "api_plan": "pro",
            "description": "اشتراك متقدم — ميزات إضافية",
            "prices": {7: 8, 30: 25, 90: 65, 365: 200}},
    "premium": {"name": "بريميوم", "emoji": "👑", "api_plan": "premium",
                "description": "اشتراك كامل — كل الميزات",
                "prices": {7: 15, 30: 45, 90: 120, 365: 380}},
}
QUANTITY_DISCOUNTS = {1: 0, 2: 5, 3: 10, 5: 15, 10: 20, 20: 25}

# مجموعات جوائز الدروب البيضاوي: (النوع، القيمة، الوزن)  — sub قيمتها (plan, days)
EGG_POOLS = {
    "light": [("points", 1, 55), ("points", 2, 30), ("points", 3, 12), ("sub", ("plus", 7), 3)],
    "medium": [("points", 2, 40), ("points", 5, 30), ("sub", ("plus", 7), 20), ("sub", ("pro", 7), 8),
               ("sub", ("plus", 30), 2)],
    "gold": [("points", 5, 35), ("sub", ("plus", 7), 25), ("sub", ("pro", 7), 20), ("sub", ("plus", 30), 10),
             ("sub", ("pro", 30), 6), ("sub", ("premium", 7), 4)],
}

LEVELS = [(0, "🥉 برونزي"), (20, "🥈 فضي"), (100, "🥇 ذهبي"), (500, "💠 ماسي"), (2000, "👑 أسطوري")]

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("wormgpt")
for n in ("httpx", "httpcore", "apscheduler"):
    logging.getLogger(n).setLevel(logging.WARNING)


def calculate_price(unit_price, quantity):
    """(الإجمالي بعد الخصم، قبل الخصم، نسبة الخصم)"""
    subtotal = unit_price * quantity
    pct = 0
    for th, p in sorted(QUANTITY_DISCOUNTS.items()):
        if quantity >= th:
            pct = p
    total = subtotal * (100 - pct) // 100
    return (max(total, 1) if quantity > 0 else 0), subtotal, pct


# ─────────── أدوات عامة ───────────
def esc(v):
    return html.escape(str(v if v is not None else ""))


def is_admin(uid):
    return uid in ADMIN_IDS


def now_dt():
    return datetime.now()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def parse_dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "").split(".")[0])
    except Exception:
        return None


def fmt_dt(d):
    dt = parse_dt(d) if d else None
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "غير محدد"


def human_delta(seconds):
    seconds = int(max(seconds, 0))
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    parts = []
    if d: parts.append(f"{d}ي")
    if h: parts.append(f"{h}س")
    if m or not parts: parts.append(f"{m}د")
    return " ".join(parts)


def relative_time(iso_str):
    dt = parse_dt(iso_str) if iso_str else None
    if not dt:
        return "غير محدد"
    s = int((datetime.now() - dt).total_seconds())
    if s < 0:
        return f"بعد {human_delta(-s)}"
    if s < 60: return f"منذ {s} ثانية"
    if s < 3600: return f"منذ {s // 60} دقيقة"
    if s < 86400: return f"منذ {s // 3600} ساعة"
    return f"منذ {s // 86400} يوم"


def plan_info(plan):
    return SUBSCRIPTION_TYPES.get(plan, {"emoji": "📦", "name": plan or "—", "prices": {}})


def mask_id(uid):
    s = str(uid)
    return f"{s[:2]}•••{s[-2:]}" if len(s) > 5 else "•••"


def mask_key(k):
    k = k or ""
    return f"{k[:6]}…{k[-4:]}" if len(k) > 12 else "••••"


def user_label(u):
    return u.get("first_name") or (f"@{u['username']}" if u.get("username") else str(u["user_id"]))


def bar(value, total, width=10):
    total = max(total, 1)
    filled = min(width, int(round(width * value / total)))
    return "█" * filled + "░" * (width - filled)


def spark(values):
    chars = "▁▂▃▄▅▆▇█"
    mx = max(values) if values and max(values) > 0 else 1
    return "".join(chars[min(7, int(v / mx * 7))] if v else "▁" for v in values)


def level_of(total_earned):
    cur, nxt = LEVELS[0], None
    for i, (th, name) in enumerate(LEVELS):
        if total_earned >= th:
            cur = (th, name)
            nxt = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    return cur, nxt


# ─────────── اللغة والمظهر (لكل مستخدم) ───────────
CUR_LANG = ContextVar("lang", default="ar")
CUR_THEME = ContextVar("theme", default="dark")


def apply_prefs(user):
    CUR_LANG.set((user or {}).get("lang") or "ar")
    CUR_THEME.set((user or {}).get("theme") or "dark")


THEMES = {
    "light": ("☀️ فاتح", "━━━━━━━━━━━━━━━━━"),
    "dark": ("🌙 داكن", "▪️▫️▪️▫️▪️▫️▪️▫️"),
    "minimal": ("✨ بسيط", ""),
}


def sep():
    s = THEMES.get(CUR_THEME.get(), THEMES["dark"])[1]
    return s + "\n" if s else ""


# ─────────── الأزرار ───────────
BTN_SHOP, BTN_SUBS, BTN_WALLET, BTN_EARN = "🛒 المتجر", "📦 اشتراكاتي", "💎 محفظتي", "🎁 اكسب نقاط"
BTN_TOP, BTN_GUIDE, BTN_CHECK, BTN_ABOUT = "🏆 الصدارة", "📖 كيف أفعّل", "🔍 فحص كود", "ℹ️ عن البوت"
BTN_STATUS, BTN_LANG, BTN_THEME, BTN_NOTIF = "📊 حالتي", "🌍 اللغة", "🌙 المظهر", "🔔 الإشعارات"
BTN_BAZAAR, BTN_ADMIN, BTN_HOME, BTN_BACK = "🏪 البازار", "🛡️ لوحة المشرف", "🏠 القائمة الرئيسية", "🔙 رجوع"
BTN_PLUS, BTN_PRO, BTN_PREMIUM = "⭐ بلس", "💎 برو", "👑 بريميوم"
BTN_QTY_1, BTN_QTY_2, BTN_QTY_3 = "1 كود", "2 كود", "3 أكواد"
BTN_QTY_5, BTN_QTY_10, BTN_QTY_CUSTOM = "5 أكواد", "10 أكواد", "✏️ عدد مخصص"
BTN_CONFIRM, BTN_EDIT_QTY, BTN_CANCEL = "✅ تأكيد", "✏️ تعديل العدد", "❌ إلغاء"
BTN_TRANSFER, BTN_DAILY, BTN_INVITE, BTN_MY_ID = "💸 تحويل نقاط", "🎁 المكافأة اليومية", "🔗 دعوة أصدقاء", "🆔 معرفي"
BTN_CALC, BTN_ARCHIVE, BTN_ACTIVE_SUBS = "🧮 حاسبة", "📚 أرشيف", "📦 النشطة"
BTN_UDROPS, BTN_REDEEM = "🎫 دروبات", "🎟 إدخال كود دروب"
# البازار
BTN_MK_BROWSE, BTN_MK_SELL, BTN_MK_MINE = "🛍 تصفح العروض", "➕ بيع كود", "📋 عروضي"
BTN_MK_COMPLAIN, BTN_MK_LEDGER = "⚠️ تقديم شكوى", "📜 سجل البازار"
# المشرف
BTN_DASH, BTN_KEYS, BTN_CHANNELS = "📊 لوحة التحكم", "🔑 إدارة المفاتيح", "📢 إدارة القنوات"
BTN_DROPS, BTN_MARKET, BTN_ANALYTICS = "🎁 الدروبات", "🛒 السوق", "📊 التحليلات المتقدمة"
BTN_MONITOR, BTN_LOGS, BTN_BROADCAST = "🔍 المراقبة", "📜 السجل", "📢 بث ونشر"
BTN_D_NORMAL, BTN_D_EGG, BTN_D_COND = "➕ إنشاء دروب عادي", "🥚 دروب بيضاوي", "🔒 دروب بشرط"
BTN_D_CHAIN, BTN_D_VOTE, BTN_D_AUCTION = "🔗 دروب تسلسلي", "🗳️ دروب بتصويت", "🔨 دروب بمزاد"
BTN_D_POINTS, BTN_D_ALL, BTN_D_SCHED, BTN_D_STATS = "💎 دروب نقاط", "📋 كل الدروبات", "⏰ المجدولة", "📊 إحصائيات الدروبات"
BTN_M_PRODUCTS, BTN_M_COMM, BTN_M_COMPLAINTS = "📋 المنتجات", "💰 العمولات", "⚠️ الشكاوى"
BTN_A_USERS, BTN_A_REV, BTN_A_DROPS, BTN_A_EXPORT = "📈 المستخدمون", "💰 الإيرادات", "🎁 تحليل الدروبات", "📤 التصدير"
BTN_MON_SYS, BTN_MON_ALERTS, BTN_MON_BACKUP = "⚙️ حالة النظام", "⚠️ التنبيهات", "💾 النسخ الاحتياطي"
BTN_B_USERS, BTN_B_CHANNEL = "📣 بث للمستخدمين", "📡 نشر في القنوات"
BTN_CH_ADD, BTN_ADD_KEY, BTN_BK_NOW = "➕ إضافة قناة", "➕ إضافة مفتاح", "💾 نسخة الآن"
BTN_NOW, BTN_SKIP = "🚀 الآن", "⏭ تخطي"

PLAN_BUTTONS = {BTN_PLUS: "plus", BTN_PRO: "pro", BTN_PREMIUM: "premium"}
QTY_BUTTONS = {BTN_QTY_1: 1, BTN_QTY_2: 2, BTN_QTY_3: 3, BTN_QTY_5: 5, BTN_QTY_10: 10}

BTN_EN = {
    BTN_SHOP: "🛒 Shop", BTN_SUBS: "📦 My subscriptions", BTN_WALLET: "💎 Wallet", BTN_EARN: "🎁 Earn points",
    BTN_TOP: "🏆 Leaderboard", BTN_GUIDE: "📖 How to activate", BTN_CHECK: "🔍 Check code", BTN_ABOUT: "ℹ️ About",
    BTN_STATUS: "📊 My status", BTN_LANG: "🌍 Language", BTN_THEME: "🌙 Theme", BTN_NOTIF: "🔔 Notifications",
    BTN_BAZAAR: "🏪 Bazaar", BTN_ADMIN: "🛡️ Admin panel", BTN_HOME: "🏠 Main menu", BTN_BACK: "🔙 Back",
    BTN_PLUS: "⭐ Plus", BTN_PRO: "💎 Pro", BTN_PREMIUM: "👑 Premium",
    BTN_QTY_1: "1 code", BTN_QTY_2: "2 codes", BTN_QTY_3: "3 codes", BTN_QTY_5: "5 codes", BTN_QTY_10: "10 codes",
    BTN_QTY_CUSTOM: "✏️ Custom amount", BTN_CONFIRM: "✅ Confirm", BTN_EDIT_QTY: "✏️ Edit quantity",
    BTN_CANCEL: "❌ Cancel", BTN_TRANSFER: "💸 Transfer points", BTN_DAILY: "🎁 Daily bonus",
    BTN_INVITE: "🔗 Invite friends", BTN_MY_ID: "🆔 My ID", BTN_CALC: "🧮 Calculator", BTN_ARCHIVE: "📚 Archive",
    BTN_ACTIVE_SUBS: "📦 Active", BTN_UDROPS: "🎫 Drops", BTN_REDEEM: "🎟 Enter drop code",
    BTN_MK_BROWSE: "🛍 Browse offers", BTN_MK_SELL: "➕ Sell a code", BTN_MK_MINE: "📋 My listings",
    BTN_MK_COMPLAIN: "⚠️ File complaint", BTN_MK_LEDGER: "📜 Bazaar ledger",
    BTN_NOW: "🚀 Now", BTN_SKIP: "⏭ Skip",
}
REV_EN = {v: k for k, v in BTN_EN.items()}

MSG_EN = {
    "🏠 <b>القائمة الرئيسية</b>": "🏠 <b>Main menu</b>",
    "استخدم الأزرار بالأسفل:": "Use the buttons below:",
    "🤔 استخدم الأزرار بالأسفل.": "🤔 Please use the buttons below.",
    "🌍 <b>اللغة</b>\n\nاختر لغة الواجهة:": "🌍 <b>Language</b>\n\nChoose the interface language:",
    "🌙 <b>المظهر</b>\n\nاختر شكل البطاقات والفواصل:": "🌙 <b>Theme</b>\n\nChoose the look of cards and separators:",
    "🔔 <b>الإشعارات</b>\n\nاضغط لتفعيل/تعطيل:": "🔔 <b>Notifications</b>\n\nTap to toggle:",
    "🚫 تم حظرك من استخدام البوت.": "🚫 You have been banned from using this bot.",
    "✅ تم التحديث": "✅ Updated",
    "❌ للإلغاء": "❌ to cancel",
}


def tr(text):
    return MSG_EN.get(text, text) if CUR_LANG.get() == "en" else text


def tb(label):
    return BTN_EN.get(label, label) if CUR_LANG.get() == "en" else label


def canon(text):
    """يعيد الصيغة العربية الأصلية للزر مهما كانت لغة الواجهة"""
    return REV_EN.get(text, text)
