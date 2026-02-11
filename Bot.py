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
TOKEN = os.environ.get('TOKEN', "8583903565:AAFveu3KYGkPsC8L6WyermoaZ1tI_EtFHHs")

MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "dwwcerrElpgYHDmOrQLnjAG9B85BhHUR")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "pixtral-large-latest"          # للرؤية والاستخراج البصري
MISTRAL_MODEL_AUDIT = "mistral-large-latest"    # للتدقيق والتحليل المنطقي
MODEL_SUMMARY = "mistral-medium-latest"         # للتلخيص والتحسين

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
        <p>Obeida Trading - (Triple Model System)</p>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return {"status": "active", "ai_provider": "Mistral AI", "model": f"{MISTRAL_MODEL} + {MISTRAL_MODEL_AUDIT} + {MODEL_SUMMARY}", "timestamp": time.time()}

@app.route('/ping')
def ping():
    return "PONG"

# =====================================================================
# ================ دوال المساعدة الجديدة - الإصدار المحسن =============
# =====================================================================

def get_kill_zone_status():
    """تحديد منطقة القتل السعري (Kill Zone) بناءً على توقيت غزة"""
    try:
        gaza_time = datetime.now(GAZA_TIMEZONE)
        hour = gaza_time.hour
        
        if 8 <= hour < 10:
            return "🔵 جلسة آسيا/لندن المتداخلة - بداية السيولة"
        elif 10 <= hour < 13:
            return "🔴 منطقة القتل السعري (لندن 10-13) - سيولة مرتفعة"
        elif 13 <= hour < 15:
            return "🟡 جلسة لندن/نيويورك المتداخلة - تذبذب عالي"
        elif 15 <= hour < 18:
            return "🔴 منطقة القتل السعري (نيويورك 15-18) - سيولة قصوى"
        elif 18 <= hour < 20:
            return "🟠 جلسة نيويورك المتأخرة - تصفية مراكز"
        elif 20 <= hour < 22:
            return "🟣 جلسة ما بعد نيويورك - هدوء نسبي"
        elif 22 <= hour < 24 or 0 <= hour < 8:
            return "🟢 جلسة آسيا/المحيط الهادئ - سيولة منخفضة"
        else:
            return "⚪ خارج مناطق القتل الرئيسية"
    except Exception as e:
        print(f"⚠️ خطأ في تحديد kill zone: {e}")
        return "⚪ غير محدد"

def check_last_minute_status():
    """كشف دقائق الغدر والتلاعب (14, 29, 44, 59)"""
    try:
        gaza_time = datetime.now(GAZA_TIMEZONE)
        minute = gaza_time.minute
        second = gaza_time.second
        
        if minute in [14, 29, 44, 59]:
            if second > 45:
                return f"🔥 حرجة - دقيقة غدر ({minute}:{second}) - تلاعب سيولة"
            else:
                return f"⚠️ تحذير - دقيقة غدر ({minute}) - استعد لتذبذب"
        
        return f"✅ عادية - الدقيقة {minute}"
    except Exception as e:
        print(f"⚠️ خطأ في كشف دقيقة الغدر: {e}")
        return "✅ عادية"

def calculate_seconds_remaining(candle):
    """حساب الثواني المتبقية لإغلاق الشمعة"""
    try:
        gaza_time = datetime.now(GAZA_TIMEZONE)
        current_second = gaza_time.second
        current_minute = gaza_time.minute
        current_hour = gaza_time.hour
        
        seconds_remaining = 60 - current_second
        
        if candle.startswith('S'):
            candle_seconds = int(candle[1:]) if candle[1:].isdigit() else 5
            seconds_remaining = candle_seconds - (current_second % candle_seconds)
            if seconds_remaining == candle_seconds:
                seconds_remaining = 0
                
        elif candle.startswith('M'):
            candle_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            total_seconds_passed = (current_minute % candle_minutes) * 60 + current_second
            seconds_remaining = (candle_minutes * 60) - total_seconds_passed
            
        elif candle.startswith('H'):
            candle_hours = int(candle[1:]) if candle[1:].isdigit() else 1
            total_minutes_passed = (current_hour % candle_hours) * 60 + current_minute
            seconds_remaining = (candle_hours * 3600) - (total_minutes_passed * 60 + current_second)
            
        elif candle.startswith('D'):
            seconds_remaining = (24 * 3600) - (current_hour * 3600 + current_minute * 60 + current_second)
        
        return max(0, seconds_remaining)
    except Exception as e:
        print(f"⚠️ خطأ في حساب الوقت المتبقي: {e}")
        return 60

def get_candle_closing_status(candle):
    """توليد وصف لحالة إغلاق الشمعة"""
    try:
        seconds_remaining = calculate_seconds_remaining(candle)
        percentage_passed = 0
        
        if candle.startswith('S'):
            total = int(candle[1:]) if candle[1:].isdigit() else 5
            percentage_passed = ((total - seconds_remaining) / total) * 100
        elif candle.startswith('M'):
            total = int(candle[1:]) * 60 if candle[1:].isdigit() else 60
            percentage_passed = ((total - seconds_remaining) / total) * 100
        elif candle.startswith('H'):
            total = int(candle[1:]) * 3600 if candle[1:].isdigit() else 3600
            percentage_passed = ((total - seconds_remaining) / total) * 100
        elif candle.startswith('D'):
            total = 24 * 3600
            percentage_passed = ((total - seconds_remaining) / total) * 100
        
        if seconds_remaining < 10:
            return f"⚠️ حرجة جداً - إغلاق وشيك! {seconds_remaining} ثانية متبقية ({percentage_passed:.0f}% اكتمل)"
        elif seconds_remaining < 30:
            return f"⚠️ حرجة - اقتراب الإغلاق: {seconds_remaining} ثانية ({percentage_passed:.0f}% اكتمل)"
        elif seconds_remaining < 60:
            return f"⚡ قريبة من الإغلاق: {seconds_remaining} ثانية ({percentage_passed:.0f}% اكتمل)"
        elif percentage_passed < 20:
            return f"🟢 بداية الشمعة: {seconds_remaining} ثانية متبقية ({percentage_passed:.0f}% اكتمل)"
        else:
            return f"🟡 جارية: {seconds_remaining} ثانية متبقية ({percentage_passed:.0f}% اكتمل)"
    except Exception as e:
        print(f"⚠️ خطأ في حالة إغلاق الشمعة: {e}")
        return f"🟡 جارية: {calculate_seconds_remaining(candle)} ثانية متبقية"

def get_news_impact():
    """تقييم تأثير الأخبار الاقتصادية"""
    try:
        gaza_time = datetime.now(GAZA_TIMEZONE)
        current_hour = gaza_time.hour
        current_minute = gaza_time.minute
        current_time_minutes = current_hour * 60 + current_minute
        
        # أوقات الأخبار عالية التأثير (بتوقيت غزة)
        high_impact_news = [
            (14, 30), (16, 0), (20, 0),  # الفوركس
            (8, 0), (9, 0), (10, 0),      # آسيا
            (2, 30), (4, 0),              # استراليا/نيوزيلندا
            (17, 30),                     # النفط
            (15, 30),                     # مؤشرات أمريكية
        ]
        
        medium_impact_news = [
            (11, 0), (12, 0), (13, 0),    # أوروبا
            (18, 0), (19, 0),             # أمريكا المتأخرة
            (3, 0), (5, 0),               # آسيا
        ]
        
        # فحص الأخبار عالية التأثير
        for news_hour, news_minute in high_impact_news:
            news_time = news_hour * 60 + news_minute
            time_diff = abs(current_time_minutes - news_time)
            
            if time_diff <= 15:
                return {
                    "impact": "🔴 عالي جداً",
                    "multiplier": 3.0,
                    "warning": "⚠️ **تحذير قصوى:** خبر اقتصادي قوي جداً خلال ±15 دقيقة",
                    "status": "خبر نشط - حظر تام"
                }
            elif time_diff <= 30:
                return {
                    "impact": "🔴 عالي",
                    "multiplier": 2.5,
                    "warning": "⚠️ **تحذير:** خبر اقتصادي قوي خلال ±30 دقيقة",
                    "status": "اقتراب خبر - تخفيف الحجم"
                }
            elif time_diff <= 60:
                return {
                    "impact": "🟡 متوسط",
                    "multiplier": 1.8,
                    "warning": "📢 **تنبيه:** خبر اقتصادي خلال ±60 دقيقة",
                    "status": "خبر وشيك - استعداد"
                }
        
        # فحص الأخبار متوسطة التأثير
        for news_hour, news_minute in medium_impact_news:
            news_time = news_hour * 60 + news_minute
            time_diff = abs(current_time_minutes - news_time)
            
            if time_diff <= 30:
                return {
                    "impact": "🟡 متوسط",
                    "multiplier": 1.5,
                    "warning": "📢 **تنبيه:** خبر اقتصادي متوسط التأثير",
                    "status": "خبر قريب - حذر"
                }
            elif time_diff <= 60:
                return {
                    "impact": "🟢 منخفض",
                    "multiplier": 1.2,
                    "warning": "ℹ️ **معلومة:** خبر اقتصادي خفيف",
                    "status": "خبر بعيد - طبيعي"
                }
        
        return {
            "impact": "🟢 منخفض جداً",
            "multiplier": 1.0,
            "warning": "✅ الوضع هادئ - لا أخبار مؤثرة",
            "status": "آمن للتداول"
        }
    except Exception as e:
        print(f"⚠️ خطأ في تقييم الأخبار: {e}")
        return {
            "impact": "🟢 منخفض",
            "multiplier": 1.0,
            "warning": "✅ الوضع آمن",
            "status": "طبيعي"
        }

def calculate_verification_timeframe(candle):
    """حساب فريم التحقق الديناميكي بناءً على فريم التداول"""
    try:
        if not candle:
            return "M15"
        
        if candle.startswith('S'):
            if candle in ['S5', 'S10', 'S15']:
                return "S30"
            else:
                return "M1"
                
        elif candle.startswith('M'):
            candle_value = int(candle[1:]) if candle[1:].isdigit() else 1
            if candle_value <= 5:
                return "M15"
            elif candle_value <= 15:
                return "M30"
            elif candle_value <= 30:
                return "H1"
            else:
                return "H4"
                
        elif candle.startswith('H'):
            candle_value = int(candle[1:]) if candle[1:].isdigit() else 1
            if candle_value <= 1:
                return "H4"
            elif candle_value <= 4:
                return "D1"
            else:
                return "D1"
                
        elif candle.startswith('D'):
            return "D1"
            
        return "M15"
    except Exception as e:
        print(f"⚠️ خطأ في حساب فريم التحقق: {e}")
        return "M15"

def get_candle_category(candle):
    """تصنيف الفريم الزمني"""
    try:
        if candle.startswith('S'):
            seconds = int(candle[1:]) if candle[1:].isdigit() else 5
            return f"فائق السرعة ({seconds} ثانية) - سكالبينج حاد"
        elif candle.startswith('M'):
            minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            if minutes <= 5:
                return f"سريع ({minutes} دقائق) - سكالبينج"
            elif minutes <= 15:
                return f"متوسط سريع ({minutes} دقائق) - إنتراداي"
            else:
                return f"متوسط بطيء ({minutes} دقائق) - سوينج قصير"
        elif candle.startswith('H'):
            hours = int(candle[1:]) if candle[1:].isdigit() else 1
            return f"بطيء ({hours} ساعات) - سوينج"
        elif candle.startswith('D'):
            return "طويل الأمد (يومي) - بوزيشن"
        else:
            return f"غير معروف ({candle})"
    except Exception as e:
        print(f"⚠️ خطأ في تصنيف الفريم: {e}")
        return f"فريم {candle}"

def get_trading_strategy(trade_time):
    """تحديد استراتيجية التداول بناءً على مدة الصفقة"""
    if trade_time == "قصير (1m-15m)":
        return "سكالبينج - Scalping"
    elif trade_time == "متوسط (4h-Daily)":
        return "سوينج - Swing Trading"
    elif trade_time == "طويل (Weekly-Monthly)":
        return "بوزيشن - Position Trading"
    else:
        return f"استراتيجية {trade_time}"

def get_position_sizing(trade_time, news_multiplier=1.0):
    """تحديد حجم الصفقة المناسب"""
    base_size = 1.0
    
    if trade_time == "قصير (1m-15m)":
        base_size = 2.0  # حجم كبير نسبياً
    elif trade_time == "متوسط (4h-Daily)":
        base_size = 1.0  # حجم معتدل
    elif trade_time == "طويل (Weekly-Monthly)":
        base_size = 0.5  # حجم صغير
    
    # تعديل حسب الأخبار
    adjusted_size = base_size / news_multiplier
    
    if adjusted_size >= 1.5:
        return f"كبير (x{adjusted_size:.1f})"
    elif adjusted_size >= 0.8:
        return f"معتدل (x{adjusted_size:.1f})"
    else:
        return f"صغير (x{adjusted_size:.1f})"

def get_spread_estimate(symbol=None):
    """تقدير السبريد للأصل المالي"""
    try:
        if not symbol:
            return 1  # افتراضي
        
        symbol_lower = symbol.lower()
        
        # الفوركس - سبريد منخفض
        if any(x in symbol_lower for x in ['eur', 'usd', 'gbp', 'jpy', 'chf', 'aud', 'cad', 'nzd']):
            if 'otc' in symbol_lower:
                return 1.5  # OTC سبريد أعلى قليلاً
            return 0.8
        
        # المؤشرات - سبريد متوسط
        elif any(x in symbol_lower for x in ['spx', 'dow', 'nasdaq', 'dax', 'cac', 'ftse', 'hang seng', 'nikkei']):
            return 2.0
        
        # السلع - سبريد مرتفع
        elif any(x in symbol_lower for x in ['gold', 'silver', 'oil', 'natural gas']):
            return 3.0
        
        # الأسهم - سبريد متغير
        elif any(x in symbol_lower for x in ['apple', 'amazon', 'google', 'microsoft', 'tesla']):
            return 2.5
        
        return 1.0
    except Exception as e:
        print(f"⚠️ خطأ في تقدير السبريد: {e}")
        return 1.0

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

async def extract_platform_time_from_image(base64_img):
    """استخراج وقت المنصة من الصورة باستخدام Pixtral"""
    try:
        prompt = """
        أنت خبير في قراءة الوقت من صور الشارتات.
        
        مهمتك: استخرج الوقت الظاهر في الصورة بدقة متناهية.
        
        🔍 **تعليمات الاستخراج:**
        1. ابحث عن الوقت في الزاوية السفلية اليسرى أو اليمنى من الشارت
        2. اقرأ الساعة والدقيقة والثانية بدقة
        3. إذا كان الوقت غير ظاهر أو غير واضح، قدره بناءً على الشموع
        
        🎯 **التنسيق المطلوب:**
        الوقت: [HH:MM:SS]
        المصدر: [منصة التداول]
        وضوح: [واضح / غير واضح / مقدر]
        
        أعد فقط الوقت بالتنسيق المطلوب، بدون أي نص إضافي.
        """
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        payload = {
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
            "max_tokens": 100,
            "temperature": 0.0
        }
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=15)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content'].strip()
            
            # استخراج الوقت باستخدام regex
            time_pattern = r'(\d{1,2}):(\d{2}):(\d{2})'
            match = re.search(time_pattern, result)
            
            if match:
                return match.group(0), result
            else:
                return "غير محدد", result
        else:
            return "غير محدد", "غير ظاهر"
            
    except Exception as e:
        print(f"⚠️ خطأ في استخراج وقت المنصة: {e}")
        return "غير محدد", "فشل الاستخراج"

# =====================================================================
# ================ دوال التنظيف وإدارة الذاكرة =======================
# =====================================================================

def cleanup_old_images():
    """تنظيف الصور القديمة التي مضى عليها أكثر من 30 دقيقة"""
    try:
        current_time = time.time()
        deleted_count = 0
        
        for filename in os.listdir(IMAGE_CACHE_DIR):
            filepath = os.path.join(IMAGE_CACHE_DIR, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 1800:  # 30 دقيقة
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                    except Exception as e:
                        print(f"⚠️ خطأ في حذف الملف {filename}: {e}")
        
        if deleted_count > 0:
            print(f"🧹 تم حذف {deleted_count} ملف صورة قديم")
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف الصور القديمة: {e}")

def compress_image(image_path, max_size=MAX_IMAGE_SIZE, quality=IMAGE_QUALITY):
    """ضغط الصورة لتقليل الحجم مع الحفاظ على الجودة"""
    try:
        with Image.open(image_path) as img:
            # تحويل إلى RGB إذا كان RGBA
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
            if not compressed_path.endswith('.jpg'):
                compressed_path = compressed_path + '_compressed.jpg'
            
            img.save(compressed_path, 'JPEG', quality=quality, optimize=True)
            
            original_size = os.path.getsize(image_path) / 1024
            compressed_size = os.path.getsize(compressed_path) / 1024
            
            print(f"📦 تم ضغط الصورة: {original_size:.1f}KB → {compressed_size:.1f}KB")
            
            return compressed_path
    except Exception as e:
        print(f"⚠️ خطأ في ضغط الصورة: {e}")
        return image_path

def encode_image(image_path):
    """تحويل الصورة إلى base64 بشكل صحيح"""
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        return encoded_string
    except Exception as e:
        print(f"❌ خطأ في تشفير الصورة: {e}")
        return None

def clean_repeated_text(text):
    """تنظيف النص من التكرارات وتحسين التنسيق - الإصدار المحسّن"""
    if not text:
        return ""
    
    # إزالة الأسطر المكررة
    lines = text.split('\n')
    unique_lines = []
    seen_lines = set()
    
    for line in lines:
        line_strip = line.strip()
        if line_strip and line_strip not in seen_lines:
            unique_lines.append(line)
            seen_lines.add(line_strip)
        elif not line_strip:
            unique_lines.append(line)
    
    text = '\n'.join(unique_lines)
    
    # إزالة الأقسام المكررة
    patterns = [
        "📊 **التحليل الفني المتقدم**:", "📊 التحليل الفني المتقدم:",
        "🎯 **الإشارة التنفيذية**:", "🎯 الإشارة التنفيذية:",
        "⚠️ **إدارة المخاطر**:", "⚠️ إدارة المخاطر:",
        "📊 **نتائج الفحص الفني**:", "📊 نتائج الفحص الفني:",
        "🎯 **التوصية والتوقعات**:", "🎯 التوصية والتوقعات:",
        "📝 **ملاحظات التحليل**:", "📝 ملاحظات التحليل:"
    ]
    
    for pattern in patterns:
        if text.count(pattern) > 1:
            parts = text.split(pattern)
            text = parts[0] + pattern + parts[-1]
    
    # إزالة التكرارات في التحليل المزدوج
    if "📊 **التحليل الفني المتقدم**:" in text and "📊 التحليل الفني المتقدم:" in text:
        text = text.replace("📊 التحليل الفني المتقدم:", "")
    
    return text

def split_message(text, max_length=4000):
    """تقسيم الرسالة الطويلة إلى أجزاء"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    while len(text) > max_length:
        # محاولة القطع عند نهاية سطر
        split_point = text[:max_length].rfind('\n\n')
        if split_point == -1:
            split_point = text[:max_length].rfind('\n')
        if split_point == -1:
            split_point = text[:max_length].rfind('. ')
        if split_point == -1:
            split_point = max_length - 100
        
        parts.append(text[:split_point])
        text = text[split_point:].lstrip()
    
    if text:
        parts.append(text)
    
    return parts

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
        
        # تنظيف user_data
        keys_to_remove = [
            'dual_images', 'dual_image_paths', 'dual_analysis_mode',
            'last_analysis', 'dual_analysis_start', 'original_paths',
            'last_recommendation_symbol', 'vision_extracted_data',
            'audit_corrections', 'optimized_result'
        ]
        
        for key in keys_to_remove:
            if key in context.user_data:
                del context.user_data[key]
                
        print(f"✅ تم تنظيف الذاكرة والملفات للمستخدم {user_id}")
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف الذاكرة: {e}")

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

async def periodic_cleanup():
    """تنظيف دوري للملفات المؤقتة"""
    while True:
        try:
            await asyncio.sleep(1800)  # كل 30 دقيقة
            cleanup_old_images()
            cleanup_old_database_records()
            print("🧹 تم التنظيف الدوري للملفات المؤقتة")
        except Exception as e:
            print(f"⚠️ خطأ في التنظيف الدوري: {e}")

# =====================================================================
# ================ قاعدة البيانات ====================================
# =====================================================================

def init_db():
    """تهيئة قاعدة البيانات"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # جدول المستخدمين
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            candle TEXT DEFAULT 'M1', 
            trade_time TEXT DEFAULT 'قصير (1m-15m)',
            chat_context TEXT DEFAULT '',
            last_analysis_context TEXT DEFAULT '',
            last_analysis_time DATETIME DEFAULT NULL,
            preferred_symbols TEXT DEFAULT ''
        )
    ''')
    
    # جدول سجل الدردشة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # جدول سجل التحليلات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analysis_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT,
            candle TEXT,
            trade_time TEXT,
            analysis TEXT,
            confidence INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Database initialized")

def save_user_setting(user_id, col, val):
    """حفظ إعدادات المستخدم"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(f"INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (val, user_id))
    conn.commit()
    conn.close()

def get_user_setting(user_id):
    """الحصول على إعدادات المستخدم"""
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
    summary = analysis_text[:500]  # حفظ أول 500 حرف فقط
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
            try:
                time_diff = (datetime.now() - datetime.fromisoformat(context_time)).total_seconds() / 60
                if time_diff > 10:  # أقدم من 10 دقائق
                    return "", None
            except:
                pass
        return context, context_time
    
    return "", None

def save_last_recommendation_symbol(context: ContextTypes.DEFAULT_TYPE, symbol: str):
    """حفظ آخر رمز تم اختياره في التوصيات"""
    context.user_data['last_recommendation_symbol'] = symbol

def get_last_recommendation_symbol(context: ContextTypes.DEFAULT_TYPE):
    """الحصول على آخر رمز تم اختياره في التوصيات"""
    return context.user_data.get('last_recommendation_symbol', 'EUR/USD (OTC)')

def save_analysis_history(user_id, symbol, candle, trade_time, analysis, confidence):
    """حفظ سجل التحليل"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO analysis_history (user_id, symbol, candle, trade_time, analysis, confidence) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, symbol, candle, trade_time, analysis[:1000], confidence)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ خطأ في حفظ سجل التحليل: {e}")

# =====================================================================
# ================ سحب الصور من TradingView ==========================
# =====================================================================

def download_chart_image(symbol="BTCUSDT"):
    """سحب صورة شارت من TradingView"""
    try:
        API_KEY = "c94425"  # مفتاح ScreenshotMachine
        
        # تحويل اسم الرمز إلى تنسيق TradingView
        symbol_map = {
            # أزواج العملات
            "EUR/USD (OTC)": "FX:EURUSD",
            "GBP/USD (OTC)": "FX:GBPUSD",
            "USD/JPY (OTC)": "FX:USDJPY",
            "USD/CHF (OTC)": "FX:USDCHF",
            "AUD/USD (OTC)": "FX:AUDUSD",
            "USD/CAD (OTC)": "FX:USDCAD",
            "NZD/USD (OTC)": "FX:NZDUSD",
            "EUR/GBP (OTC)": "FX:EURGBP",
            "EUR/JPY (OTC)": "FX:EURJPY",
            "GBP/JPY (OTC)": "FX:GBPJPY",
            "EUR/CHF (OTC)": "FX:EURCHF",
            "AUD/JPY (OTC)": "FX:AUDJPY",
            "EUR/AUD (OTC)": "FX:EURAUD",
            "EUR/CAD (OTC)": "FX:EURCAD",
            "GBP/AUD (OTC)": "FX:GBPAUD",
            "CAD/JPY (OTC)": "FX:CADJPY",
            "CHF/JPY (OTC)": "FX:CHFJPY",
            "NZD/JPY (OTC)": "FX:NZDJPY",
            "GBP/CHF (OTC)": "FX:GBPCHF",
            "AUD/CAD (OTC)": "FX:AUDCAD",
            
            # المؤشرات
            "S&P 500 (OTC)": "SP:SPX",
            "Dow Jones (OTC)": "DJ:DJI",
            "Nasdaq 100 (OTC)": "NASDAQ:NDX",
            "DAX 40 (OTC)": "GER40",
            "CAC 40 (OTC)": "FRA40",
            "FTSE 100 (OTC)": "UK100",
            "Hang Seng (OTC)": "HK50",
            "Nikkei 225 (OTC)": "JPN225",
            
            # السلع
            "Gold (OTC)": "TVC:GOLD",
            "Silver (OTC)": "TVC:SILVER",
            "UKOIL (OTC)": "TVC:UKOIL",
            "USOIL (OTC)": "TVC:USOIL",
            "Natural Gas (OTC)": "TVC:NATGAS",
            
            # الأسهم
            "Apple (OTC)": "NASDAQ:AAPL",
            "Amazon (OTC)": "NASDAQ:AMZN",
            "Google (OTC)": "NASDAQ:GOOGL",
            "Facebook (OTC)": "NASDAQ:META",
            "Microsoft (OTC)": "NASDAQ:MSFT",
            "Tesla (OTC)": "NASDAQ:TSLA",
            "Netflix (OTC)": "NASDAQ:NFLX",
            "Intel (OTC)": "NASDAQ:INTC",
            "Boeing (OTC)": "NYSE:BA",
            "Visa (OTC)": "NYSE:V",
            "McDonald's (OTC)": "NYSE:MCD",
            "Pfizer (OTC)": "NYSE:PFE",
            "Coca-Cola (OTC)": "NYSE:KO",
            "Disney (OTC)": "NYSE:DIS",
            "Alibaba (OTC)": "NYSE:BABA",
            "Walmart (OTC)": "NYSE:WMT"
        }
        
        chart_symbol = symbol_map.get(symbol, symbol.replace(" (OTC)", ""))
        
        # بناء URL الشارت
        CHART_URL = f"https://www.tradingview.com/chart/?symbol={chart_symbol}&interval=5"
        
        # URL API سكرين شوت
        api_url = f"https://api.screenshotmachine.com/?key={API_KEY}&url={CHART_URL}&dimension=800x600&device=desktop&delay=3000&format=jpg"
        
        print(f"📥 جاري سحب صورة لـ {symbol}...")
        
        response = requests.get(api_url, timeout=30)
        
        if response.status_code == 200:
            timestamp = int(time.time())
            safe_symbol = symbol.replace(" ", "_").replace("/", "_").replace("(", "").replace(")", "")
            image_path = os.path.join(IMAGE_CACHE_DIR, f"chart_{safe_symbol}_{timestamp}.jpg")
            
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

# =====================================================================
# ================ نظام التوصية وتحليل الشارت ========================
# =====================================================================

async def analyze_chart_image_triple_model(update, context, image_path, candle, trade_time, symbol):
    """
    تحليل صورة الشارت باستخدام النظام الثلاثي:
    1. Pixtral: استخراج البيانات البصرية
    2. Mistral Large: تدقيق وتطبيق هرم السلطة
    3. Mistral Medium: تحسين وتنسيق النتيجة
    """
    try:
        user_id = update.effective_user.id
        prev_context, prev_time = get_analysis_context(user_id)
        
        # ضغط الصورة
        compressed_path = compress_image(image_path)
        
        # تشفير الصورة
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            return "❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة."
        
        # ============ المرحلة 1: استخراج البيانات البصرية ============
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data.get('wait_msg_id'),
            text="🔍 **المرحلة 1/3:** استخراج البيانات البصرية من الصورة..."
        )
        
        # استخراج وقت المنصة
        platform_time, platform_time_raw = await extract_platform_time_from_image(base64_img)
        
        # ============ جمع جميع المتغيرات للبرومبت ============
        
        # معلومات السوق
        gaza_time = datetime.now(GAZA_TIMEZONE)
        session_name = get_market_session()[0] if callable(get_market_session) else "جلسة عالمية"
        session_time = get_market_session()[1] if callable(get_market_session) else "غير محددة"
        session_vol = get_market_session()[2] if callable(get_market_session) else "متوسطة"
        
        # المتغيرات الجديدة
        kill_zone_status = get_kill_zone_status()
        last_minute_status = check_last_minute_status()
        seconds_remaining = calculate_seconds_remaining(candle)
        candle_closing_status = get_candle_closing_status(candle)
        news_data = get_news_impact()
        verification_timeframe = calculate_verification_timeframe(candle)
        candle_category = get_candle_category(candle)
        trading_strategy = get_trading_strategy(trade_time)
        position_sizing = get_position_sizing(trade_time, news_data['multiplier'])
        spread_estimate = get_spread_estimate(symbol)
        
        # سياق التحليل السابق
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
        
        # ============ المرحلة 2: البرومبت الرئيسي (Pixtral) ============
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data.get('wait_msg_id'),
            text="🧠 **المرحلة 2/3:** تطبيق التحليل الفني المتقدم..."
        )
        
        # برومبت الرؤية - استخراج البيانات البصرية
        VISION_PROMPT = f"""
        🚨 **مهمة الاستخراج البصري الدقيق (Visual Data Extraction):**
        
        أنت خبير في قراءة وتحليل الشارتات. مهمتك هي استخراج البيانات التالية من الصورة بدقة متناهية:
        
        📊 **البيانات المطلوبة من الصورة:**
        
        1. **الوقت على المنصة:** [HH:MM:SS] - ابحث في الزاوية السفلية
        2. **السعر الحالي:** [رقم دقيق] - اقرأ من المحور Y مقابل آخر شمعة
        3. **آخر 3 شموع:** 
           - الشمعة -1: [فتح، إغلاق، أعلى، أدنى، حجم الجسم، حجم الذيل العلوي، حجم الذيل السفلي]
           - الشمعة -2: [فتح، إغلاق، أعلى، أدنى، حجم الجسم، حجم الذيل العلوي، حجم الذيل السفلي]
           - الشمعة -3: [فتح، إغلاق، أعلى، أدنى، حجم الجسم، حجم الذيل العلوي، حجم الذيل السفلي]
        
        4. **مستويات الدعم/المقاومة:** [أدخل المستويات المرئية]
        5. **القمم والقيعان:** [آخر قمة، آخر قاع]
        6. **الفجوات السعرية (FVG):** [إن وجدت]
        7. **الوقت المتبقي للشمعة:** [احسب من وقت المنصة وفريم {candle}]
        
        🎯 **التنسيق المطلوب - JSON:**
        {{
          "platform_time": "HH:MM:SS",
          "current_price": 0.00000,
          "candles": {{
            "candle_1": {{"open": 0.0, "close": 0.0, "high": 0.0, "low": 0.0, "body_percent": 0, "upper_wick": 0, "lower_wick": 0}},
            "candle_2": {{...}},
            "candle_3": {{...}}
          }},
          "support_levels": [0.0, 0.0],
          "resistance_levels": [0.0, 0.0],
          "last_high": 0.0,
          "last_low": 0.0,
          "fvgs": [],
          "seconds_remaining": 0
        }}
        
        ⚠️ **ملاحظة هامة:** أعد JSON فقط، بدون أي نص إضافي أو شرح.
        """
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        vision_payload = {
            "model": MISTRAL_MODEL,
            "messages": [
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}", "detail": "high"}}
                    ]
                }
            ],
            "max_tokens": 910,
            "temperature": 0.0,
            "top_p": 1.0,
            "random_seed": 42
        }
        
        vision_response = requests.post(MISTRAL_URL, headers=headers, json=vision_payload, timeout=45)
        
        if vision_response.status_code != 200:
            print(f"❌ خطأ في الاستخراج البصري: {vision_response.status_code}")
            vision_data = {}
        else:
            try:
                vision_text = vision_response.json()['choices'][0]['message']['content'].strip()
                # محاولة استخراج JSON من النص
                json_match = re.search(r'\{.*\}', vision_text, re.DOTALL)
                if json_match:
                    vision_data = json.loads(json_match.group())
                else:
                    vision_data = {}
            except Exception as e:
                print(f"⚠️ خطأ في تحليل JSON: {e}")
                vision_data = {}
        
        # ============ المرحلة 3: برومبت التدقيق والتحليل المنطقي ============
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data.get('wait_msg_id'),
            text="🔬 **المرحلة 3/3:** تدقيق وتطبيق هرم السلطة..."
        )
        
        # برومبت التدقيق - Mistral Large
        AUDIT_PROMPT = f"""
        🚀 **نظام التدقيق الذكي - تطبيق هرم السلطة (Power Hierarchy):**
        
        📊 **البيانات المستخرجة من الصورة:**
        {json.dumps(vision_data, indent=2, ensure_ascii=False)}
        
        📋 **المتغيرات البرمجية المستلمة:**
        • فريم التداول: {candle}
        • فريم التحقق: {verification_timeframe}
        • استراتيجية: {trading_strategy}
        • حجم الصفقة: {position_sizing}
        • جلسة السوق: {session_name}
        • حالة السيولة: {session_vol}
        • منطقة القتل: {kill_zone_status}
        • دقيقة الغدر: {last_minute_status}
        • الوقت المتبقي: {seconds_remaining} ثانية
        • حالة الشمعة: {candle_closing_status}
        • تأثير الأخبار: {news_data['impact']} (معامل ×{news_data['multiplier']})
        • {news_data['warning']}
        • السبريد التقديري: {spread_estimate} نقطة
        
        {previous_context_info}
        
        🎯 **هرم السلطة النهائي (Priority Protocol):**
        
        **المستوى 1 - فلاتر الحظر المطلق:**
        1. ✅ **فلتر المسافة الحقيقية:** إذا كانت المسافة بين السعر والهدف < 5 نقاط → القرار = احتفاظ 🟡
        2. ✅ **فلتر السبريد:** إذا كانت المسافة الصافية < 3 نقاط → القرار = احتفاظ 🟡
        3. ✅ **فلتر الأخبار:** إذا كانت شمعة الخبر > 300% من المتوسط → حظر لمدة 3 شموع
        4. ✅ **فلتر الدقيقة الأخيرة:** إذا كانت الثواني المتبقية < 10 → حظر الدخول
        
        **المستوى 2 - الزخم (Momentum):**
        • **قاعدة 3 شموع:** إذا كانت آخر 3 شموع بنفس اللون وأجسام >80% → الزخم يلغي الهيكل
        • **الاندفاع المؤسسي:** شمعة ماروبوزو بحجم >200% من المتوسط → استمرار الاتجاه
        
        **المستوى 3 - السيولة (Liquidity):**
        • **سحب السيولة:** هل حدث Sweep للقمة/القاع السابق؟
        • **القمم المتساوية:** هل هناك Equal Highs/Lows؟
        
        **المستوى 4 - الفتيلة (Wick):**
        • **نسبة الذيل:** هل >60%؟
        • **تأكيد الابتلاع:** هل الشمعة التالية ابتلعت 50% من الجسم؟
        
        **المستوى 5 - الهيكل (Structure):**
        • BOS/CHoCh على فريم التحقق {verification_timeframe}
        • مناطق العرض/الطلب
        
        **المستوى 6 - المؤشرات (Indicators):**
        • تأتي في المرتبة الأخيرة عند التعارض
        
        🎯 **المطلوب - تحليل متكامل:**
        
        1. **تطبيق هرم السلطة** - ابدأ من المستوى 1 إلى 6
        2. **حساب المسافة الحقيقية:** 
           - السعر الحالي: {vision_data.get('current_price', 'غير محدد')}
           - الهدف المقترح: [حدد الهدف]
           - المسافة: X نقطة
           - المسافة الصافية: X - ({spread_estimate} × 2) = Y نقطة
           - قرار المسافة: [مقبول / غير مقبول]
        
        3. **تحديد سبب القرار الرئيسي** - بناءً على أعلى مستوى في الهرم
        
        4. **حساب الثقة:** 
           - أساس 70 + نقاط القوة - نقاط الضعف
           - نقاط القوة: زخم +15، سيولة +10، فتيلة +5، هيكل +5
           - نقاط الضعف: تعارض -10، أخبار -15، مسافة صغيرة -20
        
        📊 **التنسيق المطلوب للإجابة:**
        
        📊 **التحليل الفني المتقدم لـ {symbol}:**
        
        **🛡️ فلاتر الحظر:**
        • فلتر المسافة: [مقبول / غير مقبول] - المسافة: [X] نقطة
        • فلتر السبريد: [مقبول / غير مقبول] - المسافة الصافية: [Y] نقطة
        • فلتر الأخبار: {news_data['status']}
        • فلتر الدقيقة الأخيرة: {last_minute_status}
        
        **⚡ هرم السلطة المطبق:**
        • المستوى 1 (فلاتر): [تم التجاوز / سبب المنع]
        • المستوى 2 (زخم): [قوي / متوسط / ضعيف]
        • المستوى 3 (سيولة): [موجودة / غير موجودة]
        • المستوى 4 (فتيلة): [نعم/لا - نسبة: X% - تأكيد: نعم/لا]
        • المستوى 5 (هيكل): [صاعد/هابط/جانبي]
        
        **🎯 الإشارة التنفيذية:**
        • القرار: (شراء 🟢 / بيع 🔴 / احتفاظ 🟡)
        • سبب القرار الرئيسي: [السبب حسب أعلى مستوى]
        • قوة الإشارة: [عالية 💥 / متوسطة ⚡ / ضعيفة ❄️]
        • نقطة الدخول: [السعر الدقيق]
        • TP1: [الهدف الأول]
        • TP2: [الهدف الثاني]
        • SL: [وقف الخسارة]
        
        **⚠️ إدارة المخاطر:**
        • الثقة: [0-100]%
        • نقطة الإلغاء: [السعر]
        • فريم التحقق: {verification_timeframe}
        
        **📝 ملاحظات التحليل:**
        • {kill_zone_status}
        • {candle_closing_status}
        • وقت المنصة المستخرج: {platform_time}
        • توقيت غزة: {gaza_time.strftime('%H:%M:%S')}
        """
        
        audit_payload = {
            "model": MISTRAL_MODEL_AUDIT,
            "messages": [
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": AUDIT_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}", "detail": "low"}}
                    ]
                }
            ],
            "max_tokens": 910,
            "temperature": 0.1,
            "top_p": 1.0,
            "random_seed": 42
        }
        
        audit_response = requests.post(MISTRAL_URL, headers=headers, json=audit_payload, timeout=45)
        
        if audit_response.status_code == 200:
            audit_result = audit_response.json()['choices'][0]['message']['content'].strip()
        else:
            print(f"⚠️ خطأ في التدقيق: {audit_response.status_code}")
            audit_result = "⚠️ تعذر إجراء التدقيق. استخدم التحليل الأساسي."
        
        # ============ المرحلة 4: التحسين والتنسيق النهائي ============
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data.get('wait_msg_id'),
            text="✨ **اللمسات النهائية:** تنسيق النتيجة..."
        )
        
        # برومبت التحسين - Mistral Medium
        OPTIMIZE_PROMPT = f"""
        أنت خبير في تنسيق وتحسين تقارير التحليل الفني.
        
        مهمتك: تحسين وتنسيق التحليل التالي ليكون واضحاً ومنظماً وجذاباً.
        
        **التحليل الأولي:**
        {audit_result}
        
        **تعليمات التنسيق:**
        1. استخدم الرموز التعبيرية المناسبة 📊 🎯 ⚠️
        2. نظم المعلومات في أقسام واضحة
        3. أزل أي تكرار أو نص غير ضروري
        4. تأكد من وضوح القرار النهائي
        5. أضف السطر الأخير: "━━━━━━━━━━━━━━━━\n🤖 **Powered by - Obeida Trading**"
        
        أعد التحليل المحسن فقط، بدون شرح إضافي.
        """
        
        optimize_payload = {
            "model": MODEL_SUMMARY,
            "messages": [{"role": "user", "content": OPTIMIZE_PROMPT}],
            "max_tokens": 910,
            "temperature": 0.2
        }
        
        optimize_response = requests.post(MISTRAL_URL, headers=headers, json=optimize_payload, timeout=30)
        
        if optimize_response.status_code == 200:
            optimized_result = optimize_response.json()['choices'][0]['message']['content'].strip()
        else:
            optimized_result = audit_result
        
        # تنظيف النص من التكرارات
        optimized_result = clean_repeated_text(optimized_result)
        
        # حفظ سياق التحليل
        save_analysis_context(user_id, optimized_result)
        
        # استخراج مستوى الثقة من التحليل
        confidence_match = re.search(r'الثقة:\s*(\d+)%', optimized_result)
        confidence = int(confidence_match.group(1)) if confidence_match else 70
        
        # حفظ سجل التحليل
        save_analysis_history(user_id, symbol, candle, trade_time, optimized_result[:200], confidence)
        
        return optimized_result
        
    except requests.exceptions.Timeout:
        return "⏱️ **تجاوز الوقت المحدد**\nيرجى المحاولة مرة أخرى."
    except Exception as e:
        print(f"❌ خطأ في تحليل الصورة: {traceback.format_exc()}")
        return f"❌ **حدث خطأ في تحليل الصورة:** {str(e)[:200]}"
    finally:
        # تنظيف الملفات المؤقتة
        try:
            if os.path.exists(image_path):
                os.remove(image_path)
            if 'compressed_path' in locals() and os.path.exists(compressed_path) and compressed_path != image_path:
                os.remove(compressed_path)
        except:
            pass

# =====================================================================
# ================ معالجات Telegram ==================================
# =====================================================================

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
        "🤖 **الخدمات المتاحة:**\n"
        "• 📊 **تحليل الشارتات** - نظام ثلاثي المراحل\n"
        "• 📈 **توصيات جاهزة** - مع سحب الصور تلقائياً\n"
        "• 💬 **دردشة ذكية** - مساعد متعدد التخصصات\n"
        "• ⚙️ **إعدادات مخصصة** - ضبط الفريم والمدة\n\n"
        "⬇️ **اختر أحد الخيارات:** ⬇️",
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
            "🕧 **اختر سرعة الشموع:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SETTINGS_CANDLE
    
    elif user_message == "📊 تحليل صورة":
        candle, trade_time, _, _ = get_user_setting(user_id)
        
        if not candle or not trade_time:
            keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
            await update.message.reply_text(
                "❌ **يجب ضبط الإعدادات أولاً**\n\n"
                "الرجاء ضبط سرعة الشموع ومدة الصفقة من قائمة الإعدادات.",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return MAIN_MENU
        else:
            keyboard = [["الرجوع للقائمة الرئيسية"]]
            time_display = format_trade_time_for_prompt(trade_time)
            
            await update.message.reply_text(
                f"📊 **نظام التحليل جاهز**\n\n"
                f"⚙️ **الإعدادات الحالية:**\n"
                f"• 🕧 سرعة الشموع: {candle}\n"
                f"• 📈 استراتيجية: {time_display}\n\n"
                f"📤 **الرجاء إرسال صورة الشارت الآن:**",
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
        "❌ **خيار غير صحيح**\nالرجاء الاختيار من الأزرار:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
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
            "🏠 **العودة للقائمة الرئيسية**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    if user_message in CANDLE_SPEEDS:
        save_user_setting(user_id, "candle", user_message)
        
        keyboard = [TRADE_TIMES[i:i+2] for i in range(0, len(TRADE_TIMES), 2)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        candle_category = get_candle_category(user_message)
        verification = calculate_verification_timeframe(user_message)
        
        await update.message.reply_text(
            f"✅ **تم تعيين سرعة الشموع:** {user_message}\n"
            f"📊 تصنيف: {candle_category}\n"
            f"🔍 فريم التحقق: {verification}\n\n"
            f"⬇️ **الآن حدد مدة الصفقة:** ⬇️",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SETTINGS_TIME
    
    await update.message.reply_text(
        "❌ **الرجاء اختيار سرعة شموع صحيحة**",
        parse_mode="Markdown"
    )
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
            "🏠 **العودة للقائمة الرئيسية**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    if user_message in TRADE_TIMES:
        save_user_setting(user_id, "trade_time", user_message)
        
        candle, _, _, _ = get_user_setting(user_id)
        strategy = get_trading_strategy(user_message)
        position = get_position_sizing(user_message)
        verification = calculate_verification_timeframe(candle)
        
        keyboard = [["📊 تحليل صورة"], ["💬 دردشة"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        
        await update.message.reply_text(
            f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ **الإعدادات النهائية:**\n"
            f"• 🕧 سرعة الشموع: {candle}\n"
            f"• 🔍 فريم التحقق: {verification}\n"
            f"• 📈 استراتيجية: {strategy}\n"
            f"• 💰 حجم الصفقة: {position}\n"
            f"• ⏱️ مدة الصفقة: {user_message}\n\n"
            f"📤 **يمكنك الآن إرسال صورة الشارت للتحليل**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text(
        "❌ **الرجاء اختيار مدة صفقة صحيحة**",
        parse_mode="Markdown"
    )
    return SETTINGS_TIME

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة وضع التحليل"""
    user_message = update.message.text
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"]
        ]
        await update.message.reply_text(
            "🏠 **العودة للقائمة الرئيسية**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text(
        "📤 **الرجاء إرسال صورة الشارت فقط**",
        reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True),
        parse_mode="Markdown"
    )
    return ANALYZE_MODE

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور للتحليل الفني المتقدم"""
    user_id = update.effective_user.id
    candle, trade_time, _, _ = get_user_setting(user_id)
    
    if not candle or not trade_time:
        keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    # رسالة الانتظار
    wait_msg = await update.message.reply_text("📥 **جاري استلام الصورة...**")
    context.user_data['wait_msg_id'] = wait_msg.message_id
    
    # تحميل الصورة
    photo = await update.message.photo[-1].get_file()
    timestamp = int(time.time())
    image_path = os.path.join(IMAGE_CACHE_DIR, f"analysis_{user_id}_{timestamp}.jpg")
    await photo.download_to_drive(image_path)
    
    try:
        # تحليل الصورة بالنظام الثلاثي
        analysis_result = await analyze_chart_image_triple_model(
            update, context, image_path, candle, trade_time, "شارت"
        )
        
        # إرسال النتيجة
        if len(analysis_result) > 4000:
            parts = split_message(analysis_result)
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=wait_msg.message_id,
                text=parts[0],
                parse_mode="Markdown"
            )
            for part in parts[1:]:
                await update.message.reply_text(part, parse_mode="Markdown")
        else:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=wait_msg.message_id,
                text=analysis_result,
                parse_mode="Markdown"
            )
        
        # عرض خيارات المتابعة
        keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات"], ["الرجوع للقائمة الرئيسية"]]
        await update.message.reply_text(
            "🔄 **اختر الإجراء التالي:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        
    except Exception as e:
        print(f"❌ خطأ في معالجة الصورة: {traceback.format_exc()}")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=wait_msg.message_id,
            text=f"❌ **حدث خطأ في التحليل**\n{str(e)[:200]}",
            parse_mode="Markdown"
        )
    finally:
        # تنظيف
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except:
                pass
    
    return MAIN_MENU

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
        text="🚀 **وضع الدردشة - Obeida Trading**\n\n"
             "أنا مساعدك الذكي متعدد المجالات:\n"
             "• 💬 أرسل سؤالك مباشرة\n"
             "• 🎯 اختر تخصصاً من الأزرار\n"
             "• 📊 اسأل عن التحليل الفني\n\n"
             "**جاهز لخدمتك!**",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return CHAT_MODE

async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة رسائل الدردشة"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "ايقاف الدردشة":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "✅ **تم إنهاء وضع الدردشة**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    elif user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 **العودة للقائمة الرئيسية**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    # رسالة الانتظار
    wait_msg = await update.message.reply_text("🤔 **جاري التفكير...**")
    
    try:
        # تحديد البرومبت المناسب
        system_prompt = """أنت Obeida Trading، مساعد ذكي شامل. 
        تمتلك معرفة عميقة في التحليل الفني، التداول، البرمجة، والاستشارات.
        أجب باللغة العربية الفصحى مع لمسة عصرية.
        كن دقيقاً، منظماً، ومفيداً."""
        
        payload = {
            "model": MODEL_SUMMARY,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 2500,
            "temperature": 0.3,
            "top_p": 1.0
        }
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content']
            result = clean_repeated_text(result)
            
            if len(result) > 4000:
                parts = split_message(result)
                await wait_msg.edit_text(parts[0], parse_mode="Markdown")
                for part in parts[1:]:
                    await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(result, parse_mode="Markdown")
        else:
            await wait_msg.edit_text(f"❌ **خطأ:** {response.status_code}")
            
    except Exception as e:
        print(f"❌ خطأ في الدردشة: {e}")
        await wait_msg.edit_text("❌ **حدث خطأ. حاول مرة أخرى.**")
    
    return CHAT_MODE

async def start_recommendation_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع التوصية"""
    reply_keyboard = [[key] for key in CATEGORIES.keys()]
    reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    await update.message.reply_text(
        "🚀 **نظام التوصيات**\n\n"
        "اختر القسم المطلوب:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return RECOMMENDATION_MODE

async def handle_recommendation_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات نظام التوصية"""
    user_text = update.message.text.strip()
    
    if user_text == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        await update.message.reply_text(
            "🏠 **العودة للقائمة الرئيسية**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    if user_text in CATEGORIES:
        keyboard = [[asset] for asset in CATEGORIES[user_text]]
        keyboard.append(["🔙 العودة للقائمة", "الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            f"📍 **قسم: {user_text}**\nاختر العملة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return CATEGORY_SELECTION
    
    # البحث عن العملة المختارة
    symbol_to_analyze = None
    for category_list in CATEGORIES.values():
        if user_text in category_list:
            symbol_to_analyze = user_text
            break
    
    if symbol_to_analyze:
        save_last_recommendation_symbol(context, symbol_to_analyze)
        
        wait_msg = await update.message.reply_text(
            f"⏳ **جاري سحب صورة {symbol_to_analyze}...**"
        )
        context.user_data['wait_msg_id'] = wait_msg.message_id
        
        # سحب الصورة
        chart_image_path = download_chart_image(symbol_to_analyze)
        
        if chart_image_path and os.path.exists(chart_image_path):
            # إرسال الصورة للمستخدم
            with open(chart_image_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"📈 شارت {symbol_to_analyze}"
                )
            
            # الحصول على إعدادات المستخدم
            user_id = update.effective_user.id
            candle, trade_time, _, _ = get_user_setting(user_id)
            
            if not candle or not trade_time:
                await wait_msg.edit_text("❌ **يجب ضبط الإعدادات أولاً**")
                return RECOMMENDATION_MODE
            
            # تحليل الصورة
            analysis_result = await analyze_chart_image_triple_model(
                update, context, chart_image_path, candle, trade_time, symbol_to_analyze
            )
            
            await wait_msg.edit_text(analysis_result, parse_mode="Markdown")
            
            # تنظيف الصورة
            try:
                os.remove(chart_image_path)
            except:
                pass
        else:
            await wait_msg.edit_text(f"❌ **فشل سحب صورة {symbol_to_analyze}**")
    
    # عرض الأزرار للمتابعة
    reply_keyboard = [[key] for key in CATEGORIES.keys()]
    reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    await update.message.reply_text(
        "🔽 **اختر قسماً آخر:**",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return RECOMMENDATION_MODE

async def handle_recommendation_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور في وضع التوصية"""
    user_id = update.effective_user.id
    last_symbol = get_last_recommendation_symbol(context)
    
    wait_msg = await update.message.reply_text(f"📊 **تحليل {last_symbol}...**")
    context.user_data['wait_msg_id'] = wait_msg.message_id
    
    try:
        # تحميل الصورة
        photo = await update.message.photo[-1].get_file()
        timestamp = int(time.time())
        image_path = os.path.join(IMAGE_CACHE_DIR, f"recommendation_{user_id}_{timestamp}.jpg")
        await photo.download_to_drive(image_path)
        
        # الحصول على الإعدادات
        candle, trade_time, _, _ = get_user_setting(user_id)
        
        if not candle or not trade_time:
            await wait_msg.edit_text("❌ **يجب ضبط الإعدادات أولاً**")
            return RECOMMENDATION_MODE
        
        # تحليل الصورة
        analysis_result = await analyze_chart_image_triple_model(
            update, context, image_path, candle, trade_time, last_symbol
        )
        
        await wait_msg.edit_text(analysis_result, parse_mode="Markdown")
        
    except Exception as e:
        print(f"❌ خطأ في تحليل صورة التوصية: {e}")
        await wait_msg.edit_text("❌ **حدث خطأ في معالجة الصورة**")
    
    # عرض خيارات المتابعة
    reply_keyboard = [[key] for key in CATEGORIES.keys()]
    reply_keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    await update.message.reply_text(
        "🔽 **اختر قسماً آخر:**",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return RECOMMENDATION_MODE

# =====================================================================
# ================ معالج الأخطاء ====================================
# =====================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأخطاء في البوت"""
    try:
        error_msg = f"❌ **حدث خطأ في البوت**\n"
        
        if update and hasattr(update, 'effective_user'):
            error_msg += f"المستخدم: {update.effective_user.id}\n"
        
        error_msg += f"الخطأ: {context.error}\n"
        
        # تسجيل الخطأ في ملف
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = ''.join(tb_list)
        
        with open("bot_errors.log", "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"الخطأ: {error_msg}\n")
            f.write(f"Traceback:\n{tb_string}\n")
            f.write(f"{'='*60}\n")
        
        print(f"❌ خطأ مسجل: {context.error}")
        
        # إرسال رسالة للمستخدم
        if update and hasattr(update, 'effective_chat'):
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⚠️ **حدث خطأ تقني. النظام يعمل على إصلاحه.**",
                    parse_mode="Markdown"
                )
            except:
                pass
                
    except Exception as e:
        print(f"❌ خطأ في معالج الأخطاء: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر المساعدة"""
    help_text = """
    🤖 **Obeida Trading - دليل الاستخدام**
    
    **الأوامر:**
    /start - بدء البوت
    /help - عرض المساعدة
    /cancel - إلغاء وإعادة تشغيل
    
    **الخدمات المتاحة:**
    
    📊 **تحليل الشارتات:**
    • نظام ثلاثي المراحل (Pixtral + Audit + Optimize)
    • استخراج دقيق للبيانات من الصورة
    • تطبيق هرم السلطة الفني
    
    📈 **توصيات جاهزة:**
    • 50+ أداة مالية
    • سحب تلقائي للصور من TradingView
    • تحليل فوري
    
    💬 **دردشة ذكية:**
    • مساعد متعدد التخصصات
    • استشارات مالية وبرمجية
    • كتابة وتحليل
    
    ⚙️ **إعدادات مخصصة:**
    • 14 فريم زمني (من S5 إلى D1)
    • 3 استراتيجيات تداول
    • حفظ تلقائي للإعدادات
    
    **تم التطوير بواسطة Obeida Trading** 🤖
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    if update.effective_user:
        cleanup_user_data(context, update.effective_user.id)
    
    await update.message.reply_text(
        "✅ **تم الإلغاء وتنظيف الذاكرة**\n"
        "اكتب /start للبدء من جديد",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# =====================================================================
# ================ دوال السوق المساعدة ==============================
# =====================================================================

def get_market_session():
    """الحصول على جلسة السوق باستخدام توقيت غزة"""
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

# =====================================================================
# ================ Flask Server =====================================
# =====================================================================

def run_flask_server():
    """تشغيل Flask server"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# =====================================================================
# ================ الدالة الرئيسية ==================================
# =====================================================================

def main():
    """الدالة الرئيسية"""
    print("🤖 **Starting Obeida Trading - Triple Model System**")
    
    # تشغيل Flask
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    print(f"🌐 Flask server started on port {os.environ.get('PORT', 8080)}")
    
    # تهيئة قاعدة البيانات
    init_db()
    print("✅ Database initialized")
    
    # تنظيف أولي
    cleanup_old_images()
    cleanup_old_database_records()
    
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
                MessageHandler(filters.PHOTO, handle_photo_analysis)
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
    
    # إضافة المعالجات
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_error_handler(error_handler)
    
    # تشغيل مهمة التنظيف الدورية
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(periodic_cleanup())
    
    print("✅ **Obeida Trading Bot is online!**")
    print("📡 Waiting for messages...")
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
