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
from typing import Set, Dict, List, Optional, Tuple
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
AGENCY_DB_NAME = "agency_system.db"
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

MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, ANALYZE_MODE, ADMIN_MENU, AGENCY_MENU, ADD_AGENCY, SELECT_AGENCY, REGISTER_AGENCY, SET_COUNTRY, SESSION_ALERT, SHOP_MENU, VIEW_ORDERS = range(13)

for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

# ============================================
# نظام المناطق الزمنية المتقدم
# ============================================

TIMEZONES_BY_GMT = {
    "GMT-12:00": {"name": "خط التاريخ الدولي", "countries": ["جزر بيكر", "جزيرة هاولاند"]},
    "GMT-11:00": {"name": "ساموا", "countries": ["ساموا", "ساموا الأمريكية", "نييوي"]},
    "GMT-10:00": {"name": "هاواي", "countries": ["هاواي (الولايات المتحدة)", "جزر كوك", "تاهيتي"]},
    "GMT-09:00": {"name": "ألاسكا", "countries": ["ألاسكا (الولايات المتحدة)"]},
    "GMT-08:00": {"name": "المحيط الهادئ", "countries": ["لوس أنجلوس", "فانكوفر", "تيخوانا"]},
    "GMT-07:00": {"name": "المنطقة الجبلية", "countries": ["دنفر", "إدمونتون", "فينيكس"]},
    "GMT-06:00": {"name": "المنطقة الوسطى", "countries": ["شيكاغو", "مدينة مكسيكو", "وينيبيغ", "كوستاريكا"]},
    "GMT-05:00": {"name": "المنطقة الشرقية", "countries": ["نيويورك", "تورونتو", "ليما", "بوغوتا", "بوسطن"]},
    "GMT-04:00": {"name": "الأطلسي", "countries": ["سانتياغو", "أسونسيون", "هاليفاكس", "كاراكاس"]},
    "GMT-03:00": {"name": "الأرجنتين", "countries": ["بوينس آيرس", "ساو باولو", "مونتيفيديو", "جرينلاند"]},
    "GMT-02:00": {"name": "المحيط الأطلسي الأوسط", "countries": ["جزر فوكلاند", "ساندويتش الجنوبية"]},
    "GMT-01:00": {"name": "الأزور", "countries": ["جزر الأزور", "الرأس الأخضر"]},
    "GMT+00:00": {"name": "توقيت غرينتش", "countries": ["لندن", "دبلن", "لشبونة", "الدار البيضاء", "نواكشوط", "أكرا", "دكار"]},
    "GMT+01:00": {"name": "أوروبا الوسطى", "countries": ["برلين", "باريس", "مدريد", "روما", "فيينا", "بروكسل", "أمستردام", "وارسو", "براغ", "الجزائر", "تونس", "طرابلس"]},
    "GMT+02:00": {"name": "أوروبا الشرقية", "countries": ["القاهرة", "الرياض", "القدس", "عمّان", "بيروت", "دمشق", "بغداد", "الكويت", "الدوحة", "المنامة", "أثينا", "اسطنبول", "هلسنكي", "صوفيا", "بوخارست", "كييف"]},
    "GMT+03:00": {"name": "العربية/موسكو", "countries": ["موسكو", "جدة", "أبوظبي", "الدوحة", "البحرين", "الكويت", "بغداد", "صنعاء", "مسقط", "أنقرة", "دمشق", "عَمَّان", "بيروت", "القدس", "نيروبي", "دار السلام", "أديس أبابا", "مقديشو"]},
    "GMT+03:30": {"name": "إيران", "countries": ["طهران"]},
    "GMT+04:00": {"name": "الخليج", "countries": ["دبي", "مسقط", "باكو", "تبليسي", "يريفان", "موريشيوس", "سيشل"]},
    "GMT+04:30": {"name": "أفغانستان", "countries": ["كابول"]},
    "GMT+05:00": {"name": "باكستان", "countries": ["كراتشي", "طشقند", "عشق آباد", "دوشنبه", "يكاترينبورغ"]},
    "GMT+05:30": {"name": "الهند", "countries": ["دلهي", "مومباي", "كولمبو"]},
    "GMT+05:45": {"name": "نيبال", "countries": ["كاتماندو"]},
    "GMT+06:00": {"name": "بنغلاديش", "countries": ["دكا", "ألماتي", "بيشكك", "أومسك"]},
    "GMT+06:30": {"name": "ميانمار", "countries": ["يانغون"]},
    "GMT+07:00": {"name": "تايلاند", "countries": ["بانكوك", "جاكرتا", "هانوي", "نوفوسيبيرسك"]},
    "GMT+08:00": {"name": "الصين", "countries": ["بكين", "سنغافورة", "كوالالمبور", "تايبيه", "مانيلا", "بيرث", "كراسنويارسك"]},
    "GMT+09:00": {"name": "اليابان", "countries": ["طوكيو", "سول", "بيونغيانغ", "ياكوتسك"]},
    "GMT+09:30": {"name": "أستراليا الوسطى", "countries": ["أديلايد", "داروين"]},
    "GMT+10:00": {"name": "أستراليا الشرقية", "countries": ["سيدني", "ملبورن", "بريسبان", "فلاديفوستوك"]},
    "GMT+11:00": {"name": "جزر سليمان", "countries": ["هونيارا", "نوميا", "بورت فيلا"]},
    "GMT+12:00": {"name": "نيوزيلندا", "countries": ["ويلينغتون", "أوكلاند", "فيجي"]},
    "GMT+13:00": {"name": "تونغا", "countries": ["نوكو ألوفا", "أبيان"]},
    "GMT+14:00": {"name": "كيريباتي", "countries": ["كيريتيماتي"]}
}

COUNTRY_TIMEZONE_MAP = {
    "فلسطين": "GMT+02:00", "مصر": "GMT+02:00", "السعودية": "GMT+03:00", "الإمارات": "GMT+04:00",
    "الكويت": "GMT+03:00", "قطر": "GMT+03:00", "البحرين": "GMT+03:00", "عمان": "GMT+04:00",
    "اليمن": "GMT+03:00", "العراق": "GMT+03:00", "الأردن": "GMT+02:00", "لبنان": "GMT+02:00",
    "سوريا": "GMT+02:00", "ليبيا": "GMT+02:00", "تونس": "GMT+01:00", "الجزائر": "GMT+01:00",
    "المغرب": "GMT+00:00", "موريتانيا": "GMT+00:00", "السودان": "GMT+02:00", "الصومال": "GMT+03:00",
    "تركيا": "GMT+03:00", "إيران": "GMT+03:30", "باكستان": "GMT+05:00", "الهند": "GMT+05:30",
    "الصين": "GMT+08:00", "اليابان": "GMT+09:00", "كوريا الجنوبية": "GMT+09:00", "بريطانيا": "GMT+00:00",
    "ألمانيا": "GMT+01:00", "فرنسا": "GMT+01:00", "إيطاليا": "GMT+01:00", "إسبانيا": "GMT+01:00",
    "روسيا": "GMT+03:00", "الولايات المتحدة": "GMT-05:00", "كندا": "GMT-05:00", "البرازيل": "GMT-03:00",
    "الأرجنتين": "GMT-03:00", "تشيلي": "GMT-04:00", "كولومبيا": "GMT-05:00", "المكسيك": "GMT-06:00",
    "جنوب أفريقيا": "GMT+02:00", "كينيا": "GMT+03:00", "نيجيريا": "GMT+01:00"
}

# ============================================
# جلسات التداول (تلقائية)
# ============================================

TRADING_SESSIONS = {
    'asian': {
        'name': '🌏 الجلسة الآسيوية',
        'start': 0, 'end': 8,
        'volatility': 'منخفضة',
        'pairs': 'الين الياباني، الدولار الأسترالي، الدولار النيوزيلندي',
        'success_rate': '65-70%',
        'features': 'حركات هادئة، اختراقات حقيقية قليلة',
        'icon': '🌏', 'time_range': '00:00-08:00 UTC'
    },
    'london': {
        'name': '🇬🇧 جلسة لندن',
        'start': 8, 'end': 16,
        'volatility': 'مرتفعة',
        'pairs': 'اليورو، الجنيه، الفرنك',
        'success_rate': '75-85%',
        'features': 'سيولة عالية، فرص انعكاس ممتازة',
        'icon': '🇬🇧', 'time_range': '08:00-16:00 UTC'
    },
    'newyork': {
        'name': '🗽 جلسة نيويورك',
        'start': 13, 'end': 21,
        'volatility': 'عالية جداً',
        'pairs': 'الدولار، المؤشرات الأمريكية',
        'success_rate': '80-90%',
        'features': 'ذروة السيولة، تحركات عنيفة',
        'icon': '🗽', 'time_range': '13:00-21:00 UTC'
    },
    'pacific': {
        'name': '🌊 جلسة المحيط الهادئ',
        'start': 21, 'end': 24,
        'volatility': 'منخفضة جداً',
        'pairs': 'جميع الأزواج',
        'success_rate': '55-65%',
        'features': 'سيولة منخفضة، حركات جانبية',
        'icon': '🌊', 'time_range': '21:00-00:00 UTC'
    }
}

# ============================================
# نظام الوكالات والتسجيل
# ============================================

class AgencySystem:
    @staticmethod
    def init_db():
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agencies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                platform TEXT NOT NULL,
                terms TEXT NOT NULL,
                register_link TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER,
                is_active BOOLEAN DEFAULT 1
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                agency_id INTEGER NOT NULL,
                platform_id TEXT,
                verified BOOLEAN DEFAULT 0,
                verified_by INTEGER,
                verified_at TIMESTAMP,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (agency_id) REFERENCES agencies(id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_country (
                user_id INTEGER PRIMARY KEY,
                country TEXT NOT NULL,
                timezone TEXT NOT NULL,
                set_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_session_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shop_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                agency_id INTEGER NOT NULL,
                subscription_type TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (agency_id) REFERENCES agencies(id)
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ تم تهيئة نظام الوكالات")

    @staticmethod
    def add_agency(name, platform, terms, register_link, created_by):
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO agencies (name, platform, terms, register_link, created_by)
                VALUES (?, ?, ?, ?, ?)
            ''', (name, platform, terms, register_link, created_by))
            conn.commit()
            return True, cursor.lastrowid
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def get_agencies():
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT id, name, platform, terms, register_link FROM agencies WHERE is_active = 1 ORDER BY id DESC')
        agencies = cursor.fetchall()
        conn.close()
        return agencies
    
    @staticmethod
    def register_user_agency(user_id, agency_id, platform_id=None):
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO user_registrations (user_id, agency_id, platform_id)
                VALUES (?, ?, ?)
            ''', (user_id, agency_id, platform_id))
            conn.commit()
            return True, "تم التسجيل في الوكالة بنجاح"
        except sqlite3.IntegrityError:
            return False, "أنت مسجل بالفعل في هذه الوكالة"
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def verify_user_registration(user_id, agency_id, verified_by):
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                UPDATE user_registrations 
                SET verified = 1, verified_by = ?, verified_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND agency_id = ?
            ''', (verified_by, user_id, agency_id))
            conn.commit()
            return True, "تم توثيق التسجيل"
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()
    
    @staticmethod
    def is_user_registered(user_id):
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM user_registrations 
            WHERE user_id = ? AND verified = 1
        ''', (user_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    
    @staticmethod
    def get_user_registrations(user_id):
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.id, a.name, a.platform, r.verified, r.registered_at
            FROM user_registrations r
            JOIN agencies a ON r.agency_id = a.id
            WHERE r.user_id = ?
        ''', (user_id,))
        registrations = cursor.fetchall()
        conn.close()
        return registrations
    
    @staticmethod
    def get_unverified_registrations():
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.id, r.user_id, r.platform_id, a.name, r.registered_at
            FROM user_registrations r
            JOIN agencies a ON r.agency_id = a.id
            WHERE r.verified = 0
            ORDER BY r.registered_at DESC
        ''')
        registrations = cursor.fetchall()
        conn.close()
        return registrations
    
    @staticmethod
    def set_user_country(user_id, country):
        timezone = COUNTRY_TIMEZONE_MAP.get(country, "GMT+02:00")
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO user_country (user_id, country, timezone)
            VALUES (?, ?, ?)
        ''', (user_id, country, timezone))
        conn.commit()
        conn.close()
        return timezone
    
    @staticmethod
    def get_user_country(user_id):
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('SELECT country, timezone FROM user_country WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result if result else (None, "GMT+02:00")
    
    @staticmethod
    def add_session_alert(user_id, session_name):
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO user_session_alerts (user_id, session_name)
                VALUES (?, ?)
            ''', (user_id, session_name))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    
    @staticmethod
    def get_session_alerts(session_name):
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id FROM user_session_alerts WHERE session_name = ?
        ''', (session_name,))
        users = cursor.fetchall()
        conn.close()
        return [u[0] for u in users]
    
    @staticmethod
    def add_shop_order(user_id, agency_id, subscription_type):
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO shop_orders (user_id, agency_id, subscription_type)
            VALUES (?, ?, ?)
        ''', (user_id, agency_id, subscription_type))
        conn.commit()
        order_id = cursor.lastrowid
        conn.close()
        return order_id
    
    @staticmethod
    def get_pending_orders():
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT o.id, o.user_id, o.subscription_type, a.name, o.created_at
            FROM shop_orders o
            JOIN agencies a ON o.agency_id = a.id
            WHERE o.status = 'pending'
            ORDER BY o.created_at DESC
        ''')
        orders = cursor.fetchall()
        conn.close()
        return orders

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
                currency_tag TEXT
            )
        ''')
        conn.commit()
        conn.close()

    def load_processed_news(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT guid FROM sent_news ORDER BY id DESC LIMIT 500")
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

    def send_telegram_message(self, chat_id: str, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
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
                    for news in feed.entries[:8]:
                        news_id = news.get('id', news.get('guid', news.title.strip()))
                        if news_id not in self.processed_news and news_id not in all_news:
                            all_news[news_id] = news
                except:
                    continue
            
            for news_id, news in all_news.items():
                currency_tag = self.get_currency_tag(news.title)
                message_text = (
                    f"🚨 <b>خبر اقتصادي جديد</b>\n\n"
                    f"{news.title}\n\n"
                    f"💰 <b>العملات المتأثرة:</b> {currency_tag}\n\n"
                    f"━━━━━━━━━━━━━\n"
                    f"⚡ <b>القناة:</b> @ObeidaTrading\n"
                    f"💬 <b>المناقشة:</b> @ObeidaTradingchat"
                )
                self.send_telegram_message(self.chat_group_id, message_text)
                self.processed_news.add(news_id)
                
                conn = sqlite3.connect(self.db_name)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO sent_news (guid, title, link, published, currency_tag)
                    VALUES (?, ?, ?, ?, ?)
                ''', (news_id, news.title[:200], news.link, news.get('published', ''), currency_tag))
                conn.commit()
                conn.close()
                
                await asyncio.sleep(1)
                
        except Exception as e:
            print(f"خطأ في الرادار: {e}")

    async def start_radar(self):
        print("📡 رادار الأخبار يعمل...")
        while True:
            try:
                await self.process_news()
            except Exception as e:
                print(f"خطأ: {e}")
            await asyncio.sleep(20)

# ============================================
# دوال مساعدة
# ============================================

def init_all_dbs():
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
            total_analyses INTEGER DEFAULT 0
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
    
    conn.commit()
    conn.close()
    
    AgencySystem.init_db()
    print("✅ تم تهيئة جميع قواعد البيانات")

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

def get_user_timezone(user_id):
    country, timezone = AgencySystem.get_user_country(user_id)
    if timezone:
        return timezone
    return "GMT+02:00"

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
        return datetime.utcnow()

def get_user_local_session(user_id):
    try:
        country, tz_str = AgencySystem.get_user_country(user_id)
        if tz_str.startswith('GMT'):
            offset_str = tz_str.replace('GMT', '')
            if offset_str and offset_str[0] in ('+', '-'):
                hours = int(offset_str.split(':')[0])
                minutes = int(offset_str.split(':')[1]) if ':' in offset_str else 0
                timezone = pytz.FixedOffset(hours * 60 + (minutes if hours >= 0 else -minutes))
                local_hour = datetime.now(timezone).hour
                
                for session in TRADING_SESSIONS.values():
                    if session['start'] <= local_hour < session['end'] or (session['end'] == 24 and local_hour >= session['start']):
                        return session
    except:
        pass
    return get_current_session()

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
    except Exception as e:
        print(f"خطأ في ضغط الصورة: {e}")
        return image_path

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
    cursor.execute("SELECT candle, trade_time, last_analysis_context FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        return res
    return ("M1", "قصير (1m-15m)", "")

def increment_analyses(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET total_analyses = total_analyses + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def format_trade_time_for_prompt(trade_time):
    if trade_time == "قصير (1m-15m)":
        return "مدة الصفقة المتوقعة: قصير الأجل (1 دقيقة إلى 15 دقيقة)"
    elif trade_time == "متوسط (4h-Daily)":
        return "مدة الصفقة المتوقعة: متوسط الأجل (4 ساعات إلى يومي)"
    elif trade_time == "طويل (Weekly-Monthly)":
        return "مدة الصفقة المتوقعة: طويل الأجل (أسبوعي إلى شهري)"
    return f"مدة الصفقة المتوقعة: {trade_time}"

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

def cleanup_user_data(context: ContextTypes.DEFAULT_TYPE, user_id: int = None):
    try:
        if user_id and os.path.exists(IMAGE_CACHE_DIR):
            for filename in os.listdir(IMAGE_CACHE_DIR):
                if f"_{user_id}_" in filename:
                    filepath = os.path.join(IMAGE_CACHE_DIR, filename)
                    if os.path.exists(filepath):
                        try:
                            os.remove(filepath)
                        except:
                            pass
        
        keys_to_remove = ['temp_image_path', 'compressed_image_path']
        for key in keys_to_remove:
            if key in context.user_data:
                del context.user_data[key]
    except:
        pass

# ============================================
# دوال معالجة البوت
# ============================================

async def check_agency_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        return True
    
    if AgencySystem.is_user_registered(user_id):
        return True
    
    keyboard = [
        ["🏢 التسجيل في الوكالة"],
        ["🛒 المتجر", "📞 تواصل مع الدعم"]
    ]
    
    await update.message.reply_text(
        "⚠️ **مطلوب التسجيل في إحدى وكالاتنا** ⚠️\n\n"
        "للحصول على خدمات التحليل الفني، يجب عليك التسجيل في وكالة من وكالاتنا المعتمدة.\n\n"
        "🔑 **مميزات التسجيل:**\n"
        "• تحليلات فنية غير محدودة\n"
        "• تحديثات فورية للصفقات\n"
        "• دعم فني متواصل\n\n"
        "👇 اضغط على الزر أدناه للتسجيل:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return False

async def check_country_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    country, _ = AgencySystem.get_user_country(user_id)
    
    if country:
        return True
    
    countries_list = list(COUNTRY_TIMEZONE_MAP.keys())
    keyboard = [countries_list[i:i+2] for i in range(0, min(20, len(countries_list)), 2)]
    keyboard.append(["🔄 تحديث", "❌ إلغاء"])
    
    await update.message.reply_text(
        "🌍 **مرحباً بك في Obeida Trading** 🌍\n\n"
        "لإكمال التسجيل، يرجى اختيار بلدك من القائمة:\n\n"
        "سيتم ضبط المنطقة الزمنية تلقائياً حسب بلدك.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return False

# ============================================
# دوال معالجة البوت (المعدلة)
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    cleanup_user_data(context, user_id)
    
    # التحقق من وجود بلد مسجل للمستخدم
    country, _ = AgencySystem.get_user_country(user_id)
    
    # إذا لم يكن هناك بلد مسجل، اطلب اختيار البلد أولاً
    if not country:
        countries_list = list(COUNTRY_TIMEZONE_MAP.keys())
        # ترتيب الدول العربية أولاً
        arab_countries = ["مصر", "السعودية", "الإمارات", "الكويت", "قطر", "البحرين", "عمان", "اليمن", 
                         "العراق", "الأردن", "لبنان", "سوريا", "فلسطين", "ليبيا", "تونس", "الجزائر", 
                         "المغرب", "موريتانيا", "السودان", "الصومال"]
        other_countries = [c for c in countries_list if c not in arab_countries]
        sorted_countries = arab_countries + other_countries
        
        # تقسيم الدول إلى أزرار (3 أعمدة)
        keyboard = [sorted_countries[i:i+2] for i in range(0, min(30, len(sorted_countries)), 2)]
        keyboard.append(["🔄 تحديث", "❌ إلغاء"])
        
        await update.message.reply_text(
            "🌍 **مرحباً بك في Obeida Trading** 🌍\n\n"
            f"👤 **مرحباً:** {user.first_name}\n\n"
            "📌 **لإكمال التسجيل، يرجى اختيار بلدك من القائمة:**\n\n"
            "سيتم ضبط المنطقة الزمنية والجلسات تلقائياً حسب بلدك.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SET_COUNTRY
    
    # إذا كان البلد موجوداً، انتقل للقائمة الرئيسية
    return await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض القائمة الرئيسية بعد تسجيل البلد"""
    user = update.effective_user
    user_id = user.id
    
    country, _ = AgencySystem.get_user_country(user_id)
    
    # التحقق من التسجيل في وكالة
    if not AgencySystem.is_user_registered(user_id) and user_id != ADMIN_ID:
        return await check_agency_required(update, context)
    
    current_session = get_user_local_session(user_id)
    local_time = get_localized_time(user_id)
    
    welcome_msg = (
        "🚀 **أهلاً بك في Obeida Trading** 🚀\n\n"
        f"👤 **مرحباً:** {user.first_name}\n"
        f"🌍 **بلدك:** {country}\n"
        f"🕒 **وقتك المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"🌐 **الجلسة الحالية:** {current_session['name']}\n"
        f"📊 **نسبة النجاح:** {current_session['success_rate']}\n\n"
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
        ["🏢 وكالاتي", "🔔 تنبيه الجلسات"],
        ["🛒 المتجر", "🌍 تغيير البلد"]
    ]
    
    if user_id == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    
    await update.message.reply_text(
        welcome_msg,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def handle_set_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار البلد"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "❌ إلغاء":
        # إلغاء - إظهار رسالة ترحيب بسيطة
        await update.message.reply_text(
            "👋 **تم الإلغاء**\n\n"
            "يمكنك إعادة المحاولة بكتابة /start",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    if user_message == "🔄 تحديث":
        # تحديث القائمة
        countries_list = list(COUNTRY_TIMEZONE_MAP.keys())
        arab_countries = ["مصر", "السعودية", "الإمارات", "الكويت", "قطر", "البحرين", "عمان", "اليمن", 
                         "العراق", "الأردن", "لبنان", "سوريا", "فلسطين", "ليبيا", "تونس", "الجزائر", 
                         "المغرب", "موريتانيا", "السودان", "الصومال"]
        other_countries = [c for c in countries_list if c not in arab_countries]
        sorted_countries = arab_countries + other_countries
        
        keyboard = [sorted_countries[i:i+2] for i in range(0, min(30, len(sorted_countries)), 2)]
        keyboard.append(["🔄 تحديث", "❌ إلغاء"])
        
        await update.message.reply_text(
            "🌍 **اختر بلدك:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return SET_COUNTRY
    
    if user_message in COUNTRY_TIMEZONE_MAP:
        # تعيين البلد والمنطقة الزمنية
        timezone = AgencySystem.set_user_country(user_id, user_message)
        
        # رسالة تأكيد
        await update.message.reply_text(
            f"✅ **تم تعيين بلدك: {user_message}**\n\n"
            f"🌍 **المنطقة الزمنية:** {timezone}\n\n"
            f"🔄 جاري تحويلك للقائمة الرئيسية...",
            parse_mode="Markdown"
        )
        
        # عرض القائمة الرئيسية
        return await show_main_menu(update, context)
    
    # رسالة خطأ
    await update.message.reply_text(
        "❌ **الرجاء اختيار بلد من القائمة**\n\n"
        "اضغط على زر 🔄 تحديث لعرض القائمة مرة أخرى.",
        parse_mode="Markdown"
    )
    return SET_COUNTRY

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "🌍 تغيير البلد":
        # عرض قائمة الدول لتغيير البلد
        countries_list = list(COUNTRY_TIMEZONE_MAP.keys())
        arab_countries = ["مصر", "السعودية", "الإمارات", "الكويت", "قطر", "البحرين", "عمان", "اليمن", 
                         "العراق", "الأردن", "لبنان", "سوريا", "فلسطين", "ليبيا", "تونس", "الجزائر", 
                         "المغرب", "موريتانيا", "السودان", "الصومال"]
        other_countries = [c for c in countries_list if c not in arab_countries]
        sorted_countries = arab_countries + other_countries
        
        keyboard = [sorted_countries[i:i+2] for i in range(0, min(30, len(sorted_countries)), 2)]
        keyboard.append(["🔄 تحديث", "🔙 العودة"])
        
        await update.message.reply_text(
            "🌍 **تغيير البلد**\n\n"
            "اختر بلدك الجديد من القائمة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SET_COUNTRY
    
    elif user_message == "🏢 التسجيل في الوكالة" or user_message == "🏢 وكالاتي":
        agencies = AgencySystem.get_agencies()
        
        if not agencies:
            if user_id == ADMIN_ID:
                keyboard = [["➕ إضافة وكالة"], ["🔙 العودة"]]
                await update.message.reply_text(
                    "📭 **لا توجد وكالات مسجلة بعد**\n\n"
                    "كمدير، يمكنك إضافة وكالة جديدة:",
                    reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                    parse_mode="Markdown"
                )
                return AGENCY_MENU
            else:
                await update.message.reply_text(
                    "📭 **لا توجد وكالات متاحة حالياً**\n\n"
                    "يرجى التواصل مع المشرف: @Sz2zv",
                    parse_mode="Markdown"
                )
                return MAIN_MENU
        
        user_regs = AgencySystem.get_user_registrations(user_id)
        registered_agencies = [r[1] for r in user_regs if r[3] == 1]
        
        if registered_agencies:
            reg_text = "✅ **وكالاتك المسجل فيها:**\n\n"
            for reg in user_regs:
                if reg[3]:
                    reg_text += f"• {reg[1]} ({reg[2]}) - موثق ✅\n"
                else:
                    reg_text += f"• {reg[1]} ({reg[2]}) - قيد المراجعة ⏳\n"
            
            reg_text += "\n📊 **يمكنك الآن استخدام التحليل الفني.**"
            await update.message.reply_text(reg_text, parse_mode="Markdown")
            return MAIN_MENU
        
        keyboard = []
        for agency in agencies:
            keyboard.append([f"📝 {agency[1]} - {agency[2]}"])
        keyboard.append(["🔙 العودة"])
        
        await update.message.reply_text(
            "🏢 **اختر الوكالة للتسجيل:**\n\n"
            "يرجى قراءة الشروط ثم التسجيل:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SELECT_AGENCY
    
    elif user_message == "🔔 تنبيه الجلسات":
        keyboard = [
            ["🌏 الجلسة الآسيوية", "🇬🇧 جلسة لندن"],
            ["🗽 جلسة نيويورك", "🌊 جلسة المحيط الهادئ"],
            ["📋 تنبيهاتي", "🔙 العودة"]
        ]
        
        await update.message.reply_text(
            "🔔 **تنبيه بدء الجلسات**\n\n"
            "اختر الجلسة التي تريد تلقي إشعار عند بدايتها:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SESSION_ALERT
    
    elif user_message == "🛒 المتجر":
        keyboard = [
            ["🕐 اشتراك ساعة - 1$", "📅 اشتراك يوم - 5$"],
            ["📆 اشتراك أسبوع - 15$", "📅 اشتراك شهر - 40$"],
            ["♾️ اشتراك سنة - 400$", "📋 طلباتي"],
            ["🔙 العودة"]
        ]
        
        await update.message.reply_text(
            "🛒 **المتجر الإلكتروني**\n\n"
            "اختر نوع الاشتراك المناسب لك:\n\n"
            "• 🕐 ساعة: 1$ - تجربة سريعة\n"
            "• 📅 يوم: 5$ - تجربة يوم كامل\n"
            "• 📆 أسبوع: 15$ - توفير 30%\n"
            "• 📅 شهر: 40$ - توفير 50%\n"
            "• ♾️ سنة: 400$ - أفضل قيمة\n\n"
            "بعد الشراء، سيتواصل معك الدعم لتوثيق العملية.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SHOP_MENU
    
    elif user_message == "👑 لوحة التحكم" and user_id == ADMIN_ID:
        return await start_admin_panel(update, context)
    
    elif user_message == "📊 معلومات الجلسة":
        current_session = get_user_local_session(user_id)
        utc_session = get_current_session()
        local_time = get_localized_time(user_id)
        country, _ = AgencySystem.get_user_country(user_id)
        
        session_info = (
            f"🌐 **معلومات الجلسة والوقت**\n\n"
            f"🌍 **بلدك:** {country}\n"
            f"🕒 **وقتك المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"📍 **جلسة UTC الحالية:** {utc_session['name']}\n"
            f"📍 **جلسة منطقتك:** {current_session['name']}\n"
            f"📊 **التقلب:** {current_session['volatility']}\n"
            f"🎯 **نسبة النجاح:** {current_session['success_rate']}\n"
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
        user_regs = AgencySystem.get_user_registrations(user_id)
        country, tz = AgencySystem.get_user_country(user_id)
        local_time = get_localized_time(user_id)
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT total_analyses FROM users WHERE user_id = ?", (user_id,))
        total_analyses = cursor.fetchone()
        total_analyses = total_analyses[0] if total_analyses else 0
        conn.close()
        
        info_msg = (
            f"👤 **معلومات المستخدم**\n\n"
            f"🆔 **المعرف:** `{user_id}`\n"
            f"👤 **اليوزر:** @{update.effective_user.username or 'لا يوجد'}\n"
            f"🌍 **البلد:** {country}\n"
            f"🕒 **المنطقة الزمنية:** {tz}\n"
            f"⏰ **الوقت المحلي:** {local_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📊 **إجمالي التحليلات:** {total_analyses}\n\n"
            f"🏢 **الوكالات المسجل بها:**\n"
        )
        
        regs = AgencySystem.get_user_registrations(user_id)
        if regs:
            for reg in regs:
                status = "✅ موثق" if reg[3] else "⏳ قيد المراجعة"
                info_msg += f"• {reg[1]} ({reg[2]}) - {status}\n"
        else:
            info_msg += "• لا توجد وكالات مسجل بها\n"
        
        await update.message.reply_text(info_msg, parse_mode="Markdown")
        return MAIN_MENU
    
    elif user_message == "⚙️ إعدادات التحليل":
        keyboard = [CANDLE_SPEEDS[i:i+3] for i in range(0, len(CANDLE_SPEEDS), 3)]
        keyboard.append(["🔙 العودة"])
        
        await update.message.reply_text(
            "⚙️ *إعدادات التحليل الفني*\n\n"
            "حدد سرعة الشموع للبدء:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SETTINGS_CANDLE
    
    elif user_message == "📊 تحليل صورة":
        candle, trade_time, _ = get_user_setting(user_id)
        
        if not candle or not trade_time:
            keyboard = [["⚙️ إعدادات التحليل"], ["🔙 العودة"]]
            await update.message.reply_text(
                "❌ *يجب ضبط الإعدادات أولاً*\n\n"
                "الرجاء ضبط سرعة الشموع ومدة الصفقة قبل التحليل.",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return MAIN_MENU
        
        await update.message.reply_text(
            f"📊 **نظام جاهز لتحليل الشارت**\n\n"
            f"⚙️ **الإعدادات:**\n"
            f"• سرعة الشموع: {candle}\n"
            f"• مدة الصفقة: {trade_time}\n\n"
            f"📤 **قم برفع صورة الشارت الآن:**",
            reply_markup=ReplyKeyboardMarkup([["🔙 العودة"]], resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return ANALYZE_MODE
    
    # إذا لم يتعرف على الأمر، عرض القائمة مرة أخرى
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🏢 وكالاتي", "🔔 تنبيه الجلسات"],
        ["🛒 المتجر", "🌍 تغيير البلد"]
    ]
    if user_id == ADMIN_ID:
        keyboard.append(["👑 لوحة التحكم"])
    
    await update.message.reply_text(
        "اختر أحد الخيارات من القائمة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    return MAIN_MENU

async def handle_settings_candle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "🔙 العودة":
        return await handle_main_menu(update, context)
    
    if user_message in CANDLE_SPEEDS:
        save_user_setting(user_id, "candle", user_message)
        
        keyboard = [TRADE_TIMES[i:i+2] for i in range(0, len(TRADE_TIMES), 2)]
        keyboard.append(["🔙 العودة"])
        
        await update.message.reply_text(
            f"✅ **تم تعيين سرعة الشموع:** {user_message}\n\n"
            f"⬇️ الآن حدد **مدة الصفقة** المتوقعة:\n\n"
            f"• **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة\n"
            f"• **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة\n"
            f"• **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SETTINGS_TIME
    
    await update.message.reply_text("❌ الرجاء اختيار سرعة شموع صحيحة.")
    return SETTINGS_CANDLE

async def handle_settings_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "🔙 العودة":
        return await handle_main_menu(update, context)
    
    if user_message in TRADE_TIMES:
        save_user_setting(user_id, "trade_time", user_message)
        
        await update.message.reply_text(
            f"✅ **تم حفظ الإعدادات بنجاح!**\n\n"
            f"📤 قم الآن برفع صورة الشارت للتحليل.",
            reply_markup=ReplyKeyboardMarkup([["📊 تحليل صورة", "🔙 العودة"]], resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    
    if user_message == "🔙 العودة":
        return await handle_main_menu(update, context)
    
    await update.message.reply_text(
        "📤 **الرجاء إرسال صورة الشارت فقط**\nأو اضغط '🔙 العودة'",
        reply_markup=ReplyKeyboardMarkup([["🔙 العودة"]], resize_keyboard=True, one_time_keyboard=False)
    )
    return ANALYZE_MODE

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not AgencySystem.is_user_registered(user_id) and user_id != ADMIN_ID:
        return await check_agency_required(update, context)
    
    candle, trade_time, _ = get_user_setting(user_id)
    
    if not candle or not trade_time:
        keyboard = [["⚙️ إعدادات التحليل"], ["🔙 العودة"]]
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**\n\n"
            "الرجاء استخدام أزرار القائمة لضبط الإعدادات.",
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
        
        await asyncio.sleep(2)
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬛⬜⬜⬜⬜ 20%]")
        
        compressed_path = compress_image(original_path)
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            return MAIN_MENU
        
        current_time = datetime.utcnow()
        current_second = current_time.second
        
        if candle.startswith('S'):
            candle_seconds = int(candle[1:]) if candle[1:].isdigit() else 5
            seconds_passed = current_second % candle_seconds
        elif candle.startswith('M'):
            candle_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            current_minute = current_time.minute
            seconds_passed = (current_minute % candle_minutes) * 60 + current_second
        else:
            seconds_passed = 0
        
        in_golden_window = 2 <= seconds_passed <= 58
        
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬛⬛⬜⬜⬜ 40%]")
        
        MAIN_PROMPT = f"""
أنت المحرك التحليلي لنظام Obeida Trading. استخرج المعطيات الرقمية بدقة من الصورة المرفقة.

المطلوب استخراجه من الصورة:
• أعلى قمة (HH): [سعر]
• أدنى قاع (LL): [سعر]
• السعر الحالي: [سعر]
• قوة الاندفاع (حجم الشمعة الحالية / متوسط حجم آخر 3 شموع): [رقم عشري]
• اتجاه الموجة الحالية: [UP/DOWN]
• مستوى 0.500 فيبوناتشي (بين القمة والقاع): [سعر]
• مستوى 0.236 فيبوناتشي: [سعر]
• مستوى 0.382 فيبوناتشي: [سعر]
• مستوى 0.618 فيبوناتشي: [سعر]
• مستوى 0.786 فيبوناتشي: [سعر]
• مستوى 1.272 فيبوناتشي (بعد الكسر): [سعر]
• مستوى 1.618 فيبوناتشي (بعد الكسر): [سعر]
• FVG (فجوة سعرية غير مملوءة): [True/False]
• مستويات FVG: [من X إلى Y]
• Order Block (كتلة أوامر مؤسسية): [True/False]
• التمركز السعري بالنسبة لمستوى 0.500: [PREMIUM/DISCOUNT/TRAP_ZONE]

ملاحظات مهمة:
- استخرج الأسعار بدقة من المحور اليميني (Y-axis)
- إذا كان السعر أعلى من 0.500 = PREMIUM
- إذا كان السعر أقل من 0.500 = DISCOUNT
- إذا كان السعر عند قمة أو قاع تاريخي = TRAP_ZONE
"""
        
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬛⬛⬛⬜⬜ 60%]")
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
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
            "max_tokens": 800,
            "temperature": 0.0,
            "top_p": 1.0
        }
        
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬛⬛⬛⬛⬜ 80%]")
        
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
        
        await wait_msg.edit_text("🤌 جاري تحليل الشارت... [⬛⬛⬛⬛⬛ 100%]")
        
        if not response or response.status_code != 200:
            await wait_msg.edit_text(f"❌ خطأ: {response.status_code if response else 'لا يوجد استجابة'}")
            return MAIN_MENU
            
        analysis_result = response.json()['choices'][0]['message']['content'].strip()
        
        # استخراج البيانات
        import re
        hh_match = re.search(r'أعلى قمة.*?:\s*([\d.]+)', analysis_result)
        ll_match = re.search(r'أدنى قاع.*?:\s*([\d.]+)', analysis_result)
        current_price_match = re.search(r'السعر الحالي.*?:\s*([\d.]+)', analysis_result)
        breakout_force_match = re.search(r'قوة الاندفاع.*?:\s*([\d.]+)', analysis_result)
        direction_match = re.search(r'اتجاه الموجة.*?:\s*([A-Z]+)', analysis_result, re.IGNORECASE)
        fvg_match = re.search(r'FVG.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        fvg_levels_match = re.search(r'مستويات FVG.*?:\s*من\s*([\d.]+)\s*إلى\s*([\d.]+)', analysis_result)
        order_block_match = re.search(r'Order Block.*?:\s*(True|False)', analysis_result, re.IGNORECASE)
        positioning_match = re.search(r'التمركز السعري.*?:\s*([A-Z_]+)', analysis_result, re.IGNORECASE)
        
        # استخراج مستويات فيبوناتشي
        fib_levels = {}
        fib_keys = ['0.236', '0.382', '0.500', '0.618', '0.786', '1.272', '1.618']
        for fib_key in fib_keys:
            fib_match = re.search(f'مستوى {fib_key}.*?:\s*([\d.]+)', analysis_result)
            if fib_match:
                fib_levels[fib_key] = float(fib_match.group(1))
        
        current_price = float(current_price_match.group(1)) if current_price_match else 0
        breakout_force = float(breakout_force_match.group(1)) if breakout_force_match else 1.0
        current_direction = direction_match.group(1).upper() if direction_match else "UP"
        has_fvg = fvg_match.group(1).upper() == "TRUE" if fvg_match else False
        has_order_block = order_block_match.group(1).upper() == "TRUE" if order_block_match else False
        positioning = positioning_match.group(1) if positioning_match else "NEUTRAL"
        
        fvg_details = ""
        if has_fvg and fvg_levels_match:
            fvg_from = float(fvg_levels_match.group(1))
            fvg_to = float(fvg_levels_match.group(2))
            fvg_details = f"[{fvg_from} إلى {fvg_to}]"
        
        fib_500 = fib_levels.get("0.500", current_price)
        
        # تحديد القرار الفني
        decision = get_simple_trading_decision(current_direction, breakout_force, has_fvg, has_order_block, positioning, current_price, fib_500)
        
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
        
        if confidence >= 85:
            success_rate = 85 + (confidence - 85) * 0.5
            success_evaluation = "ممتاز"
            confidence_icon = "💥"
        elif confidence >= 70:
            success_rate = 70 + (confidence - 70) * 0.5
            success_evaluation = "جيد"
            confidence_icon = "🔥"
        elif confidence >= 55:
            success_rate = 55 + (confidence - 55) * 0.5
            success_evaluation = "متوسط"
            confidence_icon = "⚡"
        elif confidence >= 40:
            success_rate = confidence * 0.8
            success_evaluation = "ضعيف"
            confidence_icon = "🔄"
        else:
            success_rate = confidence * 0.6
            success_evaluation = "ضعيف جداً"
            confidence_icon = "🚫"
        
        success_rate = min(95, max(40, success_rate))
        
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
            expected_duration = f"{int(candle[1:])} ثانية"
        elif candle.startswith('M'):
            expected_duration = f"{int(candle[1:])} دقيقة"
        else:
            expected_duration = "غير محدد"
        
        # بناء النتيجة
        formatted_result = f"""
📊 **التحليل الفني المتقدم:**
• الوقت على المنصة: {datetime.utcnow().strftime('%H:%M:%S')} UTC
• حالة النافذة الذهبية: {"✅ داخل النافذة - دقة عالية" if in_golden_window else "❌ خارج النافذة - دقة أقل 15%"}
• FVG (الفجوة السعرية): {fvg_details if fvg_details else 'غير موجودة'} - {"مؤكدة" if has_fvg else "غير مؤكدة"}
• Order Block (كتلة الأوامر): {"موجودة" if has_order_block else "غير موجودة"}

🎯 **الإشارة التنفيذية:**
• السعر الحالي: {current_price:.6f}
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

⚠️ **إدارة المخاطر:**
• مستوى الثقة: {confidence:.0f}% = {confidence_icon}
• نسبة نجاح الصفقة المتوقعة : {success_rate:.0f}% - {success_evaluation}
"""
        
        await wait_msg.edit_text(formatted_result, parse_mode="Markdown")
        
        increment_analyses(user_id)
        
    except asyncio.CancelledError:
        raise
    except requests.exceptions.Timeout:
        await wait_msg.edit_text("❌ انتهت مهلة الاتصال بالخادم\n\nيرجى المحاولة مرة أخرى.")
    except Exception as e:
        print(f"خطأ: {traceback.format_exc()}")
        await wait_msg.edit_text(f"❌ خطأ: {str(e)[:200]}")
    finally:
        for filepath in [original_path, compressed_path]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass
    
    return MAIN_MENU

def get_simple_trading_decision(direction, breakout_force, has_fvg, has_order_block, positioning, current_price, fib_500):
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

async def handle_select_agency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "🔙 العودة":
        return await start(update, context)
    
    if user_message.startswith("📝 "):
        agency_name = user_message.replace("📝 ", "").split(" - ")[0]
        agencies = AgencySystem.get_agencies()
        
        selected_agency = None
        for agency in agencies:
            if agency[1] == agency_name:
                selected_agency = agency
                break
        
        if selected_agency:
            context.user_data['selected_agency'] = selected_agency
            
            await update.message.reply_text(
                f"🏢 **{selected_agency[1]}**\n\n"
                f"📱 **المنصة:** {selected_agency[2]}\n\n"
                f"📋 **شروط التسجيل:**\n{selected_agency[3]}\n\n"
                f"🔗 **رابط التسجيل:**\n{selected_agency[4]}\n\n"
                f"⚠️ بعد التسجيل، أرسل معرفك في المنصة (User ID) لتوثيق حسابك:\n"
                f"مثال: `user_123456`",
                parse_mode="Markdown"
            )
            return REGISTER_AGENCY
    
    await update.message.reply_text("❌ الرجاء اختيار وكالة من القائمة.")
    return SELECT_AGENCY

async def handle_register_agency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text.strip()
    user_id = update.effective_user.id
    selected_agency = context.user_data.get('selected_agency')
    
    if not selected_agency:
        await update.message.reply_text("❌ حدث خطأ، يرجى المحاولة مرة أخرى.")
        return MAIN_MENU
    
    if user_message == "🔙 العودة":
        return await start(update, context)
    
    success, result = AgencySystem.register_user_agency(user_id, selected_agency[0], user_message)
    
    if success:
        await update.message.reply_text(
            f"✅ **تم تسجيلك في وكالة {selected_agency[1]} بنجاح!**\n\n"
            f"📱 **معرفك في المنصة:** {user_message}\n\n"
            f"⏳ سيتم مراجعة طلبك من قبل الإدارة.\n"
            f"بعد التوثيق، سيمكنك استخدام جميع خدمات التحليل.\n\n"
            f"شكراً لثقتك بـ Obeida Trading!",
            parse_mode="Markdown"
        )
        
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🆕 **طلب تسجيل جديد**\n\n"
                         f"👤 المستخدم: `{user_id}`\n"
                         f"🏢 الوكالة: {selected_agency[1]}\n"
                         f"📱 معرف المنصة: {user_message}\n\n"
                         f"للتوثيق: /verify_user {user_id}",
                    parse_mode="Markdown"
                )
            except:
                pass
        
        context.user_data.pop('selected_agency', None)
        return await start(update, context)
    else:
        await update.message.reply_text(f"❌ فشل التسجيل: {result}")
        return SELECT_AGENCY

async def handle_session_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "🔙 العودة":
        return await handle_main_menu(update, context)
    
    if user_message == "📋 تنبيهاتي":
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT session_name FROM user_session_alerts WHERE user_id = ?", (user_id,))
        alerts = cursor.fetchall()
        conn.close()
        
        if alerts:
            text = "🔔 **تنبيهات الجلسات النشطة:**\n\n"
            for alert in alerts:
                text += f"• {alert[0]}\n"
        else:
            text = "📭 لا توجد تنبيهات جلسات نشطة.\n\nاختر جلسة من القائمة لتفعيل التنبيه."
        
        await update.message.reply_text(text, parse_mode="Markdown")
        return SESSION_ALERT
    
    session_map = {
        "🌏 الجلسة الآسيوية": "asian",
        "🇬🇧 جلسة لندن": "london",
        "🗽 جلسة نيويورك": "newyork",
        "🌊 جلسة المحيط الهادئ": "pacific"
    }
    
    if user_message in session_map:
        session_key = session_map[user_message]
        session_name = TRADING_SESSIONS[session_key]['name']
        
        AgencySystem.add_session_alert(user_id, session_name)
        
        await update.message.reply_text(
            f"✅ **تم تفعيل تنبيه {session_name}**\n\n"
            f"ستصلك رسالة عند بدء الجلسة.",
            parse_mode="Markdown"
        )
        return SESSION_ALERT
    
    await update.message.reply_text("❌ الرجاء اختيار جلسة من القائمة.")
    return SESSION_ALERT

async def handle_shop_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "🔙 العودة":
        return await handle_main_menu(update, context)
    
    if user_message == "📋 طلباتي":
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT o.id, a.name, o.subscription_type, o.status, o.created_at
            FROM shop_orders o
            JOIN agencies a ON o.agency_id = a.id
            WHERE o.user_id = ?
            ORDER BY o.created_at DESC
        ''', (user_id,))
        orders = cursor.fetchall()
        conn.close()
        
        if orders:
            text = "📋 **طلبات الشراء الخاصة بك:**\n\n"
            for order in orders:
                status_emoji = "⏳" if order[3] == "pending" else "✅" if order[3] == "completed" else "❌"
                text += f"{status_emoji} #{order[0]} - {order[1]} - {order[2]} - {order[4][:16]}\n"
        else:
            text = "📭 لا توجد طلبات شراء سابقة."
        
        await update.message.reply_text(text, parse_mode="Markdown")
        return SHOP_MENU
    
    subscription_map = {
        "🕐 اشتراك ساعة - 1$": ("hourly", "ساعة", 1),
        "📅 اشتراك يوم - 5$": ("daily", "يوم", 5),
        "📆 اشتراك أسبوع - 15$": ("weekly", "أسبوع", 15),
        "📅 اشتراك شهر - 40$": ("monthly", "شهر", 40),
        "♾️ اشتراك سنة - 400$": ("yearly", "سنة", 400)
    }
    
    if user_message in subscription_map:
        sub_type, sub_name, price = subscription_map[user_message]
        
        agencies = AgencySystem.get_agencies()
        if not agencies:
            await update.message.reply_text(
                "❌ **لا توجد وكالات متاحة حالياً**\n\n"
                "يرجى التواصل مع المشرف: @Sz2zv",
                parse_mode="Markdown"
            )
            return SHOP_MENU
        
        order_id = AgencySystem.add_shop_order(user_id, agencies[0][0], sub_type)
        
        await update.message.reply_text(
            f"✅ **تم استلام طلبك بنجاح!**\n\n"
            f"🛒 **تفاصيل الطلب:**\n"
            f"• رقم الطلب: #{order_id}\n"
            f"• الاشتراك: {sub_name}\n"
            f"• السعر: {price}$\n\n"
            f"⏳ سيتم التواصل معك من قبل الدعم لإتمام عملية الشراء.\n\n"
            f"📞 للتواصل: @Sz2zv",
            parse_mode="Markdown"
        )
        
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🛒 **طلب شراء جديد**\n\n"
                         f"👤 المستخدم: `{user_id}`\n"
                         f"📦 الاشتراك: {sub_name}\n"
                         f"💰 السعر: {price}$\n"
                         f"🆔 رقم الطلب: #{order_id}",
                    parse_mode="Markdown"
                )
            except:
                pass
        
        return SHOP_MENU
    
    await update.message.reply_text("❌ الرجاء اختيار اشتراك من القائمة.")
    return SHOP_MENU

async def start_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول للوحة التحكم.")
        return MAIN_MENU
    
    keyboard = [
        ["➕ إضافة وكالة", "📋 طلبات التسجيل"],
        ["🛒 طلبات الشراء", "👥 المستخدمين"],
        ["📊 إحصائيات", "💾 نسخ احتياطي"],
        ["🔙 العودة"]
    ]
    
    conn = sqlite3.connect(AGENCY_DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_registrations WHERE verified = 0")
    pending_regs = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM shop_orders WHERE status = 'pending'")
    pending_orders = cursor.fetchone()[0]
    conn.close()
    
    await update.message.reply_text(
        f"👑 **لوحة تحكم المشرف** 👑\n\n"
        f"📋 طلبات تسجيل معلقة: {pending_regs}\n"
        f"🛒 طلبات شراء معلقة: {pending_orders}\n\n"
        f"اختر الإجراء المطلوب:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
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
    
    elif user_message == "➕ إضافة وكالة":
        await update.message.reply_text(
            "➕ **إضافة وكالة جديدة**\n\n"
            "أرسل معلومات الوكالة بهذا التنسيق:\n\n"
            "`اسم الوكالة | المنصة | شروط التسجيل | رابط التسجيل`\n\n"
            "مثال:\n"
            "`Obeida FX | MetaTrader 5 | الحد الأدنى للإيداع 100$ | https://example.com/register`",
            parse_mode="Markdown"
        )
        return ADD_AGENCY
    
    elif user_message == "📋 طلبات التسجيل":
        registrations = AgencySystem.get_unverified_registrations()
        
        if not registrations:
            await update.message.reply_text("📭 لا توجد طلبات تسجيل معلقة.")
            return ADMIN_MENU
        
        text = "📋 **طلبات تسجيل الوكالات:**\n\n"
        for reg in registrations:
            text += f"🆔 #{reg[0]} | المستخدم: `{reg[1]}`\n"
            text += f"📱 معرف المنصة: `{reg[2] or 'غير مدخل'}`\n"
            text += f"🏢 الوكالة: {reg[3]}\n"
            text += f"📅 التاريخ: {reg[4][:16]}\n"
            text += f"📝 /verify {reg[0]} {reg[1]}\n\n"
        
        for part in split_message(text, 4000):
            await update.message.reply_text(part, parse_mode="Markdown")
        
        return ADMIN_MENU
    
    elif user_message == "🛒 طلبات الشراء":
        orders = AgencySystem.get_pending_orders()
        
        if not orders:
            await update.message.reply_text("📭 لا توجد طلبات شراء معلقة.")
            return ADMIN_MENU
        
        text = "🛒 **طلبات الشراء المعلقة:**\n\n"
        for order in orders:
            text += f"🆔 #{order[0]} | المستخدم: `{order[1]}`\n"
            text += f"📦 الاشتراك: {order[2]}\n"
            text += f"🏢 الوكالة: {order[3]}\n"
            text += f"📅 التاريخ: {order[4][:16]}\n"
            text += f"📝 /complete_order {order[0]}\n\n"
        
        for part in split_message(text, 4000):
            await update.message.reply_text(part, parse_mode="Markdown")
        
        return ADMIN_MENU
    
    elif user_message == "👥 المستخدمين":
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT u.user_id, c.country, COUNT(r.id) as reg_count,
                   (SELECT COUNT(*) FROM user_registrations WHERE user_id = u.user_id AND verified = 1) as verified_count
            FROM (SELECT DISTINCT user_id FROM user_registrations UNION SELECT user_id FROM user_country) u
            LEFT JOIN user_country c ON u.user_id = c.user_id
            LEFT JOIN user_registrations r ON u.user_id = r.user_id
            GROUP BY u.user_id
            ORDER BY u.user_id DESC
            LIMIT 50
        ''')
        users = cursor.fetchall()
        conn.close()
        
        if not users:
            await update.message.reply_text("📭 لا يوجد مستخدمين.")
            return ADMIN_MENU
        
        text = "👥 **قائمة المستخدمين:**\n\n"
        for user in users:
            text += f"🆔 `{user[0]}` | {user[1] or 'لا يوجد بلد'}\n"
            text += f"📋 مسجل في {user[2]} وكالة | موثق: {user[3]}\n\n"
        
        for part in split_message(text, 4000):
            await update.message.reply_text(part, parse_mode="Markdown")
        
        return ADMIN_MENU
    
    elif user_message == "📊 إحصائيات":
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM user_country")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_registrations WHERE verified = 1")
        verified_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM user_registrations WHERE verified = 0")
        pending_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM shop_orders WHERE status = 'pending'")
        pending_orders = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM shop_orders WHERE status = 'completed'")
        completed_orders = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM agencies")
        total_agencies = cursor.fetchone()[0]
        
        conn.close()
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(total_analyses) FROM users")
        total_analyses = cursor.fetchone()[0] or 0
        conn.close()
        
        stats_text = (
            f"📊 **إحصائيات النظام**\n\n"
            f"👥 **المستخدمين:**\n"
            f"• إجمالي المستخدمين: {total_users}\n"
            f"• موثقين: {verified_users}\n"
            f"• قيد المراجعة: {pending_users}\n\n"
            f"🏢 **الوكالات:**\n"
            f"• إجمالي الوكالات: {total_agencies}\n\n"
            f"🛒 **المتجر:**\n"
            f"• طلبات معلقة: {pending_orders}\n"
            f"• طلبات منتهية: {completed_orders}\n\n"
            f"📊 **التحليلات:**\n"
            f"• إجمالي التحليلات: {total_analyses}\n"
        )
        
        await update.message.reply_text(stats_text, parse_mode="Markdown")
        return ADMIN_MENU
    
    elif user_message == "💾 نسخ احتياطي":
        DataProtection.backup_database()
        
        await update.message.reply_text(
            "✅ **تم إنشاء نسخة احتياطية**\n\n"
            f"📁 المجلد: {BACKUP_DIR}\n"
            f"📅 التاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    return ADMIN_MENU

async def handle_add_agency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    try:
        parts = user_message.split("|")
        if len(parts) != 4:
            await update.message.reply_text(
                "❌ صيغة غير صحيحة.\n\n"
                "الرجاء استخدام التنسيق:\n"
                "`اسم الوكالة | المنصة | شروط التسجيل | رابط التسجيل`",
                parse_mode="Markdown"
            )
            return ADD_AGENCY
        
        name = parts[0].strip()
        platform = parts[1].strip()
        terms = parts[2].strip()
        register_link = parts[3].strip()
        
        success, result = AgencySystem.add_agency(name, platform, terms, register_link, user_id)
        
        if success:
            await update.message.reply_text(
                f"✅ **تم إضافة الوكالة بنجاح!**\n\n"
                f"🏢 الاسم: {name}\n"
                f"📱 المنصة: {platform}\n"
                f"🆔 ID: {result}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"❌ فشل في إضافة الوكالة: {result}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)}")
    
    return ADMIN_MENU

async def handle_command_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return
    
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "❌ الاستخدام: /verify <registration_id> <user_id>\n"
                "مثال: /verify 5 123456789"
            )
            return
        
        reg_id = int(args[0])
        target_user_id = int(args[1])
        
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT agency_id FROM user_registrations WHERE id = ?", (reg_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            await update.message.reply_text("❌ لم يتم العثور على طلب التسجيل.")
            return
        
        agency_id = result[0]
        success, message = AgencySystem.verify_user_registration(target_user_id, agency_id, user_id)
        
        if success:
            await update.message.reply_text(
                f"✅ **تم توثيق تسجيل المستخدم!**\n\n"
                f"👤 المستخدم: `{target_user_id}`\n"
                f"{message}",
                parse_mode="Markdown"
            )
            
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text="✅ **تم توثيق تسجيلك في الوكالة!**\n\n"
                         "🎉 يمكنك الآن استخدام جميع خدمات التحليل الفني.\n\n"
                         "📊 استخدم /start للبدء.",
                    parse_mode="Markdown"
                )
            except:
                pass
        else:
            await update.message.reply_text(f"❌ {message}")
            
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)}")

async def handle_command_complete_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return
    
    try:
        args = context.args
        if len(args) < 1:
            await update.message.reply_text(
                "❌ الاستخدام: /complete_order <order_id>\n"
                "مثال: /complete_order 10"
            )
            return
        
        order_id = int(args[0])
        
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM shop_orders WHERE id = ?", (order_id,))
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            await update.message.reply_text("❌ لم يتم العثور على الطلب.")
            return
        
        target_user_id = result[0]
        
        cursor.execute("UPDATE shop_orders SET status = 'completed' WHERE id = ?", (order_id,))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"✅ **تم إكمال الطلب!**\n\n"
            f"🆔 رقم الطلب: #{order_id}\n"
            f"👤 المستخدم: `{target_user_id}`",
            parse_mode="Markdown"
        )
        
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text="✅ **تم إكمال طلب الشراء الخاص بك!**\n\n"
                     "🎉 شكراً لثقتك بـ Obeida Trading.\n\n"
                     "📊 استخدم /start للبدء في التحليل.",
                parse_mode="Markdown"
            )
        except:
            pass
            
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    🤖 **أوامر البوت:**
    
    /start - بدء البوت والعودة للقائمة الرئيسية
    /help - عرض رسالة المساعدة
    
    ⚙️ **كيفية الاستخدام:**
    1. قم بتسجيل بلدك أولاً
    2. سجل في إحدى وكالاتنا
    3. انتظر توثيق التسجيل من المشرف
    4. استخدم التحليل الفني
    
    📊 **جلسات التداول:**
    
    🌏 الآسيوية (00:00-08:00 UTC): 65-70%
    🇬🇧 لندن (08:00-16:00 UTC): 75-85%
    🗽 نيويورك (13:00-21:00 UTC): 80-90%
    🌊 المحيط الهادئ (21:00-00:00 UTC): 55-65%
    
    🔔 **تنبيه الجلسات:**
    يمكنك تفعيل تنبيه ليصلك إشعار عند بدء أي جلسة.
    
    🏢 **الوكالات:**
    يجب التسجيل في وكالة لاستخدام البوت.
    
    📞 للتواصل مع المشرف: @Sz2zv
    """
    
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        cleanup_user_data(context, update.effective_user.id)
    
    await update.message.reply_text(
        "تم الإلغاء. اكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        print(f"❌ خطأ: {context.error}")
        
        if update and hasattr(update, 'effective_chat'):
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⚠️ حدث خطأ تقني. يرجى المحاولة مرة أخرى."
                )
            except:
                pass
        
    except Exception as e:
        print(f"❌ خطأ في معالج الأخطاء: {e}")

async def session_alert_scheduler():
    last_session = None
    
    while True:
        try:
            current_session = get_current_session()
            current_session_name = current_session['name']
            
            if last_session != current_session_name:
                last_session = current_session_name
                
                users = AgencySystem.get_session_alerts(current_session_name)
                
                if users:
                    message = (
                        f"🔔 **بدء الجلسة** 🔔\n\n"
                        f"{current_session['icon']} **{current_session_name}**\n"
                        f"⏰ **الوقت:** {current_session['time_range']}\n"
                        f"📊 **التقلب:** {current_session['volatility']}\n"
                        f"🎯 **نسبة النجاح:** {current_session['success_rate']}\n"
                        f"💰 **الأزواج:** {current_session['pairs']}\n\n"
                        f"✨ **مميزات الجلسة:**\n{current_session['features']}\n\n"
                        f"📊 استخدم /start لبدء التحليل"
                    )
                    
                    for user_id in users:
                        try:
                            await application.bot.send_message(
                                chat_id=user_id,
                                text=message,
                                parse_mode="Markdown"
                            )
                            await asyncio.sleep(0.1)
                        except:
                            pass
                    
                    print(f"✅ تم إرسال تنبيه {current_session_name} لـ {len(users)} مستخدم")
            
            await asyncio.sleep(60)
            
        except Exception as e:
            print(f"⚠️ خطأ في جدولة التنبيهات: {e}")
            await asyncio.sleep(60)

class DataProtection:
    @staticmethod
    def backup_database():
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(BACKUP_DIR, f"main_backup_{timestamp}.db")
            if os.path.exists(DB_NAME):
                shutil.copy2(DB_NAME, backup_file)
                print(f"💾 تم نسخ قاعدة البيانات الرئيسية: {backup_file}")
                
                backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('main_backup_')])
                while len(backups) > 20:
                    oldest = backups.pop(0)
                    os.remove(os.path.join(BACKUP_DIR, oldest))
                    
            backup_agency = os.path.join(BACKUP_DIR, f"agency_backup_{timestamp}.db")
            if os.path.exists(AGENCY_DB_NAME):
                shutil.copy2(AGENCY_DB_NAME, backup_agency)
                print(f"💾 تم نسخ قاعدة بيانات الوكالات: {backup_agency}")
                
        except Exception as e:
            print(f"⚠️ خطأ في نسخ قاعدة البيانات: {e}")

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
    <title>👑 لوحة التحكم</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { background: white; border-radius: 20px; padding: 30px; margin-bottom: 30px; box-shadow: 0 10px 40px rgba(0,0,0,0.1); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; }
        .header h1 { color: #333; font-size: 2.2em; margin-bottom: 5px; }
        .header .subtitle { color: #666; font-size: 1.1em; }
        .logout-btn { background: #dc3545; color: white; padding: 10px 20px; border-radius: 10px; text-decoration: none; font-weight: 600; transition: background 0.3s; }
        .logout-btn:hover { background: #c82333; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: white; border-radius: 15px; padding: 25px; box-shadow: 0 5px 20px rgba(0,0,0,0.05); transition: transform 0.3s; }
        .stat-card:hover { transform: translateY(-5px); }
        .stat-card h3 { color: #666; font-size: 1em; margin-bottom: 10px; }
        .stat-card .number { color: #667eea; font-size: 2.5em; font-weight: bold; }
        .section { background: white; border-radius: 20px; padding: 30px; margin-bottom: 30px; box-shadow: 0 5px 20px rgba(0,0,0,0.05); }
        .section-title { color: #333; font-size: 1.8em; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 2px solid #f0f0f0; }
        .table-container { overflow-x: auto; border-radius: 10px; border: 1px solid #f0f0f0; }
        table { width: 100%; border-collapse: collapse; }
        th { background: #667eea; color: white; padding: 15px; font-weight: 600; text-align: center; }
        td { padding: 15px; border-bottom: 1px solid #f0f0f0; text-align: center; }
        tr:hover { background: #f8f9ff; }
        .status-badge { padding: 5px 12px; border-radius: 20px; font-size: 0.9em; font-weight: 600; display: inline-block; }
        .status-pending { background: #ffc107; color: #333; }
        .status-verified { background: #d4edda; color: #155724; }
        .code-box { background: #f8f9ff; padding: 5px 10px; border-radius: 5px; font-family: monospace; border: 1px dashed #667eea; }
        .btn { padding: 8px 16px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; transition: all 0.3s; text-decoration: none; display: inline-block; font-size: 0.9em; }
        .btn-primary { background: #667eea; color: white; }
        .btn-primary:hover { background: #5a67d8; }
        .btn-success { background: #28a745; color: white; }
        .btn-success:hover { background: #218838; }
        .btn-danger { background: #dc3545; color: white; }
        .btn-danger:hover { background: #c82333; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); justify-content: center; align-items: center; z-index: 1000; }
        .modal-content { background: white; padding: 30px; border-radius: 20px; width: 90%; max-width: 500px; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 5px; color: #666; font-weight: 600; }
        .form-group input, .form-group textarea { width: 100%; padding: 12px; border: 2px solid #f0f0f0; border-radius: 10px; font-size: 1em; }
        .form-group input:focus, .form-group textarea:focus { outline: none; border-color: #667eea; }
        .modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 20px; }
        .notification { position: fixed; top: 20px; right: 20px; padding: 15px 25px; background: white; border-radius: 10px; box-shadow: 0 5px 20px rgba(0,0,0,0.2); display: none; border-right: 5px solid #28a745; }
        .footer { text-align: center; color: white; margin-top: 30px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🤖 لوحة تحكم Obeida Trading</h1>
                <p class="subtitle">نظام إدارة الوكالات والمتجر</p>
            </div>
            <a href="/logout" class="logout-btn">تسجيل الخروج</a>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>👥 المستخدمين</h3>
                <div class="number">{{ total_users }}</div>
            </div>
            <div class="stat-card">
                <h3>✅ الموثقين</h3>
                <div class="number">{{ verified_users }}</div>
            </div>
            <div class="stat-card">
                <h3>⏳ قيد المراجعة</h3>
                <div class="number">{{ pending_users }}</div>
            </div>
            <div class="stat-card">
                <h3>🏢 الوكالات</h3>
                <div class="number">{{ total_agencies }}</div>
            </div>
            <div class="stat-card">
                <h3>🛒 طلبات معلقة</h3>
                <div class="number">{{ pending_orders }}</div>
            </div>
            <div class="stat-card">
                <h3>📊 إجمالي التحليلات</h3>
                <div class="number">{{ total_analyses }}</div>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">📋 طلبات تسجيل الوكالات</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr><th>ID</th><th>المستخدم</th><th>الوكالة</th><th>معرف المنصة</th><th>التاريخ</th><th>الإجراءات</th></tr>
                    </thead>
                    <tbody>
                        {% for reg in pending_registrations %}
                        <tr>
                            <td><span class="code-box">{{ reg.id }}</span></td>
                            <td><span class="code-box">{{ reg.user_id }}</span></td>
                            <td>{{ reg.agency_name }}</td>
                            <td>{{ reg.platform_id or '-' }}</td>
                            <td>{{ reg.created_at[:16] }}</td>
                            <td><button class="btn btn-success" onclick="verifyRegistration({{ reg.id }}, {{ reg.user_id }})">✅ توثيق</button></td>
                        </tr>
                        {% else %}
                        <tr><td colspan="6">لا توجد طلبات معلقة</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">🛒 طلبات الشراء المعلقة</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr><th>ID</th><th>المستخدم</th><th>الوكالة</th><th>الاشتراك</th><th>التاريخ</th><th>الإجراءات</th></tr>
                    </thead>
                    <tbody>
                        {% for order in pending_shop_orders %}
                        <tr>
                            <td><span class="code-box">{{ order.id }}</span></td>
                            <td><span class="code-box">{{ order.user_id }}</span></td>
                            <td>{{ order.agency_name }}</td>
                            <td>{{ order.subscription_type }}</td>
                            <td>{{ order.created_at[:16] }}</td>
                            <td><button class="btn btn-success" onclick="completeOrder({{ order.id }})">✅ إكمال</button></td>
                        </tr>
                        {% else %}
                        <tr><td colspan="6">لا توجد طلبات معلقة</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <h2 class="section-title">🏢 قائمة الوكالات</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr><th>ID</th><th>الاسم</th><th>المنصة</th><th>الشروط</th><th>رابط التسجيل</th></tr>
                    </thead>
                    <tbody>
                        {% for agency in agencies %}
                        <tr>
                            <td>{{ agency.id }}</td>
                            <td>{{ agency.name }}</td>
                            <td>{{ agency.platform }}</td>
                            <td style="max-width: 300px;">{{ agency.terms[:50] }}...</td>
                            <td><a href="{{ agency.register_link }}" target="_blank">{{ agency.register_link[:30] }}</a></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            <button class="btn btn-primary" onclick="showAddAgencyModal()" style="margin-top: 20px;">➕ إضافة وكالة جديدة</button>
        </div>
    </div>

    <div id="addAgencyModal" class="modal">
        <div class="modal-content">
            <h2>➕ إضافة وكالة جديدة</h2>
            <div class="form-group">
                <label>اسم الوكالة</label>
                <input type="text" id="agencyName" placeholder="مثال: Obeida FX">
            </div>
            <div class="form-group">
                <label>المنصة</label>
                <input type="text" id="agencyPlatform" placeholder="مثال: MetaTrader 5">
            </div>
            <div class="form-group">
                <label>شروط التسجيل</label>
                <textarea id="agencyTerms" rows="3" placeholder="شروط التسجيل في الوكالة..."></textarea>
            </div>
            <div class="form-group">
                <label>رابط التسجيل</label>
                <input type="url" id="agencyLink" placeholder="https://example.com/register">
            </div>
            <div class="modal-actions">
                <button class="btn btn-success" onclick="addAgency()">✅ إضافة</button>
                <button class="btn btn-danger" onclick="closeModal('addAgencyModal')">❌ إلغاء</button>
            </div>
        </div>
    </div>

    <div id="notification" class="notification"></div>

    <div class="footer">
        <p>🤯 Obeida Trading - نظام إدارة الوكالات والمتجر</p>
    </div>

    <script>
        function showModal(modalId) { document.getElementById(modalId).style.display = 'flex'; }
        function closeModal(modalId) { document.getElementById(modalId).style.display = 'none'; }
        
        function showNotification(message, type = 'success') {
            let notification = document.getElementById('notification');
            notification.textContent = message;
            notification.style.borderRightColor = type === 'success' ? '#28a745' : '#dc3545';
            notification.style.display = 'block';
            setTimeout(() => { notification.style.display = 'none'; }, 3000);
        }
        
        function showAddAgencyModal() { showModal('addAgencyModal'); }
        
        function addAgency() {
            let name = document.getElementById('agencyName').value;
            let platform = document.getElementById('agencyPlatform').value;
            let terms = document.getElementById('agencyTerms').value;
            let link = document.getElementById('agencyLink').value;
            
            if (!name || !platform || !terms || !link) {
                showNotification('الرجاء ملء جميع الحقول', 'error');
                return;
            }
            
            fetch('/admin/add_agency', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, platform, terms, register_link: link })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('✅ تم إضافة الوكالة بنجاح');
                    closeModal('addAgencyModal');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => showNotification('❌ ' + error, 'error'));
        }
        
        function verifyRegistration(regId, userId) {
            fetch('/admin/verify_registration', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reg_id: regId, user_id: userId })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('✅ تم توثيق التسجيل');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => showNotification('❌ ' + error, 'error'));
        }
        
        function completeOrder(orderId) {
            fetch('/admin/complete_order', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ order_id: orderId })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('✅ تم إكمال الطلب');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => showNotification('❌ ' + error, 'error'));
        }
        
        window.onclick = function(event) {
            let modal = document.getElementById('addAgencyModal');
            if (event.target == modal) modal.style.display = 'none';
        }
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
        return render_template_string(LOGIN_PAGE_HTML, error='اسم المستخدم أو كلمة المرور غير صحيحة')
    return render_template_string(LOGIN_PAGE_HTML)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/admin')
@login_required
def admin_panel():
    conn = sqlite3.connect(AGENCY_DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM user_country")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM user_registrations WHERE verified = 1")
    verified_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM user_registrations WHERE verified = 0")
    pending_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM agencies")
    total_agencies = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM shop_orders WHERE status = 'pending'")
    pending_orders = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT r.id, r.user_id, a.name, r.platform_id, r.registered_at
        FROM user_registrations r
        JOIN agencies a ON r.agency_id = a.id
        WHERE r.verified = 0
        ORDER BY r.registered_at DESC
    ''')
    pending_regs = [{'id': r[0], 'user_id': r[1], 'agency_name': r[2], 'platform_id': r[3], 'created_at': r[4]} for r in cursor.fetchall()]
    
    cursor.execute('''
        SELECT o.id, o.user_id, a.name, o.subscription_type, o.created_at
        FROM shop_orders o
        JOIN agencies a ON o.agency_id = a.id
        WHERE o.status = 'pending'
        ORDER BY o.created_at DESC
    ''')
    pending_shop_orders = [{'id': o[0], 'user_id': o[1], 'agency_name': o[2], 'subscription_type': o[3], 'created_at': o[4]} for o in cursor.fetchall()]
    
    cursor.execute("SELECT id, name, platform, terms, register_link FROM agencies WHERE is_active = 1")
    agencies = [{'id': a[0], 'name': a[1], 'platform': a[2], 'terms': a[3], 'register_link': a[4]} for a in cursor.fetchall()]
    
    conn.close()
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(total_analyses) FROM users")
    total_analyses = cursor.fetchone()[0] or 0
    conn.close()
    
    return render_template_string(ADMIN_PANEL_HTML, 
        total_users=total_users, verified_users=verified_users, pending_users=pending_users,
        total_agencies=total_agencies, pending_orders=pending_orders, total_analyses=total_analyses,
        pending_registrations=pending_regs, pending_shop_orders=pending_shop_orders, agencies=agencies)

@app.route('/admin/add_agency', methods=['POST'])
@login_required
def add_agency():
    try:
        data = request.json
        name = data.get('name')
        platform = data.get('platform')
        terms = data.get('terms')
        register_link = data.get('register_link')
        
        success, result = AgencySystem.add_agency(name, platform, terms, register_link, ADMIN_ID)
        
        if success:
            return jsonify({'success': True, 'id': result})
        return jsonify({'success': False, 'error': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/verify_registration', methods=['POST'])
@login_required
def verify_registration():
    try:
        data = request.json
        reg_id = data.get('reg_id')
        user_id = data.get('user_id')
        
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT agency_id FROM user_registrations WHERE id = ?", (reg_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return jsonify({'success': False, 'error': 'لم يتم العثور على الطلب'})
        
        agency_id = result[0]
        success, message = AgencySystem.verify_user_registration(user_id, agency_id, ADMIN_ID)
        
        if success:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/complete_order', methods=['POST'])
@login_required
def complete_order():
    try:
        data = request.json
        order_id = data.get('order_id')
        
        conn = sqlite3.connect(AGENCY_DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM shop_orders WHERE id = ?", (order_id,))
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return jsonify({'success': False, 'error': 'لم يتم العثور على الطلب'})
        
        cursor.execute("UPDATE shop_orders SET status = 'completed' WHERE id = ?", (order_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def run_flask_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ============================================
# التشغيل الرئيسي
# ============================================

async def main():
    global application
    print("🤖 Starting Obeida Trading with Agency System...")
    
    init_all_dbs()
    DataProtection.backup_database()
    
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    print("✅ Flask server running on port 8080")
    
    if os.path.exists(IMAGE_CACHE_DIR):
        for filename in os.listdir(IMAGE_CACHE_DIR):
            filepath = os.path.join(IMAGE_CACHE_DIR, filename)
            if os.path.isfile(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass
    else:
        os.makedirs(IMAGE_CACHE_DIR)
    
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu)],
            SETTINGS_CANDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_candle)],
            SETTINGS_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_settings_time)],
            ANALYZE_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_analyze_mode), MessageHandler(filters.PHOTO, handle_photo_analysis)],
            ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_menu)],
            ADD_AGENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_agency)],
            SELECT_AGENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_select_agency)],
            REGISTER_AGENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_register_agency)],
            SET_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_country)],
            SESSION_ALERT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session_alert)],
            SHOP_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_shop_menu)],
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("verify_user", handle_command_verify))
    application.add_handler(CommandHandler("complete_order", handle_command_complete_order))
    application.add_error_handler(error_handler)
    
    news_radar = ObeidaNewsRadar(TOKEN)
    asyncio.create_task(news_radar.start_radar())
    
    asyncio.create_task(session_alert_scheduler())
    
    print("✅ Telegram Bot ready...")
    print("📡 News Radar running...")
    print("🔔 Session Alerts active...")
    
    await application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
