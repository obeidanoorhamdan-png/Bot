from flask import Flask
from threading import Thread
import os

app_web = Flask(__name__)
@app_web.route('/')
def home(): return "I am alive"

def keep_alive():
    t = Thread(target=lambda: app_web.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080))))
    t.start()
import logging
import base64
import os
import sqlite3
import requests
import asyncio
import hashlib
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# --- الإعدادات ---
TOKEN = "7324911542:AAFqB9NRegwE2_bG5rCTaEWocbh8N3vgWeo"
MISTRAL_KEY = "EABRT5zGsHYhezkaJJomt15VR2iBrPWq"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
DB_NAME = "abood-gpt.db"

# قوائم الإعدادات
CANDLE_SPEEDS = ["S5", "S10", "S15", "S30", "M1", "M2", "M3", "M5", "M10", "M15", "M30", "H1", "H4", "D1"]
TRADE_TIMES = ["S3", "S15", "S30", "M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]

# حالات المحادثة
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, MONITORING_MODE = range(6)

# تخزين حالات المراقبة النشطة
active_monitoring = {}

# --- قاعدة البيانات ---
def init_db():
    """تهيئة قاعدة البيانات"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # جدول المستخدمين
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            candle TEXT DEFAULT 'M5', 
            trade_time TEXT DEFAULT 'H1',
            chat_context TEXT DEFAULT '',
            last_analysis_result TEXT DEFAULT '',
            monitoring_active INTEGER DEFAULT 0,
            monitoring_end_time TEXT,
            current_chart_image TEXT DEFAULT ''
        )
    ''')
    
    # جدول سجل المراقبة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS monitoring_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            result_hash TEXT,
            result_data TEXT,
            status TEXT
        )
    ''')
    
    # جدول سجل الدردشة
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def save_user_setting(user_id, col, val):
    """حفظ إعدادات المستخدم"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(f"INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute(f"UPDATE users SET {col} = ? WHERE user_id = ?", (val, user_id))
    conn.commit()
    conn.close()

def get_user_setting(user_id):
    """الحصول على إعدادات المستخدم"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT candle, trade_time, monitoring_active, monitoring_end_time, current_chart_image, last_analysis_result FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    
    if res:
        return res
    return ("M5", "H1", 0, None, "", "")

def update_last_analysis(user_id, result):
    """تحديث نتيجة التحليل الأخيرة"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET last_analysis_result = ? WHERE user_id = ?", (result, user_id))
    conn.commit()
    conn.close()

def start_monitoring(user_id, end_time):
    """بدء وضع المراقبة في قاعدة البيانات"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET monitoring_active = 1, monitoring_end_time = ? WHERE user_id = ?", (end_time, user_id))
    conn.commit()
    conn.close()

def stop_monitoring(user_id):
    """إيقاف وضع المراقبة في قاعدة البيانات"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET monitoring_active = 0, monitoring_end_time = NULL WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def save_monitoring_result(user_id, result_hash, result_data, status="new"):
    """حفظ نتيجة المراقبة"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO monitoring_history (user_id, result_hash, result_data, status) VALUES (?, ?, ?, ?)",
                   (user_id, result_hash, result_data, status))
    conn.commit()
    conn.close()

def get_last_monitoring_hash(user_id):
    """الحصول على آخر هاش للمراقبة"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT result_hash FROM monitoring_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else None

# --- معالجة الصور ---
def encode_image(image_path):
    """تشفير الصورة إلى base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

def generate_result_hash(result_text):
    """توليد هاش فريد للنتيجة"""
    return hashlib.md5(result_text.encode()).hexdigest()[:16]

# --- تحليل الصور مع Mistral ---
async def analyze_image_with_mistral(image_path, candle, trade_time):
    """تحليل الصورة باستخدام Mistral API"""
    try:
        base64_img = encode_image(image_path)
        
        # برومبت التحليل الفني
        prompt = f"""
        أنت محلل فني خبير في أسواق المال. الصورة المرفقة هي رسم بياني (شارت) للتداول.
        
        **الإعدادات المطلوبة:**
        - سرعة الشموع: {candle}
        - مدة الصفقة المتوقعة: {trade_time}
        
        **مطلوب منك:**
        1. تحليل شامل للصورة
        2. تحديد الأنماط الفنية الظاهرة
        3. تقييم قوة الاتجاه
        4. تقديم توقع واضح
        5. تحليل ذكي للصورة 
        6. توقعات ناحجة جدآ 
        7. قدم إجابات دقيقة وموضوعية تعتمد على الحقائق والبيانات المتاحة 
        8. لا تقدم نسب مخاطرة وهمية ولا توقعات مضمونة.
        9. كن واقعياً وموضوعياً في جميع إجاباتك.
        10. توقعات ناحجة و رسمية بدون اي إجابات سريعة أو وهمية
        11. انتا ذكي جدآ وتوقعات مضمونة و صحيحة 100٪
        12. اجعل كل شئ بالغة العربية.
        13. اختصار الإجابة بدقة و وضوح و صحة بيانات
        
        **التنسيق المطلوب للإجابة:**
        📊 **التحليل الفني:**
        - النمط السائد: (تصاعدي/تنازلي/جانبي)
        - مستويات الدعم/المقاومة: (إن وجدت)
        - توقع مستويات الدعم/المقاومة القادم: (إن وجدت)
        🎯 **التوقع:**
        - الإتجاه: (🟢 صعود ⬆️ / 🔴 نزول ⬇️ / 🟡 ثابت ➡️ )
        - توقع الإتجاه: ( صعود عالي / صعود منخفض / صعود متوسط/ نزول مرتفع / نزول منخفض/ نزول متوسط )
        - توقع: ( بيع 🔴 / شراء 🟢 / الإحتفاظ 🟡 )
        - حد الربح الحالي:
        - توقع حد الربح:
        - حد الخسارة الحالية:
        - توقع حد الخسارة:
        - مستوى الثقة: XX٪
        - نقطة الدخول المقترحة: 
        - توقع نقطة الوصول:
        - هدف الربح:
        - توقع هدف الربح:
        - وقف الخسارة:
        - توقع هدف الخسارة:
        
        ⚠️ **التحذيرات والمخاطر:**
        - المخاطر المحتملة:
        """
        
        payload = {
            "model": "pixtral-12b-2409",
            "messages": [
                {
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
                    ]
                }
            ],
            "max_tokens": 1000,
            "temperature": 0.3
        }
        
        headers = {
            "Authorization": f"Bearer {MISTRAL_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            logging.error(f"Mistral Vision API Error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logging.error(f"Error in analyze_image_with_mistral: {e}")
        return None

# --- نظام المراقبة التلقائية ---
async def monitoring_task(context, user_id, chat_id, image_path, candle, trade_time, end_time):
    """مهمة المراقبة الدورية"""
    try:
        end_datetime = datetime.fromisoformat(end_time)
        
        while datetime.now() < end_datetime and user_id in active_monitoring:
            # تحليل الصورة
            result = await analyze_image_with_mistral(image_path, candle, trade_time)
            
            if result:
                current_hash = generate_result_hash(result)
                last_hash = get_last_monitoring_hash(user_id)
                
                save_monitoring_result(user_id, current_hash, result, 
                                      "new" if last_hash != current_hash else "same")
                
                if last_hash != current_hash:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"🔄 **تحديث المراقبة**\n"
                                 f"⏰ {datetime.now().strftime('%H:%M:%S')}\n"
                                 f"━━━━━━━━━━━━━━━━\n"
                                 f"{result}\n\n"
                                 f"📊 **الإعدادات:**\n"
                                 f"• سرعة الشموع: {candle}\n"
                                 f"• مدة الصفقة: {trade_time}",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logging.error(f"Error sending update to user {user_id}: {e}")
                
                update_last_analysis(user_id, result)
            
            await asyncio.sleep(30)
            
            if not os.path.exists(image_path):
                logging.info(f"Image removed for user {user_id}, stopping monitoring")
                break
        
        if user_id in active_monitoring:
            del active_monitoring[user_id]
            stop_monitoring(user_id)
            
            try:
                keyboard = [["📊 تحليل صورة جديدة"], ["💬 دردشة"], ["🏠 القائمة الرئيسية"]]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⏹️ **تم إنهاء وضع المراقبة**\n\n"
                         "✅ اكتملت فترة المراقبة المحددة.\n"
                         "يمكنك الآن تحليل صورة جديدة أو استخدام الخدمات الأخرى.",
                    reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
                )
            except Exception as e:
                logging.error(f"Error sending end message to user {user_id}: {e}")
            
            if os.path.exists(image_path):
                os.remove(image_path)
                
    except Exception as e:
        logging.error(f"Monitoring task error for user {user_id}: {e}")
        if user_id in active_monitoring:
            del active_monitoring[user_id]
            stop_monitoring(user_id)

# --- بدء وضع المراقبة ---
async def start_monitoring_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, image_path):
    """بدء وضع المراقبة"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    candle, trade_time, _, _, _, _ = get_user_setting(user_id)
    
    # حساب وقت انتهاء المراقبة
    trade_durations = {
        "S3": 3, "S15": 15, "S30": 30, "M1": 60, "M3": 180, "M5": 300,
        "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400,
        "W1": 604800, "MN1": 2592000
    }
    
    duration_seconds = trade_durations.get(trade_time, 3600)
    end_time = datetime.now() + timedelta(seconds=duration_seconds)
    
    start_monitoring(user_id, end_time.isoformat())
    
    active_monitoring[user_id] = {
        "chat_id": chat_id,
        "image_path": image_path,
        "candle": candle,
        "trade_time": trade_time,
        "end_time": end_time
    }
    
    asyncio.create_task(
        monitoring_task(context, user_id, chat_id, image_path, candle, trade_time, end_time.isoformat())
    )
    
    keyboard = [["⏹️ إيقاف المراقبة"], ["📊 تحليل صورة جديدة"], ["🏠 القائمة الرئيسية"]]
    
    await update.message.reply_text(
        f"🔍 **تم تفعيل وضع المراقبة**\n\n"
        f"✅ سيتم مراقبة السوق تلقائياً كل 30 ثانية\n"
        f"⏰ مدة المراقبة: {trade_time}\n"
        f"⏳ ينتهي في: {end_time.strftime('%H:%M:%S')}\n\n"
        f"📊 **سيتم إرسال تحديثات فورية عند تغيير التوقعات.**",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    
    return MONITORING_MODE

# --- معالجة أوضاع المراقبة ---
async def handle_monitoring_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأوامر في وضع المراقبة"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "⏹️ إيقاف المراقبة":
        if user_id in active_monitoring:
            del active_monitoring[user_id]
        stop_monitoring(user_id)
        
        keyboard = [["📊 تحليل صورة جديدة"], ["💬 دردشة"], ["🏠 القائمة الرئيسية"]]
        await update.message.reply_text(
            "⏹️ **تم إيقاف المراقبة**\n\n"
            "✅ توقفت عملية المراقبة التلقائية.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "📊 تحليل صورة جديدة":
        keyboard = [["🏠 القائمة الرئيسية"]]
        await update.message.reply_text(
            "📤 **أرسل صورة الرسم البياني الجديدة:**",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        
        if user_id in active_monitoring:
            del active_monitoring[user_id]
        stop_monitoring(user_id)
        
        return ANALYZE_MODE
    
    elif user_message == "🏠 القائمة الرئيسية":
        if user_id in active_monitoring:
            del active_monitoring[user_id]
        stop_monitoring(user_id)
        
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    else:
        keyboard = [["⏹️ إيقاف المراقبة"], ["📊 تحليل صورة جديدة"], ["🏠 القائمة الرئيسية"]]
        await update.message.reply_text(
            "🔍 **وضع المراقبة نشط**\n\n"
            "استخدم الأزرار للتحكم:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MONITORING_MODE

# --- معالجة الصور للتحليل ---
async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور للتحليل الفني"""
    user_id = update.effective_user.id
    candle, trade_time, monitoring_active, _, _, _ = get_user_setting(user_id)
    
    if not candle or not trade_time:
        keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
        await update.message.reply_text(
            "❌ **يجب ضبط الإعدادات أولاً**\n\n"
            "الرجاء استخدام أزرار القائمة لضبط الإعدادات قبل تحليل الصور.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU

    wait_msg = await update.message.reply_text("🔍 **جاري فحص الشارت 📊 ...**")
    photo = await update.message.photo[-1].get_file()
    path = f"img_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    await photo.download_to_drive(path)

    try:
        result = await analyze_image_with_mistral(path, candle, trade_time)
        
        if result:
            result_hash = generate_result_hash(result)
            
            update_last_analysis(user_id, result)
            save_monitoring_result(user_id, result_hash, result, "initial")
            
            keyboard = [
                ["🔍 تفعيل المراقبة التلقائية", "📊 تحليل صورة أخرى"],
                ["💬 دردشة", "🏠 القائمة الرئيسية"]
            ]
            
            await wait_msg.edit_text(
                f"✅ **تم التحليل بنجاح!**\n"
                f"📈 **نتائج تحليل الشارت:**\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"{result}\n\n"
                f"📊 **الإعدادات المستخدمة:**\n"
                f"• سرعة الشموع: {candle}\n"
                f"• مدة الصفقة: {trade_time}\n\n"
                f"🔍 **يمكنك تفعيل المراقبة التلقائية لمتابعة التغييرات.**",
                parse_mode="Markdown"
            )
            
            await update.message.reply_text(
                "📊 **اختر الإجراء التالي:**",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
            )
            
            return ANALYZE_MODE
        else:
            raise Exception("فشل في تحليل الصورة")
    
    except Exception as e:
        logging.error(f"خطأ في تحليل الصورة: {e}")
        keyboard = [["الرجوع للقائمة الرئيسية"]]
        await wait_msg.edit_text(
            "❌ **حدث خطأ في تحليل الصورة.**\n"
            "يرجى التأكد من وضوح الصورة والمحاولة مرة أخرى."
        )
        
        if os.path.exists(path):
            os.remove(path)
            
        return MAIN_MENU

# --- معالجة وضع التحليل ---
async def handle_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة وضع التحليل"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "🔍 تفعيل المراقبة التلقائية":
        candle, trade_time, _, _, _, _ = get_user_setting(user_id)
        
        import glob
        user_images = glob.glob(f"img_{user_id}_*.jpg")
        
        if user_images:
            latest_image = max(user_images, key=os.path.getctime)
            return await start_monitoring_mode(update, context, latest_image)
        else:
            await update.message.reply_text(
                "❌ **لا توجد صورة حديثة للتحليل.**\n"
                "الرجاء إرسال صورة جديدة أولاً."
            )
            return ANALYZE_MODE
    
    elif user_message in ["📊 تحليل صورة أخرى", "الرجوع للقائمة الرئيسية", "🏠 القائمة الرئيسية"]:
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    keyboard = [["🏠 القائمة الرئيسية"]]
    await update.message.reply_text(
        "📤 **الرجاء إرسال صورة الشارت فقط**\n"
        "أو اضغط '🏠 القائمة الرئيسية'",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    return ANALYZE_MODE

# --- معالجة القائمة الرئيسية ---
async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات القائمة الرئيسية"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "⚙️ إعدادات التحليل":
        keyboard = [CANDLE_SPEEDS[i:i+3] for i in range(0, len(CANDLE_SPEEDS), 3)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            "⚙️ **إعدادات التحليل الفني**\n\n"
            "حدد سرعة الشموع للبدء:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return SETTINGS_CANDLE
    
    elif user_message == "📊 تحليل صورة":
        candle, trade_time, monitoring_active, _, _, _ = get_user_setting(user_id)
        
        if not candle or not trade_time:
            keyboard = [["⚙️ إعدادات التحليل"], ["الرجوع للقائمة الرئيسية"]]
            await update.message.reply_text(
                "❌ **يجب ضبط الإعدادات أولاً**\n\n"
                "الرجاء ضبط سرعة الشموع ومدة الصفقة قبل التحليل.",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return MAIN_MENU
        else:
            keyboard = [["🏠 القائمة الرئيسية"]]
            await update.message.reply_text(
                f"📊 **جاهز للتحليل**\n\n"
                f"الإعدادات الحالية:\n"
                f"• سرعة الشموع: {candle}\n"
                f"• مدة الصفقة: {trade_time}\n\n"
                f"أرسل صورة الرسم البياني (الشارت) الآن:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
                parse_mode="Markdown"
            )
            return ANALYZE_MODE
    
    elif user_message == "💬 دردشة":
        return await start_chat_mode(update, context)
    
    keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة"]]
    await update.message.reply_text(
        "اختر أحد الخيارات من القائمة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    )
    return MAIN_MENU

# --- وضع الدردشة ---
async def start_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع الدردشة"""
    keyboard = [
        ["ايقاف الدردشة"],
        ["الرجوع للقائمة الرئيسية"]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="💬 **وضع الدردشة مع ABOOD GPT**\n\n"
             "يمكنك الآن الدردشة مع الذكاء الاصطناعي.\n"
             "أرسل رسالتك أو استخدم الأزرار أدناه:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return CHAT_MODE

async def handle_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة رسائل الدردشة"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "ايقاف الدردشة":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة"]]
        await update.message.reply_text(
            "✅ تم إنهاء وضع الدردشة.",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    elif user_message == "الرجوع للقائمة الرئيسية":
        main_keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    wait_msg = await update.message.reply_text("ABOOD GPT 🤔 ... ")
    
    try:
        system_prompt = """أنت الآن تعمل كمساعد ذكي خبير وشامل (Thought Partner). مهمتك هي الإجابة على أي استفسار أطرحه عليك بدقة وموضوعية. اتبع القواعد التالية في إجاباتك:
التحليل العميق: قبل الإجابة، قم بتحليل القصد الحقيقي من سؤالي لتقديم الفائدة القصوى.
الهيكلية: استخدم العناوين، النقاط، والجداول إذا لزم الأمر لتنظيم المعلومات وجعلها سهلة القراءة.
التوازن: اجمع بين الدقة العلمية والأسلوب الودود والمبسط.
الشفافية: إذا كان السؤال يحتمل أكثر من إجابة أو وجهة نظر، فاذكر الخيارات المتاحة.
الإيجاز غير المخل: لا تطل في الشرح إذا كانت الإجابة المباشرة كافية، ولا تقتضب إذا كان الموضوع يحتاج تفصيلاً.
اسمك هو: ABOOD GPT 🤖"""
        
        payload = {
            "model": "mistral-medium",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 500,
            "temperature": 0.7
        }
        
        headers = {
            "Authorization": f"Bearer {MISTRAL_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content']
            
            chat_keyboard = [["ايقاف الدردشة"], ["الرجوع للقائمة الرئيسية"]]
            
            if len(result) > 4000:
                parts = [result[i:i+4000] for i in range(0, len(result), 4000)]
                for part in parts:
                    await wait_msg.edit_text(
                        f"💭 **رد ABOOD GPT:**\n\n{part}",
                        parse_mode="Markdown"
                    )
                    wait_msg = await update.message.reply_text("...")
            else:
                await wait_msg.edit_text(
                    f"💭 **رد ABOOD GPT:**\n\n{result}",
                    parse_mode="Markdown"
                )
        else:
            await wait_msg.edit_text(f"❌ حدث خطأ في التواصل مع الذكاء الاصطناعي. الرمز: {response.status_code}")
    
    except requests.exceptions.Timeout:
        await wait_msg.edit_text("⏱️ تجاوز الوقت المحدد للاتصال. حاول مرة أخرى.")
    except Exception as e:
        logging.error(f"خطأ في الدردشة: {e}")
        await wait_msg.edit_text("❌ حدث خطأ في النظام. حاول مرة أخرى.")
    
    return CHAT_MODE

# --- الأوامر الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    keyboard = [
        ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
        ["💬 دردشة"]
    ]
    
    await update.message.reply_text(
        "🤖 **أهلاً بك في ABOOD GPT**\n\n"
        "اختر أحد الخيارات التالية:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def handle_settings_candle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار سرعة الشموع"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in CANDLE_SPEEDS:
        save_user_setting(user_id, "candle", user_message)
        
        keyboard = [TRADE_TIMES[i:i+3] for i in range(0, len(TRADE_TIMES), 3)]
        keyboard.append(["الرجوع للقائمة الرئيسية"])
        
        await update.message.reply_text(
            f"✅ **تم تعيين سرعة الشموع:** {user_message}\n\n"
            f"الآن حدد **مدة الصفقة** المتوقعة:",
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
    
    if user_message == "الرجوع للقائمة الرئيسية":
        keyboard = [["⚙️ إعدادات التحليل", "📊 تحليل صورة"], ["💬 دردشة"]]
        await update.message.reply_text(
            "🏠 العودة للقائمة الرئيسية",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        )
        return MAIN_MENU
    
    if user_message in TRADE_TIMES:
        save_user_setting(user_id, "trade_time", user_message)
        
        keyboard = [["📊 تحليل صورة"], ["💬 دردشة مع الذكاء الاصطناعي"], ["الرجوع للقائمة الرئيسية"]]
        
        candle, _ = get_user_setting(user_id)
        
        await update.message.reply_text(
            f"🚀 **تم حفظ الإعدادات بنجاح!**\n\n"
            f"✅ سرعة الشموع: {candle}\n"
            f"✅ مدة الصفقة: {user_message}\n\n"
            f"يمكنك الآن تحليل صورة أو الدردشة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    await update.message.reply_text("❌ الرجاء اختيار مدة صفقة صحيحة.")
    return SETTINGS_TIME

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر المساعدة"""
    help_text = """
    🤖 **أوامر البوت:**
    
    /start - بدء البوت والعودة للقائمة الرئيسية
    /help - عرض رسالة المساعدة
    
    ⚙️ **كيفية الاستخدام:**
    1. استخدم أزرار القائمة للتنقل
    2. أرسل صورة الشارت للتحليل
    3. اختر "دردشة" للاستفسارات النصية
    
    🔍 **ميزة المراقبة الجديدة:**
    • بعد تحليل الصورة، يمكنك تفعيل المراقبة التلقائية
    • يرسل البوت تحديثات كل 30 ثانية
    • يرسل تحديثات فقط عند تغيير التوقعات
    • يتوقف تلقائياً بعد انتهاء مدة الصفقة
    
    📊 **مميزات البوت:**
    • تحليل فني للرسوم البيانية
    • دردشة ذكية مع الذكاء الاصطناعي
    • مراقبة تلقائية للسوق
    • حفظ إعداداتك الشخصية
    • واجهة سهلة بالأزرار
    """
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء المحادثة"""
    user_id = update.effective_user.id
    if user_id in active_monitoring:
        del active_monitoring[user_id]
        stop_monitoring(user_id)
    
    await update.message.reply_text(
        "تم الإلغاء. اكتب /start للبدء من جديد.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

# --- الدالة الرئيسية ---
if __name__ == "__main__":
    # إعداد التسجيل
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        filename='bot.log'
    )
    
    # تهيئة قاعدة البيانات
    init_db()
    
    # إنشاء التطبيق
    app = Application.builder().token(TOKEN).build()
    
    # معالج المحادثة الرئيسي
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
                MessageHandler(filters.PHOTO, handle_photo_analysis)
            ],
            MONITORING_MODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_monitoring_mode)
            ],
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    
    # إضافة المعالجات
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel))
    
    # تشغيل البوت
    print("🤖 --- البوت يعمل الآن ---")
    print("📊 - نظام التحليل الفني مفعل")
    print("💬 - نظام الدردشة مفعل")
    print("🔍 - نظام المراقبة التلقائية مفعل")
    print("🔄 - تحديثات كل 30 ثانية عند التغيير")
    print("✅ - تم تشغيل البوت بنجاح")
    
if __name__ == '__main__':
    keep_alive()
    app.run_polling()
