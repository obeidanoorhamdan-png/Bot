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
import feedparser
from datetime import datetime, timedelta
from typing import Dict, List, Set, Tuple, Optional
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.error import NetworkError, TimedOut
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for, flash, send_file
from functools import wraps
from PIL import Image
import pytz
import psutil
import csv
from io import StringIO

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
DATA_VERSION = "2.2.0"

ADMIN_ID = 6207431030

SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'Obeida059')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', hashlib.sha256('Obeida059'.encode()).hexdigest())

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

IMAGE_CACHE_DIR = "image_cache"
MAX_IMAGE_SIZE = (1024, 1024)
IMAGE_QUALITY = 95

# أسعار الاشتراكات الافتراضية
SUBSCRIPTION_PRICES = {
    'hourly': 1.0,
    'daily': 5.0,
    'weekly': 15.0,
    'monthly': 40.0,
    'yearly': 400.0,
    'lifetime': 0  # غير متاح للشراء المباشر
}

MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, ANALYZE_MODE, ADMIN_MENU, ADD_SUBSCRIPTION, SELECT_DURATION, REMOVE_USER, BROADCAST_MESSAGE, SET_USER_DURATION, SET_BOT_STATUS, SET_SESSION_CONTROL, TIMEZONE_MENU, SEARCH_COUNTRY, AFFILIATE_MENU, AFFILIATE_REGISTER = range(16)

for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"📁 تم إنشاء المجلد: {directory}")

# قاموس لتتبع التحليلات النشطة لكل مستخدم
active_analyses: Dict[int, asyncio.Task] = {}

# ============================================
# دوال مساعدة
# ============================================

def escape_markdown(text):
    """تنظيف النص من الأحرف الخاصة في Markdown"""
    if not text:
        return text
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def format_analysis_with_copy_button(analysis_text):
    """تنسيق التحليل مع زر نسخ"""
    analysis_text = re.sub(r'```[\w]*\n?', '', analysis_text)
    analysis_text = re.sub(r'```\n?', '', analysis_text)
    formatted_text = f"```\n{analysis_text}\n```\n\n"
    formatted_text += "📋 **لنسخ التحليل:** اضغط على الرمز أعلاه ثم اختر Copy"
    return formatted_text

# ============================================
# نظام رادار الأخبار
# ============================================

class ObeidaNewsRadar:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.chat_group_id = '-1003449363659'
        self.rss_url = 'https://sa.investing.com/rss/news.rss'
        self.db_name = NEWS_RADAR_DB
        self.last_guid = ""
        self.processed_news: Set[str] = set()
        self.broadcast_enabled = True
        self.init_db()
        self.load_processed_news()
        logger.info("📡 نظام رادار Obeida Trading قيد التشغيل...")

    CURRENCY_MAP = {
        "دولار": "USD 🇺🇸", "يورو": "EUR 🇪🇺", "إسترليني": "GBP 🇬🇧",
        "ين": "JPY 🇯🇵", "فرنك": "CHF 🇨🇭", "كندي": "CAD 🇨🇦",
        "أسترالي": "AUD 🇦🇺", "نيوزيلندي": "NZD 🇳🇿", "ذهب": "XAU 🟡",
        "فضة": "XAG ⚪", "نفط": "OIL 🛢️", "بتكوين": "BTC ₿",
        "إيثريوم": "ETH 💎", "مؤشر": "INDEX 📊"
    }

    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sent_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guid TEXT UNIQUE,
                title TEXT,
                link TEXT,
                published TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                currency_tag TEXT,
                chat_id TEXT
            )
        ''')
        conn.commit()
        conn.close()

    def load_processed_news(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT guid FROM sent_news ORDER BY id DESC LIMIT 100")
        rows = cursor.fetchall()
        for row in rows:
            self.processed_news.add(row[0])
        conn.close()

    def get_currency_tag(self, title: str) -> str:
        title_lower = title.lower()
        for currency, tag in self.CURRENCY_MAP.items():
            if currency in title:
                return tag
        return "#سوق_عام 🌐"

    def format_news_message(self, title: str, link: str, published: str, currency_tag: str) -> str:
        message = (
            f"📰 <b>🚨 خبر جديد:</b>\n\n"
            f"{title}\n\n"
            f"💰 <b>العملات المتأثرة:</b> {currency_tag}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>للتحليل الفوري:</b> @ObeidaTrading\n"
            f"💬 <b>مناقشة الأخبار:</b> @ObeidaTradingchat"
        )
        return message

    def send_telegram_message(self, chat_id: str, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
        try:
            response = requests.post(url, data=payload, timeout=10)
            return response.status_code == 200
        except:
            return False

    async def process_news(self):
        try:
            rss_urls = [
                'https://sa.investing.com/rss/news.rss',
                'https://sa.investing.com/rss/economic_calendar.rss',
                'https://sa.investing.com/rss/forex_news.rss',
                'https://sa.investing.com/rss/commodities_news.rss',
            ]
            headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'ar-SA,ar;q=0.9'}
            all_news = {}
            
            for rss_url in rss_urls:
                try:
                    response = requests.get(rss_url, headers=headers, timeout=15)
                    if response.status_code != 200:
                        continue
                    feed = feedparser.parse(response.content)
                    for news in feed.entries[:10]:
                        news_id = news.get('id', news.get('guid', news.title.strip()))
                        if news_id not in self.processed_news and news_id not in all_news:
                            all_news[news_id] = news
                except:
                    continue
            
            for news_id, news in all_news.items():
                currency_tag = self.get_currency_tag(news.title)
                message_text = (
                    f"🚨 <b>خبر اقتصادي جديد:</b>\n\n"
                    f"{news.title}\n\n"
                    f"💰 <b>العملات المتأثرة:</b> {currency_tag}\n\n"
                    f"━━━━━━━━━━━━━\n"
                    f"⚡ <b>القناة:</b> @ObeidaTrading\n"
                    f"💬 <b>المناقشة:</b> @ObeidaTradingchat\n"
                )
                self.send_telegram_message(self.chat_group_id, message_text)
                self.processed_news.add(news_id)
                await asyncio.sleep(1.5)
                
        except Exception as e:
            logger.error(f"خطأ في الرادار: {e}")

    async def start_radar(self):
        logger.info("📡 رادار الأخبار يعمل...")
        while True:
            try:
                await self.process_news()
            except Exception as e:
                logger.error(f"خطأ: {e}")
            await asyncio.sleep(20)

# ============================================
# نظام الوكالة (Affiliate System)
# ============================================

class AffiliateSystem:
    @staticmethod
    def init_db():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS affiliates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_code TEXT UNIQUE,
                owner_id INTEGER,
                owner_username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                affiliate_code TEXT,
                status TEXT DEFAULT 'pending',
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP,
                approved_by INTEGER,
                FOREIGN KEY (affiliate_code) REFERENCES affiliates(affiliate_code)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS subscription_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_type TEXT UNIQUE,
                price REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_by INTEGER
            )
        ''')
        
        # إضافة الأسعار الافتراضية
        default_prices = [
            ('hourly', 1.0), ('daily', 5.0), ('weekly', 15.0),
            ('monthly', 40.0), ('yearly', 400.0), ('lifetime', 0)
        ]
        for sub_type, price in default_prices:
            cursor.execute('''
                INSERT OR IGNORE INTO subscription_prices (subscription_type, price, updated_by)
                VALUES (?, ?, ?)
            ''', (sub_type, price, ADMIN_ID))
        
        conn.commit()
        conn.close()

    @staticmethod
    def generate_code(owner_id: int, owner_username: str) -> str:
        code = f"OBEIDA_{owner_id}_{secrets.token_hex(4).upper()}"
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO affiliates (affiliate_code, owner_id, owner_username)
                VALUES (?, ?, ?)
            ''', (code, owner_id, owner_username))
            conn.commit()
            return code
        except:
            return None
        finally:
            conn.close()

    @staticmethod
    def get_affiliate_codes(owner_id: int) -> List[Dict]:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT affiliate_code, created_at, is_active,
                   (SELECT COUNT(*) FROM affiliate_registrations WHERE affiliate_code = a.affiliate_code AND status = 'approved') as approved_count
            FROM affiliates a WHERE owner_id = ?
        ''', (owner_id,))
        rows = cursor.fetchall()
        conn.close()
        return [{'code': r[0], 'created_at': r[1], 'is_active': r[2], 'approved_count': r[3]} for r in rows]

    @staticmethod
    def register_user(user_id: int, affiliate_code: str) -> bool:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                SELECT id FROM affiliates WHERE affiliate_code = ? AND is_active = 1
            ''', (affiliate_code,))
            if not cursor.fetchone():
                return False
            cursor.execute('''
                INSERT INTO affiliate_registrations (user_id, affiliate_code, status)
                VALUES (?, ?, 'pending')
            ''', (user_id, affiliate_code))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()

    @staticmethod
    def get_pending_registrations() -> List[Dict]:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.id, r.user_id, r.affiliate_code, r.registered_at, a.owner_id, a.owner_username
            FROM affiliate_registrations r
            JOIN affiliates a ON r.affiliate_code = a.affiliate_code
            WHERE r.status = 'pending'
            ORDER BY r.registered_at DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        return [{'id': r[0], 'user_id': r[1], 'code': r[2], 'date': r[3], 'owner_id': r[4], 'owner_username': r[5]} for r in rows]

    @staticmethod
    def approve_registration(reg_id: int, approved_by: int) -> bool:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                UPDATE affiliate_registrations
                SET status = 'approved', approved_at = CURRENT_TIMESTAMP, approved_by = ?
                WHERE id = ? AND status = 'pending'
            ''', (approved_by, reg_id))
            conn.commit()
            return cursor.rowcount > 0
        except:
            return False
        finally:
            conn.close()

    @staticmethod
    def reject_registration(reg_id: int) -> bool:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                UPDATE affiliate_registrations SET status = 'rejected' WHERE id = ? AND status = 'pending'
            ''', (reg_id,))
            conn.commit()
            return cursor.rowcount > 0
        except:
            return False
        finally:
            conn.close()

    @staticmethod
    def get_subscription_price(subscription_type: str) -> float:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT price FROM subscription_prices WHERE subscription_type = ?', (subscription_type,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else SUBSCRIPTION_PRICES.get(subscription_type, 0)

    @staticmethod
    def set_subscription_price(subscription_type: str, price: float, updated_by: int) -> bool:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO subscription_prices (subscription_type, price, updated_at, updated_by)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?)
            ''', (subscription_type, price, updated_by))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()

    @staticmethod
    def is_affiliate_registered(user_id: int) -> bool:
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT status FROM affiliate_registrations WHERE user_id = ? AND status != 'rejected'
        ''', (user_id,))
        row = cursor.fetchone()
        conn.close()
        return row is not None and row[0] == 'approved'

# ============================================
# نظام الاشتراك المتطور (مدفوع فقط)
# ============================================

class SubscriptionSystem:
    SUBSCRIPTION_TYPES = {
        'trial': {'name': 'تجريبي', 'duration': 86400, 'features': ['basic_analysis']},
        'hourly': {'name': 'ساعة', 'duration': 3600, 'features': ['basic_analysis']},
        'daily': {'name': 'يوم', 'duration': 86400, 'features': ['basic_analysis']},
        'weekly': {'name': 'أسبوع', 'duration': 604800, 'features': ['basic_analysis']},
        'monthly': {'name': 'شهر', 'duration': 2592000, 'features': ['basic_analysis']},
        'yearly': {'name': 'سنة', 'duration': 31536000, 'features': ['basic_analysis']},
        'lifetime': {'name': 'مدى الحياة', 'duration': -1, 'features': ['all']}
    }
    
    @staticmethod
    def init_db():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                is_bot BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_blocked BOOLEAN DEFAULT 0,
                blocked_reason TEXT,
                total_analyses INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}',
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
                auto_renew BOOLEAN DEFAULT 0,
                payment_method TEXT,
                payment_reference TEXT,
                price REAL,
                currency TEXT DEFAULT 'USD',
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT DEFAULT '{}',
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT DEFAULT 'active',
                scheduled_time TIMESTAMP,
                scheduled_action TEXT,
                session_control TEXT DEFAULT 'all',
                updated_by INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS session_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_name TEXT,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                active_users INTEGER,
                analyses_count INTEGER,
                success_rate REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("SELECT COUNT(*) FROM bot_status")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO bot_status (status, session_control) VALUES ('active', 'all')")
        
        conn.commit()
        conn.close()
        AffiliateSystem.init_db()
        print("✅ تم تهيئة نظام الاشتراك المتطور بنجاح")
    
    @staticmethod
    def register_user(user_id, username=None, first_name=None, last_name=None, language_code=None, is_bot=False):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if cursor.fetchone():
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
            cursor.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, language_code, is_bot)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, username, first_name, last_name, language_code, is_bot))
            conn.commit()
            return True, "تم تسجيل المستخدم بنجاح"
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def add_subscription(user_id, subscription_type, added_by=ADMIN_ID, reason=""):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            if subscription_type not in SubscriptionSystem.SUBSCRIPTION_TYPES:
                return False, "نوع اشتراك غير صالح"
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            duration = SubscriptionSystem.SUBSCRIPTION_TYPES[subscription_type]['duration']
            start_date = datetime.now()
            end_date = None
            if duration > 0:
                end_date = start_date + timedelta(seconds=duration)
            cursor.execute("""
                UPDATE active_subscriptions 
                SET is_active = 0
                WHERE user_id = ? AND is_active = 1
            """, (user_id,))
            sub_name = SubscriptionSystem.SUBSCRIPTION_TYPES[subscription_type]['name']
            price = AffiliateSystem.get_subscription_price(subscription_type)
            cursor.execute("""
                INSERT INTO active_subscriptions (
                    user_id, subscription_type, subscription_name, 
                    start_date, end_date, is_active, added_by, price
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """, (user_id, subscription_type, sub_name, 
                  start_date.isoformat(), end_date.isoformat() if end_date else None, 
                  added_by, price))
            conn.commit()
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
    def check_subscription(user_id):
        if user_id == ADMIN_ID:
            return True, "مشرف", {'type': 'admin', 'name': 'مشرف'}
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if result and result[0] == 1:
                return False, "محظور", {'blocked': True}
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
            if end_date:
                try:
                    end = datetime.fromisoformat(end_date)
                    if end < datetime.now():
                        cursor.execute("""
                            UPDATE active_subscriptions 
                            SET is_active = 0
                            WHERE user_id = ? AND is_active = 1
                        """, (user_id,))
                        conn.commit()
                        return False, "انتهت صلاحية الاشتراك", None
                except:
                    pass
            return True, sub_name, {'type': sub_type, 'name': sub_name, 'start_date': start_date, 'end_date': end_date}
        except Exception as e:
            return False, str(e), None
        finally:
            conn.close()
    
    @staticmethod
    def get_user_info(user_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT user_id, username, first_name, created_at, last_active, is_blocked, total_analyses, timezone
                FROM users WHERE user_id = ?
            """, (user_id,))
            user_data = cursor.fetchone()
            if not user_data:
                return None
            cursor.execute("""
                SELECT subscription_name, start_date, end_date
                FROM active_subscriptions 
                WHERE user_id = ? AND is_active = 1
                ORDER BY id DESC LIMIT 1
            """, (user_id,))
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
                'subscription_start': sub_data[1] if sub_data else None,
                'subscription_end': sub_data[2] if sub_data else None
            }
        except:
            return None
        finally:
            conn.close()
    
    @staticmethod
    def increment_analyses(user_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE users 
                SET total_analyses = total_analyses + 1,
                    last_active = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (user_id,))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    @staticmethod
    def get_all_users():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT u.user_id, u.username, u.first_name, u.created_at, u.last_active, u.is_blocked,
                       u.total_analyses, u.timezone,
                       a.subscription_name, a.start_date, a.end_date, a.is_active
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
            cursor.execute("""
                SELECT COUNT(DISTINCT user_id) 
                FROM active_subscriptions 
                WHERE is_active = 1 AND (end_date IS NULL OR end_date > datetime('now'))
            """)
            stats['active_users'] = cursor.fetchone()[0]
            cursor.execute("SELECT SUM(total_analyses) FROM users")
            stats['total_analyses'] = cursor.fetchone()[0] or 0
            cursor.execute("""
                SELECT subscription_type, COUNT(*) 
                FROM active_subscriptions 
                WHERE is_active = 1
                GROUP BY subscription_type
            """)
            stats['subscriptions_by_type'] = dict(cursor.fetchall())
            cursor.execute("""
                SELECT COUNT(DISTINCT user_id) FROM users 
                WHERE last_active > datetime('now', '-1 day')
            """)
            stats['active_today'] = cursor.fetchone()[0]
            cursor.execute("""
                SELECT COUNT(*) FROM active_subscriptions 
                WHERE is_active = 1 AND end_date IS NOT NULL 
                AND end_date < datetime('now', '+7 day')
            """)
            stats['expiring_soon'] = cursor.fetchone()[0]
        except Exception as e:
            print(f"⚠️ خطأ في جلب الإحصائيات: {e}")
        finally:
            conn.close()
        return stats
    
    @staticmethod
    def block_user(user_id, reason="Blocked by admin", performed_by=ADMIN_ID):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE users 
                SET is_blocked = 1, blocked_reason = ?
                WHERE user_id = ?
            """, (reason, user_id))
            cursor.execute("""
                UPDATE active_subscriptions 
                SET is_active = 0
                WHERE user_id = ? AND is_active = 1
            """, (user_id,))
            conn.commit()
            return True, "تم حظر المستخدم بنجاح"
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def unblock_user(user_id, performed_by=ADMIN_ID):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE users 
                SET is_blocked = 0, blocked_reason = NULL
                WHERE user_id = ?
            """, (user_id,))
            conn.commit()
            return True, "تم إلغاء حظر المستخدم بنجاح"
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def extend_subscription(user_id, additional_seconds, performed_by=ADMIN_ID):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT id, end_date FROM active_subscriptions
                WHERE user_id = ? AND is_active = 1
                LIMIT 1
            """, (user_id,))
            result = cursor.fetchone()
            if not result:
                return False, "لا يوجد اشتراك نشط لهذا المستخدم"
            sub_id, current_end = result
            if current_end:
                current_end_date = datetime.fromisoformat(current_end)
                new_end_date = current_end_date + timedelta(seconds=additional_seconds)
            else:
                new_end_date = datetime.now() + timedelta(seconds=additional_seconds)
            cursor.execute("""
                UPDATE active_subscriptions 
                SET end_date = ?
                WHERE id = ?
            """, (new_end_date.isoformat(), sub_id))
            conn.commit()
            return True, f"تم تمديد الاشتراك حتى {new_end_date.strftime('%Y-%m-%d %H:%M')}"
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def get_bot_status():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT status, scheduled_time, scheduled_action, session_control FROM bot_status ORDER BY id DESC LIMIT 1")
            result = cursor.fetchone()
            if result:
                return {'status': result[0], 'scheduled_time': result[1], 'scheduled_action': result[2], 'session_control': result[3]}
            return {'status': 'active', 'session_control': 'all'}
        except:
            return {'status': 'active', 'session_control': 'all'}
        finally:
            conn.close()
    
    @staticmethod
    def set_bot_status(status, scheduled_time=None, scheduled_action=None, session_control='all', updated_by=ADMIN_ID):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO bot_status (status, scheduled_time, scheduled_action, session_control, updated_by)
                VALUES (?, ?, ?, ?, ?)
            """, (status, scheduled_time, scheduled_action, session_control, updated_by))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    @staticmethod
    def set_user_timezone(user_id, timezone):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE users SET timezone = ? WHERE user_id = ?
            """, (timezone, user_id))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()

# ============================================
# دوال مساعدة إضافية
# ============================================

def init_db_with_subscription():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
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
            timezone TEXT DEFAULT 'GMT+02:00'
        )
    ''')
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (ADMIN_ID,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, username, is_admin, timezone) VALUES (?, ?, ?, ?)", 
                      (ADMIN_ID, "Sz2zv", 1, "GMT+02:00"))
    conn.commit()
    conn.close()
    SubscriptionSystem.init_db()

TRADING_SESSIONS = {
    'asian': {'name': '🌏 الجلسة الآسيوية', 'start': 0, 'end': 8, 'volatility': 'منخفضة', 'pairs': 'الين الياباني، الدولار الأسترالي', 'success_rate': '65-70%', 'features': 'حركات هادئة', 'icon': '🌏', 'time_range': '00:00-08:00 UTC', 'best_timezones': ['GMT+03:00', 'GMT+08:00']},
    'london': {'name': '🇬🇧 جلسة لندن', 'start': 8, 'end': 16, 'volatility': 'مرتفعة', 'pairs': 'اليورو، الجنيه', 'success_rate': '75-85%', 'features': 'سيولة عالية', 'icon': '🇬🇧', 'time_range': '08:00-16:00 UTC', 'best_timezones': ['GMT+01:00', 'GMT+02:00']},
    'newyork': {'name': '🗽 جلسة نيويورك', 'start': 13, 'end': 21, 'volatility': 'عالية جداً', 'pairs': 'الدولار', 'success_rate': '80-90%', 'features': 'سيولة عالية', 'icon': '🗽', 'time_range': '13:00-21:00 UTC', 'best_timezones': ['GMT-05:00']},
    'pacific': {'name': '🌊 جلسة المحيط الهادئ', 'start': 21, 'end': 24, 'volatility': 'منخفضة', 'pairs': 'جميع الأزواج', 'success_rate': '55-65%', 'features': 'سيولة منخفضة', 'icon': '🌊', 'time_range': '21:00-00:00 UTC', 'best_timezones': ['GMT-10:00']}
}

TIMEZONES_BY_GMT = {
    "GMT+02:00": {"name": "القدس/القاهرة", "countries": ["فلسطين", "مصر", "الأردن", "لبنان", "سوريا"]},
    "GMT+03:00": {"name": "السعودية/موسكو", "countries": ["السعودية", "العراق", "الكويت", "قطر", "البحرين"]},
    "GMT+04:00": {"name": "الإمارات", "countries": ["الإمارات", "عمان"]},
    "GMT+00:00": {"name": "غرينتش", "countries": ["بريطانيا", "المغرب"]},
    "GMT+01:00": {"name": "أوروبا الوسطى", "countries": ["ألمانيا", "فرنسا", "إيطاليا"]},
    "GMT-05:00": {"name": "نيويورك", "countries": ["نيويورك", "واشنطن"]},
}

COUNTRY_TIMEZONE_MAP = {country: gmt for gmt, info in TIMEZONES_BY_GMT.items() for country in info['countries']}

def get_current_session():
    current_hour = datetime.utcnow().hour
    if 0 <= current_hour < 8: return TRADING_SESSIONS['asian']
    elif 8 <= current_hour < 16: return TRADING_SESSIONS['london']
    elif 13 <= current_hour < 21: return TRADING_SESSIONS['newyork']
    else: return TRADING_SESSIONS['pacific']

def get_user_local_session(user_id):
    try:
        user_info = SubscriptionSystem.get_user_info(user_id)
        if user_info and user_info.get('timezone'):
            tz_str = user_info['timezone']
            if tz_str.startswith('GMT'):
                offset_str = tz_str.replace('GMT', '')
                if offset_str and offset_str[0] in ('+', '-'):
                    hours = int(offset_str.split(':')[0])
                    timezone = pytz.FixedOffset(hours * 60)
                    local_hour = datetime.now(timezone).hour
                    for session in TRADING_SESSIONS.values():
                        if session['start'] <= local_hour < session['end']:
                            return session
    except: pass
    return get_current_session()

def get_user_timezone(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT timezone FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "GMT+02:00"

def set_user_timezone(user_id, timezone):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (timezone, user_id))
    conn.commit()
    conn.close()
    SubscriptionSystem.set_user_timezone(user_id, timezone)
    return True

def get_localized_time(user_id):
    try:
        timezone_str = get_user_timezone(user_id)
        if timezone_str.startswith('GMT'):
            offset_str = timezone_str.replace('GMT', '')
            if offset_str and offset_str[0] in ('+', '-'):
                hours = int(offset_str.split(':')[0])
                return datetime.now(pytz.FixedOffset(hours * 60))
        return datetime.now(pytz.UTC)
    except:
        return datetime.now()

def save_user_setting(user_id, col, val):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (val, user_id))
    conn.commit()
    conn.close()

def get_user_setting(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT candle, trade_time, last_analysis_context, last_analysis_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res if res else ("M1", "قصير (1m-15m)", "", None)

def compress_image(image_path, max_size=MAX_IMAGE_SIZE, quality=IMAGE_QUALITY):
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
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except:
        return None

def clean_repeated_text(text):
    if not text: return ""
    lines = text.split('\n')
    unique_lines = []
    for line in lines:
        if line.strip() not in [ul.strip() for ul in unique_lines] or line.strip() == "":
            unique_lines.append(line)
    text = '\n'.join(unique_lines)
    patterns = ["📊 التحليل الفني المتقدم:", "🎯 الإشارة التنفيذية:", "⚠️ إدارة المخاطر:"]
    for p in patterns:
        if text.count(p) > 1:
            parts = text.split(p)
            text = parts[0] + p + parts[-1]
    return text

def split_message(text, max_length=4000):
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

def cleanup_old_images():
    try:
        current_time = time.time()
        for filename in os.listdir(IMAGE_CACHE_DIR):
            filepath = os.path.join(IMAGE_CACHE_DIR, filename)
            if os.path.isfile(filepath) and current_time - os.path.getmtime(filepath) > 60:
                os.remove(filepath)
    except: pass

# ============================================
# دوال معالجة البوت
# ============================================

async def check_bot_status(update: Update) -> bool:
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        return True
    bot_status = SubscriptionSystem.get_bot_status()
    if bot_status['status'] == 'maintenance':
        await update.message.reply_text("🔧 **البوت تحت الصيانة حاليًا**\n\nسيتم العودة قريبًا.", parse_mode="Markdown")
        return False
    elif bot_status['status'] == 'stopped':
        await update.message.reply_text("⏸️ **البوت متوقف حاليًا**\n\nيرجى المحاولة لاحقًا.", parse_mode="Markdown")
        return False
    user_local_session = get_user_local_session(user_id)
    if not is_session_allowed(bot_status['session_control'], user_local_session):
        await update.message.reply_text(f"⏳ **غير مسموح بالتداول في هذه الجلسة**\n\nالجلسة الحالية: {user_local_session['name']}", parse_mode="Markdown")
        return False
    return True

def is_session_allowed(session_control, current_session):
    if session_control == 'all':
        return True
    session_map = {'asian': TRADING_SESSIONS['asian']['name'], 'london': TRADING_SESSIONS['london']['name'],
                   'newyork': TRADING_SESSIONS['newyork']['name'], 'pacific': TRADING_SESSIONS['pacific']['name']}
    return session_control in session_map and session_map[session_control] == current_session['name']

async def subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        return True
    user = update.effective_user
    SubscriptionSystem.register_user(user_id, user.username, user.first_name, user.last_name, user.language_code, user.is_bot)
    if not await check_bot_status(update):
        return False
    status, message, details = SubscriptionSystem.check_subscription(user_id)
    if not status:
        prices = "\n".join([f"• {SubscriptionSystem.SUBSCRIPTION_TYPES[t]['name']}: ${AffiliateSystem.get_subscription_price(t)}" 
                           for t in ['hourly', 'daily', 'weekly', 'monthly']])
        await update.message.reply_text(
            f"⚠️ **عذراً، ليس لديك اشتراك نشط**\n\n"
            f"❌ الحالة: {message}\n\n"
            f"🔑 **أسعار الاشتراكات:**\n{prices}\n\n"
            f"📞 للاشتراك والتواصل مع المشرف: @Sz2zv",
            parse_mode="Markdown"
        )
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    SubscriptionSystem.register_user(user_id, user.username, user.first_name, user.last_name, user.language_code, user.is_bot)
    user_info = SubscriptionSystem.get_user_info(user_id)
    status, sub_name, details = SubscriptionSystem.check_subscription(user_id)
    local_time = get_localized_time(user_id)
    welcome_msg = (
        "🚀 **أهلاً بك في Obeida Trading** 🚀\n\n"
        f"🆔 **معرفك:** `{user_id}`\n"
        f"👤 **اليوزر:** @{user.username or user_id}\n\n"
        f"📊 **حالة الاشتراك:** {'✅ ' + sub_name if status else '❌ غير مشترك'}\n"
        f"📈 **إجمالي التحليلات:** {user_info['total_analyses'] if user_info else 0}\n"
    )
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🌍 المناطق الزمنية", "🔄 تم تحديث البوت"],
        ["💎 الاشتراك", "🔗 التسجيل تحت وكالتنا"]
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
    if user_id != ADMIN_ID and not await check_bot_status(update):
        return MAIN_MENU
    if user_message == "🌍 المناطق الزمنية":
        return await timezone_menu(update, context)
    elif user_message == "👑 لوحة التحكم" and user_id == ADMIN_ID:
        return await start_admin_panel(update, context)
    elif user_message == "🔄 تم تحديث البوت":
        await update.message.reply_text(
            "🔄 **تم تحديث البوت إلى الإصدار الأحدث!** 🔄\n\n"
            "✨ **المميزات الجديدة:**\n"
            "• تحليل أسرع وأدق باستخدام الذكاء الاصطناعي\n"
            "• دعم أفضل للعملات والأسواق العالمية\n"
            "• نظام اشتراكات متطور مع أسعار مرنة\n"
            "• رادار أخبار فوري للأحداث الاقتصادية\n\n"
            "📊 **لبدء التحليل، اضغط /start**\n\n"
            "شكراً لثقتكم بنا! 🤖",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    elif user_message == "💎 الاشتراك":
        prices = []
        for t in ['hourly', 'daily', 'weekly', 'monthly']:
            price = AffiliateSystem.get_subscription_price(t)
            name = SubscriptionSystem.SUBSCRIPTION_TYPES[t]['name']
            prices.append(f"• {name}: ${price}")
        await update.message.reply_text(
            f"💎 **باقات الاشتراك في Obeida Trading** 💎\n\n"
            f"{chr(10).join(prices)}\n\n"
            f"📞 **للاشتراك:**\n"
            f"• تواصل مع المشرف: @Sz2zv\n"
            f"• أرسل معرفك (`{user_id}`) للمشرف لتفعيل الاشتراك\n\n"
            f"✅ بعد التفعيل، يمكنك استخدام جميع ميزات البوت بدون حدود!",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    elif user_message == "🔗 التسجيل تحت وكالتنا":
        await update.message.reply_text(
            "🔗 **التسجيل تحت وكالة Obeida Trading** 🔗\n\n"
            "للتسجيل تحت وكالتنا، يرجى إرسال الكود الخاص بالوكيل.\n\n"
            "📝 **أرسل الكود الآن:**\n"
            "مثال: `OBEIDA_123456_ABCD`\n\n"
            "إذا لم يكن لديك كود، تواصل مع أحد وكلائنا المعتمدين.",
            parse_mode="Markdown"
        )
        return AFFILIATE_REGISTER
    elif user_message == "📊 معلومات الجلسة":
        current_session = get_user_local_session(user_id)
        local_time = get_localized_time(user_id)
        session_info = (
            f"🌐 **معلومات الجلسة والوقت**\n\n"
            f"🕒 **وقتك المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📍 **جلسة منطقتك:** {current_session['name']}\n"
            f"📊 **التقلب:** {current_session['volatility']}\n"
            f"🎯 **نسبة النجاح:** {current_session['success_rate']}\n"
            f"✨ **المميزات:** {current_session['features']}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"**الجلسات الأخرى:**\n"
            f"🌏 الآسيوية: 65-70%\n"
            f"🇬🇧 لندن: 75-85%\n"
            f"🗽 نيويورك: 80-90%"
        )
        await update.message.reply_text(session_info, parse_mode="Markdown")
        return MAIN_MENU
    elif user_message == "👤 معلوماتي":
        user_info = SubscriptionSystem.get_user_info(user_id)
        status, sub_name, details = SubscriptionSystem.check_subscription(user_id)
        local_time = get_localized_time(user_id)
        info_msg = (
            f"👤 **معلومات المستخدم**\n\n"
            f"🆔 **المعرف:** `{user_id}`\n"
            f"👤 **اليوزر:** @{update.effective_user.username or 'لا يوجد'}\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"📊 **حالة الاشتراك:** {'✅ ' + sub_name if status else '❌ غير مشترك'}\n"
        )
        if details and details.get('end_date'):
            end_date = datetime.fromisoformat(details['end_date'])
            remaining = end_date - datetime.now()
            if remaining.days > 0:
                info_msg += f"⏳ **ينتهي بعد:** {remaining.days} يوم\n"
            else:
                info_msg += f"⏳ **ينتهي بعد:** {remaining.seconds//3600} ساعة\n"
        info_msg += (
            f"📈 **إجمالي التحليلات:** {user_info['total_analyses'] if user_info else 0}\n"
            f"🌍 **منطقتك الزمنية:** {user_info['timezone'] if user_info else 'GMT+02:00'}\n"
            f"🕒 **وقتك المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        await update.message.reply_text(info_msg, parse_mode="Markdown")
        return MAIN_MENU
    elif user_message == "⚙️ إعدادات التحليل":
        keyboard = [CANDLE_SPEEDS[i:i+3] for i in range(0, len(CANDLE_SPEEDS), 3)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        await update.message.reply_text("⚙️ *إعدادات التحليل الفني*\n\nحدد سرعة الشموع:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
        return SETTINGS_CANDLE
    elif user_message == "📊 تحليل صورة":
        candle, trade_time, _, _ = get_user_setting(user_id)
        if not candle or not trade_time:
            await update.message.reply_text("❌ *يجب ضبط الإعدادات أولاً*\n\nالرجاء ضبط سرعة الشموع ومدة الصفقة.", parse_mode="Markdown")
            return MAIN_MENU
        await update.message.reply_text(f"📊* نظام جاهز لتحليل الشارت*\n\n⚙️ الإعدادات:\n• سرعة الشموع: {candle}\n\n📤 قم برفع صورة الشارت الآن:", reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True), parse_mode="Markdown")
        return ANALYZE_MODE
    keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["📊 معلومات الجلسة", "👤 معلوماتي"], ["🌍 المناطق الزمنية", "🔄 تم تحديث البوت"], ["💎 الاشتراك", "🔗 التسجيل تحت وكالتنا"]]
    if user_id == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    await update.message.reply_text("اختر أحد الخيارات من القائمة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return MAIN_MENU

async def handle_affiliate_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text.strip()
    user_id = update.effective_user.id
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    if AffiliateSystem.is_affiliate_registered(user_id):
        await update.message.reply_text("✅ أنت مسجل بالفعل تحت وكالتنا! شكراً لك.")
        return await start(update, context)
    code = user_message.upper()
    if code.startswith("OBEIDA_"):
        if AffiliateSystem.register_user(user_id, code):
            await update.message.reply_text(
                "✅ **تم تسجيل طلبك بنجاح!**\n\n"
                "سيتم مراجعة طلبك من قبل المشرف.\n"
                "عند الموافقة، ستتلقى إشعاراً بتفعيل عضويتك.\n\n"
                "شكراً لتسجيلك تحت وكالة Obeida Trading! 🚀",
                parse_mode="Markdown"
            )
            # إرسال إشعار للمشرف
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🔔 **طلب تسجيل وكيل جديد**\n\n"
                     f"👤 المستخدم: {user_id}\n"
                     f"🔑 الكود: {code}\n\n"
                     f"استخدم /affiliates لإدارة الطلبات",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("❌ **الكود غير صالح**\n\nيرجى التأكد من الكود والمحاولة مرة أخرى.", parse_mode="Markdown")
            return AFFILIATE_REGISTER
    else:
        await update.message.reply_text("❌ **صيغة الكود غير صحيحة**\n\nيجب أن يبدأ الكود بـ `OBEIDA_`", parse_mode="Markdown")
        return AFFILIATE_REGISTER
    return await start(update, context)

async def handle_settings_candle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    if user_message in CANDLE_SPEEDS:
        save_user_setting(user_id, "candle", user_message)
        keyboard = [TRADE_TIMES[i:i+2] for i in range(0, len(TRADE_TIMES), 2)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        await update.message.reply_text(f"✅ **تم تعيين سرعة الشموع:** {user_message}\n\n⬇️ حدد **مدة الصفقة**:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode="Markdown")
        return SETTINGS_TIME
    await update.message.reply_text("❌ الرجاء اختيار سرعة شموع صحيحة.")
    return SETTINGS_CANDLE

async def handle_settings_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    if user_message in TRADE_TIMES:
        save_user_setting(user_id, "trade_time", user_message)
        candle, _, _, _ = get_user_setting(user_id)
        await update.message.reply_text(f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n✅ سرعة الشموع: {candle}\n✅ مدة الصفقة: {user_message}\n\n📤 قم الآن برفع صورة الشارت:", reply_markup=ReplyKeyboardMarkup([["📊 تحليل صورة"], ["الرجوع للقائمة الرئيسية"]], resize_keyboard=True), parse_mode="Markdown")
        return MAIN_MENU
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    await update.message.reply_text("📤 **الرجاء إرسال صورة الشارت فقط**", reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True))
    return ANALYZE_MODE

async def cancel_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    if user_id in active_analyses:
        active_analyses[user_id].cancel()
        del active_analyses[user_id]
        await update.message.reply_text("✅ **تم إلغاء التحليل الحالي**\n\nيمكنك إرسال صورة جديدة للتحليل.")
    else:
        await update.message.reply_text("ℹ️ **لا يوجد تحليل قيد التنفيذ حالياً**")

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in active_analyses and not active_analyses[user_id].done():
        keyboard = [[InlineKeyboardButton("❌ إلغاء التحليل", callback_data=f"cancel_analysis_{user_id}")]]
        await update.message.reply_text(
            "⏳ **يوجد تحليل قيد التنفيذ بالفعل!**\n\n"
            "الرجاء الانتظار حتى انتهاء التحليل الحالي.\n"
            "أو اضغط على الزر أدناه للإلغاء.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    if not await subscription_required(update, context):
        return MAIN_MENU
    candle, trade_time, prev_context, prev_time = get_user_setting(user_id)
    if not candle or not trade_time:
        await update.message.reply_text("❌ **يجب ضبط الإعدادات أولاً**", parse_mode="Markdown")
        return MAIN_MENU
    photo = await update.message.photo[-1].get_file()
    timestamp = int(time.time())
    original_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}_original.jpg")
    try:
        await photo.download_to_drive(original_path)
        compressed_path = compress_image(original_path)
        task = asyncio.create_task(analyze_chart_image(update, context, compressed_path, candle, trade_time, "الشارت المرفق", user_id))
        active_analyses[user_id] = task
        try:
            await task
        except asyncio.CancelledError:
            await update.message.reply_text("✅ تم إلغاء التحليل.")
        finally:
            if user_id in active_analyses:
                del active_analyses[user_id]
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)[:200]}")
    finally:
        for filepath in [original_path, compressed_path]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass
    return MAIN_MENU

async def analyze_chart_image(update, context, image_path, candle, trade_time, symbol, user_id):
    try:
        wait_msg = await update.message.reply_text("🤌 جاري تحليل الشارت... [⬜⬜⬜⬜⬜ 0%]")
        compressed_path = compress_image(image_path)
        base64_img = encode_image(compressed_path)
        if not base64_img:
            await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            return
        current_time = datetime.utcnow()
        current_hour = current_time.hour
        current_minute = current_time.minute
        current_second = current_time.second
        seconds_remaining = 0
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
        else:
            seconds_remaining = 0
        in_golden_window = 2 <= seconds_passed <= 58
        MAIN_PROMPT = f"""
أنت الآن المحرك التحليلي لنظام Obeida Trading. استخرج المعطيات الرقمية من الصورة بدقة.

📊 **بيانات الإطار الزمني:**
- الفريم: {candle}
- الوقت المتبقي: {seconds_remaining} ثانية

🎯 **التنسيق المطلوب:**
📐 **المعطيات الرقمية:**
• أعلى قمة (HH): [السعر]
• أدنى قاع (LL): [السعر]
• السعر الحالي: [السعر]

⚡ **معطيات الزخم:**
• قوة الاندفاع: [رقم عشري]
• اتجاه الموجة: [UP/DOWN]
• نسبة جسم الشمعة: [0.xx]

🔍 **معطيات SMC:**
• FVG: [True/False]
• Liquidity Sweep: [True/False]
• Order Block: [True/False]
"""
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": MISTRAL_MODEL,
            "messages": [{"role": "user", "content": [{"type": "text", "text": MAIN_PROMPT}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}]}],
            "max_tokens": 800, "temperature": 0.0, "top_p": 1.0, "random_seed": 42
        }
        # شريط التقدم
        for i in range(1, 6):
            await asyncio.sleep(3)
            progress = i * 20
            bars = "⬜" * (5 - i) + "🟩" * i
            await wait_msg.edit_text(f"🤌 جاري تحليل الشارت... [{bars} {progress}%]")
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=60)
        if response.status_code != 200:
            await wait_msg.edit_text(f"❌ خطأ: {response.status_code}")
            return
        analysis_result = response.json()['choices'][0]['message']['content'].strip()
        analysis_result = clean_repeated_text(analysis_result)
        import re
        hh_match = re.search(r'أعلى قمة.*?:\s*([\d.]+)', analysis_result)
        ll_match = re.search(r'أدنى قاع.*?:\s*([\d.]+)', analysis_result)
        current_price_match = re.search(r'السعر الحالي.*?:\s*([\d.]+)', analysis_result)
        breakout_force_match = re.search(r'قوة الاندفاع.*?:\s*([\d.]+)', analysis_result)
        direction_match = re.search(r'اتجاه الموجة.*?:\s*([A-Z]+)', analysis_result)
        fvg_match = re.search(r'FVG.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        has_fvg = fvg_match.group(1).upper() == "TRUE" if fvg_match else False
        highest_high = float(hh_match.group(1)) if hh_match else 0
        lowest_low = float(ll_match.group(1)) if ll_match else 0
        current_price = float(current_price_match.group(1)) if current_price_match else 0
        breakout_force = float(breakout_force_match.group(1)) if breakout_force_match else 1.0
        current_direction = direction_match.group(1).upper() if direction_match else "UP"
        session_name, liquidity_level, session_message = ("LONDON", "HIGH", "🇬🇧 جلسة لندن - سيولة عالية") if 8 <= current_hour < 16 else ("NEW_YORK", "HIGH", "🇺🇸 جلسة نيويورك - سيولة عالية") if 13 <= current_hour < 21 else ("ASIA", "LOW", "🇯🇵 جلسة آسيا - سيولة منخفضة")
        # بناء النتيجة
        formatted_result = f"""
📊 **التحليل الفني المتقدم:**

🎯 **الإشارة التنفيذية:**
• **السعر الحالي:** {current_price}
• **اتجاه الموجة:** {'صاعد 📈' if current_direction == 'UP' else 'هابط 📉'}
• **قوة الاندفاع:** {breakout_force:.2f}x
• **FVG (الفجوة السعرية):** {'✅ موجودة' if has_fvg else '❌ غير موجودة'}

• **نقطة الدخول المقترحة:** {current_price}
• **وقف الخسارة:** {current_price * (0.998 if current_direction == 'UP' else 1.002):.6f}
• **الأهداف الربحية:**
  🎯 TP1: {current_price * (1.005 if current_direction == 'UP' else 0.995):.6f}
  🎯 TP2: {current_price * (1.01 if current_direction == 'UP' else 0.99):.6f}
  🚀 TP3: {current_price * (1.02 if current_direction == 'UP' else 0.98):.6f}

⚠️ **إدارة المخاطر:**
• **نسبة نجاح متوقعة:** {70 + int(breakout_force * 10):.0f}%
• **حجم الاستثمار المقترح:** {('3%' if breakout_force > 1.2 else '2%')}

━━━━━━━━━━━━━━━
🌍 **جلسة التداول:** {session_message}
⏱️ **الفريم:** {candle} | **الوقت المتبقي:** {seconds_remaining} ثانية
🤖 **Obeida Trading Pro v2.2**
"""
        full_result = clean_repeated_text(formatted_result)
        await wait_msg.edit_text(full_result, parse_mode="Markdown")
        SubscriptionSystem.increment_analyses(user_id)
        return full_result
    except asyncio.CancelledError:
        raise
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)[:200]}")
        return None

async def timezone_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    current_tz = get_user_timezone(user_id)
    local_time = get_localized_time(user_id)
    keyboard = [[f"{gmt} ({info['name']})"] for gmt, info in TIMEZONES_BY_GMT.items()]
    keyboard.append(["🔍 بحث عن دولة", "🕒 منطقتي الحالية"])
    keyboard.append(["الرجوع للقائمة الرئيسية"])
    await update.message.reply_text(f"🌍 **المناطق الزمنية**\n\n🕒 **منطقتك:** {current_tz}\n⏰ **الوقت:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}\n\nاختر المنطقة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode="Markdown")
    return TIMEZONE_MENU

async def handle_timezone_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    elif user_message == "🕒 منطقتي الحالية":
        current_tz = get_user_timezone(user_id)
        await update.message.reply_text(f"📍 **منطقتك الزمنية:** {current_tz}", parse_mode="Markdown")
        return TIMEZONE_MENU
    elif user_message == "🔍 بحث عن دولة":
        await update.message.reply_text("🔍 **أدخل اسم الدولة**\nمثال: `مصر`، `السعودية`", parse_mode="Markdown")
        return SEARCH_COUNTRY
    selected_gmt = None
    for gmt in TIMEZONES_BY_GMT.keys():
        if user_message.startswith(gmt):
            selected_gmt = gmt
            break
    if selected_gmt:
        set_user_timezone(user_id, selected_gmt)
        await update.message.reply_text(f"✅ **تم تعيين منطقتك إلى {selected_gmt}**", parse_mode="Markdown")
        return TIMEZONE_MENU
    await update.message.reply_text("❌ لم أتمكن من العثور على المنطقة.")
    return TIMEZONE_MENU

async def handle_country_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    found = []
    for country, tz in COUNTRY_TIMEZONE_MAP.items():
        if user_message.lower() in country.lower():
            found.append((country, tz))
    if not found:
        await update.message.reply_text(f"❌ لم يتم العثور على '{user_message}'")
        return SEARCH_COUNTRY
    if len(found) == 1:
        country, tz = found[0]
        set_user_timezone(user_id, tz)
        await update.message.reply_text(f"✅ **تم تعيين منطقتك إلى {tz} حسب {country}**", parse_mode="Markdown")
        return await timezone_menu(update, context)
    keyboard = [[f"{c} ({tz})"] for c, tz in found[:10]]
    keyboard.append(["🔙 رجوع"])
    await update.message.reply_text(f"🔍 **نتائج البحث عن '{user_message}':**\nاختر الدولة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return SEARCH_COUNTRY

async def start_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    bot_status = SubscriptionSystem.get_bot_status()
    stats = SubscriptionSystem.get_stats()
    keyboard = [
        ["👥 عرض المستخدمين", "➕ إضافة اشتراك"],
        ["🗑️ إلغاء اشتراك", "⏱️ تمديد اشتراك"],
        ["🚫 حظر مستخدم", "✅ إلغاء حظر"],
        ["📢 إذاعة للمشتركين", "📊 إحصائيات"],
        ["🔧 حالة البوت", "🌐 التحكم بالجلسات"],
        ["💰 تعديل أسعار الاشتراكات", "🔗 إدارة الوكالة"],
        ["📥 تحميل قاعدة المشتركين", "🔄 تحديث البوت"],
        ["🔙 العودة"]
    ]
    await update.message.reply_text(
        f"👑 **لوحة تحكم المشرف**\n\n"
        f"📊 **حالة البوت:** {bot_status['status']}\n"
        f"🌐 **الجلسات:** {bot_status['session_control']}\n"
        f"👥 المستخدمين: {stats.get('total_users', 0)}\n"
        f"✅ النشطين: {stats.get('active_users', 0)}\n"
        f"📈 التحليلات: {stats.get('total_analyses', 0)}\n\n"
        f"اختر الإجراء:",
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
                uid, username, first_name, created, last_active, is_blocked, total_analyses, timezone, sub_name, start_date, end_date, is_active = u
                status = "🚫 محظور" if is_blocked == 1 else ("✅ نشط" if is_active == 1 else "❌ غير نشط")
                display = first_name or username or str(uid)
                msg += f"• **{display}**\n  🆔 `{uid}`\n  📊 اشتراك: {sub_name or 'لا يوجد'} ({status})\n  📈 تحليلات: {total_analyses}\n\n"
            for part in split_message(msg):
                await update.message.reply_text(part, parse_mode="Markdown")
        return ADMIN_MENU
    elif user_message == "➕ إضافة اشتراك":
        await update.message.reply_text("➕ **إضافة اشتراك**\n\nأرسل معرف المستخدم:", parse_mode="Markdown")
        return ADD_SUBSCRIPTION
    elif user_message == "🗑️ إلغاء اشتراك":
        await update.message.reply_text("🗑️ **إلغاء اشتراك**\n\nأرسل معرف المستخدم:", parse_mode="Markdown")
        return REMOVE_USER
    elif user_message == "⏱️ تمديد اشتراك":
        await update.message.reply_text("⏱️ **تمديد اشتراك**\n\nأرسل معرف المستخدم والمدة (بالثواني):\nمثال: `123456789 86400`", parse_mode="Markdown")
        return SET_USER_DURATION
    elif user_message == "🚫 حظر مستخدم":
        context.user_data['last_action'] = 'block'
        await update.message.reply_text("🚫 **حظر مستخدم**\n\nأرسل معرف المستخدم:", parse_mode="Markdown")
        return REMOVE_USER
    elif user_message == "✅ إلغاء حظر":
        context.user_data['last_action'] = 'unblock'
        await update.message.reply_text("✅ **إلغاء حظر**\n\nأرسل معرف المستخدم:", parse_mode="Markdown")
        return REMOVE_USER
    elif user_message == "📢 إذاعة للمشتركين":
        await update.message.reply_text("📢 **إذاعة**\n\nأرسل الرسالة:", parse_mode="Markdown")
        return BROADCAST_MESSAGE
    elif user_message == "📊 إحصائيات":
        stats = SubscriptionSystem.get_stats()
        await update.message.reply_text(
            f"📊 **إحصائيات النظام**\n\n"
            f"👥 إجمالي المستخدمين: {stats['total_users']}\n"
            f"✅ نشط اليوم: {stats['active_today']}\n"
            f"✅ المشتركين النشطين: {stats['active_users']}\n"
            f"🚫 المحظورين: {stats['blocked_users']}\n"
            f"📈 إجمالي التحليلات: {stats['total_analyses']}\n"
            f"⏳ منتهي قريباً: {stats['expiring_soon']}\n\n"
            f"📊 **الاشتراكات:**\n" + "\n".join([f"• {k}: {v}" for k, v in stats['subscriptions_by_type'].items()]),
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    elif user_message == "🔧 حالة البوت":
        bot_status = SubscriptionSystem.get_bot_status()
        keyboard = [["✅ تشغيل", "⏸️ إيقاف", "🔧 صيانة"], ["🔙 العودة"]]
        await update.message.reply_text(f"🔧 **حالة البوت:** {bot_status['status']}\n\nاختر الحالة الجديدة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
        return SET_BOT_STATUS
    elif user_message == "🌐 التحكم بالجلسات":
        keyboard = [["🌏 آسيوية فقط", "🇬🇧 لندن فقط"], ["🗽 نيويورك فقط", "🌍 جميع الجلسات"], ["🔙 العودة"]]
        await update.message.reply_text("🌐 **التحكم بالجلسات**\n\nاختر الجلسات المسموحة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
        return SET_SESSION_CONTROL
    elif user_message == "💰 تعديل أسعار الاشتراكات":
        await update.message.reply_text(
            "💰 **تعديل أسعار الاشتراكات**\n\n"
            "أرسل النوع والسعر الجديد:\n"
            "مثال: `monthly 35`\n\n"
            "الأنواع المتاحة: hourly, daily, weekly, monthly, yearly",
            parse_mode="Markdown"
        )
        context.user_data['admin_action'] = 'set_price'
        return ADMIN_MENU
    elif user_message == "🔗 إدارة الوكالة":
        pending = AffiliateSystem.get_pending_registrations()
        if pending:
            msg = "🔗 **طلبات التسجيل تحت الوكالة:**\n\n"
            for p in pending[:20]:
                msg += f"• المستخدم: `{p['user_id']}`\n  الكود: `{p['code']}`\n  الوكيل: @{p['owner_username']}\n  التاريخ: {p['date'][:16]}\n\n"
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            await update.message.reply_text("📭 **لا توجد طلبات تسجيل جديدة.**")
        await update.message.reply_text(
            "🔧 **إدارة الوكالة**\n\n"
            "لإنشاء كود وكيل جديد: `/gen_affiliate`\n"
            "لقبول طلب: `/approve_affiliate <رقم الطلب>`\n"
            "لرفض طلب: `/reject_affiliate <رقم الطلب>`\n"
            "لعرض رموزك: `/my_affiliates`",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    elif user_message == "📥 تحميل قاعدة المشتركين":
        users = SubscriptionSystem.get_all_users()
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['User ID', 'Username', 'Name', 'Subscription Type', 'Start Date', 'End Date', 'Status', 'Total Analyses', 'Timezone', 'Created At'])
        for u in users:
            uid, username, first_name, created, last_active, is_blocked, total_analyses, timezone, sub_name, start_date, end_date, is_active = u
            status = "محظور" if is_blocked == 1 else ("نشط" if is_active == 1 else "منتهي")
            writer.writerow([uid, username or '', first_name or '', sub_name or '', start_date or '', end_date or '', status, total_analyses, timezone, created])
        output.seek(0)
        await update.message.reply_document(document=io.BytesIO(output.getvalue().encode('utf-8-sig')), filename=f"subscribers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        return ADMIN_MENU
    elif user_message == "🔄 تحديث البوت":
        users = SubscriptionSystem.get_all_users()
        sent = 0
        for u in users:
            try:
                await context.bot.send_message(
                    chat_id=u[0],
                    text="🔄 **تم تحديث البوت إلى الإصدار الأحدث!** 🔄\n\n"
                         "✨ **المميزات الجديدة:**\n"
                         "• تحليل أسرع وأدق\n"
                         "• نظام اشتراكات متطور\n"
                         "• رادار أخبار فوري\n\n"
                         "📊 **لبدء التحليل، اضغط /start**",
                    parse_mode="Markdown"
                )
                sent += 1
                await asyncio.sleep(0.1)
            except:
                pass
        await update.message.reply_text(f"✅ تم إرسال إشعار التحديث لـ {sent} مستخدم")
        return ADMIN_MENU
    return ADMIN_MENU

async def handle_add_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return MAIN_MENU
    try:
        target_user_id = int(update.message.text.strip())
        context.user_data['target_user_id'] = target_user_id
        keyboard = [["🕐 ساعة", "📅 يوم"], ["📆 أسبوع", "📅 شهر"], ["♾️ مدى الحياة", "🔙 العودة"]]
        await update.message.reply_text(f"➕ **إضافة اشتراك للمستخدم:** `{target_user_id}`\n\nاختر المدة:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode="Markdown")
        return SELECT_DURATION
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال معرف المستخدم (أرقام فقط).")
        return ADD_SUBSCRIPTION

async def handle_select_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    duration_choice = update.message.text.strip()
    if user_id != ADMIN_ID:
        return MAIN_MENU
    if duration_choice == "🔙 العودة":
        return await start_admin_panel(update, context)
    sub_map = {"🕐 ساعة": "hourly", "📅 يوم": "daily", "📆 أسبوع": "weekly", "📅 شهر": "monthly", "♾️ مدى الحياة": "lifetime"}
    subscription_type = sub_map.get(duration_choice)
    if not subscription_type:
        await update.message.reply_text("❌ اختيار غير صالح.")
        return SELECT_DURATION
    target_user_id = context.user_data.get('target_user_id')
    if not target_user_id:
        await update.message.reply_text("❌ خطأ: لم يتم تحديد المستخدم.")
        return ADMIN_MENU
    success, result = SubscriptionSystem.add_subscription(target_user_id, subscription_type, added_by=user_id)
    if success:
        end_info = f"ينتهي في: {datetime.fromisoformat(result['end_date']).strftime('%Y-%m-%d %H:%M')}" if result['end_date'] else "غير محدد"
        await update.message.reply_text(f"✅ **تم إضافة الاشتراك!**\n\n👤 المستخدم: `{target_user_id}`\n📅 النوع: {result['subscription_name']}\n{end_info}", parse_mode="Markdown")
        try:
            await context.bot.send_message(chat_id=target_user_id, text=f"✅ **تم تفعيل اشتراكك!**\n\n📅 النوع: {result['subscription_name']}\n{end_info}\n\nأرسل /start للبدء", parse_mode="Markdown")
        except: pass
    else:
        await update.message.reply_text(f"❌ فشل: {result}")
    context.user_data.pop('target_user_id', None)
    return ADMIN_MENU

async def handle_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id != ADMIN_ID:
        return MAIN_MENU
    last_action = context.user_data.get('last_action')
    try:
        target_user_id = int(text)
        if last_action == 'block':
            success, msg = SubscriptionSystem.block_user(target_user_id, performed_by=user_id)
            await update.message.reply_text(f"✅ {msg}" if success else f"❌ {msg}")
        elif last_action == 'unblock':
            success, msg = SubscriptionSystem.unblock_user(target_user_id, performed_by=user_id)
            await update.message.reply_text(f"✅ {msg}" if success else f"❌ {msg}")
        else:
            success, msg = SubscriptionSystem.revoke_subscription(target_user_id, performed_by=user_id)
            if success:
                await update.message.reply_text(f"✅ {msg}")
                try:
                    await context.bot.send_message(chat_id=target_user_id, text="⚠️ **تم إلغاء اشتراكك**\n\nللتواصل: @Sz2zv", parse_mode="Markdown")
                except: pass
            else:
                await update.message.reply_text(f"❌ {msg}")
        context.user_data['last_action'] = None
        return ADMIN_MENU
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال معرف المستخدم (أرقام فقط).")
        return REMOVE_USER

async def handle_set_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return MAIN_MENU
    try:
        parts = update.message.text.strip().split()
        if len(parts) != 2:
            await update.message.reply_text("❌ استخدم: `user_id seconds`", parse_mode="Markdown")
            return SET_USER_DURATION
        target_user_id = int(parts[0])
        additional_seconds = int(parts[1])
        success, msg = SubscriptionSystem.extend_subscription(target_user_id, additional_seconds, performed_by=user_id)
        await update.message.reply_text(f"✅ {msg}" if success else f"❌ {msg}")
        if success:
            try:
                await context.bot.send_message(chat_id=target_user_id, text=f"✅ **تم تمديد اشتراكك**\n\n{msg}", parse_mode="Markdown")
            except: pass
    except:
        await update.message.reply_text("❌ صيغة خاطئة.")
    return ADMIN_MENU

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    broadcast_message = update.message.text.strip()
    if user_id != ADMIN_ID:
        return MAIN_MENU
    users = SubscriptionSystem.get_all_users()
    if not users:
        await update.message.reply_text("📭 لا يوجد مستخدمين.")
        return ADMIN_MENU
    wait_msg = await update.message.reply_text(f"📢 جاري الإذاعة لـ {len(users)} مستخدم...")
    success_count = 0
    for user in users:
        try:
            await context.bot.send_message(chat_id=user[0], text=f"📢 **رسالة إدارية**\n\n{broadcast_message}\n\n━━━━━━━━━━━━\nObeida Trading", parse_mode="Markdown")
            success_count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await wait_msg.edit_text(f"📊 **نتيجة الإذاعة**\n\n✅ تم الإرسال لـ: {success_count} مستخدم\n❌ فشل: {len(users) - success_count}")
    return ADMIN_MENU

async def handle_set_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return ADMIN_MENU
    user_message = update.message.text
    if user_message == "🔙 العودة":
        return await start_admin_panel(update, context)
    status_map = {"✅ تشغيل": "active", "⏸️ إيقاف": "stopped", "🔧 صيانة": "maintenance"}
    if user_message in status_map:
        SubscriptionSystem.set_bot_status(status_map[user_message], session_control='all', updated_by=user_id)
        await update.message.reply_text(f"✅ تم تغيير الحالة إلى: {user_message}")
        return await start_admin_panel(update, context)
    return SET_BOT_STATUS

async def handle_session_control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return ADMIN_MENU
    user_message = update.message.text
    if user_message == "🔙 العودة":
        return await start_admin_panel(update, context)
    session_map = {"🌏 آسيوية فقط": "asian", "🇬🇧 لندن فقط": "london", "🗽 نيويورك فقط": "newyork", "🌍 جميع الجلسات": "all"}
    if user_message in session_map:
        SubscriptionSystem.set_bot_status('active', session_control=session_map[user_message], updated_by=user_id)
        await update.message.reply_text(f"✅ تم تعيين الجلسات إلى: {user_message}")
        return await start_admin_panel(update, context)
    return SET_SESSION_CONTROL

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 **أوامر البوت:**
/start - بدء البوت
/help - عرض المساعدة
/cancel - إلغاء التحليل الحالي

⚙️ **كيفية الاستخدام:**
1. اضبط الإعدادات من القائمة
2. أرسل صورة الشارت للتحليل

💎 **الاشتراك:**
• ساعة: 1$
• يوم: 5$
• أسبوع: 15$
• شهر: 40$

📞 للتواصل: @Sz2zv
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cancel_analysis(update, context, update.effective_user.id)
    await update.message.reply_text("تم الإلغاء. اكتب /start للبدء.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"خطأ: {context.error}")
    if update and hasattr(update, 'effective_chat'):
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ حدث خطأ تقني. يرجى المحاولة مرة أخرى.")
        except: pass

async def periodic_cleanup():
    while True:
        await asyncio.sleep(60)
        cleanup_old_images()

class DataProtection:
    @staticmethod
    def backup_database():
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(BACKUP_DIR, f"main_backup_{timestamp}.db")
            if os.path.exists(DB_NAME):
                shutil.copy2(DB_NAME, backup_file)
                backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('main_backup_')])
                while len(backups) > 20:
                    os.remove(os.path.join(BACKUP_DIR, backups.pop(0)))
        except: pass
    @staticmethod
    def backup_codes_db():
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(BACKUP_DIR, f"subscription_backup_{timestamp}.db")
            if os.path.exists(SUBSCRIPTION_DB_NAME):
                shutil.copy2(SUBSCRIPTION_DB_NAME, backup_file)
        except: pass

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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔐 لوحة تسجيل الدخول</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif;}
        body{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);min-height:100vh;display:flex;justify-content:center;align-items:center;}
        .login-container{background:white;border-radius:20px;padding:40px;width:90%;max-width:400px;box-shadow:0 10px 40px rgba(0,0,0,0.2);}
        .login-header{text-align:center;margin-bottom:30px;}
        .login-header h1{color:#333;font-size:2em;}
        .logo{font-size:4em;margin-bottom:20px;}
        .form-group{margin-bottom:20px;}
        .form-group input{width:100%;padding:12px;border:2px solid #f0f0f0;border-radius:10px;}
        .btn-login{width:100%;padding:14px;background:#667eea;color:white;border:none;border-radius:10px;font-weight:600;cursor:pointer;}
        .error-message{background:#f8d7da;color:#721c24;padding:10px;border-radius:10px;margin-bottom:20px;}
        .footer{text-align:center;margin-top:20px;color:#999;}
    </style>
</head>
<body>
    <div class="login-container">
        <div class="login-header"><div class="logo">🤖</div><h1>Obeida Trading</h1></div>
        {% if error %}<div class="error-message">{{ error }}</div>{% endif %}
        <form method="POST" action="/login">
            <div class="form-group"><input type="text" name="username" placeholder="👤 اسم المستخدم" required autofocus></div>
            <div class="form-group"><input type="password" name="password" placeholder="🔑 كلمة المرور" required></div>
            <button type="submit" class="btn-login">تسجيل الدخول</button>
        </form>
        <div class="footer"><p>© 2026 Obeida Trading</p></div>
    </div>
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
        return render_template_string(LOGIN_PAGE_HTML, error='اسم المستخدم أو كلمة المرور غير صحيحة')
    return render_template_string(LOGIN_PAGE_HTML)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def home():
    stats = SubscriptionSystem.get_stats()
    return f"""
    <!DOCTYPE html>
    <html><head><title>🤖 Obeida Trading</title><style>body{{font-family:Arial;text-align:center;padding:50px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;}}</style></head>
    <body><h1>🤖 Obeida Trading</h1><p>AI Technical Analysis Bot</p>
    <div>👥 المستخدمين: {stats.get('total_users',0)} | ✅ النشطين: {stats.get('active_users',0)} | 📈 التحليلات: {stats.get('total_analyses',0)}</div>
    <a href="/admin" style="background:#28a745;color:white;padding:15px 30px;border-radius:5px;text-decoration:none;display:inline-block;margin-top:20px;">🔐 لوحة التحكم</a>
    </body></html>
    """

@app.route('/admin')
@login_required
def admin_panel():
    stats = SubscriptionSystem.get_stats()
    bot_status = SubscriptionSystem.get_bot_status()
    users = SubscriptionSystem.get_all_users()
    current_session = get_current_session()
    return render_template_string("""
    <!DOCTYPE html>
    <html dir="rtl"><head><meta charset="UTF-8"><title>لوحة التحكم</title><style>
        *{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI';}
        body{background:#f5f5f5;padding:20px;}
        .header{background:white;border-radius:15px;padding:20px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;}
        .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:15px;margin-bottom:20px;}
        .stat-card{background:white;border-radius:15px;padding:20px;box-shadow:0 2px 10px rgba(0,0,0,0.1);}
        .stat-card .number{font-size:2em;font-weight:bold;color:#667eea;}
        table{width:100%;background:white;border-radius:15px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,0.1);}
        th,td{padding:12px;text-align:center;border-bottom:1px solid #eee;}
        th{background:#667eea;color:white;}
        .btn{padding:8px 16px;border:none;border-radius:8px;cursor:pointer;margin:2px;}
        .btn-primary{background:#667eea;color:white;}
        .btn-danger{background:#dc3545;color:white;}
        .logout-btn{background:#dc3545;color:white;padding:10px 20px;border-radius:10px;text-decoration:none;}
        .status-active{background:#d4edda;color:#155724;padding:4px 12px;border-radius:20px;}
        .status-blocked{background:#f8d7da;color:#721c24;padding:4px 12px;border-radius:20px;}
    </style></head>
    <body>
        <div class="header"><h1>🤖 لوحة تحكم Obeida Trading</h1><a href="/logout" class="logout-btn">تسجيل الخروج</a></div>
        <div class="stats">
            <div class="stat-card"><h3>👥 المستخدمين</h3><div class="number">{{ stats.total_users }}</div></div>
            <div class="stat-card"><h3>✅ النشطين</h3><div class="number">{{ stats.active_users }}</div></div>
            <div class="stat-card"><h3>📈 التحليلات</h3><div class="number">{{ stats.total_analyses }}</div></div>
            <div class="stat-card"><h3>🌐 الجلسة</h3><div class="number">{{ current_session.name }}</div></div>
        </div>
        <div style="margin-bottom:20px;"><a href="/admin/subscribers_export" class="btn btn-primary">📥 تحميل قاعدة المشتركين</a></div>
        <table><thead><tr><th>المعرف</th><th>الاسم</th><th>الاشتراك</th><th>انتهاء الاشتراك</th><th>الحالة</th><th>التحليلات</th></tr></thead><tbody>
        {% for u in users %}<tr><td>{{ u[0] }}</td><td>{{ u[2] or u[1] or u[0] }}</td><td>{{ u[8] or 'غير مشترك' }}</td><td>{{ (u[10] or '-')[:16] }}</td><td><span class="{% if u[5]==1 %}status-blocked{% elif u[11]==1 %}status-active{% else %}status-blocked{% endif %}">{{ 'محظور' if u[5]==1 else ('نشط' if u[11]==1 else 'منتهي') }}</span></td><td>{{ u[6] }}</td></tr>{% endfor %}
        </tbody></table>
    </body></html>
    """, stats=stats, bot_status=bot_status, users=users, current_session=current_session)

@app.route('/admin/subscribers_export')
@login_required
def subscribers_export():
    users = SubscriptionSystem.get_all_users()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['User ID', 'Username', 'Name', 'Subscription Type', 'Start Date', 'End Date', 'Status', 'Total Analyses', 'Timezone', 'Created At'])
    for u in users:
        uid, username, first_name, created, last_active, is_blocked, total_analyses, timezone, sub_name, start_date, end_date, is_active = u
        status = "محظور" if is_blocked == 1 else ("نشط" if is_active == 1 else "منتهي")
        writer.writerow([uid, username or '', first_name or '', sub_name or '', start_date or '', end_date or '', status, total_analyses, timezone, created])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name=f"subscribers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")

@app.route('/health')
def health():
    stats = SubscriptionSystem.get_stats()
    return {"status": "active", "users": stats.get('total_users', 0), "active": stats.get('active_users', 0)}

def run_flask_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ============================================
# أوامر المشرف الإضافية
# ============================================

async def generate_affiliate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return
    username = update.effective_user.username or str(user_id)
    code = AffiliateSystem.generate_code(user_id, username)
    if code:
        await update.message.reply_text(f"✅ **تم إنشاء كود وكيل جديد:**\n`{code}`\n\nيمكنك مشاركة هذا الكود مع العملاء للتسجيل تحت وكالتك.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ فشل في إنشاء الكود.")

async def approve_affiliate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return
    try:
        reg_id = int(context.args[0])
        if AffiliateSystem.approve_registration(reg_id, user_id):
            await update.message.reply_text(f"✅ تم قبول الطلب رقم {reg_id}")
        else:
            await update.message.reply_text("❌ فشل في قبول الطلب.")
    except:
        await update.message.reply_text("❌ استخدم: `/approve_affiliate <رقم الطلب>`", parse_mode="Markdown")

async def reject_affiliate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return
    try:
        reg_id = int(context.args[0])
        if AffiliateSystem.reject_registration(reg_id):
            await update.message.reply_text(f"✅ تم رفض الطلب رقم {reg_id}")
        else:
            await update.message.reply_text("❌ فشل في رفض الطلب.")
    except:
        await update.message.reply_text("❌ استخدم: `/reject_affiliate <رقم الطلب>`", parse_mode="Markdown")

async def my_affiliates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    codes = AffiliateSystem.get_affiliate_codes(user_id)
    if not codes:
        await update.message.reply_text("📭 **ليس لديك رموز وكالة حتى الآن.**\n\nلإنشاء رمز جديد استخدم `/gen_affiliate`", parse_mode="Markdown")
        return
    msg = "🔗 **رموز الوكالة الخاصة بك:**\n\n"
    for c in codes:
        msg += f"• `{c['code']}`\n  تاريخ: {c['created_at'][:16]}\n  حالة: {'✅ نشط' if c['is_active'] else '❌ غير نشط'}\n  ✅ تم قبول: {c['approved_count']}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def set_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return
    try:
        sub_type = context.args[0]
        price = float(context.args[1])
        if AffiliateSystem.set_subscription_price(sub_type, price, user_id):
            await update.message.reply_text(f"✅ تم تعديل سعر {sub_type} إلى ${price}")
        else:
            await update.message.reply_text("❌ فشل في تعديل السعر.")
    except:
        await update.message.reply_text("❌ استخدم: `/set_prices <type> <price>`\nالأنواع: hourly, daily, weekly, monthly, yearly", parse_mode="Markdown")

# ============================================
# Main
# ============================================

def main():
    print("🤖 Starting Obeida Trading Bot v2.2...")
    init_db_with_subscription()
    DataProtection.backup_database()
    DataProtection.backup_codes_db()
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    application = Application.builder().token(TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu)],
            SETTINGS_CANDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_candle)],
            SETTINGS_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_time)],
            ANALYZE_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_analyze_mode), MessageHandler(filters.PHOTO, handle_photo_analysis)],
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            ADD_SUBSCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_subscription)],
            SELECT_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_select_duration)],
            REMOVE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_user)],
            SET_USER_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_duration)],
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)],
            SET_BOT_STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_bot_status)],
            SET_SESSION_CONTROL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session_control)],
            TIMEZONE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timezone_selection)],
            SEARCH_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_country_search)],
            AFFILIATE_REGISTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_affiliate_register)],
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("gen_affiliate", generate_affiliate))
    application.add_handler(CommandHandler("approve_affiliate", approve_affiliate))
    application.add_handler(CommandHandler("reject_affiliate", reject_affiliate))
    application.add_handler(CommandHandler("my_affiliates", my_affiliates))
    application.add_handler(CommandHandler("set_prices", set_prices))
    application.add_error_handler(error_handler)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(periodic_cleanup())
    # تشغيل رادار الأخبار
    news_radar = ObeidaNewsRadar(TOKEN)
    loop.create_task(news_radar.start_radar())
    print("✅ البوت جاهز للعمل...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
