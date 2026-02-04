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
import asyncio
import websockets
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from flask import Flask
import aiohttp

# --- الإعدادات ---
TOKEN = os.environ.get('TOKEN', "7324911542:AAGcVkwzjtf3wDB3u7cprOLVyoMLA5JCm8U")

# ⚡ إعدادات Mistral AI API الجديدة
MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "WhGHh0RvwtLLsRwlHYozaNrmZWkFK2f1")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "pixtral-large-latest"
MISTRAL_MODEL_AUDIT = "mistral-large-pixtral-2411"  # موديل التدقيق

# إعدادات Binary.com لسحب الشارتات
BINARY_TOKEN = "M8RHa6kCMAdCOOd"
BINARY_APP_ID = "1089"
BINARY_WS_URL = f"wss://ws.binaryws.com/websockets/v3?app_id={BINARY_APP_ID}"

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

# تعيين الرموز لـ Binary.com
BINARY_SYMBOLS = {
    "EUR/USD (OTC)": "frxEURUSD",
    "GBP/USD (OTC)": "frxGBPUSD",
    "USD/JPY (OTC)": "frxUSDJPY",
    "USD/CHF (OTC)": "frxUSDCHF",
    "AUD/USD (OTC)": "frxAUDUSD",
    "USD/CAD (OTC)": "frxUSDCAD",
    "NZD/USD (OTC)": "frxNZDUSD",
    "EUR/GBP (OTC)": "frxEURGBP",
    "EUR/JPY (OTC)": "frxEURJPY",
    "GBP/JPY (OTC)": "frxGBPJPY",
    "EUR/CHF (OTC)": "frxEURCHF",
    "AUD/JPY (OTC)": "frxAUDJPY",
    "EUR/AUD (OTC)": "frxEURAUD",
    "EUR/CAD (OTC)": "frxEURCAD",
    "GBP/AUD (OTC)": "frxGBPAUD",
    "CAD/JPY (OTC)": "frxCADJPY",
    "CHF/JPY (OTC)": "frxCHFJPY",
    "NZD/JPY (OTC)": "frxNZDJPY",
    "GBP/CHF (OTC)": "frxGBPCHF",
    "AUD/CAD (OTC)": "frxAUDCAD",
    "S&P 500 (OTC)": "R_50",
    "Dow Jones (OTC)": "R_30",
    "Nasdaq 100 (OTC)": "NDX100",
    "DAX 40 (OTC)": "R_DAX",
    "CAC 40 (OTC)": "R_CAC",
    "FTSE 100 (OTC)": "R_FTSE",
    "Hang Seng (OTC)": "R_HK50",
    "Nikkei 225 (OTC)": "R_J225",
    "Gold (OTC)": "frxXAUUSD",
    "Silver (OTC)": "frxXAGUSD",
    "UKOIL (OTC)": "frxUKOIL",
    "USOIL (OTC)": "frxUSOIL",
    "Natural Gas (OTC)": "frxNGAS",
    "Volatility 100 (OTC)": "R_100",
    "Volatility 75 (OTC)": "R_75",
    "Volatility 10 (1s) (Fast OTC)": "1HZ10V"
}

# حالات المحادثة
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION, TIME_SELECTION = range(8)

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
        <p>Obeida Trading - (Dual Model System + Auto Chart)</p>
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

# --- نظام Binary.com لسحب الشارتات ---
async def get_binary_chart(symbol, timeframe="1m", count=100):
    """سحب شارت من Binary.com عبر WebSocket"""
    try:
        async with websockets.connect(BINARY_WS_URL) as websocket:
            # طلب الشارت
            chart_request = {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": count,
                "end": "latest",
                "start": 1,
                "style": "candles",
                "granularity": self.get_granularity_from_timeframe(timeframe),
                "subscribe": 1
            }
            
            await websocket.send(json.dumps(chart_request))
            
            # استلام البيانات
            response = await websocket.recv()
            data = json.loads(response)
            
            if "candles" in data:
                candles = data["candles"]
                
                # تحويل البيانات إلى صورة
                chart_image = await generate_chart_image(symbol, candles, timeframe)
                return chart_image
            else:
                print(f"Error: No candles in response for {symbol}")
                return None
                
    except Exception as e:
        print(f"Error fetching chart from Binary.com: {e}")
        return None

def get_granularity_from_timeframe(timeframe):
    """تحويل timeframe إلى granularity لـ Binary.com"""
    timeframe_map = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "1d": 86400
    }
    return timeframe_map.get(timeframe, 60)

async def generate_chart_image(symbol, candles, timeframe):
    """إنشاء صورة للشارت باستخدام matplotlib"""
    try:
        import matplotlib
        matplotlib.use('Agg')  # لعدم استخدام GUI
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime
        
        # تحضير البيانات
        dates = []
        opens = []
        highs = []
        lows = []
        closes = []
        
        for candle in candles:
            if candle.get('open') and candle.get('close'):
                # تحويل timestamp إلى datetime
                date = datetime.fromtimestamp(candle['epoch'])
                dates.append(date)
                opens.append(float(candle['open']))
                highs.append(float(candle['high']))
                lows.append(float(candle['low']))
                closes.append(float(candle['close']))
        
        if not dates:
            return None
        
        # إنشاء الشارت
        fig, ax = plt.subplots(figsize=(12, 6))
        
        # تحديد لون الشموع
        colors = []
        for i in range(len(closes)):
            if closes[i] >= opens[i]:
                colors.append('green')  # شمعة صاعدة
            else:
                colors.append('red')    # شمعة هابطة
        
        # رسم الشموع
        width = 0.6
        for i in range(len(dates)):
            # رسم الجسم
            ax.bar(dates[i], closes[i] - opens[i], width, bottom=opens[i], color=colors[i], edgecolor='black')
            # رسم الظلال
            ax.plot([dates[i], dates[i]], [lows[i], highs[i]], color='black', linewidth=0.5)
        
        # تنسيق المحاور
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        plt.xticks(rotation=45)
        ax.set_xlabel('الوقت')
        ax.set_ylabel('السعر')
        ax.set_title(f'{symbol} - {timeframe}')
        ax.grid(True, alpha=0.3)
        
        # حفظ الصورة
        image_path = f"chart_{symbol}_{int(time.time())}.png"
        plt.tight_layout()
        plt.savefig(image_path, dpi=100, bbox_inches='tight')
        plt.close()
        
        # تحويل الصورة إلى base64
        with open(image_path, "rb") as img_file:
            encoded_image = base64.b64encode(img_file.read()).decode()
        
        # حذف الملف المؤقت
        os.remove(image_path)
        
        return encoded_image
        
    except Exception as e:
        print(f"Error generating chart image: {e}")
        return None

# --- وظائف نظام التوصية الجديد ---
async def get_mistral_analysis_with_chart(symbol, timeframe="1m"):
    """الحصول على تحليل من Mistral AI API مع شارت تلقائي"""
    try:
        # الحصول على الشارت من Binary.com
        print(f"📊 جاري سحب شارت {symbol} من Binary.com...")
        binary_symbol = BINARY_SYMBOLS.get(symbol)
        
        if not binary_symbol:
            # إذا لم يكن هناك رمز محدد، استخدم رمز افتراضي
            if "USD" in symbol or "EUR" in symbol or "JPY" in symbol:
                binary_symbol = "frxEURUSD"  # رمز افتراضي للعملات
            else:
                binary_symbol = "R_100"  # رمز افتراضي للمؤشرات
        
        chart_image = await get_binary_chart(binary_symbol, timeframe)
        
        if not chart_image:
            return await get_mistral_analysis(symbol)
        
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
        
        **ملاحظة**: هذا التحليل مبني على بيانات السوق الحية من Binary.com.
        """
        
        body = {
            "model": MISTRAL_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{chart_image}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            "temperature": 0.1,
            "max_tokens": 1500
        }

        response = requests.post(MISTRAL_URL, json=body, headers=headers, timeout=45)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content'].strip()
        
    except Exception as e:
        print(f"Error in get_mistral_analysis_with_chart: {e}")
        return await get_mistral_analysis(symbol)

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
        "🚀 **نظام التوصيات المتقدم**\n\n"
        "📊 **مميزات جديدة:**\n"
        "• سحب شارتات حية تلقائياً\n"
        "• تحليل فني مباشر\n"
        "• بيانات من Binary.com\n\n"
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
    
    # إذا وجدت العملة، اختيار timeframe
    if symbol_to_analyze:
        # حفظ الرمز في context
        context.user_data['selected_symbol'] = symbol_to_analyze
        
        # عرض خيارات timeframe
        timeframes_keyboard = [
            ["📈 1 دقيقة (سريع)", "📊 5 دقائق"],
            ["📉 15 دقيقة", "📈 1 ساعة"],
            ["🔙 العودة للقائمة", "الرجوع للقائمة الرئيسية"]
        ]
        
        await update.message.reply_text(
            f"⏰ **اختر إطار الزمن لـ {symbol_to_analyze}**:\n\n"
            f"• 📈 1 دقيقة: تحليل سريع للتداول اليومي\n"
            f"• 📊 5 دقائق: تحليل متوسط المدى\n"
            f"• 📉 15 دقيقة: تحليل سوينج\n"
            f"• 📈 1 ساعة: تحليل طويل المدى",
            reply_markup=ReplyKeyboardMarkup(timeframes_keyboard, resize_keyboard=True)
        )
        return TIME_SELECTION
    
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

async def handle_timeframe_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار timeframe"""
    user_text = update.message.text.strip()
    
    # العودة للقائمة الرئيسية
    if user_text == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    # العودة للقائمة السابقة
    if user_text == "🔙 العودة للقائمة":
        reply_keyboard = [[key] for key in CATEGORIES.keys()]
        reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "🔙 **العودة للقائمة الرئيسية للتوصيات**\nاختر القسم المطلوب:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
        )
        return RECOMMENDATION_MODE
    
    # تحديد timeframe
    timeframe_map = {
        "📈 1 دقيقة (سريع)": "1m",
        "📊 5 دقائق": "5m",
        "📉 15 دقيقة": "15m",
        "📈 1 ساعة": "1h"
    }
    
    if user_text in timeframe_map:
        symbol = context.user_data.get('selected_symbol')
        timeframe = timeframe_map[user_text]
        
        wait_msg = await update.message.reply_text(
            f"⏳ جاري سحب شارت وتوصيات `{symbol}` ({timeframe})...\n"
            f"📡 الاتصال بـ Binary.com..."
        )
        
        try:
            # الحصول على التحليل مع الشارت
            analysis = await get_mistral_analysis_with_chart(symbol, timeframe)
            
            final_msg = (
                f"📈 **توصيات {symbol} - {timeframe}**\n"
                f"🕒 **وقت التحديث:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"{analysis}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📊 **المصدر:** Binary.com + Obeida Trading AI\n"
                f"🤖 **Powered by - Obeida Trading**"
            )
            
            # تنظيف النص من التكرارات
            final_msg = clean_repeated_text(final_msg)
            
            await wait_msg.edit_text(
                final_msg,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            print(f"Error in recommendation: {e}")
            await wait_msg.edit_text(
                f"⚠️ **حدث خطأ أثناء تحليل {symbol}**\n"
                f"الخطأ: {str(e)[:100]}\n\n"
                f"يرجى المحاولة مرة أخرى."
            )
        
        # عرض الأزرار للاستمرار
        reply_keyboard = [[key] for key in CATEGORIES.keys()]
        reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "🔽 **اختر قسم آخر أو العودة للقائمة الرئيسية:**",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)
        )
        return RECOMMENDATION_MODE
    
    # إذا لم يطابق النص أي timeframe
    await update.message.reply_text(
        "❌ خيار غير صحيح. يرجى اختيار إطار زمني من القائمة.",
        reply_markup=ReplyKeyboardMarkup([
            ["📈 1 دقيقة (سريع)", "📊 5 دقائق"],
            ["📉 15 دقيقة", "📈 1 ساعة"],
            ["🔙 العودة للقائمة", "الرجوع للقائمة الرئيسية"]
        ], resize_keyboard=True)
    )
    return TIME_SELECTION

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
    """معالجة الصور للتحليل الفني المتقدم مع نظام الموديل المزدوج"""
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
        
        # ========== ربط معطيات الإعدادات في البرومبت ==========
        # تحديد التصنيف بناءً على سرعة الشموع
        candle_category = ""
        if candle.startswith('S'):  # ثواني
            candle_category = "فريمات سريعة جداً (ثواني) - حركات سريعة وانعكاسات مفاجئة"
        elif candle.startswith('M'):  # دقائق
            candle_category = "فريمات متوسطة (دقائق) - حركات متوسطة السرعة"
        elif candle.startswith('H'):  # ساعات
            candle_category = "فريمات بطيئة (ساعات) - حركات بطيئة وثابتة"
        elif candle.startswith('D'):  # يومي
            candle_category = "فريمات طويلة (يومي) - اتجاهات طويلة الأمد"
        
        # تحديد استراتيجية التداول بناءً على مدة الصفقة
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
        
        # البرومبت الجديد الكامل مع ربط المعطيات
        prompt = f"""
أنت محلل فني خبير في مدرسة Smart Money Concepts (SMC) متخصص في الأسهم والصناديق والسلع والكريبتو والعملات. مهمتك هي تحليل الشارت المرفق وتقديم التوصيات وفقاً للتنسيق المحدد.

🔰 **القواعد الأساسية الحاكمة**
1. **المدرسة المعتمدة:** SMC كإطار عمل رئيسي مع دعم بالتحليل الكلاسيكي
2. **الدرع الأساسي:** {news_warning if news_warning else "✅ الوضع آمن من الأخبار"}
3. **التصنيف الزمني:** {candle_category}
4. **استراتيجية التداول:** {trading_strategy}
5. **إدارة الحجم:** {position_sizing}
6. **أولوية الزخم:** الشموع الابتلاعية (>80%) مع إغلاق فوق قمة سابقة = إشارة استمرار. ممنوع توقع الانعكاس لمجرد وجود FVG غير مغطاة.
7. **منطق OTC:** ابحث عن 'تتابع الشموع' (3 شموع قوية → الشمعة الرابعة في نفس الاتجاه).
8. **التصحيح الزمني:** في الفريمات الصغيرة، تجاهل MACD عند تعارضه مع السلوك السعري الواضح. استخدمه كتأكيد ثانوي فقط.
9. **كشف وهم الزخم:** تحقق من استدامة الحركة.
10. **استخراج البيانات:** إحداثيات دقيقة من المحور اليميني.
11. **فلتر الجدوى:** نسبة RR ≥ 1:2 مع تعديل الأخبار.
12. **المصداقية المطلقة:** لا إشارة إلا إذا كانت 100% واضحة.
13. **تقييد الوسطية:** قرار واضح فقط (شراء/بيع/احتفاظ) مع مستوى الثقة.

📊 **المرحلة 1: الفحص الأولي والتحذيرات**
#1.1 نظام الأمان ثلاثي الطبقات
• الطبقة 1: الدرع الأساسي - {news_warning if news_warning else "✅ الوضع آمن"}
• الطبقة 2: كشف وهم الزخم - فحص الشموع الكبيرة، اختبار الاستدامة (3 شموع)، تحليل المتابعة
• الطبقة 3: التحقق من البيانات - استخراج السعر بدقة، مطابقة الأرقام، تحديد النطاق

#1.2 كشف مخاطر OTC
• إشارات التلاعب: انعكاس لحظي، اختراق ثم عودة، حركة غير متوافقة مع الحجم، تشكيلات غير منطقية
• إستراتيجية الحماية: تجنب آخر 10 ثوانٍ، استخدام أوامر معلقة، زيادة SL بنسبة 20%

#1.3 تحليل الارتباط السعري
• Forex: مؤشر الدولار، العملات المرتبطة، السندات
• Stocks: المؤشر العام، القطاع، أخبار الأرباح
• Crypto: البيتكوين، علاقة الألتكوين، مؤشر الخوف والجشع

📈 **المرحلة 2: التحليل الهيكلي المتقدم**
#2.1 تحديد مدرسة التحليل
• SMC مع دعم كلاسيكي، استخدام مصطلحات SMC بدقة، تحديد BOS و CHoCh

#2.2 استخراج الإحداثيات الرقمية
• قراءة الأسعار من المحور، تحديد الأعلى والأدنى، حساب النسب المئوية، التحقق من الدقة

#2.3 مصفاة التسعير (PD Array)
• تحديد القمة والقاع، خط التوازن 50%
• منطقة الخصم للشراء، منطقة الغلاء للبيع
• الدخول مع الكسر فقط عند BOS بزخم قوي
• مناطق الطوارئ (أقل 20% / أعلى 80%)

💰 **المرحلة 3: تحليل السيولة والزخم المتقدم**
#3.1 كشف وهم الزخم
• العلامات: شمعة خبر منفردة، فجوات سعرية، غياب المتابعة، ذيول طويلة، V-Reversal
• الاختبار الحقيقي: 3 شموع متتالية، تدرج في الأجسام، توافق مع الهيكل، زيادة الحجم، اختراق مستويات

#3.2 خرائط السيولة المتقدمة
• Equal Highs/Lows، مناطق Inducement، Liquidity Sweeps، FVG مفتوحة، Stop Levels

#3.3 تحليل انعكاس الزخم المفاجئ
• الإشارات: شمعة رفض بعد اندفاع، فشل اختراق سيولة، انخفاض الحجم، ديفرجنس
• الإستراتيجية: خروج جزئي عند أول رفض، تحريك SL للتعادل، عدم الدخول ضد 3 شموع قوية

🎯 **المرحلة 4: نظام القرار الذكي**
#4.1 فلتر التلاقي الرباعي (4/4)
• POI صالح، نموذج شموعي، سلوك سعري واضح، توافق مع الاتجاه

#4.2 تعديل المخاطر حسب الأخبار
• Stop Loss = SL × {news_risk_multiplier}
• الحجم = الحجم ÷ {news_risk_multiplier}
• RR ≥ 1:{max(3, 2 * news_risk_multiplier)}

#4.3 شروط الحظر الكامل
• خبر عالي التأثير ±30 دقيقة، زخم وهمي واضح، فشل فلتر التلاقي، السعر في Equilibrium
• V-Reversal حديث، تضارب حاد بين المؤشرات والسلوك

#4.4 حل تضارب المؤشرات
• الأولوية: 1) السلوك السعري، 2) السيولة والزخم، 3) المؤشرات (تأكيد فقط)، 4) السياق الزمني

📊 **المرحلة 5: مراقبة سلوك الشموع**
#5.1 استجابة الشموع عند POI
• النمط: رفض / امتصاص / جانبي
• القوة: جسم/ذيول، الحجم: منخفض / طبيعي / مرتفع
• الأنماط الحاسمة: شمعة اختبار (ظل طويل + إغلاق بعيد + حجم معتدل)، شمعة رفض (Pin Bar + إغلاق معاكس + حجم مرتفع)

#5.2 قانون 3 شموع
• صعود: اختبار دعم → تصحيح خفيف → اختراق أعلى
• هبوط: اختبار مقاومة → ارتداد خفيف → اختراق أسفل

#5.3 التتابع الزمني
• الشمعة 1: رد فعل، الشمعة 2: تأكيد/تكذيب، الشمعة 3: قرار
• معايير: عدم التأكيد خلال 3 شموع → تجاهل، اختراق ثم عودة خلال شمعة → إشارة قوية

📉 **المرحلة 6: تحليل MACD المحسن**
#6.1 التحليل الرباعي
• مرحلة التقاطع وزاويته، موقع خط الصفر والمسافة، حالة الهيستوجرام وربطها بالزخم، فحص الدايفرجنس عند السيولة أو POI

#6.2 قواعد حسب الفريم
• 1–5 دقائق: تجاهل التقاطعات البطيئة، التركيز على الهيستوجرام المتوسط، استخدام كتأكيد فقط
• 15–60 دقيقة: التركيز على خط الصفر، البحث عن الدايفرجنس عند POI، أحد معايير التلاقي

#6.3 حل التعارض
1. سلوك سعري واضح → تجاهل MACD
2. تعارض مع 3 شموع → تقليل الحجم 50%
3. تعارض مع BOS → تأجيل شمعة
4. تعارض مع دايفرجنس → تحذير فقط

⏰ **المرحلة 7: تحليل تعدد الإطارات**
#7.1 نظام الإطارات الأربعة
• HTF: الاتجاه العام، MTF1: مناطق العرض/الطلب، MTF2: Order Blocks نشطة، LTF: توقيت الدخول

#7.2 توافق الاتجاهات
• قوي (4/4) → +40 ثقة، جيد (3/4) → +30 ثقة
• متعارض جزئي (2/4) → تقليل الحجم 50%، متعارض قوي (1/4) → تجنب الدخول

#7.3 إستراتيجية التعدد الزمني
• للشراء: HTF صاعد → تصحيح لمنطقة عرض → OB في Discount → إشارة شراء
• للبيع: HTF هابط → ارتداد لمنطقة طلب → OB في Premium → إشارة بيع

🎯 **المرحلة 8: نظام درجات الثقة**
#8.1 إضافة النقاط (+)
• POI صالح: +25، نموذج شموعي واضح: +20، سلوك سعري واضح: +25
• توافق الإطارات (3/4+): +30، حجم أعلى من المتوسط: +15، أخبار هادئة: +20
• BOS مؤكد: +30، تغطية فجوة سعرية: +15، توافق MACD: +10، لا تعارض مؤشرات: +15

#8.2 خصم النقاط (-)
• تعارض مؤشرات: -20، أخبار قوية: -25، زخم وهمي: -15
• V-Reversal قريب: -30، سيولة OTC منخفضة: -10

#8.3 مستويات الثقة
• 95–100: 💥💥 استثنائي (حجم كامل +20%)
• 85–94: 💥 قوي جداً (حجم كامل)
• 70–84: 🔥 قوي (80%)
• 55–69: ⚡ متوسط (60%)
• 40–54: ❄️ ضعيف (30% أو تجنب)
• <40: 🚫 مرفوض

📊 **المرحلة 9: تحليل الحجم المتقدم**
#9.1 أنماط الحجم
• اختراق: >150% من المتوسط، امتصاص: حجم عالي + حركة محدودة
• تصحيح: <70% من المتوسط، تردد: حجم منخفض + تذبذب
• انعكاس: حجم مرتفع مفاجئ بعد حركة طويلة

#9.2 نقاط التحكم الحجمي
• POC: أعلى حجم = دعم/مقاومة، VA: 70% تداول = توازن
• EVA: خارج VA = إشارة قوية، مناطق حجم منخفض: اختراق محتمل

🔄 **المرحلة 10: إدارة الصفقات الديناميكية**
#10.1 الخروج المتدرج
• Long: TP1: SL للتعادل + خروج 40%، TP2: SL أعلى شمعة + خروج 30%
• TP3: ترك 30% بترايل أو خروج كامل عند مقاومة

#10.2 نظام التراجع الذكي
• تراجع 40%: خروج 50%، كسر الدخول: خروج كامل
• ديفرجنس عكسي: تحريك SL، V-Reversal: خروج 80%

#10.3 حماية OTC
• SL موسع +20%، دخول بعد إغلاق 3 شموع
• حجم متدرج (33/33/34)، خروج مبكر عند 70% من TP1

🧠 **المرحلة 11: التحليل السلوكي المتقدم**
#11.1 حالات السوق النفسية
• الخوف: ظلال طويلة + أحجام مرتفعة مفاجئة
• الجشع: تسارع بدون تصحيح + أجسام كبيرة متتالية
• التردد: شموع داخلية/دوجي + أحجام منخفضة
• الاستسلام: اختراق حاسم بحجم ضخم + شمعة كبيرة جداً
• التلاعب: حركات غير منطقية + اختراقات زائفة

#11.2 كشف التلاعب المؤسسي
• Liquidity Sweep: اختراق ثم عودة، Stop Hunt: سحب وقف ثم انعكاس
• False Breakout: اختراق بحجم ضعيف، Bait Pattern: إشارة جذابة ثم انعكاس
• التمييز: اختراق بذيل + عودة = فخ سيولة، اختراق بجسم كامل + إغلاق خلف المستوى = BOS حقيقي

#11.3 سلوك OTC
• إشارات الخوارزمية: تكرار نمط 3 مرات، اختراقات في أوقات ثابتة، حركة ضد المنطق الفني، شمعة واحدة تغير السياق
• إستراتيجية المواجهة: لا تعتمد على نمط واحد، تأكيد من نمطين على الأقل، تجنب أوقات السيولة الضعيفة، استخدم أوامر معلقة بعيدة

📊 **المعطيات الفنية:**
• **إطار الزمن:** {candle} ({candle_category})
• **استراتيجية التداول:** {trading_strategy}
• **جلسة السوق:** {session_name} ({session_time})
• **حالة السيولة:** {session_vol}
• **تأثير الأخبار:** {news_impact} (معامل ×{news_risk_multiplier})
• **توقيت التحليل:** {current_time.strftime('%Y-%m-%d %H:%M GMT')}
• **المستوى:** Professional باك تيست 15000 صفقة

🎯 **التنسيق المطلوب للإجابة (الالتزام حرفياً):**

📊 **التحليل الفني المتقدم:**
• **البصمة الزمنية:** {kill_zone_status}
• **حالة الهيكل:** (صاعد/هابط) + (مرحلة وايكوف الحالية) + (توافق 4/4 إطارات: نعم/لا)
• **خريطة السيولة:** (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• **الفجوات السعرية:** (المناطق التي سيعود السعر لتغطيتها)

🎯 **الإشارة التنفيذية:**
• **السعر الحالي:** [السعر الدقيق من الشارت]
• **حالة الشمعة:** [مفتوحة / مغلقة]
• **القرار الفني:** (شراء 🟢 / بيع 🔴 / احتفاظ 🟡)
• **قوة الإشارة:** (عالية جدا 💥 / عالية 🔥 / متوسطة ⚡ / ضعيفة ❄️)
• **نقطة الدخول:** [السعر الدقيق بناءً على OB + شرط الإغلاق]
• **الأهداف الربحية:**
  🎯 **TP1:** [سحب أول سيولة داخلية], [احتمالية الوصول]
  🎯 **TP2:** [الهدف الرئيسي - منطقة عرض/طلب قوية]
  🎯 **TP3:** [سيولة خارجية أو سد فجوة سعرية]
• **وقف الخسارة:** [السعر مع 3 طبقات حماية]
• **المدة المتوقعة:** [عدد الدقائق] (بناءً على معادلة الزخم السعري)
• **وقت الذروة المتوقع:** [مثلاً: خلال الـ 3 شموع القادمة]
• **الحالة النفسية:** [خوف 🥺 / جشع 🤑 / تردد 🤌 / استسلام 👎]
• **علامات التلاعب:** [موجودة ✔️ / غير موجودة ❎]

⚠️ **إدارة المخاطر:**
• **مستوى الثقة:** [0-100]٪ = [💥/🔥/⚡/❄️/🚫]
• **نقطة الإلغاء:** [السعر الذي يفسد التحليل]

الآن قم بتحليل الشارت المرفق وأعطني الإجابة بالتنسيق المطلوب أعلاه.
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        # --- الخطوة 1: التحليل الأولي بواسطة الموديل الأساسي (Latest) ---
        await wait_msg.edit_text("📊 جاري تحليل (المرحلة 1/2)...")
        
        payload_1 = {
            "model": MISTRAL_MODEL,
            "messages": [
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}", "detail": "high"}}
                    ]
                }
            ],
            "max_tokens": 1800,
            "temperature": 0.10,
            "top_p": 0.95,
            "random_seed": 42,
        }
        
        response_1 = requests.post(MISTRAL_URL, headers=headers, json=payload_1, timeout=45)
        
        if response_1.status_code != 200:
            print(f"Obeida Vision Error (Model 1): {response_1.status_code} - {response_1.text}")
            raise Exception(f"خطأ في التحليل الأول: {response_1.status_code}")
        
        initial_analysis = response_1.json()['choices'][0]['message']['content'].strip()
        
        # --- الخطوة 2: الدمج والتدقيق بواسطة الموديل الثاني (2411) ---
        await wait_msg.edit_text("📊 جاري التحليل (المرحلة 2/2)...")
        
        prompt_audit = f"""
أنت المدقق النهائي في مؤسسة Obeida Trading. 
إليك التحليل المقترح: {initial_analysis}

**مهمتك:** راجع هذا التحليل بناءً على الشارت المرفق، تأكد من دقة الأرقام (Entry, SL, TP)، 
وصحح أي أخطاء بصرية، ثم أخرج التقرير النهائي الأسطوري بالتنسيق المطلوب حرفياً.

**قواعد التدقيق الصارمة:**
1. **دقة الأرقام:** تأكد من مطابقة الأسعار المذكورة مع ما هو ظاهر في الشارت
2. **سلامة المنطق:** تحقق من عدم وجود تناقضات في التحليل
3. **التنسيق:** الالتزام الكامل بالتنسيق المطلوب
4. **تحسين الصياغة:** جعل اللغة أكثر احترافية ووضوحاً
5. **إضافة الفوائد:** أضف أي رؤى إضافية مفيدة لم تذكر في التحليل الأول

**التنسيق المطلوب (يجب الالتزام به حرفياً):**

📊 **التحليل الفني المتقدم:**
• **البصمة الزمنية:** {kill_zone_status}
• **حالة الهيكل:** (صاعد/هابط) + (مرحلة وايكوف الحالية) + (توافق 4/4 إطارات: نعم/لا)
• **خريطة السيولة:** (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• **الفجوات السعرية:** (المناطق التي سيعود السعر لتغطيتها)

🎯 **الإشارة التنفيذية:**
• **السعر الحالي:** [السعر الدقيق من الشارت]
• **حالة الشمعة:** [مفتوحة / مغلقة]
• **القرار الفني:** (شراء 🟢 / بيع 🔴 / احتفاظ 🟡)
• **قوة الإشارة:** (عالية جدا 💥 / عالية 🔥 / متوسطة ⚡ / ضعيفة ❄️)
• **نقطة الدخول:** [السعر الدقيق بناءً على OB + شرط الإغلاق]
• **الأهداف الربحية:**
  🎯 **TP1:** [سحب أول سيولة داخلية], [احتمالية الوصول]
  🎯 **TP2:** [الهدف الرئيسي - منطقة عرض/طلب قوية]
  🎯 **TP3:** [سيولة خارجية أو سد فجوة سعرية]
• **وقف الخسارة:** [السعر مع 3 طبقات حماية]
• **المدة المتوقعة:** [عدد الدقائق] (بناءً على معادلة الزخم السعري)
• **وقت الذروة المتوقع:** [مثلاً: خلال الـ 3 شموع القادمة]
• **الحالة النفسية:** [خوف 🥺 / جشع 🤑 / تردد 🤌 / استسلام 👎]
• **علامات التلاعب:** [موجودة ✔️ / غير موجودة ❎]

⚠️ **إدارة المخاطر:**
• **مستوى الثقة:** [0-100]٪ = [💥/🔥/⚡/❄️/🚫]
• **نقطة الإلغاء:** [السعر الذي يفسد التحليل]

**ملاحظة:** استخدم الصورة المرفقة للتحقق من جميع الأرقام والمستويات المذكورة.
"""
        
        payload_2 = {
            "model": MISTRAL_MODEL_AUDIT,
            "messages": [
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": prompt_audit},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                    ]
                }
            ],
            "max_tokens": 1800,
            "temperature": 0.0,
            "top_p": 0.95,
            "random_seed": 42,
        }
        
        response_2 = requests.post(MISTRAL_URL, headers=headers, json=payload_2, timeout=45)
        
        if response_2.status_code == 200:
            result = response_2.json()['choices'][0]['message']['content'].strip()
        else:
            print(f"Obeida Vision Warning (Model 2): {response_2.status_code} - استخدام التحليل الأول")
            result = initial_analysis
        
        # تنظيف النص من التكرار
        result = clean_repeated_text(result)
        
        if "### تحليل الشارت المرفق" in result:
            parts = result.split("### تحليل الشارت المرفق")
            if len(parts) > 1:
                result = parts[1].strip()
        
        if "نتائج الفحص الفني:" in result:
            result = result.replace("نتائج الفحص الفني:", "📊 **التحليل الفني:**").strip()
        
        keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        
        # تنسيق وقت الصفقة للعرض
        time_display = format_trade_time_for_prompt(trade_time)
        
        # إعداد النص النهائي بدون تكرار
        full_result = (
            f"✅ **تم التحليل بنجاح!**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{result}\n\n"
            f"📊 **الإعدادات المستخدمة:**\n"
            f"• سرعة الشموع: {candle}\n"
            f"• {time_display}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🤖 ** Powered by - Obeida Trading **"
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
        "🚀 **ميزة جديدة:** سحب شارتات حية من Binary.com\n\n"
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
    
    🚀 **ميزة جديدة:**
    • سحب شارتات حية من Binary.com
    • بيانات مباشرة من السوق
    • تحليل تلقائي مع الشارتات
    
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
    print(f"📡 Binary.com API Connected: APP_ID={BINARY_APP_ID}")
    
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
            TIME_SELECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timeframe_selection)
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
    print("🌐 Binary.com WebSocket ready for chart fetching...")
    
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
