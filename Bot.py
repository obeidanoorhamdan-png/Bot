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
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, ANALYZE_MODE, ADMIN_MENU, BROADCAST_MESSAGE, TIMEZONE_MENU, SEARCH_COUNTRY, REGISTRATION_MENU, REGISTER_AFFILIATE, SHOP_MENU, SESSION_ALERTS_MENU = range(12)

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
    'free': {'name': 'مجاني', 'duration': 0, 'price': 0, 'analyses': 3},
    'daily': {'name': 'يوم', 'duration': 86400, 'price': 5, 'analyses': -1},
    'weekly': {'name': 'أسبوع', 'duration': 604800, 'price': 15, 'analyses': -1},
    'monthly': {'name': 'شهر', 'duration': 2592000, 'price': 40, 'analyses': -1},
    'yearly': {'name': 'سنة', 'duration': 31536000, 'price': 150, 'analyses': -1},
    'lifetime': {'name': 'مدى الحياة', 'duration': -1, 'price': 300, 'analyses': -1}
}

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
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS purchase_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subscription_type TEXT,
                payment_method TEXT,
                payment_reference TEXT,
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending'
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_name TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, session_name)
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
        
        # التحقق من التحليلات المجانية
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
                cursor.execute('UPDATE users SET total_analyses = total_analyses + 1 WHERE user_id = ?', (user_id,))
                conn.commit()
                conn.close()
                return True
        
        if user and user['affiliate_verified']:
            cursor.execute('UPDATE users SET total_analyses = total_analyses + 1 WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            return True
        
        if user and user['free_analyses'] > 0:
            cursor.execute('UPDATE users SET free_analyses = free_analyses - 1, total_analyses = total_analyses + 1 WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            return True
        
        conn.close()
        return False

    @staticmethod
    def request_affiliate_registration(user_id, affiliate_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('SELECT id FROM affiliate_requests WHERE user_id = ? AND status = "pending"', (user_id,))
        if cursor.fetchone():
            conn.close()
            return False, "لديك طلب قيد المراجعة بالفعل"
        
        cursor.execute('INSERT INTO affiliate_requests (user_id, affiliate_id) VALUES (?, ?)', (user_id, affiliate_id))
        conn.commit()
        conn.close()
        return True, "تم إرسال طلب التسجيل بنجاح"

    @staticmethod
    def get_pending_affiliate_requests():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.id, r.user_id, r.affiliate_id, r.requested_at, u.username, u.first_name
            FROM affiliate_requests r LEFT JOIN users u ON r.user_id = u.user_id
            WHERE r.status = 'pending' ORDER BY r.requested_at DESC
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
            cursor.execute('UPDATE users SET affiliate_id = ?, affiliate_verified = 1, free_analyses = 3 WHERE user_id = ?', (affiliate_id, user_id))
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
    def get_all_users():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, username, first_name, free_analyses, total_analyses, subscription_type, affiliate_id, affiliate_verified, timezone FROM users ORDER BY user_id')
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
        conn.close()
        return {'total_users': total_users, 'total_analyses': total_analyses, 'affiliate_users': affiliate_users, 'paid_users': paid_users, 'pending_affiliate': pending_affiliate}

# ============================================
# نظام جلسات التداول
# ============================================
TRADING_SESSIONS = {
    'asian': {'name': '🌏 الجلسة الآسيوية', 'start': 0, 'end': 8, 'volatility': 'منخفضة', 'pairs': 'الين الياباني، الدولار الأسترالي', 'success_rate': '65-70%', 'features': 'حركات هادئة', 'time_range': '00:00-08:00 UTC', 'icon': '🌏'},
    'london': {'name': '🇬🇧 جلسة لندن', 'start': 8, 'end': 16, 'volatility': 'مرتفعة', 'pairs': 'اليورو، الجنيه، الفرنك', 'success_rate': '75-85%', 'features': 'سيولة عالية', 'time_range': '08:00-16:00 UTC', 'icon': '🇬🇧'},
    'newyork': {'name': '🗽 جلسة نيويورك', 'start': 13, 'end': 21, 'volatility': 'عالية جداً', 'pairs': 'الدولار، المؤشرات', 'success_rate': '80-90%', 'features': 'ذروة السيولة', 'time_range': '13:00-21:00 UTC', 'icon': '🗽'},
    'pacific': {'name': '🌊 جلسة المحيط الهادئ', 'start': 21, 'end': 24, 'volatility': 'منخفضة جداً', 'pairs': 'جميع الأزواج', 'success_rate': '55-65%', 'features': 'حركات جانبية', 'time_range': '21:00-00:00 UTC', 'icon': '🌊'},
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
# دوال التنبيهات
# ============================================
def add_user_alert(user_id: int, session_name: str) -> bool:
    try:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO user_alerts (user_id, session_name, enabled) VALUES (?, ?, 1)', (user_id, session_name))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def remove_user_alert(user_id: int, session_name: str) -> bool:
    try:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM user_alerts WHERE user_id = ? AND session_name = ?', (user_id, session_name))
        conn.commit()
        conn.close()
        return True
    except:
        return False

def get_user_alerts(user_id: int) -> list:
    try:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT session_name FROM user_alerts WHERE user_id = ? AND enabled = 1', (user_id,))
        rows = cursor.fetchall()
        conn.close()
        return [row[0] for row in rows]
    except:
        return []

def get_users_for_session_alert(session_name: str) -> list:
    try:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM user_alerts WHERE session_name = ? AND enabled = 1', (session_name,))
        rows = cursor.fetchall()
        conn.close()
        return [row[0] for row in rows]
    except:
        return []

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
        welcome_msg += "\n🔓 **للحصول على صلاحية:**\n• تسجيل في الوكالة (مجاني)\n• شراء اشتراك مدفوع\n"
    
    welcome_msg += (
        f"\n🌐 **الجلسة الحالية:** {current_session['name']}\n"
        f"📊 **نسبة النجاح:** {current_session['success_rate']}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        "**أنواع القرارات الفنية:**\n"
        "🟢 شراء | 🔴 بيع | 💥 كسر هيكلي | 🔄 سحب سيولة | 🔄 انعكاس فوري\n\n"
        "🤖 **Powered by Obeida Trading**"
    )
    
    # تحديد الأزرار حسب حالة المستخدم فقط
    if status:
        # المستخدم مسجل أو مشترك - تظهر جميع الأزرار
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["🔔 تنبيه بدء جلسة", "👤 معلوماتي"],
            ["🌍 المناطق الزمنية", "🛒 المتجر"],
            ["📝 تسجيل الوكالة"]
        ]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
    else:
        # المستخدم غير مسجل - تظهر أزرار محدودة فقط (بدون تحليل)
        keyboard = [
            ["👤 معلوماتي"],
            ["🔔 تنبيه بدء جلسة", "🛒 المتجر"],
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
    status, sub_type, user_data = SubscriptionSystem.check_subscription(user_id)
    
    if user_message == "🌍 المناطق الزمنية":
        return await timezone_menu(update, context)
    
    elif user_message == "👑 لوحة التحكم" and user_id == ADMIN_ID:
        return await start_admin_panel(update, context)
    
    elif user_message == "👤 معلوماتي":
        user_data = SubscriptionSystem.get_user(user_id)
        status, sub_type, _ = SubscriptionSystem.check_subscription(user_id)
        info = f"👤 **معلوماتك**\n\n🆔 **المعرف:** `{user_id}`\n👤 **اليوزر:** @{update.effective_user.username or 'لا يوجد'}\n\n"
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
        info += f"📈 **إجمالي التحليلات:** {user_data['total_analyses']}\n🌍 **المنطقة:** {user_data['timezone']}\n"
        
        user_alerts = get_user_alerts(user_id)
        if user_alerts:
            alerts_list = [TRADING_SESSIONS[s]['name'] for s in user_alerts if s in TRADING_SESSIONS]
            if alerts_list:
                info += f"\n🔔 **التنبيهات المفعلة:**\n" + "\n".join([f"• {a}" for a in alerts_list])
        
        await update.message.reply_text(info, parse_mode="Markdown")
        return MAIN_MENU
    
    elif user_message == "🔔 تنبيه بدء جلسة":
        return await session_alerts_menu(update, context)
    
    elif user_message == "🛒 المتجر":
        return await shop_menu(update, context)
    
    elif user_message == "📝 تسجيل الوكالة":
        return await affiliate_registration_menu(update, context)
    
    # هذه الأزرار تظهر فقط للمستخدمين المسجلين/المشتركين
    if status:
        if user_message == "⚙️ إعدادات التحليل":
            keyboard = [CANDLE_SPEEDS[i:i+3] for i in range(0, len(CANDLE_SPEEDS), 3)]
            keyboard.append(["الرجوع"])
            await update.message.reply_text(
                "⚙️ **إعدادات التحليل**\n\nاختر سرعة الشموع:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
                parse_mode="Markdown"
            )
            return SETTINGS_CANDLE
        
        elif user_message == "📊 تحليل صورة":
            candle, trade_time, _, _ = get_user_setting(user_id)
            if not candle:
                await update.message.reply_text(
                    "❌ **يجب ضبط الإعدادات أولاً**\nاستخدم زر '⚙️ إعدادات التحليل'",
                    parse_mode="Markdown"
                )
                return MAIN_MENU
            await update.message.reply_text(
                f"📤 **أرسل صورة الشارت**\n\n⚙️ **الإعدادات:** {candle} | {trade_time}",
                reply_markup=ReplyKeyboardMarkup([["الرجوع"]], resize_keyboard=True),
                parse_mode="Markdown"
            )
            return ANALYZE_MODE
    else:
        # إذا حاول المستخدم الضغط على أزرار غير مسموحة
        if user_message in ["⚙️ إعدادات التحليل", "📊 تحليل صورة"]:
            await update.message.reply_text(
                "⚠️ **ليس لديك صلاحية للتحليل** ⚠️\n\n"
                "🔓 **طرق الحصول على صلاحية:**\n"
                "• تسجيل في الوكالة (مجاني) - اضغط '📝 تسجيل الوكالة'\n"
                "• شراء اشتراك مدفوع - اضغط '🛒 المتجر'\n\n"
                "بعد التفعيل، ستظهر لك أزرار التحليل والإعدادات.",
                parse_mode="Markdown"
            )
            return MAIN_MENU
    
    if user_message == "الرجوع":
        return await start(update, context)
    
    return MAIN_MENU

async def session_alerts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_alerts = get_user_alerts(user_id)
    
    keyboard = []
    for session_key, session in TRADING_SESSIONS.items():
        is_active = session_key in user_alerts
        status = "✅" if is_active else "⬜"
        keyboard.append([f"{status} {session['icon']} {session['name']}"])
    
    keyboard.append(["🗑️ إلغاء الكل"])
    keyboard.append(["🔙 العودة للقائمة الرئيسية"])
    
    await update.message.reply_text(
        f"🔔 **إدارة تنبيهات الجلسات**\n\nاختر الجلسات التي تريد تلقي تنبيه عند بدايتها:\n\n✅ = مفعل\n⬜ = غير مفعل\n\nسيتم إرسال تنبيه لك فور بدء كل جلسة.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return SESSION_ALERTS_MENU

async def handle_session_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "🔙 العودة للقائمة الرئيسية":
        return await start(update, context)
    
    elif user_message == "🗑️ إلغاء الكل":
        for session_key in TRADING_SESSIONS.keys():
            remove_user_alert(user_id, session_key)
        await update.message.reply_text("✅ تم إلغاء جميع تنبيهات الجلسات", parse_mode="Markdown")
        return await session_alerts_menu(update, context)
    
    for session_key, session in TRADING_SESSIONS.items():
        if session['name'] in user_message:
            user_alerts = get_user_alerts(user_id)
            if session_key in user_alerts:
                remove_user_alert(user_id, session_key)
                await update.message.reply_text(f"❌ تم إلغاء تنبيه {session['name']}", parse_mode="Markdown")
            else:
                add_user_alert(user_id, session_key)
                await update.message.reply_text(
                    f"✅ تم تفعيل تنبيه {session['name']}\n\nستتلقى إشعاراً فور بدء الجلسة.",
                    parse_mode="Markdown"
                )
            break
    
    return await session_alerts_menu(update, context)

async def shop_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 يوم - 5$", callback_data="buy_daily")],
        [InlineKeyboardButton("📆 أسبوع - 15$", callback_data="buy_weekly")],
        [InlineKeyboardButton("📅 شهر - 40$", callback_data="buy_monthly")],
        [InlineKeyboardButton("📅 سنة - 150$", callback_data="buy_yearly")],
        [InlineKeyboardButton("♾️ مدى الحياة - 300$", callback_data="buy_lifetime")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_menu")]
    ])
    await update.message.reply_text(
        "🛒 **متجر الاشتراكات**\n\nاختر نوع الاشتراك:\n\n"
        "📅 يوم: 5$ - تحليلات غير محدودة\n"
        "📆 أسبوع: 15$ - تحليلات غير محدودة\n"
        "📅 شهر: 40$ - تحليلات غير محدودة\n"
        "📅 سنة: 150$ - تحليلات غير محدودة\n"
        "♾️ مدى الحياة: 300$ - تحليلات غير محدودة\n\n"
        "💰 **طرق الدفع:** تحويل بنكي، USDT، باي بال\n\n"
        "بعد الدفع، أرسل الإيصال مع معرف المستخدم للمشرف @Sz2zv",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    return SHOP_MENU

async def affiliate_registration_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = SubscriptionSystem.get_user(user_id)
    
    if user_data and user_data['affiliate_verified']:
        await update.message.reply_text(
            f"✅ **أنت مسجل بالفعل في الوكالة!**\n\n🆔 **معرف الوكالة:** `{user_data['affiliate_id']}`\n\nيمكنك استخدام البوت مجاناً.",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text(
        "📝 **التسجيل في وكالة Tickmill (مجاني)**\n\n"
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
    
    if data.startswith("buy_"):
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
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🛒 **طلب شراء جديد**\n\n👤 المستخدم: {user_id}\n📦 الاشتراك: {name}\n💰 السعر: {price}$",
                parse_mode="Markdown"
            )
    
    elif data == "back_to_menu":
        await query.edit_message_text("🏠 العودة للقائمة الرئيسية")

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

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    if user_message == "الرجوع":
        return await start(update, context)
    return ANALYZE_MODE

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # التحقق من الصلاحية قبل التحليل
    status, sub_type, user_data = SubscriptionSystem.check_subscription(user_id)
    
    if not status:
        await update.message.reply_text(
            "⚠️ **ليس لديك صلاحية للتحليل** ⚠️\n\n"
            f"📊 **تحليلك المجاني المتبقي:** {user_data['free_analyses']}\n\n"
            "🔓 **طرق الحصول على صلاحية:**\n"
            "• تسجيل في الوكالة (مجاني) - اضغط '📝 تسجيل الوكالة'\n"
            "• شراء اشتراك مدفوع - اضغط '🛒 المتجر'\n\n"
            "بعد التفعيل، يمكنك استخدام التحليل.",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    # استخدام التحليل
    if not SubscriptionSystem.use_analysis(user_id):
        await update.message.reply_text("❌ حدث خطأ، يرجى المحاولة مرة أخرى", parse_mode="Markdown")
        return MAIN_MENU
    
    candle, trade_time, _, _ = get_user_setting(user_id)
    await analyze_chart_image(update, context, candle, trade_time, user_id)
    return MAIN_MENU

async def analyze_chart_image(update: Update, context: ContextTypes.DEFAULT_TYPE, candle, trade_time, user_id):
    try:
        wait_msg = await update.message.reply_text("🤌 جاري تحليل الشارت... [⬜⬜⬜⬜⬜ 0%]")
        photo = await update.message.photo[-1].get_file()
        timestamp = int(time.time())
        original_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}.jpg")
        
        await photo.download_to_drive(original_path)
        compressed_path = compress_image(original_path)
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ خطأ في قراءة الصورة")
            return
        
        current_time = datetime.utcnow()
        current_hour, current_minute, current_second = current_time.hour, current_time.minute, current_time.second
        seconds_remaining, seconds_passed = 0, 0
        
        if candle.startswith('S'):
            candle_seconds = int(candle[1:]) if candle[1:].isdigit() else 5
            seconds_passed = current_second % candle_seconds
            seconds_remaining = candle_seconds - seconds_passed
        elif candle.startswith('M'):
            candle_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            total_minutes = current_hour * 60 + current_minute
            minutes_passed = total_minutes % candle_minutes
            seconds_passed = minutes_passed * 60 + current_second
            seconds_remaining = (candle_minutes * 60) - seconds_passed
        
        in_golden_window = 2 <= seconds_passed <= 58
        candle_status = "مغلقة" if seconds_remaining <= 0 else "مفتوحة"
        
        await asyncio.sleep(1.5)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬜⬜⬜⬜🟩 20%]")
        
        MAIN_PROMPT = f"""
أنت المحرك التحليلي لنظام Obeida Trading. استخرج المعطيات الرقمية بدقة.

المطلوب استخراجه:
• أعلى قمة (HH): [سعر]
• أدنى قاع (LL): [سعر]
• السعر الحالي: [سعر]
• قوة الاندفاع: [رقم عشري]
• اتجاه الموجة: [UP/DOWN]
• مستوى 0.500 فيبوناتشي: [سعر]
• مستوى 0.236 فيبوناتشي: [سعر]
• مستوى 0.382 فيبوناتشي: [سعر]
• مستوى 0.618 فيبوناتشي: [سعر]
• مستوى 0.786 فيبوناتشي: [سعر]
• مستوى 1.272 فيبوناتشي: [سعر]
• مستوى 1.618 فيبوناتشي: [سعر]
• FVG: [True/False]
• مستويات FVG: [من X إلى Y]
• Order Block: [True/False]
• التمركز السعري: [PREMIUM/DISCOUNT/TRAP_ZONE]
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": MISTRAL_MODEL,
            "messages": [{"role": "user", "content": [{"type": "text", "text": MAIN_PROMPT}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}]}],
            "max_tokens": 600,
            "temperature": 0.0,
            "top_p": 1.0
        }
        
        for i in range(1, 4):
            await asyncio.sleep(1.5)
            progress = i * 26
            bars = "⬜" * (3 - i) + "🟩" * i
            await wait_msg.edit_text(f"🤌 جاري تحليل الشارت... [{bars} {progress}%]")
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=90)
        
        if response.status_code != 200:
            await wait_msg.edit_text(f"❌ خطأ: {response.status_code}")
            return
        
        analysis_result = response.json()['choices'][0]['message']['content'].strip()
        
        import re
        current_price_match = re.search(r'السعر الحالي.*?:\s*([\d.]+)', analysis_result)
        breakout_force_match = re.search(r'قوة الاندفاع.*?:\s*([\d.]+)', analysis_result)
        direction_match = re.search(r'اتجاه الموجة.*?:\s*([A-Z]+)', analysis_result)
        fvg_match = re.search(r'FVG.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        order_block_match = re.search(r'Order Block.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        positioning_match = re.search(r'التمركز السعري.*?:\s*([A-Z_]+)', analysis_result)
        
        # استخراج مستويات فيبوناتشي
        fib_levels = {}
        for fib_key in ['0.236', '0.382', '0.500', '0.618', '0.786', '1.272', '1.618']:
            fib_match = re.search(f'مستوى {fib_key}.*?:\s*([\d.]+)', analysis_result)
            if fib_match:
                fib_levels[fib_key] = float(fib_match.group(1))
        
        has_fvg = fvg_match.group(1).upper() == "TRUE" if fvg_match else False
        has_order_block = order_block_match.group(1).upper() == "TRUE" if order_block_match else False
        positioning = positioning_match.group(1) if positioning_match else "NEUTRAL"
        current_price = float(current_price_match.group(1)) if current_price_match else 0
        breakout_force = float(breakout_force_match.group(1)) if breakout_force_match else 1.0
        current_direction = direction_match.group(1).upper() if direction_match else "UP"
        fib_500 = fib_levels.get("0.500", current_price)
        
        # حساب نقاط الدخول والخروج
        if current_direction == "UP":
            entry_price = current_price
            tp1 = fib_levels.get("0.500", current_price * 1.005)
            tp2 = fib_levels.get("0.236", current_price * 1.01)
            tp3 = fib_levels.get("1.272", current_price * 1.02)
            stop_loss = fib_levels.get("0.618", current_price * 0.995)
        else:
            entry_price = current_price
            tp1 = fib_levels.get("0.500", current_price * 0.995)
            tp2 = fib_levels.get("0.236", current_price * 0.99)
            tp3 = fib_levels.get("1.272", current_price * 0.98)
            stop_loss = fib_levels.get("0.618", current_price * 1.005)
        
        # حساب قوة الإشارة
        if breakout_force > 1.5:
            signal_strength = "عالية جدا 💥"
            confidence = 85
        elif breakout_force > 1.2:
            signal_strength = "عالية 🔥"
            confidence = 75
        elif breakout_force > 1.0:
            signal_strength = "متوسطة ⚡"
            confidence = 65
        else:
            signal_strength = "ضعيفة ❄️"
            confidence = 55
        
        if has_fvg:
            confidence += 5
        if has_order_block:
            confidence += 5
        if positioning == "DISCOUNT" and current_direction == "UP":
            confidence += 5
        elif positioning == "PREMIUM" and current_direction == "DOWN":
            confidence += 5
        elif positioning == "TRAP_ZONE":
            confidence -= 10
        confidence = min(100, max(0, confidence))
        
        # تحديد القرار الفني
        if current_direction == "UP":
            if breakout_force > 1.5 and has_fvg:
                action = "شراء 🟢"
                entry_type = "دخول مباشر"
            elif breakout_force > 1.2 and has_order_block:
                action = "كسر هيكلي صاعد 💥🟢"
                entry_type = "دخول مباشر"
            elif positioning == "DISCOUNT" and current_price < fib_500:
                action = "سحب سيولة صاعد 🔄🟢"
                entry_type = "إعادة اختبار"
            elif breakout_force > 1.0:
                action = "انعكاس فوري صاعد 🔄🟢"
                entry_type = "انعكاس فوري"
            else:
                action = "انتظار تصحيح 🟡"
                entry_type = "دخول معلق"
        else:
            if breakout_force > 1.5 and has_fvg:
                action = "بيع 🔴"
                entry_type = "دخول مباشر"
            elif breakout_force > 1.2 and has_order_block:
                action = "كسر هيكلي هابط 💥🔴"
                entry_type = "دخول مباشر"
            elif positioning == "PREMIUM" and current_price > fib_500:
                action = "سحب سيولة هابط 🔄🔴"
                entry_type = "إعادة اختبار"
            elif breakout_force > 1.0:
                action = "انعكاس فوري هابط 🔄🔴"
                entry_type = "انعكاس فوري"
            else:
                action = "انتظار تصحيح 🟡"
                entry_type = "دخول معلق"
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [🟩🟩🟩🟩🟩 100%]")
        
        # بناء النتيجة مع الأهداف
        formatted_result = f"""
📊 **التحليل الفني المتقدم:**
• الوقت: {current_time.strftime('%H:%M:%S')} UTC
• حالة النافذة: {"✅ داخل النافذة الذهبية" if in_golden_window else "❌ خارج النافذة"}
• FVG: {"موجودة ✅" if has_fvg else "غير موجودة ❌"}
• Order Block: {"موجود ✅" if has_order_block else "غير موجود ❌"}

🎯 **الإشارة التنفيذية:**
• السعر الحالي: {current_price:.6f}
• حالة الشمعة: {candle_status} - الوقت المتبقي: {seconds_remaining} ثانية
• القرار الفني: {action}
• قوة الإشارة: {signal_strength}
• نوع الدخول: {entry_type}
• نقطة الدخول: {entry_price:.6f}

📈 **الأهداف الربحية:**
• 🎯 TP1: {tp1:.6f} - (مستوى 0.500 فيبوناتشي)
• 🎯 TP2: {tp2:.6f} - (مستوى 0.236 فيبوناتشي)
• 🎯 TP3: {tp3:.6f} - (مستوى 1.272 فيبوناتشي)

🛑 **وقف الخسارة:**
• 🛡️ SL: {stop_loss:.6f} - (مستوى 0.618 فيبوناتشي)

⚠️ **إدارة المخاطر:**
• مستوى الثقة: {confidence:.0f}%
• نسبة نجاح متوقعة: {"85-95%" if confidence >= 85 else "70-85%" if confidence >= 70 else "55-70%" if confidence >= 55 else "40-55%"}
• حجم الاستثمار المقترح: {"5%" if confidence >= 85 else "3%" if confidence >= 70 else "2%" if confidence >= 55 else "1%"}
• نسبة المخاطرة/العائد: 1:2 على الأقل
"""
        
        await wait_msg.edit_text(formatted_result, parse_mode="Markdown")
        
        # تنظيف الملفات المؤقتة
        if os.path.exists(original_path):
            os.remove(original_path)
        if os.path.exists(compressed_path):
            os.remove(compressed_path)
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)[:200]}", parse_mode="Markdown")

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
        return MAIN_MENU
    keyboard = [["👥 المستخدمين", "📝 طلبات الوكالة"], ["📊 إحصائيات", "📢 إذاعة"], ["🔙 العودة"]]
    await update.message.reply_text(
        "👑 **لوحة التحكم**\n\nاختر الإجراء:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return ADMIN_MENU

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    if update.effective_user.id != ADMIN_ID:
        return MAIN_MENU
    
    if user_message == "🔙 العودة":
        return await start(update, context)
    
    elif user_message == "👥 المستخدمين":
        users = SubscriptionSystem.get_all_users()
        msg = "👥 **المستخدمين:**\n\n"
        for u in users[:20]:
            status_text = "✅ مفعل" if u[7] else "⏳ غير مفعل"
            sub_text = u[5] if u[5] != 'free' else "مجاني"
            msg += f"• {u[1] or u[0]} (`{u[0]}`)\n  📊 تحليلات: {u[3]}/{u[4]} | {sub_text} | وكالة: {status_text}\n\n"
        for part in split_message(msg, 4000):
            await update.message.reply_text(part, parse_mode="Markdown")
        return ADMIN_MENU
    
    elif user_message == "📝 طلبات الوكالة":
        pending = SubscriptionSystem.get_pending_affiliate_requests()
        if not pending:
            await update.message.reply_text("📭 لا توجد طلبات", parse_mode="Markdown")
            return ADMIN_MENU
        for req in pending:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ قبول", callback_data=f"aff_approve_{req[0]}"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"aff_reject_{req[0]}")]
            ])
            await update.message.reply_text(
                f"📝 **طلب وكالة جديد**\n\n👤 المستخدم: {req[1]} ({req[4] or req[5]})\n🆔 المعرف: `{req[2]}`\n📅 الوقت: {req[3][:16]}",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        return ADMIN_MENU
    
    elif user_message == "📊 إحصائيات":
        stats = SubscriptionSystem.get_stats()
        await update.message.reply_text(
            f"📊 **إحصائيات النظام**\n\n"
            f"👥 إجمالي المستخدمين: {stats['total_users']}\n"
            f"📊 إجمالي التحليلات: {stats['total_analyses']}\n"
            f"✅ مستخدمي الوكالة: {stats['affiliate_users']}\n"
            f"💎 المشتركين المدفوعين: {stats['paid_users']}\n"
            f"📝 طلبات وكالة معلقة: {stats['pending_affiliate']}\n\n"
            f"🌐 الجلسة الحالية: {get_current_session()['name']}",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    elif user_message == "📢 إذاعة":
        await update.message.reply_text(
            "📢 **إذاعة للمستخدمين**\n\nأرسل الرسالة التي تريد إذاعتها:",
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
        await query.edit_message_text(f"✅ {msg}", parse_mode="Markdown")
        
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
        await query.edit_message_text(f"❌ {msg}", parse_mode="Markdown")

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return MAIN_MENU
    message = update.message.text
    users = SubscriptionSystem.get_all_users()
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(
                chat_id=u[0],
                text=f"📢 **رسالة إدارية**\n\n{message}\n\n━━━━━━━━━━━━\nObeida Trading",
                parse_mode="Markdown"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await update.message.reply_text(f"✅ تم الإرسال لـ {sent} مستخدم", parse_mode="Markdown")
    return ADMIN_MENU

# ============================================
# جدولة التنبيهات
# ============================================
async def session_alert_scheduler(context):
    last_sent = {}
    while True:
        current_hour = datetime.utcnow().hour
        current_minute = datetime.utcnow().minute
        
        for session_key, session in TRADING_SESSIONS.items():
            if current_hour == session['start'] and current_minute == 0:
                if session_key not in last_sent or last_sent[session_key] != datetime.utcnow().date():
                    users = get_users_for_session_alert(session_key)
                    if users:
                        alert_msg = (
                            f"🔔 **{session['icon']} بدأت {session['name']}** 🔔\n\n"
                            f"📊 **التقلب:** {session['volatility']}\n"
                            f"🎯 **نسبة النجاح:** {session['success_rate']}\n"
                            f"💰 **الأزواج المناسبة:** {session['pairs']}\n"
                            f"✨ **المميزات:** {session['features']}\n"
                            f"⏰ **توقيت الجلسة:** {session['time_range']}\n\n"
                            f"🚀 استخدم /start للبدء في التحليل الآن!"
                        )
                        for user_id in users:
                            try:
                                await context.bot.send_message(
                                    chat_id=user_id,
                                    text=alert_msg,
                                    parse_mode="Markdown"
                                )
                                await asyncio.sleep(0.1)
                            except Exception as e:
                                print(f"⚠️ فشل إرسال تنبيه للمستخدم {user_id}: {e}")
                    last_sent[session_key] = datetime.utcnow().date()
        await asyncio.sleep(60)

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
<h2>📝 طلبات الوكالة ({{ stats.pending_affiliate }})</h2>
<table>
<thead><tr><th>المعرف</th><th>الاسم</th><th>معرف الوكالة</th><th>الوقت</th><th>الإجراءات</th></tr></thead>
<tbody>
{% for req in affiliate_requests %}
<tr><td>{{ req.user_id }}</td><td>{{ req.username or req.user_id }}</td><td>{{ req.affiliate_id }}</td><td>{{ req.requested_at[:16] }}</td><td><button class="btn btn-success" onclick="approve({{ req.id }})">✅ قبول</button><button class="btn btn-danger" onclick="reject({{ req.id }})">❌ رفض</button></td></tr>
{% endfor %}
</tbody>
</table>
</div>
<div class="section">
<h2>📢 إذاعة</h2>
<textarea id="broadcastMsg" rows="3" style="width:100%;padding:10px;margin:10px 0" placeholder="أدخل الرسالة..."></textarea>
<button class="btn btn-success" onclick="broadcast()">📢 إرسال</button>
</div>
</div>
<script>
function approve(id){fetch('/admin/affiliate/'+id+'/approve',{method:'POST'}).then(()=>location.reload())}
function reject(id){fetch('/admin/affiliate/'+id+'/reject',{method:'POST'}).then(()=>location.reload())}
function broadcast(){let msg=document.getElementById('broadcastMsg').value;fetch('/admin/broadcast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg})}).then(()=>alert('تم الإرسال'))}
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
            'id': req[0],
            'user_id': req[1],
            'affiliate_id': req[2],
            'requested_at': req[3],
            'username': req[4] or req[5]
        })
    return render_template_string(ADMIN_PANEL, stats=stats, affiliate_requests=affiliate_reqs)

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

@app.route('/admin/broadcast', methods=['POST'])
@login_required
def broadcast():
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
    
    async def run_scheduler():
        await session_alert_scheduler(application)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(run_scheduler())
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu)],
            SETTINGS_CANDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_candle)],
            SETTINGS_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_time)],
            ANALYZE_MODE: [MessageHandler(filters.PHOTO, handle_photo_analysis), MessageHandler(filters.TEXT & ~filters.COMMAND, handle_analyze_mode)],
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)],
            TIMEZONE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timezone_selection)],
            REGISTER_AFFILIATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_affiliate_id)],
            SHOP_MENU: [CallbackQueryHandler(handle_callback_query)],
            SESSION_ALERTS_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session_alerts)],
        },
        fallbacks=[CommandHandler('start', start)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^aff_"))
    
    print("✅ Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
