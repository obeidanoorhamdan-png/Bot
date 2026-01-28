import logging
import base64
import os
import sqlite3
import re
import requests
import threading
import time
import sys
import google.generativeai as genai
import traceback
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from flask import Flask
import PIL.Image

# ========== إعدادات التسجيل ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== إعدادات API ==========
TOKEN = os.environ.get('TOKEN', "7324911542:AAGcVkwzjtf3wDB3u7cprOLVyoMLA5JCm8U")
GEMINI_KEY = os.environ.get('GEMINI_KEY', "AIzaSyBHWahWkqVT9C4yT4efcvFdfH0BfgJV9Bs")

# التحقق من المفاتيح
if not TOKEN or TOKEN == "7324911542:AAGcVkwzjtf3wDB3u7cprOLVyoMLA5JCm8U":
    logger.warning("⚠️  يرجى تعيين TOKEN صحيح في متغيرات البيئة")
    
if not GEMINI_KEY or GEMINI_KEY == "AIzaSyBHWahWkqVT9C4yT4efcvFdfH0BfgJV9Bs":
    logger.warning("⚠️  يرجى تعيين GEMINI_KEY صحيح في متغيرات البيئة")

DB_NAME = "abood-gpt.db"

# ========== إعدادات Gemini ==========
try:
    genai.configure(api_key=GEMINI_KEY)
    logger.info("✅ تم تهيئة Gemini بنجاح")
except Exception as e:
    logger.error(f"❌ فشل في تهيئة Gemini: {e}")

# النماذج المتاحة
CURRENT_MODEL = "gemini-1.5-flash"  # النموذج الرئيسي
BACKUP_MODEL = "gemini-1.5-pro"     # نموذج احتياطي

# ========== إعدادات التداول ==========
CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

# توزيع العملات
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
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION = range(7)

# ========== Flask Server ==========
app = Flask(__name__)

@app.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Obeida Trading</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
            .container { background: rgba(255, 255, 255, 0.1); padding: 30px; border-radius: 20px; backdrop-filter: blur(10px); }
            h1 { color: white; margin-bottom: 20px; }
            .status { background: #4CAF50; color: white; padding: 12px 24px; border-radius: 10px; display: inline-block; margin: 10px; }
            .info-box { background: rgba(255, 255, 255, 0.2); padding: 15px; border-radius: 10px; margin: 15px 0; }
            .gemini-badge { background: #4285f4; color: white; padding: 10px 20px; border-radius: 25px; display: inline-block; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Obeida Trading Telegram Bot</h1>
            <p>Chat & Technical Analysis Bot Powered by AI</p>
            <div class="status">✅ البوت يعمل بنجاح</div>
            <div class="gemini-badge">🚀 مدعوم بـ Google Gemini AI</div>
            
            <div class="info-box">
                <p>🕒 آخر تحديث: """ + time.strftime("%Y-%m-%d %H:%M:%S") + """</p>
                <p>🧠 نموذج الذكاء الاصطناعي: Gemini 1.5 Flash</p>
                <p>📊 إصدار البوت: 3.0.0</p>
            </div>
            
            <div style="margin-top: 30px;">
                <a href="/health" style="color: #FFD700; margin: 0 10px;">الحالة الصحية</a>
                <a href="/ping" style="color: #FFD700; margin: 0 10px;">اختبار الاتصال</a>
            </div>
        </div>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return {
        "status": "active",
        "ai_engine": "gemini",
        "model": CURRENT_MODEL,
        "timestamp": time.time(),
        "services": {
            "telegram_bot": "running",
            "gemini_ai": "connected",
            "database": "connected"
        }
    }

@app.route('/ping')
def ping():
    return "PONG - Obeida Trading Bot is Alive!"

# ========== إدارة قاعدة البيانات ==========
def init_db():
    """تهيئة قاعدة البيانات"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, 
                candle TEXT DEFAULT 'M5', 
                trade_time TEXT DEFAULT 'متوسط (4h-Daily)',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_users INTEGER DEFAULT 0,
                total_analyses INTEGER DEFAULT 0,
                total_chats INTEGER DEFAULT 0,
                last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ تم تهيئة قاعدة البيانات بنجاح")
    except Exception as e:
        logger.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")

def save_user_setting(user_id, col, val):
    """حفظ إعدادات المستخدم"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(f"INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        cursor.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (val, user_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في حفظ الإعدادات: {e}")
        return False

def get_user_setting(user_id):
    """الحصول على إعدادات المستخدم"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT candle, trade_time FROM users WHERE user_id = ?", (user_id,))
        res = cursor.fetchone()
        conn.close()
        if res:
            return res
        return ("M5", "متوسط (4h-Daily)")
    except Exception as e:
        logger.error(f"❌ خطأ في قراءة الإعدادات: {e}")
        return ("M5", "متوسط (4h-Daily)")

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

# ========== دوال المساعدة ==========
def clean_repeated_text(text):
    """تنظيف النص من التكرارات"""
    if not text:
        return ""
    
    # إزالة التكرارات الشائعة
    patterns = [
        r'📊\s*\*\*التحليل الفني\*\*:.*?(?=\n\n|\n📊|\n🎯|\n⚠️|$)',
        r'🎯\s*\*\*التوصية والتوقعات\*\*:.*?(?=\n\n|\n📊|\n🎯|\n⚠️|$)',
        r'⚠️\s*\*\*إدارة المخاطر\*\*:.*?(?=\n\n|\n📊|\n🎯|\n⚠️|$)'
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        if len(matches) > 1:
            # الاحتفاظ بأول تكرار فقط
            text = re.sub(pattern, lambda m: m.group() if m.start() == text.find(m.group()) else '', text, flags=re.DOTALL)
    
    # إزالة الأسطر الفارغة المتكررة
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()

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
            split_point = max_length - 200
        
        parts.append(text[:split_point])
        text = text[split_point:].lstrip()
    
    if text:
        parts.append(text)
    
    return parts

def test_gemini_connection():
    """اختبار اتصال Gemini"""
    try:
        model = genai.GenerativeModel(CURRENT_MODEL)
        response = model.generate_content("Hello", safety_settings=[
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}
        ])
        if response and response.text:
            logger.info(f"✅ اتصال Gemini ناجح مع النموذج: {CURRENT_MODEL}")
            return True
    except Exception as e:
        logger.error(f"❌ فشل اتصال Gemini: {str(e)[:100]}")
    return False

# ========== دوال Gemini ==========
def get_gemini_analysis(symbol):
    """الحصول على تحليل من Gemini للعملة"""
    try:
        # اختبار الاتصال أولاً
        if not test_gemini_connection():
            return "⚠️ تعذر الاتصال بخدمة Gemini AI. يرجى التحقق من اتصال الإنترنت والمحاولة لاحقاً."
        
        model = genai.GenerativeModel(CURRENT_MODEL)
        
        # برومبت بسيط وفعال
        prompt = f"""
        قم بتحليل فني مختصر للعملة/المؤشر: {symbol}
        
        قدم الإجابة باللغة العربية بالتنسيق التالي:
        
        📊 **التحليل الفني لـ {symbol}:**
        
        - **الاتجاه العام:** (صاعد 🟢 / هابط 🔴 / عرضي 🟡)
        - **مستوى الثقة:** (مرتفع 🔥 / متوسط ⚡ / منخفض ❄️)
        
        🎯 **توصيات التداول:**
        1. **نقطة الدخول:** 
        2. **الهدف الأول (TP1):** 
        3. **الهدف الثاني (TP2):** 
        4. **وقف الخسارة (SL):** 
        
        ⚠️ **إدارة المخاطرة:**
        (نصائح لإدارة المخاطر)
        
        📝 **ملاحظات التحليل:**
        (ملاحظات إضافية)
        """
        
        # إعدادات الجيل
        generation_config = {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 40,
            "max_output_tokens": 1000,
        }
        
        # إعدادات السلامة
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ]
        
        response = model.generate_content(
            prompt,
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        
        if response and response.text:
            return response.text.strip()
        else:
            return "⚠️ لم يتم الحصول على رد من Gemini. قد يكون النموذج غير متاح حاليًا."
            
    except Exception as e:
        logger.error(f"❌ خطأ في تحليل {symbol}: {e}")
        
        # رسائل خطأ محددة
        error_msg = str(e).lower()
        if "api key" in error_msg or "key" in error_msg:
            return "❌ خطأ في مفتاح API. يرجى التحقق من المفتاح."
        elif "quota" in error_msg or "limit" in error_msg or "429" in error_msg:
            return "⚠️ تم تجاوز الحد المسموح. يرجى المحاولة لاحقاً."
        elif "model" in error_msg or "not found" in error_msg:
            return f"⚠️ النموذج {CURRENT_MODEL} غير متاح. يرجى المحاولة لاحقاً."
        else:
            return "⚠️ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى."

# ========== دوال البوت الرئيسية ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    user = update.effective_user
    logger.info(f"🚀 بدء تشغيل البوت للمستخدم: {user.username} ({user.id})")
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["💬 دردشة ذكية", "📈 توصيات فورية"]
    ]
    
    welcome_text = f"""
    🎉 **مرحباً {user.first_name}!**
    
    🤖 **أهلاً بك في Obeida Trading Bot**
    
    🚀 **المميزات الجديدة:**
    • تحليل فني متقدم للشارتات
    • دردشة ذكية مع Gemini AI
    • نظام توصيات فورية للعملات
    • إعدادات تخصيص كاملة
    
    📊 **اختر أحد الخيارات أدناه:**
    """
    
    await update.message.reply_text(
        welcome_text,
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
            "📊 **اختر سرعة الشموع المناسبة:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return SETTINGS_CANDLE
    
    elif user_message == "📊 تحليل صورة":
        candle, trade_time = get_user_setting(user_id)
        
        keyboard = [["الرجوع للقائمة الرئيسية"]]
        
        time_display = format_trade_time_for_prompt(trade_time)
        
        await update.message.reply_text(
            f"📊 **جاهز لتحليل الصورة**\n\n"
            f"🤖 **المحرك:** Gemini Vision AI\n"
            f"🔧 **الإعدادات الحالية:**\n"
            f"• سرعة الشموع: {candle}\n"
            f"• {time_display}\n\n"
            f"📤 **أرسل صورة الرسم البياني الآن:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return ANALYZE_MODE
    
    elif user_message == "💬 دردشة ذكية":
        return await start_chat_mode(update, context)
    
    elif user_message == "📈 توصيات فورية":
        return await start_recommendation_mode(update, context)
    
    else:
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة ذكية", "📈 توصيات فورية"]]
        await update.message.reply_text(
            "👋 **اختر أحد الخيارات من القائمة:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU

async def start_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع الدردشة"""
    keyboard = [
        ["🚀 مساعد شامل", "📈 استشارات تداول"],
        ["💻 دعم فني", "📝 كتابة محتوى"],
        ["ايقاف الدردشة", "الرجوع للقائمة الرئيسية"]
    ]
    
    await update.message.reply_text(
        "💬 **وضع الدردشة الذكية**\n\n"
        "🤖 **أنا مساعدك الذكي Obeida Trading**\n"
        "يمكنني مساعدتك في:\n"
        "• تحليل الأسواق والتداول\n"
        "• الاستشارات المالية\n"
        "• الدعم الفني والبرمجي\n"
        "• كتابة المحتوى الإبداعي\n\n"
        "📝 **اختر نوع المساعدة أو اكتب سؤالك مباشرة:**",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return CHAT_MODE

async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة رسائل الدردشة"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    # التحقق من الأوامر الخاصة
    if user_message == "ايقاف الدردشة":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة ذكية", "📈 توصيات فورية"]]
        await update.message.reply_text(
            "✅ تم إنهاء وضع الدردشة.",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "الرجوع للقائمة الرئيسية":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة ذكية", "📈 توصيات فورية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    # إذا كان اختياراً من القائمة، أطلب التفاصيل
    if user_message in ["🚀 مساعد شامل", "📈 استشارات تداول", "💻 دعم فني", "📝 كتابة محتوى"]:
        await update.message.reply_text(
            f"✅ **تم اختيار: {user_message}**\n\n"
            f"🤖 **جاهز لمساعدتك في هذا المجال**\n"
            f"🚀 **المحرك:** Gemini AI\n\n"
            f"📝 **أرسل سؤالك الآن وسأقدم لك إجابة مفصلة:**",
            parse_mode="Markdown"
        )
        return CHAT_MODE
    
    # معالجة الرسالة
    wait_msg = await update.message.reply_text("🤔 Obeida Trading يفكر...")
    
    try:
        # اختبار الاتصال
        if not test_gemini_connection():
            await wait_msg.edit_text(
                "❌ **تعذر الاتصال بخدمة الذكاء الاصطناعي**\n\n"
                "الأسباب المحتملة:\n"
                "1. 🔌 مشكلة في الاتصال بالإنترنت\n"
                "2. 🔑 مشكلة في مفتاح API\n"
                "3. ⏳ تجاوز الحد اليومي\n\n"
                "📞 يرجى المحاولة لاحقاً أو استخدام الخدمات الأخرى."
            )
            return CHAT_MODE
        
        model = genai.GenerativeModel(CURRENT_MODEL)
        
        # برومبت بسيط وفعال
        prompt = f"""
        أنت Obeida Trading، مساعد ذكي متخصص في التداول والتحليل الفني.
        
        السؤال: {user_message}
        
        أجب باللغة العربية بتنسيق منظم:
        
        💡 **الإجابة:**
        (قدم إجابة واضحة ومنظمة)
        
        🔍 **التفاصيل:**
        (شرح إضافي إن لزم)
        
        ⚠️ **ملاحظات مهمة:**
        (نصائح أو تحذيرات إن وجدت)
        
        كن دقيقاً، واقعياً، ومفيداً.
        """
        
        response = model.generate_content(prompt)
        
        if response and response.text:
            result = response.text.strip()
            
            # تنظيف النص
            result = clean_repeated_text(result)
            
            # إضافة التذييل
            result = result + f"\n\n━━━━━━━━━━━━━━━━━━\n🤖 **Obeida Trading**\n🚀 **المحرك:** Gemini AI"
            
            # إرسال النتيجة
            if len(result) > 4000:
                parts = split_message(result, max_length=4000)
                await wait_msg.edit_text(parts[0], parse_mode="Markdown")
                for part in parts[1:]:
                    await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(f"💬 **Obeida Trading يجيب:**\n\n{result}", parse_mode="Markdown")
            
            # عرض الأزرار مرة أخرى
            chat_keyboard = [
                ["🚀 مساعد شامل", "📈 استشارات تداول"],
                ["💻 دعم فني", "📝 كتابة محتوى"],
                ["ايقاف الدردشة", "الرجوع للقائمة الرئيسية"]
            ]
            
            await update.message.reply_text(
                "🔽 **اختر مجالاً آخر أو اطرح سؤالاً جديداً:**",
                reply_markup=ReplyKeyboardMarkup(chat_keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
            
        else:
            await wait_msg.edit_text(
                "❌ **لم أستطع الحصول على إجابة من الذكاء الاصطناعي**\n\n"
                "📞 يرجى:\n"
                "1. إعادة صياغة السؤال\n"
                "2. المحاولة مرة أخرى\n"
                "3. استخدام خدمة أخرى"
            )
    
    except Exception as e:
        logger.error(f"❌ خطأ في الدردشة: {e}")
        await wait_msg.edit_text(
            "❌ **حدث خطأ غير متوقع**\n\n"
            "📞 يرجى المحاولة مرة أخرى لاحقاً."
        )
    
    return CHAT_MODE

async def start_recommendation_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع التوصيات"""
    reply_keyboard = [[key] for key in CATEGORIES.keys()]
    reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    await update.message.reply_text(
        "📈 **نظام التوصيات الفورية**\n\n"
        "🚀 **اختر القسم الذي تريد التوصيات منه:**\n"
        "سأقدم لك تحليلاً فورياً لأي عملة تختارها.",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return RECOMMENDATION_MODE

async def handle_recommendation_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات التوصيات"""
    user_text = update.message.text.strip()
    
    # العودة للقائمة الرئيسية
    if user_text == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة ذكية", "📈 توصيات فورية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    # التحقق من الأقسام الرئيسية
    if user_text in CATEGORIES:
        keyboard = [[asset] for asset in CATEGORIES[user_text]]
        keyboard.append(["🔙 العودة", "الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            f"📂 **القسم:** {user_text}\n\n"
            f"💰 **اختر العملة/المؤشر للتحليل:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return CATEGORY_SELECTION
    
    # التحقق من العملة المختارة
    symbol_to_analyze = None
    for category_list in CATEGORIES.values():
        if user_text in category_list:
            symbol_to_analyze = user_text
            break
    
    # معالجة الأزرار الخاصة
    if user_text == "🔙 العودة":
        reply_keyboard = [[key] for key in CATEGORIES.keys()]
        reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "📂 **اختر القسم المطلوب:**",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return RECOMMENDATION_MODE
    
    # إذا وجدت العملة، ابدأ التحليل
    if symbol_to_analyze:
        wait_msg = await update.message.reply_text(f"⏳ **جاري تحليل {symbol_to_analyze}...**")
        
        # الحصول على التحليل
        analysis = get_gemini_analysis(symbol_to_analyze)
        
        # تنسيق النتيجة
        final_msg = (
            f"📊 **تحليل {symbol_to_analyze}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{analysis}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Obeida Trading** - نظام التوصيات\n"
            f"🚀 **المحرك:** Gemini AI"
        )
        
        # تنظيف النص
        final_msg = clean_repeated_text(final_msg)
        
        # إرسال النتيجة
        await wait_msg.edit_text(final_msg, parse_mode="Markdown")
        
        # عرض خيارات للاستمرار
        reply_keyboard = [[key] for key in CATEGORIES.keys()]
        reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "🔽 **اختر قسماً آخر أو العودة للقائمة الرئيسية:**",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return RECOMMENDATION_MODE
    
    # إذا لم يطابق النص أي شيء
    await update.message.reply_text(
        "❌ **خيار غير موجود**\n\n"
        "📌 يرجى اختيار عملة من القائمة الظاهرة في الأزرار.",
        reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True, one_time_keyboard=False)
    )
    return RECOMMENDATION_MODE

async def handle_settings_candle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار سرعة الشموع"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة ذكية", "📈 توصيات فورية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in CANDLE_SPEEDS:
        if save_user_setting(user_id, "candle", user_message):
            keyboard = [TRADE_TIMES[i:i+2] for i in range(0, len(TRADE_TIMES), 2)]
            keyboard.append(["الرجوع للقائمة الرئيسية"])
            
            await update.message.reply_text(
                f"✅ **تم تعيين سرعة الشموع:** `{user_message}`\n\n"
                f"⏰ **الآن اختر مدة الصفقة:**",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return SETTINGS_TIME
        else:
            await update.message.reply_text("❌ حدث خطأ في حفظ الإعدادات. يرجى المحاولة مرة أخرى.")
            return SETTINGS_CANDLE
    
    await update.message.reply_text("❌ الرجاء اختيار سرعة شموع صحيحة من القائمة.")
    return SETTINGS_CANDLE

async def handle_settings_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار مدة الصفقة"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة ذكية", "📈 توصيات فورية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in TRADE_TIMES:
        if save_user_setting(user_id, "trade_time", user_message):
            candle, _ = get_user_setting(user_id)
            
            keyboard = [["📊 تحليل صورة"], ["💬 دردشة ذكية"], ["📈 توصيات فورية"], ["الرجوع للقائمة الرئيسية"]]
            
            await update.message.reply_text(
                f"🎉 **تم حفظ الإعدادات بنجاح!**\n\n"
                f"✅ **سرعة الشموع:** {candle}\n"
                f"✅ **مدة الصفقة:** {user_message}\n\n"
                f"🤖 **يمكنك الآن:**\n"
                f"• تحليل الصور 📊\n"
                f"• الدردشة الذكية 💬\n"
                f"• الحصول على توصيات 📈",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return MAIN_MENU
        else:
            await update.message.reply_text("❌ حدث خطأ في حفظ الإعدادات. يرجى المحاولة مرة أخرى.")
            return SETTINGS_TIME
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة من القائمة.")
    return SETTINGS_TIME

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة تحليل الصور"""
    user_id = update.effective_user.id
    candle, trade_time = get_user_setting(user_id)
    
    wait_msg = await update.message.reply_text("📊 **جاري تحليل الصورة...**")
    
    try:
        # تحميل الصورة
        photo = await update.message.photo[-1].get_file()
        path = f"img_{user_id}_{int(time.time())}.jpg"
        await photo.download_to_drive(path)
        
        # اختبار اتصال Gemini
        if not test_gemini_connection():
            await wait_msg.edit_text(
                "❌ **تعذر الاتصال بخدمة التحليل**\n\n"
                "📞 يرجى:\n"
                "1. التحقق من اتصال الإنترنت\n"
                "2. المحاولة لاحقاً\n"
                "3. استخدام خدمة أخرى"
            )
            if os.path.exists(path):
                os.remove(path)
            return MAIN_MENU
        
        # برومبت تحليل الصورة
        prompt = f"""
        أنت محلل فني خبير. قم بتحليل الرسم البياني المرفق:
        
        معلومات الإطار الزمني:
        - إطار الشمعة: {candle}
        - مدة التداول: {trade_time}
        
        قدم تحليلاً فنيًا واضحًا يتضمن:
        1. تحديد الاتجاه العام
        2. مستويات الدعم والمقاومة الرئيسية
        3. نقاط الدخول والخروج المحتملة
        4. إدارة المخاطر المناسبة
        
        التنسيق باللغة العربية:
        
        📊 **التحليل الفني:**
        - **الاتجاه:** 
        - **الدعم الرئيسي:** 
        - **المقاومة الرئيسية:** 
        
        🎯 **توصيات التداول:**
        - **نقطة الدخول:** 
        - **الهدف الأول:** 
        - **الهدف الثاني:** 
        - **وقف الخسارة:** 
        
        ⚠️ **إدارة المخاطرة:**
        (نصائح لإدارة المخاطر)
        """
        
        model = genai.GenerativeModel(CURRENT_MODEL)
        img = PIL.Image.open(path)
        
        response = model.generate_content([prompt, img])
        
        if response and response.text:
            result = response.text.strip()
            
            # تنظيف النص
            result = clean_repeated_text(result)
            
            # التنسيق النهائي
            full_result = (
                f"✅ **تم التحليل بنجاح!**\n\n"
                f"📊 **نتائج تحليل الشارت:**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{result}\n\n"
                f"🔧 **الإعدادات المستخدمة:**\n"
                f"• سرعة الشموع: {candle}\n"
                f"• مدة الصفقة: {trade_time}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🤖 **Obeida Trading**\n"
                f"🚀 **المحرك:** Gemini Vision AI"
            )
            
            # إرسال النتيجة
            if len(full_result) > 4000:
                parts = split_message(full_result, max_length=4000)
                await wait_msg.edit_text(parts[0], parse_mode="Markdown")
                for part in parts[1:]:
                    await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(full_result, parse_mode="Markdown")
            
        else:
            await wait_msg.edit_text(
                "❌ **لم يتمكن الذكاء الاصطناعي من تحليل الصورة**\n\n"
                "📌 الأسباب المحتملة:\n"
                "1. الصورة غير واضحة\n"
                "2. الرسم البياني غير مقروء\n"
                "3. مشكلة في معالجة الصورة\n\n"
                "📸 يرجى إرسال صورة أوضح."
            )
            
    except Exception as e:
        logger.error(f"❌ خطأ في تحليل الصورة: {e}")
        await wait_msg.edit_text(
            "❌ **حدث خطأ في تحليل الصورة**\n\n"
            "📞 يرجى:\n"
            "1. التحقق من وضوح الصورة\n"
            "2. إعادة المحاولة\n"
            "3. استخدام صورة أقل حجماً"
        )
    
    finally:
        # تنظيف الملفات
        if 'path' in locals() and os.path.exists(path):
            try:
                os.remove(path)
            except:
                pass
    
    # العودة للقائمة
    keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصيات فورية"], ["الرجوع للقائمة الرئيسية"]]
    await update.message.reply_text(
        "🔽 **اختر الإجراء التالي:**",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    return MAIN_MENU

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة وضع التحليل"""
    user_message = update.message.text
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة ذكية", "📈 توصيات فورية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    await update.message.reply_text(
        "📤 **أرسل صورة الشارت للتحليل**\n"
        "أو اضغط 'الرجوع للقائمة الرئيسية'",
        reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True, one_time_keyboard=False)
    )
    return ANALYZE_MODE

async def handle_photo_in_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور في وضع التحليل"""
    return await handle_photo_analysis(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر المساعدة"""
    help_text = """
    🤖 **مرحباً بك في Obeida Trading Bot**
    
    📋 **الأوامر المتاحة:**
    /start - بدء البوت والعودة للقائمة
    /help - عرض رسالة المساعدة
    
    🎯 **كيفية الاستخدام:**
    1. استخدم أزرار القائمة للتنقل
    2. أرسل صورة الشارت للتحليل الفني
    3. اختر "دردشة ذكية" للاستفسارات
    4. اختر "توصيات فورية" لتحليل العملات
    
    📊 **المميزات:**
    • تحليل فني متقدم للشارتات
    • دردشة ذكية مع Gemini AI
    • نظام توصيات فورية للعملات
    • إعدادات تخصيص كاملة
    
    ⚙️ **إعدادات التحليل:**
    • سرعة الشموع: من S5 إلى D1
    • مدة الصفقة: قصير، متوسط، طويل
    
    🚀 **المحرك:**
    • Google Gemini AI
    • نموذج: Gemini 1.5 Flash
    • دعم اللغة العربية الكامل
    
    📞 **للتواصل والدعم:**
    @ObeidaTrading
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    await update.message.reply_text(
        "تم الإلغاء. اكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# ========== تشغيل الخوادم ==========
def run_flask_server():
    """تشغيل Flask server"""
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"🌐 بدء تشغيل Flask server على المنفذ {port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def run_telegram_bot():
    """تشغيل Telegram bot"""
    logger.info("🤖 بدء تشغيل Telegram Bot...")
    
    # إنشاء تطبيق Telegram
    application = Application.builder().token(TOKEN).build()
    
    # معالج المحادثة الرئيسي
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
    
    logger.info("✅ تم تهيئة Telegram Bot بنجاح")
    logger.info("📡 البوت يعمل وجاهز لاستقبال الرسائل...")
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

def main():
    """الدالة الرئيسية"""
    print("=" * 60)
    print("🚀 Obeida Trading Bot مع Gemini AI")
    print("=" * 60)
    
    # اختبار اتصال Gemini
    print("\n🔗 اختيار اتصال Gemini...")
    if test_gemini_connection():
        print(f"✅ اتصال Gemini ناجح! النموذج: {CURRENT_MODEL}")
    else:
        print("⚠️  تحذير: فشل اختبار اتصال Gemini")
        print("بعض المميزات قد لا تعمل بشكل كامل")
    
    # معلومات النظام
    print(f"\n🤖 توكن البوت: {'✅ مضبوط' if TOKEN and TOKEN != '7324911542:AAGcVkwzjtf3wDB3u7cprOLVyoMLA5JCm8U' else '⚠️  غير مضبوط'}")
    print(f"🔑 مفتاح Gemini: {'✅ مضبوط' if GEMINI_KEY and GEMINI_KEY != 'AIzaSyBHWahWkqVT9C4yT4efcvFdfH0BfgJV9Bs' else '⚠️  غير مضبوط'}")
    print(f"🗄️  قاعدة البيانات: {DB_NAME}")
    
    # تهيئة قاعدة البيانات
    init_db()
    
    # تشغيل Flask في thread منفصل
    print(f"\n🌐 بدء تشغيل Flask server...")
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    print("⏳ انتظر 3 ثواني لبدء Flask server...")
    time.sleep(3)
    
    # تشغيل Telegram bot
    print("\n🤖 بدء تشغيل Telegram Bot...")
    run_telegram_bot()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        print(f"\n❌ خطأ غير متوقع: {e}")
        logger.error(f"❌ خطأ غير متوقع: {traceback.format_exc()}")
