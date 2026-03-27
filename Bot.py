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
import random
import string
import secrets
import feedparser
from datetime import datetime, timedelta
from typing import Set, Dict, Any, Optional, Tuple
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.error import NetworkError, TimedOut
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for, send_file, Response
from functools import wraps
from PIL import Image
import pytz
import psutil

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

# --- تعريف الأزرار والثوابت ---
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, ANALYZE_MODE, ADMIN_MENU, ADD_SUBSCRIPTION, SELECT_DURATION, REMOVE_USER, BROADCAST_MESSAGE, SET_USER_DURATION, SET_BOT_STATUS, SET_SESSION_CONTROL, TIMEZONE_MENU, SEARCH_COUNTRY, REGISTRATION_MENU, REGISTER_AFFILIATE, VERIFY_AFFILIATE_ID, AFFILIATE_SHOP = range(18)

CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["قصير (1m-15m)", "متوسط (4h-Daily)", "طويل (Weekly-Monthly)"]

# --- إعداد المجلدات ---
IMAGE_CACHE_DIR = "image_cache"
for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"📁 تم إنشاء المجلد: {directory}")

# ============================================
# نظام وكالة Tickmill
# ============================================
class AffiliateRegistrationSystem:
    @staticmethod
    def init_db():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        # جدول المستخدمين المسجلين في الوكالة
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                affiliate_id TEXT NOT NULL,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_verified INTEGER DEFAULT 0,
                verified_by INTEGER,
                verified_at TIMESTAMP,
                platform TEXT DEFAULT 'Tickmill',
                UNIQUE(user_id)
            )
        ''')
        # جدول طلبات التسجيل المعلقة
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pending_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                affiliate_id TEXT NOT NULL,
                platform TEXT DEFAULT 'Tickmill',
                requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                UNIQUE(user_id)
            )
        ''')
        # جدول إعدادات الوكالة
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS affiliate_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                registration_link TEXT,
                terms TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(platform)
            )
        ''')
        
        # إضافة وكالة Tickmill بشكل افتراضي
        cursor.execute('''
            INSERT OR IGNORE INTO affiliate_settings (platform, name, description, registration_link, terms)
            VALUES (?, ?, ?, ?, ?)
        ''', ('Tickmill', 'Tickmill', 'منصة تداول عالمية مرخصة', 'https://my.tickmill.com/en/sign-up/account-type?utm_campaign=ib_link&utm_content=IB96119705_MT', 'يجب التسجيل باستخدام رابط الوكالة ثم إرسال معرف العميل (Account ID) للمشرف للتحقق'))
        
        conn.commit()
        conn.close()
        print("✅ تم تهيئة نظام التسجيل عبر الوكالة")

    @staticmethod
    def is_user_registered(user_id: int) -> Tuple[bool, Optional[str], Optional[bool]]:
        """التحقق من تسجيل المستخدم عبر الوكالة"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                SELECT affiliate_id, is_verified FROM affiliate_registrations WHERE user_id = ?
            ''', (user_id,))
            result = cursor.fetchone()
            if result:
                return True, result[0], bool(result[1])
            
            # التحقق من وجود طلب معلق
            cursor.execute('''
                SELECT affiliate_id FROM pending_verifications WHERE user_id = ? AND status = 'pending'
            ''', (user_id,))
            pending = cursor.fetchone()
            if pending:
                return True, pending[0], False
                
            return False, None, False
        except Exception as e:
            print(f"⚠️ خطأ في is_user_registered: {e}")
            return False, None, False
        finally:
            conn.close()

    @staticmethod
    def request_registration(user_id: int, affiliate_id: str) -> Tuple[bool, str]:
        """تقديم طلب تسجيل جديد"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            # التحقق من عدم وجود طلب معلق
            cursor.execute('''
                SELECT id FROM pending_verifications WHERE user_id = ? AND status = 'pending'
            ''', (user_id,))
            if cursor.fetchone():
                return False, "لديك طلب قيد المراجعة بالفعل. يرجى الانتظار."

            # التحقق من عدم وجود تسجيل سابق
            cursor.execute('''
                SELECT id FROM affiliate_registrations WHERE user_id = ?
            ''', (user_id,))
            if cursor.fetchone():
                return False, "أنت مسجل بالفعل في الوكالة."

            # إضافة طلب جديد
            cursor.execute('''
                INSERT INTO pending_verifications (user_id, affiliate_id)
                VALUES (?, ?)
            ''', (user_id, affiliate_id))
            conn.commit()
            return True, "تم إرسال طلب التسجيل. سيتم مراجعته من قبل المشرف قريبًا."
        except Exception as e:
            conn.rollback()
            return False, f"حدث خطأ: {str(e)}"
        finally:
            conn.close()

    @staticmethod
    def get_pending_verifications() -> list:
        """جلب طلبات التسجيل المعلقة"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                SELECT p.id, p.user_id, p.affiliate_id, p.platform, p.requested_at,
                       u.username, u.first_name
                FROM pending_verifications p
                LEFT JOIN users u ON p.user_id = u.user_id
                WHERE p.status = 'pending'
                ORDER BY p.requested_at ASC
            ''')
            rows = cursor.fetchall()
            return rows
        except Exception as e:
            print(f"⚠️ خطأ في get_pending_verifications: {e}")
            return []
        finally:
            conn.close()

    @staticmethod
    def verify_registration(request_id: int, admin_id: int, approve: bool) -> Tuple[bool, str]:
        """الموافقة على طلب تسجيل أو رفضه"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                SELECT user_id, affiliate_id, platform FROM pending_verifications WHERE id = ?
            ''', (request_id,))
            req = cursor.fetchone()
            if not req:
                return False, "الطلب غير موجود"

            user_id, affiliate_id, platform = req

            if approve:
                # تحديث حالة الطلب
                cursor.execute('''
                    UPDATE pending_verifications SET status = 'approved' WHERE id = ?
                ''', (request_id,))
                # إضافة إلى جدول المسجلين
                cursor.execute('''
                    INSERT OR REPLACE INTO affiliate_registrations (user_id, affiliate_id, is_verified, verified_by, verified_at, platform)
                    VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP, ?)
                ''', (user_id, affiliate_id, admin_id, platform))
                # إعادة تعيين التحليلات المجانية
                conn_main = sqlite3.connect(DB_NAME)
                cursor_main = conn_main.cursor()
                cursor_main.execute('''
                    UPDATE users SET free_analyses = 3 WHERE user_id = ?
                ''', (user_id,))
                conn_main.commit()
                conn_main.close()
                conn.commit()
                return True, f"تم الموافقة على تسجيل المستخدم {user_id} بنجاح."
            else:
                cursor.execute('''
                    UPDATE pending_verifications SET status = 'rejected' WHERE id = ?
                ''', (request_id,))
                conn.commit()
                return True, f"تم رفض طلب المستخدم {user_id}."
        except Exception as e:
            conn.rollback()
            return False, f"حدث خطأ: {str(e)}"
        finally:
            conn.close()

    @staticmethod
    def get_affiliate_settings(platform: str = 'Tickmill') -> dict:
        """جلب إعدادات الوكالة"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                SELECT platform, name, description, registration_link, terms
                FROM affiliate_settings WHERE platform = ?
            ''', (platform,))
            row = cursor.fetchone()
            if row:
                return {
                    'platform': row[0],
                    'name': row[1],
                    'description': row[2],
                    'registration_link': row[3],
                    'terms': row[4]
                }
            return None
        except:
            return None
        finally:
            conn.close()

# ============================================
# نظام رادار الأخبار
# ============================================
class ObeidaNewsRadar:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.chat_group_id = '-1003449363659'
        self.db_name = NEWS_RADAR_DB
        self.processed_news: Set[str] = set()
        self.init_db()
        self.load_processed_news()
        print("📡 نظام رادار Obeida Trading قيد التشغيل...")

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
        try:
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            cursor.execute("SELECT guid FROM sent_news ORDER BY id DESC LIMIT 100")
            rows = cursor.fetchall()
            for row in rows:
                self.processed_news.add(row[0])
            conn.close()
        except:
            pass

    def get_currency_tag(self, title: str) -> str:
        title_lower = title.lower()
        for currency, tag in self.CURRENCY_MAP.items():
            if currency in title_lower:
                return tag
        return "#سوق_عام 🌐"

    def send_telegram_message(self, chat_id: str, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
        try:
            response = requests.post(url, data=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"⚠️ خطأ في إرسال الخبر: {e}")
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
                except Exception as e:
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
            print(f"❌ خطأ في رادار الأخبار: {e}")

    async def start_radar(self):
        print("📡 رادار الأخبار يعمل...")
        while True:
            try:
                await self.process_news()
            except Exception as e:
                print(f"❌ خطأ: {e}")
            await asyncio.sleep(20)

# ============================================
# نظام جلسات التداول
# ============================================
TRADING_SESSIONS = {
    'asian': {
        'name': '🌏 الجلسة الآسيوية',
        'start': 0,
        'end': 8,
        'volatility': 'منخفضة',
        'pairs': 'الين الياباني، الدولار الأسترالي، الدولار النيوزيلندي',
        'success_rate': '65-70%',
        'features': 'حركات هادئة، اختراقات حقيقية قليلة',
        'icon': '🌏',
        'time_range': '00:00-08:00 UTC',
    },
    'london': {
        'name': '🇬🇧 جلسة لندن',
        'start': 8,
        'end': 16,
        'volatility': 'مرتفعة',
        'pairs': 'اليورو، الجنيه، الفرنك',
        'success_rate': '75-85%',
        'features': 'سيولة عالية، فرص انعكاس ممتازة',
        'icon': '🇬🇧',
        'time_range': '08:00-16:00 UTC',
    },
    'newyork': {
        'name': '🗽 جلسة نيويورك',
        'start': 13,
        'end': 21,
        'volatility': 'عالية جداً',
        'pairs': 'الدولار، المؤشرات الأمريكية',
        'success_rate': '80-90%',
        'features': 'ذروة السيولة، تحركات عنيفة',
        'icon': '🗽',
        'time_range': '13:00-21:00 UTC',
    },
    'pacific': {
        'name': '🌊 جلسة المحيط الهادئ',
        'start': 21,
        'end': 24,
        'volatility': 'منخفضة جداً',
        'pairs': 'جميع الأزواج',
        'success_rate': '55-65%',
        'features': 'سيولة منخفضة، حركات جانبية',
        'icon': '🌊',
        'time_range': '21:00-00:00 UTC',
    },
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
def init_db():
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
            free_analyses INTEGER DEFAULT 3,
            total_analyses INTEGER DEFAULT 0,
            is_admin INTEGER DEFAULT 0,
            timezone TEXT DEFAULT 'GMT+02:00'
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (ADMIN_ID,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, username, is_admin, free_analyses) VALUES (?, ?, ?, ?)", 
                      (ADMIN_ID, "Sz2zv", 1, 1000))
    
    conn.commit()
    conn.close()
    print("✅ تم تهيئة قاعدة البيانات الأساسية")

def get_user_setting(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT candle, trade_time, last_analysis_context, last_analysis_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        return res
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
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "GMT+02:00"

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

def compress_image(image_path, max_size=(800, 800), quality=70):
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
    except Exception as e:
        print(f"⚠️ خطأ في ضغط الصورة: {e}")
        return image_path

def encode_image(image_path):
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
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
async def check_registration(update: Update, user_id: int) -> bool:
    """التحقق من تسجيل المستخدم في الوكالة"""
    if user_id == ADMIN_ID:
        return True
    
    is_reg, aff_id, is_verified = AffiliateRegistrationSystem.is_user_registered(user_id)
    
    if not is_reg:
        await update.message.reply_text(
            "⚠️ **يجب التسجيل في وكالتنا أولاً** ⚠️\n\n"
            "للاستفادة من خدمات البوت المجانية، يرجى التسجيل في منصة Tickmill عبر رابط الوكالة الخاص بنا.\n\n"
            "📌 **خطوات التسجيل:**\n"
            "1️⃣ اضغط على زر التسجيل أدناه\n"
            "2️⃣ قم بإنشاء حساب على Tickmill\n"
            "3️⃣ أرسل معرف العميل (Account ID) الخاص بك للمشرف\n\n"
            "🔗 **رابط التسجيل:**\n"
            "https://my.tickmill.com/en/sign-up/account-type?utm_campaign=ib_link&utm_content=IB96119705_MT\n\n"
            "✅ بعد إرسال المعرف، سيتم تفعيل حسابك خلال 24 ساعة.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 تسجيل في Tickmill", url="https://my.tickmill.com/en/sign-up/account-type?utm_campaign=ib_link&utm_content=IB96119705_MT")],
                [InlineKeyboardButton("✅ لدي حساب - إرسال المعرف", callback_data="send_affiliate_id")]
            ])
        )
        return False
    
    if not is_verified:
        await update.message.reply_text(
            "⏳ **حسابك قيد المراجعة** ⏳\n\n"
            f"معرف الوكالة المسجل: `{aff_id}`\n\n"
            "يرجى الانتظار حتى يتم التحقق من حسابك من قبل المشرف. سيتم إعلامك عند التفعيل.",
            parse_mode="Markdown"
        )
        return False
    
    return True

# ============================================
# دوال معالجة البوت
# ============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # تسجيل المستخدم في قاعدة البيانات
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, username, free_analyses) VALUES (?, ?, 3)", (user_id, user.username))
    conn.commit()
    conn.close()
    
    is_reg, aff_id, is_verified = AffiliateRegistrationSystem.is_user_registered(user_id)
    current_session = get_current_session()
    local_time = get_localized_time(user_id)
    
    welcome_msg = (
        "🚀 **أهلاً بك في Obeida Trading** 🚀\n\n"
        f"🆔 **معرفك:** `{user_id}`\n"
        f"👤 **اليوزر:** @{user.username or 'لا يوجد'}\n\n"
    )
    
    if is_verified:
        welcome_msg += "✅ **حالة التسجيل:** مفعل\n\n"
    elif is_reg:
        welcome_msg += f"⏳ **حالة التسجيل:** قيد المراجعة\n🆔 **معرف الوكالة:** `{aff_id}`\n\n"
    else:
        welcome_msg += "⚠️ **حالة التسجيل:** غير مسجل\nيرجى التسجيل في وكالتنا للاستفادة من الخدمات.\n\n"
    
    welcome_msg += (
        f"🌐 **الجلسة الحالية (UTC):** {current_session['name']}\n"
        f"📊 **معدل النجاح:** {current_session['success_rate']}\n"
        f"🎯 **المميزات:** {current_session['features']}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "4️⃣ **قراءة النتائج - أنواع القرارات الفنية** 🔍\n\n"
        "   **🟢 شراء:** السعر صاعد - ادخل مع الاتجاه\n"
        "   **🔴 بيع:** السعر هابط - ادخل مع الاتجاه\n"
        "   **💥 كسر هيكلي:** تغير الاتجاه\n"
        "   **🔄 سحب سيولة:** عكس مؤقت ثم استمرار\n"
        "   **🔄 انعكاس فوري:** تغير سريع ثم استقرار\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🤖 **Powered by Obeida Trading**"
    )
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🌍 المناطق الزمنية"]
    ]
    
    if not is_verified and not is_reg:
        keyboard.append(["📝 تسجيل في الوكالة"])
    
    if user_id == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    
    await update.message.reply_text(
        welcome_msg,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
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
        
        session_info = (
            f"🌐 **معلومات الجلسة والوقت**\n\n"
            f"⏰ **التوقيت العالمي (UTC):** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"🌍 **منطقتك الزمنية:** {user_tz}\n"
            f"🕒 **وقتك المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"📍 **الجلسة الحالية (UTC):** {current_session['name']}\n"
            f"📊 **التقلب:** {current_session['volatility']}\n"
            f"🎯 **نسبة النجاح:** {current_session['success_rate']}\n"
            f"💰 **الأزواج المناسبة:** {current_session['pairs']}\n"
            f"✨ **المميزات:** {current_session['features']}\n"
        )
        
        await update.message.reply_text(session_info, parse_mode="Markdown")
        return MAIN_MENU
    
    elif user_message == "👤 معلوماتي":
        is_reg, aff_id, is_verified = AffiliateRegistrationSystem.is_user_registered(user_id)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT free_analyses, total_analyses FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()
        conn.close()
        
        info_msg = (
            f"👤 **معلومات المستخدم**\n\n"
            f"🆔 **المعرف:** `{user_id}`\n"
            f"👤 **اليوزر:** @{update.effective_user.username or 'لا يوجد'}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        
        if is_verified:
            info_msg += f"✅ **حالة التسجيل:** مفعل (معرف: `{aff_id}`)\n"
        elif is_reg:
            info_msg += f"⏳ **حالة التسجيل:** قيد المراجعة (معرف: `{aff_id}`)\n"
        else:
            info_msg += "❌ **حالة التسجيل:** غير مسجل\n"
        
        info_msg += (
            f"🔢 **التحليلات المجانية المتبقية:** {user_data[0] if user_data else 3}\n"
            f"📈 **إجمالي التحليلات:** {user_data[1] if user_data else 0}\n"
            f"🌍 **منطقتك الزمنية:** {get_user_timezone(user_id)}\n"
            f"🕒 **وقتك المحلي:** {get_localized_time(user_id).strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        
        await update.message.reply_text(info_msg, parse_mode="Markdown")
        return MAIN_MENU
    
    elif user_message == "⚙️ إعدادات التحليل":
        keyboard = [CANDLE_SPEEDS[i:i+3] for i in range(0, len(CANDLE_SPEEDS), 3)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "⚙️ *إعدادات التحليل الفني*\n\nحدد سرعة الشموع للبدء:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return SETTINGS_CANDLE
    
    elif user_message == "📊 تحليل صورة":
        if not await check_registration(update, user_id):
            return MAIN_MENU
            
        candle, trade_time, _, _ = get_user_setting(user_id)
        
        if not candle or not trade_time:
            keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
            await update.message.reply_text(
                "❌ *يجب ضبط الإعدادات أولاً*\n\nالرجاء ضبط سرعة الشموع ومدة الصفقة قبل التحليل.",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return MAIN_MENU
        else:
            keyboard = [["الرجوع للقائمة الرئيسية"]]
            await update.message.reply_text(
                f"📊* نظام جاهز لتحليل *:\n\n"
                f"⚙️ الإعدادات المستخدمة:\n"
                f"• سرعة الشموع: {candle}\n"
                f"• مدة الصفقة: {trade_time}\n\n"
                f"📤 قم برفع صورة الشارت الآن:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return ANALYZE_MODE
    
    elif user_message == "📝 تسجيل في الوكالة":
        return await registration_menu(update, context)
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🌍 المناطق الزمنية"]
    ]
    if user_id == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    
    await update.message.reply_text(
        "اختر أحد الخيارات من القائمة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    return MAIN_MENU

async def registration_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_reg, aff_id, is_verified = AffiliateRegistrationSystem.is_user_registered(user_id)
    
    if is_verified:
        await update.message.reply_text(
            f"✅ **أنت مسجل بالفعل في وكالتنا!**\n\n🆔 **معرف الوكالة:** `{aff_id}`\n\nيمكنك الآن استخدام البوت مجاناً.",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    if is_reg:
        await update.message.reply_text(
            f"⏳ **لديك طلب قيد المراجعة**\n\n🆔 **معرف الوكالة المرسل:** `{aff_id}`\n\nسيتم إعلامك عند اكتمال التحقق.",
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
        "3️⃣ بعد الانتهاء، أرسل معرف العميل (Account ID) الخاص بك\n\n"
        "✅ **أرسل معرف العميل الآن:**\n"
        "مثال: `12345678`",
        parse_mode="Markdown"
    )
    return REGISTER_AFFILIATE

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    
    if data == "send_affiliate_id":
        await query.edit_message_text(
            "📝 **أرسل معرف العميل (Account ID)**\n\n"
            "يرجى إرسال معرف حسابك على منصة Tickmill.\n"
            "مثال: `12345678`\n\n"
            "سيتم التحقق من المعرف وتفعيل حسابك خلال 24 ساعة.",
            parse_mode="Markdown"
        )
        return REGISTER_AFFILIATE
    
    elif data.startswith("verify_reg_"):
        if user_id != ADMIN_ID:
            await query.answer("ليس لديك صلاحية", show_alert=True)
            return
        
        try:
            parts = data.split('_')
            request_id = int(parts[2])
            action = parts[3]
            
            if action == "approve":
                success, message = AffiliateRegistrationSystem.verify_registration(request_id, ADMIN_ID, True)
                await query.edit_message_text(f"✅ {message}")
            else:
                success, message = AffiliateRegistrationSystem.verify_registration(request_id, ADMIN_ID, False)
                await query.edit_message_text(f"❌ {message}")
                
            conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM pending_verifications WHERE id = ?", (request_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                try:
                    if action == "approve":
                        await context.bot.send_message(
                            chat_id=row[0],
                            text="✅ **تم تفعيل حسابك بنجاح!**\n\nيمكنك الآن استخدام البوت مجاناً.\nأرسل /start للبدء.",
                            parse_mode="Markdown"
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=row[0],
                            text="❌ **تم رفض طلب التسجيل**\n\nيرجى التواصل مع المشرف @Sz2zv للمساعدة.",
                            parse_mode="Markdown"
                        )
                except:
                    pass
        except Exception as e:
            await query.edit_message_text(f"❌ خطأ: {str(e)}")
    
    return

async def handle_affiliate_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    affiliate_id = update.message.text.strip()
    
    if not affiliate_id.isdigit() or len(affiliate_id) < 5:
        await update.message.reply_text(
            "❌ **معرف غير صالح**\n\nالرجاء إرسال معرف الحساب الصحيح (أرقام فقط).\nمثال: `12345678`",
            parse_mode="Markdown"
        )
        return REGISTER_AFFILIATE
    
    success, message = AffiliateRegistrationSystem.request_registration(user_id, affiliate_id)
    
    if success:
        await update.message.reply_text(
            f"✅ {message}\n\n🆔 **المعرف المرسل:** `{affiliate_id}`\n\nسيتم مراجعته من قبل المشرف.",
            parse_mode="Markdown"
        )
        
        pending_reqs = AffiliateRegistrationSystem.get_pending_verifications()
        if pending_reqs:
            keyboard = []
            for req in pending_reqs:
                req_id, uid, aid, platform, req_time, username, name = req
                keyboard.append([
                    InlineKeyboardButton(f"✅ قبول {uid}", callback_data=f"verify_reg_{req_id}_approve"),
                    InlineKeyboardButton(f"❌ رفض {uid}", callback_data=f"verify_reg_{req_id}_reject")
                ])
            
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📝 **طلب تسجيل جديد**\n\n👤 المستخدم: {user_id}\n🆔 المعرف: `{affiliate_id}`\n\nاختر الإجراء:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
    else:
        await update.message.reply_text(f"❌ {message}")
    
    return MAIN_MENU

async def handle_settings_candle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    
    if user_message in CANDLE_SPEEDS:
        save_user_setting(user_id, "candle", user_message)
        
        keyboard = [TRADE_TIMES[i:i+2] for i in range(0, len(TRADE_TIMES), 2)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            f"✅ **تم تعيين سرعة الشموع:** {user_message}\n\n⬇️ الآن حدد **مدة الصفقة** المتوقعة:\n\n• **قصير (1m-15m)**: تنفيذ سريع\n• **متوسط (4h-Daily)**: انتظار أيام\n• **طويل (Weekly-Monthly)**: استثمار طويل\n\nاختر الإطار الزمني المناسب:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
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
        save_user_setting(user_id, "trade_time", user_message)
        
        await update.message.reply_text(
            f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n✅ سرعة الشموع: {get_user_setting(user_id)[0]}\n✅ مدة الصفقة: {user_message}\n\n📤 قم الآن برفع صورة الشارت:",
            reply_markup=ReplyKeyboardMarkup([["📊 تحليل صورة"], ["الرجوع للقائمة الرئيسية"]], resize_keyboard=True),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    
    await update.message.reply_text(
        "📤 **الرجاء إرسال صورة الشارت فقط**\nأو اضغط 'الرجوع للقائمة الرئيسية'",
        reply_markup=ReplyKeyboardMarkup([["الرجوع للقائمة الرئيسية"]], resize_keyboard=True)
    )
    return ANALYZE_MODE

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await check_registration(update, user_id):
        return MAIN_MENU
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT free_analyses FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0] <= 0:
        await update.message.reply_text(
            "⚠️ **لقد استنفدت تحليلاتك المجانية** ⚠️\n\nيمكنك الحصول على تحليلات إضافية عن طريق التواصل مع المشرف @Sz2zv",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    candle, trade_time, _, _ = get_user_setting(user_id)
    
    if not candle or not trade_time:
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**\n\nالرجاء استخدام أزرار القائمة لضبط الإعدادات.",
            reply_markup=ReplyKeyboardMarkup([["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]], resize_keyboard=True),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    wait_msg = await update.message.reply_text("🤌 جاري تحليل الشارت... [⬜⬜⬜⬜⬜ 0%]")
    photo = await update.message.photo[-1].get_file()
    
    timestamp = int(time.time())
    original_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}_original.jpg")
    
    try:
        await photo.download_to_drive(original_path)
        compressed_path = compress_image(original_path)
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            return MAIN_MENU
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬜⬜⬜⬜🟩 20%]")
        
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
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬜⬜⬜🟩🟩 40%]")
        
        MAIN_PROMPT = f"""
أنت محلل فني متخصص. استخرج المعطيات الرقمية بدقة من الصورة المرفقة.

المطلوب استخراجه:
• أعلى قمة (HH): [سعر]
• أدنى قاع (LL): [سعر]
• السعر الحالي: [سعر]
• قوة الاندفاع: [رقم عشري]
• اتجاه الموجة: [UP/DOWN]
• مستوى 0.500 فيبوناتشي: [سعر]
• مستوى 0.236 فيبوناتشي: [سعر]
• مستوى 0.618 فيبوناتشي: [سعر]
• FVG: [True/False]
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
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬜⬜🟩🟩🟩 60%]")
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=90)
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬜🟩🟩🟩🟩 80%]")
        
        if response.status_code != 200:
            await wait_msg.edit_text(f"❌ خطأ: {response.status_code}")
            return MAIN_MENU
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [🟩🟩🟩🟩🟩 100%]")
        
        analysis_result = response.json()['choices'][0]['message']['content'].strip()
        
        import re
        current_price_match = re.search(r'السعر الحالي.*?:\s*([\d.]+)', analysis_result)
        breakout_force_match = re.search(r'قوة الاندفاع.*?:\s*([\d.]+)', analysis_result)
        direction_match = re.search(r'اتجاه الموجة.*?:\s*([A-Z]+)', analysis_result, re.IGNORECASE)
        fvg_match = re.search(r'FVG.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        order_block_match = re.search(r'Order Block.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        positioning_match = re.search(r'التمركز السعري.*?:\s*([A-Z_]+)', analysis_result)
        
        fib_match = re.search(r'مستوى 0.500.*?:\s*([\d.]+)', analysis_result)
        fib_500 = float(fib_match.group(1)) if fib_match else 0
        
        current_price = float(current_price_match.group(1)) if current_price_match else 0
        breakout_force = float(breakout_force_match.group(1)) if breakout_force_match else 1.0
        current_direction = direction_match.group(1).upper() if direction_match else "UP"
        has_fvg = fvg_match.group(1).upper() == "TRUE" if fvg_match else False
        has_order_block = order_block_match.group(1).upper() == "TRUE" if order_block_match else False
        positioning = positioning_match.group(1) if positioning_match else "NEUTRAL"
        
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
        confidence = min(100, max(0, confidence))
        
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
            else:
                action = "انتظار تصحيح 🟡"
                entry_type = "دخول معلق"
        
        formatted_result = f"""
📊 **التحليل الفني المتقدم:**
• الوقت: {current_time.strftime('%H:%M:%S')} UTC
• FVG: {"موجودة" if has_fvg else "غير موجودة"}
• Order Block: {"موجود" if has_order_block else "غير موجود"}

🎯 **الإشارة التنفيذية:**
• السعر الحالي: {current_price:.6f}
• القرار الفني: {action}
• قوة الإشارة: {signal_strength}
• نوع الدخول: {entry_type}

⚠️ **إدارة المخاطر:**
• مستوى الثقة: {confidence:.0f}%
• حجم الاستثمار: {"5%" if confidence >= 85 else "3%" if confidence >= 70 else "2%"}
"""
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET free_analyses = free_analyses - 1, total_analyses = total_analyses + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        
        await wait_msg.edit_text(formatted_result, parse_mode="Markdown")
        
    except Exception as e:
        print(f"❌ خطأ: {traceback.format_exc()}")
        await wait_msg.edit_text(f"❌ حدث خطأ: {str(e)[:200]}")
    finally:
        for filepath in [original_path]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass
    
    return MAIN_MENU

# ============================================
# المناطق الزمنية
# ============================================
TIMEZONES_BY_GMT = {
    "GMT+00:00": {"name": "توقيت غرينتش", "countries": ["لندن"]},
    "GMT+01:00": {"name": "أوروبا الوسطى", "countries": ["برلين", "باريس"]},
    "GMT+02:00": {"name": "أوروبا الشرقية", "countries": ["القاهرة", "الرياض", "القدس"]},
    "GMT+03:00": {"name": "العربية", "countries": ["جدة", "أبوظبي", "الدوحة"]},
    "GMT+04:00": {"name": "الخليج", "countries": ["دبي", "مسقط"]},
}

async def timezone_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    current_tz = get_user_timezone(user_id)
    local_time = get_localized_time(user_id)
    
    keyboard = [[f"{gmt} ({info['name']})"] for gmt, info in TIMEZONES_BY_GMT.items()]
    keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    await update.message.reply_text(
        f"🌍 **المناطق الزمنية**\n\n🕒 **منطقتك:** {current_tz}\n⏰ **الوقت:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}\n\nاختر المنطقة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return TIMEZONE_MENU

async def handle_timezone_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        return await start(update, context)
    
    selected_gmt = None
    for gmt in TIMEZONES_BY_GMT.keys():
        if user_message.startswith(gmt):
            selected_gmt = gmt
            break
    
    if selected_gmt:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (selected_gmt, user_id))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"✅ **تم تعيين منطقتك الزمنية بنجاح!**\n\n🕒 **المنطقة:** {selected_gmt}",
            parse_mode="Markdown"
        )
        return TIMEZONE_MENU
    
    await update.message.reply_text("❌ لم أتمكن من العثور على المنطقة الزمنية.")
    return TIMEZONE_MENU

# ============================================
# لوحة تحكم المشرف
# ============================================
async def start_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    keyboard = [
        ["👥 عرض المستخدمين", "📝 طلبات التسجيل"],
        ["📢 إذاعة", "📊 إحصائيات"],
        ["🔙 العودة"]
    ]
    
    await update.message.reply_text(
        f"👑 **لوحة تحكم المشرف** 👑\n\nاختر الإجراء المطلوب:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return ADMIN_MENU

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    if user_message == "🔙 العودة":
        return await start(update, context)
    
    elif user_message == "👥 عرض المستخدمين":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, free_analyses, total_analyses, timezone FROM users ORDER BY user_id")
        users = cursor.fetchall()
        conn.close()
        
        msg = "👥 **قائمة المستخدمين:**\n\n"
        for u in users:
            is_reg, aff_id, is_verified = AffiliateRegistrationSystem.is_user_registered(u[0])
            reg_status = "✅ مفعل" if is_verified else "⏳ قيد المراجعة" if is_reg else "❌ غير مسجل"
            msg += f"• {u[1] or u[0]} (`{u[0]}`)\n  🔢 تحليلات: {u[2]}/{u[3]} | {reg_status}\n  🌍 منطقة: {u[4]}\n\n"
        
        for part in split_message(msg, 4000):
            await update.message.reply_text(part, parse_mode="Markdown")
        return ADMIN_MENU
    
    elif user_message == "📝 طلبات التسجيل":
        pending = AffiliateRegistrationSystem.get_pending_verifications()
        
        if not pending:
            await update.message.reply_text("📭 لا توجد طلبات تسجيل معلقة.")
            return ADMIN_MENU
        
        for req in pending:
            req_id, uid, aid, platform, req_time, username, name = req
            keyboard = [
                [InlineKeyboardButton("✅ قبول", callback_data=f"verify_reg_{req_id}_approve"),
                 InlineKeyboardButton("❌ رفض", callback_data=f"verify_reg_{req_id}_reject")]
            ]
            await update.message.reply_text(
                f"📝 **طلب تسجيل جديد**\n\n👤 المستخدم: {uid} ({username or name})\n🆔 المعرف: `{aid}`\n📅 الوقت: {req_time[:16]}\n🏦 المنصة: {platform}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        return ADMIN_MENU
    
    elif user_message == "📢 إذاعة":
        await update.message.reply_text(
            "📢 **إذاعة للمستخدمين**\n\nأرسل الرسالة التي تريد إذاعتها:",
            parse_mode="Markdown"
        )
        return BROADCAST_MESSAGE
    
    elif user_message == "📊 إحصائيات":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(total_analyses) FROM users")
        analyses = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM users WHERE free_analyses > 0")
        active = cursor.fetchone()[0]
        conn.close()
        
        pending = len(AffiliateRegistrationSystem.get_pending_verifications())
        
        await update.message.reply_text(
            f"📊 **إحصائيات النظام**\n\n"
            f"👥 إجمالي المستخدمين: {total}\n"
            f"✅ مستخدمين نشطين: {active}\n"
            f"📊 إجمالي التحليلات: {analyses}\n"
            f"📝 طلبات معلقة: {pending}\n\n"
            f"🌐 الجلسة الحالية: {get_current_session()['name']}",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    return ADMIN_MENU

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    broadcast_message = update.message.text.strip()
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        await update.message.reply_text("📭 لا يوجد مستخدمين.")
        return ADMIN_MENU
    
    wait_msg = await update.message.reply_text(f"📢 جاري الإذاعة لـ {len(users)} مستخدم...")
    
    success_count = 0
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user[0],
                text=f"📢 **رسالة إدارية**\n\n{broadcast_message}\n\nObeida Trading",
                parse_mode="Markdown"
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await wait_msg.edit_text(f"✅ تم الإرسال لـ {success_count} مستخدم")
    return ADMIN_MENU

# ============================================
# Flask Web Server
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

LOGIN_PAGE_HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔐 لوحة التحكم - Obeida Trading</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; justify-content: center; align-items: center; }
        .login-container { background: white; border-radius: 20px; padding: 40px; width: 90%; max-width: 400px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); }
        .login-header { text-align: center; margin-bottom: 30px; }
        .login-header h1 { color: #333; font-size: 2em; }
        .logo { font-size: 4em; margin-bottom: 20px; }
        .form-group { margin-bottom: 20px; }
        .form-group input { width: 100%; padding: 12px; border: 2px solid #f0f0f0; border-radius: 10px; font-size: 1em; }
        .btn-login { width: 100%; padding: 14px; background: #667eea; color: white; border: none; border-radius: 10px; font-size: 1.1em; cursor: pointer; }
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
        <form method="POST">
            <div class="form-group">
                <input type="text" name="username" placeholder="اسم المستخدم" required autofocus>
            </div>
            <div class="form-group">
                <input type="password" name="password" placeholder="كلمة المرور" required>
            </div>
            <button type="submit" class="btn-login">تسجيل الدخول</button>
        </form>
        <div class="footer">
            <p>© 2026 Obeida Trading</p>
        </div>
    </div>
</body>
</html>
'''

ADMIN_PANEL_HTML = '''
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>👑 لوحة التحكم - Obeida Trading</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background: white; border-radius: 20px; padding: 30px; margin-bottom: 30px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #333; }
        .logout-btn { background: #dc3545; color: white; padding: 10px 20px; border-radius: 10px; text-decoration: none; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: white; border-radius: 15px; padding: 25px; text-align: center; }
        .stat-card .number { font-size: 2.5em; font-weight: bold; color: #667eea; }
        .section { background: white; border-radius: 20px; padding: 30px; margin-bottom: 30px; }
        .section-title { color: #333; font-size: 1.5em; margin-bottom: 20px; border-bottom: 2px solid #f0f0f0; padding-bottom: 10px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: center; border-bottom: 1px solid #f0f0f0; }
        th { background: #667eea; color: white; }
        .status-badge { padding: 5px 10px; border-radius: 20px; display: inline-block; }
        .status-active { background: #d4edda; color: #155724; }
        .status-pending { background: #fff3cd; color: #856404; }
        .btn { padding: 8px 16px; border: none; border-radius: 8px; cursor: pointer; margin: 2px; }
        .btn-success { background: #28a745; color: white; }
        .btn-danger { background: #dc3545; color: white; }
        .footer { text-align: center; color: white; margin-top: 30px; }
        .search-box { margin-bottom: 20px; }
        .search-box input { width: 100%; padding: 12px; border: 2px solid #f0f0f0; border-radius: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🤖 لوحة تحكم Obeida Trading</h1>
                <p>نظام إدارة الوكالة</p>
            </div>
            <div>
                <span style="margin-right: 20px;">مرحباً، المالك</span>
                <a href="/logout" class="logout-btn">تسجيل الخروج</a>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card"><h3>👥 المستخدمين</h3><div class="number">{{ stats.total_users }}</div></div>
            <div class="stat-card"><h3>📊 التحليلات</h3><div class="number">{{ stats.total_analyses }}</div></div>
            <div class="stat-card"><h3>📝 طلبات معلقة</h3><div class="number">{{ pending_count }}</div></div>
            <div class="stat-card"><h3>🌐 الجلسة</h3><div class="number" style="font-size: 1.2em;">{{ current_session.name }}</div></div>
        </div>

        <div class="section">
            <h2 class="section-title">👥 المستخدمين</h2>
            <div class="search-box"><input type="text" id="searchUsers" placeholder="🔍 بحث..." onkeyup="searchUsers()"></div>
            <div class="table-container">
                <table id="usersTable">
                    <thead><tr><th>المعرف</th><th>الاسم</th><th>تحليلات</th><th>الحالة</th><th>المنطقة</th></tr></thead>
                    <tbody>
                        {% for user in users %}
                        <tr>
                            <td>{{ user.id }}</td>
                            <td>{{ user.name }}</td>
                            <td>{{ user.free }}/{{ user.total }}</td>
                            <td><span class="status-badge {% if user.affiliate_status == 'مفعل' %}status-active{% else %}status-pending{% endif %}">{{ user.affiliate_status }}</span></td>
                            <td>{{ user.timezone }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">📝 طلبات التسجيل المعلقة</h2>
            <table>
                <thead><tr><th>المعرف</th><th>الاسم</th><th>معرف الوكالة</th><th>الوقت</th><th>الإجراءات</th></tr></thead>
                <tbody>
                    {% for req in pending %}
                    <tr>
                        <td>{{ req.user_id }}</td>
                        <td>{{ req.username }}</td>
                        <td>{{ req.affiliate_id }}</td>
                        <td>{{ req.requested_at[:16] }}</td>
                        <td>
                            <button class="btn btn-success" onclick="approve({{ req.id }})">✅ قبول</button>
                            <button class="btn btn-danger" onclick="reject({{ req.id }})">❌ رفض</button>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2 class="section-title">📢 إجراءات سريعة</h2>
            <button class="btn btn-success" onclick="exportData()">📊 تصدير البيانات</button>
        </div>

        <div class="footer">
            <p>🤯 Obeida Trading - نظام إدارة الوكالة</p>
        </div>
    </div>

    <script>
        function searchUsers() {
            let input = document.getElementById('searchUsers');
            let filter = input.value.toLowerCase();
            let table = document.getElementById('usersTable');
            let tr = table.getElementsByTagName('tr');
            for (let i = 1; i < tr.length; i++) {
                let td = tr[i].getElementsByTagName('td')[1];
                if (td) {
                    let txt = td.textContent || td.innerText;
                    tr[i].style.display = txt.toLowerCase().indexOf(filter) > -1 ? '' : 'none';
                }
            }
        }

        function approve(reqId) {
            fetch('/admin/verify', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({request_id: reqId, approve: true})
            }).then(() => location.reload());
        }

        function reject(reqId) {
            fetch('/admin/verify', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({request_id: reqId, approve: false})
            }).then(() => location.reload());
        }

        function exportData() {
            window.location.href = '/admin/export';
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
        hashed = hashlib.sha256(password.encode()).hexdigest()
        if username == ADMIN_USERNAME and hashed == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('admin_panel'))
        return render_template_string(LOGIN_PAGE_HTML, error='بيانات غير صحيحة')
    return render_template_string(LOGIN_PAGE_HTML)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_panel():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(total_analyses) FROM users")
    total_analyses = cursor.fetchone()[0] or 0
    cursor.execute("SELECT user_id, username, free_analyses, total_analyses, timezone FROM users")
    users_raw = cursor.fetchall()
    conn.close()
    
    users = []
    for u in users_raw:
        is_reg, aff_id, is_verified = AffiliateRegistrationSystem.is_user_registered(u[0])
        users.append({
            'id': u[0],
            'name': u[1] or str(u[0]),
            'free': u[2],
            'total': u[3],
            'timezone': u[4],
            'affiliate_status': 'مفعل' if is_verified else 'قيد المراجعة' if is_reg else 'غير مسجل'
        })
    
    pending_raw = AffiliateRegistrationSystem.get_pending_verifications()
    pending = []
    for p in pending_raw:
        pending.append({
            'id': p[0],
            'user_id': p[1],
            'affiliate_id': p[2],
            'platform': p[3],
            'requested_at': p[4],
            'username': p[5] or str(p[1])
        })
    
    return render_template_string(
        ADMIN_PANEL_HTML,
        stats={'total_users': total_users, 'total_analyses': total_analyses},
        pending_count=len(pending),
        pending=pending,
        users=users,
        current_session=get_current_session()
    )

@app.route('/admin/verify', methods=['POST'])
@login_required
def verify():
    data = request.json
    request_id = data.get('request_id')
    approve = data.get('approve', True)
    success, msg = AffiliateRegistrationSystem.verify_registration(request_id, ADMIN_ID, approve)
    return jsonify({'success': success})

@app.route('/admin/export')
@login_required
def export():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    conn.close()
    return Response(
        json.dumps({'users': users, 'export_date': datetime.now().isoformat()}, indent=2, default=str),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment;filename=obeida_export.json'}
    )

@app.route('/')
def home():
    return "<h1>🤖 Obeida Trading Bot</h1><p><a href='/admin'>لوحة التحكم</a></p>"

@app.route('/health')
def health():
    return {"status": "active", "timestamp": time.time()}

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ============================================
# الدالة الرئيسية
# ============================================
def main():
    print("🚀 Starting Obeida Trading Bot...")
    
    init_db()
    AffiliateRegistrationSystem.init_db()
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
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
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)],
            TIMEZONE_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timezone_selection)],
            REGISTRATION_MENU: [CallbackQueryHandler(handle_callback_query)],
            REGISTER_AFFILIATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_affiliate_registration)],
        },
        fallbacks=[CommandHandler('start', start)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    
    news_radar = ObeidaNewsRadar(TOKEN)
    
    async def run_radar():
        await news_radar.start_radar()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(run_radar())
    
    print("✅ Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
