import logging
import base64
import os
import sqlite3
import re
import requests
import threading
import time
import sys
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from flask import Flask

# --- الإعدادات ---
TOKEN = os.environ.get('TOKEN', "7324911542:AAGcVkwzjtf3wDB3u7cprOLVyoMLA5JCm8U")

# ⚡ إعدادات Mistral AI API الجديدة
MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "WhGHh0RvwtLLsRwlHYozaNrmZWkFK2f1")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "pixtral-large-latest"
MISTRAL_MODEL_AUDIT = "mistral-large-pixtral-2411"  # موديل التدقيق

DB_NAME = "abood-gpt.db"

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

# توزيع العملات للنظام الجديد
CATEGORIES = {
    "أزواج العملات 🏛️": [
        "EUR/USD (OTC)", "GBP/USD (OTC)", "USD/JPY (OTC)", "USD/CHF (OTC)",
        "AUD/USD (OTC)", "USD/CAD (OTC)", "NZD/USD (OTC)", "EUR/GBP (OTC)",
        "EUR/JPY (OTC)", "GBP/JPY (OTC)", "EUR/CHF (OTC)", "AUD/JPY (OTC)",
        "EUR/AUD (OTC)", "EUR/CAD (OTC)", "GBP/AUD (OTC)", "CAD/JPY (OTC)",
        "CHF/JPY (OTC)", "NZD/JPY (OTC)", "GBP/CHF (OTC)", "AUD/CAD (OTC)"
    ],
    "مؤشرات الأسواق 📊": [
        "S&P 500 (OTC)", "Dow Jones (OTC)", "Nasdaq 100 (OTC)", 
        "DAX 40 (OTC)", "CAC 40 (OTC)", "FTSE 100 (OTC)", 
        "Hang Seng (OTC)", "Nikkei 225 (OTC)"
    ],
    "سلع وطاقة 🕯️": [
        "Gold (OTC)", "Silver (OTC)", "UKOIL (OTC)", 
        "USOIL (OTC)", "Natural Gas (OTC)"
    ],
    "أسهم الشركات 🍎": [
        "Apple (OTC)", "Amazon (OTC)", "Google (OTC)", "Facebook (OTC)",
        "Microsoft (OTC)", "Tesla (OTC)", "Netflix (OTC)", "Intel (OTC)",
        "Boeing (OTC)", "Visa (OTC)", "McDonald's (OTC)", "Pfizer (OTC)",
        "Coca-Cola (OTC)", "Disney (OTC)", "Alibaba (OTC)", "Walmart (OTC)"
    ]
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
        </style>
    </head>
    <body>
        <h1> 📊 Obeida Trading Telegram Bot 📊</h1>
        <p>Chat & Technical Analysis Bot</p>
        <div class="status">✅ Obeida Trading Running</div>
        <p>Last Ping: """ + time.strftime("%Y-%m-%d %H:%M:%S") + """</p>
        <p>Obeida Trading - (Dual Model System)</p>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return {"status": "active", "ai_provider": "Mistral AI", "model": f"{MISTRAL_MODEL} + {MISTRAL_MODEL_AUDIT}", "timestamp": time.time()}

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
    cursor.execute("SELECT candle, trade_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        return res
    return ("M1", "قصير (1m-15m)")

def get_market_session():
    current_hour = (datetime.utcnow() + timedelta(hours=2)).hour  # توقيت غزة

    if 2 <= current_hour < 8:
        return "الجلسة الآسيوية", "02:00-08:00 بتوقيت غزة", "منخفضة"
    elif 8 <= current_hour < 14:
        return "جلسة لندن/أوروبا", "08:00-14:00 بتوقيت غزة", "مرتفعة"
    elif 14 <= current_hour < 20:
        return "جلسة نيويورك", "14:00-20:00 بتوقيت غزة", "عالية جداً"
    elif 20 <= current_hour < 24 or 0 <= current_hour < 2:
        return "جلسة المحيط الهادئ", "20:00-02:00 بتوقيت غزة", "منخفضة"
    else:
        return "جلسة عالمية", "متداخلة", "متوسطة"
        
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
    
    if "📊 **نتائج الفحص الفني**:" in text:
        text = re.sub(r'(📊 \*\*نتائج الفحص الفني\*\*:[\s\S]*?)(?=📊 \*\*نتائج الفحص الفني\*\*:)', '', text, flags=re.DOTALL)
    
    if "### تحليل الشارت المرفق" in text:
        sections = text.split("### تحليل الشارت المرفق")
        if len(sections) > 1:
            text = "### تحليل الشارت المرفق" + sections[1]
    
    patterns_to_clean = [
        r'📊\s*\*\*التحليل الفني\*\*:',
        r'🎯\s*\*\*التوصية والتوقعات\*\*:',
        r'⚠️\s*\*\*إدارة المخاطر\*\*:',
        r'📝\s*\*\*ملاحظات التحليل\*\*:'
    ]
    
    for pattern in patterns_to_clean:
        matches = re.findall(pattern, text)
        if len(matches) > 1:
            parts = re.split(pattern, text)
            if len(parts) > 1:
                text = parts[0] + re.search(pattern, text).group() + parts[1]
                for i in range(2, len(parts)):
                    text += parts[i]
    
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    
    unique_paragraphs = []
    seen_paragraphs = set()
    
    for paragraph in paragraphs:
        key = paragraph[:50].strip().lower()
        if key not in seen_paragraphs:
            unique_paragraphs.append(paragraph)
            seen_paragraphs.add(key)
    
    cleaned_text = '\n\n'.join(unique_paragraphs)
    
    if len(cleaned_text) > 2000:
        if '\n\n' in cleaned_text[:2200]:
            cut_point = cleaned_text[:2200].rfind('\n\n')
            cleaned_text = cleaned_text[:cut_point] + "\n\n📋 ...تم اختصار النتيجة"
        else:
            cleaned_text = cleaned_text[:2000] + "...\n\n📋 تم اختصار النتيجة"
    
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
    """الحصول على تحليل من Mistral AI API للعملة"""
    headers = {
        "Authorization": f"Bearer {MISTRAL_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    بصفتك خبير تداول كمي، حلل {symbol} بناءً على "تلاقي الأدلة" (Confluence Analysis). 
    
    🛑 **شروط الفلترة الصارمة (إلغاء الصفقة فوراً إذا لم تتحقق):**
    1. حتمية الاستنفاذ: فشل آخر موجة جهد في كسر الهيكل.
    2. توافق الفركتلات: تطابق الاتجاه على فريمات (H4, H1, M15).
    3. سحب السيولة (Sweep): يجب حدوث كسر وهمي للسيولة قبل الدخول.
    4. منطقة التوازن (OTE): الدخول حصراً بين مستويات فيبوناتشي 0.618 و 0.886.

    🔍 **المطلوب تحليل (SMC + Wyckoff + Volume Profile):**
    - رصد الـ Order Block النشط و الـ FVG غير المغطى.
    - تحديد منطقة الفخ (Inducement) والسيولة المستهدفة (BSL/SSL).
    - حساب قوة الاتجاه باستخدام (RSI Divergence) وحجم التداول.

    قدم التقرير باللغة العربية بهذا التنسيق حصراً:
    
    📊 **ملخص فحص {symbol}**:
    - **الهيكل**: (صاعد/هابط/تجميع) | **السيولة**: (أقرب فخ + الهدف القادم)
    - **الفجوات**: (أهم منطقة FVG نشطة)
    
    🎯 **خطة التنفيذ**:
    - **القرار**: (شراء 🟢 / بيع 🔴) | **القوة**: (عالية/متوسطة/ضعيفة)
    - **الدخول**: [السعر الدقيق] | **الهدف (TP1/TP2)**: [مستويات السيولة]
    - **الوقف (SL)**: [خلف منطقة الحماية] | **الزمن**: [الوقت المتوقع بالدقائق]
    
    ⚠️ **المخاطرة**:
    - **الثقة**: [%] | **نقطة الإلغاء**: [السعر الذي يفسد السيناريو]
    """
    
    body = {
        "model": MISTRAL_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 1500
    }

    try:
        response = requests.post(MISTRAL_URL, json=body, headers=headers, timeout=30)
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
            f"🤖 **Powered by - Obeida Trading**"
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
        "🚀 مساعد شامل": """أنت Obeida Trading، مساعد ذكي شامل يمتلك معرفة عميقة في:
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
        # استدعاء واجهة Mistral AI
        payload = {
            "model": MISTRAL_MODEL,
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
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content']
            
            # تنظيف النص من التكرارات
            result = clean_repeated_text(result)
            
            # إضافة تذييل مميز
            footer = "\n\n━━━━━━━━━━━━━━━━━━\n🤖 **Obeida Trading** - Powered by Obeida Trading 🤖"
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
            print(f"Obeida Trading Error: {response.status_code} - {response.text}")
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

# --- كود تحليل الصور المحسن والمدمج الكامل مع نظام الموديل المزدوج ---
async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور للتحليل الفني المتقدم مع نظام الموديل المزدوج - الإصدار المحسّن"""
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

    wait_msg = await update.message.reply_text("📊 جاري تحليل شارت بتقنيات متطورة ... ")
    photo = await update.message.photo[-1].get_file()
    path = f"img_{user_id}_{int(time.time())}.jpg"
    
    try:
        await photo.download_to_drive(path)
        base64_img = encode_image(path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            if os.path.exists(path):
                os.remove(path)
            return MAIN_MENU
        
        # الحصول على معلومات السيولة والتوقيت
        session_name, session_time, session_vol = get_market_session()
        current_time = datetime.utcnow()
        current_hour = current_time.hour
        current_minute = current_time.minute
        
        # ========== نظام الدرع الأساسي (Fundamental Shield) ==========
        news_impact = "🟢 منخفض"
        news_warning = ""
        news_risk_multiplier = 1.0
        
        # تحديد أوقات الأخبار الخطيرة
        high_impact_hours = [
            (14, 30), (16, 0), (20, 0),  # أخبار أمريكية
            (8, 0), (9, 0), (10, 0),     # أخبار أوروبية
            (2, 30), (4, 0),             # أخبار يابانية وآسيوية
            (17, 30),                    # EIA النفط
        ]
        
        for news_hour, news_minute in high_impact_hours:
            time_diff = abs((current_hour * 60 + current_minute) - (news_hour * 60 + news_minute))
            if time_diff <= 60:
                news_impact = "🔴 عالي جداً"
                news_risk_multiplier = 2.5
                news_warning = f"⚠️ **تحذير:** خبر اقتصادي قوي خلال ±60 دقيقة"
                break
            elif time_diff <= 120:
                news_impact = "🟡 متوسط"
                news_risk_multiplier = 1.5
                news_warning = f"📢 **تنبيه:** اقتراب من وقت أخبار مهمة"
                break
        
        # ========== الفلتر الزمني (Kill Zones) ==========
        kill_zone_status = ""
        if 10 <= current_hour < 13:
            kill_zone_status = "داخل منطقة القتل السعري (لندن 10-13 بتوقيت غزة)"
        elif 15 <= current_hour < 18:
            kill_zone_status = "داخل منطقة القتل السعري (نيويورك 15-18 بتوقيت غزة)"
        elif 0 <= current_hour < 9 or current_hour >= 22:
            kill_zone_status = "خارج منطقة القتل (جلسة آسيوية)"
        else:
            kill_zone_status = "خارج مناطق القتل الرئيسية"
        
        # ========== ربط معطيات الإعدادات ==========
        candle_category = ""
        if candle.startswith('S'):
            candle_category = "فريمات سريعة جداً (ثواني) - حركات سريعة وانعكاسات مفاجئة"
        elif candle.startswith('M'):
            candle_category = "فريمات متوسطة (دقائق) - حركات متوسطة السرعة"
        elif candle.startswith('H'):
            candle_category = "فريمات بطيئة (ساعات) - حركات بطيئة وثابتة"
        elif candle.startswith('D'):
            candle_category = "فريمات طويلة (يومي) - اتجاهات طويلة الأمد"
        
        trading_strategy = ""
        position_sizing = ""
        
        if trade_time == "قصير (1m-15m)":
            trading_strategy = "تداول سكالبينج (Scalping) - دخول وخروج سريع"
            position_sizing = "حجم كبير نسبياً مع وقف خسارة ضيق"
        elif trade_time == "متوسط (4h-Daily)":
            trading_strategy = "تداول سوينج (Swing) - متوسط الأجل"
            position_sizing = "حجم معتدل مع وقف خسارة متوسط"
        elif trade_time == "طويل (Weekly-Monthly)":
            trading_strategy = "تداول موقف (Position) - طويل الأجل"
            position_sizing = "حجم صغير مع وقف خسارة واسع"
        
        # ========== تحديد فريم التحقق الديناميكي ==========
        # بناءً على إعدادات المستخدم، نحدد الفريم المناسب للتحقق
        verification_timeframe = ""
        
        # تحويل candle setting إلى قيمة رقمية للتحقق
        candle_value = candle[1:] if candle.startswith(('S', 'M', 'H', 'D')) else candle
        
        # تحديد الفريم الأنسب للتحقق بناءً على سرعة الشموع
        if candle.startswith('S'):  # ثواني
            if candle_value in ['5', '10', '15']:
                verification_timeframe = "S15"  # فريم 15 ثانية للثواني الصغيرة
            else:
                verification_timeframe = "S30"  # فريم 30 ثانية للثواني الكبيرة
        elif candle.startswith('M'):  # دقائق
            if int(candle_value) <= 5:
                verification_timeframe = "M1"  # فريم 1 دقيقة للدقائق الصغيرة
            elif int(candle_value) <= 15:
                verification_timeframe = "M5"  # فريم 5 دقائق للدقائق المتوسطة
            else:
                verification_timeframe = "M15"  # فريم 15 دقيقة للدقائق الكبيرة
        elif candle.startswith('H'):  # ساعات
            verification_timeframe = "H1"  # فريم 1 ساعة للساعات
        elif candle.startswith('D'):  # يومي
            verification_timeframe = "H4"  # فريم 4 ساعات لليومي
        
        # ========== إعدادات ثابتة ==========
        GENERATION_CONFIG = {
            "max_tokens": 910,
            "temperature": 0.0,
            "top_p": 1.0,
            "random_seed": 42
        }
        
        # ========== البرومبت الرئيسي المحدث ==========
        MAIN_PROMPT = f"""
أنت محلل فني خبير SMC + ICT (أسهم، صناديق، سلع، كريبتو، فوركس، OTC). مهمتك تحليل الشارت المرفق بدقة بكسلية وإصدار توصيات تنفيذية دقيقة جداً وفق منطق السيولة والزخم والسلوك السعري.

🔰 القواعد الأساسية
• المدرسة: SMC + ICT + دعم كلاسيكي + فلاتر الأرقام المستديرة  
• الدرع الأساسي: {news_warning if news_warning else "✅ الوضع آمن من الأخبار"}  
• التصنيف الزمني: {candle_category}  
• استراتيجية التداول: {trading_strategy}  
• إدارة الحجم: {position_sizing}  
• أولوية الزخم: شموع ابتلاعية ≥80% + إغلاق فوق القمة السابقة = استمرار  
• منطق OTC: 3 شموع قوية → الشمعة الرابعة بنفس الاتجاه  
• تصحيح الفريم الصغير: تجاهل MACD عند التعارض مع السلوك السعري، فقط تأكيد ثانوي  
• كشف وهم الزخم: تحقق من استدامة الحركة  
• استخراج البيانات: أسعار دقيقة من المحور اليمني  
• فلتر الجدوى: RR ≥ 1:2 بعد تعديل الأخبار  
• المصداقية المطلقة: لا إشارة إلا عند وضوح 100%  
• تقييد الوسطية: قرار واضح فقط (شراء/بيع/احتفاظ) مع مستوى الثقة  

🛡️ طبقات الحماية من الانعكاس الفاشل
- ضد القطار السريع: لا دخول عكس الاتجاه إلا بعد شمعة ابتلاعية ≥100%  
- تأكيد البكسل للكسر: لا BOS إلا إذا أغلق جسم الشمعة بالكامل، لمس الذيل = Liquidity Sweep  
- تباعد الحجم والسعر: صعود أجسام الشموع تتناقص = زخم وهمي  
- قاعدة الانتظار الزمني: في OTC، انتظر إغلاق شمعتين بعد POI  
- فلتر السيولة الزمنية: منع الدخول خارج جلسات السيولة العالية أو آخر 15 دقيقة إلا بكسر هيكلي واضح + 3 شموع  
- فلتر التوازن السعري: منع الدخول قرب خط 50% بدون كسر هيكلي أو امتصاص  
- فلتر التشبع السعري: لا دخول بعد 3 شموع متتالية بنفس الاتجاه بدون تصحيح  
- فلتر الانعكاس الهيكلي الزائف: CHoCh/BOS صحيح فقط مع سحب سيولة + إغلاق خلف الهيكل + متابعة بشمعة ثانية  
- توافق الإطارات: تنفيذ فقط عند توافق 3/4+ أو HTF متوافق مع POI وOB  
- تأكيد RR النهائي: RR ≥ 1:2 بعد مراجعة الأخبار والتقلبات  

- فلتر الأرقام المستديرة (Round Numbers):  
"يُمنع دخول عكس الاتجاه (بيع) عند اقتراب السعر من رقم صحيح (مثلاً .000 أو .500) بمسافة أقل من 5 نقاط، إلا بعد حدوث كسر هيكلي حقيقي (CHoCh) على فريم الـ {verification_timeframe}، لأن الخوارزمية غالباً ما تستهدف السيولة فوق هذه الأرقام."

- قاعدة الاستهداف الرقمي (Digital Targeting Rule):  
"إذا كان السعر يبعد أقل من 10 نقاط عن رقم صحيح (.000) وزخم الشموع صاعد، فإن الهدف الأول والوحيد هو ملامسة هذا الرقم، وتُلغى كافة إشارات البيع قبله."

- قاعدة "دقيقة الغدر" (Last Minute Rule):  
"في سوق OTC، إذا كانت الشمعة الحالية هي آخر دقيقة في إغلاق ساعة أو نصف ساعة (مثل الدقيقة 59 أو 29)، يتم تجاهل إشارات الانعكاس تماماً، والأولوية لاستمرار الزخم (Momentum Continuity) لأن المنصات تضخ سيولة لضرب مناطق الـ Stop Loss عند الإغلاقات الكبرى."

- فلتر "الفجوة المندفعة" (Impulsive Gap):  
"إذا اخترق السعر منطقة عرض/طلب بشمعة Marubozu (بدون ذيول)، يتم إلغاء أي سيناريو بيع فوراً واعتبار الاختراق حقيقياً (BOS) وليس فخاً (Sweep)، مع انتظار إعادة الاختبار للدخول مع الاتجاه."

- قاعدة الحظر القطعي (Premium/Discount):  
"لا شراء في منطقة Premium (فوق خط 50% من الموجة) ولا بيع في منطقة Discount (تحت خط 50%) إلا بعد كسر هيكلي حقيقي (CHoCh) أو امتصاص واضح للسيولة."

- ميزان القوى (Structure vs Momentum):  
"إذا تعارض اتجاه الهيكل العام مع زخم آخر 5 شموع (بأجسام ممتلئة)، تُلغى صفقات الارتداد ويتم الدخول مع استمرار الزخم (Trend Following) حتى الوصول لأقرب FVG أو رقم مستدير."

- قاعدة السيولة العميقة (Deep Liquidity Sweep Rule):  
"أي ذيل طويل يخترق قاعاً أو قمة سابقة ثم يغلق السعر داخل النطاق خلال شمعتين، يُعتبر دخولاً فورياً عكس الاتجاه (Rejection Entry) لأن السيولة تم تفعيلها."

- منطق توقيت الشموع (Candle Timing Logic):  
"في فريم الدقيقة، إذا أغلقت الشمعة عند سعر (00) أو (50) في الثواني الأخيرة، توقع انفجاراً سعرياً في الشمعة التالية لسحب السيولة المخفية."

- قاعدة المغناطيس للفجوات (Gap Magnet Rule):  
"لا تدخل بيعاً وهناك FVG لم تُغطَّ بالأعلى، ولا تدخل شراءً وهناك FVG بالأسفل. السعر في الـ OTC يتحرك من فجوة إلى فجوة (Gap to Gap) قبل أن يرتد."

📊 المرحلة 1: الفحص الأولي والتحذيرات
1.1 نظام الأمان ثلاثي الطبقات:  
• الدرع الأساسي  
• كشف وهم الزخم: 3 شموع كبيرة، فحص الاستدامة  
• التحقق الرقمي: استخراج الأسعار من المحور اليمني ومطابقتها مع الشارت  

1.2 كشف مخاطر OTC:  
• إشارات التلاعب: اختراق ثم عودة، انعكاس لحظي، حركة بدون حجم  
• حماية: تجنب آخر 10 ثوانٍ، أوامر معلقة، SL +20%  

1.3 تحليل الارتباط السعري:  
• Forex: مؤشر الدولار، العملات المرتبطة، السندات  
• Stocks: المؤشر العام، القطاع، أرباح  
• Crypto: BTC، Altcoins، مؤشر الخوف والجشع  

💰 المرحلة 2: التحليل الهيكلي
2.1 تحديد الهيكل: SMC + BOS/CHoCh بدقة  
2.2 استخراج الإحداثيات: High/Low، نسبة الحركة، دقة مطلقة  
2.3 مصفاة التسعير: Discount للشراء، Premium للبيع، مناطق الطوارئ <20%/>80%  

💰 المرحلة 3: السيولة والزخم
3.1 كشف وهم الزخم: فجوات، شموع خبر، ذيول طويلة، V-Reversal  
3.2 خرائط السيولة: Equal Highs/Lows، Inducement، Liquidity Sweeps، FVG  
3.3 انعكاس الزخم المفاجئ: رفض بعد اندفاع، فشل اختراق، انخفاض حجم، دايفرجنس  

🎯 المرحلة 4: القرار الذكي
• POI صالح + نموذج شموعي + سلوك سعري واضح + توافق الاتجاه  
• تعديل المخاطر حسب الأخبار: SL × {news_risk_multiplier}, الحجم ÷ {news_risk_multiplier}  
• حظر كامل: أخبار قوية ±30 دقيقة، زخم وهمي، فشل الفلاتر، V-Reversal، تضارب المؤشرات  
• حل التعارض: الأولوية: السيولة → الزخم → الفجوات → الهيكل → المؤشرات → السياق الزمني  

📊 المرحلة 5: سلوك الشموع
• استجابة POI: رفض/امتصاص/جانبي، القوة: جسم/ذيول، الحجم  
• قانون 3 شموع: اختبار → تصحيح → اختراق  
• التتابع الزمني: شمعة 1 رد فعل، شمعة 2 تأكيد، شمعة 3 قرار  

📉 المرحلة 6: MACD المحسن
• 1-5 دقائق: تجاهل التقاطعات البطيئة  
• 15-60 دقيقة: خط الصفر + دايفرجنس عند POI  
• حل التعارض: سلوك سعري واضح → تجاهل MACD  

⏰ المرحلة 7: تعدد الإطارات
• HTF: الاتجاه العام، MTF1: العرض/الطلب، MTF2: OB نشطة، LTF: الدخول  
• توافق: 4/4=+40, 3/4=+30, 2/4=تقليل حجم 50%, 1/4=منع الدخول  
• استراتيجية: شراء = HTF صاعد → تصحيح → OB → إشارة، بيع = HTF هابط → ارتداد → OB → إشارة  

🎯 المرحلة 8: درجات الثقة (معدلة)
• + نقاط: POI +25، نموذج شموعي +20، سلوك واضح +25، توافق الإطارات +30، حجم أعلى +15، أخبار هادئة +20، BOS +30، تغطية فجوة +15، MACD +10، اختراق مع بداية ساعة جديدة +25  
• - خصومات: تعارض مؤشرات -20، أخبار قوية -25، زخم وهمي -15، V-Reversal قريب -30، سيولة OTC منخفضة -10، بيع فوق 50% من موجة صاعدة بدون دخول Premium -40  
• مستويات: 95-100 💥💥, 85-94 💥, 70-84 🔥, 55-69 ⚡, 40-54 ❄️, <40 🚫  

📊 المرحلة 9: الحجم المتقدم
• اختراق >150%, امتصاص: حجم عالي + حركة محدودة  
• تصحيح <70%, انعكاس: حجم مرتفع مفاجئ  
• نقاط التحكم: POC = دعم/مقاومة, VA 70% توازن, EVA خارج VA = قوة  

🔄 المرحلة 10: إدارة الصفقات
• Long: TP1 SL للتعادل +40%, TP2 أعلى شمعة +30%, TP3 30% بترايل  
• Short: نفس النمط  
• OTC حماية: SL +20%, بعد 3 شموع، حجم 33/33/34  

🧠 المرحلة 11: التحليل السلوكي والتلاعب
• الخوف، الجشع، التردد، الاستسلام  
• التلاعب: Liquidity Sweep, Stop Hunt, False Breakout, Bait Pattern  
• سلوك OTC: نمط 3 مرات، اختراقات محددة، شمعة تغير السياق، أوامر معلقة  
• التمييز: ذيل + عودة = فخ, جسم كامل + إغلاق = BOS  

⚠️ المرحلة 12: تثبيت القرار النهائي
1. قرار ثابت لكل صورة متطابقة  
2. لا تغيير إلا عند تغير واضح في الشارت  
3. تأكيد مزدوج: القرار صحيح عند ظهور مرتين بنفس المعطيات  
4. تحقق من كل الأسعار والمستويات في الصورة بدقة  

📊 المعطيات الفنية:
• إطار الزمن الحالي: {candle} ({candle_category})  
• فريم التحقق: {verification_timeframe} (مخصص للتحقق من كسر الهيكل)  
• استراتيجية التداول: {trading_strategy}  
• جلسة السوق: {session_name} ({session_time})  
• حالة السيولة: {session_vol}  
• تأثير الأخبار: {news_impact} (معامل ×{news_risk_multiplier})  
• توقيت التحليل: {current_time.strftime('%Y-%m-%d %H:%M GMT')}  
• المستوى: Professional باك تيست 15000 صفقة  

🎯 التنسيق المطلوب للإجابة (الالتزام حرفياً):

📊 التحليل الفني المتقدم:
• البصمة الزمنية: {kill_zone_status}  
• حالة الهيكل: (صاعد/هابط) + (مرحلة وايكوف الحالية) + (توافق 4/4 إطارات: نعم/لا)  
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)  
• الفجوات السعرية: (المناطق التي سيعود السعر لتغطيتها)  

🎯 الإشارة التنفيذية:
• السعر الحالي: [السعر الدقيق من الشارت]  
• حالة الشمعة: [مفتوحة / مغلقة]  
• القرار الفني: (شراء 🟢 / بيع 🔴 / احتفاظ 🟡)  
• قوة الإشارة: (عالية جدا 💥 / عالية 🔥 / متوسطة ⚡ / ضعيفة ❄️)  
• نقطة الدخول: [السعر الدقيق بناءً على OB + شرط الإغلاق]  
• الأهداف الربحية:
  🎯 TP1: [سحب أول سيولة داخلية], [احتمالية الوصول]  
  🎯 TP2: [الهدف الرئيسي - منطقة عرض/طلب قوية]  
  🎯 TP3: [سيولة خارجية أو سد فجوة سعرية]  
• وقف الخسارة: [السعر مع 3 طبقات حماية]  
• المدة المتوقعة: [عدد الدقائق] (بناءً على معادلة الزخم السعري)  
• وقت الذروة المتوقع: [مثلاً: خلال الـ 3 شموع القادمة]  
• الحالة النفسية: [خوف 🥺 / جشع 🤑 / تردد 🤌 / استسلام 👎]  
• علامات التلاعب: [موجودة ✔️ / غير موجودة ❎]  

⚠️ إدارة المخاطر:
• مستوى الثقة: [0-100]٪ = [💥/🔥/⚡/❄️/🚫]  
• نقطة الإلغاء: [السعر الذي يفسد التحليل]  
• فريم التحقق: {verification_timeframe} (للتأكد من كسر الهيكل الحقيقي)  

💡 تعليمات نهائية:
"الأولوية القصوى: في حالة التعارض بين ذيول الشموع وقوة الاندفاع (Momentum)، تغلُب قوة الاندفاع في سوق الـ OTC، ويُمنع توقع القمم والقيعان (Top/Bottom Fishing)."

الآن قم بتحليل الشارت المرفق وأعطني الإجابة بالتنسيق المطلوب أعلاه فقط، بدون أي نص إضافي أو تفسيرات خارج الهيكل.
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        # --- الخطوة 1: التحليل الأولي بواسطة الموديل الأساسي ---
        await wait_msg.edit_text("📊 جاري تحليل (المرحلة 1/2)...")
        
        payload_1 = {
            "model": MISTRAL_MODEL,
            "messages": [
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": MAIN_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}", "detail": "high"}}
                    ]
                }
            ],
            **GENERATION_CONFIG
        }
        
        response_1 = requests.post(MISTRAL_URL, headers=headers, json=payload_1, timeout=45)
        
        if response_1.status_code != 200:
            print(f"Obeida Vision Error (Model 1): {response_1.status_code} - {response_1.text}")
            raise Exception(f"خطأ في التحليل الأول: {response_1.status_code}")
        
        initial_analysis = response_1.json()['choices'][0]['message']['content'].strip()
        
        # --- الخطوة 2: التدقيق والتحسين بواسطة الموديل الثاني ---
        await wait_msg.edit_text("📊 جاري تدقيق التحليل (المرحلة 2/2)...")
        
        # برومبت التدقيق المحدث
        AUDIT_PROMPT = f"""
أنت مدقق فني خبير SMC + ICT. مهمتك تدقيق التحليل التالي الذي تم إجراؤه على الشارت المرفق:

**التحليل الأولي:** {initial_analysis}

📋 **معلومات التحليل:**
• إطار الزمن: {candle} ({candle_category})
• فريم التحقق: {verification_timeframe}
• استراتيجية: {trading_strategy}
• الحجم: {position_sizing}
• الجلسة: {session_name} ({session_time})
• الأخبار: {news_impact}
• فريم التحقق للكسر الهيكلي: {verification_timeframe}

🔍 **أمر التدقيق:**
1. تحقق من كل سعر ومستوى مذكور في التحليل مع الصورة بدقة بكسلية
2. تأكد من تطبيق جميع القواعد التالية:
   - فلتر الأرقام المستديرة: فريم التحقق = {verification_timeframe}
   - قاعدة المغناطيس للفجوات
   - ميزان القوى (الهيكل vs الزخم)
3. تحقق من صحة:
   • الأسعار الدقيقة من المحور اليمني
   • مناطق FVG والحاجة لتغطيتها
   • تطابق الهيكل مع مرحلة وايكوف
   • صحة قوة الإشارة بناءً على النقاط المحسوبة
4. صحح أي أخطاء في:
   • تحديد السعر الحالي
   • نقاط الدخول والخروج
   • مستويات الثقة
   • مدة الصفقة المتوقعة

🎯 **التنسيق المطلوب للإجابة (الالتزام حرفياً):**

📊 *التحليل الفني المتقدم:*
• البصمة الزمنية: {kill_zone_status}
• حالة الهيكل: (صاعد/هابط) + (مرحلة وايكوف الحالية) + (توافق 4/4 إطارات: نعم/لا)
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• الفجوات السعرية: (المناطق التي سيعود السعر لتغطيتها)

🎯 *الإشارة التنفيذية:*
• مقارنة مع التحليل السابق: [✅ مطابق تماماً / ⚡ محسّن / ❌ مصحح]، درجة التشابه: [0–100]%
• السعر الحالي: [السعر الدقيق من الشارت]
• حالة الشمعة: [مفتوحة / مغلقة]
• القرار الفني: (شراء 🟢 / بيع 🔴 / احتفاظ 🟡)
• قوة الإشارة: (عالية جدا 💥 / عالية 🔥 / متوسطة ⚡ / ضعيفة ❄️)
• نقطة الدخول: [السعر الدقيق بناءً على OB + شرط الإغلاق]
• *الأهداف الربحية:*
  🎯 TP1: [سحب أول سيولة داخلية], [احتمالية الوصول]
  🎯 TP2: [الهدف الرئيسي - منطقة عرض/طلب قوية]
  🎯 TP3: [سيولة خارجية أو سد فجوة سعرية]
• وقف الخسارة: [السعر مع 3 طبقات حماية]
• المدة المتوقعة: [عدد الدقائق] (بناءً على معادلة الزخم السعري)
• وقت الذروة المتوقع: [مثلاً: خلال الـ 3 شموع القادمة]
• الحالة النفسية: [خوف 🥺 / جشع 🤑 / تردد 🤌 / استسلام 👎]
• علامات التلاعب: [موجودة ✔️ / غير موجودة ❎]

⚠️ *إدارة المخاطر:*
• مستوى الثقة: [0-100]٪ = [💥/🔥/⚡/❄️/🚫]
• نقطة الإلغاء: [السعر الذي يفسد التحليل]
• فريم التحقق للكسر: {verification_timeframe}

📝 *ملاحظات التدقيق:*
• نقاط القوة: [...]
• نقاط الضعف: [...]
• التعديلات المطبقة: [...]

*تذكر:* يجب أن يكون تدقيقك موضوعياً ويعتمد على الصورة فقط. لا تخترع أسعاراً أو مستويات غير موجودة.
"""
        
        payload_2 = {
            "model": MISTRAL_MODEL_AUDIT,
            "messages": [
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": AUDIT_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}", "detail": "high"}}
                    ]
                }
            ],
            **GENERATION_CONFIG
        }
        
        response_2 = requests.post(MISTRAL_URL, headers=headers, json=payload_2, timeout=45)
        
        if response_2.status_code == 200:
            audit_result = response_2.json()['choices'][0]['message']['content'].strip()
        else:
            print(f"Obeida Vision Warning (Model 2): {response_2.status_code} - استخدام التحليل الأول")
            audit_result = f"📋 **ملاحظة:** تعذر التدقيق - استخدام التحليل الأولي مباشرة\n\n{initial_analysis}"
        
        # تنظيف النصوص من التكرار
        audit_result = clean_repeated_text(audit_result)
        
        keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        
        # تنسيق وقت الصفقة للعرض
        time_display = format_trade_time_for_prompt(trade_time)
        
        # إعداد النص النهائي
        full_result = (
            f"✅ **تم التحليل والتدقيق بنجاح!**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 **إطار التحليل:** {candle} | فريم التحقق: {verification_timeframe}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{audit_result}\n\n"
            f"🔧 **الإعدادات المستخدمة:**\n"
            f"• سرعة الشموع: {candle} ({candle_category})\n"
            f"• استراتيجية التداول: {time_display}\n"
            f"• فريم التحقق للكسر: {verification_timeframe}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Powered by - Obeida Trading**"
        )
        
        # تنظيف النهائي من التكرارات
        full_result = clean_repeated_text(full_result)
        
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
        
    except requests.exceptions.Timeout:
        await wait_msg.edit_text("⏱️ تجاوز الوقت المحدد إرسال الصورة. حاول مرة أخرى.")
    except Exception as e:
        print(f"خطأ في تحليل الصورة: {e}")
        keyboard = [["📊 تحليل صورة"], ["الرجوع للقائمة الرئيسية"]]
        await wait_msg.edit_text(f"❌ **حدث خطأ في تحليل الصورة:** {str(e)[:200]}\nيرجى المحاولة مرة أخرى.")
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
        "🚀 **أهلاً بك في Obeida Trading **\n\n"
        "🤖 **المميزات الجديدة:**\n"
        "• تحليل فني متقدم للشارتات \n"
        "• 🆕 دردشة \n"
        "• 📈 نظام توصيات جاهزة\n"
        "• إعدادات تخصيص كاملة\n"
        "• تحليل دقيق بالأرقام\n\n"
        "📡 **نظام التحليل المزدوج:**\n"
        f"1. التحليل الأولي\n"
        f"2. التدقيق النهائي\n\n"
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
            
            await update.message.reply_text(
                f"📊 **جاهز للتحليل**\n\n"
                f"الإعدادات الحالية:\n"
                f"• سرعة الشموع: {candle}\n"
                f"• {time_display}\n\n"
                f"📡 **نظام التحليل:** موديل مزدوج\n"
                f"1. التحليل الأولي\n"
                f"2. التدقيق النهائي\n\n"
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
        
        keyboard = [TRADE_TIMES[i:i+2] for i in range(0, len(TRADE_TIMES), 2)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            f"✅ **تم تعيين سرعة الشموع:** {user_message}\n\n"
            f"الآن حدد **مدة الصفقة** المتوقعة:\n\n"
            f"📊 **خيارات مدة الصفقة:**\n"
            f"• **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة\n"
            f"• **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة\n"
            f"• **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة\n\n"
            f"اختر الإطار الزمني المناسب لاستراتيجيتك:",
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
        save_user_setting(user_id, "trade_time", user_message)
        
        keyboard = [["📊 تحليل صورة"], ["💬 دردشة"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        
        candle, _ = get_user_setting(user_id)
        
        await update.message.reply_text(
            f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ سرعة الشموع: {candle}\n"
            f"✅ مدة الصفقة: {user_message}\n\n"
            f"📡 **نظام التحليل:** موديل مزدوج\n"
            f"يمكنك الآن تحليل صورة أو الدردشة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

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
    help_text = f"""
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
    
    ⏱️ **خيارات مدة الصفقة:**
    • **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة
    • **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة
    • **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة
    
    📡 **نظام المزدوج للتحليل:**
    • **المرحلة 1:** التحليل الأولي
    • **المرحلة 2:** التدقيق النهائي والدقة
    
    📊 **مميزات البوت:**
    • تحليل فني للرسوم البيانية 
    • دردشة ذكية 
    • نظام توصيات العملات
    • حفظ إعداداتك الشخصية
    • واجهة سهلة بالأزرار
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    await update.message.reply_text(
        "تم الإلغاء. اكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# --- الحل النهائي ---
def run_flask_server():
    """تشغيل Flask server"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def run_telegram_bot():
    """تشغيل Telegram bot"""
    print("🤖 Starting Telegram Bot...")
    print(f"⚡ Powered by - Obeida Trading")
    
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
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

def main():
    """الدالة الرئيسية"""
    print("🤖 Starting Powered by - Obeida Trading ...")
    print("=" * 60)
    
    # تشغيل Flask في thread منفصل
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    print(f"🌐 Flask server started on port {os.environ.get('PORT', 8080)}")
    print("=" * 60)
    
    # تشغيل Telegram bot في thread الرئيسي
    run_telegram_bot()

if __name__ == "__main__":
    main()
