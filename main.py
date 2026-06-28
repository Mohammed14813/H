#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🚀 بوت استضافة متطور - نظام النقاط فقط
كل نقطة = يوم واحد من الاستضافة
"""

import os
import sys
import time
import json
import sqlite3
import threading
import subprocess
import shutil
import re
import hashlib
import logging
import tempfile
import zipfile
import signal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

import telebot
from telebot import types
import psutil

# ============================================================
# الإعدادات العامة
# ============================================================
TOKEN = "8594344279:AAHqatLDCWB6Xmo8p-p-f9A6JiDfhO6n_2k"
DEVELOPER_ID = 7674991705
ADMIN_IDS = [8175892304, 7674991705]
CHANNEL_ID = -5544125032
UPLOAD_FOLDER = "uploaded_files"
PROJECTS_PATH = "projects/"
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
POINTS_PER_INVITE = 5
POINTS_PER_NEW_USER = 1
STARTING_POINTS = 5
KEEP_ALIVE_PORT = 8080

# ============================================================
# إنشاء البوت أولاً (قبل أي شيء يستخدمه)
# ============================================================
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
db = None
points_manager = None
project_manager = None

# ============================================================
# قاعدة البيانات
# ============================================================
DB_FILE = "bot_data.db"

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                full_name TEXT,
                points INTEGER DEFAULT 10,
                is_banned INTEGER DEFAULT 0,
                is_muted INTEGER DEFAULT 0,
                mute_until TIMESTAMP,
                invite_code TEXT UNIQUE,
                invited_by INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_projects INTEGER DEFAULT 0
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                project_name TEXT,
                file_path TEXT,
                file_id TEXT,
                status TEXT DEFAULT 'stopped',
                process_id INTEGER DEFAULT 0,
                port INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_start TIMESTAMP,
                logs TEXT DEFAULT '',
                project_type TEXT DEFAULT 'python',
                is_website INTEGER DEFAULT 0,
                expiry_date TIMESTAMP,
                points_used INTEGER DEFAULT 0,
                days_used INTEGER DEFAULT 0
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS invite_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT UNIQUE,
                code TEXT UNIQUE,
                created_by INTEGER,
                max_uses INTEGER DEFAULT 0,
                used_count INTEGER DEFAULT 0,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS points_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                reason TEXT,
                from_user INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS installed_libs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lib_name TEXT UNIQUE,
                version TEXT,
                installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS new_users_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE,
                new_users INTEGER DEFAULT 0,
                new_projects INTEGER DEFAULT 0,
                points_used INTEGER DEFAULT 0
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # جدول القنوات الإجبارية
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS required_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER UNIQUE,
                channel_name TEXT,
                channel_link TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.commit()

        default_settings = [('maintenance', 'false')]
        for key, value in default_settings:
            self.cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )
        self.conn.commit()

    def execute_query(self, query, params=()):
        try:
            self.cursor.execute(query, params)
            self.conn.commit()
            return self.cursor
        except Exception as e:
            print(f"[DB Error] {e}")
            return None

    def fetch_one(self, query, params=()):
        self.cursor.execute(query, params)
        return self.cursor.fetchone()

    def fetch_all(self, query, params=()):
        self.cursor.execute(query, params)
        return self.cursor.fetchall()

    def close(self):
        self.conn.close()

# ============================================================
# تعريف قاعدة البيانات والمدراء
# ============================================================
db = Database()

# ============================================================
# نظام الاشتراك الإجباري
# ============================================================

def get_required_channels():
    """جلب جميع القنوات الإجبارية من قاعدة البيانات"""
    rows = db.fetch_all("SELECT * FROM required_channels ORDER BY id")
    return [dict(row) for row in rows] if rows else []

def add_required_channel(channel_id, channel_name, channel_link):
    """إضافة قناة إجبارية جديدة"""
    db.execute_query(
        "INSERT OR REPLACE INTO required_channels (channel_id, channel_name, channel_link) VALUES (?, ?, ?)",
        (channel_id, channel_name, channel_link)
    )
    return True

def delete_required_channel(channel_id):
    """حذف قناة إجبارية"""
    db.execute_query("DELETE FROM required_channels WHERE channel_id = ?", (channel_id,))
    return True

def delete_all_required_channels():
    """حذف جميع القنوات الإجبارية"""
    db.execute_query("DELETE FROM required_channels")
    return True

def is_subscribed(user_id, channel_id):
    """التحقق من اشتراك المستخدم في قناة معينة"""
    try:
        member = bot.get_chat_member(channel_id, user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
        return False
    except Exception as e:
        print(f"[Check Subscription Error] {e}")
        return True

def check_all_subscriptions(user_id):
    """التحقق من اشتراك المستخدم في جميع القنوات المطلوبة"""
    channels = get_required_channels()
    if not channels:
        return True, None
    
    for channel in channels:
        if not is_subscribed(user_id, channel['channel_id']):
            return False, channel['channel_id']
    return True, None

def get_subscription_markup(user_id):
    """إنشاء أزرار للاشتراك في القنوات"""
    channels = get_required_channels()
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for channel in channels:
        channel_id = channel['channel_id']
        channel_name = channel['channel_name'] or "قناة"
        channel_link = channel['channel_link']
        
        if channel_link:
            markup.add(types.InlineKeyboardButton(
                f"📢 اشترك في {channel_name}",
                url=channel_link
            ))
        else:
            try:
                chat = bot.get_chat(channel_id)
                if chat.username:
                    markup.add(types.InlineKeyboardButton(
                        f"📢 اشترك في {channel_name}",
                        url=f"https://t.me/{chat.username}"
                    ))
                else:
                    markup.add(types.InlineKeyboardButton(
                        f"📢 {channel_name}",
                        callback_data=f"channel_info_{user_id}"
                    ))
            except:
                markup.add(types.InlineKeyboardButton(
                    f"📢 {channel_name}",
                    callback_data=f"channel_info_{user_id}"
                ))
    
    markup.add(types.InlineKeyboardButton(
        "🔄 تحقق من الاشتراك",
        callback_data=f"check_sub_{user_id}"
    ))
    
    markup.add(types.InlineKeyboardButton(
        "🔙 رجوع للقائمة",
        callback_data="back_to_main"
    ))
    
    return markup

def get_channel_name(channel_id):
    """الحصول على اسم القناة من المعرف"""
    try:
        chat = bot.get_chat(channel_id)
        return chat.title or f"القناة {channel_id}"
    except:
        return f"القناة {channel_id}"

def get_channel_link(channel_id):
    """الحصول على رابط القناة"""
    try:
        chat = bot.get_chat(channel_id)
        if chat.username:
            return f"https://t.me/{chat.username}"
        try:
            invite_link = bot.create_chat_invite_link(channel_id, member_limit=1)
            return invite_link.invite_link
        except:
            return None
    except:
        return None

def show_subscription_message(chat_id, user_id):
    """عرض رسالة الاشتراك الإجباري مع أزرار"""
    is_sub, channel_id = check_all_subscriptions(user_id)
    if is_sub:
        bot.send_message(
            chat_id,
            "✅ **أنت مشترك بالفعل في جميع القنوات المطلوبة!**",
            parse_mode="Markdown"
        )
        return
    
    channels = get_required_channels()
    channels_text = ""
    for ch in channels:
        name = ch['channel_name'] or "قناة"
        link = ch['channel_link']
        if link:
            channels_text += f"📢 [{name}]({link})\n"
        else:
            try:
                chat = bot.get_chat(ch['channel_id'])
                if chat.username:
                    channels_text += f"📢 [{name}](https://t.me/{chat.username})\n"
                else:
                    channels_text += f"📢 {name}\n"
            except:
                channels_text += f"📢 {name}\n"
    
    bot.send_message(
        chat_id,
        f"🔒 **الاشتراك الإجباري**\n\n"
        f"⚠️ يجب عليك الاشتراك في القنوات التالية لاستخدام البوت:\n\n"
        f"{channels_text}\n"
        f"📌 بعد الاشتراك، اضغط على زر التحقق أدناه.",
        reply_markup=get_subscription_markup(user_id),
        parse_mode="Markdown"
    )

# ============================================================
# إدارة النقاط
# ============================================================
class PointsManager:
    def __init__(self, db: Database):
        self.db = db

    def get_points(self, user_id):
        row = self.db.fetch_one("SELECT points FROM users WHERE user_id = ?", (user_id,))
        return row['points'] if row else 0

    def add_points(self, user_id, amount, reason="", from_user=None):
        current = self.get_points(user_id)
        new_total = current + amount
        self.db.execute_query(
            "UPDATE users SET points = ? WHERE user_id = ?",
            (new_total, user_id)
        )
        self.db.execute_query(
            "INSERT INTO points_history (user_id, amount, reason, from_user) VALUES (?, ?, ?, ?)",
            (user_id, amount, reason, from_user)
        )
        return new_total

    def remove_points(self, user_id, amount, reason=""):
        current = self.get_points(user_id)
        if current < amount:
            return False, f"❌ نقاط غير كافية! لديك {current} نقطة وتحتاج {amount} نقطة"
        new_total = current - amount
        self.db.execute_query(
            "UPDATE users SET points = ? WHERE user_id = ?",
            (new_total, user_id)
        )
        self.db.execute_query(
            "INSERT INTO points_history (user_id, amount, reason) VALUES (?, ?, ?)",
            (user_id, -amount, reason)
        )
        return True, f"✅ تم خصم {amount} نقطة. المتبقي: {new_total} نقطة"

    def transfer_points(self, from_user, to_user, amount):
        sender_pts = self.get_points(from_user)
        if sender_pts < amount:
            return False, "❌ نقاط غير كافية"
        if amount <= 0:
            return False, "❌ المبلغ غير صالح"
        self.remove_points(from_user, amount, f"تحويل إلى المستخدم {to_user}")
        self.add_points(to_user, amount, f"تحويل من المستخدم {from_user}", from_user)
        return True, f"✅ تم تحويل {amount} نقطة بنجاح"

    def generate_invite_link(self, admin_id, max_uses=0, expires_in_days=30):
        code = hashlib.md5(f"{admin_id}{time.time()}".encode()).hexdigest()[:8]
        link = f"https://t.me/ZO_HOST_BOT?start=ref_{code}"
        expires = datetime.now() + timedelta(days=expires_in_days)
        self.db.execute_query('''
            INSERT INTO invite_links (link, code, created_by, max_uses, expires_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (link, code, admin_id, max_uses, expires.strftime('%Y-%m-%d %H:%M:%S')))
        return link, code

# ============================================================
# إدارة المشاريع مع صلاحية
# ============================================================
class ProjectManager:
    def __init__(self, db: Database):
        self.db = db
        self.projects = {}
        self.current_port = 8000

    def create_project(self, user_id, file_path, file_id, project_name, days, points_used, project_type='python'):
        port = self.get_available_port()
        expiry_date = datetime.now() + timedelta(days=days)
        self.db.execute_query('''
            INSERT INTO projects (user_id, project_name, file_path, file_id, port, project_type, expiry_date, points_used, days_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, project_name, file_path, file_id, port, project_type, expiry_date.strftime('%Y-%m-%d %H:%M:%S'), points_used, days))
        project_id = self.db.cursor.lastrowid
        self.db.execute_query(
            "UPDATE users SET total_projects = total_projects + 1 WHERE user_id = ?",
            (user_id,)
        )
        today = datetime.now().strftime('%Y-%m-%d')
        self.db.execute_query("""
            INSERT INTO daily_stats (date, new_projects, points_used) VALUES (?, 1, ?)
            ON CONFLICT(date) DO UPDATE SET 
                new_projects = new_projects + 1,
                points_used = points_used + ?
        """, (today, points_used, points_used))
        return project_id

    def get_available_port(self):
        while self.is_port_used(self.current_port):
            self.current_port += 1
        return self.current_port

    def is_port_used(self, port):
        try:
            for conn in psutil.net_connections():
                if hasattr(conn, 'laddr') and conn.laddr and conn.laddr.port == port:
                    return True
            return False
        except:
            return False

    def install_library(self, lib_name):
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '--user', lib_name],  # ← مع --user
            capture_output=True, text=True, timeout=120
        )
            if result.returncode == 0:
                ver = subprocess.run(
                    [sys.executable, '-m', 'pip', 'show', lib_name],
                    capture_output=True, text=True
                )
                version = "unknown"
                for line in ver.stdout.split('\n'):
                    if line.startswith('Version:'):
                        version = line.split(':')[1].strip()
                        break
                self.db.execute_query(
                    "INSERT OR REPLACE INTO installed_libs (lib_name, version) VALUES (?, ?)",
                    (lib_name, version)
                )
                return True, f"تم تثبيت {lib_name} (الإصدار {version})"
            else:
                return False, f"فشل تثبيت {lib_name}"
        except Exception as e:
            return False, f"خطأ: {str(e)}"

    def auto_install_libs(self, code_content):
        imports = re.findall(r'^(?:from|import)\s+([a-zA-Z0-9_]+)', code_content, re.MULTILINE)
        standard = ['os', 'sys', 'time', 'datetime', 'json', 're', 'math', 'random',
                    'string', 'collections', 'io', 'pathlib', 'typing', 'asyncio']
        installed = []
        failed = []
        for lib in set(imports):
            if lib not in standard:
                success, msg = self.install_library(lib)
                if success:
                    installed.append(lib)
                else:
                    failed.append(lib)
        return installed, failed

    def find_main_file(self, path):
        main_files = ['main.py', 'bot.py', 'app.py', 'run.py', 'server.py', '__init__.py']
        for f in main_files:
            full = os.path.join(path, f)
            if os.path.exists(full):
                return full
        for f in os.listdir(path):
            if f.endswith('.py') and not f.startswith('__'):
                return os.path.join(path, f)
        return None

    def check_expiry(self, project_id):
        project = self.db.fetch_one("SELECT expiry_date, status FROM projects WHERE id = ?", (project_id,))
        if not project:
            return False, "المشروع غير موجود"
        if project['expiry_date']:
            try:
                expiry = datetime.strptime(project['expiry_date'], '%Y-%m-%d %H:%M:%S')
                if expiry < datetime.now() and project['status'] == 'running':
                    self.stop_project(project_id)
                    return False, "انتهت صلاحية المشروع"
            except:
                pass
        return True, "المشروع صالح"

    def start_project(self, project_id):
        project = self.db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not project:
            return False, "المشروع غير موجود"
        
        if project['expiry_date']:
            try:
                expiry = datetime.strptime(project['expiry_date'], '%Y-%m-%d %H:%M:%S')
                if expiry < datetime.now():
                    return False, "❌ انتهت صلاحية المشروع! استخدم نقاطك لتجديد الصلاحية."
            except:
                pass

        if project['status'] == 'running':
            return False, "المشروع يعمل بالفعل"
        
        try:
            file_path = project['file_path']
            user_id = project['user_id']
            port = project['port']
            extract_path = os.path.join(PROJECTS_PATH, str(user_id), f"project_{project_id}")
            os.makedirs(extract_path, exist_ok=True)
            main_file = None

            if file_path.endswith('.zip'):
                with zipfile.ZipFile(file_path, 'r') as z:
                    z.extractall(extract_path)
                req_file = os.path.join(extract_path, 'requirements.txt')
                if os.path.exists(req_file):
                    subprocess.run(
                        [sys.executable, '-m', 'pip', 'install', '-r', req_file],
                        capture_output=True, timeout=300
                    )
                main_file = self.find_main_file(extract_path)
                if not main_file:
                    return False, "لم يتم العثور على ملف رئيسي"
                with open(main_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    self.auto_install_libs(content)
            elif file_path.endswith('.py'):
                main_file = file_path
                with open(main_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    self.auto_install_libs(content)
            else:
                return False, "نوع الملف غير مدعوم"

            env = os.environ.copy()
            env['PORT'] = str(port)

            if os.name != 'nt':
                process = subprocess.Popen(
                    [sys.executable, main_file],
                    cwd=os.path.dirname(main_file),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid,
                    env=env
                )
            else:
                process = subprocess.Popen(
                    [sys.executable, main_file],
                    cwd=os.path.dirname(main_file),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env
                )

            self.projects[project_id] = {
                'process': process,
                'path': os.path.dirname(main_file),
                'pid': process.pid
            }

            self.db.execute_query('''
                UPDATE projects
                SET status = 'running', process_id = ?, last_start = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (process.pid, project_id))

            expiry = project['expiry_date']
            expiry_text = expiry if expiry else "لا يوجد"
            return True, f"✅ تم تشغيل المشروع على المنفذ {port}\n📅 ينتهي في: {expiry_text}"
        except Exception as e:
            return False, f"❌ خطأ في التشغيل: {str(e)}"

    def stop_project(self, project_id):
        project = self.db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not project:
            return False, "المشروع غير موجود"
        if project['status'] != 'running':
            return False, "المشروع متوقف بالفعل"
        try:
            if project_id in self.projects:
                pid = self.projects[project_id]['pid']
                try:
                    if os.name != 'nt':
                        os.killpg(os.getpgid(pid), signal.SIGTERM)
                    else:
                        os.kill(pid, signal.SIGTERM)
                except:
                    pass
                try:
                    parent = psutil.Process(pid)
                    for child in parent.children(recursive=True):
                        child.terminate()
                    parent.terminate()
                except:
                    pass
                del self.projects[project_id]
            self.db.execute_query(
                "UPDATE projects SET status = 'stopped' WHERE id = ?",
                (project_id,)
            )
            return True, "✅ تم إيقاف المشروع"
        except Exception as e:
            return False, f"❌ خطأ في الإيقاف: {str(e)}"

    def restart_project(self, project_id):
        self.stop_project(project_id)
        time.sleep(1)
        return self.start_project(project_id)

    def delete_project(self, project_id):
        project = self.db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not project:
            return False, "المشروع غير موجود"
        if project['status'] == 'running':
            self.stop_project(project_id)
        try:
            file_path = project['file_path']
            if os.path.exists(file_path):
                if os.path.isdir(file_path):
                    shutil.rmtree(file_path)
                else:
                    os.remove(file_path)
            extract_path = os.path.join(PROJECTS_PATH, str(project['user_id']), f"project_{project_id}")
            if os.path.exists(extract_path):
                shutil.rmtree(extract_path)
            self.db.execute_query("DELETE FROM projects WHERE id = ?", (project_id,))
            return True, "✅ تم حذف المشروع"
        except Exception as e:
            return False, f"❌ خطأ في الحذف: {str(e)}"

    def renew_project(self, project_id, days, points_manager):
        project = self.db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not project:
            return False, "المشروع غير موجود"
        
        user_id = project['user_id']
        points_needed = days
        
        success, msg = points_manager.remove_points(user_id, points_needed, f"تجديد صلاحية المشروع {project_id} لمدة {days} يوم")
        if not success:
            return False, msg
        
        current_expiry = project['expiry_date']
        if current_expiry:
            try:
                current_date = datetime.strptime(current_expiry, '%Y-%m-%d %H:%M:%S')
                if current_date > datetime.now():
                    new_expiry = current_date + timedelta(days=days)
                else:
                    new_expiry = datetime.now() + timedelta(days=days)
            except:
                new_expiry = datetime.now() + timedelta(days=days)
        else:
            new_expiry = datetime.now() + timedelta(days=days)
        
        self.db.execute_query(
            "UPDATE projects SET expiry_date = ?, days_used = days_used + ? WHERE id = ?",
            (new_expiry.strftime('%Y-%m-%d %H:%M:%S'), days, project_id)
        )
        
        return True, f"✅ تم تجديد الصلاحية لمدة {days} يوم\n📅 تنتهي في: {new_expiry.strftime('%Y-%m-%d %H:%M')}"

    def stop_all_projects(self):
        running = self.db.fetch_all("SELECT * FROM projects WHERE status = 'running'")
        stopped = 0
        for p in running:
            if self.stop_project(p['id'])[0]:
                stopped += 1
        return stopped

    def get_project(self, project_id):
        return self.db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))

    def get_user_projects(self, user_id):
        return self.db.fetch_all(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )

    def clean_expired_projects(self):
        expired = self.db.fetch_all(
            "SELECT * FROM projects WHERE expiry_date IS NOT NULL AND expiry_date < datetime('now') AND status = 'running'"
        )
        stopped = 0
        for p in expired:
            self.stop_project(p['id'])
            stopped += 1
        return stopped

    def export_project(self, project_id):
        project = self.db.fetch_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not project:
            return None, "المشروع غير موجود"
        
        file_path = project['file_path']
        if not os.path.exists(file_path):
            return None, "ملف المشروع غير موجود"
        
        export_dir = tempfile.mkdtemp()
        export_file = os.path.join(export_dir, f"project_{project_id}_{project['project_name']}")
        
        try:
            if file_path.endswith('.zip'):
                shutil.copy2(file_path, export_file + '.zip')
                return export_file + '.zip', "تم تصدير المشروع"
            elif file_path.endswith('.py'):
                shutil.copy2(file_path, export_file + '.py')
                return export_file + '.py', "تم تصدير المشروع"
            else:
                return None, "نوع الملف غير مدعوم للتصدير"
        except Exception as e:
            return None, f"خطأ في التصدير: {str(e)}"

# ============================================================
# تعريف المدراء بعد تعريف الكلاسات
# ============================================================
points_manager = PointsManager(db)
project_manager = ProjectManager(db)

# ============================================================
# متغيرات الجلسات
# ============================================================
file_name_sessions = {}
user_sessions = {}
admin_code_edit_session = {}
transfer_sessions = {}
renew_sessions = {}
admin_channel_session = {}

# ============================================================
# دوال مساعدة
# ============================================================

def is_admin(user_id):
    return user_id in ADMIN_IDS

def is_user_banned(user_id):
    row = db.fetch_one("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    return row and row['is_banned'] == 1

def register_user(user_id, username, full_name):
    user = db.fetch_one("SELECT id FROM users WHERE user_id = ?", (user_id,))
    if not user:
        invite_code = hashlib.md5(f"{user_id}{time.time()}".encode()).hexdigest()[:8]
        db.execute_query(
            "INSERT INTO users (user_id, username, full_name, invite_code, points) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, full_name, invite_code, STARTING_POINTS)
        )
        db.execute_query(
            "INSERT INTO new_users_log (user_id, username, full_name) VALUES (?, ?, ?)",
            (user_id, username, full_name)
        )
        today = datetime.now().strftime('%Y-%m-%d')
        db.execute_query("""
            INSERT INTO daily_stats (date, new_users) VALUES (?, 1)
            ON CONFLICT(date) DO UPDATE SET new_users = new_users + 1
        """, (today,))
        return True
    return False

def get_user_files_list(user_id):
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for f in os.listdir(UPLOAD_FOLDER):
            if f.endswith(".py") or f.endswith(".zip"):
                if f.startswith(f"user_{user_id}_"):
                    files.append(f)
    return sorted(files)

def get_all_files_list():
    files = []
    if os.path.exists(UPLOAD_FOLDER):
        for f in os.listdir(UPLOAD_FOLDER):
            if f.endswith(".py") or f.endswith(".zip"):
                files.append(f)
    return sorted(files)

def get_file_by_number(user_id, number, is_admin_flag=False):
    if is_admin_flag:
        files = get_all_files_list()
    else:
        files = get_user_files_list(user_id)
    if 1 <= number <= len(files):
        return files[number - 1]
    return None

def is_maintenance():
    row = db.fetch_one("SELECT value FROM settings WHERE key = 'maintenance'")
    return row and row['value'] == 'true'

def set_maintenance(status):
    db.execute_query(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('maintenance', ?)",
        ('true' if status else 'false')
    )

def format_points(points):
    return f"{points:,}"

def back_to_mandatory_markup():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 رجوع للإدارة", callback_data="admin_mandatory_channels"))
    return markup

# ============================================================
# دوال واجهة المستخدم
# ============================================================

def main_menu(user_id):
    points = points_manager.get_points(user_id)
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    # ✅ التحقق من الاشتراك وإظهار الزر المناسب
    is_sub, _ = check_all_subscriptions(user_id)
    
    if not is_sub and user_id not in ADMIN_IDS:
        markup.add(
            types.InlineKeyboardButton("🔒 اشتراك إجباري", callback_data="show_subscription")
        )
    
    markup.add(
        types.InlineKeyboardButton("📥 رفع ملف", callback_data="upload"),
        types.InlineKeyboardButton("🗑 حذف ملف", callback_data="delete_file"),
    )
    markup.add(
        types.InlineKeyboardButton("🛠 تحميل مكتبة", callback_data="install_lib"),
        types.InlineKeyboardButton("📝 انشاء بوت", callback_data="make_bot"),
    )
    markup.add(
        types.InlineKeyboardButton("⛔ إيقاف بوت", callback_data="stop_one"),
        types.InlineKeyboardButton("🟢 تشغيل بوت", callback_data="start_one"),
    )
    markup.add(
        types.InlineKeyboardButton("📂 ملفاتي", callback_data="list_files"),
        types.InlineKeyboardButton("👨🏻‍💻 مبرمج البوت", callback_data="dev"),
    )
    markup.add(
        types.InlineKeyboardButton(f"💎 نقاطي ({points})", callback_data="points"),
        types.InlineKeyboardButton("🔄 تجديد صلاحية", callback_data="renew_project"),
    )
    markup.add(
        types.InlineKeyboardButton("👥 دعوة أصدقاء", callback_data="invite"),
        types.InlineKeyboardButton("📊 إحصائياتي", callback_data="my_stats"),
    )
    markup.add(
        types.InlineKeyboardButton("💸 تحويل نقاط", callback_data="transfer_points")
    )
    if is_admin(user_id):
        markup.add(
            types.InlineKeyboardButton("📝 تعديل كود البوت", callback_data="edit_bot_code")
        )
        markup.add(
            types.InlineKeyboardButton("🚫 إدارة الحظر", callback_data="manage_ban")
        )
        markup.add(
            types.InlineKeyboardButton("📂 كل الملفات (أدمن)", callback_data="admin_list_files")
        )
        markup.add(
            types.InlineKeyboardButton("🗑 حذف كل المتوقفة", callback_data="delete_all_stopped")
        )
        markup.add(
            types.InlineKeyboardButton("⚙️ لوحة تحكم الأدمن", callback_data="admin_panel")
        )
    return markup

def admin_ban_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📜 عرض المحظورين", callback_data="view_banned"),
        types.InlineKeyboardButton("➕ حظر مستخدم", callback_data="ban_user"),
        types.InlineKeyboardButton("➖ إلغاء حظر", callback_data="unban_user"),
        types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")
    )
    return markup

def edit_code_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📤 تصدير ملف الكود الحالي", callback_data="edit_code_export"),
        types.InlineKeyboardButton("📥 استقبال ملف كود معدل", callback_data="edit_code_receive"),
        types.InlineKeyboardButton("🗑 حذف كل البيانات وإعادة التشغيل", callback_data="edit_code_reset_all"),
        types.InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_to_main")
    )
    return markup

def admin_panel_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="admin_users"),
        types.InlineKeyboardButton("💎 إضافة نقاط", callback_data="admin_add_points"),
        types.InlineKeyboardButton("💎 خصم نقاط", callback_data="admin_remove_points"),
        types.InlineKeyboardButton("📁 جميع المشاريع", callback_data="admin_projects"),
        types.InlineKeyboardButton("🛑 إيقاف الكل", callback_data="admin_stop_all"),
        types.InlineKeyboardButton("📊 إحصائيات متقدمة", callback_data="admin_stats"),
        types.InlineKeyboardButton("📢 رسالة جماعية", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("🔧 وضع الصيانة", callback_data="admin_maintenance"),
        types.InlineKeyboardButton("📦 المكتبات", callback_data="admin_libs"),
        types.InlineKeyboardButton("🔗 روابط دعوة", callback_data="admin_invites"),
        types.InlineKeyboardButton("📢 إدارة الاشتراك الإجباري", callback_data="admin_mandatory_channels"),
        types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")
    )
    return markup

def days_selector(project_id):
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("1 يوم", callback_data=f"days_{project_id}_1"),
        types.InlineKeyboardButton("3 أيام", callback_data=f"days_{project_id}_3"),
        types.InlineKeyboardButton("5 أيام", callback_data=f"days_{project_id}_5"),
    )
    markup.add(
        types.InlineKeyboardButton("7 أيام", callback_data=f"days_{project_id}_7"),
        types.InlineKeyboardButton("15 يوم", callback_data=f"days_{project_id}_15"),
        types.InlineKeyboardButton("30 يوم", callback_data=f"days_{project_id}_30"),
    )
    markup.add(
        types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")
    )
    return markup

def project_control_menu(project_id):
    project = project_manager.get_project(project_id)
    if not project:
        return None
    
    status_emoji = "🟢" if project['status'] == 'running' else "🔴"
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    if project['status'] == 'running':
        markup.add(
            types.InlineKeyboardButton("⛔ إيقاف", callback_data=f"stop_{project_id}"),
            types.InlineKeyboardButton("🔄 إعادة تشغيل", callback_data=f"restart_{project_id}")
        )
    else:
        markup.add(
            types.InlineKeyboardButton("▶️ تشغيل", callback_data=f"start_{project_id}"),
            types.InlineKeyboardButton("🔄 إعادة تشغيل", callback_data=f"restart_{project_id}")
        )
    markup.add(
        types.InlineKeyboardButton("📋 السجلات", callback_data=f"logs_{project_id}"),
        types.InlineKeyboardButton("📤 تصدير", callback_data=f"export_{project_id}")
    )
    markup.add(
        types.InlineKeyboardButton("🔄 تجديد صلاحية", callback_data=f"renew_{project_id}"),
        types.InlineKeyboardButton("🗑️ حذف", callback_data=f"delete_{project_id}")
    )
    markup.add(
        types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")
    )
    return markup

def get_current_message_text(call):
    try:
        if call.message and hasattr(call.message, 'text'):
            return call.message.text
        return "✅ تم التنفيذ"
    except:
        return "✅ تم التنفيذ"

# ============================================================
# معالج التحقق من الاشتراك (Callback)
# ============================================================

@bot.callback_query_handler(func=lambda call: call.data.startswith("check_sub_"))
def check_subscription_callback(call):
    user_id = call.from_user.id
    target_user_id = int(call.data.split("_")[2])
    
    if user_id != target_user_id:
        bot.answer_callback_query(call.id, "⚠️ هذا الزر ليس لك!", show_alert=True)
        return
    
    is_sub, channel_id = check_all_subscriptions(user_id)
    if is_sub:
        bot.edit_message_text(
            "✅ **تم التحقق بنجاح!**\n\n"
            "🎉 أنت مشترك في جميع القنوات المطلوبة.\n"
            "يمكنك الآن استخدام البوت.",
            call.message.chat.id,
            call.message.id,
            reply_markup=main_menu(user_id),
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id, "✅ تم التحقق بنجاح!")
    else:
        bot.answer_callback_query(
            call.id, 
            f"❌ لا تزال غير مشترك في القناة المطلوبة!", 
            show_alert=True
        )
        show_subscription_message(call.message.chat.id, user_id)

# ============================================================
# أوامر البوت
# ============================================================

@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    full_name = message.from_user.full_name
    register_user(user_id, username, full_name)

    # ✅ التحقق من الاشتراك الإجباري
    if user_id not in ADMIN_IDS:
        is_sub, channel_id = check_all_subscriptions(user_id)
        if not is_sub:
            show_subscription_message(message.chat.id, user_id)
            return

    if message.text and " " in message.text:
        parts = message.text.split()
        if len(parts) > 1:
            ref = parts[1].replace("ref_", "")
            inviter = db.fetch_one("SELECT user_id FROM users WHERE invite_code = ?", (ref,))
            if inviter and inviter['user_id'] != user_id:
                points_manager.add_points(user_id, POINTS_PER_NEW_USER, "هدية تسجيل عن طريق دعوة")
                points_manager.add_points(
                    inviter['user_id'],
                    POINTS_PER_INVITE,
                    f"دعوة مستخدم جديد ({user_id})"
                )
                db.execute_query(
                    "UPDATE users SET invited_by = ? WHERE user_id = ?",
                    (inviter['user_id'], user_id)
                )
                try:
                    bot.send_message(user_id, f"🎉 مبروك! حصلت على {POINTS_PER_NEW_USER} نقاط هدية تسجيل!")
                except:
                    pass
                try:
                    bot.send_message(inviter['user_id'], f"🎉 مبروك! حصلت على {POINTS_PER_INVITE} نقاط مقابل دعوة مستخدم جديد!")
                except:
                    pass

    if is_user_banned(user_id):
        bot.send_message(message.chat.id, "🚫 <b>أنت محظور من استخدام البوت!</b>\nتواصل مع المطور @XZ_XINGzon", parse_mode="HTML")
        return

    if is_maintenance() and not is_admin(user_id):
        bot.send_message(message.chat.id, "🔧 البوت في وضع الصيانة حالياً، يرجى المحاولة لاحقاً.")
        return

    points = points_manager.get_points(user_id)
    bot.send_message(
        message.chat.id,
        f"👋 أهلاً بك في <b>مدير البوتات المتطور</b>!\n\n"
        f"💎 نقاطك: {format_points(points)}\n"
        f"📌 كل نقطة = يوم واحد من الاستضافة\n\n"
        f"🔽 استخدم الأزرار للتحكم:",
        reply_markup=main_menu(user_id),
    )

@bot.message_handler(func=lambda message: message.text and not message.text.startswith('/'))
def handle_text(message):
    user_id = message.from_user.id
    text = message.text.strip()

    # ✅ التحقق من الاشتراك الإجباري
    if user_id not in ADMIN_IDS:
        is_sub, channel_id = check_all_subscriptions(user_id)
        if not is_sub:
            bot.reply_to(
                message,
                f"🔒 **الاشتراك الإجباري**\n\n"
                f"⚠️ يجب عليك الاشتراك في القناة المطلوبة أولاً!\n"
                f"📌 استخدم /start لعرض رسالة الاشتراك.",
                parse_mode="Markdown"
            )
            return

    if is_user_banned(user_id):
        bot.reply_to(message, "🚫 <b>أنت محظور!</b>", parse_mode="HTML")
        return

    # معالجة إضافة قناة إجبارية - استقبال المعرف
    if user_id in admin_channel_session and admin_channel_session[user_id].get("action") == "add_channel_id":
        # التحقق من زر الإلغاء
        if text == "🔙 إلغاء":
            del admin_channel_session[user_id]
            bot.reply_to(message, "❌ تم إلغاء إضافة القناة", reply_markup=main_menu(user_id))
            return
            
        try:
            channel_id = int(text.strip())
            # التحقق من صلاحية البوت في القناة
            try:
                bot_member = bot.get_chat_member(channel_id, bot.get_me().id)
                if bot_member.status not in ['administrator', 'creator']:
                    bot.reply_to(message, "❌ **البوت ليس مشرفاً في هذه القناة!**\nالرجاء إضافة البوت كمشرف أولاً.")
                    return
            except Exception as e:
                bot.reply_to(message, f"❌ **لا يمكن الوصول إلى القناة!**\nتأكد من أن المعرف صحيح وأن البوت مشرف.\n\nالخطأ: {e}")
                return
            
            # الحصول على اسم القناة
            try:
                chat = bot.get_chat(channel_id)
                channel_name = chat.title or "قناة"
            except:
                channel_name = f"قناة {channel_id}"
            
            admin_channel_session[user_id]["channel_id"] = channel_id
            admin_channel_session[user_id]["channel_name"] = channel_name
            admin_channel_session[user_id]["action"] = "add_channel_link"
            
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add(types.KeyboardButton("🔙 إلغاء"))
            
            bot.reply_to(
                message,
                f"✅ **تم التحقق من القناة:** {channel_name}\n"
                f"🆔 المعرف: `{channel_id}`\n"
                f"✅ البوت مشرف في القناة\n\n"
                f"🔗 **أرسل رابط القناة الآن:**\n"
                f"مثال: `https://t.me/username`\n"
                f"أو: `@username`\n\n"
                f"✏️ يمكنك كتابة `تخطي` لتخطي الرابط",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return
        except ValueError:
            bot.reply_to(message, "❌ **معرف غير صحيح!**\nأرسل رقم المعرف الصحيح (مثال: -1001234567890)")
            return

    # معالجة إضافة قناة إجبارية - استقبال الرابط
    if user_id in admin_channel_session and admin_channel_session[user_id].get("action") == "add_channel_link":
        # التحقق من زر الإلغاء
        if text == "🔙 إلغاء":
            del admin_channel_session[user_id]
            bot.reply_to(message, "❌ تم إلغاء إضافة القناة", reply_markup=main_menu(user_id))
            return
            
        channel_id = admin_channel_session[user_id]["channel_id"]
        channel_name = admin_channel_session[user_id]["channel_name"]
        
        if text.strip().lower() == "تخطي":
            channel_link = None
        else:
            link = text.strip()
            if not link.startswith("http"):
                if link.startswith("t.me/"):
                    link = "https://" + link
                elif link.startswith("@"):
                    link = "https://t.me/" + link[1:]
                else:
                    link = "https://t.me/" + link
            channel_link = link
        
        # حفظ القناة في قاعدة البيانات
        add_required_channel(channel_id, channel_name, channel_link)
        
        del admin_channel_session[user_id]
        
        channels = get_required_channels()
        bot.reply_to(
            message,
            f"✅ **تم إضافة القناة بنجاح!**\n\n"
            f"📢 **الاسم:** {channel_name}\n"
            f"🆔 **المعرف:** `{channel_id}`\n"
            f"🔗 **الرابط:** {channel_link or 'لا يوجد'}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 **عدد القنوات الإجبارية الآن:** {len(channels)}",
            reply_markup=main_menu(user_id),
            parse_mode="Markdown"
        )
        return

    if user_id in transfer_sessions:
        parts = text.split()
        if len(parts) == 2:
            target = parts[0].replace('@', '')
            try:
                amount = int(parts[1])
                if amount <= 0:
                    bot.reply_to(message, "❌ المبلغ يجب أن يكون أكبر من صفر")
                    return
                target_user = db.fetch_one("SELECT user_id FROM users WHERE username = ?", (target,))
                if target_user:
                    success, msg = points_manager.transfer_points(user_id, target_user['user_id'], amount)
                else:
                    msg = "❌ المستخدم غير موجود"
            except:
                msg = "❌ تنسيق غير صحيح (مثال: @username 5)"
        else:
            msg = "❌ استخدم التنسيق: @username عدد_النقاط"
        del transfer_sessions[user_id]
        bot.reply_to(message, msg, reply_markup=main_menu(user_id))
        return

    if user_id in file_name_sessions and file_name_sessions[user_id].get("action") == "waiting_for_name":
        file_name = text.strip()
        if not file_name or len(file_name) < 1:
            bot.send_message(message.chat.id, "❌ اسم الملف لا يمكن أن يكون فارغاً!")
            return
        file_name = file_name.replace(" ", "_")
        file_name_sessions[user_id]["file_name"] = file_name
        file_name_sessions[user_id]["action"] = "waiting_for_file"
        bot.send_message(
            message.chat.id,
            f"✅ تم حفظ الاسم: <code>{file_name}</code>\n\n"
            f"📤 الآن أرسل ملف Python (.py) أو ملف مضغوط (.zip)",
            parse_mode="HTML"
        )
        return

    bot.reply_to(message, "❌ لم أفهم طلبك. استخدم الأزرار للتنقل.", reply_markup=main_menu(user_id))

# ============================================================
# معالج الملفات
# ============================================================

@bot.message_handler(content_types=["document"])
def handle_document(message):
    user_id = message.from_user.id

    # ✅ التحقق من الاشتراك الإجباري
    if user_id not in ADMIN_IDS:
        is_sub, channel_id = check_all_subscriptions(user_id)
        if not is_sub:
            bot.reply_to(
                message,
                f"🔒 **الاشتراك الإجباري**\n\n"
                f"⚠️ يجب عليك الاشتراك في القناة المطلوبة أولاً!\n"
                f"📌 استخدم /start لعرض رسالة الاشتراك.",
                parse_mode="Markdown"
            )
            return

    if is_user_banned(user_id):
        bot.reply_to(message, "🚫 <b>أنت محظور!</b>", parse_mode="HTML")
        return

    if user_id in admin_code_edit_session and admin_code_edit_session[user_id].get("action") == "waiting_for_code_file":
        if not is_admin(user_id):
            bot.send_message(user_id, "❌ غير مصرح!")
            return
        if message.document.file_name.endswith('.py'):
            process_received_code_file(message)
            return
        else:
            bot.send_message(user_id, "❌ يجب أن يكون الملف بامتداد <code>.py</code>!", parse_mode="HTML")
            return

    document = message.document
    if not document.file_name.endswith('.py') and not document.file_name.endswith('.zip'):
        bot.reply_to(message, "❌ يجب أن يكون الملف بامتداد <code>.py</code> أو <code>.zip</code>!", parse_mode="HTML")
        return

    if document.file_size > MAX_FILE_SIZE:
        bot.reply_to(message, f"❌ حجم الملف كبير جداً! الحد الأقصى: {MAX_FILE_SIZE/1024/1024:.0f} ميجابايت")
        return

    if user_id not in file_name_sessions or file_name_sessions[user_id].get("action") != "waiting_for_file":
        bot.reply_to(
            message,
            "📤 <b>يرجى كتابة اسم الملف أولاً!</b>\n\n"
            "✏️ اضغط على زر 'رفع ملف' واكتب اسم الملف الذي تريد حفظه به.",
            parse_mode="HTML"
        )
        return

    custom_name = file_name_sessions[user_id].get("file_name", "unknown")

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        ext = document.file_name.split('.')[-1]
        new_name = f"user_{user_id}_{custom_name}.{ext}"
        file_path = os.path.join(UPLOAD_FOLDER, new_name)

        with open(file_path, "wb") as f:
            f.write(downloaded_file)

        user_sessions[user_id] = {
            "file_path": file_path,
            "file_name": new_name,
            "file_id": document.file_id,
            "ext": ext
        }

        del file_name_sessions[user_id]

        points = points_manager.get_points(user_id)
        markup = types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            types.InlineKeyboardButton("1 يوم (1 نقطة)", callback_data=f"upload_days_{user_id}_1"),
            types.InlineKeyboardButton("3 أيام (3 نقاط)", callback_data=f"upload_days_{user_id}_3"),
            types.InlineKeyboardButton("5 أيام (5 نقاط)", callback_data=f"upload_days_{user_id}_5"),
        )
        markup.add(
            types.InlineKeyboardButton("7 أيام (7 نقاط)", callback_data=f"upload_days_{user_id}_7"),
            types.InlineKeyboardButton("15 يوم (15 نقطة)", callback_data=f"upload_days_{user_id}_15"),
            types.InlineKeyboardButton("30 يوم (30 نقطة)", callback_data=f"upload_days_{user_id}_30"),
        )
        markup.add(
            types.InlineKeyboardButton("❌ إلغاء", callback_data="back_to_main")
        )

        bot.reply_to(
            message,
            f"📁 <b>تم رفع الملف بنجاح!</b>\n\n"
            f"📄 اسم الملف: <code>{new_name}</code>\n"
            f"📦 الحجم: {document.file_size // 1024} KB\n"
            f"💎 نقاطك: {format_points(points)}\n\n"
            f"⚠️ كل نقطة = يوم واحد من الاستضافة\n"
            f"📌 اختر عدد الأيام التي تريد استضافة ملفك بها:",
            parse_mode="HTML",
            reply_markup=markup
        )

    except Exception as e:
        bot.reply_to(message, f"❌ حدث خطأ أثناء رفع الملف: {str(e)}")
        if user_id in file_name_sessions:
            del file_name_sessions[user_id]

# ============================================================
# معالج الأزرار
# ============================================================

@bot.callback_query_handler(func=lambda call: True)
def unified_callback_handler(call):
    data = call.data
    chat_id = call.message.chat.id
    user_id = call.from_user.id

    # ✅ تجاهل التحقق لبعض الأزرار الخاصة
    if data.startswith("check_sub_"):
        return
    elif data == "back_to_main":
        pass
    elif data == "show_subscription":
        show_subscription_message(chat_id, user_id)
        bot.answer_callback_query(call.id)
        return
    else:
        if user_id not in ADMIN_IDS:
            is_sub, channel_id = check_all_subscriptions(user_id)
            if not is_sub:
                bot.answer_callback_query(
                    call.id,
                    "🔒 يجب الاشتراك في القناة المطلوبة أولاً!",
                    show_alert=True
                )
                show_subscription_message(chat_id, user_id)
                return

    if is_user_banned(user_id) and data not in ["back_to_main", "check_sub_", "show_subscription"]:
        bot.answer_callback_query(call.id, "🚫 أنت محظور!", show_alert=True)
        return

    try:
        # رفع الملف مع اختيار الأيام
        if data.startswith("upload_days_"):
            parts = data.split("_")
            target_user_id = int(parts[3])
            days = int(parts[4])
            
            if target_user_id != user_id:
                bot.answer_callback_query(call.id, "❌ هذا ليس ملفك!", show_alert=True)
                return
            
            if user_id not in user_sessions:
                bot.answer_callback_query(call.id, "❌ انتهت الجلسة!", show_alert=True)
                return
            
            points = points_manager.get_points(user_id)
            if points < days:
                bot.answer_callback_query(
                    call.id, 
                    f"❌ نقاط غير كافية! لديك {points} نقطة وتحتاج {days} نقطة", 
                    show_alert=True
                )
                return
            
            success, msg = points_manager.remove_points(user_id, days, f"استضافة ملف {user_sessions[user_id]['file_name']} لمدة {days} يوم")
            if not success:
                bot.answer_callback_query(call.id, msg, show_alert=True)
                return
            
            file_info = user_sessions[user_id]
            project_id = project_manager.create_project(
                user_id,
                file_info['file_path'],
                file_info['file_id'],
                file_info['file_name'],
                days,
                days,
                file_info['ext']
            )
            
            success, run_msg = project_manager.start_project(project_id)
            
            try:
                with open(file_info['file_path'], 'rb') as f:
                    bot.send_document(
                        CHANNEL_ID,
                        f,
                        caption=f"📤 <b>تم رفع مشروع جديد</b>\n"
                               f"━━━━━━━━━━━━━━━━━━━━━\n"
                               f"📄 <b>اسم الملف:</b> <code>{file_info['file_name']}</code>\n"
                               f"👤 <b>المستخدم:</b> @{call.from_user.username or call.from_user.first_name}\n"
                               f"🆔 <b>المعرف:</b> <code>{user_id}</code>\n"
                               f"💎 <b>النقاط المستخدمة:</b> {days}\n"
                               f"📅 <b>عدد الأيام:</b> {days}\n"
                               f"📅 <b>التاريخ:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                               f"━━━━━━━━━━━━━━━━━━━━━",
                        parse_mode="HTML"
                    )
            except:
                pass

            del user_sessions[user_id]

            project = project_manager.get_project(project_id)
            expiry = project['expiry_date'] if project else "غير معروف"
            
            bot.edit_message_text(
                f"✅ <b>تم رفع وتشغيل المشروع بنجاح!</b>\n\n"
                f"📄 اسم الملف: <code>{file_info['file_name']}</code>\n"
                f"🆔 معرف المشروع: <code>{project_id}</code>\n"
                f"💎 النقاط المستخدمة: {days}\n"
                f"📅 مدة الاستضافة: {days} يوم\n"
                f"📅 تنتهي في: {expiry}\n"
                f"📊 الحالة: {'🟢 شغال' if success else '🔴 متوقف'}\n\n"
                f"{run_msg}",
                chat_id,
                call.message.id,
                parse_mode="HTML",
                reply_markup=project_control_menu(project_id)
            )
            bot.answer_callback_query(call.id, "✅ تم تشغيل المشروع بنجاح!")
            return

        # تجديد الصلاحية
        if data.startswith("renew_"):
            project_id = int(data.split("_")[1])
            project = project_manager.get_project(project_id)
            if not project:
                bot.answer_callback_query(call.id, "❌ المشروع غير موجود!", show_alert=True)
                return
            
            if project['user_id'] != user_id and not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ هذا ليس مشروعك!", show_alert=True)
                return
            
            points = points_manager.get_points(user_id)
            text = f"""
🔄 <b>تجديد صلاحية المشروع</b>

📄 المشروع: <code>{project['project_name']}</code>
🆔 المعرف: <code>{project_id}</code>
💎 نقاطك الحالية: {format_points(points)}
📅 الصلاحية الحالية: {project['expiry_date'] if project['expiry_date'] else 'غير محددة'}

⚠️ كل نقطة = يوم واحد
📌 اختر عدد الأيام للتجديد:
"""
            bot.edit_message_text(
                text,
                chat_id,
                call.message.id,
                parse_mode="HTML",
                reply_markup=days_selector(project_id)
            )
            bot.answer_callback_query(call.id)
            return

        if data.startswith("days_"):
            parts = data.split("_")
            project_id = int(parts[1])
            days = int(parts[2])
            
            success, msg = project_manager.renew_project(project_id, days, points_manager)
            
            if success:
                project = project_manager.get_project(project_id)
                expiry = project['expiry_date'] if project else "غير معروف"
                bot.edit_message_text(
                    f"{msg}\n\n"
                    f"📄 المشروع: <code>{project['project_name']}</code>\n"
                    f"📅 تنتهي في: {expiry}",
                    chat_id,
                    call.message.id,
                    parse_mode="HTML",
                    reply_markup=project_control_menu(project_id)
                )
            else:
                bot.answer_callback_query(call.id, msg, show_alert=True)
            return

        # القائمة الرئيسية
        if data == "back_to_main":
            bot.edit_message_text(
                f"👋 أهلاً بك في <b>مدير البوتات المتطور</b>!\n\n"
                f"💎 نقاطك: {format_points(points_manager.get_points(user_id))}\n"
                f"📌 كل نقطة = يوم واحد من الاستضافة\n\n"
                f"🔽 استخدم الأزرار للتحكم:",
                chat_id,
                call.message.id,
                reply_markup=main_menu(user_id),
                parse_mode="HTML"
            )
            bot.answer_callback_query(call.id, "✅ تم الرجوع للقائمة الرئيسية")
            return

        # ===== بقية الأزرار =====
        elif data == "upload":
            file_name_sessions[user_id] = {"action": "waiting_for_name"}
            bot.edit_message_text(
                "📤 <b>رفع ملف جديد</b>\n\n"
                "✏️ <b>أولاً:</b> اكتب اسم الملف الذي تريد حفظه به\n"
                "📝 مثال: <code>my_bot</code>\n"
                "📌 الاسم سيظهر في قائمة ملفاتك\n\n"
                "⏳ بعد كتابة الاسم، أرسل ملف Python (.py) أو ملف مضغوط (.zip)",
                chat_id,
                call.message.id,
                parse_mode="HTML"
            )
            bot.answer_callback_query(call.id)

        elif data == "delete_file":
            files = get_user_files_list(user_id) if not is_admin(user_id) else get_all_files_list()
            if not files:
                bot.send_message(chat_id, "📂 لا توجد ملفات.", reply_markup=main_menu(user_id))
                bot.delete_message(chat_id, call.message.id)
                bot.answer_callback_query(call.id)
                return

            msg = "📂 <b>ملفاتك (للحذف):</b>\n\n"
            for i, f in enumerate(files, 1):
                path = os.path.join(UPLOAD_FOLDER, f)
                size = os.path.getsize(path) // 1024
                msg += f"{i}. <b>{f}</b> ({size} KB)\n"
            msg += "\n🗑 <b>أرسل رقم الملف الذي تريد حذفه (مثال: 1):</b>"
            bot.edit_message_text(msg, chat_id, call.message.id, parse_mode="HTML")
            bot.register_next_step_handler(call.message, delete_file_by_number_step)
            bot.answer_callback_query(call.id)

        elif data == "install_lib":
            msg = bot.send_message(chat_id, "📦 أرسل اسم المكتبة التي تريد تحميلها (مثال: requests):")
            bot.register_next_step_handler(msg, install_lib_step)
            bot.answer_callback_query(call.id)

        elif data == "make_bot":
            msg = bot.send_message(chat_id, "✏️ أرسل كود البوت بصيغة <code>.py</code>:", parse_mode="HTML")
            bot.register_next_step_handler(msg, make_bot_step)
            bot.answer_callback_query(call.id)

        elif data == "stop_one":
            files = get_user_files_list(user_id) if not is_admin(user_id) else get_all_files_list()
            if not files:
                bot.send_message(chat_id, "📂 لا توجد ملفات.", reply_markup=main_menu(user_id))
                bot.delete_message(chat_id, call.message.id)
                bot.answer_callback_query(call.id)
                return

            msg = "📂 <b>ملفاتك (للإيقاف):</b>\n\n"
            for i, f in enumerate(files, 1):
                project = db.fetch_one("SELECT status FROM projects WHERE file_path LIKE ?", (f"%{f}%",))
                status = "🟢 شغال" if project and project['status'] == 'running' else "🔴 متوقف"
                msg += f"{i}. <b>{f}</b> - {status}\n"
            msg += "\n⛔ <b>أرسل رقم الملف الذي تريد إيقافه (مثال: 1):</b>"
            bot.edit_message_text(msg, chat_id, call.message.id, parse_mode="HTML")
            bot.register_next_step_handler(call.message, stop_one_by_number_step)
            bot.answer_callback_query(call.id)

        elif data == "start_one":
            files = get_user_files_list(user_id) if not is_admin(user_id) else get_all_files_list()
            if not files:
                bot.send_message(chat_id, "📂 لا توجد ملفات.", reply_markup=main_menu(user_id))
                bot.delete_message(chat_id, call.message.id)
                bot.answer_callback_query(call.id)
                return

            msg = "📂 <b>ملفاتك (للتشغيل):</b>\n\n"
            for i, f in enumerate(files, 1):
                project = db.fetch_one("SELECT status FROM projects WHERE file_path LIKE ?", (f"%{f}%",))
                status = "🟢 شغال" if project and project['status'] == 'running' else "🔴 متوقف"
                msg += f"{i}. <b>{f}</b> - {status}\n"
            msg += "\n🟢 <b>أرسل رقم الملف الذي تريد تشغيله (مثال: 1):</b>"
            bot.edit_message_text(msg, chat_id, call.message.id, parse_mode="HTML")
            bot.register_next_step_handler(call.message, start_one_by_number_step)
            bot.answer_callback_query(call.id)

        elif data == "list_files":
            files = get_user_files_list(user_id)
            if not files:
                bot.send_message(chat_id, "📂 لا توجد ملفات خاصة بك.", reply_markup=main_menu(user_id))
                bot.delete_message(chat_id, call.message.id)
            else:
                msg = "📋 <b>ملفاتك:</b>\n\n"
                for i, f in enumerate(files, 1):
                    path = os.path.join(UPLOAD_FOLDER, f)
                    size = os.path.getsize(path) // 1024
                    mod_time = datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')
                    msg += f"{i}. <b>{f}</b>\n"
                    msg += f"   📦 الحجم: {size} KB\n"
                    msg += f"   📅 رفع: {mod_time}\n\n"
                bot.send_message(chat_id, msg, reply_markup=main_menu(user_id), parse_mode="HTML")
                bot.delete_message(chat_id, call.message.id)
            bot.answer_callback_query(call.id)

        elif data == "admin_list_files":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ هذا الزر للأدمن فقط!", show_alert=True)
                return
            files = get_all_files_list()
            if not files:
                bot.send_message(chat_id, "📂 لا توجد ملفات.", reply_markup=main_menu(user_id))
                bot.delete_message(chat_id, call.message.id)
            else:
                msg = "📋 <b>جميع الملفات (للمطور):</b>\n\n"
                for i, f in enumerate(files, 1):
                    path = os.path.join(UPLOAD_FOLDER, f)
                    size = os.path.getsize(path) // 1024
                    mod_time = datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')
                    user_id_from_file = "غير معروف"
                    if f.startswith("user_"):
                        parts = f.split("_")
                        if len(parts) >= 2:
                            user_id_from_file = parts[1]
                    msg += f"{i}. <b>{f}</b>\n"
                    msg += f"   📦 الحجم: {size} KB\n"
                    msg += f"   📅 رفع: {mod_time}\n"
                    msg += f"   👤 المستخدم: {user_id_from_file}\n\n"
                bot.send_message(chat_id, msg, reply_markup=main_menu(user_id), parse_mode="HTML")
                bot.delete_message(chat_id, call.message.id)
            bot.answer_callback_query(call.id)

        elif data == "delete_all_stopped":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ هذا الزر للأدمن فقط!", show_alert=True)
                return
            deleted_count = 0
            files = get_all_files_list()
            for f in files:
                project = db.fetch_one("SELECT id, status FROM projects WHERE file_path LIKE ?", (f"%{f}%",))
                if not project or project['status'] != 'running':
                    path = os.path.join(UPLOAD_FOLDER, f)
                    if os.path.exists(path):
                        os.remove(path)
                        deleted_count += 1
            bot.edit_message_text(f"🗑 تم حذف {deleted_count} ملف متوقف.", chat_id, call.message.id, reply_markup=main_menu(user_id))
            bot.answer_callback_query(call.id)

        elif data == "dev":
            bot.edit_message_text(
                f"👨🏻‍💻 مبرمج البوت: @XZ_XINGzon\n\n🆔 المطور: <code>{DEVELOPER_ID}</code>",
                chat_id,
                call.message.id,
                reply_markup=main_menu(user_id),
                parse_mode="HTML"
            )
            bot.answer_callback_query(call.id)

        elif data == "points":
            points = points_manager.get_points(user_id)
            history = db.fetch_all(
                "SELECT * FROM points_history WHERE user_id = ? ORDER BY created_at DESC LIMIT 10",
                (user_id,)
            )
            text = f"💎 **نقاطك:** {format_points(points)} نقطة\n\n📜 **آخر العمليات:**\n"
            if history:
                for h in history:
                    sign = "+" if h['amount'] > 0 else ""
                    text += f"• {sign}{h['amount']} نقطة - {h['reason']}\n  🕐 {h['created_at']}\n"
            else:
                text += "• لا توجد عمليات سابقة\n"
            text += "\n📌 **طرق الحصول على النقاط:**\n• دعوة أصدقاء: +5 نقاط\n• تحويل من مستخدمين آخرين"
            bot.edit_message_text(text, chat_id, call.message.id, reply_markup=main_menu(user_id), parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        elif data == "renew_project":
            projects = project_manager.get_user_projects(user_id)
            if not projects:
                bot.edit_message_text(
                    "📭 ليس لديك مشاريع لتجديد صلاحيتها.\n"
                    "قم برفع ملف أولاً باستخدام زر 'رفع ملف'",
                    chat_id,
                    call.message.id,
                    reply_markup=main_menu(user_id)
                )
                bot.answer_callback_query(call.id)
                return
            
            text = "🔄 <b>اختر المشروع الذي تريد تجديد صلاحيته:</b>\n\n"
            markup = types.InlineKeyboardMarkup(row_width=1)
            for p in projects:
                status = "🟢" if p['status'] == 'running' else "🔴"
                expiry = p['expiry_date'] if p['expiry_date'] else "لا يوجد"
                text += f"{status} <b>{p['project_name']}</b>\n   🆔 {p['id']} | 📅 {expiry}\n\n"
                markup.add(types.InlineKeyboardButton(
                    f"{status} {p['project_name'][:20]}", 
                    callback_data=f"renew_{p['id']}"
                ))
            markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
            bot.edit_message_text(text, chat_id, call.message.id, reply_markup=markup, parse_mode="HTML")
            bot.answer_callback_query(call.id)

        elif data == "invite":
            user = db.fetch_one("SELECT invite_code FROM users WHERE user_id = ?", (user_id,))
            if not user:
                bot.answer_callback_query(call.id, "❌ حدث خطأ", show_alert=True)
                return
            invite_code = user['invite_code']
            bot_username = bot.get_me().username
            invite_link = f"https://t.me/{bot_username}?start=ref_{invite_code}"
            invited = db.fetch_all("SELECT COUNT(*) as count FROM users WHERE invited_by = ?", (user_id,))
            invited_count = invited[0]['count'] if invited else 0
            text = f"""
👥 **نظام الدعوة والمكافآت**

🔗 **رابط دعوتك الشخصي:**
`{invite_link}`

📊 **إحصائيات دعواتك:**
• عدد المدعوين: {invited_count}
• النقاط المكتسبة: {invited_count * POINTS_PER_INVITE}

🎁 **المكافآت:**
• كل دعوة تحصل على {POINTS_PER_INVITE} نقاط
• صديقك يحصل على {POINTS_PER_NEW_USER} نقاط هدية

📤 **شارك الرابط مع أصدقائك!**
"""
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("📤 مشاركة الرابط", url=f"https://t.me/share/url?url={invite_link}&text=🚀 انضم لبوت استضافة زو ZO!"))
            markup.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main"))
            bot.edit_message_text(text, chat_id, call.message.id, reply_markup=markup, parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        elif data == "my_stats":
            points = points_manager.get_points(user_id)
            projects = project_manager.get_user_projects(user_id)
            running = len([p for p in projects if p['status'] == 'running'])
            user_info = db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
            total_points_used = sum(p['points_used'] for p in projects) if projects else 0
            text = f"""
📊 **إحصائياتك الشخصية**

👤 المستخدم: {call.from_user.full_name}
🆔 المعرف: @{call.from_user.username}

📈 الإحصائيات:
• 💎 النقاط الحالية: {format_points(points)}
• 💰 إجمالي النقاط المستخدمة: {format_points(total_points_used)}
• 📁 إجمالي المشاريع: {len(projects)}
• 🟢 المشاريع النشطة: {running}

📅 تاريخ الانضمام: {user_info['created_at'] if user_info else 'غير معروف'}
"""
            bot.edit_message_text(text, chat_id, call.message.id, reply_markup=main_menu(user_id), parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        elif data == "transfer_points":
            transfer_sessions[user_id] = True
            bot.send_message(chat_id, "💸 أرسل معرف المستهدف وعدد النقاط\nمثال: @username 5")
            bot.answer_callback_query(call.id)

        # ===== إدارة الحظر =====
        elif data == "manage_ban":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ أنت لست أدمن!", show_alert=True)
                return
            bot.edit_message_text("🚫 <b>إدارة الحظر</b>\nاختر إجراء:", chat_id, call.message.id, reply_markup=admin_ban_menu(), parse_mode="HTML")
            bot.answer_callback_query(call.id)

        elif data == "view_banned":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ غير مصرح!", show_alert=True)
                return
            banned = load_banned()
            if not banned:
                bot.edit_message_text("📭 لا يوجد مستخدمين محظورين.", chat_id, call.message.id, reply_markup=admin_ban_menu())
            else:
                msg = "🚫 <b>المستخدمين المحظورين:</b>\n\n"
                for uid, info in banned.items():
                    date = datetime.fromtimestamp(info['date']).strftime('%Y-%m-%d %H:%M')
                    msg += f"• <b>{uid}</b>\n  📅 حظر في: {date}\n"
                bot.edit_message_text(msg, chat_id, call.message.id, reply_markup=admin_ban_menu(), parse_mode="HTML")
            bot.answer_callback_query(call.id)

        elif data == "ban_user":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ غير مصرح!", show_alert=True)
                return
            msg = bot.send_message(chat_id, "🚫 أرسل معرف المستخدم (ID) لحظره:")
            bot.register_next_step_handler(msg, ban_user_step)
            bot.answer_callback_query(call.id)

        elif data == "unban_user":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ غير مصرح!", show_alert=True)
                return
            msg = bot.send_message(chat_id, "✅ أرسل معرف المستخدم (ID) لإلغاء حظره:")
            bot.register_next_step_handler(msg, unban_user_step)
            bot.answer_callback_query(call.id)

        # ===== التحكم بالمشاريع =====
        elif data.startswith("project_"):
            project_id = int(data.split("_")[1])
            project = project_manager.get_project(project_id)
            if not project:
                bot.answer_callback_query(call.id, "❌ المشروع غير موجود!", show_alert=True)
                return
            if project['user_id'] != user_id and not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ هذا ليس مشروعك!", show_alert=True)
                return
            
            status_emoji = "🟢" if project['status'] == 'running' else "🔴"
            expiry = project['expiry_date'] if project['expiry_date'] else "غير محدد"
            text = f"""
📁 **التحكم بالمشروع**

🆔 المعرف: `{project['id']}`
📂 الاسم: {project['project_name']}
📊 الحالة: {status_emoji} {'يعمل' if project['status'] == 'running' else 'متوقف'}
🔌 المنفذ: {project['port']}
💎 النقاط المستخدمة: {project['points_used']}
📅 مدة الاستضافة: {project['days_used']} يوم
📅 تنتهي في: {expiry}
📅 تاريخ الرفع: {project['created_at']}
"""
            bot.edit_message_text(
                text,
                chat_id,
                call.message.id,
                parse_mode="HTML",
                reply_markup=project_control_menu(project_id)
            )
            bot.answer_callback_query(call.id)

        elif data.startswith("start_"):
            project_id = int(data.split("_")[1])
            project = project_manager.get_project(project_id)
            if project and project['expiry_date']:
                try:
                    expiry = datetime.strptime(project['expiry_date'], '%Y-%m-%d %H:%M:%S')
                    if expiry < datetime.now():
                        bot.answer_callback_query(
                            call.id, 
                            "❌ انتهت صلاحية المشروع! استخدم 'تجديد صلاحية' لإعادة تشغيله.",
                            show_alert=True
                        )
                        return
                except:
                    pass
            
            success, msg = project_manager.start_project(project_id)
            if success and project_id in project_manager.projects:
                threading.Thread(
                    target=monitor_project_logs,
                    args=(project_id, project_manager.projects[project_id]['process']),
                    daemon=True
                ).start()
            bot.answer_callback_query(call.id, msg, show_alert=True)
            bot.edit_message_text(
                get_current_message_text(call),
                chat_id,
                call.message.id,
                reply_markup=project_control_menu(project_id)
            )

        elif data.startswith("stop_"):
            project_id = int(data.split("_")[1])
            success, msg = project_manager.stop_project(project_id)
            bot.answer_callback_query(call.id, msg, show_alert=True)
            bot.edit_message_text(
                get_current_message_text(call),
                chat_id,
                call.message.id,
                reply_markup=project_control_menu(project_id)
            )

        elif data.startswith("restart_"):
            project_id = int(data.split("_")[1])
            success, msg = project_manager.restart_project(project_id)
            if success and project_id in project_manager.projects:
                threading.Thread(
                    target=monitor_project_logs,
                    args=(project_id, project_manager.projects[project_id]['process']),
                    daemon=True
                ).start()
            bot.answer_callback_query(call.id, msg, show_alert=True)
            bot.edit_message_text(
                get_current_message_text(call),
                chat_id,
                call.message.id,
                reply_markup=project_control_menu(project_id)
            )

        elif data.startswith("logs_"):
            project_id = int(data.split("_")[1])
            project = project_manager.get_project(project_id)
            if project:
                logs = project['logs'] or "لا توجد سجلات بعد"
                if len(logs) > 4000:
                    logs = logs[-4000:] + "\n\n... تم قص السجلات"
                bot.edit_message_text(
                    f"📋 **سجلات المشروع #{project_id}**\n\n<code>{logs}</code>",
                    chat_id,
                    call.message.id,
                    parse_mode="HTML",
                    reply_markup=project_control_menu(project_id)
                )
            bot.answer_callback_query(call.id)

        elif data.startswith("export_"):
            project_id = int(data.split("_")[1])
            bot.edit_message_text("⏳ جاري التصدير...", chat_id, call.message.id)
            file_path, msg = project_manager.export_project(project_id)
            if file_path:
                try:
                    with open(file_path, 'rb') as f:
                        bot.send_document(
                            chat_id,
                            f,
                            filename=os.path.basename(file_path),
                            caption=f"✅ تم تصدير المشروع #{project_id}"
                        )
                    os.remove(file_path)
                    bot.edit_message_text("✅ تم التصدير بنجاح!", chat_id, call.message.id, reply_markup=project_control_menu(project_id))
                except Exception as e:
                    bot.edit_message_text(f"❌ خطأ في التصدير: {str(e)}", chat_id, call.message.id)
            else:
                bot.edit_message_text(f"❌ {msg}", chat_id, call.message.id)
            bot.answer_callback_query(call.id)

        elif data.startswith("delete_"):
            project_id = int(data.split("_")[1])
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("✅ نعم، احذف", callback_data=f"confirm_delete_{project_id}"),
                types.InlineKeyboardButton("❌ إلغاء", callback_data=f"project_{project_id}")
            )
            bot.edit_message_text(
                "⚠️ **هل أنت متأكد من حذف هذا المشروع؟**\nلا يمكن التراجع!",
                chat_id,
                call.message.id,
                parse_mode="Markdown",
                reply_markup=markup
            )
            bot.answer_callback_query(call.id)

        elif data.startswith("confirm_delete_"):
            project_id = int(data.split("_")[2])
            success, msg = project_manager.delete_project(project_id)
            bot.edit_message_text(msg, chat_id, call.message.id, reply_markup=main_menu(user_id))
            bot.answer_callback_query(call.id)

        # ===== لوحة الأدمن =====
        elif data == "admin_panel":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ أنت لست أدمن!", show_alert=True)
                return
            total_users = db.fetch_one("SELECT COUNT(*) as count FROM users")['count']
            total_projects = db.fetch_one("SELECT COUNT(*) as count FROM projects")['count']
            running_projects = db.fetch_one("SELECT COUNT(*) as count FROM projects WHERE status = 'running'")['count']
            total_points = db.fetch_one("SELECT SUM(points) as total FROM users")['total'] or 0
            maintenance = is_maintenance()
            text = f"""
⚙️ **لوحة تحكم الأدمن**

📊 **الإحصائيات السريعة:**
• 👥 المستخدمين: {total_users}
• 📁 المشاريع: {total_projects}
• 🟢 النشطة: {running_projects}
• 💎 إجمالي النقاط: {format_points(total_points)}

🔧 الصيانة: {'🟢 مفعلة' if maintenance else '🔴 معطلة'}

🔧 اختر الإجراء:
"""
            bot.edit_message_text(text, chat_id, call.message.id, reply_markup=admin_panel_menu(), parse_mode="HTML")
            bot.answer_callback_query(call.id)

        elif data == "admin_users":
            if not is_admin(user_id):
                return
            users = db.fetch_all("SELECT * FROM users ORDER BY created_at DESC LIMIT 30")
            text = "👥 **قائمة المستخدمين:**\n\n"
            for u in users:
                status = "🚫" if u['is_banned'] else ("🔇" if u['is_muted'] else "✅")
                text += f"{status} @{u['username'] or f'ID:{u["user_id"]}'} - 💎{format_points(u['points'])}\n"
            text += f"\n📊 إجمالي: {len(users)} مستخدم"
            bot.edit_message_text(text, chat_id, call.message.id, reply_markup=admin_panel_menu(), parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        elif data == "admin_add_points":
            if not is_admin(user_id):
                return
            msg = bot.send_message(chat_id, "📝 أرسل معرف المستخدم وعدد النقاط\nمثال: `123456789 10`")
            bot.register_next_step_handler(msg, admin_add_points_step)
            bot.answer_callback_query(call.id)

        elif data == "admin_remove_points":
            if not is_admin(user_id):
                return
            msg = bot.send_message(chat_id, "📝 أرسل معرف المستخدم وعدد النقاط\nمثال: `123456789 5`")
            bot.register_next_step_handler(msg, admin_remove_points_step)
            bot.answer_callback_query(call.id)

        elif data == "admin_projects":
            if not is_admin(user_id):
                return
            projects = db.fetch_all("SELECT * FROM projects ORDER BY created_at DESC LIMIT 30")
            text = "📂 **جميع المشاريع:**\n\n"
            for p in projects:
                status_emoji = "🟢" if p['status'] == 'running' else "🔴"
                user = db.fetch_one("SELECT username FROM users WHERE user_id = ?", (p['user_id'],))
                username = user['username'] if user else f"ID:{p['user_id']}"
                expiry = p['expiry_date'] if p['expiry_date'] else "غير محدد"
                text += f"{status_emoji} #{p['id']} - {p['project_name']} (@{username}) - 📅 {expiry[:10] if expiry else 'غير محدد'}\n"
            text += f"\n📊 إجمالي: {len(projects)} مشروع"
            bot.edit_message_text(text, chat_id, call.message.id, reply_markup=admin_panel_menu(), parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        elif data == "admin_stop_all":
            if not is_admin(user_id):
                return
            bot.edit_message_text("⏳ جاري إيقاف جميع المشاريع...", chat_id, call.message.id)
            stopped = project_manager.stop_all_projects()
            bot.edit_message_text(f"✅ تم إيقاف جميع المشاريع!\nتم إيقاف: {stopped} مشروع", chat_id, call.message.id, reply_markup=admin_panel_menu())
            bot.answer_callback_query(call.id)

        elif data == "admin_stats":
            if not is_admin(user_id):
                return
            total_users = db.fetch_one("SELECT COUNT(*) as count FROM users")['count']
            today_users = db.fetch_one("SELECT COUNT(*) as count FROM users WHERE DATE(created_at) = DATE('now')")['count']
            total_projects = db.fetch_one("SELECT COUNT(*) as count FROM projects")['count']
            running = db.fetch_one("SELECT COUNT(*) as count FROM projects WHERE status = 'running'")['count']
            total_points = db.fetch_one("SELECT SUM(points) as total FROM users")['total'] or 0
            total_points_used = db.fetch_one("SELECT SUM(points_used) as total FROM projects")['total'] or 0
            top_users = db.fetch_all("""
                SELECT u.username, u.user_id, COUNT(p.id) as project_count, SUM(p.points_used) as points_used
                FROM users u LEFT JOIN projects p ON u.user_id = p.user_id
                GROUP BY u.user_id
                ORDER BY points_used DESC
                LIMIT 10
            """)
            text = f"""
📊 **إحصائيات البوت**

👥 المستخدمين:
• الإجمالي: {total_users}
• جدد اليوم: {today_users}

📁 المشاريع:
• الإجمالي: {total_projects}
• النشطة: {running}

💎 النقاط:
• إجمالي النقاط: {format_points(total_points)}
• النقاط المستخدمة: {format_points(total_points_used)}

🏆 الأكثر استخداماً للنقاط:
"""
            for i, u in enumerate(top_users, 1):
                username = u['username'] or f"ID:{u['user_id']}"
                text += f"{i}. @{username} - {u['project_count']} مشاريع - {format_points(u['points_used'])} نقطة\n"
            bot.edit_message_text(text, chat_id, call.message.id, reply_markup=admin_panel_menu(), parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        elif data == "admin_broadcast":
            if not is_admin(user_id):
                return
            msg = bot.send_message(chat_id, "📢 أرسل الرسالة التي تريد إرسالها لجميع المستخدمين:")
            bot.register_next_step_handler(msg, admin_broadcast_step)
            bot.answer_callback_query(call.id)

        elif data == "admin_maintenance":
            if not is_admin(user_id):
                return
            current = is_maintenance()
            set_maintenance(not current)
            bot.edit_message_text(f"✅ تم {'إيقاف' if current else 'تفعيل'} وضع الصيانة", chat_id, call.message.id, reply_markup=admin_panel_menu())
            bot.answer_callback_query(call.id)

        elif data == "admin_libs":
            if not is_admin(user_id):
                return
            libs = db.fetch_all("SELECT lib_name, version FROM installed_libs ORDER BY lib_name")
            if not libs:
                text = "📦 لا توجد مكتبات مثبتة حالياً"
            else:
                text = "📦 **المكتبات المثبتة:**\n\n"
                for lib in libs[:30]:
                    text += f"• {lib['lib_name']} - v{lib['version']}\n"
                text += f"\n📊 إجمالي المكتبات: {len(libs)}"
            bot.edit_message_text(text, chat_id, call.message.id, reply_markup=admin_panel_menu(), parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        elif data == "admin_invites":
            if not is_admin(user_id):
                return
            invites = db.fetch_all("""
                SELECT il.*, u.username
                FROM invite_links il
                LEFT JOIN users u ON il.created_by = u.user_id
                ORDER BY il.created_at DESC
                LIMIT 20
            """)
            text = "🔗 **روابط الدعوة:**\n\n"
            if invites:
                for inv in invites:
                    text += f"👤 @{inv['username'] or inv['created_by']}\n"
                    text += f"🔗 `{inv['link']}`\n"
                    text += f"👥 {inv['used_count']}/{inv['max_uses']}\n"
                    text += f"📅 {inv['created_at'][:10]}\n\n"
            else:
                text += "لا توجد روابط دعوة\n"
            bot.edit_message_text(text, chat_id, call.message.id, reply_markup=admin_panel_menu(), parse_mode="Markdown")
            bot.answer_callback_query(call.id)

        # ===== إدارة الاشتراك الإجباري (مُصلح) =====
        elif data == "admin_mandatory_channels":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ أنت لست أدمن!", show_alert=True)
                return
            
            channels = get_required_channels()
            channels_text = ""
            if channels:
                for idx, ch in enumerate(channels, 1):
                    name = ch['channel_name'] or "قناة"
                    ch_id = ch['channel_id']
                    channels_text += f"{idx}. 📢 {name} - `{ch_id}`\n"
            else:
                channels_text = "📭 لا توجد قنوات إجبارية"
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("➕ إضافة قناة", callback_data="add_mandatory_channel", style='primary'),
                types.InlineKeyboardButton("📋 عرض القنوات", callback_data="list_mandatory_channels", style='primary'),
                types.InlineKeyboardButton("🗑️ حذف قناة", callback_data="delete_mandatory_channel", style='danger'),
                types.InlineKeyboardButton("🗑️ حذف الكل", callback_data="clear_mandatory_channels", style='danger'),
                types.InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")
            )
            
            text = f"""
📢 **إدارة الاشتراك الإجباري**
━━━━━━━━━━━━━━━━━━━━━
📊 **القنوات الحالية:**
{channels_text}
━━━━━━━━━━━━━━━━━━━━━
اختر الإجراء المناسب:
"""
            # ✅ استخدام send_message بدلاً من edit_message_text
            bot.send_message(
                chat_id,
                text,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            bot.answer_callback_query(call.id)

        elif data == "add_mandatory_channel":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ أنت لست أدمن!", show_alert=True)
                return
            
            admin_channel_session[user_id] = {"action": "add_channel_id"}
            
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add(types.KeyboardButton("🔙 إلغاء"))
            
            # ✅ استخدام send_message بدلاً من edit_message_text
            bot.send_message(
                chat_id,
                "📢 **إضافة قناة اشتراك إجباري**\n━━━━━━━━━━━━━━━━━━━━━\n\n"
                "⚠️ **تنبيهات مهمة:**\n"
                "• يجب أن يكون البوت **مشرفاً** في القناة\n"
                "• صلاحية **حظر المستخدمين** مطلوبة\n\n"
                "📝 **أرسل معرف القناة الآن:**\n"
                "مثال: `-1001234567890`\n\n"
                "🔹 يمكنك إرسال المعرف كرقم فقط أو مع الإشارة السالبة",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            bot.answer_callback_query(call.id)

        elif data == "list_mandatory_channels":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ أنت لست أدمن!", show_alert=True)
                return
            
            channels = get_required_channels()
            if not channels:
                bot.send_message(
                    chat_id,
                    "📭 **لا توجد قنوات إجبارية**",
                    reply_markup=back_to_mandatory_markup()
                )
                bot.answer_callback_query(call.id)
                return
            
            text = "📋 **قائمة القنوات الإجبارية**\n━━━━━━━━━━━━━━━━━━━━━\n\n"
            for idx, ch in enumerate(channels, 1):
                ch_id = ch['channel_id']
                name = ch['channel_name'] or "قناة"
                link = ch['channel_link'] or "لا يوجد"
                
                bot_status = "❓"
                try:
                    member = bot.get_chat_member(ch_id, bot.get_me().id)
                    if member.status in ['administrator', 'creator']:
                        bot_status = "✅ مشرف"
                    else:
                        bot_status = "❌ ليس مشرفاً"
                except:
                    bot_status = "⚠️ لا يمكن التحقق"
                
                text += f"**{idx}.** 📢 {name}\n"
                text += f"   🆔 `{ch_id}`\n"
                text += f"   🔗 {link}\n"
                text += f"   🤖 حالة البوت: {bot_status}\n\n"
            
            bot.send_message(
                chat_id,
                text,
                reply_markup=back_to_mandatory_markup(),
                parse_mode="Markdown"
            )
            bot.answer_callback_query(call.id)

        elif data == "delete_mandatory_channel":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ أنت لست أدمن!", show_alert=True)
                return
            
            channels = get_required_channels()
            if not channels:
                bot.answer_callback_query(call.id, "📭 لا توجد قنوات للحذف!", show_alert=True)
                return
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            for idx, ch in enumerate(channels):
                name = ch['channel_name'] or f"قناة {ch['channel_id']}"
                markup.add(types.InlineKeyboardButton(
                    f"🗑️ {name}",
                    callback_data=f"delete_channel_{idx}"
                ))
            markup.add(types.InlineKeyboardButton("🔙 إلغاء", callback_data="admin_mandatory_channels"))
            
            bot.send_message(
                chat_id,
                "🗑️ **اختر القناة للحذف:**",
                reply_markup=markup
            )
            bot.answer_callback_query(call.id)

        elif data.startswith("delete_channel_"):
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ أنت لست أدمن!", show_alert=True)
                return
            
            idx = int(data.replace("delete_channel_", ""))
            channels = get_required_channels()
            
            if idx < 0 or idx >= len(channels):
                bot.answer_callback_query(call.id, "❌ القناة غير موجودة!", show_alert=True)
                return
            
            channel = channels[idx]
            channel_id = channel['channel_id']
            channel_name = channel['channel_name'] or "قناة"
            
            delete_required_channel(channel_id)
            
            channels_remaining = get_required_channels()
            bot.send_message(
                chat_id,
                f"✅ **تم حذف القناة بنجاح!**\n\n"
                f"📢 {channel_name}\n"
                f"🆔 `{channel_id}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 **القنوات المتبقية:** {len(channels_remaining)}",
                reply_markup=back_to_mandatory_markup(),
                parse_mode="Markdown"
            )
            bot.answer_callback_query(call.id, "✅ تم الحذف!")

        elif data == "clear_mandatory_channels":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ أنت لست أدمن!", show_alert=True)
                return
            
            channels = get_required_channels()
            if not channels:
                bot.answer_callback_query(call.id, "📭 لا توجد قنوات للحذف!", show_alert=True)
                return
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ نعم، احذف الكل", callback_data="confirm_clear_channels", style='danger'),
                types.InlineKeyboardButton("❌ إلغاء", callback_data="admin_mandatory_channels")
            )
            
            bot.send_message(
                chat_id,
                f"⚠️ **تأكيد حذف جميع القنوات الإجبارية**\n\n"
                f"عدد القنوات: {len(channels)}\n\n"
                f"هل أنت متأكد؟",
                reply_markup=markup
            )
            bot.answer_callback_query(call.id)

        elif data == "confirm_clear_channels":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ أنت لست أدمن!", show_alert=True)
                return
            
            count = len(get_required_channels())
            delete_all_required_channels()
            
            bot.send_message(
                chat_id,
                f"✅ **تم حذف جميع القنوات الإجبارية!**\n"
                f"🗑️ عدد المحذوف: {count}",
                reply_markup=back_to_mandatory_markup()
            )
            bot.answer_callback_query(call.id, "✅ تم حذف الكل!")

        # ===== تعديل الكود =====
        elif data == "edit_bot_code":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ هذا القسم للمطور الأساسي فقط!", show_alert=True)
                return
            show_edit_code_panel(chat_id, call.message.id)
            bot.answer_callback_query(call.id)

        elif data == "edit_code_export":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ غير مصرح!", show_alert=True)
                return
            export_bot_code(call)

        elif data == "edit_code_receive":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ غير مصرح!", show_alert=True)
                return
            admin_code_edit_session[user_id] = {"action": "waiting_for_code_file"}
            bot.edit_message_text(
                "📥 <b>استقبال ملف كود معدل</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "📤 <b>أرسل ملف Python (.py) المعدل الآن</b>\n\n"
                "⚠️ <b>تنبيهات مهمة:</b>\n"
                "• يجب أن يكون الملف بامتداد <code>.py</code>\n"
                "• سيتم عمل نسخة احتياطية تلقائياً\n"
                "• تأكد من صحة الكود قبل الإرسال\n\n"
                "🔽 أرسل الملف الآن",
                chat_id,
                call.message.id,
                parse_mode="HTML"
            )
            bot.answer_callback_query(call.id)

        elif data == "edit_code_reset_all":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ غير مصرح!", show_alert=True)
                return
            show_reset_confirm_panel(chat_id, call.message.id)
            bot.answer_callback_query(call.id)

        elif data == "confirm_reset_all":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ غير مصرح!", show_alert=True)
                return
            execute_reset_all(call)

        elif data == "do_restart_bot":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ غير مصرح!", show_alert=True)
                return
            do_restart_bot(call)

        elif data == "restore_backup_bot":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ غير مصرح!", show_alert=True)
                return
            restore_backup_bot(call)

        elif data == "back_to_edit_code":
            if not is_admin(user_id):
                bot.answer_callback_query(call.id, "❌ غير مصرح!", show_alert=True)
                return
            show_edit_code_panel(chat_id, call.message.id)
            bot.answer_callback_query(call.id)

        else:
            bot.answer_callback_query(call.id)

    except Exception as e:
        print(f"❌ خطأ في معالج الكول باك: {e}")
        try:
            bot.answer_callback_query(call.id, f"❌ حدث خطأ: {str(e)[:50]}")
        except:
            pass

# ============================================================
# باقي الدوال (المساعدة، تعديل الكود، الخ)
# ============================================================

def monitor_project_logs(project_id, process):
    logs = []
    try:
        while process.poll() is None:
            if process.stdout:
                line = process.stdout.readline()
                if line:
                    decoded = line.decode('utf-8', errors='ignore')
                    logs.append(decoded)
                    if len(logs) > 100:
                        logs = logs[-100:]
                    log_text = '\n'.join(logs)[-5000:]
                    db.execute_query(
                        "UPDATE projects SET logs = ? WHERE id = ?",
                        (log_text, project_id)
                    )
            time.sleep(0.1)
        db.execute_query(
            "UPDATE projects SET status = 'stopped' WHERE id = ?",
            (project_id,)
        )
    except:
        pass

def load_banned():
    rows = db.fetch_all("SELECT user_id FROM users WHERE is_banned = 1")
    banned = {}
    for row in rows:
        banned[str(row['user_id'])] = {'date': time.time()}
    return banned

def save_banned(banned):
    db.execute_query("UPDATE users SET is_banned = 0")
    for uid in banned:
        db.execute_query("UPDATE users SET is_banned = 1 WHERE user_id = ?", (int(uid),))

def delete_file_by_number_step(message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)
    try:
        number = int(message.text.strip())
        filename = get_file_by_number(user_id, number, is_admin_user)
        if not filename:
            bot.reply_to(message, "❌ رقم غير صحيح!")
            return
        path = os.path.join(UPLOAD_FOLDER, filename)
        if not os.path.exists(path):
            bot.reply_to(message, "❌ الملف غير موجود.")
            return
        size = os.path.getsize(path) // 1024
        confirm = types.InlineKeyboardMarkup()
        confirm.add(types.InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"confirm_delete_file_{filename}"))
        confirm.add(types.InlineKeyboardButton("❌ إلغاء", callback_data="back_to_main"))
        bot.reply_to(message, f"📂 <b>{filename}</b>\nالحجم: {size} KB\n\nهل تريد الحذف؟", reply_markup=confirm, parse_mode="HTML")
    except ValueError:
        bot.reply_to(message, "❌ يرجى إدخال رقم صحيح (مثال: 1)")

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_delete_file_"))
def confirm_delete_file(call):
    filename = call.data.replace("confirm_delete_file_", "")
    user_id = call.from_user.id
    path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(path):
        project = db.fetch_one("SELECT id FROM projects WHERE file_path LIKE ?", (f"%{filename}%",))
        if project:
            project_manager.stop_project(project['id'])
        os.remove(path)
        bot.edit_message_text(f"🗑 تم حذف الملف: {filename}", call.message.chat.id, call.message.id, reply_markup=main_menu(user_id))
    else:
        bot.edit_message_text("❌ الملف غير موجود.", call.message.chat.id, call.message.id, reply_markup=main_menu(user_id))
    bot.answer_callback_query(call.id)

def stop_one_by_number_step(message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)
    try:
        number = int(message.text.strip())
        filename = get_file_by_number(user_id, number, is_admin_user)
        if not filename:
            bot.reply_to(message, "❌ رقم غير صحيح!")
            return
        project = db.fetch_one("SELECT id, status FROM projects WHERE file_path LIKE ?", (f"%{filename}%",))
        if project and project['status'] == 'running':
            success, msg = project_manager.stop_project(project['id'])
            bot.reply_to(message, msg)
        else:
            bot.reply_to(message, "❌ البوت غير مشغل.")
    except ValueError:
        bot.reply_to(message, "❌ يرجى إدخال رقم صحيح (مثال: 1)")

def start_one_by_number_step(message):
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)
    try:
        number = int(message.text.strip())
        filename = get_file_by_number(user_id, number, is_admin_user)
        if not filename:
            bot.reply_to(message, "❌ رقم غير صحيح!")
            return
        path = os.path.join(UPLOAD_FOLDER, filename)
        if not os.path.exists(path):
            bot.reply_to(message, "❌ الملف غير موجود.")
            return
        project = db.fetch_one("SELECT id, status, expiry_date FROM projects WHERE file_path LIKE ?", (f"%{filename}%",))
        if project:
            if project['status'] == 'running':
                bot.reply_to(message, "⚠️ البوت شغال بالفعل.")
                return
            if project['expiry_date']:
                try:
                    expiry = datetime.strptime(project['expiry_date'], '%Y-%m-%d %H:%M:%S')
                    if expiry < datetime.now():
                        bot.reply_to(message, "❌ انتهت صلاحية المشروع! استخدم 'تجديد صلاحية'")
                        return
                except:
                    pass
            success, msg = project_manager.start_project(project['id'])
            if success and project['id'] in project_manager.projects:
                threading.Thread(
                    target=monitor_project_logs,
                    args=(project['id'], project_manager.projects[project['id']]['process']),
                    daemon=True
                ).start()
            bot.reply_to(message, msg)
        else:
            bot.reply_to(message, "❌ هذا الملف ليس مشروعاً مسجلاً. قم برفعه من جديد.")
    except ValueError:
        bot.reply_to(message, "❌ يرجى إدخال رقم صحيح (مثال: 1)")

def install_lib_step(message):
    lib_name = message.text.strip()
    success, msg = project_manager.install_library(lib_name)
    bot.reply_to(message, msg)

def make_bot_step(message):
    user_id = message.from_user.id
    code = message.text
    filename = f"user_{user_id}_bot_{int(time.time())}.py"
    path = os.path.join(UPLOAD_FOLDER, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    
    days = 1
    project_id = project_manager.create_project(
        user_id,
        path,
        "",
        filename,
        days,
        days,
        'python'
    )
    
    success, msg = project_manager.start_project(project_id)
    if success and project_id in project_manager.projects:
        threading.Thread(
            target=monitor_project_logs,
            args=(project_id, project_manager.projects[project_id]['process']),
            daemon=True
        ).start()
    
    bot.reply_to(message, f"✅ تم إنشاء وتشغيل البوت: {filename}\n{msg}")

def ban_user_step(message):
    try:
        target_user = int(message.text.strip())
        db.execute_query("UPDATE users SET is_banned = 1 WHERE user_id = ?", (target_user,))
        bot.reply_to(message, f"🚫 تم حظر المستخدم: {target_user}")
        try:
            bot.send_message(target_user, "🚫 تم حظرك من البوت بواسطة المطور.")
        except:
            pass
    except ValueError:
        bot.reply_to(message, "❌ يرجى إرسال معرف صحيح (رقم فقط)")

def unban_user_step(message):
    try:
        target_user = int(message.text.strip())
        db.execute_query("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_user,))
        bot.reply_to(message, f"✅ تم إلغاء حظر المستخدم: {target_user}")
        try:
            bot.send_message(target_user, "✅ تم إلغاء حظرك. يمكنك استخدام البوت الآن.")
        except:
            pass
    except ValueError:
        bot.reply_to(message, "❌ يرجى إرسال معرف صحيح (رقم فقط)")

def admin_add_points_step(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            if amount <= 0:
                bot.reply_to(message, "❌ يجب أن تكون النقاط موجبة.")
                return
            new_total = points_manager.add_points(target_id, amount, "إضافة من الأدمن")
            bot.reply_to(message, f"✅ تم إضافة {format_points(amount)} نقطة للمستخدم {target_id}. الرصيد الحالي: {format_points(new_total)}")
        except ValueError:
            bot.reply_to(message, "❌ تأكد من إدخال معرف صحيح وعدد نقاط صحيح.")
    else:
        bot.reply_to(message, "❌ استخدم التنسيق: `معرف_المستخدم عدد_النقاط`")

def admin_remove_points_step(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            if amount <= 0:
                bot.reply_to(message, "❌ يجب أن تكون النقاط موجبة.")
                return
            success, msg = points_manager.remove_points(target_id, amount, "خصم من الأدمن")
            bot.reply_to(message, msg)
        except ValueError:
            bot.reply_to(message, "❌ تأكد من إدخال معرف صحيح وعدد نقاط صحيح.")
    else:
        bot.reply_to(message, "❌ استخدم التنسيق: `معرف_المستخدم عدد_النقاط`")

def admin_broadcast_step(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return
    text = message.text
    users = db.fetch_all("SELECT user_id FROM users WHERE is_banned = 0")
    sent = 0
    failed = 0
    status_msg = bot.reply_to(message, "⏳ جاري إرسال الرسالة الجماعية...")
    for user in users:
        try:
            bot.send_message(user['user_id'], f"📢 **رسالة من الإدارة:**\n\n{text}", parse_mode="Markdown")
            sent += 1
        except:
            failed += 1
        time.sleep(0.05)
    bot.edit_message_text(
        f"✅ **تم إرسال الرسالة الجماعية!**\n\n"
        f"• ✅ تم الإرسال: {sent}\n"
        f"• ❌ فشل: {failed}\n"
        f"• 📊 الإجمالي: {len(users)}",
        chat_id=message.chat.id, message_id=status_msg.id
    )

# ============================================================
# دوال تعديل الكود
# ============================================================

def show_edit_code_panel(chat_id, message_id):
    bot.edit_message_text(
        "📝 <b>نظام تعديل كود البوت</b> 🔐\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👑 هذا القسم متاح فقط للمطور الأساسي\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ <b>تصدير ملف الكود الحالي</b> - تحميل ملف البوت كاملاً\n"
        "2️⃣ <b>استقبال ملف كود معدل</b> - إرسال ملف .py معدل (بدون طلب اسم)\n"
        "3️⃣ <b>حذف كل البيانات وإعادة التشغيل</b> - مسح كامل للبيانات والملفات\n\n"
        "⚠️ <b>تنبيه:</b> الخيار الثالث سيحذف كل شيء ويعيد البوت كأنه جديد!\n\n"
        "🔽 اختر العملية المطلوبة:",
        chat_id, message_id, reply_markup=edit_code_menu(), parse_mode="HTML"
    )

def export_bot_code(call):
    user_id = call.from_user.id
    try:
        current_file = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else os.path.abspath(__file__)
        if not os.path.exists(current_file):
            bot.answer_callback_query(call.id, "❌ لم يتم العثور على ملف الكود!", show_alert=True)
            return
        with open(current_file, 'rb') as f:
            bot.send_document(
                user_id, f,
                caption=f"📤 <b>ملف كود البوت الحالي</b>\n📄 {os.path.basename(current_file)}\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n✏️ قم بتعديله ثم أرسله مرة أخرى",
                visible_file_name=os.path.basename(current_file),
                parse_mode="HTML"
            )
        bot.answer_callback_query(call.id, "✅ تم تصدير ملف الكود بنجاح!")
        show_edit_code_panel(user_id, call.message.id)
    except Exception as e:
        bot.answer_callback_query(call.id, f"❌ خطأ: {str(e)[:50]}", show_alert=True)

def process_received_code_file(message):
    user_id = message.from_user.id
    if user_id not in admin_code_edit_session or admin_code_edit_session[user_id].get("action") != "waiting_for_code_file":
        return
    if not message.document:
        bot.send_message(user_id, "❌ يرجى إرسال <b>ملف</b> وليس نصاً!\nأرسل ملف .py", parse_mode="HTML")
        return
    if not message.document.file_name.endswith('.py'):
        bot.send_message(user_id, "❌ يجب أن يكون الملف بامتداد <code>.py</code>!", parse_mode="HTML")
        return
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        original_file = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else os.path.abspath(__file__)
        backup_file = os.path.join(os.path.dirname(original_file), f"backup_{int(time.time())}_{os.path.basename(original_file)}")
        shutil.copy2(original_file, backup_file)
        temp_new_file = os.path.join(os.path.dirname(original_file), f"new_bot_{user_id}_{int(time.time())}.py")
        with open(temp_new_file, 'wb') as f:
            f.write(downloaded_file)
        shutil.copy2(temp_new_file, original_file)
        if os.path.exists(temp_new_file):
            os.remove(temp_new_file)
        admin_code_edit_session[user_id] = {
            "action": "confirm_restart",
            "backup_file": backup_file,
            "original_file": original_file
        }
        bot.send_message(
            user_id,
            f"✅ <b>تم استلام وحفظ الكود الجديد بنجاح!</b>\n\n"
            f"📄 اسم الملف: <code>{message.document.file_name}</code>\n"
            f"📦 الحجم: {len(downloaded_file)} بايت\n"
            f"💾 النسخة الاحتياطية: <code>{os.path.basename(backup_file)}</code>\n\n"
            f"🔽 اختر الإجراء المناسب:",
            reply_markup=restart_confirm_menu(),
            parse_mode="HTML"
        )
    except Exception as e:
        bot.send_message(user_id, f"❌ خطأ في معالجة الملف: {str(e)}")
        if user_id in admin_code_edit_session:
            del admin_code_edit_session[user_id]

def restart_confirm_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔄 إعادة تشغيل البوت", callback_data="do_restart_bot"),
        types.InlineKeyboardButton("↩️ استعادة النسخة القديمة", callback_data="restore_backup_bot")
    )
    markup.add(types.InlineKeyboardButton("🔙 رجوع لقائمة تعديل الكود", callback_data="back_to_edit_code"))
    return markup

def show_reset_confirm_panel(chat_id, message_id):
    py_files_count = len([f for f in os.listdir(UPLOAD_FOLDER) if f.endswith('.py')]) if os.path.exists(UPLOAD_FOLDER) else 0
    all_files_count = len(os.listdir(UPLOAD_FOLDER)) if os.path.exists(UPLOAD_FOLDER) else 0
    backups_count = len([f for f in os.listdir(os.path.dirname(__file__)) if f.startswith('backup_')]) if os.path.exists(os.path.dirname(__file__)) else 0
    bot.edit_message_text(
        "⚠️ <b>تحذير: حذف كل البيانات</b> ⚠️\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔴 <b>سيتم حذف ما يلي:</b>\n\n"
        f"📁 مجلد الملفات المرفوعة (<code>uploaded_files</code>)\n"
        f"📄 عدد ملفات Python: <b>{py_files_count}</b>\n"
        f"📎 إجمالي الملفات: <b>{all_files_count}</b>\n\n"
        f"💾 حذف النسخ الاحتياطية: <b>{backups_count}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔄 بعد الحذف سيتم إعادة تشغيل البوت تلقائياً\n\n"
        "⚠️ <b>هذا الإجراء لا يمكن التراجع عنه!</b>\n\n"
        "هل أنت متأكد من رغبتك في المتابعة؟",
        chat_id, message_id, reply_markup=reset_confirm_menu(), parse_mode="HTML"
    )

def reset_confirm_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("⚠️ نعم، احذف كل شيء", callback_data="confirm_reset_all"),
        types.InlineKeyboardButton("❌ إلغاء", callback_data="back_to_edit_code")
    )
    return markup

def execute_reset_all(call):
    user_id = call.from_user.id
    try:
        project_manager.stop_all_projects()
        
        deleted_files = 0
        if os.path.exists(UPLOAD_FOLDER):
            deleted_files = len(os.listdir(UPLOAD_FOLDER))
            shutil.rmtree(UPLOAD_FOLDER)
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        
        deleted_backups = 0
        bot_dir = os.path.dirname(os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else os.path.abspath(__file__))
        if os.path.exists(bot_dir):
            for filename in os.listdir(bot_dir):
                if filename.startswith('backup_') and (filename.endswith('.py') or filename.endswith('.json')):
                    try:
                        os.remove(os.path.join(bot_dir, filename))
                        deleted_backups += 1
                    except:
                        pass
        
        summary = (
            f"✅ <b>تم حذف كل البيانات بنجاح!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🗑 تم حذف: <b>{deleted_files}</b> ملف\n"
            f"💾 تم حذف: <b>{deleted_backups}</b> نسخة احتياطية\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 جاري إعادة تشغيل البوت من جديد..."
        )
        bot.edit_message_text(summary, user_id, call.message.id, parse_mode="HTML")
        bot.answer_callback_query(call.id, "✅ جاري إعادة التشغيل...")
        
        def restart():
            time.sleep(3)
            python = sys.executable
            os.execv(python, [python] + sys.argv)
        threading.Thread(target=restart, daemon=True).start()
    except Exception as e:
        bot.edit_message_text(
            f"❌ <b>حدث خطأ أثناء الحذف:</b>\n<code>{str(e)}</code>\n\n"
            f"يرجى التحقق يدوياً من الملفات.",
            user_id, call.message.id, parse_mode="HTML"
        )
        bot.answer_callback_query(call.id, "❌ فشل الحذف!", show_alert=True)

def do_restart_bot(call):
    user_id = call.from_user.id
    if user_id not in admin_code_edit_session or admin_code_edit_session[user_id].get("action") != "confirm_restart":
        bot.answer_callback_query(call.id, "❌ انتهت الجلسة!", show_alert=True)
        return
    session_data = admin_code_edit_session[user_id]
    backup_file = session_data.get("backup_file")
    try:
        bot.edit_message_text(
            f"✅ <b>تم حفظ الكود الجديد!</b>\n\n"
            f"🔄 جاري إعادة تشغيل البوت...\n"
            f"💾 النسخة الاحتياطية: <code>{os.path.basename(backup_file) if backup_file else 'لا يوجد'}</code>\n\n"
            f"⚠️ <b>سيتم إعادة التشغيل خلال لحظات...</b>",
            user_id, call.message.id, parse_mode="HTML"
        )
        del admin_code_edit_session[user_id]
        project_manager.stop_all_projects()
        bot.answer_callback_query(call.id, "✅ جاري إعادة التشغيل...")
        def restart():
            time.sleep(2)
            python = sys.executable
            os.execv(python, [python] + sys.argv)
        threading.Thread(target=restart, daemon=True).start()
    except Exception as e:
        bot.edit_message_text(
            f"❌ <b>خطأ في إعادة التشغيل:</b> {str(e)}\n\n"
            f"يمكنك استعادة النسخة القديمة يدوياً من الملف:\n"
            f"<code>{backup_file}</code>",
            user_id, call.message.id, parse_mode="HTML"
        )
        bot.answer_callback_query(call.id, "❌ فشلت إعادة التشغيل!", show_alert=True)
        if user_id in admin_code_edit_session:
            del admin_code_edit_session[user_id]

def restore_backup_bot(call):
    user_id = call.from_user.id
    if user_id not in admin_code_edit_session or admin_code_edit_session[user_id].get("action") != "confirm_restart":
        bot.answer_callback_query(call.id, "❌ انتهت الجلسة!", show_alert=True)
        return
    session_data = admin_code_edit_session[user_id]
    original_file = session_data.get("original_file")
    backup_file = session_data.get("backup_file")
    try:
        if backup_file and os.path.exists(backup_file):
            shutil.copy2(backup_file, original_file)
            bot.edit_message_text(
                f"✅ <b>تم استعادة الكود القديم بنجاح!</b>\n\n"
                f"🔄 جاري إعادة تشغيل البوت بالنسخة القديمة...",
                user_id, call.message.id, parse_mode="HTML"
            )
            del admin_code_edit_session[user_id]
            project_manager.stop_all_projects()
            bot.answer_callback_query(call.id, "✅ جاري إعادة التشغيل...")
            def restart():
                time.sleep(2)
                python = sys.executable
                os.execv(python, [python] + sys.argv)
            threading.Thread(target=restart, daemon=True).start()
        else:
            bot.edit_message_text("❌ <b>لم يتم العثور على النسخة الاحتياطية!</b>", user_id, call.message.id, parse_mode="HTML")
            bot.answer_callback_query(call.id, "❌ النسخة الاحتياطية غير موجودة!", show_alert=True)
    except Exception as e:
        bot.edit_message_text(f"❌ <b>خطأ في استعادة النسخة القديمة:</b> {str(e)}", user_id, call.message.id, parse_mode="HTML")
        bot.answer_callback_query(call.id, "❌ فشلت الاستعادة!", show_alert=True)
    if user_id in admin_code_edit_session:
        del admin_code_edit_session[user_id]

# ============================================================
# خادم منع النوم
# ============================================================
def keep_alive():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
    server = HTTPServer(('0.0.0.0', KEEP_ALIVE_PORT), Handler)
    print(f"🌐 خادم منع النوم يعمل على المنفذ {KEEP_ALIVE_PORT}")
    server.serve_forever()

# ============================================================
# تشغيل البوت
# ============================================================
if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(PROJECTS_PATH, exist_ok=True)

    print("🚀 البوت يعمل الآن - نظام النقاط فقط")
    print("📌 كل نقطة = يوم واحد من الاستضافة")
    print("✅ نظام النقاط والصلاحيات مفعل!")
    print(f"👑 المطورين الأساسيين ID: {ADMIN_IDS}")
    print(f"📢 القناة: {CHANNEL_ID}")

    def clean_expired():
        while True:
            try:
                project_manager.clean_expired_projects()
            except:
                pass
            time.sleep(3600)
    threading.Thread(target=clean_expired, daemon=True).start()

    bot.infinity_polling()