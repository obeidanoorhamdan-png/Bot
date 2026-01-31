import logging
import base64
import os
import sqlite3
import re
import requests
import threading
import time
import sys
import json
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from flask import Flask
import tempfile

# --- الإعدادات ---
TOKEN = os.environ.get('TOKEN', "7324911542:AAGcVkwzjtf3wDB3u7cprOLVyoMLA5JCm8U")
GROQ_KEY = os.environ.get('GROQ_KEY', "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")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"
TWELVEDATA_API_KEY = "C77de4f6f2704790b07a5ded7bb00646"
TWELVEDATA_BASE_URL = "https://api.twelvedata.com/chart"
DB_NAME = "abood-gpt.db"

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

# توزيع العملات للنظام الجديد
CATEGORIES = {
    "فوركس - عملات رئيسية 💹": [
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", 
        "USD/CHF", "USD/CAD", "NZ$USD"
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
        "الذهب (XAU/USD)", "الفضة (XAG/USD)", "البلاتين (XPT/USD)", 
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
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION, SYMBOL_SELECTION = range(8)

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

# --- دوال TwelveData API ---
def get_twelvedata_symbol(symbol):
    """تحويل رمز العملة لصيغة TwelveData"""
    symbol_mapping = {
        "EUR/USD": "EUR/USD",
        "GBP/USD": "GBP/USD",
        "USD/JPY": "USD/JPY",
        "AUD/USD": "AUD/USD",
        "USD/CHF": "USD/CHF",
        "USD/CAD": "USD/CAD",
        "NZD/USD": "NZD/USD",
        "EUR/GBP": "EUR/GBP",
        "EUR/JPY": "EUR/JPY",
        "EUR/AUD": "EUR/AUD",
        "EUR/CAD": "EUR/CAD",
        "EUR/NZD": "EUR/NZD",
        "EUR/CHF": "EUR/CHF",
        "GBP/JPY": "GBP/JPY",
        "GBP/AUD": "GBP/AUD",
        "GBP/CAD": "GBP/CAD",
        "GBP/NZD": "GBP/NZD",
        "GBP/CHF": "GBP/CHF",
        "AUD/JPY": "AUD/JPY",
        "AUD/CAD": "AUD/CAD",
        "AUD/NZD": "AUD/NZD",
        "CAD/JPY": "CAD/JPY",
        "NZD/JPY": "NZD/JPY",
        "CHF/JPY": "CHF/JPY",
        "USD/TRY": "USD/TRY",
        "USD/ZAR": "USD/ZAR",
        "USD/MXN": "USD/MXN",
        "USD/SGD": "USD/SGD",
        "USD/NOK": "USD/NOK",
        "USD/SEK": "USD/SEK",
        "S&P 500": "SPX",
        "Dow Jones (US30)": "DJI",
        "DAX 40 (GER40)": "DAX",
        "FTSE 100": "FTSE",
        "CAC 40": "CAC40",
        "Nikkei 225": "N225",
        "ASX 200": "AS51",
        "Hang Seng": "HSI",
        "الذهب (XAUUSD)": "XAU/USD",
        "الفضة (XAGUSD)": "XAG/USD",
        "البلاتين (XPTUSD)": "XPT/USD",
        "النحاس (Copper)": "XCU/USD",
        "نفط برنت (UKOIL)": "BZ",
        "النفط الخام (USOIL)": "CL",
        "الغاز الطبيعي": "NG",
        "NAS100": "NDX",
        "US Tech 100": "NDX",
        "BTC/USD": "BTC/USD",
        "ETH/USD": "ETH/USD",
        "SOL/USD": "SOL/USD",
        "XRP/USD": "XRP/USD",
        "ADA/USD": "ADA/USD",
        "DOT/USD": "DOT/USD",
        "LTC/USD": "LTC/USD"
    }
    
    # إزالة الأقواس إن وجدت
    clean_symbol = symbol.split('(')[0].strip() if '(' in symbol else symbol.strip()
    return symbol_mapping.get(symbol, symbol_mapping.get(clean_symbol, symbol))

def get_chart_image_url(symbol, interval="5min", size="800x600"):
    """جلب رابط صورة الشارت من TwelveData"""
    try:
        # تحويل الرمز إذا لزم الأمر
        td_symbol = get_twelvedata_symbol(symbol)
        
        params = {
            "symbol": td_symbol,
            "interval": interval,
            "apikey": TWELVEDATA_API_KEY,
            "size": size,
            "type": "candlestick",
            "outputsize": "100",
            "timezone": "Asia/Riyadh",
            "prepost": "true"
        }
        
        response = requests.get(TWELVEDATA_BASE_URL, params=params, timeout=30)
        
        if response.status_code == 200:
            # تحقق من نوع الاستجابة
            content_type = response.headers.get('content-type', '')
            
            if 'image' in content_type:
                # حفظ الصورة مؤقتاً
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                temp_file.write(response.content)
                temp_file.close()
                return temp_file.name
            else:
                print(f"Unexpected response type: {content_type}")
                return None
        else:
            print(f"TwelveData API Error: {response.status_code}")
            return None
            
    except Exception as e:
        print(f"Error getting chart image: {e}")
        return None

def map_candle_to_interval(candle_speed):
    """تحويل سرعة الشموع إلى فترة زمنية لـ TwelveData"""
    mapping = {
        "S5": "5sec",
        "S10": "10sec",
        "S15": "15sec",
        "S30": "30sec",
        "M1": "1min",
        "M2": "2min",
        "M3": "3min",
        "M5": "5min",
        "M10": "10min",
        "M15": "15min",
        "M30": "30min",
        "H1": "1hour",
        "H4": "4hour",
        "D1": "1day"
    }
    return mapping.get(candle_speed, "5min")

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
    
    # إزالة أي تنسيق مكرر
    patterns_to_clean = [
        r'(📊 \*\*نتائج الفحص الفني\*\*:[\s\S]*?)(?=📊 \*\*نتائج الفحص الفني\*\*:)',
        r'(### تحليل الشارت المرفق[\s\S]*?)(?=### تحليل الشارت المرفق)',
        r'📊\s*\*\*التحليل الفني\*\*:',
        r'🎯\s*\*\*التوصية والتوقعات\*\*:',
        r'⚠️\s*\*\*إدارة المخاطر\*\*:',
        r'📝\s*\*\*ملاحظات التحليل\*\*:'
    ]
    
    for pattern in patterns_to_clean:
        if pattern in patterns_to_clean[:2]:
            text = re.sub(pattern, '', text, flags=re.DOTALL)
        else:
            matches = re.findall(pattern, text)
            if len(matches) > 1:
                parts = re.split(pattern, text)
                if len(parts) > 1:
                    text = parts[0] + re.search(pattern, text).group() + parts[1]
    
    # تقسيم النص إلى فقرات وإزالة التكرار
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    
    unique_paragraphs = []
    seen_paragraphs = set()
    
    for paragraph in paragraphs:
        key = paragraph[:50].strip().lower()
        if key not in seen_paragraphs:
            unique_paragraphs.append(paragraph)
            seen_paragraphs.add(key)
    
    cleaned_text = '\n\n'.join(unique_paragraphs)
    
    # قطع النص إذا كان طويلاً جداً
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
async def analyze_symbol_with_chart(update: Update, context: ContextTypes.DEFAULT_TYPE, symbol, user_id):
    """تحليل العملة مع جلب الصورة من TwelveData"""
    candle, trade_time = get_user_setting(user_id)
    time_for_prompt = format_trade_time_for_prompt(trade_time)
    
    # جلب صورة الشارت
    interval = map_candle_to_interval(candle)
    chart_image_path = get_chart_image_url(symbol, interval)
    
    if not chart_image_path:
        await update.message.reply_text(
            f"⚠️ لم أتمكن من جلب الشارت لـ {symbol}\n"
            f"جارٍ التحليل النصي فقط...",
            parse_mode="Markdown"
        )
        # استدعاء التحليل النصي بدون صورة
        analysis = get_groq_analysis_text_only(symbol, candle, time_for_prompt)
        
        final_msg = (
            f"📈 **نتائج تحليل {symbol}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{analysis}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Obeida Trading - نظام التوصيات**"
        )
        
        final_msg = clean_repeated_text(final_msg)
        
        return final_msg
    
    # إذا وجدنا صورة، نقوم بالتحليل مع الصورة
    try:
        base64_img = encode_image(chart_image_path)
        
        if not base64_img:
            # إذا فشل تحويل الصورة، استخدم التحليل النصي
            analysis = get_groq_analysis_text_only(symbol, candle, time_for_prompt)
        else:
            # تحليل مع الصورة
            analysis = await get_groq_analysis_with_image(symbol, base64_img, candle, time_for_prompt)
        
        final_msg = (
            f"📈 **نتائج تحليل {symbol}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{analysis}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Obeida Trading - نظام التوصيات**"
        )
        
        final_msg = clean_repeated_text(final_msg)
        
        # إرسال الصورة أولاً
        try:
            with open(chart_image_path, 'rb') as photo:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=photo,
                    caption=f"📊 شارت {symbol} - {candle}",
                    parse_mode="Markdown"
                )
        except Exception as e:
            print(f"Error sending photo: {e}")
        
        return final_msg
        
    except Exception as e:
        print(f"Error in analyze_symbol_with_chart: {e}")
        # التحليل النصي كبديل
        analysis = get_groq_analysis_text_only(symbol, candle, time_for_prompt)
        
        final_msg = (
            f"📈 **نتائج تحليل {symbol}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{analysis}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Obeida Trading - نظام التوصيات**"
        )
        
        return clean_repeated_text(final_msg)
    finally:
        # تنظيف الملف المؤقت
        try:
            if chart_image_path and os.path.exists(chart_image_path):
                os.remove(chart_image_path)
        except:
            pass

def get_groq_analysis_text_only(symbol, candle, time_for_prompt):
    """الحصول على تحليل نصي فقط"""
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    بصفتك محللاً مالياً وخبيراً في استراتيجيات التداول، قم بتحليل {symbol}:
    
    المعطيات التقنية:
    - إطار الشمعة: {candle}
    - {time_for_prompt}
    
    قدم تحليلاً شاملاً يتضمن:
    1. **الاتجاه العام** والهيكل السعري
    2. **مستويات الدعم والمقاومة** الرئيسية
    3. **فرص التداول** المحتملة
    4. **إدارة المخاطر** الموصى بها
    5. **الأهداف** ووقف الخسارة
    
    كن واقعياً وموضوعياً، واذكر نسبة الثقة في التحليل.
    """
    
    body = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 1000
    }

    try:
        response = requests.post(GROQ_URL, json=body, headers=headers, timeout=25)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"Error in get_groq_analysis_text_only: {e}")
        return "⚠️ حدث خطأ في التحليل. حاول مرة أخرى."

async def get_groq_analysis_with_image(symbol, base64_img, candle, time_for_prompt):
    """الحصول على تحليل مع صورة"""
    headers = {
        "Authorization": f"Bearer {GROQ_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    [SYSTEM_TASK: TOTAL_MARKET_DECRYPTION]
    بصفتك "المصفاة الذهبية" لتحليل الصفقات الاحترافية، قم بتحليل الشارت المرفق لـ {symbol}:
    
    🔰 **قوانين التحليل**:
    1. تحليل شامل للصورة
    2. تحديد الأنماط الفنية الظاهرة
    3. تقييم قوة الاتجاه
    4. تقديم توقع واضح
    5. تقديم توصيات عملية
    
    المعطيات التقنية:
    - إطار الشمعة: {candle}
    - {time_for_prompt}
    
    قدم التحليل باللغة العربية وبالتنسيق التالي:
    
    📊 **التحليل الفني**:
    - **الاتجاه الحالي**: (صاعد/هابط/جانبي)
    - **المستويات الرئيسية**: (الدعم والمقاومة)
    - **الإشارات الفنية**: (الأنماط والمؤشرات)
    
    🎯 **التوصيات**:
    - **القرار**: (شراء 🟢 / بيع 🔴 / الانتظار)
    - **نقطة الدخول**:
    - **الأهداف (TP)**:
    - **وقف الخسارة (SL)**:
    
    ⚠️ **إدارة المخاطر**:
    - **مستوى الثقة**:
    - **السياق العام**:
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
        "max_tokens": 1200,
        "temperature": 0.3
    }
    
    try:
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f"Error in get_groq_analysis_with_image: {e}")
        return "⚠️ حدث خطأ في تحليل الصورة. جارٍ التحليل النصي..."

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
        
        # حفظ القسم المختار في context
        context.user_data['selected_category'] = user_text
        
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
        # التحقق من وجود الإعدادات
        candle, trade_time = get_user_setting(user_id)
        
        if not candle or not trade_time:
            keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
            await update.message.reply_text(
                "❌ **يجب ضبط الإعدادات أولاً**\n\n"
                "الرجاء استخدام أزرار القائمة لضبط الإعدادات قبل الحصول على التوصيات.",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return MAIN_MENU
        
        wait_msg = await update.message.reply_text(f"⏳ جاري تحليل `{symbol_to_analyze}`...")
        
        # تحليل العملة مع جلب الشارت
        analysis_result = await analyze_symbol_with_chart(update, context, symbol_to_analyze, user_id)
        
        await wait_msg.edit_text(
            analysis_result,
            parse_mode="Markdown"
        )
        
        # عرض الأزرار للاستمرار
        category = context.user_data.get('selected_category', 'فوركس - عملات رئيسية 💹')
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
        "🚀 مساعد شامل": """أنت Obeida Trading، مساعد ذكي شامل...""",
        # ... (نفس المحتوى الأصلي للبرومبتات)
    }
    
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
        # استدعاء واجهة Groq
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": """أنت Obeida Trading، مساعد ذكي شامل..."""},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 1200,
            "temperature": 0.7
        }
        
        headers = {
            "Authorization": f"Bearer {GROQ_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content']
            result = clean_repeated_text(result)
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
            print(f"Groq API Error: {response.status_code} - {response.text}")
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

    wait_msg = await update.message.reply_text("جاري تحليل صورة 📊...")
    photo = await update.message.photo[-1].get_file()
    path = f"img_{user_id}.jpg"
    await photo.download_to_drive(path)

    try:
        base64_img = encode_image(path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.** يرجى إرسال صورة واضحة.")
            if os.path.exists(path):
                os.remove(path)
            return MAIN_MENU
        
        # تنسيق وقت الصفقة للبرومبت
        time_for_prompt = format_trade_time_for_prompt(trade_time)
        
        # برومبت آمن للتحليل الفني
        prompt = f"""[SYSTEM_TASK: TOTAL_MARKET_DECRYPTION_V12_ULTIMATE_PROTOCOL]
بصفتك "المصفاة الذهبية" لتحليل الصفقات الاحترافية، قم بتحليل الشارت المرفق:

🔰 **القوانين الأساسية**:
1. تحليل شامل للصورة
2. تحديد الأنماط الفنية الظاهرة
3. تقييم قوة الاتجاه
4. تقديم توقع واضح
5. تحليل ذكي للصورة 
6. توقعات دقيقة وموضوعية

المعطيات التقنية:
- إطار الشمعة : (Timeframe): {candle}
- وقت التداول المحدد : {time_for_prompt}

قدم التحليل باللغة العربية وبالتنسيق التالي:

📊 **التحليل الفني**:
- **البصمة الزمنية**: (داخل/خارج منطقة القتل السعري - Kill Zone)
- **حالة الهيكل**: (صاعد/هابط)
- **خريطة السيولة**: (مناطق السيولة المستهدفة)
- **الفجوات السعرية (FVG)**: (المناطق التي سيعود السعر لتغطيتها)

🎯 **الإشارة التنفيذية**:
- **السعر الحالي**: [السعر الذي تراه]
- **القرار الفني**: (شراء 🟢 / بيع 🔴 / الانتظار)
- **قوة الإشارة 🔰**: (🔥 عالية / ⚡ متوسطة / ❄️ ضعيفة)
- **نقطة الدخول (Entry)**: [السعر الدقيق]
- **الأهداف الربحية (TPs)**:
    - 🎯 **TP1**: [سعر الهدف الأول]
    - 🎯 **TP2**: [سعر الهدف الرئيسي]
    - 🎯 **TP3**: [سعر الهدف النهائي]
- **وقف الخسارة (SL)**: [سعر وقف الخسارة]
- **المدة المتوقعة 🕧**: [الوقت المتوقع للوصول للهدف]

⚠️ **إدارة المخاطر**:
- **مستوى الثقة**: [% مع ذكر التاكيدات]
- **نقطة الإلغاء**: [السعر الذي يفسد التحليل]
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
            "max_tokens": 1500,
            "temperature": 0.3
        }
        
        headers = {
            "Authorization": f"Bearer {GROQ_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content'].strip()
            result = clean_repeated_text(result)
            
            keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
            
            time_display = format_trade_time_for_prompt(trade_time)
            
            full_result = (
                f"✅ **تم التحليل بنجاح!**\n"
                f"📈 **نتائج تحليل الشارت:**\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{result}\n\n"
                f"📊 **الإعدادات المستخدمة:**\n"
                f"• سرعة الشموع: {candle}\n"
                f"• {time_display}\n\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🤖 **Obeida Trading - نظام التحليل الفني**"
            )
            
            full_result = clean_repeated_text(full_result)
            
            if len(full_result) > 4000:
                parts = split_message(full_result, max_length=4000)
                await wait_msg.edit_text(
                    parts[0],
                    parse_mode="Markdown"
                )
                for part in parts[1:]:
                    await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(
                    full_result,
                    parse_mode="Markdown"
                )
            
            await update.message.reply_text(
                "📊 **اختر الإجراء التالي:**",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
        else:
            print(f"Groq Vision API Error: {response.status_code} - {response.text}")
            keyboard = [["الرجوع للقائمة الرئيسية"]]
            await wait_msg.edit_text(f"❌ **خطأ في إرسال الصورة:** {response.status_code}")
            
    except requests.exceptions.Timeout:
        await wait_msg.edit_text("⏱️ تجاوز الوقت المحدد إرسال الصورة. حاول مرة أخرى.")
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
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["💬 دردشة", "📈 توصية"]
    ]
    
    await update.message.reply_text(
        "🚀 **أهلاً بك في Obeida Trading **\n\n"
        "🤖 **المميزات الجديدة:**\n"
        "• تحليل فني متقدم للشارتات\n"
        "• 🆕 دردشة \n"
        "• 📈 نظام توصيات جاهزة مع شارتات حية\n"
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
            f"يمكنك الآن تحليل صورة أو الحصول على توصيات:",
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
    help_text = """
    🤖 **أوامر البوت:**
    
    /start - بدء البوت والعودة للقائمة الرئيسية
    /help - عرض رسالة المساعدة
    
    ⚙️ **كيفية الاستخدام:**
    1. استخدم أزرار القائمة للتنقل
    2. أرسل صورة الشارت للتحليل
    3. اختر "دردشة" للاستفسارات النصية
    4. اختر "توصية" لتحليل العملات مع شارتات حية
    
    📈 **نظام التوصيات المطور:**
    • تحليل فني للعملات والمؤشرات
    • جلب شارتات حية من TwelveData API
    • أربعة أقسام رئيسية
    • توصيات مفصلة لكل عملة مع صور
    • تحليل سريع ومباشر
    
    ⏱️ **خيارات مدة الصفقة:**
    • **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة
    • **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة
    • **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة
    
    📊 **مميزات البوت:**
    • تحليل فني للرسوم البيانية
    • دردشة ذكية مع الذكاء الاصطناعي
    • نظام توصيات العملات مع شارتات
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
    print("🚀 Starting Obeida Trading...")
    print(f"🔑 TwelveData API Key: {TWELVEDATA_API_KEY[:10]}...")
    
    # تشغيل Flask في thread منفصل
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    print(f"🌐 Flask server started on port {os.environ.get('PORT', 8080)}")
    
    # تشغيل Telegram bot في thread الرئيسي
    run_telegram_bot()

if __name__ == "__main__":
    main()
