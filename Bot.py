import logging
import base64
import os
import sqlite3
import re
import requests
import threading
import time
import sys
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from flask import Flask

# --- الإعدادات ---
TOKEN = os.environ.get('TOKEN', "7324911542:AAGcVkwzjtf3wDB3u7cprOLVyoMLA5JCm8U")
GROQ_KEY = os.environ.get('GROQ_KEY', "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")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-70b-versatile"
DB_NAME = "abood-gpt.db"

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

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
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION = range(7)

# --- Flask Server ---
app = Flask(__name__)

@app.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Obeida Trading Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                text-align: center; 
                padding: 40px; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                min-height: 100vh;
            }
            .container {
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 40px;
                max-width: 800px;
                margin: 0 auto;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                border: 1px solid rgba(255,255,255,0.2);
            }
            h1 { 
                color: white; 
                font-size: 2.8em;
                margin-bottom: 20px;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
            }
            .status { 
                background: linear-gradient(45deg, #00b09b, #96c93d);
                color: white; 
                padding: 15px 30px; 
                border-radius: 50px; 
                display: inline-block;
                font-weight: bold;
                font-size: 1.2em;
                margin: 20px 0;
                box-shadow: 0 5px 15px rgba(0,0,0,0.2);
            }
            .features {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin: 40px 0;
                text-align: left;
            }
            .feature-item {
                background: rgba(255,255,255,0.15);
                padding: 20px;
                border-radius: 15px;
                border: 1px solid rgba(255,255,255,0.1);
            }
            .stats {
                display: flex;
                justify-content: center;
                gap: 30px;
                margin-top: 40px;
                flex-wrap: wrap;
            }
            .stat-item {
                background: rgba(255,255,255,0.1);
                padding: 20px;
                border-radius: 15px;
                min-width: 150px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Obeida Trading Telegram Bot</h1>
            <p style="font-size: 1.3em; opacity: 0.9;">Advanced Trading Analysis & AI Assistant</p>
            
            <div class="status">✅ Bot Status: RUNNING</div>
            
            <div class="features">
                <div class="feature-item">
                    <h3>📊 تحليل فني متقدم</h3>
                    <p>تحليل الشارتات باستخدام الذكاء الاصطناعي</p>
                </div>
                <div class="feature-item">
                    <h3>🤖 مساعد ذكي</h3>
                    <p>دردشة متقدمة في جميع المجالات</p>
                </div>
                <div class="feature-item">
                    <h3>📈 توصيات تداول</h3>
                    <p>نظام توصيات للعملات والمؤشرات</p>
                </div>
            </div>
            
            <div style="margin: 40px 0;">
                <h3>📡 معلومات النظام</h3>
                <p><strong>آخر تحديث:</strong> """ + time.strftime("%Y-%m-%d %H:%M:%S") + """</p>
                <p><strong>إصدار النظام:</strong> 2.0.1</p>
                <p><strong>نموذج الذكاء الاصطناعي:</strong> """ + GROQ_MODEL + """</p>
            </div>
            
            <div class="stats">
                <div class="stat-item">
                    <h3>⚙️ API Status</h3>
                    <p style="color: #4CAF50;">● Active</p>
                </div>
                <div class="stat-item">
                    <h3>🕒 Uptime</h3>
                    <p>24/7</p>
                </div>
                <div class="stat-item">
                    <h3>🔐 Security</h3>
                    <p style="color: #4CAF50;">● Secure</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return {"status": "active", "timestamp": time.time(), "model": GROQ_MODEL, "version": "2.0.1"}

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
            trade_time TEXT DEFAULT 'متوسط (4h-Daily)',
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
    return ("M5", "متوسط (4h-Daily)")

def get_market_session():
    """الحصول على معلومات جلسة السوق الحالية"""
    current_hour = datetime.utcnow().hour
    
    if 0 <= current_hour < 6:
        return "الجلسة الآسيوية", "00:00-06:00 GMT", "منخفضة"
    elif 6 <= current_hour < 12:
        return "جلسة لندن/أوروبا", "06:00-12:00 GMT", "مرتفعة"
    elif 12 <= current_hour < 18:
        return "جلسة نيويورك", "12:00-18:00 GMT", "عالية جداً"
    elif 18 <= current_hour < 24:
        return "جلسة المحيط الهادئ", "18:00-24:00 GMT", "منخفضة"
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

# --- معالجة الصور ---
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
    
    # إزالة التكرارات الشائعة
    patterns_to_clean = [
        (r'(📊\s*\*\*نتائج الفحص الفني\*\*:[\s\S]*?)(?=📊\s*\*\*نتائج الفحص الفني\*\*:)', ''),
        (r'(###\s*تحليل الشارت المرفق[\s\S]*?)(?=###\s*تحليل الشارت المرفق)', ''),
        (r'(🎯\s*\*\*التوصية والتوقعات\*\*:[\s\S]*?)(?=🎯\s*\*\*التوصية والتوقعات\*\*:)', ''),
        (r'(⚠️\s*\*\*إدارة المخاطر\*\*:[\s\S]*?)(?=⚠️\s*\*\*إدارة المخاطر\*\*:)', ''),
    ]
    
    for pattern, replacement in patterns_to_clean:
        text = re.sub(pattern, replacement, text, flags=re.DOTALL)
    
    # تقسيم النص إلى فقرات وإزالة التكرار
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    seen_paragraphs = set()
    unique_paragraphs = []
    
    for paragraph in paragraphs:
        # إنشاء مفتاح فريد للفقرات المتشابهة
        if len(paragraph) > 20:
            key = paragraph[:100].strip().lower()
            if key not in seen_paragraphs:
                unique_paragraphs.append(paragraph)
                seen_paragraphs.add(key)
        else:
            unique_paragraphs.append(paragraph)
    
    cleaned_text = '\n\n'.join(unique_paragraphs)
    
    # تقصير النص إذا كان طويلاً جداً
    if len(cleaned_text) > 3000:
        if '\n\n' in cleaned_text[:2800]:
            cut_point = cleaned_text[:2800].rfind('\n\n')
            cleaned_text = cleaned_text[:cut_point] + "\n\n📋 ...تم اختصار النتيجة للحفاظ على الوضوح"
        else:
            cleaned_text = cleaned_text[:2800] + "...\n\n📋 تم اختصار النتيجة"
    
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

def safe_api_call(url, headers, json_data, timeout=30):
    """استدعاء API آمن مع معالجة الأخطاء"""
    try:
        response = requests.post(url, headers=headers, json=json_data, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error: {e}")
        if hasattr(e.response, 'status_code'):
            if e.response.status_code == 401:
                return {"error": "❌ **خطأ في التوثيق**: يرجى التحقق من مفتاح API"}
            elif e.response.status_code == 429:
                return {"error": "❌ **تم تجاوز الحد المسموح**: يرجى الانتظار قليلاً"}
        return {"error": f"❌ **خطأ في الخادم**: {e}"}
    except requests.exceptions.Timeout:
        print("Request Timeout")
        return {"error": "⏱️ **تجاوز الوقت المحدد**: يرجى المحاولة مرة أخرى"}
    except Exception as e:
        print(f"General Error: {e}")
        return {"error": f"⚠️ **حدث خطأ**: {str(e)}"}

# --- وظائف نظام التوصية الجديد ---
def get_groq_analysis(symbol):
    """الحصول على تحليل من Groq API للعملة"""
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    🔍 **تحليل تقني متقدم لعملة {symbol}** - بروتوكول متعدد الطبقات
    
    ⚠️ **الطبقة 1: فحص الجدوى المبدئي (Pre-Flight Check)**
    1. **التحقق من السياق الزمني**: هل نحن داخل Kill Zone أم خارجه؟
    2. **فحص تأثير الأخبار**: أي أخبار اقتصادية قادمة خلال 4 ساعات؟
    3. **تقييم السيولة الحالية**: حجم التداول الحالي مقارنة بالمتوسط
    
    📊 **الطبقة 2: التحليل الهيكلي المتقدم**
    1. **تحديد الاتجاه الرئيسي** على 3 أطر زمنية (D1, H4, H1)
    2. **رسم الهيكل السعري** (Market Structure)
    3. **تحديد Order Blocks النشطة** في الاتجاه الحالي
    4. **رصد Fair Value Gaps (FVG)** التي تحتاج للتغطية
    
    💰 **الطبقة 3: تحليل السيولة**
    1. **خريطة السيولة المتساوية** (Equal Highs/Lows)
    2. **مناطق Inducement** (الإغراء)
    3. **أهداف السحب المتوقعة** (Liquidity Targets)
    
    🎯 **الطبقة 4: نظام الدخول الذكي**
    **شرط التفعيل الإلزامي**: يجب توفر واحد مما يلي:
    - اختبار Order Block مع إغلاق شمعة تأكيد
    - سد فجوة سعرية مع زيادة حجم
    - كسر مستوى مع تأكيد RSI فوق/تحت 50
    
    ⚡ **معايير الدخول (يجب توفر 3/4):**
    1. **مواءمة الفركتلات**: تطابق الاتجاه في 3 أطر زمنية
    2. **موقع السعر**: في منطقة Discount للشراء أو Premium للبيع
    3. **نمط الشموع**: Pin Bar, Engulfing, أو Inside Bar قوي
    4. **تأكيد المؤشر**: RSI أو MACD يؤكد الاتجاه
    
    ⚠️ **إدارة المخاطر الإلزامية:**
    - **نسبة RR**: لا تقل عن 1:2
    - **نقطة الإلغاء**: السعر الذي يفسد التحليل
    - **أقصى مخاطرة**: 2% من رأس المال
    
    **📋 التنسيق المطلوب للرد:**
    
    📊 **التحليل الهيكلي:**
    - **الاتجاه الرئيسي**: [صاعد/هابط/جانبي]
    - **المرحلة الحالية**: [Accumulation/Redistribution/Markup/Markdown]
    - **الهيكل السعري**: [Higher Highs/Lower Highs/...]
    
    🎯 **الإشارة التنفيذية:**
    - **السعر الحالي**: [قراءة دقيقة من البيانات المتاحة]
    - **التوصية**: [شراء/بيع/انتظار]
    - **شرط التفعيل**: [الشرط الذي يجب تحققه قبل الدخول]
    - **نقطة الدخول**: [السعر المحدد مع شرط الإغلاق]
    - **الأهداف**: 
        TP1: [سعر + احتمالية]
        TP2: [سعر + احتمالية]
        TP3: [سعر + احتمالية]
    - **وقف الخسارة**: [سعر مع 3 طبقات حماية]
    
    🧠 **ركن "افهم سوقك":**
    - **فلسفة الصفقة**: [لماذا هذه النقطة بالذات؟]
    - **سيناريو التلاعب**: [ما الذي قد يخرب الصفقة؟]
    - **الدرس الفني**: [قاعدة مستخلصة من هذا التحليل]
    
    ⚠️ **نقطة الإلغاء (إلزامية):**
    - **السعر**: [السعر الذي يبطل التحليل]
    - **الزمن**: [مدة انتظار قصوى قبل الإلغاء]
    
    **ملاحظة**: لا تعطِ توصية إذا لم تتوفر الشروط بوضوح.
    """
    
    body = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 1500
    }

    result = safe_api_call(GROQ_URL, headers, body, timeout=30)
    
    if "error" in result:
        return result["error"]
    elif "choices" in result:
        return result['choices'][0]['message']['content'].strip()
    else:
        return "⚠️ حدث خطأ غير متوقع في الاتصال بالمحلل."

async def start_recommendation_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع التوصية"""
    reply_keyboard = [[key] for key in CATEGORIES.keys()]
    reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    await update.message.reply_text(
        "🚀 **نظام التوصيات المتقدم**\n\n"
        "اختر القسم المطلوب من الأزرار:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
    )
    return RECOMMENDATION_MODE

async def handle_recommendation_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات نظام التوصية"""
    user_text = update.message.text.strip()
    user_id = update.effective_user.id
    
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
            f"📍 **قسم:** {user_text}\n"
            f"📊 **عدد العملات:** {len(CATEGORIES[user_text])}\n\n"
            f"اختر العملة الآن:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return CATEGORY_SELECTION
    
    # التحقق من العملة المختارة
    symbol_to_analyze = None
    category_name = ""
    for category, symbols in CATEGORIES.items():
        if user_text in symbols:
            symbol_to_analyze = user_text
            category_name = category
            break
    
    # إذا وجدت العملة، ابدأ التحليل
    if symbol_to_analyze:
        wait_msg = await update.message.reply_text(
            f"⏳ **جاري تحليل {symbol_to_analyze}**\n"
            f"📊 **القسم:** {category_name}\n"
            f"⏰ **الوقت:** {datetime.now().strftime('%H:%M')}"
        )
        
        analysis = get_groq_analysis(symbol_to_analyze)
        
        # إذا كان هناك خطأ
        if analysis.startswith("❌") or analysis.startswith("⚠️") or analysis.startswith("⏱️"):
            await wait_msg.edit_text(
                analysis + "\n\n🔙 **العودة لنظام التوصيات**",
                parse_mode="Markdown"
            )
        else:
            final_msg = (
                f"📈 **تقرير تحليل {symbol_to_analyze}**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{analysis}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 **ملخص التنفيذ:**\n"
                f"• وقت التحليل: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                f"• مدة الصلاحية: 4-6 ساعات\n"
                f"• مصدر التحليل: Obeida Trading AI\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ **تحذير المخاطر:**\n"
                f"التداول في الأسواق المالية يحمل مخاطر. هذه ليست نصيحة استثمارية."
            )
            
            # تنظيف النص من التكرارات
            final_msg = clean_repeated_text(final_msg)
            
            # تقسيم النص إذا كان طويلاً
            if len(final_msg) > 4000:
                parts = split_message(final_msg, max_length=4000)
                await wait_msg.edit_text(
                    parts[0],
                    parse_mode="Markdown"
                )
                for part in parts[1:]:
                    await update.message.reply_text(part, parse_mode="Markdown")
            else:
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
        text="🚀 **وضع الدردشة المتقدم - Obeida Trading**\n\n"
             "🤖 **أنا مساعدك الذكي متعدد المواهب:**\n"
             "• 📊 مستشار استثماري وتحليلات مالية\n"
             "• 💻 خبير برمجي وتقني\n"
             "• 📈 محلل بيانات واستراتيجيات\n"
             "• ✍️ كاتب محتوى إبداعي\n"
             "• 🧠 مساعد شخصي ذكي\n\n"
             "**اختر مجال المساعدة أو أرسل سؤالك مباشرة:**",
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
    wait_msg = await update.message.reply_text("🤔 Obeida Trading يفكر...")
    
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
        
        result = safe_api_call(GROQ_URL, headers, payload, timeout=60)
        
        if "error" in result:
            await wait_msg.edit_text(result["error"])
        elif "choices" in result:
            ai_response = result['choices'][0]['message']['content']
            
            # تنظيف النص من التكرارات
            ai_response = clean_repeated_text(ai_response)
            
            # إضافة تذييل مميز
            footer = f"\n\n━━━━━━━━━━━━━━━━━━\n🤖 **Obeida Trading** - المساعد الذكي • {datetime.now().strftime('%H:%M')}"
            ai_response = ai_response + footer
            
            # أزرار الدردشة المتقدمة
            chat_keyboard = [
                ["🚀 مساعد شامل", "💼 استشارات احترافية"],
                ["📈 تحليل استثماري", "👨‍💻 دعم برمجي"],
                ["📝 كتابة إبداعية", "🧠 حلول ذكية"],
                ["ايقاف الدردشة", "الرجوع للقائمة الرئيسية"]
            ]
            
            # تقسيم الرسالة الطويلة
            if len(ai_response) > 4000:
                parts = split_message(ai_response, max_length=4000)
                for i, part in enumerate(parts):
                    if i == 0:
                        await wait_msg.edit_text(
                            f"💬 **Obeida Trading يرد:**\n\n{part}",
                            parse_mode="Markdown"
                        )
                    else:
                        await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(
                    f"💬 **Obeida Trading يرد:**\n\n{ai_response}",
                    parse_mode="Markdown"
                )
            
            # إرسال الأزرار بعد الرد
            await update.message.reply_text(
                "🔽 **اختر مجالاً آخر أو اطرح سؤالاً جديداً:**",
                reply_markup=ReplyKeyboardMarkup(chat_keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
        else:
            await wait_msg.edit_text("❌ **حدث خطأ غير متوقع في الاستجابة**")
    
    except Exception as e:
        print(f"خطأ في الدردشة: {e}")
        await wait_msg.edit_text("❌ حدث خطأ غير متوقع. النظام يعمل على الإصلاح تلقائياً...")
    
    return CHAT_MODE

# --- كود تحليل الصور المحسن والمدمج الكامل ---
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
                news_warning = f"⚠️ **تحذير:** خبر اقتصادي قوي خلال ±60 دقيقة"
                break
            elif time_diff <= 120:  # خلال ساعتين من الخبر
                news_impact = "🟡 متوسط"
                news_risk_multiplier = 1.5
                news_warning = f"📢 **تنبيه:** اقتراب من وقت أخبار مهمة"
                break
        
        # ========== الفلتر الزمني (Kill Zones) ==========
        kill_zone_status = ""
        if 8 <= current_hour < 11:  # London Kill Zone
            kill_zone_status = "داخل منطقة القتل السعري (لندن 8-11 GMT)"
        elif 13 <= current_hour < 16:  # New York Kill Zone
            kill_zone_status = "داخل منطقة القتل السعري (نيويورك 13-16 GMT)"
        elif 22 <= current_hour or current_hour < 7:  # Asian Session
            kill_zone_status = "خارج منطقة القتل (جلسة آسيوية)"
        else:
            kill_zone_status = "خارج مناطق القتل الرئيسية"
        
        # البرومبت الكامل مع جميع التحسينات
        prompt = f"""[SYSTEM: ULTIMATE_MARKET_ANALYZER_PRO_V10]
أنت محلل فني خبير في مدرسة Smart Money Concepts (SMC). مهمتك هي تحليل الشارت المرفق وتقديم التوصيات وفقاً للتنسيق المحدد.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔰 **القواعد الأساسية الحاكمة**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. **المدرسة المعتمدة:** SMC (Smart Money Concepts) كإطار عمل رئيسي
2. **الدرع الأساسي (Fundamental Shield):** {news_warning if news_warning else "✅ الوضع آمن من الأخبار"}
3. **شرط التفعيل:** لا تعطِ توصية دخول بدون شرط تفعيل واضح
4. **استخراج البيانات:** قراءة دقيقة للمحاور السعرية أولاً
5. **فلتر الجدوى:** نسبة RR ≥ 1:2 مع تعديل الأخبار

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **المرحلة 1: استخراج البيانات الرقمية (إلزامي)**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 1.1 قراءة المحاور بدقة:
**🔍 اتبع هذه الخطوات بالترتيب:**
1. **مسح المحور السعري اليميني** واستخراج الأرقام
2. **تحديد السعر الحالي** من آخر شمعة مكتملة
3. **استخراج أعلى سعر (High)** وأقل سعر (Low) من آخر 5 شموع
4. **التحقق من الدقة** بمقارنة موقع الشموع مع الأرقام

### 1.2 تسجيل النتائج:
- **السعر الحالي الدقيق:** [_____]
- **أعلى سعر قريب:** [_____]
- **أقل سعر قريب:** [_____]
- **النطاق السعري:** [_____]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 **المرحلة 2: التحليل الهيكلي المتقدم**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 2.1 تحديد مدرسة التحليل:
- **الإطار:** SMC مع دعم بالتحليل الكلاسيكي عند الحاجة
- **التنسيق:** استخدام مصطلحات SMC بدقة (Order Blocks, FVG, Liquidity)
- **الهيكل:** تحديد BOS (Break of Structure) و CHoCH (Change of Character)

### 2.2 مصفاة التسعير (PD Array):
- **نطاق التعامل:** تحديد القمة والقاع الرئيسيين
- **خط التوازن (50%):** حساب النقطة الوسطى
- **منطقة الخصم (Discount):** تحت 50% - مثالي للشراء
- **منطقة الغلاء (Premium):** فوق 50% - مثالي للبيع
- **فلتر التسعير:** لا شراء إلا في Discount، لا بيع إلا في Premium

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 **المرحلة 3: تحليل السيولة والزخم**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 3.1 كشف وهم الزخم (Momentum Illusion):
**علامات الزخم الوهمي:**
1. **شمعة الخبر المنفردة:** كبيرة ومعزولة عن السياق
2. **فجوات سعرية:** قبل أو بعد الشمعة الكبيرة
3. **غياب المتابعة:** حركة قوية بدون استمرارية
4. **الذيول الطويلة جداً:** إشارة ضعف في الاختبار

### 3.2 اختبار الزخم الحقيقي:
- 3 شموع متتالية في نفس الاتجاه
- تدرج في حجم الأجسام
- توافق مع الهيكل العام
- زيادة في أحجام التداول

### 3.3 خرائط السيولة المتقدمة:
- **السيولة المتساوية:** Equal Highs/Lows
- **فخاخ الإغراء:** مناطق Inducement
- **سحب السيولة:** Liquidity Sweeps
- **الفراغات السعرية:** FVG مفتوحة

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **المرحلة 4: نظام القرار الذكي**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 4.1 شرط التفعيل الإلزامي (يجب توفر واحد):
1. **اختبار Order Block** مع إغلاق شمعة تأكيد
2. **سد فجوة سعرية (FVG)** مع زيادة حجم التداول
3. **كسر مستوى سيولة** مع تأكيد RSI فوق/تحت 50

### 4.2 فلتر التلاقي الثلاثي (يجب توفر 3/3):
1. **POI (منطقة الاهتمام):** Order Block أو FVG صالح
2. **نموذج الشموع:** Pin Bar، Engulfing، Inside Bar
3. **تأكيد إضافي:** حجم، مؤشر، أو سياق زمني

### 4.3 تعديل المخاطر حسب الأخبار:
**معامل التعديل:** ×{news_risk_multiplier}
- **Stop Loss المعدل:** = SL العادي × {news_risk_multiplier}
- **الحجم المعدل:** = الحجم العادي ÷ {news_risk_multiplier}
- **نسبة RR المطلوبة:** ≥ 1:{max(3, 2 * news_risk_multiplier)}

### 4.4 شروط الحظر الكامل (ممنوع الدخول إذا):
1. خبر عالي التأثير ±30 دقيقة
2. زخم وهمي واضح (شمعة كبيرة معزولة)
3. عدم استيفاء شرط التفعيل
4. موقع السعر في منطقة Equilibrium

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **المعطيات الفنية:**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- **إطار الزمن:** {candle}
- **جلسة السوق:** {session_name} ({session_time})
- **حالة السيولة:** {session_vol}
- **تأثير الأخبار:** {news_impact} (معامل ×{news_risk_multiplier})
- **التوقيت:** {current_time.strftime('%Y-%m-%d %H:%M GMT')}
- **البصمة الزمنية:** {kill_zone_status}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **التنسيق المطلوب للإجابة (يجب الالتزام حرفياً):**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 **التحليل الفني:**
- **استخراج البيانات:** [السعر الحالي، أعلى سعر، أقل سعر]
- **حالة الهيكل:** (صاعد/هابط/جانبي) + (مرحلة وايكوف)
- **خريطة السيولة:** (أقرب فخ سيولة Inducement)
- **الفجوات السعرية (FVG):** (المناطق التي سيعود السعر لتغطيتها)

🎯 **الإشارة التنفيذية:**
- **السعر الحالي:** [السعر الدقيق المستخرج من المحور]
- **القرار الفني:** (شراء 🟢 / بيع 🔴 / الإحتفاظ 🟡 / انتظار)
- **شرط التفعيل:** [الشرط الواضح الذي يجب تحققه قبل الدخول]
- **قوة الإشارة 🔰:** (عالية جدا 💥 / 🔥 عالية / ⚡ متوسطة / ❄️ ضعيفة)
- **نقطة الدخول:** [السعر مع شرط الإغلاق]
- **الأهداف الربحية:**
  - 🎯 **TP1:** [سحب أول سيولة داخلية]
  - 🎯 **TP2:** [الهدف الرئيسي - منطقة عرض/طلب قوية]
  - 🎯 **TP3:** [استهداف السيولة الخارجية]
- **وقف الخسارة:** [السعر مع 3 طبقات حماية]
- **المدة المتوقعة 🕧:** [عدد الدقائق للوصول للهدف TP1]

🧠 **ركن "افهم سوقك":**
- **فلسفة الدخول:** [لماذا هذه النقطة بالذات؟]
- **كاشف التلاعب:** [إشارة تغيير اتجاه صناع السوق]
- **درس الساعة:** [قاعدة فنية مستخلصة]

⚠️ **سيناريو الطوارئ:**
- **اخرج فوراً إذا:** [سلوك سعري معين]

⚠️ **إدارة المخاطر:**
- **مستوى الثقة:** [% مع ذكر عدد التاكيدات]
- **نقطة الإلغاء:** [السعر الذي يفسد التحليل]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔬 **التعليمات النهائية:**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. **ابدأ باستخراج البيانات** من المحاور أولاً
2. **لا تعطِ توصية دخول** بدون شرط تفعيل واضح
3. **تأكد من دقة الأسعار** المستخرجة
4. **كون صادقاً** في تقييم قوة الإشارة
5. **لا تخترع أرقاماً** غير موجودة في الصورة

الآن قم بتحليل الشارت المرفق وأعطني الإجابة بالتنسيق المطلوب أعلاه.
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
            "max_tokens": 2000,
            "temperature": 0.2
        }
        
        headers = {
            "Authorization": f"Bearer {GROQ_KEY}",
            "Content-Type": "application/json"
        }
        
        result = safe_api_call(GROQ_URL, headers, payload, timeout=60)
        
        if "error" in result:
            await wait_msg.edit_text(result["error"])
            return MAIN_MENU
            
        elif "choices" in result:
            analysis_result = result['choices'][0]['message']['content'].strip()
            
            # تنظيف النص من التكرار
            analysis_result = clean_repeated_text(analysis_result)
            
            # تنسيق النتيجة النهائية
            keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
            
            # إعداد النص النهائي
            full_result = (
                f"✅ **تم التحليل بنجاح!**\n"
                f"📈 **نتائج تحليل الشارت:**\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{analysis_result}\n\n"
                f"📊 **الإعدادات المستخدمة:**\n"
                f"• سرعة الشموع: {candle}\n"
                f"• {time_for_prompt}\n"
                f"• جلسة السوق: {session_name}\n"
                f"• تأثير الأخبار: {news_impact}\n\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🤖 **Obeida Trading - نظام التحليل الفني المتقدم**"
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
        else:
            await wait_msg.edit_text("❌ **حدث خطأ في تحليل الصورة**")
            
    except Exception as e:
        print(f"خطأ في تحليل الصورة: {e}")
        keyboard = [["الرجوع للقائمة الرئيسية"]]
        await wait_msg.edit_text("❌ **حدث خطأ في إرسال الصورة.**\nيرجى التأكد من وضوح الصورة والمحاولة مرة أخرى.")
    finally:
        if os.path.exists(path):
            os.remove(path)
    
    return MAIN_MENU

# --- الدوال الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    user_id = update.effective_user.id
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["💬 دردشة", "📈 توصية"]
    ]
    
    welcome_message = (
        "🚀 **أهلاً بك في Obeida Trading **\n\n"
        "🤖 **المميزات المتقدمة:**\n"
        "• 📊 تحليل فني متقدم للشارتات\n"
        "• 💬 دردشة ذكية متعددة التخصصات\n"
        "• 📈 نظام توصيات جاهزة للعملات\n"
        "• ⚙️ إعدادات تخصيص كاملة\n"
        "• 🔒 نظام أمان متقدم (Kill Zones)\n\n"
        "📡 **إحصائيات النظام:**\n"
        f"• إصدار النظام: 2.0.1\n"
        f"• نموذج الذكاء: {GROQ_MODEL}\n"
        f"• الوقت الحالي: {datetime.now().strftime('%H:%M')}\n\n"
        "**اختر أحد الخيارات:**"
    )
    
    await update.message.reply_text(
        welcome_message,
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
            
            await update.message.reply_text(
                f"📊 **جاهز للتحليل المتقدم**\n\n"
                f"⚙️ **الإعدادات الحالية:**\n"
                f"• سرعة الشموع: {candle}\n"
                f"• {time_display}\n\n"
                f"**📤 أرسل صورة الرسم البياني (الشارت) الآن:**",
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
        "🔍 **اختر أحد الخيارات من القائمة:**",
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
            f"**📊 الآن حدد مدة الصفقة المتوقعة:**\n\n"
            f"**خيارات مدة الصفقة:**\n"
            f"• **📈 قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة\n"
            f"• **📉 متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة\n"
            f"• **📊 طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة\n\n"
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
            f"🎉 **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ سرعة الشموع: {candle}\n"
            f"✅ مدة الصفقة: {user_message}\n\n"
            f"**🚀 يمكنك الآن استخدام المميزات:**\n"
            f"• تحليل صورة شارت\n"
            f"• الدردشة الذكية\n"
            f"• نظام التوصيات",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة وضع التحليل"""
    user_message = update.message.text
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    await update.message.reply_text(
        "📤 **الرجاء إرسال صورة الشارت فقط**\n"
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
    • 9 أقسام رئيسية
    • توصيات مفصلة لكل عملة
    • تحليل سريع ومباشر
    
    ⏱️ **خيارات مدة الصفقة:**
    • **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة
    • **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة
    • **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة
    
    📊 **مميزات البوت:**
    • تحليل فني للرسوم البيانية
    • دردشة ذكية مع الذكاء الاصطناعي
    • نظام توصيات العملات
    • حفظ إعداداتك الشخصية
    • واجهة سهلة بالأزرار
    • نظام أمان متقدم
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
    print(f"📡 Model: {GROQ_MODEL}")
    print(f"🔑 API Key: {'*' * 20}{GROQ_KEY[-8:] if GROQ_KEY else 'NOT SET'}")
    
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
    
    print("✅ Telegram Bot initialized successfully")
    print("📡 Bot is now polling for updates...")
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

def main():
    """الدالة الرئيسية"""
    print("🚀 Starting Obeida Trading Bot v2.0.1...")
    print("=" * 50)
    print(f"📊 System Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🤖 AI Model: {GROQ_MODEL}")
    print(f"💾 Database: {DB_NAME}")
    print("=" * 50)
    
    try:
        # تشغيل Flask في thread منفصل
        flask_thread = threading.Thread(target=run_flask_server, daemon=True)
        flask_thread.start()
        
        port = os.environ.get('PORT', 8080)
        print(f"🌐 Flask server started on port {port}")
        print(f"🔗 Health Check: http://localhost:{port}/health")
        print("=" * 50)
        
        # تشغيل Telegram bot في thread الرئيسي
        run_telegram_bot()
        
    except Exception as e:
        print(f"❌ Critical Error: {e}")
        print("🔄 Restarting in 10 seconds...")
        time.sleep(10)
        main()

if __name__ == "__main__":
    main()
