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
import io

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
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, ANALYZE_MODE, ADMIN_MENU, ADD_SUBSCRIPTION, SELECT_DURATION, REMOVE_USER, BROADCAST_MESSAGE, SET_USER_DURATION, SET_BOT_STATUS, SET_SESSION_CONTROL, TIMEZONE_MENU, SEARCH_COUNTRY, AFFILIATES_MENU, ADD_AFFILIATE, VERIFY_AFFILIATE, SHOP_SETTINGS, IMPORT_USERS = range(19)

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

def format_analysis_with_copy_button(analysis_text):
    analysis_text = re.sub(r'```[\w]*\n?', '', analysis_text)
    analysis_text = re.sub(r'```\n?', '', analysis_text)
    formatted_text = f"```\n{analysis_text}\n```\n\n"
    formatted_text += "📋 **لنسخ التحليل:** اضغط على الرمز أعلاه ثم اختر Copy"
    return formatted_text

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

def get_analysis_context(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT last_analysis_context, last_analysis_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        return res[0] or "", res[1]
    return "", None

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
        "دولار": "USD 🇺🇸", "يورو": "EUR 🇪🇺", "إسترليني": "GBP 🇬🇧", "ين": "JPY 🇯🇵",
        "ذهب": "XAU 🟡", "فضة": "XAG ⚪", "نفط": "OIL 🛢️", "بتكوين": "BTC ₿",
        "إيثريوم": "ETH 💎", "ريال سعودي": "SAR 🇸🇦", "درهم": "AED 🇦🇪"
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS radar_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

    def send_telegram_message(self, chat_id: str, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
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
                    f"🚨 <b>خبر اقتصادي جديد:</b>\n\n{news.title}\n\n"
                    f"💰 <b>العملات المتأثرة:</b> {currency_tag}\n\n"
                    f"━━━━━━━━━━━━━\n⚡ <b>القناة:</b> @ObeidaTrading\n"
                )
                self.send_telegram_message(self.chat_group_id, message_text)
                self.processed_news.add(news_id)
                await asyncio.sleep(1)
                
        except Exception as e:
            logger.error(f"❌ خطأ: {e}")

    async def start_radar(self):
        logger.info("📡 رادار الأخبار يعمل...")
        while True:
            try:
                await self.process_news()
            except Exception as e:
                logger.error(f"خطأ: {e}")
            await asyncio.sleep(20)

# ============================================
# نظام الاشتراك المتطور مع متجر
# ============================================

class SubscriptionSystem:
    SUBSCRIPTION_TYPES = {
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
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shop_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE,
                setting_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        
        cursor.execute("SELECT COUNT(*) FROM shop_settings")
        if cursor.fetchone()[0] == 0:
            for sub_type, info in DEFAULT_SUBSCRIPTION_PRICES.items():
                cursor.execute('''
                    INSERT INTO shop_settings (setting_key, setting_value)
                    VALUES (?, ?)
                ''', (f'price_{sub_type}', json.dumps(info)))
            
            cursor.execute('''
                INSERT INTO shop_settings (setting_key, setting_value)
                VALUES (?, ?)
            ''', ('payment_links', json.dumps(DEFAULT_PAYMENT_LINKS)))
        
        cursor.execute("SELECT COUNT(*) FROM bot_status")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO bot_status (status, session_control) VALUES ('active', 'all')")
        
        conn.commit()
        conn.close()
        print("✅ تم تهيئة نظام الاشتراك المتطور بنجاح")
    
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
    def update_shop_price(subscription_type, price, currency='$'):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            info = {'price': price, 'currency': currency, 'name': DEFAULT_SUBSCRIPTION_PRICES.get(subscription_type, {}).get('name', subscription_type)}
            cursor.execute('''
                INSERT OR REPLACE INTO shop_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (f'price_{subscription_type}', json.dumps(info)))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    @staticmethod
    def update_payment_links(links):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO shop_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', ('payment_links', json.dumps(links)))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
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
    def add_subscription(user_id, subscription_type, added_by=ADMIN_ID, payment_method=None, payment_reference=None):
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
            
            shop_settings = SubscriptionSystem.get_shop_settings()
            price_info = shop_settings['prices'].get(subscription_type, {'price': 0, 'currency': '$'})
            
            cursor.execute("""
                UPDATE active_subscriptions 
                SET is_active = 0
                WHERE user_id = ? AND is_active = 1
            """, (user_id,))
            
            sub_name = SubscriptionSystem.SUBSCRIPTION_TYPES[subscription_type]['name']
            cursor.execute("""
                INSERT INTO active_subscriptions (
                    user_id, subscription_type, subscription_name, 
                    start_date, end_date, is_active, added_by,
                    payment_method, payment_reference, price, currency
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """, (user_id, subscription_type, sub_name, 
                  start_date.isoformat(), end_date.isoformat() if end_date else None, 
                  added_by, payment_method, payment_reference,
                  price_info['price'], price_info['currency']))
            
            conn.commit()
            return True, {
                'subscription_type': subscription_type,
                'subscription_name': sub_name,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat() if end_date else None,
                'price': price_info['price'],
                'currency': price_info['currency']
            }
        except Exception as e:
            conn.rollback()
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def check_subscription(user_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            if result and result[0] == 1:
                return False, "محظور", {'blocked': True}
            
            cursor.execute("""
                SELECT subscription_type, subscription_name, start_date, end_date
                FROM active_subscriptions 
                WHERE user_id = ? AND is_active = 1
                ORDER BY id DESC LIMIT 1
            """, (user_id,))
            
            result = cursor.fetchone()
            if not result:
                return False, "لا يوجد اشتراك نشط", None
            
            sub_type, sub_name, start_date, end_date = result
            
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
                SELECT subscription_name, start_date, end_date, subscription_type
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
                'subscription_end': sub_data[2] if sub_data else None,
                'subscription_type': sub_data[3] if sub_data else None
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
                SELECT u.user_id, u.username, u.first_name, u.created_at, u.last_active, u.is_blocked,
                       u.total_analyses, u.timezone,
                       a.subscription_name, a.start_date, a.end_date, a.is_active, a.subscription_type
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
            print(f"⚠️ خطأ: {e}")
        finally:
            conn.close()
        
        return stats
    
    @staticmethod
    def block_user(user_id, reason="Blocked by admin", performed_by=ADMIN_ID):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("UPDATE users SET is_blocked = 1, blocked_reason = ? WHERE user_id = ?", (reason, user_id))
            cursor.execute("UPDATE active_subscriptions SET is_active = 0 WHERE user_id = ? AND is_active = 1", (user_id,))
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
            cursor.execute("UPDATE users SET is_blocked = 0, blocked_reason = NULL WHERE user_id = ?", (user_id,))
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
            
            cursor.execute("UPDATE active_subscriptions SET end_date = ? WHERE id = ?", (new_end_date.isoformat(), sub_id))
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
    def import_users_from_file(file_content: str, file_format: str = 'csv'):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        results = {'added': 0, 'updated': 0, 'failed': 0, 'errors': []}
        
        try:
            if file_format == 'csv':
                reader = csv.DictReader(io.StringIO(file_content))
                for row in reader:
                    try:
                        user_id = int(row.get('user_id', 0))
                        if user_id == 0:
                            continue
                        username = row.get('username', '')
                        subscription_type = row.get('subscription_type', 'monthly')
                        end_date_str = row.get('end_date', None)
                        
                        SubscriptionSystem.register_user(user_id, username)
                        
                        if end_date_str:
                            try:
                                end_date = datetime.fromisoformat(end_date_str.replace(' ', 'T'))
                                remaining_seconds = (end_date - datetime.now()).total_seconds()
                                if remaining_seconds > 0:
                                    SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                                    cursor.execute("""
                                        UPDATE active_subscriptions 
                                        SET end_date = ? 
                                        WHERE user_id = ? AND is_active = 1
                                    """, (end_date_str, user_id))
                                    results['updated'] += 1
                                else:
                                    SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                                    results['added'] += 1
                            except:
                                SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                                results['added'] += 1
                        else:
                            SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                            results['added'] += 1
                    except Exception as e:
                        results['failed'] += 1
                        results['errors'].append(str(e))
            
            elif file_format == 'json':
                data = json.loads(file_content)
                for row in data:
                    try:
                        user_id = int(row.get('user_id', 0))
                        if user_id == 0:
                            continue
                        username = row.get('username', '')
                        subscription_type = row.get('subscription_type', 'monthly')
                        end_date_str = row.get('end_date', None)
                        
                        SubscriptionSystem.register_user(user_id, username)
                        
                        if end_date_str:
                            try:
                                end_date = datetime.fromisoformat(end_date_str.replace(' ', 'T'))
                                remaining_seconds = (end_date - datetime.now()).total_seconds()
                                if remaining_seconds > 0:
                                    SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                                    cursor.execute("""
                                        UPDATE active_subscriptions 
                                        SET end_date = ? 
                                        WHERE user_id = ? AND is_active = 1
                                    """, (end_date_str, user_id))
                                    results['updated'] += 1
                                else:
                                    SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                                    results['added'] += 1
                            except:
                                SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                                results['added'] += 1
                        else:
                            SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                            results['added'] += 1
                    except Exception as e:
                        results['failed'] += 1
                        results['errors'].append(str(e))
            
            elif file_format == 'txt':
                for line in file_content.strip().split('\n'):
                    try:
                        parts = line.strip().split('|')
                        if len(parts) >= 2:
                            user_id = int(parts[0])
                            username = parts[1] if len(parts) > 1 else ''
                            subscription_type = parts[2] if len(parts) > 2 else 'monthly'
                            end_date_str = parts[3] if len(parts) > 3 else None
                            
                            SubscriptionSystem.register_user(user_id, username)
                            
                            if end_date_str:
                                try:
                                    end_date = datetime.fromisoformat(end_date_str.replace(' ', 'T'))
                                    remaining_seconds = (end_date - datetime.now()).total_seconds()
                                    if remaining_seconds > 0:
                                        SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                                        cursor.execute("""
                                            UPDATE active_subscriptions 
                                            SET end_date = ? 
                                            WHERE user_id = ? AND is_active = 1
                                        """, (end_date_str, user_id))
                                        results['updated'] += 1
                                    else:
                                        SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                                        results['added'] += 1
                                except:
                                    SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                                    results['added'] += 1
                            else:
                                SubscriptionSystem.add_subscription(user_id, subscription_type, added_by=ADMIN_ID)
                                results['added'] += 1
                    except Exception as e:
                        results['failed'] += 1
                        results['errors'].append(str(e))
            
            conn.commit()
        except Exception as e:
            results['failed'] += 1
            results['errors'].append(str(e))
        finally:
            conn.close()
        
        return results
    
    @staticmethod
    def export_users_to_csv():
        users = SubscriptionSystem.get_all_users()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['user_id', 'username', 'subscription_type', 'subscription_name', 'start_date', 'end_date', 'status', 'total_analyses'])
        
        for u in users:
            uid, username, first_name, created, last_active, is_blocked, total_analyses, timezone, sub_name, start_date, end_date, is_active, sub_type = u
            status = 'blocked' if is_blocked == 1 else 'active' if is_active == 1 else 'expired'
            writer.writerow([uid, username or first_name or '', sub_type or '', sub_name or '', start_date or '', end_date or '', status, total_analyses])
        
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
    
    @staticmethod
    def get_remaining_time(user_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT end_date FROM active_subscriptions
                WHERE user_id = ? AND is_active = 1
                ORDER BY id DESC LIMIT 1
            """, (user_id,))
            
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

# ============================================
# نظام الوكلاء
# ============================================

class AffiliateSystem:
    @staticmethod
    def init_db():
        conn = sqlite3.connect(AFFILIATES_DB)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS affiliates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_id TEXT UNIQUE,
                name TEXT,
                description TEXT,
                terms TEXT,
                registration_link TEXT,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                affiliate_id TEXT,
                verified_by INTEGER,
                verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_verified BOOLEAN DEFAULT 0,
                FOREIGN KEY (affiliate_id) REFERENCES affiliates(affiliate_id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def add_affiliate(affiliate_id, name, description, terms, registration_link, created_by=ADMIN_ID):
        conn = sqlite3.connect(AFFILIATES_DB)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO affiliates (affiliate_id, name, description, terms, registration_link, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (affiliate_id, name, description, terms, registration_link, created_by))
            conn.commit()
            return True, "تم إضافة الوكيل بنجاح"
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def get_all_affiliates():
        conn = sqlite3.connect(AFFILIATES_DB)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT affiliate_id, name, description, terms, registration_link, created_at, is_active FROM affiliates WHERE is_active = 1")
            return cursor.fetchall()
        except:
            return []
        finally:
            conn.close()
    
    @staticmethod
    def get_affiliate(affiliate_id):
        conn = sqlite3.connect(AFFILIATES_DB)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT affiliate_id, name, description, terms, registration_link FROM affiliates WHERE affiliate_id = ? AND is_active = 1", (affiliate_id,))
            return cursor.fetchone()
        except:
            return None
        finally:
            conn.close()
    
    @staticmethod
    def register_user_for_affiliate(user_id, affiliate_id):
        conn = sqlite3.connect(AFFILIATES_DB)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO affiliate_users (user_id, affiliate_id, is_verified)
                VALUES (?, ?, 0)
            """, (user_id, affiliate_id))
            conn.commit()
            return True, "تم تسجيل طلب التحقق"
        except:
            return False, "حدث خطأ"
        finally:
            conn.close()
    
    @staticmethod
    def verify_user(user_id, affiliate_id, verified_by=ADMIN_ID):
        conn = sqlite3.connect(AFFILIATES_DB)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE affiliate_users 
                SET is_verified = 1, verified_by = ?
                WHERE user_id = ? AND affiliate_id = ?
            """, (verified_by, user_id, affiliate_id))
            conn.commit()
            return True, "تم التحقق من المستخدم"
        except:
            return False, "حدث خطأ"
        finally:
            conn.close()
    
    @staticmethod
    def get_pending_users(affiliate_id):
        conn = sqlite3.connect(AFFILIATES_DB)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT user_id, verified_at 
                FROM affiliate_users 
                WHERE affiliate_id = ? AND is_verified = 0
            """, (affiliate_id,))
            return cursor.fetchall()
        except:
            return []
        finally:
            conn.close()

# ============================================
# نظام متابعة الصفقات
# ============================================

class TradesSystem:
    @staticmethod
    def init_db():
        conn = sqlite3.connect(TRADES_DB)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                trade_type TEXT,
                entry_price REAL,
                exit_price REAL,
                profit_loss REAL,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                total_profit REAL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def add_trade(user_id, trade_type, entry_price, exit_price=None, status='open'):
        conn = sqlite3.connect(TRADES_DB)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO trades (user_id, trade_type, entry_price, exit_price, status)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, trade_type, entry_price, exit_price, status))
            conn.commit()
            return cursor.lastrowid
        except:
            return None
        finally:
            conn.close()
    
    @staticmethod
    def close_trade(trade_id, exit_price, profit_loss):
        conn = sqlite3.connect(TRADES_DB)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE trades 
                SET exit_price = ?, profit_loss = ?, status = 'closed', closed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (exit_price, profit_loss, trade_id))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()

# ============================================
# دوال مساعدة إضافية
# ============================================

def get_user_timezone(user_id):
    user_info = SubscriptionSystem.get_user_info(user_id)
    if user_info and user_info.get('timezone'):
        return user_info['timezone']
    return "GMT+02:00"

def set_user_timezone(user_id, timezone):
    SubscriptionSystem.set_user_timezone(user_id, timezone)

def get_localized_time(user_id):
    try:
        timezone_str = get_user_timezone(user_id)
        if timezone_str.startswith('GMT'):
            offset_str = timezone_str.replace('GMT', '')
            if offset_str and offset_str[0] in ('+', '-'):
                hours = int(offset_str.split(':')[0])
                minutes = int(offset_str.split(':')[1]) if ':' in offset_str else 0
                timezone = pytz.FixedOffset(hours * 60 + (minutes if hours >= 0 else -minutes))
                return datetime.now(timezone)
        return datetime.now(pytz.UTC)
    except:
        return datetime.now()

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
# دالة التحليل المتقدمة (الجزء المطلوب)
# ============================================

async def analyze_chart_image(update, context, image_path, candle, trade_time, symbol):
    try:
        user_id = update.effective_user.id
        prev_context, prev_time = get_analysis_context(user_id)
        
        wait_msg = await update.message.reply_text("🤔 انتظار قليلآ جاري تحليل الصورة 🤌...")
        
        compressed_path = compress_image(image_path)
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            return "❌ **خطأ في قراءة الصورة.**"
        
        current_session = get_current_session()
        current_time = datetime.utcnow()
        current_hour = current_time.hour
        current_minute = current_time.minute
        current_second = current_time.second
        
        # حساب الوقت المنقضي والمتبقي بدقة
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

        if seconds_passed < 0:
            seconds_passed = 0
        if seconds_passed > candle_seconds_total:
            seconds_passed = candle_seconds_total - 1
        if seconds_remaining < 0:
            seconds_remaining = 0
        
        # تعريف نافذة التنفيذ الذهبية
        def is_in_golden_execution_window(seconds_passed, candle_type, candle_seconds_total):
            if candle_type.startswith('S'):
                return 1 <= seconds_passed <= (candle_seconds_total - 2)
            else:
                return 2 <= seconds_passed <= 58

        in_golden_window = is_in_golden_execution_window(seconds_passed, candle, candle_seconds_total)
        
        # حساب مستويات Fibonacci
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
        
        # حساب الأهداف ووقف الخسارة
        def calculate_trade_parameters(current_price, fib_levels, direction, breakout_force):
            liquidity_safety = breakout_force > 1.2
            
            if direction == "UP":
                entry_point = current_price
                sl = fib_levels.get("1.000", current_price * 0.998)
                tp1 = fib_levels.get("0.500", current_price * 1.01)
                tp2 = fib_levels.get("0.000", current_price * 1.02)
                tp3 = fib_levels.get("1.272", 0) if liquidity_safety else 0
            else:
                entry_point = current_price
                sl = fib_levels.get("1.000", current_price * 1.002)
                tp1 = fib_levels.get("0.500", current_price * 0.99)
                tp2 = fib_levels.get("0.000", current_price * 0.98)
                tp3 = fib_levels.get("1.272", 0) if liquidity_safety else 0

            return {
                "entry": round(entry_point, 6),
                "tp1": round(tp1, 6),
                "tp2": round(tp2, 6),
                "tp3": round(tp3, 6),
                "sl": round(sl, 6)
            }
        
        # تحديد وقت انتهاء الصفقة
        def calculate_trade_expiry(candle_type):
            if candle_type.startswith('S'):
                seconds = int(candle_type[1:]) if candle_type[1:].isdigit() else 5
                return f"{seconds * 3} ثانية"
            elif candle_type.startswith('M'):
                minutes = int(candle_type[1:]) if candle_type[1:].isdigit() else 1
                return f"{minutes * 3} دقائق"
            elif candle_type.startswith('H'):
                hours = int(candle_type[1:]) if candle_type[1:].isdigit() else 1
                return f"{hours} ساعات"
            else:
                return "نهاية الجلسة"
        
        # تحديد جلسة السيولة
        def get_session_liquidity(current_hour):
            if 8 <= current_hour < 16:
                return "LONDON", "HIGH", "🇬🇧 جلسة لندن - سيولة عالية"
            elif 13 <= current_hour < 21:
                return "NEW_YORK", "HIGH", "🇺🇸 جلسة نيويورك - سيولة عالية"
            elif 0 <= current_hour < 8:
                return "ASIA", "LOW", "🇯🇵 جلسة آسيا - سيولة منخفضة"
            else:
                return "OFF_HOURS", "LOW", "🌙 خارج الجلسات - سيولة ضعيفة"
        
        session_name, liquidity_level, session_message = get_session_liquidity(current_hour)
        
        # دالة حساب حجم الاستثمار
        def calculate_position_size(confidence_score):
            if confidence_score >= 85:
                return "5%", "🔥 إشارة قوية جداً - استثمر 5% من الرصيد"
            elif confidence_score >= 70:
                return "3%", "⚡ إشارة قوية - استثمر 3% من الرصيد"
            elif confidence_score >= 55:
                return "2%", "🟡 إشارة متوسطة - استثمر 2% من الرصيد"
            elif confidence_score >= 40:
                return "1%", "🟠 إشارة ضعيفة - استثمر 1% فقط"
            else:
                return "0%", "🔴 انتظر - لا تدخل"
        
        # دالة القرار الموحدة
        def get_trading_decision(current_price, fib_levels, current_direction, 
                                 in_golden_window, breakout_force, has_fvg, 
                                 fvg_details, has_order_block, candle_type):
            
            trade_params = calculate_trade_parameters(current_price, fib_levels, current_direction, breakout_force)
            trade_expiry = calculate_trade_expiry(candle_type)
            
            is_in_discount = current_price < fib_levels.get("0.500", current_price)
            is_in_premium = current_price > fib_levels.get("0.500", current_price)
            
            confidence = 50
            conditions_met = []
            
            if in_golden_window:
                confidence += 20
                conditions_met.append("✅ داخل النافذة الذهبية")
            else:
                confidence -= 10
                conditions_met.append("⚠️ خارج النافذة الذهبية - دقة أقل")
            
            if has_fvg:
                confidence += 15
                conditions_met.append(f"✅ وجود FVG: {fvg_details if fvg_details else 'مؤكد'}")
            
            if has_order_block:
                confidence += 10
                conditions_met.append("✅ وجود Order Block")
            
            if breakout_force > 1.3:
                confidence += 25
                conditions_met.append(f"✅ زخم قوي {breakout_force:.1f}x")
            elif breakout_force > 1.1:
                confidence += 10
                conditions_met.append(f"✅ زخم جيد {breakout_force:.1f}x")
            
            entry_type_display = "دخول معلق"
            if in_golden_window and ((is_in_discount and current_direction == "UP") or (is_in_premium and current_direction == "DOWN")):
                entry_type_display = "دخول مباشر 🚨"
            
            if current_direction == "UP":
                technical_decision = "كسر هيكلي صاعد 💥🟢" if breakout_force > 1.2 else "ارتداد محتمل 📈"
            else:
                technical_decision = "كسر هيكلي هابط 🔻🔴" if breakout_force > 1.2 else "ارتداد محتمل 📉"
            
            if confidence >= 80:
                signal_strength = "عالية جداً 🔥🔥"
                signal_emoji = "🔥🔥"
            elif confidence >= 65:
                signal_strength = "عالية 🔥"
                signal_emoji = "🔥"
            elif confidence >= 50:
                signal_strength = "متوسطة ⚡"
                signal_emoji = "⚡"
            else:
                signal_strength = "ضعيفة 🟡"
                signal_emoji = "🟡"
            
            success_rate = 75 + (confidence - 50) * 0.3
            success_rate = min(95, max(55, success_rate))
            
            if success_rate >= 85:
                success_evaluation = "ممتاز"
            elif success_rate >= 70:
                success_evaluation = "جيد"
            else:
                success_evaluation = "مقبول"
            
            position_size, position_advice = calculate_position_size(confidence)
            
            if (current_direction == "UP" and is_in_discount) or (current_direction == "DOWN" and is_in_premium):
                if confidence >= 60:
                    action = "شراء 🟢" if current_direction == "UP" else "بيع 🔴"
                    decision = f"INSTITUTIONAL_{'BUY' if current_direction == 'UP' else 'SELL'} {action}"
                else:
                    action = "انتظار 🟡"
                    decision = "HOLD - ثقة منخفضة 🟡"
            else:
                action = "انتظار 🟡"
                decision = "HOLD - منطقة خطر 🚫"
            
            safety_note = "⚠️ انتظر الوصول إلى نقطة الدخول المحمية لتجنب الانعكاس" if entry_type_display == "دخول معلق" else "⚠️ الدخول المباشر يتطلب مراقبة مستمرة"
            
            return {
                "decision": decision,
                "action": action,
                "technical_decision": technical_decision,
                "signal_strength": signal_strength,
                "signal_emoji": signal_emoji,
                "entry_point": trade_params["entry"],
                "tp1": trade_params["tp1"],
                "tp2": trade_params["tp2"],
                "tp3": trade_params["tp3"],
                "sl": trade_params["sl"],
                "trade_duration": trade_expiry,
                "entry_type_display": entry_type_display,
                "safety_note": safety_note,
                "confidence": round(confidence),
                "confidence_level": signal_strength,
                "success_rate": round(success_rate),
                "success_evaluation": success_evaluation,
                "position_size": position_size,
                "position_advice": position_advice,
                "conditions_met": conditions_met
            }
        
        # MAIN_PROMPT
        MAIN_PROMPT = f"""
أنت الآن المحرك التحليلي لنظام Obeida Trading. مهمتك هي استخراج "المعطيات الخام" من الصورة بدقة رياضية.

🔴 **تعليمات المعالجة (Primary Processing):**

1. **استخراج النطاق السعري (Price Range):**
   - حدد أعلى قمة (Highest High) وأدنى قاع (Lowest Low) الظاهرين في الموجة الحالية بالشارت.
   - اقرأ السعر الحالي (Current Price) من المؤشر النشط.

2. **تحليل الزخم والكسر (Momentum & Breakout):**
   - قوة الاندفاع (Breakout Force): رقم عشري (مثل 1.5 يعني 150%)
   - اتجاه الموجة (Current Direction): [UP / DOWN]
   - نسبة جسم الشمعة (Candle Body Ratio): من 0.1 إلى 1.0

3. **كشف الفجوات والسيولة (SMC Detection) - مهم جداً:**
   - FVG (Fair Value Gap): هل توجد فجوة سعرية بين شمعتين؟ [True/False]
   - إذا كان FVG = True، حدد مستويات الفجوة: [من: X.XXXXX إلى: X.XXXXX]
   - Order Block: هل يوجد منطقة أوامر مؤسسية؟ [True/False]
   - Liquidity Sweep: هل اخترق السعر القمة/القاع بذيل طويل؟ [True/False]

4. **تحديد التمركز السعري (Positioning):**
   - PREMIUM: سعر فوق 0.500 والاتجاه هابط (منطقة بيع مثالية)
   - DISCOUNT: سعر تحت 0.500 والاتجاه صاعد (منطقة شراء مثالية)
   - TRAP_ZONE: عكس ذلك

📊 **بيانات الإطار الزمني:**
- الفريم: {candle}
- الوقت المنقضي: {seconds_passed} ثانية
- الوقت المتبقي: {seconds_remaining} ثانية

🎯 **التنسيق المطلوب للإجابة (التزم بهذا التنسيق بدقة):**

📐 **المعطيات الرقمية:**
• أعلى قمة (HH): [السعر]
• أدنى قاع (LL): [السعر]
• السعر الحالي: [السعر]
• مستوى 0.500 فيبوناتشي: [السعر]

⚡ **معطيات الزخم:**
• قوة الاندفاع: [رقم عشري]
• اتجاه الموجة: [UP/DOWN]
• نسبة جسم الشمعة: [0.xx]

🔍 **معطيات SMC:**
• FVG: [True/False]
• إذا FVG = True: مستويات FVG: من [X.XXXXX] إلى [X.XXXXX]
• Liquidity Sweep: [True/False]
• Order Block: [True/False]

📍 **التمركز السعري:**
• موقع السعر من 0.500: [PREMIUM/DISCOUNT/TRAP_ZONE]
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        GENERATION_CONFIG = {
            "max_tokens": 800,
            "temperature": 0.0,
            "top_p": 1.0,
            "random_seed": 42
        }
        
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
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=60)
        
        if response.status_code != 200:
            await wait_msg.edit_text(f"❌ خطأ: {response.status_code}")
            return
        
        analysis_result = response.json()['choices'][0]['message']['content'].strip()
        analysis_result = clean_repeated_text(analysis_result)
        
        # استخراج المعطيات من التحليل
        import re
        
        hh_match = re.search(r'أعلى قمة.*?:\s*([\d.]+)', analysis_result)
        ll_match = re.search(r'أدنى قاع.*?:\s*([\d.]+)', analysis_result)
        current_price_match = re.search(r'السعر الحالي.*?:\s*([\d.]+)', analysis_result)
        fib_500_match = re.search(r'0\.500.*?:\s*([\d.]+)', analysis_result)
        
        breakout_force_match = re.search(r'قوة الاندفاع.*?:\s*([\d.]+)', analysis_result)
        direction_match = re.search(r'اتجاه الموجة.*?:\s*([A-Z]+)', analysis_result)
        
        fvg_match = re.search(r'FVG.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        fvg_levels_match = re.search(r'مستويات FVG.*?:\s*من\s*([\d.]+)\s*إلى\s*([\d.]+)', analysis_result, re.IGNORECASE)
        order_block_match = re.search(r'Order Block.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        liquidity_sweep_match = re.search(r'Liquidity Sweep.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        
        try:
            highest_high = float(hh_match.group(1)) if hh_match else 0
            lowest_low = float(ll_match.group(1)) if ll_match else 0
            current_price = float(current_price_match.group(1)) if current_price_match else 0
            fib_500 = float(fib_500_match.group(1)) if fib_500_match else 0
            
            breakout_force = float(breakout_force_match.group(1)) if breakout_force_match else 1.0
            current_direction = direction_match.group(1).upper() if direction_match else "UP"
            
            has_fvg = fvg_match.group(1).upper() == "TRUE" if fvg_match else False
            fvg_from = float(fvg_levels_match.group(1)) if fvg_levels_match else 0
            fvg_to = float(fvg_levels_match.group(2)) if fvg_levels_match else 0
            fvg_details = f"[{fvg_from} إلى {fvg_to}]" if fvg_levels_match else "مؤكدة" if has_fvg else ""
            
            has_order_block = order_block_match.group(1).upper() == "TRUE" if order_block_match else False
            has_liquidity_sweep = liquidity_sweep_match.group(1).upper() == "TRUE" if liquidity_sweep_match else False
            
        except Exception as parse_error:
            highest_high, lowest_low, current_price, fib_500 = 0, 0, 0, 0
            breakout_force, current_direction = 1.0, "UP"
            has_fvg, has_order_block, has_liquidity_sweep = False, False, False
            fvg_details = ""
        
        # حساب مستويات فيبوناتشي
        fib_levels = {}
        if highest_high > 0 and lowest_low > 0:
            fib_levels = calculate_fibonacci_levels(highest_high, lowest_low, current_direction)
            if fib_500 == 0 and "0.500" in fib_levels:
                fib_500 = fib_levels["0.500"]
        
        if "0.500" not in fib_levels and fib_500 > 0:
            fib_levels["0.500"] = fib_500
        
        # تنفيذ دالة القرار
        decision = get_trading_decision(
            current_price=current_price,
            fib_levels=fib_levels,
            current_direction=current_direction,
            in_golden_window=in_golden_window,
            breakout_force=breakout_force,
            has_fvg=has_fvg,
            fvg_details=fvg_details,
            has_order_block=has_order_block,
            candle_type=candle
        )
        
        # بناء النتيجة النهائية
        golden_window_status = "✅ داخل النافذة الذهبية" if in_golden_window else "❌ خارج النافذة - دقة أقل 15%"
        
        fvg_display = "❌ غير موجود"
        if has_fvg and fvg_details:
            fvg_display = f"{fvg_details} - مؤكدة"
        elif has_fvg:
            fvg_display = "✅ موجودة - مؤكدة"
        
        order_block_display = "✅ موجودة" if has_order_block else "❌ غير موجودة"
        candle_status = "مغلقة" if seconds_remaining <= 0 else f"مفتوحة - الوقت المتبقي: {seconds_remaining} ثانية"
        
        tp1_label = "🎯 TP1 (توازن 0.500)"
        tp2_label = "🎯 TP2 (القمة/القاع 0.000)"
        tp3_label = "🚀 TP3 (انفجار 1.272)"
        
        tp1_display = f"{decision['tp1']} - {tp1_label}" if decision['tp1'] != 0 else "غير محدد"
        tp2_display = f"{decision['tp2']} - {tp2_label}" if decision['tp2'] != 0 else "غير محدد"
        tp3_display = f"{decision['tp3']} - {tp3_label}" if decision['tp3'] != 0 else "🚫 سيولة غير كافية للانفجار"
        
        sl_display = f"{decision['sl']} - (حماية خلف الهيكل)" if decision['sl'] != 0 else "غير محدد"
        entry_display = f"{decision['entry_point']} - ({decision['entry_type_display']})" if decision['entry_point'] != 0 else "غير محدد - انتظر الإشارة"
        
        formatted_result = f"""
📊 **التحليل الفني المتقدم:**
• **بروتوكول الفشل الذاتي:** الصورة صالحة
• **الوقت على المنصة (المرجع):** {current_time.strftime('%H:%M:%S')} UTC+2
• **حالة النافذة الذهبية:** {golden_window_status}
• **FVG (الفجوة السعرية):** {fvg_display}
• **Order Block (كتلة الأوامر):** {order_block_display}
• **Liquidity Sweep:** {'✅ موجود' if has_liquidity_sweep else '❌ غير موجود'}

🎯 **الإشارة التنفيذية:**
• **السعر الحالي:** {current_price}
• **حالة الشمعة:** {candle_status}

• **القرار الفني:** {decision['technical_decision']}

• **قوة الإشارة:** {decision['signal_strength']}
• **نقطة الدخول:** {entry_display}
• **نوع الدخول 🚨:** {decision['entry_type_display']}

• **الأهداف الربحية:**
  {tp1_display}
  {tp2_display}
  {tp3_display}

• **وقف الخسارة:** {sl_display}
• **المدة المتوقعة:** {decision['trade_duration']}
• **وقت الذروة المتوقع:** خلال الـ 3 شموع القادمة

⚠️ **إدارة المخاطر:**
• **مستوى الثقة:** {decision['confidence']}% = {decision['signal_emoji']}
• **نسبة نجاح الصفقة المتوقعة:** {decision['success_rate']}% - التقييم: {decision['success_evaluation']}
• **حجم الاستثمار:** {decision['position_size']} - {decision['position_advice']}

**ملاحظة مهمة:** {decision['safety_note']}
━━━━━━━━━━━━━━━
🌍 **جلسة التداول:** {session_message}
⏱️ **بيانات الوقت:**
• **الفريم:** {candle}
• **الوقت المنقضي:** {seconds_passed} ثانية
• **الوقت المتبقي:** {seconds_remaining} ثانية
• **حالة النافذة:** {'✅ داخل النافذة الذهبية' if in_golden_window else '❌ خارج النافذة الذهبية'}
🟢 {current_time.strftime('%H:%M:%S')} UTC
🤖 **Obeida Trading v1.0 Pro**
"""
        
        full_result = clean_repeated_text(formatted_result)
        
        try:
            if len(full_result) > 4000:
                parts = split_message(full_result, 3500)
                await wait_msg.edit_text(parts[0], parse_mode="Markdown")
                for part in parts[1:]:
                    await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(full_result, parse_mode="Markdown")
        except Exception as e:
            await wait_msg.edit_text(full_result)
        
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["📊 معلومات الجلسة", "👤 معلوماتي"],
            ["🌍 المناطق الزمنية"]
        ]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        await update.message.reply_text(
            "📊 **تم التحليل - اختر الإجراء التالي:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        
        return full_result
        
    except requests.exceptions.Timeout:
        try:
            await wait_msg.edit_text("⏱️ تجاوز الوقت المحدد")
        except:
            await update.message.reply_text("⏱️ تجاوز الوقت المحدد")
        return "⏱️ تجاوز الوقت المحدد"
    except Exception as e:
        print(f"❌ خطأ: {traceback.format_exc()}")
        error_msg = f"❌ خطأ: {str(e)[:200]}"
        try:
            await wait_msg.edit_text(error_msg)
        except:
            await update.message.reply_text(error_msg)
        return error_msg

# ============================================
# دوال معالجة البوت الأخرى
# ============================================

async def check_subscription(update: Update, user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    
    status, message, _ = SubscriptionSystem.check_subscription(user_id)
    if status:
        return True
    
    shop_settings = SubscriptionSystem.get_shop_settings()
    prices_text = "\n".join([f"• {info['name']}: {info['price']}{info['currency']}" for sub_type, info in shop_settings['prices'].items()])
    payment_links = shop_settings['payment_links']
    
    await update.message.reply_text(
        f"⚠️ **عذراً، ليس لديك اشتراك نشط**\n\n"
        f"🔑 **أسعار الاشتراكات:**\n{prices_text}\n\n"
        f"💳 **طرق الدفع:**\n"
        f"• PayPal: {payment_links.get('paypal', 'غير متوفر')}\n"
        f"• كريبتو: {payment_links.get('crypto', 'غير متوفر')}\n"
        f"• تحويل بنكي: {payment_links.get('bank', 'غير متوفر')}\n\n"
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
        last_name=user.last_name,
        language_code=user.language_code,
        is_bot=user.is_bot
    )
    
    user_info = SubscriptionSystem.get_user_info(user_id)
    status, sub_name, _ = SubscriptionSystem.check_subscription(user_id)
    
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
        return await send_update_notification(update, context)
    
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
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return SETTINGS_CANDLE
    
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
        payment_links = shop_settings['payment_links']
        
        await update.message.reply_text(
            f"✅ **تم اختيار: {user_message}**\n\n"
            f"💰 **السعر:** {price_info['price']}{price_info['currency']}\n\n"
            f"💳 **طرق الدفع المتاحة:**\n"
            f"• PayPal: {payment_links.get('paypal', 'غير متوفر')}\n"
            f"• كريبتو: {payment_links.get('crypto', 'غير متوفر')}\n"
            f"• تحويل بنكي: {payment_links.get('bank', 'غير متوفر')}\n\n"
            f"📞 بعد إتمام الدفع، أرسل إيصال الدفع مع معرف المستخدم ({update.effective_user.id}) للمشرف: @Sz2zv\n\n"
            f"سيتم تفعيل اشتراكك فور تأكيد الدفع.",
            parse_mode="Markdown"
        )
    
    return MAIN_MENU

async def send_update_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    await update.message.reply_text(
        "🔄 **تم تحديث البوت!** 🔄\n\n"
        "✨ **المميزات الجديدة:**\n"
        "• نظام تحليل متطور مع شريط تقدم\n"
        "• دعم التحليل المتزامن لعدة مستخدمين\n"
        "• متجر اشتراكات متكامل\n"
        "• نظام وكلاء للترويج\n"
        "• تحسينات في سرعة الاستجابة\n\n"
        "📊 **لتحليل الشارت:** أرسل صورة الشارت\n\n"
        "🤖 **لبدء الاستخدام:** اضغط /start",
        parse_mode="Markdown"
    )
    
    if user_id == ADMIN_ID:
        users = SubscriptionSystem.get_all_users()
        sent = 0
        for user in users[:10]:
            try:
                await context.bot.send_message(
                    chat_id=user[0],
                    text="🔄 **تم تحديث البوت!**\n\nتم إضافة مميزات جديدة وتحسينات. اضغط /start لبدء الاستخدام.",
                    parse_mode="Markdown"
                )
                sent += 1
                await asyncio.sleep(0.1)
            except:
                pass
        await update.message.reply_text(f"✅ تم إرسال إشعار التحديث لـ {sent} مستخدم")
    
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
            cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
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
            cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        cursor.execute("UPDATE users SET trade_time = ? WHERE user_id = ?", (user_message, user_id))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ مدة الصفقة: {user_message}\n\n"
            f"📤 **قم الآن برفع صورة الشارت للتحليل:**",
            reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

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
            await asyncio.sleep(0.5)
            progress = "█" * i + "▱" * (10 - i)
            await wait_msg.edit_text(
                f"🤌 **جاري تحليل الصورة...** 🤌\n\n"
                f"{progress} {i*10}%",
                parse_mode="Markdown"
            )
        
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
        
        result = await analyze_chart_image(update, context, compressed_path, candle, trade_time, "الشارت")
        
        SubscriptionSystem.increment_analysis_count(user_id)
        
        for path in [image_path, compressed_path]:
            if os.path.exists(path):
                os.remove(path)
                
    except Exception as e:
        await update.message.reply_text(f"❌ **حدث خطأ:** {str(e)[:200]}")
        logger.error(f"خطأ في التحليل: {traceback.format_exc()}")
    finally:
        with analysis_lock:
            active_analyses[user_id] = False
    
    return MAIN_MENU

async def timezone_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    current_tz = get_user_timezone(user_id)
    local_time = get_localized_time(user_id)
    
    timezones = [
        "GMT-12:00", "GMT-11:00", "GMT-10:00", "GMT-09:00", "GMT-08:00", "GMT-07:00", "GMT-06:00", "GMT-05:00",
        "GMT-04:00", "GMT-03:00", "GMT-02:00", "GMT-01:00", "GMT+00:00", "GMT+01:00", "GMT+02:00", "GMT+03:00",
        "GMT+03:30", "GMT+04:00", "GMT+04:30", "GMT+05:00", "GMT+05:30", "GMT+05:45", "GMT+06:00", "GMT+06:30",
        "GMT+07:00", "GMT+08:00", "GMT+09:00", "GMT+09:30", "GMT+10:00", "GMT+11:00", "GMT+12:00", "GMT+13:00", "GMT+14:00"
    ]
    
    keyboard = [[tz] for tz in timezones[:15]]
    keyboard.append(["🔙 العودة"])
    
    await update.message.reply_text(
        f"🌍 **المناطق الزمنية**\n\n"
        f"🕒 **منطقتك الحالية:** {current_tz}\n"
        f"⏰ **الوقت المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
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
        set_user_timezone(user_id, user_message)
        local_time = get_localized_time(user_id)
        await update.message.reply_text(
            f"✅ **تم تعيين منطقتك الزمنية بنجاح!**\n\n"
            f"🕒 **المنطقة:** {user_message}\n"
            f"⏰ **الوقت المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}",
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
    shop_settings = SubscriptionSystem.get_shop_settings()
    
    keyboard = [
        ["👥 عرض المستخدمين", "➕ إضافة اشتراك"],
        ["🗑️ إلغاء اشتراك", "⏱️ تمديد اشتراك"],
        ["🚫 حظر مستخدم", "✅ إلغاء حظر"],
        ["📢 إذاعة", "📊 إحصائيات"],
        ["🔧 حالة البوت", "🌐 التحكم بالجلسات"],
        ["⚙️ إعدادات المتجر", "👥 إدارة الوكلاء"],
        ["📥 تحميل المشتركين", "📁 استقبال ملف المشتركين"],
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
                uid, username, first_name, created, last_active, is_blocked, total_analyses, timezone, sub_name, start_date, end_date, is_active, sub_type = u
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
            f"📈 إجمالي التحليلات: {stats.get('total_analyses', 0)}\n"
            f"⏳ منتهي قريباً: {stats.get('expiring_soon', 0)}",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    elif user_message == "🔧 حالة البوت":
        bot_status = SubscriptionSystem.get_bot_status()
        keyboard = [
            ["✅ تشغيل", "⏸️ إيقاف", "🔧 صيانة"],
            ["🔙 العودة"]
        ]
        await update.message.reply_text(
            f"🔧 **حالة البوت:** {bot_status['status']}\n\nاختر الحالة الجديدة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return SET_BOT_STATUS
    
    elif user_message == "🌐 التحكم بالجلسات":
        keyboard = [
            ["🌏 آسيوية فقط", "🇬🇧 لندن فقط", "🗽 نيويورك فقط"],
            ["🌊 المحيط الهادئ فقط", "🌍 جميع الجلسات"],
            ["🔙 العودة"]
        ]
        await update.message.reply_text(
            "🌐 **التحكم بالجلسات**\n\nاختر الجلسات المسموحة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return SET_SESSION_CONTROL
    
    elif user_message == "⚙️ إعدادات المتجر":
        shop_settings = SubscriptionSystem.get_shop_settings()
        keyboard = [
            ["💰 تعديل أسعار الاشتراكات"],
            ["💳 تعديل روابط الدفع"],
            ["🔙 العودة"]
        ]
        await update.message.reply_text(
            f"⚙️ **إعدادات المتجر**\n\n"
            f"💰 **الأسعار الحالية:**\n" + 
            "\n".join([f"• {info['name']}: {info['price']}{info['currency']}" for info in shop_settings['prices'].values()]) + 
            f"\n\n💳 **روابط الدفع:**\n"
            f"• PayPal: {shop_settings['payment_links'].get('paypal', 'غير متوفر')}\n"
            f"• كريبتو: {shop_settings['payment_links'].get('crypto', 'غير متوفر')}\n"
            f"• بنكي: {shop_settings['payment_links'].get('bank', 'غير متوفر')}\n\n"
            f"اختر الإجراء:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return SHOP_SETTINGS
    
    elif user_message == "👥 إدارة الوكلاء":
        affiliates = AffiliateSystem.get_all_affiliates()
        keyboard = [
            ["➕ إضافة وكيل جديد"],
            ["🔙 العودة"]
        ]
        await update.message.reply_text(
            f"👥 **إدارة الوكلاء**\n\n"
            f"📋 **الوكلاء الحاليون:**\n" + 
            ("\n".join([f"• {aff[0]}: {aff[1]}" for aff in affiliates]) if affiliates else "لا يوجد وكلاء") +
            f"\n\nاختر الإجراء:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return AFFILIATES_MENU
    
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
    
    elif user_message == "📁 استقبال ملف المشتركين":
        await update.message.reply_text(
            "📁 **استقبال ملف المشتركين**\n\n"
            "قم برفع ملف CSV أو JSON أو TXT يحتوي على بيانات المشتركين.\n\n"
            "**تنسيق CSV:**\n"
            "```csv\nuser_id,username,subscription_type,end_date\n123456789,user1,monthly,2026-12-31\n```\n\n"
            "**تنسيق JSON:**\n"
            "```json\n[{\"user_id\": 123456789, \"username\": \"user1\", \"subscription_type\": \"monthly\", \"end_date\": \"2026-12-31\"}]\n```\n\n"
            "**تنسيق TXT:**\n"
            "```\n123456789|user1|monthly|2026-12-31\n```",
            parse_mode="Markdown"
        )
        return IMPORT_USERS
    
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
        
        if target_user_id == ADMIN_ID:
            await update.message.reply_text("❌ لا يمكن إلغاء اشتراك المشرف الرئيسي.")
            return ADMIN_MENU
        
        last_action = context.user_data.get('last_action')
        
        if last_action == 'block':
            success, message = SubscriptionSystem.block_user(target_user_id, performed_by=user_id)
            context.user_data['last_action'] = None
        elif last_action == 'unblock':
            success, message = SubscriptionSystem.unblock_user(target_user_id, performed_by=user_id)
            context.user_data['last_action'] = None
        else:
            success, message = SubscriptionSystem.revoke_subscription(target_user_id, performed_by=user_id)
            if success:
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text="⚠️ **تم إلغاء اشتراكك في البوت**\n\nللتواصل مع المشرف: @Sz2zv",
                        parse_mode="Markdown"
                    )
                except:
                    pass
        
        await update.message.reply_text(f"✅ {message}" if success else f"❌ {message}")
        
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
        
        if target_user_id == ADMIN_ID:
            await update.message.reply_text("✅ المشرف الرئيسي لديه صلاحيات غير محدودة.")
            return ADMIN_MENU
        
        success, message = SubscriptionSystem.extend_subscription(target_user_id, additional_seconds, performed_by=user_id)
        
        if success:
            await update.message.reply_text(f"✅ {message}")
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"✅ **تم تمديد اشتراكك!**\n\n{message}",
                    parse_mode="Markdown"
                )
            except:
                pass
        else:
            await update.message.reply_text(f"❌ {message}")
        
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

async def handle_set_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return ADMIN_MENU
    
    if user_message == "🔙 العودة":
        return await start_admin_panel(update, context)
    
    status_map = {
        "✅ تشغيل": "active",
        "⏸️ إيقاف": "stopped",
        "🔧 صيانة": "maintenance"
    }
    
    if user_message in status_map:
        new_status = status_map[user_message]
        SubscriptionSystem.set_bot_status(new_status, updated_by=user_id)
        await update.message.reply_text(f"✅ تم تغيير حالة البوت إلى: {user_message}")
        return await start_admin_panel(update, context)
    
    return SET_BOT_STATUS

async def handle_session_control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return ADMIN_MENU
    
    if user_message == "🔙 العودة":
        return await start_admin_panel(update, context)
    
    session_map = {
        "🌏 آسيوية فقط": "asian",
        "🇬🇧 لندن فقط": "london",
        "🗽 نيويورك فقط": "newyork",
        "🌊 المحيط الهادئ فقط": "pacific",
        "🌍 جميع الجلسات": "all"
    }
    
    if user_message in session_map:
        session_control = session_map[user_message]
        SubscriptionSystem.set_bot_status('active', session_control=session_control, updated_by=user_id)
        await update.message.reply_text(f"✅ تم تعيين الجلسات المسموحة إلى: {user_message}")
        return await start_admin_panel(update, context)
    
    return SET_SESSION_CONTROL

async def handle_shop_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return ADMIN_MENU
    
    if user_message == "🔙 العودة":
        return await start_admin_panel(update, context)
    
    elif user_message == "💰 تعديل أسعار الاشتراكات":
        await update.message.reply_text(
            "💰 **تعديل أسعار الاشتراكات**\n\n"
            "أرسل السعر الجديد بالتنسيق:\n"
            "`hourly:2` (ساعة)\n"
            "`daily:10` (يوم)\n"
            "`weekly:30` (أسبوع)\n"
            "`monthly:80` (شهر)\n"
            "`yearly:800` (سنة)\n"
            "`lifetime:2000` (مدى الحياة)\n\n"
            "مثال: `monthly:50`",
            parse_mode="Markdown"
        )
        context.user_data['shop_action'] = 'edit_price'
        return SHOP_SETTINGS
    
    elif user_message == "💳 تعديل روابط الدفع":
        await update.message.reply_text(
            "💳 **تعديل روابط الدفع**\n\n"
            "أرسل الروابط الجديدة بالتنسيق:\n"
            "`paypal:https://paypal.me/username`\n"
            "`crypto:0x742d35Cc6634C0532925a3b844Bc9e7598f0bEb0`\n"
            "`bank:للتواصل مع المشرف`\n\n"
            "مثال: `paypal:https://paypal.me/obeidatrading`",
            parse_mode="Markdown"
        )
        context.user_data['shop_action'] = 'edit_payment'
        return SHOP_SETTINGS
    
    elif context.user_data.get('shop_action') == 'edit_price':
        try:
            parts = user_message.split(':')
            if len(parts) == 2:
                sub_type = parts[0].strip()
                price = float(parts[1].strip())
                
                if sub_type in ['hourly', 'daily', 'weekly', 'monthly', 'yearly', 'lifetime']:
                    SubscriptionSystem.update_shop_price(sub_type, price)
                    await update.message.reply_text(f"✅ تم تحديث سعر {sub_type} إلى {price}$")
                else:
                    await update.message.reply_text("❌ نوع اشتراك غير صالح")
            else:
                await update.message.reply_text("❌ صيغة خاطئة. استخدم: `monthly:50`")
        except:
            await update.message.reply_text("❌ خطأ في الإدخال")
        
        context.user_data['shop_action'] = None
        return await handle_admin_menu(update, context)
    
    elif context.user_data.get('shop_action') == 'edit_payment':
        try:
            parts = user_message.split(':', 1)
            if len(parts) == 2:
                key = parts[0].strip()
                value = parts[1].strip()
                
                if key in ['paypal', 'crypto', 'bank']:
                    shop_settings = SubscriptionSystem.get_shop_settings()
                    links = shop_settings['payment_links']
                    links[key] = value
                    SubscriptionSystem.update_payment_links(links)
                    await update.message.reply_text(f"✅ تم تحديث رابط {key}")
                else:
                    await update.message.reply_text("❌ نوع رابط غير صالح. استخدم: paypal, crypto, bank")
            else:
                await update.message.reply_text("❌ صيغة خاطئة. استخدم: `paypal:https://...`")
        except:
            await update.message.reply_text("❌ خطأ في الإدخال")
        
        context.user_data['shop_action'] = None
        return await handle_admin_menu(update, context)
    
    return SHOP_SETTINGS

async def handle_affiliates_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return ADMIN_MENU
    
    if user_message == "🔙 العودة":
        return await start_admin_panel(update, context)
    
    elif user_message == "➕ إضافة وكيل جديد":
        await update.message.reply_text(
            "➕ **إضافة وكيل جديد**\n\n"
            "أرسل معلومات الوكيل بالتنسيق:\n"
            "`معرف_الوكيل|الاسم|الوصف|الشروط|رابط_التسجيل`\n\n"
            "مثال:\n"
            "`obeida|Obeida Trading|وكيل معتمد|شروط الاستخدام|https://t.me/ObeidaTrading`",
            parse_mode="Markdown"
        )
        context.user_data['affiliate_action'] = 'add'
        return AFFILIATES_MENU
    
    elif context.user_data.get('affiliate_action') == 'add':
        try:
            parts = user_message.split('|')
            if len(parts) >= 5:
                affiliate_id = parts[0].strip()
                name = parts[1].strip()
                description = parts[2].strip()
                terms = parts[3].strip()
                registration_link = parts[4].strip()
                
                success, message = AffiliateSystem.add_affiliate(
                    affiliate_id, name, description, terms, registration_link, user_id
                )
                await update.message.reply_text(f"✅ {message}" if success else f"❌ {message}")
            else:
                await update.message.reply_text("❌ صيغة خاطئة. استخدم: `معرف|اسم|وصف|شروط|رابط`")
        except:
            await update.message.reply_text("❌ خطأ في الإدخال")
        
        context.user_data['affiliate_action'] = None
        return await handle_admin_menu(update, context)
    
    return AFFILIATES_MENU

async def handle_import_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return MAIN_MENU
    
    if update.message.document:
        file = await update.message.document.get_file()
        file_content = await file.download_as_bytearray()
        file_content = file_content.decode('utf-8')
        
        filename = update.message.document.file_name.lower()
        
        if filename.endswith('.csv'):
            file_format = 'csv'
        elif filename.endswith('.json'):
            file_format = 'json'
        elif filename.endswith('.txt'):
            file_format = 'txt'
        else:
            await update.message.reply_text("❌ صيغة ملف غير مدعومة. استخدم CSV, JSON, أو TXT")
            return ADMIN_MENU
        
        wait_msg = await update.message.reply_text("📥 جاري معالجة الملف...")
        
        results = SubscriptionSystem.import_users_from_file(file_content, file_format)
        
        response = (
            f"✅ **تم معالجة الملف بنجاح!**\n\n"
            f"📊 **النتائج:**\n"
            f"• ✅ تمت الإضافة: {results['added']} مستخدم\n"
            f"• 🔄 تم التحديث: {results['updated']} مستخدم\n"
            f"• ❌ فشل: {results['failed']} مستخدم\n\n"
        )
        
        if results['errors']:
            response += f"⚠️ **الأخطاء:**\n" + "\n".join(results['errors'][:5])
        
        await wait_msg.edit_text(response, parse_mode="Markdown")
        
    else:
        await update.message.reply_text("❌ الرجاء إرسال ملف صالح (CSV, JSON, TXT)")
    
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
        uid, username, first_name, created, last_active, is_blocked, total_analyses, timezone, sub_name, start_date, end_date, is_active, sub_type = u
        
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
    news_radar = ObeidaNewsRadar(TOKEN)
    await news_radar.start_radar()

def main():
    print("🤖 Starting Obeida Trading Bot...")
    
    SubscriptionSystem.init_db()
    AffiliateSystem.init_db()
    TradesSystem.init_db()
    
    for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
        if not os.path.exists(directory):
            os.makedirs(directory)
    
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu)],
            SETTINGS_CANDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_candle)],
            SETTINGS_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_time)],
            ANALYZE_MODE: [MessageHandler(filters.PHOTO, handle_photo_analysis)],
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            ADD_SUBSCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_subscription)],
            SELECT_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_select_duration)],
            REMOVE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_user)],
            SET_USER_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_duration)],
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)],
            SET_BOT_STATUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_bot_status)],
            SET_SESSION_CONTROL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session_control)],
            TIMEZONE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timezone_selection)],
            SEARCH_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timezone_selection)],
            AFFILIATES_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_affiliates_menu)],
            ADD_AFFILIATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_affiliates_menu)],
            VERIFY_AFFILIATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_affiliates_menu)],
            SHOP_SETTINGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_shop_settings)],
            IMPORT_USERS: [MessageHandler(filters.Document.ALL, handle_import_users), MessageHandler(filters.TEXT & ~filters.COMMAND, handle_import_users)],
        },
        fallbacks=[CommandHandler('cancel', cancel), CommandHandler('start', start)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_error_handler(error_handler)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(periodic_tasks())
    loop.create_task(news_radar_task())
    
    print("✅ البوت جاهز للعمل!")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
