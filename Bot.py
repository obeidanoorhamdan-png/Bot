import logging
import base64
import os
import sqlite3
import re
import requests
import threading
import time
import sys
import asyncio
import telegram
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from flask import Flask

# --- الإعدادات ---
TOKEN = os.environ.get('TOKEN', "7324911542:AAFqB9NRegwE2_bG5rCTaEWocbh8N3vgWeo")
MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "EABRT5zGsHYhezkaJJomt15VR2iBrPWq")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
DB_NAME = "abood-gpt.db"

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["S3", "S15", "S30", "M1", "M3", "M5", "M30", "H1", "H4", "H24", "⏱️ وقت يدوي"]

# توزيع العملات للنظام الجديد
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


# حالات المحادثة
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, SETTINGS_MANUAL_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION = range(8)

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
        </style>
    </head>
    <body>
        <h1> 📊 Obeida Trading Telegram Bot 📊</h1>
        <p>Chat & Technical Analysis Bot</p>
        <div class="status">✅ Obeida Trading Running</div>
        <p>Last Ping: """ + time.strftime("%Y-%m-%d %H:%M:%S") + """</p>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return {"status": "active", "timestamp": time.time()}

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
            candle TEXT DEFAULT 'M5', 
            trade_time TEXT DEFAULT 'H1',
            manual_time TEXT DEFAULT '',
            chat_context TEXT DEFAULT ''
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
    cursor.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (val, user_id))
    conn.commit()
    conn.close()

def get_user_setting(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT candle, trade_time, manual_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        return res
    return ("M1", "M5", "")

# --- دوال معالجة الوقت اليدوي ---
def parse_manual_time(time_str):
    """تحويل النص المدخل إلى وقت بالتنسيق 00:00:00"""
    try:
        if re.match(r'^\d{1,2}:\d{2}:\d{2}$', time_str):
            hours, minutes, seconds = map(int, time_str.split(':'))
            if 0 <= hours <= 23 and 0 <= minutes <= 59 and 0 <= seconds <= 59:
                return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        
        elif 'يوم' in time_str or 'يومين' in time_str or 'أيام' in time_str:
            days = 0
            if 'يومين' in time_str:
                days = 2
            elif 'يوم' in time_str:
                numbers = re.findall(r'\d+', time_str)
                if numbers:
                    days = int(numbers[0])
                else:
                    days = 1
            return f"{days} يوم"
        
        elif 'ساعة' in time_str or 'ساعات' in time_str:
            hours = 0
            numbers = re.findall(r'\d+', time_str)
            if numbers:
                hours = int(numbers[0])
            else:
                hours = 1
            return f"{hours} ساعة"
        
        elif 'دقيقة' in time_str or 'دقائق' in time_str:
            minutes = 0
            numbers = re.findall(r'\d+', time_str)
            if numbers:
                minutes = int(numbers[0])
            else:
                minutes = 1
            return f"{minutes} دقيقة"
        
        elif 'ثانية' in time_str or 'ثواني' in time_str:
            seconds = 0
            numbers = re.findall(r'\d+', time_str)
            if numbers:
                seconds = int(numbers[0])
            else:
                seconds = 1
            return f"{seconds} ثانية"
        
        elif time_str.isdigit():
            hours = int(time_str)
            return f"{hours} ساعة"
            
    except Exception as e:
        print(f"Error parsing manual time: {e}")
    
    return None

def format_trade_time_for_prompt(trade_time, manual_time=""):
    """تنسيق وقت الصفقة للبرومبت"""
    if trade_time == "⏱️ وقت يدوي" and manual_time:
        return f"مدة الصفقة المتوقعة: {manual_time} (مدخل يدوي)"
    else:
        return f"مدة الصفقة المتوقعة: {trade_time}"

# --- معالجة الصور ---
def encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

# --- دوال المساعدة للتعامل مع النصوص ---
def clean_repeated_text(text):
    """تنظيف النص من التكرارات"""
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    
    unique_paragraphs = []
    seen_paragraphs = set()
    
    for paragraph in paragraphs:
        simplified = paragraph[:100].strip()
        if simplified not in seen_paragraphs:
            unique_paragraphs.append(paragraph)
            seen_paragraphs.add(simplified)
    
    cleaned_text = '\n\n'.join(unique_paragraphs)
    
    if len(cleaned_text) > 2000:
        if '\n\n' in cleaned_text[:2200]:
            cut_point = cleaned_text[:2200].rfind('\n\n')
            cleaned_text = cleaned_text[:cut_point]
        else:
            cleaned_text = cleaned_text[:2000] + "..."
    
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
def get_mistral_analysis(symbol):
    """الحصول على تحليل من Mistral للعملة"""
    headers = {
        "Authorization": f"Bearer {MISTRAL_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    بصفتك محللًا ماليًا خبيرًا، قم بتحليل {symbol} بناءً على مراحل القوة التالية:

المرحلة 1 (السياق): حلل الاتجاه على الفريم اليومي (صورة كبيرة) وفريم 4 ساعات (نقطة دخول).
المرحلة 2 (المؤشرات): ادمج مستويات الدعم/المقاومة مع مؤشر RSI وحالة المتوسطات المتحركة.
المرحلة 3 (السيناريوهات): حدد ماذا يحدث في حال اختراق المقاومة أو كسر الدعم.

التنسيق المطلوب للرد (باللغة العربية فقط):

 * التوقعات *
- اسم العملة: {symbol}
- الاتجاه العام: (صاعد 🟢 / هابط 🔴 / عرضي 🟡)
- السعر الحالي: [أدخل السعر]
- مستوى الثقة: XX٪ (بناءً على تلاقي المؤشرات)
- الأهداف (TP): 
   1. هدف أول: 
   2. هدف ثاني: 
- وقف الخسارة (SL): [نقطة الخروج الضرورية]
- مبرر الدخول: (ذكر سبب فني واحد باختصار)
- مدة استهداف صفقة: 
    """
    
    body = {
        "model": "mistral-medium",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }

    try:
        response = requests.post(MISTRAL_URL, json=body, headers=headers, timeout=25)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"Error in get_mistral_analysis: {e}")
        return "⚠️ حدث خطأ في الاتصال بالمحلل."

async def start_recommendation_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع التوصية"""
    reply_keyboard = [[key] for key in CATEGORIES.keys()]
    reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    await update.message.reply_text(
        "🚀 **نظام التوصيات **\n\n"
        "اختر القسم المطلوب من الأزرار:",
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
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    # التحقق من الأقسام الرئيسية
    if user_text in CATEGORIES:
        keyboard = [[asset] for asset in CATEGORIES[user_text]]
        keyboard.append(["🔙 العودة للقائمة", "الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            f"📍 قسم: {user_text}\nاختر العملة الآن:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return CATEGORY_SELECTION
    
    # التحقق من العملة المختارة
    symbol_to_analyze = None
    for category_list in CATEGORIES.values():
        if user_text in category_list:
            symbol_to_analyze = user_text
            break
    
    # إذا وجدت العملة، ابدأ التحليل
    if symbol_to_analyze:
        wait_msg = await update.message.reply_text(f"⏳ جاري إرسال توصيات `{symbol_to_analyze}`...")
        analysis = get_mistral_analysis(symbol_to_analyze)
        
        final_msg = (
            f"📈 **نتائج توصية {symbol_to_analyze}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{analysis}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Obeida Trading - نظام التوصيات**"
        )
        
        # تنظيف النص من التكرارات
        final_msg = clean_repeated_text(final_msg)
        
        await wait_msg.edit_text(
            final_msg,
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
        "❌ خيار غير موجود. يرجى اختيار عملة من القائمة الظاهرة في الأزرار.\n\n"
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
        text="🚀 **وضع الدردشة Obeida Trading**\n\n"
             "أنا مساعدك الذكي متعدد المواهب:\n"
             "• مستشار استثماري وتحليلات مالية\n"
             "• خبير برمجي وتقني\n"
             "• محلل بيانات واستراتيجيات\n"
             "• كاتب محتوى إبداعي\n"
             "• مساعد شخصي ذكي\n\n"
             "اختر مجال المساعدة أو أرسل سؤالك مباشرة:",
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
            "✅ تم إنهاء وضع الدردشة.",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "الرجوع للقائمة الرئيسية":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    # برومبتات متخصصة حسب الاختيار
    system_prompts = {
        "🚀 مساعد ذكي شامل": """أنت Obeida Trading، مساعد ذكي شامل يمتلك معرفة عميقة في:
🎯 **التحليل الفني والمالي:** خبرة في أسواق المال، تحليل الشارتات، واستراتيجيات التداول
💻 **البرمجة والتقنية:** إتقان Python، JavaScript، تطوير الويب، الذكاء الاصطناعي
📊 **البيانات والتحليل:** تحليل البيانات، الإحصاء، وتقديم رؤى استراتيجية
✍️ **الكتابة والإبداع:** صياغة المحتوى، التقارير، والمواد الإعلامية
🧠 **التفكير النقدي:** حل المشكلات المعقدة، التحليل المنطقي، واتخاذ القرارات

**مبادئك الأساسية:**
1. **الدقة أولاً:** معلومات موثوقة ومدروسة
2. **التنظيم:** هيكل واضح مع عناوين ونقاط
3. **القيمة المضافة:** تقديم نصائح إضافية غير مطلوبة
4. **الوضوح:** شرح المفاهيم المعقدة ببساطة
5. **الإبداع:** حلول مبتكرة للمشكلات

**تنسيق الإجابة المثالي:**
🎯 **الجوهر:** (ملخص سريع)
📋 **التفاصيل:** (نقاط مرتبة)
💡 **الإثراء:** (معلومات إضافية مفيدة)
🚀 **التطبيق:** (خطوات عملية)

استخدم اللغة العربية بطلاقة مع لمسة عصرية وجذابة.""",

        "💼 استشارات احترافية": """أنت Obeida Trading، مستشار احترافي في:
📈 **الاستشارات المالية:** تحليل الأسواق، تقييم المخاطر، استراتيجيات الاستثمار
👔 **التخطيط الاستراتيجي:** تحليل SWOT، وضع الأهداف، متابعة الأداء
🤝 **العلاقات المهنية:** التواصل الفعال، التفاوض، بناء الشبكات
📋 **إدارة المشاريع:** التخطيط، التنفيذ، المتابعة، التقييم

**التزاماتك المهنية:**
• الموضوعية والشفافية
• احترام السرية المهنية
• التطوير المستمر
• الالتزام بالأخلاقيات المهنية
• التركيز على النتائج العملية""",

        "📈 تحليل استثماري": """أنت Obeida Trading، محلل استثماري متخصص في:
📊 **التحليل الفني:** قراءة الشارتات، المؤشرات الفنية، أنماط التداول
📉 **التحليل الأساسي:** الأرباح، القوائم المالية، المؤشرات الاقتصادية
🎯 **إدارة المخاطر:** تحديد المخاطر، التحوط، موازنة المحفظة
🔍 **البحث والتنقيب:** فرص الاستثمار، اتجاهات السوق، التنبؤات

**قواعد التحليل:**
• اعتماد البيانات الرسمية والموثوقة
• تحليل متعدد الأبعاد
• مراعاة السياق الاقتصادي
• التوازن بين العائد والمخاطرة
• الشفافية في الافتراضات""",

        "👨‍💻 دعم برمجي": """أنت Obeida Trading، مبرمج خبير ودعم تقني في:
🐍 **Python:** تطبيقات الويب، الذكاء الاصطناعي، تحليل البيانات
🌐 **تطوير الويب:** Frontend, Backend, APIs, Databases
🤖 **الذكاء الاصطناعي:** Machine Learning, NLP, Computer Vision
🛠️ **حل المشكلات:** Debugging, Optimization, Best Practices

**أسلوب العمل:**
• كتابة أكواد نظيفة وموثوقة
• شرح المفاهيم البرمجية بوضوح
• تقديم حلول عملية وفعالة
• تعليم أفضل الممارسات
• دعم التعلم المستمر""",

        "📝 كتابة إبداعية": """أنت Obeida Trading، كاتب إبداعي محترف في:
📄 **المحتوى التقني:** تقارير، أبحاث، مستندات فنية
🎨 **المحتوى التسويقي:** إعلانات، حملات، محتوى وسائل التواصل
📚 **المحتوى التعليمي:** شروحات، دورات، مواد تعليمية
✒️ **الكتابة الإبداعية:** قصص، مقالات، محتوى ممتع

**مبادئ الكتابة:**
• لغة عربية سليمة وجذابة
• تنظيم منطقي وسهل المتابعة
• تكييف الأسلوب حسب الجمهور
• الإبداع مع الحفاظ على الدقة
• جذب الانتباه والإقناع"""
    }
    
    # تحديد البرومبت المناسب
    selected_prompt = system_prompts.get(user_message, """أنت Obeida Trading، مساعد ذكي شامل يمتلك مزيجاً فريداً من:
🧠 **الذكاء العميق:** فهم شامل لمجالات متعددة
🎯 **الدقة الشديدة:** معلومات موثوقة ومدروسة بدقة
🚀 **الإبداع العملي:** حلول مبتكرة وقابلة للتطبيق
💡 **البصيرة الاستراتيجية:** رؤية أعمق من السؤال المطروح

**شخصيتك المميزة:**
- ذكي، صبور، ومتحمس للمعرفة
- تتحدث بلغة عربية فصيحة مع لمسة عصرية
- تحب التفاصيل ولكن تقدمها بشكل منظم
- دائماً تبحث عن "القيمة المخفية" في كل سؤال

**قواعدك الأساسية:**
1. **لا تقل أبداً "لا أعرف"** - ابحث عن أفضل إجابة ممكنة
2. **كن منظماً بشكل ممتاز** - استخدم التبويب والعناوين المناسبة
3. **فكر في ما وراء السؤال** - قدم نصائح إضافية غير متوقعة
4. **ادعم بأمثلة عملية** - اجعل الإجابة قابلة للتطبيق
5. **حفز الفضول** - أضف معلومة تشجع على البحث أكثر

**هيكل الإجابة الأمثل:**
🎯 **اللب:** (تلخيص مركز في جملة واحدة)
📊 **التفاصيل المنظمة:** (نقاط مرتبة ومنطقية)
💎 **القيمة المضافة:** (معلومات إضافية ذكية)
🚀 **الخطوة التالية:** (اقتراح عملي للتنفيذ)

**تذكر جيداً:** أنت Obeida Trading، المساعد الذكي الذي يحول التعقيد إلى بساطة، ويمنحك دائماً أكثر مما تطلب!""")
    
    # إذا كان اختياراً من القائمة، اطلب التفاصيل
    if user_message in system_prompts:
        await update.message.reply_text(
            f"✅ **تم اختيار: {user_message}**\n\n"
            f"🎯 **جاهز لخدمتك في هذا التخصص**\n"
            f"أرسل سؤالك الآن وسأقدم لك إجابة متخصصة وشاملة:",
            parse_mode="Markdown"
        )
        return CHAT_MODE
    
    # إظهار حالة المعالجة
    wait_msg = await update.message.reply_text("Obeida Trading 🤔...")
    
    try:
        # استدعاء واجهة Mistral
        payload = {
            "model": "mistral-medium",
            "messages": [
                {"role": "system", "content": selected_prompt},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 1200,
            "temperature": 0.7
        }
        
        headers = {
            "Authorization": f"Bearer {MISTRAL_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content']
            
            # تنظيف النص من التكرارات
            result = clean_repeated_text(result)
            
            # إضافة تذييل مميز
            footer = "\n\n━━━━━━━━━━━━━━━━━━\n🤖 **Obeida Trading** - المساعد الذكي "
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
                            f"Obeida Trading 💬\n\n{part}",
                            parse_mode="Markdown"
                        )
                    else:
                        await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(
                    f"Obeida Trading 💬\n\n{result}",
                    parse_mode="Markdown"
                )
            
            # إرسال الأزرار بعد الرد
            await update.message.reply_text(
                "🔽 **اختر مجالاً آخر أو اطرح سؤالاً جديداً:**",
                reply_markup=ReplyKeyboardMarkup(chat_keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
            
        else:
            print(f"Mistral API Error: {response.status_code} - {response.text}")
            await wait_msg.edit_text(f"❌ حدث خطأ تقني. الرمز: {response.status_code}\nيرجى المحاولة مرة أخرى.")
    
    except requests.exceptions.Timeout:
        await wait_msg.edit_text("⏱️ تجاوز الوقت المحدد. السؤال يحتاج تفكيراً أعمق!\nيمكنك إعادة صياغة السؤال بشكل أوضح.")
    except requests.exceptions.RequestException as e:
        print(f"Network error in chat: {e}")
        await wait_msg.edit_text("🌐 خطأ في الاتصال. تأكد من اتصالك بالإنترنت وحاول مرة أخرى.")
    except Exception as e:
        print(f"خطأ في الدردشة: {e}")
        await wait_msg.edit_text("❌ حدث خطأ غير متوقع. النظام يعمل على الإصلاح تلقائياً...")
    
    return CHAT_MODE

# --- كود تحليل الصور ---
async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور للتحليل الفني"""
    user_id = update.effective_user.id
    candle, trade_time, manual_time = get_user_setting(user_id)
    
    if not candle or not trade_time:
        keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**\n\n"
            "الرجاء استخدام أزرار القائمة لضبط الإعدادات قبل تحليل الصور.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    wait_msg = await update.message.reply_text("جاري تحليل صورة 📊...")
    photo = await update.message.photo[-1].get_file()
    path = f"img_{user_id}.jpg"
    await photo.download_to_drive(path)

    try:
        base64_img = encode_image(path)
        
        # تنسيق وقت الصفقة للبرومبت
        time_for_prompt = format_trade_time_for_prompt(trade_time, manual_time)
        
        # برومبت آمن للتحليل الفني
        prompt = f"""
        [SYSTEM_TASK: INSTITUTIONAL_STRUCTURE_DECRYPTION_V2]
    بصفتك خوارزمية تحليل مالي احترافية، قم بتشريح الشارت المرفق وفق 'بروتوكول المراحل الست' لضمان دقة 100%:

    المرحلة 1: تشفير الهيكل (Market Structure) - تحديد BOS، CHoCH، والقمم/القاع.
    المرحلة 2: خريطة السيولة (Liquidity Mapping) - تحديد مناطق سحب السيولة و Equal Highs/Lows.
    المرحلة 3: تحليل المكونات (S&D Zones) - تحديد الـ Order Blocks والـ Fair Value Gaps (FVG).
    المرحلة 4: تقييم القوة والضعف (Momentum Analysis) - قياس حدة الزخم البيعي مقابل الشرائي.
    المرحلة 5: وضع السيناريوهات (Scenarios) - بناء سيناريو الدخول، الهدف، ووقف الخسارة.
    المرحلة 6: التحديث والإبطال (Invalidation) - تحديد السعر الذي يلغي النظرة الفنية.

    الإعدادات الفنية:
    - فريم الشموع: {candle}
    - مدة التداول: {time_for_prompt}

    قدم الإجابة باختصار شديد باللغة العربية حصراً وفق التنسيق التالي:

    📊 التحليل الفني :
    - النمط السائد:
    - مستويات الدعم/المقاومة الحرج:
    - فجوات السيولة المرصودة:

    🎯 التوقع التنفيذي:
    - الإتجاه: (صعود ⬆️ / نزول ⬇️ / ثابت ➡️)
    - التوصية: (بيع 🔴 / شراء 🟢 / إحتفاظ 🟡)
    - قوة الإتجاه المتوقعة: (عالي 🔥 / متوسط ⚡ / منخفض ❄️) -> [هذا المعيار يحدد جودة الصفقة]
    - السعر الحالي:
    - نقطة الدخول الذهبية:
    - الهدف الأول (TP1):
    - الهدف الثاني (TP2):
    - وقف الخسارة (SL):
    - مستوى الثقة الرقمي: %
    - مدة استهداف الصفقة:

    ⚠️ التحذيرات والمخاطر:
    - نقطة بطلان التحليل (Invalidation Point):
    - المخاطر المحتملة (تلاعب، ضعف فوليوم، أخبار):
        """
        
        payload = {
            "model": "mistral-large-latest",
            "messages": [
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                    ]
                }
            ],
            "max_tokens": 800,
            "temperature": 0.3
        }
        
        headers = {
            "Authorization": f"Bearer {MISTRAL_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=45)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content']
            
            # ✅ حل مشكلة التكرار: تنظيف النص من التكرار
            result = clean_repeated_text(result)
            
            keyboard = [["📊 تحليل صورة"], ["💬 دردشة"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
            
            # تنسيق وقت الصفقة للعرض
            time_display = format_trade_time_for_prompt(trade_time, manual_time)
            
            # إعداد النص النهائي مع الإعدادات
            full_result = (
                f"✅ **تم التحليل بنجاح!**\n"
                f"📈 **نتائج تحليل الشارت:**\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{result}\n\n"
                f"📊 **الإعدادات المستخدمة:**\n"
                f"• سرعة الشموع: {candle}\n"
                f"• {time_display}"
            )
            
            # تقسيم النتيجة إذا كانت طويلة
            if len(full_result) > 4000:
                parts = split_message(full_result, max_length=4000)
                
                # إرسال الجزء الأول مع تعديل الرسالة المنتظرة
                await wait_msg.edit_text(
                    parts[0],
                    parse_mode="Markdown"
                )
                
                # إرسال الأجزاء المتبقية
                for part in parts[1:]:
                    await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(
                    full_result,
                    parse_mode="Markdown"
                )
            
            # إرسال الأزرار
            await update.message.reply_text(
                "📊 **اختر الإجراء التالي:**",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
        else:
            print(f"Mistral Vision API Error: {response.status_code} - {response.text}")
            keyboard = [["الرجوع للقائمة الرئيسية"]]
            await wait_msg.edit_text(
                f"❌ **خطأ في إرسال الصورة:** {response.status_code}\n"
                f"يرجى المحاولة مرة أخرى.",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
            
    except requests.exceptions.Timeout:
        keyboard = [["الرجوع للقائمة الرئيسية"]]
        await wait_msg.edit_text(
            "⏱️ تجاوز الوقت المحدد إرسال الصورة. حاول مرة أخرى.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
    except Exception as e:
        print(f"خطأ في تحليل الصورة: {e}")
        keyboard = [["الرجوع للقائمة الرئيسية"]]
        await wait_msg.edit_text(
            f"❌ **حدث خطأ في إرسال الصورة.**\n"
            f"يرجى التأكد من وضوح الصورة والمحاولة مرة أخرى.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
    finally:
        if os.path.exists(path):
            os.remove(path)
    
    return MAIN_MENU

# --- الدوال الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["💬 دردشة", "📈 توصية"]
    ]
    
    await update.message.reply_text(
        "🚀 **أهلاً بك في Obeida Trading - لتوصيات \n\n"
        "🤖 **المميزات الجديدة:**\n"
        "• تحليل فني متقدم للشارتات\n"
        "• 🆕 دردشة \n"
        "• 📈 نظام توصيات جاهزة\n"
        "• إعدادات تخصيص كاملة\n"
        "اختر أحد الخيارات:",
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
            "⚙️ **إعدادات التحليل الفني**\n\n"
            "حدد سرعة الشموع للبدء:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return SETTINGS_CANDLE
    
    elif user_message == "📊 تحليل صورة":
        candle, trade_time, manual_time = get_user_setting(user_id)
        
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
            
            time_display = format_trade_time_for_prompt(trade_time, manual_time)
            
            await update.message.reply_text(
                f"📊 **جاهز للتحليل**\n\n"
                f"الإعدادات الحالية:\n"
                f"• سرعة الشموع: {candle}\n"
                f"• {time_display}\n\n"
                f"أرسل صورة الرسم البياني (الشارت) الآن:",
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
        "اختر أحد الخيارات من القائمة:",
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
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in CANDLE_SPEEDS:
        save_user_setting(user_id, "candle", user_message)
        
        keyboard = [TRADE_TIMES[i:i+3] for i in range(0, len(TRADE_TIMES), 3)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            f"✅ **تم تعيين سرعة الشموع:** {user_message}\n\n"
            f"الآن حدد **مدة الصفقة** المتوقعة:\n\n"
            f"يمكنك اختيار:\n"
            f"• أحد الأوقات الجاهزة\n"
            f"• ⏱️ وقت يدوي (لتحديد وقت مخصص)",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SETTINGS_TIME
    
    await update.message.reply_text("❌ الرجاء اختيار سرعة شموع صحيحة.")
    return SETTINGS_CANDLE

async def handle_settings_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار مدة الصفقة"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in TRADE_TIMES:
        if user_message == "⏱️ وقت يدوي":
            keyboard = [["الرجوع للقائمة الرئيسية"]]
            
            await update.message.reply_text(
                "⏱️ **إدخال وقت يدوي**\n\n"
                "📝 **أرسل وقت الصفقة يدوياً بإحدى الطرق:**\n\n"
                "1. **تنسيق الوقت:** 00:00:00 (ساعات:دقائق:ثواني)\n"
                "   مثال: 02:30:00 (ساعتين ونصف)\n"
                "   مثال: 00:15:00 (15 دقيقة)\n"
                "   مثال: 00:00:30 (30 ثانية)\n\n"
                "2. **كتابة نصي:**\n"
                "   مثال: 2 ساعة\n"
                "   مثال: 30 دقيقة\n"
                "   مثال: 3 أيام\n"
                "   مثال: 45 ثانية\n\n"
                "3. **أرقام فقط:**\n"
                "   مثال: 4 (سيتم اعتبارها 4 ساعات)\n\n"
                "❌ للإلغاء، اضغط 'الرجوع للقائمة الرئيسية'",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return SETTINGS_MANUAL_TIME
        else:
            save_user_setting(user_id, "trade_time", user_message)
            save_user_setting(user_id, "manual_time", "")
            
            keyboard = [["📊 تحليل صورة"], ["💬 دردشة"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
            
            candle, _, _ = get_user_setting(user_id)
            
            await update.message.reply_text(
                f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n"
                f"✅ سرعة الشموع: {candle}\n"
                f"✅ مدة الصفقة: {user_message}\n\n"
                f"يمكنك الآن تحليل صورة أو الدردشة:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return MAIN_MENU
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

async def handle_manual_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال الوقت يدوياً"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    parsed_time = parse_manual_time(user_message)
    
    if parsed_time:
        save_user_setting(user_id, "trade_time", "⏱️ وقت يدوي")
        save_user_setting(user_id, "manual_time", parsed_time)
        
        keyboard = [["📊 تحليل صورة"], ["💬 دردشة"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        
        candle, _, _ = get_user_setting(user_id)
        
        await update.message.reply_text(
            f"⏱️ **تم حفظ الوقت اليدوي بنجاح!**\n\n"
            f"✅ سرعة الشموع: {candle}\n"
            f"✅ مدة الصفقة: {parsed_time} (مدخل يدوي)\n\n"
            f"يمكنك الآن تحليل صورة أو الدردشة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    else:
        keyboard = [["الرجوع للقائمة الرئيسية"]]
        await update.message.reply_text(
            "❌ **تنسوق وقت غير صحيح!**\n\n"
            "📝 **أعد الإدخال بإحدى الطرق:**\n\n"
            "1. **تنسيق الوقت:** 00:00:00 (ساعات:دقائق:ثواني)\n"
            "   مثال: 02:30:00 (ساعتين ونصف)\n\n"
            "2. **كتابة نصي:**\n"
            "   مثال: 2 ساعة\n"
            "   مثال: 30 دقيقة\n\n"
            "3. **أرقام فقط:**\n"
            "   مثال: 4 (سيتم اعتبارها 4 ساعات)",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SETTINGS_MANUAL_TIME

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة وضع التحليل"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
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
    help_text = """
    🤖 **أوامر البوت:**
    
    /start - بدء البوت والعودة للقائمة الرئيسية
    /help - عرض رسالة المساعدة
    
    ⚙️ **كيفية الاستخدام:**
    1. استخدم أزرار القائمة للتنقل
    2. أرسل صورة الشارت للتحليل
    3. اختر "دردشة" للاستفسارات النصية
    4. اختر "توصية" لتحليل العملات
    
    📈 **نظام التوصيات:**
    • تحليل فني للعملات والمؤشرات
    • أربعة أقسام رئيسية
    • توصيات مفصلة لكل عملة
    • تحليل سريع ومباشر
    
    ⏱️ **خاصية الوقت اليدوي:**
    • يمكنك تحديد وقت الصفقة يدوياً
    • التنسيقات المدعومة:
      - 00:00:00 (ساعات:دقائق:ثواني)
      - عدد الأيام (مثال: 2 يوم)
      - عدد الساعات (مثال: 3 ساعة)
      - عدد الدقائق (مثال: 45 دقيقة)
      - عدد الثواني (مثال: 30 ثانية)
    
    📊 **مميزات البوت:**
    • تحليل فني للرسوم البيانية
    • دردشة ذكية مع الذكاء الاصطناعي
    • نظام توصيات العملات
    • حفظ إعداداتك الشخصية
    • واجهة سهلة بالأزرار
    • إدخال وقت مخصص يدوياً
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    await update.message.reply_text(
        "تم الإلغاء. اكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# --- معالج الأخطاء ---
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأخطاء العام"""
    print(f"⚠️ Error occurred: {context.error}")
    
    # تجاهل أخطاء التعارض المؤقتة
    if isinstance(context.error, telegram.error.Conflict):
        print("⚠️ Conflict error ignored (another instance might be running)")
        return
    
    try:
        if update and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ حدث خطأ غير متوقع. جاري إعادة التشغيل تلقائياً..."
            )
    except:
        pass

# --- الحل النهائي ---
def run_flask_server():
    """تشغيل Flask server"""
    port = int(os.environ.get('PORT', 10000))
    print(f"🌐 Trying to start Flask server on port {port}...")
    
    # حاول استخدام منافذ بديلة إذا كان 10000 مشغولاً
    for p in range(port, port + 5):
        try:
            app.run(host='0.0.0.0', port=p, debug=False, use_reloader=False)
            break
        except OSError as e:
            if "Address already in use" in str(e):
                print(f"⚠️ Port {p} is in use, trying next port...")
                continue
            else:
                raise e

def cleanup_bot_sessions():
    """تنظيف جلسات البوت القديمة"""
    try:
        # إنشاء bot مؤقت لتنظيف الجلسات
        temp_bot = telegram.Bot(token=TOKEN)
        
        # حذف Webhook إن وجد
        result = temp_bot.delete_webhook(drop_pending_updates=True)
        print("✅ Deleted any existing webhook")
        
        # الحصول على معلومات البوت للتأكد من اتصاله
        bot_info = temp_bot.get_me()
        print(f"✅ Bot verified: {bot_info.first_name} (@{bot_info.username})")
        
        return True
    except telegram.error.Conflict as e:
        print(f"⚠️ Conflict during cleanup: {e}")
        print("⚠️ Another bot instance might be running. Waiting 5 seconds...")
        time.sleep(5)
        return False
    except Exception as e:
        print(f"⚠️ Cleanup warning: {e}")
        return True  # نواصل حتى مع وجود أخطاء في التنظيف

def run_telegram_bot():
    """تشغيل Telegram bot"""
    print("🤖 Starting Telegram Bot...")
    
    # تنظيف الجلسات القديمة
    max_cleanup_attempts = 3
    for attempt in range(max_cleanup_attempts):
        print(f"Attempt {attempt + 1}/{max_cleanup_attempts} to clean bot sessions...")
        if cleanup_bot_sessions():
            print("✅ Bot sessions cleaned successfully")
            break
        elif attempt == max_cleanup_attempts - 1:
            print("❌ Failed to clean bot sessions after multiple attempts")
            print("⚠️ Trying to continue anyway...")
    
    # تهيئة قاعدة البيانات
    init_db()
    
    # إنشاء تطبيق Telegram
    application = Application.builder().token(TOKEN).build()
    
    # إضافة معالج الأخطاء
    application.add_error_handler(error_handler)
    
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
            SETTINGS_MANUAL_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_manual_time)
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
    
    # إضافة معالج للنصوص العامة
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu))
    
    print("✅ Telegram Bot initialized successfully")
    print("📡 Bot is now polling for updates...")
    
    # تشغيل البوت مع إعدادات صحيحة
    # إصلاح: إزالة المتغيرات غير المدعومة في run_polling()
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,  # مهم جداً لحل مشكلة التعارض
        poll_interval=0.5,
        timeout=30,
        bootstrap_retries=3,
        close_loop=False
    )

def main():
    """الدالة الرئيسية"""
    print("🚀 Starting Obeida Trading...")
    print(f"📅 {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # تشغيل Flask في thread منفصل
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    print(f"🌐 Flask server started in background")
    print("🔧 Waiting 3 seconds for Flask to initialize...")
    time.sleep(3)
    
    # تشغيل Telegram bot
    try:
        run_telegram_bot()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"❌ Critical error: {e}")
        print("🔄 Restarting in 10 seconds...")
        time.sleep(10)
        main()  # إعادة التشغيل

if __name__ == "__main__":
    main()
