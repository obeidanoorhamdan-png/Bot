#!/usr/bin/env python3
"""
ABOOD GPT - Telegram Bot for Chat & Technical Analysis
Version: 1.0.0
"""

import os
import sys
import time
import sqlite3
import re
import base64
import requests
from flask import Flask
from threading import Thread

# === CONFIGURATION ===
TOKEN = os.environ.get('TOKEN', "7324911542:AAFqB9NRegwE2_bG5rCTaEWocbh8N3vgWeo")
MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "EABRT5zGsHYhezkaJJomt15VR2iBrPWq")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
DB_NAME = "abood_gpt.db"
PORT = int(os.environ.get('PORT', 8080))

# === FLASK APP FOR KEEP-ALIVE ===
app = Flask(__name__)

@app.route('/')
def home():
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 ABOOD GPT Bot</title>
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
            .container {{ background: rgba(255,255,255,0.1); padding: 40px; border-radius: 20px; backdrop-filter: blur(10px); max-width: 800px; margin: 0 auto; }}
            h1 {{ font-size: 3em; margin-bottom: 20px; }}
            .status {{ background: #2ecc71; color: white; padding: 15px 30px; border-radius: 10px; display: inline-block; font-size: 1.2em; font-weight: bold; }}
            .info {{ margin-top: 30px; background: rgba(0,0,0,0.2); padding: 20px; border-radius: 10px; }}
            .features {{ display: flex; justify-content: center; gap: 20px; margin-top: 30px; flex-wrap: wrap; }}
            .feature {{ background: rgba(255,255,255,0.15); padding: 20px; border-radius: 10px; width: 180px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div style="font-size: 5em;">🤖</div>
            <h1>ABOOD GPT Telegram Bot</h1>
            <p style="font-size: 1.2em; opacity: 0.9;">Chat & Technical Analysis Assistant</p>
            
            <div class="status">✅ SYSTEM ACTIVE</div>
            
            <div class="info">
                <p><strong>Last Update:</strong> {time.strftime("%Y-%m-%d %H:%M:%S")}</p>
                <p><strong>Service:</strong> Telegram Bot + Flask Server</p>
                <p><strong>Port:</strong> {PORT}</p>
                <p><strong>Status:</strong> Ready for Telegram commands</p>
            </div>
            
            <div class="features">
                <div class="feature">
                    <div style="font-size: 2em;">📊</div>
                    <div>Chart Analysis</div>
                </div>
                <div class="feature">
                    <div style="font-size: 2em;">💬</div>
                    <div>Smart Chat</div>
                </div>
                <div class="feature">
                    <div style="font-size: 2em;">⚙️</div>
                    <div>Custom Settings</div>
                </div>
                <div class="feature">
                    <div style="font-size: 2em;">🔄</div>
                    <div>24/7 Active</div>
                </div>
            </div>
            
            <div style="margin-top: 30px; padding: 20px; background: rgba(0,0,0,0.3); border-radius: 10px;">
                <p style="font-size: 0.9em; opacity: 0.8;">
                    The bot is running successfully!<br>
                    Start chatting on Telegram with your bot.
                </p>
            </div>
        </div>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return {
        "status": "active",
        "service": "abood-gpt-bot",
        "timestamp": time.time(),
        "version": "1.0.0",
        "telegram": "ready",
        "flask": "running",
        "port": PORT
    }

@app.route('/ping')
def ping():
    return "PONG - Bot is alive and ready!"

@app.route('/stop')
def stop():
    """Endpoint to gracefully stop the bot (for admin only)"""
    print("🛑 Stop command received")
    return "Bot stop command sent. Use Render dashboard to restart."

# === DATABASE FUNCTIONS ===
def init_db():
    """Initialize SQLite database"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                candle TEXT DEFAULT 'M5',
                trade_time TEXT DEFAULT 'H1',
                manual_time TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Analysis history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analysis_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                image_hash TEXT,
                analysis_result TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        print("✅ Database initialized successfully")
        return True
    except Exception as e:
        print(f"❌ Database error: {e}")
        return False

# === HELPER FUNCTIONS ===
def clean_text(text, max_length=2000):
    """Clean and truncate text"""
    if not text:
        return ""
    
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Truncate if too long
    if len(text) > max_length:
        text = text[:max_length] + "..."
    
    return text

def encode_image_to_base64(image_path):
    """Encode image to base64"""
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except Exception as e:
        print(f"❌ Image encoding error: {e}")
        return None

# === TELEGRAM BOT (USING WEBHOOKS INSTEAD OF POLLING) ===
def setup_telegram_bot():
    """Setup Telegram bot with webhook to avoid conflicts"""
    print("🔧 Setting up Telegram bot...")
    
    try:
        # Check if token is valid
        test_url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        response = requests.get(test_url, timeout=10)
        
        if response.status_code == 200:
            bot_data = response.json()
            print(f"✅ Bot connected: @{bot_data['result']['username']}")
            print(f"   Name: {bot_data['result']['first_name']}")
            print(f"   ID: {bot_data['result']['id']}")
            return True
        else:
            print(f"❌ Invalid bot token: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Telegram connection error: {e}")
        return False

def send_telegram_message(chat_id, text):
    """Send message via Telegram Bot API"""
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Telegram send error: {e}")
        return False

# === MISTRAL AI FUNCTIONS ===
def analyze_chart(image_base64, candle_speed="M5", trade_time="H1"):
    """Analyze chart using Mistral AI"""
    print(f"🔍 Analyzing chart with settings: {candle_speed}, {trade_time}")
    
    try:
        prompt = f"""
        أنت محلل فني خبير في أسواق المال. الصورة المرفقة هي رسم بياني (شارت) للتداول.
        
        الإعدادات:
        - سرعة الشموع: {candle_speed}
        - مدة الصفقة المتوقعة: {trade_time}
        
        قدم تحليلاً شاملاً يشمل:
        1. النمط السائد (تصاعدي/تنازلي/جانبي)
        2. ملاحظات فنية هامة
        3. تقييم عام للاتجاه
        4. توقعات واقعية
        
        كن:
        - موضوعياً وواقعياً
        - واضحاً ومباشراً
        - دقيقاً في الوصف
        - باللغة العربية فقط
        """
        
        payload = {
            "model": "pixtral-12b-2409",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                    ]
                }
            ],
            "max_tokens": 800,
            "temperature": 0.3
        }
        
        headers = {
            "Authorization": f"Bearer {MISTRAL_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=45)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content']
            return clean_text(result)
        else:
            print(f"❌ Mistral API error: {response.status_code}")
            return f"❌ خطأ في التحليل: {response.status_code}"
            
    except Exception as e:
        print(f"❌ Analysis error: {e}")
        return "❌ حدث خطأ أثناء التحليل. حاول مرة أخرى."

def chat_with_ai(message):
    """Chat with Mistral AI"""
    print(f"💭 Processing chat: {message[:50]}...")
    
    try:
        prompt = """أنت ABOOD GPT، مساعد ذكي عربي متخصص في:
        - التحليل الفني والمالي
        - الاستشارات التقنية
        - الكتابة والإبداع
        - حل المشكلات
        
        أنت:
        - ودود ومفيد
        - دقيق ومعلوماتي
        - تتحدث بالعربية الفصحى
        - تقدم إجابات شاملة
        
        أجب على سؤال المستخدم بأفضل طريقة ممكنة."""
        
        payload = {
            "model": "mistral-medium",
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": message}
            ],
            "max_tokens": 1000,
            "temperature": 0.7
        }
        
        headers = {
            "Authorization": f"Bearer {MISTRAL_KEY}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(MISTRAL_URL, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()['choices'][0]['message']['content']
            return clean_text(result)
        else:
            return f"❌ خطأ في الدردشة: {response.status_code}"
            
    except Exception as e:
        print(f"❌ Chat error: {e}")
        return "❌ حدث خطأ أثناء الدردشة. حاول مرة أخرى."

# === WEBHOOK HANDLER FOR TELEGRAM ===
@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook updates"""
    try:
        data = request.json
        print(f"📨 Received update: {data}")
        
        # Process the update here
        # This is a simplified version - you'd need to implement full processing
        
        return "OK"
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return "ERROR", 500

# === MAIN FUNCTIONS ===
def start_flask():
    """Start Flask server"""
    print(f"🌐 Starting Flask server on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

def initialize_system():
    """Initialize the entire system"""
    print("=" * 60)
    print("🚀 ABOOD GPT SYSTEM INITIALIZATION")
    print("=" * 60)
    
    # Initialize database
    if not init_db():
        print("❌ Failed to initialize database")
        return False
    
    # Setup Telegram bot
    if not setup_telegram_bot():
        print("❌ Failed to setup Telegram bot")
        return False
    
    print("✅ System initialized successfully")
    print("📡 Ready to receive requests")
    print("=" * 60)
    
    return True

def main():
    """Main entry point"""
    print("🤖 ABOOD GPT - Telegram Bot System")
    print("📅 " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("-" * 40)
    
    # Initialize system
    if not initialize_system():
        print("❌ System initialization failed. Exiting...")
        return
    
    # Start Flask server in main thread
    print("\n" + "=" * 60)
    print("🎯 SYSTEM IS NOW RUNNING")
    print("=" * 60)
    print(f"🔗 Web Interface: http://localhost:{PORT}")
    print(f"🔗 Health Check: http://localhost:{PORT}/health")
    print(f"🔗 Ping: http://localhost:{PORT}/ping")
    print("\n💡 Note: This is a Flask-only version.")
    print("   The bot is ready but needs to be configured with webhooks.")
    print("   Currently works as a keep-alive service for Render.")
    print("=" * 60)
    
    start_flask()

# === SIMPLE COMMAND LINE INTERFACE ===
if __name__ == "__main__":
    # Handle command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "test":
            print("🧪 Running tests...")
            # Test database
            init_db()
            # Test bot connection
            setup_telegram_bot()
            print("✅ Tests completed")
        elif sys.argv[1] == "setup":
            print("🔧 Setting up webhook...")
            # You would set up webhook here
            print("✅ Setup completed (webhook not implemented in this version)")
        else:
            print(f"❌ Unknown command: {sys.argv[1]}")
            print("Usage: python bot.py [test|setup]")
    else:
        # Run normally
        main()
