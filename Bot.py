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

# حالات المحادثة (للنسخة الجديدة V2)
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION, ADMIN_MENU, ADD_SUBSCRIPTION, SELECT_DURATION, REMOVE_USER, BROADCAST_MESSAGE, SET_USER_DURATION = range(13)

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
                    INSERT INTO users (user_id, username, subscription_end, candle, trade_time)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, username, end_date, "M1", "قصير (1m-15m)"))
            
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
# باقي الكود كما هو (بدون تغيير) - جميع الدوال الأخرى تبقى كما هي
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
    
    print("✅ Database initialized")
    
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
    """تنظيف الصور القديمة"""
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

# --- دوال معالجة البوت (محدثة لنظام الاشتراك الجديد) ---

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
            f"📞 للاشتراك والتواصل مع المشرف: @Sz2zv",
            parse_mode="Markdown"
        )
        return False
    
    # تسجيل استخدام
    SubscriptionSystem.increment_usage(user_id, 'bot_access')
    
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت - مع إخفاء الأزرار لغير المشتركين"""
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
    
    if is_admin:
        # المشرف - يرى جميع الأزرار
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"],
            ["👑 لوحة التحكم"]
        ]
        
        welcome_msg = (
            "🚀 أهلاً بك في Obeida Trading\n\n"
            "👑 أنت مسؤول النظام - لديك صلاحيات كاملة.\n\n"
            "🤖 الخدمات المتاحة:\n"
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
            f"🤖 الخدمات المتاحة:\n"
            f"• 📊 تحليل الشارتات\n"
            f"• 📈 توصيات فورية\n"
            f"• 💬 دردشة ذكية\n"
            f"• ⚙️ إعدادات متقدمة\n\n"
            f"اختر أحد الخيارات:"
        )
        
    else:
        # مستخدم غير مشترك - لا يرى أي أزرار، فقط رسالة للتواصل مع المشرف
        await update.message.reply_text(
            f"⚠️ عذراً، ليس لديك صلاحية استخدام البوت\n\n"
            f"❌ الحالة: {message}\n\n"
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

async def handle_photo_in_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور في وضع التحليل"""
    return await handle_photo_analysis(update, context)

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور للتحليل الفني المتقدم"""
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return MAIN_MENU
    
    user_id = update.effective_user.id
    candle, trade_time, prev_context, prev_time = get_user_setting(user_id)
    
    # تسجيل استخدام
    SubscriptionSystem.increment_usage(user_id, 'photo_analysis')
    
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
مهمتك تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية دقيقة بنظام متعدد الطبقات.

🎯 **التوجيه الأساسي: التركيز الكامل على استخراج البيانات وتحليل الصورة**
أنت ملزم بالتركيز التام على الصورة المرفقة فقط. يجب أن تستخرج كل البيانات والأرقام من الصورة بكامل قدراتك البصرية. لا تتشتت بأي معلومات خارجية أو افتراضات. كل ما تحتاجه موجود في الصورة:

1. **استخراج الأسعار بدقة مطلقة:** اقرأ الأسعار من المحور اليميني (Y-axis) بدقة بكسلية
2. **تحديد الزمن الحالي:** ابحث عن ساعة المنصة في الزاوية اليسرى/اليمنى السفلية - **هذا هو المرجع الوحيد لدقائق الغدر**
3. **تحليل الشموع:** افحص آخر 5-7 شموع (الأجسام، الذيول، الألوان، الأحجام)
4. **رسم المستويات:** حدد الدعم/المقاومة، الفجوات، مناطق السيولة
5. **التطبيق العملي:** طبق كل القواعد أدناه على البيانات المستخرجة من الصورة فقط

{previous_context_info}

🚨 **تنبيه الربط البرمجي الإلزامي (Compulsory Variable Binding):**
هذا البرومبت يعمل ضمن نظام Bot.py ويستقبل المتغيرات التالية من الكود. تأكد من تمريرها جميعاً في دالة .format().

📌 **المعطيات الذي تم إرسالها من مستخدم:**
• إطار الشموع: {{candle}}
• فريم التحقق: {{verification_timeframe}}
• استراتيجية التداول: {{trading_strategy}}
• جلسة السوق: {{session_name}} ({{session_time}})
• حالة السيولة: {{session_vol}}
• تأثير الأخبار: {{news_impact}} (معامل ×{{news_risk_multiplier}})
• تحذير الأخبار: {{news_warning if news_warning else "✅ الوضع آمن"}}
• منطقة القتل السعري: {{kill_zone_status}}
• حالة دقيقة الغدر: {{last_minute_status}} **(مرجع توثيقي فقط - المرجع الفعلي هو ساعة المنصة)**
• حالة إغلاق الشمعة: {{candle_closing_status}}
• الوقت المتبقي للإغلاق: {{seconds_remaining}} ثانية
• توقيت التحليل: {{gaza_time.strftime('%Y-%m-%d %H:%M:%S بتوقيت غزة')}} **(مرجع توثيقي فقط)**
• تصنيف الفريم: {{candle_category}}
• حجم الصفقة: {{position_sizing}}
• سياق التحليل السابق: {{"✅ متوفر" if previous_context_info else "❌ غير متوفر"}}

✅ **جميع المتغيرات مستلمة ومعرّفة بشكل صحيح.**

🎯 **نظام الاستخراج البصري المتقدم (Advanced Visual Extraction System):**

🔍 **المرحلة 1: مسح الصورة الشامل (Full Image Scan):**
1. ابدأ بمسح شامل للصورة من الأعلى إلى الأسفل، ومن اليسار إلى اليمين
2. حدد العناصر الأساسية: محور الأسعار (Y-axis)، محور الزمن (X-axis)، منطقة الشموع، ساعة المنصة
3. استخرج البيانات التالية بالترتيب:
   - **الأسعار:** السعر الحالي، أعلى سعر، أدنى سعر، مستويات الدعم/المقاومة
   - **الزمن:** الوقت الحالي على الشارت، إطار الشموع ({{candle}}) - **(ساعة المنصة هي المرجع الوحيد)**
   - **الشموع:** آخر 5 شموع (فتحة، إغلاق، أعلى، أدنى، الجسم، الذيول)
   - **المؤشرات:** أي مؤشرات مرئية (إن وجدت)

🔍 **المرحلة 2: التحليل الاستبعادي البصري (Visual Rejection Analysis):**
1. **الوصف الإلزامي قبل التحليل:** ابدأ بوصف الشموع الثلاث الأخيرة بدقة:
   • **الشمعة -1:** [جسم/ذيل العلوي/ذيل السفلي]، النسبة: جسم/شمعة = [%]
   • **الشمعة -2:** [جسم/ذيل العلوي/ذيل السفلي]، النسبة: جسم/شمعة = [%]
   • **الشمعة -3:** [جسم/ذيل العلوي/ذيل السفلي]، النسبة: جسم/شمعة = [%]

2. **تحليل الاتجاه الحجمي:** 
   • إذا كانت أحجام الأجسام في ازدياد: ↝ منع أي إشارة عكسية
   • إذا كانت أحجام الأجسام في تناقص: ↝ احتمالية انعكاس

3. **الفحص الرياضي الدقيق مع معامل الانزلاق السعري (Anti-Visual Bias with Slippage Factor):**
   • **السعر الحالي الملاحظ:** [قراءة مباشرة من المحور Y]
   • **سعر الهدف الملاحظ:** [قراءة مباشرة من المستوى المستهدف]
   • **المعادلة الحسابية الأساسية:** |السعر الحالي - سعر الهدف| = [النقاط]
   • **في فريمات S5, S10:** أضف "معامل الانزلاق السعري" = خصم نقطة أمان واحدة من نقطة الدخول
   • **نقطة الدخول المعدلة:** [السعر الحالي + (نقطة أمان في اتجاه الصفقة)]
   • **التأكيد:** يجب أن تتطابق الحسابات مع المسافات البصرية

4. **كشف التناقضات البصرية:**
   • إذا كانت المسافة المحسوبة ≠ المسافة المقدرة بالعين → أعد المسح
   • تأكد من تطابق كل ذيل مع رقمه على المحور اليميني
   • تحقق من مقياس الرسم (Scale) بوحدات متساوية
   • **عند قراءة المحور Y، إذا كان الرقم غير واضح، قم بمقارنة موضع الشمعة بمستويات الدعم/المقاومة المرئية الأقرب لتقدير السعر بدقة +/- 1 نقطة.**

🔍 **المرحلة 3: التحقق المتعدد (Multi-Layer Verification):**
1. **التحقق المزدوج:** كل رقم يتم استخراجه يجب التحقق منه مرتين
2. **الربط المنطقي:** تأكد من تطابق الأسعار مع مواقع الشموع
3. **الكشف عن التناقضات:** إذا وجدت تناقضاً، أعد المسح من البداية

🛡️ **بروتوكول الفشل الذاتي (Self-Failure Protocol):**
إذا كانت الصورة تحتوي على أي من العيوب التالية، يجب التوقف فوراً وإبلاغ المستخدم:

1. **ضوضاء بصرية عالية:** مؤشرات كثيرة تغطي الشموع وتحجب الرؤية
2. **محور أسعار مقطوع:** عدم وضوح الأرقام على المحور Y
3. **ساعة منصة غير واضحة:** عدم القدرة على تحديد الوقت بدقة
4. **جودة صورة رديئة:** عدم وضوح تفاصيل الشموع

✅ **الإجراء الإلزامي:** "الصورة غير صالحة للتحليل - يرجى إرسال صورة أوضح مع شموع ومحور أسعار واضحين."

🔥 **قانون الفتيلة المؤكدة مع معالجة الشموع الانعكاسية الصغيرة (The Confirmed Wick Law with Micro-Rejection Filter):**

✅ **قاعدة التأكيد الإلزامي الصارم:**
1. **لا تثق في الذيل الطويل (الفتيلة) إلا إذا بدأت الشمعة التالية بالتحرك عكس اتجاه الذيل لمسافة تغطي 50% على الأقل من جسم الشمعة السابقة.**
2. إذا لم يحدث هذا التأكيد خلال الشمعة التالية، اعتبر الذيل "فراغاً سعرياً" (Price Void) سيتم ملؤه، واستمر مع الاتجاه الأصلي بقوة.
3. نسبة الذيل >60% تعطي أولوية للمراقبة فقط، وليس للدخول المباشر.
4. **ممنوع إصدار إشارة انعكاس بناءً على الذيل وحده تحت أي ظرف.**

❌ **سيناريو الرفض الإلزامي:**
إذا ظهر ذيل طويل ثم أغلقت الشمعة التالية دون ابتلاع 50% من الجسم السابق:
• تجاهل إشارة الانعكاس فوراً
• استمر مع اتجاه الزخم السابق
• انتظر امتلاء الفراغ السعري
• القرار الإلزامي: "احتفاظ 🟡" حتى اكتمال التأكيد

🔄 **قاعدة معالجة الشموع الانعكاسية الصغيرة (Micro-Rejection Filter):**
• إذا ظهرت شمعة واحدة عكس الاتجاه (دوجي أو شمعة صغيرة)
• **وحجمها أقل من 25% من حجم الشمعة الاندفاعية السابقة**
• **تُهمل هذه الشمعة تماماً ولا تؤثر على التحليل**
• **يستمر التحليل مع الاتجاه الأصلي وكأنها لم تظهر**
• **المنطق:** في الـ OTC، هذه الشموع تعني "تجميع سيولة لحظي" وليس انعكاساً حقيقياً

💰 **ميزة التصحيح السعري الرقمي (Price Action Calibration):**
الراديكالية في تحديد الأسعار هي المفتاح. ابحث عن "الأرقام المستديرة" (مثل 1.68000) داخل الصورة واربطها بالزخم.
✅ **القاعدة:** إذا كان السعر متجهاً لرقم مستدير بفتحات شموع واسعة، فمن الانتحار التداول عكسه.
📌 **التعديل الجديد:** "الرقم المستدير مغناطيس؛ لا تعطي إشارة ارتداد إلا بعد ملامسته بـ 'ذيل' (Wick) ثم العودة للإغلاق دونه. قبل ذلك، الهدف هو لمس الرقم."

🚀 **دمج خوارزمية الزخم (Momentum vs Structure):**
• في الأسواق الحقيقية (Real Market)، الهيكل (Structure) هو الملك.
• في أسواق الـ OTC، الزخم (Momentum) هو الملك.
✅ **الشرط الإلزامي:** إذا وجدت 3 شموع متتالية بنفس اللون وبأجسام ممتلئة (>80%)، يُحظر البيع حتى لو وصل السعر لقمة تاريخية. الزخم في هذه الحالة أقوى من أي تحليل فني.

🎯 **كشف التلاعب بالسيولة (Liquidity Sweep Detection):**
بدلاً من البحث عن نماذج مثل "الرأس والكتفين"، ابحث عن "القمم المتساوية" (Equal Highs). صناع السوق يضعون أوامرهم فوقها.
✅ **المنطق العملي:** إذا رأيت قمتين متساويتين في الصورة، يجب أن تكون التوصية: "انتظر اختراق القمة ثم ادخل بيعاً مع العودة"، وليس البيع من القمة مباشرة.

🔄 **قفل الأمان للزخم (The Momentum Deadlock):**
إذا رصدت 3 شموع متتالية بنفس اللون بجسم يمثل >80% من حجم الشمعة، يُحظر تماماً إصدار إشارة عكسية. في هذه الحالة، تتحول المهمة من 'توقع الانعكاس' إلى 'تحديد أقرب هدف مغناطيسي (Liquidity/Round Number)' والمطالبة باللحاق بالاتجاه.

🔍 **ميزة "كشف كسر الحقيقة" (The Truth-Break Detection):**
عند حدوث هبوط ثم صعود مفاجئ (V-Shape)، قارن حجم شمعة الارتداد بآخر شموع الهبوط. إذا ابتلعت الشمعة الخضراء آخر شمعتين حمراوين، اعتبر الهبوط السابق 'Stop Hunt' (فخ سيولة) وألغِ جميع مستويات المقاومة القريبة؛ السعر الآن في وضع اندفاع مؤسسي.

🎯 **تعديل "قاعدة الرقم المستدير كهدف" (Round Number Target Logic):**
في سوق الـ OTC، الأرقام المستديرة (مثل 0.18900) هي أهداف وليست مقاومات. لا تعطي إشارة ارتداد من الرقم المستدير إلا بعد أن يتم اختراقه بـ 'ذيل' (Wick) ثم العودة للإغلاق دونه. قبل ذلك، الهدف هو لمس الرقم.

🎯 **نظام "مضاد للخسارة" - الإضافات النهائية:**

🔄 **قاعدة "إعادة اختبار الفراغ" (FVG Rejection):**
عندما يعود السعر لفجوة (FVG) هابطة بشمعة اندفاعية خضراء ولا يحترمها (لا يرتد منها للأسفل)، فهذا يسمى **Inversion FVG**.
✅ **القاعدة التنفيذية:** بمجرد إغلاق شمعة دقيقة **فوق منتصف الفجوة الهابطة**، تتحول وظيفة هذه المنطقة من "مقاومة للبيع" إلى "منصة إطلاق للشراء". يتم إلغاء جميع أوامر البيع وإصدار إشارة شراء فورية مع جعل قاع الفجوة نقطة وقف.

⏰ **ميزة "تزامن الوقت مع الرقم المستدير" (Kill-Zone Timing):**
في الـ OTC، الدقائق التي تنتهي بـ **(5، 10، 15، 30، 45)** هي دقائق "تعديل المراكز".
✅ **القاعدة الإلزامية:** إذا كان السعر قريباً من رقم مستدير (مثل 0.18900) عند الدقيقة **44 أو 59 أو 14 أو 29**، فإنه سيقوم بـ **"قفزة انتحارية" للمس الرقم قبل إغلاق الشمعة الكبيرة**. يُحظر إصدار إشارة انعكاس قبل هذه اللحظة، والمطلوب هو متابعة الاتجاه حتى يلمس الرقم.
**ملاحظة هامة: ساعة المنصة (المستخرجة من الصورة) هي المرجع الوحيد لحساب هذه الدقائق.**

📊 **فلتر "ثبات الحجم البصري" (Visual Volume Stability):**
إذا كانت الشمعة الصاعدة **تفتقر للذيل العلوي (Flat Top)**، فهذا يعني أن ضغط الشراء لم ينتهِ بعد، ولا يوجد بائعون تجرأوا على الدخول.
✅ **قاعدة الحظر:** **ممنوع البيع حتى تظهر أول شمعة بـ 'ذيل علوي' يمثل 30% على الأقل من حجمها الكلي.** الشموع المسطحة القمة هي علامة على اندفاع مؤسسي مستمر.

🛡️ **نظام "الخوارزمية لا تقهر" - الإضافات النهائية:**

🎯 **قاعدة "فخ القمة المزدوجة في الـ OTC - النسخة المعدلة V3":**
في الـ OTC، القمم المتساوية (Equal Highs) **ليست مقاومات، بل هي "بنزين للصعود" وأهداف سيولة إلزامية.**
✅ **المنطق التنفيذي الحاسم:**
    - **لا تعتبر القمة المتساوية نقطة انعكاس أبداً.**
    - **الوظيفة الوحيدة للقمة المتساوية هي "هدف يجب ضربه" (Liquidity Sweep Target).**
    - إذا رأيت قمتين متساويتين والسعر يقترب منهما بزخم قوي (3 شموع خضراء)، فإن السعر **ملزم** باختراق هذه القمة (Stop Hunt) لضرب أوامر البيع المعلقة والسيولة.
    - **القرار التنفيذي:**
        1.  يُحظر البيع من القمة مباشرة أو قبلها.
        2.  الهدف هو **الاختراق**.
        3.  الدخول يكون إما **مع الاختراق** (Momentum)، أو **انتظار العودة بعد الاختراق** (Pullback) للبيع، وليس البيع من القمة قبل لمسها.

🎯 **ميزة "التدقيق البكسلي للمحور Y" (Precision Y-Axis):**
أنت ملزم بمطابقة ذيل الشمعة مع الرقم المقابل له على المسطرة اليمنى بدقة.
✅ **القاعدة الإلزامية:** "إذا كان الذيل يلمس 0.18877، فلا تقرب الهدف لـ 0.18880؛ استخدم الرقم الفعلي لحساب الفجوة السعرية (FVG) بدقة."

🎯 **قانون "السيولة المغناطيسية" (Magnet Liquidity):**
السعر يتحرك دائماً من "سيولة داخلية" (فجوة) إلى "سيولة خارجية" (قمة/قاع).
✅ **القاعدة التنفيذية:** "عندما يغلق السعر فوق فجوة (FVG) هابطة، تصبح هذه الفجوة 'دفعاً' (Propulsion). الهدف القادم حتماً هو القمة السابقة (Old High). **لا تقترح أي صفقة بيع طالما لم يتم لمس القمة الخارجية**."

🛡️ **لمسات النظام المكتمل بنسبة 100%:**

🎯 **قاعدة "إغلاق الثغرة الأمنية" (The Safety Gap Rule):**
إذا تم تغطية مسار هابط استغرق 10 دقائق بصعود استغرق دقيقتين فقط، فهذا ليس مجرد صعود، بل هو **'اندفاع مؤسسي' (Institutional Drive)**.
✅ **قاعدة الحظر:** "في هذه الحالة، **يُمنع البيع حتى لو ظهرت شمعة حمراء صغيرة**، لأنها ستكون مجرد 'توقف مؤقت' (Rest Point) قبل انفجار ثانٍ."

🎯 **ميزة "كشف الفتيلة الكاذبة" (False Wick Detection):**
أحياناً يترك السعر ذيلاً طويلاً بالأعلى يوهمك بالهبوط، لكنه في الحقيقة "جس نبض" للسيولة.
✅ **المنطق التنفيذي:** "إذا ترك السعر ذيلاً طويلاً (Wick) ثم أغلقت الشمعة التالية **فوق منتصف هذا الذيل**، فهذا يعني أن الذيل كان 'سحباً للسيولة' وليس رفضاً. **القرار: استمرار مع الاتجاه وليس الانعكاس**."

🎯 **فلتر "وقت انتهاء الصلاحية" (The 5-Minute Fatigue):**
في الـ OTC، الزخم غالباً ما يتعب بعد 5 شموع متتالية من نفس اللون.
✅ **القاعدة:** "عند عد 5 شموع خضراء ممتلئة متتالية، ابدأ بالبحث عن **'شمعة المنهك' (Exhaustion Candle)** وهي شمعة كبيرة جداً بشكل مبالغ فيه (أكبر بـ 3 مرات من سابقاتها). **هذه هي اللحظة الوحيدة التي يُسمح فيها بتوقع ارتداد قصير**."

⚡ **قواعد المعايرة الذاتية (Self-Calibration):**
1. **تفضيل الزخم:** في حال التعارض بين 'منطقة عرض' (SMC) و 'اندفاع قوي' (OTC Momentum)، **يفوز الزخم دائماً**. لا تقترح البيع إلا بعد ظهور **CHoCh واضح على فريم أصغر**.
2. **قاعدة الـ V-Shape:** أي هبوط يتم استرداده فوراً بنسبة **>100%** يعتبر فخاً. **الهدف القادم هو القمة التي تليها مباشرة**.
3. **ميزان القوى:** قارن حجم الشموع الخضراء بالحمراء في آخر 10 دقائق؛ **اللون المسيطر حجماً هو الذي يحدد اتجاه صفقاتك** حتى لو كان الهيكل عكس ذلك.

🔥 **اللمسات النهائية للخوارزمية المتكاملة:**

🎯 **قاعدة "الاندفاع الانتحاري" (The Climax Rule):**
في الـ OTC، عندما تقترب الشمعة من هدفها النهائي (مثل رقم مستدير أو قمة تاريخية)، يزداد حجمها بشكل جنوني في آخر 10 ثوانٍ.
✅ **قاعدة الحظر:** "إذا زاد حجم جسم الشمعة الحالية عن **200% من متوسط حجم آخر 5 شموع** وهي تقترب من مستوى سيولة، فهذا يسمى **'اندفاع الذروة'**. **لا تدخل عكسه أبداً حتى يكتمل المسح بالكامل (Full Sweep) وتظهر شمعة رفض واضحة**."

🎯 **قاعدة "الفجوة المكسورة" (The Inversion Logic):**
أي منطقة عرض (Supply) يتم اختراقها بشمعة اندفاعية (Marubozu) تتحول فوراً إلى منطقة طلب (Demand).
✅ **القاعدة التنفيذية:** "إذا عاد السعر ليلمس منطقة عرض تم اختراقها سابقاً، فالقرار هو **شراء مع الاتجاه الجديد، وليس البيع منها مرة أخرى**."

🧠 **التحديث المنطقي - القواعد المدمجة حديثاً:**

1. **فلتر الإنهاك (التعامل مع الانفجار السعري):**
"قاعدة الزخم المنهك: إذا كانت الشمعة الأخيرة ضخمة جداً وبدون ذيول مقارنة بما قبلها، يُمنع الاستمرار. انتظر ذيل رفض عكسي؛ فالانفجار السعري OTC غالباً ما يتبعه انهيار مفاجئ."

2. **الفراغ المغناطيسي (الأرقام المستديرة):**
"منطق الفراغ السعري: لا تتوقع انعكاساً قبل لمس الرقم المستدير فعلياً. إذا وجد فراغ بصري بين السعر والرقم، اعتبر السعر منجذباً للرقم لإكمال السيولة قبل أي ارتداد."

3. **تناقص العزم (حجم الشموع):**
"تلاشي القوة: إذا صغر حجم أجسام الشموع المتتالية رغم ثبات اللون، تُلغى إشارة (قوي) وتتحول لـ (توقع انعكاس وشيك)؛ لأن هذا يعني انسحاب السيولة تدريجياً."

4. **حماية الدقيقة الأخيرة:**
"فلتر التلاعب الزمني: إذا حدثت حركة عنيفة في آخر 10 ثوانٍ من الشمعة، صنفها (تلاعب سيولة). اطلب الانتظار للشمعة الجديدة ولا تدخل مع الاندفاع المتأخر."

🛡️ **بروتوكول "درع الحماية من الانعكاس الوهمي" (The Anti-Fakeout Shield):**

1. **قاعدة الابتلاع الإلزامي (The Mandatory Engulfing Rule):**
يُحظر تماماً إصدار إشارة "عكس الاتجاه" بناءً على ذيل (Wick) فقط، مهما كان طوله.
الشرط: لا يتم اعتماد الانعكاس إلا إذا ظهرت شمعة عكسية تبتلع 50% على الأقل من جسم الشمعة الاندفاعية السابقة وتغلق فوق/تحت منتصفها.
إذا لم يتحقق شرط الابتلاع، اعتبر الذيل "فخ سيولة" (Stop Hunt) واستمر مع الاتجاه الأصلي بقوة.

2. **فلتر "القطار المؤسسي" (Institutional Train Filter):**
إذا رصدت شمعة (Marubozu) ممتلئة بنسبة >90% وبحجم ضخم (أكبر بـ 200% من الشموع السابقة)، يُمنع توقع القاع أو القمة.
القاعدة: "الزخم الممتلئ يقتل الذيول"؛ استمر في البيع/الشراء مع اتجاه الشمعة الضخمة حتى تظهر 3 شموع عرضية صغيرة متتالية، فهي العلامة الوحيدة على توقف القطار.

3. **قانون "كسر الرقم المستدير بالجسم":**
إذا اخترق "ذيل" الشمعة الرقم المستدير، فهو مغناطيس.
إذا اخترق "جسم" الشمعة الرقم المستدير وأغلق خلفه بمسافة تزيد عن 5 نقاط، فقد تحول الرقم من (هدف) إلى (نقطة انطلاق جديدة).
القرار: اتبع الكسر بالجسم فوراً ولا تراهن على الارتداد من الرقم المكسور.

4. **ميزان "الذيل كهدف" (Wick as a Target):**
في سوق الـ OTC، إذا ظهر ذيل طويل ولم يتبعه ارتداد فوري في الشمعة التالية، فإن هذا الذيل يصبح "فراغاً سعرياً" (Price Void).
المهمة: تحول وظيفة النظام من "توقع الارتداد من الذيل" إلى "استهداف كسر قمة/قاع الذيل".

5. **【تعديل إلزامي V3 - قاعدة "منع الارتداد المبكر" (The Early Rejection Kill-Switch):**
    ✅ **الشرط:** إذا تحقق الشرطان التاليان معاً:
        1.  **الزخم:** آخر 3 شموع خضراء متتالية بأجسام متزايدة في الحجم.
        2.  **الفجوة:** وجود فجوة سعرية هابطة (Bearish FVG) أمام السعر.
    ✅ **القرار الإلزامي:**
        - **يُلغى فوراً أي تحليل يشير إلى ارتداد من الفجوة أو بيع من مستويات المقاربة.**
        - **المنطق:** الزخم الصاعد القوي والأجسام المتزايدة تغلب على وجود فجوة هابطة. الفجوة في هذه الحالة هي "ممتص زخم" مؤقت وليست "مانع اختراق".
        - **الإجراء:** التحول من "توقع الهبوط" إلى "استهداف القمة المتساوية/الرقم المستدير التالي".
        - **ممنوع إصدار أي إشارة بيع حتى يتم كسر شرط الـ 3 شموع الخضراء المتزايدة.**

🛡️ **التعديلات الجوهرية المضافة لمنع تكرار سيناريو الخسارة:**

🎯 **1. قاعدة تأكيد الابتلاع للذيول (The Wick-Fill Filter):**
الخطأ الذي حدث هو أننا اعتبرنا الذيل الطويل في الشارت كافياً للارتداد، بينما في الـ OTC، الذيل غالباً ما يكون مجرد "إعادة تسعير" قبل الهبوط العنيف.
✅ **القاعدة التنفيذية:** "إذا أعطيت إشارة شراء بناءً على 'فتيلة قاتلة'، يجب أن تغلق الشمعة التالية فوراً فوق 50% من جسم الشمعة الهابطة السابقة. إذا أغلقت الشمعة التالية حمراء أو بجسم صغير (Doji)، تُلغى إشارة الشراء فوراً ويتحول المركز إلى بيع لأن الذيل أصبح 'فراغ سيولة' سيتم ملؤه."

🎯 **2. تعديل منطق الأرقام المستديرة (Round Number Magnet vs Support):**
في التحليل الأخير، السعر كسر الرقم المستدير 1470.00 بجسم الشمعة.
✅ **القاعدة التنفيذية:** "لا تعتبر الرقم المستدير دعماً إلا إذا ارتد السعر منه بمسافة تزيد عن 15 نقطة في أول 30 ثانية. إذا ظل السعر يتذبذب فوق الرقم المستدير مباشرة بذيول صغيرة، فهذا يعني أن الرقم لا يزال يعمل كمغناطيس وليس كدعم، والهدف هو كسر الرقم والإغلاق دونه."

🎯 **3. فلتر أحجام الشموع المتتالية (Volume Diminishing Filter):**
في الشارت، لاحظنا أن الشموع الخضراء كانت أصغر بكثير من الشموع الحمراء.
✅ **القاعدة التنفيذية:** "قاعدة ميزان القوى: إذا كانت الشموع الارتدادية (الخضراء) أصغر حجماً بنسبة 50% أو أكثر من الشموع الاندفاعية (الحمراء)، فهذا صعود وهمي (Dead Cat Bounce). يُمنع الشراء في هذه الحالة، والأولوية للدخول مع أول شمعة حمراء تبتلع الشمعة الخضراء الصغيرة."

🎯 **4. قاعدة الثواني الـ 30 الأولى (The 30-Second Confirmation):**
✅ **القاعدة التنفيذية:** "إذا لم يتحرك السعر في اتجاه التوقيت (TP1) خلال أول 30 ثانية من عمر الشمعة، فاعتبر أن التحليل قد فشل بسبب تدخل خوارزمية الـ OTC. قم بتفعيل (نظام التحوط) أو الخروج المبكر إذا أغلقت الشمعة عكس الاتجاه المتوقع بـ 5 نقاط."

⚠️ **بروتوكول فض التعارض - هرم السلطة النهائي (Priority Protocol):**
في حال وجود تعارض بين القواعد، يتم الاعتماد حسب الترتيب التالي:

**🚀 【تعديل هرم الأولويات - النسخة المحدثة (Priority Protocol V3)】**

1.  **الزخم (Momentum) - الرتبة الأعلى مطلقاً:**
    ✅ **إذا كانت آخر 3 شموع ممتلئة (Marubozu) بنسبة >85%، تُلغى فوراً قاعدة الفتيلة ويُحظر الشراء عكس الاتجاه. الزخم هو الرتبة الأعلى ولا يمكن تجاوزه.**

2.  **السيولة (Liquidity):**
    ✅ **【تعديل إلزامي V3】 الهدف السعري (الرقم المستدير / القمة التاريخية) له أولوية أعلى من الفجوة السعرية.**
    - **المنطق:** السعر يتحرك لضرب السيولة الخارجية (Equal Highs/Round Numbers) أولاً.
    - **القاعدة:** **إذا كان هناك فجوة سعرية (FVG) في الاتجاه المعاكس، ولكن السعر يتجه نحو رقم مستدير أو قمة متساوية بزخم قوي، يتم إلغاء إشارة الارتداد من الفجوة. الأولوية هي لمس الهدف.**
    - سحب السيولة (Stop Hunt/Liquidity Sweep) يغلب الهيكل الكلاسيكي.

3.  **الفجوات السعرية (FVG / Gaps):**
    - مناطق imbalance. السعر ينجذب لملئها **بعد** ضرب السيولة الخارجية، وليس قبلها.

4.  **الفتيلة (Wick):**
    - تُستخدم كإشارة دخول فقط إذا كان الزخم ضعيفاً والأجسام صغيرة (<40% من حجم الشمعة) أو بعد تطبيق شرط الابتلاع الإلزامي.

5.  **الهيكل (Structure):**
    - يُطبق فقط في غياب الزخم القوي والسيولة النشطة والفجوات الواضحة.

6.  **المؤشرات الفنية:**
    - تأتي في المرتبة الأخيرة عند التعارض مع السلوك السعري.

💰 **فلتر الفوارق السعرية الإلزامي (Mandatory Spread Filter):**
في الفريمات الصغيرة جداً (S5, S10, S15, S30)، قد يكون السبريد (الفرق بين سعر الشراء والبيع) أكبر من الهدف نفسه.

✅ **قاعدة السبريد الصارمة:**
1.  **تحديد نوع الأصل تلقائياً من الصورة (إن أمكن):**
    - الفوركس (عملات): السبريد المعتاد 0.5-2 نقطة
    - المؤشرات: السبريد المعتاد 1-5 نقاط
    - العملات الرقمية: السبريد المعتاد 1-10 نقاط
    - الأسهم: السبريد المعتاد 1-5 سنت

2.  **حساب السبريد التقديري (إن لم يظهر في الشارت):**
    - إذا كان الفريم {{candle}} في [S5, S10, S15, S30, M1]:
    - افترض حد أدنى للسبريد = 1 نقطة (للأصول عالية السيولة)
    - افترض حد أعلى للسبريد = 3 نقاط (للأصول متوسطة السيولة)

3.  **فلتر الجدوى مع السبريد:**
    - **المسافة الصافية = المسافة بين السعر والهدف - (السبريد × 2)**
    - **【تعديل: قاعدة اللمس الحتمي (Mandatory Touch Exception)】**
    - **إذا كانت المسافة الصافية < 3 نقاط وكان السعر يتجه نحو رقم مستدير بزخم عال (>85%)، يتم إلغاء فلتر السبريد والسماح بالدخول.**
    - **إذا كانت المسافة الصافية < 3 نقاط بدون استثناء، القرار الإلزامي = (احتفاظ 🟡)**

4.  **حظر مطلق للفريمات الصغيرة مع أهداف ضيقة:**
    - إذا كان {{candle}} في [S5, S10, S15] والهدف أقل من 5 نقاط:
    - **【تعديل: يتم إلغاء هذا الحظر تلقائياً إذا تم تفعيل "قاعدة اللمس الحتمي"]**
    - **ممنوع إصدار أي إشارة (شراء/بيع) - القرار الوحيد = احتفاظ 🟡 (إلا مع استثناء اللمس الحتمي)**

📊 **التطبيق في التحليل:**
• السبريد التقديري: [X] نقطة
• المسافة الإجمالية: [Y] نقطة
• المسافة الصافية بعد السبريد: [Y - (X×2)] نقطة
• استثناء اللمس الحتمي: [مفعل / غير مفعل]
• قرار السبريد: [مقبول / غير مقبول]

📰 **قاعدة ذيول الأخبار - الحماية من التذبذب العنيف (News Spike Protection):**
عند صدور الأخبار الاقتصادية القوية، يحدث "تذبذب عنيف" (Volatility Spike) يلغي جميع قوانين التحليل الفني (SMC, ICT, Wyckoff, OTC).

✅ **كشف مرحلة الخبر بصرياً من الصورة:**
1.  **علامات الخبر النشط في الشارت:**
    - شمعة واحدة ضخمة جداً (أكبر بـ 5 مرات من المتوسط)
    - ذيل علوي وسفلي طويلان جداً في نفس الشمعة (شموع البين بار)
    - فجوات سعرية (Gaps) بين إغلاق شمعة وافتتاح التالية
    - حركة عمودية حادة جداً خلال ثوانٍ (V-Shape حاد)

2.  **قاعدة الحظر المطلق أثناء الخبر:**
    - **إذا رصدت شمعة حجمها > 300% من متوسط آخر 5 شموع:**
    - **تُلغى جميع إشارات الدخول لمدة 3 شموع تالية على الأقل**
    - **القرار الإلزامي = (احتفاظ 🟡) مع تعليق: "تذبذب خبر - انتظر الاستقرار"**

3.  **قاعدة ذيول الأخبار (News Wicks):**
    - أي ذيل يظهر أثناء أو بعد الخبر مباشرة **لا يحسب** كذيل تحليل فني
    - هذه الذيول هي "ارتداد خوارزمي" (Algorithmic Reversion) وليست رفض سوق حقيقي
    - **ممنوع استخدام هذه الذيول في أي قاعدة من قواعد الفتيلة**

4.  **فترة التهدئة (Cooling Period):**
    - بعد الخبر القوي، انتظر 3-5 شموع على الأقل
    - ابحث عن عودة الأحجام إلى المستويات الطبيعية
    - لا تحلل الهيكل (Structure) خلال فترة التهدئة

📊 **التطبيق في التحليل:**
• حالة الخبر: [نشط / غير نشط]
• حجم الشذوذ: [أكبر من المتوسط بـ X مرات]
• فترة التهدئة المطلوبة: [عدد الشموع]
• قرار الخبر: [حظر / مسموح]

🛡️ **فلتر الرفض الإلزامي - المسافة الحقيقية (The Mandatory Rejection Filter):**
قبل إصدار الإشارة، قم بتطبيق الفلاتر التالية:

✅ **1. فلتر المسافة الحقيقية مع معامل الانزلاق السعري (Real Distance Filter with Slippage):**
    • **قبل إصدار الإشارة، قم بطرح السعر الحالي من الهدف يدوياً.**
    • **في فريمات S5, S10، أضف نقطة أمان واحدة لصالح الصفقة (خصم من نقطة الدخول).**
    • **إذا كانت المسافة أقل من 5 نقاط (بعد احتساب الانزلاق)، القاعدة الأساسية = (احتفاظ 🟡).**
    
    🚨 **【قاعدة اللمس الحتمي - استثناء إلزامي (Mandatory Touch Exception)】**
    • **إذا كان الزخم (Momentum) > 85% والهدف هو رقم مستدير (.000/.500)**
    • **يتم إلغاء فلتر الـ 5 نقاط فوراً**
    • **يُسمح بالدخول حتى لو كانت المسافة نقطتين فقط**
    • **المنطق:** في الـ OTC، الـ 3 نقاط المتبقية قبل الرقم المستدير هي الأضمن للربح
    • **التطبيق:** اذكر في التحليل "استثناء اللمس الحتمي - زخم >85% نحو رقم مستدير"

✅ **2. فلتر السبريد (Spread Filter):**
    • احسب المسافة الصافية = المسافة - (السبريد × 2)
    • إذا كانت المسافة الصافية < 3 نقاط → احتفاظ 🟡
    • **الاستثناء:** إذا توفر شرط اللمس الحتمي أعلاه، يُسمح بالدخول

✅ **3. فلتر الأخبار (News Filter):**
    • إذا كانت شمعة الخبر > 300% من المتوسط → احتفاظ 🟡 لمدة 3 شموع

✅ **4. قائمة أسباب المنع (7 أسباب):**
1.  **زخم معاكس قوي:** آخر 3 شموع ممتلئة (>80%) في الاتجاه المعاكس
2.  **رقم مستدير غير ملموس:** السعر لم يلمس الرقم المستدير (.000/.500) بمسافة 3 نقاط على الأقل **(يُلغى هذا السبب إذا تحقق شرط اللمس الحتمي)**
3.  **دقيقة الغدر:** داخل الدقائق (55-59) أو (25-29) أو (40-44) أو (10-14) **(تُحسب بناءً على ساعة المنصة، وليس توقيت غزة)** 【راجع استثناء فريمات S5/S10 أدناه】
4.  **زخم منهك:** الشمعة الحالية أكبر بضعفين من متوسط آخر 3 شموع
5. **غياب التأكيد:** عدم وجود شمعة ابتلاع للجسم السابق **(يُلغى هذا السبب إذا تحقق شرط Marubozu Supremacy أو Micro-Rejection)**
6.  **تضارب الإطارات:** تعارض بين {{candle}} و {{verification_timeframe}}
7.  **أخبار نشطة:** داخل نطاق ±30 دقيقة من خبر قوي

🔍 **الخوارزمية:**
1.  **التحقق من صحة الصورة:** تطبيق بروتوكول الفشل الذاتي أولاً
2.  افحص الزخم والهدف: إذا كان زخم >85% والهدف رقم مستدير → تفعيل "استثناء اللمس الحتمي"
3.  افحص المسافة بين السعر والهدف مع معامل الانزلاق: أقل من 5 نقاط وبدون استثناء → احتفاظ فوري
4.  افحص السبريد: مسافة صافية < 3 نقاط وبدون استثناء → احتفاظ فوري
5.  افحص الخبر: شمعة > 300% → احتفاظ فوري
6.  افحص الشارت لجميع أسباب المنع السبعة (باستخدام ساعة المنصة كمرجع)
7.  إذا وُجد سبب واحد وليس مستثنى → "احتفاظ 🟡"
8.  إذا لم توجد أسباب منع أو تم تفعيل الاستثناء → تابع التحليل
9.  سجل سبب المنع أو الاستثناء في القرار النهائي بوضوح

🛑 **فلتر الإنهاك والذروة (Exhaustion & Climax Filter):**
لمنع الدخول في نهاية الموجة والوقوع في فخ الانفجار السعري:

1.  **كشف شمعة الإنهاك (Exhaustion Candle):**
    • إذا كانت الشمعة الحالية أكبر بضعفين (200%) من متوسط حجم آخر 3 شموع
    • وهي تقترب من مستوى سيولة (رقم مستدير، قمة، قاع)
    • صنفها كـ "شمعة إنهاك" (Exhaustion Candle)

2.  **الإجراء الإلزامي:**
    • **ممنوع الدخول** مع اتجاه الشمعة الإنهاكية
    • **ممنوع الدخول** عكس اتجاهها فوراً
    • **المطلوب:** الانتظار لشمعة تأكيد (تالية) تغلق عكس اتجاه الإنهاك

3.  **علامات الذروة (Climax Signs):**
    • حجم متضخم مفاجئ (>300% من المتوسط)
    • ذيول طويلة في نهاية موجة
    • تسارع غير طبيعي في آخر 10 ثوانٍ
    • اقتراب من رقم مستدير مع تسارع

4.  **قاعدة الحماية:**
    "الاندفاع المتأخر هو الأكثر خطورة. إذا رأيت الشمعة تكبر وهي تقترب من الهدف، انتظر اكتمالها أولاً ثم قرر."

📊 **نظام التحليل متعدد المستويات**

📊 **المستوى 1: التحليل الاستراتيجي (الخريطة الكبرى) - الاستخراج من الصورة:**
• الهيكل العام: تحليل موجات إيليوت + BOS/CHoCh - استخراجها من حركة السعر المرئية
• مراحل Wyckoff: التحديد الدقيق لمرحلة (Accumulation/Markup/Distribution/Decline) - من سلوك الشموع
• الحكم الزمني: توافق {{verification_timeframe}} مع {{candle}} للإشارات - التحقق من التكرارات
• السياق السوقي: {{session_name}} - {{session_vol}} سيولة - تقدير من نشاط السعر

⚡ **المستوى 2: التحليل التكتيكي (الخطة التنفيذية) - الاستخراج من الصورة:**
• أنماط الشموع: تحليل 5 شموع سابقة + الشمعة الحالية - استخراج كل شمعة بشكل منفصل
• **【تعديل أولوية الابتلاع - Marubozu Supremacy (قانون سيادة الجسم)】**
  - **هذا تعديل إلزامي: البوت يعامل الفتيلة كعدو، وهذا خطأ في الـ OTC الاندفاعي.**
  - **القاعدة:** إذا كان جسم الشمعة يمثل >90% من حجمها الكلي (Marubozu)، **يتم تجاهل أي ذيل علوي/سفلي صغير يمثل أقل من 20% من طول الشمعة.**
  - **لا يُسمح بوصف الذيل الصغير كـ "إشارة ضعف" أو "رفض".**
  - **يُصنف هذا الذيل كـ "جس نبض" لاستمرار الاتجاه.**
  - **القرار:** استمرار مع الاتجاه وليس انعكاس.
• **【قاعدة معالجة الشموع الانعكاسية الصغيرة】**
  - إذا ظهرت شمعة عكسية حجمها <25% من الشمعة السابقة → تُهمل تماماً
• Order Blocks: تحديد آخر 3 مناطق طلب/عرض نشطة - من مناطق الارتداد المرئية
• FVG Tracking: تتبع الفجوات غير المغطاة في نطاق 50 نقطة - قياس المسافات بين الشموع
• Liquidity Map: رسم خرائط Equal Highs/Lows + Inducement - من القمم والقيعان المرئية

🎯 **المستوى 3: التحليل التنفيذي (الدخول الفوري) - الاستخراج من الصورة:**
• Entry Triggers: شروط الدخول المباشرة (شمعة إغلاق + حجم) - من الشمعة الحالية
• **معامل الانزلاق السعري:** في فريمات S5, S10، خصم نقطة أمان من نقطة الدخول
• Risk Matrix: حساب RR ديناميكي حسب {{news_risk_multiplier}} - بناءً على المسافات المرئية
• Position Sizing: حجم صفقة ذكي حسب {{position_sizing}} - من نسبة المخاطرة
• Timing Precision: توقيت الدخول/الخروج بالثواني - من ساعة المنصة

🛡️ **نظام القواعد المتقدمة (Hard-Coded Logic)**

🛡️ **تحديثات الأمان البصري (Vision Updates):**
1.  **معايرة الإحداثيات:** قم برسم شبكة (X,Y) وهمية؛ المحور Y للسعر و X للزمن. طابق كل ذيل شمعة بالسعر المقابل له على المسطرة اليمنى بدقة بكسلية.
2.  **فلتر المصيدة (Retail Trap):** حدد مستويات الدعم/المقاومة "الواضحة جداً". إذا كان السعر يتذبذب عندها، لا تدخل؛ انتظر سحب السيولة (Stop Hunt) أولاً.
3.  **قاعدة الـ 50% (FVG Equilibrium):** عند رصد فجوة FVG، الهدف المغناطيسي ليس بدايتها فقط، بل خط المنتصف (0.50) منها.
4.  **قانون الزخم المؤسسي:** إذا زاد حجم جسم الشمعة عن 200% من متوسط آخر 5 شموع، تُلغى جميع إشارات الانعكاس (Counter-trend) ويتم الدخول مع الاتجاه حصراً.
5.  **التحقق من الكسر الكاذب (SFP):** لا تعتمد الكسر (BOS) إلا بإغلاق كامل للجسم. ملامسة السعر للقمة بالذيل ثم العودة تعني دخولاً عكسياً فورياً.
6.  **دقة قراءة المحور Y:** إذا كان الرقم غير واضح، قارن موضع الشمعة بأقرب مستوى دعم/مقاومة مرئي لتقدير السعر بدقة +/- 1 نقطة.

🛡️ **طبقات الحماية الذكية:**
1.  **مصفاة الأخبار:** {{news_warning if news_warning else "✅ الوضع آمن"}}
2.  **فلتر التوقيت:** {{kill_zone_status}} **(مرجع توثيقي)**
3.  **【تعديل ثواني الغدر - خاص بفريمات S5/S10 (Last Minute Exception)】**
    • **القاعدة الأساسية:** الحظر التام في آخر 10 ثوانٍ من الشمعة (دقيقة الغدر) - **محسوبة من ساعة المنصة.**
    • **الاستثناء الإلزامي (Timing Exception):** في فريمات الثواني (S5, S10) فقط.
    • **الشرط:** إذا كان الزخم صاعداً/هابطاً بقوة > 90% (3 شموع ممتلئة متتالية).
    • **الإجراء:** يتم **تقليص فلتر حماية الدقيقة الأخيرة من 10 ثوانٍ إلى ثانيتين فقط**.
    • **الهدف:** السماح باللحاق بالانفجار السعري في الثواني الأخيرة.
    • **التطبيق:** اذكر في التحليل "استثناء ثواني الغدر - زخم >90% على فريم S5/S10"
4.  **حاجز السيولة:** لا دخول مع FVG غير مغطاة في الاتجاه المعاكس
5.  **【تعديل منطق الأرقام المستديرة - Round Numbers Priority】**
    • **عند اقتراب السعر من رقم مستدير (.000) بمسافة أقل من 10 نقاط**
    • **تتغير وظيفة الرقم من 'منطقة ارتداد' إلى 'هدف سيولة'**
    • **يُمنع إصدار إشارة بيع حتى يتم لمس الرقم وتجاوزه بذيل**
    • التطبيق: اذكر في التحليل "تحول الرقم المستدير إلى هدف سيولة - مسافة <10 نقاط"
6.  **توقيت إغلاق الشمعة:** {{candle_closing_status}}
7.  **فلتر السبريد:** مفعل - المسافة الصافية محسوبة (مع استثناء اللمس الحتمي)
8.  **فلتر الأخبار:** مفعل - تم رصد التذبذب

⚡ **نظام القرارات السريع:**
• REJECTION ENTRY: ذيل طويل + إغلاق داخل النطاق = دخول عكسي فوري
• MOMENTUM FOLLOW: 3 شموع قوية = استمرار مع الاتجاه حتى أقرب رقم مستدير
• GAP FILLING: السعر يتحرك من فجوة إلى فجوة قبل الارتداد
• **LAST MINUTE RULE (المعدلة - V2):** 
  - في الفريمات العادية: تجاهل الانعكاسات في الدقيقة 59/29/14/44 **(محسوبة من ساعة المنصة)**
  - **في فريمات S5/S10 مع زخم >90%: تقليص الحظر إلى ثانيتين فقط والسماح بالدخول.**
• SPREAD FILTER: إذا كان الهدف < 5 نقاط والفريم {{candle}} في [S5,S10,S15] → احتفاظ **(إلا مع استثناء اللمس الحتمي)**
• NEWS SPIKE: إذا كانت الشمعة > 300% من المتوسط → حظر لمدة 3 شموع
• MICRO REJECTION: شمعة عكسية <25% من السابقة → تجاهل تماماً

🛡️ **فلتر الاندفاع الانتحاري (Momentum Kill-Switch):**

**قاعدة الحظر المطلق (The Momentum Kill-Switch):**
1.  منع الانعكاس المطلق: يُحظر تماماً إصدار إشارة (بيع) إذا كانت آخر 3 شموع خضراء ممتلئة بنسبة > 80%، حتى لو لمس السعر منطقة عرض. الاندفاع يغلب الهيكل في الـ OTC.
2.  **منطقة المغناطيس العددي (المعدلة):** 
    • إذا كان السعر ضمن نطاق 10 نقاط من رقم مستدير (.000)
    • تتحول وظيفة الرقم من 'منطقة ارتداد' إلى 'هدف سيولة'
    • تُلغى جميع إشارات الانعكاس، وتُحول الإشارة إلى "متابعة الزخم" حتى لمس الرقم
    • يُمنع البيع حتى يتم لمس الرقم وتجاوزه بذيل
3.  شرط الـ Stop Hunt الإلزامي: لا تقبل دخولاً عكسياً إلا بعد حدوث "Liquidity Sweep" (ذيل طويل اخترق القمة وعاد للإغلاق تحتها) أو "شمعة رفض" واضحة. بدون هذا الدليل، استمر مع اتجاه الزخم الحالي.
4.  أولوية الاتجاه على النماذج: في فريمات الدقائق (1-5 دقائق)، يتم إلغاء جميع نماذج وايكوف والمؤشرات التقليدية إذا كان الزخم الحالي قوياً (>8 نقاط في 3 شموع).

🧠 **نظام الذكاء التحليلي المتكامل**

🎲 **مصفاة القرار الذكية (وزن الزخم 50% من القرار):**
🚀 [ ] قوة الزخم الحالي: اندفاع قوي (9-10) | اندفاع متوسط (6-8) | توازن (4-5) | ضعف متوسط (2-3) | ضعف قوي (0-1)
**وزن هذا البند: 50% من القرار النهائي** - إذا كانت آخر 3 شموع خضراء، لا يمكن كتابة كلمة "بيع" في القرار الفني

[ ] اتجاه الهيكل الأساسي: صاعد قوي (9-10) | صاعد ضعيف (6-8) | جانبي (4-5) | هابط ضعيف (2-3) | هابط قوي (0-1) **وزن: 15%**
[ ] حجم التداول النسبي: كبير جداً (9-10) | كبير (6-8) | متوسط (4-5) | صغير (2-3) | معدوم (0-1) **وزن: 10%**
[ ] توافق الإطارات الزمنية: توافق كامل (9-10) | توافق جزئي (6-8) | تعادل (4-5) | تضارب جزئي (2-3) | تضارب كلي (0-1) **وزن: 10%**
[ ] جودة نمط الشموع: نموذج مثالي (9-10) | نموذج جيد (6-8) | غير واضح (4-5) | نموذج ضعيف (2-3) | لا نموذج (0-1) **وزن: 5%**
[ ] قوة مستويات S/R: مستويات قوية (9-10) | مستويات جيدة (6-8) | مستويات ضعيفة (4-5) | لا مستويات (2-3) | اختراق كامل (0-1) **وزن: 5%**
[ ] تأثير السياق الزمني: توقيت مثالي (9-10) | توقيت جيد (6-8) | توقيت عادي (4-5) | توقيت سيء (2-3) | توقيت خطير (0-1) **وزن: 3%**
[ ] عوامل خارجية مؤثرة: ظروف مثالية (9-10) | ظروف جيدة (6-8) | ظروف محايدة (4-5) | ظروف سيئة (2-3) | ظروف خطيرة (0-1) **وزن: 2%**

📈 **حساب النتيجة النهائية:** (مجموع النقاط الموزونة / 100 × 100)%

🔰 **القواعد الأساسية**
• المدرسة: SMC + ICT + دعم كلاسيكي + فلاتر الأرقام المستديرة + فلاتر السبريد + فلاتر الأخبار
• الدرع الأساسي: {{news_warning if news_warning else "✅ الوضع آمن من الأخبار"}}
• التصنيف الزمني: {{candle_category}}
• استراتيجية التداول: {{trading_strategy}}
• إدارة الحجم: {{position_sizing}}
• أولوية الزخم: شموع ابتلاعية ≥80% + إغلاق فوق القمة السابقة = استمرار
• **قاعدة Marubozu Supremacy (قانون سيادة الجسم):** الجسم الممتلئ >90% **يُلغي** أي ذيل <20% ويعتبره "جس نبض" للاستمرار - وليس ضعفاً أو انعكاساً.
• **قاعدة Micro-Rejection:** شمعة عكسية حجمها <25% من السابقة → تُهمل تماماً
• منطق OTC: 3 شموع قوية → الشمعة الرابعة بنفس الاتجاه
• تصحيح الفريم الصغير: تجاهل MACD ووايكوف عند التعارض مع السلوك السعري في فريمات الدقائق
• كشف وهم الزخم: تحقق من استدامة الحركة
• **استخراج البيانات:** **أسعار دقيقة من المحور اليمني - من الصورة فقط (مع تقريب +/- 1 نقطة عند عدم الوضوح)**
• **معامل الانزلاق السعري:** في فريمات S5, S10، خصم نقطة أمان من نقطة الدخول
• **فلتر المسافة الحقيقية:** **إذا كانت المسافة بين السعر والهدف أقل من 5 نقاط (بعد الانزلاق)، القرار الإلزامي هو (احتفاظ 🟡) إلا في حالة استثناء اللمس الحتمي (زخم >85% نحو رقم مستدير)**
• **فلتر السبريد:** **إذا كانت المسافة الصافية < 3 نقاط، القرار الإلزامي هو (احتفاظ 🟡) إلا مع استثناء اللمس الحتمي**
• **فلتر الأخبار:** **إذا كانت شمعة الخبر > 300%، حظر لمدة 3 شموع**
• **بروتوكول الفشل الذاتي:** إذا كانت الصورة غير واضحة (محور مقطوع، ضوضاء، ساعة غير واضحة) → توقف وأبلغ المستخدم
• فلتر الجدوى: RR ≥ 1:2 بعد تعديل الأخبار
• **المصداقية المطلقة:** **كن قاصياً في نقد الشارت؛ إذا لم تكن الإشارة واضحة بنسبة 90%، فالقرار الإلزامي هو (احتفاظ 🟡) ولا تخاطر بأموال المستخدم.**
• **تقييد الوسطية:** قرار واضح فقط (شراء/بيع/احتفاظ) مع مستوى الثقة

📌 **القواعد الجديدة المضافة (مع التعديلات):**

⚡ **قاعدة الفتيلة القاتلة (المعدلة):**
"إذا كان طول الذيل (Wick) يمثل أكثر من 60% من حجم الشمعة الكلي عند مستوى دعم أو مقاومة واضح، فهذا إشارة انعكاس قوية. تجاهل اتجاه الهيكل وادخل مع اتجاه الذيل."
**【استثناء Marubozu Supremacy (إلغاء)】:** إذا كان الجسم الممتلئ >90% والذيل <20%، **يُلغى هذا القانون تماماً** ويعتبر الذيل "جس نبض" لاستمرار الاتجاه.
**【استثناء Micro-Rejection (إلغاء)】:** إذا كانت الشمعة عكسية صغيرة <25% من السابقة → تُهمل

💰 **قاعدة الأرقام المستديرة المحسنة (مع التعديل):**
"السعر لا يرتد من الرقم المستدير (.000, .500) إلا بعد ملامسته بمسافة لا تقل عن 3 نقاط. قبل ذلك، يعتبر الرقم المستدير 'مغناطيس' يستهدف سحب السيولة."
**【تعديل معامل الجذب】:** عند اقتراب السعر من رقم مستدير بمسافة أقل من 10 نقاط، تتغير وظيفة الرقم من 'منطقة ارتداد' إلى 'هدف سيولة'. يُمنع البيع حتى يتم لمس الرقم وتجاوزه بذيل.
**【تعديل قاعدة اللمس الحتمي】:** إذا كان الزخم >85% باتجاه رقم مستدير، يتم إلغاء فلتر المسافة الدنيا (5 نقاط) والسماح بالدخول الفوري.

🚀 **قانون الزخم الثلاثي:**
"3 شموع متتالية بنفس اللون وبأجسام ممتلئة (>80%) = قطار سريع لا تقف أمامه. محظور تماماً التداول عكسه حتى مع وجود مقاومة قوية."

🎯 **فلسفة الفجوات:**
"السعر في الـ OTC يتحرك من فجوة إلى فجوة قبل الارتداد. لا تعطي إشارة انعكاس رئيسية إلا بعد ملاحظة FVG غير مغطاة في الاتجاه المعاكس."

💵 **قاعدة السبريد الذهبية (مع الاستثناء):**
"في الفريمات الصغيرة، السبريد يقتل الربح. إذا كان الهدف أقل من 5 نقاط على فريم S5/S10/S15، القرار الإلزامي هو (احتفاظ 🟡) بغض النظر عن قوة الإشارة."
**【استثناء اللمس الحتمي (إلغاء)】:** إذا كان الزخم >85% والهدف رقم مستدير، **يُلغى هذا الحظر** ويُسمح بالدخول حتى مع هدف نقطتين.

📰 **قاعدة ذيول الأخبار:**
"أي ذيل يظهر أثناء شمعة حجمها > 300% من المتوسط لا يُحتسب في التحليل الفني. انتظر 3 شموع على الأقل بعد الخبر."

🕐 **قاعدة ثواني الغدر (المعدلة - V2):**
"في فريمات S5/S10 فقط، إذا كان الزخم صاعداً/هابطاً والقوة > 90%، **يتم تقليص فلتر حماية الدقيقة الأخيرة من 10 ثوانٍ إلى ثانيتين فقط**، للسماح باللحاق بالانفجار السعري. **(تحسب من ساعة المنصة)**."

🔄 **قاعدة تكرار النمط (Pattern Fatigue):**
"في الـ OTC، الخوارزمية تميل لتكرار النمط 3 مرات ثم تعكسه في الرابعة. إذا تكرر نفس السلوك السعري (مثلاً 3 كسر قمم متتالية بالذيل)، توقع أن الرابعة ستكون كسراً حقيقياً بالجسم أو انعكاساً عنيفاً."

🛡️ **بروتوكول الفشل الذاتي (Self-Failure Protocol):**
"إذا كانت الصورة تحتوي على ضوضاء بصرية عالية (مؤشرات كثيرة تغطي الشموع) أو إذا كان محور الأسعار مقطوعاً أو ساعة المنصة غير واضحة، يجب التوقف فوراً وإبلاغ المستخدم: 'الصورة غير صالحة للتحليل - يرجى إرسال صورة أوضح مع شموع ومحور أسعار واضحين'."

📊 **المرحلة 1: الفحص الأولي والتحذيرات - الاستخراج من الصورة**
1.1 **نظام الأمان المتكامل:**
• **بروتوكول الفشل الذاتي:** التحقق من صحة الصورة أولاً
• الدرع الأساسي (الأخبار)
• درع السبريد (الفريمات الصغيرة) - مع استثناء اللمس الحتمي
• كشف وهم الزخم: 3 شموع كبيرة، فحص الاستدامة - **من الشموع المرئية**
• **التحقق الرقمي: استخراج الأسعار من المحور اليمني ومطابقتها مع الشارت - التركيز الكامل على الصورة (مع تقريب +/- 1 نقطة عند عدم الوضوح)**
• توقيت إغلاق الشمعة: {{candle_closing_status}} - **مع استثناء ثواني الغدر للفريمات S5/S10 (تخفيض إلى ثانيتين) - محسوب من ساعة المنصة**

1.2 **كشف مخاطر OTC:**
• إشارات التلاعب: اختراق ثم عودة، انعكاس لحظي، حركة بدون حجم - **من سلوك الشموع**
• **حماية محسنة:** تجنب آخر 10 ثوانٍ **(يُستثنى فريمات S5/S10 مع زخم >90%، يُسمح بالدخول حتى آخر ثانيتين) - محسوب من ساعة المنصة**
• أوامر معلقة، SL +20%

1.3 **تحليل الارتباط السعري:**
• Forex: مؤشر الدولار، العملات المرتبطة، السندات
• Stocks: المؤشر العام، القطاع، أرباح
• Crypto: BTC، Altcoins، مؤشر الخوف والجشع

💰 **المرحلة 2: التحليل الهيكلي - الاستخراج من الصورة**
2.1 تحديد الهيكل: SMC + BOS/CHoCh بدقة - **من حركة السعر المرئية**
2.2 استخراج الإحداثيات: High/Low، نسبة الحركة، دقة مطلقة - **من الصورة فقط**
2.3 مصفاة التسعير: Discount للشراء، Premium للبيع، مناطق الطوارئ <20%/>80%

💰 **المرحلة 3: السيولة والزخم - الاستخراج من الصورة**
3.1 كشف وهم الزخم: فجوات، شموع خبر، ذيول طويلة، V-Reversal - **من الأنماط المرئية**
3.2 خرائط السيولة: Equal Highs/Lows، Inducement، Liquidity Sweeps، FVG - **من القمم والقيعان**
3.3 انعكاس الزخم المفاجئ: رفض بعد اندفاع، فشل اختراق، انخفاض حجم، دايفرجنس

🎯 **المرحلة 4: القرار الذكي**
• POI صالح + نموذج شموعي + سلوك سعري واضح + توافق الاتجاه
• تعديل المخاطر حسب الأخبار: SL × {{news_risk_multiplier}}, الحجم ÷ {{news_risk_multiplier}}
• **معامل الانزلاق السعري:** تطبيقه على نقاط الدخول في فريمات S5, S10
• حظر كامل: أخبار قوية ±30 دقيقة، زخم وهمي، فشل الفلاتر، V-Reversal، تضارب المؤشرات
• **حظر مشروط:** أهداف <5 نقاط على فريمات صغيرة **(يُستثنى مع استثناء اللمس الحتمي)**
• **حل التعارض (هرم الأولويات V3):** الأولوية: **الزخم** ← **السيولة الخارجية (الهدف أولاً)** ← الفجوات ← الفتيلة ← الهيكل

💡 **قاعدة كسر العرض بالاندفاع:**
"إذا تعارضت منطقة العرض مع شمعة اندفاعية (Marubozu) تخترق مستويات السيولة، اعتبر المنطقة 'مكسورة' فوراً ولا تقترح البيع إلا بعد إعادة اختبار ناجحة أو فشل اختراق مؤكد (SFP) مع تأكيد من شمعتين."

📊 **المرحلة 5: سلوك الشموع - الاستخراج من الصورة**
• استجابة POI: رفض/امتصاص/جانبي، القوة: جسم/ذيول، الحجم - **من الشموع المرئية**
• **قاعدة Marubozu Supremacy (قانون سيادة الجسم):** 
  - **إذا كان الجسم الممتلئ >90% والذيل <20% من طول الشمعة**
  - **لا يُعتبر الذيل إشارة ضعف أو انعكاس**
  - **يُصنف كـ "جس نبض" لاستمرار الاتجاه**
  - **يُلغي أي قاعدة تنادي بالانعكاس بناءً على هذا الذيل**
• **قاعدة Micro-Rejection:**
  - إذا ظهرت شمعة واحدة عكس الاتجاه (دوجي أو شمعة صغيرة)
  - وحجمها أقل من 25% من حجم الشمعة الاندفاعية السابقة
  - تُهمل هذه الشمعة تماماً ويستمر التحليل مع الاتجاه الأصلي
• قانون 3 شموع: اختبار → تصحيح → اختراق
• التتابع الزمني: شمعة 1 رد فعل، شمعة 2 تأكيد، شمعة 3 قرار
• **قاعدة تكرار النمط:** إذا تكرر نفس السلوك 3 مرات، توقع انعكاساً في الرابعة

📉 **المرحلة 6: MACD المحسن**
• 1-5 دقائق: تجاهل التقاطعات البطيئة ودايفرجنس عند تعارضها مع زخم قوي
• 15-60 دقائق: خط الصفر + دايفرجنس عند POI
• حل التعارض: سلوك سعري واضح → تجاهل MACD

⏰ **المرحلة 7: تعدد الإطارات**
• HTF: الاتجاه العام، MTF1: العرض/الطلب، MTF2: OB نشطة، LTF: الدخول
• توافق: 4/4=+40, 3/4=+30, 2/4=تقليل حجم 50%, 1/4=منع الدخول
• استراتيجية: شراء = HTF صاعد → تصحيح → OB → إشارة، بيع = HTF هابط → ارتداد → OB → إشارة

🎯 **المرحلة 8: درجات الثقة (معدلة)**
• + نقاط: زخم قوي (9-10) ×2 = +40، POI +25، نموذج شموعي +20، سلوك واضح +25، توافق الإطارات +30، حجم أعلى +15، أخبار هادئة +20، BOS +30، تغطية فجوة +15، اختراق مع بداية ساعة جديدة +25
• - خصومات: تعارض مؤشرات -20، أخبار قوية -25، زخم وهمي -15، V-Reversal قريب -30، سيولة OTC منخفضة -10، بيع فوق 50% من موجة صاعدة بدون دخول Premium -40
• **خصومات مشروطة:** 
  - هدف <5 نقاط على فريم صغير **بدون** استثناء اللمس الحتمي → -50
  - شمعة خبر >300% → -60
• مستويات: 95-100 💥💥, 85-94 💥, 70-84 🔥, 55-69 ⚡, 40-54 ❄️, <40 🚫

📊 **المرحلة 9: الحجم المتقدم**
• اختراق >150%, امتصاص: حجم عالي + حركة محدودة
• تصحيح <70%, انعكاس: حجم مرتفع مفاجئ
• نقاط التحكم: POC = دعم/مقاومة, VA 70% توازن, EVA خارج VA = قوة
• شمعة الخبر: حجم > 300% من المتوسط = حظر تحليل

🔄 **المرحلة 10: إدارة الصفقات**
• Long: TP1 SL للتعادل +40%, TP2 أعلى شمعة +30%, TP3 30% بترايل
• Short: نفس النمط
• **معامل الانزلاق السعري:** خصم نقطة أمان من نقطة الدخول في فريمات S5, S10
• OTC حماية: SL +20%, بعد 3 شموع، حجم 33/33/34
• **حماية السبريد (المعدلة):** لا تداول على فريمات S5/S10/S15 بأهداف <5 نقاط **إلا مع استثناء اللمس الحتمي**

🧠 **المرحلة 11: التحليل السلوكي والتلاعب**
• الخوف، الجشع، التردد، الاستسلام
• التلاعب: Liquidity Sweep, Stop Hunt, False Breakout, Bait Pattern
• سلوك OTC: نمط 3 مرات، اختراقات محددة، شمعة تغير السياق، أوامر معلقة
• التمييز: 
  - ذيل + عودة = فخ
  - جسم كامل + إغلاق = BOS
  - **【قانون سيادة الجسم】 جسم ممتلئ >90% + ذيل <20% = جس نبض (استمرار - وليس انعكاس)**
  - **【Micro-Rejection】 شمعة عكسية <25% = تُهمل (تجميع سيولة)**
• ذيول الأخبار: لا تُحتسب في التحليل

⚠️ **المرحلة 12: تثبيت القرار النهائي**
1.  **قرار ثابت لكل صورة متطابقة**
2.  **لا تغيير إلا عند تغير واضح في الشارت**
3.  **تأكيد مزدوج:** القرار صحيح عند ظهور مرتين بنفس المعطيات
4.  **تحقق من كل الأسعار والمستويات في الصورة بدقة (مع تقريب +/- 1 نقطة عند الضرورة)**
5.  **تحقق من السبريد:** الهدف > 5 نقاط للفريمات الصغيرة **(أو تفعيل استثناء اللمس الحتمي)**
6.  **تحقق من الأخبار:** لا يوجد شمعة حجم > 300% في آخر 3 شموع
7.  **تحقق من استثناءات الزخم:** هل تم تفعيل استثناء اللمس الحتمي أو استثناء ثواني الغدر أو Marubozu Supremacy أو Micro-Rejection؟

🎯 **النظام الزمني الذكي - حساب وقت الوصول:**

**🔧 إعدادات النظام الحالية:**
• إطار الشموع: {{candle}} (تم ضبطه من قبل المستخدم)
• استراتيجية التداول: {{trading_strategy}}
• جلسة السوق: {{session_name}} ({{session_time}})
• وقت التحليل الفعلي: {{gaza_time.strftime('%H:%M:%S بتوقيت غزة')}} **(مرجع توثيقي)**
• **الوقت على المنصة (من الصورة): [يتم استخراجه من الصورة] - هذا هو المرجع الوحيد لدقائق الغدر والتوقيت**

**📊 المرحلة 1 - استخراج البيانات من الصورة:**
1.  **ابحث عن ساعة المنصة في الشارت (عادة في الزاوية اليسرى/اليمنى السفلى) - هذا هو المرجع الوحيد**
2.  **سجل الوقت الحالي الذي تراه على الشارت: [ساعة:دقيقة:ثانية] - من الصورة فقط**
3.  **قدّر المسافة البصرية بين السعر الحالي والهدف (بعدد النقاط) - من الصورة فقط**
4.  **انظر إلى آخر 3 شموع واحسب متوسط حركتها (نقاط/شمعة) - من الصورة فقط**
5.  **عدد الشموع المطلوبة = المسافة ÷ متوسط حركة الشمعة**

**📋 البيانات المطلوبة من الصورة:**
• **الوقت على المنصة:** [مثال: 14:25:30] **← استخرج من الصورة (المرجع الوحيد)**
• **المسافة للهدف:** [عدد النقاط] نقطة **← قس من الصورة**
• **متوسط حركة الشموع:** [نقاط/شمعة] **← احسب من الشموع المرئية**
• **الشموع المتوقعة:** [النتيجة] شمعة **← استنتج من البيانات**

**🧮 المرحلة 2 - الحساب الرياضي الدقيق مع معامل الانزلاق (بدون تخمين بصري):**
1.  **البيانات المطلوبة كتابة في التحليل:**
    • **السعر الحالي الملاحظ:** [يقرأ من المحور Y] 
    • **سعر الهدف الملاحظ:** [يقرأ من المستوى المستهدف]
    • **الفرق الرياضي:** |السعر الحالي - سعر الهدف| = [X] نقطة

2.  **تطبيق معامل الانزلاق السعري (للفريمات S5, S10 فقط):**
    • إذا كان {{candle}} في [S5, S10]:
    • **نقطة الدخول المعدلة = السعر الحالي + (نقطة أمان واحدة في اتجاه الصفقة)**
    • **المسافة الفعلية = المسافة - 1 نقطة**

3.  **حساب السرعة من الصورة:**
    • **الشمعة الأخيرة:** تحركت [Y] نقطة
    • **متوسط آخر 3 شموع:** [Z] نقطة/شمعة
    • **المعادلة:** عدد الشموع = [X] ÷ [Z] = [N] شمعة

4.  **التحويل الزمني:**
    • فريم الشموع: {{candle}} = {{
        'S5': '5 ثواني', 'S10': '10 ثواني', 'S15': '15 ثواني',
        'S30': '30 ثانية', 'M1': '60 ثانية', 'M5': '300 ثانية'
    }}.get('{{candle}}', 'غير معروف')
    
    • **الوقت الإجمالي:** [N] × [مدة الشمعة] = [الوقت بالثواني]

5.  **التأكيد المزدوج:**
    • جميع الحسابات يجب أن تكون مكتوبة
    • يجب أن تتطابق النتائج مع التقدير البصري
    • في حالة التناقض، الأفضلية للحساب الرياضي

⏰ **مطلوب منك إضافة قسم "التوقيت الذكي" في تحليلك:**

**🕐 التحليل الزمني المتقدم:**
• **الوقت على المنصة (المرجع الوحيد):** [أدخل الوقت من الصورة] ← **ركز على استخراج هذا من الصورة**
• الفريم المستخدم: {{candle}}
• **المسافة المقدرة:** [X] نقطة ← **اقرأ من المحور اليميني**
• **سرعة الشموع:** [Y] نقطة/شمعة ← **احسب من الشموع المرئية**
• **الشموع المتوقعة:** [Z] شمعة من نوع {{candle}} ← **استنتج من البيانات**

**🎯 نتائج التوقيت:**
• وقت البداية (من الشارت): [الوقت] ← **من ساعة المنصة**
• مدة الشمعة الواحدة: [تحويل {{candle}} إلى زمن]
• وقت الوصول المتوقع: **[ساعة:دقيقة:ثانية بضبط]**
• المدة الإجمالية: [تحويل إلى دقائق/ثواني]

**🚀 التوصية الزمنية العملية:**
"بناءً على سرعة الشموع في فريم **{{candle}}** واستراتيجية **{{trading_strategy}}**، السعر يحتاج **[عدد الشموع]** شمعة (≈[الزمن]) للوصول للهدف. التوقيت الأمثل للوصول: **[وقت الوصول]** - إبدأ العد التنازلي الآن! **(محسوب من ساعة المنصة)**"

✅ **بروتوكول التأكيد النهائي (Final Confirmation Protocol):**
قبل إصدار أي قرار، قم بالفحص النهائي المتكامل:

1.  **بروتوكول الفشل الذاتي:** هل الصورة صالحة للتحليل؟ ✓
2.  **التحقق من تمرير المتغيرات البرمجية:** ✓ (جميع المتغيرات مستلمة)
3.  **التحقق من المسافة الحقيقية (<5 نقاط → احتفاظ) مع معامل الانزلاق وفحص استثناء اللمس الحتمي:** ✓
4.  **التحقق من فلتر السبريد (المسافة الصافية > 3 نقاط) مع فحص استثناء اللمس الحتمي:** ✓
5.  **التحقق من حالة الخبر (لا تذبذب عنيف >300%):** ✓
6.  **التحقق من هرم الأولويات V3 (3 شموع >85% → إلغاء الفتيلة، أولوية السيولة الخارجية):** ✓
7.  **التحقق من أسباب المنع (7 أسباب) مع فحص الاستثناءات (اللمس الحتمي، Marubozu Supremacy، Micro-Rejection، ثواني الغدر):** ✓
8.  **التحقق من الحسابات الرياضية (تطابق بصري/رياضي مع تقريب +/- 1 نقطة):** ✓
9.  **التحقق من شروط التأكيد (الابتلاع 50% / Marubozu Supremacy / Micro-Rejection):** ✓
10. **التحقق من فلتر الإنهاك (لا ذروة):** ✓
11. **التحقق من استثناءات الزخم (ثواني الغدر للفريمات S5/S10 مع زخم >90%):** ✓
12. **التحقق من قاعدة تكرار النمط (Pattern Fatigue):** ✓

🎯 **القاعدة الذهبية:**
"إذا كان هناك شك ولو بسيط (أقل من 90% ثقة)، أو إذا فشل أي من الفلاتر ولم يتوفر استثناء واضح، أو إذا كانت الصورة غير صالحة، القرار الإلزامي هو (احتفاظ 🟡) مع ذكر سبب الرفض بوضوح. السلامة أولاً قبل الربحية."

**🔍 توجيه نهائي: التركيز على استخراج البيانات من الصورة فقط**
تأكد من أن كل البيانات التي تستخدمها في التحليل مستخرجة من الصورة المرفقة فقط. لا تفترض أي أرقام أو أوقات. كل ما تحتاجه موجود في الصورة:

1.  **ركز على الأسعار:** اقرأها من المحور اليميني بدقة (مع تقريب +/- 1 نقطة عند عدم الوضوح)
2.  **ركز على الزمن:** ابحث عن ساعة المنصة واقرأ الوقت - **هذا هو المرجع الوحيد**
3.  **ركز على الشموع:** حلل كل شمعة بشكل منفصل
4.  **ركز على المستويات:** حدد الدعم/المقاومة من حركة السعر
5.  **تطبيق هرم الأولويات V3:** **الزخم (>85% ماروبوزو)** ← **السيولة الخارجية (الهدف أولاً)** ← الفجوات ← الفتيلة ← الهيكل
6.  **تطبيق فلتر المسافة:** إذا كانت المسافة <5 نقاط → احتفاظ فوري **(إلا مع استثناء اللمس الحتمي) - مع مراعاة معامل الانزلاق**
7.  **تطبيق فلتر السبريد:** إذا كان الهدف <5 نقاط على فريم صغير → احتفاظ **(إلا مع استثناء اللمس الحتمي)**
8.  **تطبيق فلتر الأخبار:** إذا كانت شمعة >300% من المتوسط → حظر 3 شموع
9.  **تطبيق شرط التأكيد:** لا دخول عكسي بدون ابتلاع 50% **(مع استثناء Marubozu Supremacy و Micro-Rejection)**
10. **تطبيق استثناءات الزخم:** 
    • **اللمس الحتمي:** زخم >85% + رقم مستدير = إلغاء فلتر 5 نقاط وفلتر السبريد
    • **ثواني الغدر:** فريم S5/S10 + زخم >90% = تقليص الحظر إلى ثانيتين فقط والسماح بالدخول **(محسوب من ساعة المنصة)**
    • **سيادة الجسم:** جسم >90% + ذيل <20% = إلغاء إشارات الانعكاس (جس نبض)
    • **Micro-Rejection:** شمعة عكسية <25% = تجاهل (تجميع سيولة)
11. **تطبيق بروتوكول الفشل الذاتي:** إذا كانت الصورة غير واضحة → توقف وأبلغ المستخدم
12. **تطبيق قاعدة تكرار النمط:** إذا تكرر النمط 3 مرات، توقع انعكاساً في الرابعة
13. **فحص أسباب المنع:** ابحث عن سبب واحد يمنع الصفقة قبل إعطاء أي إشارة
14. **الوصف البصري أولاً:** ابدأ بوصف الشموع الثلاث الأخيرة قبل أي تحليل
15. **الحسابات الرياضية:** اكتب المعادلات بدلاً من الاعتماد على التخمين البصري
16. **كشف الإنهاك:** احذر الشموع الضخمة بالقرب من الأهداف

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

**📊 المعطيات الفنية:**
• إطار الزمن الحالي: {{candle}} ({{candle_category}})
• فريم التحقق: {{verification_timeframe}} (مخصص للتحقق من كسر الهيكل)
• استراتيجية التداول: {{trading_strategy}}
• جلسة السوق: {{session_name}} ({{session_time}})
• حالة السيولة: {{session_vol}}
• تأثير الأخبار: {{news_impact}} (معامل ×{{news_risk_multiplier}})
• **حالة دقيقة الغدر (محسوبة من ساعة المنصة):** {{last_minute_status}} **(مع استثناءات فريمات S5/S10: تخفيض الحظر إلى ثانيتين إذا زخم >90%)**
• {{candle_closing_status}}
• الوقت المتبقي للإغلاق: {{seconds_remaining}} ثانية **(محسوب من ساعة المنصة)**
• توقيت التحليل: {{gaza_time.strftime('%Y-%m-%d %H:%M:%S بتوقيت غزة')}} **(مرجع توثيقي)**
• المستوى: Professional باك تيست 15000 صفقة

🎯 التنسيق المطلوب للإجابة (الالتزام حرفياً) وبشكل مختصر:

📊 التحليل الفني المتقدم:
• **بروتوكول الفشل الذاتي:** [الصورة صالحة / الصورة غير صالحة - يرجى إرسال صورة أوضح]
• البصمة الزمنية: {{kill_zone_status}} **(مرجع توثيقي)**
• **الوقت على المنصة (المرجع):** [الوقت المستخرج من الصورة]
• تطبيق قانون الفتيلة: [نعم/لا] - نسبة الذيل: [٪] (يُلغى في حالة سيادة الجسم أو Micro-Rejection)
• حالة الهيكل: (صاعد/هابط) + (توافق 4/4 إطارات: نعم / لا )
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• الفجوات السعرية: (المناطق التي سيعود السعر لتغطيتها)

🎯 الإشارة التنفيذية:
• السعر الحالي: [ السعر الدقيق من الشارت ]
• **نقطة الدخول المعدلة (بعد معامل الانزلاق إن وجد):** [السعر]
• حالة الشمعة: [مفتوحة / مغلقة] [ حمراء 🔴  / خضراء 🟢 ] - الوقت المتبقي: [ وقت دقيق من ساعة المنصة ]
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
• وقت الذروة المتوقع: [مثلاً: خلال الـ 3 شموع القادمة] **(محسوب من ساعة المنصة)**
• الحالة النفسية: [خوف 🥺 / جشع 🤑 / تردد 🤌 / استسلام 👎]
• علامات التلاعب: [موجودة ✔️ / غير موجودة ❎]

⚠️ إدارة المخاطر:
• مستوى الثقة: [0-100]٪ = [💥/🔥/⚡/❄️/🚫]
• نقطة الإلغاء: [السعر الذي يفسد التحليل]

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
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة", "📈 توصية"], ["👑 لوحة التحكم"]]
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
            ["إلغاء"]
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
    
    🔑 **نظام الاشتراك المتطور V2:**
    • إدارة الاشتراكات عبر لوحة المشرف
    • إضافة اشتراك مباشر باستخدام User ID
    • خيارات مدة: ساعة، يوم، أسبوع، شهر، سنة، مدى الحياة
    • إمكانية حظر المستخدمين وإلغاء حظرهم
    • نظام إذاعة للمشتركين
    
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
    • لوحة تحكم متكاملة
    • إضافة اشتراكات باختيار المدة
    • عرض جميع المستخدمين
    • إلغاء الاشتراكات
    • حظر وإلغاء حظر المستخدمين
    • إذاعة للمشتركين
    • إحصائيات متقدمة
    
    💾 **نظام حماية البيانات:**
    • تخزين آمن ومشفر
    • نسخ احتياطي تلقائي
    • تتبع التغييرات
    • منع التلاعب
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
            <p style="color: #28a745; font-size: 0.9em;">✅ نظام إدارة الاشتراكات المباشر نشط - إضافة اشتراك باستخدام User ID</p>
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
        <div class="status">✅ نظام إدارة الاشتراكات المتطور V2 نشط - إضافة اشتراك باستخدام User ID</div>
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
        "data_version": DATA_VERSION,
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
                last_backup = backups[-1].replace('backup_', '').replace('.db', '')
        
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
            DataProtection.backup_database()
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
            DataProtection.backup_database()
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
            DataProtection.backup_database()
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

@app.route('/ping')
def ping():
    return "PONG"

def main():
    """الدالة الرئيسية - النسخة النهائية مع نظام إدارة الاشتراكات المتطور V2"""
    print("🤖 Starting Obeida Trading with Advanced Subscription System V2 ...")
    print("📋 تم تفعيل نظام النسخ السريع - جميع التحليلات قابلة للنسخ بنقرة واحدة")
    print("👑 المشرف الرئيسي: 6207431030 (@Sz2zv)")
    print("🔑 نظام إدارة الاشتراكات المتطور V2 - إضافة اشتراك باستخدام User ID")
    print("🔒 نظام إخفاء الأزرار لغير المشتركين - مفعل")
    print("💾 نظام حماية البيانات والنسخ الاحتياطي - مفعل")
    print(f"📊 نسخة البيانات: {DATA_VERSION}")
    
    # تهيئة قاعدة البيانات الرئيسية
    init_db_with_subscription()
    
    # إنشاء نسخة احتياطية أولية
    DataProtection.backup_database()
    DataProtection.backup_codes_db()
    
    # تشغيل Flask
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
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
    
    print("✅ Telegram Bot with Advanced Subscription System V2 ready ...")
    print("📡 Bot Obeida Trading V2 online with admin panel and subscription system...")
    print("📢 نظام الإذاعة للمشتركين مفعل")
    print("👥 إدارة المستخدمين: إضافة، حظر، تمديد، إلغاء")
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
