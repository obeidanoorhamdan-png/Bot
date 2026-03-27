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
import hashlib
import secrets
import feedparser
from datetime import datetime, timedelta
from typing import Set, Dict, Any, Optional, Tuple
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for, Response
from functools import wraps
from PIL import Image
import pytz

# --- الإعدادات الأساسية ---
TOKEN = os.environ.get('TOKEN', "8583903565:AAFveu3KYGkPsC8L6WyermoaZ1tI_EtFHHs")
MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "vdzRyRlSfAUr6gX7zOZXVSaQhqRTkGpH")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "pixtral-large-latest"

DB_NAME = "abood-gpt.db"
BACKUP_DIR = "backups"
SUBSCRIPTION_DB_NAME = "subscription_system.db"
DATA_VERSION = "2.1.0"

ADMIN_ID = 6207431030
SECRET_KEY = secrets.token_hex(32)
ADMIN_USERNAME = 'Obeida059'
ADMIN_PASSWORD_HASH = hashlib.sha256('Obeida059'.encode()).hexdigest()

# --- تعريف الأزرار والثوابت ---
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, ANALYZE_MODE, ADMIN_MENU, BROADCAST_MESSAGE, TIMEZONE_MENU, SEARCH_COUNTRY, REGISTRATION_MENU, REGISTER_AFFILIATE, SHOP_MENU, BUY_SUBSCRIPTION, VERIFY_PAYMENT = range(13)

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

# --- إعداد المجلدات ---
IMAGE_CACHE_DIR = "image_cache"
for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

# ============================================
# نظام الاشتراكات والمتجر
# ============================================
SUBSCRIPTION_TYPES = {
    'free': {'name': 'مجاني', 'duration': 0, 'price': 0, 'analyses': 3, 'features': ['basic_analysis']},
    'daily': {'name': 'يوم', 'duration': 86400, 'price': 5, 'analyses': -1, 'features': ['all']},
    'weekly': {'name': 'أسبوع', 'duration': 604800, 'price': 15, 'analyses': -1, 'features': ['all']},
    'monthly': {'name': 'شهر', 'duration': 2592000, 'price': 40, 'analyses': -1, 'features': ['all']},
    'yearly': {'name': 'سنة', 'duration': 31536000, 'price': 150, 'analyses': -1, 'features': ['all']},
    'lifetime': {'name': 'مدى الحياة', 'duration': -1, 'price': 300, 'analyses': -1, 'features': ['all']}
}

class SubscriptionSystem:
    @staticmethod
    def init_db():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        # جدول المستخدمين
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                free_analyses INTEGER DEFAULT 3,
                total_analyses INTEGER DEFAULT 0,
                subscription_type TEXT DEFAULT 'free',
                subscription_end TIMESTAMP,
                affiliate_id TEXT,
                affiliate_verified INTEGER DEFAULT 0,
                timezone TEXT DEFAULT 'GMT+02:00',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # جدول طلبات التسجيل في الوكالة
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                affiliate_id TEXT,
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                UNIQUE(user_id)
            )
        ''')
        
        # جدول طلبات الشراء
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS purchase_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subscription_type TEXT,
                payment_method TEXT,
                payment_reference TEXT,
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                UNIQUE(user_id, subscription_type)
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ تم تهيئة نظام الاشتراكات")

    @staticmethod
    def get_user(user_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id, username, free_analyses, total_analyses, subscription_type, subscription_end, affiliate_id, affiliate_verified, timezone
            FROM users WHERE user_id = ?
        ''', (user_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'user_id': row[0],
                'username': row[1],
                'free_analyses': row[2],
                'total_analyses': row[3],
                'subscription_type': row[4],
                'subscription_end': row[5],
                'affiliate_id': row[6],
                'affiliate_verified': bool(row[7]),
                'timezone': row[8] or 'GMT+02:00'
            }
        return None

    @staticmethod
    def register_user(user_id, username=None, first_name=None):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
        ''', (user_id, username, first_name))
        conn.commit()
        conn.close()

    @staticmethod
    def check_subscription(user_id):
        user = SubscriptionSystem.get_user(user_id)
        if not user:
            return False, 'غير مسجل', {'free_analyses': 3}
        
        # التحقق من الاشتراك المدفوع
        if user['subscription_type'] != 'free' and user['subscription_end']:
            end_date = datetime.fromisoformat(user['subscription_end'])
            if end_date > datetime.now():
                return True, user['subscription_type'], user
        
        # التحقق من التسجيل في الوكالة
        if user['affiliate_verified']:
            return True, 'وكالة', user
        
        # المستخدم المجاني
        if user['free_analyses'] > 0:
            return True, 'مجاني', user
        
        return False, 'انتهت الصلاحية', user

    @staticmethod
    def use_analysis(user_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        user = SubscriptionSystem.get_user(user_id)
        
        if user and user['subscription_type'] != 'free' and user['subscription_end']:
            end_date = datetime.fromisoformat(user['subscription_end'])
            if end_date > datetime.now():
                cursor.execute('''
                    UPDATE users SET total_analyses = total_analyses + 1 WHERE user_id = ?
                ''', (user_id,))
                conn.commit()
                conn.close()
                return True
        
        if user and user['affiliate_verified']:
            cursor.execute('''
                UPDATE users SET total_analyses = total_analyses + 1 WHERE user_id = ?
            ''', (user_id,))
            conn.commit()
            conn.close()
            return True
        
        if user and user['free_analyses'] > 0:
            cursor.execute('''
                UPDATE users SET free_analyses = free_analyses - 1, total_analyses = total_analyses + 1 WHERE user_id = ?
            ''', (user_id,))
            conn.commit()
            conn.close()
            return True
        
        conn.close()
        return False

    @staticmethod
    def add_subscription(user_id, sub_type, admin_id=None):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        if sub_type not in SUBSCRIPTION_TYPES:
            return False, "نوع اشتراك غير صالح"
        
        duration = SUBSCRIPTION_TYPES[sub_type]['duration']
        end_date = None
        if duration > 0:
            end_date = (datetime.now() + timedelta(seconds=duration)).isoformat()
        
        cursor.execute('''
            UPDATE users SET subscription_type = ?, subscription_end = ?, free_analyses = 0 WHERE user_id = ?
        ''', (sub_type, end_date, user_id))
        conn.commit()
        conn.close()
        
        return True, f"تم تفعيل اشتراك {SUBSCRIPTION_TYPES[sub_type]['name']}"

    @staticmethod
    def request_affiliate_registration(user_id, affiliate_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        # التحقق من عدم وجود طلب سابق
        cursor.execute('''
            SELECT id FROM affiliate_requests WHERE user_id = ? AND status = 'pending'
        ''', (user_id,))
        if cursor.fetchone():
            conn.close()
            return False, "لديك طلب قيد المراجعة بالفعل"
        
        cursor.execute('''
            INSERT INTO affiliate_requests (user_id, affiliate_id)
            VALUES (?, ?)
        ''', (user_id, affiliate_id))
        conn.commit()
        conn.close()
        return True, "تم إرسال طلب التسجيل بنجاح"

    @staticmethod
    def get_pending_affiliate_requests():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.id, r.user_id, r.affiliate_id, r.requested_at, u.username, u.first_name
            FROM affiliate_requests r
            LEFT JOIN users u ON r.user_id = u.user_id
            WHERE r.status = 'pending'
            ORDER BY r.requested_at DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        return rows

    @staticmethod
    def verify_affiliate(request_id, admin_id, approve):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT user_id, affiliate_id FROM affiliate_requests WHERE id = ?', (request_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False, "الطلب غير موجود"
        
        user_id, affiliate_id = row
        
        if approve:
            cursor.execute('''
                UPDATE users SET affiliate_id = ?, affiliate_verified = 1, free_analyses = 3 WHERE user_id = ?
            ''', (affiliate_id, user_id))
            cursor.execute('UPDATE affiliate_requests SET status = "approved" WHERE id = ?', (request_id,))
            conn.commit()
            conn.close()
            return True, f"تم الموافقة على تسجيل المستخدم {user_id}"
        else:
            cursor.execute('UPDATE affiliate_requests SET status = "rejected" WHERE id = ?', (request_id,))
            conn.commit()
            conn.close()
            return True, f"تم رفض طلب المستخدم {user_id}"

    @staticmethod
    def request_purchase(user_id, sub_type, payment_method, payment_reference):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO purchase_requests (user_id, subscription_type, payment_method, payment_reference)
            VALUES (?, ?, ?, ?)
        ''', (user_id, sub_type, payment_method, payment_reference))
        conn.commit()
        conn.close()
        return True, "تم إرسال طلب الشراء بنجاح"

    @staticmethod
    def get_pending_purchases():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.id, p.user_id, p.subscription_type, p.payment_method, p.payment_reference, p.requested_at, u.username, u.first_name
            FROM purchase_requests p
            LEFT JOIN users u ON p.user_id = u.user_id
            WHERE p.status = 'pending'
            ORDER BY p.requested_at DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        return rows

    @staticmethod
    def approve_purchase(request_id, admin_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT user_id, subscription_type FROM purchase_requests WHERE id = ?', (request_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False, "الطلب غير موجود"
        
        user_id, sub_type = row
        
        duration = SUBSCRIPTION_TYPES[sub_type]['duration']
        end_date = None
        if duration > 0:
            end_date = (datetime.now() + timedelta(seconds=duration)).isoformat()
        
        cursor.execute('''
            UPDATE users SET subscription_type = ?, subscription_end = ?, free_analyses = 0 WHERE user_id = ?
        ''', (sub_type, end_date, user_id))
        cursor.execute('UPDATE purchase_requests SET status = "approved" WHERE id = ?', (request_id,))
        conn.commit()
        conn.close()
        
        return True, f"تم تفعيل اشتراك {SUBSCRIPTION_TYPES[sub_type]['name']} للمستخدم {user_id}"

    @staticmethod
    def get_all_users():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id, username, first_name, free_analyses, total_analyses, subscription_type, subscription_end, affiliate_id, affiliate_verified, timezone
            FROM users ORDER BY user_id
        ''')
        rows = cursor.fetchall()
        conn.close()
        return rows

    @staticmethod
    def get_stats():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(total_analyses) FROM users")
        total_analyses = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE affiliate_verified = 1")
        affiliate_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_type != 'free'")
        paid_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM affiliate_requests WHERE status = 'pending'")
        pending_affiliate = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM purchase_requests WHERE status = 'pending'")
        pending_purchases = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_users': total_users,
            'total_analyses': total_analyses,
            'affiliate_users': affiliate_users,
            'paid_users': paid_users,
            'pending_affiliate': pending_affiliate,
            'pending_purchases': pending_purchases
        }

# ============================================
# نظام جلسات التداول
# ============================================
TRADING_SESSIONS = {
    'asian': {'name': '🌏 الجلسة الآسيوية', 'start': 0, 'end': 8, 'volatility': 'منخفضة', 'pairs': 'الين الياباني، الدولار الأسترالي', 'success_rate': '65-70%', 'features': 'حركات هادئة', 'time_range': '00:00-08:00 UTC'},
    'london': {'name': '🇬🇧 جلسة لندن', 'start': 8, 'end': 16, 'volatility': 'مرتفعة', 'pairs': 'اليورو، الجنيه، الفرنك', 'success_rate': '75-85%', 'features': 'سيولة عالية', 'time_range': '08:00-16:00 UTC'},
    'newyork': {'name': '🗽 جلسة نيويورك', 'start': 13, 'end': 21, 'volatility': 'عالية جداً', 'pairs': 'الدولار، المؤشرات', 'success_rate': '80-90%', 'features': 'ذروة السيولة', 'time_range': '13:00-21:00 UTC'},
    'pacific': {'name': '🌊 جلسة المحيط الهادئ', 'start': 21, 'end': 24, 'volatility': 'منخفضة جداً', 'pairs': 'جميع الأزواج', 'success_rate': '55-65%', 'features': 'حركات جانبية', 'time_range': '21:00-00:00 UTC'},
}

def get_current_session():
    current_hour = datetime.utcnow().hour
    if 0 <= current_hour < 8:
        return TRADING_SESSIONS['asian']
    elif 8 <= current_hour < 16:
        return TRADING_SESSIONS['london']
    elif 13 <= current_hour < 21:
        return TRADING_SESSIONS['newyork']
    else:
        return TRADING_SESSIONS['pacific']

# ============================================
# دوال قاعدة البيانات الأساسية
# ============================================
def init_main_db():
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
    print("✅ تم تهيئة قاعدة البيانات الرئيسية")

def get_user_setting(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT candle, trade_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        return (res[0], res[1], "", None)
    return ("M1", "قصير (1m-15m)", "", None)

def save_user_setting(user_id, col, val):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (val, user_id))
    conn.commit()
    conn.close()

def get_user_timezone(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT timezone FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else "GMT+02:00"

def set_user_timezone(user_id, tz):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (tz, user_id))
    conn.commit()
    conn.close()

def get_localized_time(user_id):
    try:
        tz_str = get_user_timezone(user_id)
        if tz_str.startswith('GMT'):
            offset_str = tz_str.replace('GMT', '')
            if offset_str and offset_str[0] in ('+', '-'):
                hours = int(offset_str.split(':')[0]) if offset_str.split(':')[0] else 0
                minutes = int(offset_str.split(':')[1]) if ':' in offset_str else 0
                tz = pytz.FixedOffset(hours * 60 + (minutes if hours >= 0 else -minutes))
                return datetime.now(tz)
        return datetime.now(pytz.UTC)
    except:
        return datetime.now()

def compress_image(image_path, max_size=(800, 800), quality=70):
    try:
        with Image.open(image_path) as img:
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'RGBA':
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
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except:
        return None

def split_message(text, max_length=3500):
    parts = []
    while len(text) > max_length:
        split_index = text[:max_length].rfind('\n')
        if split_index == -1:
            split_index = max_length
        parts.append(text[:split_index])
        text = text[split_index:]
    parts.append(text)
    return parts

# ============================================
# دوال المساعدة للبوت
# ============================================
async def check_access(update: Update, user_id: int) -> bool:
    """التحقق من صلاحية المستخدم للتحليل"""
    if user_id == ADMIN_ID:
        return True
    
    SubscriptionSystem.register_user(user_id, update.effective_user.username, update.effective_user.first_name)
    
    status, sub_type, user_data = SubscriptionSystem.check_subscription(user_id)
    
    if status:
        return True
    
    # عرض خيارات التسجيل والشراء
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 تسجيل في الوكالة (مجاني)", callback_data="show_affiliate")],
        [InlineKeyboardButton("🛒 شراء اشتراك", callback_data="show_shop")],
        [InlineKeyboardButton("📊 تجربة مجانية", callback_data="try_free")]
    ])
    
    await update.message.reply_text(
        "⚠️ **ليس لديك صلاحية استخدام البوت** ⚠️\n\n"
        f"📊 **تحليلك المجاني المتبقي:** {user_data['free_analyses'] if user_data else 0}\n\n"
        "🔓 **طرق الحصول على صلاحية:**\n\n"
        "1️⃣ **تسجيل في وكالتنا (مجاني)**\n"
        "   • التسجيل عبر رابط الوكالة\n"
        "   • إرسال معرف العميل\n"
        "   • تفعيل مجاني فوري\n\n"
        "2️⃣ **شراء اشتراك مدفوع**\n"
        f"   • يوم: {SUBSCRIPTION_TYPES['daily']['price']}$\n"
        f"   • أسبوع: {SUBSCRIPTION_TYPES['weekly']['price']}$\n"
        f"   • شهر: {SUBSCRIPTION_TYPES['monthly']['price']}$\n"
        f"   • سنة: {SUBSCRIPTION_TYPES['yearly']['price']}$\n\n"
        "3️⃣ **تجربة مجانية**\n"
        "   • لديك 3 تحليلات مجانية\n\n"
        "اختر إحدى الطرق أدناه:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    return False

async def send_access_denied(update: Update):
    """إرسال رسالة رفض الوصول مع الخيارات"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 تسجيل في الوكالة", callback_data="show_affiliate")],
        [InlineKeyboardButton("🛒 شراء اشتراك", callback_data="show_shop")],
        [InlineKeyboardButton("📊 تحليل مجاني", callback_data="use_free")]
    ])
    
    await update.message.reply_text(
        "⚠️ **ليس لديك صلاحية** ⚠️\n\n"
        "اختر إحدى الطرق للحصول على صلاحية:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# ============================================
# دوال معالجة البوت
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    SubscriptionSystem.register_user(user_id, user.username, user.first_name)
    
    status, sub_type, user_data = SubscriptionSystem.check_subscription(user_id)
    current_session = get_current_session()
    
    welcome_msg = (
        "🚀 **أهلاً بك في Obeida Trading** 🚀\n\n"
        f"🆔 **معرفك:** `{user_id}`\n"
        f"👤 **اليوزر:** @{user.username or 'لا يوجد'}\n\n"
    )
    
    if status:
        if sub_type == 'وكالة':
            welcome_msg += f"✅ **الحالة:** مسجل في الوكالة (مفعل)\n"
        elif sub_type != 'مجاني':
            welcome_msg += f"✅ **الاشتراك:** {SUBSCRIPTION_TYPES[sub_type]['name']}\n"
        else:
            welcome_msg += f"📊 **تحليلات مجانية متبقية:** {user_data['free_analyses']}\n"
    else:
        welcome_msg += f"⚠️ **الحالة:** غير مفعل\n📊 **تحليلات مجانية متبقية:** {user_data['free_analyses']}\n"
    
    welcome_msg += (
        f"\n🌐 **الجلسة الحالية:** {current_session['name']}\n"
        f"📊 **نسبة النجاح:** {current_session['success_rate']}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        "**أنواع القرارات الفنية:**\n"
        "🟢 شراء | 🔴 بيع | 💥 كسر هيكلي | 🔄 سحب سيولة | 🔄 انعكاس فوري\n\n"
        "🤖 **Powered by Obeida Trading**"
    )
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🌍 المناطق الزمنية", "🛒 المتجر"],
        ["📝 تسجيل الوكالة"]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    
    await update.message.reply_text(
        welcome_msg,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "🌍 المناطق الزمنية":
        return await timezone_menu(update, context)
    
    elif user_message == "👑 لوحة التحكم" and user_id == ADMIN_ID:
        return await start_admin_panel(update, context)
    
    elif user_message == "📊 معلومات الجلسة":
        current_session = get_current_session()
        user_tz = get_user_timezone(user_id)
        local_time = get_localized_time(user_id)
        
        info = (
            f"🌐 **معلومات الجلسة**\n\n"
            f"⏰ **UTC:** {datetime.utcnow().strftime('%H:%M:%S')}\n"
            f"🌍 **منطقتك:** {user_tz}\n"
            f"🕒 **وقتك:** {local_time.strftime('%H:%M:%S')}\n\n"
            f"📍 **الجلسة:** {current_session['name']}\n"
            f"📊 **التقلب:** {current_session['volatility']}\n"
            f"🎯 **نسبة النجاح:** {current_session['success_rate']}\n"
            f"💰 **الأزواج:** {current_session['pairs']}\n"
        )
        await update.message.reply_text(info, parse_mode="Markdown")
        return MAIN_MENU
    
    elif user_message == "👤 معلوماتي":
        user_data = SubscriptionSystem.get_user(user_id)
        status, sub_type, _ = SubscriptionSystem.check_subscription(user_id)
        
        info = (
            f"👤 **معلوماتك**\n\n"
            f"🆔 **المعرف:** `{user_id}`\n"
            f"👤 **اليوزر:** @{update.effective_user.username or 'لا يوجد'}\n\n"
        )
        
        if status:
            if sub_type == 'وكالة':
                info += f"✅ **الحالة:** مسجل في الوكالة\n🆔 **معرف الوكالة:** `{user_data['affiliate_id']}`\n"
            elif sub_type != 'مجاني':
                info += f"✅ **الاشتراك:** {SUBSCRIPTION_TYPES[sub_type]['name']}\n"
                if user_data['subscription_end']:
                    end = datetime.fromisoformat(user_data['subscription_end'])
                    info += f"⏳ **ينتهي:** {end.strftime('%Y-%m-%d')}\n"
            else:
                info += f"📊 **تحليلات مجانية:** {user_data['free_analyses']}\n"
        else:
            info += f"⚠️ **الحالة:** غير مفعل\n📊 **تحليلات مجانية:** {user_data['free_analyses']}\n"
        
        info += f"📈 **إجمالي التحليلات:** {user_data['total_analyses']}\n"
        info += f"🌍 **المنطقة:** {user_data['timezone']}\n"
        
        await update.message.reply_text(info, parse_mode="Markdown")
        return MAIN_MENU
    
    elif user_message == "⚙️ إعدادات التحليل":
        keyboard = [CANDLE_SPEEDS[i:i+3] for i in range(0, len(CANDLE_SPEEDS), 3)]
        keyboard.append(["الرجوع"])
        
        await update.message.reply_text(
            "⚙️ **إعدادات التحليل**\n\nاختر سرعة الشموع:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return SETTINGS_CANDLE
    
    elif user_message == "📊 تحليل صورة":
        status, _, _ = SubscriptionSystem.check_subscription(user_id)
        
        if not status:
            await send_access_denied(update)
            return MAIN_MENU
        
        candle, trade_time, _, _ = get_user_setting(user_id)
        
        if not candle:
            await update.message.reply_text(
                "❌ **يجب ضبط الإعدادات أولاً**\nاستخدم زر '⚙️ إعدادات التحليل'",
                parse_mode="Markdown"
            )
            return MAIN_MENU
        
        await update.message.reply_text(
            f"📤 **أرسل صورة الشارت**\n\n"
            f"⚙️ **الإعدادات:** {candle} | {trade_time}",
            reply_markup=ReplyKeyboardMarkup([["الرجوع"]], resize_keyboard=True),
            parse_mode="Markdown"
        )
        return ANALYZE_MODE
    
    elif user_message == "🛒 المتجر":
        return await shop_menu(update, context)
    
    elif user_message == "📝 تسجيل الوكالة":
        return await affiliate_registration_menu(update, context)
    
    elif user_message == "الرجوع":
        return await start(update, context)
    
    return MAIN_MENU

async def shop_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المتجر"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 يوم - 5$", callback_data="buy_daily")],
        [InlineKeyboardButton("📆 أسبوع - 15$", callback_data="buy_weekly")],
        [InlineKeyboardButton("📅 شهر - 40$", callback_data="buy_monthly")],
        [InlineKeyboardButton("📅 سنة - 150$", callback_data="buy_yearly")],
        [InlineKeyboardButton("♾️ مدى الحياة - 300$", callback_data="buy_lifetime")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_menu")]
    ])
    
    await update.message.reply_text(
        "🛒 **متجر الاشتراكات**\n\n"
        "اختر نوع الاشتراك المناسب لك:\n\n"
        f"📅 **يوم:** {SUBSCRIPTION_TYPES['daily']['price']}$ - تحليلات غير محدودة\n"
        f"📆 **أسبوع:** {SUBSCRIPTION_TYPES['weekly']['price']}$ - تحليلات غير محدودة\n"
        f"📅 **شهر:** {SUBSCRIPTION_TYPES['monthly']['price']}$ - تحليلات غير محدودة\n"
        f"📅 **سنة:** {SUBSCRIPTION_TYPES['yearly']['price']}$ - تحليلات غير محدودة\n"
        f"♾️ **مدى الحياة:** {SUBSCRIPTION_TYPES['lifetime']['price']}$ - تحليلات غير محدودة\n\n"
        "💰 **طرق الدفع:**\n"
        "• تحويل بنكي\n"
        "• USDT (TRC20)\n"
        "• باي بال\n\n"
        "بعد الدفع، أرسل إيصال الدفع مع معرف المستخدم الخاص بك للمشرف @Sz2zv",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    return SHOP_MENU

async def affiliate_registration_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة التسجيل في الوكالة"""
    user_id = update.effective_user.id
    user_data = SubscriptionSystem.get_user(user_id)
    
    if user_data and user_data['affiliate_verified']:
        await update.message.reply_text(
            f"✅ **أنت مسجل بالفعل في الوكالة!**\n\n🆔 **معرف الوكالة:** `{user_data['affiliate_id']}`\n\nيمكنك استخدام البوت مجاناً.",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text(
        "📝 **التسجيل في وكالة Tickmill**\n\n"
        "🔗 **رابط التسجيل:**\n"
        "https://my.tickmill.com/en/sign-up/account-type?utm_campaign=ib_link&utm_content=IB96119705_MT\n\n"
        "📌 **خطوات التسجيل:**\n"
        "1️⃣ اضغط على الرابط أعلاه\n"
        "2️⃣ قم بإنشاء حساب جديد\n"
        "3️⃣ بعد الانتهاء، أرسل معرف العميل (Account ID)\n\n"
        "✅ **أرسل معرف العميل الآن:**\n"
        "مثال: `12345678`\n\n"
        "سيتم تفعيل حسابك خلال 24 ساعة",
        parse_mode="Markdown"
    )
    return REGISTER_AFFILIATE

async def handle_affiliate_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إرسال معرف الوكالة"""
    user_id = update.effective_user.id
    affiliate_id = update.message.text.strip()
    
    if not affiliate_id.isdigit() or len(affiliate_id) < 5:
        await update.message.reply_text(
            "❌ **معرف غير صالح**\nالرجاء إرسال معرف الحساب الصحيح (أرقام فقط)",
            parse_mode="Markdown"
        )
        return REGISTER_AFFILIATE
    
    success, msg = SubscriptionSystem.request_affiliate_registration(user_id, affiliate_id)
    
    if success:
        await update.message.reply_text(
            f"✅ {msg}\n\n🆔 **المعرف المرسل:** `{affiliate_id}`\n\nسيتم إعلامك عند التفعيل.",
            parse_mode="Markdown"
        )
        
        # إشعار المشرف
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📝 **طلب تسجيل وكالة جديد**\n\n👤 المستخدم: {user_id}\n🆔 المعرف: `{affiliate_id}`\n\nاستخدم لوحة التحكم للموافقة.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ {msg}", parse_mode="Markdown")
    
    return MAIN_MENU

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    
    if data == "show_affiliate":
        await query.edit_message_text(
            "📝 **التسجيل في الوكالة (مجاني)**\n\n"
            "🔗 **رابط التسجيل:**\n"
            "https://my.tickmill.com/en/sign-up/account-type?utm_campaign=ib_link&utm_content=IB96119705_MT\n\n"
            "📌 **خطوات التسجيل:**\n"
            "1️⃣ اضغط على الرابط أعلاه\n"
            "2️⃣ قم بإنشاء حساب جديد\n"
            "3️⃣ أرسل معرف العميل (Account ID)\n\n"
            "✅ **أرسل المعرف الآن:**",
            parse_mode="Markdown"
        )
        return REGISTER_AFFILIATE
    
    elif data == "show_shop":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 يوم - 5$", callback_data="buy_daily")],
            [InlineKeyboardButton("📆 أسبوع - 15$", callback_data="buy_weekly")],
            [InlineKeyboardButton("📅 شهر - 40$", callback_data="buy_monthly")],
            [InlineKeyboardButton("📅 سنة - 150$", callback_data="buy_yearly")],
            [InlineKeyboardButton("♾️ مدى الحياة - 300$", callback_data="buy_lifetime")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_menu")]
        ])
        
        await query.edit_message_text(
            "🛒 **متجر الاشتراكات**\n\n"
            f"📅 يوم: {SUBSCRIPTION_TYPES['daily']['price']}$\n"
            f"📆 أسبوع: {SUBSCRIPTION_TYPES['weekly']['price']}$\n"
            f"📅 شهر: {SUBSCRIPTION_TYPES['monthly']['price']}$\n"
            f"📅 سنة: {SUBSCRIPTION_TYPES['yearly']['price']}$\n"
            f"♾️ مدى الحياة: {SUBSCRIPTION_TYPES['lifetime']['price']}$\n\n"
            "💰 **طرق الدفع:** تحويل بنكي، USDT، باي بال\n\n"
            "بعد الدفع، أرسل الإيصال للمشرف @Sz2zv",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return SHOP_MENU
    
    elif data.startswith("buy_"):
        sub_type = data.replace("buy_", "")
        if sub_type in SUBSCRIPTION_TYPES:
            price = SUBSCRIPTION_TYPES[sub_type]['price']
            name = SUBSCRIPTION_TYPES[sub_type]['name']
            
            await query.edit_message_text(
                f"🛒 **طلب شراء اشتراك {name}**\n\n"
                f"💰 **السعر:** {price}$\n\n"
                f"📝 **لإتمام الشراء:**\n"
                f"1️⃣ قم بالتحويل إلى:\n"
                f"   • باي بال: obeida@trading.com\n"
                f"   • USDT (TRC20): TXxx...xxx\n\n"
                f"2️⃣ أرسل إيصال الدفع مع معرف المستخدم `{user_id}` للمشرف @Sz2zv\n\n"
                f"3️⃣ سيتم تفعيل اشتراكك خلال 24 ساعة",
                parse_mode="Markdown"
            )
            
            # إشعار المشرف
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🛒 **طلب شراء جديد**\n\n👤 المستخدم: {user_id}\n📦 الاشتراك: {name}\n💰 السعر: {price}$\n⏰ الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                parse_mode="Markdown"
            )
        return
    
    elif data == "use_free":
        user_data = SubscriptionSystem.get_user(user_id)
        if user_data and user_data['free_analyses'] > 0:
            await query.edit_message_text(
                "✅ **يمكنك استخدام تحليل مجاني!**\n\nأرسل /start ثم اختر '📊 تحليل صورة'",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                "❌ **لا يوجد تحليلات مجانية متبقية**\n\nيرجى التسجيل في الوكالة أو شراء اشتراك.",
                parse_mode="Markdown"
            )
    
    elif data == "back_to_menu":
        await query.edit_message_text("🏠 العودة للقائمة الرئيسية")
        return MAIN_MENU

async def handle_settings_candle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع":
        return await start(update, context)
    
    if user_message in CANDLE_SPEEDS:
        save_user_setting(user_id, "candle", user_message)
        
        keyboard = [TRADE_TIMES[i:i+2] for i in range(0, len(TRADE_TIMES), 2)]
        keyboard.append(["الرجوع"])
        
        await update.message.reply_text(
            f"✅ **تم تعيين سرعة الشموع:** {user_message}\n\n⬇️ حدد مدة الصفقة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return SETTINGS_TIME
    
    await update.message.reply_text("❌ اختر سرعة صحيحة")
    return SETTINGS_CANDLE

async def handle_settings_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع":
        return await start(update, context)
    
    if user_message in TRADE_TIMES:
        save_user_setting(user_id, "trade_time", user_message)
        
        await update.message.reply_text(
            f"✅ **تم حفظ الإعدادات!**\n\n📤 أرسل صورة الشارت الآن:",
            reply_markup=ReplyKeyboardMarkup([["📊 تحليل صورة"], ["الرجوع"]], resize_keyboard=True),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text("❌ اختر مدة صحيحة")
    return SETTINGS_TIME

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # التحقق من الصلاحية
    status, _, user_data = SubscriptionSystem.check_subscription(user_id)
    
    if not status:
        await update.message.reply_text(
            "⚠️ **ليس لديك صلاحية للتحليل**\n\n"
            f"📊 **تحليلك المجاني المتبقي:** {user_data['free_analyses']}\n\n"
            "🔓 **طرق الحصول على صلاحية:**\n"
            "• تسجيل في الوكالة (مجاني)\n"
            "• شراء اشتراك مدفوع\n\n"
            "استخدم /start للبدء",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    # استخدام التحليل
    if not SubscriptionSystem.use_analysis(user_id):
        await update.message.reply_text("❌ حدث خطأ، يرجى المحاولة مرة أخرى")
        return MAIN_MENU
    
    candle, trade_time, _, _ = get_user_setting(user_id)
    
    wait_msg = await update.message.reply_text("🤌 جاري تحليل الشارت... [⬜⬜⬜⬜⬜ 0%]")
    photo = await update.message.photo[-1].get_file()
    
    timestamp = int(time.time())
    original_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}.jpg")
    
    try:
        await photo.download_to_drive(original_path)
        compressed_path = compress_image(original_path)
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ خطأ في قراءة الصورة")
            return MAIN_MENU
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري التحليل... [⬜⬜⬜⬜🟩 20%]")
        
        current_time = datetime.utcnow()
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري التحليل... [⬜⬜⬜🟩🟩 40%]")
        
        PROMPT = f"""
أنت محلل فني. استخرج من الصورة:
• السعر الحالي: [سعر]
• قوة الاندفاع: [رقم]
• اتجاه الموجة: [UP/DOWN]
• FVG: [True/False]
• Order Block: [True/False]
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": MISTRAL_MODEL,
            "messages": [{"role": "user", "content": [{"type": "text", "text": PROMPT}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}]}],
            "max_tokens": 400,
            "temperature": 0.0
        }
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري التحليل... [⬜⬜🟩🟩🟩 60%]")
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=60)
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري التحليل... [⬜🟩🟩🟩🟩 80%]")
        
        if response.status_code != 200:
            await wait_msg.edit_text(f"❌ خطأ: {response.status_code}")
            return MAIN_MENU
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري التحليل... [🟩🟩🟩🟩🟩 100%]")
        
        result = response.json()['choices'][0]['message']['content']
        
        import re
        price_match = re.search(r'السعر الحالي.*?:\s*([\d.]+)', result)
        direction_match = re.search(r'اتجاه الموجة.*?:\s*([A-Z]+)', result, re.IGNORECASE)
        
        current_price = price_match.group(1) if price_match else "غير متاح"
        direction = direction_match.group(1) if direction_match else "غير محدد"
        
        if direction == "UP":
            action = "شراء 🟢"
        elif direction == "DOWN":
            action = "بيع 🔴"
        else:
            action = "انتظار 🟡"
        
        formatted = f"""
📊 **التحليل الفني**

• السعر الحالي: {current_price}
• الاتجاه: {direction}
• القرار: {action}

📌 **نقاط الدخول:**
• TP1: {float(current_price) * 1.005 if current_price != "غير متاح" else "..."}
• TP2: {float(current_price) * 1.01 if current_price != "غير متاح" else "..."}
• SL: {float(current_price) * 0.995 if current_price != "غير متاح" else "..."}

⚠️ **إدارة المخاطر:**
• استخدم وقف خسارة
• حجم مخاطرة مناسب
"""
        
        await wait_msg.edit_text(formatted, parse_mode="Markdown")
        
    except Exception as e:
        await wait_msg.edit_text(f"❌ خطأ: {str(e)[:200]}")
    finally:
        if os.path.exists(original_path):
            os.remove(original_path)
    
    return MAIN_MENU

# ============================================
# المناطق الزمنية
# ============================================
TIMEZONES = {
    "GMT+00:00": "لندن",
    "GMT+01:00": "برلين",
    "GMT+02:00": "القاهرة",
    "GMT+03:00": "الرياض",
    "GMT+04:00": "دبي",
    "GMT-05:00": "نيويورك",
}

async def timezone_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    current_tz = get_user_timezone(user_id)
    local_time = get_localized_time(user_id)
    
    keyboard = [[f"{gmt} ({name})"] for gmt, name in TIMEZONES.items()]
    keyboard.append(["الرجوع"])
    
    await update.message.reply_text(
        f"🌍 **المناطق الزمنية**\n\n🕒 **منطقتك:** {current_tz}\n⏰ **الوقت:** {local_time.strftime('%H:%M:%S')}\n\nاختر المنطقة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return TIMEZONE_MENU

async def handle_timezone_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع":
        return await start(update, context)
    
    for gmt in TIMEZONES.keys():
        if user_message.startswith(gmt):
            set_user_timezone(user_id, gmt)
            await update.message.reply_text(f"✅ تم تعيين المنطقة: {gmt}", parse_mode="Markdown")
            break
    
    return TIMEZONE_MENU

# ============================================
# لوحة تحكم المشرف
# ============================================
async def start_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ غير مصرح")
        return MAIN_MENU
    
    keyboard = [
        ["👥 المستخدمين", "📝 طلبات الوكالة"],
        ["🛒 طلبات الشراء", "📊 إحصائيات"],
        ["📢 إذاعة", "🔙 العودة"]
    ]
    
    await update.message.reply_text(
        "👑 **لوحة التحكم**\n\nاختر الإجراء:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return ADMIN_MENU

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return MAIN_MENU
    
    if user_message == "🔙 العودة":
        return await start(update, context)
    
    elif user_message == "👥 المستخدمين":
        users = SubscriptionSystem.get_all_users()
        msg = "👥 **المستخدمين:**\n\n"
        for u in users[:20]:
            msg += f"• {u[1] or u[0]} (`{u[0]}`)\n"
            msg += f"  📊 تحليلات: {u[3]}/{u[4]} | {u[5]}\n"
            msg += f"  ✅ وكالة: {'نعم' if u[8] else 'لا'}\n\n"
        
        for part in split_message(msg, 4000):
            await update.message.reply_text(part, parse_mode="Markdown")
        return ADMIN_MENU
    
    elif user_message == "📝 طلبات الوكالة":
        pending = SubscriptionSystem.get_pending_affiliate_requests()
        
        if not pending:
            await update.message.reply_text("📭 لا توجد طلبات")
            return ADMIN_MENU
        
        for req in pending:
            req_id, uid, aid, req_time, username, name = req
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ قبول", callback_data=f"aff_approve_{req_id}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"aff_reject_{req_id}")]
            ])
            await update.message.reply_text(
                f"📝 **طلب وكالة**\n\n👤 المستخدم: {uid} ({username or name})\n🆔 المعرف: `{aid}`\n📅 الوقت: {req_time[:16]}",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        return ADMIN_MENU
    
    elif user_message == "🛒 طلبات الشراء":
        pending = SubscriptionSystem.get_pending_purchases()
        
        if not pending:
            await update.message.reply_text("📭 لا توجد طلبات شراء")
            return ADMIN_MENU
        
        for req in pending:
            req_id, uid, sub_type, method, ref, req_time, username, name = req
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تفعيل", callback_data=f"purchase_approve_{req_id}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"purchase_reject_{req_id}")]
            ])
            await update.message.reply_text(
                f"🛒 **طلب شراء**\n\n👤 المستخدم: {uid} ({username or name})\n📦 الاشتراك: {SUBSCRIPTION_TYPES[sub_type]['name']}\n💰 السعر: {SUBSCRIPTION_TYPES[sub_type]['price']}$\n📅 الوقت: {req_time[:16]}",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        return ADMIN_MENU
    
    elif user_message == "📊 إحصائيات":
        stats = SubscriptionSystem.get_stats()
        msg = (
            f"📊 **إحصائيات النظام**\n\n"
            f"👥 المستخدمين: {stats['total_users']}\n"
            f"📊 التحليلات: {stats['total_analyses']}\n"
            f"✅ وكالة: {stats['affiliate_users']}\n"
            f"💎 مشتركين: {stats['paid_users']}\n"
            f"📝 طلبات وكالة: {stats['pending_affiliate']}\n"
            f"🛒 طلبات شراء: {stats['pending_purchases']}\n\n"
            f"🌐 الجلسة: {get_current_session()['name']}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return ADMIN_MENU
    
    elif user_message == "📢 إذاعة":
        await update.message.reply_text(
            "📢 **إذاعة**\n\nأرسل الرسالة للإذاعة:",
            parse_mode="Markdown"
        )
        return BROADCAST_MESSAGE
    
    return ADMIN_MENU

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("aff_approve_"):
        req_id = int(data.replace("aff_approve_", ""))
        success, msg = SubscriptionSystem.verify_affiliate(req_id, ADMIN_ID, True)
        await query.edit_message_text(f"✅ {msg}")
        
        # إعلام المستخدم
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM affiliate_requests WHERE id = ?", (req_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            try:
                await context.bot.send_message(
                    chat_id=row[0],
                    text="✅ **تم تفعيل حسابك في الوكالة!**\n\nيمكنك الآن استخدام البوت مجاناً.\nأرسل /start للبدء.",
                    parse_mode="Markdown"
                )
            except:
                pass
    
    elif data.startswith("aff_reject_"):
        req_id = int(data.replace("aff_reject_", ""))
        success, msg = SubscriptionSystem.verify_affiliate(req_id, ADMIN_ID, False)
        await query.edit_message_text(f"❌ {msg}")
    
    elif data.startswith("purchase_approve_"):
        req_id = int(data.replace("purchase_approve_", ""))
        success, msg = SubscriptionSystem.approve_purchase(req_id, ADMIN_ID)
        await query.edit_message_text(f"✅ {msg}")
        
        # إعلام المستخدم
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM purchase_requests WHERE id = ?", (req_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            try:
                await context.bot.send_message(
                    chat_id=row[0],
                    text="✅ **تم تفعيل اشتراكك!**\n\nشكراً لثقتك. يمكنك الآن استخدام البوت.\nأرسل /start للبدء.",
                    parse_mode="Markdown"
                )
            except:
                pass
    
    elif data.startswith("purchase_reject_"):
        req_id = int(data.replace("purchase_reject_", ""))
        await query.edit_message_text("❌ تم رفض الطلب")

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.message.text.strip()
    
    if user_id != ADMIN_ID:
        return MAIN_MENU
    
    users = SubscriptionSystem.get_all_users()
    sent = 0
    
    wait_msg = await update.message.reply_text(f"📢 جاري الإذاعة لـ {len(users)} مستخدم...")
    
    for u in users:
        try:
            await context.bot.send_message(
                chat_id=u[0],
                text=f"📢 **رسالة إدارية**\n\n{message}\n\nObeida Trading",
                parse_mode="Markdown"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await wait_msg.edit_text(f"✅ تم الإرسال لـ {sent} مستخدم")
    return ADMIN_MENU

# ============================================
# Flask Web Server
# ============================================
app = Flask(__name__)
app.secret_key = SECRET_KEY

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

LOGIN_PAGE = '''
<!DOCTYPE html>
<html dir="rtl">
<head><title>🔐 تسجيل الدخول</title>
<style>
body{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;display:flex;justify-content:center;align-items:center;font-family:sans-serif}
.login{background:white;padding:40px;border-radius:20px;width:350px;text-align:center}
input{width:100%;padding:12px;margin:10px 0;border:2px solid #eee;border-radius:10px}
button{width:100%;padding:12px;background:#667eea;color:white;border:none;border-radius:10px;cursor:pointer}
.error{background:#f8d7da;color:#721c24;padding:10px;border-radius:10px;margin-bottom:20px}
</style>
</head>
<body>
<div class="login">
<h1>🤖 Obeida Trading</h1>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="POST">
<input type="text" name="username" placeholder="اسم المستخدم" required>
<input type="password" name="password" placeholder="كلمة المرور" required>
<button type="submit">تسجيل الدخول</button>
</form>
</div>
</body>
</html>
'''

ADMIN_PANEL = '''
<!DOCTYPE html>
<html dir="rtl">
<head><title>👑 لوحة التحكم</title>
<style>
body{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:20px;font-family:sans-serif}
.container{max-width:1200px;margin:0 auto}
.header{background:white;padding:20px;border-radius:20px;margin-bottom:20px;display:flex;justify-content:space-between}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;margin-bottom:20px}
.stat{background:white;padding:20px;border-radius:15px;text-align:center}
.stat .num{font-size:2em;font-weight:bold;color:#667eea}
.section{background:white;border-radius:20px;padding:20px;margin-bottom:20px}
h2{border-bottom:2px solid #eee;padding-bottom:10px}
table{width:100%;border-collapse:collapse}
th,td{padding:12px;text-align:center;border-bottom:1px solid #eee}
th{background:#667eea;color:white}
.btn{padding:8px 16px;border:none;border-radius:8px;cursor:pointer;margin:2px}
.btn-success{background:#28a745;color:white}
.btn-danger{background:#dc3545;color:white}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>👑 لوحة تحكم Obeida Trading</h1>
<a href="/logout" style="background:#dc3545;color:white;padding:10px 20px;border-radius:10px;text-decoration:none">تسجيل الخروج</a>
</div>

<div class="stats">
<div class="stat"><h3>👥 المستخدمين</h3><div class="num">{{ stats.total_users }}</div></div>
<div class="stat"><h3>📊 التحليلات</h3><div class="num">{{ stats.total_analyses }}</div></div>
<div class="stat"><h3>✅ وكالة</h3><div class="num">{{ stats.affiliate_users }}</div></div>
<div class="stat"><h3>💎 مشتركين</h3><div class="num">{{ stats.paid_users }}</div></div>
</div>

<div class="section">
<h2>📝 طلبات الوكالة ({{ pending_affiliate }})</h2>
<table>
<thead><tr><th>المعرف</th><th>الاسم</th><th>معرف الوكالة</th><th>الوقت</th><th>الإجراءات</th></tr></thead>
<tbody>
{% for req in affiliate_requests %}
<tr>
<td>{{ req.user_id }}</td>
<td>{{ req.username or req.user_id }}</td>
<td>{{ req.affiliate_id }}</td>
<td>{{ req.requested_at[:16] }}</td>
<td>
<button class="btn btn-success" onclick="approveAffiliate({{ req.id }})">✅ قبول</button>
<button class="btn btn-danger" onclick="rejectAffiliate({{ req.id }})">❌ رفض</button>
</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>

<div class="section">
<h2>🛒 طلبات الشراء ({{ pending_purchases }})</h2>
<table>
<thead><tr><th>المعرف</th><th>الاسم</th><th>الاشتراك</th><th>السعر</th><th>الوقت</th><th>الإجراءات</th></tr></thead>
<tbody>
{% for req in purchase_requests %}
<tr>
<td>{{ req.user_id }}</td>
<td>{{ req.username or req.user_id }}</td>
<td>{{ req.subscription_type }}</td>
<td>{{ req.price }}$</td>
<td>{{ req.requested_at[:16] }}</td>
<td>
<button class="btn btn-success" onclick="approvePurchase({{ req.id }})">✅ تفعيل</button>
<button class="btn btn-danger" onclick="rejectPurchase({{ req.id }})">❌ رفض</button>
</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>

<div class="section">
<h2>📢 إذاعة</h2>
<textarea id="broadcastMsg" rows="3" style="width:100%;padding:10px;margin:10px 0" placeholder="أدخل الرسالة..."></textarea>
<button class="btn btn-success" onclick="broadcast()">📢 إرسال</button>
</div>

<div class="footer" style="text-align:center;color:white;margin-top:20px">
<p>Obeida Trading - نظام إدارة الاشتراكات</p>
</div>
</div>

<script>
function approveAffiliate(id){
fetch('/admin/affiliate/'+id+'/approve',{method:'POST'}).then(()=>location.reload())
}
function rejectAffiliate(id){
fetch('/admin/affiliate/'+id+'/reject',{method:'POST'}).then(()=>location.reload())
}
function approvePurchase(id){
fetch('/admin/purchase/'+id+'/approve',{method:'POST'}).then(()=>location.reload())
}
function rejectPurchase(id){
fetch('/admin/purchase/'+id+'/reject',{method:'POST'}).then(()=>location.reload())
}
function broadcast(){
let msg=document.getElementById('broadcastMsg').value
fetch('/admin/broadcast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg})}).then(()=>alert('تم الإرسال'))
}
</script>
</body>
</html>
'''

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD_HASH:
            session['logged_in'] = True
            return redirect(url_for('admin_panel'))
        return render_template_string(LOGIN_PAGE, error='بيانات غير صحيحة')
    return render_template_string(LOGIN_PAGE)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_panel():
    stats = SubscriptionSystem.get_stats()
    
    affiliate_reqs = []
    for req in SubscriptionSystem.get_pending_affiliate_requests():
        affiliate_reqs.append({
            'id': req[0], 'user_id': req[1], 'affiliate_id': req[2],
            'requested_at': req[3], 'username': req[4] or req[5]
        })
    
    purchase_reqs = []
    for req in SubscriptionSystem.get_pending_purchases():
        purchase_reqs.append({
            'id': req[0], 'user_id': req[1], 'subscription_type': SUBSCRIPTION_TYPES[req[2]]['name'],
            'price': SUBSCRIPTION_TYPES[req[2]]['price'], 'requested_at': req[5],
            'username': req[6] or req[7]
        })
    
    return render_template_string(
        ADMIN_PANEL,
        stats=stats,
        pending_affiliate=stats['pending_affiliate'],
        pending_purchases=stats['pending_purchases'],
        affiliate_requests=affiliate_reqs,
        purchase_requests=purchase_reqs
    )

@app.route('/admin/affiliate/<int:req_id>/approve', methods=['POST'])
@login_required
def approve_affiliate(req_id):
    success, msg = SubscriptionSystem.verify_affiliate(req_id, ADMIN_ID, True)
    return jsonify({'success': success})

@app.route('/admin/affiliate/<int:req_id>/reject', methods=['POST'])
@login_required
def reject_affiliate(req_id):
    success, msg = SubscriptionSystem.verify_affiliate(req_id, ADMIN_ID, False)
    return jsonify({'success': success})

@app.route('/admin/purchase/<int:req_id>/approve', methods=['POST'])
@login_required
def approve_purchase(req_id):
    success, msg = SubscriptionSystem.approve_purchase(req_id, ADMIN_ID)
    return jsonify({'success': success})

@app.route('/admin/purchase/<int:req_id>/reject', methods=['POST'])
@login_required
def reject_purchase(req_id):
    return jsonify({'success': True})

@app.route('/admin/broadcast', methods=['POST'])
@login_required
def broadcast():
    # يتم الإرسال عبر البوت
    return jsonify({'success': True})

@app.route('/')
def home():
    return "<h1>🤖 Obeida Trading Bot</h1><p><a href='/admin'>لوحة التحكم</a></p>"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ============================================
# الدالة الرئيسية
# ============================================
def main():
    print("🚀 Starting Obeida Trading Bot...")
    
    init_main_db()
    SubscriptionSystem.init_db()
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu)],
            SETTINGS_CANDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_candle)],
            SETTINGS_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_time)],
            ANALYZE_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_photo_analysis), MessageHandler(filters.PHOTO, handle_photo_analysis)],
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)],
            TIMEZONE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timezone_selection)],
            REGISTER_AFFILIATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_affiliate_id)],
            SHOP_MENU: [CallbackQueryHandler(handle_callback_query)],
        },
        fallbacks=[CommandHandler('start', start)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^(aff_|purchase_)"))
    
    print("✅ Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
