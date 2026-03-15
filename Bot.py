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
import pickle
import hashlib
import random
import string
import uuid
import secrets
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.error import NetworkError, TimedOut
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for, flash
from functools import wraps
from PIL import Image
import pytz

# --- الإعدادات الأساسية ---
TOKEN = os.environ.get('TOKEN', "8583903565:AAFveu3KYGkPsC8L6WyermoaZ1tI_EtFHHs")

MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "FcKi7dASoXlT5aLDHS1IzlOmFUEPmyWZ")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "pixtral-large-latest"          # للرؤية والاستخراج البصري
MISTRAL_MODEL_AUDIT = "mistral-large-latest"        # للتحليل المنطقي
MODEL_SUMMARY = "mistral-medium-latest"        # للتلخيص السريع

DB_NAME = "abood-gpt.db"
BACKUP_DIR = "backups"
SUBSCRIPTION_DB_NAME = "subscription_system.db"  # قاعدة بيانات نظام الاشتراك الجديدة
DATA_VERSION = "2.0.0"  # نسخة البيانات للحماية من التغييرات

# معرف المشرف الرئيسي
ADMIN_ID = 6207431030

# إعدادات Flask
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'Obeida059')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', hashlib.sha256('Obeida059'.encode()).hexdigest())  # تشفير كلمة المرور

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

# ============================================
# نظام المناطق الزمنية حسب الدولة (NEW)
# ============================================

# قائمة الدول مع المناطق الزمنية الخاصة بها
COUNTRIES_TIMEZONES = {
    "فلسطين 🇵🇸": "Asia/Gaza",
    "مصر 🇪🇬": "Africa/Cairo",
    "السعودية 🇸🇦": "Asia/Riyadh",
    "الإمارات 🇦🇪": "Asia/Dubai",
    "قطر 🇶🇦": "Asia/Qatar",
    "الكويت 🇰🇼": "Asia/Kuwait",
    "البحرين 🇧🇭": "Asia/Bahrain",
    "عمان 🇴🇲": "Asia/Muscat",
    "اليمن 🇾🇪": "Asia/Aden",
    "العراق 🇮🇶": "Asia/Baghdad",
    "الأردن 🇯🇴": "Asia/Amman",
    "لبنان 🇱🇧": "Asia/Beirut",
    "سوريا 🇸🇾": "Asia/Damascus",
    "ليبيا 🇱🇾": "Africa/Tripoli",
    "تونس 🇹🇳": "Africa/Tunis",
    "الجزائر 🇩🇿": "Africa/Algiers",
    "المغرب 🇲🇦": "Africa/Casablanca",
    "موريتانيا 🇲🇷": "Africa/Nouakchott",
    "السودان 🇸🇩": "Africa/Khartoum",
    "الصومال 🇸🇴": "Africa/Mogadishu",
    "جيبوتي 🇩🇯": "Africa/Djibouti",
    "جزر القمر 🇰🇲": "Indian/Comoro",
    "تركيا 🇹🇷": "Europe/Istanbul",
    "إيران 🇮🇷": "Asia/Tehran",
    "باكستان 🇵🇰": "Asia/Karachi",
    "أفغانستان 🇦🇫": "Asia/Kabul",
    "الهند 🇮🇳": "Asia/Kolkata",
    "بنجلاديش 🇧🇩": "Asia/Dhaka",
    "إندونيسيا 🇮🇩": "Asia/Jakarta",
    "ماليزيا 🇲🇾": "Asia/Kuala_Lumpur",
    "تايلاند 🇹🇭": "Asia/Bangkok",
    "سنغافورة 🇸🇬": "Asia/Singapore",
    "الصين 🇨🇳": "Asia/Shanghai",
    "اليابان 🇯🇵": "Asia/Tokyo",
    "كوريا الجنوبية 🇰🇷": "Asia/Seoul",
    "أستراليا 🇦🇺": "Australia/Sydney",
    "نيوزيلندا 🇳🇿": "Pacific/Auckland",
    "المملكة المتحدة 🇬🇧": "Europe/London",
    "ألمانيا 🇩🇪": "Europe/Berlin",
    "فرنسا 🇫🇷": "Europe/Paris",
    "إيطاليا 🇮🇹": "Europe/Rome",
    "إسبانيا 🇪🇸": "Europe/Madrid",
    "هولندا 🇳🇱": "Europe/Amsterdam",
    "بلجيكا 🇧🇪": "Europe/Brussels",
    "النمسا 🇦🇹": "Europe/Vienna",
    "سويسرا 🇨🇭": "Europe/Zurich",
    "السويد 🇸🇪": "Europe/Stockholm",
    "النرويج 🇳🇴": "Europe/Oslo",
    "الدنمارك 🇩🇰": "Europe/Copenhagen",
    "فنلندا 🇫🇮": "Europe/Helsinki",
    "روسيا 🇷🇺": "Europe/Moscow",
    "أوكرانيا 🇺🇦": "Europe/Kiev",
    "بولندا 🇵🇱": "Europe/Warsaw",
    "اليونان 🇬🇷": "Europe/Athens",
    "البرتغال 🇵🇹": "Europe/Lisbon",
    "كندا 🇨🇦": "America/Toronto",
    "الولايات المتحدة 🇺🇸 (نيويورك)": "America/New_York",
    "الولايات المتحدة 🇺🇸 (شيكاغو)": "America/Chicago",
    "الولايات المتحدة 🇺🇸 (دنفر)": "America/Denver",
    "الولايات المتحدة 🇺🇸 (لوس أنجلوس)": "America/Los_Angeles",
    "المكسيك 🇲🇽": "America/Mexico_City",
    "البرازيل 🇧🇷": "America/Sao_Paulo",
    "الأرجنتين 🇦🇷": "America/Argentina/Buenos_Aires",
    "تشيلي 🇨🇱": "America/Santiago",
    "كولومبيا 🇨🇴": "America/Bogota",
    "فنزويلا 🇻🇪": "America/Caracas",
    "بيرو 🇵🇪": "America/Lima",
    "جنوب أفريقيا 🇿🇦": "Africa/Johannesburg",
    "نيجيريا 🇳🇬": "Africa/Lagos",
    "كينيا 🇰🇪": "Africa/Nairobi",
    "إثيوبيا 🇪🇹": "Africa/Addis_Ababa"
}

# قائمة الدول العربية المختصرة للعرض السريع
ARAB_COUNTRIES = {
    "فلسطين 🇵🇸": "Asia/Gaza",
    "مصر 🇪🇬": "Africa/Cairo",
    "السعودية 🇸🇦": "Asia/Riyadh",
    "الإمارات 🇦🇪": "Asia/Dubai",
    "قطر 🇶🇦": "Asia/Qatar",
    "الكويت 🇰🇼": "Asia/Kuwait",
    "البحرين 🇧🇭": "Asia/Bahrain",
    "عمان 🇴🇲": "Asia/Muscat",
    "اليمن 🇾🇪": "Asia/Aden",
    "العراق 🇮🇶": "Asia/Baghdad",
    "الأردن 🇴🇯": "Asia/Amman",
    "لبنان 🇱🇧": "Asia/Beirut",
    "سوريا 🇸🇾": "Asia/Damascus",
    "ليبيا 🇾🇱": "Africa/Tripoli",
    "تونس 🇹🇳": "Africa/Tunis",
    "الجزائر 🇩🇿": "Africa/Algiers",
    "المغرب 🇲🇦": "Africa/Casablanca",
    "موريتانيا 🇲🇷": "Africa/Nouakchott",
    "السودان 🇸🇩": "Africa/Khartoum",
    "الصومال 🇸🇴": "Africa/Mogadishu",
    "جيبوتي 🇩🇯": "Africa/Djibouti",
    "جزر القمر 🇰🇲": "Indian/Comoro"
}

# الدول الرئيسية للعرض السريع
MAJOR_COUNTRIES = {
    "🇺🇸 أمريكا (نيويورك)": "America/New_York",
    "🇬🇧 بريطانيا": "Europe/London",
    "🇯🇵 اليابان": "Asia/Tokyo",
    "🇨🇳 الصين": "Asia/Shanghai",
    "🇦🇺 أستراليا": "Australia/Sydney",
    "🇩🇪 ألمانيا": "Europe/Berlin",
    "🇫🇷 فرنسا": "Europe/Paris",
    "🇷🇺 روسيا": "Europe/Moscow",
    "🇨🇦 كندا": "America/Toronto",
    "🇧🇷 البرازيل": "America/Sao_Paulo",
    "🇮🇳 الهند": "Asia/Kolkata",
    "🇹🇷 تركيا": "Europe/Istanbul",
    "🇿🇦 جنوب أفريقيا": "Africa/Johannesburg"
}

# دالة للحصول على المنطقة الزمنية للمستخدم
def get_user_timezone(user_id):
    """الحصول على المنطقة الزمنية للمستخدم من قاعدة البيانات"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # التحقق من وجود عمود country_timezone
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'country_timezone' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN country_timezone TEXT DEFAULT 'Asia/Gaza'")
        conn.commit()
    
    cursor.execute("SELECT country_timezone FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        return result[0]
    return "Asia/Gaza"  # القيمة الافتراضية

def set_user_timezone(user_id, timezone):
    """تعيين المنطقة الزمنية للمستخدم"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # التحقق من وجود المستخدم
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
    
    cursor.execute("UPDATE users SET country_timezone = ? WHERE user_id = ?", (timezone, user_id))
    conn.commit()
    conn.close()
    
    log_data_change("update_timezone", user_id, f"timezone={timezone}")
    return True

def get_localized_time(user_id):
    """الحصول على الوقت المحلي حسب منطقة المستخدم"""
    try:
        timezone_str = get_user_timezone(user_id)
        user_tz = pytz.timezone(timezone_str)
        return datetime.now(user_tz)
    except Exception as e:
        print(f"⚠️ خطأ في الحصول على الوقت المحلي: {e}")
        return datetime.now(pytz.timezone('Asia/Gaza'))  # الرجوع للافتراضي

# تعديل دالة get_market_session لاستخدام المنطقة الزمنية للمستخدم
def get_market_session(user_id=None):
    """الحصول على جلسة السوق باستخدام المنطقة الزمنية للمستخدم"""
    try:
        if user_id:
            local_time = get_localized_time(user_id)
        else:
            local_time = datetime.now(pytz.timezone('Asia/Gaza'))
        
        current_hour = local_time.hour
        timezone_name = get_user_timezone(user_id) if user_id else "Asia/Gaza"
        
        # تحديد الجلسة حسب التوقيت العالمي
        if 2 <= current_hour < 8:
            return "الجلسة الآسيوية", f"{current_hour:02d}:00-{current_hour+6:02d}:00 بالتوقيت المحلي", "منخفضة", timezone_name
        elif 8 <= current_hour < 14:
            return "جلسة لندن/أوروبا", f"{current_hour:02d}:00-{current_hour+6:02d}:00 بالتوقيت المحلي", "مرتفعة", timezone_name
        elif 14 <= current_hour < 20:
            return "جلسة نيويورك", f"{current_hour:02d}:00-{current_hour+6:02d}:00 بالتوقيت المحلي", "عالية جداً", timezone_name
        elif 20 <= current_hour < 24 or 0 <= current_hour < 2:
            return "جلسة المحيط الهادئ", f"{20 if current_hour>=20 else 0:02d}:00-{2 if current_hour>=20 else 8:02d}:00 بالتوقيت المحلي", "منخفضة", timezone_name
        else:
            return "جلسة عالمية", "متداخلة", "متوسطة", timezone_name
    except Exception as e:
        print(f"⚠️ خطأ في تحديد جلسة السوق: {e}")
        return "جلسة عالمية", "غير محددة", "متوسطة", "Asia/Gaza"

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
IMAGE_CACHE_DIR = "image_cache"
MAX_IMAGE_SIZE = (1024, 1024)  # أقصى حجم للصورة بعد الضغط
IMAGE_QUALITY = 95  # جودة الصورة بعد الضغط (من 0-100)

# حالات المحادثة (للنسخة الجديدة V2)
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION, ADMIN_MENU, ADD_SUBSCRIPTION, SELECT_DURATION, REMOVE_USER, BROADCAST_MESSAGE, SET_USER_DURATION, COUNTRY_SELECTION = range(14)

# إنشاء المجلدات الضرورية
for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"📁 تم إنشاء المجلد: {directory}")

# ============================================
# نظام الاشتراك المتطور الجديد (Subscription System V2)
# ============================================

class SubscriptionSystem:
    """
    نظام اشتراك متطور وآمن بالكامل
    """
    
    # أنواع الاشتراكات
    SUBSCRIPTION_TYPES = {
        'trial': {'name': 'تجريبي', 'duration': 86400, 'features': ['basic_analysis']},
        'hourly': {'name': 'ساعة', 'duration': 3600, 'features': ['basic_analysis']},
        'daily': {'name': 'يوم', 'duration': 86400, 'features': ['basic_analysis', 'chat']},
        'weekly': {'name': 'أسبوع', 'duration': 604800, 'features': ['basic_analysis', 'chat', 'recommendations']},
        'monthly': {'name': 'شهر', 'duration': 2592000, 'features': ['basic_analysis', 'chat', 'recommendations', 'advanced_analysis']},
        'yearly': {'name': 'سنة', 'duration': 31536000, 'features': ['basic_analysis', 'chat', 'recommendations', 'advanced_analysis', 'priority']},
        'lifetime': {'name': 'مدى الحياة', 'duration': -1, 'features': ['all']}
    }
    
    @staticmethod
    def init_db():
        """تهيئة قاعدة بيانات نظام الاشتراك المتطور"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        # جدول المستخدمين الرئيسي
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                is_bot BOOLEAN DEFAULT 0,
                is_premium BOOLEAN DEFAULT 0,
                added_to_attachment_menu BOOLEAN DEFAULT 0,
                can_join_groups BOOLEAN DEFAULT 1,
                can_read_all_group_messages BOOLEAN DEFAULT 0,
                supports_inline_queries BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_blocked BOOLEAN DEFAULT 0,
                blocked_reason TEXT,
                metadata TEXT DEFAULT '{}'
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
                auto_renew BOOLEAN DEFAULT 0,
                payment_method TEXT,
                payment_reference TEXT,
                price REAL,
                currency TEXT DEFAULT 'USD',
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (added_by) REFERENCES users(user_id)
            )
        ''')
        
        # جدول سجل الاشتراكات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscription_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                old_subscription TEXT,
                new_subscription TEXT,
                reason TEXT,
                performed_by INTEGER,
                performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (performed_by) REFERENCES users(user_id)
            )
        ''')
        
        # جدول الصلاحيات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                permission TEXT NOT NULL,
                granted_by INTEGER,
                granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (granted_by) REFERENCES users(user_id),
                UNIQUE(user_id, permission)
            )
        ''')
        
        # جدول إحصائيات الاستخدام
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usage_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                feature TEXT NOT NULL,
                count INTEGER DEFAULT 0,
                last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_time_used REAL DEFAULT 0,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                UNIQUE(user_id, feature)
            )
        ''')
        
        # جدول سجل التغييرات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS change_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                record_id INTEGER,
                action TEXT NOT NULL,
                old_data TEXT,
                new_data TEXT,
                changed_by INTEGER,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}'
            )
        ''')
        
        conn.commit()
        conn.close()
        
        # إنشاء فهارس لتحسين الأداء
        SubscriptionSystem.create_indexes()
        
        print("✅ تم تهيئة نظام الاشتراك المتطور بنجاح")
    
    @staticmethod
    def create_indexes():
        """إنشاء فهارس لتحسين أداء قاعدة البيانات"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)",
            "CREATE INDEX IF NOT EXISTS idx_users_created ON users(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_active_subscriptions_user ON active_subscriptions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_active_subscriptions_active ON active_subscriptions(is_active)",
            "CREATE INDEX IF NOT EXISTS idx_active_subscriptions_end ON active_subscriptions(end_date)",
            "CREATE INDEX IF NOT EXISTS idx_subscription_history_user ON subscription_history(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_subscription_history_time ON subscription_history(performed_at)",
            "CREATE INDEX IF NOT EXISTS idx_permissions_user ON permissions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_usage_stats_user ON usage_stats(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_change_log_table ON change_log(table_name)",
            "CREATE INDEX IF NOT EXISTS idx_change_log_time ON change_log(changed_at)"
        ]
        
        for index in indexes:
            try:
                cursor.execute(index)
            except:
                pass
        
        conn.commit()
        conn.close()
        print("📊 تم إنشاء فهارس قاعدة البيانات")
    
    @staticmethod
    def register_user(user_id, username=None, first_name=None, last_name=None, language_code=None, is_bot=False):
        """تسجيل مستخدم جديد في النظام"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            # التحقق من وجود المستخدم
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if cursor.fetchone():
                # تحديث وقت آخر نشاط
                cursor.execute("""
                    UPDATE users 
                    SET last_active = CURRENT_TIMESTAMP,
                        username = COALESCE(?, username),
                        first_name = COALESCE(?, first_name),
                        last_name = COALESCE(?, last_name),
                        language_code = COALESCE(?, language_code)
                    WHERE user_id = ?
                """, (username, first_name, last_name, language_code, user_id))
                
                conn.commit()
                return True, "مستخدم موجود بالفعل"
            
            # إضافة مستخدم جديد
            cursor.execute("""
                INSERT INTO users (
                    user_id, username, first_name, last_name, language_code, is_bot
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, username, first_name, last_name, language_code, is_bot))
            
            conn.commit()
            
            # تسجيل الحدث
            SubscriptionSystem.log_change('users', user_id, 'INSERT', 
                                        old_data=None, 
                                        new_data={'user_id': user_id, 'username': username},
                                        changed_by=user_id)
            
            return True, "تم تسجيل المستخدم بنجاح"
            
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def add_subscription(user_id, subscription_type, added_by=ADMIN_ID, reason=""):
        """إضافة اشتراك مباشر لمستخدم"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            # بدء المعاملة
            cursor.execute("BEGIN TRANSACTION")
            
            # التحقق من صحة نوع الاشتراك
            if subscription_type not in SubscriptionSystem.SUBSCRIPTION_TYPES:
                conn.rollback()
                return False, "نوع اشتراك غير صالح"
            
            # التحقق من وجود المستخدم
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if not cursor.fetchone():
                # تسجيل المستخدم إذا لم يكن موجوداً
                cursor.execute("""
                    INSERT INTO users (user_id) VALUES (?)
                """, (user_id,))
            
            # الحصول على مدة الاشتراك
            duration = SubscriptionSystem.SUBSCRIPTION_TYPES[subscription_type]['duration']
            
            # حساب تاريخ انتهاء الاشتراك
            start_date = datetime.now()
            end_date = None
            if duration > 0:
                end_date = start_date + timedelta(seconds=duration)
            
            # إلغاء أي اشتراكات سابقة نشطة
            cursor.execute("""
                UPDATE active_subscriptions 
                SET is_active = 0
                WHERE user_id = ? AND is_active = 1
            """, (user_id,))
            
            # إنشاء اشتراك جديد
            sub_name = SubscriptionSystem.SUBSCRIPTION_TYPES[subscription_type]['name']
            cursor.execute("""
                INSERT INTO active_subscriptions (
                    user_id, subscription_type, subscription_name, 
                    start_date, end_date, is_active, added_by
                ) VALUES (?, ?, ?, ?, ?, 1, ?)
            """, (user_id, subscription_type, sub_name, 
                  start_date.isoformat(), end_date.isoformat() if end_date else None, 
                  added_by))
            
            sub_id = cursor.lastrowid
            
            # إضافة الصلاحيات المرتبطة بالاشتراك
            features = SubscriptionSystem.SUBSCRIPTION_TYPES[subscription_type]['features']
            for feature in features:
                if feature == 'all':
                    # إضافة جميع الصلاحيات
                    all_features = ['basic_analysis', 'chat', 'recommendations', 'advanced_analysis', 'priority']
                    for f in all_features:
                        try:
                            cursor.execute("""
                                INSERT OR REPLACE INTO permissions (user_id, permission, granted_by, is_active)
                                VALUES (?, ?, ?, 1)
                            """, (user_id, f, added_by))
                        except:
                            pass
                else:
                    try:
                        cursor.execute("""
                            INSERT OR REPLACE INTO permissions (user_id, permission, granted_by, is_active)
                            VALUES (?, ?, ?, 1)
                        """, (user_id, feature, added_by))
                    except:
                        pass
            
            # تسجيل في سجل الاشتراكات
            cursor.execute("""
                INSERT INTO subscription_history (
                    user_id, action, new_subscription, reason, performed_by
                ) VALUES (?, ?, ?, ?, ?)
            """, (user_id, 'add', subscription_type, reason, added_by))
            
            conn.commit()
            
            # تحديث قاعدة البيانات الرئيسية
            SubscriptionSystem.sync_with_main_db(user_id, end_date.isoformat() if end_date else None)
            
            return True, {
                'subscription_type': subscription_type,
                'subscription_name': sub_name,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat() if end_date else None
            }
            
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def sync_with_main_db(user_id, end_date):
        """مزامنة مع قاعدة البيانات الرئيسية"""
        conn = None
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            
            # الحصول على اسم المستخدم من قاعدة البيانات الجديدة
            sub_conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
            sub_cursor = sub_conn.cursor()
            sub_cursor.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
            user_result = sub_cursor.fetchone()
            username = user_result[0] if user_result else None
            sub_conn.close()
            
            # التحقق من وجود المستخدم في قاعدة البيانات الرئيسية
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if cursor.fetchone():
                cursor.execute("""
                    UPDATE users 
                    SET subscription_end = ?,
                        username = COALESCE(?, username)
                    WHERE user_id = ?
                """, (end_date, username, user_id))
            else:
                cursor.execute("""
                    INSERT INTO users (user_id, username, subscription_end, candle, trade_time, country_timezone)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (user_id, username, end_date, "M1", "قصير (1m-15m)", "Asia/Gaza"))
            
            conn.commit()
            print(f"✅ تمت مزامنة المستخدم {user_id} مع قاعدة البيانات الرئيسية")
            
        except Exception as e:
            print(f"⚠️ خطأ في مزامنة قاعدة البيانات: {e}")
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def check_subscription(user_id):
        """التحقق من صلاحية اشتراك المستخدم"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            # التحقق من حظر المستخدم
            cursor.execute("""
                SELECT is_blocked FROM users WHERE user_id = ?
            """, (user_id,))
            result = cursor.fetchone()
            if result and result[0] == 1:
                return False, "محظور", {'blocked': True}
            
            # البحث عن اشتراك نشط
            cursor.execute("""
                SELECT subscription_type, subscription_name, start_date, end_date, metadata
                FROM active_subscriptions 
                WHERE user_id = ? AND is_active = 1
                ORDER BY id DESC LIMIT 1
            """, (user_id,))
            
            result = cursor.fetchone()
            if not result:
                return False, "لا يوجد اشتراك نشط", None
            
            sub_type, sub_name, start_date, end_date, metadata = result
            
            # التحقق من تاريخ الانتهاء
            if end_date:
                try:
                    end = datetime.fromisoformat(end_date)
                    if end < datetime.now():
                        # تحديث حالة الاشتراك
                        cursor.execute("""
                            UPDATE active_subscriptions 
                            SET is_active = 0
                            WHERE user_id = ? AND is_active = 1
                        """, (user_id,))
                        conn.commit()
                        return False, "انتهت صلاحية الاشتراك", None
                except:
                    pass
            
            # الحصول على الصلاحيات
            cursor.execute("""
                SELECT permission FROM permissions 
                WHERE user_id = ? AND is_active = 1
            """, (user_id,))
            
            permissions = [p[0] for p in cursor.fetchall()]
            
            return True, sub_name, {
                'type': sub_type,
                'name': sub_name,
                'start_date': start_date,
                'end_date': end_date,
                'permissions': permissions
            }
            
        except Exception as e:
            return False, str(e), None
        finally:
            conn.close()
    
    @staticmethod
    def has_permission(user_id, permission):
        """التحقق من صلاحية محددة للمستخدم"""
        if user_id == ADMIN_ID:
            return True
        
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            # التحقق من الحظر أولاً
            cursor.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if result and result[0] == 1:
                return False
            
            cursor.execute("""
                SELECT id FROM permissions 
                WHERE user_id = ? AND permission = ? AND is_active = 1
            """, (user_id, permission))
            
            return cursor.fetchone() is not None
        except:
            return False
        finally:
            conn.close()
    
    @staticmethod
    def increment_usage(user_id, feature, time_used=0):
        """تسجيل استخدام ميزة"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO usage_stats (user_id, feature, count, total_time_used)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(user_id, feature) DO UPDATE SET
                    count = count + 1,
                    total_time_used = total_time_used + ?,
                    last_used = CURRENT_TIMESTAMP
            """, (user_id, feature, time_used, time_used))
            
            conn.commit()
        except:
            pass
        finally:
            conn.close()
    
    @staticmethod
    def get_user_subscriptions(user_id):
        """الحصول على جميع اشتراكات المستخدم"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT subscription_type, subscription_name, start_date, end_date, is_active, added_at
                FROM active_subscriptions 
                WHERE user_id = ?
                ORDER BY start_date DESC
            """, (user_id,))
            
            return cursor.fetchall()
        except:
            return []
        finally:
            conn.close()
    
    @staticmethod
    def get_all_users():
        """الحصول على جميع المستخدمين"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT u.user_id, u.username, u.first_name, u.created_at, u.last_active, u.is_blocked,
                       a.subscription_type, a.subscription_name, a.start_date, a.end_date, a.is_active
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
    def get_all_subscribers():
        """الحصول على جميع المشتركين النشطين فقط"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT u.user_id, u.username, u.first_name, u.last_active,
                       a.subscription_type, a.subscription_name, a.start_date, a.end_date
                FROM users u
                INNER JOIN active_subscriptions a ON u.user_id = a.user_id
                WHERE a.is_active = 1 AND u.is_blocked = 0
                AND (a.end_date IS NULL OR a.end_date > datetime('now'))
                ORDER BY u.created_at DESC
            """)
            
            return cursor.fetchall()
        except:
            return []
        finally:
            conn.close()
    
    @staticmethod
    def get_stats():
        """الحصول على إحصائيات النظام"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        stats = {}
        
        try:
            # إجمالي المستخدمين
            cursor.execute("SELECT COUNT(*) FROM users")
            stats['total_users'] = cursor.fetchone()[0]
            
            # المستخدمين المحظورين
            cursor.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
            stats['blocked_users'] = cursor.fetchone()[0]
            
            # المستخدمين النشطين
            cursor.execute("""
                SELECT COUNT(DISTINCT user_id) 
                FROM active_subscriptions 
                WHERE is_active = 1 AND (end_date IS NULL OR end_date > datetime('now'))
            """)
            stats['active_users'] = cursor.fetchone()[0]
            
            # الاشتراكات حسب النوع
            cursor.execute("""
                SELECT subscription_type, COUNT(*) 
                FROM active_subscriptions 
                WHERE is_active = 1
                GROUP BY subscription_type
            """)
            stats['subscriptions_by_type'] = dict(cursor.fetchall())
            
            # استخدام الميزات
            cursor.execute("""
                SELECT feature, SUM(count) as total
                FROM usage_stats
                GROUP BY feature
                ORDER BY total DESC
                LIMIT 10
            """)
            stats['feature_usage'] = dict(cursor.fetchall())
            
        except Exception as e:
            print(f"⚠️ خطأ في جلب الإحصائيات: {e}")
        finally:
            conn.close()
        
        return stats
    
    @staticmethod
    def log_change(table_name, record_id, action, old_data, new_data, changed_by):
        """تسجيل تغيير في قاعدة البيانات"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO change_log (
                    table_name, record_id, action, old_data, new_data, changed_by
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                table_name, record_id, action,
                json.dumps(old_data) if old_data else None,
                json.dumps(new_data) if new_data else None,
                changed_by
            ))
            conn.commit()
        except:
            pass
        finally:
            conn.close()
    
    @staticmethod
    def revoke_subscription(user_id, reason="Revoked by admin", performed_by=ADMIN_ID):
        """إلغاء اشتراك مستخدم"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("BEGIN TRANSACTION")
            
            # الحصول على الاشتراك النشط
            cursor.execute("""
                SELECT subscription_type FROM active_subscriptions
                WHERE user_id = ? AND is_active = 1
                LIMIT 1
            """, (user_id,))
            
            result = cursor.fetchone()
            if not result:
                conn.rollback()
                return False, "لا يوجد اشتراك نشط لهذا المستخدم"
            
            old_subscription = result[0]
            
            # إلغاء الاشتراك
            cursor.execute("""
                UPDATE active_subscriptions 
                SET is_active = 0
                WHERE user_id = ? AND is_active = 1
            """, (user_id,))
            
            # إلغاء الصلاحيات
            cursor.execute("""
                UPDATE permissions 
                SET is_active = 0
                WHERE user_id = ?
            """, (user_id,))
            
            # تسجيل في سجل الاشتراكات
            cursor.execute("""
                INSERT INTO subscription_history (
                    user_id, action, old_subscription, reason, performed_by
                ) VALUES (?, ?, ?, ?, ?)
            """, (user_id, 'revoke', old_subscription, reason, performed_by))
            
            conn.commit()
            
            # تحديث قاعدة البيانات الرئيسية
            SubscriptionSystem.sync_with_main_db(user_id, None)
            
            return True, "تم إلغاء الاشتراك بنجاح"
            
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def block_user(user_id, reason="Blocked by admin", performed_by=ADMIN_ID):
        """حظر مستخدم"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("BEGIN TRANSACTION")
            
            # حظر المستخدم
            cursor.execute("""
                UPDATE users 
                SET is_blocked = 1, blocked_reason = ?
                WHERE user_id = ?
            """, (reason, user_id))
            
            # إلغاء أي اشتراكات نشطة
            cursor.execute("""
                UPDATE active_subscriptions 
                SET is_active = 0
                WHERE user_id = ? AND is_active = 1
            """, (user_id,))
            
            # إلغاء الصلاحيات
            cursor.execute("""
                UPDATE permissions 
                SET is_active = 0
                WHERE user_id = ?
            """, (user_id,))
            
            # تسجيل في سجل الاشتراكات
            cursor.execute("""
                INSERT INTO subscription_history (
                    user_id, action, reason, performed_by
                ) VALUES (?, ?, ?, ?)
            """, (user_id, 'block', reason, performed_by))
            
            conn.commit()
            
            return True, "تم حظر المستخدم بنجاح"
            
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def unblock_user(user_id, performed_by=ADMIN_ID):
        """إلغاء حظر مستخدم"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE users 
                SET is_blocked = 0, blocked_reason = NULL
                WHERE user_id = ?
            """, (user_id,))
            
            # تسجيل في سجل الاشتراكات
            cursor.execute("""
                INSERT INTO subscription_history (
                    user_id, action, performed_by
                ) VALUES (?, ?, ?)
            """, (user_id, 'unblock', performed_by))
            
            conn.commit()
            
            return True, "تم إلغاء حظر المستخدم بنجاح"
            
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def extend_subscription(user_id, additional_seconds, reason="Extended by admin", performed_by=ADMIN_ID):
        """تمديد اشتراك مستخدم"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("BEGIN TRANSACTION")
            
            # الحصول على الاشتراك النشط
            cursor.execute("""
                SELECT id, end_date FROM active_subscriptions
                WHERE user_id = ? AND is_active = 1
                LIMIT 1
            """, (user_id,))
            
            result = cursor.fetchone()
            if not result:
                conn.rollback()
                return False, "لا يوجد اشتراك نشط لهذا المستخدم"
            
            sub_id, current_end = result
            
            # حساب تاريخ الانتهاء الجديد
            if current_end:
                try:
                    current_end_date = datetime.fromisoformat(current_end)
                    new_end_date = current_end_date + timedelta(seconds=additional_seconds)
                except:
                    new_end_date = datetime.now() + timedelta(seconds=additional_seconds)
            else:
                new_end_date = datetime.now() + timedelta(seconds=additional_seconds)
            
            # تحديث الاشتراك
            cursor.execute("""
                UPDATE active_subscriptions 
                SET end_date = ?
                WHERE id = ?
            """, (new_end_date.isoformat(), sub_id))
            
            # تسجيل في سجل الاشتراكات
            cursor.execute("""
                INSERT INTO subscription_history (
                    user_id, action, old_subscription, new_subscription, reason, performed_by
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, 'extend', 'active', 'extended', reason, performed_by))
            
            conn.commit()
            
            # تحديث قاعدة البيانات الرئيسية
            SubscriptionSystem.sync_with_main_db(user_id, new_end_date.isoformat())
            
            return True, f"تم تمديد الاشتراك حتى {new_end_date.strftime('%Y-%m-%d %H:%M')}"
            
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()

# دالة مساعدة للتحقق من الاشتراك في الدوال الأخرى
def check_user_subscription(user_id):
    """التحقق من صلاحية المستخدم (متوافقة مع الكود القديم)"""
    if user_id == ADMIN_ID:
        return True, "مشرف"
    
    status, message, details = SubscriptionSystem.check_subscription(user_id)
    return status, message

# ============================================
# باقي الكود كما هو مع تعديلات دالة get_market_session
# ============================================

def init_db_with_subscription():
    """تهيئة قاعدة البيانات الرئيسية"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # التحقق من وجود قاعدة بيانات قديمة
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    table_exists = cursor.fetchone()
    
    if table_exists:
        # التحقق من بنية الجدول الحالية وإضافة الأعمدة المفقودة
        cursor.execute("PRAGMA table_info(users)")
        existing_columns = [col[1] for col in cursor.fetchall()]
        
        # الأعمدة المطلوبة مع القيم الافتراضية
        required_columns = {
            'username': 'TEXT',
            'candle': "TEXT DEFAULT 'M1'",
            'trade_time': "TEXT DEFAULT 'قصير (1m-15m)'",
            'chat_context': 'TEXT DEFAULT ""',
            'last_analysis_context': 'TEXT DEFAULT ""',
            'last_analysis_time': 'DATETIME DEFAULT NULL',
            'subscription_start': 'DATETIME DEFAULT NULL',
            'subscription_end': 'DATETIME DEFAULT NULL',
            'is_admin': 'INTEGER DEFAULT 0',
            'country_timezone': "TEXT DEFAULT 'Asia/Gaza'"  
        }
        
        for col_name, col_type in required_columns.items():
            if col_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
                    print(f"➕ تم إضافة عمود {col_name} إلى جدول users")
                except Exception as e:
                    print(f"⚠️ خطأ في إضافة عمود {col_name}: {e}")
    
    # إنشاء جدول المستخدمين الأساسي إذا لم يكن موجوداً
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            username TEXT,
            candle TEXT DEFAULT 'M1', 
            trade_time TEXT DEFAULT 'قصير (1m-15m)',
            chat_context TEXT DEFAULT '',
            last_analysis_context TEXT DEFAULT '',
            last_analysis_time DATETIME DEFAULT NULL,
            subscription_start DATETIME DEFAULT NULL,
            subscription_end DATETIME DEFAULT NULL,
            is_admin INTEGER DEFAULT 0,
            country_timezone TEXT DEFAULT 'Asia/Gaza'
        )
    ''')
    
    # جدول سجل المحادثات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # جدول تتبع التغييرات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS data_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            user_id INTEGER,
            details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # إضافة المشرف الرئيسي مع الحفاظ على البيانات الموجودة
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (ADMIN_ID,))
    admin_exists = cursor.fetchone()
    
    if admin_exists:
        # تحديث صلاحية المشرف إذا كان موجوداً
        cursor.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (ADMIN_ID,))
    else:
        # إضافة المشرف الجديد
        cursor.execute("INSERT INTO users (user_id, username, is_admin, country_timezone) VALUES (?, ?, ?, ?)", 
                      (ADMIN_ID, "Sz2zv", 1, "Asia/Gaza"))
    
    conn.commit()
    conn.close()
    
    print("✅ Database initialized with timezone support")
    
    # تهيئة نظام الاشتراك الجديد
    SubscriptionSystem.init_db()

def check_subscription(user_id):
    """التحقق من صلاحية اشتراك المستخدم (متوافقة مع الكود القديم)"""
    return check_user_subscription(user_id)

def log_data_change(action, user_id, details=""):
    """تسجيل أي تغيير في البيانات"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO data_log (action, user_id, details, timestamp)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (action, user_id, details))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ خطأ في تسجيل التغيير: {e}")

def cleanup_old_images():
    """تنظيف الصور القديمة - حذف الملفات الأقدم من 1 دقائق"""
    try:
        current_time = time.time()
        deleted_count = 0
        for filename in os.listdir(IMAGE_CACHE_DIR):
            filepath = os.path.join(IMAGE_CACHE_DIR, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 60:  
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                        print(f"🧹 تم حذف الملف القديم: {filename} (عمره {int(file_age/60)} دقيقة)")
                    except Exception as e:
                        print(f"⚠️ خطأ في حذف الملف {filename}: {e}")
        
        if deleted_count > 0:
            print(f"✅ تم حذف {deleted_count} ملف مؤقت قديم (أقدم من 5 دقائق)")
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

def download_chart_image(symbol="BTCUSDT"):
    """سحب صورة شارت من TradingView"""
    try:
        API_KEY = "c94425"
        
        # تحويل اسم الرمز إلى تنسيق TradingView
        symbol_mapping = {
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
            "S&P 500 (OTC)": "SP:SPX",
            "Dow Jones (OTC)": "DJ:DJI",
            "Nasdaq 100 (OTC)": "NASDAQ:NDX",
            "DAX 40 (OTC)": "GER30",
            "CAC 40 (OTC)": "FRA40",
            "FTSE 100 (OTC)": "UK100",
            "Hang Seng (OTC)": "HK50",
            "Nikkei 225 (OTC)": "JPN225",
            "Gold (OTC)": "TVC:GOLD",
            "Silver (OTC)": "TVC:SILVER",
            "UKOIL (OTC)": "TVC:UKOIL",
            "USOIL (OTC)": "TVC:USOIL",
            "Natural Gas (OTC)": "TVC:NATGAS",
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
        
        chart_symbol = symbol_mapping.get(symbol, "FX:EURUSD")
        CHART_URL = f"https://www.tradingview.com/chart/?symbol={chart_symbol}&interval=5"
        
        api_url = f"https://api.screenshotmachine.com/?key={API_KEY}&url={CHART_URL}&dimension=800x600&device=desktop&delay=2000&format=png"
        
        print(f"📥 جاري سحب صورة للعملة الذي تم اختيارها 📥")
        
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

def save_user_setting(user_id, col, val):
    """حفظ إعدادات المستخدم مع تسجيل التغيير"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # التحقق من وجود المستخدم
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
    
    # حفظ الإعداد
    cursor.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (val, user_id))
    conn.commit()
    conn.close()
    
    # تسجيل التغيير
    log_data_change("update_setting", user_id, f"{col}={val}")

def get_user_setting(user_id):
    """الحصول على إعدادات المستخدم"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT candle, trade_time, last_analysis_context, last_analysis_time, country_timezone FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        return res
    return ("M1", "قصير (1m-15m)", "", None, "Asia/Gaza")

def save_analysis_context(user_id, analysis_text):
    """حفظ تحليل الصورة الأخيرة كسياق للتحليل التالي"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    summary = analysis_text[:500]
    cursor.execute("UPDATE users SET last_analysis_context = ?, last_analysis_time = CURRENT_TIMESTAMP WHERE user_id = ?", (summary, user_id))
    conn.commit()
    conn.close()
    log_data_change("save_analysis", user_id, "analysis saved")

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
                if time_diff > 10:
                    return "", None
            except:
                pass
        return context, context_time
    return "", None

def cleanup_old_database_records():
    """تنظيف سجلات قاعدة البيانات القديمة مع الحفاظ على بيانات المشتركين"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # حذف سجلات المحادثات القديمة فقط (أقدم من 1 يوم)
        month_ago = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("DELETE FROM chat_history WHERE timestamp < ?", (month_ago,))
        deleted_rows = cursor.rowcount
        
        # حذف سجلات التتبع القديمة (أقدم من 1 يوم)
        two_months_ago = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("DELETE FROM data_log WHERE timestamp < ?", (two_months_ago,))
        
        conn.commit()
        conn.close()
        
        if deleted_rows > 0:
            print(f"🧹 تم حذف {deleted_rows} سجل دردشة قديم (أقدم من 1 يوم)")
            
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف قاعدة البيانات: {e}")

# تعديل دالة format_trade_time_for_prompt
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

def encode_image(image_path):
    """تحويل الصورة إلى base64 بشكل صحيح"""
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        return encoded_string
    except Exception as e:
        print(f"Error encoding image: {e}")
        return None

def clean_repeated_text(text):
    """تنظيف النص من التكرارات وتحسين التنسيق"""
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

def format_as_copyable_code(text, language="markdown"):
    """تنسيق النص على شكل Code Block قابل للنسخ بنقرة واحدة"""
    text = clean_repeated_text(text)
    text = re.sub(r'```[\w]*\n?', '', text)
    text = re.sub(r'```\n?', '', text)
    formatted_text = f"```{language}\n{text}\n```"
    return formatted_text

def split_message_with_copy_support(text, max_length=3500):
    """تقسيم الرسالة الطويلة مع الحفاظ على دعم النسخ"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    current_part = ""
    
    for line in text.split('\n'):
        if len(current_part) + len(line) + 1 > max_length - 50:
            if current_part:
                parts.append(current_part)
                current_part = ""
        
        current_part += line + '\n'
    
    if current_part:
        parts.append(current_part)
    
    formatted_parts = []
    for i, part in enumerate(parts):
        if i < len(parts) - 1:
            part += "\n━━━━━━━━━━━━━━━━\n[يتبع في الجزء التالي...]"
        formatted_parts.append(format_as_copyable_code(part, "text"))
    
    return formatted_parts

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

def cleanup_user_data(context: ContextTypes.DEFAULT_TYPE, user_id: int = None, immediate_delete: bool = True):
    """تنظيف البيانات المؤقتة للمستخدم مع حذف فوري للملفات
    
    Args:
        context: سياق البوت
        user_id: معرف المستخدم
        immediate_delete: حذف فوري للملفات (True) أو الانتظار للتنظيف الدوري (False)
    """
    try:
        deleted_files = 0
        if user_id and os.path.exists(IMAGE_CACHE_DIR):
            try:
                for filename in os.listdir(IMAGE_CACHE_DIR):
                    # حذف جميع ملفات المستخدم (الملفات التي تحتوي على user_id في اسمها)
                    if f"_{user_id}_" in filename or filename.startswith(f"img_{user_id}_") or filename.startswith(f"chart_{user_id}_"):
                        filepath = os.path.join(IMAGE_CACHE_DIR, filename)
                        if os.path.exists(filepath):
                            try:
                                os.remove(filepath)
                                deleted_files += 1
                                if immediate_delete:
                                    print(f"🗑️ تم الحذف الفوري لملف المستخدم: {filename}")
                            except Exception as e:
                                print(f"⚠️ خطأ في حذف الملف {filename}: {e}")
            except Exception as e:
                print(f"⚠️ خطأ في تنظيف ملفات المستخدم {user_id}: {e}")
        
        # حذف المفاتيح من سياق المستخدم
        keys_to_remove = [
            'dual_images', 'dual_image_paths', 'dual_analysis_mode',
            'last_analysis', 'dual_analysis_start', 'original_paths',
            'last_recommendation_symbol', 'target_user_id', 'last_action',
            'temp_image_path', 'compressed_image_path'
        ]
        
        removed_keys = 0
        for key in keys_to_remove:
            if key in context.user_data:
                del context.user_data[key]
                removed_keys += 1
        
        if deleted_files > 0 or removed_keys > 0:
            print(f"✅ تنظيف المستخدم {user_id}: {deleted_files} ملف, {removed_keys} مفتاح ذاكرة")
                
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف الذاكرة: {e}")

def save_last_recommendation_symbol(context: ContextTypes.DEFAULT_TYPE, symbol: str):
    """حفظ آخر رمز تم اختياره في التوصيات"""
    context.user_data['last_recommendation_symbol'] = symbol

def get_last_recommendation_symbol(context: ContextTypes.DEFAULT_TYPE):
    """الحصول على آخر رمز تم اختياره في التوصيات"""
    return context.user_data.get('last_recommendation_symbol', 'BTC (OTC)')

def get_mistral_analysis(symbol):

    headers = {
        "Authorization": f"Bearer {MISTRAL_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    بصفتك خبير تداول كمي، حلل {symbol} بناءً على "تلاقي الأدلة" (Confluence Analysis) مع تطبيق قوانين السيادة الجديدة.
    
    🛑 *شروط الفلترة الصارمة مع التحديثات الجديدة:*
    1. حتمية الاستنفاذ: فشل آخر موجة جهد في كسر الهيكل.
    2. توافق الفركتلات: تطابق الاتجاه على فريمات (H4, H1, M15).
    3. سحب السيولة (Sweep): يجب حدوث كسر وهمي للسيولة قبل الدخول.
    4. منطقة التوازن (OTE): الدخول حصراً بين مستويات فيبوناتشي 0.618 و 0.886.

    🔥 **قوانين السيادة الجديدة (V5 - Omni Priority):**
    
    【الرتبة 1】 سيادة الزخم (Momentum Supremacy):
    - إذا كانت آخر 3 شموع ممتلئة بنسبة >85%، تُلغى فوراً جميع التحذيرات الزمنية وفلاتر المسافة.
    - القطار لا يتوقف - استمر مع الاتجاه.
    
    【الرتبة 2】 السيولة المغناطيسية (Targeting):
    - إذا كان السعر متجهاً لقمة/قاع تاريخي أو رقم مستدير، يُمنع البيع نهائياً حتى يتم "ضرب" السعر بفتيلة أو اختراقه بجسم.
    
    【الرتبة 3】 الهيكل الزمني: يُطبق فقط في الأسواق العرضية (Side-way).
    
    🔍 **قانون الفتيلة العالمي (The Universal Wick Filter):**
    - الذيل الصاعد (في اتجاه التريند): يُعتبر "استمراراً" طالما أن الشمعة التالية لم تبتلع 50% من جسم الشمعة السابقة لأسفل.
    - تجاهل الذيول المجهرية: أي ذيل يمثل أقل من 15% من طول الشمعة يُعامل كأنه غير موجود (Marubozu Mode).
    
    📏 **فلتر المسافة المرن (Dynamic Distance Filter):**
    - يُمنع رفض الصفقات القريبة من الهدف إذا كانت سرعة الشموع (Volatility) عالية.
    - القاعدة: "إذا كان الهدف على بُعد 3 نقاط والزخم قوي، ادخل؛ لأن الانزلاق السعري في الـ OTC سيجلب لك الربح في ثانية."
    
    🔍 *المطلوب تحليل (SMC + Wyckoff + Volume Profile):*
    - رصد الـ Order Block النشط و الـ FVG غير المغطى.
    - تحديد منطقة الفخ (Inducement) والسيولة المستهدفة (BSL/SSL).
    - حساب قوة الاتجاه باستخدام (RSI Divergence) وحجم التداول.

    🚨 **خوارزمية القرار النهائي (Final Decision Matrix):**
    - **هل الزخم > 90%؟** نعم ↝ تجاهل (الوقت، المسافة، الفجوات) ↝ القرار: **شراء/بيع فوري مع الاتجاه**.
    - **هل السعر عند رقم مستدير؟** نعم ↝ انتظر الاختراق ↝ القرار: **استمرار مع الكسر**.
    - **هل السوق متذبذب؟** نعم ↝ طبّق قواعد SMC و Wyckoff بدقة ↝ القرار: **احتفاظ 🟡**.

    قدم التقرير باللغة العربية بهذا التنسيق حصراً:
    
    📊 *ملخص فحص {symbol}*:
    - الهيكل: (صاعد/هابط/تجميع) 
    - السيولة: (أقرب فخ + الهدف القادم)
    - الفجوات: (أهم منطقة FVG نشطة)
    - تطبيق قوانين السيادة: [تم/غير مطلوب]
    
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
    - القوانين المطبقة: [الزخم ✓ / السيولة ✓ / المسافة المرنة ✓]
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

# --- دوال معالجة البوت (محدثة لنظام الاشتراك الجديد مع إضافة اختيار الدولة) ---

async def subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """التحقق من الاشتراك للمستخدمين غير المشتركين"""
    user_id = update.effective_user.id
    
    # المشرف دائماً له صلاحية
    if user_id == ADMIN_ID:
        return True
    
    # تسجيل المستخدم في النظام
    user = update.effective_user
    SubscriptionSystem.register_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        is_bot=user.is_bot
    )
    
    status, message, details = SubscriptionSystem.check_subscription(user_id)
    
    if not status:
        # إخفاء جميع الأزرار وإظهار رسالة اشتراك فقط
        await update.message.reply_text(
            f"⚠️ **عذراً، ليس لديك صلاحية استخدام البوت**\n\n"
            f"❌ الحالة: {message}\n\n"
            f"🔑 **أسعار الاشتراكات:**\n"
            f"• ساعة: 1$\n"
            f"• يوم: 5$\n"
            f"• أسبوعي: 15$\n"
            f"• شهري: 40$\n\n"
            f"📞 للاشتراك والتواصل مع المشرف: @Sz2zv",
            parse_mode="Markdown"
        )
        return False
    
    # تسجيل استخدام
    SubscriptionSystem.increment_usage(user_id, 'bot_access')
    
    return True

async def country_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة الدول لاختيار المنطقة الزمنية"""
    user_id = update.effective_user.id
    
    # تجميع الدول في أزرار (كل صف يحتوي على 3 دول)
    country_list = list(ARAB_COUNTRIES.keys()) + list(MAJOR_COUNTRIES.keys())
    keyboard = []
    row = []
    
    for i, country in enumerate(country_list):
        row.append(country)
        if len(row) == 2:  # صف من زرين
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    # إضافة خيارات إضافية
    keyboard.append(["🌍 جميع الدول", "🔍 بحث عن دولة"])
    keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    current_timezone = get_user_timezone(user_id)
    current_time = get_localized_time(user_id).strftime('%Y-%m-%d %H:%M:%S')
    
    await update.message.reply_text(
        f"🌍 **اختر دولتك**\n\n"
        f"🕒 **وقتك الحالي:** {current_time}\n"
        f"📍 **منطقتك الحالية:** {current_timezone}\n\n"
        f"اختر دولتك من الأزرار أدناه لتحديث التوقيت المحلي:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return COUNTRY_SELECTION

async def handle_country_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار الدولة"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"],
            ["🌍 دولتك"]
        ]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "🌍 جميع الدول":
        # عرض جميع الدول (العربية + الكبرى)
        all_countries = {**ARAB_COUNTRIES, **MAJOR_COUNTRIES}
        keyboard = []
        row = []
        
        for country in list(all_countries.keys())[:30]:  # عرض أول 30 دولة
            row.append(country)
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "🌍 **جميع الدول المتاحة:**\nاختر دولتك:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return COUNTRY_SELECTION
    
    elif user_message == "🔍 بحث عن دولة":
        await update.message.reply_text(
            "🔍 **أدخل اسم الدولة** (مثال: مصر، السعودية، أمريكا):",
            parse_mode="Markdown"
        )
        return COUNTRY_SELECTION
    
    # البحث عن الدولة المختارة
    all_countries = {**ARAB_COUNTRIES, **MAJOR_COUNTRIES, **COUNTRIES_TIMEZONES}
    
    if user_message in all_countries:
        timezone = all_countries[user_message]
        set_user_timezone(user_id, timezone)
        
        # عرض الوقت الجديد
        local_time = get_localized_time(user_id).strftime('%Y-%m-%d %H:%M:%S')
        
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"],
            ["🌍 دولتك"]
        ]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        await update.message.reply_text(
            f"✅ **تم تعيين دولتك بنجاح!**\n\n"
            f"📍 **الدولة:** {user_message}\n"
            f"🕒 **التوقيت المحلي:** {local_time}\n\n"
            f"سيتم استخدام هذا التوقيت في جميع التحليلات المستقبلية.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    # إذا كان بحثاً
    if len(user_message) > 2:
        matching_countries = []
        for country in all_countries.keys():
            if user_message in country:
                matching_countries.append(country)
        
        if matching_countries:
            keyboard = [[c] for c in matching_countries[:10]]
            keyboard.append(["الرجوع للقائمة الرئيسية"])
            
            await update.message.reply_text(
                f"🔍 **نتائج البحث عن '{user_message}':**\nاختر الدولة المناسبة:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return COUNTRY_SELECTION
    
    await update.message.reply_text(
        "❌ الدولة غير موجودة. الرجاء اختيار من القائمة أو كتابة اسم صحيح.",
        reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True, one_time_keyboard=False)
    )
    return COUNTRY_SELECTION

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت - مع إضافة زر دولتك"""
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    
    # تسجيل المستخدم في النظام الجديد
    SubscriptionSystem.register_user(
        user_id=user_id,
        username=username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        is_bot=user.is_bot
    )
    
    # تنظيف الذاكرة المؤقتة
    cleanup_user_data(context, user_id)
    
    # التحقق من صلاحية المشرف والاشتراك
    is_admin = (user_id == ADMIN_ID)
    status, message, details = SubscriptionSystem.check_subscription(user_id)
    
    # الحصول على الوقت المحلي للمستخدم
    local_time = get_localized_time(user_id).strftime('%Y-%m-%d %H:%M:%S')
    user_timezone = get_user_timezone(user_id)
    
    if is_admin:
        # المشرف - يرى جميع الأزرار
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"],
            ["🌍 دولتك", "👑 لوحة التحكم"]
        ]
        
        welcome_msg = (
            "🚀 أهلاً بك في Obeida Trading\n\n"
            "👑 أنت مسؤول النظام - لديك صلاحيات كاملة.\n\n"
            f"🕒 **توقيتك المحلي:** {local_time} ({user_timezone})\n\n"
            "🤖 الخدمات المتاحة:\n"
            "• 📊 تحليل الشارتات\n"
            "• 📈 توصيات فورية\n"
            "• 💬 دردشة ذكية\n"
            "• 🌍 اختيار دولتك\n"
            "• ⚙️ إعدادات متقدمة\n\n"
            "اختر أحد الخيارات:"
        )
        
    elif status:
        # مستخدم مشترك - يرى جميع الأزرار مع زر الدولة
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"],
            ["🌍 دولتك"]
        ]
        
        sub_name = details['name'] if details else 'مشترك'
        end_info = ""
        if details and details['end_date']:
            try:
                end_date = datetime.fromisoformat(details['end_date'])
                remaining = end_date - datetime.now()
                if remaining.days > 0:
                    end_info = f"ينتهي بعد {remaining.days} يوم"
                else:
                    end_info = f"ينتهي بعد {remaining.seconds//3600} ساعة"
            except:
                pass
        
        welcome_msg = (
            f"🚀 أهلاً بك في Obeida Trading\n\n"
            f"✅ اشتراكك: {sub_name}\n"
            f"{end_info}\n\n"
            f"🕒 **توقيتك المحلي:** {local_time} ({user_timezone})\n\n"
            f"🤖 الخدمات المتاحة:\n"
            f"• 📊 تحليل الشارتات\n"
            f"• 📈 توصيات فورية\n"
            f"• 💬 دردشة ذكية\n"
            f"• 🌍 اختيار دولتك\n"
            f"• ⚙️ إعدادات متقدمة\n\n"
            f"اختر أحد الخيارات:"
        )
        
    else:
        # مستخدم غير مشترك - لا يرى أي أزرار، فقط رسالة للتواصل مع المشرف
        await update.message.reply_text(
            f"⚠️ عذراً، ليس لديك صلاحية استخدام البوت\n\n"
            f"❌ الحالة: {message}\n\n"
            f"🔑 **أسعار الاشتراكات:**\n"
            f"• ساعة: 1$\n"
            f"• يوم: 5$\n"
            f"• أسبوعي: 15$\n"
            f"• شهري: 40$\n\n"
            f"📞 للاشتراك والتواصل مع المشرف: @Sz2zv\n\n"
            f"بعد الاشتراك، أرسل /start مرة أخرى"
        )
        return MAIN_MENU
    
    # إظهار القائمة للمستخدمين المشتركين فقط
    await update.message.reply_text(
        welcome_msg,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    return MAIN_MENU
    
async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات القائمة الرئيسية"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    # التحقق من الاشتراك (لغير المشرف)
    if user_id != ADMIN_ID:
        status, _, _ = SubscriptionSystem.check_subscription(user_id)
        if not status:
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك.\n\n"
                "📞 للتواصل مع المشرف: @Sz2zv"
            )
            return MAIN_MENU
    
    if user_message == "🌍 دولتك":
        return await country_selection(update, context)
    
    if user_message == "👑 لوحة التحكم" and user_id == ADMIN_ID:
        return await start_admin_panel(update, context)
    
    if user_message == "⚙️ إعدادات التحليل":
        keyboard = [CANDLE_SPEEDS[i:i+3] for i in range(0, len(CANDLE_SPEEDS), 3)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "⚙️ *إعدادات التحليل الفني*\n\n"
            "حدد سرعة الشموع للبدء:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return SETTINGS_CANDLE
    
    elif user_message == "📊 تحليل صورة":
        candle, trade_time, _, _, _ = get_user_setting(user_id)
        
        if not candle or not trade_time:
            keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"], ["🌍 دولتك"]]
            if user_id == ADMIN_ID:
                keyboard.append(["👑 لوحة التحكم"])
            await update.message.reply_text(
                "❌ *يجب ضبط الإعدادات أولاً*\n\n"
                "الرجاء ضبط سرعة الشموع ومدة الصفقة قبل التحليل.",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return MAIN_MENU
        else:
            keyboard = [["الرجوع للقائمة الرئيسية"]]
            
            time_display = format_trade_time_for_prompt(trade_time)
            
            await update.message.reply_text(
                f"📊* نظام جاهز لتحليل *:\n\n"
                f"⚙️ الإعدادات الذي تستخدمها ⚙️:\n"
                f"•🏃 سرعة الشموع: {candle}\n"
                f"•🕧 استراتيجية : {time_display}\n\n"
                f"📤 قم برفع صورة (الشارت) الآن: 📤\n",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return ANALYZE_MODE
    
    elif user_message == "💬 دردشة":
        return await start_chat_mode(update, context)
    
    elif user_message == "📈 توصية":
        return await start_recommendation_mode(update, context)
    
    # إعادة عرض القائمة الرئيسية
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["💬 دردشة", "📈 توصية"],
        ["🌍 دولتك"]
    ]
    if user_id == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    
    await update.message.reply_text(
        "اختر أحد الخيارات من القائمة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    return MAIN_MENU

async def handle_settings_candle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار سرعة الشموع"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    # التحقق من الاشتراك
    if user_id != ADMIN_ID:
        status, _, _ = SubscriptionSystem.check_subscription(user_id)
        if not status:
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك.\n\n"
                "📞 للتواصل مع المشرف: @Sz2zv"
            )
            return SETTINGS_CANDLE
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"],
            ["🌍 دولتك"]
        ]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
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
            f" ⬇️ الآن حدد ** مدة الصفقة** المتوقعة ⬇️:\n\n"
            f"📊 **🕧 خيارات مدة الصفقة: 🕧**\n"
            f"•1- **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة\n"
            f"•2- **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة\n"
            f"•3- **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة\n\n"
            f" 🖊️ اختر الإطار الزمني المناسب لاستراتيجيتك:🖊️",
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
    
    # التحقق من الاشتراك
    if user_id != ADMIN_ID:
        status, _, _ = SubscriptionSystem.check_subscription(user_id)
        if not status:
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك.\n\n"
                "📞 للتواصل مع المشرف: @Sz2zv"
            )
            return SETTINGS_TIME
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"],
            ["🌍 دولتك"]
        ]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in TRADE_TIMES:
        save_user_setting(user_id, "trade_time", user_message)
        
        keyboard = [["📊 تحليل صورة"], ["💬 دردشة"], ["📈 توصية"], ["🌍 دولتك"], ["الرجوع للقائمة الرئيسية"]]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        candle, _, _, _, _ = get_user_setting(user_id)
        
        await update.message.reply_text(
            f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ سرعة الشموع: {candle}\n"
            f"✅ مدة الصفقة: {user_message}\n\n"
            f"📡 ** نظام تحليل مستهدفة :** \n"
            f"• 1- تفكيك هيكل شارت  =مفعلة ✔️\n"
            f"• تحليل صورة شارت = مفعلة ✔️\n"
            f" 📤 قم الأن برفع صورة شارت : 📤",
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
    
    # التحقق من الاشتراك
    if user_id != ADMIN_ID:
        status, _, _ = SubscriptionSystem.check_subscription(user_id)
        if not status:
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك.\n\n"
                "📞 للتواصل مع المشرف: @Sz2zv"
            )
            return ANALYZE_MODE
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"],
            ["🌍 دولتك"]
        ]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
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

async def start_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع الدردشة المتقدم"""
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return CHAT_MODE
    
    user_id = update.effective_user.id
    SubscriptionSystem.increment_usage(user_id, 'chat_mode')
    
    keyboard = [
        ["🚀 مساعد شامل", "💼 استشارات احترافية"],
        ["📈 تحليل استثماري", "👨‍💻 دعم برمجي"],
        ["📝 كتابة إبداعية", "🧠 حلول ذكية"],
        ["ايقاف الدردشة", "الرجوع للقائمة الرئيسية"]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🚀 **وضع الدردشة مع Obeida Trading**\n\n"
             "أنا مساعدك الذكي متعدد المجالات :\n"
             "اختر مجال المساعدة أو أرسل سؤالك مباشرة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return CHAT_MODE

async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة رسائل الدردشة مع برومبت قوي"""
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return CHAT_MODE
    
    user_message = update.message.text
    user_id = update.effective_user.id
    
    # تسجيل استخدام
    SubscriptionSystem.increment_usage(user_id, 'chat_message')
    
    # التحقق من الأوامر الخاصة
    if user_message == "ايقاف الدردشة":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"], ["🌍 دولتك"]]
        if user_id == ADMIN_ID:
            main_keyboard.append(["👑 لوحة التحكم"])
        await update.message.reply_text(
            "✅ تم إنهاء وضع الدردشة.",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "الرجوع للقائمة الرئيسية":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"], ["🌍 دولتك"]]
        if user_id == ADMIN_ID:
            main_keyboard.append(["👑 لوحة التحكم"])
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    # برومبتات متخصصة حسب الاختيار (نفس الكود السابق)
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
            f"🎯 **جاهز لخدمتك في اي وقت **\n"
            f"💬  أرسل سؤالك الآن 💬:",
            parse_mode="Markdown"
        )
        return CHAT_MODE
    
    # إظهار حالة المعالجة
    wait_msg = await update.message.reply_text("قاعد افكر استنى شوي 🤌...")
    
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
            footer = "\n\n━━━━━━━━━━━━\n🤖 **Powered by - Obeida Trading ** 🤖"
            result = result + footer
            
            # أزرار الدردشة المتقدمة
            chat_keyboard = [
                ["🚀 مساعد شامل", "💼 استشارات احترافية"],
                ["📈 تحليل استثماري", "👨‍💻 دعم برمجي"],
                ["📝 كتابة إبداعية", "🧠 حلول ذكية"],
                ["ايقاف الدردشة", "الرجوع للقائمة الرئيسية"]
            ]
            
            # تنسيق النتيجة مع دعم النسخ السريع
            formatted_result = format_as_copyable_code(result, "text")
            
            # تقسيم الرسالة الطويلة
            if len(formatted_result) > 4000:
                parts = split_message_with_copy_support(result, max_length=3500)
                for i, part in enumerate(parts):
                    if i == 0:
                        await wait_msg.edit_text(
                            part,
                            parse_mode="Markdown"
                        )
                    else:
                        await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(
                    formatted_result,
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

async def start_recommendation_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع التوصية"""
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return RECOMMENDATION_MODE
    
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
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return RECOMMENDATION_MODE
    
    user_text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # العودة للقائمة الرئيسية
    if user_text == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"], ["🌍 دولتك"]]
        if update.effective_user.id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
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
        wait_msg = await update.message.reply_text(f"📸 تم سحب صورة وجاري تحليل 🤌...")
        
        # تسجيل استخدام الميزة
        SubscriptionSystem.increment_usage(user_id, 'recommendation')
        
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
                await wait_msg.edit_text(f"استنى شويه قاعد أحلل {symbol_to_analyze} 🤌...")
                
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
                candle, trade_time, _, _, _ = get_user_setting(user_id)
                
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
                
                # تنسيق النتيجة مع دعم النسخ السريع
                formatted_result = format_as_copyable_code(analysis_result, "text")
                await wait_msg.edit_text(formatted_result, parse_mode="Markdown")
                
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
                formatted_result = format_as_copyable_code(final_msg, "text")
                await wait_msg.edit_text(formatted_result, parse_mode="Markdown")
            
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
            formatted_result = format_as_copyable_code(final_msg, "text")
            await wait_msg.edit_text(formatted_result, parse_mode="Markdown")
        
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
    """تحليل صورة الشارت مع النسخة الهجومية V8.0 ELITE SNIPER - مكافحة الغدر"""
    try:
        user_id = update.effective_user.id
        prev_context, prev_time = get_analysis_context(user_id)
        
        # إنشاء رسالة انتظار
        wait_msg = await update.message.reply_text("🤔 جاري تحليل الصورة...")
        
        # ضغط الصورة
        compressed_path = compress_image(image_path)
        
        # استخدام الصورة المضغوطة للتحليل
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            return "❌ **خطأ في قراءة الصورة.**"
        
        # الحصول على معلومات السيولة والتوقيت
        session_name, session_time, session_vol, user_tz = get_market_session(user_id)
        local_time = get_localized_time(user_id)
        current_hour = local_time.hour
        current_minute = local_time.minute
        current_second = local_time.second
        
        # حساب الثواني المتبقية
        seconds_remaining = 60 - current_second
        if candle.startswith('S'):
            candle_seconds = int(candle[1:]) if candle[1:].isdigit() else 5
            seconds_remaining = (candle_seconds) - ((current_second) % candle_seconds)
        elif candle.startswith('M'):
            candle_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            seconds_remaining = (candle_minutes * 60) - ((current_minute % candle_minutes) * 60 + current_second)
        elif candle.startswith('H'):
            candle_hours = int(candle[1:]) if candle[1:].isdigit() else 1
            minutes_passed = local_time.hour % candle_hours * 60 + current_minute
            seconds_remaining = (candle_hours * 3600) - (minutes_passed * 60 + current_second)

        is_fast_frame = candle.startswith('S')
        seconds_passed = 60 - seconds_remaining

        # ⚡ نافذة التنفيذ المطورة V8.0 - توسيع النطاق لالتقاط الفرص الحقيقية
        if candle.startswith('S'):  # فريمات الثواني
            # نطاق جديد: من 0 إلى 45 ثانية (تجاهل آخر 15 ثانية فقط)
            in_golden_window = seconds_passed <= 45
            window_status = f"الثواني المنقضية: {seconds_passed} (نافذة التنفيذ: 0-45 ثانية ✅)"
        else:  # فريمات الدقائق
            # نطاق جديد: من 0 إلى 50 ثانية (تجاهل آخر 10 ثوانٍ فقط)
            in_golden_window = seconds_passed <= 50
            window_status = f"الثواني المنقضية: {seconds_passed} (نافذة التنفيذ: 0-50 ثانية ✅)"
        
        # نظام الدرع الأساسي
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
        
        # الفلتر الزمني
        kill_zone_status = ""
        if 10 <= current_hour < 13:
            kill_zone_status = "داخل منطقة القتل السعري (لندن 10-13 بالتوقيت المحلي)"
        elif 15 <= current_hour < 18:
            kill_zone_status = "داخل منطقة القتل السعري (نيويورك 15-18 بالتوقيت المحلي)"
        elif 0 <= current_hour < 9 or current_hour >= 22:
            kill_zone_status = "خارج منطقة القتل (جلسة آسيوية)"
        else:
            kill_zone_status = "خارج مناطق القتل الرئيسية"
        
        # تعديل دقائق الغدر
        is_trap_minute = current_minute in [14, 29, 44, 59]
        
        # تحديد نمط السوق
        market_mode = "AGGRESSIVE" if candle.startswith('S') else "CONSERVATIVE"

        # ⚡⚡⚡ دوال المسح الاستبعادي المطورة V8.0 ⚡⚡⚡
        
        def calculate_breakout_force(candle_volume, avg_volume):
            """حساب قوة الكسر الهيكلي"""
            if avg_volume > 0:
                return candle_volume / avg_volume
            return 1.0

        def has_institutional_fvg(chart_data):
            """التحقق من وجود فجوة سعرية مؤسسية"""
            return False

        def check_liquidity_sweep(chart_data):
            """التحقق من سحب السيولة"""
            return False

        def improved_exclusionary_scan_v8(
            current_price, historical_max, historical_min,
            candle_open, candle_close, candle_high, candle_low,
            candle_volume, avg_volume, avg_volume_5,
            breakout_level, distance_to_peak, distance_to_bottom,
            engulfing_ratio, is_trap_minute, seconds_remaining,
            current_direction, candle_body_ratio, candle_wick_ratio
        ):
            """
            ⚡ نسخة V8.0 - مكافحة الغدر والصفقات الخاطئة ⚡
            الأولويات: تجنب الانعكاس المفاجئ + الدخول القناص
            """
            reasons = []
            action = "HOLD"
            confidence = 50
            reversal_signal = None
            entry_level = None
            stop_loss = None
            targets = []
            trade_duration = None
            
            # حساب حجم الجسم والذيل
            body_size = abs(candle_close - candle_open)
            total_size = candle_high - candle_low
            upper_wick = candle_high - max(candle_open, candle_close)
            lower_wick = min(candle_open, candle_close) - candle_low
            
            if body_size > 0:
                upper_wick_ratio = upper_wick / total_size
                lower_wick_ratio = lower_wick / total_size
            else:
                upper_wick_ratio = 0
                lower_wick_ratio = 0
            
            # ===== 🛑 الحاجز 1: فحص القمم التاريخية (PEAK LIQUIDITY DANGER) =====
            # ممنوع الشراء عند القمة التاريخية مهما كان الزخم
            if current_price >= historical_max * 0.995:  # في حدود 0.5% من القمة
                if current_direction == "UP":
                    action = "HOLD"
                    reasons.append("🚫 PEAK_LIQUIDITY_DANGER: قمة تاريخية - ممنوع الشراء")
                    reversal_signal = "LIQUIDITY_TRAP_REVERSAL 🔴"
                    confidence = 95
                    
                    # تحديد نقطة الدخول للبيع الانعكاسي
                    entry_level = historical_max - 2  # نقطتين تحت القمة
                    stop_loss = historical_max + 3  # 3 نقاط فوق القمة
                    targets = [
                        historical_max - 5,  # هدف أول
                        historical_max - 10,  # هدف ثاني
                        historical_max - 15   # هدف ثالث
                    ]
                    trade_duration = "3-5 شموع"
                    
                    return {
                        "action": action,
                        "reasons": reasons,
                        "reversal_signal": reversal_signal,
                        "confidence": confidence,
                        "entry_level": entry_level,
                        "stop_loss": stop_loss,
                        "targets": targets,
                        "trade_duration": trade_duration
                    }
            
            # ===== 🛑 الحاجز 2: فحص القيعان التاريخية =====
            if current_price <= historical_min * 1.005:  # في حدود 0.5% من القاع
                if current_direction == "DOWN":
                    action = "HOLD"
                    reasons.append("🚫 BOTTOM_LIQUIDITY_DANGER: قاع تاريخي - ممنوع البيع")
                    reversal_signal = "LIQUIDITY_TRAP_REVERSAL 🟢"
                    confidence = 95
                    
                    # تحديد نقطة الدخول للشراء الانعكاسي
                    entry_level = historical_min + 2  # نقطتين فوق القاع
                    stop_loss = historical_min - 3  # 3 نقاط تحت القاع
                    targets = [
                        historical_min + 5,
                        historical_min + 10,
                        historical_min + 15
                    ]
                    trade_duration = "3-5 شموع"
                    
                    return {
                        "action": action,
                        "reasons": reasons,
                        "reversal_signal": reversal_signal,
                        "confidence": confidence,
                        "entry_level": entry_level,
                        "stop_loss": stop_loss,
                        "targets": targets,
                        "trade_duration": trade_duration
                    }
            
            # ===== 🛑 الحاجز 3: فلتر الرفض السعري (Early Rejection Check) =====
            # رفض الدخول إذا كان الذيل العلوي > 30% للشموع الصاعدة
            if current_direction == "UP" and upper_wick_ratio > 0.3:
                action = "WAIT"
                reasons.append(f"⏳ REJECTION_IN_PROGRESS: ذيل علوي {upper_wick_ratio*100:.0f}% > 30%")
                confidence = 60
                return {
                    "action": action,
                    "reasons": reasons,
                    "reversal_signal": None,
                    "confidence": confidence
                }
            
            # رفض الدخول إذا كان الذيل السفلي > 30% للشموع الهابطة
            if current_direction == "DOWN" and lower_wick_ratio > 0.3:
                action = "WAIT"
                reasons.append(f"⏳ REJECTION_IN_PROGRESS: ذيل سفلي {lower_wick_ratio*100:.0f}% > 30%")
                confidence = 60
                return {
                    "action": action,
                    "reasons": reasons,
                    "reversal_signal": None,
                    "confidence": confidence
                }
            
            # ===== 🛑 الحاجز 4: فلتر إجهاد الموجة المطور V8.0 =====
            # ⚡ السماح بالشموع الكبيرة (الزخم القوي) حتى 5x بدلاً من 3x
            volume_ratio = candle_volume / avg_volume_5 if avg_volume_5 > 0 else 1.0
            
            if volume_ratio > 5.0:  # زيادة الحد من 3x إلى 5x
                reasons.append(f"⚠️ EXHAUSTION: حجم {volume_ratio:.1f}x > 5x (إجهاد شديد)")
                # نمنع الدخول ولكن لا نعتبرها انعكاساً فورياً
                action = "HOLD"
                confidence = 50
            elif volume_ratio > 3.0:  # شمعة قوية (3x-5x) - زخم حقيقي
                reasons.append(f"💪 STRONG_MOMENTUM: حجم {volume_ratio:.1f}x (زخم مؤسسي)")
                # نسمح بالدخول لكن بحذر
                confidence = 85
            
            # ===== 🛑 الحاجز 5: قانون الابتلاع المطور V8.0 =====
            # التصحيح السريع مسموح به حتى 70% بدلاً من 50%
            if engulfing_ratio > 0.7:  # زيادة من 0.5 إلى 0.7
                reasons.append(f"🔄 DEEP_RETRACEMENT: ابتلاع {engulfing_ratio*100:.0f}% > 70%")
                reversal_signal = "POSSIBLE_REVERSAL"
                confidence = 80
            elif engulfing_ratio > 0.5:  # تصحيح طبيعي (50%-70%)
                reasons.append(f"⏳ NORMAL_RETRACEMENT: ابتلاع {engulfing_ratio*100:.0f}%")
                # لا نعتبرها انعكاساً فورياً
                confidence = 70
            
            # ===== 🎯 شرط دخول "القناص" (Sniper Entry) V8.0 =====
            # نطاق الدخول المثالي: 2-5 نقاط من مستوى الكسر
            if breakout_level:
                current_dist = abs(current_price - breakout_level)
                
                if current_dist <= 2:  # السعر في منطقة الكسر مباشرة
                    action = "EXECUTE_NOW"
                    reasons.append(f"🎯 SNIPER_ZONE: السعر عند مستوى الكسر ({current_dist} نقطة)")
                    entry_level = current_price
                    confidence = 95
                elif current_dist <= 5:  # السعر قريب من مستوى الكسر
                    action = "EXECUTE"
                    reasons.append(f"🎯 ENTRY_ZONE: السعر قريب من مستوى الكسر ({current_dist} نقطة)")
                    entry_level = current_price
                    confidence = 90
                elif current_dist > 5 and current_dist <= 10:  # السعر ابتعد قليلاً
                    action = "WAIT_FOR_RETEST"
                    reasons.append(f"⏳ WAIT_RETEST: السعر ابتعد {current_dist} نقطة - انتظر إعادة اختبار {breakout_level}")
                    entry_level = breakout_level
                    confidence = 85
            
            # ===== ⏱️ حساب مدة الصفقة المتوقعة =====
            if action in ["EXECUTE", "EXECUTE_NOW"] and targets:
                # تقدير المدة بناءً على المسافة للهدف الأول ومتوسط الحركة
                atr_estimate = total_size  # تقديري باستخدام حجم الشمعة الحالية
                if atr_estimate > 0 and targets[0]:
                    dist_to_target = abs(targets[0] - current_price)
                    expected_candles = max(2, min(10, round(dist_to_target / atr_estimate)))
                    trade_duration = f"{expected_candles} شموع"
            
            # ===== ✅ النتيجة النهائية =====
            result = {
                "action": action,
                "reasons": reasons,
                "reversal_signal": reversal_signal,
                "confidence": min(100, confidence),
                "volume_ratio": volume_ratio,
                "upper_wick_ratio": upper_wick_ratio,
                "lower_wick_ratio": lower_wick_ratio
            }
            
            if entry_level:
                result["entry_level"] = entry_level
            if stop_loss:
                result["stop_loss"] = stop_loss
            if targets:
                result["targets"] = targets
            if trade_duration:
                result["trade_duration"] = trade_duration
            
            return result

        def calculate_trade_duration(distance_to_target, avg_candle_range, timeframe):
            """
            حساب مدة الصفقة المتوقعة بعدد الشموع
            """
            if avg_candle_range <= 0:
                return "غير معروف"
            
            expected_candles = round(distance_to_target / avg_candle_range)
            # تأمين المدة: لا تقل عن 2 ولا تزيد عن 10 لضمان الأمان
            safe_duration = max(2, min(expected_candles, 10))
            
            # تحويل إلى دقائق/ساعات حسب الفريم
            if timeframe.startswith('S'):
                unit = "ثانية"
                total_seconds = safe_duration * int(timeframe[1:])
                return f"{safe_duration} شموع ({total_seconds} ثانية)"
            elif timeframe.startswith('M'):
                unit = "دقيقة"
                total_minutes = safe_duration * int(timeframe[1:])
                return f"{safe_duration} شموع ({total_minutes} دقيقة)"
            elif timeframe.startswith('H'):
                unit = "ساعة"
                total_hours = safe_duration * int(timeframe[1:])
                return f"{safe_duration} شموع ({total_hours} ساعة)"
            else:
                return f"{safe_duration} شموع"

        def calculate_success_weighted_score_v8(
            distance_safety, volume_force, reversal_potential,
            timeframe_alignment, candle_shape, wick_rejection
        ):
            """
            معادلة ترجيح النجاح V8.0 - مع إضافة معامل رفض الذيل
            الأولويات المعدلة: 30% مسافة | 25% حجم | 20% انعكاس | 15% شكل | 10% رفض الذيل
            """
            # مسافة الأمان (30%)
            distance_score = min(30, distance_safety * 3)
            
            # قوة الحجم (25%) - مع السماح بالشموع الكبيرة
            volume_score = min(25, volume_force * 12.5)  # 2x = 25 درجة
            
            # إمكانية الانعكاس (20%)
            reversal_score = min(20, reversal_potential * 10)
            
            # توافق الإطارات (15%)
            timeframe_score = min(15, timeframe_alignment * 3.75)
            
            # شكل الشمعة (10%)
            shape_score = min(10, candle_shape * 2)
            
            # معامل رفض الذيل (10%) - جديد
            # كلما قل الذيل، زادت الدرجة
            wick_score = max(0, 10 - (wick_rejection * 20))  # ذيل 50% = 0 درجة
            
            total_score = distance_score + volume_score + reversal_score + timeframe_score + shape_score + wick_score
            
            return {
                "score": min(100, max(0, total_score)),
                "breakdown": {
                    "distance": distance_score,
                    "volume": volume_score,
                    "reversal": reversal_score,
                    "timeframe": timeframe_score,
                    "shape": shape_score,
                    "wick": wick_score
                }
            }

        # متغيرات بروتوكول التشريح الهيكلي
        breakout_force = 0.0
        avg_volume = 0.0
        has_fvg = False
        liquidity_sweep_detected = False
        engulfing_ratio = 0.0
        distance_to_peak = 100.0
        distance_to_bottom = 100.0
        is_at_historical_peak = False
        is_at_historical_bottom = False
        current_direction = "UP"
        
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
            "max_tokens": 1200,
            "temperature": 0.0,
            "top_p": 1.0,
            "random_seed": 42
        }
        
        # تحضير سياق التحليل السابق
        previous_context_info = ""
        
        # البرومبت المطور V8.0
        MAIN_PROMPT = f"""
أنت محلل فني خبير SMC/ICT مع تحديث V8.0 ELITE SNIPER.
مهمتك تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية مع تحديد مدة الصفقة المتوقعة بعدد الشموع.

🎯 التوجيه الأساسي: التركيز الكامل على استخراج البيانات من الصورة فقط

[1] المعطيات المستلمة:
• إطار الشموع: {candle}
• فريم التحقق: {verification_timeframe}
• جلسة السوق: {session_name} ({session_time})
• نمط السوق: {market_mode}
• نافذة التنفيذ: {window_status}

[2] نظام الاستخراج البصري المتقدم:

🔍 المرحلة 1: استخراج البيانات الأساسية
1. السعر الحالي (Current Price): [اقرأ من المحور Y]
2. أعلى سعر (High): [اقرأ من آخر شمعة]
3. أدنى سعر (Low): [اقرأ من آخر شمعة]
4. سعر الافتتاح (Open): [اقرأ من آخر شمعة]
5. سعر الإغلاق (Close): [اقرأ من آخر شمعة]
6. الوقت على المنصة: [اقرأ من ساعة المنصة]
7. القمة التاريخية (Historical Peak): [أعلى نقطة في آخر 30 شمعة]
8. القاع التاريخي (Historical Bottom): [أدنى نقطة في آخر 30 شمعة]

🔍 المرحلة 2: تحليل الشموع الثلاث الأخيرة
• الشمعة -1: [اللون]، الجسم/الشمعة = [%]، الذيل العلوي = [%]، الذيل السفلي = [%]، الحجم النسبي = [x من المتوسط]
• الشمعة -2: [اللون]، الجسم/الشمعة = [%]، الذيل العلوي = [%]، الذيل السفلي = [%]، الحجم النسبي = [x من المتوسط]
• الشمعة -3: [اللون]، الجسم/الشمعة = [%]، الذيل العلوي = [%]، الذيل السفلي = [%]، الحجم النسبي = [x من المتوسط]

🔍 المرحلة 3: حساب المسافات والمستويات
• المسافة للقمة التاريخية: [X.X] نقطة
• المسافة للقاع التاريخي: [X.X] نقطة
• أقرب مستوى دعم: [السعر]
• أقرب مستوى مقاومة: [السعر]
• مستوى الكسر المحتمل: [السعر]
• متوسط حركة الشمعة (ATR): [X.X] نقطة

🔍 المرحلة 4: حساب مدة الصفقة المتوقعة
• المسافة للهدف الأول: [X.X] نقطة
• عدد الشموع المتوقعة = المسافة / متوسط الحركة = [X] شموع
• المدة الزمنية المقترحة: [X] شموع على فريم {candle}

[3] قوانين V8.0 ELITE SNIPER:

⚡ 1. نافذة التنفيذ الموسعة:
   - فريمات الثواني: الدخول مسموح من 0 إلى 45 ثانية
   - فريمات الدقائق: الدخول مسموح من 0 إلى 50 ثانية
   - المنطق: أول 5 ثوانٍ تحدد اتجاه الشمعة، آخر 10 ثوانٍ للتجميع

💪 2. فلتر الزخم القوي:
   - الشموع من 3x إلى 5x من المتوسط = زخم مؤسسي حقيقي (مسموح)
   - الشموع > 5x من المتوسط = إجهاد شديد (احتفاظ)

🔄 3. قانون الابتلاع المطور:
   - ابتلاع 50-70% = تصحيح طبيعي (لا انعكاس)
   - ابتلاع > 70% = انعكاس محتمل

🎯 4. منطقة القناص (Sniper Zone):
   - الدخول المثالي: 0-2 نقطة من مستوى الكسر (تنفيذ فوري)
   - منطقة آمنة: 2-5 نقاط من مستوى الكسر (تنفيذ)
   - خارج المنطقة: انتظر إعادة اختبار المستوى

🚫 5. حظر القمم التاريخية:
   - ممنوع الشراء عند قمة تاريخية مهما كان الزخم
   - عند القمة: انعكاس فوري (بيع) من القمة -2 نقطة

⏱️ 6. حساب المدة الزمنية:
   - المدة = المسافة للهدف / متوسط الحركة
   - الناتج = عدد الشموع المتوقعة
   - تأمين المدة: بين 2 و 10 شموع

📊 مصفوفة القرار V8.0:

| الحالة | الشرط | القرار | المدة المتوقعة |
|--------|-------|--------|----------------|
| قمة تاريخية | السعر ≥ 99.5% من القمة | بيع انعكاسي 🔴 من (القمة-2) | 3-5 شموع |
| قاع تاريخي | السعر ≤ 100.5% من القاع | شراء انعكاسي 🟢 من (القاع+2) | 3-5 شموع |
| كسر حقيقي | حجم 3-5x + ذيل <30% + في نافذة التنفيذ | دخول مع الاتجاه 🟢/🔴 | 2-4 شموع |
| في منطقة القناص | 0-2 نقطة من الكسر | تنفيذ فوري ⚡ | 2-3 شموع |
| خارج المنطقة | >5 نقاط من الكسر | انتظر إعادة اختبار ⏳ | - |
| ابتلاع >70% | شمعة عكسية تبتلع >70% | انعكاس محتمل 🔄 | 4-6 شموع |

⚠️ نظام الأولويات النهائي V8.0:
1. فحص القمم/القيعان التاريخية (حظر فوري)
2. فحص الرفض السعري (ذيل >30%)
3. فحص الابتلاع (تصحيح vs انعكاس)
4. فحص الزخم (حجم 3-5x = قوة، >5x = إجهاد)
5. منطقة الدخول (0-5 نقاط من الكسر)
6. حساب المدة المتوقعة

🎯 التنسيق المطلوب للإجابة (الالتزام حرفياً) وبشكل مختصر:

📊 التحليل الفني المتقدم:
• بروتوكول الفشل الذاتي: [الصورة صالحة / الصورة غير صالحة - يرجى إرسال صورة أوضح]
• الوقت على المنصة (المرجع): [الوقت المستخرج من الصورة]
• حالة الهيكل: [صاعد/هابط/متذبذب] + (توافق 4/4 إطارات: نعم / لا)
• خريطة السيولة: [أقرب فخ سيولة Inducement] + [مناطق السيولة المستهدفة]
• الثواني المنقضية: [X] ثانية (نافذة التنفيذ: [داخل/خارج] النافذة الذهبية)

🎯 الإشارة التنفيذية:
• السعر الحالي: [السعر الدقيق من الشارت]
• نقطة الدخول المعدلة: [السعر بعد معامل الانزلاق إن وجد - أو نقطة إعادة الاختبار]
• حالة الشمعة: [مفتوحة / مغلقة] [حمراء 🔴 / خضراء 🟢] - الوقت المتبقي: [الوقت الدقيق من ساعة المنصة] 
• القرار الفني: [شراء 🟢 / بيع 🔴 / كسر هيكلي 💥 / سحب سيولة 🔄 / انعكاس فوري 🔄 / فخ سيولة 🚨]
• قوة الإشارة: [عالية جدا 💥 / عالية 🔥 / متوسطة ⚡ / ضعيفة ❄️]
• نقطة الدخول: [السعر الدقيق + شرط الدخول - يفضل إعادة اختبار المستوى]
• نوع الدخول 🚨: [دخول مباشر / إعادة اختبار / انعكاس فوري]
• وقت الدخول: [ فوري (Flash) / بعد إغلاق الشمعة الحالية / بعد إعادة اختبار المستوى]
• الأهداف الربحية:
  🎯 TP1: [السعر] - [سحب أول سيولة داخلية] - [احتمالية الوصول: عالية/متوسطة/منخفضة]
  🎯 TP2: [السعر] - [الهدف الرئيسي - منطقة عرض/طلب قوية] - [احتمالية الوصول: عالية/متوسطة/منخفضة]
  🎯 TP3: [السعر] - [سيولة خارجية أو سد فجوة سعرية] - [احتمالية الوصول: عالية/متوسطة/منخفضة]
• وقف الخسارة: [السعر] - [مع 3 طبقات حماية]
• المدة المتوقعة: [عدد الدقائق/الساعات]
• وقت الذروة المتوقع: [خلال الـ X شموع القادمة / بعد X دقيقة]

⚠️ إدارة المخاطر:
• مستوى الثقة: [0-100]٪ = [💥/🔥/⚡/🔄/❄️/🚫]
• نسبة نجاح الصفقة المتوقعة (V7.5 FINAL): [XX]% - [التقييم: ممتاز/جيد/متوسط/ضعيف]
• نقطة الإلغاء: [السعر الذي يفسد التحليل]
• 🚨 تنبيه فخ السيولة: [نشط / غير نشط] - [إذا نشط: تفاصيل التنبيه]

الآن قم بتحليل الشارت المرفق بتطبيق قوانين V8.0 وأعطني النتيجة بالتنسيق المطلوب.
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        # --- التحليل ---
        await wait_msg.edit_text("🔍 جاري التحليل بتقنية V8.0 ELITE SNIPER...")
        
        payload = {
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
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=45)
        
        if response.status_code != 200:
            print(f"Obeida Vision Error: {response.status_code} - {response.text}")
            await wait_msg.edit_text(f"❌ خطأ في التحليل: {response.status_code}")
            raise Exception(f"خطأ في التحليل: {response.status_code}")
        
        analysis_result = response.json()['choices'][0]['message']['content'].strip()
        
        # تنظيف النصوص من التكرار
        analysis_result = clean_repeated_text(analysis_result)
        
        # حفظ سياق التحليل
        save_analysis_context(user_id, analysis_result)
        
        # إضافة تحذيرات V8.0 إذا لزم الأمر
        enhanced_result = analysis_result
        
        if "قمة تاريخية" in analysis_result and "شراء" in analysis_result.lower():
            enhanced_result += """

🚨 **تنبيه Obeida Trading V8.0 ELITE SNIPER:**  
⚠️⚠️⚠️ **تحذير عالي الخطورة** ⚠️⚠️⚠️  
السعر عند قمة تاريخية!  
✅ القرار الإلزامي: **انعكاس فوري (بيع)** من مستوى القمة - 2 نقطة  
🚫 ممنوع الشراء نهائياً في هذه المنطقة"""
        
        # إضافة معلومات نافذة التنفيذ
        enhanced_result += f"""

⏱️ **معلومات التوقيت V8.0:**
• الثواني المنقضية: {seconds_passed} ثانية
• نافذة التنفيذ: {"✅ داخل النافذة (مسموح)" if in_golden_window else "❌ خارج النافذة (احتفاظ)"}
• وقت إغلاق الشمعة: {seconds_remaining} ثانية متبقية"""
        
        # إعداد الأزرار
        keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصية"], ["🌍 دولتك"], ["الرجوع للقائمة الرئيسية"]]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        # تنسيق النتيجة النهائية
        full_result = (
            f"✅ **تم التحليل V8.0 ELITE SNIPER**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{enhanced_result}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔧 **الإعدادات:**\n"
            f"• فريم التحليل: {candle}\n"
            f"• جلسة السوق: {session_name}\n"
            f"• نافذة التنفيذ: {window_status}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Obeida Trading V8.0 - قناص بلا أخطاء**"
        )
        
        # تنظيف النهائي من التكرارات
        full_result = clean_repeated_text(full_result)
        
        # تنسيق النتيجة مع دعم النسخ السريع
        formatted_result = format_as_copyable_code(full_result, "text")
        
        # تقسيم النتيجة إذا كانت طويلة
        if len(formatted_result) > 4000:
            parts = split_message_with_copy_support(full_result, max_length=3500)
            
            await wait_msg.edit_text(
                parts[0],
                parse_mode="Markdown"
            )
            
            for part in parts[1:]:
                await update.message.reply_text(part, parse_mode="Markdown")
        else:
            await wait_msg.edit_text(
                formatted_result,
                parse_mode="Markdown"
            )
        
        # إرسال الأزرار
        await update.message.reply_text(
            "📊 **اختر الإجراء التالي:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        
        return full_result
        
    except requests.exceptions.Timeout:
        if 'wait_msg' in locals():
            await wait_msg.edit_text("⏱️ تجاوز الوقت المحدد. حاول مرة أخرى.")
        else:
            await update.message.reply_text("⏱️ تجاوز الوقت المحدد. حاول مرة أخرى.")
        return "⏱️ تجاوز الوقت المحدد"
    except Exception as e:
        print(f"❌ خطأ في تحليل الصورة: {traceback.format_exc()}")
        error_msg = f"❌ **حدث خطأ:** {str(e)[:200]}\nيرجى المحاولة مرة أخرى."
        if 'wait_msg' in locals():
            await wait_msg.edit_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return error_msg
    finally:
        # تنظيف الملفات المؤقتة
        for filepath in [image_path, compressed_path] if 'compressed_path' in locals() else [image_path]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception as e:
                    print(f"⚠️ خطأ في حذف الملف المؤقت: {e}")
    
    return MAIN_MENU

async def handle_recommendation_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور في وضع التوصية"""
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return RECOMMENDATION_MODE
    
    user_id = update.effective_user.id
    
    # تسجيل استخدام
    SubscriptionSystem.increment_usage(user_id, 'recommendation_photo')
    
    # الحصول على آخر عملة تم اختيارها
    last_symbol = get_last_recommendation_symbol(context)
    
    wait_msg = await update.message.reply_text(f"📊 استنى شوي العملة: {last_symbol} قيد تحليل 🤌...")
    
    try:
        # حفظ الصورة مؤقتاً
        photo = await update.message.photo[-1].get_file()
        timestamp = int(time.time())
        image_path = os.path.join(IMAGE_CACHE_DIR, f"recommendation_{user_id}_{timestamp}.jpg")
        await photo.download_to_drive(image_path)
        
        # الحصول على إعدادات المستخدم
        candle, trade_time, _, _, _ = get_user_setting(user_id)
        
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
        
        # تنسيق النتيجة مع دعم النسخ السريع
        formatted_result = format_as_copyable_code(analysis_result, "text")
        await wait_msg.edit_text(formatted_result, parse_mode="Markdown")
                
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

async def handle_photo_in_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور في وضع التحليل"""
    return await handle_photo_analysis(update, context)

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور للتحليل الفني المتقدم"""
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return MAIN_MENU
    
    user_id = update.effective_user.id
    candle, trade_time, prev_context, prev_time, _ = get_user_setting(user_id)
    
    # تسجيل استخدام
    SubscriptionSystem.increment_usage(user_id, 'photo_analysis')
    
    if not candle or not trade_time:
        keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"], ["🌍 دولتك"]]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**\n\n"
            "الرجاء استخدام أزرار القائمة لضبط الإعدادات قبل تحليل الصور.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    wait_msg = await update.message.reply_text(" 📸 تم استلام الصورة بنجاح 📸")
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
        
        # تحليل الصورة باستخدام الدالة المحسنة
        analysis_result = await analyze_chart_image(
            update, 
            context, 
            original_path, 
            candle, 
            trade_time, 
            "الشارت المرفق"
        )
        
        # التحليل_result يحتوي بالفعل على النتيجة النهائية مع التنسيق
        # تم التعامل مع إرسال النتيجة داخل الدالة analyze_chart_image
        
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

async def start_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء لوحة تحكم المشرف"""
    user_id = update.effective_user.id
    
    # التحقق من أن المستخدم هو المشرف
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول للوحة التحكم.")
        return MAIN_MENU
    
    keyboard = [
        ["👥 عرض المستخدمين", "➕ إضافة اشتراك"],
        ["🗑️ إلغاء اشتراك", "⏱️ تمديد اشتراك"],
        ["🚫 حظر مستخدم", "✅ إلغاء حظر"],
        ["📢 إذاعة للمشتركين", "📊 إحصائيات"],
        ["🔙 العودة للقائمة الرئيسية"]
    ]
    
    await update.message.reply_text(
        "👑 **لوحة تحكم المشرف** 👑\n\n"
        "🔑 **نظام إدارة الاشتراكات المتطور V2**\n"
        "اختر الإجراء المطلوب:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return ADMIN_MENU

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات لوحة تحكم المشرف"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول للوحة التحكم.")
        return MAIN_MENU
    
    if user_message == "🔙 العودة للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"], ["🌍 دولتك"], ["👑 لوحة التحكم"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "👥 عرض المستخدمين":
        users = SubscriptionSystem.get_all_users()
        
        if not users:
            await update.message.reply_text("📭 لا يوجد مستخدمين بعد.")
        else:
            msg = "👥 **قائمة المستخدمين:**\n\n"
            for u in users:
                uid, username, first_name, created, last_active, is_blocked, sub_type, sub_name, start_date, end_date, is_active = u
                
                # تحديد الحالة
                if is_blocked == 1:
                    status = "🚫 محظور"
                elif is_active == 1 and (not end_date or datetime.fromisoformat(end_date) > datetime.now()):
                    status = "✅ نشط"
                else:
                    status = "❌ غير نشط"
                
                # معلومات الاشتراك
                sub_info = f"{sub_name or 'لا يوجد اشتراك'}"
                end_info = f" حتى {end_date[:16]}" if end_date else " (غير محدد)"
                
                # اسم العرض
                display_name = first_name or username or str(uid)
                
                msg += f"• **{display_name}**\n"
                msg += f"  🆔 `{uid}`\n"
                msg += f"  📅 الاشتراك: {sub_info}{end_info}\n"
                msg += f"  ⚡ الحالة: {status}\n"
                msg += f"  📆 آخر نشاط: {last_active[:16] if last_active else 'غير معروف'}\n\n"
            
            for part in split_message(msg, 4000):
                await update.message.reply_text(part, parse_mode="Markdown")
        
        return ADMIN_MENU
    
    elif user_message == "➕ إضافة اشتراك":
        await update.message.reply_text(
            "➕ **إضافة اشتراك لمستخدم**\n\n"
            "أرسل معرف المستخدم (User ID):\n"
            "مثال: `123456789`",
            parse_mode="Markdown"
        )
        return ADD_SUBSCRIPTION
    
    elif user_message == "🗑️ إلغاء اشتراك":
        await update.message.reply_text(
            "🗑️ **إلغاء اشتراك مستخدم**\n\n"
            "أرسل معرف المستخدم للإلغاء:\n"
            "مثال: `123456789`",
            parse_mode="Markdown"
        )
        return REMOVE_USER
    
    elif user_message == "⏱️ تمديد اشتراك":
        await update.message.reply_text(
            "⏱️ **تمديد اشتراك مستخدم**\n\n"
            "أرسل معرف المستخدم والمدة الإضافية بالثواني:\n"
            "مثال: `123456789 86400` لتمديد يوم",
            parse_mode="Markdown"
        )
        return SET_USER_DURATION
    
    elif user_message == "🚫 حظر مستخدم":
        await update.message.reply_text(
            "🚫 **حظر مستخدم**\n\n"
            "أرسل معرف المستخدم للحظر:\n"
            "مثال: `123456789`\n\n"
            "يمكنك إضافة سبب بعد المعرف (اختياري)",
            parse_mode="Markdown"
        )
        return REMOVE_USER
    
    elif user_message == "✅ إلغاء حظر":
        await update.message.reply_text(
            "✅ **إلغاء حظر مستخدم**\n\n"
            "أرسل معرف المستخدم لإلغاء الحظر:\n"
            "مثال: `123456789`",
            parse_mode="Markdown"
        )
        return REMOVE_USER
    
    elif user_message == "📢 إذاعة للمشتركين":
        await update.message.reply_text(
            "📢 **إذاعة للمشتركين**\n\n"
            "أرسل الرسالة التي تريد إذاعتها لجميع المشتركين النشطين:",
            parse_mode="Markdown"
        )
        return BROADCAST_MESSAGE
    
    elif user_message == "📊 إحصائيات":
        stats = SubscriptionSystem.get_stats()
        
        stats_text = (
            "📊 **إحصائيات النظام المتقدم:**\n\n"
            f"👥 إجمالي المستخدمين: {stats.get('total_users', 0)}\n"
            f"🚫 المحظورين: {stats.get('blocked_users', 0)}\n"
            f"✅ المشتركين النشطين: {stats.get('active_users', 0)}\n\n"
            f"📈 **الاشتراكات حسب النوع:**\n"
        )
        
        for sub_type, count in stats.get('subscriptions_by_type', {}).items():
            stats_text += f"  • {sub_type}: {count}\n"
        
        stats_text += f"\n📊 **أكثر الميزات استخداماً:**\n"
        for feature, count in list(stats.get('feature_usage', {}).items())[:5]:
            stats_text += f"  • {feature}: {count} مرة\n"
        
        await update.message.reply_text(stats_text, parse_mode="Markdown")
        return ADMIN_MENU
    
    return ADMIN_MENU

async def handle_add_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إضافة اشتراك لمستخدم"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    try:
        target_user_id = int(update.message.text.strip())
        
        # حفظ معرف المستخدم في السياق
        context.user_data['target_user_id'] = target_user_id
        
        # عرض خيارات المدة
        keyboard = [
            ["🕐 ساعة", "📅 يوم"],
            ["📆 أسبوع", "📅 شهر"],
            ["📅 سنة", "♾️ مدى الحياة"],
            ["إلغاء", "🔙 العودة للقائمة الرئيسية"],
            ["➕ إضافة اشتراك"]
        ]
        
        await update.message.reply_text(
            f"➕ **إضافة اشتراك للمستخدم:** `{target_user_id}`\n\n"
            f"اختر مدة الاشتراك:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
            parse_mode="Markdown"
        )
        return SELECT_DURATION
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال معرف المستخدم (أرقام فقط).")
        return ADD_SUBSCRIPTION

async def handle_select_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار مدة الاشتراك"""
    user_id = update.effective_user.id
    duration_choice = update.message.text.strip()
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    if duration_choice == "إلغاء":
        return await start_admin_panel(update, context)
    
    # تحديد نوع الاشتراك حسب الاختيار
    subscription_type = None
    if duration_choice == "🕐 ساعة":
        subscription_type = "hourly"
    elif duration_choice == "📅 يوم":
        subscription_type = "daily"
    elif duration_choice == "📆 أسبوع":
        subscription_type = "weekly"
    elif duration_choice == "📅 شهر":
        subscription_type = "monthly"
    elif duration_choice == "📅 سنة":
        subscription_type = "yearly"
    elif duration_choice == "♾️ مدى الحياة":
        subscription_type = "lifetime"
    else:
        await update.message.reply_text("❌ اختيار غير صالح.")
        return SELECT_DURATION
    
    target_user_id = context.user_data.get('target_user_id')
    if not target_user_id:
        await update.message.reply_text("❌ خطأ: لم يتم تحديد المستخدم.")
        return ADMIN_MENU
    
    # إضافة الاشتراك
    success, result = SubscriptionSystem.add_subscription(
        user_id=target_user_id,
        subscription_type=subscription_type,
        added_by=user_id,
        reason=f"Added by admin via panel"
    )
    
    if success:
        sub_info = result
        end_info = ""
        if sub_info['end_date']:
            end_datetime = datetime.fromisoformat(sub_info['end_date'])
            end_info = f"ينتهي في: {end_datetime.strftime('%Y-%m-%d %H:%M')}"
        else:
            end_info = "غير محدد المدة (مدى الحياة)"
        
        await update.message.reply_text(
            f"✅ **تم إضافة الاشتراك بنجاح!**\n\n"
            f"👤 المستخدم: `{target_user_id}`\n"
            f"📅 النوع: {sub_info['subscription_name']}\n"
            f"📆 تاريخ البداية: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"{end_info}",
            parse_mode="Markdown"
        )
        
        # محاولة إرسال إشعار للمستخدم
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=f"✅ **تم تفعيل اشتراكك في البوت الخاص بنا!**\n\n"
                     f"📅 نوع الاشتراك: {sub_info['subscription_name']}\n"
                     f"{end_info}\n\n"
                     f"أرسل /start لبدء استخدام البوت",
                parse_mode="Markdown"
            )
        except:
            await update.message.reply_text("⚠️ لم أتمكن من إرسال إشعار للمستخدم (ربما قام بحظر البوت).")
        
    else:
        await update.message.reply_text(f"❌ فشل في إضافة الاشتراك: {result}")
    
    # تنظيف السياق
    if 'target_user_id' in context.user_data:
        del context.user_data['target_user_id']
    
    return ADMIN_MENU

async def handle_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إلغاء اشتراك أو حظر مستخدم"""
    user_id = update.effective_user.id
    previous_message = update.message.text.strip()
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    try:
        # التحقق مما إذا كان الإجراء السابق هو حظر أو إلغاء حظر
        if context.user_data.get('last_action') == 'block':
            target_user_id = int(previous_message.split()[0])
            reason = ' '.join(previous_message.split()[1:]) if len(previous_message.split()) > 1 else "Blocked by admin"
            
            success, message = SubscriptionSystem.block_user(target_user_id, reason, performed_by=user_id)
            
            if success:
                await update.message.reply_text(f"✅ {message}")
            else:
                await update.message.reply_text(f"❌ {message}")
            
            context.user_data['last_action'] = None
            return ADMIN_MENU
        
        elif context.user_data.get('last_action') == 'unblock':
            target_user_id = int(previous_message)
            
            success, message = SubscriptionSystem.unblock_user(target_user_id, performed_by=user_id)
            
            if success:
                await update.message.reply_text(f"✅ {message}")
            else:
                await update.message.reply_text(f"❌ {message}")
            
            context.user_data['last_action'] = None
            return ADMIN_MENU
        
        else:
            # إلغاء اشتراك عادي
            target_user_id = int(previous_message)
            
            if target_user_id == ADMIN_ID:
                await update.message.reply_text("❌ لا يمكن إلغاء اشتراك المشرف الرئيسي.")
                return ADMIN_MENU
            
            success, message = SubscriptionSystem.revoke_subscription(target_user_id, performed_by=user_id)
            
            if success:
                await update.message.reply_text(f"✅ {message}")
                
                # محاولة إرسال إشعار للمستخدم
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=f"⚠️ **تم إلغاء اشتراكك في البوت الخاص بنا**\n\n"
                             f"للتواصل مع المشرف: @Sz2zv",
                        parse_mode="Markdown"
                    )
                except:
                    pass
            else:
                await update.message.reply_text(f"❌ {message}")
            
            return ADMIN_MENU
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال معرف المستخدم (أرقام فقط).")
        return REMOVE_USER

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الإذاعة للمشتركين"""
    user_id = update.effective_user.id
    broadcast_message = update.message.text.strip()
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    # الحصول على جميع المشتركين النشطين
    subscribers = SubscriptionSystem.get_all_subscribers()
    
    if not subscribers:
        await update.message.reply_text("📭 لا يوجد مشتركين نشطين للإذاعة.")
        return ADMIN_MENU
    
    wait_msg = await update.message.reply_text(f"📢 جاري الإذاعة لـ {len(subscribers)} مشترك...")
    
    success_count = 0
    fail_count = 0
    
    for sub in subscribers:
        target_id = sub[0]
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"📢 **رسالة إدارية**\n\n{broadcast_message}\n\n━━━━━━━━━━━━\nObeida Trading",
                parse_mode="Markdown"
            )
            success_count += 1
            await asyncio.sleep(0.05)  # تجنب تجاوز حدود التليجرام
        except Exception as e:
            print(f"⚠️ فشل إرسال للمستخدم {target_id}: {e}")
            fail_count += 1
    
    await wait_msg.edit_text(
        f"📊 **نتيجة الإذاعة**\n\n"
        f"✅ تم الإرسال بنجاح لـ: {success_count} مشترك\n"
        f"❌ فشل الإرسال لـ: {fail_count} مشترك"
    )
    
    return ADMIN_MENU

async def handle_set_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة تمديد اشتراك مستخدم"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    try:
        parts = update.message.text.strip().split()
        if len(parts) != 2:
            await update.message.reply_text(
                "❌ صيغة خاطئة. استخدم: `user_id 86400`\n"
                "مثال: `123456789 86400`",
                parse_mode="Markdown"
            )
            return SET_USER_DURATION
        
        target_user_id = int(parts[0])
        additional_seconds = int(parts[1])
        
        if target_user_id == ADMIN_ID:
            await update.message.reply_text("✅ المشرف الرئيسي لديه صلاحيات غير محدودة.")
            return ADMIN_MENU
        
        success, message = SubscriptionSystem.extend_subscription(
            target_user_id, 
            additional_seconds, 
            performed_by=user_id
        )
        
        if success:
            await update.message.reply_text(f"✅ {message}")
            
            # محاولة إرسال إشعار للمستخدم
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"✅ **تم تمديد اشتراكك في البوت الخاص بنا**\n\n{message}",
                    parse_mode="Markdown"
                )
            except:
                pass
        else:
            await update.message.reply_text(f"❌ {message}")
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال معرف المستخدم والمدة بشكل صحيح.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)}")
    
    return ADMIN_MENU

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

async def periodic_cleanup():
    """تنظيف دوري للملفات المؤقتة - كل 1 دقائق"""
    while True:
        try:
            await asyncio.sleep(60)  # كل 1 دقائق (بدلاً من 5 دقيقة)
            cleanup_old_images()
            cleanup_old_database_records()
            print("🧹 تم التنظيف الدوري للملفات المؤقتة (كل 1 دقائق)")
            
        except Exception as e:
            print(f"⚠️ خطأ في التنظيف الدوري: {e}")

async def backup_scheduler():
    """جدولة النسخ الاحتياطي التلقائي كل ساعة"""
    while True:
        try:
            await asyncio.sleep(3600)  # كل 60 دقائق
            DataProtection.auto_backup()
            print("⏰ تم تنفيذ النسخ الاحتياطي التلقائي المجدول")
        except Exception as e:
            print(f"⚠️ خطأ في النسخ الاحتياطي المجدول: {e}")

# دالة DataProtection للنسخ الاحتياطي (مبسطة)
class DataProtection:
    @staticmethod
    def backup_database():
        """نسخ احتياطي لقاعدة البيانات الرئيسية"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(BACKUP_DIR, f"main_backup_{timestamp}.db")
            if os.path.exists(DB_NAME):
                shutil.copy2(DB_NAME, backup_file)
                print(f"💾 تم نسخ قاعدة البيانات الرئيسية: {backup_file}")
        except Exception as e:
            print(f"⚠️ خطأ في نسخ قاعدة البيانات: {e}")
    
    @staticmethod
    def backup_codes_db():
        """نسخ احتياطي لقاعدة بيانات الاشتراك"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(BACKUP_DIR, f"subscription_backup_{timestamp}.db")
            if os.path.exists(SUBSCRIPTION_DB_NAME):
                shutil.copy2(SUBSCRIPTION_DB_NAME, backup_file)
                print(f"💾 تم نسخ قاعدة بيانات الاشتراك: {backup_file}")
        except Exception as e:
            print(f"⚠️ خطأ في نسخ قاعدة بيانات الاشتراك: {e}")
    
    @staticmethod
    def auto_backup():
        """نسخ احتياطي تلقائي للجميع"""
        DataProtection.backup_database()
        DataProtection.backup_codes_db()

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
    
    🌍 **نظام المناطق الزمنية:**
    • اختر "دولتك" من القائمة الرئيسية
    • سيتم ضبط جميع التحليلات على توقيت دولتك
    • دعم أكثر من 70 دولة حول العالم
    
    🔑 **أسعار الاشتراكات:**
    • ساعة : 1$
    • يوم : 5$
    • سبوعي : 15$
    • شهري : 40$
    • نسخة خاص بيك : 300$
    
    🤯  انواع القرار الفني 🤯
     🟢 شراء :  مع الاتجاه 
     🔴 بيع : مع الاتجاه
     💥 كسر هيكلي :  تغيير اتجاه
     🔄 سحب سيولة :  عكس الاتجاه
     🔄 انعكاس فوري : تصحيح مسار
    
    ⏱️ **خيارات مدة الصفقة:**
    • **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة
    • **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة
    • **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة
    """
    
    formatted_help = format_as_copyable_code(help_text, "markdown")
    await update.message.reply_text(formatted_help, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    if update.effective_user:
        cleanup_user_data(context, update.effective_user.id)
    
    await update.message.reply_text(
        "تم الإلغاء وتم تنظيف الذاكرة. اكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def run_flask_server():
    """تشغيل Flask server مع نظام مصادقة متكامل"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# --- Flask Server مع واجهة تحكم محسنة ونظام مصادقة ---
app = Flask(__name__)
app.secret_key = SECRET_KEY

# دالة للتحقق من تسجيل الدخول
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# HTML template لصفحة تسجيل الدخول
LOGIN_PAGE_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🛑 لوحة تسجيل الدخول 🛑</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        .login-container {
            background: white;
            border-radius: 20px;
            padding: 40px;
            width: 90%;
            max-width: 400px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            animation: slideIn 0.5s ease;
        }
        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translateY(-20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        .login-header {
            text-align: center;
            margin-bottom: 30px;
        }
        .login-header h1 {
            color: #333;
            font-size: 2em;
            margin-bottom: 10px;
        }
        .login-header p {
            color: #666;
            font-size: 0.9em;
        }
        .logo {
            font-size: 4em;
            margin-bottom: 20px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        .form-group label {
            display: block;
            margin-bottom: 5px;
            color: #666;
            font-weight: 600;
        }
        .form-group input {
            width: 100%;
            padding: 12px;
            border: 2px solid #f0f0f0;
            border-radius: 10px;
            font-size: 1em;
            transition: border-color 0.3s;
        }
        .form-group input:focus {
            outline: none;
            border-color: #667eea;
        }
        .btn-login {
            width: 100%;
            padding: 14px;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 1.1em;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.3s;
        }
        .btn-login:hover {
            background: #5a67d8;
        }
        .error-message {
            background: #f8d7da;
            color: #721c24;
            padding: 10px;
            border-radius: 10px;
            margin-bottom: 20px;
            text-align: center;
            border: 1px solid #f5c6cb;
        }
        .footer {
            text-align: center;
            margin-top: 20px;
            color: #999;
            font-size: 0.8em;
        }
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
            <button type="submit" class="btn-login">تسجيل الدخول </button>
        </form>
        
        <div class="footer">
            <p>© 2026 Obeida Trading - جميع الحقوق محفوظة</p>
        </div>
    </div>
</body>
</html>
"""

# HTML template للوحة التحكم المحسنة
ADMIN_PANEL_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🛑 لوحة التحكم المتطورة 🛑</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        .header {
            background: white;
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }
        .header h1 {
            color: #333;
            font-size: 2.2em;
            margin-bottom: 5px;
        }
        .header .subtitle {
            color: #666;
            font-size: 1.1em;
        }
        .logout-btn {
            background: #dc3545;
            color: white;
            padding: 10px 20px;
            border-radius: 10px;
            text-decoration: none;
            font-weight: 600;
            transition: background 0.3s;
        }
        .logout-btn:hover {
            background: #c82333;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.05);
            transition: transform 0.3s;
        }
        .stat-card:hover {
            transform: translateY(-5px);
        }
        .stat-card h3 {
            color: #666;
            font-size: 1em;
            margin-bottom: 10px;
        }
        .stat-card .number {
            color: #667eea;
            font-size: 2.5em;
            font-weight: bold;
        }
        .stat-card .trend {
            color: #28a745;
            font-size: 0.9em;
            margin-top: 5px;
        }
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .tab {
            background: white;
            padding: 15px 30px;
            border-radius: 10px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.3s;
            border: 2px solid transparent;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }
        .tab.active {
            background: #667eea;
            color: white;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .section {
            background: white;
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.05);
        }
        .section-title {
            color: #333;
            font-size: 1.8em;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #f0f0f0;
        }
        .table-container {
            overflow-x: auto;
            border-radius: 10px;
            border: 1px solid #f0f0f0;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        th {
            background: #667eea;
            color: white;
            padding: 15px;
            font-weight: 600;
            text-align: center;
        }
        td {
            padding: 15px;
            border-bottom: 1px solid #f0f0f0;
            text-align: center;
        }
        tr:hover {
            background: #f8f9ff;
        }
        .status-badge {
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.9em;
            font-weight: 600;
            display: inline-block;
        }
        .status-active {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .status-expired {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .status-blocked {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .code-box {
            background: #f8f9ff;
            padding: 5px 10px;
            border-radius: 5px;
            font-family: monospace;
            font-size: 1.1em;
            border: 1px dashed #667eea;
        }
        .actions {
            display: flex;
            gap: 10px;
            justify-content: center;
            flex-wrap: wrap;
        }
        .btn {
            padding: 8px 16px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.3s;
            text-decoration: none;
            display: inline-block;
            font-size: 0.9em;
        }
        .btn-primary {
            background: #667eea;
            color: white;
        }
        .btn-primary:hover {
            background: #5a67d8;
        }
        .btn-success {
            background: #28a745;
            color: white;
        }
        .btn-success:hover {
            background: #218838;
        }
        .btn-warning {
            background: #ffc107;
            color: #333;
        }
        .btn-warning:hover {
            background: #e0a800;
        }
        .btn-danger {
            background: #dc3545;
            color: white;
        }
        .btn-danger:hover {
            background: #c82333;
        }
        .btn-info {
            background: #17a2b8;
            color: white;
        }
        .btn-info:hover {
            background: #138496;
        }
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }
        .modal-content {
            background: white;
            padding: 30px;
            border-radius: 20px;
            width: 90%;
            max-width: 600px;
            max-height: 80vh;
            overflow-y: auto;
        }
        .modal-content h2 {
            margin-bottom: 20px;
            color: #333;
        }
        .form-group {
            margin-bottom: 20px;
        }
        .form-group label {
            display: block;
            margin-bottom: 5px;
            color: #666;
            font-weight: 600;
        }
        .form-group input, .form-group select, .form-group textarea {
            width: 100%;
            padding: 12px;
            border: 2px solid #f0f0f0;
            border-radius: 10px;
            font-size: 1em;
            transition: border-color 0.3s;
        }
        .form-group input:focus, .form-group select:focus, .form-group textarea:focus {
            outline: none;
            border-color: #667eea;
        }
        .modal-actions {
            display: flex;
            gap: 10px;
            justify-content: flex-end;
            margin-top: 20px;
        }
        .search-box {
            margin-bottom: 20px;
        }
        .search-box input {
            width: 100%;
            padding: 12px;
            border: 2px solid #f0f0f0;
            border-radius: 10px;
            font-size: 1em;
        }
        .footer {
            text-align: center;
            color: white;
            margin-top: 30px;
        }
        .quick-actions {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .quick-action-card {
            background: white;
            border-radius: 15px;
            padding: 20px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
            border: 2px solid transparent;
        }
        .quick-action-card:hover {
            transform: translateY(-5px);
            border-color: #667eea;
            box-shadow: 0 5px 20px rgba(102, 126, 234, 0.2);
        }
        .quick-action-card .icon {
            font-size: 2em;
            margin-bottom: 10px;
        }
        .quick-action-card .title {
            font-weight: 600;
            color: #333;
        }
        .progress-bar {
            width: 100%;
            height: 10px;
            background: #f0f0f0;
            border-radius: 5px;
            margin-top: 10px;
        }
        .progress-fill {
            height: 100%;
            background: #667eea;
            border-radius: 5px;
            transition: width 0.3s;
        }
        .notification {
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 15px 25px;
            background: white;
            border-radius: 10px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.2);
            display: none;
            z-index: 1001;
            border-right: 5px solid #667eea;
        }
        .notification.success {
            border-right-color: #28a745;
        }
        .notification.error {
            border-right-color: #dc3545;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🤖 لوحة تحكم المنصورة عبيدة للتداول 🤖</h1>
                <p class="subtitle">نظام إدارة الاشتراكات المتطور - المشرف: @Sz2zv</p>
            </div>
            <div>
                <span style="margin-right: 20px; color: #666;">مرحباً، المالك</span>
                <a href="/logout" class="logout-btn">تسجيل الخروج</a>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>👥 جميع المستخدمين </h3>
                <div class="number">{{ stats.total_users }}</div>
                <div class="trend">+{{ stats.new_users_today if stats.new_users_today else 0 }} اليوم</div>
            </div>
            <div class="stat-card">
                <h3>✅ المشتركين الموجودين</h3>
                <div class="number">{{ stats.active_users }}</div>
                <div class="trend">{{ (stats.active_users / stats.total_users * 100)|round(1) if stats.total_users > 0 else 0 }}% من الإجمالي</div>
            </div>
            <div class="stat-card">
                <h3>🚫 المحظورين</h3>
                <div class="number">{{ stats.blocked_users }}</div>
                <div class="trend" style="color: #dc3545;">{{ (stats.blocked_users / stats.total_users * 100)|round(1) if stats.total_users > 0 else 0 }}% من الإجمالي</div>
            </div>
        </div>

        <div class="quick-actions">
            <div class="quick-action-card" onclick="showAddSubscriptionModal()">
                <div class="icon">➕</div>
                <div class="title">إضافة اشتراك</div>
            </div>
            <div class="quick-action-card" onclick="showBroadcastModal()">
                <div class="icon">📢</div>
                <div class="title">إذاعة</div>
            </div>
            <div class="quick-action-card" onclick="exportData()">
                <div class="icon">📊</div>
                <div class="title">تصدير البيانات</div>
            </div>
            <div class="quick-action-card" onclick="createBackup()">
                <div class="icon">💾</div>
                <div class="title">نسخ احتياطي</div>
            </div>
        </div>

        <div class="tabs">
            <div class="tab active" onclick="showTab('users')">👥 المستخدمين</div>
            <div class="tab" onclick="showTab('subscriptions')">📊 الاشتراكات</div>
            <div class="tab" onclick="showTab('stats')">📈 إحصائيات</div>
            <div class="tab" onclick="showTab('backups')">💾 النسخ الاحتياطي</div>
        </div>

        <div id="users-tab" class="tab-content active">
            <div class="section">
                <h2 class="section-title">👥 قائمة المستخدمين</h2>
                
                <div class="search-box">
                    <input type="text" id="searchUsers" placeholder="🔍 بحث عن مستخدم (الاسم، المعرف، البريد)..." onkeyup="searchUsers()">
                </div>

                <div class="table-container">
                    <table id="usersTable">
                        <thead>
                            <tr>
                                <th>المعرف</th>
                                <th>الاسم</th>
                                <th>نوع الاشتراك</th>
                                <th>تاريخ الانتهاء</th>
                                <th>الحالة</th>
                                <th>آخر نشاط</th>
                                <th>المنطقة الزمنية</th>
                                <th>الإجراءات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for user in users %}
                            <tr>
                                <td><span class="code-box">{{ user.id }}</span></td>
                                <td>{{ user.name }}</td>
                                <td>{{ user.subscription_type }}</td>
                                <td>{{ user.end_date }}</td>
                                <td>
                                    <span class="status-badge 
                                        {% if user.status == 'نشط' %}status-active
                                        {% elif user.status == 'محظور' %}status-blocked
                                        {% else %}status-expired{% endif %}">
                                        {{ user.status }}
                                    </span>
                                </td>
                                <td>{{ user.last_active }}</td>
                                <td>{{ user.timezone }}</td>
                                <td class="actions">
                                    <button class="btn btn-success" onclick="extendSubscription('{{ user.id }}')">⏱️ تمديد</button>
                                    <button class="btn btn-warning" onclick="blockUser('{{ user.id }}')">🚫 حظر</button>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div id="subscriptions-tab" class="tab-content">
            <div class="section">
                <h2 class="section-title">📊 توزيع الاشتراكات</h2>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>نوع الاشتراك</th>
                                <th>العدد</th>
                                <th>النسبة المئوية</th>
                                <th>الإيرادات التقديرية ($)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for type, count in stats.subscriptions_by_type.items() %}
                            <tr>
                                <td>{{ type }}</td>
                                <td>{{ count }}</td>
                                <td>{{ (count / stats.active_users * 100)|round(1) if stats.active_users > 0 else 0 }}%</td>
                                <td>${{ count * 10 }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div id="stats-tab" class="tab-content">
            <div class="section">
                <h2 class="section-title">📈 إحصائيات استخدام الميزات</h2>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>الميزة</th>
                                <th>عدد مرات الاستخدام</th>
                                <th>نسبة الاستخدام</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for feature, count in stats.feature_usage.items() %}
                            <tr>
                                <td>{{ feature }}</td>
                                <td>{{ count }}</td>
                                <td>
                                    <div style="width: 200px;">
                                        <div class="progress-bar">
                                            <div class="progress-fill" style="width: {{ (count / stats.total_usage * 100)|round(1) if stats.total_usage > 0 else 0 }}%"></div>
                                        </div>
                                    </div>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div id="backups-tab" class="tab-content">
            <div class="section">
                <h2 class="section-title">💾 النسخ الاحتياطية</h2>
                
                <div class="quick-actions">
                    <div class="quick-action-card" onclick="createManualBackup()">
                        <div class="icon">➕</div>
                        <div class="title">إنشاء نسخة احتياطية</div>
                    </div>
                </div>

                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>اسم الملف</th>
                                <th>التاريخ</th>
                                <th>الحجم</th>
                                <th>الإجراءات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for backup in backups %}
                            <tr>
                                <td>{{ backup.name }}</td>
                                <td>{{ backup.date }}</td>
                                <td>{{ backup.size }}</td>
                                <td class="actions">
                                    <button class="btn btn-primary" onclick="downloadBackup('{{ backup.name }}')">📥 تحميل</button>
                                    <button class="btn btn-danger" onclick="restoreBackup('{{ backup.name }}')">🔄 استعادة</button>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal إضافة اشتراك -->
    <div id="addSubscriptionModal" class="modal">
        <div class="modal-content">
            <h2>➕ إضافة اشتراك جديد</h2>
            <div class="form-group">
                <label>معرف المستخدم (User ID)</label>
                <input type="number" id="modalUserId" placeholder="مثال: 123456789">
            </div>
            <div class="form-group">
                <label>نوع الاشتراك</label>
                <select id="modalSubscriptionType">
                    <option value="hourly">🕐 ساعة</option>
                    <option value="daily">📅 يوم</option>
                    <option value="weekly">📆 أسبوع</option>
                    <option value="monthly">📅 شهر</option>
                    <option value="yearly">📅 سنة</option>
                    <option value="lifetime">♾️ مدى الحياة</option>
                </select>
            </div>
            <div class="modal-actions">
                <button class="btn btn-success" onclick="confirmAddSubscription()">✅ إضافة</button>
                <button class="btn btn-danger" onclick="closeModal('addSubscriptionModal')">❌ إلغاء</button>
            </div>
        </div>
    </div>

    <!-- Modal إذاعة -->
    <div id="broadcastModal" class="modal">
        <div class="modal-content">
            <h2>📢 إذاعة للمشتركين</h2>
            <div class="form-group">
                <label>الرسالة</label>
                <textarea id="broadcastMessage" rows="5" placeholder="أدخل نص الرسالة..."></textarea>
            </div>
            <div class="modal-actions">
                <button class="btn btn-success" onclick="confirmBroadcast()">✅ إرسال</button>
                <button class="btn btn-danger" onclick="closeModal('broadcastModal')">❌ إلغاء</button>
            </div>
        </div>
    </div>

    <!-- Modal تمديد اشتراك -->
    <div id="extendModal" class="modal">
        <div class="modal-content">
            <h2>⏱️ تمديد اشتراك</h2>
            <div class="form-group">
                <label>معرف المستخدم</label>
                <input type="text" id="extendUserId" readonly>
            </div>
            <div class="form-group">
                <label>المدة الإضافية (بالثواني)</label>
                <input type="number" id="extendSeconds" value="86400" min="1">
            </div>
            <div class="form-group">
                <label>مدة مكافئة</label>
                <select onchange="document.getElementById('extendSeconds').value = this.value">
                    <option value="3600">ساعة واحدة (3600 ثانية)</option>
                    <option value="86400" selected>يوم واحد (86400 ثانية)</option>
                    <option value="604800">أسبوع (604800 ثانية)</option>
                    <option value="2592000">شهر (2592000 ثانية)</option>
                    <option value="31536000">سنة (31536000 ثانية)</option>
                </select>
            </div>
            <div class="modal-actions">
                <button class="btn btn-success" onclick="confirmExtend()">✅ تمديد</button>
                <button class="btn btn-danger" onclick="closeModal('extendModal')">❌ إلغاء</button>
            </div>
        </div>
    </div>

    <!-- Modal حظر مستخدم -->
    <div id="blockModal" class="modal">
        <div class="modal-content">
            <h2>🚫 حظر مستخدم</h2>
            <div class="form-group">
                <label>معرف المستخدم</label>
                <input type="text" id="blockUserId" readonly>
            </div>
            <div class="form-group">
                <label>سبب الحظر</label>
                <input type="text" id="blockReason" placeholder="سبب الحظر...">
            </div>
            <div class="modal-actions">
                <button class="btn btn-warning" onclick="confirmBlock()">🚫 حظر</button>
                <button class="btn btn-danger" onclick="closeModal('blockModal')">❌ إلغاء</button>
            </div>
        </div>
    </div>

    <!-- Modal إشعار -->
    <div id="notification" class="notification"></div>

    <div class="footer">
        <p>🤯 Obeida Trading 🤯 - نظام إدارة الاشتراكات المتطور V2</p>
        <p style="font-size: 0.8em;">نسخة البيانات: {{ data_version }} | آخر تحديث: {{ now }}</p>
    </div>

    <script>
        let currentUserId = '';

        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            document.querySelector(`.tab:nth-child(${tabName === 'users' ? 1 : tabName === 'subscriptions' ? 2 : tabName === 'stats' ? 3 : 4})`).classList.add('active');
            document.getElementById(tabName + '-tab').classList.add('active');
        }

        function searchUsers() {
            let input = document.getElementById('searchUsers');
            let filter = input.value.toLowerCase();
            let table = document.getElementById('usersTable');
            let tr = table.getElementsByTagName('tr');

            for (let i = 1; i < tr.length; i++) {
                let tdId = tr[i].getElementsByTagName('td')[0];
                let tdName = tr[i].getElementsByTagName('td')[1];
                if (tdId || tdName) {
                    let txtId = tdId.textContent || tdId.innerText;
                    let txtName = tdName.textContent || tdName.innerText;
                    if (txtId.toLowerCase().indexOf(filter) > -1 || txtName.toLowerCase().indexOf(filter) > -1) {
                        tr[i].style.display = '';
                    } else {
                        tr[i].style.display = 'none';
                    }
                }
            }
        }

        function showModal(modalId) {
            document.getElementById(modalId).style.display = 'flex';
        }

        function closeModal(modalId) {
            document.getElementById(modalId).style.display = 'none';
        }

        function showAddSubscriptionModal() {
            showModal('addSubscriptionModal');
        }

        function showBroadcastModal() {
            showModal('broadcastModal');
        }

        function extendSubscription(userId) {
            currentUserId = userId;
            document.getElementById('extendUserId').value = userId;
            showModal('extendModal');
        }

        function blockUser(userId) {
            currentUserId = userId;
            document.getElementById('blockUserId').value = userId;
            showModal('blockModal');
        }

        function showNotification(message, type = 'success') {
            let notification = document.getElementById('notification');
            notification.textContent = message;
            notification.className = 'notification ' + type;
            notification.style.display = 'block';
            
            setTimeout(() => {
                notification.style.display = 'none';
            }, 3000);
        }

        function confirmAddSubscription() {
            let userId = document.getElementById('modalUserId').value;
            let subType = document.getElementById('modalSubscriptionType').value;
            
            if (!userId) {
                showNotification('الرجاء إدخال معرف المستخدم', 'error');
                return;
            }
            
            fetch('/admin/add_subscription_v2', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    user_id: parseInt(userId),
                    subscription_type: subType
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('✅ تم إضافة الاشتراك بنجاح');
                    closeModal('addSubscriptionModal');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => {
                showNotification('❌ حدث خطأ: ' + error, 'error');
            });
        }

        function confirmBroadcast() {
            let message = document.getElementById('broadcastMessage').value;
            
            if (!message) {
                showNotification('الرجاء إدخال نص الرسالة', 'error');
                return;
            }
            
            fetch('/admin/broadcast', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message: message })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification(`✅ تم إرسال الرسالة لـ ${data.sent} مشترك`);
                    closeModal('broadcastModal');
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => {
                showNotification('❌ حدث خطأ: ' + error, 'error');
            });
        }

        function confirmExtend() {
            let seconds = document.getElementById('extendSeconds').value;
            
            fetch('/admin/extend_subscription_v2', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    user_id: parseInt(currentUserId),
                    additional_seconds: parseInt(seconds)
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('✅ تم تمديد الاشتراك بنجاح');
                    closeModal('extendModal');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => {
                showNotification('❌ حدث خطأ: ' + error, 'error');
            });
        }

        function confirmBlock() {
            let reason = document.getElementById('blockReason').value || 'Blocked by admin';
            
            fetch('/admin/block_user_v2', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    user_id: parseInt(currentUserId),
                    reason: reason
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('✅ تم حظر المستخدم بنجاح');
                    closeModal('blockModal');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => {
                showNotification('❌ حدث خطأ: ' + error, 'error');
            });
        }

        function exportData() {
            window.location.href = '/admin/export_data';
        }

        function createBackup() {
            fetch('/admin/create_backup', {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('✅ تم إنشاء النسخة الاحتياطية بنجاح');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => {
                showNotification('❌ حدث خطأ: ' + error, 'error');
            });
        }

        function downloadBackup(filename) {
            window.location.href = '/admin/download_backup/' + filename;
        }

        function restoreBackup(filename) {
            if (confirm('هل أنت متأكد من استعادة هذه النسخة؟ سيتم استبدال البيانات الحالية.')) {
                fetch('/admin/restore_backup/' + filename, {
                    method: 'POST'
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showNotification('✅ تم استعادة النسخة بنجاح');
                        setTimeout(() => location.reload(), 1000);
                    } else {
                        showNotification('❌ ' + data.error, 'error');
                    }
                })
                .catch(error => {
                    showNotification('❌ حدث خطأ: ' + error, 'error');
                });
            }
        }

        // Close modals when clicking outside
        window.onclick = function(event) {
            let modals = ['addSubscriptionModal', 'broadcastModal', 'extendModal', 'blockModal'];
            modals.forEach(modalId => {
                let modal = document.getElementById(modalId);
                if (event.target == modal) {
                    modal.style.display = 'none';
                }
            });
        }
    </script>
</body>
</html>
"""

# دوال Flask
@app.route('/login', methods=['GET', 'POST'])
def login():
    """صفحة تسجيل الدخول"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # تشفير كلمة المرور المدخلة
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        
        if username == ADMIN_USERNAME and hashed_password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('admin_panel'))
        else:
            return render_template_string(LOGIN_PAGE_HTML, error='اسم المستخدم أو كلمة المرور غير صحيحة')
    
    return render_template_string(LOGIN_PAGE_HTML)

@app.route('/logout')
def logout():
    """تسجيل الخروج"""
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def home():
    """الصفحة الرئيسية"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 لوحة تحكم المتطورة في بوت عبيدة للتداول 🤖</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
            h1 { font-size: 3em; margin-bottom: 20px; }
            .status { background: rgba(255,255,255,0.2); padding: 20px 40px; border-radius: 10px; display: inline-block; margin: 20px; }
            .admin-link { background: #28a745; color: white; padding: 15px 30px; border-radius: 5px; text-decoration: none; display: inline-block; margin: 20px; }
        </style>
    </head>
    <body>
        <h1> 📊 Obeida Trading - Subscription System 📊</h1>
        <p>Chat & Technical Analysis Bot with Advanced Subscription System</p>
        <div class="status">✅ جميع الأنظمة قيد التشغيل ✅</div>
        <p>Last Ping: """ + time.strftime("%Y-%m-%d %H:%M:%S") + """</p>
        <a href="/admin" class="admin-link">🔐 انقر هنا لدخول الى لوحة التحكم 🔐</a>
    </body>
    </html>
    """

@app.route('/health')
def health():
    """فحص صحة النظام"""
    stats = SubscriptionSystem.get_stats()
    return {
        "status": "active", 
        "ai_provider": "Mistral AI", 
        "subscription_system": "advanced_v2",
        "timezone_support": True,
        "data_version": DATA_VERSION,
        "total_users": stats.get('total_users', 0),
        "active_users": stats.get('active_users', 0),
        "blocked_users": stats.get('blocked_users', 0),
        "timestamp": time.time()
    }

@app.route('/admin')
@login_required
def admin_panel():
    """لوحة تحكم المشرف المحسنة مع نظام المصادقة"""
    try:
        stats = SubscriptionSystem.get_stats()
        users = SubscriptionSystem.get_all_users()
        
        # حساب إحصائيات إضافية
        total_usage = sum(stats.get('feature_usage', {}).values())
        stats['total_usage'] = total_usage
        stats['new_users_today'] = 0  # يمكن حسابها من قاعدة البيانات
        
        # تنسيق بيانات المستخدمين
        formatted_users = []
        for u in users:
            uid, username, first_name, created, last_active, is_blocked, sub_type, sub_name, start_date, end_date, is_active = u
            
            # تحديد الحالة
            if is_blocked == 1:
                status = "محظور"
            elif is_active == 1 and (not end_date or datetime.fromisoformat(end_date) > datetime.now()):
                status = "نشط"
            else:
                status = "منتهي"
            
            # اسم العرض
            display_name = first_name or username or str(uid)
            
            # تنسيق تاريخ الانتهاء
            end_display = end_date[:16] if end_date else "غير محدد"
            if end_date and datetime.fromisoformat(end_date) < datetime.now():
                end_display += " (منتهي)"
            
            # الحصول على المنطقة الزمنية للمستخدم
            timezone = get_user_timezone(uid)
            
            formatted_users.append({
                'id': uid,
                'name': display_name,
                'subscription_type': sub_name or 'غير مشترك',
                'end_date': end_display,
                'status': status,
                'last_active': last_active[:16] if last_active else '-',
                'timezone': timezone
            })
        
        # معلومات النسخ الاحتياطي
        backups = []
        if os.path.exists(BACKUP_DIR):
            files = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')], reverse=True)
            for f in files[:10]:  # آخر 10 نسخ
                filepath = os.path.join(BACKUP_DIR, f)
                size = os.path.getsize(filepath) / 1024  # KB
                mod_time = os.path.getmtime(filepath)
                backups.append({
                    'name': f,
                    'date': datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M'),
                    'size': f'{size:.1f} KB'
                })
        
        return render_template_string(
            ADMIN_PANEL_HTML,
            stats=stats,
            users=formatted_users,
            backups=backups,
            data_version=DATA_VERSION,
            now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
    except Exception as e:
        return f"خطأ في تحميل لوحة التحكم: {str(e)}"

@app.route('/admin/add_subscription_v2', methods=['POST'])
@login_required
def add_subscription_v2():
    """إضافة اشتراك لمستخدم"""
    try:
        data = request.json
        user_id = data.get('user_id')
        subscription_type = data.get('subscription_type', 'monthly')
        
        success, result = SubscriptionSystem.add_subscription(
            user_id=user_id,
            subscription_type=subscription_type,
            added_by=ADMIN_ID,
            reason="Added via web panel"
        )
        
        if success:
            DataProtection.backup_database()
            return jsonify({'success': True, 'subscription': result})
        else:
            return jsonify({'success': False, 'error': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/revoke_subscription_v2', methods=['POST'])
@login_required
def revoke_subscription_v2():
    """إلغاء اشتراك مستخدم"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        success, message = SubscriptionSystem.revoke_subscription(user_id, performed_by=ADMIN_ID)
        
        if success:
            DataProtection.backup_database()
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/extend_subscription_v2', methods=['POST'])
@login_required
def extend_subscription_v2():
    """تمديد اشتراك مستخدم"""
    try:
        data = request.json
        user_id = data.get('user_id')
        additional_seconds = data.get('additional_seconds', 86400)
        
        success, message = SubscriptionSystem.extend_subscription(
            user_id, 
            additional_seconds, 
            performed_by=ADMIN_ID
        )
        
        if success:
            DataProtection.backup_database()
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/block_user_v2', methods=['POST'])
@login_required
def block_user_v2():
    """حظر مستخدم"""
    try:
        data = request.json
        user_id = data.get('user_id')
        reason = data.get('reason', 'Blocked by admin')
        
        success, message = SubscriptionSystem.block_user(user_id, reason, performed_by=ADMIN_ID)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/unblock_user_v2', methods=['POST'])
@login_required
def unblock_user_v2():
    """إلغاء حظر مستخدم"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        success, message = SubscriptionSystem.unblock_user(user_id, performed_by=ADMIN_ID)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/broadcast', methods=['POST'])
@login_required
def broadcast():
    """إذاعة رسالة للمشتركين"""
    try:
        data = request.json
        message = data.get('message')
        
        if not message:
            return jsonify({'success': False, 'error': 'الرسالة مطلوبة'})
        
        subscribers = SubscriptionSystem.get_all_subscribers()
        sent_count = 0
        
        # هنا يمكن إضافة منطق الإذاعة عبر البوت
        # سيتم تنفيذه بشكل غير متزامن
        
        return jsonify({'success': True, 'sent': sent_count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/create_backup', methods=['POST'])
@login_required
def create_backup():
    """إنشاء نسخة احتياطية"""
    try:
        DataProtection.backup_database()
        DataProtection.backup_codes_db()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/download_backup/<filename>')
@login_required
def download_backup(filename):
    """تحميل نسخة احتياطية"""
    try:
        filepath = os.path.join(BACKUP_DIR, filename)
        if os.path.exists(filepath):
            return send_file(filepath, as_attachment=True)
        return jsonify({'success': False, 'error': 'الملف غير موجود'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/restore_backup/<filename>', methods=['POST'])
@login_required
def restore_backup(filename):
    """استعادة نسخة احتياطية"""
    try:
        filepath = os.path.join(BACKUP_DIR, filename)
        if not os.path.exists(filepath):
            return jsonify({'success': False, 'error': 'الملف غير موجود'})
        
        # استعادة قاعدة البيانات الرئيسية
        if 'main_backup' in filename:
            shutil.copy2(filepath, DB_NAME)
        # استعادة قاعدة بيانات الاشتراك
        elif 'subscription_backup' in filename:
            shutil.copy2(filepath, SUBSCRIPTION_DB_NAME)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/export_data')
@login_required
def export_data():
    """تصدير البيانات كملف JSON"""
    try:
        users = SubscriptionSystem.get_all_users()
        stats = SubscriptionSystem.get_stats()
        
        data = {
            'export_date': datetime.now().isoformat(),
            'data_version': DATA_VERSION,
            'stats': stats,
            'users': users
        }
        
        # إنشاء ملف JSON للتحميل
        json_str = json.dumps(data, indent=2, default=str)
        
        from flask import Response
        return Response(
            json_str,
            mimetype='application/json',
            headers={'Content-Disposition': 'attachment;filename=obeida_export.json'}
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/get_stats_v2')
@login_required
def get_stats_v2():
    """الحصول على الإحصائيات"""
    stats = SubscriptionSystem.get_stats()
    return jsonify({'success': True, 'stats': stats})

@app.route('/ping')
def ping():
    return "PONG"

def main():
    """الدالة الرئيسية - النسخة النهائية مع نظام المناطق الزمنية ولوحة تحكم محمية"""
    print("🤖 Starting Obeida Trading with Timezone Support and Secure Admin Panel ...")
    
    # تهيئة قاعدة البيانات الرئيسية
    init_db_with_subscription()
    
    # إنشاء نسخة احتياطية أولية
    DataProtection.backup_database()
    DataProtection.backup_codes_db()
    
    # تشغيل Flask مع نظام المصادقة
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    # تنظيف جميع الملفات المؤقتة عند بدء التشغيل
    print("🧹 جاري تنظيف الملفات المؤقتة عند بدء التشغيل...")
    try:
        if os.path.exists(IMAGE_CACHE_DIR):
            files = os.listdir(IMAGE_CACHE_DIR)
            deleted_count = 0
            for filename in files:
                filepath = os.path.join(IMAGE_CACHE_DIR, filename)
                if os.path.isfile(filepath):
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                    except Exception as e:
                        print(f"⚠️ فشل حذف {filename}: {e}")
            print(f"✅ تم حذف {deleted_count} ملف مؤقت عند بدء التشغيل")
        else:
            os.makedirs(IMAGE_CACHE_DIR)
            print(f"📁 تم إنشاء مجلد الصور: {IMAGE_CACHE_DIR}")
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف الملفات: {e}")
    
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
            ADMIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)
            ],
            ADD_SUBSCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_subscription)
            ],
            SELECT_DURATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_select_duration)
            ],
            REMOVE_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_user)
            ],
            SET_USER_DURATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_duration)
            ],
            BROADCAST_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)
            ],
            COUNTRY_SELECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_country_selection)
            ]
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    
    # إضافة معالج للأخطاء
    application.add_error_handler(error_handler)
    
    # تشغيل المهام الدورية
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(periodic_cleanup())
    loop.create_task(backup_scheduler())
    
    print("✅ Telegram Bot with Timezone Support and Secure Admin Panel ready ...")
    print(f"🌍 Admin Panel URL: http://localhost:8080/admin")
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
    
    
