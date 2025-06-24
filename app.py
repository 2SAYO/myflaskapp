
from flask import Flask, render_template_string, request, redirect, url_for, session, jsonify, flash
import sqlite3
import secrets
import hashlib
import uuid
import os
import shutil
import threading
import time
import subprocess
import re
import psutil
import json
from datetime import datetime, timedelta
import requests
import atexit
import re

# إعدادات التطبيق
app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
app.permanent_session_lifetime = timedelta(days=30)

# المتغيرات الشاملة
DATABASE_PATH = 'sayohosting.db'
UPLOAD_FOLDER = 'uploaded_files'
ADMIN_ID = 'admin123'

# متغيرات التتبع العامة
active_projects = {}
process_map = {}
user_id_cache = {}
active_timers = {}
sandbox_installed_packages = {}
subscription_cache = set()
last_subscription_check = 0
project_outputs = {}
tunnel_process = None

# وظائف مساعدة
def cleanup_directories():
    """تنظيف وإعادة إنشاء المجلدات"""
    directories = ['uploaded_files', 'pending_files_temp', 'sandbox_environments']
    for directory in directories:
        if os.path.exists(directory):
            shutil.rmtree(directory)
        os.makedirs(directory, exist_ok=True)

def generate_user_id():
    """توليد معرف مستخدم فريد"""
    import random
    import string

    numbers = [str(random.randint(0, 9)) for _ in range(11)]
    letters = [random.choice(string.ascii_uppercase) for _ in range(4)]
    combined = numbers + letters
    random.shuffle(combined)
    return ''.join(combined)

def check_subscription_status():
    """فحص حالة الاشتراك من GitHub"""
    global subscription_cache, last_subscription_check

    current_time = time.time()
    if current_time - last_subscription_check < 300:
        return

    try:
        response = requests.get('https://raw.githubusercontent.com/2SAYO/Ids/refs/heads/main/ids.txt', timeout=10)
        if response.status_code == 200:
            subscription_cache = set(response.text.strip().split('\n'))
        last_subscription_check = current_time
    except:
        pass

def is_user_subscribed(user_id):
    """التحقق من اشتراك المستخدم"""
    check_subscription_status()
    return user_id in subscription_cache

def scan_for_malicious_code(file_path):
    """فحص الكود للبحث عن أنماط مشبوهة"""
    malicious_patterns = [
        'os.system', 'subprocess.run', 'eval', 'shutil.rmtree',
        'os.remove', 'socket.socket', '__import__', 'getattr',
        'setattr', 'delattr', 'open.*w', 'requests.post.*os.walk'
    ]

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        for pattern in malicious_patterns:
            if re.search(pattern, content):
                return True, f"تم اكتشاف نمط مشبوه: {pattern}"

        return False, "الملف آمن"
    except:
        return True, "خطأ في قراءة الملف"

def get_db_connection():
    """إنشاء اتصال بقاعدة البيانات"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def dict_from_row(row):
    """تحويل sqlite3.Row إلى قاموس"""
    return dict(row) if row else None

def allowed_file(filename):
    """التحقق من امتداد الملف المسموح"""
    allowed_extensions = {'py', 'txt', 'zip', 'html', 'css', 'js', 'json', 'md'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

def hash_password(password):
    """تجزئة كلمة المرور"""
    return hashlib.sha256(password.encode()).hexdigest()

def get_server_status():
    """الحصول على حالة الخادم"""
    try:
        cpu_usage = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot_time = psutil.boot_time()

        uptime_seconds = time.time() - boot_time
        uptime_days = int(uptime_seconds // 86400)
        uptime_hours = int((uptime_seconds % 86400) // 3600)

        return {
            'cpu_usage': round(cpu_usage, 1),
            'memory_usage': round(memory.percent, 1),
            'total_memory_gb': round(memory.total / 1024**3, 1),
            'disk_usage_percent': round(disk.percent, 1),
            'uptime_days': uptime_days,
            'uptime_hours': uptime_hours,
            'cpu_count': psutil.cpu_count(),
            'os_info': f"{psutil.os.name}",
            'ping_ms': 25
        }
    except:
        return {
            'cpu_usage': 0, 'memory_usage': 0, 'total_memory_gb': 0,
            'disk_usage_percent': 0, 'uptime_days': 0, 'uptime_hours': 0,
            'cpu_count': 0, 'os_info': 'غير متاح', 'ping_ms': 0
        }

def run_project_simple(project_id, script_path):
    """تشغيل المشروع بطريقة بسيطة"""
    try:
        project_dir = os.path.dirname(script_path)
        
        # تثبيت المتطلبات إذا وجدت
        requirements_path = os.path.join(project_dir, 'requirements.txt')
        if os.path.exists(requirements_path):
            try:
                subprocess.run(['pip', 'install', '-r', requirements_path], 
                             cwd=project_dir, check=False)
            except:
                pass

        # تشغيل الملف
        process = subprocess.Popen(
            ['python', os.path.basename(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=project_dir,
            text=True
        )

        active_projects[project_id] = {
            'process': process,
            'start_time': time.time(),
            'status': 'running'
        }

        # تهيئة مخرجات المشروع
        if project_id not in project_outputs:
            project_outputs[project_id] = []

        project_outputs[project_id].append(f"تم بدء تشغيل المشروع في {datetime.now().strftime('%H:%M:%S')}")

        return process

    except Exception as e:
        if project_id not in project_outputs:
            project_outputs[project_id] = []
        project_outputs[project_id].append(f"خطأ في التشغيل: {str(e)}")
        return None

def monitor_project_output(project_id):
    """مراقبة مخرجات المشروع"""
    if project_id not in active_projects:
        return

    process = active_projects[project_id]['process']
    
    try:
        stdout, stderr = process.communicate(timeout=1)
        
        if stdout:
            project_outputs[project_id].extend(stdout.strip().split('\n'))
        
        if stderr:
            project_outputs[project_id].extend([f"خطأ: {line}" for line in stderr.strip().split('\n')])
            
        # الحد من حجم المخرجات
        if len(project_outputs[project_id]) > 100:
            project_outputs[project_id] = project_outputs[project_id][-50:]
            
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        project_outputs[project_id].append(f"خطأ في مراقبة المخرجات: {str(e)}")

def init_database():
    """تهيئة قاعدة البيانات"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # جدول المستخدمين
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            user_id TEXT UNIQUE NOT NULL,
            is_subscribed INTEGER DEFAULT 0,
            language TEXT DEFAULT 'ar',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # جدول المشاريع
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            file_count INTEGER DEFAULT 0,
            size REAL DEFAULT 0,
            status TEXT DEFAULT 'stopped',
            project_dir TEXT,
            url TEXT,
            pid INTEGER,
            sandbox_id TEXT,
            session_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_run TIMESTAMP,
            auto_stop_time TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    # جدول الملفات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects (id)
        )
    ''')

    # جدول المستخدمين المحظورين
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS banned_users (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            ban_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reason TEXT
        )
    ''')

    # جدول المستخدمين المرقّين
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS upgraded_users (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            max_files INTEGER DEFAULT 10,
            upgrade_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # إضافة أعمدة إضافية إذا لم تكن موجودة
    try:
        cursor.execute('ALTER TABLE projects ADD COLUMN auto_stop_time TIMESTAMP')
    except:
        pass

    try:
        cursor.execute('ALTER TABLE projects ADD COLUMN session_id TEXT')
    except:
        pass

    conn.commit()
    conn.close()

# قوالب HTML
LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SayoHosting - استضافة احترافية</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;700&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Cairo', sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 25%, #3498db 50%, #5dade2 75%, #85c1e8 100%);
            background-attachment: fixed;
        }

        .glass-effect {
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(15px);
            border: 2px solid rgba(255, 255, 255, 0.3);
            box-shadow: 0 25px 45px rgba(0, 0, 0, 0.1);
        }

        .animate-fade-in {
            animation: fadeIn 1.2s ease-in-out;
        }

        .animate-slide-up {
            animation: slideUp 0.8s ease-out;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: scale(0.8); }
            to { opacity: 1; transform: scale(1); }
        }

        @keyframes slideUp {
            from { transform: translateY(30px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }

        .btn-hover {
            transition: all 0.4s ease;
            position: relative;
            overflow: hidden;
        }

        .btn-hover:hover {
            transform: translateY(-3px);
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.3);
        }

        .btn-hover::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
            transition: left 0.5s;
        }

        .btn-hover:hover::before {
            left: 100%;
        }

        .input-focus:focus {
            transform: scale(1.03);
            box-shadow: 0 0 25px rgba(176, 224, 230, 0.4);
        }

        .logo-glow {
            box-shadow: 0 0 30px rgba(176, 224, 230, 0.6);
            animation: pulse 3s ease-in-out infinite;
        }

        @keyframes pulse {
            0%, 100% { transform: scale(1); box-shadow: 0 0 30px rgba(176, 224, 230, 0.6); }
            50% { transform: scale(1.05); box-shadow: 0 0 40px rgba(176, 224, 230, 0.8); }
        }

        @media (max-width: 768px) {
            .glass-effect {
                margin: 1rem;
                padding: 1.5rem;
            }
        }
    </style>
</head>
<body class="min-h-screen flex items-center justify-center p-6">
    <div class="glass-effect rounded-3xl p-10 w-full max-w-md animate-fade-in shadow-2xl">
        <!-- الشعار والعنوان -->
        <div class="text-center mb-8">
            <img src="https://raw.githubusercontent.com/2SAYO/Pictures/refs/heads/main/Picsart_25-06-20_02-35-07-998.png" 
                 alt="SayoHosting" class="w-24 h-24 mx-auto rounded-full logo-glow mb-4">
            <h1 class="text-5xl font-bold text-white drop-shadow-lg mb-2">SayoHosting</h1>
            <p class="text-gray-200 text-lg font-medium">استضافة ملفاتك ومشاريعك بأمان واحترافية عالمية</p>
        </div>

        <!-- رسائل الفلاش -->
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                {% for message in messages %}
                    <div class="bg-red-500 bg-opacity-90 text-white p-4 rounded-xl mb-3 animate-slide-up shadow-lg">
                        <i class="fas fa-exclamation-triangle mr-2"></i>{{ message }}
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <!-- أزرار التبديل -->
        <div class="flex mb-6 bg-white bg-opacity-10 rounded-xl p-1">
            <button id="loginBtn" onclick="showLogin()" 
                    class="flex-1 py-3 px-4 rounded-lg bg-gradient-to-r from-blue-600 to-blue-700 text-white font-bold transition-all duration-300">
                <i class="fas fa-sign-in-alt mr-2"></i>تسجيل الدخول
            </button>
            <button id="registerBtn" onclick="showRegister()" 
                    class="flex-1 py-3 px-4 rounded-lg bg-transparent border-3 border-white text-white font-bold transition-all duration-300">
                <i class="fas fa-user-plus mr-2"></i>حساب جديد
            </button>
        </div>

        <!-- نموذج تسجيل الدخول -->
        <form id="loginForm" method="POST" action="/login" class="space-y-4">
            <div>
                <input type="email" name="email" required
                       class="w-full px-4 py-4 bg-white bg-opacity-25 text-white placeholder-gray-300 border-2 border-white border-opacity-40 rounded-xl focus:outline-none focus:ring-3 focus:ring-blue-400 input-focus transition-all duration-300"
                       placeholder="أدخل بريدك الإلكتروني">
            </div>
            <div>
                <input type="password" name="password" required
                       class="w-full px-4 py-4 bg-white bg-opacity-25 text-white placeholder-gray-300 border-2 border-white border-opacity-40 rounded-xl focus:outline-none focus:ring-3 focus:ring-blue-400 input-focus transition-all duration-300"
                       placeholder="أدخل كلمة المرور">
            </div>
            <button type="submit" 
                    class="w-full py-4 bg-gradient-to-r from-blue-600 via-purple-600 to-blue-800 text-white rounded-xl font-bold btn-hover shadow-lg">
                <i class="fas fa-rocket mr-2"></i>دخول إلى المنصة
            </button>
        </form>

        <!-- نموذج التسجيل -->
        <form id="registerForm" method="POST" action="/register" class="space-y-4 hidden">
            <div>
                <input type="text" name="name" required
                       class="w-full px-4 py-4 bg-white bg-opacity-25 text-white placeholder-gray-300 border-2 border-white border-opacity-40 rounded-xl focus:outline-none focus:ring-3 focus:ring-green-400 input-focus transition-all duration-300"
                       placeholder="أدخل اسمك الكامل">
            </div>
            <div>
                <input type="email" name="email" required
                       class="w-full px-4 py-4 bg-white bg-opacity-25 text-white placeholder-gray-300 border-2 border-white border-opacity-40 rounded-xl focus:outline-none focus:ring-3 focus:ring-green-400 input-focus transition-all duration-300"
                       placeholder="أدخل بريدك الإلكتروني">
            </div>
            <div>
                <input type="password" name="password" required
                       class="w-full px-4 py-4 bg-white bg-opacity-25 text-white placeholder-gray-300 border-2 border-white border-opacity-40 rounded-xl focus:outline-none focus:ring-3 focus:ring-green-400 input-focus transition-all duration-300"
                       placeholder="أدخل كلمة مرور قوية">
            </div>
            <div>
                <input type="password" name="confirm_password" required
                       class="w-full px-4 py-4 bg-white bg-opacity-25 text-white placeholder-gray-300 border-2 border-white border-opacity-40 rounded-xl focus:outline-none focus:ring-3 focus:ring-green-400 input-focus transition-all duration-300"
                       placeholder="أعد كتابة كلمة المرور">
            </div>
            <button type="submit" 
                    class="w-full py-4 bg-gradient-to-r from-green-600 via-blue-600 to-green-800 text-white rounded-xl font-bold btn-hover shadow-lg">
                <i class="fas fa-user-plus mr-2"></i>إنشاء حساب جديد
            </button>
        </form>

        <!-- النص السفلي -->
        <p class="text-center text-gray-200 text-sm font-medium mt-6">
            نحن نحمي بياناتك بأحدث تقنيات الأمان العالمية
        </p>
    </div>

    <script>
        function showLogin() {
            document.getElementById('loginForm').classList.remove('hidden');
            document.getElementById('registerForm').classList.add('hidden');
            document.getElementById('loginBtn').classList.add('bg-gradient-to-r', 'from-blue-600', 'to-blue-700');
            document.getElementById('loginBtn').classList.remove('bg-transparent', 'border-3', 'border-white');
            document.getElementById('registerBtn').classList.remove('bg-gradient-to-r', 'from-blue-600', 'to-blue-700');
            document.getElementById('registerBtn').classList.add('bg-transparent', 'border-3', 'border-white');
        }

        function showRegister() {
            document.getElementById('registerForm').classList.remove('hidden');
            document.getElementById('loginForm').classList.add('hidden');
            document.getElementById('registerBtn').classList.add('bg-gradient-to-r', 'from-blue-600', 'to-blue-700');
            document.getElementById('registerBtn').classList.remove('bg-transparent', 'border-3', 'border-white');
            document.getElementById('loginBtn').classList.remove('bg-gradient-to-r', 'from-blue-600', 'to-blue-700');
            document.getElementById('loginBtn').classList.add('bg-transparent', 'border-3', 'border-white');
        }
    </script>
</body>
</html>
'''

DASHBOARD_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SayoHosting - لوحة التحكم</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;700&display=swap" rel="stylesheet">
    <style>
        body {
            font-family: 'Cairo', sans-serif;
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 25%, #3498db 50%, #5dade2 75%, #85c1e8 100%);
            background-attachment: fixed;
        }

        .glass-effect {
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(20px);
            border: 2px solid rgba(255, 255, 255, 0.25);
            box-shadow: 0 25px 50px rgba(0, 0, 0, 0.15);
        }

        .btn-hover {
            transition: all 0.4s ease;
            position: relative;
            overflow: hidden;
        }

        .btn-hover:hover {
            transform: translateY(-4px);
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.25);
        }

        .logo-glow {
            box-shadow: 0 0 25px rgba(176, 224, 230, 0.5);
            animation: pulse 2s ease-in-out infinite;
        }

        @keyframes pulse {
            0%, 100% { transform: scale(1); box-shadow: 0 0 25px rgba(176, 224, 230, 0.5); }
            50% { transform: scale(1.05); box-shadow: 0 0 35px rgba(176, 224, 230, 0.7); }
        }

        .sidebar {
            width: 280px;
            transition: all 0.3s ease;
        }

        .main-content {
            margin-right: 280px;
            transition: all 0.3s ease;
        }

        .sidebar-item {
            transition: all 0.3s ease;
            cursor: pointer;
        }

        .sidebar-item:hover {
            background: rgba(255, 255, 255, 0.1);
            transform: translateX(-5px);
        }

        .active-page {
            background: rgba(255, 255, 255, 0.2) !important;
            border-right: 4px solid #40E0D0;
        }

        .console-box {
            background: #1a1a1a;
            border: 2px solid #333;
            border-radius: 8px;
            height: 300px;
            overflow-y: auto;
            padding: 15px;
            font-family: 'Courier New', monospace;
            color: #00ff00;
            font-size: 14px;
            line-height: 1.6;
        }

        .console-line {
            margin-bottom: 2px;
            word-wrap: break-word;
        }

        .project-card {
            transition: all 0.5s ease;
            cursor: pointer;
        }

        .project-card:hover {
            transform: translateY(-8px) scale(1.02);
            box-shadow: 0 25px 50px rgba(0, 0, 0, 0.3);
        }

        .library-console {
            background: #2d3748;
            border-radius: 8px;
            max-height: 300px;
            overflow-y: auto;
            padding: 10px;
            font-family: 'Courier New', monospace;
            color: #fff;
            font-size: 13px;
            line-height: 1.4;
            margin-top: 10px;
        }

        /* استجابة للجوال */
        @media (max-width: 768px) {
            .sidebar {
                width: 100%;
                position: fixed;
                top: 80px;
                right: -100%;
                z-index: 30;
                height: calc(100vh - 80px);
                transition: right 0.3s ease;
            }

            .sidebar.mobile-open {
                right: 0;
            }

            .main-content {
                margin-right: 0;
                padding: 1rem;
            }

            .mobile-menu-btn {
                display: block;
            }

            .desktop-only {
                display: none;
            }
        }

        @media (min-width: 769px) {
            .mobile-menu-btn {
                display: none;
            }
        }
    </style>
</head>
<body class="bg-gray-100">
    <!-- الهيدر -->
    <header class="fixed top-0 right-0 left-0 z-40 glass-effect border-b-2 border-white border-opacity-30">
        <div class="flex items-center justify-between px-6 py-4">
            <div class="flex items-center">
                <button class="mobile-menu-btn text-white text-2xl mr-4" onclick="toggleSidebar()">
                    <i class="fas fa-bars"></i>
                </button>
                <img src="https://raw.githubusercontent.com/2SAYO/Pictures/refs/heads/main/Picsart_25-06-20_02-35-07-998.png" 
                     alt="SayoHosting" class="w-12 h-12 rounded-full logo-glow ml-3">
                <div>
                    <h1 class="text-2xl font-bold text-white drop-shadow-lg">SayoHosting</h1>
                    <p class="text-gray-200 text-sm font-medium">منصة الاستضافة الاحترافية</p>
                </div>
            </div>

            <div class="flex items-center space-x-4">
                <span class="text-white bg-white bg-opacity-10 px-4 py-2 rounded-full flex items-center">
                    <i class="fas fa-user mr-2"></i>{{ user_name }}
                    {% if user_subscribed %}
                        <img src="https://raw.githubusercontent.com/2SAYO/Pictures/refs/heads/main/Picsart_25-06-20_02-31-52-831.png" 
                             alt="مشترك" class="w-6 h-6 mr-2">
                    {% endif %}
                </span>
            </div>
        </div>
    </header>

    <!-- الشريط الجانبي -->
    <aside id="sidebar" class="sidebar glass-effect fixed right-0 top-20 bottom-0 overflow-y-auto z-30">
        <nav class="p-4">
            <div class="space-y-2">
                <div class="sidebar-item px-4 py-3 text-white rounded-xl active-page" onclick="showPage('dashboard')">
                    <i class="fas fa-tachometer-alt text-blue-400 mr-3"></i>
                    <span>لوحة التحكم</span>
                </div>
                <div class="sidebar-item px-4 py-3 text-white rounded-xl" onclick="showPage('upload')">
                    <i class="fas fa-cloud-upload-alt text-green-400 mr-3"></i>
                    <span>رفع مشروع/ملف</span>
                </div>
                <div class="sidebar-item px-4 py-3 text-white rounded-xl" onclick="showPage('projects')">
                    <i class="fas fa-folder-open text-purple-400 mr-3"></i>
                    <span>مشاريعي/ملفاتي</span>
                </div>
                <div class="sidebar-item px-4 py-3 text-white rounded-xl" onclick="showPage('server-status')">
                    <i class="fas fa-server text-yellow-400 mr-3"></i>
                    <span>حالة الخادم</span>
                </div>
                <div class="sidebar-item px-4 py-3 text-white rounded-xl" onclick="showPage('library-request')">
                    <i class="fas fa-book text-orange-400 mr-3"></i>
                    <span>تحميل المكتبات</span>
                </div>
                <div class="sidebar-item px-4 py-3 text-white rounded-xl" onclick="showPage('profile')">
                    <i class="fas fa-user-circle text-pink-400 mr-3"></i>
                    <span>البروفايل</span>
                </div>
            </div>
        </nav>
    </aside>

    <!-- المحتوى الرئيسي -->
    <main class="main-content pt-24 p-8">
        <!-- صفحة لوحة التحكم -->
        <div id="dashboard-page">
            <!-- بطاقات الإحصائيات -->
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                <div class="glass-effect rounded-2xl p-6">
                    <div class="flex items-center">
                        <div class="w-12 h-12 bg-gradient-to-r from-blue-500 to-blue-600 rounded-full flex items-center justify-center ml-4">
                            <i class="fas fa-folder text-white text-xl"></i>
                        </div>
                        <div>
                            <h3 class="text-white text-lg font-bold">{{ project_count }}</h3>
                            <p class="text-gray-300 text-sm">مشروع نشط</p>
                        </div>
                    </div>
                </div>

                <div class="glass-effect rounded-2xl p-6">
                    <div class="flex items-center">
                        <div class="w-12 h-12 bg-gradient-to-r from-green-500 to-green-600 rounded-full flex items-center justify-center ml-4">
                            <i class="fas fa-file text-white text-xl"></i>
                        </div>
                        <div>
                            <h3 class="text-white text-lg font-bold">{{ file_count }}</h3>
                            <p class="text-gray-300 text-sm">ملف محفوظ</p>
                        </div>
                    </div>
                </div>

                <div class="glass-effect rounded-2xl p-6">
                    <div class="flex items-center">
                        <div class="w-12 h-12 bg-gradient-to-r from-purple-500 to-purple-600 rounded-full flex items-center justify-center ml-4">
                            <i class="fas fa-hdd text-white text-xl"></i>
                        </div>
                        <div>
                            <h3 class="text-white text-lg font-bold">{{ "%.2f"|format(storage_used) }}</h3>
                            <p class="text-gray-300 text-sm">ميجابايت مستخدم</p>
                        </div>
                    </div>
                </div>

                <div class="glass-effect rounded-2xl p-6">
                    <div class="flex items-center">
                        <div class="w-12 h-12 bg-gradient-to-r from-red-500 to-red-600 rounded-full flex items-center justify-center ml-4">
                            <i class="fas fa-play text-white text-xl"></i>
                        </div>
                        <div>
                            <h3 class="text-white text-lg font-bold">{{ running_projects }}</h3>
                            <p class="text-gray-300 text-sm">مشروع يعمل الآن</p>
                        </div>
                    </div>
                </div>
            </div>

            <!-- قسم الترحيب -->
            <div class="glass-effect rounded-3xl p-8 text-center">
                <img src="https://raw.githubusercontent.com/2SAYO/Pictures/refs/heads/main/Picsart_25-06-20_02-35-07-998.png" 
                     alt="SayoHosting" class="w-32 h-32 mx-auto rounded-full opacity-80 mb-6">
                <h2 class="text-3xl font-bold text-white mb-4">مرحباً بك في SayoHosting! 🚀</h2>
                <p class="text-gray-200 text-lg mb-6">
                    منصتك الاحترافية لاستضافة وإدارة مشاريع Python بأمان وكفاءة عالية. 
                    ابدأ برفع مشروعك الأول أو استكشاف الميزات المتقدمة المتاحة لك.
                </p>
            </div>
        </div>

        <!-- صفحة رفع الملفات -->
        <div id="upload-page" class="hidden">
            <div class="glass-effect rounded-3xl p-8">
                <h2 class="text-3xl font-bold text-white mb-6 flex items-center">
                    <i class="fas fa-cloud-upload-alt text-blue-400 mr-3"></i>
                    رفع مشروع جديد
                </h2>

                <form method="POST" action="/upload" enctype="multipart/form-data" class="space-y-6">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label class="block text-white font-bold mb-2">اسم المشروع</label>
                            <input type="text" name="project_name" required
                                   class="w-full px-4 py-3 bg-white bg-opacity-20 border border-white border-opacity-30 rounded-xl text-white placeholder-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-400"
                                   placeholder="أدخل اسم المشروع الاحترافي">
                        </div>
                        <div>
                            <label class="block text-white font-bold mb-2">وصف المشروع</label>
                            <input type="text" name="project_description"
                                   class="w-full px-4 py-3 bg-white bg-opacity-20 border border-white border-opacity-30 rounded-xl text-white placeholder-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-400"
                                   placeholder="وصف مفصل للمشروع">
                        </div>
                    </div>

                    <div>
                        <label class="block text-white font-bold mb-2">الملفات</label>
                        <input type="file" name="files" multiple accept=".py,.html,.css,.js,.txt,.zip,.json,.md" 
                               class="w-full px-4 py-3 bg-white bg-opacity-20 border border-white border-opacity-30 rounded-xl text-white">
                    </div>

                    <button type="submit"
                            class="w-full py-5 bg-gradient-to-r from-green-600 via-blue-600 to-green-800 text-white rounded-xl font-bold text-lg btn-hover shadow-lg">
                        <i class="fas fa-rocket mr-2"></i>رفع المشروع للمنصة
                    </button>
                </form>
            </div>
        </div>

        <!-- صفحة المشاريع -->
        <div id="projects-page" class="hidden">
            <div class="glass-effect rounded-3xl p-8">
                <h2 class="text-3xl font-bold text-white mb-6 flex items-center">
                    <i class="fas fa-folder-open text-purple-400 mr-3"></i>
                    مشاريعي الاحترافية
                </h2>

                {% if projects %}
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {% for project in projects %}
                        <div class="project-card glass-effect rounded-2xl p-6" onclick="openProjectConsole('{{ project.id }}', '{{ project.name }}')">
                            <div class="flex items-center mb-4">
                                <div class="w-12 h-12 bg-gradient-to-r from-purple-500 to-blue-600 rounded-full flex items-center justify-center ml-3">
                                    <i class="fas fa-file-code text-white text-xl"></i>
                                </div>
                                <div class="flex-1">
                                    <h3 class="text-xl font-bold text-white">{{ project.name }}</h3>
                                    <p class="text-gray-300 text-sm">{{ project.description or 'لا يوجد وصف' }}</p>
                                </div>
                            </div>

                            <div class="space-y-2">
                                <div class="flex items-center justify-between">
                                    <span id="project-status-{{ project.id }}">
                                        {% if project.status == 'running' %}
                                            <span class="bg-green-500 bg-opacity-30 px-2 py-1 rounded-full text-green-200 text-sm">
                                                <i class="fas fa-circle mr-1 animate-pulse"></i>يعمل
                                            </span>
                                        {% else %}
                                            <span class="bg-gray-500 bg-opacity-30 px-2 py-1 rounded-full text-gray-300 text-sm">
                                                <i class="fas fa-pause-circle mr-1"></i>متوقف
                                            </span>
                                        {% endif %}
                                    </span>
                                </div>
                                <p class="text-gray-400 text-xs">
                                    {{ project.file_count or 0 }} ملف • 
                                    {{ project.created_at.strftime('%Y-%m-%d') if project.created_at else 'غير محدد' }}
                                </p>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                {% else %}
                    <div class="text-center py-12">
                        <i class="fas fa-folder-open text-6xl text-white text-opacity-30 mb-4"></i>
                        <h3 class="text-2xl font-bold text-white mb-4">لا توجد مشاريع بعد</h3>
                        <p class="text-gray-300 mb-6">ابدأ بإنشاء مشروعك الأول الآن!</p>
                        <button onclick="showPage('upload')" class="bg-gradient-to-r from-blue-600 to-purple-600 text-white px-6 py-3 rounded-xl font-bold btn-hover">
                            <i class="fas fa-plus mr-2"></i>إنشاء مشروع جديد
                        </button>
                    </div>
                {% endif %}
            </div>
        </div>

        <!-- صفحة حالة الخادم -->
        <div id="server-status-page" class="hidden">
            <div class="glass-effect rounded-3xl p-8">
                <h2 class="text-3xl font-bold text-white mb-6 flex items-center">
                    <i class="fas fa-server text-yellow-400 mr-3"></i>
                    حالة الخادم
                </h2>

                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                    <div class="glass-effect rounded-2xl p-6">
                        <div class="flex items-center">
                            <div class="w-12 h-12 bg-gradient-to-r from-blue-500 to-blue-600 rounded-full flex items-center justify-center ml-4">
                                <i class="fas fa-microchip text-white text-xl"></i>
                            </div>
                            <div>
                                <h3 class="text-white text-lg font-bold">{{ server_status.cpu_usage }}%</h3>
                                <p class="text-gray-300 text-sm">استخدام المعالج</p>
                            </div>
                        </div>
                    </div>

                    <div class="glass-effect rounded-2xl p-6">
                        <div class="flex items-center">
                            <div class="w-12 h-12 bg-gradient-to-r from-green-500 to-green-600 rounded-full flex items-center justify-center ml-4">
                                <i class="fas fa-memory text-white text-xl"></i>
                            </div>
                            <div>
                                <h3 class="text-white text-lg font-bold">{{ server_status.memory_usage }}%</h3>
                                <p class="text-gray-300 text-sm">استخدام الذاكرة</p>
                            </div>
                        </div>
                    </div>

                    <div class="glass-effect rounded-2xl p-6">
                        <div class="flex items-center">
                            <div class="w-12 h-12 bg-gradient-to-r from-purple-500 to-purple-600 rounded-full flex items-center justify-center ml-4">
                                <i class="fas fa-clock text-white text-xl"></i>
                            </div>
                            <div>
                                <h3 class="text-white text-lg font-bold">{{ server_status.uptime_days }} يوم</h3>
                                <p class="text-gray-300 text-sm">وقت التشغيل</p>
                            </div>
                        </div>
                    </div>

                    <div class="glass-effect rounded-2xl p-6">
                        <div class="flex items-center">
                            <div class="w-12 h-12 bg-gradient-to-r from-red-500 to-red-600 rounded-full flex items-center justify-center ml-4">
                                <i class="fas fa-wifi text-white text-xl"></i>
                            </div>
                            <div>
                                <h3 class="text-white text-lg font-bold">{{ server_status.ping_ms }}ms</h3>
                                <p class="text-gray-300 text-sm">زمن الاستجابة</p>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="glass-effect rounded-2xl p-6">
                    <h3 class="text-2xl font-bold text-white mb-4 flex items-center">
                        <i class="fas fa-chart-line text-blue-400 mr-3"></i>
                        تفاصيل النظام
                    </h3>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-white">
                        <div>
                            <strong>نظام التشغيل:</strong>
                            <span class="text-gray-300">{{ server_status.os_info }}</span>
                        </div>
                        <div>
                            <strong>إجمالي الذاكرة:</strong>
                            <span class="text-gray-300">{{ server_status.total_memory_gb }} GB</span>
                        </div>
                        <div>
                            <strong>عدد المعالجات:</strong>
                            <span class="text-gray-300">{{ server_status.cpu_count }}</span>
                        </div>
                        <div>
                            <strong>استخدام القرص:</strong>
                            <span class="text-gray-300">{{ server_status.disk_usage_percent }}%</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- صفحة تحميل المكتبات -->
        <div id="library-request-page" class="hidden">
            <div class="glass-effect rounded-3xl p-8">
                <h2 class="text-3xl font-bold text-white mb-6 flex items-center">
                    <i class="fas fa-book text-orange-400 mr-3"></i>
                    تحميل المكتبات
                </h2>

                <form id="library-install-form" class="space-y-6">
                    <div>
                        <label class="block text-white font-bold mb-2">اسم المكتبة</label>
                        <input type="text" id="library_name" required
                               class="w-full px-4 py-3 bg-white bg-opacity-20 border border-white border-opacity-30 rounded-xl text-white placeholder-gray-300 focus:outline-none focus:ring-2 focus:ring-orange-400"
                               placeholder="مثال: requests, numpy, pandas">
                    </div>

                    <button type="submit"
                            class="w-full py-4 bg-gradient-to-r from-orange-600 to-red-600 text-white rounded-xl font-bold btn-hover">
                        <i class="fas fa-download mr-2"></i>تحميل المكتبة
                    </button>
                </form>

                <div id="library-console" class="library-console hidden mt-4">
                    <div class="text-green-400 mb-2">جاهز لتثبيت المكتبات...</div>
                </div>
            </div>
        </div>

        <!-- صفحة البروفايل -->
        <div id="profile-page" class="hidden">
            <div class="glass-effect rounded-3xl p-8">
                <h2 class="text-3xl font-bold text-white mb-6 flex items-center">
                    <i class="fas fa-user-circle text-pink-400 mr-3"></i>
                    البروفايل الشخصي
                </h2>

                <div class="max-w-2xl mx-auto">
                    <!-- صورة البروفايل -->
                    <div class="text-center mb-8">
                        <div class="w-32 h-32 bg-gradient-to-r from-blue-500 to-purple-600 rounded-full flex items-center justify-center mx-auto mb-4">
                            <i class="fas fa-user text-5xl text-white"></i>
                        </div>
                        {% if user_subscribed %}
                            <div class="flex items-center justify-center">
                                <img src="https://raw.githubusercontent.com/2SAYO/Pictures/refs/heads/main/Picsart_25-06-20_02-31-52-831.png" 
                                     alt="مشترك" class="w-8 h-8 mr-2">
                                <span class="text-green-400 font-bold">مستخدم مشترك</span>
                            </div>
                        {% endif %}
                    </div>

                    <!-- معلومات المستخدم -->
                    <form id="profile-form" class="space-y-6">
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <label class="block text-white font-bold mb-2">الاسم</label>
                                <input type="text" id="user_name" value="{{ user_name }}"
                                       class="w-full px-4 py-3 bg-white bg-opacity-20 border border-white border-opacity-30 rounded-xl text-white placeholder-gray-300 focus:outline-none focus:ring-2 focus:ring-pink-400">
                            </div>
                            <div>
                                <label class="block text-white font-bold mb-2">البريد الإلكتروني</label>
                                <input type="email" value="{{ user_email }}" readonly
                                       class="w-full px-4 py-3 bg-gray-600 bg-opacity-50 border border-white border-opacity-30 rounded-xl text-gray-300 cursor-not-allowed">
                            </div>
                        </div>

                        <div>
                            <label class="block text-white font-bold mb-2">المعرف الفريد (ID)</label>
                            <div class="flex">
                                <input type="text" id="unique_id" value="{{ user_unique_id }}" readonly
                                       class="flex-1 px-4 py-3 bg-gray-600 bg-opacity-50 border border-white border-opacity-30 rounded-r-xl text-gray-300 cursor-not-allowed">
                                <button type="button" onclick="copyToClipboard('unique_id')"
                                        class="px-4 py-3 bg-blue-600 text-white rounded-l-xl hover:bg-blue-700 transition-colors">
                                    <i class="fas fa-copy"></i>
                                </button>
                            </div>
                            <p class="text-gray-400 text-sm mt-2">هذا المعرف فريد ولا يمكن تغييره</p>
                        </div>

                        <div class="flex space-x-4">
                            <button type="submit"
                                    class="flex-1 py-4 bg-gradient-to-r from-green-600 to-blue-600 text-white rounded-xl font-bold btn-hover">
                                <i class="fas fa-save mr-2"></i>حفظ التغييرات
                            </button>
                            <a href="/logout"
                               class="flex-1 py-4 bg-gradient-to-r from-red-600 to-red-700 text-white rounded-xl font-bold btn-hover text-center">
                                <i class="fas fa-sign-out-alt mr-2"></i>تسجيل الخروج
                            </a>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <!-- مودال الكونسول البسيط -->
        <div id="console-modal" class="hidden fixed inset-0 bg-black bg-opacity-75 z-50 flex items-center justify-center p-4">
            <div class="glass-effect rounded-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden">
                <div class="flex items-center justify-between p-6 border-b border-white border-opacity-20">
                    <h3 id="console-project-name" class="text-2xl font-bold text-white"></h3>
                    <button onclick="closeConsole()" class="text-white hover:text-red-400 text-2xl">
                        <i class="fas fa-times"></i>
                    </button>
                </div>

                <div class="p-6">
                    <div id="project-console" class="console-box mb-4">
                        <div class="console-line text-blue-400">جاهز لتشغيل المشروع...</div>
                    </div>

                    <div class="flex flex-wrap gap-4">
                        <button id="run-btn" onclick="runProject()" 
                                class="px-6 py-3 bg-gradient-to-r from-green-500 to-green-600 text-white rounded-xl font-bold btn-hover">
                            <i class="fas fa-play mr-2"></i>تشغيل
                        </button>
                        <button id="stop-btn" onclick="stopProject()"
                                class="px-6 py-3 bg-gradient-to-r from-orange-500 to-orange-600 text-white rounded-xl font-bold btn-hover">
                            <i class="fas fa-stop mr-2"></i>إيقاف
                        </button>
                        <button id="clear-btn" onclick="clearConsole()"
                                class="px-6 py-3 bg-gradient-to-r from-blue-500 to-blue-600 text-white rounded-xl font-bold btn-hover">
                            <i class="fas fa-broom mr-2"></i>مسح
                        </button>
                        <button id="delete-btn" onclick="deleteProject()" 
                                class="px-6 py-3 bg-gradient-to-r from-red-500 to-red-600 text-white rounded-xl font-bold btn-hover">
                            <i class="fas fa-trash mr-2"></i>حذف
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </main>

    <script>
        let currentProjectId = null;
        let currentProjectName = '';
        let outputUpdateInterval = null;

        // إظهار الصفحة المطلوبة
        function showPage(pageId) {
            // إخفاء جميع الصفحات
            const pages = ['dashboard-page', 'upload-page', 'projects-page', 'server-status-page', 'library-request-page', 'profile-page'];
            pages.forEach(page => {
                const element = document.getElementById(page);
                if (element) {
                    element.classList.add('hidden');
                }
            });

            // إزالة الفئة النشطة من جميع عناصر الشريط الجانبي
            document.querySelectorAll('.sidebar-item').forEach(item => {
                item.classList.remove('active-page');
            });

            // عرض الصفحة المطلوبة
            const targetPage = document.getElementById(pageId + '-page');
            if (targetPage) {
                targetPage.classList.remove('hidden');
            }

            // إضافة الفئة النشطة للعنصر المحدد
            const activeItem = document.querySelector(`[onclick="showPage('${pageId}')"]`);
            if (activeItem) {
                activeItem.classList.add('active-page');
            }

            // إغلاق الشريط الجانبي في الجوال
            closeSidebar();
        }

        // تبديل الشريط الجانبي
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.toggle('mobile-open');
        }

        // إغلاق الشريط الجانبي
        function closeSidebar() {
            const sidebar = document.getElementById('sidebar');
            sidebar.classList.remove('mobile-open');
        }

        // فتح كونسول المشروع
        function openProjectConsole(projectId, projectName) {
            currentProjectId = projectId;
            currentProjectName = projectName;

            const modal = document.getElementById('console-modal');
            const projectNameEl = document.getElementById('console-project-name');

            if (projectNameEl) projectNameEl.textContent = projectName;
            if (modal) modal.classList.remove('hidden');

            // تحديث المخرجات مرة واحدة
            updateProjectOutput();
        }

        // إغلاق الكونسول
        function closeConsole() {
            const modal = document.getElementById('console-modal');
            if (modal) modal.classList.add('hidden');
            
            currentProjectId = null;
            currentProjectName = '';
        }

        // تحديث كونسول المشروع
        function updateConsole(message, isError = false) {
            const consoleEl = document.getElementById('project-console');
            if (consoleEl) {
                const timestamp = new Date().toLocaleTimeString('ar-SA');
                const lineClass = isError ? 'text-red-400' : 'text-green-400';
                const newLine = `<div class="console-line ${lineClass}">
                    <span class="text-gray-400">[${timestamp}]</span> ${message}
                </div>`;
                consoleEl.innerHTML += newLine;
                consoleEl.scrollTop = consoleEl.scrollHeight;
            }
        }

        // مسح الكونسول
        function clearConsole() {
            const consoleEl = document.getElementById('project-console');
            if (consoleEl) {
                consoleEl.innerHTML = '<div class="console-line text-blue-400">تم مسح الكونسول...</div>';
            }
        }

        // تحديث مخرجات المشروع
        async function updateProjectOutput() {
            if (!currentProjectId) return;

            try {
                const response = await fetch(`/get-output/${currentProjectId}`);
                const data = await response.json();

                if (data.success && data.output) {
                    const consoleEl = document.getElementById('project-console');
                    if (consoleEl) {
                        consoleEl.innerHTML = '';
                        data.output.forEach(line => {
                            const timestamp = new Date().toLocaleTimeString('ar-SA');
                            const lineClass = line.includes('خطأ') ? 'text-red-400' : 'text-green-400';
                            consoleEl.innerHTML += `<div class="console-line ${lineClass}">
                                <span class="text-gray-400">[${timestamp}]</span> ${line}
                            </div>`;
                        });
                        consoleEl.scrollTop = consoleEl.scrollHeight;
                    }
                }
            } catch (error) {
                console.error('خطأ في تحديث المخرجات:', error);
            }
        }

        // تشغيل المشروع
        async function runProject() {
            if (!currentProjectId) return;

            updateConsole('🚀 جارٍ تشغيل المشروع...');

            try {
                const response = await fetch(`/run/${currentProjectId}`, { method: 'POST' });
                const data = await response.json();

                if (data.success) {
                    updateConsole('✅ تم تشغيل المشروع بنجاح');
                    // تحديث حالة المشروع في الصفحة
                    const statusElement = document.getElementById(`project-status-${currentProjectId}`);
                    if (statusElement) {
                        statusElement.innerHTML = '<span class="bg-green-500 bg-opacity-30 px-2 py-1 rounded-full text-green-200 text-sm"><i class="fas fa-circle mr-1 animate-pulse"></i>يعمل</span>';
                    }
                    
                    // تحديث المخرجات بعد ثانيتين
                    setTimeout(updateProjectOutput, 2000);
                } else {
                    updateConsole('❌ خطأ في التشغيل: ' + data.message, true);
                }
            } catch (error) {
                updateConsole('❌ خطأ في الاتصال: ' + error.message, true);
            }
        }

        // إيقاف المشروع
        async function stopProject() {
            if (!currentProjectId) return;

            updateConsole('⏹️ جارٍ إيقاف المشروع...');

            try {
                const response = await fetch(`/stop/${currentProjectId}`, { method: 'POST' });
                const data = await response.json();

                if (data.success) {
                    updateConsole('✅ تم إيقاف المشروع بنجاح');
                    // تحديث حالة المشروع في الصفحة
                    const statusElement = document.getElementById(`project-status-${currentProjectId}`);
                    if (statusElement) {
                        statusElement.innerHTML = '<span class="bg-gray-500 bg-opacity-30 px-2 py-1 rounded-full text-gray-300 text-sm"><i class="fas fa-pause-circle mr-1"></i>متوقف</span>';
                    }
                } else {
                    updateConsole('❌ خطأ في الإيقاف: ' + data.message, true);
                }
            } catch (error) {
                updateConsole('❌ خطأ في الاتصال: ' + error.message, true);
            }
        }

        // حذف المشروع
        async function deleteProject() {
            if (!currentProjectId) return;

            if (!confirm('هل أنت متأكد من حذف هذا المشروع؟ لا يمكن التراجع عن هذا الإجراء.')) {
                return;
            }

            updateConsole('🗑️ جارٍ حذف المشروع...');

            try {
                const response = await fetch(`/delete/${currentProjectId}`, { method: 'DELETE' });
                const data = await response.json();

                if (data.success) {
                    updateConsole('✅ تم حذف المشروع بنجاح');
                    alert('تم حذف المشروع بنجاح');
                    closeConsole();
                    location.reload();
                } else {
                    updateConsole('❌ خطأ في الحذف: ' + data.message, true);
                }
            } catch (error) {
                updateConsole('❌ خطأ في الاتصال: ' + error.message, true);
            }
        }

        // تثبيت المكتبة
        document.getElementById('library-install-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const libraryName = document.getElementById('library_name').value;
            if (!libraryName) return;

            const consoleEl = document.getElementById('library-console');
            consoleEl.classList.remove('hidden');
            consoleEl.innerHTML = '<div class="text-blue-400">جارٍ تثبيت المكتبة: ' + libraryName + '...</div>';

            try {
                const response = await fetch('/install-library-simple', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ library_name: libraryName })
                });

                const data = await response.json();
                
                if (data.success) {
                    consoleEl.innerHTML += '<div class="text-green-400">تم تثبيت المكتبة بنجاح</div>';
                    consoleEl.innerHTML += '<div class="text-gray-300">' + data.output + '</div>';
                } else {
                    consoleEl.innerHTML += '<div class="text-red-400">فشل في تثبيت المكتبة: ' + data.message + '</div>';
                }

                document.getElementById('library_name').value = '';

            } catch (error) {
                consoleEl.innerHTML += '<div class="text-red-400">خطأ في تثبيت المكتبة: ' + error.message + '</div>';
            }
        });

        // تحديث البروفايل
        document.getElementById('profile-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const userName = document.getElementById('user_name').value;
            
            try {
                const response = await fetch('/update-profile', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ name: userName })
                });

                const data = await response.json();
                
                if (data.success) {
                    alert('تم تحديث البروفايل بنجاح');
                    location.reload();
                } else {
                    alert('خطأ في تحديث البروفايل: ' + data.message);
                }
            } catch (error) {
                alert('خطأ في تحديث البروفايل: ' + error.message);
            }
        });

        // نسخ إلى الحافظة
        function copyToClipboard(elementId) {
            const element = document.getElementById(elementId);
            element.select();
            document.execCommand('copy');
            alert('تم نسخ المعرف إلى الحافظة');
        }

        // إغلاق الشريط الجانبي عند النقر خارجه في الجوال
        document.addEventListener('click', function(event) {
            const sidebar = document.getElementById('sidebar');
            const menuBtn = document.querySelector('.mobile-menu-btn');
            
            if (window.innerWidth <= 768 && sidebar.classList.contains('mobile-open')) {
                if (!sidebar.contains(event.target) && !menuBtn.contains(event.target)) {
                    closeSidebar();
                }
            }
        });

        // تشغيل الصفحة الافتراضية عند التحميل
        document.addEventListener('DOMContentLoaded', function() {
            showPage('dashboard');
        });
    </script>
</body>
</html>
'''

# المسارات
@app.route('/')
def home():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        if not email or not password:
            flash('يرجى ملء جميع الحقول')
            return render_template_string(LOGIN_TEMPLATE)

        try:
            conn = get_db_connection()
            user = conn.execute(
                'SELECT * FROM users WHERE email = ? AND password = ?',
                (email, hash_password(password))
            ).fetchone()

            if user:
                # التحقق من الحظر
                banned = conn.execute(
                    'SELECT * FROM banned_users WHERE user_id = ?',
                    (user['user_id'],)
                ).fetchone()

                if banned:
                    flash('تم حظر هذا الحساب')
                    conn.close()
                    return render_template_string(LOGIN_TEMPLATE)

                session.permanent = True
                session['user_id'] = user['id']
                session['user_email'] = user['email']
                session['user_name'] = user['name']
                session['user_unique_id'] = user['user_id']

                conn.close()
                return redirect(url_for('dashboard'))
            else:
                flash('البريد الإلكتروني أو كلمة المرور غير صحيحة')
        except Exception as e:
            flash('حدث خطأ في تسجيل الدخول')
        finally:
            if 'conn' in locals():
                conn.close()

    return render_template_string(LOGIN_TEMPLATE)

@app.route('/register', methods=['POST'])
def register():
    name = request.form.get('name')
    email = request.form.get('email')
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')

    if not all([name, email, password, confirm_password]):
        flash('يرجى ملء جميع الحقول')
        return render_template_string(LOGIN_TEMPLATE)

    if password != confirm_password:
        flash('كلمات المرور غير متطابقة')
        return render_template_string(LOGIN_TEMPLATE)

    try:
        conn = get_db_connection()

        # التحقق من وجود المستخدم
        existing_user = conn.execute(
            'SELECT id FROM users WHERE email = ?', (email,)
        ).fetchone()

        if existing_user:
            flash('البريد الإلكتروني مستخدم بالفعل')
            conn.close()
            return render_template_string(LOGIN_TEMPLATE)

        # إنشاء مستخدم جديد
        user_id = str(uuid.uuid4())
        unique_user_id = generate_user_id()

        conn.execute('''
            INSERT INTO users (id, name, email, password, user_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, name, email, hash_password(password), unique_user_id))

        conn.commit()

        # تسجيل دخول المستخدم
        session.permanent = True
        session['user_id'] = user_id
        session['user_email'] = email
        session['user_name'] = name
        session['user_unique_id'] = unique_user_id

        conn.close()
        return redirect(url_for('dashboard'))

    except Exception as e:
        flash('حدث خطأ في إنشاء الحساب')
        if 'conn' in locals():
            conn.close()

    return render_template_string(LOGIN_TEMPLATE)

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('home'))

    try:
        conn = get_db_connection()

        # جلب مشاريع المستخدم
        projects = conn.execute('''
            SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC
        ''', (session['user_id'],)).fetchall()

        # تحويل إلى قواميس
        projects = [dict_from_row(project) for project in projects]

        # معالجة التواريخ
        for project in projects:
            if project['created_at']:
                try:
                    project['created_at'] = datetime.fromisoformat(project['created_at'])
                except:
                    project['created_at'] = None

        # حساب الإحصائيات
        project_count = len(projects)

        # حساب عدد الملفات
        file_count = conn.execute('''
            SELECT COUNT(*) as count FROM files f
            JOIN projects p ON f.project_id = p.id
            WHERE p.user_id = ?
        ''', (session['user_id'],)).fetchone()['count']

        # حساب حجم التخزين
        storage_used = conn.execute('''
            SELECT COALESCE(SUM(p.size), 0) as total_size FROM projects p
            WHERE p.user_id = ?
        ''', (session['user_id'],)).fetchone()['total_size']

        # حساب المشاريع قيد التشغيل
        running_projects = conn.execute('''
            SELECT COUNT(*) as count FROM projects
            WHERE user_id = ? AND status = 'running'
        ''', (session['user_id'],)).fetchone()['count']

        # التحقق من الاشتراك
        user_subscribed = is_user_subscribed(session['user_unique_id'])

        # الحصول على حالة الخادم
        server_status = get_server_status()

        conn.close()

        return render_template_string(DASHBOARD_TEMPLATE,
            projects=projects,
            project_count=project_count,
            file_count=file_count,
            storage_used=storage_used,
            running_projects=running_projects,
            user_name=session['user_name'],
            user_email=session['user_email'],
            user_unique_id=session['user_unique_id'],
            user_subscribed=user_subscribed,
            server_status=server_status
        )

    except Exception as e:
        flash('حدث خطأ في تحميل لوحة التحكم')
        return redirect(url_for('home'))

@app.route('/upload', methods=['POST'])
def upload():
    if 'user_id' not in session:
        flash('يجب تسجيل الدخول أولاً')
        return redirect(url_for('dashboard'))

    try:
        conn = get_db_connection()

        # التحقق من حدود المستخدم
        project_count = conn.execute(
            'SELECT COUNT(*) as count FROM projects WHERE user_id = ?',
            (session['user_id'],)
        ).fetchone()['count']

        # التحقق من الترقية
        upgraded_user = conn.execute(
            'SELECT max_files FROM upgraded_users WHERE user_id = ?',
            (session['user_unique_id'],)
        ).fetchone()

        max_projects = upgraded_user['max_files'] if upgraded_user else 2

        if project_count >= max_projects:
            flash(f'تم الوصول للحد الأقصى من المشاريع ({max_projects})')
            return redirect(url_for('dashboard'))

        project_name = request.form.get('project_name')
        project_description = request.form.get('project_description', '')
        files = request.files.getlist('files')

        if not project_name or not files:
            flash('يرجى ملء جميع الحقول المطلوبة')
            return redirect(url_for('dashboard'))

        # إنشاء معرف المشروع
        project_id = str(uuid.uuid4())

        # إنشاء مجلد مؤقت
        temp_dir = os.path.join('pending_files_temp', project_id)
        os.makedirs(temp_dir, exist_ok=True)

        total_size = 0
        file_records = []

        # حفظ الملفات وفحصها
        for file in files:
            if file.filename and allowed_file(file.filename):
                file_path = os.path.join(temp_dir, file.filename)
                file.save(file_path)

                # فحص الأمان
                is_malicious, reason = scan_for_malicious_code(file_path)
                if is_malicious:
                    shutil.rmtree(temp_dir)
                    flash(f'تم رفض الملف {file.filename}: {reason}')
                    return redirect(url_for('dashboard'))

                file_size = os.path.getsize(file_path)
                total_size += file_size

                file_records.append({
                    'id': str(uuid.uuid4()),
                    'filename': file.filename,
                    'file_path': file_path,
                    'file_size': file_size
                })

        if not file_records:
            shutil.rmtree(temp_dir)
            flash('لم يتم رفع أي ملفات صالحة')
            return redirect(url_for('dashboard'))

        # نقل الملفات إلى المجلد النهائي
        final_dir = os.path.join(UPLOAD_FOLDER, project_id)
        shutil.move(temp_dir, final_dir)

        # تحديث مسارات الملفات
        for record in file_records:
            record['file_path'] = os.path.join(final_dir, record['filename'])

        # حفظ في قاعدة البيانات
        conn.execute('''
            INSERT INTO projects (id, user_id, name, description, file_count, size, project_dir)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (project_id, session['user_id'], project_name, project_description, 
              len(file_records), total_size / 1024 / 1024, final_dir))

        for record in file_records:
            conn.execute('''
                INSERT INTO files (id, project_id, filename, file_path, file_size)
                VALUES (?, ?, ?, ?, ?)
            ''', (record['id'], project_id, record['filename'], record['file_path'], record['file_size']))

        conn.commit()
        conn.close()

        flash('تم رفع المشروع بنجاح!')
        return redirect(url_for('dashboard'))

    except Exception as e:
        if 'temp_dir' in locals() and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        flash('حدث خطأ في رفع المشروع')
        return redirect(url_for('dashboard'))

@app.route('/run/<project_id>', methods=['POST'])
def run_project(project_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'غير مسموح'})

    try:
        conn = get_db_connection()
        project = conn.execute(
            'SELECT * FROM projects WHERE id = ? AND user_id = ?',
            (project_id, session['user_id'])
        ).fetchone()

        if not project:
            return jsonify({'success': False, 'message': 'المشروع غير موجود'})

        if project_id in active_projects:
            return jsonify({'success': False, 'message': 'المشروع يعمل بالفعل'})

        # البحث عن ملف Python للتشغيل
        project_dir = project['project_dir']
        python_files = [f for f in os.listdir(project_dir) if f.endswith('.py')]

        if not python_files:
            return jsonify({'success': False, 'message': 'لا يوجد ملفات Python للتشغيل'})

        # اختيار الملف الرئيسي
        main_file = 'main.py' if 'main.py' in python_files else python_files[0]
        script_path = os.path.join(project_dir, main_file)

        # تشغيل المشروع
        process = run_project_simple(project_id, script_path)

        if process:
            # تحديد وقت الإيقاف التلقائي للمستخدمين غير المشتركين
            auto_stop_time = None
            if not is_user_subscribed(session['user_unique_id']):
                auto_stop_time = datetime.now() + timedelta(days=1, hours=12)

            # تحديث قاعدة البيانات
            conn.execute('''
                UPDATE projects SET status = 'running', pid = ?, last_run = CURRENT_TIMESTAMP, auto_stop_time = ?
                WHERE id = ?
            ''', (process.pid, auto_stop_time, project_id))
            conn.commit()

            conn.close()
            return jsonify({'success': True, 'message': 'تم تشغيل المشروع بنجاح'})
        else:
            conn.close()
            return jsonify({'success': False, 'message': 'فشل في تشغيل المشروع'})

    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ في تشغيل المشروع: {str(e)}'})

@app.route('/stop/<project_id>', methods=['POST'])
def stop_project(project_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'غير مسموح'})

    try:
        conn = get_db_connection()
        project = conn.execute('SELECT * FROM projects WHERE id = ? AND user_id = ?',
            (project_id, session['user_id'])
        ).fetchone()

        if not project:
            return jsonify({'success': False, 'message': 'المشروع غير موجود'})

        if project_id not in active_projects:
            return jsonify({'success': False, 'message': 'المشروع متوقف بالفعل'})

        # إيقاف العملية
        process = active_projects[project_id]['process']
        
        try:
            process.terminate()
            process.wait(timeout=5)
        except:
            try:
                process.kill()
            except:
                pass

        # تحديث قاعدة البيانات
        conn.execute(
            'UPDATE projects SET status = "stopped", pid = NULL WHERE id = ?',
            (project_id,)
        )
        conn.commit()

        # إزالة من المشاريع النشطة
        del active_projects[project_id]

        # إضافة رسالة إيقاف للمخرجات
        if project_id in project_outputs:
            project_outputs[project_id].append("تم إيقاف المشروع بواسطة المستخدم")

        conn.close()
        return jsonify({'success': True, 'message': 'تم إيقاف المشروع بنجاح'})

    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ في إيقاف المشروع: {str(e)}'})

@app.route('/delete/<project_id>', methods=['DELETE'])
def delete_project(project_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'غير مسموح'})

    try:
        conn = get_db_connection()
        project = conn.execute(
            'SELECT * FROM projects WHERE id = ? AND user_id = ?',
            (project_id, session['user_id'])
        ).fetchone()

        if not project:
            return jsonify({'success': False, 'message': 'المشروع غير موجود'})

        # إيقاف العملية إذا كانت تعمل
        if project_id in active_projects:
            try:
                active_projects[project_id]['process'].terminate()
                active_projects[project_id]['process'].kill()
            except:
                pass
            del active_projects[project_id]

        # حذف المخرجات من الذاكرة
        if project_id in project_outputs:
            del project_outputs[project_id]

        # حذف مجلد المشروع
        if project['project_dir'] and os.path.exists(project['project_dir']):
            shutil.rmtree(project['project_dir'])

        # حذف من قاعدة البيانات
        conn.execute('DELETE FROM files WHERE project_id = ?', (project_id,))
        conn.execute('DELETE FROM projects WHERE id = ?', (project_id,))
        conn.commit()

        conn.close()
        return jsonify({'success': True, 'message': 'تم حذف المشروع بنجاح'})

    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ في حذف المشروع: {str(e)}'})

@app.route('/get-output/<project_id>')
def get_output(project_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'غير مسموح'})

    try:
        if project_id in project_outputs:
            return jsonify({
                'success': True,
                'output': project_outputs[project_id][-50:]  # آخر 50 سطر
            })
        else:
            return jsonify({
                'success': True,
                'output': ['لا توجد مخرجات بعد...']
            })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/install-library-simple', methods=['POST'])
def install_library_simple():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'غير مسموح'})

    data = request.get_json()
    library_name = data.get('library_name')
    
    if not library_name:
        return jsonify({'success': False, 'message': 'اسم المكتبة مطلوب'})

    try:
        result = subprocess.run(
            ['pip', 'install', library_name],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode == 0:
            return jsonify({
                'success': True, 
                'message': f'تم تثبيت {library_name} بنجاح',
                'output': result.stdout
            })
        else:
            return jsonify({
                'success': False, 
                'message': f'فشل في تثبيت {library_name}',
                'output': result.stderr
            })
            
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'message': 'انتهت مهلة التثبيت'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ: {str(e)}'})

@app.route('/update-profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'غير مسموح'})

    try:
        data = request.get_json()
        new_name = data.get('name')

        if not new_name:
            return jsonify({'success': False, 'message': 'الاسم مطلوب'})

        conn = get_db_connection()
        conn.execute(
            'UPDATE users SET name = ? WHERE id = ?',
            (new_name, session['user_id'])
        )
        conn.commit()
        conn.close()

        # تحديث الجلسة
        session['user_name'] = new_name

        return jsonify({'success': True, 'message': 'تم تحديث البروفايل بنجاح'})

    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ في تحديث البروفايل: {str(e)}'})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

def start_server():
    """تشغيل الخادم بدون tunnel"""
    print(f"\n{'='*70}")
    print("🚀 مرحباً بك في SayoHosting - منصة الاستضافة الاحترافية")
    print(f"{'='*70}")
    
    print("⚙️ تهيئة قاعدة البيانات والمجلدات...")
    
    # تهيئة قاعدة البيانات والمجلدات
    init_database()
    cleanup_directories()
    
    print("✅ تم تهيئة النظام بنجاح")
    print("🔥 بدء الخادم...")
    
    print("✅ تم بدء الخادم على المنفذ 5000")
    print("📱 الموقع متاح على: http://0.0.0.0:5000")
    print("🌍 للحصول على رابط عام، استخدم Replit Deployment")
    print("🚀 اضغط على زر Deploy في الشريط العلوي")
    
    # تشغيل التطبيق