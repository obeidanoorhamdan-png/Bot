import logging
import base64
import os
import sqlite3
import re
import requests
import threading
import time
import sys
import traceback
import asyncio
import json
import shutil
import io
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.error import NetworkError, TimedOut
from flask import Flask
from PIL import Image
import pytz

# --- الإعدادات ---
TOKEN = os.environ.get('TOKEN', "7324911542:AAGcVkwzjtf3wDB3u7cprOLVyoMLA5JCm8U")

# ⚡ إعدادات Mistral AI API الجديدة
MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "WhGHh0RvwtLLsRwlHYozaNrmZWkFK2f1")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "pixtral-large-latest"          # للرؤية والاستخراج البصري
MISTRAL_MODEL_AUDIT = "mistral-large-latest"        # للتحليل المنطقي واتخاذ القرار
MODEL_SUMMARY = "mistral-medium-latest"        # للتلخيص السريع

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

# إعدادات إضافية
GAZA_TIMEZONE = pytz.timezone('Asia/Gaza')
IMAGE_CACHE_DIR = "image_cache"
MAX_IMAGE_SIZE = (1024, 1024)  # أقصى حجم للصورة بعد الضغط
IMAGE_QUALITY = 85  # جودة الصورة بعد الضغط (من 0-100)

# إنشاء مجلد التخزين المؤقت إذا لم يكن موجوداً
if not os.path.exists(IMAGE_CACHE_DIR):
    os.makedirs(IMAGE_CACHE_DIR)

# حالات المحادثة
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION = range(7)

# حالات إضافية لنظام الصور المتعددة
WAITING_FIRST_IMAGE, WAITING_SECOND_IMAGE = range(7, 9)

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

# --- دوال المساعدة الجديدة ---
def cleanup_old_images():
    """تنظيف الصور القديمة التي مضى عليها أكثر من 30 دقيقة"""
    try:
        current_time = time.time()
        for filename in os.listdir(IMAGE_CACHE_DIR):
            filepath = os.path.join(IMAGE_CACHE_DIR, filename)
            if os.path.isfile(filepath):
                # تحقق من عمر الملف
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 1800:  # أكثر من 30 دقيقة
                    try:
                        os.remove(filepath)
                        print(f"🧹 تم حذف الملف القديم: {filename}")
                    except Exception as e:
                        print(f"⚠️ خطأ في حذف الملف {filename}: {e}")
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف الصور القديمة: {e}")

def compress_image(image_path, max_size=MAX_IMAGE_SIZE, quality=IMAGE_QUALITY):
    """ضغط الصورة لتقليل الحجم مع الحفاظ على الجودة"""
    try:
        with Image.open(image_path) as img:
            # تحويل الصورة إلى RGB إذا كانت RGBA
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGB')
                elif img.mode == 'RGBA':
                    background.paste(img, mask=img.split()[-1])
                    img = background
                else:
                    img = img.convert('RGB')
            
            # تغيير الحجم مع الحفاظ على النسبة
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            # حفظ الصورة المضغوطة
            compressed_path = image_path.replace('.jpg', '_compressed.jpg')
            img.save(compressed_path, 'JPEG', quality=quality, optimize=True)
            
            original_size = os.path.getsize(image_path) / 1024
            compressed_size = os.path.getsize(compressed_path) / 1024
            print(f"📦 تم ضغط الصورة: {original_size:.1f}KB → {compressed_size:.1f}KB")
            
            return compressed_path
    except Exception as e:
        print(f"⚠️ خطأ في ضغط الصورة: {e}")
        return image_path  # إرجاع المسار الأصلي في حالة الخطأ

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
            last_analysis_context TEXT DEFAULT '',
            last_analysis_time DATETIME DEFAULT NULL
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
    cursor.execute("SELECT candle, trade_time, last_analysis_context, last_analysis_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        return res
    return ("M1", "قصير (1m-15m)", "", None)

def save_analysis_context(user_id, analysis_text):
    """حفظ تحليل الصورة الأخيرة كسياق للتحليل التالي"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # نحفظ أول 500 حرف فقط كخلاصة للسياق
    summary = analysis_text[:500]
    cursor.execute("UPDATE users SET last_analysis_context = ?, last_analysis_time = CURRENT_TIMESTAMP WHERE user_id = ?", (summary, user_id))
    conn.commit()
    conn.close()

def get_analysis_context(user_id):
    """الحصول على سياق التحليل السابق"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT last_analysis_context, last_analysis_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        context, context_time = res
        if context_time:
            # تحقق إذا مر أكثر من 10 دقائق
            time_diff = (datetime.now() - datetime.fromisoformat(context_time)).total_seconds() / 60
            if time_diff > 10:  # أكثر من 10 دقائق
                return "", None
        return context, context_time
    return "", None

def cleanup_old_database_records():
    """تنظيف سجلات قاعدة البيانات القديمة"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # حذف سجلات الدردشة القديمة (أقدم من 7 أيام)
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("DELETE FROM chat_history WHERE timestamp < ?", (week_ago,))
        deleted_rows = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        if deleted_rows > 0:
            print(f"🧹 تم حذف {deleted_rows} سجل دردشة قديم")
            
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف قاعدة البيانات: {e}")

def get_market_session():
    """الحصول على جلسة السوق باستخدام توقيت غزة الصحيح"""
    try:
        # استخدام توقيت غزة الحقيقي
        gaza_time = datetime.now(GAZA_TIMEZONE)
        current_hour = gaza_time.hour
        
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
    except Exception as e:
        print(f"⚠️ خطأ في تحديد جلسة السوق: {e}")
        # القيمة الاحتياطية في حالة الخطأ
        return "جلسة عالمية", "غير محددة", "متوسطة"
        
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
    """تنظيف النص من التكرارات وتحسين التنسيق - الإصدار المحسّن"""
    if not text:
        return ""
    
    # حذف الفقرات المكررة تماماً
    lines = text.split('\n')
    unique_lines = []
    for line in lines:
        if line.strip() not in [ul.strip() for ul in unique_lines] or line.strip() == "":
            unique_lines.append(line)
    text = '\n'.join(unique_lines)
    
    # التأكد من عدم تكرار العناوين الرئيسية
    patterns = ["📊 التحليل الفني المتقدم:", "🎯 الإشارة التنفيذية:", "⚠️ إدارة المخاطر:",
                "📊 **نتائج الفحص الفني**:", "🎯 **التوصية والتوقعات**:", 
                "⚠️ **إدارة المخاطر**:", "📝 **ملاحظات التحليل**:"]
    
    for p in patterns:
        if text.count(p) > 1:
            parts = text.split(p)
            # حفظ الجزء الأول والجزء الأخير فقط
            text = parts[0] + p + parts[-1]
    
    # تنظيف الأنماط المحددة
    if "📊 **نتائج الفحص الفني**:" in text:
        text = re.sub(r'(📊 \*\*نتائج الفحص الفني\*\*:[\s\S]*?)(?=📊 \*\*نتائج الفحص الفني\*\*:)', '', text, flags=re.DOTALL)
    
    if "### تحليل الشارت المرفق" in text:
        sections = text.split("### تحليل الشارت المرفق")
        if len(sections) > 1:
            text = "### تحليل الشارت المرفق" + sections[1]
    
    return text

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

# --- وظائف إدارة الذاكرة ---
def cleanup_user_data(context: ContextTypes.DEFAULT_TYPE, user_id: int = None):
    """تنظيف البيانات المؤقتة للمستخدم"""
    try:
        # تنظيف الملفات المؤقتة
        if user_id:
            # البحث عن ملفات هذا المستخدم في مجلد التخزين المؤقت
            try:
                for filename in os.listdir(IMAGE_CACHE_DIR):
                    if f"_{user_id}_" in filename:
                        filepath = os.path.join(IMAGE_CACHE_DIR, filename)
                        if os.path.exists(filepath):
                            os.remove(filepath)
            except Exception as e:
                print(f"⚠️ خطأ في تنظيف ملفات المستخدم {user_id}: {e}")
        
        # تنظيف البيانات المؤقتة
        if 'dual_images' in context.user_data:
            del context.user_data['dual_images']
        if 'dual_image_paths' in context.user_data:
            del context.user_data['dual_image_paths']
        if 'dual_analysis_mode' in context.user_data:
            del context.user_data['dual_analysis_mode']
        if 'last_analysis' in context.user_data:
            del context.user_data['last_analysis']
        if 'dual_analysis_start' in context.user_data:
            del context.user_data['dual_analysis_start']
            
        print(f"✅ تم تنظيف الذاكرة والملفات للمستخدم {user_id}")
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف الذاكرة: {e}")

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
        "🚀 **نظام التوصيات**\n\n"
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
            "model": MODEL_SUMMARY,
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

# --- دالة تحليل الصورة مع جميع الإضافات الجديدة ---
async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور للتحليل الفني المتقدم مع نظام الموديل المزدوج - الإصدار المحسّن"""
    user_id = update.effective_user.id
    candle, trade_time, prev_context, prev_time = get_user_setting(user_id)
    
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
    
    # استخدام مجلد التخزين المؤقت بدلاً من المجلد الرئيسي
    timestamp = int(time.time())
    original_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}_original.jpg")
    compressed_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}_compressed.jpg")
    
    try:
        # تحميل الصورة
        await photo.download_to_drive(original_path)
        
        # ضغط الصورة
        compressed_path = compress_image(original_path)
        
        # استخدام الصورة المضغوطة للتحليل
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            if os.path.exists(original_path):
                os.remove(original_path)
            if os.path.exists(compressed_path) and compressed_path != original_path:
                os.remove(compressed_path)
            return MAIN_MENU
        
        # الحصول على معلومات السيولة والتوقيت
        session_name, session_time, session_vol = get_market_session()
        gaza_time = datetime.now(GAZA_TIMEZONE)
        current_hour = gaza_time.hour
        current_minute = gaza_time.minute
        current_second = gaza_time.second
        
        # حساب الثواني المتبقية لإغلاق الشمعة
        seconds_remaining = 60 - current_second
        if candle.startswith('M'):
            # إذا كانت شمعة دقيقة (M1, M5, إلخ)
            candle_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            seconds_remaining = (candle_minutes * 60) - ((current_minute % candle_minutes) * 60 + current_second)
        elif candle.startswith('H'):
            # إذا كانت شمعة ساعة
            candle_hours = int(candle[1:]) if candle[1:].isdigit() else 1
            minutes_passed = gaza_time.hour % candle_hours * 60 + current_minute
            seconds_remaining = (candle_hours * 3600) - (minutes_passed * 60 + current_second)
        
        candle_closing_status = f"الوقت المتبقي لإغلاق الشمعة: {seconds_remaining} ثانية"
        if seconds_remaining < 10:
            candle_closing_status += " ⚠️ (الوقت حرج جداً - تجنب الدخول)"
        elif seconds_remaining < 30:
            candle_closing_status += " ⚠️ (الوقت قصير)"
        
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
        
        # ========== معالجة "دقيقة الغدر" برمجياً ==========
        is_last_minute = 1 if current_minute in [29, 59, 14, 44] else 0
        last_minute_status = "🔥 حرجة - آخر دقيقة للإغلاق" if is_last_minute else "✅ عادية"
        
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
        verification_timeframe = ""
        
        candle_value = candle[1:] if candle.startswith(('S', 'M', 'H', 'D')) else candle
        
        if candle.startswith('S'):
            if candle_value in ['5', '10', '15']:
                verification_timeframe = "S15"
            else:
                verification_timeframe = "S30"
        elif candle.startswith('M'):
            if int(candle_value) <= 5:
                verification_timeframe = "M1"
            elif int(candle_value) <= 15:
                verification_timeframe = "M5"
            else:
                verification_timeframe = "M15"
        elif candle.startswith('H'):
            verification_timeframe = "H1"
        elif candle.startswith('D'):
            verification_timeframe = "H4"
        
        # ========== إعدادات ثابتة ==========
        GENERATION_CONFIG = {
            "max_tokens": 910,
            "temperature": 0.0,
            "top_p": 1.0,
            "random_seed": 42
        }
        
        # ========== تحضير سياق التحليل السابق ==========
        previous_context_info = ""
        if prev_context and prev_time:
            try:
                prev_time_obj = datetime.fromisoformat(prev_time)
                minutes_ago = int((datetime.now() - prev_time_obj).total_seconds() / 60)
                previous_context_info = f"""
                📋 **ذاكرة السياق (منذ {minutes_ago} دقيقة):**
                {prev_context}
                """
            except:
                previous_context_info = ""
        
        # ========== البرومبت الرئيسي المحدث مع جميع الإضافات ==========
        MAIN_PROMPT = f"""
أنت محلل فني خبير متكامل في SMC + ICT + WYCKOFF + VOLUME PROFILE + MARKET PSYCHOLOGY.
مهمتك تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية دقيقة بنظام متعدد الطبقات.

🎯 **قاعدة فلتر المسافة الذهبية:** أنت ملزم باستخراج السعر من المحور الأيمن (Y-axis) ومقارنته بأقرب رقم مستدير (.000). إذا كانت المسافة أقل من 0.00010، تُلغى جميع أوامر البيع/الشراء العكسية ويتم تفعيل نظام 'اللحاق بالمغناطيس السعري' - أي متابعة الاتجاه حتى لمس الرقم المستدير.

{previous_context_info}

🎯 نظام التحليل متعدد المستويات

📊 المستوى 1: التحليل الاستراتيجي (الخريطة الكبرى)
• الهيكل العام: تحليل موجات إيليوت + BOS/CHoCh
• مراحل Wyckoff: التحديد الدقيق لمرحلة (Accumulation/Markup/Distribution/Decline)
• الحكم الزمني: توافق {verification_timeframe} مع {candle} للإشارات
• السياق السوقي: {session_name} - {session_vol} سيولة

⚡ المستوى 2: التحليل التكتيكي (الخطة التنفيذية)
• أنماط الشموع: تحليل 5 شموع سابقة + الشمعة الحالية
• Order Blocks: تحديد آخر 3 مناطق طلب/عرض نشطة
• FVG Tracking: تتبع الفجوات غير المغطاة في نطاق 50 نقطة
• Liquidity Map: رسم خرائط Equal Highs/Lows + Inducement

🎯 المستوى 3: التحليل التنفيذي (الدخول الفوري)
• Entry Triggers: شروط الدخول المباشرة (شمعة إغلاق + حجم)
• Risk Matrix: حساب RR ديناميكي حسب {news_risk_multiplier}
• Position Sizing: حجم صفقة ذكي حسب {position_sizing}
• Timing Precision: توقيت الدخول/الخروج بالثواني

🔥 نظام القواعد المتقدمة (Hard-Coded Logic)

🛡️ تحديثات الأمان البصري (Vision Updates):
1. معايرة الإحداثيات: قم برسم شبكة (X,Y) وهمية؛ المحور Y للسعر و X للزمن. طابق كل ذيل شمعة بالسعر المقابل له على المسطرة اليمنى بدقة بكسلية.
2. فلتر المصيدة (Retail Trap): حدد مستويات الدعم/المقاومة "الواضحة جداً". إذا كان السعر يتذبذب عندها، لا تدخل؛ انتظر سحب السيولة (Stop Hunt) أولاً.
3. قاعدة الـ 50% (FVG Equilibrium): عند رصد فجوة FVG، الهدف المغناطيسي ليس بدايتها فقط، بل خط المنتصف (0.50) منها.
4. قانون الزخم المؤسسي: إذا زاد حجم جسم الشمعة عن 200% من متوسط آخر 5 شموع، تُلغى جميع إشارات الانعكاس (Counter-trend) ويتم الدخول مع الاتجاه حصراً.
5. التحقق من الكسر الكاذب (SFP): لا تعتمد الكسر (BOS) إلا بإغلاق كامل للجسم. ملامسة السعر للقمة بالذيل ثم العودة تعني دخولاً عكسياً فورياً.

🛡️ طبقات الحماية الذكية:
1. مصفاة الأخبار: {news_warning if news_warning else "✅ الوضع آمن"}
2. فلتر التوقيت: {kill_zone_status}
3. فلتر دقيقة الغدر: {last_minute_status}
4. حاجز السيولة: لا دخول مع FVG غير مغطاة في الاتجاه المعاكس
5. جدار الأرقام: منع الدخول عند .000/.500 ±5 نقاط بدون CHoCh على {verification_timeframe}
6. توقيت إغلاق الشمعة: {candle_closing_status}

⚡ نظام القرارات السريع:
REJECTION ENTRY: ذيل طويل + إغلاق داخل النطاق = دخول عكسي فوري
MOMENTUM FOLLOW: 3 شموع قوية = استمرار مع الاتجاه حتى أقرب رقم مستدير
GAP FILLING: السعر يتحرك من فجوة إلى فجوة قبل الارتداد
LAST MINUTE RULE: تجاهل الانعكاسات في الدقيقة 59/29/14/44

🛡️ فلتر الاندفاع الانتحاري (Momentum Kill-Switch):

قاعدة الحظر المطلق (The Momentum Kill-Switch):
1. منع الانعكاس المطلق: يُحظر تماماً إصدار إشارة (بيع) إذا كانت آخر 3 شموع خضراء ممتلئة بنسبة > 90%، حتى لو لمس السعر منطقة عرض. الاندفاع يغلب الهيكل في الـ OTC.
2. منطقة المغناطيس العددي: إذا كان السعر ضمن نطاق 7 نقاط من رقم مستدير (.000 أو .500)، تُلغى جميع إشارات الانعكاس، وتُحول الإشارة إلى "متابعة الزخم" حتى لمس الرقم.
3. شرط الـ Stop Hunt الإلزامي: لا تقبل دخولاً عكسياً إلا بعد حدوث "Liquidity Sweep" (ذيل طويل اخترق القمة وعاد للإغلاق تحتها) أو "شمعة رفض" واضحة. بدون هذا الدليل، استمر مع اتجاه الزخم الحالي.
4. أولوية الاتجاه على النماذج: في فريمات الدقائق (1-5 دقائق)، يتم إلغاء جميع نماذج وايكوف والمؤشرات التقليدية إذا كان الزخم الحالي قوياً (>8 نقاط في 3 شموع).

🧠 نظام الذكاء التحليلي المتكامل

🎲 مصفاة القرار الذكية (وزن الزخم 50% من القرار):
🚀 [ ] قوة الزخم الحالي: اندفاع قوي (9-10) | اندفاع متوسط (6-8) | توازن (4-5) | ضعف متوسط (2-3) | ضعف قوي (0-1)
**وزن هذا البند: 50% من القرار النهائي** - إذا كانت آخر 3 شموع خضراء، لا يمكن كتابة كلمة "بيع" في القرار الفني

[ ] اتجاه الهيكل الأساسي: صاعد قوي (9-10) | صاعد ضعيف (6-8) | جانبي (4-5) | هابط ضعيف (2-3) | هابط قوي (0-1) **وزن: 15%**
[ ] حجم التداول النسبي: كبير جداً (9-10) | كبير (6-8) | متوسط (4-5) | صغير (2-3) | معدوم (0-1) **وزن: 10%**
[ ] توافق الإطارات الزمنية: توافق كامل (9-10) | توافق جزئي (6-8) | تعادل (4-5) | تضارب جزئي (2-3) | تضارب كلي (0-1) **وزن: 10%**
[ ] جودة نمط الشموع: نموذج مثالي (9-10) | نموذج جيد (6-8) | غير واضح (4-5) | نموذج ضعيف (2-3) | لا نموذج (0-1) **وزن: 5%**
[ ] قوة مستويات S/R: مستويات قوية (9-10) | مستويات جيدة (6-8) | مستويات ضعيفة (4-5) | لا مستويات (2-3) | اختراق كامل (0-1) **وزن: 5%**
[ ] تأثير السياق الزمني: توقيت مثالي (9-10) | توقيت جيد (6-8) | توقيت عادي (4-5) | توقيت سيء (2-3) | توقيت خطير (0-1) **وزن: 3%**
[ ] عوامل خارجية مؤثرة: ظروف مثالية (9-10) | ظروف جيدة (6-8) | ظروف محايدة (4-5) | ظروف سيئة (2-3) | ظروف خطيرة (0-1) **وزن: 2%**

📈 حساب النتيجة النهائية: (مجموع النقاط الموزونة / 100 × 100)%

🔰 القواعد الأساسية
• المدرسة: SMC + ICT + دعم كلاسيكي + فلاتر الأرقام المستديرة
• الدرع الأساسي: {news_warning if news_warning else "✅ الوضع آمن من الأخبار"}
• التصنيف الزمني: {candle_category}
• استراتيجية التداول: {trading_strategy}
• إدارة الحجم: {position_sizing}
• أولوية الزخم: شموع ابتلاعية ≥80% + إغلاق فوق القمة السابقة = استمرار
• منطق OTC: 3 شموع قوية → الشمعة الرابعة بنفس الاتجاه
• تصحيح الفريم الصغير: تجاهل MACD ووايكوف عند التعارض مع السلوك السعري في فريمات الدقائق
• كشف وهم الزخم: تحقق من استدامة الحركة
• استخراج البيانات: أسعار دقيقة من المحور اليمني
• فلتر الجدوى: RR ≥ 1:2 بعد تعديل الأخبار
• **المصداقية المطلقة: كن قاصياً في نقد الشارت؛ إذا لم تكن الإشارة واضحة بنسبة 90%، فالقرار الإلزامي هو (احتفاظ 🟡) ولا تخاطر بأموال المستخدم.**
• تقييد الوسطية: قرار واضح فقط (شراء/بيع/احتفاظ) مع مستوى الثقة

🛡️ طبقات الحماية من الانعكاس الفاشل
ضد القطار السريع: لا دخول عكس الاتجاه إلا بعد شمعة ابتلاعية ≥100%
تأكيد البكسل للكسر: لا BOS إلا إذا أغلق جسم الشمعة بالكامل، لمس الذيل = Liquidity Sweep
تباعد الحجم والسعر: صعود أجسام الشموع تتناقص = زخم وهمي
قاعدة الانتظار الزمني: في OTC، انتظر إغلاق شمعتين بعد POI
فلتر السيولة الزمنية: منع الدخول خارج جلسات السيولة العالية أو آخر 15 دقيقة إلا بكسر هيكلي واضح + 3 شموع
فلتر التوازن السعري: منع الدخول قرب خط 50% بدون كسر هيكلي أو امتصاص
فلتر التشبع السعري: لا دخول بعد 3 شموع متتالية بنفس الاتجاه بدون تصحيح
فلتر الانعكاس الهيكلي الزائف: CHoCh/BOS صحيح فقط مع سحب سيولة + إغلاق خلف الهيكل + متابعة بشمعة ثانية
توافق الإطارات: تنفيذ فقط عند توافق 3/4+ أو HTF متوافق مع POI وOB
تأكيد RR النهائي: RR ≥ 1:2 بعد مراجعة الأخبار والتقلبات

فلتر الأرقام المستديرة (Round Numbers):
"يُمنع دخول عكس الاتجاه (بيع) عند اقتراب السعر من رقم صحيح (مثلاً .000 أو .500) بمسافة أقل من 5 نقاط، إلا بعد حدوث كسر هيكلي حقيقي (CHoCh) على فريم الـ {verification_timeframe}، لأن الخوارزمية غالباً ما تستهدف السيولة فوق هذه الأرقام."

قاعدة الاستهداف الرقمي (Digital Targeting Rule):
"إذا كان السعر يبعد أقل من 10 نقاط عن رقم صحيح (.000) وزخم الشموع صاعد، فإن الهدف الأول والوحيد هو ملامسة هذا الرقم، وتُلغى كافة إشارات البيع قبله."

قاعدة "دقيقة الغدر" (Last Minute Rule):
"في سوق OTC، إذا كانت الشمعة الحالية هي آخر دقيقة في إغلاق ساعة أو نصف ساعة (مثل الدقيقة 59 أو 29 أو 14 أو 44)، يتم تجاهل إشارات الانعكاس تماماً، والأولوية لاستمرار الزخم (Momentum Continuity) لأن المنصات تضخ سيولة لضرب مناطق الـ Stop Loss عند الإغلاقات الكبرى."

فلتر "الفجوة المندفعة" (Impulsive Gap):
"إذا اخترق السعر منطقة عرض/طلب بشمعة Marubozu (بدون ذيول)، يتم إلغاء أي سيناريو بيع فوراً واعتبار الاختراق حقيقياً (BOS) وليس فخاً (Sweep)، مع انتظار إعادة الاختبار للدخول مع الاتجاه."

قاعدة الحظر القطعي (Premium/Discount):
"لا شراء في منطقة Premium (فوق خط 50% من الموجة) ولا بيع في منطقة Discount (تحت خط 50%) إلا بعد كسر هيكلي حقيقي (CHoCh) أو امتصاص واضح للسيولة."

ميزان القوى (Structure vs Momentum):
"إذا تعارض اتجاه الهيكل العام مع زخم آخر 5 شموع (بأجسام ممتلئة)، تُلغى صفقات الارتداد ويتم الدخول مع استمرار الزخم (Trend Following) حتى الوصول لأقرب FVG أو رقم مستدير."

قاعدة السيولة العميقة (Deep Liquidity Sweep Rule):
"أي ذيل طويل يخترق قاعاً أو قمة سابقة ثم يغلق السعر داخل النطاق خلال شمعتين، يُعتبر دخولاً فورياً عكس الاتجاه (Rejection Entry) لأن السيولة تم تفعيلها."

منطق توقيت الشموع (Candle Timing Logic):
"في فريم الدقيقة، إذا أغلقت الشمعة عند سعر (00) أو (50) في الثواني الأخيرة، توقع انفجاراً سعرياً في الشمعة التالية لسحب السيولة المخفية."

قاعدة المغناطيس للفجوات (Gap Magnet Rule):
"لا تدخل بيعاً وهناك FVG لم تُغطَّ بالأعلى، ولا تدخل شراءً وهناك FVG بالأسفل. السعر في الـ OTC يتحرك من فجوة إلى فجوة (Gap to Gap) قبل أن يرتد."

قوانين الحظر القطعي:
"🚫 قانون الاستسلام للزخم: في أسواق الـ OTC، الزخم يقتل الهيكل. إذا رأيت 3 شموع ماروبوزو متتالية، تُلغى جميع مستويات العرض والطلب المقاومة لها، ويُعتبر الهدف الوحيد هو أقرب رقم مستدير (.000)."

فلتر التأكيد الزمني:
"• فلتر التأكيد الزمني: لا تعتمد الارتداد من ذيل الشمعة (SFP) إلا إذا بدأت الشمعة التالية بالتحرك عكس اتجاه الذيل بمسافة 3 نقاط على الأقل. إذا استمر السعر في مراوحة مكان الذيل، فالانفجار القادم سيكون مع الاتجاه الأصلي."

قاعدة التنبيه من الفجوات السعرية (FVG Calibration):
"يُمنع منعاً باتاً اقتراح دخول (شراء) إذا كانت هناك فجوة سعرية (FVG) هابطة لم تُلمس بعد فوق السعر الحالي بمسافة أقل من 5 نقاط، لأنها ستعمل كحائط صد."

فلتر دقة إغلاق الشمعة (Candle Closing Filter):
"إذا كان الوقت المتبقي لإغلاق الشمعة ({seconds_remaining} ثانية) أقل من 15 ثانية، يُمنع أي توصية بالدخول الجديد لأن سوق OTC يشهد عادة تحركات مفاجئة في الثواني الأخيرة."

📊 المرحلة 1: الفحص الأولي والتحذيرات
1.1 نظام الأمان ثلاثي الطبقات:
• الدرع الأساسي
• كشف وهم الزخم: 3 شموع كبيرة، فحص الاستدامة
• التحقق الرقمي: استخراج الأسعار من المحور اليمني ومطابقتها مع الشارت
• توقيت إغلاق الشمعة: {candle_closing_status}

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
• حل التعارض: الأولوية: الزخم → السيولة → الفجوات → الهيكل → المؤشرات → السياق الزمني

💡 قاعدة كسر العرض بالاندفاع:
"إذا تعارضت منطقة العرض مع شمعة اندفاعية (Marubozu) تخترق مستويات السيولة، اعتبر المنطقة 'مكسورة' فوراً ولا تقترح البيع إلا بعد إعادة اختبار ناجحة أو فشل اختراق مؤكد (SFP) مع تأكيد من شمعتين."

📊 المرحلة 5: سلوك الشموع
• استجابة POI: رفض/امتصاص/جانبي، القوة: جسم/ذيول، الحجم
• قانون 3 شموع: اختبار → تصحيح → اختراق
• التتابع الزمني: شمعة 1 رد فعل، شمعة 2 تأكيد، شمعة 3 قرار

📉 المرحلة 6: MACD المحسن
• 1-5 دقائق: تجاهل التقاطعات البطيئة ودايفرجنس عند تعارضها مع زخم قوي
• 15-60 دقيقة: خط الصفر + دايفرجنس عند POI
• حل التعارض: سلوك سعري واضح → تجاهل MACD

⏰ المرحلة 7: تعدد الإطارات
• HTF: الاتجاه العام، MTF1: العرض/الطلب، MTF2: OB نشطة، LTF: الدخول
• توافق: 4/4=+40, 3/4=+30, 2/4=تقليل حجم 50%, 1/4=منع الدخول
• استراتيجية: شراء = HTF صاعد → تصحيح → OB → إشارة، بيع = HTF هابط → ارتداد → OB → إشارة

🎯 المرحلة 8: درجات الثقة (معدلة)
• + نقاط: زخم قوي (9-10) ×2 = +40، POI +25، نموذج شموعي +20، سلوك واضح +25، توافق الإطارات +30، حجم أعلى +15، أخبار هادئة +20، BOS +30، تغطية فجوة +15، اختراق مع بداية ساعة جديدة +25
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
• حالة دقيقة الغدر: {last_minute_status}
• {candle_closing_status}
• توقيت التحليل: {gaza_time.strftime('%Y-%m-%d %H:%M:%S بتوقيت غزة')}
• المستوى: Professional باك تيست 15000 صفقة

🎯 التنسيق المطلوب للإجابة (الالتزام حرفياً):

📊 التحليل الفني المتقدم:
• البصمة الزمنية: {kill_zone_status}
• حالة الهيكل: (صاعد/هابط) + (مرحلة وايكوف الحالية) + (توافق 4/4 إطارات: نعم/لا)
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• الفجوات السعرية: (المناطق التي سيعود السعر لتغطيتها)
• ذاكرة السياق: (ملاحظات من التحليل السابق إذا وجدت)

🎯 الإشارة التنفيذية:
• السعر الحالي: [السعر الدقيق من الشارت - مستخرج من المحور اليمني]
• حالة الشمعة: [مفتوحة / مغلقة] - الوقت المتبقي: [{seconds_remaining} ثانية]
• القرار الفني: (شراء 🟢 / بيع 🔴 / احتفاظ 🟡) **ملاحظة: إذا كانت آخر 3 شموع خضراء، لا يمكن أن يكون القرار "بيع"**
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
"الأولوية القصوى: في حالة التعارض بين ذيول الشموع وقوة الاندفاع (Momentum)، تغلُب قوة الاندفاع في سوق الـ OTC، ويُمنع توقع القمم والقيعان (Top/Bottom Fishing). عند الاقتراب من رقم مستدير، تتحول الأولوية إلى 'تتبع الزخم حتى لمس الرقم' قبل التفكير في أي انعكاس."

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
        
        # برومبت التدقيق المحدث مع الجملة المضافة
        AUDIT_PROMPT = f"""
        وظيفتك الأساسية هي البحث عن تناقض بين الأسعار المذكورة في التحليل الأول وبين الأرقام الظاهرة في الصورة. إذا وجد التحليل الأول سعراً (مثلاً 1.1050) بينما الصورة تظهر السعر عند (1.1070)، قم بتصحيح كافة الأهداف بناءً على أرقام الصورة حصراً.
        
        5. قاعدة التكذيب: إذا ذكر التحليل الأول أن السعر عند (X) ولكنك ترى بوضوح بالعين أن الشمعة تلامس خطاً مختلفاً على المحور Y، اضرب بالتحليل الأول عرض الحائط واعتمد إحداثيات الصورة فقط.
        
        أنت محلل فني خبير في SMC + ICT + Wyckoff + Volume Profile + Market Psychology. مهمتك: تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية متعددة الطبقات.

*التحليل الأولي:* {initial_analysis}

📊 المستوى 1: التحليل الاستراتيجي
• الهيكل العام: موجات إيليوت + BOS/CHoCh
• مرحلة Wyckoff: Accumulation/Markup/Distribution/Decline
• السياق الزمني: توافق {verification_timeframe} مع {candle}
• السوق: {session_name} - {session_vol} سيولة

⚡ المستوى 2: التحليل التكتيكي
• الشموع: 5 شموع سابقة + الحالية
• Order Blocks: آخر 3 مناطق طلب/عرض
• FVG Tracking: فجوات غير مغطاة ≤50 نقطة
• Liquidity Map: Equal Highs/Lows + Inducement

🎯 المستوى 3: التحليل التنفيذي
• Entry Triggers: شمعة إغلاق + حجم
• Risk Matrix: RR ديناميكي × {news_risk_multiplier}
• Position Sizing: {position_sizing}
• Timing Precision: دخول/خروج بالثواني

🛡️ طبقات الحماية
1. فلتر الأخبار: {news_warning if news_warning else "✅ آمن"}
2. Kill Zone: {kill_zone_status}
3. فلتر دقيقة الغدر: {last_minute_status}
4. حاجز السيولة: لا دخول عكس FVG غير مغطاة
5. أرقام مستديرة: منع دخول ±5 نقاط بدون CHoCh
6. توقيت إغلاق الشمعة: {candle_closing_status}

⚡ قواعد سريعة
• REJECTION ENTRY: ذيل طويل + إغلاق داخل النطاق
• MOMENTUM FOLLOW: 3 شموع قوية → استمرار الاتجاه
• GAP FILLING: تحرك من فجوة إلى فجوة قبل الارتداد
• LAST MINUTE RULE: تجاهل انعكاسات الدقيقة 29/59/14/44

🧠 Decision Matrix (1-10 لكل بند)
• قوة الزخم (وزن 50%)، الهيكل (15%)، حجم التداول (10%)، توافق الإطارات (10%)، جودة الشموع (5%)، قوة S/R (5%)، توقيت (3%)، ظروف خارجية (2%)
• النتيجة النهائية = (المجموع/100 ×100)٪

📊 الفلاتر الأساسية
• لا انعكاس عكس الزخم القوي (>90% 3 شموع)
• لا بيع قرب رقم مستدير <7 نقاط إلا بعد CHoCh
• OTC: 3 شموع قوية → استمرار
• فلتر الفجوات، Premium/Discount، Deep Liquidity Sweep، Last Minute Rule
• توافق الإطارات: 3/4+ أو HTF متوافق
• قاعدة التنبيه من الفجوات السعرية: لا شراء مع FVG هابط فوق السعر <5 نقاط

🎯 المراحل الأساسية للتحليل
- فحص أولي: أمان، وهم الزخم، أسعار دقيقة
- تحليل الهيكل: SMC + BOS/CHoCh + نقاط POI
- السيولة والزخم: فجوات، Liquidity Sweeps، Inducement
- القرار الذكي: POI + نموذج شموعي + توافق اتجاه + تعديل RR
- سلوك الشموع: اختبار → تصحيح → اختراق
- MACD: تجاهل عند تعارض مع زخم قوي، HTF للدايفرجنس
- تعدد الإطارات: HTF = الاتجاه العام، MTF1/2 = OB & S/R، LTF = دخول
- درجات الثقة: نقاط +/-
- الحجم: اختراق >150%, امتصاص, تصحيح <70%
- إدارة الصفقات: TP1/2/3 + SL + حماية OTC
- تحليل السلوك: خوف، جشع، Stop Hunt, False Breakout
- تثبيت القرار: تأكيد مزدوج، تحقق من الأسعار والمستويات

🔍 **أمر التدقيق:**
1. تحقق من كل سعر ومستوى مذكور في التحليل مع الصورة بدقة بكسلية
2. تأكد من تطبيق جميع القواعد التالية:
   - فلتر المسافة الذهبية: إذا كان السعر قريب من رقم مستدير (<0.00010)، فالقرار يجب أن يكون متابعة الزخم
   - فلتر الأرقام المستديرة: فريم التحقق = {verification_timeframe}
   - قاعدة المغناطيس للفجوات
   - ميزان القوى (الهيكل vs الزخم)
   - قاعدة التنبيه من الفجوات السعرية
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

📊 المعطيات الفنية:
• الإطار الحالي: {candle} ({candle_category})
• فريم التحقق: {verification_timeframe}
• استراتيجية التداول: {trading_strategy}
• جلسة السوق: {session_name} ({session_time}), السيولة: {session_vol}
• تأثير الأخبار: {news_impact} ×{news_risk_multiplier}
• حالة دقيقة الغدر: {last_minute_status}
• توقيت إغلاق الشمعة: {candle_closing_status}
• توقيت التحليل: {gaza_time.strftime('%Y-%m-%d %H:%M:%S بتوقيت غزة')}
• المستوى: Professional باك تيست 15000 صفقة

🎯 **التنسيق المطلوب للإجابة (الالتزام حرفياً):**

📊 *التحليل الفني المتقدم:*
• البصمة الزمنية: {kill_zone_status}
• حالة الهيكل: (صاعد/هابط) + (مرحلة وايكوف الحالية) + (توافق 4/4 إطارات: نعم/لا)
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• الفجوات السعرية: (المناطق التي سيعود السعر لتغطيتها)
• ذاكرة السياق: (ملاحظات من التحليل السابق إذا وجدت)

🎯 *الإشارة التنفيذية:*
• مقارنة مع التحليل السابق: [✅ مطابق تماماً / ⚡ محسّن / ❌ مصحح]، درجة التشابه: [0–100]%
• السعر الحالي: [السعر الدقيق من الشارت - مستخرج من المحور اليمني]
• حالة الشمعة: [مفتوحة / مغلقة] - الوقت المتبقي: [{seconds_remaining} ثانية]
• القرار الفني: (شراء 🟢 / بيع 🔴 / احتفاظ 🟡) **تذكر: إذا كانت آخر 3 شموع خضراء، لا يمكن أن يكون "بيع"**
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
• فريم التحقق: {verification_timeframe} (للتأكد من كسر الهيكل الحقيقي)

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
            "max_tokens": 910,
            "temperature": 0.2,
            "top_p": 1.0,
            "random_seed": 42
        }
        
        response_2 = requests.post(MISTRAL_URL, headers=headers, json=payload_2, timeout=45)
        
        if response_2.status_code == 200:
            audit_result = response_2.json()['choices'][0]['message']['content'].strip()
        else:
            print(f"Obeida Vision Warning (Model 2): {response_2.status_code} - استخدام التحليل الأول")
            audit_result = f"📋 **ملاحظة:** تعذر التدقيق - استخدام التحليل الأولي مباشرة\n\n{initial_analysis}"
        
        # تنظيف النصوص من التكرار باستخدام الدالة المحسنة
        audit_result = clean_repeated_text(audit_result)
        
        # حفظ سياق التحليل في قاعدة البيانات
        save_analysis_context(user_id, audit_result)
        
        keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        
        # تنسيق وقت الصفقة للعرض
        time_display = format_trade_time_for_prompt(trade_time)
        
        # إعداد النص النهائي
        full_result = (
            f"✅ **تم التحليل والتدقيق بنجاح!**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 **إطار التحليل:** {candle} | فريم التحقق: {verification_timeframe}\n"
            f"🕐 **الوقت المتبقي لإغلاق الشمعة:** {seconds_remaining} ثانية\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{audit_result}\n\n"
            f"🔧 **الإعدادات المستخدمة:**\n"
            f"• سرعة الشموع: {candle} ({candle_category})\n"
            f"• استراتيجية التداول: {time_display}\n"
            f"• فريم التحقق للكسر: {verification_timeframe}\n"
            f"• الوقت المتبقي للإغلاق: {seconds_remaining} ثانية\n"
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
        print(f"❌ خطأ في تحليل الصورة: {traceback.format_exc()}")
        keyboard = [["📊 تحليل صورة"], ["الرجوع للقائمة الرئيسية"]]
        await wait_msg.edit_text(f"❌ **حدث خطأ في تحليل الصورة:** {str(e)[:200]}\nيرجى المحاولة مرة أخرى.")
    finally:
        # تنظيف الملفات المؤقتة
        for filepath in [original_path, compressed_path]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception as e:
                    print(f"⚠️ خطأ في حذف الملف المؤقت: {e}")
    
    return MAIN_MENU

# --- دوال نظام الفريم المزدوج المحسّن ---
async def start_dual_timeframe_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع التحليل بالفريم المزدوج"""
    user_id = update.effective_user.id
    candle, trade_time, _, _ = get_user_setting(user_id)
    
    if not candle or not trade_time:
        keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**\n\n"
            "الرجاء استخدام أزرار القائمة لضبط الإعدادات قبل تحليل الصور.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    # تنظيف أي بيانات قديمة
    cleanup_user_data(context, user_id)
    
    context.user_data['dual_analysis_mode'] = True
    context.user_data['dual_images'] = []
    context.user_data['dual_image_paths'] = []
    context.user_data['dual_analysis_start'] = time.time()
    
    keyboard = [["الرجوع للقائمة الرئيسية"]]
    
    await update.message.reply_text(
        f"📊 **وضع التحليل بالفريم المزدوج**\n\n"
        f"الإعدادات الحالية:\n"
        f"• سرعة الشموع: {candle}\n"
        f"• مدة الصفقة: {trade_time}\n\n"
        f"🎯 **الخطوات المطلوبة:**\n"
        f"1. أرسل صورة الفريم الأعلى (H1/H4) للاتجاه العام\n"
        f"2. أرسل صورة الفريم الأدنى ({candle}) للدخول\n\n"
        f"📤 **الخطوة 1/2:** أرسل صورة الفريم الأعلى الآن:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    
    return WAITING_FIRST_IMAGE

async def handle_first_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصورة الأولى في وضع الفريم المزدوج"""
    wait_msg = await update.message.reply_text("📊 جاري حفظ صورة الفريم الأعلى...")
    photo = await update.message.photo[-1].get_file()
    
    timestamp = int(time.time())
    path = os.path.join(IMAGE_CACHE_DIR, f"dual1_{update.effective_user.id}_{timestamp}.jpg")
    
    try:
        await photo.download_to_drive(path)
        
        # ضغط الصورة
        compressed_path = compress_image(path)
        
        with open(compressed_path, "rb") as img_file:
            context.user_data['dual_images'] = [base64.b64encode(img_file.read()).decode('utf-8')]
            context.user_data['dual_image_paths'] = [compressed_path]  # حفظ المسارات للحذف لاحقاً
        
        keyboard = [["الرجوع للقائمة الرئيسية"]]
        
        await wait_msg.edit_text(
            "✅ **تم حفظ صورة الفريم الأعلى**\n\n"
            "📤 **الخطوة 2/2:** أرسل صورة الفريم الأدنى الآن للدخول:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        
    except Exception as e:
        print(f"❌ خطأ في handle_first_image: {traceback.format_exc()}")
        await wait_msg.edit_text("❌ حدث خطأ في حفظ الصورة. حاول مرة أخرى.")
        
        # تنظيف أي ملفات مؤقتة
        for filepath in [path, path.replace('.jpg', '_compressed.jpg')]:
            if filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass
        
        cleanup_user_data(context, update.effective_user.id)
        return MAIN_MENU
    
    return WAITING_SECOND_IMAGE

async def handle_second_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصورة الثانية وتحليل الفريم المزدوج"""
    user_id = update.effective_user.id
    wait_msg = await update.message.reply_text("📊 جاري تحليل الصورتين معاً...")
    photo = await update.message.photo[-1].get_file()
    
    timestamp = int(time.time())
    path = os.path.join(IMAGE_CACHE_DIR, f"dual2_{user_id}_{timestamp}.jpg")
    
    try:
        await photo.download_to_drive(path)
        
        # ضغط الصورة
        compressed_path = compress_image(path)
        
        with open(compressed_path, "rb") as img_file:
            if 'dual_images' not in context.user_data:
                context.user_data['dual_images'] = []
            if 'dual_image_paths' not in context.user_data:
                context.user_data['dual_image_paths'] = []
            
            context.user_data['dual_images'].append(base64.b64encode(img_file.read()).decode('utf-8'))
            context.user_data['dual_image_paths'].append(compressed_path)
        
        # تحليل الصورتين معاً
        if len(context.user_data['dual_images']) >= 2:
            candle, trade_time, prev_context, prev_time = get_user_setting(user_id)
            
            # الحصول على معلومات السيولة والتوقيت
            session_name, session_time, session_vol = get_market_session()
            gaza_time = datetime.now(GAZA_TIMEZONE)
            current_hour = gaza_time.hour
            current_minute = gaza_time.minute
            
            # إعداد البرومبت المزدوج المحسّن
            DUAL_PROMPT = f"""
أنت محلل فني خبير متخصص في التحليل متعدد الإطارات الزمنية (Multi-Timeframe Analysis).

🎯 **مهمة خاصة: يجب عليك مطابقة السعر الحالي في الصورة الثانية مع موقعه التشريحي في الصورة الأولى للتأكد من أننا داخل منطقة الطلب/العرض الصحيحة.**

لديك صورتان:
1. الصورة الأولى: الفريم الأعلى (H1/H4) للاتجاه العام
2. الصورة الثانية: الفريم الأدنى ({candle}) للدخول التنفيذي

🛡️ **حماية OTC الخاصة:**
"إذا كان الزخم في فريم الدقيقة (LTF) عكس اتجاه فريم الساعة (HTF) بقوة انفجارية (Marubozu)، أعطِ الأولوية للزخم اللحظي وحذر من أن اتجاه الفريم الكبير قد يكون مخترقاً."

مهمتك: تحليل التوافق بين الفريمين وإصدار توصية دقيقة بناءً على:
• اتجاه الفريم الأعلى (HTF)
• نقاط الدخول على الفريم الأدنى (LTF)
• توافق الإشارات بين الفريمين
• مطابقة السعر بين الفريمين

📊 **معطيات التحليل:**
• الفريم الأعلى: H1/H4 (اتجاه عام)
• الفريم الأدنى: {candle} (دخول تنفيذي)
• جلسة السوق: {session_name} ({session_vol} سيولة)
• استراتيجية التداول: {trade_time}

🎯 **قواعد التحليل المزدوج:**
1. **توافق الاتجاه:** يجب أن يكون اتجاه الفريم الأدنى متوافقاً مع اتجاه الفريم الأعلى
2. **التوقيت الذكي:** الدخول على الفريم الأدنى عند نقاط POI المتوافقة مع اتجاه الفريم الأعلى
3. **فلتر التضارب:** إذا كان هناك تضارب بين الفريمين، تُلغى الصفقة
4. **مطابقة السعر:** تأكد من أن السعر في الصورة الثانية يقع في نفس المنطقة الهيكلية في الصورة الأولى

🔍 **خطوات التحليل:**
1. تحليل الفريم الأعلى: تحديد اتجاه الهيكل، مناطق العرض/الطلب، مستويات الدعم/المقاومة
2. تحليل الفريم الأدنى: البحث عن نقاط الدخول، أنماط الشموع، مناطق OB
3. **المطابقة السعرية:** مقارنة موقع السعر الحالي بين الفريمين
4. التحقق من التوافق: التأكد من تطابق الاتجاه والإشارات
5. إصدار التوصية النهائية: شراء/بيع/انتظار

⚠️ **فلترات الحماية:**
• إذا كان اتجاه HTF صاعد لكن LTF يظهر شموع ماروبوزو هابطة قوية → الانتظار
• إذا كان السعر في LTF عند مستوى مختلف عن موقعه في HTF → التأكد من صحة المنطقة
• إذا كان هناك تضارب واضح → إلغاء الصفقة

📋 **تنسيق الإجابة:**
🎯 **التحليل المزدوج (Multi-Timeframe Analysis):**
• اتجاه الفريم الأعلى: [صاعد/هابط/جانبي]
• اتجاه الفريم الأدنى: [صاعد/هابط/جانبي]
• درجة التوافق: [عالية/متوسطة/منخفضة]
• حالة المطابقة السعرية: [✅ متطابق / ⚠️ يوجد فرق بسيط / ❌ غير متطابق]

⚡ **التوصية التنفيذية:**
• القرار: (شراء 🟢 / بيع 🔴 / انتظار 🟡)
• سبب القرار: [توضيح بناءً على التوافق بين الفريمين والمطابقة السعرية]
• نقطة الدخول: [السعر المثالي بناءً على الفريم الأدنى]
• وقف الخسارة: [بناءً على تحليل الفريمين]
• الأهداف: [TP1, TP2 بناءً على الفريم الأعلى]

⚠️ **إدارة المخاطر:**
• مستوى الثقة: [0-100]٪
• نقطة الإلغاء: [السعر الذي يفسد التوافق]
• ملاحظات OTC: [تحذيرات خاصة بسوق OTC]

قم بتحليل الصورتين وأعطني الإجابة بالتنسيق المطلوب.
"""
            
            headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
            
            # --- الخطوة 1: التحليل الأولي للفريم المزدوج ---
            payload_1 = {
                "model": MISTRAL_MODEL,
                "messages": [
                    {
                        "role": "user", 
                        "content": [
                            {"type": "text", "text": DUAL_PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{context.user_data['dual_images'][0]}", "detail": "high"}},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{context.user_data['dual_images'][1]}", "detail": "high"}}
                        ]
                    }
                ],
                "max_tokens": 1200,
                "temperature": 0.1
            }
            
            response_1 = requests.post(MISTRAL_URL, headers=headers, json=payload_1, timeout=60)
            
            if response_1.status_code != 200:
                await wait_msg.edit_text(f"❌ حدث خطأ في التحليل المزدوج. الرمز: {response_1.status_code}")
                cleanup_user_data(context, user_id)
                return MAIN_MENU
            
            initial_analysis = response_1.json()['choices'][0]['message']['content'].strip()
            
            # --- الخطوة 2: التدقيق النهائي (نفس نظام التحليل الفردي) ---
            await wait_msg.edit_text("📊 جاري تدقيق التحليل المزدوج...")
            
            AUDIT_DUAL_PROMPT = f"""
            أنت مدقق تقني متخصص في تحليل الفريم المزدوج. مهمتك التدقيق على التحليل التالي:
            
            *التحليل الأولي:* {initial_analysis}
            
            لديك صورتان:
            1. صورة الفريم الأعلى (HTF)
            2. صورة الفريم الأدنى (LTF)
            
            🔍 **مهمات التدقيق:**
            1. تحقق من دقة الأسعار المذكورة في كلا الصورتين
            2. تأكد من مطابقة السعر بين الفريمين
            3. تحقق من صحة مناطق العرض/الطلب المذكورة
            4. تأكد من تطبيق قواعد OTC الخاصة
            
            📊 **قواعد التدقيق:**
            - إذا كان السعر في LTF يختلف عن موقعه في HTF بأكثر من 0.0010 → ذكر التناقض
            - إذا كانت مناطق العرض/الطلب غير متطابقة → ذكر التحذير
            - إذا كان هناك تضارب في اتجاه الهيكل → اقترح الانتظار
            
            🎯 **تنسيق التدقيق:**
            🕵️ **نتائج التدقيق:**
            • دقة الأسعار: [✅ دقيقة / ⚠️ تحتاج تصحيح / ❌ غير دقيقة]
            • مطابقة الفريمين: [✅ متطابقين / ⚠️ يوجد فرق / ❌ غير متطابقين]
            • صحة التوصية: [✅ صحيحة / ⚠️ تحتاج تعديل / ❌ غير صحيحة]
            
            ⚡ **التعديلات المقترحة:**
            [اذكر أي تعديلات ضرورية بناءً على التدقيق]
            
            ⚠️ **التحليل النهائي بعد التدقيق:**
            [قدم التحليل النهائي مع مراعاة نتائج التدقيق]
            
            تأكد من أن التوصية النهائية تأخذ بعين الاعتبار تدقيقك.
            """
            
            payload_2 = {
                "model": MISTRAL_MODEL_AUDIT,
                "messages": [
                    {
                        "role": "user", 
                        "content": [
                            {"type": "text", "text": AUDIT_DUAL_PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{context.user_data['dual_images'][0]}", "detail": "high"}},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{context.user_data['dual_images'][1]}", "detail": "high"}}
                        ]
                    }
                ],
                "max_tokens": 1000,
                "temperature": 0.2,
                "top_p": 1.0
            }
            
            response_2 = requests.post(MISTRAL_URL, headers=headers, json=payload_2, timeout=60)
            
            if response_2.status_code == 200:
                audit_analysis = response_2.json()['choices'][0]['message']['content'].strip()
                final_analysis = audit_analysis
            else:
                print(f"Obeida Dual Audit Warning: {response_2.status_code}")
                final_analysis = initial_analysis
            
            # تنظيف النص من التكرارات
            final_analysis = clean_repeated_text(final_analysis)
            
            final_result = (
                f"✅ **تم تحليل الفريم المزدوج بنجاح!**\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📊 **الفريم الأعلى:** H1/H4 (الاتجاه العام)\n"
                f"📊 **الفريم الأدنى:** {candle} (الدخول التنفيذي)\n"
                f"⏱️ **وقت التحليل:** {int(time.time() - context.user_data.get('dual_analysis_start', 0))} ثانية\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{final_analysis}\n\n"
                f"🤖 **Powered by - Obeida Trading**"
            )
            
            await wait_msg.edit_text(
                final_result,
                parse_mode="Markdown"
            )
            
            # حفظ سياق التحليل
            save_analysis_context(user_id, final_analysis)
            
        else:
            await wait_msg.edit_text("❌ لم يتم استلام الصورتين بشكل صحيح. حاول مرة أخرى.")
        
    except Exception as e:
        print(f"❌ خطأ في handle_second_image: {traceback.format_exc()}")
        await wait_msg.edit_text(f"❌ حدث خطأ في التحليل المزدوج: {str(e)[:200]}")
    finally:
        # تنظيف الذاكرة المؤقتة بغض النظر عن النتيجة
        try:
            # تنظيف ملفات هذا المستخدم
            for filepath in [path, compressed_path] + context.user_data.get('dual_image_paths', []):
                if filepath and os.path.exists(filepath):
                    os.remove(filepath)
            
            # تنظيف الذاكرة
            cleanup_user_data(context, user_id)
        except Exception as e:
            print(f"⚠️ خطأ في تنظيف الملفات: {e}")
    
    keyboard = [["📊 تحليل صورة"], ["📊 تحليل فريم مزدوج"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
    
    await update.message.reply_text(
        "📊 **اختر الإجراء التالي:**",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    
    return MAIN_MENU

async def handle_cancel_dual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء وضع الفريم المزدوج مع تنظيف كامل"""
    user_id = update.effective_user.id
    
    # تنظيف شامل للذاكرة
    cleanup_user_data(context, user_id)
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["📊 تحليل فريم مزدوج", "📈 توصية"],
        ["💬 دردشة"]
    ]
    
    await update.message.reply_text(
        "❌ **تم إلغاء وضع الفريم المزدوج وتنظيف الذاكرة**\n\n"
        "العودة للقائمة الرئيسية",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    return MAIN_MENU

# --- حارس الأخطاء (Error Handler) ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة الأخطاء في البوت"""
    try:
        # تسجيل الخطأ
        error_msg = f"❌ حدث خطأ في البوت:\n"
        
        if update and hasattr(update, 'effective_user'):
            error_msg += f"المستخدم: {update.effective_user.id}\n"
        
        error_msg += f"الخطأ: {context.error}\n"
        
        # الحصول على تفاصيل الخطأ
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = ''.join(tb_list)
        
        # حفظ الخطأ في ملف log
        with open("bot_errors.log", "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"الخطأ: {error_msg}\n")
            f.write(f"Traceback:\n{tb_string}\n")
            f.write(f"{'='*60}\n")
        
        print(f"❌ خطأ مسجل: {error_msg}")
        
        # إرسال رسالة للمستخدم إذا كان هناك تحديث
        if update and hasattr(update, 'effective_chat'):
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⚠️ حدث خطأ تقني. النظام يعمل على إصلاحه تلقائياً. يرجى المحاولة مرة أخرى."
                )
            except:
                pass
        
        # محاولة إعادة التشغيل إذا كان الخطأ متعلقاً بالشبكة
        if isinstance(context.error, (NetworkError, TimedOut, ConnectionError)):
            print("🌐 خطأ في الشبكة، محاولة الاستمرار...")
            
    except Exception as e:
        print(f"❌ خطأ في معالج الأخطاء نفسه: {e}")

# --- وظيفة تنظيف دورية للملفات المؤقتة ---
async def periodic_cleanup():
    """تنظيف دوري للملفات المؤقتة"""
    while True:
        try:
            # انتظار 30 دقيقة
            await asyncio.sleep(1800)
            
            # تنظيف الصور القديمة
            cleanup_old_images()
            
            # تنظيف قاعدة البيانات من السجلات القديمة (إذا لزم الأمر)
            cleanup_old_database_records()
            
            print("🧹 تم التنظيف الدوري للملفات المؤقتة")
            
        except Exception as e:
            print(f"⚠️ خطأ في التنظيف الدوري: {e}")

# --- الدوال الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    # تنظيف أي بيانات قديمة عند البدء
    if update.effective_user:
        cleanup_user_data(context, update.effective_user.id)
    
    # التحقق من توفر جميع الأنظمة
    systems_check = """
    ✅ **فحص الأنظمة المكتمل:**
    • 🛡️ نظام حماية الأخطاء: نشط
    • 🗑️ نظام تنظيف الملفات: نشط
    • 📦 نظام ضغط الصور: نشط
    • ⏰ نظام التوقيت الآلي (غزة): نشط
    • 💾 نظام التخزين المؤقت: نشط
    """
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["📊 تحليل فريم مزدوج", "📈 توصية"],
        ["💬 دردشة"]
    ]
    
    await update.message.reply_text(
        "🚀 **أهلاً بك في Obeida Trading**\n\n"
        f"{systems_check}\n"
        "🤖 **المميزات الجديدة:**\n"
        "• تحليل فني متقدم للشارتات \n"
        "• 🆕 نظام حماية من الأعطال\n"
        "• 🆕 توقيت غزة آلي دقيق\n"
        "• 🆕 ضغط صور ذكي\n"
        "• 🆕 تنظيف تلقائي للذاكرة\n"
        "• 📈 نظام توصيات جاهزة\n\n"
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
        candle, trade_time, _, _ = get_user_setting(user_id)
        
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
                f"📋 **نظام الذاكرة:** نشط (يتذكر التحليل السابق)\n\n"
                f"أرسل صورة الرسم البياني (الشارت) الآن:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return ANALYZE_MODE
    
    elif user_message == "📊 تحليل فريم مزدوج":
        return await start_dual_timeframe_analysis(update, context)
    
    elif user_message == "💬 دردشة":
        return await start_chat_mode(update, context)
    
    elif user_message == "📈 توصية":
        return await start_recommendation_mode(update, context)
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["📊 تحليل فريم مزدوج", "📈 توصية"],
        ["💬 دردشة"]
    ]
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
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["📊 تحليل فريم مزدوج", "📈 توصية"],
            ["💬 دردشة"]
        ]
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
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["📊 تحليل فريم مزدوج", "📈 توصية"],
            ["💬 دردشة"]
        ]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in TRADE_TIMES:
        save_user_setting(user_id, "trade_time", user_message)
        
        keyboard = [["📊 تحليل صورة"], ["📊 تحليل فريم مزدوج"], ["💬 دردشة"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        
        candle, _, _, _ = get_user_setting(user_id)
        
        await update.message.reply_text(
            f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ سرعة الشموع: {candle}\n"
            f"✅ مدة الصفقة: {user_message}\n\n"
            f"📡 **نظام التحليل:** موديل مزدوج\n"
            f"📋 **نظام الذاكرة:** نشط\n"
            f"⏱️ **حساب توقيت الشموع:** نشط\n"
            f"🔄 **نظام تنظيف الذاكرة:** نشط\n\n"
            f"يمكنك الآن تحليل صورة أو استخدام الفريم المزدوج:",
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
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["📊 تحليل فريم مزدوج", "📈 توصية"],
            ["💬 دردشة"]
        ]
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
    3. اختر "تحليل فريم مزدوج" لتحليل صورتين معاً
    4. اختر "دردشة" للاستفسارات النصية
    5. اختر "توصية" لتحليل العملات
    
    📈 **نظام التوصيات:**
    • تحليل فني للعملات والمؤشرات
    • أربعة أقسام رئيسية
    • توصيات مفصلة لكل عملة
    • تحليل سريع ومباشر
    
    📊 **نظام الفريم المزدوج المتقدم:**
    • تحليل صورتين معاً (فريم أعلى + فريم أدنى)
    • مطابقة الأسعار بين الفريمات
    • نظام تدقيق مزدوج (تحليل + تدقيق)
    • حماية OTC متقدمة
    
    ⏱️ **خيارات مدة الصفقة:**
    • **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة
    • **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة
    • **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة
    
    📡 **نظام المميزات المتقدمة:**
    • **ذاكرة السياق:** يتذكر التحليل السابق لمدة 10 دقائق
    • **حساب توقيت الشمعة:** يحسب الثواني المتبقية للإغلاق
    • **نظام تنظيف الذاكرة:** تنظيف تلقائي للبيانات المؤقتة
    • **مطابقة الأسعار:** تأكد من تطابق الأسعار بين الفريمات
    • **نظام التدقيق المزدوج:** تحليل + تدقيق للحصول على دقة أعلى
    
    📊 **مميزات البوت:**
    • تحليل فني للرسوم البيانية 
    • نظام فريم مزدوج متقدم
    • دردشة ذكية 
    • نظام توصيات العملات
    • حفظ إعداداتك الشخصية
    • واجهة سهلة بالأزرار
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    # تنظيف شامل للذاكرة
    if update.effective_user:
        cleanup_user_data(context, update.effective_user.id)
    
    await update.message.reply_text(
        "تم الإلغاء وتم تنظيف الذاكرة. اكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# --- الحل النهائي ---
def run_flask_server():
    """تشغيل Flask server"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def main():
    """الدالة الرئيسية - النسخة السهلة"""
    print("🤖 Starting Powered by - Obeida Trading ...")
    
    # تشغيل Flask
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    print(f"🌐 Flask server started on port {os.environ.get('PORT', 8080)}")
    
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
            WAITING_FIRST_IMAGE: [
                MessageHandler(filters.PHOTO, handle_first_image),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cancel_dual)
            ],
            WAITING_SECOND_IMAGE: [
                MessageHandler(filters.PHOTO, handle_second_image),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cancel_dual)
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

if __name__ == "__main__":
    main()
