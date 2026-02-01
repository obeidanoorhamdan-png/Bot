import logging
import base64
import os
import sqlite3
import re
import requests
import threading
import time
import sys
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from flask import Flask
from datetime import datetime, timedelta

# --- الإعدادات ---
TOKEN = os.environ.get('TOKEN', "7324911542:AAGcVkwzjtf3wDB3u7cprOLVyoMLA5JCm8U")
GROQ_KEY = os.environ.get('GROQ_KEY', "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")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"
DB_NAME = "abood-gpt.db"

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

# توزيع العملات للنظام الجديد مع إضافة السيولة والأخبار
CATEGORIES = {
    "فوركس - عملات رئيسية 💹": [
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", 
        "USD/CHF", "USD/CAD", "NZD/USD"
    ],
    "فوركس - تقاطعات اليورو 🇪🇺": [
        "EUR/GBP", "EUR/JPY", "EUR/AUD", "EUR/CAD", 
        "EUR/NZD", "EUR/CHF"
    ],
    "فوركس - تقاطعات الباوند 🇬🇧": [
        "GBP/JPY", "GBP/AUD", "GBP/CAD", "GBP/NZD", 
        "GBP/CHF"
    ],
    "عملات ثانوية وأخرى 💱": [
        "AUD/JPY", "AUD/CAD", "AUD/NZD", "CAD/JPY", 
        "NZD/JPY", "CHF/JPY"
    ],
    "عملات غريبة (Exotics) 🌍": [
        "USD/TRY", "USD/ZAR", "USD/MXN", "USD/SGD", 
        "USD/NOK", "USD/SEK"
    ],
    "مؤشرات عالمية 📊": [
        "S&P 500", "Dow Jones (US30)", "DAX 40 (GER40)", 
        "FTSE 100", "CAC 40", "Nikkei 225", "ASX 200", "Hang Seng"
    ],
    "معادن وطاقة 🏗️": [
        "الذهب (XAUUSD)", "الفضة (XAGUSD)", "البلاتين (XPTUSD)", 
        "النحاس (Copper)", "نفط برنت (UKOIL)", "النفط الخام (USOIL)", "الغاز الطبيعي"
    ],
    "ناسداك وتكنولوجيا 🖥️": [
        "NAS100", "US Tech 100", "FANG+"
    ],
    "عملات رقمية ₿": [
        "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", 
        "ADA/USD", "DOT/USD", "LTC/USD"
    ]
}

# أوقات السيولة للأسواق
MARKET_SESSIONS = {
    "سيدني": "22:00-07:00 GMT",
    "طوكيو": "00:00-09:00 GMT", 
    "لندن": "08:00-17:00 GMT",
    "نيويورك": "13:00-22:00 GMT",
    "تداخل لندن-نيويورك": "13:00-17:00 GMT",
    "تداخل طوكيو-لندن": "08:00-09:00 GMT"
}

# حالات المحادثة
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION = range(7)

# --- Flask Server ---
app = Flask(__name__)

@app.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Obeida Trading</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
            h1 { color: #2c3e50; }
            .status { background: #2ecc71; color: white; padding: 10px 20px; border-radius: 5px; display: inline-block; }
            .info-box { background: #f8f9fa; border-radius: 10px; padding: 20px; margin: 20px auto; max-width: 600px; text-align: left; }
        </style>
    </head>
    <body>
        <h1>📊 Obeida Trading Telegram Bot 📊</h1>
        <p>Chat & Technical Analysis Bot</p>
        <div class="status">✅ Obeida Trading Running</div>
        <p>Last Ping: """ + time.strftime("%Y-%m-%d %H:%M:%S") + """</p>
        
        <div class="info-box">
            <h3>🚀 Bot Features:</h3>
            <ul>
                <li>📈 Advanced Chart Analysis</li>
                <li>💬 Smart Chat Assistant</li>
                <li>🎯 Trading Recommendations</li>
                <li>⚙️ Custom Settings</li>
                <li>🌐 Market Sessions Tracking</li>
                <li>📊 Multiple Asset Categories</li>
            </ul>
        </div>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return {"status": "active", "timestamp": time.time(), "bot": "Obeida Trading"}

@app.route('/ping')
def ping():
    return "PONG"

# --- قاعدة البيانات ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            candle TEXT DEFAULT 'M1', 
            trade_time TEXT DEFAULT 'قصير (1m-15m)',
            chat_context TEXT DEFAULT '',
            last_activity DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print("✅ Database initialized")

def save_user_setting(user_id, col, val):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(f"INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute(f"UPDATE users SET {col} = ?, last_activity = CURRENT_TIMESTAMP WHERE user_id = ?", (val, user_id))
    conn.commit()
    conn.close()

def get_user_setting(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT candle, trade_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        return res
    return ("M1", "قصير (1m-15m)")

def format_trade_time_for_prompt(trade_time):
    """تنسيق وقت الصفقة للبرومبت"""
    if trade_time == "قصير (1m-15m)":
        return "مدة الصفقة المتوقعة: قصير الأجل (1 دقيقة إلى 15 دقيقة) - تنفيذ سريع، مخاطر منخفضة"
    elif trade_time == "متوسط (4h-Daily)":
        return "مدة الصفقة المتوقعة: متوسط الأجل (4 ساعات إلى يومي) - انتظار أيام، مخاطر متوسطة"
    elif trade_time == "طويل (Weekly-Monthly)":
        return "مدة الصفقة المتوقعة: طويل الأجل (أسبوعي إلى شهري) - استثمار طويل، مخاطر مرتفعة"
    else:
        return f"مدة الصفقة المتوقعة: {trade_time}"

def get_market_session():
    """الحصول على جلسة السوق الحالية"""
    current_hour = datetime.utcnow().hour
    
    if 22 <= current_hour or current_hour < 7:
        return "سيدني", MARKET_SESSIONS["سيدني"], "متوسطة"
    elif 0 <= current_hour < 9:
        return "طوكيو", MARKET_SESSIONS["طوكيو"], "متوسطة"
    elif 8 <= current_hour < 13:
        return "لندن", MARKET_SESSIONS["لندن"], "عالية"
    elif 13 <= current_hour < 17:
        return "تداخل لندن-نيويورك", MARKET_SESSIONS["تداخل لندن-نيويورك"], "عالية جداً"
    elif 13 <= current_hour < 22:
        return "نيويورك", MARKET_SESSIONS["نيويورك"], "عالية"
    else:
        return "جلسة خاملة", "00:00-00:00 GMT", "منخفضة"

# --- معالجة الصور بشكل صحيح ---
def encode_image(image_path):
    """تحويل الصورة إلى base64 بشكل صحيح"""
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        return encoded_string
    except Exception as e:
        print(f"Error encoding image: {e}")
        return None

# --- دوال المساعدة للتعامل مع النصوص ---
def clean_repeated_text(text):
    """تنظيف النص من التكرارات وتحسين التنسيق"""
    if not text:
        return ""
    
    # إزالة التكرارات المختلفة
    patterns_to_clean = [
        (r'📊\s*\*\*نتائج الفحص الفني\*\*:', '📊 **التحليل الفني:**'),
        (r'🎯\s*\*\*التوصية والتوقعات\*\*:', '🎯 **الإشارة التنفيذية:**'),
        (r'⚠️\s*\*\*إدارة المخاطر\*\*:', '⚠️ **إدارة المخاطر:**'),
        (r'📝\s*\*\*ملاحظات التحليل\*\*:', '📝 **ملاحظات التحليل:**'),
        (r'━━━━━━━━━━━━━━━━━━', '━━━━━━━━━━━━━━━━━━'),
        (r'🤖\s*\*\*Obeida Trading\*\*', '🤖 **Obeida Trading**')
    ]
    
    for pattern, replacement in patterns_to_clean:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if len(matches) > 1:
            # الحفاظ على أول ظهور فقط
            first_pos = matches[0].start()
            parts = []
            last_end = 0
            for i, match in enumerate(matches):
                if i == 0:
                    parts.append(text[last_end:match.end()])
                else:
                    parts.append(text[last_end:match.start()])
                last_end = match.end()
            parts.append(text[last_end:])
            text = ''.join(parts)
    
    # إزالة العناوين المكررة
    text = re.sub(r'(📊 \*\*نتائج الفحص الفني\*\*:[\s\S]*?)(?=📊 \*\*نتائج الفحص الفني\*\*:)', '', text, flags=re.DOTALL)
    text = re.sub(r'(🎯 \*\*الإشارة التنفيذية\*\*:[\s\S]*?)(?=🎯 \*\*الإشارة التنفيذية\*\*:)', '', text, flags=re.DOTALL)
    
    # تقسيم النص إلى فقرات وإزالة التكرار
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    
    unique_paragraphs = []
    seen_paragraphs = set()
    
    for paragraph in paragraphs:
        # إنشاء مفتاح فريد لكل فقرة
        key = re.sub(r'\s+', ' ', paragraph[:80].strip().lower())
        if key not in seen_paragraphs:
            unique_paragraphs.append(paragraph)
            seen_paragraphs.add(key)
    
    cleaned_text = '\n\n'.join(unique_paragraphs)
    
    # قطع النص إذا كان طويلاً جداً
    if len(cleaned_text) > 4000:
        if '\n\n' in cleaned_text[:3800]:
            cut_point = cleaned_text[:3800].rfind('\n\n')
            cleaned_text = cleaned_text[:cut_point] + "\n\n📋 **...تم اختصار النتيجة للتنسيق الأمثل**"
        else:
            cleaned_text = cleaned_text[:3800] + "...\n\n📋 **تم اختصار النتيجة**"
    
    return cleaned_text

def split_message(text, max_length=4000):
    """تقسيم الرسالة الطويلة إلى أجزاء"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    while len(text) > max_length:
        split_point = text[:max_length].rfind('\n\n')
        if split_point == -1:
            split_point = text[:max_length].rfind('\n')
        if split_point == -1:
            split_point = max_length - 100
        
        parts.append(text[:split_point])
        text = text[split_point:].lstrip()
    
    if text:
        parts.append(text)
    
    return parts

# --- وظائف نظام التوصية الجديد ---
def get_groq_analysis(symbol):
    """الحصول على تحليل من Groq API للعملة"""
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    
    # الحصول على جلسة السوق الحالية
    session_name, session_time, session_vol = get_market_session()
    
    # الحصول على الوقت الحالي وأوقات الأخبار المهمة
    current_time = datetime.utcnow()
    news_warning = ""
    if 13 <= current_time.hour <= 15:  # وقت إصدار الأخبار الأمريكية
        news_warning = "⚠️ **تحذير:** نحن في وقت إصدار الأخبار الاقتصادية الأمريكية الرئيسية - احذر من التقلبات المفاجئة"
    
    prompt = f"""
    ⚡ **نظام التوصيات الاحترافي - Obeida Trading Pro** ⚡
    
    📊 **المعطيات الأساسية:**
    - **العملة/المؤشر:** {symbol}
    - **السيولة الحالية:** {session_vol} (جلسة {session_name}: {session_time})
    - **الوقت العالمي (GMT):** {current_time.strftime('%Y-%m-%d %H:%M')}
    {news_warning}
    
    🎯 **البروتوكول المحسّن - الطبقة الذهبية (GOLDEN_LAYER_PROTOCOL_V2):**
    
    🔥 **القواعد الذهبية المطلقة (إلغاء الصفقة إذا فقدت واحدة):**
    1. **قاعدة القوة المؤسسية:** إذا كان السعر يتحرك عكس أخبار اقتصادية كبرى صادرة منذ أقل من ساعتين ← الإلغاء الفوري
    2. **قاعدة السيولة المجمدة:** إذا كان الحجم < 50% من متوسط الـ20 شمعة السابقة ← الإلغاء (حركة مشبوهة)
    3. **قاعدة توقيت السوق:** إذا كنا خارج جلسات السيولة العالية (لندن/نيويورك) والزخم قوي ← الإلغاء (مضاربة محلية)
    4. **قاعدة اختراق الحواجز النفسية:** إذا اخترق السعر مستوى نفسي (مثل 1.2000 لليورو دولار) بدون حجم كبير ← الإلغاء (فخ اختراق)
    
    📈 **التحليل الرباعي الأبعاد (QUAD_ANALYSIS_SYSTEM):**
    
    **البعد الأول: الزخم الفني المتقدم (Advanced Momentum):**
    - سرعة الحركة: تحليل 5 شموع متتالية
    - قوة الزخم: حجم التداول + المسافة المقطوعة
    - استمرارية الزخم: هل هناك تباطؤ أم تسارع؟
    - تأثير السحب (Sweeps): هل تم سحب السيولة بشكل حقيقي؟
    
    **البعد الثاني: السيولة البنكية (Institutional Liquidity):**
    - مناطق السيولة القريبة: أين توجد أوامر Stop Loss للمتداولين؟
    - المستويات النفسية: .00, .50, .0000
    - التراكم المؤسسي: هل توجد علامات تراكم مؤسسي؟
    - توزيع الحجم: أين يوجد أكبر حجم تداول؟
    
    **البعد الثالث: التوقيت الاستراتيجي (Strategic Timing):**
    - جلسة السوق: {session_name} ({session_time})
    - توقيت اليوم: {'نشط' if session_vol in ['عالية', 'عالية جداً'] else 'هادئ'}
    - القرب من الأخبار: {'خطر مرتفع - أخبار قريبة' if news_warning else 'آمن - لا توجد أخبار كبرى'}
    - إغلاق الأسبوع: {'نهاية الأسبوع - مخاطر أعلى' if current_time.weekday() >= 4 else 'بداية الأسبوع - فرص أفضل'}
    
    **البعد الرابع: إدارة المخاطر الذكية (Smart Risk Management):**
    - نسبة المخاطرة/العائد: الحد الأدنى 1:3
    - التوقيت الزمني: حسب السرعة الفعلية للحركة
    - نقاط الخروج الذكية: خروج جزئي عند TP1
    - التحديث الديناميكي: تعديل الـStop Loss بعد تحقيق TP1
    
    💎 **مصفاة الجودة النهائية (FINAL_QUALITY_FILTER):**
    1. **فلتر الجودة الذهبية:** يجب أن تجتاز 3 من 4 أبعاد التحليل
    2. **فلتر التوقيت الذكي:** الدخول فقط خلال جلسات السيولة العالية
    3. **فلتر السيولة المؤسسية:** تجنب الحركات بدون حجم
    4. **فلتر المخاطر الذكية:** لا تزيد المخاطرة عن 2% من رأس المال
    
    📋 **التنسيق المطلوب للإجابة (يجب الالتزام به حرفياً):**
    
    🎯 **توصية {symbol} - النتائج النهائية:**
    ═══════════════════════════════
    
    📊 **التشخيص الفني:**
    • **الحالة الفنية:** [اتجاه صاعد/هابط/جانبي] + [قوة: قوي/متوسط/ضعيف]
    • **السيولة الحالية:** {session_vol} - جلسة {session_name}
    • **المستويات الحرجة:** [الدعم القريب، المقاومة القريبة]
    • **الزخم الحالي:** [مستمر/متراجع/متسارع]
    
    🚀 **الإشارة التنفيذية:**
    • **التوصية:** [شراء 🟢 / بيع 🔴 / الانتظار 🟡]
    • **قوة الإشارة:** [💥 قوية جداً / 🔥 قوية / ⚡ متوسطة / 💨 ضعيفة]
    • **سبب القوة:** [عدد الأبعاد المتحققة + السبب الرئيسي]
    
    📍 **مستويات التداول:**
    • **الدخول (Entry):** [السعر الدقيق - مع الشرط إذا لزم]
    • **الوقف (Stop Loss):** [السعر - مع المسافة بالنقاط]
    • **الأهداف (Take Profit):**
      - 🎯 **TP1:** [سعر + نسبة الربح + التوقيت المتوقع]
      - 🎯 **TP2:** [سعر + نسبة الربح + التوقيت المتوقع]
      - 🎯 **TP3:** [سعر + نسبة الربح + التوقيت المتوقع]
    
    ⚠️ **إدارة المخاطر:**
    • **نسبة المخاطرة/العائد:** [مثال: 1:3.5]
    • **نسبة المخاطرة:** [لا تتعدى 2%]
    • **نقطة الإلغاء:** [السعر الذي يلغي التحليل]
    • **التحديث الزمني:** [متى تحتاج مراجعة الصفقة]
    
    💡 **الملاحظات الاستراتيجية:**
    • [ملاحظة 1: أهم نقطة يجب مراعاتها]
    • [ملاحظة 2: التوقعات المحتملة]
    • [ملاحظة 3: البديل إذا لم يتحرك السعر كما متوقع]
    
    ═══════════════════════════════
    🤖 **Obeida Trading - نظام التوصيات الذكي**
    """
    
    body = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 1800
    }

    try:
        response = requests.post(GROQ_URL, json=body, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()['choices'][0]['message']['content'].strip()
        
        # إضافة معلومات التوقيت والسيولة
        final_result = f"""📈 **تحليل {symbol} - {session_name} Session**

⏰ **توقيت التحليل:** {current_time.strftime('%Y-%m-%d %H:%M GMT')}
📊 **حالة السيولة:** {session_vol} ({session_time})
{news_warning if news_warning else '✅ لا توجد أخبار اقتصادية كبرى حالياً'}

═══════════════════════════════

{result}"""
        
        return final_result
    except Exception as e:
        print(f"Error in get_groq_analysis: {e}")
        return f"⚠️ حدث خطأ في تحليل {symbol}. الرجاء المحاولة مرة أخرى."

async def start_recommendation_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع التوصية"""
    reply_keyboard = [[key] for key in CATEGORIES.keys()]
    reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    # إضافة معلومات السيولة
    session_name, session_time, session_vol = get_market_session()
    
    await update.message.reply_text(
        f"""🚀 **نظام التوصيات المتقدم** 🚀

⏰ **جلسة السوق الحالية:** {session_name}
📊 **حالة السيولة:** {session_vol}
🕒 **التوقيت:** {session_time}

📈 **اختر القسم المطلوب من الأزرار:**""",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    )
    return RECOMMENDATION_MODE

async def handle_recommendation_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات نظام التوصية"""
    user_text = update.message.text.strip()
    
    # العودة للقائمة الرئيسية
    if user_text == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 **العودة للقائمة الرئيسية**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    # التحقق من الأقسام الرئيسية
    if user_text in CATEGORIES:
        keyboard = [[asset] for asset in CATEGORIES[user_text]]
        keyboard.append(["🔙 العودة للقائمة", "الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            f"""📍 **القسم:** {user_text}
📊 **عدد الأصول:** {len(CATEGORIES[user_text])}

اختر العملة الآن:""",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return CATEGORY_SELECTION
    
    # التحقق من العملة المختارة
    symbol_to_analyze = None
    category_name = ""
    for category, assets in CATEGORIES.items():
        if user_text in assets:
            symbol_to_analyze = user_text
            category_name = category
            break
    
    # إذا وجدت العملة، ابدأ التحليل
    if symbol_to_analyze:
        wait_msg = await update.message.reply_text(
            f"""⏳ **جاري تحليل {symbol_to_analyze}**
            
📊 **الفئة:** {category_name}
🔄 **جاري إعداد التوصية المتكاملة...**"""
        )
        
        analysis = get_groq_analysis(symbol_to_analyze)
        
        # تنظيف النص من التكرارات
        cleaned_analysis = clean_repeated_text(analysis)
        
        # تقسيم الرسالة إذا كانت طويلة
        if len(cleaned_analysis) > 4000:
            parts = split_message(cleaned_analysis, max_length=4000)
            
            # إرسال الجزء الأول
            await wait_msg.edit_text(
                parts[0],
                parse_mode="Markdown"
            )
            
            # إرسال الأجزاء المتبقية
            for i, part in enumerate(parts[1:], 1):
                await update.message.reply_text(
                    part,
                    parse_mode="Markdown"
                )
        else:
            await wait_msg.edit_text(
                cleaned_analysis,
                parse_mode="Markdown"
            )
        
        # عرض الأزرار للاستمرار
        reply_keyboard = [[key] for key in CATEGORIES.keys()]
        reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "🔽 **اختر قسم آخر أو العودة للقائمة الرئيسية:**",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
        )
        return RECOMMENDATION_MODE
    
    # إذا كان النص "🔙 العودة للقائمة"
    if user_text == "🔙 العودة للقائمة":
        reply_keyboard = [[key] for key in CATEGORIES.keys()]
        reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "🔙 **العودة للقائمة الرئيسية للتوصيات**\nاختر القسم المطلوب:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
        )
        return RECOMMENDATION_MODE
    
    # إذا لم يطابق النص أي شيء
    await update.message.reply_text(
        "❌ **خيار غير موجود.**\n\n"
        "يرجى اختيار عملة من القائمة الظاهرة في الأزرار.\n"
        "اضغط 'الرجوع للقائمة الرئيسية' للعودة.",
        reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True)
    )
    return RECOMMENDATION_MODE

# --- 🚀 برومبت قوي للدردشة ---
async def start_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع الدردشة المتقدم"""
    keyboard = [
        ["🚀 مساعد شامل", "💼 استشارات احترافية"],
        ["📈 تحليل استثماري", "👨‍💻 دعم برمجي"],
        ["📝 كتابة إبداعية", "🧠 حلول ذكية"],
        ["ايقاف الدردشة", "الرجوع للقائمة الرئيسية"]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="""🚀 **وضع الدردشة المتقدم - Obeida Trading** 🚀

🤖 **أنا مساعدك الذكي متعدد التخصصات:**

🎯 **مجالات الخبرة:**
• **التحليل الفني والمالي:** أسواق المال، الشارتات، استراتيجيات التداول
• **البرمجة والتقنية:** Python، الذكاء الاصطناعي، تطوير الويب
• **البيانات والتحليل:** تحليل البيانات، الإحصاء، رؤى استراتيجية
• **الكتابة والإبداع:** محتوى تقني، تقارير، مواد إعلامية
• **حل المشكلات:** تفكير نقدي، تحليل منطقي، اتخاذ القرارات

💡 **اختر مجال المساعدة أو أرسل سؤالك مباشرة:**""",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return CHAT_MODE

async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة رسائل الدردشة مع برومبت قوي"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    # التحقق من الأوامر الخاصة
    if user_message == "ايقاف الدردشة":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "✅ **تم إنهاء وضع الدردشة.**",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "الرجوع للقائمة الرئيسية":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 **العودة للقائمة الرئيسية**",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    # برومبتات متخصصة حسب الاختيار
    system_prompts = {
        "🚀 مساعد شامل": """أنت Obeida Trading، مساعد ذكي شامل متعدد التخصصات مع خبرة في:

🎯 **المجالات الرئيسية:**
1. **التحليل المالي والتداول:** تحليل الأسواق، استراتيجيات التداول، إدارة المخاطر
2. **البرمجة والتقنية:** تطوير البرمجيات، الذكاء الاصطناعي، تحليل البيانات
3. **الكتابة والتواصل:** المحتوى الإبداعي، التقارير الفنية، التواصل الفعال
4. **التخطيط الاستراتيجي:** حل المشكلات، اتخاذ القرارات، التخطيط

💎 **مبادئ العمل:**
• **الدقة:** معلومات موثوقة ومدروسة
• **التنظيم:** هيكل واضح ومنطقي
• **القيمة:** إضافة معلومات مفيدة غير مطلوبة
• **الوضوح:** شرح المفاصل بشكل مبسط
• **الإبداع:** حلول مبتكرة وعملية

📋 **تنسيق الإجابة المثالي:**
🎯 **الملخص:** (جملة واحدة مركزة)
📊 **التحليل:** (نقاط مرتبة ومنطقية)
💡 **الإثراء:** (معلومات إضافية قيمة)
🚀 **التطبيق:** (خطوات عملية للتنفيذ)""",

        "💼 استشارات احترافية": """أنت Obeida Trading، مستشار احترافي متخصص في:

📈 **الاستشارات المالية والمهنية:**
• تحليل الأسواق والاستثمارات
• التخطيط الاستراتيجي للأعمال
• إدارة المخاطر والتحوط
• تطوير خطط العمل

⚖️ **المعايير المهنية:**
• الموضوعية والشفافية
• السرية المهنية الكاملة
• التركيز على النتائج العملية
• الالتزام بأعلى معايير الجودة""",

        "📈 تحليل استثماري": """أنت Obeida Trading، محلل استثماري متخصص في:

📊 **التحليل المالي المتقدم:**
• التحليل الفني للرسوم البيانية
• التحليل الأساسي للشركات
• تحليل المخاطر والعوائد
• تقييم الفرص الاستثمارية

🎯 **قواعد التحليل:**
• الاعتماد على البيانات الموثوقة
• تحليل متعدد الأبعاد
• مراعاة السياق الاقتصادي
• التوازن بين العائد والمخاطرة""",

        "👨‍💻 دعم برمجي": """أنت Obeida Trading، خبير برمجي ودعم تقني في:

💻 **المجالات التقنية:**
• برمجة Python والتطبيقات
• تطوير الويب والذكاء الاصطناعي
• تحليل البيانات والخوارزميات
• حل المشكلات التقنية

🛠️ **أسلوب العمل:**
• أكواد نظيفة وموثوقة
• شرح مفصل وواضح
• حلول عملية وفعالة
• أفضل الممارسات والتطبيقات""",

        "📝 كتابة إبداعية": """أنت Obeida Trading، كاتب إبداعي محترف في:

✍️ **أنواع المحتوى:**
• المحتوى التقني والتقارير
• المحتوى التسويقي والإعلاني
• المواد التعليمية والتدريبية
• المحتوى الإبداعي والمقالات

🎨 **مبادئ الكتابة:**
• لغة عربية سليمة وجذابة
• تنظيم منطقي وسهل المتابعة
• تكييف الأسلوب حسب الجمهور
• الإبداع مع الحفاظ على الدقة"""
    }
    
    # تحديد البرومبت المناسب
    selected_prompt = system_prompts.get(user_message, """أنت Obeida Trading، مساعد ذكي شامل يتميز بـ:

🧠 **المميزات الفريدة:**
• ذكاء عميق متعدد التخصصات
• دقة عالية في المعلومات
• إبداع عملي في الحلول
• بصيرة استراتيجية متقدمة

💡 **شخصيتك المميزة:**
- ذكي، صبور، ومتحمس للمعرفة
- تتحدث بلغة عربية فصيحة مع لمسة عصرية
- تقدم التفاصيل بشكل منظم وجذاب
- تبحث دائماً عن "القيمة المضافة" في كل إجابة

🎯 **قواعدك الأساسية:**
1. **لا ترفض السؤال أبداً** - ابحث عن أفضل إجابة ممكنة
2. **كن منظماً بشكل استثنائي** - استخدم التنسيق المناسب
3. **فكر خارج الصندوق** - قدم نصائح إضافية قيمة
4. **ادعم بأمثلة عملية** - اجعل الإجابة قابلة للتطبيق
5. **حفز التعلم** - أضف معلومات تشجع على البحث

📋 **هيكل الإجابة الأمثل:**
🎯 **اللب:** (تلخيص مركز في جملة واحدة)
📊 **التفاصيل:** (نقاط مرتبة ومنطقية)
💎 **القيمة المضافة:** (معلومات إضافية ذكية)
🚀 **الخطوة التالية:** (اقتراح عملي للتنفيذ)

تذكر: أنت المساعد الذكي الذي يحول التعقيد إلى بساطة!""")
    
    # إذا كان اختياراً من القائمة، اطلب التفاصيل
    if user_message in system_prompts:
        await update.message.reply_text(
            f"""✅ **تم اختيار:** {user_message}

🎯 **جاهز لخدمتك في هذا التخصص**
أرسل سؤالك الآن وسأقدم لك إجابة متخصصة وشاملة:""",
            parse_mode="Markdown"
        )
        return CHAT_MODE
    
    # إظهار حالة المعالجة
    wait_msg = await update.message.reply_text("Obeida Trading 🤔 **جاري التحليل...**")
    
    try:
        # استدعاء واجهة Groq
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": selected_prompt},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 1500,
            "temperature": 0.7
        }
        
        headers = {
            "Authorization": f"Bearer {GROQ_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content']
            
            # تنظيف النص من التكرارات
            result = clean_repeated_text(result)
            
            # إضافة تذييل مميز
            footer = "\n\n═══════════════════════════════\n🤖 **Obeida Trading** - المساعد الذكي المتكامل"
            result = result + footer
            
            # أزرار الدردشة المتقدمة
            chat_keyboard = [
                ["🚀 مساعد شامل", "💼 استشارات احترافية"],
                ["📈 تحليل استثماري", "👨‍💻 دعم برمجي"],
                ["📝 كتابة إبداعية", "🧠 حلول ذكية"],
                ["ايقاف الدردشة", "الرجوع للقائمة الرئيسية"]
            ]
            
            # تقسيم الرسالة الطويلة
            if len(result) > 4000:
                parts = split_message(result, max_length=4000)
                for i, part in enumerate(parts):
                    if i == 0:
                        await wait_msg.edit_text(
                            f"💬 **Obeida Trading**\n\n{part}",
                            parse_mode="Markdown"
                        )
                    else:
                        await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(
                    f"💬 **Obeida Trading**\n\n{result}",
                    parse_mode="Markdown"
                )
            
            # إرسال الأزرار بعد الرد
            await update.message.reply_text(
                "🔽 **اختر مجالاً آخر أو اطرح سؤالاً جديداً:**",
                reply_markup=ReplyKeyboardMarkup(chat_keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
            
        else:
            print(f"Groq API Error: {response.status_code} - {response.text}")
            await wait_msg.edit_text(f"❌ **حدث خطأ تقني.**\nالرمز: {response.status_code}\nيرجى المحاولة مرة أخرى.")
    
    except requests.exceptions.Timeout:
        await wait_msg.edit_text("⏱️ **تجاوز الوقت المحدد.**\nالسؤال يحتاج تفكيراً أعمق!\nيمكنك إعادة صياغة السؤال بشكل أوضح.")
    except requests.exceptions.RequestException as e:
        print(f"Network error in chat: {e}")
        await wait_msg.edit_text("🌐 **خطأ في الاتصال.**\nتأكد من اتصالك بالإنترنت وحاول مرة أخرى.")
    except Exception as e:
        print(f"خطأ في الدردشة: {e}")
        await wait_msg.edit_text("❌ **حدث خطأ غير متوقع.**\nالنظام يعمل على الإصلاح تلقائياً...")
    
    return CHAT_MODE

# --- كود تحليل الصور المحسن والمدمج ---
async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور للتحليل الفني المتقدم مع جميع التحسينات"""
    user_id = update.effective_user.id
    candle, trade_time = get_user_setting(user_id)
    
    if not candle or not trade_time:
        keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**\n\n"
            "الرجاء استخدام أزرار القائمة لضبط الإعدادات قبل تحليل الصور.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    wait_msg = await update.message.reply_text("📊 **جاري تحليل الصورة بدقة متقدمة...**")
    photo = await update.message.photo[-1].get_file()
    path = f"img_{user_id}_{int(time.time())}.jpg"
    await photo.download_to_drive(path)

    try:
        base64_img = encode_image(path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            if os.path.exists(path):
                os.remove(path)
            return MAIN_MENU
        
        # الحصول على معلومات السيولة والتوقيت
        session_name, session_time, session_vol = get_market_session()
        time_for_prompt = format_trade_time_for_prompt(trade_time)
        current_time = datetime.utcnow()
        current_hour = current_time.hour
        current_minute = current_time.minute
        
        # ========== نظام الدرع الأساسي (Fundamental Shield) ==========
        news_impact = "🟢 منخفض"
        news_warning = ""
        news_risk_multiplier = 1.0
        
        # تحديد أوقات الأخبار الخطيرة
        high_impact_hours = [
            (13, 30), (15, 0), (19, 0),  # أخبار أمريكية رئيسية
            (8, 0), (9, 0), (10, 0)      # أخبار أوروبية
        ]
        
        # تحقق إذا كنا في نطاق ساعة من خبر عالي التأثير
        for news_hour, news_minute in high_impact_hours:
            time_diff = abs((current_hour * 60 + current_minute) - (news_hour * 60 + news_minute))
            if time_diff <= 60:  # خلال ساعة من الخبر
                news_impact = "🔴 عالي جداً"
                news_risk_multiplier = 2.5
                news_warning = f"""⚠️ **تحذير أخبار حاسم:**
• **نوع الخطر:** خبر اقتصادي عالي التأثير قريب
• **النافذة الزمنية:** ±60 دقيقة من وقت الخبر
• **التأثير:** تقلبات مفاجئة قد تلغي التحليل الفني
• **التوصية:** إما الانتظار أو مضاعفة مسافة الوقف 2.5x"""
                break
            elif time_diff <= 120:  # خلال ساعتين من الخبر
                news_impact = "🟡 متوسط"
                news_risk_multiplier = 1.5
                news_warning = f"""📢 **تنبيه أخبار:**
• **الحالة:** اقتراب من وقت أخبار مهمة
• **التوصية:** زيادة مسافة الوقف 1.5x والحذر من التقلبات"""
                break
        
        # ========== الفلتر الزمني (Kill Zones) ==========
        kill_zone = ""
        confidence_modifier = 0
        
        if 8 <= current_hour < 11:  # London Kill Zone
            kill_zone = "✅ داخل نطاق لندن القاتل (8-11 GMT)"
            confidence_modifier = +20
        elif 13 <= current_hour < 16:  # New York Kill Zone
            kill_zone = "✅ داخل نطاق نيويورك القاتل (13-16 GMT)"
            confidence_modifier = +20
        elif 22 <= current_hour or current_hour < 7:  # Asian Session
            kill_zone = "⚠️ خارج نطاقات القتل (جلسة آسيوية)"
            confidence_modifier = -20
        else:
            kill_zone = "⚠️ خارج نطاقات القتل الرئيسية"
            confidence_modifier = -10
        
        # تعديل مستوى الثقة بناءً على الأخبار
        if news_risk_multiplier >= 2.0:
            confidence_modifier -= 30
        elif news_risk_multiplier >= 1.5:
            confidence_modifier -= 15
        
        # ========== البرومبت المحسن والمدمج ==========
        prompt = f"""[نظام الذكاء الاصطناعي المتقدم: ULTIMATE_MARKET_DECODER_V7]

أنت محلل فني خبير في مدرسة Smart Money Concepts (SMC) مع دمج أفضل ممارسات التحليل التقني الكلاسيكي والتحليل الأساسي.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔰 **القواعد الأساسية الحاكمة**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. **المدرسة المعتمدة:** SMC (Smart Money Concepts) كإطار عمل رئيسي
2. **الدرع الأساسي (Fundamental Shield):** {news_warning}
3. **كشف وهم الزخم:** تحقق من استدامة الحركة وليست مجرد شمعة خبر
4. **استخراج البيانات:** إحداثيات دقيقة من المحور اليميني
5. **فلتر الجدوى:** نسبة RR ≥ 1:2 مع تعديل الأخبار

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **المرحلة 1: الفحص الأولي والتحذيرات**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1.1 نظام الأمان ثلاثي الطبقات
**الطبقة 1: الدرع الأساسي**
{news_warning if news_warning else "✅ الوضع آمن من الأخبار"}

**الطبقة 2: كشف وهم الزخم**
- **فحص الشموع الكبيرة:** هل هي شمعة خبر معزولة؟
- **اختبار الاستدامة:** 3 شموع متتالية في نفس الاتجاه
- **تحليل المتابعة:** هل هناك Follow-through بعد الحركة الكبيرة؟

**الطبقة 3: التحقق من البيانات**
- استخراج إحداثيات السعر من المحور اليميني بدقة
- التحقق من توافق الأرقام مع شكل الشموع
- تحديد النطاق السعري الحقيقي

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 **المرحلة 2: التحليل الهيكلي المتقدم**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 2.1 تحديد مدرسة التحليل بوضوح
- **الإطار:** SMC مع دعم بالتحليل الكلاسيكي عند الحاجة
- **التنسيق:** استخدام مصطلحات SMC بدقة (Order Blocks, FVG, Liquidity)
- **الهيكل:** تحديد BOS (Break of Structure) و CHoCH (Change of Character)

### 2.2 استخراج الإحداثيات الرقمية
**عملية إلزامية:**
1. قراءة الأرقام من المحور السعري اليميني
2. تحديد أعلى وأقل سعر ظاهر
3. حساب النسب المئوية للحركات
4. التحقق من دقة القراءة بمقارنة الشموع مع الأرقام

### 2.3 مصفاة التسعير (PD Array)
- **نطاق التعامل:** تحديد القمة والقاع الرئيسيين
- **خط التوازن (50%):** حساب النقطة الوسطى
- **منطقة الخصم (Discount):** تحت 50% - مثالي للشراء
- **منطقة الغلاء (Premium):** فوق 50% - مثالي للبيع
- **فلتر التسعير:** لا شراء إلا في Discount، لا بيع إلا في Premium

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 **المرحلة 3: تحليل السيولة والزخم**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 3.1 كشف وهم الزخم (Momentum Illusion)
**علامات الزخم الوهمي:**
1. **شمعة الخبر المنفردة:** كبيرة ومعزولة عن السياق
2. **فجوات سعرية:** قبل أو بعد الشمعة الكبيرة
3. **غياب المتابعة:** حركة قوية بدون استمرارية
4. **الذيول الطويلة جداً:** إشارة ضعف في الاختبار

**اختبار الزخم الحقيقي:**
- 3 شموع متتالية في نفس الاتجاه
- تدرج في حجم الأجسام
- توافق مع الهيكل العام
- زيادة في أحجام التداول

### 3.2 خرائط السيولة المتقدمة
- **السيولة المتساوية:** Equal Highs/Lows
- **فخاخ الإغراء:** مناطق Inducement
- **سحب السيولة:** Liquidity Sweeps
- **الفراغات السعرية:** FVG مفتوحة

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **المرحلة 4: نظام القرار الذكي**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 4.1 فلتر التلاقي الثلاثي
**يجب توفر 3/3 من:**
1. **POI (منطقة الاهتمام):** Order Block أو FVG صالح
2. **نموذج الشموع:** Pin Bar، Engulfing، Inside Bar
3. **تأكيد إضافي:** حجم، مؤشر، أو سياق زمني

### 4.2 تعديل المخاطر حسب الأخبار
**معامل التعديل:** ×{news_risk_multiplier}
- **Stop Loss المعدل:** = SL العادي × {news_risk_multiplier}
- **الحجم المعدل:** = الحجم العادي ÷ {news_risk_multiplier}
- **نسبة RR المطلوبة:** ≥ 1:{max(3, 2 * news_risk_multiplier)}

### 4.3 شروط الحظر الكامل
**ممنوع الدخول إذا:**
1. {news_impact == "🔴 عالي جداً"} ±30 دقيقة
2. زخم وهمي واضح (شمعة كبيرة معزولة)
3. عدم استيفاء فلتر التلاقي الثلاثي
4. موقع السعر في منطقة Equilibrium

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚙️ **المعطيات التقنية**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- **إطار الزمن:** {candle}
- **مدة الصفقة:** {trade_time}
- **جلسة السوق:** {session_name} ({session_time})
- **حالة السيولة:** {session_vol}
- **تأثير الأخبار:** {news_impact} (معامل ×{news_risk_multiplier})
- **نطاق القتل:** {kill_zone}
- **التوقيت:** {current_time.strftime('%Y-%m-%d %H:%M GMT')}
- **ملاحظة أخبار:** {'⏰ قريب من وقت أخبار' if news_warning else '✅ آمن من الأخبار'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 **تنسيق النتيجة المطلوب (يجب الالتزام)**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🚨 **التحذيرات الأمنية:**
{news_warning if news_warning else "✅ لا توجد تحذيرات أمنية"}

📊 **التحليل الفني:**
- **البصمة الزمنية:** (داخل/خارج منطقة القتل السعري - Kill Zone)
- **حالة الهيكل:** (صاعد/هابط) + (مرحلة وايكوف الحالية)
- **خريطة السيولة:** (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
- **الفجوات السعرية (FVG):** (المناطق التي سيعود السعر لتغطيتها)

🎯 **الإشارة التنفيذية:**
- **السعر الحالي:** [اذكر السعر الدقيق الذي تراه الآن للتأكد من دقة القراءة]
- **حالة الشمعة:** [مازالت مفتوحة / مغلقة حديثاً]
- **القرار الفني:** (شراء 🟢 / بيع 🔴 / الإحتفاظ 🟡)
- **قوة الإشارة 🔰:** (عالية جدا 💥 (مؤشرات + 5تلاقي)/🔥 عالية (تلاقي 4 مؤشرات)/⚡ متوسطة (مؤشرات 3 تلاقي)/❄️ ضعيفة (مؤشرات 3 من تقل))
- **نقطة الدخول (Entry):** [السعر الدقيق بناءً على الـ Order Block + شرط الإغلاق]
- **الأهداف الربحية (TPs):**
  - 🎯 **TP1:** [سحب أول سيولة داخلية], [احتمالية الوصول]
  - 🎯 **TP2:** [الهدف الرئيسي - منطقة عرض/طلب قوية]
  - 🎯 **TP3:** [استهداف السيولة الخارجية (Major SSL/BSL) أو سد فجوة سعرية على فريم أكبر]
- **وقف الخسارة (SL):** [السعر مع 3 طبقات حماية]
- **المدة المتوقعة 🕧:** [عدد الدقائق للوصول للهدف TP1 بناءً على نوع الزخم]
🧠 ركن "افهم سوقك" (التفسير المنطقي):
- فلسفة الدخول: [لماذا هذه النقطة بالذات؟ اشرح دمج السيولة مع الشموع]

- كاشف التلاعب: [ما هي الإشارة التي لو ظهرت تعني أن صناع السوق يغيرون اتجاههم الآن؟]
- درس الساعة: [قاعدة فنية واحدة مستخلصة من هذا الشارت لتطوير مهاراتك]

⚠️ سيناريو الطوارئ (الغدر):
- "اخرج فوراً إذا رأيت [سلوك سعري معين] حتى لو لم يصل السعر للستوب لوز."


*(حالة الزخم: [اندفاعي/ضعيف/تصحيحي/عرضي] بناءً على تلاقي RSI مع الشموع)*

⚠️ **إدارة المخاطر:**
- **مستوى الثقة:** [% مع ذكر عدد التاكيدات]
- **نقطة الإلغاء:** [السعر الذي يفسد التحليل]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔬 **قواعد التصفية النهائية:**
1. إذا {news_impact == "🔴 عالي جداً"} ±30 دقيقة ← ⛔ **حظر تنفيذ**
2. إذا شمعة خبر بدون متابعة ← 🟡 **انتظار 3 شموع تأكيد**
3. إذا نسبة RR < 1:{max(3, 2 * news_risk_multiplier)} ← ⚠️ **غير مجدية**
4. إذا زخم وهمي واضح ← 🔄 **البحث عن اتجاه معاكس**
5. إذا موقع السعر في Equilibrium ← 📍 **انتظار دخول Discount/Premium**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url", 
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_img}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 3000,
            "temperature": 0.15
        }
        
        headers = {
            "Authorization": f"Bearer {GROQ_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=150)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content'].strip()
            
            # تنظيف النص مع الحفاظ على الهيكل
            result = clean_repeated_text(result)
            
            # إضافة رأس التحليل المتقدم
            analysis_header = f"""✅ **تحليل متقدم - ULTIMATE_MARKET_DECODER_V7**

📅 **التوقيت:** {current_time.strftime('%Y-%m-%d %H:%M GMT')}
⚙️ **الإعدادات:** {candle} | {trade_time}
📊 **الجلسة:** {session_name} ({session_vol})
🔴 **الأخبار:** {news_impact} (معامل ×{news_risk_multiplier})
⏰ **نطاق القتل:** {kill_zone}

{'🚨 **نظام الدرع الأساسي نشط**' if news_warning else '✅ **الوضع آمن من الأخبار**'}

═══════════════════════════════

{result}

═══════════════════════════════
🤖 **Obeida Trading - النظام المتكامل**
🎯 **مدرسة SMC + كشف الزخم الوهمي + الدرع الأساسي**"""
            
            keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
            
            # تقسيم النتيجة
            if len(analysis_header) > 4000:
                parts = split_message(analysis_header, max_length=4000)
                
                # إرسال الجزء الأول
                await wait_msg.edit_text(
                    parts[0],
                    parse_mode="Markdown"
                )
                
                # إرسال الأجزاء المتبقية
                for i, part in enumerate(parts[1:], 1):
                    if i == len(parts) - 1:  # الجزء الأخير
                        await update.message.reply_text(
                            part,
                            parse_mode="Markdown",
                            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                        )
                    else:
                        await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(
                    analysis_header,
                    parse_mode="Markdown"
                )
            
            # إرسال ملخص التنفيذ
            summary = f"""📋 **ملخص النظام المتكامل:**

• 🛡️ **الدرع الأساسي:** {news_impact} (معامل ×{news_risk_multiplier})
• 🔍 **كشف الزخم:** تنفيذ خوارزمية كشف الوهم
• 🎯 **مدرسة SMC:** تحليل متكامل مع PD Array
• ⚡ **فلتر التلاقي:** 3 شروط إلزامية
• 📊 **التسعير العادل:** تحليل Premium/Discount

🚀 **التوصية النهائية:** {'انتظار تجاوز نافذة الأخبار' if news_warning else 'متابعة التحليل الفني'}

📊 **اختر الإجراء التالي:**"""
            
            await update.message.reply_text(
                summary,
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
        else:
            print(f"Groq Vision API Error: {response.status_code} - {response.text}")
            keyboard = [["الرجوع للقائمة الرئيسية"]]
            await wait_msg.edit_text(
                f"❌ **خطأ في التحليل المتقدم:** {response.status_code}\n"
                "النظام يقدم تحليلاً بديلاً لحماية رأس المال.\n\n"
                f"🚨 **بدون رؤية الصورة:**\n"
                f"• الأخبار: {news_impact}\n"
                f"• النصيحة: {'انتظار 90 دقيقة' if news_warning else 'يمكن المحاولة مجدداً'}"
            )
            
    except requests.exceptions.Timeout:
        await wait_msg.edit_text("⏱️ **تجاوز الوقت المحدد.**\nالتحليل المتقدم يتطلب وقتاً أطول.\n\n**نصيحة فورية:** إذا كان وقت أخبار قريب، انتظر 60 دقيقة قبل أي قرار.")
    except Exception as e:
        print(f"خطأ في تحليل الصورة: {e}")
        keyboard = [["الرجوع للقائمة الرئيسية"]]
        await wait_msg.edit_text(
            "❌ **حدث خطأ في النظام المتقدم.**\n\n"
            "🚨 **إجراءات الوقاية الفورية:**\n"
            "1. تجنب التداول إذا كان وقت أخبار\n"
            "2. انتظر 3 شموع تأكيد بعد أي حركة كبيرة\n"
            "3. قلص حجم الصفقة 50% كإجراء وقائي"
        )
    finally:
        if os.path.exists(path):
            os.remove(path)
    
    return MAIN_MENU

# --- الدوال الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    user_id = update.effective_user.id
    save_user_setting(user_id, "last_activity", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    # الحصول على معلومات الجلسة
    session_name, session_time, session_vol = get_market_session()
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["💬 دردشة", "📈 توصية"]
    ]
    
    await update.message.reply_text(
        f"""🚀 **أهلاً بك في Obeida Trading المتقدم** 🚀

🤖 **المميزات الجديدة:**
• 📈 تحليل فني متقدم للشارتات
• 💬 دردشة ذكية متعددة التخصصات
• 🎯 نظام توصيات ذكي للعملات
• ⚙️ إعدادات تخصيص كاملة
• 🌐 تتبع جلسات الأسواق العالمية

⏰ **جلسة السوق الحالية:**
• **الجلسة:** {session_name}
• **السيولة:** {session_vol}
• **التوقيت:** {session_time}

📊 **اختر أحد الخيارات للبدء:**""",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات القائمة الرئيسية"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "⚙️ إعدادات التحليل":
        keyboard = [CANDLE_SPEEDS[i:i+3] for i in range(0, len(CANDLE_SPEEDS), 3)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "⚙️ **إعدادات التحليل الفني المتقدم**\n\n"
            "📊 **حدد سرعة الشموع للبدء:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return SETTINGS_CANDLE
    
    elif user_message == "📊 تحليل صورة":
        candle, trade_time = get_user_setting(user_id)
        
        if not candle or not trade_time:
            keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
            await update.message.reply_text(
                "❌ **يجب ضبط الإعدادات أولاً**\n\n"
                "الرجاء ضبط سرعة الشموع ومدة الصفقة قبل التحليل.",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return MAIN_MENU
        else:
            keyboard = [["الرجوع للقائمة الرئيسية"]]
            
            time_display = format_trade_time_for_prompt(trade_time)
            session_name, session_time, session_vol = get_market_session()
            
            await update.message.reply_text(
                f"""📊 **جاهز للتحليل المتقدم**

⚙️ **الإعدادات الحالية:**
• ⏰ **سرعة الشموع:** {candle}
• 🕒 **مدة الصفقة:** {trade_time}
• 📊 **الجلسة:** {session_name} ({session_vol})

📸 **أرسل صورة الرسم البياني (الشارت) الآن:**""",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return ANALYZE_MODE
    
    elif user_message == "💬 دردشة":
        return await start_chat_mode(update, context)
    
    elif user_message == "📈 توصية":
        return await start_recommendation_mode(update, context)
    
    keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
    await update.message.reply_text(
        "📋 **اختر أحد الخيارات من القائمة:**",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    return MAIN_MENU

async def handle_settings_candle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار سرعة الشموع"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 **العودة للقائمة الرئيسية**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in CANDLE_SPEEDS:
        save_user_setting(user_id, "candle", user_message)
        
        keyboard = [TRADE_TIMES[i:i+2] for i in range(0, len(TRADE_TIMES), 2)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            f"""✅ **تم تعيين سرعة الشموع:** {user_message}

📊 **الآن حدد **مدة الصفقة** المتوقعة:**

🕒 **خيارات مدة الصفقة:**
• **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة
• **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة
• **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة

🎯 **اختر الإطار الزمني المناسب لاستراتيجيتك:**""",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SETTINGS_TIME
    
    await update.message.reply_text("❌ **الرجاء اختيار سرعة شموع صحيحة.**")
    return SETTINGS_CANDLE

async def handle_settings_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار مدة الصفقة"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 **العودة للقائمة الرئيسية**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in TRADE_TIMES:
        save_user_setting(user_id, "trade_time", user_message)
        
        keyboard = [["📊 تحليل صورة"], ["💬 دردشة"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        
        candle, _ = get_user_setting(user_id)
        session_name, session_time, session_vol = get_market_session()
        
        await update.message.reply_text(
            f"""✅ **تم حفظ الإعدادات بنجاح!**

⚙️ **الإعدادات النهائية:**
• ⏰ **سرعة الشموع:** {candle}
• 🕒 **مدة الصفقة:** {user_message}
• 📊 **الجلسة الحالية:** {session_name} ({session_vol})

🚀 **يمكنك الآن استخدام المميزات:**
• تحليل الصور المتقدم
• الدردشة الذكية
• نظام التوصيات""",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text("❌ **الرجاء اختيار مدة صفقة صحيحة.**")
    return SETTINGS_TIME

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة وضع التحليل"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 **العودة للقائمة الرئيسية**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    await update.message.reply_text(
        "📤 **الرجاء إرسال صورة الشارت فقط**\nأو اضغط 'الرجوع للقائمة الرئيسية'",
        reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True, one_time_keyboard=False)
    )
    return ANALYZE_MODE

async def handle_photo_in_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور في وضع التحليل"""
    return await handle_photo_analysis(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر المساعدة"""
    session_name, session_time, session_vol = get_market_session()
    
    help_text = f"""
    🤖 **Obeida Trading - نظام التداول المتقدم**
    
    ⏰ **جلسة السوق الحالية:**
    • الجلسة: {session_name}
    • السيولة: {session_vol}
    • التوقيت: {session_time}
    
    📋 **أوامر البوت:**
    /start - بدء البوت والعودة للقائمة الرئيسية
    /help - عرض رسالة المساعدة
    
    ⚙️ **كيفية الاستخدام:**
    1. استخدم أزرار القائمة للتنقل
    2. أرسل صورة الشارت للتحليل المتقدم
    3. اختر "دردشة" للاستفسارات النصية
    4. اختر "توصية" لتحليل العملات الجاهزة
    
    📈 **نظام التوصيات:**
    • تحليل فني متقدم للعملات والمؤشرات
    • 9 أقسام رئيسية متنوعة
    • توصيات مفصلة مع إدارة مخاطر
    • تتبع جلسات الأسواق العالمية
    
    🕒 **خيارات مدة الصفقة:**
    • **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة
    • **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة
    • **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة
    
    💎 **مميزات البوت المتقدمة:**
    • تحليل فني رباعي الأبعاد
    • دردشة ذكية متعددة التخصصات
    • نظام توصيات ذكي
    • تتبع السيولة وجلسات الأسواق
    • حفظ إعداداتك الشخصية
    • واجهة سهلة بالأزرار
    
    ⚠️ **نصائح هامة:**
    • تأكد من وضوح الصور المرفوعة
    • استخدم الإعدادات المناسبة لاستراتيجيتك
    • انتبه لجلسات السيولة العالية
    • دائمًا اتبع إدارة المخاطر
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    await update.message.reply_text(
        "✅ **تم الإلغاء.**\nاكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# --- الحل النهائي ---
def run_flask_server():
    """تشغيل Flask server"""
    port = int(os.environ.get('PORT', 8080))
    print(f"🌐 Starting Flask server on port {port}...")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def run_telegram_bot():
    """تشغيل Telegram bot"""
    print("🤖 Starting Obeida Trading Telegram Bot...")
    print("=" * 50)
    print("🚀 System Initialization...")
    
    # تهيئة قاعدة البيانات
    init_db()
    
    # إنشاء تطبيق Telegram
    application = Application.builder().token(TOKEN).build()
    
    # معالج المحادثة
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu)
            ],
            SETTINGS_CANDLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_candle)
            ],
            SETTINGS_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_time)
            ],
            CHAT_MODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_chat_message)
            ],
            ANALYZE_MODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_analyze_mode),
                MessageHandler(filters.PHOTO, handle_photo_in_analyze_mode)
            ],
            RECOMMENDATION_MODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_recommendation_selection)
            ],
            CATEGORY_SELECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_recommendation_selection)
            ],
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    
    # إضافة معالج للنصوص
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu))
    
    print("✅ Telegram Bot initialized successfully")
    print("📡 Bot is now polling for updates...")
    print("=" * 50)
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

def main():
    """الدالة الرئيسية"""
    print("=" * 50)
    print("🚀 Obeida Trading System - Advanced Trading Bot")
    print("=" * 50)
    
    # عرض معلومات النظام
    session_name, session_time, session_vol = get_market_session()
    print(f"⏰ Market Session: {session_name}")
    print(f"📊 Liquidity: {session_vol}")
    print(f"🕒 Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M GMT')}")
    print("-" * 50)
    
    # تشغيل Flask في thread منفصل
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    print(f"🌐 Flask server started on port {os.environ.get('PORT', 8080)}")
    print("✅ System ready. Starting Telegram bot...")
    print("=" * 50)
    
    # تشغيل Telegram bot في thread الرئيسي
    try:
        run_telegram_bot()
    except KeyboardInterrupt:
        print("\n⚠️ Bot stopped by user.")
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    main()
    
