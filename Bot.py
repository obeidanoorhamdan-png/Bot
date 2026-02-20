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
SUBSCRIPTION_DB_NAME = "subscription_system.db"  # قاعدة بيانات نظام الاشتراك الجديدة
DATA_VERSION = "2.0.0"  # نسخة البيانات للحماية من التغييرات

# معرف المشرف الرئيسي
ADMIN_ID = 6207431030

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

# ============================================
# نظام الحماية والنسخ الاحتياطي المتقدم (NEW)
# ============================================

class DataProtectionAdvanced:
    """
    نظام حماية البيانات والنسخ الاحتياطي المتقدم
    - نسخ احتياطي تلقائي متعدد المواقع
    - تشفير البيانات الحساسة
    - استعادة البيانات تلقائياً عند إعادة التشغيل
    - حماية ضد الحذف العرضي
    """
    
    BACKUP_INTERVAL = 1800  # 30 دقيقة
    MAX_BACKUPS = 50  # الاحتفاظ بآخر 50 نسخة
    ENCRYPTION_KEY = hashlib.sha256(str(ADMIN_ID).encode()).hexdigest()[:32]
    
    @staticmethod
    def encrypt_data(data):
        """تشفير البيانات الحساسة (تشفير بسيط)"""
        try:
            if isinstance(data, str):
                data = data.encode()
            elif not isinstance(data, bytes):
                data = str(data).encode()
            
            # تشفير بسيط (للحماية الأساسية)
            key = DataProtectionAdvanced.ENCRYPTION_KEY.encode()
            encrypted = bytearray()
            for i, byte in enumerate(data):
                encrypted.append(byte ^ key[i % len(key)])
            return base64.b64encode(bytes(encrypted)).decode()
        except Exception as e:
            print(f"⚠️ خطأ في التشفير: {e}")
            return data
    
    @staticmethod
    def decrypt_data(encrypted_data):
        """فك تشفير البيانات"""
        try:
            if isinstance(encrypted_data, str):
                encrypted_bytes = base64.b64decode(encrypted_data.encode())
            else:
                encrypted_bytes = base64.b64decode(encrypted_data)
            
            key = DataProtectionAdvanced.ENCRYPTION_KEY.encode()
            decrypted = bytearray()
            for i, byte in enumerate(encrypted_bytes):
                decrypted.append(byte ^ key[i % len(key)])
            return bytes(decrypted).decode()
        except Exception:
            return encrypted_data
    
    @staticmethod
    def backup_databases_secure():
        """نسخ احتياطي آمن متعدد المواقع"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_id = str(uuid.uuid4())[:8]
        
        databases = [
            (DB_NAME, "main"),
            (SUBSCRIPTION_DB_NAME, "subscription")
        ]
        
        backup_info = {
            'timestamp': timestamp,
            'backup_id': backup_id,
            'version': DATA_VERSION,
            'files': []
        }
        
        for db_path, db_type in databases:
            if os.path.exists(db_path):
                try:
                    # 1. نسخة احتياطية محلية
                    local_backup = os.path.join(BACKUP_DIR, f"{db_type}_backup_{timestamp}_{backup_id}.db")
                    shutil.copy2(db_path, local_backup)
                    
                    # 2. نسخة احتياطية JSON للبيانات المهمة
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    
                    # تصدير جميع الجداول إلى JSON
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = cursor.fetchall()
                    
                    json_backup = {
                        'timestamp': timestamp,
                        'backup_id': backup_id,
                        'db_type': db_type,
                        'tables': {}
                    }
                    
                    for table in tables:
                        table_name = table[0]
                        cursor.execute(f"SELECT * FROM {table_name}")
                        columns = [description[0] for description in cursor.description]
                        rows = cursor.fetchall()
                        
                        # تحويل الصفوف إلى قواميس
                        table_data = []
                        for row in rows:
                            row_dict = {}
                            for i, col in enumerate(columns):
                                value = row[i]
                                if isinstance(value, (int, float, str, type(None))):
                                    row_dict[col] = value
                                else:
                                    row_dict[col] = str(value)
                            table_data.append(row_dict)
                        
                        json_backup['tables'][table_name] = table_data
                    
                    conn.close()
                    
                    # حفظ نسخة JSON
                    json_backup_path = os.path.join(BACKUP_DIR, f"{db_type}_json_{timestamp}_{backup_id}.json")
                    with open(json_backup_path, 'w', encoding='utf-8') as f:
                        json.dump(json_backup, f, ensure_ascii=False, indent=2)
                    
                    # 3. نسخة احتياطية مشفرة للبيانات الحساسة
                    if db_type == 'subscription':
                        encrypted_backup_path = os.path.join(BACKUP_DIR, f"{db_type}_encrypted_{timestamp}_{backup_id}.enc")
                        with open(db_path, 'rb') as src:
                            with open(encrypted_backup_path, 'wb') as dst:
                                dst.write(base64.b64encode(src.read()))
                    
                    backup_info['files'].append({
                        'type': db_type,
                        'local': local_backup,
                        'json': json_backup_path,
                        'encrypted': encrypted_backup_path if db_type == 'subscription' else None
                    })
                    
                    print(f"✅ تم إنشاء نسخة احتياطية آمنة لـ {db_type}: {local_backup}")
                    
                except Exception as e:
                    print(f"⚠️ خطأ في نسخ {db_path}: {e}")
        
        # حفظ معلومات النسخة الاحتياطية
        info_path = os.path.join(BACKUP_DIR, f"backup_info_{timestamp}_{backup_id}.json")
        with open(info_path, 'w', encoding='utf-8') as f:
            json.dump(backup_info, f, ensure_ascii=False, indent=2)
        
        # تنظيف النسخ القديمة
        DataProtectionAdvanced.cleanup_old_backups()
        
        return backup_info
    
    @staticmethod
    def cleanup_old_backups():
        """تنظيف النسخ الاحتياطية القديمة - الاحتفاظ بآخر MAX_BACKUPS نسخة"""
        try:
            all_backups = []
            for filename in os.listdir(BACKUP_DIR):
                if filename.endswith('.db') or filename.endswith('.json') or filename.endswith('.enc'):
                    filepath = os.path.join(BACKUP_DIR, filename)
                    all_backups.append((filepath, os.path.getmtime(filepath)))
            
            # ترتيب حسب التاريخ (الأحدث أولاً)
            all_backups.sort(key=lambda x: x[1], reverse=True)
            
            # الاحتفاظ بآخر MAX_BACKUPS * 3 (لأن كل نسخة لها 3 ملفات)
            keep_count = DataProtectionAdvanced.MAX_BACKUPS * 3
            if len(all_backups) > keep_count:
                for filepath, _ in all_backups[keep_count:]:
                    try:
                        os.remove(filepath)
                        print(f"🗑️ تم حذف نسخة احتياطية قديمة: {os.path.basename(filepath)}")
                    except Exception as e:
                        print(f"⚠️ خطأ في حذف {filepath}: {e}")
        except Exception as e:
            print(f"⚠️ خطأ في تنظيف النسخ القديمة: {e}")
    
    @staticmethod
    def restore_latest_backup():
        """استعادة أحدث نسخة احتياطية عند بدء التشغيل"""
        try:
            # البحث عن أحدث نسخة احتياطية
            backups = []
            for filename in os.listdir(BACKUP_DIR):
                if filename.startswith('main_backup_') and filename.endswith('.db'):
                    filepath = os.path.join(BACKUP_DIR, filename)
                    backups.append((filepath, os.path.getmtime(filepath)))
            
            if not backups:
                print("📭 لا توجد نسخ احتياطية سابقة")
                return False
            
            # اختيار أحدث نسخة
            backups.sort(key=lambda x: x[1], reverse=True)
            latest_backup = backups[0][0]
            
            # استعادة قاعدة البيانات الرئيسية
            if os.path.exists(DB_NAME):
                # إنشاء نسخة احتياطية قبل الاستعادة (للحماية)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                pre_restore = os.path.join(BACKUP_DIR, f"pre_restore_main_{timestamp}.db")
                shutil.copy2(DB_NAME, pre_restore)
                print(f"💾 تم حفظ نسخة قبل الاستعادة: {pre_restore}")
            
            shutil.copy2(latest_backup, DB_NAME)
            print(f"✅ تم استعادة قاعدة البيانات الرئيسية من: {latest_backup}")
            
            # البحث عن أحدث نسخة للاشتراكات
            sub_backups = []
            for filename in os.listdir(BACKUP_DIR):
                if filename.startswith('subscription_backup_') and filename.endswith('.db'):
                    filepath = os.path.join(BACKUP_DIR, filename)
                    sub_backups.append((filepath, os.path.getmtime(filepath)))
            
            if sub_backups:
                sub_backups.sort(key=lambda x: x[1], reverse=True)
                latest_sub_backup = sub_backups[0][0]
                
                if os.path.exists(SUBSCRIPTION_DB_NAME):
                    pre_restore_sub = os.path.join(BACKUP_DIR, f"pre_restore_sub_{timestamp}.db")
                    shutil.copy2(SUBSCRIPTION_DB_NAME, pre_restore_sub)
                
                shutil.copy2(latest_sub_backup, SUBSCRIPTION_DB_NAME)
                print(f"✅ تم استعادة قاعدة بيانات الاشتراكات من: {latest_sub_backup}")
            
            return True
            
        except Exception as e:
            print(f"⚠️ خطأ في استعادة النسخة الاحتياطية: {e}")
            return False
    
    @staticmethod
    def export_users_to_json():
        """تصدير بيانات المستخدمين إلى JSON للحفظ الدائم"""
        try:
            users_data = {
                'export_date': datetime.now().isoformat(),
                'version': DATA_VERSION,
                'users': []
            }
            
            conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT u.user_id, u.username, u.first_name, u.created_at, u.is_blocked,
                       a.subscription_type, a.subscription_name, a.start_date, a.end_date, a.is_active
                FROM users u
                LEFT JOIN active_subscriptions a ON u.user_id = a.user_id
                ORDER BY u.created_at DESC
            """)
            
            for row in cursor.fetchall():
                user = {
                    'user_id': row[0],
                    'username': row[1],
                    'first_name': row[2],
                    'created_at': row[3],
                    'is_blocked': row[4],
                    'subscription': {
                        'type': row[5],
                        'name': row[6],
                        'start_date': row[7],
                        'end_date': row[8],
                        'is_active': row[9]
                    }
                }
                users_data['users'].append(user)
            
            conn.close()
            
            # حفظ في ملف JSON دائم
            json_path = os.path.join(BACKUP_DIR, f"users_export_{datetime.now().strftime('%Y%m%d')}.json")
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(users_data, f, ensure_ascii=False, indent=2)
            
            print(f"✅ تم تصدير بيانات المستخدمين إلى: {json_path}")
            
            # إنشاء نسخة في مكان آمن (يمكن تعديل المسار)
            try:
                # محاولة حفظ نسخة في مسار مطلق آمن
                secure_path = os.path.join(os.path.expanduser('~'), 'obeida_backups')
                if not os.path.exists(secure_path):
                    os.makedirs(secure_path)
                
                secure_json = os.path.join(secure_path, f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
                shutil.copy2(json_path, secure_json)
                print(f"✅ تم حفظ نسخة آمنة في: {secure_json}")
            except Exception as e:
                print(f"⚠️ لا يمكن حفظ النسخة الآمنة: {e}")
            
            return json_path
            
        except Exception as e:
            print(f"⚠️ خطأ في تصدير المستخدمين: {e}")
            return None
    
    @staticmethod
    def import_users_from_json(json_path=None):
        """استيراد بيانات المستخدمين من JSON (للبوت الجديد)"""
        try:
            if json_path is None:
                # البحث عن أحدث ملف تصدير
                json_files = []
                for filename in os.listdir(BACKUP_DIR):
                    if filename.startswith('users_export_') and filename.endswith('.json'):
                        json_files.append(os.path.join(BACKUP_DIR, filename))
                
                if not json_files:
                    print("📭 لا توجد ملفات تصدير")
                    return False
                
                json_files.sort(key=os.path.getmtime, reverse=True)
                json_path = json_files[0]
            
            with open(json_path, 'r', encoding='utf-8') as f:
                users_data = json.load(f)
            
            print(f"📦 جاري استيراد {len(users_data['users'])} مستخدم...")
            
            conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
            cursor = conn.cursor()
            
            imported = 0
            for user in users_data['users']:
                try:
                    # إضافة المستخدم
                    cursor.execute("""
                        INSERT OR REPLACE INTO users 
                        (user_id, username, first_name, created_at, is_blocked)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        user['user_id'],
                        user['username'],
                        user['first_name'],
                        user['created_at'],
                        user['is_blocked']
                    ))
                    
                    # إضافة الاشتراك إذا وجد
                    if user['subscription'] and user['subscription']['type']:
                        cursor.execute("""
                            INSERT INTO active_subscriptions 
                            (user_id, subscription_type, subscription_name, start_date, end_date, is_active)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            user['user_id'],
                            user['subscription']['type'],
                            user['subscription']['name'],
                            user['subscription']['start_date'],
                            user['subscription']['end_date'],
                            user['subscription']['is_active']
                        ))
                    
                    imported += 1
                except Exception as e:
                    print(f"⚠️ خطأ في استيراد المستخدم {user['user_id']}: {e}")
            
            conn.commit()
            conn.close()
            
            print(f"✅ تم استيراد {imported} مستخدم بنجاح")
            return True
            
        except Exception as e:
            print(f"⚠️ خطأ في استيراد المستخدمين: {e}")
            return False
    
    @staticmethod
    def create_restore_point():
        """إنشاء نقطة استعادة كاملة للنظام"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            restore_point = os.path.join(BACKUP_DIR, f"restore_point_{timestamp}")
            os.makedirs(restore_point, exist_ok=True)
            
            # نسخ جميع قواعد البيانات
            for db in [DB_NAME, SUBSCRIPTION_DB_NAME]:
                if os.path.exists(db):
                    shutil.copy2(db, os.path.join(restore_point, db))
            
            # نسخ مجلد الصور المؤقتة
            if os.path.exists(IMAGE_CACHE_DIR):
                shutil.copytree(IMAGE_CACHE_DIR, os.path.join(restore_point, IMAGE_CACHE_DIR))
            
            # حفظ معلومات نقطة الاستعادة
            info = {
                'timestamp': timestamp,
                'version': DATA_VERSION,
                'files': os.listdir(restore_point)
            }
            
            with open(os.path.join(restore_point, 'info.json'), 'w') as f:
                json.dump(info, f, indent=2)
            
            print(f"✅ تم إنشاء نقطة استعادة: {restore_point}")
            return restore_point
            
        except Exception as e:
            print(f"⚠️ خطأ في إنشاء نقطة استعادة: {e}")
            return None
    
    @staticmethod
    def verify_database_integrity():
        """التحقق من سلامة قواعد البيانات وإصلاحها إن أمكن"""
        try:
            for db_path in [DB_NAME, SUBSCRIPTION_DB_NAME]:
                if not os.path.exists(db_path):
                    print(f"⚠️ قاعدة البيانات غير موجودة: {db_path}")
                    continue
                
                try:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    
                    # فحص سلامة قاعدة البيانات
                    cursor.execute("PRAGMA integrity_check")
                    result = cursor.fetchone()
                    
                    if result and result[0] == 'ok':
                        print(f"✅ قاعدة البيانات سليمة: {db_path}")
                    else:
                        print(f"⚠️ قاعدة البيانات تالفة: {db_path}")
                        
                        # محاولة الإصلاح
                        cursor.execute("VACUUM")
                        cursor.execute("REINDEX")
                        print(f"🔧 تم محاولة إصلاح: {db_path}")
                    
                    conn.close()
                    
                except Exception as e:
                    print(f"⚠️ خطأ في فحص {db_path}: {e}")
                    
                    # محاولة استعادة من النسخ الاحتياطي
                    print(f"🔄 جاري استعادة {db_path} من النسخ الاحتياطي...")
                    DataProtectionAdvanced.restore_latest_backup()
                    
        except Exception as e:
            print(f"⚠️ خطأ في التحقق من سلامة البيانات: {e}")

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
    "ليبيا 🇾🇱": "Africa/Tripoli",
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
IMAGE_QUALITY = 85  # جودة الصورة بعد الضغط (من 0-100)

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
            
            # إنشاء نسخة احتياطية فورية بعد إضافة اشتراك مهم
            DataProtectionAdvanced.backup_databases_secure()
            DataProtectionAdvanced.export_users_to_json()
            
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
            
            # إنشاء نسخة احتياطية
            DataProtectionAdvanced.backup_databases_secure()
            
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
            
            # إنشاء نسخة احتياطية
            DataProtectionAdvanced.backup_databases_secure()
            
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
    """تهيئة قاعدة البيانات الرئيسية مع استعادة النسخ الاحتياطي إن وجد"""
    # محاولة استعادة أحدث نسخة احتياطية
    try:
        if os.path.exists(BACKUP_DIR):
            DataProtectionAdvanced.restore_latest_backup()
    except Exception as e:
        print(f"⚠️ خطأ في استعادة النسخ الاحتياطية: {e}")
    
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
            'country_timezone': "TEXT DEFAULT 'Asia/Gaza'"  # عمود المنطقة الزمنية الجديد
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
    
    # إنشاء نسخة احتياطية أولية وتصدير JSON
    try:
        DataProtectionAdvanced.backup_databases_secure()
        DataProtectionAdvanced.export_users_to_json()
        DataProtectionAdvanced.create_restore_point()
    except Exception as e:
        print(f"⚠️ خطأ في إنشاء النسخ الاحتياطية الأولية: {e}")

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
    """تنظيف الصور القديمة - حذف الملفات الأقدم من 5 دقائق"""
    try:
        current_time = time.time()
        deleted_count = 0
        for filename in os.listdir(IMAGE_CACHE_DIR):
            filepath = os.path.join(IMAGE_CACHE_DIR, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 300:  # 5 دقائق (300 ثانية)
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
    """الحصول على تحليل من Mistral AI API للعملة مع القوانين الجديدة"""
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
    """تحليل صورة الشارت مع القوانين الجديدة والتعديلات الأربعة الجوهرية V9.1"""
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
        
        # الحصول على معلومات السيولة والتوقيت باستخدام المنطقة الزمنية للمستخدم
        session_name, session_time, session_vol, user_tz = get_market_session(user_id)
        local_time = get_localized_time(user_id)
        platform_time = datetime.now(pytz.timezone('Asia/Riyadh'))  # توقيت المنصة (GMT+3)
        
        current_hour = local_time.hour
        current_minute = local_time.minute
        current_second = local_time.second
        
        # تحديد جلسة التداول الحالية بتوقيت المنصة
        platform_hour = platform_time.hour
        platform_minute = platform_time.minute
        
        if 3 <= platform_hour < 11:
            trading_session = "آسيوية"
        elif 11 <= platform_hour < 15:
            trading_session = "لندن (افتتاح)"
        elif 15 <= platform_hour < 19:
            trading_session = "لندن + نيويورك (تداخل)"
        elif 19 <= platform_hour < 23:
            trading_session = "نيويورك (متأخر)"
        else:
            trading_session = "المحيط الهادئ"
        
        # حساب الثواني المتبقية
        seconds_remaining = 60 - current_second
        if candle.startswith('M'):
            candle_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            seconds_remaining = (candle_minutes * 60) - ((current_minute % candle_minutes) * 60 + current_second)
        elif candle.startswith('H'):
            candle_hours = int(candle[1:]) if candle[1:].isdigit() else 1
            minutes_passed = local_time.hour % candle_hours * 60 + current_minute
            seconds_remaining = (candle_hours * 3600) - (minutes_passed * 60 + current_second)

        is_fast_frame = candle.startswith('S')

        if seconds_remaining < (3 if is_fast_frame else 10):
            time_status = "⚡ حالة زخم متفجر (Ignition Phase)"
        else:
            time_status = "✅ توقيت مستقر للتحليل"

        # منطق ديناميكي: في الزخم القوي، الثواني الأخيرة هي "فرصة" وليست "خطراً"
        momentum_boost = True if seconds_remaining <= 10 and candle.startswith('S') else False
        
        candle_closing_status = f"الوقت المتبقي: {seconds_remaining} ثانية"
        if seconds_remaining < 5:
            candle_closing_status += " ⚡ (قرب انفجار السعر - استعد للتبديل)"
        elif seconds_remaining < 15:
            candle_closing_status += " ⚠️ (مرحلة تأكيد الاتجاه)"
        
        # نظام الدرع الأساسي (Fundamental Shield) - بتوقيت المنصة GMT+3
        news_impact = "🟢 منخفض"
        news_warning = ""
        news_risk_multiplier = 1.0
        
        # التحقق من أن السوق OTC لتعطيل فلاتر الأخبار
        is_otc_market = "OTC" in symbol.upper() if symbol else False
        
        # إذا كان السوق OTC، قم بتعطيل فلاتر الأخبار فوراً
        if is_otc_market:
            news_impact = "⚡ OTC - غير متأثر بالأخبار"
            news_warning = "✅ سوق OTC - تم تعطيل فلاتر الأخبار"
            news_risk_multiplier = 1.0  # لا يوجد تأثير للأخبار في OTC
        else:
            # أوقات الأخبار الاقتصادية الهامة بتوقيت المنصة (GMT+3) - للسوق الحقيقي فقط
            high_impact_hours = [
                # الفوركس والسلع
                (10, 30),  # GDP بريطانيا 10:30 بتوقيت المنصة
                (11, 0),   # مؤشر PMI الخدمات بريطانيا 11:00
                (11, 30),  # مؤشر PMI التصنيع بريطانيا 11:30
                (12, 0),   # مؤشر PMI التصنيع أوروبا 12:00
                (12, 30),  # مؤشر PMI الخدمات أوروبا 12:30
                (15, 0),   # ADP الوظائف الأمريكية 15:00
                (15, 30),  # الناتج المحلي الأمريكي 15:30
                (15, 30),  # مطالبات البطالة الأمريكية 15:30
                (16, 0),   # مؤشر PMI التصنيع الأمريكي 16:00
                (16, 45),  # مؤشر PMI الخدمات الأمريكي 16:45
                (17, 0),   # مبيعات المنازل الأمريكية 17:00
                (17, 30),  # مخزونات النفط 17:30
                
                # اجتماعات الفائدة
                (20, 0),   # الفائدة الأمريكية (أيام الأربعاء) 20:00
                (14, 45),  # الفائدة الأوروبية 14:45
                (14, 0),   # الفائدة البريطانية 14:00
                
                # الوظائف الأمريكية (الجمعة الأولى من الشهر)
                (15, 30),  # الوظائف غير الزراعية 15:30
                
                # السلع
                (9, 0),    # بيانات النفط الآسيوية 9:00
                (16, 30),  # بيانات الذهب 16:30
            ]
            
            for news_hour, news_minute in high_impact_hours:
                time_diff = abs((platform_hour * 60 + platform_minute) - (news_hour * 60 + news_minute))
                if time_diff <= 60:
                    news_impact = "🔴 عالي جداً"
                    news_risk_multiplier = 2.5
                    news_warning = f"⚠️ **تحذير:** خبر اقتصادي قوي خلال ±60 دقيقة (بتوقيت المنصة)"
                    break
                elif time_diff <= 120:
                    news_impact = "🟡 متوسط"
                    news_risk_multiplier = 1.5
                    news_warning = f"📢 **تنبيه:** اقتراب من وقت أخبار مهمة (بتوقيت المنصة)"
                    break
        
        # الفلتر الزمني (Kill Zones) - بتوقيت المنصة GMT+3
        kill_zone_status = ""
        
        # مناطق القتل السعري الصحيحة بتوقيت GMT+3
        # افتتاح لندن: 11:00 - 14:00
        if 11 <= platform_hour < 14:
            kill_zone_status = "⚡ منطقة القتل السعري - افتتاح لندن (11:00-14:00 بتوقيت المنصة)"
        # تداخل لندن + نيويورك: 15:00 - 19:00 (أقوى منطقة)
        elif 15 <= platform_hour < 19:
            kill_zone_status = "💥 منطقة القتل السعري القصوى - تداخل لندن + نيويورك (15:00-19:00 بتوقيت المنصة)"
        # افتتاح نيويورك: 15:00 - 18:00
        elif 15 <= platform_hour < 18:
            kill_zone_status = "⚡ منطقة القتل السعري - افتتاح نيويورك (15:00-18:00 بتوقيت المنصة)"
        # إغلاق لندن + نيويورك: 19:00 - 20:00
        elif 19 <= platform_hour < 20:
            kill_zone_status = "⚠️ منطقة إغلاق الجلسات - تقلبات عالية (19:00-20:00 بتوقيت المنصة)"
        # الجلسة الآسيوية: 03:00 - 11:00
        elif 3 <= platform_hour < 11:
            kill_zone_status = "🌏 جلسة آسيوية - سيولة منخفضة (03:00-11:00 بتوقيت المنصة)"
        # جلسة المحيط الهادئ: 23:00 - 03:00
        elif 23 <= platform_hour < 24 or 0 <= platform_hour < 3:
            kill_zone_status = "🌊 جلسة المحيط الهادئ - سيولة منخفضة جداً (23:00-03:00 بتوقيت المنصة)"
        else:
            kill_zone_status = "⏰ خارج مناطق القتل الرئيسية - سيولة متوسطة"
        
        # دقائق التحول (Pivot Minutes) - الأوقات الحرجة في التداول بتوقيت المنصة
        # الدقائق التي يحدث فيها انعكاسات قوية: 00, 15, 30, 45 (افتتاح شموع جديدة)
        # الدقائق الأخيرة من كل شمعة: 14, 29, 44, 59
        is_critical_minute = platform_minute in [0, 15, 30, 45]  # دقائق افتتاح الشموع
        is_trap_minute = platform_minute in [14, 29, 44, 59]      # دقائق الغدر (آخر دقيقة)
        
        if is_trap_minute:
            last_minute_status = "⚠️ دقيقة الغدر - حظر الدخول إلا مع اختراق حقيقي"
        elif is_critical_minute:
            last_minute_status = "🔄 افتتاح شمعة جديدة - ترقب الانعكاسات"
        else:
            last_minute_status = "✅ وقت مستقر للتحليل"
        
        # تقدير القمم التاريخية (سيتم استخراجها من الصورة بواسطة الذكاء الاصطناعي)
        estimated_historical_peak = None
        is_near_historical_peak = False
        peak_proximity_points = 999
        wick_ratio = 50  # نسبة افتراضية، سيتم تحديثها من تحليل الصورة
        velocity = 0  # سرعة الحركة الافتراضية
        momentum = 50  # الزخم الافتراضي
        
        # إضافة متغير "مفتاح القوة" لإلغاء القيود
        market_mode = "AGGRESSIVE" if candle.startswith('S') or is_trap_minute else "CONSERVATIVE"
        
        # ============================================
        # إضافة دالة "الحساب الرقمي" (أجهزة الاستشعار) V9.1
        # ============================================
        def calculate_precise_metrics(candle_data):
            """حساب الزخم والسرعة من بيانات الشمعة"""
            try:
                # حساب الزخم: نسبة جسم الشمعة إلى كامل طولها
                full_length = abs(candle_data.get('high', 0) - candle_data.get('low', 0))
                body_length = abs(candle_data.get('close', 0) - candle_data.get('open', 0))
                momentum = (body_length / full_length * 100) if full_length > 0 else 0
                
                # حساب السرعة: مقدار الحركة في آخر وحدة زمنية
                # نفترض شمعة واحدة كمرجع سرعة
                velocity = body_length / 1.0 if body_length > 0 else 0
                
                return momentum, velocity
            except Exception as e:
                print(f"⚠️ خطأ في حساب المقاييس: {e}")
                return 50, 0  # قيم افتراضية في حالة الخطأ
        
        # بيانات افتراضية للشمعة (سيتم استبدالها من تحليل الصورة)
        extracted_candle_info = {
            'high': 0,
            'low': 0,
            'open': 0,
            'close': 0
        }
        
        # ============================================
        # تعديل منطق "المسافة المرنة" (قلب تحديث V9.1)
        # ============================================
        momentum, velocity = calculate_precise_metrics(extracted_candle_info)

        if momentum > 85 and velocity > 1.3:
            required_distance = 0.3  # تقليص المسافة للدخول الفوري في الانفجار
            force_trade = True       # إلغاء حالة "الاحتفاظ"
            time_lock_disabled = True # السماح بالدخول حتى في "دقائق الغدر"
            market_mode = "V9.1_MOMENTUM_SNIPER"  # وضع القناص في الزخم العالي
        else:
            required_distance = 1.5  # المسافة الآمنة في الحالات العادية
            force_trade = False
            time_lock_disabled = False
        
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
            "max_tokens": 1200,
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
        
        # ============================================
        # التعديلات الأربعة الجوهرية - نسخة V8.0
        # ============================================
        
        # [التعديل 1 - قاعدة الـ 2 نقطة الفورية] (The 2-Point Flash Rule)
        two_point_flash_rule = """
        【التعديل 1 - قاعدة الـ 2 نقطة الفورية V8.0 (The 2-Point Flash Rule)】
        - الانتظار حتى إغلاق الشمعة فوق المستوى قد يفوتك الانفجار
        - إذا تجاوز السعر المستوى بـ 1.5-2 نقطة أثناء تكون الشمعة وبسرعة عالية (Volatility > 1.3)
        - يعتبر الاختراق ناجحاً ويتم إصدار أمر دخول 'فوري' دون انتظار إغلاق الشمعة
        - الشرط: سرعة الحركة (Velocity) > 1.3 نقطة في آخر 10 ثوانٍ
        - **لصفقات الشراء (CALL):** تجاوز المقاومة بـ 2 نقطة بسرعة = دخول فوري
        - **لصفقات البيع (PUT):** كسر الدعم بـ 2 نقطة بسرعة = دخول فوري
        """
        
        # [التعديل 2 - فلتر الابتلاع الديناميكي] (Dynamic Kill Switch)
        dynamic_kill_switch = """
        【التعديل 2 - فلتر الابتلاع الديناميكي (Dynamic Kill Switch)】
        - لا تنتظر الشمعة التالية لترى الابتلاع
        - راقب الشمعة الحالية: إذا تراجعت الشمعة الحالية وابتلعت 50% من جسمها بعد أن كانت ممتلئة >70%
        - يتم إلغاء إشارة الدخول فوراً حتى قبل إغلاق الشمعة
        - هذا يحمي من "فتائل الغدر" قبل وقوعها
        - **لصفقات البيع (PUT):** إذا رأيت شمعة حمراء تبتلع أي شمعة خضراء سابقة ولو كانت صغيرة، ادخل بيع فوراً
        - **لصفقات الشراء (CALL):** إذا رأيت شمعة خضراء تبتلع أي شمعة حمراء سابقة، ادخل شراء فوراً
        """
        
        # [التعديل 3 - هرم السيادة الجديد] (Logic Hierarchy - Marubozu Supremacy)
        marubozu_supremacy = """
        【التعديل 3 - هرم السيادة الجديد (Marubozu Supremacy)】
        ⚠️ **قانون التجاوز الطارئ (Emergency Override):**
        
        في حال رصد اندفاع مؤسسي (جميع الشروط التالية):
        - شمعة ممتلئة بنسبة >80% (Marubozu) - تم تخفيضها من 90% لزيادة الفرص
        - عدم وجود ذيول علوية (في الشراء) أو سفلية (في البيع) بنسبة <15%
        - اختراق مستوى مقاومة/دعم رئيسي (مستوى مغلق)
        - سرعة حركة عالية (Volatility > 1.5x المتوسط)
        
        ✅ **يتم تفعيل التجاوز الفوري:**
        1. يتم تعطيل فلتر القمم التاريخية - الانفجار سيخلق قمة جديدة
        2. يتم تعطيل بروتوكول دقائق التحول (دقائق 14,29,44,59)
        3. يتم تعطيل فلتر المسافة - حتى لو كان الهدف على بعد نقطتين فقط
        4. يتم الدخول بمجرد تجاوز المستوى بـ 1.5 نقطة (Flash Entry) دون انتظار الإغلاق
        5. حجم الصفقة: مضاعف (2x) بسبب قوة الإشارة
        
        ⚠️ **هام:** إذا لم تكن الشمعة انفجارية (<80%)، تعود جميع القيود الصارمة للعمل فوراً.
        """
        
        # [التعديل 4 - تحرير دقائق التحول] (Time-Lock Release)
        time_lock_release = """
        【التعديل 4 - تحرير دقائق التحول (Time-Lock Release)】
        - القاعدة القديمة: حظر الدخول في آخر 20 ثانية من الدقائق (14, 29, 44, 59)
        - القاعدة الجديدة: يُسمح بالدخول في 'دقائق الغدر' فقط إذا كان السعر يخترق 'مستوى مغلقاً' بشمعة انفجارية
        - الزخم الحقيقي يمتص تقلبات افتتاح الشموع الجديدة
        - إذا لم يكن هناك اختراق حقيقي، استمر في الحظر
        
        **شروط الدخول في دقائق الغدر:**
        - اختراق مستوى مغلق واضح
        - زخم ≥ 70% (أجسام الشموع)
        - تجاوز المستوى بـ 1.5 نقطة على الأقل
        - سرعة حركة > 1.3x المتوسط
        """
        
        # ============================================
        # بروتوكول V8.0 الهجومي - إضافة فلاتر ذكية
        # ============================================
        v8_protocol = """
        ⚡ **بروتوكول الهجومي - قناص الزخم (Momentum Sniper):**

        【فلتر 1 - كشف السوق العرضي (ADX Filter)】
        - إذا كانت الشموع تتحرك في نطاق ضيق (أقل من 30% من متوسط الحركة)
        - وعدم وجود شموع انفجارية >70%
        - ابقَ في وضع "الانتظار" حتى يخرج السعر من هذا النطاق بقوة
        - الشرط: حجم الشمعة الحالية < 1.3x متوسط آخر 5 شموع = سوق عرضي

        【فلتر 2 - قانون "اللحظة الصفرية" (Zero-Wait Rule)】
        - إذا كانت الشمعة الحالية أكبر من متوسط آخر 3 شموع بـ 1.5x
        - وتتجه لكسر مستوى دعم/مقاومة رئيسي
        - ادخل فوراً (دون انتظار الإغلاق)

        【فلتر 3 - قانون "تجاوز القيعان والقمم" (Floor/Ceiling Bypass)】
        - عند وجود زخم (>75%)، لا تعتبر القمة التاريخية "مقاومة"
        - اعتبرها "هدفاً سيتم كسره" وادخل مع الكسر
        - السعر لن يحترم القمم التاريخية في الزخم القوي

        【فلتر 4 - نسبة الذيل المسموح بها (Wick Ratio V8.0)】
        - نسبة الذيل المسموح بها = 15% (بدلاً من 5%)
        - أي ذيل أقل من 15% = شمعة ممتلئة صالحة للدخول
        - أي ذيل أكثر من 60% = رفض سعري (احتفاظ)
        
        【فلتر 5 - سرعة الحركة (Velocity Filter)】
        - احسب سرعة الحركة: (السعر الحالي - سعر قبل 10 ثوانٍ) / 10
        - إذا كانت السرعة > 1.3 نقطة/ثانية = زخم انفجاري
        - إذا كانت السرعة < 0.5 نقطة/ثانية = سوق هادئ (انتظار)
        """
        
        # ============================================
        # الحلول الجذرية الثلاثة - إضافات V8.1
        # ============================================
        
        historical_peak_protection = """
        ⚠️ **【الحل الجذري 1 - فلتر القمة التاريخية (Historical Peak Filter) V8.1】**

        **القاعدة البرمجية الصارمة:**
        - إذا كان السعر الحالي >= أعلى قمة في آخر 20 شمعة (القمة التاريخية)
        - ومعامل الزخم (Momentum) > 50%
        - القرار الإلزامي: **احتفاظ 🟡 (HOLD)**
        - يمنع تماماً البيع أو الشراء عند القمة التاريخية إلا بشرطين معاً:
          1. ابتلاع 60% من الشمعة السابقة (Engulfing ≥ 60%)
          2. تأكيد بكسر مستوى الدعم/المقاومة المجاور
        
        **منطق التطبيق:**
        if current_price >= historical_peak AND momentum > 50%:
            decision = "HOLD 🟡"
            confidence_score = MIN(confidence_score, 20)  # خفض الثقة فوراً
            market_mode = "SUPER_CONSERVATIVE"
        """
        
        safe_distance_rule = """
        ⚠️ **【الحل الجذري 2 - شرط المسافة الآمنة (1.5 Point Rule) V8.1】**

        **لا تسمح بالدخول بمجرد لمس المستوى:**
        - يجب أن يبتعد السعر عن المستوى المخترق بـ 1.5 نقطة على الأقل
        - هذا يضمن أن الاختراق حقيقي وليس مجرد ذيل لسحب السيولة
        
        **المعادلة الإجبارية:**
        - لصفقات الشراء (CALL): current_price > (resistance_level + 1.5)
        - لصفقات البيع (PUT): current_price < (support_level - 1.5)
        
        **الاستثناء الوحيد (التجاوز الطارئ):**
        - شمعة Marubozu >80% + سرعة حركة > 1.5x المتوسط
        - عندها يُسمح بـ 1 نقطة فقط (وليس 1.5)
        """
        
        confidence_formula_modifier = """
        ⚠️ **【الحل الجذري 3 - تعديل معادلة الثقة (Confidence Formula Modifier) V8.1】**

        **معامل خصم الثقة (Trust Deduction):**
        - إذا كان هناك "فجوة سعرية" (FVG) قريبة عكس الاتجاه ← خصم 40% من نسبة الثقة فوراً
        - إذا كان هناك "قمة سيولة" قريبة عكس الاتجاه ← خصم 40% من نسبة الثقة فوراً
        - إذا كان السعر في "منطقة صراع" (نطاق القمة التاريخية ±3 نقاط) ← خصم 50% من نسبة الثقة
        
        **المعادلة الجديدة:**
        final_confidence = base_confidence × (1 - Σ penalties)
        where penalties = 0.4 for FVG, 0.4 for Liquidity Peak, 0.5 for Conflict Zone
        """
        
        peak_zone_filter = """
        ⚠️ **【إضافة - فلتر المنطقة المحظورة (Historical Peak Zone) V8.1】**

        **كشف منطقة الخطر:**
        - إذا كان السعر يقترب من أعلى قمة تاريخية (بفارق أقل من 3 نقاط)
        - يتم تحويل market_mode تلقائياً من AGGRESSIVE إلى SUPER_CONSERVATIVE
        - يتم إجبار الكود على طلب تأكيد مزدوج:
          1. تأكيد من فريم أعلى (Higher Timeframe Confirmation)
          2. انتظار شمعتين تأكيد بعد الاختراق
        
        **المنطق:**
        if abs(current_price - historical_peak) < 3:
            market_mode = "SUPER_CONSERVATIVE"
            require_double_confirmation = True
        """
        
        institutional_drive_sensor = """
        ⚠️ **【إضافة - مستشعر الاندفاع المؤسسي (Institutional Drive Sensor) V8.1】**

        **القاعدة الذهبية:**
        - إذا كانت الشموع المتجهة للقمة "ممتلئة" (نسبة الذيول < 15%)
        - والسعر يقترب من القمة التاريخية
        - يمنع البيع نهائياً لأن هذا "اندفاع مؤسسي" وليس مجرد تذبذب
        
        **المنطق:**
        if wick_ratio < 15% AND approaching_historical_peak:
            SELL_SIGNAL = FORBIDDEN  # يمنع البيع تماماً
            confidence_score = 0  # ثقة معدومة في البيع
        """
        
        # ============================================
        # التعديلات النهائية V8.5 - Elite Logic
        # ============================================
        
        peak_blind_spot = """
        ⚠️ **【قاعدة المنطقة العمياء (The Peak Blind Spot) V8.5】**

        **قاعدة إلزامية لا تُنتهك:**
        يُمنع منعاً باتاً إصدار إشارة (بيع/شراء) إذا كان السعر عند قمة أو قاع تاريخي إلا بعد تحقق تأكيد الاندفاع (1.5 نقطة).
        
        **التطبيق الإجباري:**
        - إذا كان السعر يلمس المستوى فقط دون الابتعاد عنه بـ 1.5 نقطة
        - يجب أن يكون القرار **احتفاظ 🟡 (HOLD)** حصراً
        - السبب: السوق هنا غير واضح ويُحتمل أن يكون فخاً للسيولة
        
        **استثناء وحيد:**
        - شمعة Marubozu >85% تكسر المستوى بـ 0.5 نقطة مع سرعة > 2.0x المتوسط
        - عندها يتم تفعيل "التجاوز الطارئ" فوراً
        """
        
        rejection_logic = """
        ⚠️ **【منطق المنع (The Rejection Logic) V8.5】**

        **قاعدة الحظر الذكي:**
        - إذا كان الزخم متوسطاً (<80%) والسعر يقترب من مستوى سيولة
        - يُعتبر السوق **غير واضح (UNCLEAR)**
        - يجب على النظام **منع الدخول فوراً** وإصدار قرار احتفاظ
        
        **التبرير الإلزامي في التحليل:**
        - يجب توضيح السبب: "عدم وضوح كسر السيولة"
        - يتم خفض الثقة إلى أقل من 30% تلقائياً
        """
        
        signal_strength_clarity = """
        ⚠️ **【ربط قوة الإشارة بالوضوح (Signal Strength & Clarity) V8.5】**

        **قانون منع الإشارات الوهمية:**
        - لا تُعطِ إشارة **عالية جداً 💥** أبداً إذا كان السعر في منطقة تذبذب أو قمة تاريخية
        - أي إشارة عند هذه المناطق يجب أن تكون **ضعيفة ❄️** فقط
        
        **التصنيف الإلزامي:**
        - السعر في منطقة تذبذب (Sideways) → أقصى قوة إشارة = متوسطة ⚡
        - السعر عند قمة تاريخية → أقصى قوة إشارة = ضعيفة ❄️
        - السعر في منطقة صراع (±1.5 نقطة من القمة) → القرار = احتفاظ 🟡
        
        **الهدف:** حتى يخرج السعر من منطقة الخطر بمسافة آمنة (≥ 1.5 نقطة)
        """
        
        exit_zone_rule = """
        ⚠️ **【قانون الهروب من منطقة الصراع (The Exit Zone Rule) V8.5】**

        **قاعدة الحظر المطلق:**
        يُمنع إصدار أي إشارة (بيع/شراء) طالما السعر داخل نطاق **±1.5 نقطة** من قمة أو قاع تاريخي.
        
        **الاستثناء الوحيد:**
        - إذا كانت الشمعة **انفجارية** (Marubozu >85%)
        - وكسرت المستوى بـ 0.5 نقطة
        - يتم تفعيل "التجاوز الطارئ" فوراً
        
        **التطبيق:**
        if abs(current_price - historical_peak) <= 1.5:
            if is_marubozu and marubozu_ratio > 85% and (current_price > historical_peak + 0.5):
                ACTIVATE_EMERGENCY_OVERRIDE()
            else:
                DECISION = "HOLD 🟡"
        """
        
        smart_hold_protocol = """
        ⚠️ **【بروتوكول احتفاظ 🟡 الذكي (Smart HOLD Protocol) V8.5】**

        **شروط إخراج قرار الاحتفاظ:**
        يتم إخراج قرار **احتفاظ 🟡** في حالة واحدة فقط:
        
        ✅ **الشرط الدقيق:**
        - السعر عند مستوى سيولة (قمة/قاع تاريخي)
        - **والزخم ضعيف <60%** أو **سرعة الحركة <1.0 نقطة/ثانية**
        
        ❌ **ممنوع الاحتفاظ في حالة:**
        - إذا كان الزخم قوي (>80%) والسعر اخترق القمة بـ ≥ 1.5 نقطة
        - عندها يجب التحول فوراً للقرار المناسب للزخم (شراء/بيع)
        
        **المنطق:**
        if price_at_liquidity_level:
            if momentum < 60% or velocity < 1.0:
                decision = "HOLD 🟡"
            elif momentum > 80% and price > peak + 1.5:
                decision = "BUY 🟢 (Breakout Confirmed)"
        """
        
        anti_counter_trend = """
        ⚠️ **【فلتر منع الانتحار العكسي (Anti-Counter Trend Filter) V8.5】**

        **قانون حماية رأس المال:**
        إذا كان السعر يقترب من قمة تاريخية، يُحظر تماماً إصدار إشارة **"بيع"** إلا بعد ظهور:
        
        ✅ **شرط البيع الوحيد:**
        - ظهور **ابتلاع ديناميكي** (Dynamic Engulfing)
        - يغلق أسفل القمة بـ **1.5 نقطة على الأقل**
        
        **المنطق:**
        if approaching_historical_peak:
            if not (dynamic_engulfing and current_price < peak - 1.5):
                SELL_SIGNAL = FORBIDDEN  # يمنع البيع تماماً
        
        **التبرير:**
        هذا التعديل يضمن أنك لن تبيع أبداً أمام "قطار صاعد" (Strong Uptrend)
        """
        
        # ============================================
        # إضافات V9.1 - منع ضياع الصفقات
        # ============================================
        
        v9_1_additions = f"""
⚡ **【إضافات V9.1 - منع ضياع الصفقات】**

**1. تقليص مسافة الاختراق (Breakout Distance Reduction):**
- في الحالات العادية: مسافة الاختراق المطلوبة = 1.5 نقطة
- **في حالة الزخم الانفجاري (Momentum > 85% & Velocity > 1.3):**
  - مسافة الاختراق المطلوبة = {required_distance} نقطة فقط
  - السبب: الدخول فور بداية الاختراق قبل أن يطير السعر بعيداً

**2. مستشعر السرعة اللحظية (Velocity Sensor):**
- السرعة المحسوبة: {velocity:.2f} نقطة/ثانية
- عتبة السرعة للتفعيل: > 1.3 نقطة/ثانية
- إذا كانت السرعة > 1.3: يتم إلغاء جميع قيود الحظر والدخول فوراً

**3. قاعدة "إلغاء الاحتفاظ" الإجباري (Force Execution):**
- الوضع الحالي: {"✅ مفعل - تم إلغاء الاحتفاظ" if force_trade else "❌ غير مفعل - الاحتفاظ مسموح"}
- إذا كان الزخم > 85% والسرعة > 1.3: يمنع إصدار قرار "احتفاظ 🟡" تماماً
- السبب: في الزخم العالي، "الاحتفاظ" يُعتبر خطأً فنيًا

**4. تعطيل القفل الزمني (Time Lock Disabled):**
- حالة القفل الزمني: {"✅ معطل - مسموح بالدخول في دقائق الغدر" if time_lock_disabled else "❌ مفعل - يطبق حظر دقائق الغدر"}
- عند تفعيل وضع V9.1، يتم تجاهل دقائق الغدر (14,29,44,59) تماماً

**5. نمط السوق المطور (Market Mode):**
- نمط السوق الحالي: {market_mode}
- في وضع V9.1_MOMENTUM_SNIPER: يتم الدخول فوراً مع أول إشارة اختراق
"""
        
        # دمج جميع التعديلات في البرومبت الرئيسي
        four_updates_section = f"""
⚡ **التعديلات الأربعة الجوهرية + بروتوكول الهجومي + الحلول الجذرية V8.1 + التعديلات النهائية V8.5 + إضافات V9.1:**

{two_point_flash_rule}

{dynamic_kill_switch}

{marubozu_supremacy}

{time_lock_release}

{v8_protocol}

{historical_peak_protection}

{safe_distance_rule}

{confidence_formula_modifier}

{peak_zone_filter}

{institutional_drive_sensor}

{peak_blind_spot}

{rejection_logic}

{signal_strength_clarity}

{exit_zone_rule}

{smart_hold_protocol}

{anti_counter_trend}

{v9_1_additions}

🔍 **كيفية تطبيق التعديلات في التحليل (V9.1 ULTIMATE):**

1. **الفحص الأولي - منطقة الخطر:**
   - هل السعر داخل نطاق ±1.5 نقطة من قمة/قاع تاريخي؟
   - نعم → احتفاظ 🟡 (إلا مع Marubozu >85% + كسر 0.5 نقطة)

2. **تحديد نوع السوق:**
   - هل السوق OTC؟ → تعطيل فلاتر الأخبار تماماً
   - هل السوق عرضي؟ → تفعيل وضع "الانتظار" حتى الخروج من النطاق
   - هل هناك زخم >80% مع اختراق القمة بـ 1.5 نقطة؟ → شراء/بيع مع الاتجاه

3. **عند رصد اندفاع مؤسسي (Marubozu >85%):**
   - فعّل "قانون التجاوز الطارئ" فوراً (يسمح بكسر 0.5 نقطة فقط)
   - تجاهل دقائق الغدر، فلتر المسافة
   - أدخل فوراً

4. **في حالة الزخم المتوسط (50-80%):**
   - طبق قاعدة الـ 2 نقطة الفورية
   - التزم بشرط المسافة المطلوبة ({required_distance} نقطة)
   - راقب الابتلاع الديناميكي

5. **منطق الاحتفاظ الذكي:**
   - احتفاظ 🟡 = السعر عند مستوى سيولة + (زخم <60% أو سرعة <1.0)
   - إذا كان الزخم >85% والسرعة >1.3 → **يمنع الاحتفاظ إجبارياً** (Force Trade = ON)

6. **منع البيع العكسي:**
   - عند اقتراب السعر من قمة تاريخية → يمنع البيع إلا بابتلاع ديناميكي + إغلاق -1.5 نقطة

7. **بروتوكول V9.1 - القناص اللحظي:**
   - إذا كان الزخم >85% والسرعة >1.3:
     * المسافة المطلوبة = 0.3 نقطة فقط
     * القفل الزمني معطل (يسمح بالدخول في دقائق الغدر)
     * يمنع الاحتفاظ إجبارياً
     * الدخول فوري مع أول إشارة اختراق
"""
        
        # تعديل "قرار القمة التاريخية" - إضافة شرط force_trade
        historical_peak_decision_modifier = """
⚠️ **【تعديل قرار القمة التاريخية V9.1】**

إذا كان هناك زخم انفجاري (force_trade = True) والسعر الحالي > (القمة التاريخية + 0.3):
    القرار النهائي = "شراء 🟢"
    يتم تعزيز الثقة +20% لأن الاختراق مدعوم بزخم حقيقي
"""
        
        # البرومبت الرئيسي مع جميع التعديلات
        MAIN_PROMPT = f"""
أنت محلل فني خبير متكامل في SMC + ICT + WYCKOFF + VOLUME PROFILE + MARKET PSYCHOLOGY.
مهمتك تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية دقيقة بنظام متعدد الطبقات مع تطبيق **قوانين السيادة الجديدة V9.1 ULTIMATE**.

🎯 **التوجيه الأساسي: التركيز الكامل على استخراج البيانات وتحليل الصورة**
أنت ملزم بالتركيز التام على الصورة المرفقة فقط. يجب أن تستخرج كل البيانات والأرقام من الصورة بكامل قدراتك البصرية. لا تتشتت بأي معلومات خارجية أو افتراضات. كل ما تحتاجه موجود في الصورة:

1. **استخراج الأسعار بدقة مطلقة:** اقرأ الأسعار من المحور اليميني (Y-axis) بدقة بكسلية
2. **تحديد الزمن الحالي:** ابحث عن ساعة المنصة في الزاوية اليسرى/اليمنى السفلية - **هذا هو المرجع الوحيد لدقائق الغدر**
3. **تحليل الشموع:** افحص آخر 5-7 شموع (الأجسام، الذيول، الألوان، الأحجام)
4. **رسم المستويات:** حدد الدعم/المقاومة، الفجوات، مناطق السيولة، القمم والقيعان التاريخية
5. **التطبيق العملي:** طبق كل القواعد أدناه على البيانات المستخرجة من الصورة فقط

{previous_context_info}

{four_updates_section}

{historical_peak_decision_modifier}

⚡ **الملخص التنفيذي V9.1 ULTIMATE:**

🛠️ **1. فلتر سحب السيولة (Liquidity Sweep Filter):**
قبل أي قرار شراء عند القمة، ابحث عن قمة تاريخية قريبة. إذا لمسها السعر وارتد بفتيلة أو ظهرت شمعة حمراء سريعة، يتم إلغاء الشراء فوراً والتحول للبيع (انعكاس). هذا "فخ سيولة" لاستدراج المشترين قبل الهبوط.

📊 **2. الزخم المشروط (Conditional Momentum):**
لا يكفي أن تكون الشموع قوية (3 شموع >75%)؛ يجب أن يكون الطريق أمامها خالياً من "فخاخ السيولة" أو "الأرقام المستديرة" غير الملموسة. إذا كانت هناك قمة سيولة قريبة، الزخم ملغي.

⚡ **3. قانون الابتلاع القاتل (The Kill Switch - Rule 8):**
بمجرد ظهور شمعة عكسية تبتلع 50% من الشمعة الاندفاعية السابقة، يُعتبر الزخم "منتهياً" أو "خداعياً" ويمنع الدخول مع الاتجاه القديم. السعر الآن في مرحلة تغيير هيكل (Market Structure Shift).

⏰ **4. بروتوكول دقائق التحول (Pivot Minutes):**
يمنع الدخول تماماً في الثواني الأخيرة (آخر 20 ثانية) من الدقائق الحرجة (14، 29، 44، 59) لتجنب "غدر" افتتاح الشمعة الجديدة. الاستثناء الوحيد: اختراق حقيقي لمستوى مغلق بشمعة انفجارية أو **تفعيل وضع V9.1 (Momentum >85% + Velocity >1.3)**.

🧲 **5. مغناطيس الفجوات (FVG Magnet):**
إذا ترك السعر خلفه فجوة سعرية كبيرة (>5 نقاط)، يرفض البوت الدخول عند القمة، وينتظر عودة السعر لملء 50% من الفجوة (منطقة الخصم) لضمان دخول آمن. لا تشتري أبداً عند رأس الانفجار.

✅ **6. تأكيد الاندفاع (Momentum Confirmation) V9.1:**
لا يعتبر الاختراق حقيقياً إلا إذا:
- تجاوز السعر المستوى المخترق بـ **{required_distance} نقطة** على الأقل
- أو كان Marubozu >85% وتجاوز بـ 0.5 نقطة (تجاوز طارئ)
- أو كان Momentum >85% و Velocity >1.3 → مسافة {required_distance} نقطة فقط

⚠️ **7. قاعدة المنطقة العمياء (Peak Blind Spot) V8.5:**
يُمنع منعاً باتاً إصدار إشارة (بيع/شراء) إذا كان السعر عند قمة أو قاع تاريخي دون تأكيد الاندفاع ({required_distance} نقطة). القرار الإجباري = احتفاظ 🟡 (إلا مع تفعيل Force Trade)

⚠️ **8. منطق المنع (Rejection Logic) V8.5:**
إذا كان الزخم متوسطاً (<80%) والسعر يقترب من مستوى سيولة، يُعتبر السوق غير واضح ويُمنع الدخول فوراً مع توضيح السبب "عدم وضوح كسر السيولة".

⚠️ **9. ربط قوة الإشارة بالوضوح V8.5:**
- السعر في منطقة تذبذب → أقصى قوة إشارة = متوسطة ⚡
- السعر عند قمة تاريخية → أقصى قوة إشارة = ضعيفة ❄️ (إلا مع تفعيل Force Trade)
- السعر في منطقة صراع (±1.5 نقطة من القمة) → القرار = احتفاظ 🟡 (إلا مع تفعيل Force Trade)

⚠️ **10. قانون الهروب من منطقة الصراع (Exit Zone Rule) V9.1:**
يُمنع إصدار أي إشارة طالما السعر داخل نطاق ±1.5 نقطة من قمة/قاع تاريخي. الاستثناءات:
- Marubozu >85% + كسر 0.5 نقطة
- Momentum >85% + Velocity >1.3 + كسر {required_distance} نقطة

⚠️ قاعدة الجوكر المنضبط (Disciplined Joker) V9.1:
- لا يُسمح بتجاهل الوقت (الجوكر) إلا إذا كانت الشمعة تخترق مستوى مغلق.
- يجب أن يتجاوز السعر المستوى المخترق بـ {required_distance} نقطة على الأقل (تأكيد الاندفاع).
- إذا كان الاختراق لقمة تاريخية، انتظر تأكيد الاندفاع بـ {required_distance} نقطة.

📜 **قواعد السيادة العشر V9.1 ULTIMATE:**

⚡ **قانون التجاوز الطارئ (Emergency Override) V9.1:**

في حال رصد اندفاع مؤسسي (جميع الشروط):
✅ شمعة ممتلئة >85% (Marubozu)
✅ عدم وجود ذيول >15%
✅ اختراق مستوى رئيسي
✅ سرعة حركة > 1.5x المتوسط

🚨 **عند التفعيل:**
1. تعطيل جميع القواعد
2. الدخول بمجرد تجاوز السعر المستوى بـ **0.5 نقطة** (بدلاً من 1.5)
3. حجم الصفقة: مضاعف (2x)

⚡ **قاعدة الـ 2 نقطة الفورية (Flash Rule):**
- زخم 50-80% + تجاوز المستوى بـ 2 نقطة + سرعة >1.3 = دخول فوري

⚡ **فلتر الابتلاع الديناميكي (Dynamic Kill Switch):**
- إذا تراجعت الشمعة الحالية وابتلعت 50% من جسمها = إلغاء فوري

⚡ **قانون القناص اللحظي (Momentum Sniper) V9.1:**
- زخم >85% + سرعة >1.3 = دخول فوري عند **{required_distance} نقطة**
- إلغاء الاحتفاظ الإجباري
- تعطيل القفل الزمني

⚡ **هرم الأولويات V9.1:**

【الرتبة 0】 التجاوز الطارئ (Marubozu >85% + كسر 0.5 نقطة)
   ↓
【الرتبة 1】 قانون القناص اللحظي V9.1 (Momentum >85% + Velocity >1.3)
   ↓
【الرتبة 2】 قاعدة الـ 2 نقطة الفورية (زخم متوسط + سرعة)
   ↓
【الرتبة 3】 القواعد القياسية مع الحلول الجذرية V8.5
   ↓
【الرتبة 4】 الانتظار والمراقبة

⚡ **دقائق التحول (Time-Lock Release) V9.1:**
- يُسمح بالدخول في الدقائق الحرجة إذا تحقق:
  * اختراق مستوى مغلق
  * زخم ≥ 70%
  * تجاوز المستوى بـ {required_distance} نقطة (أو 0.5 مع Marubozu >85%)

1. **الزخم المشروط V9.1:**
   آخر 3 شموع >75% بشرط:
   - عدم وجود قمة سيولة قريبة
   - السعر > (القمة + {required_distance} نقطة) للشراء

2. **بروتوكول الانفجار السعري:**
   لا ترفض صفقة في الثواني الأخيرة إذا:
   - زخم >75% + اختراق حقيقي + تجاوز {required_distance} نقطة

3. **فحص الذيول (15/60 Rule):**
   - ذيل <15% = Marubozu Mode
   - ذيل >60% = رفض سعري قطعي

4. **مغناطيس الأرقام المستديرة:**
   انتظر تجاوز الرقم بـ {required_distance} نقطة مع زخم

5. **هندسة السيولة (SMC/ICT):**
   Liquidity Sweep = انعكاس محتمل

6. **معامل الفجوات:**
   انتظر عودة السعر لملء 50% من الفجوة

7. **فلتر سحب السيولة:**
   قمة تاريخية مع ارتداد = إلغاء الشراء

8. **قانون الابتلاع القاتل:**
   شمعة عكسية تبتلع 50% = إلغاء الاتجاه

9. **مغناطيس الفجوات:**
   فجوة >5 نقاط = انتظر عودة 50%

10. **التقييم النهائي:**
    "السيولة تسبق الزخم" - لا تدخل عند القمة دون تأكيد

⚠️ **نظام حساب الثقة المتقدم V9.1 مع معاملات الخصم:**

base_confidence = (زخم_الاتجاه × 30%) + (توافق_الإطارات × 25%) + (وضوح_المستويات × 20%) + (حجم_سيولة × 15%) + (تطابق_أنماط × 10%)

**معاملات الخصم:**
- وجود FVG عكس الاتجاه ← خصم 40%
- وجود قمة سيولة قريبة عكس الاتجاه ← خصم 40%
- السعر في منطقة صراع (±3 نقاط من القمة) ← خصم 50%
- نسبة الذيول <15% + اقتراب من القمة ← خصم 100% للبيع
- السعر داخل نطاق ±1.5 نقطة من القمة ← خصم 80% + احتفاظ إجباري (إلا مع Force Trade)

**معاملات التعزيز V9.1:**
- Momentum >85% + Velocity >1.3 ← تعزيز +40%
- تفعيل Force Trade ← تعزيز +30%
- تعطيل Time Lock ← تعزيز +20%

**مستويات الثقة:**
- 💥 90-100%: ثقة عالية جداً (بشرط عدم وجود قمم قريبة أو مع Force Trade)
- 🔥 71-89%: ثقة عالية
- ⚡ 60-70%: ثقة متوسطة
- ❄️ 40-59%: ثقة منخفضة
- 🚫 أقل من 40%: احتفاظ وانتظار (إلا مع Force Trade)

[1] تنبيه الربط البرمجي الإلزامي - المعطيات المستلمة من المستخدم

📌 **المعطيات الأساسية:**
• توقيت المنصة (GMT+3): {platform_time.strftime('%H:%M:%S')}
• جلسة التداول الحالية: {trading_session}
• إطار الشموع: {candle}
• فريم التحقق: {verification_timeframe}
• استراتيجية التداول: {trading_strategy}
• جلسة السوق: {session_name} ({session_time})
• حالة السيولة: {session_vol}
• تصنيف الفريم: {candle_category}
• حجم الصفقة: {position_sizing}
• نمط السوق: {market_mode}
• وصف التوقيت: {time_status}
• الوضع الديناميكي: {"تجاهل القيود الزمنية وادخل مع الزخم (بشرط اختراق مستوى مغلق)" if momentum_boost else "التزم بالقواعد القياسية"}

📌 **إعدادات V9.1:**
• المسافة المطلوبة للاختراق: {required_distance} نقطة
• إلغاء الاحتفاظ الإجباري: {"✅ مفعل" if force_trade else "❌ غير مفعل"}
• تعطيل القفل الزمني: {"✅ معطل" if time_lock_disabled else "❌ مفعل"}
• السرعة المحسوبة: {velocity:.2f} نقطة/ثانية
• الزخم المقدر: {momentum:.1f}%

📌 **تأثير الأخبار والتوقيت:**
• تأثير الأخبار: {news_impact} (معامل ×{news_risk_multiplier})
• تحذير الأخبار: {news_warning if news_warning else "✅ الوضع آمن"}
• منطقة القتل السعري: {kill_zone_status}
• حالة السوق: {last_minute_status}
• حالة إغلاق الشمعة: {candle_closing_status}
• الوقت المتبقي للإغلاق: {seconds_remaining} ثانية
• توقيت التحليل: {local_time.strftime('%Y-%m-%d %H:%M:%S')} (بتوقيت المستخدم)

📌 **سياق التحليل السابق:**
• {"✅ متوفر" if previous_context_info else "❌ غير متوفر"}

✅ **جميع المتغيرات مستلمة ومعرّفة بشكل صحيح.**

[2] نظام الاستخراج البصري المتقدم

🔍 **المرحلة 1: مسح الصورة الشامل**
1. ابدأ بمسح شامل للصورة من الأعلى إلى الأسفل، ومن اليسار إلى اليمين
2. حدد العناصر الأساسية: محور الأسعار (Y-axis)، محور الزمن (X-axis)، منطقة الشموع، ساعة المنصة
3. استخرج البيانات التالية بالترتيب:
   - **الأسعار:** السعر الحالي، أعلى سعر، أدنى سعر، مستويات الدعم/المقاومة، القمم التاريخية، القيعان التاريخية
   - **الزمن:** الوقت الحالي على الشارت، إطار الشموع ({candle})
   - **الشموع:** آخر 5 شموع (فتحة، إغلاق، أعلى، أدنى، الجسم، الذيول)
   - **المؤشرات:** أي مؤشرات مرئية (إن وجدت)

🔍 **المرحلة 2: التحليل الاستبعادي البصري**
1. **الوصف الإلزامي قبل التحليل:** ابدأ بوصف الشموع الثلاث الأخيرة بدقة:
   • **الشمعة -1:** [جسم/ذيل العلوي/ذيل السفلي]، النسبة: جسم/شمعة = [%]
   • **الشمعة -2:** [جسم/ذيل العلوي/ذيل السفلي]، النسبة: جسم/شمعة = [%]
   • **الشمعة -3:** [جسم/ذيل العلوي/ذيل السفلي]، النسبة: جسم/شمعة = [%]

2. **تحليل الاتجاه الحجمي:** 
   • إذا كانت أحجام الأجسام في ازدياد: ↝ منع أي إشارة عكسية
   • إذا كانت أحجام الأجسام في تناقص: ↝ احتمالية انعكاس

🔍 **المرحلة 3: الفحص الرياضي الدقيق مع معامل الانزلاق السعري**
   • **السعر الحالي الملاحظ:** [قراءة مباشرة من المحور Y]
   • **سعر الهدف الملاحظ:** [قراءة مباشرة من المستوى المستهدف]
   • **المعادلة الحسابية الأساسية:** |السعر الحالي - سعر الهدف| = [النقاط]
   • **المسافة إلى أقرب قمة تاريخية:** [نقاط] - هل هي ≥ {required_distance} نقطة للاختراق الحقيقي؟
   • **هل السعر داخل نطاق ±1.5 نقطة من القمة؟** [نعم/لا]
   • **في فريمات S5, S10:** أضف "معامل الانزلاق السعري" = خصم نقطة أمان واحدة من نقطة الدخول
   • **نقطة الدخول المعدلة:** [السعر الحالي + (نقطة أمان في اتجاه الصفقة)]

🔍 **المرحلة 4: حساب السرعة والزخم V9.1**
   • **سرعة الحركة (Velocity):** احسبها بدقة من الصورة
   • **الزخم (Momentum):** احسبه من أحجام الشموع
   • **تطبيق قوانين V9.1:** إذا كان Momentum >85% و Velocity >1.3:
     - المسافة المطلوبة = 0.3 نقطة
     - Force Trade = مفعل
     - Time Lock = معطل

🔍 **المرحلة 5: كشف التناقضات البصرية ودقة القراءة**
   • إذا كانت المسافة المحسوبة ≠ المسافة المقدرة بالعين → أعد المسح
   • تأكد من تطابق كل ذيل مع رقمه على المحور اليميني
   • **عند قراءة المحور Y، إذا كان الرقم غير واضح، قم بمقارنة موضع الشمعة بمستويات الدعم/المقاومة المرئية الأقرب لتقدير السعر بدقة +/- 1 نقطة.**
   • **إذا فشلت في قراءة السعر بدقة من الصورة، توقف فوراً وأرسل "الصورة غير صالحة للتحليل - السعر غير واضح"**

[3] بروتوكول الفشل الذاتي - توقف فوراً إذا:

1. **ضوضاء بصرية عالية:** مؤشرات كثيرة تغطي الشموع وتحجب الرؤية
2. **محور أسعار مقطوع:** عدم وضوح الأرقام على المحور Y
3. **ساعة منصة غير واضحة:** عدم القدرة على تحديد الوقت بدقة
4. **جودة صورة رديئة:** عدم وضوح تفاصيل الشموع
5. **السعر الحالي غير واضح:** عدم القدرة على قراءة السعر الحالي بدقة

✅ **الإجراء الإلزامي:** "الصورة غير صالحة للتحليل - يرجى إرسال صورة أوضح مع شموع ومحور أسعار واضحين."

[4] قوانين السيادة V9.1 (Omni Priority) 

【الرتبة 0】 قانون التجاوز الطارئ (Emergency Override) - Marubozu >85%
   ✅ إذا تحققت جميع الشروط:
      - شمعة ممتلئة >85% (Marubozu)
      - عدم وجود ذيول >15%
      - اختراق مستوى رئيسي
      - سرعة حركة > 1.5x المتوسط
   ✅ يتم تفعيل التجاوز الفوري: يُسمح بالدخول عند +0.5 نقطة فقط

【الرتبة 1】 قانون القناص اللحظي V9.1 (Momentum Sniper)
   ✅ إذا كان Momentum >85% و Velocity >1.3:
      - المسافة المطلوبة = 0.3 نقطة
      - Force Trade = مفعل
      - Time Lock = معطل
      - دخول فوري عند تحقيق المسافة المطلوبة

【الرتبة 2】 قاعدة المنطقة العمياء (Peak Blind Spot)
   ✅ إذا كان السعر داخل نطاق ±1.5 نقطة من قمة/قاع تاريخي:
      - القرار الإجباري = احتفاظ 🟡
      - (إلا مع تفعيل Force Trade)

【الرتبة 3】 قاعدة الـ 2 نقطة الفورية (2-Point Flash Rule)
   ✅ إذا كان الزخم 50-80% وتجاوز السعر المستوى بـ 2 نقطة بسرعة >1.3
   ✅ دخول فوري دون انتظار إغلاق الشمعة

【الرتبة 4】 الزخم المشروط مع فلتر القمم V9.1
   ✅ إذا كانت آخر 3 شموع >75%، بشرط:
      - عدم وجود قمة سيولة قريبة
      - **السعر > (القمة التاريخية + {required_distance} نقطة) للشراء**
   ✅ إذا ظهرت شمعة عكسية تبتلع 50% = إلغاء

【الرتبة 5】 السيولة المغناطيسية مع فلتر الفخاخ
   ✅ يُمنع الشراء/البيع حتى اختراق حقيقي (≥ {required_distance} نقطة مع زخم)

【الرتبة 6】 الهيكل الزمني - للأسواق العرضية فقط

【الرتبة 7】 الفجوات السعرية - انتظر عودة 50%

【الرتبة 8】 الفتيلة - فقط مع زخم ضعيف <30%

【الرتبة 9】 المؤشرات الفنية - المرتبة الأخيرة

[5] قانون الفتيلة العالمي V9.1:

1. **الذيل الصاعد:** استمرار طالما لم تبتلع الشمعة التالية 50%
2. **الذيل المعاكس:** يحتاج تأكيد بابتلاع 50%
3. **تجاهل الذيول المجهرية:** <15% = Marubozu Mode
4. **نسبة الذيل >60%:** رفض سعري (احتفاظ) - إلا مع Force Trade
5. **ممنوع إشارة انعكاس بناءً على الذيل وحده**

[6] فلتر المسافة المرن V9.1:

• **القاعدة الأساسية:** يُمنع رفض الصفقات القريبة إذا كانت السرعة عالية
• **شرط إجباري:** اختراق حقيقي = تجاوز المستوى بـ ≥ {required_distance} نقطة مع زخم
• **استثناء مغناطيسي:** إذا كان الهدف على بعد نقطتين والزخم قوي، ادخل
• **استثناء V9.1:** إذا كان Momentum >85% و Velocity >1.3 → مسافة {required_distance} نقطة فقط

[7] قوانين إضافية متقدمة V9.1:

💰 **7.1 قاعدة الأرقام المستديرة:** انتظر تجاوز الرقم بـ {required_distance} نقطة
🚀 **7.2 الزخم الثلاثي:** 3 شموع >75% = استمرار (بشرط عدم وجود قمة قريبة)
💪 **7.3 سيادة الجسم:** Marubozu >85% يلغي قانون الفتيلة
🔄 **7.4 الشموع الانعكاسية الصغيرة:** <25% من الشمعة السابقة = تُهمل
🏢 **7.5 الاندفاع المؤسسي:** تغطية هبوط 10 دقائق بصعود دقيقتين = يمنع البيع
⚡ **7.6 اختراق المقاومة الصامت:** انتظر تجاوز بـ {required_distance} نقطة مع زخم
🧲 **7.7 قانون الفجوة المغناطيسية:** لا تشتري عند رأس الانفجار

[8] فلاتر الرفض الإلزامية V9.1:

💰 **8.1 فلتر المسافة الحقيقية:** المسافة الصافية < {required_distance} نقطة = احتفاظ 🟡 (إلا مع Force Trade)
📰 **8.2 فلتر الأخبار:** OTC = تعطيل، حقيقي = احتفاظ عند شموع >300%

⚠️ **8.3 أسباب المنع التسعة V9.1:**
1. زخم معاكس: آخر 3 شموع >70% عكس الاتجاه
2. فخ سيولة: السعر عند قمة تاريخية مع ارتداد سابق
3. **اختراق غير حقيقي: عدم تجاوز المستوى بـ {required_distance} نقطة مع زخم**
4. **منطقة عمياء: السعر داخل نطاق ±1.5 نقطة من القمة (إلا مع Force Trade)**
5. دقيقة الغدر: داخل (55-59) إلخ (تُلغى مع الاختراق الحقيقي أو Force Trade)
6. زخم منهك: الشمعة الحالية >200% من المتوسط
7. غياب التأكيد: عدم ابتلاع 50%
8. تضارب الإطارات
9. أخبار نشطة (للسوق الحقيقي فقط)

[9] النظام الزمني الذكي مع دقائق التحول V9.1:

【استخراج الوقت】
• الوقت على المنصة: [______] من الصورة - المرجع الوحيد
• الفريم: {candle}
• المسافة للهدف: [______] نقطة
• سرعة الشموع: [______] نقطة/شمعة
• السرعة اللحظية: [______] نقطة/ثانية

【معامل الانزلاق - S5, S10 فقط】
• نقطة الدخول المعدلة = السعر الحالي + نقطة أمان

⚠️ **بروتوكول دقائق التحول V9.1:**
• **الدقائق الحرجة (00,15,30,45):** انتظر 20 ثانية بعد الافتتاح
• **دقائق الغدر (14,29,44,59):** حظر الدخول إلا إذا تحقق:
  - اختراق مستوى مغلق
  - زخم ≥ 70%
  - تجاوز المستوى بـ {required_distance} نقطة (أو 0.5 مع Marubozu >85%)
  - **أو تفعيل Force Trade (Momentum >85% + Velocity >1.3)**

[10] مفتاح الطوارئ - The Momentum Override V9.1:

🚨 إذا تحقق (Marubozu >85% + كسر 0.5 نقطة + سرعة >1.5x):
1. تُلغى جميع الفلاتر
2. دخول فوري بحجم مضاعف

🚨 **إذا تحقق (Momentum >85% + Velocity >1.3):**
1. تفعيل وضع القناص اللحظي
2. المسافة المطلوبة = 0.3 نقطة
3. إلغاء الاحتفاظ الإجباري
4. تعطيل القفل الزمني
5. دخول فوري بحجم 1.5x

[11] خوارزمية القرار النهائي V9.1 (Final Decision Matrix):

🚨 **القرار النهائي حسب الأولويات:**

1. **هل هناك اندفاع مؤسسي (Marubozu >85% + كسر 0.5 نقطة)؟** → **تجاوز طارئ**

2. **هل الزخم >85% والسرعة >1.3؟** → **قناص لحظي (دخول فوري عند 0.3 نقطة)**

3. **هل السعر داخل نطاق ±1.5 نقطة من قمة/قاع تاريخي مع Force Trade مفعل؟** → **دخول مع الاتجاه**

4. **هل السعر داخل نطاق ±1.5 نقطة من قمة/قاع تاريخي بدون Force Trade؟** → **احتفاظ 🟡**

5. **هل هناك اختراق بـ 2 نقطة بسرعة >1.3 (زخم متوسط)؟** → **دخول فوري (Flash)**

6. **هل هناك فخ سيولة (قمة تاريخية + ارتداد)؟** → **انعكاس 🔄**

7. **هل السعر عند قمة تاريخية والزخم >50% (دون اختراق {required_distance} نقطة)؟** → **احتفاظ 🟡**

8. **هل الزخم >80% مع تجاوز القمة بـ {required_distance} نقطة؟** → **شراء/بيع مع الاتجاه**

9. **هل السوق متذبذب (عرضي)؟** → **احتفاظ 🟡**

10. **إذا شككت (<80% ثقة)** → **احتفاظ 🟡**

🎯 **التنسيق المطلوب للإجابة (الالتزام حرفياً) وبشكل مختصر:**

📊 التحليل الفني المتقدم:
• **بروتوكول الفشل الذاتي:** [الصورة صالحة / الصورة غير صالحة - يرجى إرسال صورة أوضح]
• **الوقت على المنصة (المرجع):** [الوقت المستخرج من الصورة]
• **تطبيق القناص اللحظي V9.1:** [نعم/لا] - السرعة: [نقاط/ثانية] - الزخم: [٪]
• **تطبيق التجاوز الطارئ:** [نعم/لا] - نسبة الزخم: [٪]
• **تطبيق قاعدة الـ 2 نقطة الفورية:** [نعم/لا] - سرعة الحركة: [نقاط/ثانية]
• **تطبيق فلتر الابتلاع الديناميكي:** [نعم/لا] - نسبة الابتلاع: [٪]
• حالة الهيكل: (صاعد/هابط) + (توافق 4/4 إطارات: نعم / لا )
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)

🎯 الإشارة التنفيذية:
• السعر الحالي: [ السعر الدقيق من الشارت ]
• **نقطة الدخول المعدلة (بعد معامل الانزلاق إن وجد):** [السعر]
• حالة الشمعة: [مفتوحة / مغلقة] [ حمراء 🔴  / خضراء 🟢 ] - الوقت المتبقي: [ وقت دقيق من ساعة المنصة ]
• القرار الفني: (شراء 🟢 / بيع 🔴 / احتفاظ 🟡 / انعكاس 🔄)
• قوة الإشارة: (عالية جدا 💥 / عالية 🔥 / متوسطة ⚡ / ضعيفة ❄️)
• نقطة الدخول: [السعر الدقيق بناءً على OB + شرط الدخول]
• وقت الدخول: [الآن / فوري (Flash) / بعد إغلاق الشمعة الحالية / بعد X شموع]
• الأهداف الربحية:
🎯 TP1: [سحب أول سيولة داخلية], [احتمالية الوصول]
🎯 TP2: [الهدف الرئيسي - منطقة عرض/طلب قوية]
🎯 TP3: [سيولة خارجية أو سد فجوة سعرية]
• وقف الخسارة: [السعر مع 3 طبقات حماية]
• المدة المتوقعة: [عدد الدقائق]
• وقت الذروة المتوقع: [مثلاً: خلال الـ 3 شموع القادمة]

⚠️ إدارة المخاطر:
• مستوى الثقة: [0-100]٪ = [💥/🔥/⚡/❄️/🚫]
🎯 نسبة نجاح الصفقة المتوقعة: [XX]% - [التقييم]
• نقطة الإلغاء: [السعر الذي يفسد التحليل]
• القوانين المطبقة: [القناص اللحظي V9.1 ✓ / التجاوز الطارئ ✓ / قاعدة الـ 2 نقطة ✓ / الابتلاع الديناميكي ✓]

الآن قم بتحليل الشارت المرفق **بتركيز كامل على استخراج البيانات من الصورة** بتطبيق التعديلات الأربعة الجوهرية وبروتوكول الهجومي وأعطني الإجابة بالتنسيق المطلوب أعلاه فقط.
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        # --- الخطوة 1: التحليل الأولي الأساسي ---
        await wait_msg.edit_text("⚡ انتظار قليلآ جاري تحليل الصورة ...")
        
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
            await wait_msg.edit_text(f"❌ خطأ في التحليل: {response_1.status_code}")
            raise Exception(f"خطأ في التحليل الأول: {response_1.status_code}")
        
        initial_analysis = response_1.json()['choices'][0]['message']['content'].strip()
        
        # تنظيف النصوص من التكرار
        initial_analysis = clean_repeated_text(initial_analysis)
        
        # حفظ سياق التحليل في قاعدة البيانات
        save_analysis_context(user_id, initial_analysis)
        
        keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصية"], ["🌍 دولتك"], ["الرجوع للقائمة الرئيسية"]]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        # تنسيق وقت الصفقة للعرض
        time_display = format_trade_time_for_prompt(trade_time)
        
        # إعداد النص النهائي
        full_result = (
            f"✅ **تم التحليل بنجاح (V9.1 القناص اللحظي)** ✅\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{initial_analysis}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔧 **الإعدادات المستخدمة:**\n"
            f"•🕧 فريم التحقق للكسر: {verification_timeframe}\n"
            f"•🕧 جلسة السوق: {session_name} ({session_time})\n"
            f"•🕧 نمط السوق: {market_mode}\n"
            f"•🕧 نوع السوق: {'OTC 🚀' if is_otc_market else 'حقيقي 🌍'}\n"
            f"•📊 الزخم: {momentum:.1f}% | السرعة: {velocity:.2f} نقطة/ثانية\n"
            f"•🎯 مسافة الاختراق: {required_distance} نقطة\n"
            f"•⚡ وضع القناص: {'✅ مفعل' if force_trade else '❌ غير مفعل'}\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"🤖 **Powered by - Obeida Trading V9.1 (Momentum Sniper)**"
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
        
        return full_result
        
    except requests.exceptions.Timeout:
        if 'wait_msg' in locals():
            await wait_msg.edit_text("⏱️ تجاوز الوقت المحدد إرسال الصورة. حاول مرة أخرى.")
        else:
            await update.message.reply_text("⏱️ تجاوز الوقت المحدد إرسال الصورة. حاول مرة أخرى.")
        return "⏱️ تجاوز الوقت المحدد"
    except Exception as e:
        print(f"❌ خطأ في تحليل الصورة: {traceback.format_exc()}")
        error_msg = f"❌ **حدث خطأ في تحليل الصورة:** {str(e)[:200]}\nيرجى المحاولة مرة أخرى."
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

    wait_msg = await update.message.reply_text(" 📸 تم استلام الصورة 📸")
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
            ["إلغاء", "🔙 العودة للقائمة الرئيسية"]
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
                text=f"✅ **تم تفعيل اشتراكك في Obeida Trading!**\n\n"
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
                        text=f"⚠️ **تم إلغاء اشتراكك في Obeida Trading**\n\n"
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
                    text=f"✅ **تم تمديد اشتراكك في Obeida Trading**\n\n{message}",
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
    """تنظيف دوري للملفات المؤقتة - كل 5 دقائق"""
    while True:
        try:
            await asyncio.sleep(300)  # كل 5 دقائق (بدلاً من 30 دقيقة)
            cleanup_old_images()
            cleanup_old_database_records()
            print("🧹 تم التنظيف الدوري للملفات المؤقتة (كل 5 دقائق)")
            
        except Exception as e:
            print(f"⚠️ خطأ في التنظيف الدوري: {e}")

async def backup_scheduler():
    """جدولة النسخ الاحتياطي التلقائي كل ساعة"""
    while True:
        try:
            await asyncio.sleep(3600)  # كل ساعة
            DataProtectionAdvanced.backup_databases_secure()
            DataProtectionAdvanced.export_users_to_json()
            print("⏰ تم تنفيذ النسخ الاحتياطي التلقائي المجدول")
        except Exception as e:
            print(f"⚠️ خطأ في النسخ الاحتياطي المجدول: {e}")

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
    """تشغيل Flask server"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# --- Flask Server مع واجهة تحكم (محدثة) ---
app = Flask(__name__)

# HTML template for admin panel (محدث)
ADMIN_PANEL_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>لوحة تحكم Obeida Trading - نظام الاشتراك المتطور</title>
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
        .status-blocked {
            background: #f8d7da;
            color: #721c24;
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
            <h1>🤖 لوحة تحكم Obeida Trading - V2</h1>
            <p>نظام إدارة الاشتراكات المتطور - المشرف: @Sz2zv</p>
            <p style="color: #28a745; font-size: 0.9em;">🌍 نظام المناطق الزمنية للمستخدمين نشط</p>
            <p style="color: #ffc107; font-size: 0.9em;">💾 نظام النسخ الاحتياطي المتقدم - آخر نسخة: {{ last_backup }}</p>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>إجمالي المستخدمين</h3>
                <div class="number">{{ stats.total_users }}</div>
            </div>
            <div class="stat-card">
                <h3>المشتركين النشطين</h3>
                <div class="number">{{ stats.active_users }}</div>
            </div>
            <div class="stat-card">
                <h3>المحظورين</h3>
                <div class="number">{{ stats.blocked_users }}</div>
            </div>
        </div>

        <div class="tabs">
            <div class="tab active" onclick="showTab('users')">👥 المستخدمين</div>
            <div class="tab" onclick="showTab('subscriptions')">📊 الاشتراكات</div>
            <div class="tab" onclick="showTab('stats')">📈 إحصائيات</div>
        </div>

        <div id="users-tab" class="tab-content active">
            <div class="section">
                <h2 class="section-title">👥 قائمة المستخدمين</h2>
                
                <div class="search-box">
                    <input type="text" id="searchUsers" placeholder="🔍 بحث عن مستخدم..." onkeyup="searchUsers()">
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
                            </tr>
                        </thead>
                        <tbody>
                            {% for user in users %}
                            <tr>
                                <td>{{ user.id }}</td>
                                <td>{{ user.name }}</td>
                                <td>{{ user.subscription_type }}</td>
                                <td>{{ user.end_date }}</td>
                                <td>
                                    <span class="status-badge {% if user.status == 'نشط' %}status-active{% elif user.status == 'محظور' %}status-blocked{% else %}status-expired{% endif %}">
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
                            </tr>
                        </thead>
                        <tbody>
                            {% for type, count in stats.subscriptions_by_type.items() %}
                            <tr>
                                <td>{{ type }}</td>
                                <td>{{ count }}</td>
                                <td>{{ (count / stats.active_users * 100)|round(1) if stats.active_users > 0 else 0 }}%</td>
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
                            </tr>
                        </thead>
                        <tbody>
                            {% for feature, count in stats.feature_usage.items() %}
                            <tr>
                                <td>{{ feature }}</td>
                                <td>{{ count }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div class="footer">
        <p>Obeida Trading - نظام إدارة الاشتراكات المتطور V2</p>
        <p style="font-size: 0.8em;">نسخة البيانات: {{ data_version }} - آخر نسخة احتياطية: {{ last_backup }}</p>
    </div>

    <script>
        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            document.querySelector(`.tab:nth-child(${tabName === 'users' ? 1 : tabName === 'subscriptions' ? 2 : 3})`).classList.add('active');
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
    # التحقق من سلامة قاعدة البيانات عند كل زيارة
    DataProtectionAdvanced.verify_database_integrity()
    
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Obeida Trading V2</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
            h1 { font-size: 3em; margin-bottom: 20px; }
            .status { background: rgba(255,255,255,0.2); padding: 20px 40px; border-radius: 10px; display: inline-block; margin: 20px; }
            .admin-link { background: #28a745; color: white; padding: 15px 30px; border-radius: 5px; text-decoration: none; display: inline-block; margin: 20px; }
        </style>
    </head>
    <body>
        <h1> 📊 Obeida Trading V2 - Subscription System 📊</h1>
        <p>Chat & Technical Analysis Bot with Advanced Subscription System</p>
        <div class="status">🌍 نظام المناطق الزمنية للمستخدمين نشط - اختر دولتك</div>
        <p>Last Ping: """ + time.strftime("%Y-%m-%d %H:%M:%S") + """</p>
        <a href="/admin" class="admin-link">🔐 لوحة تحكم المشرف</a>
    </body>
    </html>
    """

@app.route('/health')
def health():
    stats = SubscriptionSystem.get_stats()
    return {
        "status": "active", 
        "ai_provider": "Mistral AI", 
        "subscription_system": "advanced_v2",
        "timezone_support": True,
        "data_version": DATA_VERSION,
        "backup_system": "advanced",
        "total_users": stats.get('total_users', 0),
        "active_users": stats.get('active_users', 0),
        "blocked_users": stats.get('blocked_users', 0),
        "timestamp": time.time()
    }

@app.route('/admin')
def admin_panel():
    """لوحة تحكم المشرف المحدثة"""
    try:
        stats = SubscriptionSystem.get_stats()
        users = SubscriptionSystem.get_all_users()
        
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
            
            formatted_users.append({
                'id': uid,
                'name': display_name,
                'subscription_type': sub_name or 'غير مشترك',
                'end_date': end_display,
                'status': status,
                'last_active': last_active[:16] if last_active else '-'
            })
        
        # معلومات النسخ الاحتياطي
        last_backup = "لا توجد نسخ"
        if os.path.exists(BACKUP_DIR):
            backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')])
            if backups:
                last_backup = backups[-1].replace('main_backup_', '').replace('subscription_backup_', '').replace('.db', '')
        
        return render_template_string(
            ADMIN_PANEL_HTML,
            stats=stats,
            users=formatted_users,
            data_version=DATA_VERSION,
            last_backup=last_backup
        )
    except Exception as e:
        return f"خطأ في تحميل لوحة التحكم: {str(e)}"

@app.route('/admin/add_subscription_v2', methods=['POST'])
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
            # إنشاء نسخة احتياطية فورية
            DataProtectionAdvanced.backup_databases_secure()
            DataProtectionAdvanced.export_users_to_json()
            return jsonify({'success': True, 'subscription': result})
        else:
            return jsonify({'success': False, 'error': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/revoke_subscription_v2', methods=['POST'])
def revoke_subscription_v2():
    """إلغاء اشتراك مستخدم"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        success, message = SubscriptionSystem.revoke_subscription(user_id, performed_by=ADMIN_ID)
        
        if success:
            DataProtectionAdvanced.backup_databases_secure()
            DataProtectionAdvanced.export_users_to_json()
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/extend_subscription_v2', methods=['POST'])
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
            DataProtectionAdvanced.backup_databases_secure()
            DataProtectionAdvanced.export_users_to_json()
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/block_user_v2', methods=['POST'])
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

@app.route('/admin/get_stats_v2')
def get_stats_v2():
    """الحصول على الإحصائيات"""
    stats = SubscriptionSystem.get_stats()
    return jsonify({'success': True, 'stats': stats})

@app.route('/admin/restore_backup', methods=['POST'])
def restore_backup():
    """استعادة آخر نسخة احتياطية"""
    try:
        success = DataProtectionAdvanced.restore_latest_backup()
        if success:
            return jsonify({'success': True, 'message': 'تم استعادة آخر نسخة احتياطية بنجاح'})
        else:
            return jsonify({'success': False, 'error': 'فشل في استعادة النسخة الاحتياطية'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/export_users')
def export_users():
    """تصدير المستخدمين إلى JSON"""
    try:
        json_path = DataProtectionAdvanced.export_users_to_json()
        if json_path and os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return jsonify({'success': True, 'data': data})
        else:
            return jsonify({'success': False, 'error': 'فشل في تصدير البيانات'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/ping')
def ping():
    return "PONG"

def main():
    """الدالة الرئيسية - النسخة النهائية مع نظام المناطق الزمنية"""
    print("🤖 Starting Obeida Trading with Timezone Support ...")
    
    # التحقق من سلامة قاعدة البيانات قبل التهيئة
    DataProtectionAdvanced.verify_database_integrity()
    
    # تهيئة قاعدة البيانات الرئيسية مع استعادة النسخ الاحتياطية
    init_db_with_subscription()
    
    # إنشاء نسخة احتياطية أولية وتصدير JSON
    DataProtectionAdvanced.backup_databases_secure()
    DataProtectionAdvanced.export_users_to_json()
    DataProtectionAdvanced.create_restore_point()
    
    # تشغيل Flask
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
            print(f"📁 تم إنشاء مجلد الصور المؤقتة: {IMAGE_CACHE_DIR}")
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف الملفات عند بدء التشغيل: {e}")
    
    print(f"🌐 Flask server started on port {os.environ.get('PORT', 8080)}")
    print(f"📊 لوحة تحكم المشرف: http://localhost:{os.environ.get('PORT', 8080)}/admin")
    print(f"💾 مجلد النسخ الاحتياطي: {os.path.abspath(BACKUP_DIR)}")
    
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
    
    print("✅ Telegram Bot with Timezone Support ready ...")
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
