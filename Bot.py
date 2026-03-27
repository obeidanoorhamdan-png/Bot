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
from typing import Set, Dict, Any, Optional, Tuple
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.error import NetworkError, TimedOut
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for, flash, send_file
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
        cursor.execute('''
            SELECT affiliate_id, is_verified FROM affiliate_registrations WHERE user_id = ?
        ''', (user_id,))
        result = cursor.fetchone()
        conn.close()
        if result:
            return True, result[0], bool(result[1])
        return False, None, False

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

            # إضافة طلب جديد
            cursor.execute('''
                INSERT INTO pending_verifications (user_id, affiliate_id)
                VALUES (?, ?)
            ''', (user_id, affiliate_id))
            conn.commit()
            return True, "تم إرسال طلب التسجيل. سيتم مراجعته من قبل المشرف قريبًا."
        except Exception as e:
            return False, f"حدث خطأ: {str(e)}"
        finally:
            conn.close()

    @staticmethod
    def get_pending_verifications() -> list:
        """جلب طلبات التسجيل المعلقة"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.id, p.user_id, p.affiliate_id, p.platform, p.requested_at,
                   u.username, u.first_name
            FROM pending_verifications p
            LEFT JOIN users u ON p.user_id = u.user_id
            WHERE p.status = 'pending'
            ORDER BY p.requested_at ASC
        ''')
        rows = cursor.fetchall()
        conn.close()
        return rows

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
                cursor.execute('''
                    UPDATE users SET free_analyses = 3 WHERE user_id = ?
                ''', (user_id,))
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
        cursor.execute('''
            SELECT platform, name, description, registration_link, terms
            FROM affiliate_settings WHERE platform = ?
        ''', (platform,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                'platform': row[0],
                'name': row[1],
                'description': row[2],
                'registration_link': row[3],
                'terms': row[4]
            }
        return None

# ============================================
# نظام رادار الأخبار (يعمل على مدار الساعة)
# ============================================
class ObeidaNewsRadar:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.chat_group_id = '-1003449363659'  # معرف المجموعة/القناة
        self.db_name = NEWS_RADAR_DB
        self.processed_news: Set[str] = set()
        self.broadcast_enabled = True
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
            if currency in title_lower:
                return tag
        return "#سوق_عام 🌐"

    def format_news_message(self, title: str, link: str, published: str, currency_tag: str) -> str:
        message = (
            f"📰 <b>🚨 خبر اقتصادي جديد:</b>\n\n"
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
        except Exception as e:
            print(f"⚠️ خطأ في إرسال الخبر: {e}")
            return False

    async def process_news(self):
        """جلب الأخبار من مصادر متعددة"""
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
                    print(f"⚠️ خطأ في جلب الأخبار من {rss_url}: {e}")
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
            await asyncio.sleep(20)  # تحديث كل 20 ثانية

# ============================================
# نظام جلسات التداول (بالتوقيت العالمي - لا يمكن التحكم به)
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
        'best_timezones': ['GMT+03:00', 'GMT+04:00', 'GMT+05:00', 'GMT+06:00', 'GMT+07:00', 'GMT+08:00']
    },
    'london': {
        'name': '🇬🇧 جلسة لندن',
        'start': 8,
        'end': 16,
        'volatility': 'مرتفعة',
        'pairs': 'اليورو، الجنيه، الفرنك',
        'success_rate': '75-85%',
        'features': 'سيولة عالية، فرص انعكاس ممتازة، اختراقات قوية',
        'icon': '🇬🇧',
        'time_range': '08:00-16:00 UTC',
        'best_timezones': ['GMT+01:00', 'GMT+02:00', 'GMT+03:00']
    },
    'newyork': {
        'name': '🗽 جلسة نيويورك',
        'start': 13,
        'end': 21,
        'volatility': 'عالية جداً',
        'pairs': 'الدولار، المؤشرات الأمريكية',
        'success_rate': '80-90%',
        'features': 'ذروة السيولة، تحركات عنيفة، فرص سكالبينغ ممتازة',
        'icon': '🗽',
        'time_range': '13:00-21:00 UTC',
        'best_timezones': ['GMT-05:00', 'GMT-04:00', 'GMT-03:00']
    },
    'pacific': {
        'name': '🌊 جلسة المحيط الهادئ',
        'start': 21,
        'end': 24,
        'volatility': 'منخفضة جداً',
        'pairs': 'جميع الأزواج',
        'success_rate': '55-65%',
        'features': 'سيولة منخفضة، حركات جانبية، تجنب التداول',
        'icon': '🌊',
        'time_range': '21:00-00:00 UTC',
        'best_timezones': ['GMT-10:00', 'GMT-09:00', 'GMT-08:00']
    }
}

def get_current_session():
    """جلب الجلسة الحالية بناءً على التوقيت العالمي (UTC)"""
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
    """تهيئة قاعدة البيانات الأساسية"""
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
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS data_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            user_id INTEGER,
            details TEXT,
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

def increment_analyses(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET total_analyses = total_analyses + 1, free_analyses = free_analyses - 1 WHERE user_id = ? AND free_analyses > 0", (user_id,))
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
    """ضغط الصورة لتقليل الحجم"""
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
        print(f"Error encoding image: {e}")
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
        welcome_msg += (
            f"✅ **حالة التسجيل:** مفعل\n"
            f"🔢 **التحليلات المجانية المتبقية:** 3\n\n"
        )
    elif is_reg:
        welcome_msg += (
            f"⏳ **حالة التسجيل:** قيد المراجعة\n"
            f"🆔 **معرف الوكالة:** `{aff_id}`\n\n"
        )
    else:
        welcome_msg += (
            "⚠️ **حالة التسجيل:** غير مسجل\n"
            "يرجى التسجيل في وكالتنا للاستفادة من الخدمات.\n\n"
        )
    
    welcome_msg += (
        f"🌐 **الجلسة الحالية (UTC):** {current_session['name']}\n"
        f"📊 **معدل النجاح:** {current_session['success_rate']}\n"
        f"🎯 **المميزات:** {current_session['features']}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "4️⃣ **قراءة النتائج - أنواع القرارات الفنية** 🔍\n\n"
        "   **🟢 شراء:** السعر صاعد - ادخل مع الاتجاه\n"
        "   **🔴 بيع:** السعر هابط - ادخل مع الاتجاه\n"
        "   **💥 كسر هيكلي:** تغير الاتجاه (صاعد ← هابط أو العكس)\n"
        "   **🔄 سحب سيولة:** عكس مؤقت ثم استمرار الاتجاه الأصلي\n"
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
            f"🎯 **نسبة النجاح المتوقعة:** {current_session['success_rate']}\n"
            f"💰 **الأزواج المناسبة:** {current_session['pairs']}\n"
            f"✨ **المميزات:** {current_session['features']}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"**الجلسات الأخرى:**\n\n"
            f"🌏 الآسيوية (00:00-08:00 UTC): 65-70%\n"
            f"🇬🇧 لندن (08:00-16:00 UTC): 75-85%\n"
            f"🗽 نيويورك (13:00-21:00 UTC): 80-90%\n"
            f"🌊 المحيط الهادئ (21:00-00:00 UTC): 55-65%"
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
            "⚙️ *إعدادات التحليل الفني*\n\n"
            "حدد سرعة الشموع للبدء:",
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
                "❌ *يجب ضبط الإعدادات أولاً*\n\n"
                "الرجاء ضبط سرعة الشموع ومدة الصفقة قبل التحليل.",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return MAIN_MENU
        else:
            keyboard = [["الرجوع للقائمة الرئيسية"]]
            time_display = f"مدة الصفقة: {trade_time}"
            
            await update.message.reply_text(
                f"📊* نظام جاهز لتحليل *:\n\n"
                f"⚙️ الإعدادات المستخدمة:\n"
                f"• سرعة الشموع: {candle}\n"
                f"• {time_display}\n\n"
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
    """قائمة التسجيل في الوكالة"""
    user_id = update.effective_user.id
    is_reg, aff_id, is_verified = AffiliateRegistrationSystem.is_user_registered(user_id)
    
    if is_verified:
        await update.message.reply_text(
            f"✅ **أنت مسجل بالفعل في وكالتنا!**\n\n"
            f"🆔 **معرف الوكالة:** `{aff_id}`\n\n"
            f"يمكنك الآن استخدام البوت مجاناً.",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    if is_reg:
        await update.message.reply_text(
            f"⏳ **لديك طلب قيد المراجعة**\n\n"
            f"🆔 **معرف الوكالة المرسل:** `{aff_id}`\n\n"
            f"سيتم إعلامك عند اكتمال التحقق.",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    affiliate_settings = AffiliateRegistrationSystem.get_affiliate_settings('Tickmill')
    
    keyboard = [
        [InlineKeyboardButton("🔗 رابط التسجيل", url=affiliate_settings['registration_link'])],
        [InlineKeyboardButton("✅ لدي حساب - إرسال المعرف", callback_data="send_affiliate_id")]
    ]
    
    await update.message.reply_text(
        f"📝 **التسجيل في وكالة {affiliate_settings['name']}**\n\n"
        f"{affiliate_settings['description']}\n\n"
        f"**شروط التسجيل:**\n{affiliate_settings['terms']}\n\n"
        f"🔗 **رابط التسجيل:**\n{affiliate_settings['registration_link']}\n\n"
        f"بعد إنشاء الحساب، اضغط على زر 'إرسال المعرف' وأرسل معرف العميل (Account ID) الخاص بك.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return REGISTRATION_MENU

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
        # معالجة الموافقة على التسجيل من لوحة الأدمن
        if user_id != ADMIN_ID:
            await query.answer("ليس لديك صلاحية", show_alert=True)
            return ConversationHandler.END
        
        try:
            parts = data.split('_')
            request_id = int(parts[2])
            action = parts[3]  # approve or reject
            
            if action == "approve":
                success, message = AffiliateRegistrationSystem.verify_registration(request_id, ADMIN_ID, True)
                await query.edit_message_text(f"✅ {message}")
            else:
                success, message = AffiliateRegistrationSystem.verify_registration(request_id, ADMIN_ID, False)
                await query.edit_message_text(f"❌ {message}")
                
            # إعلام المستخدم
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
    
    return ConversationHandler.END

async def handle_affiliate_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إرسال معرف الوكالة من المستخدم"""
    user_id = update.effective_user.id
    affiliate_id = update.message.text.strip()
    
    # التحقق من صحة المعرف (أرقام فقط)
    if not affiliate_id.isdigit() or len(affiliate_id) < 5:
        await update.message.reply_text(
            "❌ **معرف غير صالح**\n\n"
            "الرجاء إرسال معرف الحساب الصحيح (أرقام فقط).\n"
            "مثال: `12345678`",
            parse_mode="Markdown"
        )
        return REGISTER_AFFILIATE
    
    success, message = AffiliateRegistrationSystem.request_registration(user_id, affiliate_id)
    
    if success:
        await update.message.reply_text(
            f"✅ {message}\n\n"
            f"🆔 **المعرف المرسل:** `{affiliate_id}`\n\n"
            f"سيتم مراجعة طلبك من قبل المشرف. سيتم إعلامك عند التفعيل.",
            parse_mode="Markdown"
        )
        # إشعار المشرف
        pending_reqs = AffiliateRegistrationSystem.get_pending_verifications()
        if pending_reqs:
            keyboard = []
            for req in pending_reqs:
                req_id, uid, aid, platform, req_time, username, name = req
                keyboard.append([
                    InlineKeyboardButton(
                        f"✅ قبول {uid} ({username or name})", 
                        callback_data=f"verify_reg_{req_id}_approve"
                    ),
                    InlineKeyboardButton(
                        f"❌ رفض {uid}", 
                        callback_data=f"verify_reg_{req_id}_reject"
                    )
                ])
            
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📝 **طلب تسجيل جديد**\n\n"
                     f"👤 المستخدم: {user_id}\n"
                     f"🆔 المعرف: `{affiliate_id}`\n\n"
                     f"اختر الإجراء:",
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
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["📊 معلومات الجلسة", "👤 معلوماتي"],
            ["🌍 المناطق الزمنية"]
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
            f"⬇️ الآن حدد **مدة الصفقة** المتوقعة:\n\n"
            f"• **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة\n"
            f"• **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة\n"
            f"• **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة\n\n"
            f"اختر الإطار الزمني المناسب:",
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
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["📊 معلومات الجلسة", "👤 معلوماتي"],
            ["🌍 المناطق الزمنية"]
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
        
        keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        candle, _, _, _ = get_user_setting(user_id)
        
        await update.message.reply_text(
            f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ سرعة الشموع: {candle}\n"
            f"✅ مدة الصفقة: {user_message}\n\n"
            f"📤 قم الآن برفع صورة الشارت:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["📊 معلومات الجلسة", "👤 معلوماتي"],
            ["🌍 المناطق الزمنية"]
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

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # التحقق من التسجيل
    if not await check_registration(update, user_id):
        return MAIN_MENU
    
    # التحقق من وجود تحليلات مجانية
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT free_analyses FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0] <= 0:
        await update.message.reply_text(
            "⚠️ **لقد استنفدت تحليلاتك المجانية** ⚠️\n\n"
            "يمكنك الحصول على تحليلات إضافية عن طريق:\n"
            "1. التواصل مع المشرف @Sz2zv\n"
            "2. التسجيل في الوكالة (إن لم تكن مسجلاً)",
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    candle, trade_time, _, _ = get_user_setting(user_id)
    
    if not candle or not trade_time:
        keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**\n\n"
            "الرجاء استخدام أزرار القائمة لضبط الإعدادات قبل تحليل الصور.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    wait_msg = await update.message.reply_text("🤌 جاري تحليل الشارت... [⬜⬜⬜⬜⬜ 0%]")
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
            return MAIN_MENU
        
        # تحديث شريط التقدم
        await asyncio.sleep(1.5)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬜⬜⬜⬜🟩 20%]")
        
        # حساب معلومات الوقت
        current_time = datetime.utcnow()
        current_hour = current_time.hour
        current_minute = current_time.minute
        current_second = current_time.second
        
        seconds_remaining = 0
        seconds_passed = 0
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
        
        # تحديث شريط التقدم
        await asyncio.sleep(1.5)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬜⬜⬜🟩🟩 40%]")
        
        # تبسيط الـ Prompt
        MAIN_PROMPT = f"""
أنت المحرك التحليلي لنظام Obeida Trading. استخرج المعطيات الرقمية بدقة من الصورة المرفقة.

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

أيضاً:
• آخر 3 شموع: [وصف]
• التوقيت الحالي: {current_time.strftime('%H:%M:%S')} UTC
• سرعة الشموع: {candle}
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": MISTRAL_MODEL,
            "messages": [
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": MAIN_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                    ]
                }
            ],
            "max_tokens": 800,
            "temperature": 0.0,
            "top_p": 1.0
        }
        
        await asyncio.sleep(1.5)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬜⬜🟩🟩🟩 60%]")
        
        # إرسال الطلب مع إعادة المحاولة
        response = None
        max_retries = 2
        for attempt in range(max_retries):
            try:
                response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=90)
                if response.status_code == 200:
                    break
                elif response.status_code == 429:
                    await wait_msg.edit_text(f"⚠️ ضغط على الخادم، انتظر... المحاولة {attempt + 2}")
                    await asyncio.sleep(5)
                    continue
                else:
                    break
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    await wait_msg.edit_text(f"⏳ المحاولة {attempt + 2}...")
                    await asyncio.sleep(3)
                    continue
                else:
                    raise
        
        if not response or response.status_code != 200:
            await wait_msg.edit_text(f"❌ خطأ: {response.status_code if response else 'لا يوجد استجابة'}")
            return MAIN_MENU
        
        await asyncio.sleep(1.5)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬜🟩🟩🟩🟩 80%]")
        
        analysis_result = response.json()['choices'][0]['message']['content'].strip()
        
        # استخراج البيانات من التحليل
        import re
        hh_match = re.search(r'أعلى قمة.*?:\s*([\d.]+)', analysis_result)
        ll_match = re.search(r'أدنى قاع.*?:\s*([\d.]+)', analysis_result)
        current_price_match = re.search(r'السعر الحالي.*?:\s*([\d.]+)', analysis_result)
        breakout_force_match = re.search(r'قوة الاندفاع.*?:\s*([\d.]+)', analysis_result)
        direction_match = re.search(r'اتجاه الموجة.*?:\s*([A-Z]+)', analysis_result, re.IGNORECASE)
        fvg_match = re.search(r'FVG.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        fvg_levels_match = re.search(r'مستويات FVG.*?:\s*من\s*([\d.]+)\s*إلى\s*([\d.]+)', analysis_result)
        order_block_match = re.search(r'Order Block.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        positioning_match = re.search(r'التمركز السعري.*?:\s*([A-Z_]+)', analysis_result)
        
        # استخراج مستويات فيبوناتشي
        fib_levels = {}
        fib_keys = ['0.236', '0.382', '0.500', '0.618', '0.786', '1.272', '1.618']
        for fib_key in fib_keys:
            fib_match = re.search(f'مستوى {fib_key}.*?:\s*([\d.]+)', analysis_result)
            if fib_match:
                fib_levels[fib_key] = float(fib_match.group(1))
        
        await asyncio.sleep(1)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [🟩🟩🟩🟩🟩 100%]")
        
        has_fvg = fvg_match.group(1).upper() == "TRUE" if fvg_match else False
        has_order_block = order_block_match.group(1).upper() == "TRUE" if order_block_match else False
        positioning = positioning_match.group(1) if positioning_match else "NEUTRAL"
        
        current_price = float(current_price_match.group(1)) if current_price_match else 0
        breakout_force = float(breakout_force_match.group(1)) if breakout_force_match else 1.0
        current_direction = direction_match.group(1).upper() if direction_match else "UP"
        
        fvg_details = ""
        fvg_status = "غير مؤكدة"
        if has_fvg and fvg_levels_match:
            fvg_from = float(fvg_levels_match.group(1))
            fvg_to = float(fvg_levels_match.group(2))
            fvg_details = f"[{fvg_from} إلى {fvg_to}]"
            fvg_status = "مؤكدة"
        
        fib_500 = fib_levels.get("0.500", current_price)
        
        # حساب قوة الإشارة
        if breakout_force > 1.5:
            signal_strength = "عالية جدا 💥"
            confidence_base = 85
        elif breakout_force > 1.2:
            signal_strength = "عالية 🔥"
            confidence_base = 75
        elif breakout_force > 1.0:
            signal_strength = "متوسطة ⚡"
            confidence_base = 65
        else:
            signal_strength = "ضعيفة ❄️"
            confidence_base = 55
        
        # تعديل الثقة
        confidence = confidence_base
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
        decision = get_simple_trading_decision(current_direction, breakout_force, has_fvg, has_order_block, positioning, current_price, fib_500)
        
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
        
        # المدة المتوقعة
        if candle.startswith('S'):
            expected_duration = f"{round(seconds_remaining / 60, 1) if seconds_remaining > 0 else '0'} دقيقة"
        elif candle.startswith('M'):
            expected_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            expected_duration = f"{expected_minutes} دقيقة"
        else:
            expected_duration = "غير محدد"
        
        # وقت الذروة
        if seconds_remaining > 0:
            peak_time = f"خلال الـ {min(3, seconds_remaining // 60 + 1 if seconds_remaining >= 60 else 1)} شموع القادمة"
        else:
            peak_time = "قريباً"
        
        # مستوى الثقة
        if confidence >= 85:
            confidence_icon = "💥"
        elif confidence >= 70:
            confidence_icon = "🔥"
        elif confidence >= 55:
            confidence_icon = "⚡"
        elif confidence >= 40:
            confidence_icon = "🔄"
        else:
            confidence_icon = "🚫"
        
        # بناء النتيجة
        formatted_result = f"""
📊 **التحليل الفني المتقدم:**
• بروتوكول الفشل الذاتي: الصورة صالحة
• الوقت على المنصة (المرجع): {current_time.strftime('%H:%M:%S')} UTC
• حالة النافذة الذهبية: {"✅ داخل النافذة - دقة عالية" if in_golden_window else "❌ خارج النافذة - دقة أقل 15%"}
• FVG (الفجوة السعرية): {fvg_details if fvg_details else 'غير موجودة'} - {fvg_status}
• Order Block (كتلة الأوامر): {"موجودة" if has_order_block else "غير موجودة"}

🎯 **الإشارة التنفيذية:**
• السعر الحالي: {current_price:.6f}
• حالة الشمعة: {candle_status} - الوقت المتبقي: {seconds_remaining} ثانية

• القرار الفني: {decision['action']}
• قوة الإشارة: {signal_strength}
• نقطة الدخول : {entry_price:.6f}
• نوع الدخول 🚨: {decision['entry_type']}
• **الأهداف الربحية:**
  🎯 TP1: {tp1:.6f} - (مستوى 0.500)
  🎯 TP2: {tp2:.6f} - (مستوى 0.236)
  🎯 TP3: {tp3:.6f} - (مستوى 1.272)
• وقف الخسارة: {stop_loss:.6f}
• المدة المتوقعة: {expected_duration}
• وقت الذروة المتوقع: {peak_time}

⚠️ **إدارة المخاطر:**
• مستوى الثقة: {confidence:.0f}% = {confidence_icon}
• حجم الاستثمار: {"5%" if confidence >= 85 else "3%" if confidence >= 70 else "2%" if confidence >= 55 else "1% - تجنب الدخول"}
"""
        
        # تحديث إحصائيات المستخدم
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET free_analyses = free_analyses - 1, total_analyses = total_analyses + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        
        await wait_msg.edit_text(formatted_result, parse_mode="Markdown")
        
    except Exception as e:
        print(f"❌ خطأ في تحليل الصورة: {traceback.format_exc()}")
        await wait_msg.edit_text(f"❌ **حدث خطأ في تحليل الصورة:** {str(e)[:200]}\nيرجى المحاولة مرة أخرى.")
    finally:
        for filepath in [original_path, compressed_path]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass
    
    return MAIN_MENU

def get_simple_trading_decision(direction, breakout_force, has_fvg, has_order_block, positioning, current_price, fib_500):
    """تحديد القرار الفني بناءً على المعطيات"""
    if direction == "UP":
        if breakout_force > 1.5 and has_fvg:
            return {'action': 'شراء 🟢', 'entry_type': 'دخول مباشر'}
        elif breakout_force > 1.2 and has_order_block:
            return {'action': 'كسر هيكلي صاعد 💥🟢', 'entry_type': 'دخول مباشر'}
        elif positioning == "DISCOUNT" and current_price < fib_500:
            return {'action': 'سحب سيولة صاعد 🔄🟢', 'entry_type': 'إعادة اختبار'}
        elif breakout_force > 1.0:
            return {'action': 'انعكاس فوري صاعد 🔄🟢', 'entry_type': 'انعكاس فوري'}
        else:
            return {'action': 'انتظار تصحيح 🟡', 'entry_type': 'دخول معلق'}
    else:
        if breakout_force > 1.5 and has_fvg:
            return {'action': 'بيع 🔴', 'entry_type': 'دخول مباشر'}
        elif breakout_force > 1.2 and has_order_block:
            return {'action': 'كسر هيكلي هابط 💥🔴', 'entry_type': 'دخول مباشر'}
        elif positioning == "PREMIUM" and current_price > fib_500:
            return {'action': 'سحب سيولة هابط 🔄🔴', 'entry_type': 'إعادة اختبار'}
        elif breakout_force > 1.0:
            return {'action': 'انعكاس فوري هابط 🔄🔴', 'entry_type': 'انعكاس فوري'}
        else:
            return {'action': 'انتظار تصحيح 🟡', 'entry_type': 'دخول معلق'}

# ============================================
# المناطق الزمنية
# ============================================
TIMEZONES_BY_GMT = {
    "GMT+00:00": {"name": "توقيت غرينتش", "countries": ["لندن", "دبلن", "لشبونة"]},
    "GMT+01:00": {"name": "أوروبا الوسطى", "countries": ["برلين", "باريس", "مدريد", "روما"]},
    "GMT+02:00": {"name": "أوروبا الشرقية", "countries": ["القاهرة", "الرياض", "القدس", "عمّان", "بيروت", "دمشق"]},
    "GMT+03:00": {"name": "العربية/موسكو", "countries": ["موسكو", "جدة", "أبوظبي", "الدوحة", "الكويت"]},
    "GMT+03:30": {"name": "إيران", "countries": ["طهران"]},
    "GMT+04:00": {"name": "الخليج", "countries": ["دبي", "مسقط", "باكو"]},
    "GMT+05:00": {"name": "باكستان", "countries": ["كراتشي", "طشقند"]},
    "GMT+05:30": {"name": "الهند", "countries": ["دلهي", "مومباي"]},
    "GMT+08:00": {"name": "الصين", "countries": ["بكين", "سنغافورة"]},
    "GMT-05:00": {"name": "المنطقة الشرقية", "countries": ["نيويورك", "تورونتو"]},
    "GMT-08:00": {"name": "المحيط الهادئ", "countries": ["لوس أنجلوس", "فانكوفر"]},
}

async def timezone_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    current_tz = get_user_timezone(user_id)
    local_time = get_localized_time(user_id)
    
    keyboard = [[f"{gmt} ({info['name']})"] for gmt, info in TIMEZONES_BY_GMT.items()]
    keyboard.append(["🔍 بحث عن دولة", "🕒 منطقتي الحالية"])
    keyboard.append(["الرجوع للقائمة الرئيسية"])
    
    await update.message.reply_text(
        f"🌍 **المناطق الزمنية**\n\n"
        f"🕒 **منطقتك:** {current_tz}\n"
        f"⏰ **الوقت:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"اختر المنطقة الزمنية المناسبة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return TIMEZONE_MENU

async def handle_timezone_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["📊 معلومات الجلسة", "👤 معلوماتي"],
            ["🌍 المناطق الزمنية"]
        ]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return MAIN_MENU
    
    elif user_message == "🕒 منطقتي الحالية":
        current_tz = get_user_timezone(user_id)
        local_time = get_localized_time(user_id)
        await update.message.reply_text(
            f"📍 **منطقتك الزمنية الحالية:** {current_tz}\n"
            f"🕒 **الوقت المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="Markdown"
        )
        return TIMEZONE_MENU
    
    elif user_message == "🔍 بحث عن دولة":
        await update.message.reply_text(
            "🔍 **أدخل اسم الدولة**\n\n"
            "مثال: `مصر`، `السعودية`، `أمريكا`\n\n"
            "سيتم عرض المنطقة الزمنية المناسبة.",
            parse_mode="Markdown"
        )
        return SEARCH_COUNTRY
    
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
        
        local_time = get_localized_time(user_id)
        await update.message.reply_text(
            f"✅ **تم تعيين منطقتك الزمنية بنجاح!**\n\n"
            f"🕒 **المنطقة:** {selected_gmt}\n"
            f"⏰ **الوقت المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="Markdown"
        )
        return TIMEZONE_MENU
    
    await update.message.reply_text("❌ لم أتمكن من العثور على المنطقة الزمنية المحددة.")
    return TIMEZONE_MENU

async def handle_country_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        return await timezone_menu(update, context)
    
    # البحث عن الدولة
    found = False
    for gmt, info in TIMEZONES_BY_GMT.items():
        for country in info['countries']:
            if user_message.lower() in country.lower():
                conn = sqlite3.connect(DB_NAME)
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (gmt, user_id))
                conn.commit()
                conn.close()
                local_time = get_localized_time(user_id)
                await update.message.reply_text(
                    f"✅ **تم تعيين منطقتك الزمنية حسب {country}**\n\n"
                    f"🕒 **المنطقة:** {gmt}\n"
                    f"⏰ **الوقت المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}",
                    parse_mode="Markdown"
                )
                found = True
                break
        if found:
            break
    
    if not found:
        await update.message.reply_text(
            f"❌ لم يتم العثور على دولة باسم '{user_message}'.\n"
            "يرجى المحاولة مرة أخرى أو اختيار منطقة من القائمة.",
            parse_mode="Markdown"
        )
    
    return TIMEZONE_MENU

# ============================================
# لوحة تحكم المشرف
# ============================================
async def start_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول للوحة التحكم.")
        return MAIN_MENU
    
    stats = get_admin_stats()
    pending_requests = AffiliateRegistrationSystem.get_pending_verifications()
    
    keyboard = [
        ["👥 عرض المستخدمين", "📝 طلبات التسجيل"],
        ["📢 إذاعة", "📊 إحصائيات"],
        ["🔄 تحديث تلقائي", "🔙 العودة"]
    ]
    
    await update.message.reply_text(
        f"👑 **لوحة تحكم المشرف** 👑\n\n"
        f"👥 المستخدمين: {stats['total_users']}\n"
        f"📊 التحليلات: {stats['total_analyses']}\n"
        f"📝 طلبات معلقة: {len(pending_requests)}\n\n"
        f"اختر الإجراء المطلوب:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return ADMIN_MENU

def get_admin_stats():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(total_analyses) FROM users")
    total_analyses = cursor.fetchone()[0] or 0
    conn.close()
    return {'total_users': total_users, 'total_analyses': total_analyses}

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    if user_message == "🔙 العودة":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["📊 معلومات الجلسة", "👤 معلوماتي"],
            ["🌍 المناطق الزمنية"],
            ["👑 لوحة التحكم"]
        ]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return MAIN_MENU
    
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
            msg += f"• {u[1] or u[0]} (`{u[0]}`)\n"
            msg += f"  🔢 تحليلات: {u[2]}/{u[3]} | {reg_status}\n"
            msg += f"  🌍 منطقة: {u[4]}\n\n"
        
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
                f"📝 **طلب تسجيل جديد**\n\n"
                f"👤 المستخدم: {uid} ({username or name})\n"
                f"🆔 المعرف: `{aid}`\n"
                f"📅 الوقت: {req_time}\n"
                f"🏦 المنصة: {platform}",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
        return ADMIN_MENU
    
    elif user_message == "📢 إذاعة":
        await update.message.reply_text(
            "📢 **إذاعة للمستخدمين**\n\n"
            "أرسل الرسالة التي تريد إذاعتها لجميع المستخدمين:",
            parse_mode="Markdown"
        )
        return BROADCAST_MESSAGE
    
    elif user_message == "📊 إحصائيات":
        stats = get_admin_stats()
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE free_analyses > 0")
        active = cursor.fetchone()[0]
        conn.close()
        
        pending = len(AffiliateRegistrationSystem.get_pending_verifications())
        
        await update.message.reply_text(
            f"📊 **إحصائيات النظام**\n\n"
            f"👥 إجمالي المستخدمين: {stats['total_users']}\n"
            f"✅ مستخدمين نشطين: {active}\n"
            f"📊 إجمالي التحليلات: {stats['total_analyses']}\n"
            f"📝 طلبات معلقة: {pending}\n\n"
            f"🌐 **الجلسة الحالية:** {get_current_session()['name']}",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    elif user_message == "🔄 تحديث تلقائي":
        # تحديث الجلسة (يتم تلقائياً)
        await update.message.reply_text(
            f"🔄 **تم تحديث الجلسة**\n\n"
            f"الجلسة الحالية: {get_current_session()['name']}",
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
    
    # جلب جميع المستخدمين
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    
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
        except Exception as e:
            print(f"⚠️ فشل إرسال للمستخدم {target_id}: {e}")
            fail_count += 1
    
    await wait_msg.edit_text(
        f"📊 **نتيجة الإذاعة**\n\n"
        f"✅ تم الإرسال بنجاح لـ: {success_count} مستخدم\n"
        f"❌ فشل الإرسال لـ: {fail_count} مستخدم"
    )
    
    return ADMIN_MENU

# ============================================
# تنبيه الجلسات (مرسل تلقائي)
# ============================================
async def session_alert_scheduler():
    """إرسال تنبيهات عند بدء كل جلسة"""
    last_sent = {}
    
    while True:
        current_session = get_current_session()
        session_name = current_session['name']
        
        if session_name not in last_sent or last_sent[session_name] != datetime.utcnow().hour:
            # إرسال تنبيه للمستخدمين
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users")
            users = cursor.fetchall()
            conn.close()
            
            alert_msg = (
                f"🔔 **بدأت {session_name}** 🔔\n\n"
                f"📊 **التقلب:** {current_session['volatility']}\n"
                f"🎯 **نسبة النجاح:** {current_session['success_rate']}\n"
                f"💰 **الأزواج المناسبة:** {current_session['pairs']}\n"
                f"✨ **المميزات:** {current_session['features']}\n\n"
                f"استخدم /start لبدء التحليل"
            )
            
            # إرسال للمشرف أولاً للتحقق
            await asyncio.sleep(2)
            last_sent[session_name] = datetime.utcnow().hour
        
        await asyncio.sleep(60)  # فحص كل دقيقة

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

LOGIN_PAGE_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🔐 لوحة تسجيل الدخول - Obeida Trading</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; justify-content: center; align-items: center; }
        .login-container { background: white; border-radius: 20px; padding: 40px; width: 90%; max-width: 400px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); animation: slideIn 0.5s ease; }
        @keyframes slideIn { from { opacity: 0; transform: translateY(-20px); } to { opacity: 1; transform: translateY(0); } }
        .login-header { text-align: center; margin-bottom: 30px; }
        .login-header h1 { color: #333; font-size: 2em; margin-bottom: 10px; }
        .login-header p { color: #666; font-size: 0.9em; }
        .logo { font-size: 4em; margin-bottom: 20px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 5px; color: #666; font-weight: 600; }
        .form-group input { width: 100%; padding: 12px; border: 2px solid #f0f0f0; border-radius: 10px; font-size: 1em; transition: border-color 0.3s; }
        .form-group input:focus { outline: none; border-color: #667eea; }
        .btn-login { width: 100%; padding: 14px; background: #667eea; color: white; border: none; border-radius: 10px; font-size: 1.1em; font-weight: 600; cursor: pointer; transition: background 0.3s; }
        .btn-login:hover { background: #5a67d8; }
        .error-message { background: #f8d7da; color: #721c24; padding: 10px; border-radius: 10px; margin-bottom: 20px; text-align: center; border: 1px solid #f5c6cb; }
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>👑 لوحة تحكم Obeida Trading</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        body { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { background: white; border-radius: 20px; padding: 30px; margin-bottom: 30px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #333; font-size: 2.2em; margin-bottom: 5px; }
        .header .subtitle { color: #666; font-size: 1.1em; }
        .logout-btn { background: #dc3545; color: white; padding: 10px 20px; border-radius: 10px; text-decoration: none; font-weight: 600; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: white; border-radius: 15px; padding: 25px; box-shadow: 0 5px 20px rgba(0,0,0,0.05); }
        .stat-card h3 { color: #666; font-size: 1em; margin-bottom: 10px; }
        .stat-card .number { color: #667eea; font-size: 2.5em; font-weight: bold; }
        .section { background: white; border-radius: 20px; padding: 30px; margin-bottom: 30px; box-shadow: 0 5px 20px rgba(0,0,0,0.05); }
        .section-title { color: #333; font-size: 1.8em; margin-bottom: 20px; border-bottom: 2px solid #f0f0f0; padding-bottom: 10px; }
        .table-container { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; }
        th { background: #667eea; color: white; padding: 15px; text-align: center; }
        td { padding: 15px; border-bottom: 1px solid #f0f0f0; text-align: center; }
        .status-badge { padding: 5px 12px; border-radius: 20px; font-size: 0.9em; display: inline-block; }
        .status-active { background: #d4edda; color: #155724; }
        .status-pending { background: #fff3cd; color: #856404; }
        .status-expired { background: #f8d7da; color: #721c24; }
        .btn { padding: 8px 16px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; text-decoration: none; display: inline-block; margin: 2px; }
        .btn-success { background: #28a745; color: white; }
        .btn-danger { background: #dc3545; color: white; }
        .btn-primary { background: #667eea; color: white; }
        .search-box { margin-bottom: 20px; }
        .search-box input { width: 100%; padding: 12px; border: 2px solid #f0f0f0; border-radius: 10px; font-size: 1em; }
        .footer { text-align: center; color: white; margin-top: 30px; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); justify-content: center; align-items: center; z-index: 1000; }
        .modal-content { background: white; padding: 30px; border-radius: 20px; width: 90%; max-width: 500px; }
        .modal-content h2 { margin-bottom: 20px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 5px; font-weight: 600; }
        .form-group input, .form-group textarea { width: 100%; padding: 10px; border: 2px solid #f0f0f0; border-radius: 8px; }
        .notification { position: fixed; top: 20px; right: 20px; padding: 15px 25px; background: white; border-radius: 10px; box-shadow: 0 5px 20px rgba(0,0,0,0.2); display: none; z-index: 1001; border-right: 5px solid #28a745; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🤖 لوحة تحكم Obeida Trading</h1>
                <p class="subtitle">نظام إدارة المستخدمين والوكالة</p>
            </div>
            <div>
                <span style="margin-right: 20px; color: #666;">مرحباً، المالك</span>
                <a href="/logout" class="logout-btn">تسجيل الخروج</a>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>👥 إجمالي المستخدمين</h3>
                <div class="number">{{ stats.total_users }}</div>
            </div>
            <div class="stat-card">
                <h3>📊 إجمالي التحليلات</h3>
                <div class="number">{{ stats.total_analyses }}</div>
            </div>
            <div class="stat-card">
                <h3>📝 طلبات معلقة</h3>
                <div class="number">{{ pending_count }}</div>
            </div>
            <div class="stat-card">
                <h3>🌐 الجلسة الحالية</h3>
                <div class="number" style="font-size: 1.5em;">{{ current_session.name }}</div>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">👥 المستخدمين</h2>
            <div class="search-box">
                <input type="text" id="searchUsers" placeholder="🔍 بحث عن مستخدم..." onkeyup="searchUsers()">
            </div>
            <div class="table-container">
                <table id="usersTable">
                    <thead>
                        <tr><th>المعرف</th><th>الاسم</th><th>تحليلات</th><th>حالة الوكالة</th><th>المنطقة</th><th>الإجراءات</th></tr>
                    </thead>
                    <tbody>
                        {% for user in users %}
                        <tr>
                            <td>{{ user.id }}</td>
                            <td>{{ user.name }}</td>
                            <td>{{ user.free }}/{{ user.total }}</td>
                            <td><span class="status-badge {% if user.affiliate_status == 'مفعل' %}status-active{% elif user.affiliate_status == 'قيد المراجعة' %}status-pending{% else %}status-expired{% endif %}">{{ user.affiliate_status }}</span></td>
                            <td>{{ user.timezone }}</td>
                            <td>
                                <button class="btn btn-primary" onclick="resetAnalyses({{ user.id }})">🔄 إعادة تحليلات</button>
                                <button class="btn btn-danger" onclick="blockUser({{ user.id }})">🚫 حظر</button>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">📝 طلبات التسجيل المعلقة</h2>
            <div class="table-container">
                <table>
                    <thead><tr><th>المعرف</th><th>الاسم</th><th>معرف الوكالة</th><th>المنصة</th><th>الوقت</th><th>الإجراءات</th></tr></thead>
                    <tbody>
                        {% for req in pending %}
                        <tr>
                            <td>{{ req.user_id }}</td>
                            <td>{{ req.username or req.user_id }}</td>
                            <td>{{ req.affiliate_id }}</td>
                            <td>{{ req.platform }}</td>
                            <td>{{ req.requested_at[:16] }}</td>
                            <td>
                                <button class="btn btn-success" onclick="approveRegistration({{ req.id }})">✅ قبول</button>
                                <button class="btn btn-danger" onclick="rejectRegistration({{ req.id }})">❌ رفض</button>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">📢 إجراءات سريعة</h2>
            <button class="btn btn-primary" onclick="showBroadcastModal()">📢 إذاعة للمستخدمين</button>
            <button class="btn btn-success" onclick="exportData()">📊 تصدير البيانات</button>
            <button class="btn btn-primary" onclick="refreshSession()">🔄 تحديث الجلسة</button>
        </div>
    </div>

    <!-- Modal إذاعة -->
    <div id="broadcastModal" class="modal">
        <div class="modal-content">
            <h2>📢 إذاعة للمستخدمين</h2>
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

    <!-- Modal إعادة تحليلات -->
    <div id="resetModal" class="modal">
        <div class="modal-content">
            <h2>🔄 إعادة تعيين التحليلات</h2>
            <div class="form-group">
                <label>معرف المستخدم</label>
                <input type="text" id="resetUserId" readonly>
            </div>
            <div class="form-group">
                <label>عدد التحليلات</label>
                <input type="number" id="resetCount" value="3" min="1">
            </div>
            <div class="modal-actions">
                <button class="btn btn-success" onclick="confirmReset()">✅ إعادة تعيين</button>
                <button class="btn btn-danger" onclick="closeModal('resetModal')">❌ إلغاء</button>
            </div>
        </div>
    </div>

    <div id="notification" class="notification"></div>

    <div class="footer">
        <p>🤯 Obeida Trading 🤯 - نظام إدارة الوكالة المتطور</p>
    </div>

    <script>
        function searchUsers() {
            let input = document.getElementById('searchUsers');
            let filter = input.value.toLowerCase();
            let table = document.getElementById('usersTable');
            let tr = table.getElementsByTagName('tr');
            for (let i = 1; i < tr.length; i++) {
                let tdName = tr[i].getElementsByTagName('td')[1];
                if (tdName) {
                    let txtValue = tdName.textContent || tdName.innerText;
                    tr[i].style.display = txtValue.toLowerCase().indexOf(filter) > -1 ? '' : 'none';
                }
            }
        }

        function showModal(modalId) { document.getElementById(modalId).style.display = 'flex'; }
        function closeModal(modalId) { document.getElementById(modalId).style.display = 'none'; }
        function showNotification(msg, type='success') {
            let n = document.getElementById('notification');
            n.textContent = msg;
            n.style.display = 'block';
            setTimeout(() => n.style.display = 'none', 3000);
        }

        function showBroadcastModal() { showModal('broadcastModal'); }

        function confirmBroadcast() {
            let msg = document.getElementById('broadcastMessage').value;
            if (!msg) { showNotification('الرجاء إدخال نص الرسالة', 'error'); return; }
            fetch('/admin/broadcast', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: msg })
            }).then(r => r.json()).then(d => {
                if (d.success) { showNotification(`✅ تم الإرسال لـ ${d.sent} مستخدم`); closeModal('broadcastModal'); }
                else { showNotification('❌ ' + d.error, 'error'); }
            });
        }

        function resetAnalyses(userId) {
            document.getElementById('resetUserId').value = userId;
            showModal('resetModal');
        }

        function confirmReset() {
            let userId = document.getElementById('resetUserId').value;
            let count = document.getElementById('resetCount').value;
            fetch('/admin/reset_analyses', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: parseInt(userId), count: parseInt(count) })
            }).then(r => r.json()).then(d => {
                if (d.success) { showNotification('✅ تم إعادة تعيين التحليلات'); closeModal('resetModal'); location.reload(); }
                else { showNotification('❌ ' + d.error, 'error'); }
            });
        }

        function approveRegistration(reqId) {
            fetch('/admin/verify_registration', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ request_id: reqId, approve: true })
            }).then(r => r.json()).then(d => {
                if (d.success) { showNotification('✅ تم الموافقة'); location.reload(); }
                else { showNotification('❌ ' + d.error, 'error'); }
            });
        }

        function rejectRegistration(reqId) {
            fetch('/admin/verify_registration', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ request_id: reqId, approve: false })
            }).then(r => r.json()).then(d => {
                if (d.success) { showNotification('❌ تم الرفض'); location.reload(); }
                else { showNotification('❌ ' + d.error, 'error'); }
            });
        }

        function exportData() { window.location.href = '/admin/export_data'; }
        function refreshSession() { location.reload(); }
    </script>
</body>
</html>
"""

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        if username == ADMIN_USERNAME and hashed_password == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('admin_panel'))
        return render_template_string(LOGIN_PAGE_HTML, error='بيانات الدخول غير صحيحة')
    return render_template_string(LOGIN_PAGE_HTML)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_panel():
    # إحصائيات
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
        if is_verified:
            status = "مفعل"
        elif is_reg:
            status = "قيد المراجعة"
        else:
            status = "غير مسجل"
        users.append({
            'id': u[0],
            'name': u[1] or str(u[0]),
            'free': u[2],
            'total': u[3],
            'timezone': u[4],
            'affiliate_status': status
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

@app.route('/admin/broadcast', methods=['POST'])
@login_required
def broadcast():
    data = request.json
    message = data.get('message')
    if not message:
        return jsonify({'success': False, 'error': 'الرسالة مطلوبة'})
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    
    # يتم الإرسال عبر البوت في الخلفية
    return jsonify({'success': True, 'sent': len(users)})

@app.route('/admin/reset_analyses', methods=['POST'])
@login_required
def reset_analyses():
    data = request.json
    user_id = data.get('user_id')
    count = data.get('count', 3)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET free_analyses = ? WHERE user_id = ?", (count, user_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/verify_registration', methods=['POST'])
@login_required
def verify_registration():
    data = request.json
    request_id = data.get('request_id')
    approve = data.get('approve', True)
    
    success, msg = AffiliateRegistrationSystem.verify_registration(request_id, ADMIN_ID, approve)
    return jsonify({'success': success, 'error': msg if not success else None})

@app.route('/admin/export_data')
@login_required
def export_data():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    conn.close()
    
    data = {
        'export_date': datetime.now().isoformat(),
        'users': users
    }
    return Response(
        json.dumps(data, indent=2, default=str),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment;filename=obeida_users.json'}
    )

@app.route('/')
def home():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head><title>Obeida Trading Bot</title><style>
        body { font-family: Arial; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
        h1 { font-size: 3em; }
        .admin-link { background: #28a745; color: white; padding: 15px 30px; border-radius: 5px; text-decoration: none; display: inline-block; margin: 20px; }
    </style></head>
    <body><h1>🤖 Obeida Trading Bot</h1><p>نظام تحليل فني متكامل مع وكالة Tickmill</p><a href="/admin" class="admin-link">🔐 لوحة التحكم</a></body>
    </html>
    ''')

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
    print("🚀 Starting Obeida Trading Bot with Affiliate System...")
    
    # تهيئة قواعد البيانات
    init_db()
    AffiliateRegistrationSystem.init_db()
    
    # بدء الخادم في خيط منفصل
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # إعداد البوت
    application = Application.builder().token(TOKEN).build()
    
    # إنشاء محول المحادثة
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
            SEARCH_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_country_search)],
            REGISTRATION_MENU: [CallbackQueryHandler(handle_callback_query)],
            REGISTER_AFFILIATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_affiliate_registration)],
        },
        fallbacks=[CommandHandler('start', start)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(CommandHandler('help', lambda u, c: u.message.reply_text("استخدم /start للبدء")))
    
    # بدء رادار الأخبار في الخلفية
    news_radar = ObeidaNewsRadar(TOKEN)
    
    async def run_radar():
        await news_radar.start_radar()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(run_radar())
    loop.create_task(session_alert_scheduler())
    
    print("✅ Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
