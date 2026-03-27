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
import hashlib
import secrets
import feedparser
from datetime import datetime, timedelta
from typing import Dict, List, Set, Tuple, Optional
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
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
AFFILIATES_DB = "affiliates.db"
TRADES_DB = "trades.db"
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
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, ANALYZE_MODE, ADMIN_MENU, ADD_SUBSCRIPTION, SELECT_DURATION, REMOVE_USER, BROADCAST_MESSAGE, SET_USER_DURATION, SET_BOT_STATUS, SET_SESSION_CONTROL, TIMEZONE_MENU, SEARCH_COUNTRY, AFFILIATES_MENU, ADD_AFFILIATE, SHOP_SETTINGS, IMPORT_USERS = range(18)

# تخزين مؤقت للتحليلات قيد التنفيذ
active_analyses = {}
analysis_lock = threading.Lock()

for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"📁 تم إنشاء المجلد: {directory}")

# ============================================
# أسعار الاشتراكات الافتراضية
# ============================================
DEFAULT_SUBSCRIPTION_PRICES = {
    'hourly': {'price': 1, 'currency': '$', 'name': 'ساعة'},
    'daily': {'price': 5, 'currency': '$', 'name': 'يوم'},
    'weekly': {'price': 15, 'currency': '$', 'name': 'أسبوع'},
    'monthly': {'price': 40, 'currency': '$', 'name': 'شهر'},
    'yearly': {'price': 400, 'currency': '$', 'name': 'سنة'},
    'lifetime': {'price': 1000, 'currency': '$', 'name': 'مدى الحياة'}
}

DEFAULT_PAYMENT_LINKS = {
    'paypal': 'https://paypal.me/obeidatrading',
    'crypto': '0x742d35Cc6634C0532925a3b844Bc9e7598f0bEb0',
    'bank': 'للتواصل مع المشرف للتحويل البنكي'
}

# ============================================
# دوال مساعدة
# ============================================

def escape_markdown(text):
    if not text:
        return text
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

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

# ============================================
# نظام الاشتراك المتطور
# ============================================

class SubscriptionSystem:
    SUBSCRIPTION_TYPES = {
        'hourly': {'name': 'ساعة', 'duration': 3600},
        'daily': {'name': 'يوم', 'duration': 86400},
        'weekly': {'name': 'أسبوع', 'duration': 604800},
        'monthly': {'name': 'شهر', 'duration': 2592000},
        'yearly': {'name': 'سنة', 'duration': 31536000},
        'lifetime': {'name': 'مدى الحياة', 'duration': -1}
    }
    
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
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_blocked BOOLEAN DEFAULT 0,
                total_analyses INTEGER DEFAULT 0,
                timezone TEXT DEFAULT 'GMT+02:00'
            )
        ''')
        
        # جدول الاشتراكات النشطة
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
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # جدول إعدادات المتجر
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shop_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE,
                setting_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # جدول حالة البوت
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT DEFAULT 'active',
                session_control TEXT DEFAULT 'all',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # إضافة الإعدادات الافتراضية
        cursor.execute("SELECT COUNT(*) FROM shop_settings")
        if cursor.fetchone()[0] == 0:
            for sub_type, info in DEFAULT_SUBSCRIPTION_PRICES.items():
                cursor.execute('INSERT INTO shop_settings (setting_key, setting_value) VALUES (?, ?)', 
                             (f'price_{sub_type}', json.dumps(info)))
            cursor.execute('INSERT INTO shop_settings (setting_key, setting_value) VALUES (?, ?)', 
                         ('payment_links', json.dumps(DEFAULT_PAYMENT_LINKS)))
        
        cursor.execute("SELECT COUNT(*) FROM bot_status")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO bot_status (status, session_control) VALUES ('active', 'all')")
        
        conn.commit()
        conn.close()
        print("✅ تم تهيئة نظام الاشتراك")
    
    @staticmethod
    def get_shop_settings():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        settings = {'prices': {}, 'payment_links': {}}
        
        try:
            cursor.execute("SELECT setting_key, setting_value FROM shop_settings")
            for key, value in cursor.fetchall():
                if key.startswith('price_'):
                    sub_type = key.replace('price_', '')
                    settings['prices'][sub_type] = json.loads(value)
                elif key == 'payment_links':
                    settings['payment_links'] = json.loads(value)
        except:
            settings['prices'] = DEFAULT_SUBSCRIPTION_PRICES
            settings['payment_links'] = DEFAULT_PAYMENT_LINKS
        finally:
            conn.close()
        return settings
    
    @staticmethod
    def register_user(user_id, username=None, first_name=None, last_name=None):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if cursor.fetchone():
                cursor.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP, username = COALESCE(?, username), first_name = COALESCE(?, first_name), last_name = COALESCE(?, last_name) WHERE user_id = ?", 
                             (username, first_name, last_name, user_id))
            else:
                cursor.execute("INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)", 
                             (user_id, username, first_name, last_name))
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
            if subscription_type not in SubscriptionSystem.SUBSCRIPTION_TYPES:
                return False, "نوع اشتراك غير صالح"
            
            duration = SubscriptionSystem.SUBSCRIPTION_TYPES[subscription_type]['duration']
            start_date = datetime.now()
            end_date = None if duration == -1 else start_date + timedelta(seconds=duration)
            
            shop_settings = SubscriptionSystem.get_shop_settings()
            price_info = shop_settings['prices'].get(subscription_type, {'price': 0, 'currency': '$'})
            sub_name = SubscriptionSystem.SUBSCRIPTION_TYPES[subscription_type]['name']
            
            # إلغاء تنشيط الاشتراكات السابقة
            cursor.execute("UPDATE active_subscriptions SET is_active = 0 WHERE user_id = ? AND is_active = 1", (user_id,))
            
            cursor.execute("""
                INSERT INTO active_subscriptions (user_id, subscription_type, subscription_name, start_date, end_date, is_active, price, currency, added_by)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """, (user_id, subscription_type, sub_name, start_date.isoformat(), end_date.isoformat() if end_date else None, price_info['price'], price_info['currency'], added_by))
            
            conn.commit()
            return True, {'subscription_name': sub_name, 'end_date': end_date.isoformat() if end_date else None, 'price': price_info['price'], 'currency': price_info['currency']}
        except Exception as e:
            conn.rollback()
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
                SELECT subscription_type, subscription_name, end_date
                FROM active_subscriptions 
                WHERE user_id = ? AND is_active = 1
                ORDER BY id DESC LIMIT 1
            """, (user_id,))
            
            result = cursor.fetchone()
            if not result:
                return False, "لا يوجد اشتراك نشط", None
            
            sub_type, sub_name, end_date = result
            
            if end_date:
                try:
                    end = datetime.fromisoformat(end_date)
                    if end < datetime.now():
                        cursor.execute("UPDATE active_subscriptions SET is_active = 0 WHERE user_id = ? AND is_active = 1", (user_id,))
                        conn.commit()
                        return False, "انتهت صلاحية الاشتراك", None
                except:
                    pass
            
            return True, sub_name, {'type': sub_type, 'end_date': end_date}
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
    def get_all_users():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT u.user_id, u.username, u.first_name, u.created_at, u.last_active, u.is_blocked, u.total_analyses, u.timezone,
                       a.subscription_name, a.end_date, a.is_active
                FROM users u
                LEFT JOIN active_subscriptions a ON u.user_id = a.user_id AND a.is_active = 1
                ORDER BY u.created_at DESC
            """)
            return cursor.fetchall()
        except:
            return []
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
    def export_users_to_csv():
        users = SubscriptionSystem.get_all_users()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['user_id', 'username', 'subscription', 'end_date', 'status', 'total_analyses'])
        
        for u in users:
            uid, username, first_name, created, last_active, is_blocked, total_analyses, timezone, sub_name, end_date, is_active = u
            status = 'blocked' if is_blocked else 'active' if is_active else 'expired'
            writer.writerow([uid, username or first_name or '', sub_name or '', end_date or '', status, total_analyses])
        
        return output.getvalue()
    
    @staticmethod
    def set_user_timezone(user_id, timezone):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (timezone, user_id))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()

# ============================================
# دوال معالجة البوت
# ============================================

async def check_subscription(update: Update, user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    
    status, message, _ = SubscriptionSystem.check_subscription(user_id)
    if status:
        return True
    
    shop_settings = SubscriptionSystem.get_shop_settings()
    prices_text = "\n".join([f"• {info['name']}: {info['price']}{info['currency']}" for info in shop_settings['prices'].values()])
    
    await update.message.reply_text(
        f"⚠️ **عذراً، ليس لديك اشتراك نشط**\n\n"
        f"🔑 **أسعار الاشتراكات:**\n{prices_text}\n\n"
        f"📞 للاشتراك والتواصل مع المشرف: @Sz2zv",
        parse_mode="Markdown"
    )
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    SubscriptionSystem.register_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    user_info = SubscriptionSystem.get_user_info(user_id)
    status, sub_name, _ = SubscriptionSystem.check_subscription(user_id)
    
    # إنشاء جدول المستخدم في قاعدة البيانات المحلية إذا لم يكن موجوداً
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, username, candle, trade_time) VALUES (?, ?, ?, ?)", 
                      (user_id, user.username, "M1", "قصير (1m-15m)"))
    conn.commit()
    conn.close()
    
    welcome_msg = (
        "🚀 **أهلاً بك في Obeida Trading** 🚀\n\n"
        f"🆔 **معرفك:** `{user_id}`\n"
        f"👤 **اليوزر:** @{user.username or 'لا يوجد'}\n\n"
        f"📊 **حالة الاشتراك:** {'✅ ' + sub_name if status else '❌ غير مشترك'}\n"
        f"📈 **إجمالي التحليلات:** {user_info['total_analyses'] if user_info else 0}\n\n"
        f"📊 **لتحليل الشارت:** أرسل صورة الشارت\n"
    )
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "🛒 الاشتراك"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🌍 المناطق الزمنية", "🔄 تم التحديث"]
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
    
    elif user_message == "🛒 الاشتراك":
        return await show_shop(update, context)
    
    elif user_message == "🔄 تم التحديث":
        await update.message.reply_text(
            "🔄 **تم تحديث البوت!** 🔄\n\n"
            "✨ **المميزات الجديدة:**\n"
            "• نظام تحليل متطور مع شريط تقدم\n"
            "• دعم التحليل المتزامن لعدة مستخدمين\n"
            "• متجر اشتراكات متكامل\n"
            "• تحسينات في سرعة الاستجابة\n\n"
            "📊 **لتحليل الشارت:** أرسل صورة الشارت\n\n"
            "🤖 **لبدء الاستخدام:** اضغط /start",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    elif user_message == "👑 لوحة التحكم" and user_id == ADMIN_ID:
        return await start_admin_panel(update, context)
    
    elif user_message == "📊 معلومات الجلسة":
        current_session = get_current_session()
        session_info = (
            f"🌐 **معلومات الجلسة**\n\n"
            f"📍 **الجلسة الحالية:** {current_session['name']}\n"
            f"📊 **التقلب:** {current_session['volatility']}\n"
            f"🎯 **نسبة النجاح المتوقعة:** {current_session['success_rate']}\n"
            f"✨ **المميزات:** {current_session['features']}\n\n"
            f"⏰ **وقت الجلسة:** {current_session['time_range']}\n"
        )
        await update.message.reply_text(session_info, parse_mode="Markdown")
        return MAIN_MENU
    
    elif user_message == "👤 معلوماتي":
        user_info = SubscriptionSystem.get_user_info(user_id)
        status, sub_name, details = SubscriptionSystem.check_subscription(user_id)
        
        remaining = SubscriptionSystem.get_remaining_time(user_id)
        remaining_text = ""
        if remaining:
            days = remaining.days
            hours = remaining.seconds // 3600
            remaining_text = f"\n⏳ **المدة المتبقية:** {days} يوم {hours} ساعة"
        
        info_msg = (
            f"👤 **معلومات المستخدم**\n\n"
            f"🆔 **المعرف:** `{user_id}`\n"
            f"📊 **حالة الاشتراك:** {'✅ ' + sub_name if status else '❌ غير مشترك'}"
            f"{remaining_text}\n"
            f"📈 **إجمالي التحليلات:** {user_info['total_analyses'] if user_info else 0}\n"
            f"🌍 **منطقتك الزمنية:** {user_info['timezone'] if user_info else 'GMT+02:00'}\n"
        )
        await update.message.reply_text(info_msg, parse_mode="Markdown")
        return MAIN_MENU
    
    elif user_message == "⚙️ إعدادات التحليل":
        keyboard = [CANDLE_SPEEDS[i:i+3] for i in range(0, len(CANDLE_SPEEDS), 3)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        await update.message.reply_text(
            "⚙️ *إعدادات التحليل الفني*\n\nحدد سرعة الشموع:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return SETTINGS_CANDLE
    
    # إذا كانت رسالة عادية، نعود للقائمة
    keyboard = [
        ["⚙️ إعدادات التحليل", "🛒 الاشتراك"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🌍 المناطق الزمنية", "🔄 تم التحديث"]
    ]
    if user_id == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    
    await update.message.reply_text(
        "اختر أحد الخيارات من القائمة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return MAIN_MENU

async def show_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    shop_settings = SubscriptionSystem.get_shop_settings()
    
    prices_text = ""
    for sub_type, info in shop_settings['prices'].items():
        duration_text = {
            'hourly': 'ساعة', 'daily': 'يوم', 'weekly': 'أسبوع', 
            'monthly': 'شهر', 'yearly': 'سنة', 'lifetime': 'مدى الحياة'
        }.get(sub_type, info['name'])
        prices_text += f"• {duration_text}: {info['price']}{info['currency']}\n"
    
    payment_links = shop_settings['payment_links']
    
    keyboard = [
        ["🕐 ساعة", "📅 يوم", "📆 أسبوع"],
        ["📅 شهر", "📅 سنة", "♾️ مدى الحياة"],
        ["🔙 العودة"]
    ]
    
    await update.message.reply_text(
        f"🛒 **متجر الاشتراكات**\n\n"
        f"💰 **الأسعار:**\n{prices_text}\n\n"
        f"💳 **طرق الدفع:**\n"
        f"• PayPal: {payment_links.get('paypal', 'غير متوفر')}\n"
        f"• كريبتو: {payment_links.get('crypto', 'غير متوفر')}\n"
        f"• تحويل بنكي: {payment_links.get('bank', 'غير متوفر')}\n\n"
        f"📞 بعد الدفع، أرسل إيصال الدفع للمشرف: @Sz2zv\n\n"
        f"**اختر مدة الاشتراك:**",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return SELECT_DURATION

async def handle_shop_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    
    if user_message == "🔙 العودة":
        return await start(update, context)
    
    sub_map = {
        "🕐 ساعة": "hourly",
        "📅 يوم": "daily",
        "📆 أسبوع": "weekly",
        "📅 شهر": "monthly",
        "📅 سنة": "yearly",
        "♾️ مدى الحياة": "lifetime"
    }
    
    if user_message in sub_map:
        subscription_type = sub_map[user_message]
        shop_settings = SubscriptionSystem.get_shop_settings()
        price_info = shop_settings['prices'].get(subscription_type, {'price': 0, 'currency': '$'})
        
        await update.message.reply_text(
            f"✅ **تم اختيار: {user_message}**\n\n"
            f"💰 **السعر:** {price_info['price']}{price_info['currency']}\n\n"
            f"📞 بعد إتمام الدفع، أرسل إيصال الدفع مع معرف المستخدم ({update.effective_user.id}) للمشرف: @Sz2zv\n\n"
            f"سيتم تفعيل اشتراكك فور تأكيد الدفع.",
            parse_mode="Markdown"
        )
    
    return MAIN_MENU

async def handle_settings_candle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    
    if user_message in CANDLE_SPEEDS:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, "user"))
        cursor.execute("UPDATE users SET candle = ? WHERE user_id = ?", (user_message, user_id))
        conn.commit()
        conn.close()
        
        keyboard = [TRADE_TIMES[i:i+2] for i in range(0, len(TRADE_TIMES), 2)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            f"✅ **تم تعيين سرعة الشموع:** {user_message}\n\n"
            f"⬇️ الآن حدد **مدة الصفقة**:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return SETTINGS_TIME
    
    await update.message.reply_text("❌ الرجاء اختيار سرعة شموع صحيحة.")
    return SETTINGS_CANDLE

async def handle_settings_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    
    if user_message in TRADE_TIMES:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, "user"))
        cursor.execute("UPDATE users SET trade_time = ? WHERE user_id = ?", (user_message, user_id))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ مدة الصفقة: {user_message}\n\n"
            f"📤 **قم الآن برفع صورة الشارت للتحليل:**\n\n"
            f"يمكنك إرسال صورة مباشرة أو الضغط على /start للعودة للقائمة",
            reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True),
            parse_mode="Markdown"
        )
        return ANALYZE_MODE
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # التحقق من الاشتراك
    if not await check_subscription(update, user_id):
        return MAIN_MENU
    
    # التحقق من عدم وجود تحليل قيد التنفيذ
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
        # إظهار رسالة انتظار مع شريط تقدم
        wait_msg = await update.message.reply_text(
            "🤌 **جاري تحليل الصورة...** 🤌\n\n"
            "▱▱▱▱▱▱▱▱▱▱ 0%",
            parse_mode="Markdown"
        )
        
        # محاكاة شريط التقدم
        for i in range(1, 11):
            await asyncio.sleep(0.5)
            progress = "█" * i + "▱" * (10 - i)
            try:
                await wait_msg.edit_text(
                    f"🤌 **جاري تحليل الصورة...** 🤌\n\n"
                    f"{progress} {i*10}%",
                    parse_mode="Markdown"
                )
            except:
                pass
        
        # الحصول على الإعدادات
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT candle, trade_time FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        candle = result[0] if result and result[0] else "M1"
        trade_time = result[1] if result and result[1] else "قصير (1m-15m)"
        
        # تحميل الصورة
        photo = await update.message.photo[-1].get_file()
        timestamp = int(time.time())
        image_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}.jpg")
        await photo.download_to_drive(image_path)
        
        # ضغط الصورة
        compressed_path = compress_image(image_path)
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            return MAIN_MENU
        
        # إكمال شريط التقدم
        await wait_msg.edit_text(
            f"🤌 **جاري تحليل الصورة...** 🤌\n\n"
            f"██████████ 100%\n\n"
            f"✅ **جارٍ استخراج البيانات...**",
            parse_mode="Markdown"
        )
        
        # بناء البرومبت للتحليل
        current_time = datetime.utcnow()
        current_session = get_current_session()
        
        prompt = f"""
أنت محلل فني محترف. قم بتحليل الشارت المرفق بدقة.

المعلومات:
- الفريم الزمني: {candle}
- استراتيجية التداول: {trade_time}
- الجلسة الحالية: {current_session['name']}

قم بتقديم تحليل شامل يشمل:
1. الاتجاه العام (صاعد/هابط/جانبي)
2. مستويات الدعم والمقاومة الرئيسية
3. نقاط الدخول المقترحة مع الأسعار
4. نقاط الخروج (جني أرباح) مع الأسعار
5. وقف الخسارة المقترح
6. نسبة المخاطرة/المكافأة
7. نسبة نجاح متوقعة (من 0 إلى 100%)
8. ملخص الإشارة النهائية (شراء/بيع/انتظار)

الرجاء الالتزام بالتنسيق الموضح أعلاه.
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
            "max_tokens": 1500,
            "temperature": 0.3
        }
        
        await wait_msg.edit_text(
            f"🤌 **جاري تحليل الصورة...** 🤌\n\n"
            f"██████████ 100%\n\n"
            f"✅ **جارٍ الاتصال بخادم التحليل...**",
            parse_mode="Markdown"
        )
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=90)
        
        await wait_msg.edit_text(
            f"🤌 **جاري تحليل الصورة...** 🤌\n\n"
            f"██████████ 100%\n\n"
            f"✅ **تم التحليل! جاري التنسيق...**",
            parse_mode="Markdown"
        )
        
        if response.status_code == 200:
            analysis_result = response.json()['choices'][0]['message']['content']
            SubscriptionSystem.increment_analysis_count(user_id)
            
            # تنسيق النتيجة
            final_result = f"📊 **نتيجة التحليل الفني:**\n\n{analysis_result}\n\n━━━━━━━━━━━━━━━\n🤖 Obeida Trading\n📊 {current_time.strftime('%Y-%m-%d %H:%M:%S')} UTC"
            
            # إرسال النتيجة
            if len(final_result) > 4000:
                parts = split_message(final_result, 3500)
                await wait_msg.delete()
                for part in parts:
                    await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(final_result, parse_mode="Markdown")
        else:
            await wait_msg.edit_text(f"❌ **خطأ في التحليل:** {response.status_code}\n\nيرجى المحاولة مرة أخرى.")
        
        # تنظيف الملفات
        for path in [image_path, compressed_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass
                
    except requests.exceptions.Timeout:
        await update.message.reply_text("⏱️ **تجاوز الوقت المحدد للتحليل**\n\nيرجى المحاولة مرة أخرى.")
    except Exception as e:
        logger.error(f"خطأ في التحليل: {traceback.format_exc()}")
        await update.message.reply_text(f"❌ **حدث خطأ:** {str(e)[:200]}\n\nيرجى المحاولة مرة أخرى.")
    finally:
        with analysis_lock:
            active_analyses[user_id] = False
    
    # العودة للقائمة الرئيسية
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

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "🛒 الاشتراك"],
            ["📊 معلومات الجلسة", "👤 معلوماتي"],
            ["🌍 المناطق الزمنية", "🔄 تم التحديث"]
        ]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return MAIN_MENU
    
    # إذا كان المستخدم يرسل صورة في وضع التحليل
    if update.message.photo:
        return await handle_photo_analysis(update, context)
    
    await update.message.reply_text(
        "📤 **الرجاء إرسال صورة الشارت للتحليل**\nأو اضغط 'الرجوع للقائمة الرئيسية'",
        reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True)
    )
    return ANALYZE_MODE

async def timezone_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # الحصول على المنطقة الزمنية الحالية
    user_info = SubscriptionSystem.get_user_info(user_id)
    current_tz = user_info['timezone'] if user_info else "GMT+02:00"
    
    # قائمة المناطق الزمنية
    timezones = [
        "GMT-12:00", "GMT-11:00", "GMT-10:00", "GMT-09:00", "GMT-08:00", "GMT-07:00", "GMT-06:00", "GMT-05:00",
        "GMT-04:00", "GMT-03:00", "GMT-02:00", "GMT-01:00", "GMT+00:00", "GMT+01:00", "GMT+02:00", "GMT+03:00",
        "GMT+03:30", "GMT+04:00", "GMT+04:30", "GMT+05:00", "GMT+05:30", "GMT+05:45", "GMT+06:00", "GMT+06:30",
        "GMT+07:00", "GMT+08:00", "GMT+09:00", "GMT+09:30", "GMT+10:00", "GMT+11:00", "GMT+12:00", "GMT+13:00"
    ]
    
    keyboard = [[tz] for tz in timezones[:15]]
    keyboard.append(["🔙 العودة"])
    
    await update.message.reply_text(
        f"🌍 **المناطق الزمنية**\n\n"
        f"🕒 **منطقتك الحالية:** {current_tz}\n\n"
        f"اختر المنطقة الزمنية المناسبة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return TIMEZONE_MENU

async def handle_timezone_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "🔙 العودة":
        return await start(update, context)
    
    if user_message.startswith("GMT"):
        SubscriptionSystem.set_user_timezone(user_id, user_message)
        
        # تحديث المنطقة في قاعدة البيانات المحلية أيضاً
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        cursor.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (user_message, user_id))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"✅ **تم تعيين منطقتك الزمنية بنجاح!**\n\n"
            f"🕒 **المنطقة:** {user_message}\n"
            f"⏰ **التوقيت المحلي:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="Markdown"
        )
    
    return await timezone_menu(update, context)

async def start_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول للوحة التحكم.")
        return MAIN_MENU
    
    stats = SubscriptionSystem.get_stats()
    bot_status = SubscriptionSystem.get_bot_status()
    
    keyboard = [
        ["👥 عرض المستخدمين", "➕ إضافة اشتراك"],
        ["🗑️ إلغاء اشتراك", "⏱️ تمديد اشتراك"],
        ["🚫 حظر مستخدم", "✅ إلغاء حظر"],
        ["📢 إذاعة", "📊 إحصائيات"],
        ["🔧 حالة البوت", "🌐 التحكم بالجلسات"],
        ["⚙️ إعدادات المتجر", "📥 تحميل المشتركين"],
        ["🔙 العودة"]
    ]
    
    await update.message.reply_text(
        f"👑 **لوحة تحكم المشرف**\n\n"
        f"📊 **الإحصائيات:**\n"
        f"• إجمالي المستخدمين: {stats.get('total_users', 0)}\n"
        f"• المشتركين النشطين: {stats.get('active_users', 0)}\n"
        f"• المحظورين: {stats.get('blocked_users', 0)}\n"
        f"• إجمالي التحليلات: {stats.get('total_analyses', 0)}\n\n"
        f"🔧 **حالة البوت:** {bot_status['status']}\n"
        f"🌐 **الجلسات المسموحة:** {bot_status['session_control']}\n\n"
        f"اختر الإجراء المطلوب:",
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
    
    elif user_message == "👥 عرض المستخدمين":
        users = SubscriptionSystem.get_all_users()
        if not users:
            await update.message.reply_text("📭 لا يوجد مستخدمين.")
        else:
            msg = "👥 **قائمة المستخدمين:**\n\n"
            for u in users[:30]:
                uid, username, first_name, created, last_active, is_blocked, total_analyses, timezone, sub_name, end_date, is_active = u
                status = "🚫 محظور" if is_blocked else "✅ نشط" if is_active else "❌ منتهي"
                display_name = first_name or username or str(uid)
                remaining = ""
                if end_date and is_active:
                    try:
                        end = datetime.fromisoformat(end_date)
                        if end > datetime.now():
                            days = (end - datetime.now()).days
                            remaining = f" (متبقي {days} يوم)"
                    except:
                        pass
                msg += f"• {display_name}\n  🆔 `{uid}` | {sub_name or 'غير مشترك'} | {status}{remaining}\n\n"
            for part in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
                await update.message.reply_text(part, parse_mode="Markdown")
        return ADMIN_MENU
    
    elif user_message == "➕ إضافة اشتراك":
        await update.message.reply_text(
            "➕ **إضافة اشتراك**\n\nأرسل معرف المستخدم:\nمثال: `123456789`",
            parse_mode="Markdown"
        )
        return ADD_SUBSCRIPTION
    
    elif user_message == "🗑️ إلغاء اشتراك":
        await update.message.reply_text(
            "🗑️ **إلغاء اشتراك**\n\nأرسل معرف المستخدم:",
            parse_mode="Markdown"
        )
        return REMOVE_USER
    
    elif user_message == "⏱️ تمديد اشتراك":
        await update.message.reply_text(
            "⏱️ **تمديد اشتراك**\n\nأرسل معرف المستخدم والمدة (بالثواني):\nمثال: `123456789 86400`",
            parse_mode="Markdown"
        )
        return SET_USER_DURATION
    
    elif user_message == "🚫 حظر مستخدم":
        await update.message.reply_text(
            "🚫 **حظر مستخدم**\n\nأرسل معرف المستخدم:",
            parse_mode="Markdown"
        )
        return REMOVE_USER
    
    elif user_message == "✅ إلغاء حظر":
        await update.message.reply_text(
            "✅ **إلغاء حظر**\n\nأرسل معرف المستخدم:",
            parse_mode="Markdown"
        )
        return REMOVE_USER
    
    elif user_message == "📢 إذاعة":
        await update.message.reply_text(
            "📢 **إذاعة**\n\nأرسل الرسالة للإذاعة:",
            parse_mode="Markdown"
        )
        return BROADCAST_MESSAGE
    
    elif user_message == "📊 إحصائيات":
        stats = SubscriptionSystem.get_stats()
        await update.message.reply_text(
            f"📊 **إحصائيات النظام**\n\n"
            f"👥 إجمالي المستخدمين: {stats.get('total_users', 0)}\n"
            f"✅ المشتركين النشطين: {stats.get('active_users', 0)}\n"
            f"📅 نشط اليوم: {stats.get('active_today', 0)}\n"
            f"🚫 المحظورين: {stats.get('blocked_users', 0)}\n"
            f"📈 إجمالي التحليلات: {stats.get('total_analyses', 0)}",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    elif user_message == "🔧 حالة البوت":
        await update.message.reply_text(
            "🔧 **حالة البوت**\n\n"
            f"✅ البوت يعمل بشكل طبيعي\n"
            f"🌐 الجلسات المسموحة: جميع الجلسات\n"
            f"📊 عدد المستخدمين النشطين: {SubscriptionSystem.get_stats().get('active_users', 0)}",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    elif user_message == "🌐 التحكم بالجلسات":
        await update.message.reply_text(
            "🌐 **التحكم بالجلسات**\n\n"
            f"حالياً: جميع الجلسات مسموحة\n\n"
            f"يمكنك تغيير الإعدادات من الكود المصدري.",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    elif user_message == "⚙️ إعدادات المتجر":
        shop_settings = SubscriptionSystem.get_shop_settings()
        await update.message.reply_text(
            f"⚙️ **إعدادات المتجر**\n\n"
            f"💰 **الأسعار الحالية:**\n" + 
            "\n".join([f"• {info['name']}: {info['price']}{info['currency']}" for info in shop_settings['prices'].values()]) + 
            f"\n\n💳 **روابط الدفع:**\n"
            f"• PayPal: {shop_settings['payment_links'].get('paypal', 'غير متوفر')}\n"
            f"• كريبتو: {shop_settings['payment_links'].get('crypto', 'غير متوفر')}\n"
            f"• بنكي: {shop_settings['payment_links'].get('bank', 'غير متوفر')}",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    elif user_message == "📥 تحميل المشتركين":
        csv_data = SubscriptionSystem.export_users_to_csv()
        from io import BytesIO
        file = BytesIO(csv_data.encode('utf-8'))
        await update.message.reply_document(
            document=file,
            filename=f"subscribers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            caption="📊 **قائمة المشتركين**\n\nتم تصدير البيانات بنجاح."
        )
        return ADMIN_MENU
    
    return ADMIN_MENU

async def handle_add_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return MAIN_MENU
    
    try:
        target_user_id = int(update.message.text.strip())
        context.user_data['target_user_id'] = target_user_id
        
        keyboard = [
            ["🕐 ساعة", "📅 يوم", "📆 أسبوع"],
            ["📅 شهر", "📅 سنة", "♾️ مدى الحياة"],
            ["🔙 إلغاء"]
        ]
        
        await update.message.reply_text(
            f"➕ **إضافة اشتراك للمستخدم:** `{target_user_id}`\n\nاختر المدة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return SELECT_DURATION
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال معرف المستخدم (أرقام فقط).")
        return ADD_SUBSCRIPTION

async def handle_select_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    duration_choice = update.message.text.strip()
    
    if user_id != ADMIN_ID:
        return MAIN_MENU
    
    if duration_choice == "🔙 إلغاء":
        return await start_admin_panel(update, context)
    
    sub_map = {
        "🕐 ساعة": "hourly",
        "📅 يوم": "daily",
        "📆 أسبوع": "weekly",
        "📅 شهر": "monthly",
        "📅 سنة": "yearly",
        "♾️ مدى الحياة": "lifetime"
    }
    
    if duration_choice in sub_map:
        subscription_type = sub_map[duration_choice]
        target_user_id = context.user_data.get('target_user_id')
        
        if not target_user_id:
            await update.message.reply_text("❌ خطأ: لم يتم تحديد المستخدم.")
            return ADMIN_MENU
        
        success, result = SubscriptionSystem.add_subscription(target_user_id, subscription_type, added_by=user_id)
        
        if success:
            end_info = f"ينتهي في: {datetime.fromisoformat(result['end_date']).strftime('%Y-%m-%d %H:%M')}" if result.get('end_date') else "غير محدد (مدى الحياة)"
            await update.message.reply_text(
                f"✅ **تم إضافة الاشتراك بنجاح!**\n\n"
                f"👤 المستخدم: `{target_user_id}`\n"
                f"📅 النوع: {result['subscription_name']}\n"
                f"{end_info}\n"
                f"💰 السعر: {result['price']}{result['currency']}",
                parse_mode="Markdown"
            )
            
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"✅ **تم تفعيل اشتراكك!**\n\n"
                         f"📅 نوع الاشتراك: {result['subscription_name']}\n"
                         f"{end_info}\n\n"
                         f"أرسل /start لبدء الاستخدام",
                    parse_mode="Markdown"
                )
            except:
                pass
        else:
            await update.message.reply_text(f"❌ فشل في إضافة الاشتراك: {result}")
        
        context.user_data.pop('target_user_id', None)
        return ADMIN_MENU
    
    await update.message.reply_text("❌ اختيار غير صالح.")
    return SELECT_DURATION

async def handle_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return MAIN_MENU
    
    try:
        target_user_id = int(update.message.text.strip())
        
        last_action = context.user_data.get('last_action')
        
        if last_action == 'block':
            # حظر المستخدم
            conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (target_user_id,))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ تم حظر المستخدم `{target_user_id}`")
            context.user_data['last_action'] = None
        elif last_action == 'unblock':
            # إلغاء حظر المستخدم
            conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (target_user_id,))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ تم إلغاء حظر المستخدم `{target_user_id}`")
            context.user_data['last_action'] = None
        else:
            # إلغاء الاشتراك
            conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE active_subscriptions SET is_active = 0 WHERE user_id = ? AND is_active = 1", (target_user_id,))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ تم إلغاء اشتراك المستخدم `{target_user_id}`")
            
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text="⚠️ **تم إلغاء اشتراكك في البوت**\n\nللتواصل مع المشرف: @Sz2zv",
                    parse_mode="Markdown"
                )
            except:
                pass
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال معرف المستخدم (أرقام فقط).")
    
    return ADMIN_MENU

async def handle_set_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return MAIN_MENU
    
    try:
        parts = update.message.text.strip().split()
        if len(parts) != 2:
            await update.message.reply_text("❌ صيغة خاطئة. استخدم: `user_id 86400`")
            return SET_USER_DURATION
        
        target_user_id = int(parts[0])
        additional_seconds = int(parts[1])
        
        # تمديد الاشتراك
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT end_date FROM active_subscriptions WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1", (target_user_id,))
        result = cursor.fetchone()
        
        if result and result[0]:
            end_date = datetime.fromisoformat(result[0])
            new_end_date = end_date + timedelta(seconds=additional_seconds)
        else:
            new_end_date = datetime.now() + timedelta(seconds=additional_seconds)
        
        cursor.execute("UPDATE active_subscriptions SET end_date = ? WHERE user_id = ? AND is_active = 1", (new_end_date.isoformat(), target_user_id))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ تم تمديد اشتراك المستخدم `{target_user_id}` حتى {new_end_date.strftime('%Y-%m-%d %H:%M')}")
        
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"✅ **تم تمديد اشتراكك!**\n\nينتهي في: {new_end_date.strftime('%Y-%m-%d %H:%M')}",
                parse_mode="Markdown"
            )
        except:
            pass
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال معرف المستخدم والمدة بشكل صحيح.")
    
    return ADMIN_MENU

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    broadcast_message = update.message.text.strip()
    
    if user_id != ADMIN_ID:
        return MAIN_MENU
    
    users = SubscriptionSystem.get_all_users()
    
    if not users:
        await update.message.reply_text("📭 لا يوجد مستخدمين للإذاعة.")
        return ADMIN_MENU
    
    wait_msg = await update.message.reply_text(f"📢 جاري الإذاعة لـ {len(users)} مستخدم...")
    
    success_count = 0
    fail_count = 0
    
    for user in users:
        target_id = user[0]
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"📢 **رسالة إدارية**\n\n{broadcast_message}\n\n━━━━━━━━━━━━\nObeida Trading",
                parse_mode="Markdown"
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except:
            fail_count += 1
    
    await wait_msg.edit_text(
        f"📊 **نتيجة الإذاعة**\n\n"
        f"✅ تم الإرسال بنجاح لـ: {success_count} مستخدم\n"
        f"❌ فشل الإرسال لـ: {fail_count} مستخدم"
    )
    
    return ADMIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 **أوامر البوت:**

/start - بدء البوت
/help - عرض المساعدة
/cancel - إلغاء العملية الحالية

⚙️ **كيفية الاستخدام:**
1. استخدم أزرار القائمة للتنقل
2. اضبط إعدادات التحليل أولاً
3. أرسل صورة الشارت للتحليل

🛒 **الاشتراك:**
• اختر "🛒 الاشتراك" من القائمة
• اختر المدة المناسبة
• تواصل مع المشرف لإتمام الدفع

📞 للتواصل: @Sz2zv
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    with analysis_lock:
        if user_id in active_analyses:
            active_analyses[user_id] = False
    
    await update.message.reply_text(
        "✅ تم إلغاء العملية الحالية.\nاكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"خطأ: {context.error}")
    print(f"❌ خطأ: {context.error}")

# ============================================
# Flask Server
# ============================================

app = Flask(__name__)
app.secret_key = SECRET_KEY

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

LOGIN_PAGE_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <title>🔐 تسجيل الدخول - Obeida Trading</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; justify-content: center; align-items: center; }
        .login-container { background: white; border-radius: 20px; padding: 40px; width: 90%; max-width: 400px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); }
        .login-header { text-align: center; margin-bottom: 30px; }
        .login-header h1 { color: #333; font-size: 2em; margin-bottom: 10px; }
        .logo { font-size: 4em; margin-bottom: 20px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 5px; color: #666; font-weight: 600; }
        .form-group input { width: 100%; padding: 12px; border: 2px solid #f0f0f0; border-radius: 10px; font-size: 1em; }
        .form-group input:focus { outline: none; border-color: #667eea; }
        .btn-login { width: 100%; padding: 14px; background: #667eea; color: white; border: none; border-radius: 10px; font-size: 1.1em; font-weight: 600; cursor: pointer; }
        .btn-login:hover { background: #5a67d8; }
        .error-message { background: #f8d7da; color: #721c24; padding: 10px; border-radius: 10px; margin-bottom: 20px; text-align: center; }
        .footer { text-align: center; margin-top: 20px; color: #999; font-size: 0.8em; }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="login-header">
            <div class="logo">🤖</div>
            <h1>Obeida Trading</h1>
            <p>نظام إدارة البوت المتطور</p>
        </div>
        {% if error %}
        <div class="error-message">{{ error }}</div>
        {% endif %}
        <form method="POST" action="/login">
            <div class="form-group">
                <label>👤 اسم المستخدم</label>
                <input type="text" name="username" required autofocus>
            </div>
            <div class="form-group">
                <label>🔑 كلمة المرور</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit" class="btn-login">تسجيل الدخول</button>
        </form>
        <div class="footer">
            <p>© 2026 Obeida Trading - جميع الحقوق محفوظة</p>
        </div>
    </div>
</body>
</html>
"""

ADMIN_PANEL_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <title>👑 لوحة التحكم - Obeida Trading</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { background: white; border-radius: 20px; padding: 30px; margin-bottom: 30px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #333; font-size: 2.2em; }
        .logout-btn { background: #dc3545; color: white; padding: 10px 20px; border-radius: 10px; text-decoration: none; font-weight: 600; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: white; border-radius: 15px; padding: 20px; text-align: center; box-shadow: 0 5px 20px rgba(0,0,0,0.05); }
        .stat-card .number { font-size: 2.5em; font-weight: bold; color: #667eea; }
        .stat-card .label { color: #666; margin-top: 5px; }
        .section { background: white; border-radius: 20px; padding: 30px; margin-bottom: 30px; }
        .section-title { font-size: 1.5em; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #f0f0f0; }
        .table-container { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; }
        th { background: #667eea; color: white; padding: 12px; }
        td { padding: 12px; border-bottom: 1px solid #f0f0f0; text-align: center; }
        .status-badge { padding: 5px 12px; border-radius: 20px; font-size: 0.9em; display: inline-block; }
        .status-active { background: #d4edda; color: #155724; }
        .status-expired { background: #f8d7da; color: #721c24; }
        .status-blocked { background: #f8d7da; color: #721c24; }
        .footer { text-align: center; color: white; margin-top: 30px; }
        .current-time { font-size: 0.9em; color: #666; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🤖 لوحة تحكم Obeida Trading</h1>
                <div class="current-time" id="currentTime"></div>
            </div>
            <a href="/logout" class="logout-btn">تسجيل الخروج</a>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="number">{{ stats.total_users }}</div>
                <div class="label">👥 إجمالي المستخدمين</div>
            </div>
            <div class="stat-card">
                <div class="number">{{ stats.active_users }}</div>
                <div class="label">✅ المشتركين النشطين</div>
            </div>
            <div class="stat-card">
                <div class="number">{{ stats.total_analyses }}</div>
                <div class="label">📊 إجمالي التحليلات</div>
            </div>
            <div class="stat-card">
                <div class="number">{{ stats.active_today }}</div>
                <div class="label">📅 نشط اليوم</div>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">👥 قائمة المستخدمين</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>المعرف</th>
                            <th>الاسم</th>
                            <th>الاشتراك</th>
                            <th>تحليلات</th>
                            <th>تاريخ الانتهاء</th>
                            <th>الحالة</th>
                            <th>آخر نشاط</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for user in users %}
                        <tr>
                            <td>{{ user.id }}</td>
                            <td>{{ user.name }}</td>
                            <td>{{ user.subscription_type }}</td>
                            <td>{{ user.total_analyses }}</td>
                            <td>{{ user.end_date }}</td>
                            <td>
                                <span class="status-badge status-{{ user.status_class }}">
                                    {{ user.status }}
                                </span>
                            </td>
                            <td>{{ user.last_active }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="footer">
            <p>🤖 Obeida Trading - نظام إدارة الاشتراكات المتطور V2.1</p>
        </div>
    </div>

    <script>
        function updateCurrentTime() {
            const now = new Date();
            document.getElementById('currentTime').innerHTML = `🕒 UTC: ${now.toUTCString()} | 📍 محلي: ${now.toLocaleString('ar-EG')}`;
        }
        setInterval(updateCurrentTime, 1000);
        updateCurrentTime();
    </script>
</body>
</html>
"""

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USERNAME and hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('admin_panel'))
        else:
            return render_template_string(LOGIN_PAGE_HTML, error='بيانات الدخول غير صحيحة')
    
    return render_template_string(LOGIN_PAGE_HTML)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def home():
    return redirect(url_for('admin_panel'))

@app.route('/admin')
@login_required
def admin_panel():
    stats = SubscriptionSystem.get_stats()
    users_data = SubscriptionSystem.get_all_users()
    
    formatted_users = []
    for u in users_data[:100]:
        uid, username, first_name, created, last_active, is_blocked, total_analyses, timezone, sub_name, end_date, is_active = u
        
        if is_blocked == 1:
            status = "محظور"
            status_class = "blocked"
        elif is_active == 1 and (not end_date or datetime.fromisoformat(end_date) > datetime.now()):
            status = "نشط"
            status_class = "active"
        else:
            status = "منتهي"
            status_class = "expired"
        
        end_display = end_date[:16] if end_date else "غير محدد"
        if end_date and datetime.fromisoformat(end_date) < datetime.now():
            end_display += " (منتهي)"
        
        formatted_users.append({
            'id': uid,
            'name': first_name or username or str(uid),
            'subscription_type': sub_name or 'غير مشترك',
            'total_analyses': total_analyses,
            'end_date': end_display,
            'status': status,
            'status_class': status_class,
            'last_active': last_active[:16] if last_active else '-'
        })
    
    return render_template_string(ADMIN_PANEL_HTML, stats=stats, users=formatted_users)

@app.route('/health')
def health():
    stats = SubscriptionSystem.get_stats()
    return {
        "status": "active",
        "total_users": stats.get('total_users', 0),
        "active_users": stats.get('active_users', 0),
        "total_analyses": stats.get('total_analyses', 0),
        "timestamp": time.time()
    }

def run_flask_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ============================================
# التشغيل الرئيسي
# ============================================

async def periodic_tasks():
    while True:
        try:
            await asyncio.sleep(3600)
            if os.path.exists(IMAGE_CACHE_DIR):
                for f in os.listdir(IMAGE_CACHE_DIR):
                    filepath = os.path.join(IMAGE_CACHE_DIR, f)
                    if os.path.isfile(filepath):
                        try:
                            os.remove(filepath)
                        except:
                            pass
            print("🧹 تم تنظيف الملفات المؤقتة")
        except Exception as e:
            print(f"⚠️ خطأ: {e}")

async def news_radar_task():
    """تشغيل رادار الأخبار"""
    while True:
        try:
            await asyncio.sleep(60)
            # يمكن إضافة منطق جلب الأخبار هنا
        except:
            pass

def main():
    print("🤖 Starting Obeida Trading Bot...")
    
    # تهيئة قواعد البيانات
    SubscriptionSystem.init_db()
    
    # إنشاء قاعدة البيانات المحلية
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            candle TEXT DEFAULT 'M1',
            trade_time TEXT DEFAULT 'قصير (1m-15m)',
            timezone TEXT DEFAULT 'GMT+02:00',
            last_analysis_context TEXT
        )
    ''')
    conn.commit()
    conn.close()
    
    for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
        if not os.path.exists(directory):
            os.makedirs(directory)
    
    # تشغيل Flask في خلفية
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    # إعداد البوت
    application = Application.builder().token(TOKEN).build()
    
    # محادثة رئيسية
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu)],
            SETTINGS_CANDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_candle)],
            SETTINGS_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_time)],
            ANALYZE_MODE: [MessageHandler(filters.PHOTO, handle_photo_analysis), MessageHandler(filters.TEXT & ~filters.COMMAND, handle_analyze_mode)],
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            ADD_SUBSCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_subscription)],
            SELECT_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_select_duration)],
            REMOVE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_user)],
            SET_USER_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_duration)],
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)],
            SET_BOT_STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            SET_SESSION_CONTROL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            TIMEZONE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timezone_selection)],
            SEARCH_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timezone_selection)],
            AFFILIATES_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            ADD_AFFILIATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            SHOP_SETTINGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            IMPORT_USERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
        },
        fallbacks=[CommandHandler('cancel', cancel), CommandHandler('start', start)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_error_handler(error_handler)
    
    # تشغيل المهام الخلفية
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(periodic_tasks())
    loop.create_task(news_radar_task())
    
    print("✅ البوت جاهز للعمل!")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
