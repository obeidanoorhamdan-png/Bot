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
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.error import NetworkError, TimedOut
from flask import Flask, request, jsonify, render_template_string
from PIL import Image
import pytz

# --- الإعدادات الأساسية ---
TOKEN = os.environ.get('TOKEN', "8583903565:AAFveu3KYGkPsC8L6WyermoaZ1tI_EtFHHs")

MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "TuXbJy2wpmUG0qzO06MmwpSgy7isTr9A")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "pixtral-large-latest"          # للرؤية والاستخراج البصري
MISTRAL_MODEL_AUDIT = "mistral-large-latest"        # للتحليل المنطقي
MODEL_SUMMARY = "mistral-medium-latest"        # للتلخيص السريع

DB_NAME = "abood-gpt.db"
BACKUP_DIR = "backups"
CODES_DB_NAME = "subscription_codes.db"  # قاعدة بيانات الأكواد الجديدة
DATA_VERSION = "1.0.0"  # نسخة البيانات للحماية من التغييرات

# معرف المشرف الرئيسي
ADMIN_ID = 6207431030

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

# حالات المحادثة
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION, ADMIN_MENU, ADD_USER, REMOVE_USER, SET_USER_DURATION, GENERATE_CODE, ACTIVATE_CODE = range(13)

# إنشاء المجلدات الضرورية
for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"📁 تم إنشاء المجلد: {directory}")

# دالة للتحقق من الأكواد الموجودة
def debug_check_codes():
    """دالة للتحقق من الأكواد الموجودة في قاعدة البيانات"""
    try:
        if not os.path.exists(CODES_DB_NAME):
            print(f"❌ ملف قاعدة البيانات {CODES_DB_NAME} غير موجود")
            return
            
        conn = sqlite3.connect(CODES_DB_NAME)
        cursor = conn.cursor()
        
        # عرض جميع الأكواد
        cursor.execute("SELECT code, duration, created_at, used_by, status FROM subscription_codes")
        codes = cursor.fetchall()
        
        print("\n🔍 الأكواد الموجودة في قاعدة البيانات:")
        print("-" * 50)
        if codes:
            for code in codes:
                print(f"كود: {code[0]}")
                print(f"مدة: {code[1]} ثانية")
                print(f"تاريخ الإنشاء: {code[2]}")
                print(f"مستخدم بواسطة: {code[3] if code[3] else 'لم يستخدم بعد'}")
                print(f"الحالة: {code[4]}")
                print("-" * 30)
        else:
            print("❌ لا توجد أكواد في قاعدة البيانات")
            
        conn.close()
    except Exception as e:
        print(f"❌ خطأ في التحقق من الأكواد: {e}")

# --- نظام توليد الأكواد العشوائية والمتطورة (نسخة مصححة) ---
class CodeGenerator:
    """نظام توليد وإدارة أكواد الاشتراك"""
    
    @staticmethod
    def generate_code():
        """توليد كود عشوائي يبدأ بـ Obeida- و 6 أحرف/أرقام عشوائية"""
        random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        return f"Obeida-{random_part}"
    
    @staticmethod
    def init_codes_db():
        """تهيئة قاعدة بيانات الأكواد"""
        conn = sqlite3.connect(CODES_DB_NAME)
        cursor = conn.cursor()
        
        # جدول الأكواد
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscription_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                duration INTEGER NOT NULL,
                created_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                used_by INTEGER DEFAULT NULL,
                used_at DATETIME DEFAULT NULL,
                is_active INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active'
            )
        ''')
        
        # جدول ربط المستخدمين بالأكواد
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                activated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                subscription_start DATETIME DEFAULT CURRENT_TIMESTAMP,
                subscription_end DATETIME,
                is_active INTEGER DEFAULT 1,
                UNIQUE(user_id, code)
            )
        ''')
        
        # جدول سجل الأكواد
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS code_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT,
                code TEXT,
                user_id INTEGER,
                details TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ تم تهيئة قاعدة بيانات أكواد الاشتراك")
    
    @staticmethod
    def create_code(duration, created_by=ADMIN_ID):
        """إنشاء كود جديد - نسخة نهائية"""
        conn = None
        try:
            # التأكد من وجود ملف قاعدة البيانات
            if not os.path.exists(CODES_DB_NAME):
                print(f"📦 إنشاء ملف قاعدة البيانات {CODES_DB_NAME}")
                CodeGenerator.init_codes_db()
            
            code = CodeGenerator.generate_code()
            print(f"📝 محاولة إنشاء كود: {code}")
            
            conn = sqlite3.connect(CODES_DB_NAME)
            cursor = conn.cursor()
            
            # التأكد من أن الكود غير موجود
            cursor.execute("SELECT code FROM subscription_codes WHERE code = ?", (code,))
            if cursor.fetchone():
                print(f"⚠️ الكود {code} موجود مسبقاً، جاري توليد كود جديد")
                code = CodeGenerator.generate_code()
            
            # إدراج الكود
            cursor.execute('''
                INSERT INTO subscription_codes (code, duration, created_by, status, is_active)
                VALUES (?, ?, ?, 'active', 1)
            ''', (code, duration, created_by))
            
            conn.commit()
            
            # التحقق من الحفظ
            cursor.execute("SELECT code, status FROM subscription_codes WHERE code = ?", (code,))
            saved = cursor.fetchone()
            
            if saved:
                print(f"✅ تم حفظ الكود {code} بنجاح في قاعدة البيانات")
                
                # تسجيل العملية
                cursor.execute('''
                    INSERT INTO code_log (action, code, user_id, details)
                    VALUES (?, ?, ?, ?)
                ''', ('create', code, created_by, f'Duration: {duration}'))
                
                conn.commit()
                
                # عرض جميع الأكواد للتأكد
                cursor.execute("SELECT code FROM subscription_codes")
                all_codes = cursor.fetchall()
                print(f"📋 الأكواد الموجودة حالياً: {[c[0] for c in all_codes]}")
                
                return code
            else:
                print(f"❌ فشل في حفظ الكود {code}")
                return None
                
        except sqlite3.IntegrityError as e:
            print(f"❌ خطأ في تكامل قاعدة البيانات: {e}")
            if conn:
                conn.rollback()
            return CodeGenerator.create_code(duration, created_by)
        except Exception as e:
            print(f"❌ خطأ في إنشاء الكود: {e}")
            if conn:
                conn.rollback()
            return None
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def validate_code(code):
        """التحقق من صحة الكود - نسخة محسنة"""
        conn = None
        try:
            conn = sqlite3.connect(CODES_DB_NAME)
            cursor = conn.cursor()
            
            # التحقق من وجود الكود وصلاحيته
            cursor.execute('''
                SELECT code, duration, status, used_by, is_active 
                FROM subscription_codes 
                WHERE code = ? AND is_active = 1 AND status = 'active' AND used_by IS NULL
            ''', (code,))
            
            result = cursor.fetchone()
            
            if result:
                print(f"✅ الكود {code} صالح ومتاح للاستخدام")
                return True, result[1]  # المدة بالثواني
            else:
                # التحقق من سبب عدم الصلاحية
                cursor.execute('''
                    SELECT status, used_by, is_active FROM subscription_codes WHERE code = ?
                ''', (code,))
                
                existing = cursor.fetchone()
                if existing:
                    status, used_by, is_active = existing
                    if used_by:
                        print(f"❌ الكود {code} مستخدم بالفعل بواسطة {used_by}")
                    elif not is_active:
                        print(f"❌ الكود {code} غير نشط")
                    elif status != 'active':
                        print(f"❌ الكود {code} حالته: {status}")
                else:
                    print(f"❌ الكود {code} غير موجود في قاعدة البيانات")
                
                return False, None
                
        except Exception as e:
            print(f"❌ خطأ في التحقق من الكود: {e}")
            return False, None
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def activate_code(code, user_id):
        """تفعيل الكود لمستخدم - نسخة محسنة"""
        conn = None
        try:
            conn = sqlite3.connect(CODES_DB_NAME)
            cursor = conn.cursor()
            
            # بدء transaction
            cursor.execute("BEGIN TRANSACTION")
            
            # التحقق من صلاحية الكود
            cursor.execute('''
                SELECT duration, status, is_active FROM subscription_codes 
                WHERE code = ? AND is_active = 1 AND status = 'active' AND used_by IS NULL
            ''', (code,))
            
            result = cursor.fetchone()
            if not result:
                conn.rollback()
                return False, "الكود غير صالح أو مستخدم بالفعل"
            
            duration, status, is_active = result
            
            # حساب تاريخ الانتهاء
            end_date = None
            if duration > 0:
                end_date = (datetime.now() + timedelta(seconds=duration)).isoformat()
            
            # تحديث الكود
            cursor.execute('''
                UPDATE subscription_codes 
                SET used_by = ?, used_at = CURRENT_TIMESTAMP, is_active = 0, status = 'used'
                WHERE code = ?
            ''', (user_id, code))
            
            if cursor.rowcount == 0:
                conn.rollback()
                return False, "فشل في تحديث الكود"
            
            # ربط المستخدم بالكود
            cursor.execute('''
                INSERT INTO user_subscriptions (user_id, code, subscription_start, subscription_end, is_active)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?, 1)
            ''', (user_id, code, end_date))
            
            # تسجيل العملية
            cursor.execute('''
                INSERT INTO code_log (action, code, user_id, details)
                VALUES (?, ?, ?, ?)
            ''', ('activate', code, user_id, f'Duration: {duration}, End: {end_date}'))
            
            conn.commit()
            print(f"✅ تم تفعيل الكود {code} للمستخدم {user_id} بنجاح")
            return True, end_date
            
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"❌ خطأ في تفعيل الكود: {e}")
            return False, str(e)
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def deactivate_code(code, admin_id=ADMIN_ID):
        """إلغاء تفعيل كود (إيقاف كود غير مستخدم)"""
        conn = None
        try:
            conn = sqlite3.connect(CODES_DB_NAME)
            cursor = conn.cursor()
            
            cursor.execute("BEGIN TRANSACTION")
            
            cursor.execute('''
                UPDATE subscription_codes 
                SET is_active = 0, status = 'cancelled'
                WHERE code = ? AND used_by IS NULL
            ''', (code,))
            
            if cursor.rowcount > 0:
                cursor.execute('''
                    INSERT INTO code_log (action, code, user_id, details)
                    VALUES (?, ?, ?, ?)
                ''', ('deactivate', code, admin_id, 'Code cancelled by admin'))
                
                conn.commit()
                print(f"✅ تم إلغاء الكود {code}")
                return True
            else:
                conn.rollback()
                return False
                
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"❌ خطأ في إلغاء الكود: {e}")
            return False
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def revoke_user_subscription(user_id, admin_id=ADMIN_ID):
        """إلغاء اشتراك مستخدم (إيقاف الكود المستخدم)"""
        conn = None
        try:
            conn = sqlite3.connect(CODES_DB_NAME)
            cursor = conn.cursor()
            
            cursor.execute("BEGIN TRANSACTION")
            
            # الحصول على الكود النشط للمستخدم
            cursor.execute('''
                SELECT code FROM user_subscriptions 
                WHERE user_id = ? AND is_active = 1
            ''', (user_id,))
            
            result = cursor.fetchone()
            if not result:
                conn.rollback()
                return False, "لا يوجد اشتراك نشط لهذا المستخدم"
            
            code = result[0]
            
            # إلغاء الاشتراك
            cursor.execute('''
                UPDATE user_subscriptions 
                SET is_active = 0
                WHERE user_id = ? AND is_active = 1
            ''', (user_id,))
            
            # تحديث حالة الكود
            cursor.execute('''
                UPDATE subscription_codes 
                SET status = 'revoked'
                WHERE code = ?
            ''', (code,))
            
            # تسجيل العملية
            cursor.execute('''
                INSERT INTO code_log (action, code, user_id, details)
                VALUES (?, ?, ?, ?)
            ''', ('revoke', code, admin_id, f'Subscription revoked for user {user_id}'))
            
            conn.commit()
            
            # تحديث قاعدة البيانات الرئيسية
            update_user_subscription_from_db(user_id, None)
            
            print(f"✅ تم إلغاء اشتراك المستخدم {user_id}")
            return True, "تم إلغاء الاشتراك بنجاح"
            
        except Exception as e:
            if conn:
                conn.rollback()
            print(f"❌ خطأ في إلغاء الاشتراك: {e}")
            return False, str(e)
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def get_user_subscription_info(user_id):
        """الحصول على معلومات اشتراك المستخدم من الأكواد"""
        conn = None
        try:
            conn = sqlite3.connect(CODES_DB_NAME)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT code, subscription_start, subscription_end, is_active
                FROM user_subscriptions
                WHERE user_id = ? AND is_active = 1
                ORDER BY activated_at DESC LIMIT 1
            ''', (user_id,))
            
            result = cursor.fetchone()
            
            if result:
                return {
                    'code': result[0],
                    'subscription_start': result[1],
                    'subscription_end': result[2],
                    'is_active': result[3]
                }
            return None
            
        except Exception as e:
            print(f"❌ خطأ في الحصول على معلومات الاشتراك: {e}")
            return None
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def check_user_active_subscription(user_id):
        """التحقق من وجود اشتراك نشط للمستخدم"""
        info = CodeGenerator.get_user_subscription_info(user_id)
        if not info:
            return False, "لا يوجد اشتراك"
        
        if not info['is_active']:
            return False, "الاشتراك غير نشط"
        
        if info['subscription_end']:
            try:
                end_date = datetime.fromisoformat(info['subscription_end'])
                if end_date <= datetime.now():
                    return False, "انتهت صلاحية الاشتراك"
            except:
                pass
        
        return True, "اشتراك نشط"
    
    @staticmethod
    def list_all_codes():
        """عرض جميع الأكواد"""
        conn = None
        try:
            conn = sqlite3.connect(CODES_DB_NAME)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT code, duration, created_at, used_by, used_at, status, is_active
                FROM subscription_codes
                ORDER BY created_at DESC
            ''')
            
            codes = cursor.fetchall()
            return codes
            
        except Exception as e:
            print(f"❌ خطأ في عرض الأكواد: {e}")
            return []
        finally:
            if conn:
                conn.close()
    
    @staticmethod
    def get_code_details(code):
        """الحصول على تفاصيل كود محدد"""
        conn = None
        try:
            conn = sqlite3.connect(CODES_DB_NAME)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT sc.*, us.user_id, us.activated_at, us.subscription_end
                FROM subscription_codes sc
                LEFT JOIN user_subscriptions us ON sc.code = us.code
                WHERE sc.code = ?
            ''', (code,))
            
            result = cursor.fetchone()
            return result
            
        except Exception as e:
            print(f"❌ خطأ في الحصول على تفاصيل الكود: {e}")
            return None
        finally:
            if conn:
                conn.close()

# دالة مساعدة لتحديث قاعدة البيانات الرئيسية
def update_user_subscription_from_db(user_id, end_date):
    """تحديث اشتراك المستخدم في قاعدة البيانات الرئيسية"""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE users 
            SET subscription_end = ?
            WHERE user_id = ?
        ''', (end_date, user_id))
        
        conn.commit()
        print(f"✅ تم تحديث قاعدة البيانات الرئيسية للمستخدم {user_id}")
    except Exception as e:
        print(f"❌ خطأ في تحديث قاعدة البيانات: {e}")
    finally:
        if conn:
            conn.close()

# دالة تحديث اشتراك المستخدم من الكود
def update_user_subscription_from_code(user_id, code, end_date):
    """تحديث اشتراك المستخدم في قاعدة البيانات الرئيسية"""
    conn = None
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # الحصول على اسم المستخدم
        username = None
        try:
            cursor.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if result:
                username = result[0]
        except:
            pass
        
        # التحقق من وجود المستخدم
        cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if cursor.fetchone():
            # تحديث المستخدم الموجود
            cursor.execute("""
                UPDATE users 
                SET subscription_start = COALESCE(subscription_start, datetime('now')),
                    subscription_end = ?,
                    username = COALESCE(?, username)
                WHERE user_id = ?
            """, (end_date, username, user_id))
        else:
            # إضافة مستخدم جديد
            cursor.execute("""
                INSERT INTO users (user_id, username, subscription_start, subscription_end, candle, trade_time)
                VALUES (?, ?, datetime('now'), ?, ?, ?)
            """, (user_id, username, end_date, "M1", "قصير (1m-15m)"))
        
        conn.commit()
        
        log_data_change("activate_code", user_id, f"code={code}, end_date={end_date}")
        print(f"✅ تم تحديث اشتراك المستخدم {user_id} بنجاح حتى {end_date}")
    except Exception as e:
        print(f"❌ خطأ في تحديث اشتراك المستخدم: {e}")
    finally:
        if conn:
            conn.close()

# --- نظام النسخ الاحتياطي وحماية البيانات ---
class DataProtection:
    """نظام حماية البيانات من الفقدان والتغيير"""
    
    @staticmethod
    def backup_database():
        """إنشاء نسخة احتياطية من قاعدة البيانات"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(BACKUP_DIR, f"backup_{timestamp}.db")
            
            # نسخ الملف
            if os.path.exists(DB_NAME):
                shutil.copy2(DB_NAME, backup_file)
                
                # إنشاء ملف معلومات مع Hash للتحقق
                info_file = backup_file.replace('.db', '_info.json')
                with open(DB_NAME, 'rb') as f:
                    file_hash = hashlib.md5(f.read()).hexdigest()
                
                info = {
                    'timestamp': timestamp,
                    'version': DATA_VERSION,
                    'file_hash': file_hash,
                    'size': os.path.getsize(DB_NAME)
                }
                
                with open(info_file, 'w', encoding='utf-8') as f:
                    json.dump(info, f, ensure_ascii=False, indent=2)
                
                # تنظيف النسخ القديمة (الاحتفاظ بآخر 50 نسخة)
                backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')])
                for old_backup in backups[:-50]:
                    old_path = os.path.join(BACKUP_DIR, old_backup)
                    old_info = old_path.replace('.db', '_info.json')
                    try:
                        os.remove(old_path)
                        if os.path.exists(old_info):
                            os.remove(old_info)
                    except:
                        pass
                
                print(f"💾 تم إنشاء نسخة احتياطية: {backup_file}")
                return backup_file
        except Exception as e:
            print(f"⚠️ خطأ في إنشاء النسخة الاحتياطية: {e}")
        return None
    
    @staticmethod
    def backup_codes_db():
        """نسخ احتياطي لقاعدة بيانات الأكواد"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(BACKUP_DIR, f"codes_backup_{timestamp}.db")
            
            if os.path.exists(CODES_DB_NAME):
                shutil.copy2(CODES_DB_NAME, backup_file)
                print(f"💾 تم إنشاء نسخة احتياطية للأكواد: {backup_file}")
                return backup_file
        except Exception as e:
            print(f"⚠️ خطأ في نسخ الأكواد: {e}")
        return None
    
    @staticmethod
    def verify_database_integrity():
        """التحقق من سلامة قاعدة البيانات"""
        try:
            if not os.path.exists(DB_NAME):
                return False, "قاعدة البيانات غير موجودة"
            
            # التحقق من إمكانية فتح قاعدة البيانات
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            
            # التحقق من وجود الجداول المطلوبة
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            
            required_tables = ['users', 'chat_history', 'data_log']
            missing_tables = [t for t in required_tables if t not in tables]
            
            if missing_tables:
                conn.close()
                return False, f"الجداول المفقودة: {missing_tables}"
            
            # التحقق من بنية جدول users
            cursor.execute("PRAGMA table_info(users)")
            columns = [col[1] for col in cursor.fetchall()]
            
            required_columns = ['user_id', 'username', 'subscription_start', 'subscription_end', 'is_admin', 'candle', 'trade_time', 'chat_context', 'last_analysis_context', 'last_analysis_time']
            missing_columns = [c for c in required_columns if c not in columns]
            
            if missing_columns:
                conn.close()
                return False, f"الأعمدة المفقودة في جدول users: {missing_columns}"
            
            conn.close()
            return True, "قاعدة البيانات سليمة"
            
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def restore_latest_backup():
        """استعادة آخر نسخة احتياطية"""
        try:
            backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db') and not f.startswith('codes_')])
            if not backups:
                return False, "لا توجد نسخ احتياطية"
            
            latest_backup = os.path.join(BACKUP_DIR, backups[-1])
            latest_info = latest_backup.replace('.db', '_info.json')
            
            # التحقق من صحة النسخة
            if os.path.exists(latest_info):
                with open(latest_info, 'r', encoding='utf-8') as f:
                    info = json.load(f)
                
                # التحقق من Hash
                with open(latest_backup, 'rb') as f:
                    file_hash = hashlib.md5(f.read()).hexdigest()
                
                if file_hash != info.get('file_hash'):
                    return False, "النسخة الاحتياطية تالفة (عدم تطابق Hash)"
                
                # استعادة النسخة
                shutil.copy2(latest_backup, DB_NAME)
                print(f"🔄 تم استعادة قاعدة البيانات من: {latest_backup}")
                return True, f"تم الاستعادة من نسخة {info.get('timestamp')}"
            else:
                # استعادة بدون تحقق
                shutil.copy2(latest_backup, DB_NAME)
                print(f"🔄 تم استعادة قاعدة البيانات من: {latest_backup} (بدون تحقق)")
                return True, "تم الاستعادة (بدون تحقق)"
                
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def auto_backup():
        """نسخ احتياطي تلقائي - يتم استدعاؤها كل ساعة"""
        try:
            # التحقق من سلامة قاعدة البيانات أولاً
            is_valid, message = DataProtection.verify_database_integrity()
            if is_valid:
                DataProtection.backup_database()
                DataProtection.backup_codes_db()
            else:
                print(f"⚠️ قاعدة البيانات غير سليمة: {message}")
                # محاولة استعادة آخر نسخة
                success, msg = DataProtection.restore_latest_backup()
                if success:
                    print(f"✅ تم استعادة قاعدة البيانات: {msg}")
                else:
                    print(f"❌ فشل استعادة قاعدة البيانات: {msg}")
        except Exception as e:
            print(f"⚠️ خطأ في النسخ الاحتياطي التلقائي: {e}")

# --- Flask Server مع واجهة تحكم ---
app = Flask(__name__)

# HTML template for admin panel (محدث مع نظام الأكواد)
ADMIN_PANEL_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>لوحة تحكم Obeida Trading</title>
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
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            background: white;
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
            text-align: center;
        }
        .header h1 {
            color: #333;
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        .header p {
            color: #666;
            font-size: 1.1em;
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
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        .tab {
            background: white;
            padding: 15px 30px;
            border-radius: 10px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.3s;
            border: 2px solid transparent;
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
        .users-section, .codes-section {
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
        .users-table, .codes-table {
            overflow-x: auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        th {
            background: #667eea;
            color: white;
            padding: 12px;
            font-weight: 600;
            text-align: center;
        }
        td {
            padding: 12px;
            border-bottom: 1px solid #f0f0f0;
            text-align: center;
        }
        tr:hover {
            background: #f8f9ff;
        }
        .status-badge {
            padding: 5px 10px;
            border-radius: 20px;
            font-size: 0.9em;
            font-weight: 600;
            display: inline-block;
        }
        .status-active {
            background: #d4edda;
            color: #155724;
        }
        .status-expired {
            background: #f8d7da;
            color: #721c24;
        }
        .status-never {
            background: #cce5ff;
            color: #004085;
        }
        .status-available {
            background: #d4edda;
            color: #155724;
        }
        .status-used {
            background: #f8d7da;
            color: #721c24;
        }
        .status-inactive {
            background: #e9ecef;
            color: #495057;
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
        .btn-secondary {
            background: #6c757d;
            color: white;
        }
        .btn-secondary:hover {
            background: #5a6268;
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
            max-width: 500px;
        }
        .modal-content h3 {
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
        .form-group input, .form-group select {
            width: 100%;
            padding: 10px;
            border: 2px solid #f0f0f0;
            border-radius: 10px;
            font-size: 1em;
        }
        .form-group input:focus, .form-group select:focus {
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
        .search-box input:focus {
            outline: none;
            border-color: #667eea;
        }
        .generated-code {
            background: #e8f5e9;
            border: 2px solid #28a745;
            border-radius: 10px;
            padding: 20px;
            text-align: center;
            margin: 20px 0;
        }
        .generated-code .code {
            font-size: 2em;
            font-weight: bold;
            color: #28a745;
            letter-spacing: 2px;
            font-family: monospace;
        }
        .footer {
            text-align: center;
            color: white;
            margin-top: 30px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 لوحة تحكم Obeida Trading</h1>
            <p>إدارة الأكواد والمستخدمين - المشرف: @Sz2zv</p>
            <p style="color: #28a745; font-size: 0.9em;">✅ نظام أكواد الاشتراك نشط - جميع الأكواد محفوظة بشكل دائم</p>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>إجمالي المستخدمين</h3>
                <div class="number">{{ total_users }}</div>
            </div>
            <div class="stat-card">
                <h3>المستخدمين النشطين</h3>
                <div class="number">{{ active_users }}</div>
            </div>
            <div class="stat-card">
                <h3>الأكواد المتاحة</h3>
                <div class="number">{{ available_codes }}</div>
            </div>
            <div class="stat-card">
                <h3>الأكواد المستخدمة</h3>
                <div class="number">{{ used_codes }}</div>
            </div>
        </div>

        <div class="tabs">
            <div class="tab active" onclick="showTab('users')">👥 المستخدمين</div>
            <div class="tab" onclick="showTab('codes')">🔑 أكواد الاشتراك</div>
        </div>

        <div id="users-tab" class="tab-content active">
            <div class="users-section">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                    <h2 class="section-title">👥 قائمة المستخدمين</h2>
                    <button class="btn btn-success" onclick="showGenerateCodeModal()">➕ توليد كود جديد</button>
                </div>
                
                <div class="search-box">
                    <input type="text" id="searchUsers" placeholder="🔍 بحث عن مستخدم..." onkeyup="searchUsers()">
                </div>

                <div class="users-table">
                    <table id="usersTable">
                        <thead>
                            <tr>
                                <th>المعرف</th>
                                <th>كود التفعيل</th>
                                <th>تاريخ الاشتراك</th>
                                <th>انتهاء الاشتراك</th>
                                <th>الحالة</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for user in users %}
                            <tr>
                                <td>{{ user.user_id }}</td>
                                <td><span class="code-box">{{ user.activation_code or '—' }}</span></td>
                                <td>{{ user.subscription_date }}</td>
                                <td>
                                    {% if user.subscription_end %}
                                        {{ user.subscription_end }}
                                    {% else %}
                                        غير محدد
                                    {% endif %}
                                </td>
                                <td>
                                    {% if user.is_admin %}
                                        <span class="status-badge status-never">👑 مشرف</span>
                                    {% elif user.subscription_end %}
                                        {% if user.is_active %}
                                            <span class="status-badge status-active">✅ نشط</span>
                                        {% else %}
                                            <span class="status-badge status-expired">❌ منتهي</span>
                                        {% endif %}
                                    {% else %}
                                        <span class="status-badge status-expired">❌ غير مشترك</span>
                                    {% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div id="codes-tab" class="tab-content">
            <div class="codes-section">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                    <h2 class="section-title">🔑 أكواد الاشتراك</h2>
                    <button class="btn btn-success" onclick="showGenerateCodeModal()">➕ توليد كود جديد</button>
                </div>
                
                <div class="search-box">
                    <input type="text" id="searchCodes" placeholder="🔍 بحث عن كود..." onkeyup="searchCodes()">
                </div>

                <div class="codes-table">
                    <table id="codesTable">
                        <thead>
                            <tr>
                                <th>الكود</th>
                                <th>المدة</th>
                                <th>تاريخ الإنشاء</th>
                                <th>المستخدم</th>
                                <th>تاريخ الاستخدام</th>
                                <th>الحالة</th>
                                <th>الإجراءات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for code in codes %}
                            <tr>
                                <td><span class="code-box">{{ code.code }}</span></td>
                                <td>
                                    {% if code.duration == 0 %}
                                        غير محدد
                                    {% elif code.duration < 86400 %}
                                        {{ (code.duration/3600)|int }} ساعة
                                    {% else %}
                                        {{ (code.duration/86400)|int }} يوم
                                    {% endif %}
                                </td>
                                <td>{{ code.created_at }}</td>
                                <td>{{ code.used_by or '—' }}</td>
                                <td>{{ code.used_at or '—' }}</td>
                                <td>
                                    {% if code.is_active and not code.used_by %}
                                        <span class="status-badge status-available">✅ متاح</span>
                                    {% elif code.used_by %}
                                        <span class="status-badge status-used">✔️ مستخدم</span>
                                    {% else %}
                                        <span class="status-badge status-inactive">⭕ غير نشط</span>
                                    {% endif %}
                                </td>
                                <td>
                                    {% if code.is_active and not code.used_by %}
                                    <button class="btn btn-danger btn-sm" onclick="deactivateCode('{{ code.code }}')">إلغاء</button>
                                    {% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Modal توليد كود -->
        <div id="generateCodeModal" class="modal">
            <div class="modal-content">
                <h3>➕ توليد كود اشتراك جديد</h3>
                <div class="form-group">
                    <label>مدة الاشتراك:</label>
                    <select id="codeDuration">
                        <option value="3600">ساعة واحدة</option>
                        <option value="86400">يوم واحد</option>
                        <option value="604800">أسبوع</option>
                        <option value="2592000">شهر</option>
                        <option value="0">غير محدد</option>
                    </select>
                </div>
                <div class="modal-actions">
                    <button class="btn btn-success" onclick="generateCode()">توليد</button>
                    <button class="btn btn-secondary" onclick="hideGenerateCodeModal()">إلغاء</button>
                </div>
            </div>
        </div>

        <!-- Modal عرض الكود المولد -->
        <div id="showCodeModal" class="modal">
            <div class="modal-content">
                <h3>✅ تم توليد الكود بنجاح</h3>
                <div class="generated-code">
                    <div class="code" id="generatedCodeDisplay"></div>
                </div>
                <p style="text-align: center; color: #666;">يمكنك نسخ الكود وإرساله للمستخدم</p>
                <div class="modal-actions" style="justify-content: center;">
                    <button class="btn btn-primary" onclick="copyGeneratedCode()">📋 نسخ الكود</button>
                    <button class="btn btn-secondary" onclick="hideShowCodeModal()">إغلاق</button>
                </div>
            </div>
        </div>
    </div>

    <div class="footer">
        <p>Obeida Trading - نظام إدارة الأكواد والاشتراكات المتقدم</p>
        <p style="font-size: 0.8em;">نسخة البيانات: {{ data_version }} - آخر نسخة احتياطية: {{ last_backup }}</p>
    </div>

    <script>
        let generatedCode = '';

        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            if (tabName === 'users') {
                document.querySelector('.tab').classList.add('active');
                document.getElementById('users-tab').classList.add('active');
            } else {
                document.querySelectorAll('.tab')[1].classList.add('active');
                document.getElementById('codes-tab').classList.add('active');
            }
        }

        function searchUsers() {
            let input = document.getElementById('searchUsers');
            let filter = input.value.toLowerCase();
            let table = document.getElementById('usersTable');
            let tr = table.getElementsByTagName('tr');

            for (let i = 1; i < tr.length; i++) {
                let tdId = tr[i].getElementsByTagName('td')[0];
                let tdCode = tr[i].getElementsByTagName('td')[1];
                if (tdId || tdCode) {
                    let txtId = tdId.textContent || tdId.innerText;
                    let txtCode = tdCode.textContent || tdCode.innerText;
                    if (txtId.toLowerCase().indexOf(filter) > -1 || txtCode.toLowerCase().indexOf(filter) > -1) {
                        tr[i].style.display = '';
                    } else {
                        tr[i].style.display = 'none';
                    }
                }
            }
        }

        function searchCodes() {
            let input = document.getElementById('searchCodes');
            let filter = input.value.toLowerCase();
            let table = document.getElementById('codesTable');
            let tr = table.getElementsByTagName('tr');

            for (let i = 1; i < tr.length; i++) {
                let tdCode = tr[i].getElementsByTagName('td')[0];
                if (tdCode) {
                    let txtCode = tdCode.textContent || tdCode.innerText;
                    if (txtCode.toLowerCase().indexOf(filter) > -1) {
                        tr[i].style.display = '';
                    } else {
                        tr[i].style.display = 'none';
                    }
                }
            }
        }

        function showGenerateCodeModal() {
            document.getElementById('generateCodeModal').style.display = 'flex';
        }

        function hideGenerateCodeModal() {
            document.getElementById('generateCodeModal').style.display = 'none';
        }

        function showShowCodeModal(code) {
            generatedCode = code;
            document.getElementById('generatedCodeDisplay').innerText = code;
            document.getElementById('showCodeModal').style.display = 'flex';
            hideGenerateCodeModal();
        }

        function hideShowCodeModal() {
            document.getElementById('showCodeModal').style.display = 'none';
            location.reload();
        }

        function copyGeneratedCode() {
            navigator.clipboard.writeText(generatedCode).then(() => {
                alert('✅ تم نسخ الكود!');
            });
        }

        async function generateCode() {
            let duration = document.getElementById('codeDuration').value;
            
            try {
                let response = await fetch('/admin/generate_code', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        duration: parseInt(duration)
                    })
                });
                
                let result = await response.json();
                if (result.success) {
                    showShowCodeModal(result.code);
                } else {
                    alert('❌ ' + result.error);
                }
            } catch (error) {
                alert('حدث خطأ في الاتصال');
            }
        }

        async function deactivateCode(code) {
            if (!confirm('هل أنت متأكد من إلغاء هذا الكود؟')) {
                return;
            }
            
            try {
                let response = await fetch('/admin/deactivate_code', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        code: code
                    })
                });
                
                let result = await response.json();
                if (result.success) {
                    alert('✅ تم إلغاء الكود بنجاح');
                    location.reload();
                } else {
                    alert('❌ ' + result.error);
                }
            } catch (error) {
                alert('حدث خطأ في الاتصال');
            }
        }

        // Close modals when clicking outside
        window.onclick = function(event) {
            let generateModal = document.getElementById('generateCodeModal');
            let showModal = document.getElementById('showCodeModal');
            if (event.target == generateModal) {
                generateModal.style.display = 'none';
            }
            if (event.target == showModal) {
                showModal.style.display = 'none';
                location.reload();
            }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Obeida Trading</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
            h1 { font-size: 3em; margin-bottom: 20px; }
            .status { background: rgba(255,255,255,0.2); padding: 20px 40px; border-radius: 10px; display: inline-block; margin: 20px; }
            .admin-link { background: #28a745; color: white; padding: 15px 30px; border-radius: 5px; text-decoration: none; display: inline-block; margin: 20px; }
        </style>
    </head>
    <body>
        <h1> 📊 Obeida Trading Telegram Bot 📊</h1>
        <p>Chat & Technical Analysis Bot with Code Subscription System</p>
        <div class="status">✅ نظام أكواد الاشتراك نشط - جميع الأكواد محفوظة</div>
        <p>Last Ping: """ + time.strftime("%Y-%m-%d %H:%M:%S") + """</p>
        <a href="/admin" class="admin-link">🔐 لوحة تحكم المشرف</a>
    </body>
    </html>
    """

@app.route('/health')
def health():
    codes_conn = sqlite3.connect(CODES_DB_NAME)
    codes_cursor = codes_conn.cursor()
    codes_cursor.execute("SELECT COUNT(*) FROM subscription_codes WHERE is_active = 1 AND used_by IS NULL")
    available_codes = codes_cursor.fetchone()[0]
    codes_conn.close()
    
    return {
        "status": "active", 
        "ai_provider": "Mistral AI", 
        "subscription_system": "code-based",
        "data_protection": "active",
        "data_version": DATA_VERSION,
        "available_codes": available_codes,
        "timestamp": time.time()
    }

@app.route('/admin')
def admin_panel():
    """لوحة تحكم المشرف"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # إحصائيات المستخدمين
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_end > datetime('now') OR subscription_end IS NULL")
        active_users = cursor.fetchone()[0]
        
        # قائمة المستخدمين مع أكواد التفعيل
        cursor.execute("""
            SELECT u.user_id, u.username, u.subscription_start, u.subscription_end, u.is_admin,
                   COALESCE(c.code, '') as activation_code
            FROM users u
            LEFT JOIN user_subscriptions c ON u.user_id = c.user_id
            ORDER BY u.subscription_end DESC
        """)
        users_data = cursor.fetchall()
        
        users = []
        for user in users_data:
            user_id, username, sub_start, sub_end, is_admin, activation_code = user
            
            # حساب حالة المستخدم
            is_active = False
            if is_admin == 1:
                is_active = True
            elif sub_end:
                try:
                    end_time = datetime.fromisoformat(sub_end)
                    is_active = end_time > datetime.now()
                except:
                    is_active = False
            else:
                is_active = False
            
            # تنسيق التواريخ
            sub_start_str = sub_start[:16] if sub_start else "غير محدد"
            sub_end_str = sub_end[:16] if sub_end else "غير محدد"
            
            users.append({
                'user_id': user_id,
                'username': username or 'لا يوجد',
                'activation_code': activation_code if activation_code else None,
                'subscription_date': sub_start_str,
                'subscription_end': sub_end_str,
                'is_active': is_active,
                'is_admin': (is_admin == 1)
            })
        
        conn.close()
        
        # إحصائيات الأكواد
        codes_conn = sqlite3.connect(CODES_DB_NAME)
        codes_cursor = codes_conn.cursor()
        
        codes_cursor.execute("SELECT COUNT(*) FROM subscription_codes WHERE is_active = 1 AND used_by IS NULL")
        available_codes = codes_cursor.fetchone()[0]
        
        codes_cursor.execute("SELECT COUNT(*) FROM subscription_codes WHERE used_by IS NOT NULL")
        used_codes = codes_cursor.fetchone()[0]
        
        # قائمة الأكواد
        codes_cursor.execute("""
            SELECT code, duration, created_at, used_by, used_at, status, is_active
            FROM subscription_codes
            ORDER BY created_at DESC
        """)
        codes_data = codes_cursor.fetchall()
        
        codes = []
        for code_data in codes_data:
            code, duration, created_at, used_by, used_at, status, is_active = code_data
            codes.append({
                'code': code,
                'duration': duration,
                'created_at': created_at[:16] if created_at else 'غير معروف',
                'used_by': used_by,
                'used_at': used_at[:16] if used_at else None,
                'status': status,
                'is_active': is_active
            })
        
        codes_conn.close()
        
        # معلومات النسخ الاحتياطي
        last_backup = "لا توجد نسخ"
        if os.path.exists(BACKUP_DIR):
            backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db') and not f.startswith('codes_')])
            if backups:
                last_backup = backups[-1].replace('backup_', '').replace('.db', '')
        
        return render_template_string(
            ADMIN_PANEL_HTML,
            total_users=total_users,
            active_users=active_users,
            available_codes=available_codes,
            used_codes=used_codes,
            users=users,
            codes=codes,
            data_version=DATA_VERSION,
            last_backup=last_backup
        )
    except Exception as e:
        return f"خطأ في تحميل لوحة التحكم: {str(e)}"

@app.route('/admin/generate_code', methods=['POST'])
def generate_code():
    """توليد كود جديد"""
    try:
        data = request.json
        duration = data.get('duration', 86400)
        
        code = CodeGenerator.create_code(duration)
        
        if code:
            # نسخ احتياطي
            DataProtection.backup_codes_db()
            return jsonify({'success': True, 'code': code})
        else:
            return jsonify({'success': False, 'error': 'فشل في توليد الكود'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/deactivate_code', methods=['POST'])
def deactivate_code():
    """إلغاء كود"""
    try:
        data = request.json
        code = data.get('code')
        
        CodeGenerator.deactivate_code(code)
        DataProtection.backup_codes_db()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/create_backup', methods=['POST'])
def create_backup():
    """إنشاء نسخة احتياطية يدوياً"""
    try:
        backup_file = DataProtection.backup_database()
        codes_backup = DataProtection.backup_codes_db()
        if backup_file or codes_backup:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'فشل إنشاء النسخة الاحتياطية'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/revoke_subscription', methods=['POST'])
def revoke_subscription():
    """إلغاء اشتراك مستخدم"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        success, message = CodeGenerator.revoke_user_subscription(user_id, ADMIN_ID)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/cancel_code', methods=['POST'])
def cancel_code():
    """إلغاء كود غير مستخدم"""
    try:
        data = request.json
        code = data.get('code')
        
        success = CodeGenerator.deactivate_code(code, ADMIN_ID)
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'لا يمكن إلغاء الكود - ربما يكون مستخدماً بالفعل'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/check_code', methods=['POST'])
def check_code():
    """التحقق من حالة كود"""
    try:
        data = request.json
        code = data.get('code')
        
        details = CodeGenerator.get_code_details(code)
        
        if details:
            return jsonify({
                'success': True,
                'code': details[1],
                'duration': details[2],
                'created_at': details[4],
                'used_by': details[5],
                'used_at': details[6],
                'status': details[7],
                'is_active': details[8]
            })
        else:
            return jsonify({'success': False, 'error': 'الكود غير موجود'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/ping')
def ping():
    return "PONG"

# --- دوال قاعدة البيانات مع نظام الأكواد ---
def init_db_with_subscription():
    """تهيئة قاعدة البيانات مع نظام الأكواد"""
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
            'is_admin': 'INTEGER DEFAULT 0'
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
            is_admin INTEGER DEFAULT 0
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
        cursor.execute("INSERT INTO users (user_id, username, is_admin) VALUES (?, ?, ?)", 
                      (ADMIN_ID, "Sz2zv", 1))
    
    conn.commit()
    conn.close()
    
    # تهيئة قاعدة بيانات الأكواد
    CodeGenerator.init_codes_db()
    
    # إنشاء نسخة احتياطية أولية
    DataProtection.backup_database()
    DataProtection.backup_codes_db()
    
    print("✅ Database initialized with code subscription system")
    print("🔑 نظام أكواد الاشتراك نشط - جميع الأكواد محفوظة بشكل دائم")

def check_subscription(user_id):
    """التحقق من صلاحية اشتراك المستخدم باستخدام الأكواد"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin, subscription_end FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return False, "غير مسجل"
        
        is_admin, sub_end = result
        
        # المشرف دائماً له صلاحية
        if is_admin == 1:
            return True, "مشرف"
        
        # التحقق من تاريخ انتهاء الاشتراك
        if sub_end:
            try:
                end_date = datetime.fromisoformat(sub_end)
                if end_date > datetime.now():
                    remaining = end_date - datetime.now()
                    hours = remaining.total_seconds() / 3600
                    if hours < 24:
                        return True, f"ينتهي بعد {int(hours)} ساعة"
                    else:
                        return True, f"ينتهي بعد {remaining.days} يوم"
                else:
                    return False, "انتهى الاشتراك"
            except:
                return False, "خطأ في تاريخ الاشتراك"
        else:
            return False, "غير مشترك"
            
    except Exception as e:
        print(f"⚠️ خطأ في التحقق من الاشتراك: {e}")
        return False, "خطأ في النظام"

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

# --- دوال المساعدة الأساسية ---
def cleanup_old_images():
    """تنظيف الصور القديمة (لا تؤثر على بيانات المشتركين)"""
    try:
        current_time = time.time()
        for filename in os.listdir(IMAGE_CACHE_DIR):
            filepath = os.path.join(IMAGE_CACHE_DIR, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 1800:  # 30 دقيقة
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
        
        # حذف سجلات المحادثات القديمة فقط (أقدم من 30 يوم)
        month_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("DELETE FROM chat_history WHERE timestamp < ?", (month_ago,))
        deleted_rows = cursor.rowcount
        
        # حذف سجلات التتبع القديمة (أقدم من 60 يوم)
        two_months_ago = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("DELETE FROM data_log WHERE timestamp < ?", (two_months_ago,))
        
        conn.commit()
        conn.close()
        
        if deleted_rows > 0:
            print(f"🧹 تم حذف {deleted_rows} سجل دردشة قديم (أقدم من 30 يوم)")
            
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

def cleanup_user_data(context: ContextTypes.DEFAULT_TYPE, user_id: int = None):
    """تنظيف البيانات المؤقتة للمستخدم"""
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

# --- دوال معالجة البوت ---
async def subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """التحقق من الاشتراك للمستخدمين غير المشتركين"""
    user_id = update.effective_user.id
    
    # المشرف دائماً له صلاحية
    if user_id == ADMIN_ID:
        return True
    
    status, message = check_subscription(user_id)
    
    if not status:
        # إخفاء جميع الأزرار وإظهار رسالة اشتراك فقط
        keyboard = [[InlineKeyboardButton("🔑 لدي كود تفعيل", callback_data="activate_code")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"⚠️ **عذراً، ليس لديك صلاحية استخدام البوت**\n\n"
            f"❌ الحالة: {message}\n\n"
            f"🔑 إذا كان لديك كود اشتراك، اضغط على الزر أدناه:\n"
            f"📞 للاستفسار والتواصل مع المشرف: @Sz2zv",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return False
    
    return True

async def activate_code_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب إدخال كود التفعيل"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🔑 **تفعيل الاشتراك**\n\n"
        "الرجاء إرسال كود الاشتراك الذي حصلت عليه:\n"
        "مثال: `Obeida-X7K9P2`\n\n"
        "📌 الكود يبدأ بـ Obeida- متبوعاً بـ 6 أحرف/أرقام",
        parse_mode="Markdown"
    )
    
    return ACTIVATE_CODE

async def handle_code_activation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إدخال كود التفعيل بشكل متطور"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    code = update.message.text.strip()
    
    # رسالة الانتظار
    wait_msg = await update.message.reply_text("🔍 جاري التحقق من الكود...")
    
    # التحقق من صيغة الكود
    if not re.match(r'^Obeida-[A-Z0-9]{6}$', code):
        await wait_msg.edit_text(
            "❌ **صيغة الكود غير صحيحة**\n\n"
            "يجب أن يكون الكود بالصيغة التالية:\n"
            "`Obeida-X7K9P2`\n\n"
            "🔑 6 أحرف/أرقام بعد Obeida-\n"
            "📞 للاستفسار: @Sz2zv",
            parse_mode="Markdown"
        )
        return ACTIVATE_CODE
    
    # التحقق أولاً إذا كان المستخدم لديه اشتراك نشط بالفعل
    has_active, status_msg = CodeGenerator.check_user_active_subscription(user_id)
    if has_active:
        # المستخدم لديه اشتراك نشط بالفعل
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"]
        ]
        
        await wait_msg.edit_text(
            f"✅ **لديك اشتراك نشط بالفعل!**\n\n"
            f"حالة الاشتراك: {status_msg}\n"
            f"يمكنك استخدام البوت مباشرة.",
            parse_mode="Markdown"
        )
        
        await update.message.reply_text(
            "🚀 **مرحباً بعودتك!** اختر أحد الخيارات:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    # التحقق من صحة الكود
    is_valid, duration = CodeGenerator.validate_code(code)
    
    if not is_valid:
        await wait_msg.edit_text(
            "❌ **الكود غير صالح**\n\n"
            "قد يكون الكود:\n"
            "• منتهي الصلاحية\n"
            "• مستخدم بالفعل\n"
            "• ملغي من قبل المشرف\n"
            "• غير موجود\n\n"
            "📞 تواصل مع المشرف: @Sz2zv",
            parse_mode="Markdown"
        )
        return ACTIVATE_CODE
    
    await wait_msg.edit_text("⏳ جاري تفعيل اشتراكك...")
    
    # تفعيل الكود للمستخدم
    success, result = CodeGenerator.activate_code(code, user_id)
    
    if success:
        end_date = result
        
        # تحديث اشتراك المستخدم في قاعدة البيانات الرئيسية
        update_user_subscription_from_code(user_id, code, end_date)
        
        # إنشاء نسخ احتياطية
        DataProtection.backup_codes_db()
        DataProtection.backup_database()
        
        # تنظيف بيانات المستخدم المؤقتة
        cleanup_user_data(context, user_id)
        
        # تجهيز القائمة الرئيسية
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"]
        ]
        
        # رسالة نجاح مع القائمة مباشرة
        if end_date:
            end_datetime = datetime.fromisoformat(end_date)
            remaining = end_datetime - datetime.now()
            hours = remaining.total_seconds() / 3600
            
            if hours < 24:
                time_msg = f"⏱️ المتبقي: {int(hours)} ساعة"
            else:
                time_msg = f"⏱️ المتبقي: {remaining.days} يوم"
            
            await wait_msg.edit_text(
                f"✅ **تم تفعيل الاشتراك بنجاح!**\n\n"
                f"🔑 الكود: `{code}`\n"
                f"📅 تاريخ التفعيل: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                f"📅 ينتهي في: {end_datetime.strftime('%Y-%m-%d %H:%M')}\n"
                f"{time_msg}\n\n"
                f"🚀 **أهلاً بك في Obeida Trading**",
                parse_mode="Markdown"
            )
        else:
            await wait_msg.edit_text(
                f"✅ **تم تفعيل الاشتراك بنجاح!**\n\n"
                f"🔑 الكود: `{code}`\n"
                f"📅 تاريخ التفعيل: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                f"📅 اشتراك غير محدد المدة\n\n"
                f"🚀 **أهلاً بك في Obeida Trading**",
                parse_mode="Markdown"
            )
        
        # عرض القائمة
        await update.message.reply_text(
            "اختر أحد الخيارات من القائمة أدناه:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        
        return MAIN_MENU
    else:
        # فشل التفعيل
        await wait_msg.edit_text(
            f"❌ **حدث خطأ في تفعيل الكود**\n\n"
            f"السبب: {result}\n\n"
            f"📞 تواصل مع المشرف: @Sz2zv",
            parse_mode="Markdown"
        )
        return ACTIVATE_CODE

async def debug_check_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر للتحقق من الكود (للمشرف فقط)"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ هذا الأمر للمشرف فقط")
        return
    
    try:
        # الحصول على الكود من الرسالة
        parts = update.message.text.split()
        if len(parts) < 2:
            await update.message.reply_text("❌ استخدم: /checkcode Obeida-XXXXXX")
            return
        
        code = parts[1].strip()
        
        # التحقق من قاعدة البيانات
        conn = sqlite3.connect(CODES_DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT code, duration, created_at, used_by, status, is_active 
            FROM subscription_codes 
            WHERE code = ?
        ''', (code,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            msg = f"✅ **الكود موجود في قاعدة البيانات**\n\n"
            msg += f"🔑 الكود: `{result[0]}`\n"
            msg += f"⏱️ المدة: {result[1]} ثانية\n"
            msg += f"📅 تاريخ الإنشاء: {result[2]}\n"
            msg += f"👤 مستخدم بواسطة: {result[3] if result[3] else 'لم يستخدم'}\n"
            msg += f"📊 الحالة: {result[4]}\n"
            msg += f"🟢 نشط: {'نعم' if result[5] else 'لا'}"
        else:
            msg = f"❌ **الكود غير موجود في قاعدة البيانات**\n\n"
            msg += f"الكود المدخل: `{code}`\n"
            msg += f"📌 تأكد من:\n"
            msg += f"• تم توليد الكود بشكل صحيح\n"
            msg += f"• لم يتم حذف قاعدة البيانات\n"
            msg += f"• الكود لم ينته صلاحيته"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)}")

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
    
    # العودة للقائمة الرئيسية
    if user_text == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
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
        wait_msg = await update.message.reply_text(f"⏳ استنى شويه بسحب صورة `{symbol_to_analyze}`🤌...")
        
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
        
        # البرومبت الرئيسي (نفس البرومبت الطويل السابق)
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
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return RECOMMENDATION_MODE
    
    user_id = update.effective_user.id
    
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

async def start_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع الدردشة المتقدم"""
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return CHAT_MODE
    
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
    
    # التحقق من الأوامر الخاصة
    if user_message == "ايقاف الدردشة":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
        if user_id == ADMIN_ID:
            main_keyboard.append(["👑 لوحة التحكم"])
        await update.message.reply_text(
            "✅ تم إنهاء وضع الدردشة.",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "الرجوع للقائمة الرئيسية":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"]]
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

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور للتحليل الفني المتقدم"""
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return MAIN_MENU
    
    user_id = update.effective_user.id
    candle, trade_time, prev_context, prev_time = get_user_setting(user_id)
    
    if not candle or not trade_time:
        keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**\n\n"
            "الرجاء استخدام أزرار القائمة لضبط الإعدادات قبل تحليل الصور.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    wait_msg = await update.message.reply_text(" ثواني وساقوم بتحليل 🤌...")
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
        
        candle_closing_status = f"الوقت المتبقي الشمعة: {seconds_remaining} ثانية"
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
            "max_tokens": 1100,
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
        
        # البرومبت الرئيسي (نفس البرومبت الطويل السابق)
        MAIN_PROMPT = f"""
أنت محلل فني خبير متكامل في SMC + ICT + WYCKOFF + VOLUME PROFILE + MARKET PSYCHOLOGY.
مهمتك تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية دقيقة.

🎯 **التوجيه الأساسي: استخرج كل شيء من الصورة فقط**
1. اقرأ الأسعار من المحور اليميني بدقة بكسلية
2. ابحث عن ساعة المنصة في الزاوية السفلية - المرجع الوحيد للوقت
3. افحص آخر 5-7 شموع (الأجسام، الذيول، الألوان، الأحجام)
4. حدد الدعم/المقاومة، الفجوات، مناطق السيولة

{previous_context_info}

📌 **المتغيرات المستلمة من Bot.py:**
• إطار الشموع: {{candle}}
• فريم التحقق: {{verification_timeframe}}
• استراتيجية التداول: {{trading_strategy}}
• جلسة السوق: {{session_name}} ({{session_time}}) - {{session_vol}}
• تأثير الأخبار: {{news_impact}} (×{{news_risk_multiplier}}) - {{news_warning if news_warning else "✅ آمن"}}
• منطقة القتل: {{kill_zone_status}}
• دقيقة الغدر: {{last_minute_status}}
• حالة الإغلاق: {{candle_closing_status}} - متبقي {{seconds_remaining}} ثانية
• تصنيف الفريم: {{candle_category}}
• حجم الصفقة: {{position_sizing}}
• سياق سابق: {{"✅ متوفر" if previous_context_info else "❌ لا يوجد"}}

🔍 **نظام الاستخراج البصري:**

【المرحلة 1: مسح الصورة】
• الأسعار: السعر الحالي، أعلى/أدنى، دعم/مقاومة
• الزمن: الوقت من ساعة المنصة - المرجع الوحيد
• الشموع: آخر 5 شموع (فتحة، إغلاق، أعلى، أدنى، الجسم، الذيول)

【المرحلة 2: الوصف الإلزامي】
• الشمعة -1: [نسبة الجسم %] - [اللون]
• الشمعة -2: [نسبة الجسم %] - [اللون]
• الشمعة -3: [نسبة الجسم %] - [اللون]
• اتجاه الأحجام: [تزايد / تناقص / ثابت]

【المرحلة 3: الحسابات الدقيقة】
• السعر الحالي: [_____] من المحور Y
• السعر المستهدف: [_____]
• المسافة: |السعر - الهدف| = [_____] نقطة
• سرعة آخر 3 شموع: [_____] نقطة/شمعة
• الشموع المتوقعة: المسافة ÷ السرعة = [_____]

【المرحلة 4: معالجة ضعف وضوح الصورة】
• إذا كان الرقم غير واضح: قارن موضع الشمعة بأقرب مستوى دعم/مقاومة
• دقة التقدير: +/- 1 نقطة

🛡️ **بروتوكول الفشل الذاتي - توقف فوراً إذا:**
1. ضوضاء بصرية عالية (مؤشرات تغطي الشموع)
2. محور أسعار مقطوع (أرقام غير واضحة)
3. ساعة منصة غير واضحة
4. جودة صورة رديئة

✅ الإجراء: "الصورة غير صالحة للتحليل - أرسل صورة أوضح"

🔥 **قوانين التحليل الأساسية:**

【قانون الفتيلة】
• لا تثق بالذيل الطويل إلا إذا ابتلعت الشمعة التالية 50% من الجسم السابق
• إذا لم يحدث الابتلاع = فراغ سعري سيتم ملؤه - استمر مع الاتجاه
• نسبة الذيل >60% = مراقبة فقط، وليس دخول فوري
• ممنوع إصدار إشارة انعكاس بناءً على الذيل وحده

【معالجة الشموع الصغيرة】
• شمعة عكسية حجمها <25% من السابقة = تُهمل تماماً (تجميع سيولة)

【الأرقام المستديرة】
• الرقم المستدير (.000) = مغناطيس وليس مقاومة
• لا تدخل عكسه إلا بعد لمسه بذيل ثم العودة
• قبل ذلك، الهدف هو لمس الرقم فقط

【الزخم】
• 3 شموع متتالية بنفس اللون بأجسام >80% = استمرار فقط (ممنوع الانعكاس)
• في OTC، الزخم يغلب الهيكل دائماً

【سيادة الجسم - Marubozu Supremacy】
• إذا كان الجسم >90% والذيل <20% = الذيل "جس نبض" وليس انعكاساً
• يُلغى قانون الفتيلة في هذه الحالة

【كشف التلاعب】
• القمم المتساوية = أهداف سيولة وليست مقاومات
• انتظر اختراق القمة ثم قرر

【الفجوات】
• FVG هابطة + 3 شموع خضراء متزايدة = إلغاء البيع (الزخم أقوى)
• Inversion FVG: إغلاق فوق منتصف الفجوة = إشارة شراء فورية

【الاندفاع المؤسسي】
• تغطية هبوط 10 دقائق بصعود دقيقتين = اندفاع - يُمنع البيع

⚠️ **هرم الأولويات - Priority Protocol V3:**

1. **الزخم:** آخر 3 شموع >85% = الرتبة الأعلى - يلغي كل قواعد الارتداد
2. **السيولة:** الأهداف السعرية (رقم مستدير/قمة) أولى من الفجوات
3. **الفجوات:** تُملأ بعد ضرب السيولة الخارجية
4. **الفتيلة:** تستخدم فقط مع زخم ضعيف (<40%) وابتلاع 50%
5. **الهيكل:** يُطبق في غياب الزخم والسيولة
6. **المؤشرات:** آخر مرتبة عند التعارض

💰 **فلاتر الرفض الإلزامية - إن وُجد سبب → احتفاظ:**

【فلتر المسافة】
• المسافة بين السعر والهدف < 5 نقاط = احتفاظ 🟡
• استثناء واحد فقط - "اللمس الحتمي": زخم >85% نحو رقم مستدير = يُسمح بدخول حتى نقطتين

【فلتر السبريد - للفريمات S5, S10, S15, S30】
• المسافة الصافية = المسافة - (السبريد × 2)
• السبريد التقديري: فوركس (1-2) / مؤشرات (2-5) / رقمية (3-10)
• المسافة الصافية < 3 نقاط = احتفاظ 🟡 (إلا مع اللمس الحتمي)

【فلتر الأخبار】
• شمعة حجمها >300% من المتوسط = احتفاظ 🟡 لمدة 3 شموع
• ذيول الأخبار لا تُحتسب في التحليل

【أسباب المنع السبعة】
1. زخم معاكس: آخر 3 شموع >80% عكس الاتجاه
2. رقم مستدير غير ملموس: المسافة >3 نقاط (إلا مع اللمس الحتمي)
3. دقيقة الغدر: (55-59) أو (25-29) أو (40-44) أو (10-14) - من ساعة المنصة
4. زخم منهك: الشمعة الحالية >200% من المتوسط
5. غياب التأكيد: عدم ابتلاع 50% (إلا مع سيادة الجسم أو Micro-Rejection)
6. تضارب الإطارات: تعارض {{candle}} مع {{verification_timeframe}}
7. أخبار نشطة: ±30 دقيقة من خبر قوي

🕐 **استثناءات الفريمات الصغيرة (S5, S10 فقط):**
• إذا الزخم >90% = يُسمح بالدخول حتى آخر ثانيتين (بدلاً من 10 ثوانٍ)
• إذا الجسم >90% والذيل <20% = استمرار وليس انعكاس
• شمعة عكسية <25% = تُهمل

🔄 **قاعدة تكرار النمط:**
• إذا تكرر نفس السلوك 3 مرات، توقع انعكاساً في الرابعة

📊 **نظام التحليل متعدد المستويات:**

【المستوى الاستراتيجي】
• الهيكل: BOS/CHoCh - من حركة السعر
• مرحلة Wyckoff: Accumulation/Markup/Distribution/Decline
• السياق: {{session_name}} - {{session_vol}}

【المستوى التكتيكي】
• أنماط الشموع: آخر 5 شموع
• Order Blocks: آخر 3 مناطق طلب/عرض
• FVG: الفجوات غير المغطاة
• السيولة: Equal Highs/Lows + Inducement

【المستوى التنفيذي】
• Entry: شمعة إغلاق + حجم
• Risk: RR ديناميكي ×{{news_risk_multiplier}}
• Timing: من ساعة المنصة

🛡️ **نظام القرارات السريع:**
• REJECTION: ذيل طويل + إغلاق داخل النطاق = دخول عكسي
• MOMENTUM: 3 شموع قوية = استمرار حتى أقرب رقم مستدير
• GAP: السعر يتحرك من فجوة إلى فجوة
• LAST MINUTE: في الفريمات العادية، تجاهل الدقيقة 59/29/14/44
• SPREAD: هدف <5 نقاط على S5/S10/S15 = احتفاظ (إلا مع اللمس الحتمي)
• NEWS SPIKE: شمعة >300% = حظر 3 شموع

🎯 **النظام الزمني - المرجع الوحيد: ساعة المنصة**

【استخراج الوقت】
• الوقت على المنصة: [_____] من الصورة
• الفريم: {{candle}}
• المسافة للهدف: [_____] نقطة
• سرعة الشموع: [_____] نقطة/شمعة
• الشموع المتوقعة: [_____]
• وقت الوصول المتوقع: [_____]

【معامل الانزلاق - S5, S10 فقط】
• نقطة الدخول المعدلة = السعر الحالي + نقطة أمان في اتجاه الصفقة
• المسافة الفعلية = المسافة - 1 نقطة

✅ **بروتوكول التأكيد النهائي - تأكد من:**
1. صحة الصورة (لا يوجد سبب للفشل الذاتي)
2. المسافة >5 نقاط أو تفعيل اللمس الحتمي
3. المسافة الصافية >3 نقاط أو تفعيل اللمس الحتمي
4. لا شمعة >300%
5. الزخم ليس معاكساً
6. لا دقيقة غدر (أو تفعيل استثناء S5/S10)
7. توفر ابتلاع 50% (أو تفعيل سيادة الجسم/Micro-Rejection)
8. لا تضارب في الإطارات
9. لا أخبار نشطة
10. لا شمعة إنهاك

🎯 **القاعدة الذهبية:**
إذا شككت (<90% ثقة) أو فشل أي فلتر → احتفاظ 🟡

📊 **تنسيق الإجابة النهائي (الالتزام بهذا الترتيب) بشكل مختصرآ:**

📊 التحليل الفني المتقدم:
• البصمة الزمنية: {{kill_zone_status}}
• تطبيق قانون الفتيلة: [نعم/لا] - نسبة الذيل: [٪] (يُلغى في حالة سيادة الجسم)
• حالة الهيكل: (صاعد/هابط) + (توافق 4/4 إطارات: نعم / لا )
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• الفجوات السعرية: (المناطق التي سيعود السعر لتغطيتها)

🎯 * الإشارة التنفيذية:*
• السعر الحالي: [ السعر الدقيق من الشارت ]
• حالة الشمعة: [مفتوحة / مغلقة] [ حمراء 🔴  / خضراء 🟢 ] - الوقت المتبقي: [ وقت دقيق من شارت ]
• القرار الفني: (شراء 🟢 / بيع 🔴 / احتفاظ 🟡)
• قوة الإشارة: (عالية جدا 💥 / عالية 🔥 / متوسطة ⚡ / ضعيفة ❄️)
• نقطة الدخول: [السعر الدقيق بناءً على OB + شرط الإغلاق]
• الوقت المتبقي للدخول: [الآن / بعد إغلاق الشمعة الحالية / بعد X شموع خضراء / بعد X شموع حمراء]
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
• فريم التحقق: {{verification_timeframe}}

**🎯 التعليمات النهائية للتركيز:**
1.  **استخرج كل البيانات من الصورة فقط**
2.  **لا تفترض أي معلومات غير موجودة في الصورة**
3.  **ركز على قراءة الأسعار من المحور اليميني بدقة (مع تقريب +/- 1 نقطة عند الضرورة)**
4.  **ابحث عن ساعة المنصة واقرأ الوقت بدقة - هذا هو المرجع الوحيد لدقائق الغدر**
5.  **حلل كل شمعة بشكل منفصل من الصورة**
6.  **طبق هرم الأولويات الصارم V3: الزخم >85% (ماروبوزو) يلغي الفتيلة، والسيولة الخارجية تسبق الفجوة**
7.  **طبق فلتر المسافة الحقيقية مع معامل الانزلاق: أقل من 5 نقاط = احتفاظ (إلا مع استثناء اللمس الحتمي)**
8.  **طبق فلتر السبريد: أهداف <5 نقاط على فريمات صغيرة = احتفاظ (إلا مع استثناء اللمس الحتمي)**
9.  **طبق فلتر الأخبار: شمعة >300% = حظر 3 شموع**
10. **طبق شرط التأكيد: لا دخول عكسي بدون ابتلاع 50% (إلا مع Marubozu Supremacy أو Micro-Rejection)**
11. **طبق استثناءات الزخم عند توفر شروطها: (اللمس الحتمي، ثواني الغدر، سيادة الجسم، Micro-Rejection)**
12. **طبق بروتوكول الفشل الذاتي: إذا كانت الصورة غير واضحة، توقف فوراً وأبلغ المستخدم**
13. **طبق قاعدة تكرار النمط: راقب التكرار 3 مرات وتوقع انعكاساً في الرابعة**

الآن قم بتحليل الشارت المرفق **بتركيز كامل على استخراج البيانات من الصورة** وأعطني الإجابة بالتنسيق المطلوب أعلاه فقط.
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        # --- الخطوة 1: التحليل الأولي الأساسي ---
        await wait_msg.edit_text("استنى شويه قاعد بحلل 🤌...")
        
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
        
        # تنظيف النصوص من التكرار
        initial_analysis = clean_repeated_text(initial_analysis)
        
        # حفظ سياق التحليل في قاعدة البيانات
        save_analysis_context(user_id, initial_analysis)
        
        keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        # تنسيق وقت الصفقة للعرض
        time_display = format_trade_time_for_prompt(trade_time)
        
        # إعداد النص النهائي
        full_result = (
            f"✅ **تم التحليل بنجاح** ✅\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{initial_analysis}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔧 **الإعدادات المستخدمة:**\n"
            f"•🕧 فريم التحقق للكسر: {verification_timeframe}\n"
            f"•🕧 جلسة السوق: {session_name} ({session_time})\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Powered by - Obeida Trading**"
        )
        
        # تنظيف النهائي من التكرارات
        full_result = clean_repeated_text(full_result)
        
        # تنسيق النتيجة مع دعم النسخ السريع
        formatted_result = format_as_copyable_code(full_result, "text")
        
        # تقسيم النتيجة إذا كانت طويلة
        if len(formatted_result) > 4000:
            parts = split_message_with_copy_support(full_result, max_length=3500)
            
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
                formatted_result,
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

async def start_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء لوحة تحكم المشرف"""
    user_id = update.effective_user.id
    
    # التحقق من أن المستخدم هو المشرف
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول للوحة التحكم.")
        return MAIN_MENU
    
    keyboard = [
        ["👥 عرض المستخدمين", "🔑 عرض الأكواد"],
        ["➕ توليد كود", "⏱️ تمديد اشتراك"],
        ["🗑️ حذف مستخدم", "📊 إحصائيات"],
        ["🔙 العودة للقائمة الرئيسية"]
    ]
    
    await update.message.reply_text(
        "👑 **لوحة تحكم المشرف** 👑\n\n"
        "🔑 **نظام أكواد الاشتراك نشط**\n"
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
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"], ["👑 لوحة التحكم"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "👥 عرض المستخدمين":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT u.user_id, u.username, u.subscription_start, u.subscription_end, u.is_admin,
                   COALESCE(c.code, '') as activation_code
            FROM users u
            LEFT JOIN user_subscriptions c ON u.user_id = c.user_id
            ORDER BY u.subscription_end DESC
        """)
        users = cursor.fetchall()
        conn.close()
        
        if not users:
            await update.message.reply_text("📭 لا يوجد مستخدمين بعد.")
        else:
            msg = "👥 **قائمة المستخدمين:**\n\n"
            for u in users:
                uid, uname, sstart, send, is_admin, code = u
                status = "👑 مشرف" if is_admin == 1 else "👤 مستخدم"
                sub_info = f"📅 الاشتراك: {send[:16] if send else 'غير محدد'}"
                code_info = f"🔑 الكود: `{code}`" if code else ""
                msg += f"• {uname or uid} - {status}\n  {sub_info}\n  {code_info}\n\n"
            
            for part in split_message(msg, 4000):
                await update.message.reply_text(part, parse_mode="Markdown")
        
        return ADMIN_MENU
    
    elif user_message == "🔑 عرض الأكواد":
        codes = CodeGenerator.list_all_codes()
        
        if not codes:
            await update.message.reply_text("📭 لا توجد أكواد بعد.")
        else:
            msg = "🔑 **قائمة أكواد الاشتراك:**\n\n"
            for code in codes:
                c_code, duration, created_at, used_by, used_at, status, is_active = code
                
                # تنسيق المدة
                if duration == 0:
                    duration_text = "غير محدد"
                elif duration < 86400:
                    duration_text = f"{duration//3600} ساعة"
                else:
                    duration_text = f"{duration//86400} يوم"
                
                # الحالة
                if used_by:
                    status_text = f"✅ مستخدم بواسطة {used_by}"
                    if used_at:
                        status_text += f" في {used_at[:16]}"
                elif status == 'cancelled':
                    status_text = "🔴 ملغي"
                elif status == 'revoked':
                    status_text = "⛔ ملغي بعد الاستخدام"
                elif is_active:
                    status_text = "🟢 متاح"
                else:
                    status_text = "🔴 غير نشط"
                
                msg += f"• `{c_code}`\n  📅 المدة: {duration_text}\n  📆 أنشئ: {created_at[:16]}\n  {status_text}\n\n"
            
            for part in split_message(msg, 4000):
                await update.message.reply_text(part, parse_mode="Markdown")
        
        return ADMIN_MENU
    
    elif user_message == "➕ توليد كود":
        await update.message.reply_text(
            "➕ **توليد كود اشتراك جديد**\n\n"
            "أرسل المدة بالثواني:\n"
            "• `3600` - ساعة\n"
            "• `86400` - يوم\n"
            "• `604800` - أسبوع\n"
            "• `2592000` - شهر\n"
            "• `0` - غير محدد\n\n"
            "مثال: `86400`",
            parse_mode="Markdown"
        )
        return GENERATE_CODE
    
    elif user_message == "⏱️ تمديد اشتراك":
        await update.message.reply_text(
            "⏱️ **تمديد اشتراك مستخدم**\n\n"
            "أرسل معرف المستخدم والمدة الجديدة:\n"
            "مثال: `123456789 86400`",
            parse_mode="Markdown"
        )
        return SET_USER_DURATION
    
    elif user_message == "🗑️ حذف مستخدم":
        await update.message.reply_text(
            "🗑️ **حذف مستخدم**\n\n"
            "أرسل معرف المستخدم للحذف:\n"
            "مثال: `123456789`",
            parse_mode="Markdown"
        )
        return REMOVE_USER
    
    elif user_message == "📊 إحصائيات":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_end > datetime('now') OR subscription_end IS NULL")
        active_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_end < datetime('now') AND subscription_end IS NOT NULL")
        expired_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1")
        admins = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM chat_history WHERE date(timestamp) = date('now')")
        today_analyses = cursor.fetchone()[0]
        
        conn.close()
        
        # إحصائيات الأكواد
        codes = CodeGenerator.list_all_codes()
        total_codes = len(codes)
        used_codes = sum(1 for c in codes if c[3] is not None)  # used_by not null
        available_codes = sum(1 for c in codes if c[5] == 'active' and c[3] is None)  # status active and not used
        
        stats = (
            "📊 **إحصائيات النظام:**\n\n"
            f"👥 إجمالي المستخدمين: {total_users}\n"
            f"✅ المستخدمين النشطين: {active_users}\n"
            f"❌ المنتهية اشتراكاتهم: {expired_users}\n"
            f"👑 عدد المشرفين: {admins}\n"
            f"📊 تحليلات اليوم: {today_analyses}\n\n"
            f"🔑 **إحصائيات الأكواد:**\n"
            f"📦 إجمالي الأكواد: {total_codes}\n"
            f"✅ الأكواد المستخدمة: {used_codes}\n"
            f"🟢 الأكواد المتاحة: {available_codes}"
        )
        
        await update.message.reply_text(stats, parse_mode="Markdown")
        return ADMIN_MENU
    
    return ADMIN_MENU

async def handle_generate_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة توليد كود جديد - نسخة محسنة"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    try:
        duration = int(update.message.text.strip())
        
        # توليد الكود
        code = CodeGenerator.create_code(duration)
        
        if not code:
            await update.message.reply_text(
                "❌ **فشل في توليد الكود**\n\n"
                "يرجى التحقق من:\n"
                "• صلاحية قاعدة البيانات\n"
                "• المساحة التخزينية\n"
                "• ثم حاول مرة أخرى",
                parse_mode="Markdown"
            )
            return ADMIN_MENU
        
        # التحقق المباشر من وجود الكود
        conn = sqlite3.connect(CODES_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT code FROM subscription_codes WHERE code = ?", (code,))
        saved = cursor.fetchone()
        conn.close()
        
        if not saved:
            await update.message.reply_text(
                "❌ **تم توليد الكود ولكن فشل في الحفظ**\n\n"
                "يرجى إبلاغ المشرف الفني",
                parse_mode="Markdown"
            )
            return ADMIN_MENU
        
        # نسخ احتياطي
        DataProtection.backup_codes_db()
        
        # تنسيق المدة
        if duration == 0:
            duration_text = "غير محدد"
        elif duration < 86400:
            duration_text = f"{duration//3600} ساعة"
        else:
            duration_text = f"{duration//86400} يوم"
        
        await update.message.reply_text(
            f"✅ **تم توليد كود جديد بنجاح!**\n\n"
            f"🔑 **الكود:** `{code}`\n"
            f"📅 **المدة:** {duration_text}\n\n"
            f"📋 يمكنك نسخ الكود وإرساله للمستخدم.\n"
            f"⚠️ الكود صالح للاستخدام مرة واحدة فقط.\n\n"
            f"✅ تم حفظ الكود في قاعدة البيانات بنجاح.",
            parse_mode="Markdown"
        )
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال رقم صحيح للمدة.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)}")
    
    return ADMIN_MENU

async def handle_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة حذف مستخدم"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    try:
        target_user_id = int(update.message.text.strip())
        
        if target_user_id == ADMIN_ID:
            await update.message.reply_text("❌ لا يمكن حذف المشرف الرئيسي.")
            return ADMIN_MENU
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (target_user_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ?", (target_user_id,))
        
        conn.commit()
        conn.close()
        
        # تسجيل التغيير وإنشاء نسخة احتياطية
        log_data_change("delete_user", target_user_id, "user deleted")
        DataProtection.backup_database()
        
        await update.message.reply_text(f"✅ تم حذف المستخدم {target_user_id} بنجاح.")
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال معرف المستخدم (أرقام فقط).")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)}")
    
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
        duration = int(parts[1])
        
        if duration > 0:
            end_date = datetime.now() + timedelta(seconds=duration)
            
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users 
                SET subscription_end = ?
                WHERE user_id = ?
            """, (end_date.isoformat(), target_user_id))
            conn.commit()
            conn.close()
            
            # تسجيل التغيير وإنشاء نسخة احتياطية
            log_data_change("update_duration", target_user_id, f"duration={duration}")
            DataProtection.backup_database()
            
            duration_text = f"{duration//3600} ساعة" if duration < 86400 else f"{duration//86400} يوم"
            await update.message.reply_text(
                f"✅ تم تحديث اشتراك المستخدم {target_user_id}\n"
                f"📅 المدة الجديدة: {duration_text}\n"
                f"📆 ينتهي في: {end_date.strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users 
                SET subscription_end = NULL
                WHERE user_id = ?
            """, (target_user_id,))
            conn.commit()
            conn.close()
            
            # تسجيل التغيير وإنشاء نسخة احتياطية
            log_data_change("update_duration", target_user_id, "unlimited")
            DataProtection.backup_database()
            
            await update.message.reply_text(f"✅ تم تحويل المستخدم {target_user_id} إلى اشتراك غير محدد.")
        
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
    """تنظيف دوري للملفات المؤقتة"""
    while True:
        try:
            await asyncio.sleep(1800)  # كل 30 دقيقة
            cleanup_old_images()
            cleanup_old_database_records()
            print("🧹 تم التنظيف الدوري للملفات المؤقتة")
            
        except Exception as e:
            print(f"⚠️ خطأ في التنظيف الدوري: {e}")

async def backup_scheduler():
    """جدولة النسخ الاحتياطي التلقائي كل ساعة"""
    while True:
        try:
            await asyncio.sleep(3600)  # كل ساعة
            DataProtection.auto_backup()
            print("⏰ تم تنفيذ النسخ الاحتياطي التلقائي المجدول")
        except Exception as e:
            print(f"⚠️ خطأ في النسخ الاحتياطي المجدول: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت - مع إخفاء الأزرار لغير المشتركين"""
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    
    # تسجيل المستخدم في قاعدة البيانات إذا لم يكن موجوداً
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # التحقق من وجود المستخدم
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        # إضافة المستخدم مع اشتراك غير محدد (يحتاج تفعيل من المشرف)
        cursor.execute("""
            INSERT INTO users (user_id, username, candle, trade_time, subscription_start)
            VALUES (?, ?, ?, ?, datetime('now'))
        """, (user_id, username, "M1", "قصير (1m-15m)"))
        conn.commit()
    
    conn.close()
    
    # تنظيف الذاكرة المؤقتة
    cleanup_user_data(context, user_id)
    
    # التحقق من صلاحية المشرف والاشتراك
    is_admin = (user_id == ADMIN_ID)
    status, message = check_subscription(user_id)
    
    if is_admin:
        # المشرف - يرى جميع الأزرار
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"],
            ["👑 لوحة التحكم"]
        ]
        
        welcome_msg = (
            "🚀 *أهلاً بك في Obeida Trading*\n\n"
            "👑 أنت مسؤول النظام - لديك صلاحيات كاملة.\n\n"
            "🔑 *نظام أكواد الاشتراك نشط*\n"
            "🤖 *الخدمات المتاحة:*\n"
            "• 📊 تحليل الشارتات\n"
            "• 📈 توصيات فورية\n"
            "• 💬 دردشة ذكية\n"
            "• ⚙️ إعدادات متقدمة\n\n"
            "اختر أحد الخيارات:"
        )
        
    elif status:
        # مستخدم مشترك - يرى جميع الأزرار
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"]
        ]
        
        welcome_msg = (
            f"🚀 *أهلاً بك في Obeida Trading*\n\n"
            f"✅ حالة اشتراكك: {message}\n\n"
            f"🤖 *الخدمات المتاحة:*\n"
            f"• 📊 تحليل الشارتات\n"
            f"• 📈 توصيات فورية\n"
            f"• 💬 دردشة ذكية\n"
            f"• ⚙️ إعدادات متقدمة\n\n"
            f"اختر أحد الخيارات:"
        )
        
    else:
        # مستخدم غير مشترك - لا يرى أي أزرار، فقط زر تفعيل الكود
        inline_keyboard = [[InlineKeyboardButton("🔑 لدي كود تفعيل", callback_data="activate_code")]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        
        await update.message.reply_text(
            f"⚠️ **عذراً، ليس لديك صلاحية استخدام البوت**\n\n"
            f"❌ الحالة: {message}\n\n"
            f"🔑 إذا كان لديك كود اشتراك، اضغط على الزر أدناه:\n"
            f"📞 للاستفسار والتواصل مع المشرف: @Sz2zv\n\n"
            f"بعد إدخال الكود، أرسل /start مرة أخرى",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    # إظهار القائمة للمستخدمين المشتركين فقط
    await update.message.reply_text(
        welcome_msg,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات القائمة الرئيسية"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    # التحقق من الاشتراك (لغير المشرف)
    if user_id != ADMIN_ID:
        status, _ = check_subscription(user_id)
        if not status:
            # إخفاء الأزرار فوراً
            inline_keyboard = [[InlineKeyboardButton("🔑 لدي كود تفعيل", callback_data="activate_code")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard)
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك. إذا كان لديك كود جديد، اضغط على الزر أدناه.",
                reply_markup=reply_markup
            )
            return MAIN_MENU
    
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
        candle, trade_time, _, _ = get_user_setting(user_id)
        
        if not candle or not trade_time:
            keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
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
                f"📡 *نظام التحليل الشارات :* \n"
                f"1. 🔧 مرحلة تفكيك هيكل الشارت \n"
                f"2. 🔬 مرحلة تحليل صورة الشارت \n\n"
                f"📤 قم برفع صورة  (الشارت) الآن: 📤",
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
        ["💬 دردشة", "📈 توصية"]
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
        status, _ = check_subscription(user_id)
        if not status:
            inline_keyboard = [[InlineKeyboardButton("🔑 لدي كود تفعيل", callback_data="activate_code")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard)
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك. إذا كان لديك كود جديد، اضغط على الزر أدناه.",
                reply_markup=reply_markup
            )
            return SETTINGS_CANDLE
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"]
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
        status, _ = check_subscription(user_id)
        if not status:
            inline_keyboard = [[InlineKeyboardButton("🔑 لدي كود تفعيل", callback_data="activate_code")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard)
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك. إذا كان لديك كود جديد، اضغط على الزر أدناه.",
                reply_markup=reply_markup
            )
            return SETTINGS_TIME
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"]
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
        
        keyboard = [["📊 تحليل صورة"], ["💬 دردشة"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        candle, _, _, _ = get_user_setting(user_id)
        
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
        status, _ = check_subscription(user_id)
        if not status:
            inline_keyboard = [[InlineKeyboardButton("🔑 لدي كود تفعيل", callback_data="activate_code")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard)
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك. إذا كان لديك كود جديد، اضغط على الزر أدناه.",
                reply_markup=reply_markup
            )
            return ANALYZE_MODE
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"]
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
    
    🔑 **نظام أكواد الاشتراك:**
    • تحصل على كود من المشرف مثل: `Obeida-X7K9P2`
    • أدخل الكود لتفعيل اشتراكك
    • الكود صالح للاستخدام مرة واحدة فقط
    
    📈 **نظام التوصيات الجديد:**
    • تحليل فني للعملات والمؤشرات
    • سحب تلقائي للصور من TradingView
    • تطبيق القوانين الجديدة
    • تحليل مزدوج (نصي + بصري)
    
    ⏱️ **خيارات مدة الصفقة:**
    • **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة
    • **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة
    • **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة
    
    👑 **للمشرفين فقط:**
    • لوحة تحكم كاملة عبر الرابط /admin
    • توليد أكواد اشتراك جديدة
    • إدارة المستخدمين والاشتراكات
    • نظام نسخ احتياطي آلي للبيانات
    
    💾 **نظام حماية البيانات:**
    • نسخ احتياطي تلقائي كل ساعة
    • حفظ آخر 50 نسخة احتياطية
    • استعادة تلقائية عند تلف البيانات
    • جميع بيانات المشتركين محفوظة بشكل دائم
    
    📡 **مميزات البوت المتقدمة:**
    • 📊 تحليل فني للرسوم البيانية بتقنيات متطورة
    • 💬 دردشة ذكية متعددة التخصصات
    • 📈 نظام توصيات الجاهزة 
    • ⚙️ حفظ إعداداتك الشخصية
    • 🔹 واجهة سهلة بالأزرار
    • 🗑️ نظام تنظيف تلقائي للذاكرة
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
    """تشغيل Flask server"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def main():
    """الدالة الرئيسية - النسخة النهائية مع نظام أكواد الاشتراك"""
    print("🤖 Starting Obeida Trading with Code Subscription System ...")
    print("📋 تم تفعيل نظام النسخ السريع - جميع التحليلات قابلة للنسخ بنقرة واحدة")
    print("👑 المشرف الرئيسي: 6207431030 (@Sz2zv)")
    print("🔑 نظام أكواد الاشتراك - مفعل (أكواد تبدأ بـ Obeida- و 6 أحرف/أرقام)")
    print("🔒 نظام إخفاء الأزرار لغير المشتركين - مفعل")
    print("💾 نظام حماية البيانات والنسخ الاحتياطي - مفعل")
    print(f"📊 نسخة البيانات: {DATA_VERSION}")
    
    # التحقق من وجود قاعدة بيانات الأكواد
    if os.path.exists(CODES_DB_NAME):
        print(f"📁 ملف قاعدة البيانات {CODES_DB_NAME} موجود")
        # عرض الأكواد الموجودة
        debug_check_codes()
    else:
        print(f"📁 ملف قاعدة البيانات {CODES_DB_NAME} غير موجود - سيتم إنشاؤه")
    
    # التحقق من سلامة قاعدة البيانات
    is_valid, message = DataProtection.verify_database_integrity()
    if not is_valid:
        print(f"⚠️ تحذير: {message}")
        print("🔄 محاولة استعادة آخر نسخة احتياطية...")
        success, msg = DataProtection.restore_latest_backup()
        if success:
            print(f"✅ {msg}")
        else:
            print(f"❌ {msg}")
            print("📦 سيتم إنشاء قاعدة بيانات جديدة")
    
    # تشغيل Flask
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    print(f"🌐 Flask server started on port {os.environ.get('PORT', 8080)}")
    print(f"📊 لوحة تحكم المشرف: http://localhost:{os.environ.get('PORT', 8080)}/admin")
    print(f"💾 مجلد النسخ الاحتياطي: {os.path.abspath(BACKUP_DIR)}")
    
    # تهيئة قاعدة البيانات مع نظام الأكواد
    init_db_with_subscription()
    
    # إنشاء نسخة احتياطية أولية
    DataProtection.backup_database()
    DataProtection.backup_codes_db()
    
    # إنشاء تطبيق Telegram
    application = Application.builder().token(TOKEN).build()
    
    # إضافة معالج للاستعلامات (CallbackQuery)
    application.add_handler(CallbackQueryHandler(activate_code_prompt, pattern="^activate_code$"))
    
    # أضف معالج لأمر التحقق من الكود
    application.add_handler(CommandHandler("checkcode", debug_check_code_command))
    
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
            GENERATE_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_generate_code)
            ],
            REMOVE_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_user)
            ],
            SET_USER_DURATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_duration)
            ],
            ACTIVATE_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code_activation)
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
    
    print("✅ Telegram Bot with Code Subscription System ready ...")
    print("📡 Bot Obeida Trading online with admin panel and code system...")
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
