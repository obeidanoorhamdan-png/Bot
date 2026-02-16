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
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from telegram.error import NetworkError, TimedOut
from flask import Flask, request, jsonify, render_template_string
from PIL import Image
import pytz

# --- الإعدادات ---
TOKEN = os.environ.get('TOKEN', "8583903565:AAFveu3KYGkPsC8L6WyermoaZ1tI_EtFHHs")

MISTRAL_KEY = os.environ.get('MISTRAL_KEY', "G3ubWgZB1AadvkGtpwFpHd1eEZlTuZ64")
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
MISTRAL_MODEL = "pixtral-large-latest"          # للرؤية والاستخراج البصري
MISTRAL_MODEL_AUDIT = "mistral-large-latest"        # للتحليل المنطقي
MODEL_SUMMARY = "mistral-medium-latest"        # للتلخيص السريع

DB_NAME = "abood-gpt.db"

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

# حالات المحادثة
MAIN_MENU, SETTINGS_CANDLE, SETTINGS_TIME, CHAT_MODE, ANALYZE_MODE, RECOMMENDATION_MODE, CATEGORY_SELECTION, ADMIN_MENU, ADD_USER, REMOVE_USER, SET_USER_DURATION = range(11)

# إنشاء مجلد التخزين المؤقت إذا لم يكن موجوداً
if not os.path.exists(IMAGE_CACHE_DIR):
    os.makedirs(IMAGE_CACHE_DIR)

# --- Flask Server مع واجهة تحكم ---
app = Flask(__name__)

# HTML template for admin panel
ADMIN_PANEL_HTML = """
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>لوحة تحكم Obeida Trading</title>
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
            max-width: 1200px;
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
        .users-section {
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
        .users-table {
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
        .status-never {
            background: #cce5ff;
            color: #004085;
        }
        .actions {
            display: flex;
            gap: 10px;
            justify-content: center;
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
        .btn-add {
            background: #28a745;
            color: white;
            margin-bottom: 20px;
        }
        .btn-add:hover {
            background: #218838;
        }
        .btn-edit {
            background: #ffc107;
            color: #333;
        }
        .btn-edit:hover {
            background: #e0a800;
        }
        .btn-delete {
            background: #dc3545;
            color: white;
        }
        .btn-delete:hover {
            background: #c82333;
        }
        .btn-renew {
            background: #17a2b8;
            color: white;
        }
        .btn-renew:hover {
            background: #138496;
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
            max-width: 500px;
        }
        .modal-content h3 {
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
        .btn-save {
            background: #28a745;
            color: white;
        }
        .btn-cancel {
            background: #6c757d;
            color: white;
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
        .search-box input:focus {
            outline: none;
            border-color: #667eea;
        }
        .duration-badge {
            background: #e9ecef;
            padding: 3px 8px;
            border-radius: 15px;
            font-size: 0.85em;
            color: #495057;
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
            <h1>🤖 لوحة تحكم Obeida Trading</h1>
            <p>إدارة المستخدمين والاشتراكات - المشرف: @Sz2zv</p>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>إجمالي المستخدمين</h3>
                <div class="number">{{ total_users }}</div>
            </div>
            <div class="stat-card">
                <h3>المستخدمين النشطين</h3>
                <div class="number">{{ active_users }}</div>
            </div>
            <div class="stat-card">
                <h3>المنتهية اشتراكاتهم</h3>
                <div class="number">{{ expired_users }}</div>
            </div>
            <div class="stat-card">
                <h3>تحليلات اليوم</h3>
                <div class="number">{{ today_analyses }}</div>
            </div>
        </div>

        <div class="users-section">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                <h2 class="section-title">👥 قائمة المستخدمين</h2>
                <button class="btn btn-add" onclick="showAddModal()">➕ إضافة مستخدم جديد</button>
            </div>
            
            <div class="search-box">
                <input type="text" id="searchInput" placeholder="🔍 بحث باسم المستخدم أو المعرف..." onkeyup="searchUsers()">
            </div>

            <div class="users-table">
                <table id="usersTable">
                    <thead>
                        <tr>
                            <th>المعرف</th>
                            <th>اسم المستخدم</th>
                            <th>تاريخ الاشتراك</th>
                            <th>انتهاء الاشتراك</th>
                            <th>الحالة</th>
                            <th>الإجراءات</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for user in users %}
                        <tr>
                            <td>{{ user.user_id }}</td>
                            <td>@{{ user.username }}</td>
                            <td>{{ user.subscription_date }}</td>
                            <td>
                                {% if user.subscription_end %}
                                    {{ user.subscription_end }}
                                    <span class="duration-badge">{{ user.duration_text }}</span>
                                {% else %}
                                    غير محدد
                                {% endif %}
                            </td>
                            <td>
                                {% if user.subscription_end %}
                                    {% if user.is_active %}
                                        <span class="status-badge status-active">✅ نشط</span>
                                    {% else %}
                                        <span class="status-badge status-expired">❌ منتهي</span>
                                    {% endif %}
                                {% else %}
                                    <span class="status-badge status-never">🔄 غير محدد</span>
                                {% endif %}
                            </td>
                            <td class="actions">
                                <button class="btn btn-edit" onclick="showDurationModal('{{ user.user_id }}', '@{{ user.username }}')">⏱️ تمديد</button>
                                <button class="btn btn-delete" onclick="deleteUser('{{ user.user_id }}')">🗑️ حذف</button>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Modal إضافة مستخدم -->
    <div id="addModal" class="modal">
        <div class="modal-content">
            <h3>➕ إضافة مستخدم جديد</h3>
            <div class="form-group">
                <label>معرف المستخدم (Username):</label>
                <input type="text" id="newUsername" placeholder="أدخل username بدون @">
            </div>
            <div class="form-group">
                <label>مدة الاشتراك:</label>
                <select id="subscriptionDuration">
                    <option value="3600">ساعة واحدة</option>
                    <option value="86400">يوم واحد</option>
                    <option value="604800">أسبوع</option>
                    <option value="2592000">شهر</option>
                    <option value="0">غير محدد</option>
                </select>
            </div>
            <div class="modal-actions">
                <button class="btn btn-save" onclick="addUser()">حفظ</button>
                <button class="btn btn-cancel" onclick="hideAddModal()">إلغاء</button>
            </div>
        </div>
    </div>

    <!-- Modal تعديل المدة -->
    <div id="durationModal" class="modal">
        <div class="modal-content">
            <h3>⏱️ تعديل مدة الاشتراك</h3>
            <p id="durationUserInfo"></p>
            <div class="form-group">
                <label>المدة الجديدة:</label>
                <select id="newDuration">
                    <option value="3600">ساعة واحدة</option>
                    <option value="86400">يوم واحد</option>
                    <option value="604800">أسبوع</option>
                    <option value="2592000">شهر</option>
                    <option value="0">غير محدد</option>
                </select>
            </div>
            <div class="modal-actions">
                <button class="btn btn-save" onclick="updateDuration()">تحديث</button>
                <button class="btn btn-cancel" onclick="hideDurationModal()">إلغاء</button>
            </div>
        </div>
    </div>

    <div class="footer">
        <p>Obeida Trading - نظام إدارة الاشتراكات المتقدم</p>
    </div>

    <script>
        let currentUserId = null;

        function searchUsers() {
            let input = document.getElementById('searchInput');
            let filter = input.value.toLowerCase();
            let table = document.getElementById('usersTable');
            let tr = table.getElementsByTagName('tr');

            for (let i = 1; i < tr.length; i++) {
                let tdUsername = tr[i].getElementsByTagName('td')[1];
                let tdId = tr[i].getElementsByTagName('td')[0];
                if (tdUsername || tdId) {
                    let txtValueUsername = tdUsername.textContent || tdUsername.innerText;
                    let txtValueId = tdId.textContent || tdId.innerText;
                    if (txtValueUsername.toLowerCase().indexOf(filter) > -1 || txtValueId.indexOf(filter) > -1) {
                        tr[i].style.display = '';
                    } else {
                        tr[i].style.display = 'none';
                    }
                }
            }
        }

        function showAddModal() {
            document.getElementById('addModal').style.display = 'flex';
        }

        function hideAddModal() {
            document.getElementById('addModal').style.display = 'none';
            document.getElementById('newUsername').value = '';
        }

        function showDurationModal(userId, username) {
            currentUserId = userId;
            document.getElementById('durationUserInfo').innerText = `المستخدم: ${username}`;
            document.getElementById('durationModal').style.display = 'flex';
        }

        function hideDurationModal() {
            document.getElementById('durationModal').style.display = 'none';
            currentUserId = null;
        }

        async function addUser() {
            let username = document.getElementById('newUsername').value.trim();
            let duration = document.getElementById('subscriptionDuration').value;
            
            if (!username) {
                alert('الرجاء إدخال اسم المستخدم');
                return;
            }

            try {
                let response = await fetch('/admin/add_user', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        username: username,
                        duration: parseInt(duration)
                    })
                });
                
                let result = await response.json();
                if (result.success) {
                    alert('✅ تم إضافة المستخدم بنجاح');
                    location.reload();
                } else {
                    alert('❌ ' + result.error);
                }
            } catch (error) {
                alert('حدث خطأ في الاتصال');
            }
            
            hideAddModal();
        }

        async function updateDuration() {
            if (!currentUserId) return;
            
            let duration = document.getElementById('newDuration').value;
            
            try {
                let response = await fetch('/admin/update_duration', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        user_id: parseInt(currentUserId),
                        duration: parseInt(duration)
                    })
                });
                
                let result = await response.json();
                if (result.success) {
                    alert('✅ تم تحديث مدة الاشتراك بنجاح');
                    location.reload();
                } else {
                    alert('❌ ' + result.error);
                }
            } catch (error) {
                alert('حدث خطأ في الاتصال');
            }
            
            hideDurationModal();
        }

        async function deleteUser(userId) {
            if (!confirm('هل أنت متأكد من حذف هذا المستخدم؟')) {
                return;
            }
            
            try {
                let response = await fetch('/admin/delete_user', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        user_id: parseInt(userId)
                    })
                });
                
                let result = await response.json();
                if (result.success) {
                    alert('✅ تم حذف المستخدم بنجاح');
                    location.reload();
                } else {
                    alert('❌ ' + result.error);
                }
            } catch (error) {
                alert('حدث خطأ في الاتصال');
            }
        }

        // Close modals when clicking outside
        window.onclick = function(event) {
            let addModal = document.getElementById('addModal');
            let durationModal = document.getElementById('durationModal');
            if (event.target == addModal) {
                addModal.style.display = 'none';
            }
            if (event.target == durationModal) {
                durationModal.style.display = 'none';
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
        <title>Obeida Trading</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
            h1 { font-size: 3em; margin-bottom: 20px; }
            .status { background: rgba(255,255,255,0.2); padding: 20px 40px; border-radius: 10px; display: inline-block; margin: 20px; }
            .admin-link { background: #28a745; color: white; padding: 15px 30px; border-radius: 5px; text-decoration: none; display: inline-block; margin: 20px; }
        </style>
    </head>
    <body>
        <h1> 📊 Obeida Trading Telegram Bot 📊</h1>
        <p>Chat & Technical Analysis Bot with Subscription System</p>
        <div class="status">✅ البوت يعمل بنظام الاشتراكات</div>
        <p>Last Ping: """ + time.strftime("%Y-%m-%d %H:%M:%S") + """</p>
        <a href="/admin" class="admin-link">🔐 لوحة تحكم المشرف</a>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return {"status": "active", "ai_provider": "Mistral AI", "subscription_system": "active", "timestamp": time.time()}

@app.route('/admin')
def admin_panel():
    """لوحة تحكم المشرف"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # إحصائيات
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_end > datetime('now') OR subscription_end IS NULL")
        active_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_end < datetime('now') AND subscription_end IS NOT NULL")
        expired_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM chat_history WHERE date(timestamp) = date('now')")
        today_analyses = cursor.fetchone()[0]
        
        # قائمة المستخدمين
        cursor.execute("""
            SELECT user_id, username, subscription_start, subscription_end 
            FROM users ORDER BY 
            CASE 
                WHEN subscription_end IS NULL THEN 1 
                ELSE 0 
            END,
            subscription_end DESC
        """)
        users_data = cursor.fetchall()
        
        users = []
        for user in users_data:
            user_id, username, sub_start, sub_end = user
            
            # حساب حالة المستخدم
            is_active = False
            if sub_end:
                try:
                    end_time = datetime.fromisoformat(sub_end)
                    is_active = end_time > datetime.now()
                except:
                    is_active = False
            else:
                is_active = True
            
            # تنسيق التواريخ
            sub_start_str = sub_start if sub_start else "غير محدد"
            sub_end_str = sub_end if sub_end else "غير محدد"
            
            # حساب مدة الاشتراك
            duration_text = ""
            if sub_end and sub_start:
                try:
                    start = datetime.fromisoformat(sub_start)
                    end = datetime.fromisoformat(sub_end)
                    delta = end - start
                    
                    if delta.days >= 30:
                        months = delta.days // 30
                        duration_text = f"{months} شهر"
                    elif delta.days >= 7:
                        weeks = delta.days // 7
                        duration_text = f"{weeks} أسبوع"
                    elif delta.days >= 1:
                        duration_text = f"{delta.days} يوم"
                    elif delta.seconds >= 3600:
                        hours = delta.seconds // 3600
                        duration_text = f"{hours} ساعة"
                    else:
                        duration_text = "أقل من ساعة"
                except:
                    duration_text = "غير محدد"
            
            users.append({
                'user_id': user_id,
                'username': username or 'لا يوجد',
                'subscription_date': sub_start_str[:16] if sub_start_str != "غير محدد" else "غير محدد",
                'subscription_end': sub_end_str[:16] if sub_end_str != "غير محدد" else "غير محدد",
                'is_active': is_active,
                'duration_text': duration_text
            })
        
        conn.close()
        
        return render_template_string(
            ADMIN_PANEL_HTML,
            total_users=total_users,
            active_users=active_users,
            expired_users=expired_users,
            today_analyses=today_analyses,
            users=users
        )
    except Exception as e:
        return f"خطأ في تحميل لوحة التحكم: {str(e)}"

@app.route('/admin/add_user', methods=['POST'])
def add_user():
    """إضافة مستخدم جديد عبر API"""
    try:
        data = request.json
        username = data.get('username', '').strip().replace('@', '')
        duration = data.get('duration', 0)  # 0 يعني غير محدد
        
        if not username:
            return jsonify({'success': False, 'error': 'اسم المستخدم مطلوب'})
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # البحث عن المستخدم في قاعدة بيانات التليجرام (هذا يتطلب تفاعل المستخدم مع البوت أولاً)
        # سنقوم بإضافة المستخدم بشكل مؤقت حتى يتفاعل مع البوت
        cursor.execute("""
            INSERT OR IGNORE INTO users (user_id, username, candle, trade_time)
            VALUES (?, ?, ?, ?)
        """, (0, username, "M1", "قصير (1m-15m)"))
        
        # تحديث معلومات الاشتراك
        if duration > 0:
            end_date = datetime.now() + timedelta(seconds=duration)
            cursor.execute("""
                UPDATE users 
                SET subscription_start = datetime('now'),
                    subscription_end = ?,
                    username = ?
                WHERE username = ? OR user_id = ?
            """, (end_date.isoformat(), username, username, 0))
        else:
            cursor.execute("""
                UPDATE users 
                SET subscription_start = datetime('now'),
                    subscription_end = NULL,
                    username = ?
                WHERE username = ? OR user_id = ?
            """, (username, username, 0))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/update_duration', methods=['POST'])
def update_duration():
    """تحديث مدة الاشتراك"""
    try:
        data = request.json
        user_id = data.get('user_id')
        duration = data.get('duration', 0)
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if duration > 0:
            end_date = datetime.now() + timedelta(seconds=duration)
            cursor.execute("""
                UPDATE users 
                SET subscription_end = ?
                WHERE user_id = ?
            """, (end_date.isoformat(), user_id))
        else:
            cursor.execute("""
                UPDATE users 
                SET subscription_end = NULL
                WHERE user_id = ?
            """, (user_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/delete_user', methods=['POST'])
def delete_user():
    """حذف مستخدم"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # حذف المستخدم وسجل محادثاته
        cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/ping')
def ping():
    return "PONG"

# --- دوال التحقق من الاشتراك ---
def init_db_with_subscription():
    """تهيئة قاعدة البيانات مع نظام الاشتراكات"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # جدول المستخدمين الأساسي
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
    
    # إضافة المشرف الرئيسي
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, is_admin) VALUES (?, ?, ?)", 
                  (ADMIN_ID, "Sz2zv", 1))
    
    conn.commit()
    conn.close()
    print("✅ Database initialized with subscription system")

def check_subscription(user_id):
    """التحقق من صلاحية اشتراك المستخدم"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin, subscription_end FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return False, "غير مسجل"
        
        is_admin, sub_end = result
        
        # المشرف دائماً له صلاحية
        if is_admin == 1:
            return True, "مشرف"
        
        # إذا لم يكن هناك اشتراك محدد
        if not sub_end:
            return False, "غير مشترك"
        
        # التحقق من تاريخ انتهاء الاشتراك
        try:
            end_date = datetime.fromisoformat(sub_end)
            if end_date > datetime.now():
                remaining = end_date - datetime.now()
                hours = remaining.total_seconds() / 3600
                if hours < 24:
                    return True, f"ينتهي بعد {int(hours)} ساعة"
                else:
                    return True, f"ينتهي بعد {remaining.days} يوم"
            else:
                return False, "انتهى الاشتراك"
        except:
            return False, "خطأ في تاريخ الاشتراك"
            
    except Exception as e:
        print(f"⚠️ خطأ في التحقق من الاشتراك: {e}")
        return False, "خطأ في النظام"

def add_user_subscription(username, duration_seconds=86400):
    """إضافة مستخدم جديد مع اشتراك"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if duration_seconds > 0:
            end_date = datetime.now() + timedelta(seconds=duration_seconds)
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, username, subscription_start, subscription_end, candle, trade_time)
                VALUES (?, ?, datetime('now'), ?, ?, ?)
            """, (0, username, end_date.isoformat(), "M1", "قصير (1m-15m)"))
        else:
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, username, subscription_start, subscription_end, candle, trade_time)
                VALUES (?, ?, datetime('now'), NULL, ?, ?)
            """, (0, username, "M1", "قصير (1m-15m)"))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"⚠️ خطأ في إضافة المستخدم: {e}")
        return False

async def subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """ديكوريتور للتحقق من الاشتراك - مع إخفاء الأزرار تماماً للمستخدمين غير المشتركين"""
    user_id = update.effective_user.id
    
    # المشرف دائماً له صلاحية
    if user_id == ADMIN_ID:
        return True
    
    status, message = check_subscription(user_id)
    
    if not status:
        # إخفاء جميع الأزرار وإظهار رسالة اشتراك فقط
        keyboard = [[InlineKeyboardButton("📞 التواصل مع المشرف", url="https://t.me/Sz2zv")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # مسح أي أزرار موجودة وإظهار رسالة الاشتراك فقط
        await update.message.reply_text(
            f"⚠️ **عذراً، ليس لديك صلاحية استخدام البوت**\n\n"
            f"❌ الحالة: {message}\n\n"
            f"📞 للاشتراك والتجديد، تواصل مع المشرف:\n"
            f"👤 @Sz2zv\n\n"
            f"بعد الاشتراك، أرسل /start مرة أخرى",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return False
    
    return True

async def show_subscription_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض رسالة الاشتراك للمستخدمين غير المشتركين"""
    user_id = update.effective_user.id
    
    # المشرف لا يحتاج هذه الرسالة
    if user_id == ADMIN_ID:
        return
    
    status, message = check_subscription(user_id)
    
    if not status:
        keyboard = [[InlineKeyboardButton("📞 التواصل مع المشرف", url="https://t.me/Sz2zv")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"⚠️ **عذراً، ليس لديك صلاحية استخدام البوت**\n\n"
            f"❌ الحالة: {message}\n\n"
            f"📞 للاشتراك والتجديد، تواصل مع المشرف:\n"
            f"👤 @Sz2zv\n\n"
            f"بعد الاشتراك، أرسل /start مرة أخرى",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return True
    return False

# --- دوال المساعدة ---
def cleanup_old_images():
    """تنظيف الصور القديمة التي مضى عليها أكثر من 30 دقيقة"""
    try:
        current_time = time.time()
        for filename in os.listdir(IMAGE_CACHE_DIR):
            filepath = os.path.join(IMAGE_CACHE_DIR, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > 1800:
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
        if symbol == "EUR/USD (OTC)":
            chart_symbol = "FX:EURUSD"
        elif symbol == "GBP/USD (OTC)":
            chart_symbol = "FX:GBPUSD"
        elif symbol == "USD/JPY (OTC)":
            chart_symbol = "FX:USDJPY"
        elif symbol == "USD/CHF (OTC)":
            chart_symbol = "FX:USDCHF"
        elif symbol == "AUD/USD (OTC)":
            chart_symbol = "FX:AUDUSD"
        elif symbol == "USD/CAD (OTC)":
            chart_symbol = "FX:USDCAD"
        elif symbol == "NZD/USD (OTC)":
            chart_symbol = "FX:NZDUSD"
        elif symbol == "EUR/GBP (OTC)":
            chart_symbol = "FX:EURGBP"
        elif symbol == "EUR/JPY (OTC)":
            chart_symbol = "FX:EURJPY"
        elif symbol == "GBP/JPY (OTC)":
            chart_symbol = "FX:GBPJPY"
        elif symbol == "EUR/CHF (OTC)":
            chart_symbol = "FX:EURCHF"
        elif symbol == "AUD/JPY (OTC)":
            chart_symbol = "FX:AUDJPY"
        elif symbol == "EUR/AUD (OTC)":
            chart_symbol = "FX:EURAUD"
        elif symbol == "EUR/CAD (OTC)":
            chart_symbol = "FX:EURCAD"
        elif symbol == "GBP/AUD (OTC)":
            chart_symbol = "FX:GBPAUD"
        elif symbol == "CAD/JPY (OTC)":
            chart_symbol = "FX:CADJPY"
        elif symbol == "CHF/JPY (OTC)":
            chart_symbol = "FX:CHFJPY"
        elif symbol == "NZD/JPY (OTC)":
            chart_symbol = "FX:NZDJPY"
        elif symbol == "GBP/CHF (OTC)":
            chart_symbol = "FX:GBPCHF"
        elif symbol == "AUD/CAD (OTC)":
            chart_symbol = "FX:AUDCAD"
        elif symbol == "S&P 500 (OTC)":
            chart_symbol = "SP:SPX"
        elif symbol == "Dow Jones (OTC)":
            chart_symbol = "DJ:DJI"
        elif symbol == "Nasdaq 100 (OTC)":
            chart_symbol = "NASDAQ:NDX"
        elif symbol == "DAX 40 (OTC)":
            chart_symbol = "GER30"
        elif symbol == "CAC 40 (OTC)":
            chart_symbol = "FRA40"
        elif symbol == "FTSE 100 (OTC)":
            chart_symbol = "UK100"
        elif symbol == "Hang Seng (OTC)":
            chart_symbol = "HK50"
        elif symbol == "Nikkei 225 (OTC)":
            chart_symbol = "JPN225"
        elif symbol == "Gold (OTC)":
            chart_symbol = "TVC:GOLD"
        elif symbol == "Silver (OTC)":
            chart_symbol = "TVC:SILVER"
        elif symbol == "UKOIL (OTC)":
            chart_symbol = "TVC:UKOIL"
        elif symbol == "USOIL (OTC)":
            chart_symbol = "TVC:USOIL"
        elif symbol == "Natural Gas (OTC)":
            chart_symbol = "TVC:NATGAS"
        elif symbol == "Apple (OTC)":
            chart_symbol = "NASDAQ:AAPL"
        elif symbol == "Amazon (OTC)":
            chart_symbol = "NASDAQ:AMZN"
        elif symbol == "Google (OTC)":
            chart_symbol = "NASDAQ:GOOGL"
        elif symbol == "Facebook (OTC)":
            chart_symbol = "NASDAQ:META"
        elif symbol == "Microsoft (OTC)":
            chart_symbol = "NASDAQ:MSFT"
        elif symbol == "Tesla (OTC)":
            chart_symbol = "NASDAQ:TSLA"
        elif symbol == "Netflix (OTC)":
            chart_symbol = "NASDAQ:NFLX"
        elif symbol == "Intel (OTC)":
            chart_symbol = "NASDAQ:INTC"
        elif symbol == "Boeing (OTC)":
            chart_symbol = "NYSE:BA"
        elif symbol == "Visa (OTC)":
            chart_symbol = "NYSE:V"
        elif symbol == "McDonald's (OTC)":
            chart_symbol = "NYSE:MCD"
        elif symbol == "Pfizer (OTC)":
            chart_symbol = "NYSE:PFE"
        elif symbol == "Coca-Cola (OTC)":
            chart_symbol = "NYSE:KO"
        elif symbol == "Disney (OTC)":
            chart_symbol = "NYSE:DIS"
        elif symbol == "Alibaba (OTC)":
            chart_symbol = "NYSE:BABA"
        elif symbol == "Walmart (OTC)":
            chart_symbol = "NYSE:WMT"
        
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
            time_diff = (datetime.now() - datetime.fromisoformat(context_time)).total_seconds() / 60
            if time_diff > 10:
                return "", None
        return context, context_time
    return "", None

def cleanup_old_database_records():
    """تنظيف سجلات قاعدة البيانات القديمة"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("DELETE FROM chat_history WHERE timestamp < ?", (week_ago,))
        deleted_rows = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted_rows > 0:
            print(f"🧹 تم حذف {deleted_rows} سجل دردشة قديم")
            
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
    """تنظيف النص من التكرارات وتحسين التنسيق - الإصدار المحسّن"""
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
    """تقسيم الرسالة الطويلة إلى أجزاء (للتوافق مع الكود القديم)"""
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
    """تنظيف البيانات المؤقتة للمستخدم - الإصدار المحسّن"""
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
                user_id = update.effective_user.id
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

📊 المرحلة 1: الفحص الأولي والتحذيرات
1.1 نظام الأمان ثلاثي الطبقات:
• الدرع الأساسي
• كشف وهم الزخم: 3 شموع كبيرة، فحص الاستدامة
• التحقق الرقمي: استخراج الأسعار من المحور اليمني ومطابقتها مع الشارت
• توقيت إغلاق الشمعة: {candle_closing_status}

1.2 كشف مخاطر OTC:
• إشارات التلاعب: اختراق ثم عودة، انعكاس لحظي، حركة بدون حجم
• حماية: تجنب آخر 10 ثوانٍ، أوامر معلقة، SL +20%

1.3 تحليل الارتباط السعري:
• Forex: مؤشر الدولار، العملات المرتبطة، السندات
• Stocks: المؤشر العام، القطاع، أرباح
• Crypto: BTC، Altcoins، مؤشر الخوف والجشع

💰 المرحلة 2: التحليل الهيكلي
2.1 تحديد الهيكل: SMC + BOS/CHoCh بدقة
2.2 استخراج الإحداثيات: High/Low، نسبة الحركة، دقة مطلقة
2.3 مصفاة التسعير: Discount للشراء، Premium للبيع، مناطق الطوارئ <20%/>80%

💰 المرحلة 3: السيولة والزخم
3.1 كشف وهم الزخم: فجوات، شموع خبر، ذيول طويلة، V-Reversal
3.2 خرائط السيولة: Equal Highs/Lows، Inducement، Liquidity Sweeps، FVG
3.3 انعكاس الزخم المفاجئ: رفض بعد اندفاع، فشل اختراق، انخفاض حجم، دايفرجنس

🎯 المرحلة 4: القرار الذكي
• POI صالح + نموذج شموعي + سلوك سعري واضح + توافق الاتجاه
• تعديل المخاطر حسب الأخبار: SL × {news_risk_multiplier}, الحجم ÷ {news_risk_multiplier}
• حظر كامل: أخبار قوية ±30 دقيقة، زخم وهمي، فشل الفلاتر، V-Reversal، تضارب المؤشرات
• حل التعارض: الأولوية: الزخم → السيولة → الفجوات → الهيكل → المؤشرات → السياق الزمني

💡 قاعدة كسر العرض بالاندفاع:
"إذا تعارضت منطقة العرض مع شمعة اندفاعية (Marubozu) تخترق مستويات السيولة، اعتبر المنطقة 'مكسورة' فوراً ولا تقترح البيع إلا بعد إعادة اختبار ناجحة أو فشل اختراق مؤكد (SFP) مع تأكيد من شمعتين."

📊 المرحلة 5: سلوك الشموع
• استجابة POI: رفض/امتصاص/جانبي، القوة: جسم/ذيول، الحجم
• قانون 3 شموع: اختبار → تصحيح → اختراق
• التتابع الزمني: شمعة 1 رد فعل، شمعة 2 تأكيد، شمعة 3 قرار

📉 المرحلة 6: MACD المحسن
• 1-5 دقائق: تجاهل التقاطعات البطيئة ودايفرجنس عند تعارضها مع زخم قوي
• 15-60 دقائق: خط الصفر + دايفرجنس عند POI
• حل التعارض: سلوك سعري واضح → تجاهل MACD

⏰ المرحلة 7: تعدد الإطارات
• HTF: الاتجاه العام، MTF1: العرض/الطلب، MTF2: OB نشطة، LTF: الدخول
• توافق: 4/4=+40, 3/4=+30, 2/4=تقليل حجم 50%, 1/4=منع الدخول
• استراتيجية: شراء = HTF صاعد → تصحيح → OB → إشارة، بيع = HTF هابط → ارتداد → OB → إشارة

🎯 المرحلة 8: درجات الثقة (معدلة)
• + نقاط: زخم قوي (9-10) ×2 = +40، POI +25، نموذج شموعي +20، سلوك واضح +25، توافق الإطارات +30، حجم أعلى +15، أخبار هادئة +20، BOS +30، تغطية فجوة +15، اختراق مع بداية ساعة جديدة +25
• - خصومات: تعارض مؤشرات -20، أخبار قوية -25، زخم وهمي -15، V-Reversal قريب -30، سيولة OTC منخفضة -10، بيع فوق 50% من موجة صاعدة بدون دخول Premium -40
• مستويات: 95-100 💥💥, 85-94 💥, 70-84 🔥, 55-69 ⚡, 40-54 ❄️, <40 🚫

📊 المرحلة 9: الحجم المتقدم
• اختراق >150%, امتصاص: حجم عالي + حركة محدودة
• تصحيح <70%, انعكاس: حجم مرتفع مفاجئ
• نقاط التحكم: POC = دعم/مقاومة, VA 70% توازن, EVA خارج VA = قوة

🔄 المرحلة 10: إدارة الصفقات
• Long: TP1 SL للتعادل +40%, TP2 أعلى شمعة +30%, TP3 30% بترايل
• Short: نفس النمط
• OTC حماية: SL +20%, بعد 3 شموع، حجم 33/33/34

🧠 المرحلة 11: التحليل السلوكي والتلاعب
• الخوف، الجشع، التردد، الاستسلام
• التلاعب: Liquidity Sweep, Stop Hunt, False Breakout, Bait Pattern
• سلوك OTC: نمط 3 مرات، اختراقات محددة، شمعة تغير السياق، أوامر معلقة
• التمييز: ذيل + عودة = فخ, جسم كامل + إغلاق = BOS

⚠️ المرحلة 12: تثبيت القرار النهائي
1. قرار ثابت لكل صورة متطابقة
2. لا تغيير إلا عند تغير واضح في الشارت
3. تأكيد مزدوج: القرار صحيح عند ظهور مرتين بنفس المعطيات
4. تحقق من كل الأسعار والمستويات في الصورة بدقة

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

async def start_chat_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع الدردشة المتقدم"""
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return CHAT_MODE
    
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
    
    # برومبتات متخصصة حسب الاختيار
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

async def handle_photo_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور للتحليل الفني المتقدم"""
    # التحقق من الاشتراك
    if not await subscription_required(update, context):
        return MAIN_MENU
    
    user_id = update.effective_user.id
    candle, trade_time, prev_context, prev_time = get_user_setting(user_id)
    
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
2. **تحديد الزمن الحالي:** ابحث عن ساعة المنصة في الزاوية اليسرى/اليمنى السفلية
3. **تحليل الشموع:** افحص آخر 5-7 شموع (الأجسام، الذيول، الألوان، الأحجام)
4. **رسم المستويات:** حدد الدعم/المقاومة، الفجوات، مناطق السيولة
5. **التطبيق العملي:** طبق كل القواعد أدناه على البيانات المستخرجة من الصورة فقط

{previous_context_info}

🚨 **تنبيه الربط البرمجي الإلزامي (Compulsory Variable Binding):**
هذا البرومبت يعمل ضمن نظام Bot.py ويستقبل المتغيرات التالية من الكود. تأكد من تمريرها جميعاً في دالة .format().

📌 **المتغيرات المستلمة من Bot.py:**
• إطار الشموع: {{candle}}
• فريم التحقق: {{verification_timeframe}}
• استراتيجية التداول: {{trading_strategy}}
• جلسة السوق: {{session_name}} ({{session_time}})
• حالة السيولة: {{session_vol}}
• تأثير الأخبار: {{news_impact}} (معامل ×{{news_risk_multiplier}})
• تحذير الأخبار: {{news_warning if news_warning else "✅ الوضع آمن"}}
• منطقة القتل السعري: {{kill_zone_status}}
• حالة دقيقة الغدر: {{last_minute_status}}
• حالة إغلاق الشمعة: {{candle_closing_status}}
• الوقت المتبقي للإغلاق: {{seconds_remaining}} ثانية
• توقيت التحليل: {{gaza_time.strftime('%Y-%m-%d %H:%M:%S بتوقيت غزة')}}
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
   - **الزمن:** الوقت الحالي على الشارت، إطار الشموع ({{candle}})
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

3. **الفحص الرياضي الدقيق (Anti-Visual Bias):**
   • **السعر الحالي الملاحظ:** [قراءة مباشرة من المحور Y]
   • **سعر الهدف الملاحظ:** [قراءة مباشرة من المستوى المستهدف]
   • **المعادلة الحسابية:** |السعر الحالي - سعر الهدف| = [النقاط]
   • **التأكيد:** يجب أن تتطابق الحسابات مع المسافات البصرية

4. **كشف التناقضات البصرية:**
   • إذا كانت المسافة المحسوبة ≠ المسافة المقدرة بالعين → أعد المسح
   • تأكد من تطابق كل ذيل مع رقمه على المحور اليميني
   • تحقق من مقياس الرسم (Scale) بوحدات متساوية

🔍 **المرحلة 3: التحقق المتعدد (Multi-Layer Verification):**
1. **التحقق المزدوج:** كل رقم يتم استخراجه يجب التحقق منه مرتين
2. **الربط المنطقي:** تأكد من تطابق الأسعار مع مواقع الشموع
3. **الكشف عن التناقضات:** إذا وجدت تناقضاً، أعد المسح من البداية

🔥 **قانون الفتيلة المؤكدة (The Confirmed Wick Law):**
الذيل (Wick) الطويل ليس إشارة دخول فورية بل "تحذير" يحتاج تأكيداً صارماً.

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

✅ **1. فلتر المسافة الحقيقية (Real Distance Filter):**
    • **قبل إصدار الإشارة، قم بطرح السعر الحالي من الهدف يدوياً.**
    • **إذا كانت المسافة أقل من 5 نقاط، القاعدة الأساسية = (احتفاظ 🟡).**
    
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
3.  **دقيقة الغدر:** داخل الدقائق (55-59) أو (25-29) أو (40-44) أو (10-14) **【راجع استثناء فريمات S5/S10 أدناه】**
4.  **زخم منهك:** الشمعة الحالية أكبر بضعفين من متوسط آخر 3 شموع
5.  **غياب التأكيد:** عدم وجود شمعة ابتلاع للجسم السابق **(يُلغى هذا السبب إذا تحقق شرط Marubozu Supremacy)**
6.  **تضارب الإطارات:** تعارض بين {{candle}} و {{verification_timeframe}}
7.  **أخبار نشطة:** داخل نطاق ±30 دقيقة من خبر قوي

🔍 **الخوارزمية:**
1.  افحص الزخم والهدف: إذا كان زخم >85% والهدف رقم مستدير → تفعيل "استثناء اللمس الحتمي"
2.  افحص المسافة بين السعر والهدف: أقل من 5 نقاط وبدون استثناء → احتفاظ فوري
3.  افحص السبريد: مسافة صافية < 3 نقاط وبدون استثناء → احتفاظ فوري
4.  افحص الخبر: شمعة > 300% → احتفاظ فوري
5.  افحص الشارت لجميع أسباب المنع السبعة
6.  إذا وُجد سبب واحد وليس مستثنى → "احتفاظ 🟡"
7.  إذا لم توجد أسباب منع أو تم تفعيل الاستثناء → تابع التحليل
8.  سجل سبب المنع أو الاستثناء في القرار النهائي بوضوح

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
• Order Blocks: تحديد آخر 3 مناطق طلب/عرض نشطة - من مناطق الارتداد المرئية
• FVG Tracking: تتبع الفجوات غير المغطاة في نطاق 50 نقطة - قياس المسافات بين الشموع
• Liquidity Map: رسم خرائط Equal Highs/Lows + Inducement - من القمم والقيعان المرئية

🎯 **المستوى 3: التحليل التنفيذي (الدخول الفوري) - الاستخراج من الصورة:**
• Entry Triggers: شروط الدخول المباشرة (شمعة إغلاق + حجم) - من الشمعة الحالية
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

🛡️ **طبقات الحماية الذكية:**
1.  **مصفاة الأخبار:** {{news_warning if news_warning else "✅ الوضع آمن"}}
2.  **فلتر التوقيت:** {{kill_zone_status}}
3.  **【تعديل ثواني الغدر - خاص بفريمات S5/S10 (Last Minute Exception)】**
    • **القاعدة الأساسية:** الحظر التام في آخر 10 ثوانٍ من الشمعة (دقيقة الغدر).
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
  - في الفريمات العادية: تجاهل الانعكاسات في الدقيقة 59/29/14/44
  - **في فريمات S5/S10 مع زخم >90%: تقليص الحظر إلى ثانيتين فقط والسماح بالدخول.**
• SPREAD FILTER: إذا كان الهدف < 5 نقاط والفريم {{candle}} في [S5,S10,S15] → احتفاظ **(إلا مع استثناء اللمس الحتمي)**
• NEWS SPIKE: إذا كانت الشمعة > 300% من المتوسط → حظر لمدة 3 شموع

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
• منطق OTC: 3 شموع قوية → الشمعة الرابعة بنفس الاتجاه
• تصحيح الفريم الصغير: تجاهل MACD ووايكوف عند التعارض مع السلوك السعري في فريمات الدقائق
• كشف وهم الزخم: تحقق من استدامة الحركة
• **استخراج البيانات:** **أسعار دقيقة من المحور اليمني - من الصورة فقط**
• **فلتر المسافة الحقيقية:** **إذا كانت المسافة بين السعر والهدف أقل من 5 نقاط، القرار الإلزامي هو (احتفاظ 🟡) إلا في حالة استثناء اللمس الحتمي (زخم >85% نحو رقم مستدير)**
• **فلتر السبريد:** **إذا كانت المسافة الصافية < 3 نقاط، القرار الإلزامي هو (احتفاظ 🟡) إلا مع استثناء اللمس الحتمي**
• **فلتر الأخبار:** **إذا كانت شمعة الخبر > 300%، حظر لمدة 3 شموع**
• فلتر الجدوى: RR ≥ 1:2 بعد تعديل الأخبار
• **المصداقية المطلقة:** **كن قاصياً في نقد الشارت؛ إذا لم تكن الإشارة واضحة بنسبة 90%، فالقرار الإلزامي هو (احتفاظ 🟡) ولا تخاطر بأموال المستخدم.**
• **تقييد الوسطية:** قرار واضح فقط (شراء/بيع/احتفاظ) مع مستوى الثقة

📌 **القواعد الجديدة المضافة (مع التعديلات):**

⚡ **قاعدة الفتيلة القاتلة (المعدلة):**
"إذا كان طول الذيل (Wick) يمثل أكثر من 60% من حجم الشمعة الكلي عند مستوى دعم أو مقاومة واضح، فهذا إشارة انعكاس قوية. تجاهل اتجاه الهيكل وادخل مع اتجاه الذيل."
**【استثناء Marubozu Supremacy (إلغاء)】:** إذا كان الجسم الممتلئ >90% والذيل <20%، **يُلغى هذا القانون تماماً** ويعتبر الذيل "جس نبض" لاستمرار الاتجاه.

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
"في فريمات S5/S10 فقط، إذا كان الزخم صاعداً/هابطاً والقوة > 90%، **يتم تقليص فلتر حماية الدقيقة الأخيرة من 10 ثوانٍ إلى ثانيتين فقط**، للسماح باللحاق بالانفجار السعري."

📊 **المرحلة 1: الفحص الأولي والتحذيرات - الاستخراج من الصورة**
1.1 **نظام الأمان رباعي الطبقات:**
• الدرع الأساسي (الأخبار)
• درع السبريد (الفريمات الصغيرة) - مع استثناء اللمس الحتمي
• كشف وهم الزخم: 3 شموع كبيرة، فحص الاستدامة - **من الشموع المرئية**
• **التحقق الرقمي: استخراج الأسعار من المحور اليمني ومطابقتها مع الشارت - التركيز الكامل على الصورة**
• توقيت إغلاق الشمعة: {{candle_closing_status}} - **مع استثناء ثواني الغدر للفريمات S5/S10 (تخفيض إلى ثانيتين)**

1.2 **كشف مخاطر OTC:**
• إشارات التلاعب: اختراق ثم عودة، انعكاس لحظي، حركة بدون حجم - **من سلوك الشموع**
• **حماية محسنة:** تجنب آخر 10 ثوانٍ **(يُستثنى فريمات S5/S10 مع زخم >90%، يُسمح بالدخول حتى آخر ثانيتين)**، أوامر معلقة، SL +20%

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
• حظر كامل: أخبار قوية ±30 دقيقة، زخم وهمي، فشل الفلاتر، V-Reversal، تضارب المؤشرات
• **حظر مشروط:** أهداف <5 نقاط على فريمات صغيرة **(يُستثنى مع استثناء اللمس الحتمي)**
• **حل التعارض (هرم الأولويات V3):** الأولوية: **الزخم** ← **السيولة (الهدف أولاً)** ← الفجوات ← الهيكل ← المؤشرات ← السياق الزمني

💡 **قاعدة كسر العرض بالاندفاع:**
"إذا تعارضت منطقة العرض مع شمعة اندفاعية (Marubozu) تخترق مستويات السيولة، اعتبر المنطقة 'مكسورة' فوراً ولا تقترح البيع إلا بعد إعادة اختبار ناجحة أو فشل اختراق مؤكد (SFP) مع تأكيد من شمعتين."

📊 **المرحلة 5: سلوك الشموع - الاستخراج من الصورة**
• استجابة POI: رفض/امتصاص/جانبي، القوة: جسم/ذيول، الحجم - **من الشموع المرئية**
• **قاعدة Marubozu Supremacy (قانون سيادة الجسم):** 
  - **إذا كان الجسم الممتلئ >90% والذيل <20% من طول الشمعة**
  - **لا يُعتبر الذيل إشارة ضعف أو انعكاس**
  - **يُصنف كـ "جس نبض" لاستمرار الاتجاه**
  - **يُلغي أي قاعدة تنادي بالانعكاس بناءً على هذا الذيل**
• قانون 3 شموع: اختبار → تصحيح → اختراق
• التتابع الزمني: شمعة 1 رد فعل، شمعة 2 تأكيد، شمعة 3 قرار

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
• ذيول الأخبار: لا تُحتسب في التحليل

⚠️ **المرحلة 12: تثبيت القرار النهائي**
1.  **قرار ثابت لكل صورة متطابقة**
2.  **لا تغيير إلا عند تغير واضح في الشارت**
3.  **تأكيد مزدوج:** القرار صحيح عند ظهور مرتين بنفس المعطيات
4.  **تحقق من كل الأسعار والمستويات في الصورة بدقة**
5.  **تحقق من السبريد:** الهدف > 5 نقاط للفريمات الصغيرة **(أو تفعيل استثناء اللمس الحتمي)**
6.  **تحقق من الأخبار:** لا يوجد شمعة حجم > 300% في آخر 3 شموع
7.  **تحقق من استثناءات الزخم:** هل تم تفعيل استثناء اللمس الحتمي أو استثناء ثواني الغدر؟

🎯 **النظام الزمني الذكي - حساب وقت الوصول:**

**🔧 إعدادات النظام الحالية:**
• إطار الشموع: {{candle}} (تم ضبطه من قبل المستخدم)
• استراتيجية التداول: {{trading_strategy}}
• جلسة السوق: {{session_name}} ({{session_time}})
• وقت التحليل الفعلي: {{gaza_time.strftime('%H:%M:%S بتوقيت غزة')}}
• الوقت على المنصة (من الصورة): [يتم استخراجه من الصورة]

**📊 المرحلة 1 - استخراج البيانات من الصورة:**
1.  **ابحث عن ساعة المنصة في الشارت (عادة في الزاوية اليسرى/اليمنى السفلى)**
2.  **سجل الوقت الحالي الذي تراه على الشارت: [ساعة:دقيقة:ثانية] - من الصورة فقط**
3.  **قدّر المسافة البصرية بين السعر الحالي والهدف (بعدد النقاط) - من الصورة فقط**
4.  **انظر إلى آخر 3 شموع واحسب متوسط حركتها (نقاط/شمعة) - من الصورة فقط**
5.  **عدد الشموع المطلوبة = المسافة ÷ متوسط حركة الشمعة**

**📋 البيانات المطلوبة من الصورة:**
• **الوقت على الشارت:** [مثال: 14:25:30] **← استخرج من الصورة**
• **المسافة للهدف:** [عدد النقاط] نقطة **← قس من الصورة**
• **متوسط حركة الشموع:** [نقاط/شمعة] **← احسب من الشموع المرئية**
• **الشموع المتوقعة:** [النتيجة] شمعة **← استنتج من البيانات**

**🧮 المرحلة 2 - الحساب الرياضي الدقيق (بدون تخمين بصري):**
1.  **البيانات المطلوبة كتابة في التحليل:**
    • **السعر الحالي الملاحظ:** [يقرأ من المحور Y] 
    • **سعر الهدف الملاحظ:** [يقرأ من المستوى المستهدف]
    • **الفرق الرياضي:** |السعر الحالي - سعر الهدف| = [X] نقطة

2.  **حساب السرعة من الصورة:**
    • **الشمعة الأخيرة:** تحركت [Y] نقطة
    • **متوسط آخر 3 شموع:** [Z] نقطة/شمعة
    • **المعادلة:** عدد الشموع = [X] ÷ [Z] = [N] شمعة

3.  **التحويل الزمني:**
    • فريم الشموع: {{candle}} = {{
        'S5': '5 ثواني', 'S10': '10 ثواني', 'S15': '15 ثواني',
        'S30': '30 ثانية', 'M1': '60 ثانية', 'M5': '300 ثانية'
    }}.get('{{candle}}', 'غير معروف')
    
    • **الوقت الإجمالي:** [N] × [مدة الشمعة] = [الوقت بالثواني]

4.  **التأكيد المزدوج:**
    • جميع الحسابات يجب أن تكون مكتوبة
    • يجب أن تتطابق النتائج مع التقدير البصري
    • في حالة التناقض، الأفضلية للحساب الرياضي

📊 المرحلة 1 - استخراج البيانات من الصورة:
1. ابحث عن ساعة المنصة في الشارت (عادة في الزاوية اليسرى/اليمنى السفلى)
2. سجل الوقت الحالي الذي تراه على الشارت: [ساعة:دقيقة:ثانية]
3. قدّر المسافة البصرية بين السعر الحالي والهدف (بعدد النقاط)
4. انظر إلى آخر 3 شموع واحسب متوسط حركتها (نقاط/شمعة)
5. عدد الشموع المطلوبة = المسافة ÷ متوسط حركة الشمعة

📋 البيانات المطلوبة من الصورة:
• الوقت على الشارت: [مثال: 14:25:30]
• المسافة للهدف: [عدد النقاط] نقطة
• متوسط حركة الشموع: [نقاط/شمعة]
• الشموع المتوقعة: [النتيجة] شمعة


⏰ **مطلوب منك إضافة قسم "التوقيت الذكي" في تحليلك:**

**🕐 التحليل الزمني المتقدم:**
• **الوقت على المنصة:** [أدخل الوقت من الصورة] ← **ركز على استخراج هذا من الصورة**
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
"بناءً على سرعة الشموع في فريم **{{candle}}** واستراتيجية **{{trading_strategy}}**، السعر يحتاج **[عدد الشموع]** شمعة (≈[الزمن]) للوصول للهدف. التوقيت الأمثل للوصول: **[وقت الوصول]** - إبدأ العد التنازلي الآن!"

✅ **بروتوكول التأكيد النهائي (Final Confirmation Protocol):**
قبل إصدار أي قرار، قم بالفحص النهائي المتكامل:

1.  **التحقق من تمرير المتغيرات البرمجية:** ✓ (جميع المتغيرات مستلمة)
2.  **التحقق من المسافة الحقيقية (<5 نقاط → احتفاظ) مع فحص استثناء اللمس الحتمي:** ✓
3.  **التحقق من فلتر السبريد (المسافة الصافية > 3 نقاط) مع فحص استثناء اللمس الحتمي:** ✓
4.  **التحقق من حالة الخبر (لا تذبذب عنيف >300%):** ✓
5.  **التحقق من هرم الأولويات V3 (3 شموع >85% → إلغاء الفتيلة، أولوية السيولة الخارجية):** ✓
6.  **التحقق من أسباب المنع (7 أسباب) مع فحص الاستثناءات (اللمس الحتمي، Marubozu Supremacy، ثواني الغدر):** ✓
7.  **التحقق من الحسابات الرياضية (تطابق بصري/رياضي):** ✓
8.  **التحقق من شروط التأكيد (الابتلاع 50% / Marubozu Supremacy):** ✓
9.  **التحقق من فلتر الإنهاك (لا ذروة):** ✓
10. **التحقق من استثناءات الزخم (ثواني الغدر للفريمات S5/S10 مع زخم >90%):** ✓

🎯 **القاعدة الذهبية:**
"إذا كان هناك شك ولو بسيط (أقل من 90% ثقة)، أو إذا فشل أي من الفلاتر ولم يتوفر استثناء واضح، القرار الإلزامي هو (احتفاظ 🟡) مع ذكر سبب الرفض بوضوح. السلامة أولاً قبل الربحية."

**🔍 توجيه نهائي: التركيز على استخراج البيانات من الصورة فقط**
تأكد من أن كل البيانات التي تستخدمها في التحليل مستخرجة من الصورة المرفقة فقط. لا تفترض أي أرقام أو أوقات. كل ما تحتاجه موجود في الصورة:

1.  **ركز على الأسعار:** اقرأها من المحور اليميني بدقة
2.  **ركز على الزمن:** ابحث عن ساعة المنصة واقرأ الوقت
3.  **ركز على الشموع:** حلل كل شمعة بشكل منفصل
4.  **ركز على المستويات:** حدد الدعم/المقاومة من حركة السعر
5.  **تطبيق هرم الأولويات V3:** **الزخم (>85% ماروبوزو)** ← **السيولة الخارجية (الهدف أولاً)** ← الفجوات ← الفتيلة ← الهيكل
6.  **تطبيق فلتر المسافة:** إذا كانت المسافة <5 نقاط → احتفاظ فوري **(إلا مع استثناء اللمس الحتمي)**
7.  **تطبيق فلتر السبريد:** إذا كان الهدف <5 نقاط على فريم صغير → احتفاظ **(إلا مع استثناء اللمس الحتمي)**
8.  **تطبيق فلتر الأخبار:** إذا كانت شمعة >300% من المتوسط → حظر 3 شموع
9.  **تطبيق شرط التأكيد:** لا دخول عكسي بدون ابتلاع 50% **(مع استثناء Marubozu Supremacy)**
10. **تطبيق استثناءات الزخم:** 
    • **اللمس الحتمي:** زخم >85% + رقم مستدير = إلغاء فلتر 5 نقاط وفلتر السبريد
    • **ثواني الغدر:** فريم S5/S10 + زخم >90% = تقليص الحظر إلى ثانيتين فقط والسماح بالدخول
    • **سيادة الجسم:** جسم >90% + ذيل <20% = إلغاء إشارات الانعكاس (جس نبض)
11. **فحص أسباب المنع:** ابحث عن سبب واحد يمنع الصفقة قبل إعطاء أي إشارة
12. **الوصف البصري أولاً:** ابدأ بوصف الشموع الثلاث الأخيرة قبل أي تحليل
13. **الحسابات الرياضية:** اكتب المعادلات بدلاً من الاعتماد على التخمين البصري
14. **كشف الإنهاك:** احذر الشموع الضخمة بالقرب من الأهداف

**📊 المعطيات الفنية:**
• إطار الزمن الحالي: {{candle}} ({{candle_category}})
• فريم التحقق: {{verification_timeframe}} (مخصص للتحقق من كسر الهيكل)
• استراتيجية التداول: {{trading_strategy}}
• جلسة السوق: {{session_name}} ({{session_time}})
• حالة السيولة: {{session_vol}}
• تأثير الأخبار: {{news_impact}} (معامل ×{{news_risk_multiplier}})
• **حالة دقيقة الغدر:** {{last_minute_status}} **(مع استثناءات فريمات S5/S10: تخفيض الحظر إلى ثانيتين إذا زخم >90%)**
• {{candle_closing_status}}
• الوقت المتبقي للإغلاق: {{seconds_remaining}} ثانية
• توقيت التحليل: {{gaza_time.strftime('%Y-%m-%d %H:%M:%S بتوقيت غزة')}}
• المستوى: Professional باك تيست 15000 صفقة

🎯 التنسيق المطلوب للإجابة (الالتزام حرفياً) وبشكل مختصرآ:

📊 التحليل الفني المتقدم:
• البصمة الزمنية: {{kill_zone_status}}
• تطبيق قانون الفتيلة: [نعم/لا] - نسبة الذيل: [٪] (يُلغى في حالة سيادة الجسم)
• حالة الهيكل: (صاعد/هابط) + (توافق 4/4 إطارات: نعم / لا )
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• الفجوات السعرية: (المناطق التي سيعود السعر لتغطيتها)

🎯 الإشارة التنفيذية:
• السعر الحالي: [ السعر الدقيق من الشارت ]
• حالة الشمعة: [مفتوحة / مغلقة] [ حمراء 🔴  / خضراء 🟢 ] - الوقت المتبقي: [ وقت دقيق من شارت ]
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
• وقت الذروة المتوقع: [مثلاً: خلال الـ 3 شموع القادمة]
• الحالة النفسية: [خوف 🥺 / جشع 🤑 / تردد 🤌 / استسلام 👎]
• علامات التلاعب: [موجودة ✔️ / غير موجودة ❎]

⚠️ إدارة المخاطر:
• مستوى الثقة: [0-100]٪ = [💥/🔥/⚡/❄️/🚫]
• نقطة الإلغاء: [السعر الذي يفسد التحليل]

**🎯 التعليمات النهائية للتركيز:**
1.  **استخرج كل البيانات من الصورة فقط**
2.  **لا تفترض أي معلومات غير موجودة في الصورة**
3.  **ركز على قراءة الأسعار من المحور اليميني بدقة**
4.  **ابحث عن ساعة المنصة واقرأ الوقت بدقة**
5.  **حلل كل شمعة بشكل منفصل من الصورة**
6.  **طبق هرم الأولويات الصارم V3: الزخم >85% (ماروبوزو) يلغي الفتيلة، والسيولة الخارجية تسبق الفجوة**
7.  **طبق فلتر المسافة الحقيقية: أقل من 5 نقاط = احتفاظ (إلا مع استثناء اللمس الحتمي)**
8.  **طبق فلتر السبريد: أهداف <5 نقاط على فريمات صغيرة = احتفاظ (إلا مع استثناء اللمس الحتمي)**
9.  **طبق فلتر الأخبار: شمعة >300% = حظر 3 شموع**
10. **طبق شرط التأكيد: لا دخول عكسي بدون ابتلاع 50% (إلا مع Marubozu Supremacy)**
11. **طبق استثناءات الزخم عند توفر شروطها: (اللمس الحتمي، ثواني الغدر، سيادة الجسم)**

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
        
        # --- الخطوة 2: التدقيق والتحسين الثاني ---
        await wait_msg.edit_text("استنى شويه اخر لمسات 🤌...")
        
        # برومبت التدقيق المحدث
        AUDIT_PROMPT = f"""
وظيفتك الأساسية هي البحث عن تناقض بين الأسعار المذكورة في التحليل الأول وبين الأرقام الظاهرة في الصورة. إذا وجد التحليل الأول سعراً مختلفاً بينما الصورة تظهر السعر عند مختلف، قم بتصحيح كافة الأهداف بناءً على أرقام الصورة حصراً.

مهمة: إجراء تدقيق عالي الدقة للأصل المالي

-خطوات التنفيذ :
- الهيكل الداخلي: حدد الهيكل السوقي (BOS / ChOCH / MSS) والمنطقة (Premium / Discount)
- خريطة السيولة: حدد السيولة الداخلية (Equal Highs/Lows) والخارجية (Stop Loss Clusters)، وابحث عن مسح السيولة قبل أي انعكاس
- كشف عدم الكفاءة: حدد أهم فجوة سعرية (FVG) أو فجوة انعكاس (Inversion FVG) وهل تعمل كمغناطيس أو دعم
- تدفق الأوامر: حدد كتلة الأوامر (Order Block) أو كتلة الكسر (Breaker Block) مع تأكيد حجم تداول عالي
- تدقيق الزخم: حلل حركة أجسام الشموع، وإذا اقترب السعر من رقم نفسي مستدير (.000, .500, .200, .800) أعط الأولوية لتأثير المغناطيس

-التوجيه النهائي:
- إذا كان الإعداد غامضاً: "NO CLEAR SETUP - STAND ASIDE"
- إذا كان الاحتمال عالياً: [الاتجاه]، [منطقة الدخول]، [المنطق]
- كن دقيقاً جداً، لا تخمن مستويات الأسعار
- قاعدة التكذيب: إذا ذكر التحليل الأول أن السعر عند (X) ولكنك ترى بوضوح بالعين أن الشمعة تلامس خطاً مختلفاً على المحور Y، اضرب بالتحليل الأول عرض الحائط واعتمد إحداثيات الصورة فقط.

أنت محلل فني خبير في SMC + ICT + Wyckoff + Volume Profile + Market Psychology. مهمتك: تحليل الشارت المرفق بدقة جراحية وإصدار توصيات تنفيذية متعددة الطبقات.

*التحليل الأولي:* {{initial_analysis}}

📊 القوانين الجديدة الإلزامية (نسخة شاملة):
1. قانون "سيادة الزخم على الهيكل" (Momentum Supremacy): إذا رصدت 3 شموع متتالية أو أكثر بنفس اللون، وبأجسام ممتلئة (Marubozu) تزيد مساحتها عن 80% من طولها الإجمالي، يُمنع منعاً باتاً إصدار إشارة انعكاس (Reverse). الزخم المؤسسي يعتبر "قطاراً سريعاً"؛ أي منطقة دعم أو مقاومة سيتم اختراقها كأنها غير موجودة. القرار الإلزامي: "اللحاق بالمغناطيس السعري" حتى يلمس السعر أقرب هدف سيولة خارجي.
2. قانون الفتيلة: ذيل >60% = انعكاس قوي.
3. قانون الأرقام المستديرة (Round Number Magnet): الأرقام المستديرة في الـ OTC هي "أهداف" وليست "عوائق". إذا كان السعر يتحرك بزخم عالٍ باتجاه رقم مستدير، تُلغى جميع إشارات الانعكاس قبل ملامسة الرقم فعلياً. الانتظار الإلزامي: لا تدخل عكس الاتجاه إلا بعد أن "يخترق" السعر الرقم المستدير ثم يعود للإغلاق خلفه بفتيلة واضحة (SFP).
4. قانون الفجوات: سعر → فجوة → فجوة → ارتداد.
5. ميزان القوى الزمني (Time-Based Volatility): في الدقائق الأولى من الساعة الجديدة أو عند إغلاقات الشموع الكبيرة، السعر يبحث عن "توازن جديد". إذا تعارضت إشارة فنية مع بداية ساعة جديدة، الأولوية دائماً لـ "شمعة الغدر" التي تمسح السيولة قبل تحديد الاتجاه الحقيقي.
6. قاعدة "السكين الساقط": أي هبوط حاد يتم استرداده فوراً بنسبة أقل من 50% من طول الموجة الهابطة يعتبر "تصحيحاً ضعيفاً" يتبعه انهيار أقوى. لا تقترح الشراء (أو البيع في الاتجاه الصاعد) إلا بعد رؤية "تغير في السلوك" (CHoCh) حقيقي.

📊 الخوارزميات المتقدمة والمكملة الإلزامية:
أ. "فخ القمة المزدوجة في الـ OTC": في الـ OTC، القمم المتساوية (Equal Highs) ليست مقاومات، بل هي "بنزين" للصعود. إذا رأيت قمتين متساويتين والسعر يقترب منهما بـ 3 شموع خضراء، احذر البيع. السعر سيقوم بـ 'Stop Hunt' لضرب الستوب لوز ثم يكمل صعوده أو يرتد لاحقاً. الدخول يكون مع الاختراق وليس ضده.
ب. "التدقيق البكسلي للمحور Y" (Precision Y-Axis): أنت ملزم بمطابقة ذيل الشمعة مع الرقم المقابل له على المسطرة اليمنى بدقة. إذا كان الذيل يلمس 0.18877، فلا تقرب الهدف لـ 0.18880؛ استخدم الرقم الفعلي لحساب الفجوة السعرية (FVG) بدقة.
ج. "السيولة المغناطيسية" (Magnet Liquidity): السعر يتحرك دائماً من "سيولة داخلية" (فجوة) إلى "سيولة خارجية" (قمة/قاع). عندما يغلق السعر فوق فجوة (FVG) هابطة، تصبح هذه الفجوة 'دفعاً' (Propulsion). الهدف القادم حتماً هو القمة السابقة (Old High). لا تقترح أي صفقة بيع طالما لم يتم لمس القمة الخارجية.
د. "إغلاق الثغرة الأمنية" (The Safety Gap Rule): إذا تم تغطية مسار هابط استغرق 10 دقائق بصعود استغرق دقيقتين فقط، فهذا ليس مجرد صعود، بل هو 'اندفاع مؤسسي' (Institutional Drive). في هذه الحالة، يُمنع البيع حتى لو ظهرت شمعة حمراء صغيرة، لأنها ستكون مجرد 'توقف مؤقت' (Rest Point) قبل انفجار ثانٍ.
ه. "فلتر الفتيلة الغادرة" (The Fake Wick Trap): لا تعتمد "الفتيلة" (Wick) كإشارة ارتداد إلا إذا كانت قد سحبت سيولة واضحة (Liquidity Sweep) من قمة أو قاع سابق. إذا ظهرت فتيلة طويلة ولكن الشمعة التالية أغلقت بجسم كامل تحت/فوق منتصف هذه الفتيلة، فهذا يسمى "Inversion"؛ أي أن الرفض فشل وتحول إلى منطقة دفع تكميلية للاتجاه السابق. القاعدة: "الجسم يبتلع الفتيلة = استمرار الانفجار السعري".
و. "وقت انتهاء الصلاحية" (The 5-Minute Fatigue): في الـ OTC، الزخم غالباً ما يتعب بعد 5 شموع متتالية من نفس اللون. عند عد 5 شموع خضراء ممتلئة متتالية، ابدأ بالبحث عن 'شمعة المنهك' (Exhaustion Candle) وهي شمعة كبيرة جداً بشكل مبالغ فيه (أكبر بـ 3 مرات من سابقاتها). هذه هي اللحظة الوحيدة التي يُسمح فيها بتوقع ارتداد قصير.
ز. "الاندفاع الانتحاري" (The Climax Rule): إذا زاد حجم جسم الشمعة الحالية عن 200% من متوسط حجم آخر 5 شموع وهي تقترب من مستوى سيولة، فهذا يسمى 'اندفاع الذروة'. لا تدخل عكسه أبداً حتى يكتمل المسح بالكامل (Full Sweep) وتظهر شمعة رفض واضحة.
ح. "التوقيت الصفري" (The Zero-Second Filter): يُحظر إصدار إشارة دخول في الثواني (00-05) من بداية الدقيقة الجديدة، أو الثواني (55-59) من نهاية الدقيقة، بسبب تذبذب الخوارزمية لتعديل السعر عند هذه النقاط.
ط. "الفجوة المكسورة" (The Inversion Logic): أي منطقة عرض (Supply) يتم اختراقها بشمعة اندفاعية (Marubozu) تتحول فوراً إلى منطقة طلب (Demand). إذا عاد السعر ليلمسها، فالقرار هو شراء مع الاتجاه الجديد، وليس البيع منها مرة أخرى.
ي. "إعادة اختبار الفراغ" (FVG Rejection): عندما يعود السعر لفجوة (FVG) هابطة بشمعة اندفاعية خضراء ولا يحترمها (أي لا يرتد منها للأسفل)، فهذا يسمى Inversion FVG. بمجرد إغلاق شمعة دقيقة فوق منتصف الفجوة الهابطة، تتحول وظيفة هذه المنطقة من "مقاومة للبيع" إلى "منصة إطلاق للشراء".
ك. "تزامن الوقت مع الرقم المستدير" (Kill-Zone Timing): في الـ OTC، الدقائق التي تنتهي بـ (5، 10، 15، 30، 45) هي دقائق "تعديل المراكز". إذا كان السعر قريباً من رقم مستدير (مثل 0.18900) عند الدقيقة 44 أو 59، فإنه سيقوم بـ "قفزة انتحارية" للمس الرقم قبل إغلاق الشمعة الكبيرة.
ل. "ثبات الحجم البصري" (Visual Volume Stability): إذا كانت الشمعة الصاعدة تفتقر للذيل العلوي (Flat Top)، فهذا يعني أن ضغط الشراء لم ينتهِ بعد، ولا يوجد بائعون تجرأوا على الدخول. ممنوع البيع حتى تظهر أول شمعة بـ 'ذيل علوي' يمثل 30% من حجمها.
م. "قفل الأمان للزخم" (The Momentum Deadlock): إذا رصدت 3 شموع متتالية بنفس اللون بجسم يمثل >80% من حجم الشمعة، يُحظر تماماً إصدار إشارة عكسية. في هذه الحالة، تتحول المهمة من 'توقع الانعكاس' إلى 'تحديد أقرب هدف مغناطيسي (Liquidity/Round Number)' والمطالبة باللحاق بالاتجاه.
ن. "كشف كسر الحقيقة" (The Truth-Break Detection): عند حدوث هبوط ثم صعود مفاجئ (V-Shape)، قارن حجم شمعة الارتداد بآخر شموع الهبوط. إذا ابتلعت الشمعة الخضراء آخر شمعتين حمراوين، اعتبر الهبوط السابق 'Stop Hunt' (فخ سيولة) وألغِ جميع مستويات المقاومة القريبة؛ السعر الآن في وضع اندفاع مؤسسي.
س. "قاعدة الرقم المستدير كهدف" (Round Number Target Logic): في سوق الـ OTC، الأرقام المستديرة (مثل 0.18900) هي أهداف وليست مقاومات. لا تعطي إشارة ارتداد من الرقم المستدير إلا بعد أن يتم اختراقه بـ 'ذيل' (Wick) ثم العودة للإغلاق دونه. قبل ذلك، الهدف هو لمس الرقم.
ع. قواعد المعايرة الذاتية (Self-Calibration):
 1. تفضيل الزخم: في حال التعارض بين 'منطقة عرض' (SMC) و 'اندفاع قوي' (OTC Momentum)، يفوز الزخم دائماً. لا تقترح البيع إلا بعد ظهور CHoCH واضح على فريم أصغر.
 2. قاعدة الـ V-Shape: أي هبوط يتم استرداده فوراً بنسبة >100% يعتبر فخاً. الهدف القادم هو القمة التي تليها مباشرة.
 3. ميزان القوى: قارن حجم الشموع الخضراء بالحمراء في آخر 10 دقائق؛ اللون المسيطر حجماً هو الذي يحدد اتجاه صفقاتك حتى لو كان الهيكل عكس ذلك.

📊 المستوى 1: التحليل الاستراتيجي
• الهيكل العام: موجات إيليوت + BOS/CHoCh
• مرحلة Wyckoff: Accumulation/Markup/Distribution/Decline
• السياق الزمني: توافق {{verification_timeframe}} مع {{candle}}
• السوق: {{session_name}} - {{session_vol}} سيولة

⚡ المستوى 2: التحليل التكتيكي
• الشموع: 5 شموع سابقة + الحالية
• Order Blocks: آخر 3 مناطق طلب/عرض
• FVG Tracking: فجوات غير مغطاة ≤50 نقطة
• Liquidity Map: Equal Highs/Lows + Inducement

🎯 المستوى 3: التحليل التنفيذي
• Entry Triggers: شمعة إغلاق + حجم
• Risk Matrix: RR ديناميكي × {{news_risk_multiplier}}
• Position Sizing: {{position_sizing}}
• Timing Precision: دخول/خروج بالثواني

🎯 النظام الزمني الذكي - حساب وقت الوصول:
*🔧 إعدادات النظام الحالية:*
• إطار الشموع: {{candle}} (تم ضبطه من قبل المستخدم)
• استراتيجية التداول: {{trading_strategy}}
• جلسة السوق: {{session_name}} ({{session_time}})
• وقت التحليل الفعلي: {{gaza_time.strftime('%H:%M:%S بتوقيت غزة')}}

📊 المرحلة 1 - استخراج البيانات من الصورة:
1. ابحث عن ساعة المنصة في الشارت (عادة في الزاوية اليسرى/اليمنى السفلى)
2. سجل الوقت الحالي الذي تراه على الشارت: [ساعة:دقيقة:ثانية]
3. قدّر المسافة البصرية بين السعر الحالي والهدف (بعدد النقاط)
4. انظر إلى آخر 3 شموع واحسب متوسط حركتها (نقاط/شمعة)
5. عدد الشموع المطلوبة = المسافة ÷ متوسط حركة الشمعة

📋 البيانات المطلوبة من الصورة:
• الوقت على الشارت: [مثال: 14:25:30]
• المسافة للهدف: [عدد النقاط] نقطة
• متوسط حركة الشموع: [نقاط/شمعة]
• الشموع المتوقعة: [النتيجة] شمعة

🧮 المرحلة 2 - الحساب الذكي (مع ربط الفريم):
1. فريم الشموع: {{candle}}
2. مدة كل شمعة: {{
 'S5': '5 ثواني',
 'S10': '10 ثواني',
 'S15': '15 ثانية',
 'S30': '30 ثانية',
 'M1': '1 دقيقة',
 'M2': '2 دقيقة',
 'M3': '3 دقائق',
 'M5': '5 دقائق',
 'M10': '10 دقائق',
 'M15': '15 دقيقة',
 'M30': '30 دقيقة',
 'H1': '1 ساعة',
 'H4': '4 ساعات',
 'D1': '1 يوم'
}}.get('{{candle}}', '{{candle}}')

3. المعادلة:
 وقت الوصول = الوقت من الشارت + (عدد الشموع × مدة الشمعة)

4. تطبيق إعدادات المستخدم:
 - الفريم: {{candle}}
 - الإستراتيجية: {{trading_strategy}}
 - حجم الصفقة: {{position_sizing}}

⏰ مطلوب منك إضافة قسم "التوقيت الذكي" في تحليلك:

🕐 التحليل الزمني المتقدم:
• الوقت على المنصة: [أدخل الوقت من الصورة]
• الفريم المستخدم: {{candle}}
• المسافة المقدرة: [X] نقطة
• سرعة الشموع: [Y] نقطة/شمعة
• الشموع المتوقعة: [Z] شمعة من نوع {{candle}}

🎯 نتائج التوقيت:
• وقت البداية (من الشارت): [الوقت]
• مدة الشمعة الواحدة: [تحويل {{candle}} إلى زمن]
• وقت الوصول المتوقع: [ساعة:دقيقة:ثانية بضبط]
• المدة الإجمالية: [تحويل إلى دقائق/ثواني]

🚀 التوصية الزمنية العملية:
"بناءً على سرعة الشموع في فريم {{candle}} واستراتيجية {{trading_strategy}}، السعر يحتاج [عدد الشموع] شمعة (≈[الزمن]) للوصول للهدف. التوقيت الأمثل للوصول: [وقت الوصول] - إبدأ العد التنازلي الآن!"

💡 ملاحظة هامة:
تأكد من أن الوقت الذي تستخرجه من الصورة يتوافق مع توقيت {{gaza_time.strftime('%H:%M:%S بتوقيت غزة')}}. إذا كان هناك فارق زمني، قم بضبط الحساب وفقاً لذلك.

🛡️ طبقات الحماية (فلاتر الحماية المتقدمة)
1. فلتر الأخبار: {{news_warning if news_warning else "✅ آمن"}}
2. Kill Zone: {{kill_zone_status}}
3. فلتر دقيقة الغدر: تم تقليص فترة الحظر في الفريمات الصغيرة جداً (S5/S10) لتصبح ثانيتين فقط بدلاً من 10 ثوانٍ، بشرط أن يكون الزخم > 90%. هذا يسمح باقتناص الانفجار السعري الأخير.
4. قاعدة اللمس الحتمي (إلغاء شرط المسافة): إذا كان السعر يتجه لرقم مستدير (Round Number) بزخم عالٍ، يتم إلغاء شرط المسافة الدنيا (5 نقاط) ويُسمح بالدخول فوراً.
5. حاجز السيولة: لا دخول عكس FVG غير مغطاة
6. أرقام مستديرة: منع دخول ±5 نقاط بدون CHoCh (يُلغى هذا الشرط تلقائياً في حال تفعيل "قاعدة اللمس الحتمي")
7. توقيت إغلاق الشمعة: {{candle_closing_status}}

⚡ قواعد سريعة
• REJECTION ENTRY: ذيل طويل + إغلاق داخل النطاق
• MOMENTUM FOLLOW: 3 شموع قوية → استمرار الاتجاه (قانون سيادة الجسم: إذا كان جسم الشمعة يمثل >80% من حجمها، يتم تجاهل أي ذيل علوي/سفلي صغير ولا يُعتبر إشارة ضعف)
• GAP FILLING: تحرك من فجوة إلى فجوة قبل الارتداد
• LAST MINUTE RULE: تجاهل انعكاسات الدقيقة 29/59/14/44 (مع استثناء الفريمات الصغيرة S5/S10 بزخم >90%)

🔍 أمر التدقيق وهرم الأولويات البرمجي:
1. تحقق من كل سعر ومستوى مذكور في التحليل مع الصورة بدقة بكسلية
2. تطبيق هرم الأولويات الإلزامي: (الزخم أولاً) ← (السيولة) ← (الفجوات السعرية) ← (الهيكل الفني). إذا كان الزخم صاعداً بقوة، يُمنع البوت من إصدار إشارة "بيع" حتى لو لمس المقاومة.
3. تأكد من تطبيق جميع القواعد التالية:
 - فلتر المسافة الذهبية: إذا كان السعر قريب من رقم مستدير (<0.00010) وبزخم عالٍ، فالقرار يجب أن يكون متابعة الزخم فوراً (إلغاء شرط المسافة)
 - فلتر الأرقام المستديرة: فريم التحقق = {{verification_timeframe}}
 - قاعدة المغناطيس للفجوات
 - ميزان القوى (الهيكل vs الزخم) - الزخم يفوز دائماً
 - قاعدة التنبيه من الفجوات السعرية
4. تحقق من صحة:
 • الأسعار الدقيقة من المحور اليمني
 • مناطق FVG والحاجة لتغطيتها
 • تطابق الهيكل مع مرحلة وايكوف
 • صحة قوة الإشارة بناءً على النقاط المحسوبة
5. صحح أي أخطاء في:
 • تحديد السعر الحالي
 • نقاط الدخول والخروج
 • مستويات الثقة
 • مدة الصفقة المتوقعة

*تذكر:* يجب أن يكون تدقيقك موضوعياً ويعتمد على الصورة فقط. لا تخترع أسعاراً أو مستويات غير موجودة.

📊 المعطيات الفنية:
• الإطار الحالي: {{candle}} ({{candle_category}})
• فريم التحقق: {{verification_timeframe}}
• استراتيجية التداول: {{trading_strategy}}
• جلسة السوق: {{session_name}} ({{session_time}}), السيولة: {{session_vol}}
• تأثير الأخبار: {{news_impact}} ×{{news_risk_multiplier}}
• حالة دقيقة الغدر: {{last_minute_status}} (مقيدة: ثانيتين فقط للفريمات S5/S10 بزخم >90%)
• توقيت إغلاق الشمعة: {{candle_closing_status}}
• توقيت التحليل: {{gaza_time.strftime('%Y-%m-%d %H:%M:%S بتوقيت غزة')}}
• المستوى: Professional باك تيست 15000 صفقة

🎯 التنسيق المطلوب للإجابة (الالتزام حرفياً) وبشكل مختصرآ:

📊 التحليل الفني المتقدم:
• البصمة الزمنية: {{kill_zone_status}}
• تطبيق قانون الفتيلة: [نعم/لا] - نسبة الذيل: [٪] (يُلغى في حالة سيادة الجسم)
• حالة الهيكل: (صاعد/هابط) + (توافق 4/4 إطارات: نعم / لا )
• خريطة السيولة: (أقرب فخ سيولة Inducement + مناطق السيولة المستهدفة)
• الفجوات السعرية: (المناطق التي سيعود السعر لتغطيتها)

🎯 الإشارة التنفيذية:
• مقارنة مع التحليل السابق: [✅ مطابق تماماً / ⚡ محسن / ❌ مصحح]، درجة التشابه: [0–100]%
• السعر الحالي: [ السعر الدقيق من الشارت ]
• حالة الشمعة: [مفتوحة / مغلقة] [ حمراء 🔴  / خضراء 🟢 ] - الوقت المتبقي: [ وقت دقيق من شارت ]
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
• وقت الذروة المتوقع: [مثلاً: خلال الـ 3 شموع القادمة]
• الحالة النفسية: [خوف 🥺 / جشع 🤑 / تردد 🤌 / استسلام 👎]
• علامات التلاعب: [موجودة ✔️ / غير موجودة ❎]

⚠️ إدارة المخاطر:
• مستوى الثقة: [0-100]٪ = [💥/🔥/⚡/❄️/🚫]
• نقطة الإلغاء: [السعر الذي يفسد التحليل]

الآن قم بتحليل الشارت المرفق وأعطني الإجابة بالتنسيق المطلوب أعلاه فقط، بدون أي نص إضافي أو تفسيرات خارج الهيكل.
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
            "max_tokens": 1000,
            "temperature": 0.0,
            "top_p": 1.0,
            "random_seed": 42
        }
        
        response_2 = requests.post(MISTRAL_URL, headers=headers, json=payload_2, timeout=45)
        
        if response_2.status_code == 200:
            audit_result = response_2.json()['choices'][0]['message']['content'].strip()
        else:
            print(f"Obeida Vision Warning (Model 2): {response_2.status_code} - استخدام التحليل الأول")
            audit_result = f"📋 **ملاحظة:** تعذر التدقيق - استخدام التحليل الأولي مباشرة\n\n{initial_analysis}"
        
        # تنظيف النصوص من التكرار
        audit_result = clean_repeated_text(audit_result)
        
        # حفظ سياق التحليل في قاعدة البيانات
        save_analysis_context(user_id, audit_result)
        
        keyboard = [["📊 تحليل صورة"], ["⚙️ إعدادات التحليل"], ["📈 توصية"], ["الرجوع للقائمة الرئيسية"]]
        if user_id == ADMIN_ID:
            keyboard.append(["👑 لوحة التحكم"])
        
        # تنسيق وقت الصفقة للعرض
        time_display = format_trade_time_for_prompt(trade_time)
        
        # إعداد النص النهائي
        full_result = (
            f"✅ **تم التحليل  صفقتك بنجاح** ✅\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{audit_result}\n\n"
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
        ["👥 عرض المستخدمين", "➕ إضافة مستخدم"],
        ["⏱️ تمديد اشتراك", "🗑️ حذف مستخدم"],
        ["📊 إحصائيات", "🔙 العودة للقائمة الرئيسية"]
    ]
    
    await update.message.reply_text(
        "👑 **لوحة تحكم المشرف** 👑\n\n"
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
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, username, subscription_start, subscription_end, is_admin 
            FROM users ORDER BY subscription_end DESC
        """)
        users = cursor.fetchall()
        conn.close()
        
        if not users:
            await update.message.reply_text("📭 لا يوجد مستخدمين بعد.")
        else:
            msg = "👥 **قائمة المستخدمين:**\n\n"
            for u in users:
                uid, uname, sstart, send, is_admin = u
                status = "👑 مشرف" if is_admin == 1 else "👤 مستخدم"
                sub_info = f"📅 الاشتراك: {send[:16] if send else 'غير محدد'}"
                msg += f"• {uname or uid} - {status}\n  {sub_info}\n\n"
            
            for part in split_message(msg, 4000):
                await update.message.reply_text(part, parse_mode="Markdown")
        
        return ADMIN_MENU
    
    elif user_message == "➕ إضافة مستخدم":
        await update.message.reply_text(
            "➕ **إضافة مستخدم جديد**\n\n"
            "أرسل اسم المستخدم (بدون @) والمدة بالثواني:\n"
            "مثال: `Sz2zv 86400` (يوم واحد)\n\n"
            "المدد المتاحة:\n"
            "• ساعة: 3600\n"
            "• يوم: 86400\n"
            "• أسبوع: 604800\n"
            "• شهر: 2592000\n"
            "• غير محدد: 0",
            parse_mode="Markdown"
        )
        return ADD_USER
    
    elif user_message == "⏱️ تمديد اشتراك":
        await update.message.reply_text(
            "⏱️ **تمديد اشتراك مستخدم**\n\n"
            "أرسل معرف المستخدم والمدة الجديدة:\n"
            "مثال: `123456789 86400`",
            parse_mode="Markdown"
        )
        return SET_USER_DURATION
    
    elif user_message == "🗑️ حذف مستخدم":
        await update.message.reply_text(
            "🗑️ **حذف مستخدم**\n\n"
            "أرسل معرف المستخدم للحذف:\n"
            "مثال: `123456789`",
            parse_mode="Markdown"
        )
        return REMOVE_USER
    
    elif user_message == "📊 إحصائيات":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_end > datetime('now') OR subscription_end IS NULL")
        active_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_end < datetime('now') AND subscription_end IS NOT NULL")
        expired_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM chat_history WHERE date(timestamp) = date('now')")
        today_analyses = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1")
        admins = cursor.fetchone()[0]
        
        conn.close()
        
        stats = (
            "📊 **إحصائيات النظام:**\n\n"
            f"👥 إجمالي المستخدمين: {total_users}\n"
            f"✅ المستخدمين النشطين: {active_users}\n"
            f"❌ المنتهية اشتراكاتهم: {expired_users}\n"
            f"👑 عدد المشرفين: {admins}\n"
            f"📊 تحليلات اليوم: {today_analyses}\n"
        )
        
        await update.message.reply_text(stats, parse_mode="Markdown")
        return ADMIN_MENU
    
    return ADMIN_MENU

async def handle_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إضافة مستخدم جديد"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    try:
        parts = update.message.text.strip().split()
        if len(parts) != 2:
            await update.message.reply_text(
                "❌ صيغة خاطئة. استخدم: `username 86400`\n"
                "مثال: `Sz2zv 86400`",
                parse_mode="Markdown"
            )
            return ADD_USER
        
        username = parts[0].replace('@', '')
        duration = int(parts[1])
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if duration > 0:
            end_date = datetime.now() + timedelta(seconds=duration)
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, username, subscription_start, subscription_end, candle, trade_time)
                VALUES (?, ?, datetime('now'), ?, ?, ?)
            """, (0, username, end_date.isoformat(), "M1", "قصير (1m-15m)"))
        else:
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, username, subscription_start, subscription_end, candle, trade_time)
                VALUES (?, ?, datetime('now'), NULL, ?, ?)
            """, (0, username, "M1", "قصير (1m-15m)"))
        
        conn.commit()
        conn.close()
        
        duration_text = "غير محدد" if duration == 0 else f"{duration//3600} ساعة" if duration < 86400 else f"{duration//86400} يوم"
        await update.message.reply_text(
            f"✅ تم إضافة المستخدم @{username}\n"
            f"📅 مدة الاشتراك: {duration_text}\n\n"
            f"ملاحظة: المستخدم سيتم تفعيله بعد أول تفاعل مع البوت."
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)}")
    
    return ADMIN_MENU

async def handle_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة حذف مستخدم"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ ليس لديك صلاحية.")
        return MAIN_MENU
    
    try:
        target_user_id = int(update.message.text.strip())
        
        if target_user_id == ADMIN_ID:
            await update.message.reply_text("❌ لا يمكن حذف المشرف الرئيسي.")
            return ADMIN_MENU
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (target_user_id,))
        cursor.execute("DELETE FROM users WHERE user_id = ?", (target_user_id,))
        
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ تم حذف المستخدم {target_user_id} بنجاح.")
        
    except ValueError:
        await update.message.reply_text("❌ الرجاء إرسال معرف المستخدم (أرقام فقط).")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {str(e)}")
    
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
        duration = int(parts[1])
        
        if duration > 0:
            end_date = datetime.now() + timedelta(seconds=duration)
            
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users 
                SET subscription_end = ?
                WHERE user_id = ?
            """, (end_date.isoformat(), target_user_id))
            conn.commit()
            conn.close()
            
            duration_text = f"{duration//3600} ساعة" if duration < 86400 else f"{duration//86400} يوم"
            await update.message.reply_text(
                f"✅ تم تحديث اشتراك المستخدم {target_user_id}\n"
                f"📅 المدة الجديدة: {duration_text}\n"
                f"📆 ينتهي في: {end_date.strftime('%Y-%m-%d %H:%M')}"
            )
        else:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users 
                SET subscription_end = NULL
                WHERE user_id = ?
            """, (target_user_id,))
            conn.commit()
            conn.close()
            
            await update.message.reply_text(f"✅ تم تحويل المستخدم {target_user_id} إلى اشتراك غير محدد.")
        
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
            await asyncio.sleep(1800)
            cleanup_old_images()
            cleanup_old_database_records()
            print("🧹 تم التنظيف الدوري للملفات المؤقتة")
            
        except Exception as e:
            print(f"⚠️ خطأ في التنظيف الدوري: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت - مع إخفاء الأزرار لغير المشتركين"""
    user = update.effective_user
    user_id = user.id
    username = user.username or str(user_id)
    
    # تسجيل المستخدم في قاعدة البيانات إذا لم يكن موجوداً
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # التحقق من وجود المستخدم
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        # إضافة المستخدم مع اشتراك غير محدد (يحتاج تفعيل من المشرف)
        cursor.execute("""
            INSERT INTO users (user_id, username, candle, trade_time, subscription_start)
            VALUES (?, ?, ?, ?, datetime('now'))
        """, (user_id, username, "M1", "قصير (1m-15m)"))
        conn.commit()
    
    conn.close()
    
    # تنظيف الذاكرة المؤقتة
    cleanup_user_data(context, user_id)
    
    # التحقق من صلاحية المشرف والاشتراك
    is_admin = (user_id == ADMIN_ID)
    status, message = check_subscription(user_id)
    
    if is_admin:
        # المشرف - يرى جميع الأزرار
        keyboard = [
            ["⚙️ إعدادات التحليل", "📊 تحليل صورة"],
            ["💬 دردشة", "📈 توصية"],
            ["👑 لوحة التحكم"]
        ]
        
        welcome_msg = (
            "🚀 *أهلاً بك في Obeida Trading*\n\n"
            "👑 أنت مسؤول النظام - لديك صلاحيات كاملة.\n\n"
            "🤖 *الخدمات المتاحة:*\n"
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
        
        welcome_msg = (
            f"🚀 *أهلاً بك في Obeida Trading*\n\n"
            f"✅ حالة اشتراكك: {message}\n\n"
            f"🤖 *الخدمات المتاحة:*\n"
            f"• 📊 تحليل الشارتات\n"
            f"• 📈 توصيات فورية\n"
            f"• 💬 دردشة ذكية\n"
            f"• ⚙️ إعدادات متقدمة\n\n"
            f"اختر أحد الخيارات:"
        )
        
    else:
        # مستخدم غير مشترك - لا يرى أي أزرار، فقط زر التواصل
        keyboard = []  # لا أزرار
        inline_keyboard = [[InlineKeyboardButton("📞 التواصل مع المشرف", url="https://t.me/Sz2zv")]]
        reply_markup = InlineKeyboardMarkup(inline_keyboard)
        
        await update.message.reply_text(
            f"⚠️ **عذراً، ليس لديك صلاحية استخدام البوت**\n\n"
            f"❌ الحالة: {message}\n\n"
            f"📞 للاشتراك والتجديد، تواصل مع المشرف:\n"
            f"👤 @Sz2zv\n\n"
            f"بعد الاشتراك، أرسل /start مرة أخرى",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return MAIN_MENU
    
    # إظهار القائمة للمستخدمين المشتركين فقط
    await update.message.reply_text(
        welcome_msg,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False),
        parse_mode="Markdown"
    )
    return MAIN_MENU

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيارات القائمة الرئيسية"""
    user_message = update.message.text
    user_id = update.effective_user.id
    
    # التحقق من الاشتراك (لغير المشرف)
    if user_id != ADMIN_ID:
        status, _ = check_subscription(user_id)
        if not status:
            # إخفاء الأزرار فوراً
            inline_keyboard = [[InlineKeyboardButton("📞 التواصل مع المشرف", url="https://t.me/Sz2zv")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard)
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك. تواصل مع المشرف للتجديد.",
                reply_markup=reply_markup
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
        status, _ = check_subscription(user_id)
        if not status:
            inline_keyboard = [[InlineKeyboardButton("📞 التواصل مع المشرف", url="https://t.me/Sz2zv")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard)
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك. تواصل مع المشرف للتجديد.",
                reply_markup=reply_markup
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
        status, _ = check_subscription(user_id)
        if not status:
            inline_keyboard = [[InlineKeyboardButton("📞 التواصل مع المشرف", url="https://t.me/Sz2zv")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard)
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك. تواصل مع المشرف للتجديد.",
                reply_markup=reply_markup
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
        status, _ = check_subscription(user_id)
        if not status:
            inline_keyboard = [[InlineKeyboardButton("📞 التواصل مع المشرف", url="https://t.me/Sz2zv")]]
            reply_markup = InlineKeyboardMarkup(inline_keyboard)
            await update.message.reply_text(
                "⚠️ انتهت صلاحية اشتراكك. تواصل مع المشرف للتجديد.",
                reply_markup=reply_markup
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

async def handle_photo_in_analyze_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الصور في وضع التحليل"""
    return await handle_photo_analysis(update, context)

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
    • لوحة تحكم كاملة عبر الرابط /admin
    • إدارة المستخدمين والاشتراكات
    
    📡 **مميزات البوت المتقدمة:**
    • 📊 تحليل فني للرسوم البيانية بتقنيات متطورة
    • 💬 دردشة ذكية متعددة التخصصات
    • 📈 نظام توصيات الجاهزة 
    • ⚙️ حفظ إعداداتك الشخصية
    • 🔹 واجهة سهلة بالأزرار
    • 🗑️ نظام تنظيف تلقائي للذاكرة
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

def main():
    """الدالة الرئيسية - النسخة النهائية مع نظام الاشتراكات"""
    print("🤖 Starting Obeida Trading with Subscription System ...")
    print("📋 تم تفعيل نظام النسخ السريع - جميع التحليلات قابلة للنسخ بنقرة واحدة")
    print("👑 المشرف الرئيسي: 6207431030 (@Sz2zv)")
    print("🔒 نظام إخفاء الأزرار لغير المشتركين - مفعل")
    
    # تشغيل Flask
    flask_thread = threading.Thread(target=run_flask_server, daemon=True)
    flask_thread.start()
    
    print(f"🌐 Flask server started on port {os.environ.get('PORT', 8080)}")
    print(f"📊 لوحة تحكم المشرف: http://localhost:{os.environ.get('PORT', 8080)}/admin")
    
    # تهيئة قاعدة البيانات مع نظام الاشتراكات
    init_db_with_subscription()
    
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
            ADD_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_user)
            ],
            REMOVE_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_user)
            ],
            SET_USER_DURATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_set_duration)
            ],
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('cancel', cancel)],
        allow_reentry=True
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    
    # إضافة معالج للأخطاء
    application.add_error_handler(error_handler)
    
    print("✅ Telegram Bot with Subscription System ready ...")
    print("📡 Bot Obeida Trading online with admin panel...")
    
    # تشغيل البوت
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
    
