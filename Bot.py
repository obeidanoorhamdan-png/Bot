import logging
import base64
import os
import sqlite3
import re
import requests
import threading
import time
import traceback
import asyncio
import json
import shutil
import io
import hashlib
import secrets
import feedparser
from datetime import datetime, timedelta
from typing import Dict, List, Set, Tuple, Optional
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telegram.error import NetworkError, TimedOut
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from functools import wraps
from PIL import Image
import pytz
import psutil
import csv

# إعداد التسجيل
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- الإعدادات الأساسية ---
TOKEN = os.environ.get('TOKEN', "8583903565:AAFveu3KYGkPsC8L6WyermoaZ1tI_EtFHHs")

MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "vdzRyRlSfAUr6gX7zOZXVSaQhqRTkGpH")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "pixtral-large-latest"

DB_NAME = "abood-gpt.db"
BACKUP_DIR = "backups"
SUBSCRIPTION_DB_NAME = "subscription_system.db"
NEWS_RADAR_DB = "news_radar.db"
DATA_VERSION = "2.1.0"

ADMIN_ID = 6207431030

SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'Obeida059')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', hashlib.sha256('Obeida059'.encode()).hexdigest())

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

IMAGE_CACHE_DIR = "image_cache"
MAX_IMAGE_SIZE = (1024, 1024)
IMAGE_QUALITY = 95

# حالات المحادثة
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, ANALYZE_MODE, ADMIN_MENU, ADD_SUBSCRIPTION, SELECT_DURATION, REMOVE_USER, BROADCAST_MESSAGE, SET_USER_DURATION, SET_BOT_STATUS, SET_SESSION_CONTROL, TIMEZONE_MENU, SEARCH_COUNTRY = range(14)

# تخزين مؤقت للتحليلات قيد التنفيذ
active_analyses = {}
analysis_lock = threading.Lock()

for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

# ============================================
# أسعار الاشتراكات
# ============================================
SUBSCRIPTION_PRICES = {
    'hourly': {'price': 1, 'currency': '$', 'name': 'ساعة'},
    'daily': {'price': 5, 'currency': '$', 'name': 'يوم'},
    'weekly': {'price': 15, 'currency': '$', 'name': 'أسبوع'},
    'monthly': {'price': 40, 'currency': '$', 'name': 'شهر'},
}

# ============================================
# دوال مساعدة
# ============================================

def compress_image(image_path, max_size=MAX_IMAGE_SIZE, quality=IMAGE_QUALITY):
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
            return compressed_path
    except:
        return image_path

def encode_image(image_path):
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except:
        return None

def clean_repeated_text(text):
    if not text:
        return ""
    lines = text.split('\n')
    unique_lines = []
    for line in lines:
        if line.strip() not in [ul.strip() for ul in unique_lines] or line.strip() == "":
            unique_lines.append(line)
    return '\n'.join(unique_lines)

def split_message(text, max_length=3500):
    if len(text) <= max_length:
        return [text]
    parts = []
    while len(text) > max_length:
        split_point = text[:max_length].rfind('\n')
        if split_point == -1:
            split_point = max_length
        parts.append(text[:split_point])
        text = text[split_point:].lstrip()
    if text:
        parts.append(text)
    return parts

def get_current_session():
    current_hour = datetime.utcnow().hour
    if 0 <= current_hour < 8:
        return {'name': '🌏 الجلسة الآسيوية', 'volatility': 'منخفضة', 'success_rate': '65-70%', 'features': 'حركات هادئة', 'time_range': '00:00-08:00 UTC'}
    elif 8 <= current_hour < 16:
        return {'name': '🇬🇧 جلسة لندن', 'volatility': 'مرتفعة', 'success_rate': '75-85%', 'features': 'سيولة عالية', 'time_range': '08:00-16:00 UTC'}
    elif 13 <= current_hour < 21:
        return {'name': '🗽 جلسة نيويورك', 'volatility': 'عالية جداً', 'success_rate': '80-90%', 'features': 'ذروة السيولة', 'time_range': '13:00-21:00 UTC'}
    else:
        return {'name': '🌊 جلسة المحيط الهادئ', 'volatility': 'منخفضة جداً', 'success_rate': '55-65%', 'features': 'سيولة منخفضة', 'time_range': '21:00-00:00 UTC'}

def get_user_timezone(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT timezone FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result and result[0] else "GMT+02:00"

def set_user_timezone(user_id, timezone):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, timezone) VALUES (?, ?)", (user_id, timezone))
    else:
        cursor.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (timezone, user_id))
    conn.commit()
    conn.close()

def get_localized_time(user_id):
    try:
        tz_str = get_user_timezone(user_id)
        if tz_str.startswith('GMT'):
            offset_str = tz_str.replace('GMT', '')
            if offset_str and offset_str[0] in ('+', '-'):
                hours = int(offset_str.split(':')[0])
                minutes = int(offset_str.split(':')[1]) if ':' in offset_str else 0
                tz = pytz.FixedOffset(hours * 60 + (minutes if hours >= 0 else -minutes))
                return datetime.now(tz)
        return datetime.now(pytz.UTC)
    except:
        return datetime.now()

# ============================================
# نظام الاشتراك
# ============================================

class SubscriptionSystem:
    @staticmethod
    def init_db():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_blocked BOOLEAN DEFAULT 0,
                total_analyses INTEGER DEFAULT 0,
                timezone TEXT DEFAULT 'GMT+02:00'
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS active_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subscription_type TEXT NOT NULL,
                subscription_name TEXT NOT NULL,
                start_date TIMESTAMP NOT NULL,
                end_date TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                price REAL,
                currency TEXT DEFAULT 'USD',
                added_by INTEGER
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT DEFAULT 'active',
                session_control TEXT DEFAULT 'all',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute("SELECT COUNT(*) FROM bot_status")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO bot_status (status, session_control) VALUES ('active', 'all')")
        
        conn.commit()
        conn.close()
        print("✅ تم تهيئة نظام الاشتراك")
    
    @staticmethod
    def register_user(user_id, username=None, first_name=None):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if cursor.fetchone():
                cursor.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP, username = COALESCE(?, username), first_name = COALESCE(?, first_name) WHERE user_id = ?", 
                             (username, first_name, user_id))
            else:
                cursor.execute("INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)", 
                             (user_id, username, first_name))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    @staticmethod
    def add_subscription(user_id, subscription_type, added_by=ADMIN_ID):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            duration_map = {'hourly': 3600, 'daily': 86400, 'weekly': 604800, 'monthly': 2592000}
            duration = duration_map.get(subscription_type, 86400)
            start_date = datetime.now()
            end_date = start_date + timedelta(seconds=duration)
            
            price_info = SUBSCRIPTION_PRICES.get(subscription_type, {'price': 0, 'currency': '$'})
            sub_name = SUBSCRIPTION_PRICES[subscription_type]['name']
            
            cursor.execute("UPDATE active_subscriptions SET is_active = 0 WHERE user_id = ? AND is_active = 1", (user_id,))
            
            cursor.execute("""
                INSERT INTO active_subscriptions (user_id, subscription_type, subscription_name, start_date, end_date, is_active, price, currency, added_by)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """, (user_id, subscription_type, sub_name, start_date.isoformat(), end_date.isoformat(), price_info['price'], price_info['currency'], added_by))
            
            conn.commit()
            return True, {'subscription_name': sub_name, 'end_date': end_date.isoformat(), 'price': price_info['price'], 'currency': price_info['currency']}
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def check_subscription(user_id):
        if user_id == ADMIN_ID:
            return True, "مشرف", None
        
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if result and result[0] == 1:
                return False, "محظور", None
            
            cursor.execute("""
                SELECT subscription_name, end_date FROM active_subscriptions 
                WHERE user_id = ? AND is_active = 1 AND (end_date IS NULL OR end_date > datetime('now'))
                ORDER BY id DESC LIMIT 1
            """, (user_id,))
            
            result = cursor.fetchone()
            if not result:
                return False, "لا يوجد اشتراك نشط", None
            
            return True, result[0], {'end_date': result[1]}
        except:
            return False, "خطأ", None
        finally:
            conn.close()
    
    @staticmethod
    def get_user_info(user_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT user_id, username, first_name, created_at, last_active, is_blocked, total_analyses, timezone FROM users WHERE user_id = ?", (user_id,))
            user_data = cursor.fetchone()
            if not user_data:
                return None
            
            cursor.execute("SELECT subscription_name, end_date FROM active_subscriptions WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1", (user_id,))
            sub_data = cursor.fetchone()
            
            return {
                'user_id': user_data[0],
                'username': user_data[1],
                'first_name': user_data[2],
                'created_at': user_data[3],
                'last_active': user_data[4],
                'is_blocked': user_data[5],
                'total_analyses': user_data[6],
                'timezone': user_data[7] or 'GMT+02:00',
                'subscription': sub_data[0] if sub_data else 'لا يوجد',
                'subscription_end': sub_data[1] if sub_data else None
            }
        except:
            return None
        finally:
            conn.close()
    
    @staticmethod
    def increment_analysis_count(user_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE users SET total_analyses = total_analyses + 1, last_active = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))
            conn.commit()
        except:
            pass
        finally:
            conn.close()
    
    @staticmethod
    def get_remaining_time(user_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT end_date FROM active_subscriptions WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1", (user_id,))
            result = cursor.fetchone()
            if result and result[0]:
                end_date = datetime.fromisoformat(result[0])
                remaining = end_date - datetime.now()
                if remaining.total_seconds() > 0:
                    return remaining
            return None
        except:
            return None
        finally:
            conn.close()
    
    @staticmethod
    def get_stats():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        stats = {}
        try:
            cursor.execute("SELECT COUNT(*) FROM users")
            stats['total_users'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
            stats['blocked_users'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(DISTINCT user_id) FROM active_subscriptions WHERE is_active = 1 AND (end_date IS NULL OR end_date > datetime('now'))")
            stats['active_users'] = cursor.fetchone()[0]
            cursor.execute("SELECT SUM(total_analyses) FROM users")
            stats['total_analyses'] = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COUNT(DISTINCT user_id) FROM users WHERE last_active > datetime('now', '-1 day')")
            stats['active_today'] = cursor.fetchone()[0]
        except:
            pass
        finally:
            conn.close()
        return stats

# ============================================
# دالة التحليل المتقدمة
# ============================================

async def analyze_chart_image(update, context, image_path, candle, trade_time, symbol):
    try:
        user_id = update.effective_user.id
        
        compressed_path = compress_image(image_path)
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            return "❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة."
        
        current_session = get_current_session()
        current_time = datetime.utcnow()
        current_hour = current_time.hour
        current_minute = current_time.minute
        current_second = current_time.second
        
        # حساب الوقت المنقضي والمتبقي
        seconds_remaining = 0
        candle_seconds_total = 0
        seconds_passed = 0
        
        if candle.startswith('S'):
            candle_seconds = int(candle[1:]) if candle[1:].isdigit() else 5
            candle_seconds_total = candle_seconds
            seconds_passed = current_second % candle_seconds
            seconds_remaining = candle_seconds_total - seconds_passed
            if seconds_remaining <= 0:
                seconds_remaining = candle_seconds_total
        elif candle.startswith('M'):
            candle_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            candle_seconds_total = candle_minutes * 60
            total_minutes_since_midnight = current_hour * 60 + current_minute
            candles_passed_count = total_minutes_since_midnight // candle_minutes
            candle_start_minutes = candles_passed_count * candle_minutes
            minutes_into_candle = total_minutes_since_midnight - candle_start_minutes
            seconds_passed = minutes_into_candle * 60 + current_second
            if seconds_passed >= candle_seconds_total:
                seconds_passed = 0
            seconds_remaining = candle_seconds_total - seconds_passed
            if seconds_remaining <= 0:
                seconds_remaining = candle_seconds_total
        elif candle.startswith('H'):
            candle_hours = int(candle[1:]) if candle[1:].isdigit() else 1
            candle_seconds_total = candle_hours * 3600
            total_hours_since_midnight = current_hour
            candles_passed_count = total_hours_since_midnight // candle_hours
            candle_start_hours = candles_passed_count * candle_hours
            hours_into_candle = total_hours_since_midnight - candle_start_hours
            seconds_passed = hours_into_candle * 3600 + current_minute * 60 + current_second
            if seconds_passed >= candle_seconds_total:
                seconds_passed = 0
            seconds_remaining = candle_seconds_total - seconds_passed
            if seconds_remaining <= 0:
                seconds_remaining = candle_seconds_total
        
        # نافذة التنفيذ الذهبية
        def is_in_golden_window(seconds_passed, candle_type, candle_seconds_total):
            if candle_type.startswith('S'):
                return 1 <= seconds_passed <= (candle_seconds_total - 2)
            return 2 <= seconds_passed <= 58
        
        in_golden_window = is_in_golden_window(seconds_passed, candle, candle_seconds_total)
        
        # حساب مستويات فيبوناتشي
        def calculate_fibonacci_levels(high_price, low_price, direction):
            diff = abs(high_price - low_price)
            levels = {}
            if direction == "UP":
                levels["1.000"] = low_price
                levels["0.786"] = low_price + (diff * 0.214)
                levels["0.618"] = low_price + (diff * 0.382)
                levels["0.500"] = low_price + (diff * 0.500)
                levels["0.236"] = low_price + (diff * 0.764)
                levels["0.000"] = high_price
                levels["1.272"] = high_price + (diff * 0.272)
                levels["1.618"] = high_price + (diff * 0.618)
            else:
                levels["1.000"] = high_price
                levels["0.786"] = high_price - (diff * 0.214)
                levels["0.618"] = high_price - (diff * 0.382)
                levels["0.500"] = high_price - (diff * 0.500)
                levels["0.236"] = high_price - (diff * 0.764)
                levels["0.000"] = low_price
                levels["1.272"] = low_price - (diff * 0.272)
                levels["1.618"] = low_price - (diff * 0.618)
            return {k: round(v, 6) for k, v in levels.items()}
        
        # حساب معاملات الصفقة
        def calculate_trade_params(current_price, fib_levels, direction, breakout_force):
            liquidity_safety = breakout_force > 1.2
            if direction == "UP":
                sl = fib_levels.get("1.000", current_price * 0.998)
                tp1 = fib_levels.get("0.500", current_price * 1.01)
                tp2 = fib_levels.get("0.000", current_price * 1.02)
                tp3 = fib_levels.get("1.272", 0) if liquidity_safety else 0
            else:
                sl = fib_levels.get("1.000", current_price * 1.002)
                tp1 = fib_levels.get("0.500", current_price * 0.99)
                tp2 = fib_levels.get("0.000", current_price * 0.98)
                tp3 = fib_levels.get("1.272", 0) if liquidity_safety else 0
            return {"entry": round(current_price, 6), "tp1": round(tp1, 6), "tp2": round(tp2, 6), "tp3": round(tp3, 6), "sl": round(sl, 6)}
        
        # تحديد جلسة السيولة
        def get_session_liquidity(hour):
            if 8 <= hour < 16:
                return "LONDON", "HIGH", "🇬🇧 جلسة لندن - سيولة عالية"
            elif 13 <= hour < 21:
                return "NEW_YORK", "HIGH", "🇺🇸 جلسة نيويورك - سيولة عالية"
            elif 0 <= hour < 8:
                return "ASIA", "LOW", "🇯🇵 جلسة آسيا - سيولة منخفضة"
            return "OFF_HOURS", "LOW", "🌙 خارج الجلسات - سيولة ضعيفة"
        
        session_name, liquidity_level, session_message = get_session_liquidity(current_hour)
        
        # دالة القرار
        def get_trading_decision(current_price, fib_levels, direction, in_golden, breakout, has_fvg, fvg_details, has_ob):
            confidence = 50
            if in_golden:
                confidence += 20
            else:
                confidence -= 10
            if has_fvg:
                confidence += 15
            if has_ob:
                confidence += 10
            if breakout > 1.3:
                confidence += 25
            elif breakout > 1.1:
                confidence += 10
            
            is_discount = current_price < fib_levels.get("0.500", current_price)
            is_premium = current_price > fib_levels.get("0.500", current_price)
            
            if direction == "UP" and is_discount:
                action = "شراء 🟢"
                decision = "BUY"
            elif direction == "DOWN" and is_premium:
                action = "بيع 🔴"
                decision = "SELL"
            else:
                action = "انتظار 🟡"
                decision = "HOLD"
            
            if confidence >= 80:
                signal = "🔥🔥 قوية جداً"
                emoji = "🔥🔥"
            elif confidence >= 65:
                signal = "🔥 قوية"
                emoji = "🔥"
            elif confidence >= 50:
                signal = "⚡ متوسطة"
                emoji = "⚡"
            else:
                signal = "🟡 ضعيفة"
                emoji = "🟡"
            
            success_rate = min(95, max(55, 75 + (confidence - 50) * 0.3))
            
            return {
                "action": action, "decision": decision, "confidence": round(confidence),
                "signal": signal, "emoji": emoji, "success_rate": round(success_rate)
            }
        
        # البرومبت للتحليل
        prompt = f"""
أنت محلل فني محترف. قم بتحليل الشارت المرفق بدقة واستخرج المعلومات التالية:

1. أعلى قمة (Highest High) - رقم
2. أدنى قاع (Lowest Low) - رقم
3. السعر الحالي (Current Price) - رقم
4. مستوى 0.500 فيبوناتشي - رقم
5. قوة الاندفاع (Breakout Force) - رقم عشري (مثل 1.5)
6. اتجاه الموجة (Current Direction) - UP أو DOWN
7. وجود FVG (Fair Value Gap) - True أو False
8. إذا كان FVG=True: مستويات الفجوة (من X إلى Y)
9. وجود Order Block - True أو False
10. وجود Liquidity Sweep - True أو False

الفريم الزمني: {candle}
الوقت المنقضي: {seconds_passed} ثانية
الوقت المتبقي: {seconds_remaining} ثانية

التنسيق المطلوب:
أعلى قمة: [السعر]
أدنى قاع: [السعر]
السعر الحالي: [السعر]
مستوى 0.500: [السعر]
قوة الاندفاع: [الرقم]
اتجاه الموجة: [UP/DOWN]
FVG: [True/False]
مستويات FVG: من [X] إلى [Y] (إذا كان True)
Order Block: [True/False]
Liquidity Sweep: [True/False]
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": MISTRAL_MODEL,
            "messages": [
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                ]}
            ],
            "max_tokens": 800,
            "temperature": 0.0
        }
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=60)
        
        if response.status_code != 200:
            return f"❌ خطأ في التحليل: {response.status_code}"
        
        analysis = response.json()['choices'][0]['message']['content'].strip()
        
        # استخراج البيانات
        hh = re.search(r'أعلى قمة:\s*([\d.]+)', analysis)
        ll = re.search(r'أدنى قاع:\s*([\d.]+)', analysis)
        cp = re.search(r'السعر الحالي:\s*([\d.]+)', analysis)
        fib = re.search(r'0\.500:\s*([\d.]+)', analysis)
        bf = re.search(r'قوة الاندفاع:\s*([\d.]+)', analysis)
        dir_match = re.search(r'اتجاه الموجة:\s*([A-Z]+)', analysis)
        fvg = re.search(r'FVG:\s*(True|False)', analysis, re.IGNORECASE)
        ob = re.search(r'Order Block:\s*(True|False)', analysis, re.IGNORECASE)
        ls = re.search(r'Liquidity Sweep:\s*(True|False)', analysis, re.IGNORECASE)
        fvg_levels = re.search(r'مستويات FVG:\s*من\s*([\d.]+)\s*إلى\s*([\d.]+)', analysis, re.IGNORECASE)
        
        try:
            highest_high = float(hh.group(1)) if hh else 0
            lowest_low = float(ll.group(1)) if ll else 0
            current_price = float(cp.group(1)) if cp else 0
            fib_500 = float(fib.group(1)) if fib else 0
            breakout_force = float(bf.group(1)) if bf else 1.0
            direction = dir_match.group(1).upper() if dir_match else "UP"
            has_fvg = fvg.group(1).upper() == "TRUE" if fvg else False
            has_ob = ob.group(1).upper() == "TRUE" if ob else False
            has_ls = ls.group(1).upper() == "TRUE" if ls else False
            fvg_detail = f"[{fvg_levels.group(1)} إلى {fvg_levels.group(2)}]" if fvg_levels else ""
        except:
            highest_high, lowest_low, current_price, fib_500 = 0, 0, 0, 0
            breakout_force, direction = 1.0, "UP"
            has_fvg, has_ob, has_ls = False, False, False
            fvg_detail = ""
        
        # حساب فيبوناتشي
        fib_levels = {}
        if highest_high > 0 and lowest_low > 0:
            fib_levels = calculate_fibonacci_levels(highest_high, lowest_low, direction)
        
        trade_params = calculate_trade_params(current_price, fib_levels, direction, breakout_force)
        decision = get_trading_decision(current_price, fib_levels, direction, in_golden_window, breakout_force, has_fvg, fvg_detail, has_ob)
        
        # تنسيق النتيجة
        result = f"""
📊 **التحليل الفني المتقدم**

⏱️ **بيانات الوقت:**
• الفريم: {candle}
• الوقت المنقضي: {seconds_passed} ثانية
• الوقت المتبقي: {seconds_remaining} ثانية
• النافذة الذهبية: {'✅ داخل' if in_golden_window else '❌ خارج'}

📐 **المعطيات الرقمية:**
• أعلى قمة: {highest_high}
• أدنى قاع: {lowest_low}
• السعر الحالي: {current_price}
• مستوى 0.500 فيبوناتشي: {fib_500}

⚡ **الزخم والاتجاه:**
• قوة الاندفاع: {breakout_force}x
• اتجاه الموجة: {'🟢 صاعد' if direction == 'UP' else '🔴 هابط'}

🔍 **مؤشرات SMC:**
• FVG: {'✅ موجود' if has_fvg else '❌ غير موجود'} {fvg_detail}
• Order Block: {'✅ موجود' if has_ob else '❌ غير موجود'}
• Liquidity Sweep: {'✅ موجود' if has_ls else '❌ غير موجود'}

🎯 **الإشارة التنفيذية:**
• القرار: {decision['action']}
• نقطة الدخول: {trade_params['entry']}
• الأهداف: TP1={trade_params['tp1']}, TP2={trade_params['tp2']}, TP3={trade_params['tp3'] if trade_params['tp3'] != 0 else 'غير متاح'}
• وقف الخسارة: {trade_params['sl']}

⚠️ **إدارة المخاطر:**
• مستوى الثقة: {decision['confidence']}% {decision['emoji']}
• نسبة النجاح المتوقعة: {decision['success_rate']}%
• قوة الإشارة: {decision['signal']}

━━━━━━━━━━━━━━━
🌍 {session_message}
🟢 {current_time.strftime('%H:%M:%S')} UTC
🤖 **Obeida Trading**
"""
        
        return clean_repeated_text(result)
        
    except Exception as e:
        logger.error(f"خطأ: {traceback.format_exc()}")
        return f"❌ خطأ: {str(e)[:200]}"

# ============================================
# دوال معالجة البوت
# ============================================

async def check_subscription(update: Update, user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    status, msg, _ = SubscriptionSystem.check_subscription(user_id)
    if status:
        return True
    prices = "\n".join([f"• {v['name']}: {v['price']}{v['currency']}" for v in SUBSCRIPTION_PRICES.values()])
    await update.message.reply_text(
        f"⚠️ **عذراً، ليس لديك اشتراك نشط**\n\n"
        f"🔑 **أسعار الاشتراكات:**\n{prices}\n\n"
        f"📞 للاشتراك: @Sz2zv",
        parse_mode="Markdown"
    )
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    SubscriptionSystem.register_user(user.id, user.username, user.first_name)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            candle TEXT DEFAULT 'M1',
            trade_time TEXT DEFAULT 'قصير (1m-15m)',
            timezone TEXT DEFAULT 'GMT+02:00'
        )
    ''')
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, username, candle, trade_time, timezone) VALUES (?, ?, ?, ?, ?)", 
                      (user.id, user.username, "M1", "قصير (1m-15m)", "GMT+02:00"))
    conn.commit()
    conn.close()
    
    status, sub_name, _ = SubscriptionSystem.check_subscription(user.id)
    user_info = SubscriptionSystem.get_user_info(user.id)
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "🛒 الاشتراك"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🌍 المناطق الزمنية", "🔄 تم التحديث"]
    ]
    if user.id == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    
    await update.message.reply_text(
        f"🚀 **أهلاً بك في Obeida Trading**\n\n"
        f"🆔 معرفك: `{user.id}`\n"
        f"📊 حالة الاشتراك: {'✅ ' + sub_name if status else '❌ غير مشترك'}\n"
        f"📈 إجمالي التحليلات: {user_info['total_analyses'] if user_info else 0}\n\n"
        f"📤 أرسل صورة الشارت للتحليل",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    uid = update.effective_user.id
    
    if msg == "🌍 المناطق الزمنية":
        return await timezone_menu(update, context)
    elif msg == "🛒 الاشتراك":
        prices = "\n".join([f"• {v['name']}: {v['price']}{v['currency']}" for v in SUBSCRIPTION_PRICES.values()])
        await update.message.reply_text(
            f"🛒 **متجر الاشتراكات**\n\n💰 الأسعار:\n{prices}\n\n"
            f"📞 للاشتراك: @Sz2zv\n\n"
            f"بعد الدفع، أرسل إيصال الدفع للمشرف",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    elif msg == "🔄 تم التحديث":
        await update.message.reply_text(
            "🔄 **تم تحديث البوت!**\n\n"
            "✨ **الميزات الجديدة:**\n"
            "• تحليل متقدم مع فيبوناتشي\n"
            "• نافذة تنفيذ ذهبية\n"
            "• شريط تقدم متحرك\n"
            "• مؤشرات SMC (FVG, Order Block)\n"
            "• تحليل دقة الوقت\n\n"
            "📤 أرسل صورة للتحليل الآن",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    elif msg == "👑 لوحة التحكم" and uid == ADMIN_ID:
        return await admin_panel(update, context)
    elif msg == "📊 معلومات الجلسة":
        session = get_current_session()
        local_time = get_localized_time(uid)
        await update.message.reply_text(
            f"🌐 **معلومات الجلسة**\n\n"
            f"📍 {session['name']}\n"
            f"📊 التقلب: {session['volatility']}\n"
            f"🎯 نسبة النجاح: {session['success_rate']}\n"
            f"⏰ وقت الجلسة: {session['time_range']}\n"
            f"✨ {session['features']}\n\n"
            f"🕒 وقتك المحلي: {local_time.strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    elif msg == "👤 معلوماتي":
        info = SubscriptionSystem.get_user_info(uid)
        remaining = SubscriptionSystem.get_remaining_time(uid)
        remain_text = f"\n⏳ المدة المتبقية: {remaining.days} يوم {remaining.seconds//3600} ساعة" if remaining else ""
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT candle, trade_time FROM users WHERE user_id = ?", (uid,))
        settings = cursor.fetchone()
        conn.close()
        
        await update.message.reply_text(
            f"👤 **معلوماتك الشخصية**\n\n"
            f"🆔 المعرف: `{uid}`\n"
            f"👤 اليوزر: @{info['username'] if info else 'لا يوجد'}\n"
            f"📈 إجمالي التحليلات: {info['total_analyses'] if info else 0}{remain_text}\n"
            f"⚙️ إعدادات التحليل:\n"
            f"  • سرعة الشموع: `{settings[0] if settings else 'M1'}`\n"
            f"  • مدة الصفقة: `{settings[1] if settings else 'قصير'}`\n"
            f"🌍 منطقتك الزمنية: {info['timezone'] if info else 'GMT+02:00'}",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    elif msg == "⚙️ إعدادات التحليل":
        keyboard = [CANDLE_SPEEDS[i:i+3] for i in range(0, len(CANDLE_SPEEDS), 3)]
        keyboard.append(["🔙 الرجوع للقائمة"])
        await update.message.reply_text(
            "⚙️ **إعدادات التحليل الفني**\n\n"
            "اختر سرعة الشموع المناسبة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return SETTINGS_CANDLE
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "🛒 الاشتراك"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🌍 المناطق الزمنية", "🔄 تم التحديث"]
    ]
    if uid == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    await update.message.reply_text(
        "📊 **اختر من القائمة:**",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return MAIN_MENU

async def handle_settings_candle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    uid = update.effective_user.id
    
    if msg == "🔙 الرجوع للقائمة":
        return await start(update, context)
    
    if msg in CANDLE_SPEEDS:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (uid,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (user_id, username, candle, trade_time) VALUES (?, ?, ?, ?)", 
                          (uid, update.effective_user.username, "M1", "قصير (1m-15m)"))
        cursor.execute("UPDATE users SET candle = ? WHERE user_id = ?", (msg, uid))
        conn.commit()
        conn.close()
        
        keyboard = [TRADE_TIMES[i:i+2] for i in range(0, len(TRADE_TIMES), 2)]
        keyboard.append(["🔙 الرجوع للقائمة"])
        await update.message.reply_text(
            f"✅ **تم تعيين سرعة الشموع:** {msg}\n\n"
            f"⬇️ الآن حدد **مدة الصفقة**:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return SETTINGS_TIME
    
    await update.message.reply_text("❌ الرجاء اختيار سرعة شموع صحيحة.")
    return SETTINGS_CANDLE

async def handle_settings_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    uid = update.effective_user.id
    
    if msg == "🔙 الرجوع للقائمة":
        return await start(update, context)
    
    if msg in TRADE_TIMES:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET trade_time = ? WHERE user_id = ?", (msg, uid))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"✅ **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ مدة الصفقة: {msg}\n\n"
            f"📤 **قم الآن برفع صورة الشارت للتحليل:**",
            reply_markup=ReplyKeyboardMarkup([["🔙 الرجوع للقائمة"]], resize_keyboard=True),
            parse_mode="Markdown"
        )
        return ANALYZE_MODE
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 الرجوع للقائمة":
        return await start(update, context)
    
    if update.message.photo:
        return await handle_photo_analysis(update, context)
    
    await update.message.reply_text(
        "📤 **الرجاء إرسال صورة الشارت للتحليل**\nأو اضغط 'الرجوع للقائمة'",
        reply_markup=ReplyKeyboardMarkup([["🔙 الرجوع للقائمة"]], resize_keyboard=True)
    )
    return ANALYZE_MODE

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await check_subscription(update, user_id):
        return MAIN_MENU
    
    with analysis_lock:
        if user_id in active_analyses and active_analyses[user_id]:
            await update.message.reply_text(
                "⏳ **يوجد تحليل قيد التنفيذ!**\n\n"
                "الرجاء الانتظار حتى انتهاء التحليل الحالي.\n\n"
                "لإيقاف التحليل الحالي، اضغط على /cancel",
                parse_mode="Markdown"
            )
            return MAIN_MENU
        active_analyses[user_id] = True
    
    try:
        wait_msg = await update.message.reply_text(
            "🤌 **جاري تحليل الصورة...** 🤌\n\n"
            "▱▱▱▱▱▱▱▱▱▱ 0%",
            parse_mode="Markdown"
        )
        
        for i in range(1, 11):
            await asyncio.sleep(0.3)
            progress = "█" * i + "▱" * (10 - i)
            try:
                await wait_msg.edit_text(
                    f"🤌 **جاري تحليل الصورة...** 🤌\n\n{progress} {i*10}%",
                    parse_mode="Markdown"
                )
            except:
                pass
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT candle, trade_time FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        candle = result[0] if result and result[0] else "M1"
        trade_time = result[1] if result and result[1] else "قصير (1m-15m)"
        
        photo = await update.message.photo[-1].get_file()
        timestamp = int(time.time())
        image_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}.jpg")
        await photo.download_to_drive(image_path)
        
        compressed_path = compress_image(image_path)
        
        await wait_msg.edit_text(
            f"🤌 **جاري تحليل الصورة...** 🤌\n\n"
            f"██████████ 100%\n\n"
            f"✅ **جارٍ استخراج البيانات...**",
            parse_mode="Markdown"
        )
        
        analysis_result = await analyze_chart_image(update, context, compressed_path, candle, trade_time, "الشارت")
        
        if analysis_result and "خطأ" not in analysis_result:
            SubscriptionSystem.increment_analysis_count(user_id)
            await wait_msg.delete()
            if len(analysis_result) > 4000:
                for part in split_message(analysis_result, 3500):
                    await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await update.message.reply_text(analysis_result, parse_mode="Markdown")
        else:
            await wait_msg.edit_text(analysis_result or "❌ حدث خطأ في التحليل")
        
        for path in [image_path, compressed_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass
                
    except Exception as e:
        logger.error(f"خطأ: {traceback.format_exc()}")
        await update.message.reply_text(f"❌ **حدث خطأ:** {str(e)[:200]}")
    finally:
        with analysis_lock:
            active_analyses[user_id] = False
        
        await update.message.reply_text(
            "✅ **جاهز لاستقبال صور جديدة!**\n\nيمكنك إرسال صورة أخرى للتحليل الآن.",
            parse_mode="Markdown"
        )
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "🛒 الاشتراك"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🌍 المناطق الزمنية", "🔄 تم التحديث"]
    ]
    if user_id == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    
    await update.message.reply_text(
        "📊 **اختر الإجراء التالي:**",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return MAIN_MENU

async def timezone_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    current_tz = get_user_timezone(uid)
    
    timezones = [
        "GMT-12:00", "GMT-11:00", "GMT-10:00", "GMT-09:00", "GMT-08:00",
        "GMT-07:00", "GMT-06:00", "GMT-05:00", "GMT-04:00", "GMT-03:00",
        "GMT-02:00", "GMT-01:00", "GMT+00:00", "GMT+01:00", "GMT+02:00",
        "GMT+03:00", "GMT+03:30", "GMT+04:00", "GMT+04:30", "GMT+05:00",
        "GMT+05:30", "GMT+05:45", "GMT+06:00", "GMT+06:30", "GMT+07:00",
        "GMT+08:00", "GMT+09:00", "GMT+09:30", "GMT+10:00", "GMT+11:00",
        "GMT+12:00", "GMT+13:00", "GMT+14:00"
    ]
    
    keyboard = [[tz] for tz in timezones[:15]]
    keyboard.append(["🔙 الرجوع للقائمة"])
    
    await update.message.reply_text(
        f"🌍 **المناطق الزمنية**\n\n"
        f"🕒 **منطقتك الحالية:** {current_tz}\n\n"
        f"اختر المنطقة الزمنية المناسبة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return TIMEZONE_MENU

async def handle_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    uid = update.effective_user.id
    
    if msg == "🔙 الرجوع للقائمة":
        return await start(update, context)
    
    if msg.startswith("GMT"):
        set_user_timezone(uid, msg)
        local_time = get_localized_time(uid)
        await update.message.reply_text(
            f"✅ **تم تعيين منطقتك الزمنية بنجاح!**\n\n"
            f"🕒 **المنطقة:** {msg}\n"
            f"⏰ **الوقت المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="Markdown"
        )
    
    return await start(update, context)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return MAIN_MENU
    
    stats = SubscriptionSystem.get_stats()
    keyboard = [
        ["👥 عرض المستخدمين", "➕ إضافة اشتراك"],
        ["🗑️ إلغاء اشتراك", "📢 إذاعة"],
        ["📊 إحصائيات", "🔙 العودة"]
    ]
    await update.message.reply_text(
        f"👑 **لوحة تحكم المشرف**\n\n"
        f"📊 **الإحصائيات:**\n"
        f"• إجمالي المستخدمين: {stats.get('total_users', 0)}\n"
        f"• المشتركين النشطين: {stats.get('active_users', 0)}\n"
        f"• إجمالي التحليلات: {stats.get('total_analyses', 0)}\n"
        f"• نشط اليوم: {stats.get('active_today', 0)}",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return ADMIN_MENU

async def handle_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    uid = update.effective_user.id
    
    if uid != ADMIN_ID:
        return MAIN_MENU
    
    if msg == "🔙 العودة":
        return await start(update, context)
    
    elif msg == "👥 عرض المستخدمين":
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, first_name, total_analyses, created_at FROM users ORDER BY created_at DESC LIMIT 30")
        users = cursor.fetchall()
        conn.close()
        
        text = "👥 **قائمة المستخدمين:**\n\n"
        for u in users:
            text += f"• `{u[0]}` | {u[1] or u[2] or 'لا اسم'} | تحليلات: {u[3]}\n"
        
        if len(text) > 4000:
            for part in split_message(text, 3500):
                await update.message.reply_text(part, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, parse_mode="Markdown")
        return ADMIN_MENU
    
    elif msg == "➕ إضافة اشتراك":
        await update.message.reply_text(
            "➕ **إضافة اشتراك**\n\n"
            "أرسل معرف المستخدم ونوع الاشتراك:\n"
            "`123456789 monthly`\n\n"
            "الأنواع المتاحة: hourly, daily, weekly, monthly",
            parse_mode="Markdown"
        )
        return ADD_SUBSCRIPTION
    
    elif msg == "🗑️ إلغاء اشتراك":
        await update.message.reply_text(
            "🗑️ **إلغاء اشتراك**\n\nأرسل معرف المستخدم:\n`123456789`",
            parse_mode="Markdown"
        )
        return REMOVE_USER
    
    elif msg == "📢 إذاعة":
        await update.message.reply_text(
            "📢 **إذاعة**\n\nأرسل الرسالة للإذاعة:",
            parse_mode="Markdown"
        )
        return BROADCAST_MESSAGE
    
    elif msg == "📊 إحصائيات":
        stats = SubscriptionSystem.get_stats()
        await update.message.reply_text(
            f"📊 **إحصائيات النظام**\n\n"
            f"👥 إجمالي المستخدمين: {stats.get('total_users', 0)}\n"
            f"✅ المشتركين النشطين: {stats.get('active_users', 0)}\n"
            f"🚫 المحظورين: {stats.get('blocked_users', 0)}\n"
            f"📈 إجمالي التحليلات: {stats.get('total_analyses', 0)}\n"
            f"📅 نشط اليوم: {stats.get('active_today', 0)}",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    return ADMIN_MENU

async def handle_add_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return MAIN_MENU
    
    try:
        parts = update.message.text.split()
        user_id = int(parts[0])
        sub_type = parts[1] if len(parts) > 1 else "monthly"
        
        if sub_type not in SUBSCRIPTION_PRICES:
            sub_type = "monthly"
        
        success, result = SubscriptionSystem.add_subscription(user_id, sub_type, ADMIN_ID)
        
        if success:
            await update.message.reply_text(
                f"✅ **تم إضافة الاشتراك بنجاح!**\n\n"
                f"👤 المستخدم: `{user_id}`\n"
                f"📅 النوع: {result['subscription_name']}\n"
                f"💰 السعر: {result['price']}{result['currency']}\n"
                f"⏰ ينتهي في: {result['end_date'][:16]}",
                parse_mode="Markdown"
            )
            
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"✅ **تم تفعيل اشتراكك!**\n\n"
                         f"📅 نوع الاشتراك: {result['subscription_name']}\n"
                         f"ينتهي في: {result['end_date'][:16]}\n\n"
                         f"أرسل /start لبدء الاستخدام",
                    parse_mode="Markdown"
                )
            except:
                pass
        else:
            await update.message.reply_text(f"❌ فشل: {result}")
            
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)[:200]}")
    
    return ADMIN_MENU

async def handle_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return MAIN_MENU
    
    try:
        user_id = int(update.message.text.strip())
        
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE active_subscriptions SET is_active = 0 WHERE user_id = ? AND is_active = 1", (user_id,))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ تم إلغاء اشتراك المستخدم `{user_id}`", parse_mode="Markdown")
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="⚠️ **تم إلغاء اشتراكك في البوت**\n\nللتواصل مع المشرف: @Sz2zv",
                parse_mode="Markdown"
            )
        except:
            pass
            
    except:
        await update.message.reply_text("❌ صيغة خاطئة. استخدم: `123456789`")
    
    return ADMIN_MENU

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return MAIN_MENU
    
    msg = update.message.text
    
    conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE is_blocked = 0")
    users = cursor.fetchall()
    conn.close()
    
    wait_msg = await update.message.reply_text(f"📢 جاري الإذاعة لـ {len(users)} مستخدم...")
    
    sent = 0
    failed = 0
    
    for u in users:
        try:
            await context.bot.send_message(
                u[0],
                f"📢 **رسالة إدارية**\n\n{msg}\n\n━━━━━━━━━━━━\nObeida Trading",
                parse_mode="Markdown"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    await wait_msg.edit_text(
        f"📊 **نتيجة الإذاعة**\n\n"
        f"✅ تم الإرسال لـ: {sent} مستخدم\n"
        f"❌ فشل الإرسال لـ: {failed} مستخدم"
    )
    
    return ADMIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **أوامر البوت:**\n\n"
        "/start - بدء البوت والعودة للقائمة\n"
        "/help - عرض هذه المساعدة\n"
        "/cancel - إلغاء العملية الحالية\n\n"
        "📤 **لتحليل الشارت:** أرسل صورة مباشرة\n"
        "⚙️ **لضبط الإعدادات:** استخدم قائمة 'إعدادات التحليل'\n"
        "🛒 **للاشتراك:** استخدم قائمة 'الاشتراك'\n\n"
        "📞 للتواصل: @Sz2zv",
        parse_mode="Markdown"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    with analysis_lock:
        active_analyses[uid] = False
    await update.message.reply_text(
        "✅ **تم إلغاء العملية الحالية**\n\nاكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"خطأ: {context.error}")
    print(f"❌ خطأ: {context.error}")

# ============================================
# رادار الأخبار
# ============================================

class ObeidaNewsRadar:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.chat_group_id = '-1003449363659'
        self.db_name = NEWS_RADAR_DB
        self.processed_news: Set[str] = set()
        self.init_db()
        self.load_processed_news()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sent_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guid TEXT UNIQUE,
                title TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    
    def load_processed_news(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT guid FROM sent_news ORDER BY id DESC LIMIT 100")
        for row in cursor.fetchall():
            self.processed_news.add(row[0])
        conn.close()
    
    def send_message(self, chat_id: str, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
            return True
        except:
            return False
    
    async def process_news(self):
        try:
            urls = [
                'https://sa.investing.com/rss/news.rss',
                'https://sa.investing.com/rss/economic_calendar.rss',
                'https://sa.investing.com/rss/forex_news.rss',
                'https://sa.investing.com/rss/commodities_news.rss',
            ]
            headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'ar-SA,ar;q=0.9'}
            all_news = {}
            
            for url in urls:
                try:
                    resp = requests.get(url, headers=headers, timeout=15)
                    if resp.status_code == 200:
                        feed = feedparser.parse(resp.content)
                        for news in feed.entries[:5]:
                            nid = news.get('id', news.get('guid', news.title))
                            if nid not in self.processed_news and nid not in all_news:
                                all_news[nid] = news
                except:
                    continue
            
            for nid, news in all_news.items():
                text = f"🚨 <b>خبر اقتصادي جديد</b>\n\n{news.title}\n\n━━━━━━━━━━━━\n⚡ @ObeidaTrading"
                self.send_message(self.chat_group_id, text)
                self.processed_news.add(nid)
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"رادار: {e}")
    
    async def start_radar(self):
        while True:
            try:
                await self.process_news()
            except:
                pass
            await asyncio.sleep(30)

# ============================================
# Flask Server
# ============================================

app = Flask(__name__)
app.secret_key = SECRET_KEY

@app.route('/health')
def health():
    stats = SubscriptionSystem.get_stats()
    return {
        "status": "active",
        "total_users": stats.get('total_users', 0),
        "active_users": stats.get('active_users', 0),
        "timestamp": time.time()
    }

@app.route('/')
def home():
    return jsonify({"status": "Obeida Trading Bot is running", "version": DATA_VERSION})

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ============================================
# التشغيل الرئيسي
# ============================================

async def news_task():
    radar = ObeidaNewsRadar(TOKEN)
    await radar.start_radar()

async def cleanup_task():
    while True:
        await asyncio.sleep(3600)
        if os.path.exists(IMAGE_CACHE_DIR):
            for f in os.listdir(IMAGE_CACHE_DIR):
                try:
                    os.remove(os.path.join(IMAGE_CACHE_DIR, f))
                except:
                    pass
        print("🧹 تم تنظيف الملفات المؤقتة")

def main():
    print("🤖 Starting Obeida Trading Bot...")
    
    SubscriptionSystem.init_db()
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            candle TEXT DEFAULT 'M1',
            trade_time TEXT DEFAULT 'قصير (1m-15m)',
            timezone TEXT DEFAULT 'GMT+02:00'
        )
    ''')
    conn.commit()
    conn.close()
    
    for d in [IMAGE_CACHE_DIR, BACKUP_DIR]:
        os.makedirs(d, exist_ok=True)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    app_bot = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu)],
            SETTINGS_CANDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_candle)],
            SETTINGS_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_time)],
            ANALYZE_MODE: [MessageHandler(filters.PHOTO, handle_photo_analysis), MessageHandler(filters.TEXT & ~filters.COMMAND, handle_analyze_mode)],
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin)],
            ADD_SUBSCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_subscription)],
            REMOVE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_user)],
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)],
            TIMEZONE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timezone)],
        },
        fallbacks=[CommandHandler('cancel', cancel), CommandHandler('start', start)],
        allow_reentry=True
    )
    
    app_bot.add_handler(conv)
    app_bot.add_handler(CommandHandler("help", help_command))
    app_bot.add_handler(CommandHandler("cancel", cancel))
    app_bot.add_error_handler(error_handler)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(news_task())
    loop.create_task(cleanup_task())
    
    print("✅ البوت جاهز للعمل!")
    app_bot.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
