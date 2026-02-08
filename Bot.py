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
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 1800:
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
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGB')
                elif img.mode == 'RGBA':
                    background.paste(img, mask=img.split()[-1])
                    img = background
                else:
                    img = img.convert('RGB')
            
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            compressed_path = image_path.replace('.jpg', '_compressed.jpg')
            img.save(compressed_path, 'JPEG', quality=quality, optimize=True)
            
            original_size = os.path.getsize(image_path) / 1024
            compressed_size = os.path.getsize(compressed_path) / 1024
            print(f"📦 تم ضغط الصورة: {original_size:.1f}KB → {compressed_size:.1f}KB")
            
            return compressed_path
    except Exception as e:
        print(f"⚠️ خطأ في ضغط الصورة: {e}")
        return image_path

# --- سحب الصور من TradingView ---
def download_chart_image(symbol="BTCUSDT"):
    """سحب صورة شارت من TradingView"""
    try:
        API_KEY = "c94425"
        
        # تحويل اسم الرمز إلى تنسيق TradingView
        if symbol == "BTC (OTC)":
            chart_symbol = "BINANCE:BTCUSDT"
        elif symbol == "EUR/USD (OTC)":
            chart_symbol = "FX:EURUSD"
        elif symbol == "Gold (OTC)":
            chart_symbol = "TVC:GOLD"
        elif symbol == "USOIL (OTC)":
            chart_symbol = "TVC:USOIL"
        elif "S&P 500 (OTC)" in symbol:
            chart_symbol = "SP:SPX"
        elif "Apple (OTC)" in symbol:
            chart_symbol = "NASDAQ:AAPL"
        else:
            chart_symbol = "BINANCE:BTCUSDT"
        
        CHART_URL = f"https://www.tradingview.com/chart/?symbol={chart_symbol}&interval=5"
        
        api_url = f"https://api.screenshotmachine.com/?key={API_KEY}&url={CHART_URL}&dimension=800x600&device=desktop&delay=2000&format=png"
        
        print(f"📥 جاري سحب صورة لـ {symbol}...")
        
        response = requests.get(api_url, timeout=30)
        
        if response.status_code == 200:
            timestamp = int(time.time())
            image_path = os.path.join(IMAGE_CACHE_DIR, f"chart_{symbol.replace(' ', '_')}_{timestamp}.png")
            
            with open(image_path, "wb") as f:
                f.write(response.content)
            
            print(f"✅ تم سحب صورة {symbol} بنجاح!")
            return image_path
        else:
            print(f"❌ خطأ في سحب الصورة: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"⚠️ خطأ في سحب الصورة: {e}")
        return None

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
            time_diff = (datetime.now() - datetime.fromisoformat(context_time)).total_seconds() / 60
            if time_diff > 10:
                return "", None
        return context, context_time
    return "", None

def cleanup_old_database_records():
    """تنظيف سجلات قاعدة البيانات القديمة"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
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
    
    lines = text.split('\n')
    unique_lines = []
    for line in lines:
        if line.strip() not in [ul.strip() for ul in unique_lines] or line.strip() == "":
            unique_lines.append(line)
    text = '\n'.join(unique_lines)
    
    patterns = ["📊 التحليل الفني المتقدم:", "🎯 الإشارة التنفيذية:", "⚠️ إدارة المخاطر:",
                "📊 **نتائج الفحص الفني**:", "🎯 **التوصية والتوقعات**:", 
                "⚠️ **إدارة المخاطر**:", "📝 **ملاحظات التحليل**:"]
    
    for p in patterns:
        if text.count(p) > 1:
            parts = text.split(p)
            text = parts[0] + p + parts[-1]
    
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
    """تنظيف البيانات المؤقتة للمستخدم - الإصدار المحسّن"""
    try:
        if user_id and os.path.exists(IMAGE_CACHE_DIR):
            try:
                for filename in os.listdir(IMAGE_CACHE_DIR):
                    if f"_{user_id}_" in filename:
                        filepath = os.path.join(IMAGE_CACHE_DIR, filename)
                        if os.path.exists(filepath):
                            os.remove(filepath)
                            print(f"🧹 تم حذف ملف المستخدم: {filename}")
            except Exception as e:
                print(f"⚠️ خطأ في تنظيف ملفات المستخدم {user_id}: {e}")
        
        keys_to_remove = [
            'dual_images', 'dual_image_paths', 'dual_analysis_mode',
            'last_analysis', 'dual_analysis_start', 'original_paths',
            'last_recommendation_symbol'
        ]
        
        for key in keys_to_remove:
            if key in context.user_data:
                del context.user_data[key]
                
        print(f"✅ تم تنظيف الذاكرة والملفات للمستخدم {user_id}")
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف الذاكرة: {e}")

def save_last_recommendation_symbol(context: ContextTypes.DEFAULT_TYPE, symbol: str):
    """حفظ آخر رمز تم اختياره في التوصيات"""
    context.user_data['last_recommendation_symbol'] = symbol

def get_last_recommendation_symbol(context: ContextTypes.DEFAULT_TYPE):
    """الحصول على آخر رمز تم اختياره في التوصيات"""
    return context.user_data.get('last_recommendation_symbol', 'BTC (OTC)')

# --- وظائف نظام التوصية الجديد ---
def get_mistral_analysis(symbol):
    """الحصول على تحليل من Mistral AI API للعملة"""
    headers = {
        "Authorization": f"Bearer {MISTRAL_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    بصفتك خبير تداول كمي، حلل {symbol} بناءً على "تلاقي الأدلة" (Confluence Analysis). 
    
    🛑 *شروط الفلترة الصارمة (إلغاء الصفقة فوراً إذا لم تتحقق):*
    1. حتمية الاستنفاذ: فشل آخر موجة جهد في كسر الهيكل.
    2. توافق الفركتلات: تطابق الاتجاه على فريمات (H4, H1, M15).
    3. سحب السيولة (Sweep): يجب حدوث كسر وهمي للسيولة قبل الدخول.
    4. منطقة التوازن (OTE): الدخول حصراً بين مستويات فيبوناتشي 0.618 و 0.886.

    🔍 *المطلوب تحليل (SMC + Wyckoff + Volume Profile):*
    - رصد الـ Order Block النشط و الـ FVG غير المغطى.
    - تحديد منطقة الفخ (Inducement) والسيولة المستهدفة (BSL/SSL).
    - حساب قوة الاتجاه باستخدام (RSI Divergence) وحجم التداول.

    قدم التقرير باللغة العربية بهذا التنسيق حصراً:
    
    📊 *ملخص فحص {symbol}*:
    - الهيكل: (صاعد/هابط/تجميع) 
    - السيولة: (أقرب فخ + الهدف القادم)
    - الفجوات: (أهم منطقة FVG نشطة)
    
    🎯 *خطة التنفيذ*:
    - القرار: (شراء 🟢 / بيع 🔴) 
    - القوة: (عالية/متوسطة/ضعيفة)
    - الدخول: [السعر الدقيق] 
    - الهدف (TP1/TP2): [مستويات السيولة]
    - الوقف (SL): [خلف منطقة الحماية] 
    - الزمن: [الوقت المتوقع بالدقائق]
    
    ⚠️ *المخاطرة*:
    - الثقة: [%] 
    - نقطة الإلغاء: [السعر الذي يفسد السيناريو]
    """
    
    body = {
        "model": MISTRAL_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 910,
        "temperature": 0.0,
        "top_p": 1.0,
        "random_seed": 42
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
    """معالجة اختيارات نظام التوصية مع سحب الصور"""
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
    
    # إذا وجدت العملة، ابدأ التحليل مع سحب الصورة
    if symbol_to_analyze:
        save_last_recommendation_symbol(context, symbol_to_analyze)
        wait_msg = await update.message.reply_text(f"⏳ جاري سحب وتحليل `{symbol_to_analyze}`...")
        
        # سحب صورة الشارت أولاً
        chart_image_path = download_chart_image(symbol_to_analyze)
        
        if chart_image_path and os.path.exists(chart_image_path):
            # إرسال الصورة للمستخدم
            with open(chart_image_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"📈 شارت {symbol_to_analyze} المباشر"
                )
            
            # استخدام الصورة للتحليل الفني
            try:
                # استدعاء تحليل الصورة
                await wait_msg.edit_text(f"📊 جاري تحليل شارت {symbol_to_analyze} بتقنيات متطورة...")
                
                # إنشاء كائن تحديث مؤقت للصورة
                from telegram import PhotoSize
                
                # تحميل الصورة ككائن PhotoSize
                photo_file = await context.bot.get_file(chart_image_path)
                
                # إنشاء تحديث مؤقت
                class TempUpdate:
                    def __init__(self, original_update, photo_path):
                        self.effective_user = original_update.effective_user
                        self.effective_chat = original_update.effective_chat
                        self.message = TempMessage(photo_path)
                
                class TempMessage:
                    def __init__(self, photo_path):
                        self.photo = [TempPhotoSize(photo_path)]
                        self.text = ""
                
                class TempPhotoSize:
                    def __init__(self, file_path):
                        self.file_path = file_path
                    
                    async def get_file(self):
                        class TempFile:
                            def __init__(self, path):
                                self.path = path
                            
                            async def download_to_drive(self, destination):
                                shutil.copy2(self.path, destination)
                                return destination
                        
                        return TempFile(self.file_path)
                
                temp_update = TempUpdate(update, chart_image_path)
                
                # الحصول على إعدادات المستخدم
                user_id = update.effective_user.id
                candle, trade_time, _, _ = get_user_setting(user_id)
                
                if not candle or not trade_time:
                    await wait_msg.edit_text("❌ يجب ضبط الإعدادات أولاً. الرجاء استخدام 'إعدادات التحليل'.")
                    
                    reply_keyboard = [[key] for key in CATEGORIES.keys()]
                    reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
                    
                    await update.message.reply_text(
                        "🔽 **اختر قسم آخر أو العودة للقائمة الرئيسية:**",
                        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
                    )
                    return RECOMMENDATION_MODE
                
                # تحليل الصورة
                analysis_result = await analyze_chart_image(
                    temp_update, 
                    context, 
                    chart_image_path, 
                    candle, 
                    trade_time, 
                    symbol_to_analyze
                )
                
                await wait_msg.edit_text(
                    analysis_result,
                    parse_mode="Markdown"
                )
                
            except Exception as e:
                print(f"❌ خطأ في تحليل الصورة التلقائية: {e}")
                # التحليل النصي الاحتياطي
                analysis = get_mistral_analysis(symbol_to_analyze)
                
                final_msg = (
                    f"📈 **نتائج توصية {symbol_to_analyze}**\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"{analysis}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🤖 **Powered by - Obeida Trading**"
                )
                
                final_msg = clean_repeated_text(final_msg)
                await wait_msg.edit_text(final_msg, parse_mode="Markdown")
            
            # حذف الصورة المؤقتة
            try:
                os.remove(chart_image_path)
            except:
                pass
        else:
            # إذا فشل سحب الصورة، استخدم التحليل النصي
            analysis = get_mistral_analysis(symbol_to_analyze)
            
            final_msg = (
                f"📈 **نتائج توصية {symbol_to_analyze}**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{analysis}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🤖 **Powered by - Obeida Trading**"
            )
            
            final_msg = clean_repeated_text(final_msg)
            await wait_msg.edit_text(final_msg, parse_mode="Markdown")
        
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

async def analyze_chart_image(update, context, image_path, candle, trade_time, symbol):
    """تحليل صورة الشارت"""
    try:
        user_id = update.effective_user.id
        prev_context, prev_time = get_analysis_context(user_id)
        
        # ضغط الصورة
        compressed_path = compress_image(image_path)
        
        # استخدام الصورة المضغوطة للتحليل
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            return "❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة."
        
        # الحصول على معلومات السيولة والتوقيت
        session_name, session_time, session_vol = get_market_session()
        gaza_time = datetime.now(GAZA_TIMEZONE)
        current_hour = gaza_time.hour
        current_minute = gaza_time.minute
        current_second = gaza_time.second
        
        # حساب الثواني المتبقية لإغلاق الشمعة
        seconds_remaining = 60 - current_second
        if candle.startswith('M'):
            candle_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            seconds_remaining = (candle_minutes * 60) - ((current_minute % candle_minutes) * 60 + current_second)
        elif candle.startswith('H'):
            candle_hours = int(candle[1:]) if candle[1:].isdigit() else 1
            minutes_passed = gaza_time.hour % candle_hours * 60 + current_minute
            seconds_remaining = (candle_hours * 3600) - (minutes_passed * 60 + current_second)
        
        candle_closing_status = f"الوقت المتبقي لإغلاق الشمعة: {seconds_remaining} ثانية"
        if seconds_remaining < 10:
            candle_closing_status += " ⚠️ (الوقت حرج جداً - تجنب الدخول)"
        elif seconds_remaining < 30:
            candle_closing_status += " ⚠️ (الوقت قصير)"
        
        # تحديد أوقات الأخبار الخطيرة
        news_impact = "🟢 منخفض"
        news_warning = ""
        news_risk_multiplier = 1.0
        
        high_impact_hours = [
            (14, 30), (16, 0), (20, 0),
            (8, 0), (9, 0), (10, 0),
            (2, 30), (4, 0),
            (17, 30),
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
        
        # الفلتر الزمني (Kill Zones)
        kill_zone_status = ""
        if 10 <= current_hour < 13:
            kill_zone_status = "داخل منطقة القتل السعري (لندن 10-13 بتوقيت غزة)"
        elif 15 <= current_hour < 18:
            kill_zone_status = "داخل منطقة القتل السعري (نيويورك 15-18 بتوقيت غزة)"
        elif 0 <= current_hour < 9 or current_hour >= 22:
            kill_zone_status = "خارج منطقة القتل (جلسة آسيوية)"
        else:
            kill_zone_status = "خارج مناطق القتل الرئيسية"
        
        # معالجة "دقيقة الغدر"
        is_last_minute = 1 if current_minute in [29, 59, 14, 44] else 0
        last_minute_status = "🔥 حرجة - آخر دقيقة للإغلاق" if is_last_minute else "✅ عادية"
        
        # ربط معطيات الإعدادات
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
        
        # تحديد فريم التحقق الديناميكي
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
        
        # إعدادات ثابتة
        GENERATION_CONFIG = {
            "max_tokens": 910,
            "temperature": 0.0,
            "top_p": 1.0,
            "random_seed": 42
        }
        
        # تحضير سياق التحليل السابق
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
        
        # البرومبت الرئيسي المحدث مع جميع الإضافات الجديدة
        MAIN_PROMPT = f"""
أنت محلل فني خبير متكامل في SMC + ICT + WYCKOFF + VOLUME PROFILE + MARKET PSYCHOLOGY.
مهمتك تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية دقيقة بنظام متعدد الطبقات.

🎯 **قاعدة فلتر المسافة الذهبية:** أنت ملزم باستخراج السعر من المحور الأيمن (Y-axis) ومقارنته بأقرب رقم مستدير (.000). إذا كانت المسافة أقل من 0.00010، تُلغى جميع أوامر البيع/الشراء العكسية ويتم تفعيل نظام 'اللحاق بالمغناطيس السعري' - أي متابعة الاتجاه حتى لمس الرقم المستدير.

{previous_context_info}

🔥 **قانون الفتيلة القاتلة (The Wick Law):**
في الصورة الواحدة، الذيل (Wick) أهم من الجسم. أي ذيل طويل يخترق منطقة سيولة ثم يعود، يعتبر "أمر تنفيذ عكسي فوراً" مهما كان اتجاه الشموع السابقة.
✅ **قاعدة التطبيق:** إذا كان ذيل الشمعة يمثل أكثر من 60% من حجمها الإجمالي عند منطقة دعم/مقاومة، فقم بإلغاء تحليل الهيكل واعتمد على الانعكاس.

💰 **ميزة التصحيح السعري الرقمي (Price Action Calibration):**
الراديكالية في تحديد الأسعار هي المفتاح. ابحث عن "الأرقام المستديرة" (مثل 1.68000) داخل الصورة واربطها بالزخم.
✅ **القاعدة:** إذا كان السعر متجهاً لرقم مستدير بفتحات شموع واسعة، فمن الانتحار التداول عكسه.
📌 **التعديل الجديد:** "الرقم المستدير مغناطيس؛ لا تعطي إشارة ارتداد إلا بعد ملامسته بـ 3 نقاط على الأقل."

🚀 **دمج خوارزمية الزخم (Momentum vs Structure):**
• في الأسواق الحقيقية (Real Market)، الهيكل (Structure) هو الملك.
• في أسواق الـ OTC، الزخم (Momentum) هو الملك.
✅ **الشرط الإلزامي:** إذا وجدت 3 شموع متتالية بنفس اللون وبأجسام ممتلئة (>80%)، يُحظر البيع حتى لو وصل السعر لقمة تاريخية. الزخم في هذه الحالة أقوى من أي تحليل فني.

🎯 **كشف التلاعب بالسيولة (Liquidity Sweep Detection):**
بدلاً من البحث عن نماذج مثل "الرأس والكتفين"، ابحث عن "القمم المتساوية" (Equal Highs). صناع السوق يضعون أوامرهم فوقها.
✅ **المنطق العملي:** إذا رأيت قمتين متساويتين في الصورة، يجب أن تكون التوصية: "انتظر اختراق القمة ثم ادخل بيعاً مع العودة"، وليس البيع من القمة مباشرة.

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
• 15-60 دقائق: خط الصفر + دايفرجنس عند POI
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

📊 **التحليل الفني المتقدم لـ {symbol}:**
• الإطار الزمني: {candle} ({candle_category})
• استراتيجية التداول: {trading_strategy}
• جلسة السوق: {session_name} ({session_time})
• حالة السيولة: {session_vol}

🔍 **المطلوب تحليل (SMC + Wyckoff + Volume Profile):**
- رصد الـ Order Block النشط و الـ FVG غير المغطى.
- تحديد منطقة الفخ (Inducement) والسيولة المستهدفة (BSL/SSL).
- حساب قوة الاتجاه باستخدام (RSI Divergence) وحجم التداول.
- تطبيق قوانين الفتيلة والزخم والأرقام المستديرة.

🎯 **التنسيق المطلوب للإجابة:**

📊 **ملخص فحص {symbol}:**
- الهيكل: (صاعد/هابط/تجميع) 
- السيولة: (أقرب فخ + الهدف القادم)
- الفجوات: (أهم منطقة FVG نشطة)
- تطبيق قانون الفتيلة: [نعم/لا]
- رقم مستدير قريب: [السعر مع المسافة]

🎯 **خطة التنفيذ:**
- القرار: (شراء 🟢 / بيع 🔴 / احتفاظ 🟡) 
- القوة: (عالية/متوسطة/ضعيفة)
- الدخول: [السعر الدقيق] 
- الهدف (TP1/TP2): [مستويات السيولة]
- الوقف (SL): [خلف منطقة الحماية] 
- الزمن: [الوقت المتوقع بالدقائق]

⚠️ **المخاطرة:**
- الثقة: [%] 
- نقطة الإلغاء: [السعر الذي يفسد السيناريو]
- تطبيق قوانين جديدة: [الفجوات ✓ / الزخم ✓ / الأرقام ✓ / الفتيلة ✓]

💡 **ملاحظات التحليل:**
- {kill_zone_status}
- {last_minute_status}
- {candle_closing_status}
- تأثير الأخبار: {news_impact}
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        # التحليل الأولي
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
        
        # التدقيق والتحسين
        AUDIT_PROMPT = f"""
        أنت محلل فني خبير متخصص في التدقيق والتحسين. مهمتك مراجعة التحليل الأول لـ {symbol} وتطبيق القواعد الجديدة:
        
        1. **قانون الفتيلة القاتلة:** تحقق من الذيول الطوحة (>60%)
        2. **قانون الزخم الثلاثي:** 3 شموع متتالية = استمرار الاتجاه
        3. **قانون الأرقام المستديرة:** الرقم المستدير = مغناطيس
        4. **قانون الفجوات:** السعر يتحرك من فجوة إلى فجوة
        
        *التحليل الأولي:* {initial_analysis}
        
        📊 **المعطيات:**
        • الإطار: {candle} ({candle_category})
        • الاستراتيجية: {trading_strategy}
        • الجلسة: {session_name} ({session_time})
        • السيولة: {session_vol}
        • الأخبار: {news_impact} (×{news_risk_multiplier})
        
        🔍 **أمر التدقيق:**
        1. تحقق من كل سعر ومستوى مذكور في التحليل
        2. تأكد من تطبيق جميع القواعد الجديدة
        3. صحح أي أخطاء في الأسعار أو المستويات
        4. أضف ملاحظات عن تطبيق القوانين الجديدة
        
        🎯 **قدم تحسينك بالتنسيق التالي:**
        
        📊 **التحليل المحسن لـ {symbol}:**
        [هنا التحليل المحسن مع التطبيق الكامل للقوانين الجديدة]
        
        🔧 **التعديلات المطبقة:**
        - [ ] قانون الفتيلة: [تم/غير مطلوب]
        - [ ] قانون الزخم: [تم/غير مطلوب]
        - [ ] قانون الأرقام: [تم/غير مطلوب]
        - [ ] قانون الفجوات: [تم/غير مطلوب]
        
        ⚡ **الخلاصة النهائية:**
        [التوصية النهائية مع مستوى الثقة]
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
            print(f"Obeida Vision Warning (Model 2): {response_2.status_code}")
            audit_result = f"📋 **ملاحظة:** تعذر التدقيق - استخدام التحليل الأولي مباشرة\n\n{initial_analysis}"
        
        # تنظيف النصوص
        audit_result = clean_repeated_text(audit_result)
        
        # حفظ سياق التحليل
        save_analysis_context(user_id, audit_result)
        
        # إعداد النص النهائي
        time_display = format_trade_time_for_prompt(trade_time)
        
        full_result = (
            f"✅ **تم تحليل {symbol} بنجاح!**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{audit_result}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔧 **الإعدادات المستخدمة:**\n"
            f"• سرعة الشموع: {candle} ({candle_category})\n"
            f"• استراتيجية التداول: {time_display}\n"
            f"• فريم التحقق للكسر: {verification_timeframe}\n"
            f"• الوقت المتبقي للإغلاق: {seconds_remaining} ثانية\n"
            f"• جلسة السوق: {session_name} ({session_time})\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Powered by - Obeida Trading**"
        )
        
        # تنظيف النهائي
        full_result = clean_repeated_text(full_result)
        
        return full_result
        
    except requests.exceptions.Timeout:
        return "⏱️ تجاوز الوقت المحدد. حاول مرة أخرى."
    except Exception as e:
        print(f"❌ خطأ في تحليل الصورة: {traceback.format_exc()}")
        return f"❌ **حدث خطأ في تحليل الصورة:** {str(e)[:200]}\nيرجى المحاولة مرة أخرى."
    finally:
        # تنظيف الملفات المؤقتة
        for filepath in [image_path, compressed_path]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass

async def handle_recommendation_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور في وضع التوصية"""
    user_id = update.effective_user.id
    
    # الحصول على آخر عملة تم اختيارها
    last_symbol = get_last_recommendation_symbol(context)
    
    wait_msg = await update.message.reply_text(f"📊 جاري تحليل {last_symbol} من الصورة المرفقة...")
    
    try:
        # حفظ الصورة مؤقتاً
        photo = await update.message.photo[-1].get_file()
        timestamp = int(time.time())
        image_path = os.path.join(IMAGE_CACHE_DIR, f"recommendation_{user_id}_{timestamp}.jpg")
        await photo.download_to_drive(image_path)
        
        # الحصول على إعدادات المستخدم
        candle, trade_time, _, _ = get_user_setting(user_id)
        
        if not candle or not trade_time:
            await wait_msg.edit_text("❌ يجب ضبط الإعدادات أولاً. الرجاء استخدام 'إعدادات التحليل'.")
            return RECOMMENDATION_MODE
        
        # تحليل الصورة
        analysis_result = await analyze_chart_image(
            update, 
            context, 
            image_path, 
            candle, 
            trade_time, 
            last_symbol
        )
        
        await wait_msg.edit_text(analysis_result, parse_mode="Markdown")
                
    except Exception as e:
        print(f"❌ خطأ في تحليل صورة التوصية: {e}")
        await wait_msg.edit_text("❌ حدث خطأ في معالجة الصورة. يرجى المحاولة مرة أخرى.")
    
    # عرض خيارات المتابعة
    reply_keyboard = [[key] for key in CATEGORIES.keys()]
    reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    await update.message.reply_text(
        "🔽 **اختر قسم آخر أو العودة للقائمة الرئيسية:**",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
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
            "max_tokens": 2500,
            "temperature": 0.10,
            "top_p": 1.0,
            "random_seed": 42
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
            footer = "\n\n━━━━━------━━━━\n🤖 **Powered by - Obeida Trading ** 🤖"
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
    """معالجة الصور للتحليل الفني المتقدم"""
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
    
    timestamp = int(time.time())
    original_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}_original.jpg")
    compressed_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}_compressed.jpg")
    
    try:
        await photo.download_to_drive(original_path)
        compressed_path = compress_image(original_path)
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
            candle_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            seconds_remaining = (candle_minutes * 60) - ((current_minute % candle_minutes) * 60 + current_second)
        elif candle.startswith('H'):
            candle_hours = int(candle[1:]) if candle[1:].isdigit() else 1
            minutes_passed = gaza_time.hour % candle_hours * 60 + current_minute
            seconds_remaining = (candle_hours * 3600) - (minutes_passed * 60 + current_second)
        
        candle_closing_status = f"الوقت المتبقي لإغلاق الشمعة: {seconds_remaining} ثانية"
        if seconds_remaining < 10:
            candle_closing_status += " ⚠️ (الوقت حرج جداً - تجنب الدخول)"
        elif seconds_remaining < 30:
            candle_closing_status += " ⚠️ (الوقت قصير)"
        
        # نظام الدرع الأساسي (Fundamental Shield)
        news_impact = "🟢 منخفض"
        news_warning = ""
        news_risk_multiplier = 1.0
        
        high_impact_hours = [
            (14, 30), (16, 0), (20, 0),
            (8, 0), (9, 0), (10, 0),
            (2, 30), (4, 0),
            (17, 30),
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
        
        # الفلتر الزمني (Kill Zones)
        kill_zone_status = ""
        if 10 <= current_hour < 13:
            kill_zone_status = "داخل منطقة القتل السعري (لندن 10-13 بتوقيت غزة)"
        elif 15 <= current_hour < 18:
            kill_zone_status = "داخل منطقة القتل السعري (نيويورك 15-18 بتوقيت غزة)"
        elif 0 <= current_hour < 9 or current_hour >= 22:
            kill_zone_status = "خارج منطقة القتل (جلسة آسيوية)"
        else:
            kill_zone_status = "خارج مناطق القتل الرئيسية"
        
        # معالجة "دقيقة الغدر"
        is_last_minute = 1 if current_minute in [29, 59, 14, 44] else 0
        last_minute_status = "🔥 حرجة - آخر دقيقة للإغلاق" if is_last_minute else "✅ عادية"
        
        # ربط معطيات الإعدادات
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
        
        # تحديد فريم التحقق الديناميكي
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
        
        # إعدادات ثابتة
        GENERATION_CONFIG = {
            "max_tokens": 910,
            "temperature": 0.0,
            "top_p": 1.0,
            "random_seed": 42
        }
        
        # تحضير سياق التحليل السابق
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
        
        # البرومبت الرئيسي المحدث مع جميع الإضافات
        MAIN_PROMPT = f"""
أنت محلل فني خبير متكامل في SMC + ICT + WYCKOFF + VOLUME PROFILE + MARKET PSYCHOLOGY.
مهمتك تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية دقيقة بنظام متعدد الطبقات.

🎯 **قاعدة فلتر المسافة الذهبية:** أنت ملزم باستخراج السعر من المحور الأيمن (Y-axis) ومقارنته بأقرب رقم مستدير (.000). إذا كانت المسافة أقل من 0.00010، تُلغى جميع أوامر البيع/الشراء العكسية ويتم تفعيل نظام 'اللحاق بالمغناطيس السعري' - أي متابعة الاتجاه حتى لمس الرقم المستدير.

{previous_context_info}

🔥 **قانون الفتيلة القاتلة (The Wick Law):**
في الصورة الواحدة، الذيل (Wick) أهم من الجسم. أي ذيل طويل يخترق منطقة سيولة ثم يعود، يعتبر "أمر تنفيذ عكسي فوراً" مهما كان اتجاه الشموع السابقة.
✅ **قاعدة التطبيق:** إذا كان ذيل الشمعة يمثل أكثر من 60% من حجمها الإجمالي عند منطقة دعم/مقاومة، فقم بإلغاء تحليل الهيكل واعتمد على الانعكاس.

💰 **ميزة التصحيح السعري الرقمي (Price Action Calibration):**
الراديكالية في تحديد الأسعار هي المفتاح. ابحث عن "الأرقام المستديرة" (مثل 1.68000) داخل الصورة واربطها بالزخم.
✅ **القاعدة:** إذا كان السعر متجهاً لرقم مستدير بفتحات شموع واسعة، فمن الانتحار التداول عكسه.
📌 **التعديل الجديد:** "الرقم المستدير مغناطيس؛ لا تعطي إشارة ارتداد إلا بعد ملامسته بـ 3 نقاط على الأقل."

🚀 **دمج خوارزمية الزخم (Momentum vs Structure):**
• في الأسواق الحقيقية (Real Market)، الهيكل (Structure) هو الملك.
• في أسواق الـ OTC، الزخم (Momentum) هو الملك.
✅ **الشرط الإلزامي:** إذا وجدت 3 شموع متتالية بنفس اللون وبأجسام ممتلئة (>80%)، يُحظر البيع حتى لو وصل السعر لقمة تاريخية. الزخم في هذه الحالة أقوى من أي تحليل فني.

🎯 **كشف التلاعب بالسيولة (Liquidity Sweep Detection):**
بدلاً من البحث عن نماذج مثل "الرأس والكتفين"، ابحث عن "القمم المتساوية" (Equal Highs). صناع السوق يضعون أوامرهم فوقها.
✅ **المنطق العملي:** إذا رأيت قمتين متساويتين في الصورة، يجب أن تكون التوصية: "انتظر اختراق القمة ثم ادخل بيعاً مع العودة"، وليس البيع من القمة مباشرة.

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
1. منع الانعكاس المطلق: يُحظر تماماً إصدار إشارة (بيع) إذا كانت آخر 3 شموع خضراء ممتلئة بنسبة > 80%، حتى لو لمس السعر منطقة عرض. الاندفاع يغلب الهيكل في الـ OTC.
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

📌 **القواعد الجديدة المضافة:**

⚡ **قاعدة الفتيلة القاتلة:**
"إذا كان طول الذيل (Wick) يمثل أكثر من 60% من حجم الشمعة الكلي عند مستوى دعم أو مقاومة واضح، فهذا إشارة انعكاس قوية. تجاهل اتجاه الهيكل وادخل مع اتجاه الذيل."

💰 **قاعدة الأرقام المستديرة المحسنة:**
"السعر لا يرتد من الرقم المستدير (.000, .500) إلا بعد ملامسته بمسافة لا تقل عن 3 نقاط. قبل ذلك، يعتبر الرقم المستدير 'مغناطيس' يستهدف سحب السيولة."

🚀 **قانون الزخم الثلاثي:**
"3 شموع متتالية بنفس اللون وبأجسام ممتلئة (>80%) = قطار سريع لا تقف أمامه. محظور تماماً التداول عكسه حتى مع وجود مقاومة قوية."

🎯 **فلسفة الفجوات:**
"السعر في الـ OTC يتحرك من فجوة إلى فجوة قبل الارتداد. لا تعطي إشارة انعكاس رئيسية إلا بعد ملاحظة FVG غير مغطاة في الاتجاه المعاكس."

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
• 15-60 دقائق: خط الصفر + دايفرجنس عند POI
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

🎯 **النظام الزمني الذكي - حساب وقت الوصول:**

**🔧 إعدادات النظام الحالية:**
• إطار الشموع: {candle} (تم ضبطه من قبل المستخدم)
• استراتيجية التداول: {trading_strategy}
• جلسة السوق: {session_name} ({session_time})
• وقت التحليل الفعلي: {gaza_time.strftime('%H:%M:%S بتوقيت غزة')}

**📊 المرحلة 1 - استخراج البيانات من الصورة:**
1. ابحث عن ساعة المنصة في الشارت (عادة في الزاوية اليسرى/اليمنى السفلى)
2. سجل **الوقت الحالي** الذي تراه على الشارت: [ساعة:دقيقة:ثانية]
3. قدّر **المسافة البصرية** بين السعر الحالي والهدف (بعدد النقاط)
4. انظر إلى **آخر 3 شموع** واحسب متوسط حركتها (نقاط/شمعة)
5. **عدد الشموع المطلوبة = المسافة ÷ متوسط حركة الشمعة**

**📋 البيانات المطلوبة من الصورة:**
• **الوقت على الشارت:** [مثال: 14:25:30]
• **المسافة للهدف:** [عدد النقاط] نقطة
• **متوسط حركة الشموع:** [نقاط/شمعة]
• **الشموع المتوقعة:** [النتيجة] شمعة

**🧮 المرحلة 2 - الحساب الذكي (مع ربط الفريم):**
1. **فريم الشموع:** {candle}
2. **مدة كل شمعة:** {{
    'S5': '5 ثواني',
    'S10': '10 ثواني', 
    'S15': '15 ثواني',
    'S30': '30 ثانية',
    'M1': '1 دقيقة',
    'M2': '2 دقيقة',
    'M3': '3 دقائق',
    'M5': '5 دقائق',
    'M10': '10 دقائق',
    'M15': '15 دقيقة',
    'M30': '30 دقيقة',
    'H1': '1 ساعة',
    'H4': '4 ساعات',
    'D1': '1 يوم'
}}.get('{candle}', '{candle}')

3. **المعادلة:**
   وقت الوصول = الوقت من الشارت + (عدد الشموع × مدة الشمعة)

4. **تطبيق إعدادات المستخدم:**
   - الفريم: {candle}
   - الإستراتيجية: {trading_strategy}
   - حجم الصفقة: {position_sizing}

⏰ **مطلوب منك إضافة قسم "التوقيت الذكي" في تحليلك:**

**🕐 التحليل الزمني المتقدم:**
• الوقت على المنصة: [أدخل الوقت من الصورة]
• الفريم المستخدم: {candle}
• المسافة المقدرة: [X] نقطة
• سرعة الشموع: [Y] نقطة/شمعة
• الشموع المتوقعة: [Z] شمعة من نوع {candle}

**🎯 نتائج التوقيت:**
• وقت البداية (من الشارت): [الوقت]
• مدة الشمعة الواحدة: [تحويل {candle} إلى زمن]
• وقت الوصول المتوقع: **[ساعة:دقيقة:ثانية بضبط]**
• المدة الإجمالية: [تحويل إلى دقائق/ثواني]

**🚀 التوصية الزمنية العملية:**
"بناءً على سرعة الشموع في فريم **{candle}** واستراتيجية **{trading_strategy}**، السعر يحتاج **[عدد الشموع]** شمعة (≈[الزمن]) للوصول للهدف. التوقيت الأمثل للوصول: **[وقت الوصول]** - إبدأ العد التنازلي الآن!"

**💡 ملاحظة هامة:**
تأكد من أن الوقت الذي تستخرجه من الصورة يتوافق مع توقيت **{gaza_time.strftime('%H:%M:%S بتوقيت غزة')}**. إذا كان هناك فارق زمني، قم بضبط الحساب وفقاً لذلك.
   

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
• تطبيق قانون الفتيلة: [نعم/لا] - نسبة الذيل: [٪]
• رقم مستدير قريب: [السعر مع المسافة]
• حالة الزخم الثلاثي: [مطبق/غير مطبق]
• حالة الهيكل: (صاعد/هابط) + (مرحلة وايكوف الحالية) + (توافق 4/4 إطارات: نعم/لا)
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• الفجوات السعرية: (المناطق التي سيعود السعر لتغطيتها)
• ذاكرة السياق: (ملاحظات من التحليل السابق إذا وجدت)

🎯 الإشارة التنفيذية:
• السعر الحالي: [السعر الدقيق من الشارت - مستخرج من المحور اليمني]
• حالة الشمعة: [مفتوحة / مغلقة] - الوقت المتبقي: [{seconds_remaining} ثانية]
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
• تطبيق قوانين جديدة: [الفجوات ✓ / الزخم ✓ / الأرقام ✓ / الفتيلة ✓]

💡 تعليمات نهائية:
"الأولوية القصوى: في حالة التعارض بين ذيول الشموع وقوة الاندفاع (Momentum)، تغلُب قوة الاندفاع في سوق الـ OTC، ويُمنع توقع القمم والقيعان (Top/Bottom Fishing). عند الاقتراب من رقم مستدير، تتحول الأولوية إلى 'تتبع الزخم حتى لمس الرقم' قبل التفكير في أي انعكاس."

الآن قم بتحليل الشارت المرفق وأعطني الإجابة بالتنسيق المطلوب أعلاه فقط، بدون أي نص إضافي أو تفسيرات خارج الهيكل.
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        # --- الخطوة 1: التحليل الأولي الأساسي ---
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
        
        # --- الخطوة 2: التدقيق والتحسين الثاني ---
        await wait_msg.edit_text("📊 جاري تدقيق التحليل (المرحلة 2/2)...")
        
        # برومبت التدقيق المحدث
        AUDIT_PROMPT = f"""
        وظيفتك الأساسية هي البحث عن تناقض بين الأسعار المذكورة في التحليل الأول وبين الأرقام الظاهرة في الصورة. إذا وجد التحليل الأول سعراً مختلفاً بينما الصورة تظهر السعر عند مختلف، قم بتصحيح كافة الأهداف بناءً على أرقام الصورة حصراً.
        
        5. قاعدة التكذيب: إذا ذكر التحليل الأول أن السعر عند (X) ولكنك ترى بوضوح بالعين أن الشمعة تلامس خطاً مختلفاً على المحور Y، اضرب بالتحليل الأول عرض الحائط واعتمد إحداثيات الصورة فقط.
        
        أنت محلل فني خبير في SMC + ICT + Wyckoff + Volume Profile + Market Psychology. مهمتك: تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية متعددة الطبقات.

*التحليل الأولي:* {initial_analysis}

📊 **القوانين الجديدة الإلزامية:**
1. **قانون الفتيلة:** ذيل >60% = انعكاس قوي
2. **قانون الزخم:** 3 شموع متتالية = استمرار الاتجاه
3. **قانون الأرقام:** رقم مستدير = مغناطيس
4. **قانون الفجوات:** سعر → فجوة → فجوة → ارتداد

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

🎯 **النظام الزمني الذكي - حساب وقت الوصول:**

**🔧 إعدادات النظام الحالية:**
• إطار الشموع: {candle} (تم ضبطه من قبل المستخدم)
• استراتيجية التداول: {trading_strategy}
• جلسة السوق: {session_name} ({session_time})
• وقت التحليل الفعلي: {gaza_time.strftime('%H:%M:%S بتوقيت غزة')}

**📊 المرحلة 1 - استخراج البيانات من الصورة:**
1. ابحث عن ساعة المنصة في الشارت (عادة في الزاوية اليسرى/اليمنى السفلى)
2. سجل **الوقت الحالي** الذي تراه على الشارت: [ساعة:دقيقة:ثانية]
3. قدّر **المسافة البصرية** بين السعر الحالي والهدف (بعدد النقاط)
4. انظر إلى **آخر 3 شموع** واحسب متوسط حركتها (نقاط/شمعة)
5. **عدد الشموع المطلوبة = المسافة ÷ متوسط حركة الشمعة**

**📋 البيانات المطلوبة من الصورة:**
• **الوقت على الشارت:** [مثال: 14:25:30]
• **المسافة للهدف:** [عدد النقاط] نقطة
• **متوسط حركة الشموع:** [نقاط/شمعة]
• **الشموع المتوقعة:** [النتيجة] شمعة

**🧮 المرحلة 2 - الحساب الذكي (مع ربط الفريم):**
1. **فريم الشموع:** {candle}
2. **مدة كل شمعة:** {{
    'S5': '5 ثواني',
    'S10': '10 ثواني', 
    'S15': '15 ثواني',
    'S30': '30 ثانية',
    'M1': '1 دقيقة',
    'M2': '2 دقيقة',
    'M3': '3 دقائق',
    'M5': '5 دقائق',
    'M10': '10 دقائق',
    'M15': '15 دقيقة',
    'M30': '30 دقيقة',
    'H1': '1 ساعة',
    'H4': '4 ساعات',
    'D1': '1 يوم'
}}.get('{candle}', '{candle}')

3. **المعادلة:**
   وقت الوصول = الوقت من الشارت + (عدد الشموع × مدة الشمعة)

4. **تطبيق إعدادات المستخدم:**
   - الفريم: {candle}
   - الإستراتيجية: {trading_strategy}
   - حجم الصفقة: {position_sizing}

⏰ **مطلوب منك إضافة قسم "التوقيت الذكي" في تحليلك:**

**🕐 التحليل الزمني المتقدم:**
• الوقت على المنصة: [أدخل الوقت من الصورة]
• الفريم المستخدم: {candle}
• المسافة المقدرة: [X] نقطة
• سرعة الشموع: [Y] نقطة/شمعة
• الشموع المتوقعة: [Z] شمعة من نوع {candle}

**🎯 نتائج التوقيت:**
• وقت البداية (من الشارت): [الوقت]
• مدة الشمعة الواحدة: [تحويل {candle} إلى زمن]
• وقت الوصول المتوقع: **[ساعة:دقيقة:ثانية بضبط]**
• المدة الإجمالية: [تحويل إلى دقائق/ثواني]

**🚀 التوصية الزمنية العملية:**
"بناءً على سرعة الشموع في فريم **{candle}** واستراتيجية **{trading_strategy}**، السعر يحتاج **[عدد الشموع]** شمعة (≈[الزمن]) للوصول للهدف. التوقيت الأمثل للوصول: **[وقت الوصول]** - إبدأ العد التنازلي الآن!"

**💡 ملاحظة هامة:**
تأكد من أن الوقت الذي تستخرجه من الصورة يتوافق مع توقيت **{gaza_time.strftime('%H:%M:%S بتوقيت غزة')}**. إذا كان هناك فارق زمني، قم بضبط الحساب وفقاً لذلك.
   

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
   
*تذكر:* يجب أن يكون تدقيقك موضوعياً ويعتمد على الصورة فقط. لا تخترع أسعاراً أو مستويات غير موجودة.

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

📊 التحليل الفني المتقدم:
• البصمة الزمنية: {kill_zone_status}
• تطبيق قانون الفتيلة: [نعم/لا] - نسبة الذيل: [٪]
• رقم مستدير قريب: [السعر مع المسافة]
• حالة الزخم الثلاثي: [مطبق/غير مطبق]
• حالة الهيكل: (صاعد/هابط) + (مرحلة وايكوف الحالية) + (توافق 4/4 إطارات: نعم/لا)
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• الفجوات السعرية: (المناطق التي سيعود السعر لتغطيتها)
• ذاكرة السياق: (ملاحظات من التحليل السابق إذا وجدت)

🎯 الإشارة التنفيذية:
• مقارنة مع التحليل السابق: [✅ مطابق تماماً / ⚡ محسّن / ❌ مصحح]، درجة التشابه: [0–100]%
• السعر الحالي: [ السعر الدقيق من الشارت ]
• حالة الشمعة: [مفتوحة / مغلقة] - الوقت المتبقي: [{seconds_remaining} ثانية]
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
• تطبيق قوانين جديدة: [الفجوات ✓ / الزخم ✓ / الأرقام ✓ / الفتيلة ✓]
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
            "max_tokens": 950,
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
        
        # تنظيف النصوص من التكرار
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
            f"{audit_result}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔧 **الإعدادات المستخدمة:**\n"
            f"• سرعة الشموع: {candle} ({candle_category})\n"
            f"• استراتيجية التداول: {time_display}\n"
            f"• فريم التحقق للكسر: {verification_timeframe}\n"
            f"• الوقت المتبقي للإغلاق: {seconds_remaining} ثانية\n"
            f"• جلسة السوق: {session_name} ({session_time})\n"
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

# --- حارس الأخطاء (Error Handler) ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة الأخطاء في البوت"""
    try:
        error_msg = f"❌ حدث خطأ في البوت:\n"
        
        if update and hasattr(update, 'effective_user'):
            error_msg += f"المستخدم: {update.effective_user.id}\n"
        
        error_msg += f"الخطأ: {context.error}\n"
        
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = ''.join(tb_list)
        
        with open("bot_errors.log", "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"الخطأ: {error_msg}\n")
            f.write(f"Traceback:\n{tb_string}\n")
            f.write(f"{'='*60}\n")
        
        print(f"❌ خطأ مسجل: {error_msg}")
        
        if update and hasattr(update, 'effective_chat'):
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⚠️ حدث خطأ تقني. النظام يعمل على إصلاحه تلقائياً. يرجى المحاولة مرة أخرى."
                )
            except:
                pass
        
        if isinstance(context.error, (NetworkError, TimedOut, ConnectionError)):
            print("🌐 خطأ في الشبكة، محاولة الاستمرار...")
            
    except Exception as e:
        print(f"❌ خطأ في معالج الأخطاء نفسه: {e}")

# --- وظيفة تنظيف دورية للملفات المؤقتة ---
async def periodic_cleanup():
    """تنظيف دوري للملفات المؤقتة"""
    while True:
        try:
            await asyncio.sleep(1800)
            cleanup_old_images()
            cleanup_old_database_records()
            print("🧹 تم التنظيف الدوري للملفات المؤقتة")
            
        except Exception as e:
            print(f"⚠️ خطأ في التنظيف الدوري: {e}")

# --- الدوال الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    if update.effective_user:
        cleanup_user_data(context, update.effective_user.id)
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["💬 دردشة", "📈 توصية"]
    ]
    
    await update.message.reply_text(
        "🚀 **أهلاً بك في Obeida Trading**\n\n"
        "🤖 **المميزات الجديدة:**\n"
        "• تحليل فني متقدم للشارتات\n"
        "• 🆕 دردشة ذكية متعددة التخصصات\n"
        "• 📈 نظام توصيات مع سحب الصور التلقائي\n"
        "• إعدادات تخصيص كاملة\n"
        "• تطبيق قوانين التداول جميعها\n\n"
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
                f"📡 **نظام التحليل:** \n"
                f"1. التحليل الأولي مع القوانين الجديدة\n"
                f"2. التدقيق النهائي\n\n"
                f"📋 **القوانين المطبقة:**\n"
                f"• قانون الفتيلة القاتلة\n"
                f"• قانون الزخم الثلاثي\n"
                f"• قانون الأرقام المستديرة\n"
                f"• قانون الفجوات\n\n"
                f"أرسل صورة الرسم البياني (الشارت) الآن:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return ANALYZE_MODE
    
    elif user_message == "💬 دردشة":
        return await start_chat_mode(update, context)
    
    elif user_message == "📈 توصية":
        return await start_recommendation_mode(update, context)
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["💬 دردشة", "📈 توصية"]
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
            ["💬 دردشة", "📈 توصية"]
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
            ["💬 دردشة", "📈 توصية"]
        ]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in TRADE_TIMES:
        save_user_setting(user_id, "trade_time", user_message)
        
        keyboard = [["📊 تحليل صورة"], ["💬 دردشة"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        
        candle, _, _, _ = get_user_setting(user_id)
        
        await update.message.reply_text(
            f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ سرعة الشموع: {candle}\n"
            f"✅ مدة الصفقة: {user_message}\n\n"
            f"📡 **نظام التحليل الجديد:** \n"
            f"• التحليل الأولي = ✔️\n"
            f"• التدقيق النهائي = ✔️\n"
            f"⬇️⬇️ يمكنك الآن تحليل صورة ⬇️⬇️:",
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
            ["💬 دردشة", "📈 توصية"]
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
    3. اختر "دردشة" للاستفسارات النصية
    4. اختر "توصية" لتحليل العملات مع سحب الصور التلقائي
    
    📈 **نظام التوصيات الجديد:**
    • تحليل فني للعملات والمؤشرات
    • سحب تلقائي للصور من TradingView
    • تطبيق القوانين الجديدة
    • تحليل مزدوج (نصي + بصري)
    
    ⏱️ **خيارات مدة الصفقة:**
    • **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة
    • **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة
    • **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة
    
    📡 **مميزات البوت المتقدمة:**
    • تحليل فني للرسوم البيانية بتقنيات متطورة
    • دردشة ذكية متعددة التخصصات
    • نظام توصيات مع سحب الصور التلقائي
    • حفظ إعداداتك الشخصية
    • واجهة سهلة بالأزرار
    • نظام تنظيف تلقائي للذاكرة
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_recommendation_selection),
                MessageHandler(filters.PHOTO, handle_recommendation_photo)
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
    
    # إضافة معالج للأخطاء
    application.add_error_handler(error_handler)
    
    print("✅ Telegram Bot initialized successfully")
    print("📡 Bot is now polling for updates...")
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
    
