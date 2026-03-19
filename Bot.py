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

MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, ANALYZE_MODE, ADMIN_MENU, ADD_SUBSCRIPTION, SELECT_DURATION, REMOVE_USER, BROADCAST_MESSAGE, SET_USER_DURATION, SET_BOT_STATUS, SET_SESSION_CONTROL, TIMEZONE_MENU, SEARCH_COUNTRY, FREE_ANALYSES_SETTINGS = range(15)

for directory in [IMAGE_CACHE_DIR, BACKUP_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"📁 تم إنشاء المجلد: {directory}")

# ============================================
# دوال مساعدة للتنسيق والنسخ
# ============================================

def escape_markdown(text):
    """تنظيف النص من الأحرف الخاصة في Markdown"""
    if not text:
        return text
    
    # قائمة الأحرف الخاصة في Markdown
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    # هروب الأحرف الخاصة
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    
    return text

def format_analysis_with_copy_button(analysis_text):
    """تنسيق التحليل مع زر نسخ"""
    # إزالة أي تنسيق Markdown مزدوج
    analysis_text = re.sub(r'```[\w]*\n?', '', analysis_text)
    analysis_text = re.sub(r'```\n?', '', analysis_text)
    
    # تنسيق النص داخل كود block للنسخ السهل
    formatted_text = f"```\n{analysis_text}\n```\n\n"
    formatted_text += "📋 **لنسخ التحليل:** اضغط على الرمز أعلاه ثم اختر Copy"
    
    return formatted_text

# ============================================
# نظام رادار الأخبار المتقدم (Obeida News Radar V2.0)
# ============================================

class ObeidaNewsRadar:
    """
    نظام رادار الأخبار المتقدم من Obeida Trading
    - يدعم جميع عملات العالم
    - يستخدم RSS من Investing.com
    - يرسل تلقائياً لقناة @ObeidaTrading ومجموعة @ObeidaTradingchat
    """
    
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.channel_id = '@ObeidaTrading'
        self.chat_group_id = '@ObeidaTradingchat'
        self.rss_url = 'https://sa.investing.com/rss/news.rss'
        self.db_name = NEWS_RADAR_DB
        self.last_guid = ""
        self.processed_news: Set[str] = set()
        self.broadcast_enabled = True
        
        # تهيئة قاعدة البيانات
        self.init_db()
        
        # تحميل GUIDs السابقة
        self.load_processed_news()
        
        logger.info("📡 نظام رادار Obeida Trading قيد التشغيل...")

    # ============================================
    # قاموس العملات الشامل (جميع عملات العالم)
    # ============================================
    CURRENCY_MAP = {
        # العملات الرئيسية (Majors)
        "دولار": "USD 🇺🇸",
        "يورو": "EUR 🇪🇺", 
        "إسترليني": "GBP 🇬🇧",
        "ين": "JPY 🇯🇵",
        "فرنك": "CHF 🇨🇭",
        "كندي": "CAD 🇨🇦",
        "أسترالي": "AUD 🇦🇺",
        "نيوزيلندي": "NZD 🇳🇿",
        
        # المعادن الثمينة
        "ذهب": "XAU 🟡",
        "فضة": "XAG ⚪",
        "بلاتين": "XPT ⚪",
        "بلاديوم": "XPD ⚪",
        
        # العملات الرقمية المشفرة
        "بتكوين": "BTC ₿",
        "بيتكوين": "BTC ₿",
        "إيثريوم": "ETH 💎",
        "عملات رقمية": "CRYPTO 💱",
        "بينانس": "BNB 🔶",
        "ريبل": "XRP 💠",
        "كاردانو": "ADA 🔷",
        "دوجكوين": "DOGE 🐕",
        "سولانا": "SOL ☀️",
        "بولكادوت": "DOT 🎯",
        
        # النفط والطاقة
        "نفط": "OIL 🛢️",
        "خام": "CRUDE 🛢️",
        "برنت": "BRENT 🛢️",
        "غاز": "NATGAS 🔥",
        "بنزين": "GASOLINE ⛽",
        
        # العملات الآسيوية
        "يوان": "CNY 🇨🇳",
        "دولار هونغ كونغ": "HKD 🇭🇰",
        "دولار سنغافوري": "SGD 🇸🇬",
        "روبيه هندي": "INR 🇮🇳",
        "وون": "KRW 🇰🇷",
        "بات": "THB 🇹🇭",
        "رينغيت": "MYR 🇲🇾",
        "روبيه إندونيسي": "IDR 🇮🇩",
        "بيزو فلبيني": "PHP 🇵🇭",
        "دونغ": "VND 🇻🇳",
        
        # العملات العربية
        "ريال سعودي": "SAR 🇸🇦",
        "درهم": "AED 🇦🇪",
        "ريال قطري": "QAR 🇶🇦",
        "دينار كويتي": "KWD 🇰🇼",
        "دينار بحريني": "BHD 🇧🇭",
        "ريال عماني": "OMR 🇴🇲",
        "دينار أردني": "JOD 🇯🇴",
        "جنيه مصري": "EGP 🇪🇬",
        "دينار عراقي": "IQD 🇮🇶",
        "ليرة سورية": "SYP 🇸🇾",
        "ليرة لبنانية": "LBP 🇱🇧",
        "دينار ليبي": "LYD 🇱🇾",
        "دينار تونسي": "TND 🇹🇳",
        "دينار جزائري": "DZD 🇩🇿",
        "درهم مغربي": "MAD 🇲🇦",
        "أوقية موريتانية": "MRU 🇲🇷",
        
        # العملات الأوروبية الأخرى
        "كرونة سويدية": "SEK 🇸🇪",
        "كرونة نرويجية": "NOK 🇳🇴",
        "كرونة دنماركية": "DKK 🇩🇰",
        "زلوتي": "PLN 🇵🇱",
        "كرونة تشيكية": "CZK 🇨🇿",
        "فورنت": "HUF 🇭🇺",
        "ليو": "RON 🇷🇴",
        "ليف": "BGN 🇧🇬",
        "كونا": "HRK 🇭🇷",
        "دينار صربي": "RSD 🇷🇸",
        "كرونة آيسلندية": "ISK 🇮🇸",
        
        # العملات الأفريقية
        "نيرة": "NGN 🇳🇬",
        "شلن كيني": "KES 🇰🇪",
        "سيدي": "GHS 🇬🇭",
        "راند": "ZAR 🇿🇦",
        "شلن تنزاني": "TZS 🇹🇿",
        "شلن أوغندي": "UGX 🇺🇬",
        "كواشا": "ZMW 🇿🇲",
        "روبية موريشيوسية": "MUR 🇲🇺",
        "بير": "ETB 🇪🇹",
        "كوانزا": "AOA 🇦🇴",
        "ميتيكال": "MZN 🇲🇿",
        
        # العملات الأمريكية
        "بيزو مكسيكي": "MXN 🇲🇽",
        "ريال برازيلي": "BRL 🇧🇷",
        "بيزو أرجنتيني": "ARS 🇦🇷",
        "بيزو تشيلي": "CLP 🇨🇱",
        "بيزو كولومبي": "COP 🇨🇴",
        "سول": "PEN 🇵🇪",
        
        # المؤشرات العالمية
        "مؤشر": "INDEX 📊",
        "داو جونز": "DJI 📈",
        "ناسداك": "NDQ 📈",
        "ستاندرد آند بورز": "SPX 📈",
        "فوتسي": "FTSE 🇬🇧",
        "داكس": "DAX 🇩🇪",
        "كاك": "CAC 🇫🇷",
        "نيكاي": "N225 🇯🇵",
        "هانغ سنغ": "HSI 🇭🇰",
        "شنغهاي": "SSEC 🇨🇳",
        
        # السندات والفائدة
        "سندات": "BOND 📉",
        "فائدة": "RATE 💹",
        "احتياطي": "FED 🏦",
        "مركزي": "CB 🏛️",
        
        # السلع
        "قمح": "WHEAT 🌾",
        "ذرة": "CORN 🌽",
        "فول": "SOYBEAN 🌱",
        "سكر": "SUGAR 🍬",
        "بن": "COFFEE ☕",
        "كاكاو": "COCOA 🍫",
        "قطن": "COTTON 👕",
        "حديد": "IRON ⚙️",
        "نحاس": "COPPER 🔌",
        "ألمنيوم": "ALUMINUM 🥫",
        "زنك": "ZINC ⚡",
        "نيكل": "NICKEL 🔋"
    }
    
    # كلمات مفتاحية موسعة لكل عملة
    CURRENCY_KEYWORDS = {
        "دولار": ["دولار", "us dollar", "usd", "dollar", "الولايات المتحدة", "america", "us economy", "الفيدرالي", "الاحتياطي الفيدرالي", "federal reserve", "fed"],
        "يورو": ["يورو", "euro", "eur", "european", "البنك المركزي الأوروبي", "ecb", "الاتحاد الأوروبي"],
        "إسترليني": ["إسترليني", "pound", "gbp", "sterling", "بريطانيا", "uk", "boe", "بنك إنجلترا"],
        "ين": ["ين", "yen", "jpy", "اليابان", "japan", "boj", "بنك اليابان"],
        "فرنك": ["فرنك", "swiss", "chf", "سويسرا", "البنك الوطني السويسري"],
        "كندي": ["كندي", "canadian", "cad", "كندا", "bank of canada"],
        "أسترالي": ["أسترالي", "australian", "aud", "أستراليا", "rba"],
        "نيوزيلندي": ["نيوزيلندي", "new zealand", "nzd", "نيوزيلندا", "rbnz"],
        "ذهب": ["ذهب", "gold", "xau", "الذهب", "المعدن النفيس"],
        "فضة": ["فضة", "silver", "xag"],
        "نفط": ["نفط", "oil", "خام", "crude", "brent", "wti", "بترول", "petroleum", "أوبك", "opec"],
        "بتكوين": ["بتكوين", "bitcoin", "btc", "بيتكوين", "عملات رقمية", "crypto"],
        "إيثريوم": ["إيثريوم", "ethereum", "eth"],
    }
    
    def init_db(self):
        """تهيئة قاعدة البيانات لتخزين الأخبار المرسلة"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # جدول الأخبار المرسلة
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
        
        # جدول إعدادات الرادار
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS radar_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # إضافة الإعدادات الافتراضية
        cursor.execute("SELECT COUNT(*) FROM radar_settings")
        if cursor.fetchone()[0] == 0:
            default_settings = [
                ('enabled', 'true'),
                ('check_interval', '300'),
                ('max_age_hours', '24'),
                ('broadcast_enabled', 'true')
            ]
            cursor.executemany("INSERT INTO radar_settings (key, value) VALUES (?, ?)", default_settings)
        
        conn.commit()
        conn.close()
        logger.info("✅ تم تهيئة قاعدة بيانات رادار الأخبار")
    
    def load_processed_news(self):
        """تحميل GUIDs الأخبار التي تمت معالجتها سابقاً"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT guid FROM sent_news ORDER BY id DESC LIMIT 100")
        rows = cursor.fetchall()
        for row in rows:
            self.processed_news.add(row[0])
            if not self.last_guid:
                self.last_guid = row[0]
        conn.close()
        logger.info(f"📚 تم تحميل {len(self.processed_news)} خبر سابق")
    
    def get_currency_tag(self, title: str) -> str:
        """
        استخراج العملة المتأثرة من عنوان الخبر
        تدعم جميع عملات العالم
        """
        title_lower = title.lower()
        matched_currencies = []
        
        # البحث في الكلمات المفتاحية الموسعة
        for currency, tag in self.CURRENCY_MAP.items():
            if currency in title:
                matched_currencies.append(tag)
                continue
            
            # البحث في الكلمات المفتاحية الإضافية
            if currency in self.CURRENCY_KEYWORDS:
                for keyword in self.CURRENCY_KEYWORDS[currency]:
                    if keyword in title_lower:
                        matched_currencies.append(tag)
                        break
        
        if matched_currencies:
            # إزالة التكرار وإرجاع أول 3 عملات
            unique_tags = list(set(matched_currencies))
            return " ".join(unique_tags[:3])
        
        return "#سوق_عام 🌐"
    
    def format_news_message(self, title: str, link: str, published: str, currency_tag: str) -> str:
        """تنسيق رسالة الخبر للإرسال"""
        published_time = ""
        try:
            # محاولة تحويل الوقت
            from dateutil import parser
            pub_date = parser.parse(published)
            published_time = pub_date.strftime("%Y-%m-%d %H:%M")
        except:
            published_time = "حديث"
        
        message = (
            f"🚀 <b> Obeida Trading - أخبار عاجلة</b> 🚀\n\n"
            f"📰 <b>🛑 الخبر 🛑:</b>\n"
            f"{title}\n\n"
            f"💰 <b>العملات المتأثرة:</b> {currency_tag}\n"
            f"⏰ <b>الوقت:</b> {published_time}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>للتحليل الفوري:</b> @ObeidaTrading\n"
            f"💬 <b>مناقشة الأخبار:</b> @ObeidaTradingchat"
        )
        return message
    
    def send_telegram_message(self, chat_id: str, text: str):
        """إرسال رسالة إلى تليجرام"""
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        try:
            response = requests.post(url, data=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"✅ تم الإرسال إلى {chat_id}")
                return True
            else:
                logger.error(f"❌ فشل الإرسال: {response.text}")
                return False
        except Exception as e:
            logger.error(f"⚠️ خطأ في الإرسال: {e}")
            return False
    
    def is_news_recent(self, published: str, max_hours: int = 24) -> bool:
        """التحقق من أن الخبر حديث"""
        try:
            from dateutil import parser
            pub_date = parser.parse(published)
            now = datetime.now(pub_date.tzinfo) if pub_date.tzinfo else datetime.now()
            age = now - pub_date
            return age.total_seconds() <= max_hours * 3600
        except:
            # إذا فشل تحليل الوقت، نعتبره مقبول
            return True
    
    def save_sent_news(self, guid: str, title: str, link: str, published: str, currency_tag: str, chat_id: str):
        """حفظ الخبر المرسل في قاعدة البيانات"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO sent_news (guid, title, link, published, currency_tag, chat_id)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (guid, title, link, published, currency_tag, chat_id))
            conn.commit()
            self.processed_news.add(guid)
            self.last_guid = guid
        except Exception as e:
            logger.error(f"⚠️ خطأ في حفظ الخبر: {e}")
        finally:
            conn.close()
    
    def get_setting(self, key: str, default: str = None) -> str:
        """الحصول على إعداد معين"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT value FROM radar_settings WHERE key = ?", (key,))
            result = cursor.fetchone()
            return result[0] if result else default
        except:
            return default
        finally:
            conn.close()
    
    def set_setting(self, key: str, value: str):
        """تحديث إعداد معين"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO radar_settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (key, value))
            conn.commit()
            logger.info(f"✅ تم تحديث الإعداد {key} = {value}")
        except Exception as e:
            logger.error(f"⚠️ خطأ في تحديث الإعداد: {e}")
        finally:
            conn.close()
    
    async def process_news(self):
        """معالجة الأخبار الجديدة وإرسالها إلى القناة والمجموعة"""
        if not self.broadcast_enabled:
            return
        
        enabled = self.get_setting('enabled', 'true')
        if enabled != 'true':
            return
        
        try:
            feed = feedparser.parse(self.rss_url)
            
            if not feed.entries:
                logger.warning("⚠️ لا توجد أخبار في التغذية")
                return
            
            latest_entry = feed.entries[0]
            
            # التحقق من أن الخبر جديد
            if latest_entry.guid in self.processed_news:
                return
            
            # التحقق من أن الخبر حديث
            max_age_hours = int(self.get_setting('max_age_hours', '24'))
            if not self.is_news_recent(latest_entry.published, max_age_hours):
                logger.info("⏰ الخبر قديم، تم تخطيه")
                return
            
            title = latest_entry.title
            link = latest_entry.link
            published = latest_entry.published
            
            # استخراج العملة المتأثرة
            currency_tag = self.get_currency_tag(title)
            
            # تنسيق الرسالة
            message = self.format_news_message(title, link, published, currency_tag)
            
            # إرسال إلى القناة
            if self.channel_id:
                self.send_telegram_message(self.channel_id, message)
                self.save_sent_news(latest_entry.guid, title, link, published, currency_tag, self.channel_id)
            
            # إرسال إلى المجموعة
            if self.chat_group_id:
                self.send_telegram_message(self.chat_group_id, message)
                self.save_sent_news(latest_entry.guid, title, link, published, currency_tag, self.chat_group_id)
            
            logger.info(f"✅ تم نشر خبر جديد في القناة والمجموعة: {title[:50]}...")
            
        except Exception as e:
            logger.error(f"⚠️ خطأ في معالجة الأخبار: {e}")
    
    async def start_radar(self):
        """بدء تشغيل الرادار"""
        logger.info("📡 رادار Obeida Trading يعمل الآن...")
        
        check_interval = int(self.get_setting('check_interval', '300'))
        
        while True:
            try:
                await self.process_news()
            except Exception as e:
                logger.error(f"⚠️ خطأ في دورة الرادار: {e}")
            
            await asyncio.sleep(check_interval)
    
    def get_stats(self) -> Dict:
        """الحصول على إحصائيات الرادار"""
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        stats = {}
        
        try:
            cursor.execute("SELECT COUNT(*) FROM sent_news")
            stats['total_news'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM sent_news WHERE date(sent_at) = date('now')")
            stats['today_news'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT currency_tag, COUNT(*) FROM sent_news GROUP BY currency_tag ORDER BY COUNT(*) DESC LIMIT 5")
            stats['top_currencies'] = cursor.fetchall()
            
        except Exception as e:
            logger.error(f"⚠️ خطأ في جلب الإحصائيات: {e}")
        finally:
            conn.close()
        
        stats['broadcast_enabled'] = self.broadcast_enabled
        stats['enabled'] = self.get_setting('enabled', 'true')
        stats['check_interval'] = self.get_setting('check_interval', '300')
        
        return stats


# ============================================
# نظام التحكم بالتحليلات المجانية
# ============================================

class FreeAnalyticsSystem:
    def __init__(self):
        self.free_analytics_enabled = True
        self.default_free_count = 3
        self.max_free_count = 10
        
    def init_db(self):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        # إضافة جدول إعدادات التحليلات المجانية
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS free_analytics_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE,
                setting_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_by INTEGER
            )
        ''')
        
        # إضافة الإعدادات الافتراضية
        cursor.execute("SELECT COUNT(*) FROM free_analytics_settings")
        if cursor.fetchone()[0] == 0:
            cursor.execute('''
                INSERT INTO free_analytics_settings (setting_key, setting_value, updated_by)
                VALUES 
                ('enabled', 'true', ?),
                ('default_count', '3', ?),
                ('max_count', '10', ?)
            ''', (ADMIN_ID, ADMIN_ID, ADMIN_ID))
        
        conn.commit()
        conn.close()
    
    def get_settings(self):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT setting_key, setting_value FROM free_analytics_settings")
            settings = dict(cursor.fetchall())
            
            return {
                'enabled': settings.get('enabled', 'true').lower() == 'true',
                'default_count': int(settings.get('default_count', '3')),
                'max_count': int(settings.get('max_count', '10'))
            }
        except:
            return {'enabled': True, 'default_count': 3, 'max_count': 10}
        finally:
            conn.close()
    
    def set_setting(self, key, value, updated_by=ADMIN_ID):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO free_analytics_settings (setting_key, setting_value, updated_at, updated_by)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?)
            ''', (key, str(value).lower() if key == 'enabled' else str(value), updated_by))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    def check_free_analytics_allowed(self, user_id):
        if user_id == ADMIN_ID:
            return True, 999
        
        settings = self.get_settings()
        if not settings['enabled']:
            return False, 0
        
        user_info = SubscriptionSystem.get_user_info(user_id)
        if user_info and user_info.get('free_analyses', 0) > 0:
            return True, user_info['free_analyses']
        
        return False, 0

# إنشاء كائنات الأنظمة الجديدة
free_analytics = FreeAnalyticsSystem()
news_radar = ObeidaNewsRadar(TOKEN)

# ============================================
# نظام المناطق الزمنية المتقدم (GMT Format مع الدول)
# ============================================

TIMEZONES_BY_GMT = {
    "GMT-12:00": {
        "name": "خط التاريخ الدولي",
        "countries": ["جزر بيكر", "جزيرة هاولاند"]
    },
    "GMT-11:00": {
        "name": "ساموا",
        "countries": ["ساموا", "ساموا الأمريكية", "نييوي"]
    },
    "GMT-10:00": {
        "name": "هاواي",
        "countries": ["هاواي (الولايات المتحدة)", "جزر كوك", "تاهيتي"]
    },
    "GMT-09:00": {
        "name": "ألاسكا",
        "countries": ["ألاسكا (الولايات المتحدة)"]
    },
    "GMT-08:00": {
        "name": "المحيط الهادئ",
        "countries": ["لوس أنجلوس (الولايات المتحدة)", "فانكوفر (كندا)", "تيخوانا (المكسيك)"]
    },
    "GMT-07:00": {
        "name": "المنطقة الجبلية",
        "countries": ["دنفر (الولايات المتحدة)", "إدمونتون (كندا)", "فينيكس (الولايات المتحدة)"]
    },
    "GMT-06:00": {
        "name": "المنطقة الوسطى",
        "countries": ["شيكاغو (الولايات المتحدة)", "مدينة مكسيكو", "وينيبيغ (كندا)", "كوستاريكا"]
    },
    "GMT-05:00": {
        "name": "المنطقة الشرقية",
        "countries": ["نيويورك (الولايات المتحدة)", "تورونتو (كندا)", "ليما (بيرو)", "بوغوتا (كولومبيا)", "بوسطن (الولايات المتحدة)"]
    },
    "GMT-04:00": {
        "name": "الأطلسي",
        "countries": ["سانتياغو (تشيلي)", "أسونسيون (باراغواي)", "هاليفاكس (كندا)", "كاراكاس (فنزويلا)"]
    },
    "GMT-03:00": {
        "name": "الأرجنتين",
        "countries": ["بوينس آيرس (الأرجنتين)", "ساو باولو (البرازيل)", "مونتيفيديو (أوروغواي)", "جرينلاند"]
    },
    "GMT-02:00": {
        "name": "المحيط الأطلسي الأوسط",
        "countries": ["جزر فوكلاند", "ساندويتش الجنوبية"]
    },
    "GMT-01:00": {
        "name": "الأزور",
        "countries": ["جزر الأزور (البرتغال)", "الرأس الأخضر"]
    },
    "GMT+00:00": {
        "name": "توقيت غرينتش",
        "countries": ["لندن (بريطانيا)", "دبلن (أيرلندا)", "لشبونة (البرتغال)", "الدار البيضاء (المغرب)", "نواكشوط (موريتانيا)", "أكرا (غانا)", "دكار (السنغال)"]
    },
    "GMT+01:00": {
        "name": "أوروبا الوسطى",
        "countries": ["برلين (ألمانيا)", "باريس (فرنسا)", "مدريد (إسبانيا)", "روما (إيطاليا)", "فيينا (النمسا)", "بروكسل (بلجيكا)", "أمستردام (هولندا)", "وارسو (بولندا)", "براغ (التشيك)", "الجزائر", "تونس", "طرابلس (ليبيا)"]
    },
    "GMT+02:00": {
        "name": "أوروبا الشرقية",
        "countries": ["القاهرة (مصر)", "الرياض (السعودية)", "القدس (فلسطين)", "عمّان (الأردن)", "بيروت (لبنان)", "دمشق (سوريا)", "بغداد (العراق)", "الكويت", "الدوحة (قطر)", "المنامة (البحرين)", "أثينا (اليونان)", "اسطنبول (تركيا)", "هلسنكي (فنلندا)", "صوفيا (بلغاريا)", "بوخارست (رومانيا)", "كييف (أوكرانيا)", "كالينينغراد (روسيا)"]
    },
    "GMT+03:00": {
        "name": "العربية/موسكو",
        "countries": ["موسكو (روسيا)", "جدة (السعودية)", "أبوظبي (الإمارات)", "الدوحة (قطر)", "البحرين", "الكويت", "بغداد (العراق)", "صنعاء (اليمن)", "مسقط (عُمان)", "أنقرة (تركيا)", "دمشق (سوريا)", "عَمَّان (الأردن)", "بيروت (لبنان)", "القدس (فلسطين)", "نيروبي (كينيا)", "دار السلام (تنزانيا)", "أديس أبابا (إثيوبيا)", "مقديشو (الصومال)"]
    },
    "GMT+03:30": {
        "name": "إيران",
        "countries": ["طهران (إيران)"]
    },
    "GMT+04:00": {
        "name": "الخليج",
        "countries": ["دبي (الإمارات)", "مسقط (عُمان)", "باكو (أذربيجان)", "تبليسي (جورجيا)", "يريفان (أرمينيا)", "موريشيوس", "سيشل"]
    },
    "GMT+04:30": {
        "name": "أفغانستان",
        "countries": ["كابول (أفغانستان)"]
    },
    "GMT+05:00": {
        "name": "باكستان",
        "countries": ["كراتشي (باكستان)", "طشقند (أوزبكستان)", "عشق آباد (تركمانستان)", "دوشنبه (طاجيكستان)", "يكاترينبورغ (روسيا)"]
    },
    "GMT+05:30": {
        "name": "الهند",
        "countries": ["دلهي (الهند)", "مومباي (الهند)", "كولمبو (سريلانكا)"]
    },
    "GMT+05:45": {
        "name": "نيبال",
        "countries": ["كاتماندو (نيبال)"]
    },
    "GMT+06:00": {
        "name": "بنغلاديش",
        "countries": ["دكا (بنغلاديش)", "ألماتي (كازاخستان)", "بيشكك (قرغيزستان)", "أومسك (روسيا)"]
    },
    "GMT+06:30": {
        "name": "ميانمار",
        "countries": ["يانغون (ميانمار)"]
    },
    "GMT+07:00": {
        "name": "تايلاند",
        "countries": ["بانكوك (تايلاند)", "جاكرتا (إندونيسيا)", "هانوي (فيتنام)", "نوفوسيبيرسك (روسيا)"]
    },
    "GMT+08:00": {
        "name": "الصين",
        "countries": ["بكين (الصين)", "سنغافورة", "كوالالمبور (ماليزيا)", "تايبيه (تايوان)", "مانيلا (الفلبين)", "بيرث (أستراليا)", "كراسنويارسك (روسيا)"]
    },
    "GMT+09:00": {
        "name": "اليابان",
        "countries": ["طوكيو (اليابان)", "سول (كوريا الجنوبية)", "بيونغيانغ (كوريا الشمالية)", "ياكوتسك (روسيا)"]
    },
    "GMT+09:30": {
        "name": "أستراليا الوسطى",
        "countries": ["أديلايد (أستراليا)", "داروين (أستراليا)"]
    },
    "GMT+10:00": {
        "name": "أستراليا الشرقية",
        "countries": ["سيدني (أستراليا)", "ملبورن (أستراليا)", "بريسبان (أستراليا)", "فلاديفوستوك (روسيا)"]
    },
    "GMT+11:00": {
        "name": "جزر سليمان",
        "countries": ["هونيارا (جزر سليمان)", "نوميا (كاليدونيا الجديدة)", "بورت فيلا (فانواتو)"]
    },
    "GMT+12:00": {
        "name": "نيوزيلندا",
        "countries": ["ويلينغتون (نيوزيلندا)", "أوكلاند (نيوزيلندا)", "فيجي"]
    },
    "GMT+13:00": {
        "name": "تونغا",
        "countries": ["نوكو ألوفا (تونغا)", "أبيان (كيريباتي)"]
    },
    "GMT+14:00": {
        "name": "كيريباتي",
        "countries": ["كيريتيماتي (جزر لاين)"]
    }
}

COUNTRY_TIMEZONE_MAP = {
    "فلسطين": "GMT+02:00",
    "مصر": "GMT+02:00",
    "السعودية": "GMT+03:00",
    "الإمارات": "GMT+04:00",
    "الكويت": "GMT+03:00",
    "قطر": "GMT+03:00",
    "البحرين": "GMT+03:00",
    "عمان": "GMT+04:00",
    "اليمن": "GMT+03:00",
    "العراق": "GMT+03:00",
    "الأردن": "GMT+02:00",
    "لبنان": "GMT+02:00",
    "سوريا": "GMT+02:00",
    "ليبيا": "GMT+02:00",
    "تونس": "GMT+01:00",
    "الجزائر": "GMT+01:00",
    "المغرب": "GMT+00:00",
    "موريتانيا": "GMT+00:00",
    "السودان": "GMT+02:00",
    "الصومال": "GMT+03:00",
    "تركيا": "GMT+03:00",
    "إيران": "GMT+03:30",
    "باكستان": "GMT+05:00",
    "الهند": "GMT+05:30",
    "الصين": "GMT+08:00",
    "اليابان": "GMT+09:00",
    "كوريا الجنوبية": "GMT+09:00",
    "أستراليا (سيدني)": "GMT+10:00",
    "نيوزيلندا": "GMT+12:00",
    "بريطانيا": "GMT+00:00",
    "ألمانيا": "GMT+01:00",
    "فرنسا": "GMT+01:00",
    "إيطاليا": "GMT+01:00",
    "إسبانيا": "GMT+01:00",
    "روسيا (موسكو)": "GMT+03:00",
    "الولايات المتحدة (نيويورك)": "GMT-05:00",
    "الولايات المتحدة (شيكاغو)": "GMT-06:00",
    "الولايات المتحدة (دنفر)": "GMT-07:00",
    "الولايات المتحدة (لوس أنجلوس)": "GMT-08:00",
    "كندا (تورونتو)": "GMT-05:00",
    "البرازيل (ساو باولو)": "GMT-03:00",
    "الأرجنتين": "GMT-03:00",
    "تشيلي": "GMT-04:00",
    "كولومبيا": "GMT-05:00",
    "المكسيك": "GMT-06:00",
    "جنوب أفريقيا": "GMT+02:00",
    "كينيا": "GMT+03:00",
    "نيجيريا": "GMT+01:00"
}

# ============================================
# جلسات التداول (بالتوقيت العالمي)
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

# ============================================
# نظام الاشتراك المتطور
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
                free_analyses INTEGER DEFAULT 3,
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
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (added_by) REFERENCES users(user_id)
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
        
        # تهيئة الأنظمة الإضافية
        free_analytics.init_db()
        
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
                INSERT INTO users (
                    user_id, username, first_name, last_name, language_code, is_bot, free_analyses
                ) VALUES (?, ?, ?, ?, ?, ?, 3)
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
                conn.rollback()
                return False, "نوع اشتراك غير صالح"
            
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO users (user_id, free_analyses) VALUES (?, 3)", (user_id,))
            
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
            cursor.execute("""
                INSERT INTO active_subscriptions (
                    user_id, subscription_type, subscription_name, 
                    start_date, end_date, is_active, added_by
                ) VALUES (?, ?, ?, ?, ?, 1, ?)
            """, (user_id, subscription_type, sub_name, 
                  start_date.isoformat(), end_date.isoformat() if end_date else None, 
                  added_by))
            
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
            
            return True, sub_name, {
                'type': sub_type,
                'name': sub_name,
                'start_date': start_date,
                'end_date': end_date
            }
            
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
                SELECT user_id, username, first_name, created_at, last_active, is_blocked, free_analyses, total_analyses, timezone
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
                'free_analyses': user_data[6],
                'total_analyses': user_data[7],
                'timezone': user_data[8] or 'GMT+02:00',
                'subscription': sub_data[0] if sub_data else 'لا يوجد',
                'subscription_start': sub_data[1] if sub_data else None,
                'subscription_end': sub_data[2] if sub_data else None
            }
        except:
            return None
        finally:
            conn.close()
    
    @staticmethod
    def use_free_analysis(user_id):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE users 
                SET free_analyses = free_analyses - 1,
                    total_analyses = total_analyses + 1,
                    last_active = CURRENT_TIMESTAMP
                WHERE user_id = ? AND free_analyses > 0
            """, (user_id,))
            conn.commit()
            return cursor.rowcount > 0
        except:
            return False
        finally:
            conn.close()
    
    @staticmethod
    def reset_free_analyses(user_id, count=3):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("UPDATE users SET free_analyses = ? WHERE user_id = ?", (count, user_id))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    @staticmethod
    def set_free_analyses_count(user_id, count):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("UPDATE users SET free_analyses = ? WHERE user_id = ?", (count, user_id))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()
    
    @staticmethod
    def add_free_analyses(user_id, count=1, performed_by=ADMIN_ID):
        """إضافة عدد محدد من التحليلات المجانية لمستخدم"""
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE users 
                SET free_analyses = free_analyses + ?,
                    last_active = CURRENT_TIMESTAMP
                WHERE user_id = ?
            """, (count, user_id))
            
            if cursor.rowcount > 0:
                conn.commit()
                
                # تسجيل العملية
                log_data_change("add_free_analyses", user_id, f"added {count} free analyses by admin {performed_by}")
                
                # جلب العدد الجديد
                cursor.execute("SELECT free_analyses FROM users WHERE user_id = ?", (user_id,))
                new_count = cursor.fetchone()[0]
                
                return True, f"تم إضافة {count} تحليلات مجانية. العدد الجديد: {new_count}"
            else:
                # إذا المستخدم غير موجود، نضيفه أولاً
                cursor.execute("INSERT INTO users (user_id, free_analyses) VALUES (?, ?)", (user_id, count))
                conn.commit()
                return True, f"تم إنشاء مستخدم جديد وإضافة {count} تحليلات مجانية"
                
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
                status, scheduled_time, scheduled_action, session_control = result
                return {
                    'status': status,
                    'scheduled_time': scheduled_time,
                    'scheduled_action': scheduled_action,
                    'session_control': session_control
                }
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
    def get_all_users():
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT u.user_id, u.username, u.first_name, u.created_at, u.last_active, u.is_blocked,
                       u.free_analyses, u.total_analyses, u.timezone,
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
                SELECT COUNT(DISTINCT user_id) FROM users 
                WHERE last_active > datetime('now', '-7 day')
            """)
            stats['active_week'] = cursor.fetchone()[0]
            
            cursor.execute("""
                SELECT COUNT(*) FROM active_subscriptions 
                WHERE is_active = 1 AND end_date IS NOT NULL 
                AND end_date < datetime('now', '+7 day')
            """)
            stats['expiring_soon'] = cursor.fetchone()[0]
            
            # إضافة إحصائيات التحليلات المجانية
            cursor.execute("SELECT COUNT(*) FROM users WHERE free_analyses > 0")
            stats['users_with_free'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT SUM(free_analyses) FROM users")
            stats['total_free_remaining'] = cursor.fetchone()[0] or 0
            
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
    def revoke_subscription(user_id, reason="Revoked by admin", performed_by=ADMIN_ID):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                UPDATE active_subscriptions 
                SET is_active = 0
                WHERE user_id = ? AND is_active = 1
            """, (user_id,))
            conn.commit()
            return True, "تم إلغاء الاشتراك بنجاح"
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
    def log_session_activity(session_name, active_users, analyses_count, success_rate):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            now = datetime.now()
            cursor.execute("""
                INSERT INTO session_logs (session_name, start_time, end_time, active_users, analyses_count, success_rate)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_name, now, now + timedelta(hours=8), active_users, analyses_count, success_rate))
            conn.commit()
        except:
            pass
        finally:
            conn.close()
    
    @staticmethod
    def get_session_stats(session_name):
        conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT AVG(active_users), AVG(analyses_count), AVG(success_rate)
                FROM session_logs
                WHERE session_name = ? AND created_at > datetime('now', '-30 day')
            """, (session_name,))
            return cursor.fetchone()
        except:
            return (0, 0, 0)
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

def check_user_subscription(user_id):
    if user_id == ADMIN_ID:
        return True, "مشرف"
    
    status, message, details = SubscriptionSystem.check_subscription(user_id)
    return status, message

# ============================================
# دوال مساعدة
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
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_automation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key TEXT UNIQUE,
            setting_value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (ADMIN_ID,))
    admin_exists = cursor.fetchone()
    
    if admin_exists:
        cursor.execute("UPDATE users SET is_admin = 1 WHERE user_id = ?", (ADMIN_ID,))
    else:
        cursor.execute("INSERT INTO users (user_id, username, is_admin, timezone) VALUES (?, ?, ?, ?)", 
                      (ADMIN_ID, "Sz2zv", 1, "GMT+02:00"))
    
    conn.commit()
    conn.close()
    
    print("✅ Database initialized with timezone support")
    SubscriptionSystem.init_db()

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

def get_user_local_session(user_id):
    try:
        user_info = SubscriptionSystem.get_user_info(user_id)
        if user_info and user_info.get('timezone'):
            tz_str = user_info['timezone']
            
            if tz_str.startswith('GMT'):
                offset_str = tz_str.replace('GMT', '')
                if offset_str and offset_str[0] in ('+', '-'):
                    hours = int(offset_str.split(':')[0])
                    minutes = int(offset_str.split(':')[1]) if ':' in offset_str else 0
                    timezone = pytz.FixedOffset(hours * 60 + (minutes if hours >= 0 else -minutes))
                    local_time = datetime.now(timezone)
                    local_hour = local_time.hour
                    
                    for session_key, session in TRADING_SESSIONS.items():
                        if session['start'] <= local_hour < session['end'] or (session['end'] == 24 and local_hour >= session['start']):
                            return session
    except:
        pass
    
    return get_current_session()

def is_session_allowed(session_control, current_session):
    if session_control == 'all':
        return True
    
    session_map = {
        'asian': TRADING_SESSIONS['asian']['name'],
        'london': TRADING_SESSIONS['london']['name'],
        'newyork': TRADING_SESSIONS['newyork']['name'],
        'pacific': TRADING_SESSIONS['pacific']['name']
    }
    
    if session_control in session_map:
        return session_map[session_control] == current_session['name']
    
    return True

def get_user_timezone(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'timezone' not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'GMT+02:00'")
        conn.commit()
    
    cursor.execute("SELECT timezone FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result and result[0]:
        return result[0]
    return "GMT+02:00"

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
    
    log_data_change("update_timezone", user_id, f"timezone={timezone}")
    return True

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
            else:
                return datetime.now(pytz.UTC)
        else:
            user_tz = pytz.timezone(timezone_str)
            return datetime.now(user_tz)
    except Exception as e:
        print(f"⚠️ خطأ في الحصول على الوقت المحلي: {e}")
        return datetime.now()

def log_data_change(action, user_id, details=""):
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
                    except Exception as e:
                        print(f"⚠️ خطأ في حذف الملف {filename}: {e}")
        
        if deleted_count > 0:
            print(f"✅ تم حذف {deleted_count} ملف مؤقت قديم")
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف الصور القديمة: {e}")

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
        print(f"⚠️ خطأ في ضغط الصورة: {e}")
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
    
    log_data_change("update_setting", user_id, f"{col}={val}")

def get_user_setting(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT candle, trade_time, last_analysis_context, last_analysis_time FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    if res:
        return res
    return ("M1", "قصير (1m-15m)", "", None)

def save_analysis_context(user_id, analysis_text):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    summary = analysis_text[:500]
    cursor.execute("UPDATE users SET last_analysis_context = ?, last_analysis_time = CURRENT_TIMESTAMP WHERE user_id = ?", (summary, user_id))
    conn.commit()
    conn.close()
    log_data_change("save_analysis", user_id, "analysis saved")

def get_analysis_context(user_id):
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
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        month_ago = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("DELETE FROM chat_history WHERE timestamp < ?", (month_ago,))
        
        two_months_ago = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("DELETE FROM data_log WHERE timestamp < ?", (two_months_ago,))
        
        conn.commit()
        conn.close()
            
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف قاعدة البيانات: {e}")

def format_trade_time_for_prompt(trade_time):
    if trade_time == "قصير (1m-15m)":
        return "مدة الصفقة المتوقعة: قصير الأجل (1 دقيقة إلى 15 دقيقة) - تنفيذ سريع، مخاطر منخفضة"
    elif trade_time == "متوسط (4h-Daily)":
        return "مدة الصفقة المتوقعة: متوسط الأجل (4 ساعات إلى يومي) - انتظار أيام، مخاطر متوسطة"
    elif trade_time == "طويل (Weekly-Monthly)":
        return "مدة الصفقة المتوقعة: طويل الأجل (أسبوعي إلى شهري) - استثمار طويل، مخاطر مرتفعة"
    else:
        return f"مدة الصفقة المتوقعة: {trade_time}"

def encode_image(image_path):
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
        return encoded_string
    except Exception as e:
        print(f"Error encoding image: {e}")
        return None

def clean_repeated_text(text):
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
    text = clean_repeated_text(text)
    text = re.sub(r'```[\w]*\n?', '', text)
    text = re.sub(r'```\n?', '', text)
    formatted_text = f"```{language}\n{text}\n```"
    return formatted_text

def split_message_with_copy_support(text, max_length=3500):
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
            part += "\n━━━━━━━━━━━━━\n[يتبع في الجزء التالي...]"
        formatted_parts.append(format_as_copyable_code(part, "text"))
    
    return formatted_parts

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

def cleanup_user_data(context: ContextTypes.DEFAULT_TYPE, user_id: int = None, immediate_delete: bool = True):
    try:
        deleted_files = 0
        if user_id and os.path.exists(IMAGE_CACHE_DIR):
            try:
                for filename in os.listdir(IMAGE_CACHE_DIR):
                    if f"_{user_id}_" in filename or filename.startswith(f"img_{user_id}_"):
                        filepath = os.path.join(IMAGE_CACHE_DIR, filename)
                        if os.path.exists(filepath):
                            try:
                                os.remove(filepath)
                                deleted_files += 1
                            except Exception as e:
                                print(f"⚠️ خطأ في حذف الملف {filename}: {e}")
            except Exception as e:
                print(f"⚠️ خطأ في تنظيف ملفات المستخدم {user_id}: {e}")
        
        keys_to_remove = ['temp_image_path', 'compressed_image_path']
        
        for key in keys_to_remove:
            if key in context.user_data:
                del context.user_data[key]
        
        if deleted_files > 0:
            print(f"✅ تنظيف المستخدم {user_id}: {deleted_files} ملف")
                
    except Exception as e:
        print(f"⚠️ خطأ في تنظيف الذاكرة: {e}")

# ============================================
# نظام الأتمتة المتقدم
# ============================================

class AutomationSystem:
    @staticmethod
    def auto_switch_session():
        try:
            current_session = get_current_session()
            bot_status = SubscriptionSystem.get_bot_status()
            
            if bot_status['session_control'] != 'all':
                session_key = bot_status['session_control']
                allowed_session = TRADING_SESSIONS.get(session_key, {}).get('name')
                
                if allowed_session and allowed_session != current_session['name']:
                    print(f"🔄 تبديل الجلسة تلقائياً: {allowed_session} ← {current_session['name']}")
                    SubscriptionSystem.set_bot_status(
                        status='active',
                        session_control='all',
                        updated_by=ADMIN_ID
                    )
                    
                    AutomationSystem.send_session_notification(current_session)
        except Exception as e:
            print(f"⚠️ خطأ في التبديل التلقائي للجلسة: {e}")
    
    @staticmethod
    def send_session_notification(session):
        try:
            conn = sqlite3.connect(SUBSCRIPTION_DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE is_blocked = 0")
            users = cursor.fetchall()
            conn.close()
            
            notification = (
                f"🔄 ** الجلسة الحالية ** 🔄\n\n"
                f"{session['icon']} **الجلسة الحالية:** {session['name']}\n"
                f"⏰ **وقت الجلسة:** {session['time_range']}\n"
                f"📊 **التقلب:** {session['volatility']}\n"
                f"🎯 **نسبة النجاح:** {session['success_rate']}\n\n"
                f"✨ **المميزات:** {session['features']}\n\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"   🤖 Obeida Trading   "
            )
            
            return notification
        except:
            return None
    
    @staticmethod
    def get_system_health():
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            return {
                'cpu': cpu_percent,
                'memory_used': memory.percent,
                'memory_available': memory.available / (1024**3),
                'disk_used': disk.percent,
                'disk_free': disk.free / (1024**3),
                'status': 'healthy' if cpu_percent < 80 and memory.percent < 80 else 'warning'
            }
        except:
            return {
                'cpu': 0,
                'memory_used': 0,
                'memory_available': 0,
                'disk_used': 0,
                'disk_free': 0,
                'status': 'unknown'
            }
    
    @staticmethod
    def auto_backup():
        try:
            DataProtection.backup_database()
            DataProtection.backup_codes_db()
            DataProtection.backup_radar_db()
            print("💾 تم إجراء النسخ الاحتياطي التلقائي")
        except Exception as e:
            print(f"⚠️ خطأ في النسخ الاحتياطي التلقائي: {e}")

# ============================================
# دوال معالجة البوت
# ============================================

async def check_bot_status(update: Update) -> bool:
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        return True
    
    bot_status = SubscriptionSystem.get_bot_status()
    
    if bot_status['status'] == 'maintenance':
        await update.message.reply_text(
            "🔧 **البوت تحت الصيانة حاليًا** 🔧\n\n"
            "سيتم العودة قريبًا. شكرًا لتفهمك.",
            parse_mode="Markdown"
        )
        return False
    
    elif bot_status['status'] == 'stopped':
        await update.message.reply_text(
            "⏸️ **البوت متوقف حاليًا** ⏸️\n\n"
            "يرجى المحاولة لاحقًا.",
            parse_mode="Markdown"
        )
        return False
    
    elif bot_status['status'] == 'scheduled':
        if bot_status['scheduled_time'] and bot_status['scheduled_action']:
            try:
                scheduled_time = datetime.fromisoformat(bot_status['scheduled_time'])
                if datetime.now() < scheduled_time:
                    remaining = scheduled_time - datetime.now()
                    minutes = int(remaining.total_seconds() / 60)
                    
                    if bot_status['scheduled_action'] == 'stop':
                        await update.message.reply_text(
                            f"⏰ **البوت سيتوقف بعد {minutes} دقيقة** ⏰\n\n"
                            f"يرجى إنهاء تحليلاتك الحالية.",
                            parse_mode="Markdown"
                        )
                    elif bot_status['scheduled_action'] == 'maintenance':
                        await update.message.reply_text(
                            f"🔧 **البوت سيدخل الصيانة بعد {minutes} دقيقة** 🔧",
                            parse_mode="Markdown"
                        )
            except:
                pass
    
    user_local_session = get_user_local_session(user_id)
    if not is_session_allowed(bot_status['session_control'], user_local_session):
        await update.message.reply_text(
            f"⏳ **غير مسموح بالتداول في هذه الجلسة** ⏳\n\n"
            f"الجلسة الحالية: {user_local_session['name']}\n"
            f"الجلسات المسموحة: {bot_status['session_control']}\n\n"
            f"الرجاء الانتظار للجلسة المناسبة.",
            parse_mode="Markdown"
        )
        return False
    
    return True

async def check_analysis_limit(update: Update, user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    
    status, _, details = SubscriptionSystem.check_subscription(user_id)
    if status:
        return True
    
    # التحقق من تفعيل التحليلات المجانية
    allowed, remaining = free_analytics.check_free_analytics_allowed(user_id)
    if allowed and remaining > 0:
        return True
    
    user_info = SubscriptionSystem.get_user_info(user_id)
    free_settings = free_analytics.get_settings()
    
    await update.message.reply_text(
        "⚠️ **لقد استنفدت تحليلاتك المجانية** ⚠️\n\n"
        f"لديك {user_info['free_analyses'] if user_info else 0} تحليل متبقي.\n\n"
        f"🔑 **للحصول على المزيد من التحليلات:**\n"
        f"• اشترك في البوت للحصول على تحليلات غير محدودة\n"
        f"• تواصل مع المشرف: @Sz2zv\n\n"
        f"📊 **أسعار الاشتراكات:**\n"
        f"• ساعة: 1$\n"
        f"• يوم: 5$\n"
        f"• أسبوعي: 15$\n"
        f"• شهري: 40$",
        parse_mode="Markdown"
    )
    return False

async def subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        return True
    
    user = update.effective_user
    SubscriptionSystem.register_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        is_bot=user.is_bot
    )
    
    if not await check_bot_status(update):
        return False
    
    status, message, details = SubscriptionSystem.check_subscription(user_id)
    
    if not status:
        # التحقق من التحليلات المجانية
        allowed, remaining = free_analytics.check_free_analytics_allowed(user_id)
        if allowed and remaining > 0:
            return True
        
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
    
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    
    SubscriptionSystem.register_user(
        user_id=user_id,
        username=username,
        first_name=user.first_name,
        last_name=user.last_name,
        language_code=user.language_code,
        is_bot=user.is_bot
    )
    
    cleanup_user_data(context, user_id)
    
    user_info = SubscriptionSystem.get_user_info(user_id)
    status, sub_name, details = SubscriptionSystem.check_subscription(user_id)
    free_settings = free_analytics.get_settings()
    
    current_session = get_user_local_session(user_id)
    utc_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    user_tz = get_user_timezone(user_id)
    local_time = get_localized_time(user_id)
    local_time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
    
    free_status = "✅ مفعلة" if free_settings['enabled'] else "❌ معطلة"
    
    welcome_msg = (
        "🚀 **أهلاً بك في Obeida Trading** 🚀\n\n"
        f"🆔 **معرفك:** `{user_id}`\n"
        f"👤 **اليوزر:** @{username}\n\n"
        f"📊 **حالة الاشتراك:** {'✅ ' + sub_name if status else '❌ غير مشترك'}\n"
        f"🔢 **التحليلات المجانية المتبقية:** {user_info['free_analyses'] if user_info else 3}\n"
        f"📈 **إجمالي التحليلات:** {user_info['total_analyses'] if user_info else 0}\n"
        f"🔓 **حالة التحليلات المجانية:** {free_status}\n\n"
    )
    
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["📊 معلومات الجلسة", "👤 معلوماتي"],
        ["🌍 المناطق الزمنية"]
    ]
    
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
    
    if user_id != ADMIN_ID and not await check_bot_status(update):
        return MAIN_MENU
    
    if user_message == "🌍 المناطق الزمنية":
        return await timezone_menu(update, context)
    
    elif user_message == "👑 لوحة التحكم" and user_id == ADMIN_ID:
        return await start_admin_panel(update, context)
    
    elif user_message == "📊 معلومات الجلسة":
        current_session = get_user_local_session(user_id)
        utc_session = get_current_session()
        utc_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        user_tz = get_user_timezone(user_id)
        local_time = get_localized_time(user_id)
        local_time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
        
        session_info = (
            f"🌐 **معلومات الجلسة والوقت**\n\n"
            f"⏰ **التوقيت العالمي (UTC):** {utc_time}\n"
            f"🌍 **منطقتك الزمنية:** {user_tz}\n"
            f"🕒 **وقتك المحلي:** {local_time_str}\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"📍 **جلسة الحالية:** {utc_session['name']}\n"
            f"📍 **جلسة منطقتك:** {current_session['name']}\n"
            f"📊 **التقلب:** {current_session['volatility']}\n"
            f"🎯 **نسبة النجاح المتوقعة:** {current_session['success_rate']}\n"
            f"✨ **المميزات:** {current_session['features']}\n\n"
            f"━━━━━━━━━━━━━━━━\n\n"
            f"**الجلسات الأخرى:**\n\n"
            f"🌏 الآسيوية (00:00-08:00 UTC): 65-70%\n"
            f"🇬🇧 لندن (08:00-16:00 UTC): 75-85%\n"
            f"🗽 نيويورك (13:00-21:00 UTC): 80-90%\n"
            f"🌊 المحيط الهادئ (21:00-00:00 UTC): 55-65%"
        )
        
        await update.message.reply_text(session_info, parse_mode="Markdown")
        return MAIN_MENU
    
    elif user_message == "👤 معلوماتي":
        user_info = SubscriptionSystem.get_user_info(user_id)
        status, sub_name, details = SubscriptionSystem.check_subscription(user_id)
        user_tz = get_user_timezone(user_id)
        local_time = get_localized_time(user_id)
        local_time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
        
        if not user_info:
            user_info = {'free_analyses': 3, 'total_analyses': 0, 'created_at': None, 'last_active': None, 'timezone': 'GMT+02:00'}
        
        info_msg = (
            f"👤 **معلومات المستخدم**\n\n"
            f"🆔 **المعرف:** `{user_id}`\n"
            f"👤 **اليوزر:** @{update.effective_user.username or 'لا يوجد'}\n"
            f"📝 **الاسم:** {update.effective_user.first_name or ''} {update.effective_user.last_name or ''}\n"
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
            f"━━━━━━━━━━━━━━━━\n\n"
            f"🔢 **التحليلات المجانية المتبقية:** {user_info['free_analyses']}\n"
            f"📈 **إجمالي التحليلات:** {user_info['total_analyses']}\n"
            f"🌍 **منطقتك الزمنية:** {user_tz}\n"
            f"🕒 **وقتك المحلي:** {local_time_str}\n"
            f"📅 **تاريخ التسجيل:** {user_info['created_at'][:16] if user_info.get('created_at') else 'غير معروف'}\n"
            f"🕒 **آخر نشاط:** {user_info['last_active'][:16] if user_info.get('last_active') else 'غير معروف'}"
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
        candle, trade_time, _, _ = get_user_setting(user_id)
        
        if not candle or not trade_time:
            keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"], ["🌍 المناطق الزمنية"]]
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
                f"⚙️ الإعدادات المستخدمة:\n"
                f"• سرعة الشموع: {candle}\n"
                f"• استراتيجية: {time_display}\n\n"
                f"📤 قم برفع صورة الشارت الآن:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return ANALYZE_MODE
    
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
        
        keyboard = [["📊 تحليل صورة"], ["📊 معلومات الجلسة"], ["👤 معلوماتي"], ["🌍 المناطق الزمنية"], ["الرجوع للقائمة الرئيسية"]]
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
    
    if user_id != ADMIN_ID and not await check_bot_status(update):
        return MAIN_MENU
    
    if not await check_analysis_limit(update, user_id):
        return MAIN_MENU
    
    candle, trade_time, prev_context, prev_time = get_user_setting(user_id)
    
    if not candle or not trade_time:
        keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"], ["🌍 المناطق الزمنية"]]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**\n\n"
            "الرجاء استخدام أزرار القائمة لضبط الإعدادات قبل تحليل الصور.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    wait_msg = await update.message.reply_text("📸 تم استلام الصورة بنجاح")
    photo = await update.message.photo[-1].get_file()
    
    timestamp = int(time.time())
    original_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}_original.jpg")
    compressed_path = os.path.join(IMAGE_CACHE_DIR, f"img_{user_id}_{timestamp}_compressed.jpg")
    
    try:
        await photo.download_to_drive(original_path)
        compressed_path = compress_image(original_path)
        
        analysis_result = await analyze_chart_image(
            update, 
            context, 
            original_path, 
            candle, 
            trade_time, 
            "الشارت المرفق"
        )
        
        if user_id != ADMIN_ID:
            status, _, _ = SubscriptionSystem.check_subscription(user_id)
            if not status:
                SubscriptionSystem.use_free_analysis(user_id)
        
    except Exception as e:
        print(f"❌ خطأ في تحليل الصورة: {traceback.format_exc()}")
        error_msg = f"❌ حدث خطأ في تحليل الصورة: {str(e)[:200]}\nيرجى المحاولة مرة أخرى."
        
        # تجنب مشكلة Markdown في رسالة الخطأ
        safe_error = escape_markdown(error_msg)
        try:
            await wait_msg.edit_text(safe_error)
        except:
            await update.message.reply_text(safe_error)
    finally:
        for filepath in [original_path, compressed_path]:
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception as e:
                    print(f"⚠️ خطأ في حذف الملف المؤقت: {e}")
    
    return MAIN_MENU

async def analyze_chart_image(update, context, image_path, candle, trade_time, symbol):
    try:
        user_id = update.effective_user.id
        prev_context, prev_time = get_analysis_context(user_id)
        
        # إرسال رسالة انتظار
        wait_msg = await update.message.reply_text("🤔 جاري تحليل الصورة...")
        
        compressed_path = compress_image(image_path)
        base64_img = encode_image(compressed_path)
        
        if not base64_img:
            try:
                await wait_msg.edit_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            except Exception as e:
                if "Message is not modified" not in str(e):
                    await update.message.reply_text("❌ **خطأ في قراءة الصورة.**\nيرجى إرسال صورة واضحة.")
            return "❌ **خطأ في قراءة الصورة.**"
        
        # ============================================
        # 1. جمع بيانات التحليل الأساسية
        # ============================================
        current_session = get_current_session()
        user_local_session = get_user_local_session(user_id)
        current_time = datetime.utcnow()
        current_hour = current_time.hour
        current_minute = current_time.minute
        current_second = current_time.second
        
        user_tz = get_user_timezone(user_id)
        local_time = get_localized_time(user_id)
        
        # حساب الوقت المتبقي
        seconds_remaining = 60 - current_second
        if candle.startswith('S'):
            candle_seconds = int(candle[1:]) if candle[1:].isdigit() else 5
            seconds_remaining = (candle_seconds) - ((current_second) % candle_seconds)
        elif candle.startswith('M'):
            candle_minutes = int(candle[1:]) if candle[1:].isdigit() else 1
            seconds_remaining = (candle_minutes * 60) - ((current_minute % candle_minutes) * 60 + current_second)
        elif candle.startswith('H'):
            candle_hours = int(candle[1:]) if candle[1:].isdigit() else 1
            minutes_passed = current_time.hour % candle_hours * 60 + current_minute
            seconds_remaining = (candle_hours * 3600) - (minutes_passed * 60 + current_second)

        # تحديث رسالة الانتظار
        try:
            await wait_msg.edit_text("🔍 انتظار قليلآ جاري تحليل 🤌🤌🤌")
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"⚠️ خطأ في تحديث الرسالة: {e}")

        # ============================================
        # 2. تحليل Kill Zones المتقدم
        # ============================================
        class KillZonesAnalyzer:
            KILL_ZONES = {
                'asian': {'name': '🌏 المنطقة الآسيوية', 'start': 0, 'end': 8, 'liquidity': 'منخفضة', 'success': '45-55%', 'strategy': 'الارتداد من الأطراف', 'personality': 'نوم عميق - حركات بطيئة'},
                'london_open': {'name': '🇬🇧 افتتاح لندن', 'start': 8, 'end': 10, 'liquidity': 'عالية جداً', 'success': '80-90%', 'strategy': 'كسر الهيكل مع الاتجاه', 'personality': 'فيضان - كل شيء يتحرك بقوة'},
                'london_mid': {'name': '🇬🇧 منتصف لندن', 'start': 10, 'end': 12, 'liquidity': 'متوسطة', 'success': '65-75%', 'strategy': 'إعادة اختبار', 'personality': 'عمل منظم - تصحيحات'},
                'london_close': {'name': '🇬🇧 إغلاق لندن', 'start': 12, 'end': 13, 'liquidity': 'منخفضة', 'success': '50-60%', 'strategy': 'جني أرباح', 'personality': 'تعب - جني أرباح'},
                'ny_open': {'name': '🗽 افتتاح نيويورك', 'start': 13, 'end': 15, 'liquidity': 'عالية جداً', 'success': '85-95%', 'strategy': 'كسر مع زخم', 'personality': 'معركة - اندفاع قوي'},
                'ny_mid': {'name': '🗽 منتصف نيويورك', 'start': 15, 'end': 17, 'liquidity': 'مرتفعة', 'success': '75-85%', 'strategy': 'استمرار + تصحيح', 'personality': 'استمرار - تصحيحات عميقة'},
                'ny_close': {'name': '🗽 إغلاق نيويورك', 'start': 17, 'end': 18, 'liquidity': 'متوسطة', 'success': '55-65%', 'strategy': 'إغلاق صفقات', 'personality': 'تسوية مراكز'},
                'london_ny_overlap': {'name': '⚡ تداخل لندن-نيويورك', 'start': 13, 'end': 16, 'liquidity': 'عالية جداً', 'success': '90-98%', 'strategy': 'أقوى تحركات اليوم', 'personality': 'انفجار نووي'},
                'pacific': {'name': '🌊 المحيط الهادئ', 'start': 18, 'end': 24, 'liquidity': 'منخفضة جداً', 'success': '30-40%', 'strategy': 'تجنب التداول', 'personality': 'صحراء - لا حركة'}
            }
            
            @staticmethod
            def get_current_kill_zone():
                hour = datetime.utcnow().hour
                if 13 <= hour < 16:
                    return KillZonesAnalyzer.KILL_ZONES['london_ny_overlap']
                for key, zone in KillZonesAnalyzer.KILL_ZONES.items():
                    if zone['start'] <= hour < zone['end']:
                        return zone
                return KillZonesAnalyzer.KILL_ZONES['pacific']
            
            @staticmethod
            def should_trade_now():
                zone = KillZonesAnalyzer.get_current_kill_zone()
                good_zones = ['🇬🇧 افتتاح لندن', '🗽 افتتاح نيويورك', '⚡ تداخل لندن-نيويورك', '🇬🇧 منتصف لندن', '🗽 منتصف نيويورك']
                if any(gz in zone['name'] for gz in good_zones):
                    return {'trade': True, 'reason': f'✅ وقت ممتاز - {zone["name"]}', 'zone': zone}
                elif 'الآسيوية' in zone['name']:
                    return {'trade': 'limited', 'reason': '⚠️ تداول محدود - أزواج الين فقط', 'zone': zone}
                else:
                    return {'trade': False, 'reason': f'❌ وقت غير مناسب - {zone["name"]}', 'zone': zone}

        kill_zone = KillZonesAnalyzer.get_current_kill_zone()
        trade_decision = KillZonesAnalyzer.should_trade_now()

        # تحديث رسالة الانتظار
        try:
            await wait_msg.edit_text("🔍 انتظار قليلآ جاري تحليل 🤌🤌🤌")
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"⚠️ خطأ في تحديث الرسالة: {e}")

        # ============================================
        # 3. تحليل PO3 (المراحل الثلاث)
        # ============================================
        class PO3Analyzer:
            PHASES = {
                'accumulation': {'name': '🔄 التجميع', 'desc': 'تذبذب ممل لجمع العقود', 'action': 'مراقبة', 'psychology': 'الملل وعدم اليقين'},
                'manipulation': {'name': '🎭 التلاعب', 'desc': 'كسر كاذب لضرب الستوبات', 'action': 'انتظر العودة', 'psychology': 'الخوف والطمع - هذا فخ'},
                'distribution': {'name': '💥 التوزيع', 'desc': 'الانفجار السعري الحقيقي', 'action': 'ادخل مع الاتجاه', 'psychology': 'اليقين والثقة'}
            }
            
            @staticmethod
            def detect_current_phase():
                if 8 <= current_hour < 16:
                    return {'phase': 'distribution', 'phase_info': PO3Analyzer.PHASES['distribution'], 'confidence': 85}
                elif 0 <= current_hour < 8:
                    return {'phase': 'accumulation', 'phase_info': PO3Analyzer.PHASES['accumulation'], 'confidence': 70}
                else:
                    return {'phase': 'manipulation', 'phase_info': PO3Analyzer.PHASES['manipulation'], 'confidence': 60}

        po3_analysis = PO3Analyzer.detect_current_phase()

        # تحديث رسالة الانتظار
        try:
            await wait_msg.edit_text("🔍 انتظار قليلآ جاري تحليل 🤌🤌🤌")
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"⚠️ خطأ في تحديث الرسالة: {e}")

        # ============================================
        # 4. تحليل VSA (Volume Spread Analysis)
        # ============================================
        class VSAAnalyzer:
            @staticmethod
            def get_volume_status(ratio):
                if ratio > 2.5: return '💥 ضخم جداً (Climax)'
                if ratio > 1.8: return '🔥 كبير (High)'
                if ratio > 1.2: return '📊 فوق المتوسط'
                if ratio > 0.8: return '📈 متوسط'
                if ratio > 0.5: return '📉 ضعيف'
                return '💤 ضعيف جداً'
            
            @staticmethod
            def analyze_fake_breakout(volume_ratio, candle_body_ratio, wick_ratio):
                fake_signals = []
                if volume_ratio < 0.7:
                    fake_signals.append('حجم منخفض')
                if candle_body_ratio < 0.3:
                    fake_signals.append('جسم صغير')
                if wick_ratio > 0.4:
                    fake_signals.append('ذيل طويل')
                
                if len(fake_signals) >= 2:
                    return {
                        'is_fake': True,
                        'confidence': 90 + (len(fake_signals) * 3),
                        'reasons': fake_signals,
                        'action': 'عكس الاتجاه - هذا فخ'
                    }
                return {'is_fake': False}

        # قيم افتراضية - سيتم استبدالها بتحليل حقيقي من الصورة
        volume_ratio = 0.6
        candle_body_ratio = 0.25
        wick_ratio = 0.45
        vsa_analysis = VSAAnalyzer.analyze_fake_breakout(volume_ratio, candle_body_ratio, wick_ratio)
        volume_status = VSAAnalyzer.get_volume_status(volume_ratio)

        # تحديث رسالة الانتظار
        try:
            await wait_msg.edit_text("🔍 انتظار قليلآ جاري تحليل 🤌🤌🤌")
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"⚠️ خطأ في تحديث الرسالة: {e}")

        # ============================================
        # 5. تحليل Fibonacci OTE
        # ============================================
        class InstitutionalFibonacci:
            @staticmethod
            def calculate_ote(high, low, price):
                diff = high - low
                ote_low = low + (diff * 0.68)
                ote_high = low + (diff * 0.82)
                in_ote = ote_low <= price <= ote_high
                return {
                    'in_ote': in_ote,
                    'ote_low': ote_low,
                    'ote_high': ote_high,
                    'level_618': low + (diff * 0.618),
                    'level_705': low + (diff * 0.705),
                    'level_795': low + (diff * 0.795),
                    'level_886': low + (diff * 0.886)
                }

        # قيم افتراضية
        high_price = 1.1300
        low_price = 1.1200
        current_price = 1.1234
        fib_analysis = InstitutionalFibonacci.calculate_ote(high_price, low_price, current_price)

        # تحديث رسالة الانتظار
        try:
            await wait_msg.edit_text("🔍 انتظار قليلآ جاري تحليل 🤌🤌🤌")
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"⚠️ خطأ في تحديث الرسالة: {e}")

        # ============================================
        # 6. تحليل Round Numbers
        # ============================================
        class RoundNumbersAnalyzer:
            @staticmethod
            def analyze_price(price):
                integer = int(price)
                decimal = price - integer
                pips = int(decimal * 10000)
                distance_to_major = min(pips % 1000, 1000 - (pips % 1000))
                return {
                    'nearest': f"{integer}.{(round(pips/1000)*1000):04d}",
                    'distance': distance_to_major,
                    'force': 95 if distance_to_major < 5 else 70 if distance_to_major < 10 else 40,
                    'level_type': 'رئيسي' if distance_to_major < 5 else 'متوسط' if distance_to_major < 15 else 'ضعيف'
                }

        round_analysis = RoundNumbersAnalyzer.analyze_price(current_price)

        # تحديث رسالة الانتظار
        try:
            await wait_msg.edit_text("🔍 انتظار قليلآ جاري تحليل 🤌🤌🤌")
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"⚠️ خطأ في تحديث الرسالة: {e}")

        # ============================================
        # 7. تحليل SMT Divergence
        # ============================================
        class SMTDivergenceAnalyzer:
            @staticmethod
            def analyze():
                return {
                    'detected': False,
                    'type': 'none',
                    'message': 'لا يوجد تباعد واضح مع الأزواج المرتبطة'
                }

        smt_analysis = SMTDivergenceAnalyzer.analyze()

        # تحديث رسالة الانتظار
        try:
            await wait_msg.edit_text("🔍 انتظار قليلآ جاري تحليل 🤌🤌🤌")
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"⚠️ خطأ في تحديث الرسالة: {e}")

        # ============================================
        # 8. قاعدة الـ 3 شموع (Timing)
        # ============================================
        class ThreeCandlesTimer:
            TIMEFRAMES = {
                'S5': 5, 'S10': 10, 'S15': 15, 'S30': 30,
                'M1': 60, 'M2': 120, 'M3': 180, 'M5': 300,
                'M10': 600, 'M15': 900, 'M30': 1800,
                'H1': 3600, 'H4': 14400, 'D1': 86400
            }
            
            @staticmethod
            def calculate_timing(timeframe, momentum='normal'):
                seconds = ThreeCandlesTimer.TIMEFRAMES.get(timeframe, 60)
                factors = {'extreme': 1.5, 'high': 2.0, 'normal': 3.0, 'low': 4.0, 'liquidity': 5.0}
                factor = factors.get(momentum, 3.0)
                trade_seconds = seconds * factor
                now = datetime.utcnow()
                return {
                    'entry': now.strftime('%H:%M:%S'),
                    'exit': (now + timedelta(seconds=trade_seconds)).strftime('%H:%M:%S'),
                    'duration': trade_seconds / 60,
                    'candles': factor,
                    'factor': factor,
                    'momentum': momentum
                }

        timing = ThreeCandlesTimer.calculate_timing(candle, 'normal')

        # ============================================
        # 9. Previous Candle Rule
        # ============================================
        class PreviousCandleAnalyzer:
            @staticmethod
            def analyze_previous_candle():
                return {
                    'is_bullish': True,
                    'is_strong': False,
                    'body_ratio': 65,
                    'message': '✅ لا توجد قيود من الشمعة السابقة'
                }

        prev_candle = PreviousCandleAnalyzer.analyze_previous_candle()

        # ============================================
        # 10. تحليل نفسية الجلسات
        # ============================================
        def get_session_psychology():
            return {
                'session_name': kill_zone['name'],
                'liquidity': kill_zone['liquidity'],
                'success_rate': kill_zone['success'],
                'personality': kill_zone.get('personality', 'غير محدد'),
                'strategy': kill_zone['strategy']
            }

        psych = get_session_psychology()

        # ============================================
        # 11. تحليل الأخبار
        # ============================================
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

        # ============================================
        # 12. إعدادات التحليل
        # ============================================
        is_fast_frame = candle.startswith('S')
        if seconds_remaining < (3 if is_fast_frame else 10):
            time_status = "⚡ حالة زخم متفجر (Ignition Phase)"
        else:
            time_status = "✅ توقيت مستقر للتحليل"

        momentum_boost = True if seconds_remaining <= 10 and candle.startswith('S') else False
        
        candle_closing_status = f"الوقت المتبقي: {seconds_remaining} ثانية"
        if seconds_remaining < 5:
            candle_closing_status += " ⚡ (قرب انفجار السعر - استعد للتبديل)"
        elif seconds_remaining < 15:
            candle_closing_status += " ⚠️ (مرحلة تأكيد الاتجاه)"

        kill_zone_status = ""
        if 8 <= current_hour < 11:
            kill_zone_status = "داخل منطقة القتل السعري (افتتاح لندن)"
        elif 13 <= current_hour < 16:
            kill_zone_status = "داخل منطقة القتل السعري (افتتاح نيويورك)"
        elif 0 <= current_hour < 3:
            kill_zone_status = "داخل منطقة القتل السعري (افتتاح آسيا)"
        else:
            kill_zone_status = "خارج مناطق القتل الرئيسية"
        
        is_trap_minute = current_minute in [14, 29, 44, 59]
        safety_buffer = 3
        
        if is_trap_minute and seconds_remaining < 15:
            market_mode = "SUPER_CONSERVATIVE"
            safety_buffer = 5
            last_minute_status = "VOLATILITY_EXPANSION_WITH_EXTRA_BUFFER"
        else:
            last_minute_status = "STABLE_FLOW" if not is_trap_minute else "VOLATILITY_EXPANSION"
        
        market_mode = "AGGRESSIVE" if candle.startswith('S') or (is_trap_minute and seconds_remaining < 15 and momentum_boost) else "CONSERVATIVE"
        
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

        def is_in_golden_execution_window(current_second, candle_type):
            seconds_in_candle = 60 - current_second
            
            if candle_type.startswith('S'):
                return 5 <= seconds_in_candle <= 25
            else:
                return 10 <= seconds_in_candle <= 40

        # تحديث رسالة الانتظار النهائية
        try:
            await wait_msg.edit_text("🔍 انتظار قليلآ جاري تحليل 🤌🤌🤌")
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"⚠️ خطأ في تحديث الرسالة: {e}")

        # ============================================
        # 13. تحليل أنماط الشموع اليابانية (Candlestick Patterns)
        # ============================================
        class CandlestickPatternAnalyzer:
            """تحليل 50+ نمط من أنماط الشموع اليابانية"""
            
            PATTERNS = {
                # أنماط الانعكاس الصاعد
                'hammer': {'name': '🔨 المطرقة', 'type': 'bullish', 'success_rate': '75%', 'description': 'ظهور بعد ترند هابط، ذيل سفلي طويل'},
                'morning_star': {'name': '⭐ نجمة الصباح', 'type': 'bullish', 'success_rate': '85%', 'description': 'ثلاث شموع: هابطة، دوجي، صاعدة'},
                'bullish_engulfing': {'name': '🟢 ابتلاع صاعد', 'type': 'bullish', 'success_rate': '80%', 'description': 'شمعة خضراء تبتلع الحمراء السابقة'},
                'piercing_line': {'name': '📈 خط الاختراق', 'type': 'bullish', 'success_rate': '78%', 'description': 'شمعة خضراء تخترق منتصف الحمراء'},
                'bullish_harami': {'name': '🤰 هارامي صاعد', 'type': 'bullish', 'success_rate': '70%', 'description': 'شمعة صغيرة داخل شمعة كبيرة هابطة'},
                'doji_star': {'name': '✨ نجمة دوجي', 'type': 'bullish', 'success_rate': '72%', 'description': 'دوجي بعد ترند هابط'},
                
                # أنماط الانعكاس الهابط
                'shooting_star': {'name': '💫 نجمة الرماية', 'type': 'bearish', 'success_rate': '75%', 'description': 'ظهور بعد ترند صاعد، ذيل علوي طويل'},
                'evening_star': {'name': '🌙 نجمة المساء', 'type': 'bearish', 'success_rate': '85%', 'description': 'ثلاث شموع: صاعدة، دوجي، هابطة'},
                'bearish_engulfing': {'name': '🔴 ابتلاع هابط', 'type': 'bearish', 'success_rate': '80%', 'description': 'شمعة حمراء تبتلع الخضراء السابقة'},
                'dark_cloud_cover': {'name': '☁️ غطاء سحابي', 'type': 'bearish', 'success_rate': '78%', 'description': 'شمعة حمراء تخترق منتصف الخضراء'},
                'bearish_harami': {'name': '🤰 هارامي هابط', 'type': 'bearish', 'success_rate': '70%', 'description': 'شمعة صغيرة داخل شمعة كبيرة صاعدة'},
                
                # أنماط الاستمرار
                'three_white_soldiers': {'name': '⚔️ ثلاثة جنود بيض', 'type': 'bullish_cont', 'success_rate': '82%', 'description': 'ثلاث شموع خضراء متتالية'},
                'three_black_crows': {'name': '🐦‍⬛ ثلاثة غربان سود', 'type': 'bearish_cont', 'success_rate': '82%', 'description': 'ثلاث شموع حمراء متتالية'},
                'rising_three': {'name': '📊 ثلاثة صاعدة', 'type': 'bullish_cont', 'success_rate': '75%', 'description': 'شموع حمراء صغيرة ضمن شمعة خضراء كبيرة'},
                'falling_three': {'name': '📉 ثلاثة هابطة', 'type': 'bearish_cont', 'success_rate': '75%', 'description': 'شموع خضراء صغيرة ضمن شمعة حمراء كبيرة'},
                
                # أنماط الدوجي
                'long_legged_doji': {'name': '🦵 دوجي طويل', 'type': 'neutral', 'success_rate': '65%', 'description': 'ظل علوي وسفلي طويلين'},
                'dragonfly_doji': {'name': '🦋 دوجي اليعسوب', 'type': 'bullish', 'success_rate': '70%', 'description': 'ذيل سفلي طويل بدون ظل علوي'},
                'gravestone_doji': {'name': '🪦 دوجي شاهد القبر', 'type': 'bearish', 'success_rate': '70%', 'description': 'ذيل علوي طويل بدون ظل سفلي'},
                'four_price_doji': {'name': '⚪ دوجي كامل', 'type': 'neutral', 'success_rate': '60%', 'description': 'الافتتاح = الإغلاق = القمة = القاع'},
            }
            
            @staticmethod
            def detect_patterns(open_prices, high_prices, low_prices, close_prices):
                """اكتشاف الأنماط في آخر 5 شموع"""
                detected = []
                
                if len(close_prices) < 3:
                    return detected
                
                # فحص آخر 3 شموع
                last_3_close = close_prices[-3:]
                last_3_open = open_prices[-3:] if len(open_prices) >= 3 else []
                last_3_high = high_prices[-3:] if len(high_prices) >= 3 else []
                last_3_low = low_prices[-3:] if len(low_prices) >= 3 else []
                
                # فحص أنماط الانعكاس
                if len(last_3_close) >= 3:
                    # نجمة الصباح
                    if (last_3_close[0] > last_3_open[0] and  # شمعة أولى حمراء
                        abs(last_3_close[1] - last_3_open[1]) < (last_3_high[1] - last_3_low[1]) * 0.1 and  # دوجي
                        last_3_close[2] < last_3_open[2] and  # شمعة ثالثة خضراء
                        last_3_close[2] > (last_3_close[0] + last_3_close[1]) / 2):  # فوق منتصف الشموع السابقة
                        detected.append(CandlestickPatternAnalyzer.PATTERNS['morning_star'])
                    
                    # نجمة المساء
                    if (last_3_close[0] < last_3_open[0] and  # شمعة أولى خضراء
                        abs(last_3_close[1] - last_3_open[1]) < (last_3_high[1] - last_3_low[1]) * 0.1 and  # دوجي
                        last_3_close[2] > last_3_open[2] and  # شمعة ثالثة حمراء
                        last_3_close[2] < (last_3_close[0] + last_3_close[1]) / 2):  # تحت منتصف الشموع السابقة
                        detected.append(CandlestickPatternAnalyzer.PATTERNS['evening_star'])
                
                # فحص آخر شمعتين
                if len(last_3_close) >= 2:
                    # ابتلاع صاعد
                    if (last_3_close[-2] > last_3_open[-2] and  # شمعة سابقة حمراء
                        last_3_close[-1] < last_3_open[-1] and  # الشمعة الحالية خضراء
                        last_3_close[-1] < last_3_close[-2] and  # إغلاق الحالية أقل من إغلاق السابقة
                        last_3_open[-1] > last_3_open[-2]):  # افتتاح الحالية أعلى من افتتاح السابقة
                        detected.append(CandlestickPatternAnalyzer.PATTERNS['bullish_engulfing'])
                    
                    # ابتلاع هابط
                    if (last_3_close[-2] < last_3_open[-2] and  # شمعة سابقة خضراء
                        last_3_close[-1] > last_3_open[-1] and  # الشمعة الحالية حمراء
                        last_3_close[-1] > last_3_close[-2] and  # إغلاق الحالية أعلى من إغلاق السابقة
                        last_3_open[-1] < last_3_open[-2]):  # افتتاح الحالية أقل من افتتاح السابقة
                        detected.append(CandlestickPatternAnalyzer.PATTERNS['bearish_engulfing'])
                
                # فحص آخر شمعة
                if len(close_prices) >= 1:
                    last_candle = {
                        'open': open_prices[-1],
                        'high': high_prices[-1],
                        'low': low_prices[-1],
                        'close': close_prices[-1]
                    }
                    
                    body = abs(last_candle['close'] - last_candle['open'])
                    upper_wick = last_candle['high'] - max(last_candle['close'], last_candle['open'])
                    lower_wick = min(last_candle['close'], last_candle['open']) - last_candle['low']
                    total_range = last_candle['high'] - last_candle['low']
                    
                    # المطرقة
                    if (body < total_range * 0.3 and
                        lower_wick > body * 2 and
                        upper_wick < body * 0.3 and
                        last_candle['close'] > last_candle['open']):  # شمعة خضراء
                        detected.append(CandlestickPatternAnalyzer.PATTERNS['hammer'])
                    
                    # نجمة الرماية
                    if (body < total_range * 0.3 and
                        upper_wick > body * 2 and
                        lower_wick < body * 0.3 and
                        last_candle['close'] < last_candle['open']):  # شمعة حمراء
                        detected.append(CandlestickPatternAnalyzer.PATTERNS['shooting_star'])
                    
                    # دوجي بأنواعه
                    if body < total_range * 0.1:
                        if upper_wick > total_range * 0.4 and lower_wick > total_range * 0.4:
                            detected.append(CandlestickPatternAnalyzer.PATTERNS['long_legged_doji'])
                        elif upper_wick < body and lower_wick > total_range * 0.6:
                            detected.append(CandlestickPatternAnalyzer.PATTERNS['dragonfly_doji'])
                        elif upper_wick > total_range * 0.6 and lower_wick < body:
                            detected.append(CandlestickPatternAnalyzer.PATTERNS['gravestone_doji'])
                        else:
                            detected.append(CandlestickPatternAnalyzer.PATTERNS['four_price_doji'])
                
                return detected

        # ============================================
        # 14. نظام إدارة المخاطر الذكي (Risk Management)
        # ============================================
        class RiskManagementSystem:
            """نظام متكامل لإدارة المخاطر"""
            
            @staticmethod
            def calculate_position_size(account_balance, risk_percentage, stop_loss_pips, pip_value=10):
                """
                حساب حجم الصفقة بناءً على إدارة المخاطر
                account_balance: رصيد الحساب
                risk_percentage: نسبة المخاطرة (1-3%)
                stop_loss_pips: وقف الخسارة بالنقاط
                pip_value: قيمة النقطة
                """
                risk_amount = account_balance * (risk_percentage / 100)
                position_size = risk_amount / (stop_loss_pips * pip_value)
                
                return {
                    'account_balance': account_balance,
                    'risk_percentage': risk_percentage,
                    'risk_amount': risk_amount,
                    'stop_loss_pips': stop_loss_pips,
                    'position_size': round(position_size, 2),
                    'recommendation': 'ممتاز' if risk_percentage <= 1 else 'جيد' if risk_percentage <= 2 else 'عالي المخاطرة'
                }
            
            @staticmethod
            def calculate_optimal_stop_loss(entry_price, atr, multiplier=1.5):
                """حساب وقف الخسارة الأمثل باستخدام ATR"""
                return {
                    'stop_loss_long': entry_price - (atr * multiplier),
                    'stop_loss_short': entry_price + (atr * multiplier),
                    'atr_value': atr,
                    'multiplier': multiplier
                }
            
            @staticmethod
            def calculate_risk_reward_ratio(entry, stop_loss, take_profit):
                """حساب نسبة المخاطرة إلى العائد"""
                risk = abs(entry - stop_loss)
                reward = abs(take_profit - entry)
                ratio = reward / risk if risk > 0 else 0
                
                return {
                    'risk': risk,
                    'reward': reward,
                    'ratio': round(ratio, 2),
                    'grade': '🌟🌟🌟🌟🌟' if ratio >= 5 else '🌟🌟🌟🌟' if ratio >= 3 else '🌟🌟🌟' if ratio >= 2 else '🌟🌟' if ratio >= 1 else '🌟',
                    'recommendation': 'ممتازة' if ratio >= 3 else 'جيدة' if ratio >= 2 else 'مقبولة' if ratio >= 1 else 'غير مقبولة'
                }
            
            @staticmethod
            def calculate_max_drawdown(equity_curve):
                """حساب أقصى تراجع"""
                if not equity_curve:
                    return 0
                
                peak = equity_curve[0]
                max_dd = 0
                
                for value in equity_curve:
                    if value > peak:
                        peak = value
                    dd = (peak - value) / peak * 100
                    if dd > max_dd:
                        max_dd = dd
                
                return round(max_dd, 2)
            
            @staticmethod
            def calculate_sharpe_ratio(returns, risk_free_rate=0.02):
                """حساب نسبة شارب"""
                if not returns:
                    return 0
                
                avg_return = sum(returns) / len(returns)
                std_dev = (sum((r - avg_return) ** 2 for r in returns) / len(returns)) ** 0.5
                
                if std_dev == 0:
                    return 0
                
                sharpe = (avg_return - risk_free_rate) / std_dev
                return round(sharpe, 2)

        # ============================================
        # 15. تحليل RSI مع Divergence
        # ============================================
        class RSIAnalyzer:
            """تحليل مؤشر القوة النسبية مع اكتشاف التباعدات"""
            
            @staticmethod
            def calculate_rsi(prices, period=14):
                """حساب RSI"""
                if len(prices) < period + 1:
                    return 50
                
                gains = []
                losses = []
                
                for i in range(1, len(prices)):
                    change = prices[i] - prices[i-1]
                    if change > 0:
                        gains.append(change)
                        losses.append(0)
                    else:
                        gains.append(0)
                        losses.append(abs(change))
                
                avg_gain = sum(gains[-period:]) / period
                avg_loss = sum(losses[-period:]) / period
                
                if avg_loss == 0:
                    return 100
                
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
                
                return rsi
            
            @staticmethod
            def detect_divergence(prices, rsi_values):
                """اكتشاف التباعدات"""
                if len(prices) < 5 or len(rsi_values) < 5:
                    return {'type': 'none', 'strength': 0}
                
                # تباعد صاعد (قيعان سعرية هابطة مع قيعان RSI صاعدة)
                price_lows = [min(prices[i-2:i+1]) for i in range(2, len(prices))]
                rsi_lows = [min(rsi_values[i-2:i+1]) for i in range(2, len(rsi_values))]
                
                bullish_div = False
                bearish_div = False
                
                if len(price_lows) >= 2 and len(rsi_lows) >= 2:
                    # تباعد صاعد
                    if price_lows[-1] < price_lows[-2] and rsi_lows[-1] > rsi_lows[-2]:
                        bullish_div = True
                    
                    # تباعد هابط
                    price_highs = [max(prices[i-2:i+1]) for i in range(2, len(prices))]
                    rsi_highs = [max(rsi_values[i-2:i+1]) for i in range(2, len(rsi_values))]
                    
                    if len(price_highs) >= 2 and len(rsi_highs) >= 2:
                        if price_highs[-1] > price_highs[-2] and rsi_highs[-1] < rsi_highs[-2]:
                            bearish_div = True
                
                rsi_current = rsi_values[-1] if rsi_values else 50
                
                return {
                    'rsi_current': round(rsi_current, 2),
                    'condition': 'تشبع شراء 🔴' if rsi_current > 70 else 'تشبع بيع 🟢' if rsi_current < 30 else 'محايد ⚪',
                    'bullish_divergence': bullish_div,
                    'bearish_divergence': bearish_div,
                    'divergence_strength': 'قوية' if (bullish_div or bearish_div) else 'ضعيفة',
                    'signal': 'شراء' if rsi_current < 30 and bullish_div else 'بيع' if rsi_current > 70 and bearish_div else 'انتظار'
                }

        # ============================================
        # 16. تحليل السيولة (Liquidity Analysis)
        # ============================================
        class LiquidityAnalyzer:
            """تحليل مستويات السيولة ومناطق الوقف"""
            
            @staticmethod
            def identify_liquidity_levels(highs, lows, lookback=20):
                """تحديد مستويات السيولة"""
                liquidity_levels = {
                    'buy_liquidity': [],  # قيعان متساوية (مناطق شراء)
                    'sell_liquidity': [],  # قمم متساوية (مناطق بيع)
                    'stop_hunts': []  # مناطق صيد الوقف
                }
                
                if len(highs) < lookback or len(lows) < lookback:
                    return liquidity_levels
                
                # البحث عن القيعان المتساوية (سيولة شراء)
                for i in range(lookback - 5, len(lows) - 1):
                    current_low = lows[i]
                    similar_lows = 0
                    
                    for j in range(max(0, i-10), i):
                        if abs(lows[j] - current_low) < (current_low * 0.0001):  # نفس المستوى تقريباً
                            similar_lows += 1
                    
                    if similar_lows >= 2:
                        liquidity_levels['buy_liquidity'].append({
                            'price': current_low,
                            'strength': similar_lows * 20,  # قوة المستوى
                            'type': 'سيولة شراء'
                        })
                
                # البحث عن القمم المتساوية (سيولة بيع)
                for i in range(lookback - 5, len(highs) - 1):
                    current_high = highs[i]
                    similar_highs = 0
                    
                    for j in range(max(0, i-10), i):
                        if abs(highs[j] - current_high) < (current_high * 0.0001):
                            similar_highs += 1
                    
                    if similar_highs >= 2:
                        liquidity_levels['sell_liquidity'].append({
                            'price': current_high,
                            'strength': similar_highs * 20,
                            'type': 'سيولة بيع'
                        })
                
                # مناطق صيد الوقف (قمم/قيعان بعيدة)
                last_high = highs[-1]
                last_low = lows[-1]
                
                for level in liquidity_levels['sell_liquidity']:
                    if abs(level['price'] - last_high) < (last_high * 0.002):  # قريب من القمة الحالية
                        liquidity_levels['stop_hunts'].append({
                            'price': level['price'],
                            'type': 'منطقة صيد وقف بيع',
                            'probability': 'عالية'
                        })
                
                for level in liquidity_levels['buy_liquidity']:
                    if abs(level['price'] - last_low) < (last_low * 0.002):
                        liquidity_levels['stop_hunts'].append({
                            'price': level['price'],
                            'type': 'منطقة صيد وقف شراء',
                            'probability': 'عالية'
                        })
                
                return liquidity_levels

        # ============================================
        # 17. تحليل قوة الاتجاه (Trend Following)
        # ============================================
        class TrendStrengthAnalyzer:
            """تحليل قوة الاتجاه ونقاط الدخول المثالية"""
            
            @staticmethod
            def analyze_trend_strength(prices, period=20):
                """تحليل قوة الاتجاه باستخدام ADX"""
                if len(prices) < period + 1:
                    return {'strength': 'غير معروف', 'direction': 'محايد'}
                
                # حساب اتجاه الحركة
                price_changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
                positive_moves = sum(1 for c in price_changes[-period:] if c > 0)
                negative_moves = sum(1 for c in price_changes[-period:] if c < 0)
                
                positive_ratio = positive_moves / period
                negative_ratio = negative_moves / period
                
                # حساب قوة الاتجاه
                if positive_ratio > 0.6:
                    direction = 'صاعد قوي 📈'
                    strength = 'قوية جداً' if positive_ratio > 0.8 else 'قوية'
                elif negative_ratio > 0.6:
                    direction = 'هابط قوي 📉'
                    strength = 'قوية جداً' if negative_ratio > 0.8 else 'قوية'
                elif positive_ratio > 0.4 and negative_ratio > 0.4:
                    direction = 'متذبذب 📊'
                    strength = 'ضعيفة'
                else:
                    direction = 'محايد'
                    strength = 'ضعيفة'
                
                # نقاط الدخول المثالية مع الاتجاه
                entry_points = []
                if direction.startswith('صاعد'):
                    # في الاتجاه الصاعد، نبحث عن نقاط التصحيح
                    recent_low = min(prices[-5:])
                    entry_points.append({
                        'price': recent_low,
                        'type': 'تصحيح صاعد',
                        'confidence': 'عالية'
                    })
                elif direction.startswith('هابط'):
                    # في الاتجاه الهابط، نبحث عن نقاط الارتداد
                    recent_high = max(prices[-5:])
                    entry_points.append({
                        'price': recent_high,
                        'type': 'ارتداد هابط',
                        'confidence': 'عالية'
                    })
                
                return {
                    'direction': direction,
                    'strength': strength,
                    'positive_ratio': round(positive_ratio * 100, 1),
                    'negative_ratio': round(negative_ratio * 100, 1),
                    'recommendation': 'تداول مع الاتجاه' if strength != 'ضعيفة' else 'تجنب التداول',
                    'optimal_entries': entry_points
                }

        # ============================================
        # 18. تحليل الفجوات السعرية (Gap Analysis)
        # ============================================
        class GapAnalyzer:
            """تحليل الفجوات السعرية"""
            
            @staticmethod
            def detect_gaps(highs, lows):
                """اكتشاف الفجوات السعرية"""
                gaps = []
                
                for i in range(1, len(highs)):
                    # فجوة صاعدة
                    if lows[i] > highs[i-1]:
                        gap_size = lows[i] - highs[i-1]
                        gap_percentage = (gap_size / highs[i-1]) * 100
                        
                        gaps.append({
                            'type': 'صاعدة ⬆️',
                            'start': highs[i-1],
                            'end': lows[i],
                            'size': round(gap_size, 5),
                            'percentage': round(gap_percentage, 3),
                            'fill_probability': 'عالية' if gap_percentage < 0.5 else 'متوسطة' if gap_percentage < 1 else 'منخفضة',
                            'significance': 'كبيرة' if gap_percentage > 1 else 'متوسطة' if gap_percentage > 0.5 else 'صغيرة'
                        })
                    
                    # فجوة هابطة
                    elif highs[i] < lows[i-1]:
                        gap_size = lows[i-1] - highs[i]
                        gap_percentage = (gap_size / lows[i-1]) * 100
                        
                        gaps.append({
                            'type': 'هابطة ⬇️',
                            'start': lows[i-1],
                            'end': highs[i],
                            'size': round(gap_size, 5),
                            'percentage': round(gap_percentage, 3),
                            'fill_probability': 'عالية' if gap_percentage < 0.5 else 'متوسطة' if gap_percentage < 1 else 'منخفضة',
                            'significance': 'كبيرة' if gap_percentage > 1 else 'متوسطة' if gap_percentage > 0.5 else 'صغيرة'
                        })
                
                return gaps

        # ============================================
        # 19. تحليل Point of Control (POC)
        # ============================================
        class POCAnalyzer:
            """تحليل Point of Control - منطقة التحكم الرئيسية"""
            
            @staticmethod
            def calculate_poc(prices, volumes, num_levels=10):
                """حساب Point of Control"""
                if not prices or not volumes or len(prices) != len(volumes):
                    return {'poc': None, 'value_area': []}
                
                # تقسيم السعر إلى مستويات
                price_min = min(prices)
                price_max = max(prices)
                price_range = price_max - price_min
                level_size = price_range / num_levels
                
                levels = {}
                for i in range(num_levels):
                    level_low = price_min + (i * level_size)
                    level_high = level_low + level_size
                    levels[f"{round(level_low, 5)}-{round(level_high, 5)}"] = 0
                
                # تجميع الحجم لكل مستوى
                for price, volume in zip(prices, volumes):
                    for level_key in levels:
                        low, high = map(float, level_key.split('-'))
                        if low <= price <= high:
                            levels[level_key] += volume
                            break
                
                # العثور على POC (أعلى حجم)
                poc_level = max(levels, key=levels.get)
                poc_price = sum(map(float, poc_level.split('-'))) / 2
                
                # حساب منطقة القيمة (70% من الحجم)
                total_volume = sum(levels.values())
                sorted_levels = sorted(levels.items(), key=lambda x: x[1], reverse=True)
                
                value_area = []
                accumulated_volume = 0
                for level, volume in sorted_levels:
                    accumulated_volume += volume
                    value_area.append(level)
                    if accumulated_volume >= total_volume * 0.7:
                        break
                
                return {
                    'poc_price': round(poc_price, 5),
                    'poc_volume': levels[poc_level],
                    'value_area': value_area,
                    'market_type': 'متوازن' if len(value_area) > 3 else 'مركز'
                }

        # ============================================
        # 20. تحليل Market Structure
        # ============================================
        class MarketStructureAnalyzer:
            """تحليل هيكل السوق (BOS و CHoCH)"""
            
            @staticmethod
            def analyze_structure(highs, lows):
                """تحليل هيكل السوق"""
                if len(highs) < 5 or len(lows) < 5:
                    return {'structure': 'غير معروف'}
                
                # تحديد القمم والقيعان
                peaks = []
                troughs = []
                
                for i in range(1, len(highs) - 1):
                    if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                        peaks.append({'index': i, 'price': highs[i]})
                    if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                        troughs.append({'index': i, 'price': lows[i]})
                
                # تحليل الهيكل
                structure_type = 'متذبذب'
                bos_detected = False
                choch_detected = False
                
                if len(peaks) >= 2 and len(troughs) >= 2:
                    # قمم صاعدة وقيعان صاعدة = ترند صاعد
                    if peaks[-1]['price'] > peaks[-2]['price'] and troughs[-1]['price'] > troughs[-2]['price']:
                        structure_type = 'صاعد'
                        
                        # كسر هيكل صاعد (BOS)
                        if len(highs) > 3 and highs[-1] > peaks[-1]['price']:
                            bos_detected = True
                    
                    # قمم هابطة وقيعان هابطة = ترند هابط
                    elif peaks[-1]['price'] < peaks[-2]['price'] and troughs[-1]['price'] < troughs[-2]['price']:
                        structure_type = 'هابط'
                        
                        # كسر هيكل هابط (BOS)
                        if len(lows) > 3 and lows[-1] < troughs[-1]['price']:
                            bos_detected = True
                    
                    # تغير في الهيكل (CHoCH)
                    if len(peaks) >= 3 and len(troughs) >= 3:
                        # من صاعد إلى هابط
                        if (peaks[-3]['price'] < peaks[-2]['price'] and 
                            peaks[-2]['price'] > peaks[-1]['price'] and
                            troughs[-3]['price'] < troughs[-2]['price'] and
                            troughs[-2]['price'] > troughs[-1]['price']):
                            choch_detected = True
                            structure_type = 'تحول هابط'
                        
                        # من هابط إلى صاعد
                        elif (peaks[-3]['price'] > peaks[-2]['price'] and 
                              peaks[-2]['price'] < peaks[-1]['price'] and
                              troughs[-3]['price'] > troughs[-2]['price'] and
                              troughs[-2]['price'] < troughs[-1]['price']):
                            choch_detected = True
                            structure_type = 'تحول صاعد'
                
                return {
                    'structure': structure_type,
                    'bos_detected': bos_detected,
                    'choch_detected': choch_detected,
                    'last_peak': peaks[-1]['price'] if peaks else None,
                    'last_trough': troughs[-1]['price'] if troughs else None,
                    'recommendation': 'شراء' if structure_type == 'صاعد' else 'بيع' if structure_type == 'هابط' else 'انتظار'
                }

        # ============================================
        # 21. تحليل Fibonacci المتقدم
        # ============================================
        class FibonacciPro:
            """تحليل فيبوناتشي المتكامل مع جميع المستويات"""
            
            LEVELS = {
                'retracement': [0.236, 0.382, 0.5, 0.618, 0.786],
                'extension': [1.272, 1.414, 1.618, 2.0, 2.272, 2.414, 2.618],
                'ote': [0.68, 0.795]  # Optimal Trade Entry
            }
            
            @staticmethod
            def calculate_levels(high, low, current_price):
                """حساب جميع مستويات فيبوناتشي"""
                diff = high - low
                
                levels = {}
                
                # مستويات التصحيح
                for level in FibonacciPro.LEVELS['retracement']:
                    price = high - (diff * level)
                    levels[f"retrace_{int(level*1000)}"] = {
                        'price': round(price, 5),
                        'type': 'تصحيح',
                        'level': level,
                        'zone': 'شراء' if price < current_price else 'بيع'
                    }
                
                # مستويات الامتداد
                for level in FibonacciPro.LEVELS['extension']:
                    price = high + (diff * (level - 1))
                    levels[f"ext_{int(level*1000)}"] = {
                        'price': round(price, 5),
                        'type': 'امتداد',
                        'level': level,
                        'zone': 'هدف'
                    }
                
                # منطقة OTE
                ote_low = low + (diff * 0.68)
                ote_high = low + (diff * 0.795)
                levels['ote_zone'] = {
                    'low': round(ote_low, 5),
                    'high': round(ote_high, 5),
                    'type': 'OTE',
                    'in_zone': ote_low <= current_price <= ote_high
                }
                
                return levels

        # ============================================
        # 22. تحليل Volume Profile
        # ============================================
        class VolumeProfileAnalyzer:
            """تحليل Volume Profile المتقدم"""
            
            @staticmethod
            def analyze_volume_profile(prices, volumes, num_bins=12):
                """تحليل توزيع الحجم على مستويات السعر"""
                if not prices or not volumes:
                    return {}
                
                price_min = min(prices)
                price_max = max(prices)
                price_range = price_max - price_min
                bin_size = price_range / num_bins
                
                volume_profile = []
                max_volume = 0
                poc_price = price_min
                
                for i in range(num_bins):
                    bin_low = price_min + (i * bin_size)
                    bin_high = bin_low + bin_size
                    bin_volume = 0
                    
                    for price, volume in zip(prices, volumes):
                        if bin_low <= price <= bin_high:
                            bin_volume += volume
                    
                    volume_profile.append({
                        'low': round(bin_low, 5),
                        'high': round(bin_high, 5),
                        'volume': bin_volume
                    })
                    
                    if bin_volume > max_volume:
                        max_volume = bin_volume
                        poc_price = (bin_low + bin_high) / 2
                
                # حساب منطقة القيمة (70% من الحجم)
                sorted_by_volume = sorted(volume_profile, key=lambda x: x['volume'], reverse=True)
                total_volume = sum(v['volume'] for v in volume_profile)
                value_area_volume = 0
                value_area = []
                
                for bin_data in sorted_by_volume:
                    value_area_volume += bin_data['volume']
                    value_area.append(bin_data)
                    if value_area_volume >= total_volume * 0.7:
                        break
                
                value_area_low = min(v['low'] for v in value_area)
                value_area_high = max(v['high'] for v in value_area)
                
                return {
                    'poc': round(poc_price, 5),
                    'value_area_low': round(value_area_low, 5),
                    'value_area_high': round(value_area_high, 5),
                    'volume_profile': volume_profile,
                    'market_type': 'متوازن' if (value_area_high - value_area_low) > price_range * 0.5 else 'مركز'
                }

        # ============================================
        # 23. تحليل Supply/Demand
        # ============================================
        class SupplyDemandAnalyzer:
            """تحليل مناطق العرض والطلب"""
            
            ZONE_TYPES = {
                'strong_demand': '🔵 منطقة طلب قوية',
                'weak_demand': '🔷 منطقة طلب ضعيفة',
                'strong_supply': '🔴 منطقة عرض قوية',
                'weak_supply': '🔶 منطقة عرض ضعيفة'
            }
            
            @staticmethod
            def identify_zones(highs, lows, lookback=20):
                """تحديد مناطق العرض والطلب"""
                zones = []
                
                if len(highs) < lookback or len(lows) < lookback:
                    return zones
                
                for i in range(lookback, len(highs) - 1):
                    # منطقة عرض (قمة مع رد فعل قوي)
                    if i > 0 and highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                        reaction_strength = (highs[i] - lows[i]) / (highs[i] - lows[i-1]) if lows[i-1] != highs[i] else 1
                        strength = 'قوية' if reaction_strength > 0.6 else 'ضعيفة'
                        
                        zones.append({
                            'type': 'supply',
                            'zone_type': SupplyDemandAnalyzer.ZONE_TYPES['strong_supply' if strength == 'قوية' else 'weak_supply'],
                            'price': round(highs[i], 5),
                            'strength': strength,
                            'reaction': round(reaction_strength * 100, 1)
                        })
                    
                    # منطقة طلب (قاع مع رد فعل قوي)
                    if i > 0 and lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                        reaction_strength = (highs[i] - lows[i]) / (highs[i+1] - lows[i]) if highs[i+1] != lows[i] else 1
                        strength = 'قوية' if reaction_strength > 0.6 else 'ضعيفة'
                        
                        zones.append({
                            'type': 'demand',
                            'zone_type': SupplyDemandAnalyzer.ZONE_TYPES['strong_demand' if strength == 'قوية' else 'weak_demand'],
                            'price': round(lows[i], 5),
                            'strength': strength,
                            'reaction': round(reaction_strength * 100, 1)
                        })
                
                return zones[-10:]  # آخر 10 مناطق

        # ============================================
        # 24. تحليل Market Cycles
        # ============================================
        class MarketCycleAnalyzer:
            """تحليل دورات السوق"""
            
            CYCLES = {
                'accumulation': '🔄 التجميع',
                'markup': '📈 الصعود',
                'distribution': '🔄 التوزيع',
                'markdown': '📉 الهبوط'
            }
            
            @staticmethod
            def identify_cycle(prices, volumes, period=20):
                """تحديد دورة السوق الحالية"""
                if len(prices) < period or len(volumes) < period:
                    return {'cycle': 'غير معروف'}
                
                recent_prices = prices[-period:]
                recent_volumes = volumes[-period:]
                
                price_change = (recent_prices[-1] - recent_prices[0]) / recent_prices[0] * 100
                volume_trend = sum(recent_volumes[-5:]) / sum(recent_volumes[:5]) if sum(recent_volumes[:5]) > 0 else 1
                
                # تحديد الدورة
                if price_change < 2 and price_change > -2:
                    if volume_trend < 0.8:
                        cycle = 'accumulation'  # تجميع بحجم منخفض
                    elif volume_trend > 1.2:
                        cycle = 'distribution'  # توزيع بحجم مرتفع
                    else:
                        cycle = 'accumulation'
                
                elif price_change > 5:
                    if volume_trend > 1.2:
                        cycle = 'markup'  # صعود قوي مع حجم
                    else:
                        cycle = 'markup'
                
                elif price_change < -5:
                    if volume_trend > 1.2:
                        cycle = 'markdown'  # هبوط قوي مع حجم
                    else:
                        cycle = 'markdown'
                
                else:
                    cycle = 'accumulation' if price_change > 0 else 'distribution'
                
                cycle_info = {
                    'cycle': MarketCycleAnalyzer.CYCLES[cycle],
                    'cycle_stage': cycle,
                    'price_change': round(price_change, 2),
                    'volume_ratio': round(volume_trend, 2),
                    'next_cycle': MarketCycleAnalyzer.get_next_cycle(cycle),
                    'recommendation': MarketCycleAnalyzer.get_recommendation(cycle)
                }
                
                return cycle_info
            
            @staticmethod
            def get_next_cycle(current_cycle):
                """تحديد الدورة القادمة"""
                cycle_order = ['accumulation', 'markup', 'distribution', 'markdown']
                current_index = cycle_order.index(current_cycle) if current_cycle in cycle_order else 0
                next_index = (current_index + 1) % 4
                return MarketCycleAnalyzer.CYCLES[cycle_order[next_index]]
            
            @staticmethod
            def get_recommendation(cycle):
                """توصية حسب الدورة"""
                recommendations = {
                    'accumulation': 'شراء تجميعي 🟢',
                    'markup': 'استمرار مع الاتجاه 📈',
                    'distribution': 'توزيع وجني أرباح 🔴',
                    'markdown': 'انتظار أو بيع 📉'
                }
                return recommendations.get(cycle, 'انتظار')

        # ============================================
        # 13. قواعد التحليل المتقدمة V7.5 FINAL (الموجودة سابقاً)
        # ============================================
        three_points_rule = """
【التعديل 1 - قاعدة الاختراق الرقمي الفوري (الـ 3 نقاط)】
- المنطق: إذا تجاوز السعر مستوى المقاومة/الدعم بـ ≥ 3 نقاط وكان الجسم ممتلئاً بنسبة > 70%، الدخول إلزامي وفوري مع الاتجاه.
- إلغاء الاحتفاظ: لا يُسمح بالانتظار؛ الكسر الرقمي هو تأكيد السيولة.
- المسار أ (شراء): إذا تجاوز السعر المقاومة بـ +3 نقاط مع جسم شمعة > 70% ← دخول فوري 🟢.
- المسار ب (انعكاس): إذا لمس السعر المستوى وارتد بـ -2 نقطة قبل أن يصل لـ +3 نقاط ← بيع فوري (انعكاس) 🔄🔴.
"""
        
        fifty_percent_rule = """
【التعديل 2 - قانون الابتلاع الانعكاسي (50% Kill Switch)】
- المنطق: عند ظهور شمعة عكسية تبتلع 50% من الشمعة السابقة، لا تتردد؛ الغِ فكرة الاستمرار وأصدر إشارة "انعكاس 🔄" في الاتجاه الجديد فوراً.
- الهدف: تحويل الضعف في الاتجاه الحالي إلى قوة في الاتجاه المعاكس بدلاً من الوقوف متفرجاً.
- مثال: كنت تحلل للشراء، ظهرت شمعة حمراء ابتلعت نصف الخضراء ← القرار: بيع 🔴 (لأن البائعين سيطروا على السيولة).
"""
        
        historical_peak_rule = """
【التعديل 3 - بروتوكول القمم التاريخية (السيولة الذكية)】
- المنطق: القمة التاريخية هي "منطقة انفجار".
- شرط الشراء الوحيد: شمعة "ماروبوزو" ممتلئة بنسبة >90% تخترق القمة وتستقر فوقها بـ 3 نقاط. (هذا هو الانفجار الحقيقي).
- شرط البيع (فخ السيولة): أي ذيل (Wick) يلمس القمة ويرتد، أو شمعة جسمها < 50% عند القمة ← بيع فوري 🔴 (لأن المؤسسات تسحب السيولة الآن).
- الهدف: اتخاذ قرار قطعي (إما اختراق أو ارتداد) ومنع "الاحتفاظ" عند القمم.
"""
        
        v7_updates = """
🚀 **تحديث: بروتوكول الربط الزمني والهجوم الشامل (V7.0)**

1. **قاعدة السمكة الكبيرة (Higher Timeframe Alignment):**
   - القانون: قبل إصدار أي إشارة على فريم الثواني (S5, S10, S15)، يجب على البوت النظر إلى هيكل فريم الدقيقة (M1) أو (M5).
   - التنفيذ: إذا كان الفريم الكبير صاعداً، تُقبل صفقات الشراء بـ 3 نقاط اختراق. إذا كان الفريم الكبير هابطاً، يُمنع الشراء تماماً عند القمم ويُستبدل ببحث فوري عن "إشارة انعكاس" (Reversal) لركوب موجة الفريم الكبير.
   - الهدف: منع الدخول عكس الاتجاه العام (Trend is your friend).

2. **مصفوفة "القرار القطعي" (إلغاء الاحتفاظ 🟡):**
   يتم استبدال حالة "الانتظار" بالخيارات التالية بناءً على السلوك الرقمي:
   - الحالة (أ): السعر يلمس المقاومة + اندفاع سريع (+3 نقاط) = شراء فوري 🟢.
   - الحالة (ب): السعر يلمس المقاومة + ظهور فتيلة علوية (Refusal) بـ 2 نقطة = بيع فوري (انعكاس) 🔴.
   - الحالة (ج): السعر عند قمة تاريخية + شمعة ممتلئة (>90%) = شراء اختراق سيولة 🟢.
   - الحالة (د): السعر عند قمة تاريخية + شمعة ضعيفة (<50%) = بيع "فخ سيولة" 🔴.

3. **تحليل سرعة تغير الزخم (Momentum Decay):**
   - "إذا كانت الشمعة الحالية أكبر من متوسط حجم آخر 5 شموع بـ 3 أضعاف، لا تدخل مع الاتجاه؛ السعر الآن في حالة 'إجهاد' (Exhaustion). انتظر 'سحب سيولة' (Correction) ثم ادخل مع الاتجاه الجديد."
   - لماذا؟ لأن الشموع الضخمة جداً غالباً ما تكون هي "نهاية الحركة" وليست بدايتها.

4. **ديناميكية الـ OTC والزخم المفرط:**
   - المنطق: في أسواق الـ OTC، الزخم القوي (>90%) يغلب جميع الفلاتر الزمنية. إذا كان السعر يندفع نحو هدف قريب (<5 نقاط)، ادخل فوراً.
   - الهدف: استغلال "المغناطيس السعري" الذي تتميز به خوارزميات الـ OTC.

5. **بروتوكول "الجوكر الهجومي" (تجاوز دقائق الغدر):**
   - القاعدة: في الدقائق (14, 29, 44, 59)، إذا تبقى أقل من 15 ثانية.
   - القرار: مسموح بالدخول فقط إذا كان السعر في حالة "اندفاع حر" (تجاوز آخر مستوى بـ 3 نقاط).
   - المنطق: الاندفاع في آخر ثوانٍ يعني أن الشمعة القادمة ستفتح بـ "Gap" أو استمرار قوي.
"""
        
        v7_3_updates = """
🔬 **تحديث: بروتوكول التشريح الهيكلي والسيولة العميقة (V7.3)**

1. **الكسر الهيكلي الرقمي (Digital MSB):**
   - المنطق: قياس دقيق لقوة الكسر بدلاً من الملاحظة البصرية فقط
   - شرط القوة: يجب أن يكون حجم شمعة الكسر ≥ 1.5x من متوسط حجم آخر 3 شموع تصحيح
   - شرط الاستقرار: تجاوز المستوى بـ ≥ 5 نقاط كاملة للفريمات السريعة (S5, S10, S15)
   - الهدف: التمييز بين "الكسر الحقيقي" و"سحب السيولة" (Liquidity Grab)

2. **خرائط السيولة المتدرجة (Liquidity Layering):**
   - تقسيم السيولة في الصورة إلى ثلاث طبقات:
     
     أ) **السيولة الداخلية (Internal Liquidity):**
        - الفجوات السعرية الصغيرة (FVG) التي تجذب السعر للتصحيح
        - معامل الجذب: السعر يجب أن يملأ 50% على الأقل قبل الاستمرار
     
     ب) **السيولة الخارجية (External Liquidity):**
        - القمم والقيعان التاريخية (التي طلبت تشديد الرقابة عليها)
        - معامل الاختراق: يتطلب 5 نقاط أمان + شمعة مؤسسية
     
     ج) **سيولة التجزئة (Retail Liquidity):**
        - القيعان المتساوية (Equal Lows) والقمم المتساوية (Equal Highs)
        - القاعدة: السعر يجب أن يضربها قبل الانطلاق (Sweep then Reverse/Break)

3. **قاعدة فلتر الأمان المعزز عند القمم التاريخية:**
   - المنطق الصارم: "إذا كان السعر عند قمة تاريخية، يُمنع الشراء حتى لو كان الزخم 99%"
   - الاستثناء الوحيد المسموح به:
     * ظهور شمعة ماروبوزو (Marubozu) تخترق القمة
     * الإغلاق فوق القمة بـ ≥ 5 نقاط كاملة (وليس 3 نقاط)
     * وجود فجوة سعرية (FVG) خلف الشمعة تؤكد الاندفاع المؤسسي
   - الهدف: منع الوقوع في فخاخ السيولة (Liquidity Traps)

4. **مصفوفة القرار الهيكلي (Structural Decision Matrix):**
   - الحالة (أ) - كسر هيكلي حقيقي:
     * الشرط: حجم ≥ 1.5x + استقرار ≥ 5 نقاط + FVG خلفي
     * القرار: دخول فوري مع الاتجاه 🟢/🔴
   
   - الحالة (ب) - سحب سيولة (Liquidity Sweep):
     * الشرط: لمس القمة + ارتداد + عدم استقرار ≥ 5 نقاط
     * القرار: انعكاس فوري (Reversal) 🔄
   
   - الحالة (ج) - فخ سيولة (Liquidity Trap):
     * الشرط: قمة تاريخية + شمعة ضعيفة (<50%) + ارتداد سريع
     * القرار: دخول عكسي (Contrarian Entry) 🔄
   
   - الحالة (د) - تذبذب غير واضح:
     * الشرط: عدم استيفاء شروط الكسر أو الانعكاس
     * القرار: احتفاظ 🟡 وانتظار تأكيد

5. **قانون التوازن الهيكلي (Structural Balance Rule):**
   - القاعدة: في الفريمات السريعة (S5-S30)، يجب أن يكون هناك توازن بين:
     * قوة الكسر (Volume Surge) ≥ 1.5x
     * مسافة الأمان (Safety Distance) ≥ 5 نقاط
     * وجود فجوة سعرية (Institutional FVG)
   - إذا تخلف شرط واحد، تتحول التوصية من "دخول" إلى "مراقبة وانتظار"
"""
        
        v7_4_updates = """
🔴 **تحديث صارم V7.4 - بروتوكول القمم التاريخية**

【القاعدة الذهبية】 الزخم العالي عند القمة هو "فخ سيولة" حتى يثبت العكس.

- يُمنع الشراء عند القمة التاريخية (أو منطقة 5 نقاط منها) إلا إذا تحقق **"الثالوث المؤسسي"**:
   1. **اختراق حقيقي:** السعر الحالي > القمة بـ +5 نقاط كاملة.
   2. **قوة الكسر:** شمعة Marubozu ممتلئة بنسبة > 90%.
   3. **الحجم:** حجم الشمعة الحالية ≥ 1.5x من متوسط آخر 3 شموع.

- إذا لم يتحقق "الثالوث"، القرار الإلزامي هو (سحب سيولة 🔄) ودخول بيع 🔴 فور ارتداد السعر بـ 2 نقطة تحت القمة.

【قانون إجهاد الموجة - Rule 7.12】
- إذا كانت الشمعة الحالية أكبر من متوسط آخر 5 شموع بـ 3 أضعاف (300%)، يُمنع الدخول مع الاتجاه.
- التفسير: هذا "شراء هلع" أو "بيع هلع" (Climax Action) وغالباً ما يتبعه انعكاس فوري.
- الإجراء: انتظر ظهور إشارة "انعكاس فوري 🔄" بدلاً من ملاحقة السعر.
"""
        
        v7_5_final_updates = """
🔒 **تحديث نهائي V7.5 FINAL - فلاتر الأمان المتقدمة (حل مشكلة الصفقات الخاطئة)**

【1. خوارزمية المسح الاستبعادي الكامل (Complete Exclusion Scan)】
   - تعمل كـ "حواجز أمان" متسلسلة قبل أي قرار
   - إذا فشل أي شرط، يتم رفض الصفقة فوراً
   - الحواجز: القمم التاريخية ← الابتلاع ← الإجهاد ← الرفض السعري ← التوقيت ← دقائق الغدر

【2. فلتر "منطقة القتل الرقمية" (5 Points Safety Buffer)】
   - إذا كان السعر ضمن 5 نقاط من قمة تاريخية:
     * يُمنع الشراء إلا بتحقق "الثالوث المؤسسي" (≥5 نقاط + حجم ≥1.5x + جسم >90% + FVG)
     * إذا لم يتحقق الثالوث ← القرار الإلزامي: انعكاس فوري (نسبة نجاح 95%)

【3. قانون الابتلاع كشرط إجباري (50% Kill Switch)】
   - إذا ابتلعت شمعة عكسية 50% من الشمعة السابقة:
     * إلغاء فوري لأي صفقة مع الاتجاه القديم
     * تحويل فوري للاتجاه المعاكس (انعكاس) 🔄
     * نسبة نجاح 85-90%

【4. فلتر إجهاد الموجة (Exhaustion Filter)】
   - إذا كانت الشمعة الحالية > 3 أضعاف متوسط آخر 5 شموع:
     * منع الدخول مع الاتجاه (إجهاد سعري)
     * انتظر تصحيح أو انعكاس
     * نسبة نجاح الاحتفاظ 80%

【5. فلتر نوع السوق (OTC vs Real Market)】
   - في OTC: الفتايل الطويلة عند المستويات = فخ سيولة
   - في OTC: الشموع الضخمة جداً عند القمم = تفريغ عقود
   - في السوق العادي: تطبيق القواعد القياسية

【6. نافذة التنفيذ الذهبية الصارمة (Golden Execution Window)】
   - فريمات الثواني: الدخول مسموح فقط بين الثانية 5 والثانية 25
   - فريمات الدقائق: الدخول مسموح فقط بين الثانية 10 والثانية 40
   - خارج هذه النوافذ: احتفاظ إلزامي (نسبة نجاح منخفضة)

【7. معادلة ترجيح النجاح المعدلة (Weighted Success Score V7.5)】
   - 35% لمسافة الأمان عن القمم/القيعان (الأولوية القصوى)
   - 25% لقوة الحجم المؤسسي (سيولة الحيتان)
   - 20% لإمكانية الانعكاس المحتمل (جديد)
   - 10% لتوافق الإطارات الزمنية
   - 10% لشكل الشمعة (الزخم البصري)

【8. تنبيه فخ السيولة المحسن (Liquidity Trap Alert V7.5)】
   - يتم تفعيله فوراً عند اكتشاف أي من:
     * قمة تاريخية + مسافة < 5 نقاط + حجم < 1.5x
     * قمة تاريخية + فتيلة علوية > 40%
     * قمة تاريخية + جسم شمعة < 50%
   - الإجراء: إلغاء الشراء وتحويل للبيع الانعكاسي فوراً

🎯 **الخلاصة النهائية V7.5 FINAL:**
   - الأولوية للانعكاس (Reversal) على الاستمرار (Continuation)
   - السيولة تسبق الزخم - لا تشتري عند القمم
   - المسافة الرقمية (5 نقاط) أهم من شكل الشمعة
   - نافذة التنفيذ الذهبية تحمي من تذبذب البداية وغدر النهاية
"""
        
        decision_matrix_v7_5 = """
📊 **مصفوفة القرار القطعي النهائية (V7.5 FINAL):**

| الحالة الفنية | الشرط | القرار النهائي | نسبة النجاح |
|---------------|-------|----------------|-------------|
| قمة تاريخية + ثالوث مؤسسي | ≥5 نقاط + حجم ≥1.5x + جسم >90% + FVG | كسر هيكلي 💥 (شراء) | 98% |
| قمة تاريخية + فتل طويل (>40%) | ذيل علوي + ارتداد | فخ سيولة 🔄 (بيع) | 95% |
| قمة تاريخية + جسم ضعيف (<50%) | عدم اختراق حقيقي | فخ سيولة 🔄 (بيع) | 95% |
| قمة تاريخية + مسافة <5 نقاط | بدون ثالوث مؤسسي | انعكاس فوري 🔄 (بيع) | 95% |
| شمعة عكسية تبتلع >50% | ابتلاع الشمعة السابقة | انعكاس فوري 🔄 | 85-90% |
| شمعة >3x المتوسط | إجهاد سعري | احتفاظ 🟡 | 80% |
| خارج نافذة التنفيذ | وقت غير مناسب | احتفاظ 🟡 | 70% |
| زخم في منتصف الطريق | حجم ≥1.3x + جسم >80% | دخول مع الاتجاه 🟢/🔴 | 90% |
"""
        
        all_updates_section = f"""
⚡ **جميع التحديثات مجمعة (V7.5 FINAL - الحل النهائي):**

{three_points_rule}

{fifty_percent_rule}

{historical_peak_rule}

{v7_updates}

{v7_3_updates}

{v7_4_updates}

{v7_5_final_updates}

{decision_matrix_v7_5}
"""

        # ============================================
        # 14. البرومبت المتكامل النهائي (محدث مع جميع التحليلات)
        # ============================================
        MAIN_PROMPT = f"""
أنت محلل فني خبير متكامل تجمع كل مدارس التحليل المتقدمة:
SMC + ICT + WYCKOFF + VSA + FIBONACCI + SMT + KILL ZONES + PO3 + ROUND NUMBERS + VOLUME PROFILE + MARKET PSYCHOLOGY + CANDLESTICK PATTERNS + LIQUIDITY ANALYSIS + TREND ANALYSIS + GAP ANALYSIS + Liquidity Swing + MARKET STRUCTURE + SUPPLY/DEMAND + Ultra Market Structure + MARKET CYCLES.

أنت محلل فني خبير متخصص في تحليل الهيكل الفائق للسوق (Ultra Market Structure) وتأرجحات السيولة (Liquidity Swing) وفق منهجية ICT 2024 والمفاهيم المؤسسية المتقدمة.

مهمتك تحليل الشارت المرفق بدقة جراحية وإصدار تحليل هيكلي متكامل مع التركيز على المستويات المتعددة للهيكل ونقاط السيولة المتأرجحة.

🔍 التعليمات الأساسية:
• ركز على استخراج البيانات بدقة من الصورة
• حدد جميع مستويات الهيكل من الميكروي إلى الفائق
• حلل تأرجحات السيولة في القمم والقيعان
• حدد نقاط التحول الهيكلي بدقة
• أعط توصيات بناءً على التحليل الهيكلي والسيولة

📊 البيانات الأساسية المستخرجة من الصورة:
• الزوج/السهم: [استخراج من الصورة]
• الإطار الزمني: [استخراج من الصورة]
• آخر سعر: [استخراج من الصورة]
• وقت الشمعة: [استخراج من الصورة]
• حجم التداول: [استخراج من الصورة]

🔬 **تحليل Ultra Market Structure (الهيكل الفائق للسوق)**

📊 **مستويات الهيكل المتعددة:**
• 🔬 Micro Structure (1-3 شموع): [تحليل تفصيلي لآخر 3 شموع - نمط التكوين الميكروي - سلوك الشموع الفردية - نقاط الارتكاز الدقيقة]
• 📊 Minor Structure (3-8 شموع): [تكوين القمم والقيعان المحلية - حالة الهيكل الصغير - اتجاه الحركة المحلية - زوايا الميل]
• 🏗️ Primary Structure (8-21 شمعة): [الاتجاه الرئيسي - قوة الهيكل الأساسي - درجة تماسك الاتجاه - خطوط الاتجاه الأولية]
• 🏛️ Major Structure (21-55 شمعة): [الدورات السعرية الكبرى - نقاط التحول الرئيسية - مستويات الدعم/المقاومة الرئيسية - القنوات السعرية]
• 🌍 Macro Structure (55-144 شمعة): [الأنماط الهيكلية طويلة المدى - الترند الكلي - المناطق السعرية التاريخية]
• 💫 Ultra Structure (144+ شمعة): [المستويات الهيكلية الحاسمة - نقاط التحول التاريخية - مناطق الانعكاس الكبرى - السيولة التراكمية]

🔄 **نقاط التحول الهيكلي (Structural Inflection Points):**
• MSS (Market Structure Shift): [صاعد/هابط] - [نسبة الثقة: %] - [السعر: X.XXXX] - [تأكيد التحول: نعم/لا] - [السبب: كسر قمة/قاع/نمط]
• BOS (Break of Structure): [مؤكد/غير مؤكد] - [اتجاه الكسر: صاعد/هابط] - [قوة الكسر: %] - [مسافة الاختراق: X نقطة] - [حجم الكسر: كبير/متوسط/صغير]
• CHoCH (Change of Character): [مكتشف/غير مكتشف] - [نوع التغير: توسع/انكماش/تحول] - [قوة التغير: %] - [الشمعة المؤكدة: رقم X]
• MSB (Market Structure Break): [مؤكد/غير مؤكد] - [نوع الكسر: حقيقي/كاذب] - [نسبة النجاح المتوقعة: %] - [منطقة إعادة الاختبار: X.XXXX]
• UTMS (Ultra Trend Structure): [حالة الاتجاه الفائق: استمرار/انعكاس/تصحيح] - [قوة الاتجاه: %] - [عمر الاتجاه: X شمعة]

🎯 **تحليل التوافق الهيكلي:**
• التوافق بين المستويات: [متوافق %] - [أقوى مستوى: المستوى X] - [مستوى التوافق: عالي/متوسط/منخفض]
• الانعكاس الهيكلي المتوقع: [خلال X شموع] - [عند السعر: X.XXXX] - [نسبة الثقة: %] - [الإشارات المصاحبة: ........]
• مناطق عدم التوازن الهيكلي: [السعر X.XXXX - X.XXXX] - [حجم عدم التوازن: كبير/متوسط/صغير] - [سبب عدم التوازن: فجوة/زخم/حجم]
• القوة النسبية للهيكل: [صاعد %] - [هابط %] - [متذبذب %] - [مؤشر الهيمنة: ........]

💧 **تحليل Liquidity Swing (تأرجح السيولة)**

🌊 **مستويات السيولة المتأرجحة (Swing Liquidity Levels):**

【سيولة القمم المتأرجحة (Swing High Liquidity)】
• القمة الأولى (Swing High 1):
  - السعر: [X.XXXX]
  - قوة السيولة: [عالية/متوسطة/منخفضة]
  - نسبة التراكم: [%]
  - نوع السيولة: [سيولة بيع / سيولة شراء / سيولة مختلطة]
  - عدد مرات الاختبار: [X مرة]
  - احتمالية الاصطياد: [%]
  - الوقت المتوقع للاصطياد: [خلال X شموع]
  - المسافة الحالية: [X نقطة]

• القمة الثانية (Swing High 2):
  - السعر: [X.XXXX]
  - قوة السيولة: [عالية/متوسطة/منخفضة]
  - نسبة التراكم: [%]
  - نوع السيولة: [سيولة بيع / سيولة شراء / سيولة مختلطة]
  - عدد مرات الاختبار: [X مرة]
  - احتمالية الاصطياد: [%]
  - الوقت المتوقع للاصطياد: [خلال X شموع]
  - المسافة الحالية: [X نقطة]

• القمة الثالثة (Swing High 3):
  - السعر: [X.XXXX]
  - قوة السيولة: [عالية/متوسطة/منخفضة]
  - نسبة التراكم: [%]
  - نوع السيولة: [سيولة بيع / سيولة شراء / سيولة مختلطة]
  - عدد مرات الاختبار: [X مرة]
  - احتمالية الاصطياد: [%]
  - الوقت المتوقع للاصطياد: [خلال X شموع]
  - المسافة الحالية: [X نقطة]

【سيولة القيعان المتأرجحة (Swing Low Liquidity)】
• القاع الأول (Swing Low 1):
  - السعر: [X.XXXX]
  - قوة السيولة: [عالية/متوسطة/منخفضة]
  - نسبة التراكم: [%]
  - نوع السيولة: [سيولة شراء / سيولة بيع / سيولة مختلطة]
  - عدد مرات الاختبار: [X مرة]
  - احتمالية الاصطياد: [%]
  - الوقت المتوقع للاصطياد: [خلال X شموع]
  - المسافة الحالية: [X نقطة]

• القاع الثاني (Swing Low 2):
  - السعر: [X.XXXX]
  - قوة السيولة: [عالية/متوسطة/منخفضة]
  - نسبة التراكم: [%]
  - نوع السيولة: [سيولة شراء / سيولة بيع / سيولة مختلطة]
  - عدد مرات الاختبار: [X مرة]
  - احتمالية الاصطياد: [%]
  - الوقت المتوقع للاصطياد: [خلال X شموع]
  - المسافة الحالية: [X نقطة]

• القاع الثالث (Swing Low 3):
  - السعر: [X.XXXX]
  - قوة السيولة: [عالية/متوسطة/منخفضة]
  - نسبة التراكم: [%]
  - نوع السيولة: [سيولة شراء / سيولة بيع / سيولة مختلطة]
  - عدد مرات الاختبار: [X مرة]
  - احتمالية الاصطياد: [%]
  - الوقت المتوقع للاصطياد: [خلال X شموع]
  - المسافة الحالية: [X نقطة]

🎯 **تحليل تدفق السيولة (Liquidity Flow Analysis):**
• اتجاه تدفق السيولة: [صاعد/هابط/جانبي] - [قوة التدفق: %]
• مناطق تجمع السيولة (Liquidity Pools): [السعر X.XXXX - X.XXXX] - [قوة المنطقة: عالية/متوسطة/منخفضة]
• السيولة المعلقة (Pending Liquidity): [كمية: كبيرة/متوسطة/صغيرة] - [الاتجاه المتوقع: ........]
• فجوات السيولة (Liquidity Gaps): [موجودة/غير موجودة] - [السعر X.XXXX - X.XXXX] - [نوع الفجوة: ........]
• مؤشر جذب السيولة: [نسبة الجذب: %] - [المستوى الأكثر جذباً: X.XXXX]

⚠️ **تحذيرات السيولة (Liquidity Alerts):**
• 🚨 منطقة صيد وقف (Stop Hunt) محتملة: [السعر X.XXXX] - [السبب: ........]
• 🚨 فخ سيولة (Liquidity Trap): [نشط/غير نشط] - [النوع: شراء/بيع] - [السعر X.XXXX]
• 🚨 سيولة مؤسسية (Institutional Liquidity): [مكتشفة/غير مكتشفة] - [الكمية: ........]
• 🚨 اختراق سيولة وشيك: [خلال X شموع] - [الاتجاه المتوقع: ........]

🔄 **الربط بين الهيكل الفائق والسيولة (Structure-Liquidity Nexus):**

【مناطق التقاء الهيكل مع السيولة】
• المنطقة الأولى: [السعر X.XXXX] - [نوع الهيكل: ........] - [نوع السيولة: ........] - [قوة الالتقاء: %]
• المنطقة الثانية: [السعر X.XXXX] - [نوع الهيكل: ........] - [نوع السيولة: ........] - [قوة الالتقاء: %]
• المنطقة الثالثة: [السعر X.XXXX] - [نوع الهيكل: ........] - [نوع السيولة: ........] - [قوة الالتقاء: %]

【المستويات الحاسمة (Critical Levels)】
• مستوى الانعكاس المحتمل (Reversal Level): [السعر X.XXXX] - [السبب: هيكل + سيولة + نمط]
• مستوى الاستمرار المحتمل (Continuation Level): [السعر X.XXXX] - [السبب: هيكل + سيولة + زخم]
• مستوى الاختراق (Breakout Level): [السعر X.XXXX] - [الشرط: اختراق مع حجم > 1.5x]

【سيناريوهات الحركة المتوقعة】
• السيناريو الرئيسي (Primary Scenario): [احتمالية %] - [الوصف: ........] - [الهدف: X.XXXX] - [وقف: X.XXXX]
• السيناريو البديل (Alternative Scenario): [احتمالية %] - [الوصف: ........] - [الهدف: X.XXXX] - [وقف: X.XXXX]
• السيناريو المستبعد (Invalid Scenario): [الشرط: كسر X.XXXX]


مهمتك تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية دقيقة بنظام متعدد الطبقات مع تطبيق جميع الاستراتيجيات المتقدمة.

🔍 **التوجيه الأساسي: التركيز الكامل على استخراج البيانات وتحليل الصورة**
أنت ملزم بالتركيز التام على الصورة المرفقة فقط. يجب أن تستخرج كل البيانات والأرقام من الصورة بكامل قدراتك البصرية.

📊 **البيانات الأساسية:**
• إطار الشموع: {candle}
• فريم التحقق: {verification_timeframe}
• استراتيجية التداول: {trading_strategy}
• الوقت الحالي (UTC): {current_time.strftime('%H:%M:%S')}
• جلسة السوق الحالية: {current_session['name']}
• حالة السيولة: {current_session['volatility']}
• الوقت المتبقي: {seconds_remaining} ثانية

🎯 التنسيق المطلوب للإجابة (الالتزام حرفياً) وبشكل مختصر:

📊 التحليل الفني المتقدم:
• بروتوكول الفشل الذاتي: [الصورة صالحة / الصورة غير صالحة - يرجى إرسال صورة أوضح]
• الوقت على المنصة (المرجع): [الوقت المستخرج من الصورة]
• حالة الهيكل: [صاعد/هابط/متذبذب] + (توافق 4/4 إطارات: نعم / لا)
• خريطة السيولة: [أقرب فخ سيولة Inducement] + [مناطق السيولة المستهدفة]

🎯 الإشارة التنفيذية:
• السعر الحالي: [السعر الدقيق من الشارت]
• حالة الشمعة: [مفتوحة / مغلقة] - الوقت المتبقي: [الوقت الدقيق من ساعة المنصة] 

• القرار الفني: [شراء 🟢 / بيع 🔴 / كسر هيكلي صاعد 💥🟢 / كسر هيكلي هابط 💥🔴 / سحب سيولة صاعد 🔄🟢 / سحب سيولة هابط 🔄🔴 / انعكاس فوري صاعد 🔄🟢 / انعكاس فوري هابط 🔄🔴 / فخ سيولة شراء 🚨🟢 / فخ سيولة بيع 🚨🔴 / انتظار 🟡]

• قوة الإشارة: [عالية جدا 💥 / عالية 🔥 / متوسطة ⚡ / ضعيفة ❄️]
• نقطة الدخول : [السعر بعد معامل الانزلاق إن وجد - أو نقطة إعادة الاختبار]
• نوع الدخول 🚨: [دخول مباشر / إعادة اختبار / انعكاس فوري]
• الأهداف الربحية:
  🎯 TP1: [السعر] - [سحب أول سيولة داخلية] - [احتمالية الوصول: عالية/متوسطة/منخفضة]
  🎯 TP2: [السعر] - [الهدف الرئيسي - منطقة عرض/طلب قوية] - [احتمالية الوصول: عالية/متوسطة/منخفضة]
  🎯 TP3: [السعر] - [سيولة خارجية أو سد فجوة سعرية] - [احتمالية الوصول: عالية/متوسطة/منخفضة]
• وقف الخسارة: [السعر] - [مع 3 طبقات حماية]
• المدة المتوقعة: [عدد الدقائق/الساعات]
• وقت الذروة المتوقع: [خلال الـ X شموع القادمة / بعد X دقيقة]

⚠️ إدارة المخاطر:
• مستوى الثقة: [0-100]٪ = [💥/🔥/⚡/🔄/❄️/🚫]
• نسبة نجاح الصفقة المتوقعة : [XX]% - [التقييم: ممتاز/جيد/متوسط/ضعيف]
• نقطة الإلغاء: [السعر الذي يفسد التحليل]
• 🚨 تنبيه فخ السيولة: [نشط / غير نشط] - [إذا نشط: تفاصيل التنبيه]

الآن قم بتحليل الشارت المرفق بتطبيق جميع التحليلات المتقدمة وأعطني الإجابة بالتنسيق المطلوب.
"""
        
        headers = {"Authorization": f"Bearer {MISTRAL_KEY}", "Content-Type": "application/json"}
        
        GENERATION_CONFIG = {
            "max_tokens": 950,
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
            print(f"خطأ في التحليل: {response.status_code} - {response.text}")
            try:
                await wait_msg.edit_text(f"❌ خطأ في التحليل: {response.status_code}")
            except Exception as e:
                if "Message is not modified" not in str(e):
                    await update.message.reply_text(f"❌ خطأ في التحليل: {response.status_code}")
            return
        
        analysis_result = response.json()['choices'][0]['message']['content'].strip()
        
        # تحسين النتائج
        analysis_result = clean_repeated_text(analysis_result)
        save_analysis_context(user_id, analysis_result)
        
        # إضافة خاتمة التحليل
        in_golden_window = is_in_golden_execution_window(current_second, candle)
        golden_window_status = "داخل النافذة الذهبية ✅" if in_golden_window else "خارج النافذة الذهبية ❌"
        seconds_passed = 60 - seconds_remaining
        
        footer = f"""
━━━━━━━━━━━━━━━
🔧 **الإعدادات المستخدمة:**
• 🕧 فريم التحليل: {candle}
• 🕧 فريم التحقق: {verification_timeframe}
• 🕧 استراتيجية: {trading_strategy}
• 🕧 جلسة السوق: {current_session['name']}
• ⏱️ الثواني المنقضية: {seconds_passed} ثانية
• ⏱️ نافذة التنفيذ: {golden_window_status}
━━━━━━━━━━━━━━━
🟢 الساعة : {current_time.strftime('%H:%M:%S')} UTC
🤖 ** Obeida Trading - التحليل المتكامل V8.0 **
"""
        
        full_result = analysis_result + footer
        full_result = clean_repeated_text(full_result)
        
        # تنسيق النتيجة مع زر نسخ
        formatted_result = format_analysis_with_copy_button(full_result)
        
        # إرسال النتيجة
        try:
            if len(formatted_result) > 4000:
                parts = split_message(formatted_result, 3500)
                await wait_msg.edit_text(parts[0], parse_mode="Markdown")
                for part in parts[1:]:
                    await update.message.reply_text(part, parse_mode="Markdown")
            else:
                await wait_msg.edit_text(formatted_result, parse_mode="Markdown")
        except Exception as e:
            error_msg = str(e)
            if "can't find end of the entity" in error_msg:
                await wait_msg.edit_text(full_result)
            else:
                await update.message.reply_text(full_result)
        
        # العودة للقائمة الرئيسية
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
            await wait_msg.edit_text("⏱️ تجاوز الوقت المحدد. حاول مرة أخرى.")
        except:
            await update.message.reply_text("⏱️ تجاوز الوقت المحدد. حاول مرة أخرى.")
        return "⏱️ تجاوز الوقت المحدد"
    except Exception as e:
        print(f"❌ خطأ في تحليل الصورة: {traceback.format_exc()}")
        error_msg = f"❌ حدث خطأ في تحليل الصورة: {str(e)[:200]}\nيرجى المحاولة مرة أخرى."
        
        try:
            await wait_msg.edit_text(error_msg)
        except:
            await update.message.reply_text(error_msg)
        return error_msg

async def timezone_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    current_tz = get_user_timezone(user_id)
    
    local_time = get_localized_time(user_id)
    time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
    
    gmt_keys = list(TIMEZONES_BY_GMT.keys())
    
    def gmt_sort_key(gmt):
        try:
            sign = 1 if '+' in gmt else -1
            hours = int(gmt.split(':')[0].replace('GMT', '').replace('+', '').replace('-', ''))
            return sign * hours
        except:
            return 0
    
    gmt_keys.sort(key=gmt_sort_key)
    
    keyboard = []
    row = []
    
    for i, gmt in enumerate(gmt_keys):
        display_name = gmt
        if gmt in TIMEZONES_BY_GMT:
            display_name = f"{gmt} ({TIMEZONES_BY_GMT[gmt]['name']})"
        
        row.append(display_name[:20])
        
        if len(row) == 2:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append(["🔍 بحث عن دولة", "🌍 جميع الدول"])
    keyboard.append(["🕒 منطقتي الحالية", "الرجوع للقائمة الرئيسية"])
    
    await update.message.reply_text(
        f"🌍 **المناطق الزمنية**\n\n"
        f"🕒 **منطقتك الحالية:** {current_tz}\n"
        f"⏰ **الوقت المحلي:** {time_str}\n\n"
        f"اختر المنطقة الزمنية المناسبة لك من القائمة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
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
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "🕒 منطقتي الحالية":
        current_tz = get_user_timezone(user_id)
        local_time = get_localized_time(user_id)
        time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
        
        countries_in_tz = []
        for country, tz in COUNTRY_TIMEZONE_MAP.items():
            if tz == current_tz:
                countries_in_tz.append(country)
        
        countries_text = "\n".join([f"• {country}" for country in countries_in_tz[:10]])
        if len(countries_in_tz) > 10:
            countries_text += f"\n• ... و {len(countries_in_tz) - 10} دولة أخرى"
        
        await update.message.reply_text(
            f"📍 **معلومات منطقتك الزمنية**\n\n"
            f"🕒 **المنطقة:** {current_tz}\n"
            f"⏰ **الوقت الحالي:** {time_str}\n\n"
            f"🌍 **الدول في هذه المنطقة:**\n{countries_text if countries_text else 'لا توجد دول محددة'}",
            parse_mode="Markdown"
        )
        return TIMEZONE_MENU
    
    elif user_message == "🔍 بحث عن دولة":
        await update.message.reply_text(
            "🔍 **أدخل اسم الدولة**\n\n"
            "مثال: `مصر`، `السعودية`، `فلسطين`، `اليابان`\n\n"
            "أو اكتب `قائمة الدول` لعرض جميع الدول المتاحة",
            parse_mode="Markdown"
        )
        return SEARCH_COUNTRY
    
    elif user_message == "🌍 جميع الدول":
        keyboard = []
        row = []
        
        countries = list(COUNTRY_TIMEZONE_MAP.keys())
        for i, country in enumerate(countries[:30]):
            tz = COUNTRY_TIMEZONE_MAP[country]
            display = f"{country} {tz}"
            row.append(display[:20])
            
            if len(row) == 2:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        keyboard.append(["🔙 رجوع", "الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "🌍 **قائمة الدول مع مناطقها الزمنية**\n\n"
            "اختر دولة لتعيين منطقتك الزمنية:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SEARCH_COUNTRY
    
    selected_gmt = None
    for gmt in TIMEZONES_BY_GMT.keys():
        if user_message.startswith(gmt) or gmt in user_message:
            selected_gmt = gmt
            break
    
    if selected_gmt:
        set_user_timezone(user_id, selected_gmt)
        
        tz_info = TIMEZONES_BY_GMT[selected_gmt]
        local_time = get_localized_time(user_id)
        time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
        
        countries_in_tz = []
        for country, tz in COUNTRY_TIMEZONE_MAP.items():
            if tz == selected_gmt:
                countries_in_tz.append(country)
        
        countries_text = "\n".join([f"• {country}" for country in countries_in_tz[:10]])
        if len(countries_in_tz) > 10:
            countries_text += f"\n• ... و {len(countries_in_tz) - 10} دولة أخرى"
        
        await update.message.reply_text(
            f"✅ **تم تعيين منطقتك الزمنية بنجاح!**\n\n"
            f"🕒 **المنطقة:** {selected_gmt}\n"
            f"📌 **الاسم:** {tz_info['name']}\n"
            f"⏰ **الوقت المحلي:** {time_str}\n\n"
            f"🌍 **الدول في هذه المنطقة:**\n{countries_text if countries_text else 'لا توجد دول محددة'}",
            parse_mode="Markdown"
        )
        
        return TIMEZONE_MENU
    
    await update.message.reply_text(
        "❌ لم أتمكن من العثور على المنطقة الزمنية المحددة.\n"
        "الرجاء الاختيار من القائمة أو البحث عن دولة.",
        reply_markup=ReplyKeyboardMarkup([["🔙 رجوع"]], resize_keyboard=True)
    )
    return TIMEZONE_MENU

async def handle_country_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية" or user_message == "🔙 رجوع":
        return await timezone_menu(update, context)
    
    if user_message == "قائمة الدول":
        countries_list = "\n".join([f"• {country}" for country in sorted(COUNTRY_TIMEZONE_MAP.keys())])
        
        for part in split_message(countries_list, 3500):
            await update.message.reply_text(
                f"🌍 **قائمة الدول المتاحة:**\n\n{part}",
                parse_mode="Markdown"
            )
        
        return SEARCH_COUNTRY
    
    found_countries = []
    for country, tz in COUNTRY_TIMEZONE_MAP.items():
        if user_message.lower() in country.lower():
            found_countries.append((country, tz))
    
    if found_countries:
        if len(found_countries) == 1:
            country, tz = found_countries[0]
            set_user_timezone(user_id, tz)
            
            tz_info = TIMEZONES_BY_GMT.get(tz, {"name": "منطقة زمنية"})
            local_time = get_localized_time(user_id)
            time_str = local_time.strftime('%Y-%m-%d %H:%M:%S')
            
            await update.message.reply_text(
                f"✅ **تم تعيين منطقتك الزمنية حسب {country}**\n\n"
                f"🕒 **المنطقة:** {tz}\n"
                f"📌 **الاسم:** {tz_info.get('name', '')}\n"
                f"⏰ **الوقت المحلي:** {time_str}",
                parse_mode="Markdown"
            )
            return await timezone_menu(update, context)
        else:
            keyboard = []
            for country, tz in found_countries[:10]:
                keyboard.append([f"{country} ({tz})"])
            
            keyboard.append(["🔙 رجوع", "الرجوع للقائمة الرئيسية"])
            
            await update.message.reply_text(
                f"🔍 **نتائج البحث عن '{user_message}':**\n\n"
                f"تم العثور على {len(found_countries)} دولة. اختر الدولة المناسبة:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return SEARCH_COUNTRY
    else:
        await update.message.reply_text(
            f"❌ لم يتم العثور على دولة باسم '{user_message}'.\n"
            "تأكد من كتابة الاسم بشكل صحيح أو اختر من القائمة.",
            parse_mode="Markdown"
        )
        return SEARCH_COUNTRY

async def start_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول للوحة التحكم.")
        return MAIN_MENU
    
    bot_status = SubscriptionSystem.get_bot_status()
    health = AutomationSystem.get_system_health()
    stats = SubscriptionSystem.get_stats()
    free_settings = free_analytics.get_settings()
    
    keyboard = [
        ["👥 عرض المستخدمين", "➕ إضافة اشتراك"],
        ["🗑️ إلغاء اشتراك", "⏱️ تمديد اشتراك"],
        ["🚫 حظر مستخدم", "✅ إلغاء حظر"],
        ["📢 إذاعة للمشتركين", "📊 إحصائيات"],
        ["🔧 حالة البوت", "⏰ جدولة"],
        ["🌐 التحكم بالجلسات", "🔄 تحديث تلقائي"],
        ["🎯 التحكم بالتحليلات المجانية"],
        ["➕ إضافة تحليلات مجانية", "🔢 تحديد عدد تحليلات"],
        ["🔙 العودة"]
    ]
    
    status_emoji = {
        'active': '✅',
        'stopped': '⏸️',
        'maintenance': '🔧',
        'scheduled': '⏰'
    }
    
    current_status = status_emoji.get(bot_status['status'], '❓')
    
    health_emoji = '✅' if health['status'] == 'healthy' else '⚠️'
    free_status = "✅ مفعلة" if free_settings['enabled'] else "❌ معطلة"
    
    await update.message.reply_text(
        f"👑 **لوحة تحكم المشرف** 👑\n\n"
        f"🔑 **نظام إدارة الاشتراكات المتطور V2.1**\n\n"
        f"📊 **حالة البوت:** {current_status} {bot_status['status']}\n"
        f"🌐 **الجلسات المسموحة:** {bot_status['session_control']}\n"
        f"🖥️ **صحة النظام:** {health_emoji} CPU: {health['cpu']}% | RAM: {health['memory_used']}%\n"
        f"🎯 **التحليلات المجانية:** {free_status} (العدد الافتراضي: {free_settings['default_count']})\n\n"
        f"📈 **إحصائيات سريعة:**\n"
        f"👥 المستخدمين: {stats.get('total_users', 0)}\n"
        f"✅ النشطين: {stats.get('active_users', 0)}\n"
        f"📊 التحليلات: {stats.get('total_analyses', 0)}\n"
        f"🔢 مستخدمين مع تحليلات مجانية: {stats.get('users_with_free', 0)}\n"
        f"🎯 تحليلات مجانية متبقية: {stats.get('total_free_remaining', 0)}\n\n"
        f"اختر الإجراء المطلوب:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return ADMIN_MENU

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول للوحة التحكم.")
        return MAIN_MENU
    
    # معالجة الإجراءات المعلقة أولاً
    if context.user_data.get('admin_action'):
        if context.user_data['admin_action'] == 'add_free_analyses':
            try:
                parts = user_message.strip().split()
                if len(parts) == 2:
                    target_user_id = int(parts[0])
                    count = int(parts[1])
                    
                    success, message = SubscriptionSystem.add_free_analyses(
                        user_id=target_user_id,
                        count=count,
                        performed_by=user_id
                    )
                    
                    await update.message.reply_text(f"✅ {message}" if success else f"❌ {message}")
                else:
                    await update.message.reply_text("❌ صيغة خاطئة. استخدم: `user_id count`")
                
                context.user_data['admin_action'] = None
                return await start_admin_panel(update, context)
            except ValueError:
                await update.message.reply_text("❌ الرجاء إدخال أرقام صحيحة")
                context.user_data['admin_action'] = None
                return ADMIN_MENU
        
        elif context.user_data['admin_action'] == 'set_free_analyses':
            try:
                parts = user_message.strip().split()
                if len(parts) == 2:
                    target_user_id = int(parts[0])
                    count = int(parts[1])
                    
                    success = SubscriptionSystem.set_free_analyses_count(target_user_id, count)
                    
                    if success:
                        await update.message.reply_text(f"✅ تم تحديد عدد التحليلات إلى {count}")
                    else:
                        await update.message.reply_text("❌ فشل في تحديد العدد")
                else:
                    await update.message.reply_text("❌ صيغة خاطئة. استخدم: `user_id count`")
                
                context.user_data['admin_action'] = None
                return await start_admin_panel(update, context)
            except ValueError:
                await update.message.reply_text("❌ الرجاء إدخال أرقام صحيحة")
                context.user_data['admin_action'] = None
                return ADMIN_MENU
    
    if user_message == "🔙 العودة":
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["📊 معلومات الجلسة", "👤 معلوماتي"],
            ["🌍 المناطق الزمنية"],
            ["👑 لوحة التحكم"]
        ]
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
            for u in users[:20]:
                uid, username, first_name, created, last_active, is_blocked, free_analyses, total_analyses, timezone, sub_name, start_date, end_date, is_active = u
                
                if is_blocked == 1:
                    status = "🚫 محظور"
                elif is_active == 1 and (not end_date or datetime.fromisoformat(end_date) > datetime.now()):
                    status = "✅ نشط"
                else:
                    status = "❌ غير نشط"
                
                display_name = first_name or username or str(uid)
                
                msg += f"• **{display_name}**\n"
                msg += f"  🆔 `{uid}`\n"
                msg += f"  📊 اشتراك: {sub_name or 'لا يوجد'} ({status})\n"
                msg += f"  🔢 تحليلات: {total_analyses} (مجاني: {free_analyses})\n"
                msg += f"  🌍 منطقة: {timezone}\n"
                msg += f"  📅 آخر نشاط: {last_active[:16] if last_active else '-'}\n\n"
            
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
            "مثال: `123456789`",
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
        health = AutomationSystem.get_system_health()
        current_session = get_current_session()
        free_settings = free_analytics.get_settings()
        
        stats_text = (
            "📊 **إحصائيات النظام المتقدم:**\n\n"
            f"👥 إجمالي المستخدمين: {stats.get('total_users', 0)}\n"
            f"✅ نشط اليوم: {stats.get('active_today', 0)}\n"
            f"📅 نشط الأسبوع: {stats.get('active_week', 0)}\n"
            f"🚫 المحظورين: {stats.get('blocked_users', 0)}\n"
            f"✅ المشتركين النشطين: {stats.get('active_users', 0)}\n"
            f"⏳ منتهي قريباً (7 أيام): {stats.get('expiring_soon', 0)}\n"
            f"📈 إجمالي التحليلات: {stats.get('total_analyses', 0)}\n"
            f"🔢 مستخدمين مع تحليلات مجانية: {stats.get('users_with_free', 0)}\n"
            f"🎯 تحليلات مجانية متبقية: {stats.get('total_free_remaining', 0)}\n\n"
            f"🎯 **إعدادات التحليلات المجانية:**\n"
            f"الحالة: {'✅ مفعلة' if free_settings['enabled'] else '❌ معطلة'}\n"
            f"العدد الافتراضي: {free_settings['default_count']}\n"
            f"الحد الأقصى: {free_settings['max_count']}\n\n"
            f"🖥️ **صحة النظام:**\n"
            f"CPU: {health['cpu']}% | RAM: {health['memory_used']}%\n"
            f"القرص: {health['disk_used']}% مستخدم ({health['disk_free']:.1f} جيجا متاح)\n\n"
            f"🌐 **الجلسة الحالية:** {current_session['name']}\n\n"
            f"📊 **الاشتراكات حسب النوع:**\n"
        )
        
        for sub_type, count in stats.get('subscriptions_by_type', {}).items():
            stats_text += f"  • {sub_type}: {count}\n"
        
        for part in split_message(stats_text, 4000):
            await update.message.reply_text(part, parse_mode="Markdown")
        return ADMIN_MENU
    
    elif user_message == "🔧 حالة البوت":
        bot_status = SubscriptionSystem.get_bot_status()
        
        status_text = (
            "🔧 **التحكم بحالة البوت**\n\n"
            f"الحالة الحالية: **{bot_status['status']}**\n"
            f"الجلسات المسموحة: **{bot_status['session_control']}**\n\n"
            "اختر الحالة الجديدة:"
        )
        
        keyboard = [
            ["✅ تشغيل", "⏸️ إيقاف", "🔧 صيانة"],
            ["🔙 العودة للوحة التحكم"]
        ]
        
        await update.message.reply_text(
            status_text,
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SET_BOT_STATUS
    
    elif user_message == "⏰ جدولة":
        await update.message.reply_text(
            "⏰ **جدولة تغيير حالة البوت**\n\n"
            "أرسل الوقت والمدة والصيغة المطلوبة:\n\n"
            "مثال: `stop 30m` (إيقاف بعد 30 دقيقة)\n"
            "مثال: `maintenance 2h` (صيانة بعد ساعتين)\n"
            "مثال: `active 1h` (تشغيل بعد ساعة)\n\n"
            "الوحدات: m (دقائق), h (ساعات)",
            parse_mode="Markdown"
        )
        return SET_BOT_STATUS
    
    elif user_message == "🌐 التحكم بالجلسات":
        keyboard = [
            ["🌏 آسيوية فقط", "🇬🇧 لندن فقط"],
            ["🗽 نيويورك فقط", "🌊 المحيط الهادئ فقط"],
            ["🌍 جميع الجلسات"],
            ["🔄 تلقائي (حسب الوقت)"],
            ["🔙 العودة للوحة التحكم"]
        ]
        
        await update.message.reply_text(
            "🌐 **التحكم بالجلسات المسموحة**\n\n"
            "اختر الجلسات التي تريد السماح بها:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return SET_SESSION_CONTROL
    
    elif user_message == "🔄 تحديث تلقائي":
        AutomationSystem.auto_switch_session()
        await update.message.reply_text(
            "🔄 **تم تحديث الجلسات تلقائياً**\n\n"
            "سيتم إرسال إشعار للمستخدمين النشطين.",
            parse_mode="Markdown"
        )
        return ADMIN_MENU
    
    elif user_message == "🎯 التحكم بالتحليلات المجانية":
        free_settings = free_analytics.get_settings()
        
        keyboard = [
            ["✅ تفعيل التحليلات المجانية", "❌ تعطيل التحليلات المجانية"],
            ["🔢 تغيير العدد الافتراضي", "🔄 إعادة تعيين الكل"],
            ["🔙 العودة للوحة التحكم"]
        ]
        
        await update.message.reply_text(
            f"🎯 **التحكم بالتحليلات المجانية**\n\n"
            f"الحالة الحالية: {'✅ مفعلة' if free_settings['enabled'] else '❌ معطلة'}\n"
            f"العدد الافتراضي: {free_settings['default_count']}\n"
            f"الحد الأقصى: {free_settings['max_count']}\n\n"
            f"اختر الإجراء المطلوب:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return FREE_ANALYSES_SETTINGS
    
    elif user_message == "➕ إضافة تحليلات مجانية":
        await update.message.reply_text(
            "➕ **إضافة تحليلات مجانية لمستخدم**\n\n"
            "أرسل معرف المستخدم وعدد التحليلات:\n"
            "مثال: `123456789 5`",
            parse_mode="Markdown"
        )
        context.user_data['admin_action'] = 'add_free_analyses'
        return ADMIN_MENU
    
    elif user_message == "🔢 تحديد عدد تحليلات":
        await update.message.reply_text(
            "🔢 **تحديد عدد التحليلات المجانية لمستخدم**\n\n"
            "أرسل معرف المستخدم والعدد الجديد:\n"
            "مثال: `123456789 10`",
            parse_mode="Markdown"
        )
        context.user_data['admin_action'] = 'set_free_analyses'
        return ADMIN_MENU
    
    return ADMIN_MENU

async def handle_free_analytics_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return ADMIN_MENU
    
    if user_message == "🔙 العودة للوحة التحكم":
        return await start_admin_panel(update, context)
    
    elif user_message == "✅ تفعيل التحليلات المجانية":
        free_analytics.set_setting('enabled', 'true', user_id)
        await update.message.reply_text("✅ تم تفعيل التحليلات المجانية")
    
    elif user_message == "❌ تعطيل التحليلات المجانية":
        free_analytics.set_setting('enabled', 'false', user_id)
        await update.message.reply_text("✅ تم تعطيل التحليلات المجانية")
    
    elif user_message == "🔢 تغيير العدد الافتراضي":
        await update.message.reply_text(
            "🔢 **تغيير العدد الافتراضي للتحليلات المجانية**\n\n"
            "أرسل الرقم الجديد (1-10):\n"
            "مثال: `5`",
            parse_mode="Markdown"
        )
        context.user_data['setting_action'] = 'set_default_count'
        return FREE_ANALYSES_SETTINGS
    
    elif user_message == "🔄 إعادة تعيين الكل":
        users = SubscriptionSystem.get_all_users()
        default_count = free_analytics.get_settings()['default_count']
        
        count = 0
        for user in users:
            user_id_db = user[0]
            if user_id_db != ADMIN_ID:
                SubscriptionSystem.reset_free_analyses(user_id_db, default_count)
                count += 1
        
        await update.message.reply_text(f"✅ تم إعادة تعيين التحليلات المجانية لـ {count} مستخدم إلى {default_count}")
    
    elif context.user_data.get('setting_action') == 'set_default_count':
        try:
            new_count = int(user_message)
            if 1 <= new_count <= 10:
                free_analytics.set_setting('default_count', new_count, user_id)
                await update.message.reply_text(f"✅ تم تغيير العدد الافتراضي إلى {new_count}")
            else:
                await update.message.reply_text("❌ الرجاء إدخال رقم بين 1 و 10")
        except:
            await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        context.user_data['setting_action'] = None
    
    return await start_admin_panel(update, context)

async def handle_set_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return ADMIN_MENU
    
    if user_message == "🔙 العودة للوحة التحكم":
        return await start_admin_panel(update, context)
    
    if user_message in ["✅ تشغيل", "⏸️ إيقاف", "🔧 صيانة"]:
        status_map = {
            "✅ تشغيل": "active",
            "⏸️ إيقاف": "stopped",
            "🔧 صيانة": "maintenance"
        }
        
        new_status = status_map[user_message]
        SubscriptionSystem.set_bot_status(new_status, session_control='all', updated_by=user_id)
        
        await update.message.reply_text(f"✅ تم تغيير حالة البوت إلى: {user_message}")
        return await start_admin_panel(update, context)
    
    try:
        parts = user_message.split()
        if len(parts) == 2:
            action = parts[0]
            duration_str = parts[1]
            
            if action in ['stop', 'maintenance', 'active']:
                if duration_str.endswith('m'):
                    minutes = int(duration_str[:-1])
                    seconds = minutes * 60
                elif duration_str.endswith('h'):
                    hours = int(duration_str[:-1])
                    seconds = hours * 3600
                else:
                    seconds = int(duration_str)
                
                scheduled_time = (datetime.now() + timedelta(seconds=seconds)).isoformat()
                SubscriptionSystem.set_bot_status('scheduled', scheduled_time, action, updated_by=user_id)
                
                await update.message.reply_text(
                    f"⏰ **تمت الجدولة بنجاح**\n\n"
                    f"الإجراء: {action}\n"
                    f"الوقت: {datetime.fromisoformat(scheduled_time).strftime('%Y-%m-%d %H:%M:%S')}",
                    parse_mode="Markdown"
                )
                return await start_admin_panel(update, context)
    except:
        pass
    
    await update.message.reply_text("❌ صيغة غير صحيحة. الرجاء المحاولة مرة أخرى.")
    return SET_BOT_STATUS

async def handle_session_control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return ADMIN_MENU
    
    if user_message == "🔙 العودة للوحة التحكم":
        return await start_admin_panel(update, context)
    
    if user_message == "🔄 تلقائي (حسب الوقت)":
        AutomationSystem.auto_switch_session()
        await update.message.reply_text(
            "🔄 **تم تفعيل الوضع التلقائي**\n\n"
            "سيتم تبديل الجلسات تلقائياً حسب الوقت العالمي.",
            parse_mode="Markdown"
        )
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

async def handle_add_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    try:
        target_user_id = int(update.message.text.strip())
        context.user_data['target_user_id'] = target_user_id
        
        keyboard = [
            ["🕐 ساعة", "📅 يوم"],
            ["📆 أسبوع", "📅 شهر"],
            ["📅 سنة", "♾️ مدى الحياة"],
            ["إلغاء", "🔙 العودة للوحة التحكم"]
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
    user_id = update.effective_user.id
    duration_choice = update.message.text.strip()
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    if duration_choice == "إلغاء" or duration_choice == "🔙 العودة للوحة التحكم":
        return await start_admin_panel(update, context)
    
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
    
    success, result = SubscriptionSystem.add_subscription(
        user_id=target_user_id,
        subscription_type=subscription_type,
        added_by=user_id
    )
    
    if success:
        sub_info = result
        end_info = ""
        if sub_info['end_date']:
            end_datetime = datetime.fromisoformat(sub_info['end_date'])
            end_info = f"ينتهي في: {end_datetime.strftime('%Y-%m-%d %H:%M')}"
        else:
            end_info = "غير محدد المدة (مدى الحياة)"
        
        SubscriptionSystem.reset_free_analyses(target_user_id, 3)
        
        await update.message.reply_text(
            f"✅ **تم إضافة الاشتراك بنجاح!**\n\n"
            f"👤 المستخدم: `{target_user_id}`\n"
            f"📅 النوع: {sub_info['subscription_name']}\n"
            f"{end_info}",
            parse_mode="Markdown"
        )
        
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
            pass
        
    else:
        await update.message.reply_text(f"❌ فشل في إضافة الاشتراك: {result}")
    
    if 'target_user_id' in context.user_data:
        del context.user_data['target_user_id']
    
    return ADMIN_MENU

async def handle_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    previous_message = update.message.text.strip()
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    last_action = context.user_data.get('last_action')
    
    try:
        if last_action == 'block':
            target_user_id = int(previous_message.split()[0])
            reason = ' '.join(previous_message.split()[1:]) if len(previous_message.split()) > 1 else "Blocked by admin"
            
            success, message = SubscriptionSystem.block_user(target_user_id, reason, performed_by=user_id)
            
            await update.message.reply_text(f"✅ {message}" if success else f"❌ {message}")
            context.user_data['last_action'] = None
            return ADMIN_MENU
        
        elif last_action == 'unblock':
            target_user_id = int(previous_message)
            
            success, message = SubscriptionSystem.unblock_user(target_user_id, performed_by=user_id)
            
            await update.message.reply_text(f"✅ {message}" if success else f"❌ {message}")
            context.user_data['last_action'] = None
            return ADMIN_MENU
        
        else:
            target_user_id = int(previous_message)
            
            if target_user_id == ADMIN_ID:
                await update.message.reply_text("❌ لا يمكن إلغاء اشتراك المشرف الرئيسي.")
                return ADMIN_MENU
            
            success, message = SubscriptionSystem.revoke_subscription(target_user_id, performed_by=user_id)
            
            if success:
                await update.message.reply_text(f"✅ {message}")
                
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=f"⚠️ **تم إلغاء اشتراكك في البوت**\n\n"
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

async def handle_set_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"✅ **تم تمديد اشتراكك في البوت**\n\n{message}",
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

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    broadcast_message = update.message.text.strip()
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
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
        except Exception as e:
            print(f"⚠️ فشل إرسال للمستخدم {target_id}: {e}")
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
    
    /start - بدء البوت والعودة للقائمة الرئيسية
    /help - عرض رسالة المساعدة
    
    ⚙️ **كيفية الاستخدام:**
    1. استخدم أزرار القائمة للتنقل
    2. أرسل صورة الشارت للتحليل
    3. اضبط إعدادات التحليل أولاً
    
    🌐 **جلسات التداول (بالتوقيت العالمي UTC):**
    
    🌏 **الآسيوية (00:00-08:00):**
    🇬🇧 **لندن (08:00-16:00):**
    🗽 **نيويورك (13:00-21:00):**
    🌊 **المحيط الهادئ (21:00-00:00):**

    ⏱️ **خيارات مدة الصفقة:**
    • **قصير (1m-15m)**: تنفيذ سريع، مخاطر منخفضة
    • **متوسط (4h-Daily)**: انتظار أيام، مخاطر متوسطة
    • **طويل (Weekly-Monthly)**: استثمار طويل، مخاطر مرتفعة
    
    🤯 **أنواع القرار الفني:**
    • 🟢 شراء: السعر صاعد - ادخل مع الاتجاه
    • 🔴 بيع: السعر هابط - ادخل مع الاتجاه
    • 💥 كسر هيكلي: تغير الاتجاه (صاعد ← هابط أو العكس)
    • 🔄 سحب سيولة: عكس مؤقت ثم استمرار الاتجاه الأصلي
    • 🔄 انعكاس فوري: تغير سريع ثم استقرار
    
    🔑 **التحليلات المجانية:**
    • 3 تحليلات مجانية لكل مستخدم جديد
    • يمكن للمشرف تفعيل/تعطيل التحليلات المجانية
    • الاشتراك يمنحك تحليلات غير محدودة
    
    📞 للتواصل مع المشرف: @Sz2zv
    """
    
    formatted_help = format_as_copyable_code(help_text, "markdown")
    await update.message.reply_text(formatted_help, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        cleanup_user_data(context, update.effective_user.id)
    
    await update.message.reply_text(
        "تم الإلغاء وتم تنظيف الذاكرة. اكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    while True:
        try:
            await asyncio.sleep(60)
            cleanup_old_images()
            cleanup_old_database_records()
            
            bot_status = SubscriptionSystem.get_bot_status()
            if bot_status['status'] == 'scheduled' and bot_status['scheduled_time']:
                try:
                    scheduled_time = datetime.fromisoformat(bot_status['scheduled_time'])
                    if datetime.now() >= scheduled_time:
                        new_status = bot_status['scheduled_action']
                        SubscriptionSystem.set_bot_status(
                            new_status, 
                            session_control=bot_status['session_control'], 
                            updated_by=ADMIN_ID
                        )
                        print(f"⏰ تم تنفيذ الإجراء المجدول: {new_status}")
                except Exception as e:
                    print(f"⚠️ خطأ في تنفيذ الجدولة: {e}")
            
            AutomationSystem.auto_switch_session()
            
            if datetime.now().minute == 0:
                AutomationSystem.auto_backup()
            
            print("🧹 تم التنظيف الدوري للملفات المؤقتة (كل دقيقة)")
            
        except Exception as e:
            print(f"⚠️ خطأ في التنظيف الدوري: {e}")

async def backup_scheduler():
    while True:
        try:
            await asyncio.sleep(3600)
            DataProtection.auto_backup()
            print("⏰ تم تنفيذ النسخ الاحتياطي التلقائي المجدول")
        except Exception as e:
            print(f"⚠️ خطأ في النسخ الاحتياطي المجدول: {e}")

async def news_radar_scheduler():
    """تشغيل رادار الأخبار كل 5 دقائق"""
    while True:
        try:
            await asyncio.sleep(300)  # 5 دقائق
            await news_radar.process_news()
            print("📡 تم تشغيل رادار الأخبار")
        except Exception as e:
            print(f"⚠️ خطأ في رادار الأخبار: {e}")

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
        except Exception as e:
            print(f"⚠️ خطأ في نسخ قاعدة البيانات: {e}")
    
    @staticmethod
    def backup_codes_db():
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(BACKUP_DIR, f"subscription_backup_{timestamp}.db")
            if os.path.exists(SUBSCRIPTION_DB_NAME):
                shutil.copy2(SUBSCRIPTION_DB_NAME, backup_file)
                print(f"💾 تم نسخ قاعدة بيانات الاشتراك: {backup_file}")
                
                backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('subscription_backup_')])
                while len(backups) > 20:
                    oldest = backups.pop(0)
                    os.remove(os.path.join(BACKUP_DIR, oldest))
        except Exception as e:
            print(f"⚠️ خطأ في نسخ قاعدة بيانات الاشتراك: {e}")
    
    @staticmethod
    def backup_radar_db():
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(BACKUP_DIR, f"radar_backup_{timestamp}.db")
            if os.path.exists(NEWS_RADAR_DB):
                shutil.copy2(NEWS_RADAR_DB, backup_file)
                print(f"💾 تم نسخ قاعدة بيانات الرادار: {backup_file}")
                
                backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('radar_backup_')])
                while len(backups) > 20:
                    oldest = backups.pop(0)
                    os.remove(os.path.join(BACKUP_DIR, oldest))
        except Exception as e:
            print(f"⚠️ خطأ في نسخ قاعدة بيانات الرادار: {e}")
    
    @staticmethod
    def auto_backup():
        DataProtection.backup_database()
        DataProtection.backup_codes_db()
        DataProtection.backup_radar_db()

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
    <title>👑 لوحة التحكم المتطورة</title>
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
        .header .current-time {
            background: #f8f9ff;
            padding: 10px 20px;
            border-radius: 10px;
            color: #667eea;
            font-weight: bold;
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
        .session-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 15px;
            margin-bottom: 20px;
        }
        .session-card h3 {
            font-size: 1.5em;
            margin-bottom: 10px;
        }
        .session-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }
        .session-stat {
            background: rgba(255,255,255,0.1);
            padding: 15px;
            border-radius: 10px;
            text-align: center;
        }
        .session-stat .label {
            font-size: 0.9em;
            opacity: 0.8;
        }
        .session-stat .value {
            font-size: 1.3em;
            font-weight: bold;
            margin-top: 5px;
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
        .status-bar {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .status-item {
            background: white;
            padding: 15px 25px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            gap: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
        }
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
        }
        .status-dot.active { background: #28a745; }
        .status-dot.stopped { background: #dc3545; }
        .status-dot.maintenance { background: #ffc107; }
        .status-dot.scheduled { background: #17a2b8; }
        .auto-refresh {
            background: #f8f9ff;
            padding: 10px 15px;
            border-radius: 10px;
            display: inline-flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
        }
        .auto-refresh input {
            width: 60px;
            padding: 5px;
            border: 1px solid #ddd;
            border-radius: 5px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🤖 لوحة تحكم Obeida Trading</h1>
                <p class="subtitle">نظام إدارة الاشتراكات المتطور - المشرف: @Sz2zv</p>
            </div>
            <div>
                <div class="current-time" id="currentTime"></div>
                <span style="margin-right: 20px; color: #666;">مرحباً، المالك</span>
                <a href="/logout" class="logout-btn">تسجيل الخروج</a>
            </div>
        </div>

        <div class="status-bar">
            <div class="status-item">
                <div class="status-dot {% if bot_status.status == 'active' %}active{% elif bot_status.status == 'stopped' %}stopped{% elif bot_status.status == 'maintenance' %}maintenance{% else %}scheduled{% endif %}"></div>
                <div>
                    <strong>حالة البوت:</strong> {{ bot_status.status }}
                </div>
            </div>
            <div class="status-item">
                <div>
                    <strong>الجلسات المسموحة:</strong> {{ bot_status.session_control }}
                </div>
            </div>
            <div class="status-item">
                <div>
                    <strong>الجلسة الحالية (UTC):</strong> {{ current_session.name }}
                </div>
            </div>
            <div class="status-item">
                <div>
                    <strong>التحليلات المجانية:</strong> {{ '✅ مفعلة' if free_settings.enabled else '❌ معطلة' }}
                </div>
            </div>
            {% if bot_status.scheduled_time %}
            <div class="status-item">
                <div>
                    <strong>مجدول:</strong> {{ bot_status.scheduled_action }} في {{ bot_status.scheduled_time[:16] }}
                </div>
            </div>
            {% endif %}
        </div>

        <div class="auto-refresh">
            <span>🔄 تحديث تلقائي كل</span>
            <input type="number" id="refreshInterval" value="30" min="5" max="300">
            <span>ثانية</span>
            <button class="btn btn-primary" onclick="startAutoRefresh()">بدء</button>
            <button class="btn btn-danger" onclick="stopAutoRefresh()">إيقاف</button>
        </div>

        <div class="session-card">
            <h3>{{ current_session.icon }} {{ current_session.name }}</h3>
            <p>{{ current_session.features }}</p>
            <div class="session-stats">
                <div class="session-stat">
                    <div class="label">⏰ وقت الجلسة</div>
                    <div class="value">{{ current_session.time_range }}</div>
                </div>
                <div class="session-stat">
                    <div class="label">📊 التقلب</div>
                    <div class="value">{{ current_session.volatility }}</div>
                </div>
                <div class="session-stat">
                    <div class="label">🎯 نسبة النجاح</div>
                    <div class="value">{{ current_session.success_rate }}</div>
                </div>
                <div class="session-stat">
                    <div class="label">💰 الأزواج</div>
                    <div class="value">{{ current_session.pairs[:30] }}...</div>
                </div>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>👥 جميع المستخدمين</h3>
                <div class="number">{{ stats.total_users }}</div>
                <div class="trend">إجمالي المسجلين</div>
            </div>
            <div class="stat-card">
                <h3>✅ المشتركين النشطين</h3>
                <div class="number">{{ stats.active_users }}</div>
                <div class="trend">{{ (stats.active_users / stats.total_users * 100)|round(1) if stats.total_users > 0 else 0 }}% من الإجمالي</div>
            </div>
            <div class="stat-card">
                <h3>🚫 المحظورين</h3>
                <div class="number">{{ stats.blocked_users }}</div>
                <div class="trend" style="color: #dc3545;">{{ (stats.blocked_users / stats.total_users * 100)|round(1) if stats.total_users > 0 else 0 }}% من الإجمالي</div>
            </div>
            <div class="stat-card">
                <h3>📊 إجمالي التحليلات</h3>
                <div class="number">{{ stats.total_analyses }}</div>
                <div class="trend">تحليل منذ إنشاء البوت</div>
            </div>
            <div class="stat-card">
                <h3>📅 نشط اليوم</h3>
                <div class="number">{{ stats.active_today }}</div>
                <div class="trend">مستخدم نشط اليوم</div>
            </div>
            <div class="stat-card">
                <h3>⏳ منتهي قريباً</h3>
                <div class="number">{{ stats.expiring_soon }}</div>
                <div class="trend" style="color: #ffc107;">خلال 7 أيام</div>
            </div>
            <div class="stat-card">
                <h3>🔢 مستخدمين مع مجاني</h3>
                <div class="number">{{ stats.users_with_free }}</div>
                <div class="trend">مستخدمين</div>
            </div>
            <div class="stat-card">
                <h3>🎯 تحليلات مجانية متبقية</h3>
                <div class="number">{{ stats.total_free_remaining }}</div>
                <div class="trend">تحليل</div>
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
            <div class="quick-action-card" onclick="forceSessionUpdate()">
                <div class="icon">🔄</div>
                <div class="title">تحديث الجلسة</div>
            </div>
            <div class="quick-action-card" onclick="sendSessionNotification()">
                <div class="icon">📨</div>
                <div class="title">إرسال إشعار الجلسة</div>
            </div>
            <div class="quick-action-card" onclick="toggleFreeAnalytics()">
                <div class="icon">🎯</div>
                <div class="title">تبديل التحليلات المجانية</div>
            </div>
        </div>

        <div class="tabs">
            <div class="tab active" onclick="showTab('users')">👥 المستخدمين</div>
            <div class="tab" onclick="showTab('subscriptions')">📊 الاشتراكات</div>
            <div class="tab" onclick="showTab('stats')">📈 إحصائيات</div>
            <div class="tab" onclick="showTab('backups')">💾 النسخ الاحتياطي</div>
            <div class="tab" onclick="showTab('sessions')">🌐 جلسات التداول</div>
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
                                <th>الاشتراك</th>
                                <th>تحليلات</th>
                                <th>متبقي</th>
                                <th>المنطقة الزمنية</th>
                                <th>تاريخ الانتهاء</th>
                                <th>الحالة</th>
                                <th>آخر نشاط</th>
                                <th>الإجراءات</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for user in users %}
                            <tr>
                                <td><span class="code-box">{{ user.id }}</span></td>
                                <td>{{ user.name }}</td>
                                <td>{{ user.subscription_type }}</td>
                                <td>{{ user.total_analyses }}</td>
                                <td>{{ user.free_analyses }}</td>
                                <td>{{ user.timezone }}</td>
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
                                <td class="actions">
                                    <button class="btn btn-success" onclick="extendSubscription('{{ user.id }}')">⏱️ تمديد</button>
                                    <button class="btn btn-warning" onclick="blockUser('{{ user.id }}')">🚫 حظر</button>
                                    <button class="btn btn-info" onclick="resetFreeAnalyses('{{ user.id }}')">🔄 إعادة تحليلات</button>
                                    <button class="btn btn-primary" onclick="setTimezone('{{ user.id }}', '{{ user.timezone }}')">🌍 تعديل منطقة</button>
                                    <button class="btn btn-secondary" onclick="setFreeCount('{{ user.id }}', '{{ user.free_analyses }}')">🔢 تعديل المجاني</button>
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
                <h2 class="section-title">📈 إحصائيات متقدمة</h2>
                <div class="stats-grid">
                    <div class="stat-card">
                        <h3>📊 متوسط التحليلات لكل مستخدم</h3>
                        <div class="number">{{ (stats.total_analyses / stats.total_users)|round(1) if stats.total_users > 0 else 0 }}</div>
                    </div>
                    <div class="stat-card">
                        <h3>🔄 نسبة المشتركين</h3>
                        <div class="number">{{ (stats.active_users / stats.total_users * 100)|round(1) if stats.total_users > 0 else 0 }}%</div>
                    </div>
                    <div class="stat-card">
                        <h3>🖥️ استخدام CPU</h3>
                        <div class="number">{{ health.cpu }}%</div>
                    </div>
                    <div class="stat-card">
                        <h3>💾 استخدام RAM</h3>
                        <div class="number">{{ health.memory_used }}%</div>
                    </div>
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

        <div id="sessions-tab" class="tab-content">
            <div class="section">
                <h2 class="section-title">🌐 جلسات التداول</h2>
                <div class="stats-grid">
                    {% for session_key, session in trading_sessions.items() %}
                    <div class="stat-card" style="{% if session.name == current_session.name %}border: 2px solid #667eea;{% endif %}">
                        <h3>{{ session.icon }} {{ session.name }}</h3>
                        <div class="number">{{ session.success_rate }}</div>
                        <div class="trend">{{ session.time_range }}</div>
                        <p style="margin-top: 10px;">{{ session.features }}</p>
                        <p style="margin-top: 5px; color: #666;">{{ session.pairs }}</p>
                        <p style="margin-top: 5px;"><small>أفضل المناطق: {{ session.best_timezones|join(', ') }}</small></p>
                    </div>
                    {% endfor %}
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

    <!-- Modal إعادة تحليلات مجانية -->
    <div id="resetAnalysesModal" class="modal">
        <div class="modal-content">
            <h2>🔄 إعادة تعيين التحليلات المجانية</h2>
            <div class="form-group">
                <label>معرف المستخدم</label>
                <input type="text" id="resetUserId" readonly>
            </div>
            <div class="form-group">
                <label>عدد التحليلات</label>
                <input type="number" id="resetCount" value="3" min="1" max="10">
            </div>
            <div class="modal-actions">
                <button class="btn btn-success" onclick="confirmResetAnalyses()">✅ إعادة تعيين</button>
                <button class="btn btn-danger" onclick="closeModal('resetAnalysesModal')">❌ إلغاء</button>
            </div>
        </div>
    </div>

    <!-- Modal تعديل منطقة زمنية -->
    <div id="timezoneModal" class="modal">
        <div class="modal-content">
            <h2>🌍 تعديل المنطقة الزمنية</h2>
            <div class="form-group">
                <label>معرف المستخدم</label>
                <input type="text" id="timezoneUserId" readonly>
            </div>
            <div class="form-group">
                <label>المنطقة الزمنية</label>
                <select id="timezoneSelect">
                    {% for gmt in timezones %}
                    <option value="{{ gmt }}">{{ gmt }} - {{ timezones[gmt].name }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="modal-actions">
                <button class="btn btn-success" onclick="confirmTimezone()">✅ حفظ</button>
                <button class="btn btn-danger" onclick="closeModal('timezoneModal')">❌ إلغاء</button>
            </div>
        </div>
    </div>

    <!-- Modal تعديل عدد التحليلات المجانية -->
    <div id="freeCountModal" class="modal">
        <div class="modal-content">
            <h2>🔢 تعديل عدد التحليلات المجانية</h2>
            <div class="form-group">
                <label>معرف المستخدم</label>
                <input type="text" id="freeCountUserId" readonly>
            </div>
            <div class="form-group">
                <label>العدد الحالي</label>
                <input type="text" id="freeCountCurrent" readonly>
            </div>
            <div class="form-group">
                <label>العدد الجديد</label>
                <input type="number" id="freeCountNew" value="3" min="0" max="10">
            </div>
            <div class="modal-actions">
                <button class="btn btn-success" onclick="confirmFreeCount()">✅ حفظ</button>
                <button class="btn btn-danger" onclick="closeModal('freeCountModal')">❌ إلغاء</button>
            </div>
        </div>
    </div>

    <!-- Modal إشعار -->
    <div id="notification" class="notification"></div>

    <div class="footer">
        <p>🤯 Obeida Trading 🤯 - نظام إدارة الاشتراكات المتطور V2.1</p>
        <p style="font-size: 0.8em;">نسخة البيانات: {{ data_version }} | آخر تحديث: {{ now }}</p>
    </div>

    <script>
        let currentUserId = '';
        let autoRefreshTimer = null;

        function updateCurrentTime() {
            const now = new Date();
            const utcString = now.toUTCString();
            const localString = now.toLocaleString('ar-EG');
            document.getElementById('currentTime').innerHTML = `🕒 UTC: ${utcString}<br>📍 محلي: ${localString}`;
        }

        function startAutoRefresh() {
            const interval = document.getElementById('refreshInterval').value * 1000;
            if (autoRefreshTimer) clearInterval(autoRefreshTimer);
            autoRefreshTimer = setInterval(() => {
                location.reload();
            }, interval);
            showNotification(`✅ تم بدء التحديث التلقائي كل ${interval/1000} ثانية`);
        }

        function stopAutoRefresh() {
            if (autoRefreshTimer) {
                clearInterval(autoRefreshTimer);
                autoRefreshTimer = null;
                showNotification('⏸️ تم إيقاف التحديث التلقائي');
            }
        }

        function forceSessionUpdate() {
            fetch('/admin/force_session_update', {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('✅ تم تحديث الجلسة بنجاح');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => {
                showNotification('❌ حدث خطأ: ' + error, 'error');
            });
        }

        function sendSessionNotification() {
            fetch('/admin/send_session_notification', {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification(`✅ تم إرسال إشعار الجلسة لـ ${data.sent} مستخدم`);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => {
                showNotification('❌ حدث خطأ: ' + error, 'error');
            });
        }

        function toggleFreeAnalytics() {
            fetch('/admin/toggle_free_analytics', {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification(`✅ تم ${data.enabled ? 'تفعيل' : 'تعطيل'} التحليلات المجانية`);
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => {
                showNotification('❌ حدث خطأ: ' + error, 'error');
            });
        }

        function setTimezone(userId, currentTz) {
            currentUserId = userId;
            document.getElementById('timezoneUserId').value = userId;
            const select = document.getElementById('timezoneSelect');
            for (let i = 0; i < select.options.length; i++) {
                if (select.options[i].value === currentTz) {
                    select.selectedIndex = i;
                    break;
                }
            }
            showModal('timezoneModal');
        }

        function confirmTimezone() {
            const timezone = document.getElementById('timezoneSelect').value;
            
            fetch('/admin/set_timezone', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    user_id: parseInt(currentUserId),
                    timezone: timezone
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('✅ تم تعديل المنطقة الزمنية بنجاح');
                    closeModal('timezoneModal');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => {
                showNotification('❌ حدث خطأ: ' + error, 'error');
            });
        }

        function setFreeCount(userId, currentCount) {
            currentUserId = userId;
            document.getElementById('freeCountUserId').value = userId;
            document.getElementById('freeCountCurrent').value = currentCount;
            document.getElementById('freeCountNew').value = currentCount;
            showModal('freeCountModal');
        }

        function confirmFreeCount() {
            const newCount = document.getElementById('freeCountNew').value;
            
            fetch('/admin/set_free_count', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    user_id: parseInt(currentUserId),
                    count: parseInt(newCount)
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification(`✅ تم تعديل عدد التحليلات المجانية إلى ${newCount}`);
                    closeModal('freeCountModal');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('❌ ' + data.error, 'error');
                }
            })
            .catch(error => {
                showNotification('❌ حدث خطأ: ' + error, 'error');
            });
        }

        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            
            document.querySelector(`.tab:nth-child(${tabName === 'users' ? 1 : tabName === 'subscriptions' ? 2 : tabName === 'stats' ? 3 : tabName === 'backups' ? 4 : 5})`).classList.add('active');
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

        function resetFreeAnalyses(userId) {
            currentUserId = userId;
            document.getElementById('resetUserId').value = userId;
            showModal('resetAnalysesModal');
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
                    showNotification(`✅ تم إرسال الرسالة لـ ${data.sent} مستخدم`);
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

        function confirmResetAnalyses() {
            let count = document.getElementById('resetCount').value;
            
            fetch('/admin/reset_free_analyses', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    user_id: parseInt(currentUserId),
                    count: parseInt(count)
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('✅ تم إعادة تعيين التحليلات المجانية');
                    closeModal('resetAnalysesModal');
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

        function createManualBackup() {
            createBackup();
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

        window.onclick = function(event) {
            let modals = ['addSubscriptionModal', 'broadcastModal', 'extendModal', 'blockModal', 'resetAnalysesModal', 'timezoneModal', 'freeCountModal'];
            modals.forEach(modalId => {
                let modal = document.getElementById(modalId);
                if (event.target == modal) {
                    modal.style.display = 'none';
                }
            });
        }

        setInterval(updateCurrentTime, 1000);
        updateCurrentTime();
    </script>
</body>
</html>
"""

def run_flask_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
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
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def home():
    bot_status = SubscriptionSystem.get_bot_status()
    stats = SubscriptionSystem.get_stats()
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 Obeida Trading Bot</title>
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
            h1 {{ font-size: 3em; margin-bottom: 20px; }}
            .status {{ background: rgba(255,255,255,0.2); padding: 20px 40px; border-radius: 10px; display: inline-block; margin: 20px; }}
            .admin-link {{ background: #28a745; color: white; padding: 15px 30px; border-radius: 5px; text-decoration: none; display: inline-block; margin: 20px; }}
            .stats {{ display: flex; gap: 20px; justify-content: center; margin: 30px 0; }}
            .stat {{ background: rgba(255,255,255,0.1); padding: 20px; border-radius: 10px; }}
        </style>
    </head>
    <body>
        <h1>📊 Obeida Trading - AI Analysis Bot</h1>
        <p>Technical Analysis Bot with Advanced Subscription System</p>
        
        <div class="stats">
            <div class="stat">
                <h3>👥 إجمالي المستخدمين</h3>
                <p style="font-size: 2em;">{stats.get('total_users', 0)}</p>
            </div>
            <div class="stat">
                <h3>✅ المشتركين النشطين</h3>
                <p style="font-size: 2em;">{stats.get('active_users', 0)}</p>
            </div>
            <div class="stat">
                <h3>📊 إجمالي التحليلات</h3>
                <p style="font-size: 2em;">{stats.get('total_analyses', 0)}</p>
            </div>
        </div>
        
        <div class="status">
            ✅ جميع الأنظمة قيد التشغيل<br>
            حالة البوت: {bot_status['status']}<br>
            الجلسات المسموحة: {bot_status['session_control']}
        </div>
        
        <p>Last Ping: {time.strftime("%Y-%m-%d %H:%M:%S")}</p>
        <a href="/admin" class="admin-link">🔐 الدخول إلى لوحة التحكم</a>
    </body>
    </html>
    """

@app.route('/health')
def health():
    stats = SubscriptionSystem.get_stats()
    bot_status = SubscriptionSystem.get_bot_status()
    current_session = get_current_session()
    health = AutomationSystem.get_system_health()
    free_settings = free_analytics.get_settings()
    
    return {
        "status": "active", 
        "ai_provider": "Mistral AI", 
        "subscription_system": "advanced_v2.1",
        "data_version": DATA_VERSION,
        "total_users": stats.get('total_users', 0),
        "active_users": stats.get('active_users', 0),
        "blocked_users": stats.get('blocked_users', 0),
        "total_analyses": stats.get('total_analyses', 0),
        "active_today": stats.get('active_today', 0),
        "expiring_soon": stats.get('expiring_soon', 0),
        "bot_status": bot_status['status'],
        "session_control": bot_status['session_control'],
        "current_session": current_session['name'],
        "system_health": health,
        "free_analytics_enabled": free_settings['enabled'],
        "free_analytics_default": free_settings['default_count'],
        "timestamp": time.time()
    }

@app.route('/admin')
@login_required
def admin_panel():
    try:
        stats = SubscriptionSystem.get_stats()
        bot_status = SubscriptionSystem.get_bot_status()
        users_data = SubscriptionSystem.get_all_users()
        current_session = get_current_session()
        health = AutomationSystem.get_system_health()
        free_settings = free_analytics.get_settings()
        
        formatted_users = []
        for u in users_data:
            uid, username, first_name, created, last_active, is_blocked, free_analyses, total_analyses, timezone, sub_name, start_date, end_date, is_active = u
            
            if is_blocked == 1:
                status = "محظور"
            elif is_active == 1 and (not end_date or datetime.fromisoformat(end_date) > datetime.now()):
                status = "نشط"
            else:
                status = "منتهي"
            
            display_name = first_name or username or str(uid)
            
            end_display = end_date[:16] if end_date else "غير محدد"
            if end_date and datetime.fromisoformat(end_date) < datetime.now():
                end_display += " (منتهي)"
            
            formatted_users.append({
                'id': uid,
                'name': display_name,
                'subscription_type': sub_name or 'غير مشترك',
                'free_analyses': free_analyses,
                'total_analyses': total_analyses,
                'timezone': timezone or 'GMT+02:00',
                'end_date': end_display,
                'status': status,
                'last_active': last_active[:16] if last_active else '-'
            })
        
        backups = []
        if os.path.exists(BACKUP_DIR):
            files = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')], reverse=True)
            for f in files[:15]:
                filepath = os.path.join(BACKUP_DIR, f)
                size = os.path.getsize(filepath) / 1024
                mod_time = os.path.getmtime(filepath)
                backups.append({
                    'name': f,
                    'date': datetime.fromtimestamp(mod_time).strftime('%Y-%m-%d %H:%M'),
                    'size': f'{size:.1f} KB'
                })
        
        return render_template_string(
            ADMIN_PANEL_HTML,
            stats=stats,
            bot_status=bot_status,
            users=formatted_users,
            backups=backups,
            current_session=current_session,
            trading_sessions=TRADING_SESSIONS,
            timezones=TIMEZONES_BY_GMT,
            health=health,
            free_settings=free_settings,
            data_version=DATA_VERSION,
            now=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        )
    except Exception as e:
        return f"خطأ في تحميل لوحة التحكم: {str(e)}"

@app.route('/admin/force_session_update', methods=['POST'])
@login_required
def force_session_update():
    try:
        AutomationSystem.auto_switch_session()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/send_session_notification', methods=['POST'])
@login_required
def send_session_notification():
    try:
        current_session = get_current_session()
        notification = AutomationSystem.send_session_notification(current_session)
        
        if not notification:
            return jsonify({'success': False, 'error': 'فشل في إنشاء الإشعار'})
        
        users = SubscriptionSystem.get_all_users()
        sent_count = 0
        
        for user in users:
            try:
                sent_count += 1
            except:
                pass
        
        return jsonify({'success': True, 'sent': sent_count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/toggle_free_analytics', methods=['POST'])
@login_required
def toggle_free_analytics():
    try:
        current = free_analytics.get_settings()
        new_state = not current['enabled']
        free_analytics.set_setting('enabled', str(new_state).lower(), ADMIN_ID)
        return jsonify({'success': True, 'enabled': new_state})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/set_free_count', methods=['POST'])
@login_required
def set_free_count():
    try:
        data = request.json
        user_id = data.get('user_id')
        count = data.get('count', 3)
        
        success = SubscriptionSystem.set_free_analyses_count(user_id, count)
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'فشل في تعديل العدد'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/set_timezone', methods=['POST'])
@login_required
def set_timezone():
    try:
        data = request.json
        user_id = data.get('user_id')
        timezone = data.get('timezone')
        
        success = SubscriptionSystem.set_user_timezone(user_id, timezone)
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'فشل في تعديل المنطقة الزمنية'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/add_subscription_v2', methods=['POST'])
@login_required
def add_subscription_v2():
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

@app.route('/admin/extend_subscription_v2', methods=['POST'])
@login_required
def extend_subscription_v2():
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

@app.route('/admin/reset_free_analyses', methods=['POST'])
@login_required
def reset_free_analyses():
    try:
        data = request.json
        user_id = data.get('user_id')
        count = data.get('count', 3)
        
        success = SubscriptionSystem.reset_free_analyses(user_id, count)
        
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'فشل في إعادة التعيين'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/broadcast', methods=['POST'])
@login_required
def broadcast():
    try:
        data = request.json
        message = data.get('message')
        
        if not message:
            return jsonify({'success': False, 'error': 'الرسالة مطلوبة'})
        
        users = SubscriptionSystem.get_all_users()
        sent_count = 0
        
        return jsonify({'success': True, 'sent': sent_count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/create_backup', methods=['POST'])
@login_required
def create_backup():
    try:
        DataProtection.backup_database()
        DataProtection.backup_codes_db()
        DataProtection.backup_radar_db()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/download_backup/<filename>')
@login_required
def download_backup(filename):
    try:
        from flask import send_file
        filepath = os.path.join(BACKUP_DIR, filename)
        if os.path.exists(filepath):
            return send_file(filepath, as_attachment=True)
        return jsonify({'success': False, 'error': 'الملف غير موجود'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/restore_backup/<filename>', methods=['POST'])
@login_required
def restore_backup(filename):
    try:
        filepath = os.path.join(BACKUP_DIR, filename)
        if not os.path.exists(filepath):
            return jsonify({'success': False, 'error': 'الملف غير موجود'})
        
        if 'main_backup' in filename:
            shutil.copy2(filepath, DB_NAME)
        elif 'subscription_backup' in filename:
            shutil.copy2(filepath, SUBSCRIPTION_DB_NAME)
        elif 'radar_backup' in filename:
            shutil.copy2(filepath, NEWS_RADAR_DB)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/export_data')
@login_required
def export_data():
    try:
        users = SubscriptionSystem.get_all_users()
        stats = SubscriptionSystem.get_stats()
        
        data = {
            'export_date': datetime.now().isoformat(),
            'data_version': DATA_VERSION,
            'stats': stats,
            'users': users
        }
        
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
    stats = SubscriptionSystem.get_stats()
    return jsonify({'success': True, 'stats': stats})

@app.route('/ping')
def ping():
    return "PONG"

def main():
    print("🤖 Starting Obeida Trading with Advanced News Radar V2.0...")
    
    init_db_with_subscription()
    
    DataProtection.backup_database()
    DataProtection.backup_codes_db()
    DataProtection.backup_radar_db()
    
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
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
    
    application = Application.builder().token(TOKEN).build()
    
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
            ANALYZE_MODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_analyze_mode),
                MessageHandler(filters.PHOTO, handle_photo_analysis)
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
            SET_BOT_STATUS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_bot_status)
            ],
            SET_SESSION_CONTROL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_session_control)
            ],
            TIMEZONE_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_timezone_selection)
            ],
            SEARCH_COUNTRY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_country_search)
            ],
            FREE_ANALYSES_SETTINGS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_analytics_settings)
            ]
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    
    application.add_error_handler(error_handler)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(periodic_cleanup())
    loop.create_task(backup_scheduler())
    loop.create_task(news_radar_scheduler())
    
    print("✅ Telegram Secure Admin Panel ready...")
    print("📡 Obeida News Radar V2.0 is running...")
    print("📢 الأخبار ستنشر تلقائياً في @ObeidaTradingchat")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
