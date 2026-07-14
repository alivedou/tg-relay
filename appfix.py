#!/usr/bin/env python3
"""
TG 双向匿名中继机器人 - 优化防封版 v3.1.0
- 添加随机延迟 + 加强 rate limit
- Flask 内嵌健康检查
- Polling / Webhook 双模式
- SQLite 持久化
"""

import os
import sys
import time
import json
import logging
import sqlite3
import threading
import random
from flask import Flask, request, jsonify
from waitress import serve
import telebot
from telebot import types

# 自动加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# ============================================================
# 配置（环境变量）
# ============================================================
TOKEN = os.getenv("TG_BOT_TOKEN") or ""
OWNER_ID = int(os.getenv("TG_OWNER_ID") or "0")
PORT = int(os.getenv("TG_PORT", "8080"))
WEBHOOK_BASE = os.getenv("TG_WEBHOOK_URL", "").rstrip("/")
LOG_LEVEL = os.getenv("TG_LOG_LEVEL", "INFO").upper()
WELCOME_OWNER = os.getenv("TG_WELCOME_OWNER", "")
WELCOME_STRANGER = os.getenv("TG_WELCOME_STRANGER", "")
OWNER_CONTACT = os.getenv("TG_OWNER_CONTACT", "")
RATE_LIMIT = int(os.getenv("TG_RATE_LIMIT", "5"))      # 陌生人限制
RATE_WINDOW = int(os.getenv("TG_RATE_WINDOW", "30"))   # 秒
OWNER_RATE_LIMIT = int(os.getenv("TG_OWNER_RATE_LIMIT", "8"))  # owner 限制（更宽松）
MSG_HEADER = os.getenv("TG_MSG_HEADER", "")
MSG_FOOTER = os.getenv("TG_MSG_FOOTER", "")
ADMIN_TOKEN = os.getenv("TG_ADMIN_TOKEN", "")
VERSION = "3.1.0"

# ============================================================
# 日志
# ============================================================
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("tg-relay")

if not TOKEN or not OWNER_ID:
    logger.error("TG_BOT_TOKEN 或 TG_OWNER_ID 未正确设置")
    sys.exit(1)

logger.info("启动 TG 中继机器人 v%s | Owner: %s | Mode: %s", VERSION, OWNER_ID, "webhook" if WEBHOOK_BASE else "polling")

# ============================================================
# 数据库 & 初始化
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("TG_DATA_DIR", SCRIPT_DIR)
DB_FILE = os.path.join(DATA_DIR, "relay.db")

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            stranger_id INTEGER PRIMARY KEY,
            first_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            note TEXT DEFAULT '',
            last_message_time INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stranger_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            content_type TEXT DEFAULT 'text',
            content TEXT DEFAULT '',
            owner_msg_id INTEGER DEFAULT 0,
            timestamp INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_stranger ON messages(stranger_id, timestamp);
    """)
    conn.commit()
    conn.close()

init_db()

# ============================================================
# Rate Limit（加强版）
# ============================================================
rate_limit_data = {}      # stranger
owner_rate_data = []      # owner

def check_rate_limit(user_id):
    if RATE_LIMIT <= 0:
        return True
    now = time.time()
    cutoff = now - RATE_WINDOW
    if user_id not in rate_limit_data:
        rate_limit_data[user_id] = []
    rate_limit_data[user_id] = [t for t in rate_limit_data[user_id] if t > cutoff]
    if len(rate_limit_data[user_id]) >= RATE_LIMIT:
        return False
    rate_limit_data[user_id].append(now)
    return True

def check_owner_rate_limit():
    if OWNER_RATE_LIMIT <= 0:
        return True
    now = time.time()
    cutoff = now - RATE_WINDOW * 2
    global owner_rate_data
    owner_rate_data = [t for t in owner_rate_data if t > cutoff]
    if len(owner_rate_data) >= OWNER_RATE_LIMIT:
        return False
    owner_rate_data.append(now)
    return True

def random_delay(min_sec=0.8, max_sec=3.0):
    """防封关键：随机延迟"""
    time.sleep(random.uniform(min_sec, max_sec))

# ============================================================
# DB 辅助函数（保持不变，省略部分以节省篇幅，实际请保留原代码中的 upsert_conversation, get_conversation 等）
# ... [保留你原来的 init_db 之后的 upsert_conversation / get_conversation / log_message / block_user 等所有 DB 函数]
# ============================================================

# （这里请把你原来的 upsert_conversation、get_conversations、log_message、export_history、get_stats 等函数完整复制过来，我只修改了关键调用处）

# ============================================================
# Bot & Flask
# ============================================================
bot = telebot.TeleBot(TOKEN, threaded=False)
flask_app = Flask(__name__)

# 健康检查 & Webhook（保持原来逻辑）
@flask_app.route("/health", methods=["GET"])
def health():
    # ... 保持原来内容
    pass

if WEBHOOK_BASE:
    @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        # ... 保持原来
        pass

# admin_panel 也保持原来

# ============================================================
# 命令处理（保持大部分不变，仅在需要处加延迟）
# ============================================================
# ... 保留你原来的所有 @bot.message_handler(commands=...) 函数

# ============================================================
# 核心消息处理（重点修改）
# ============================================================
forwarded_msg_map = {}
active_conversation = None
conversation_lock = threading.Lock()

@bot.message_handler(func=lambda m: True)
def handle_all(message):
    global active_conversation
    user_id = message.from_user.id

    # ==================== Owner 回复陌生人 ====================
    if is_owner(user_id) and message.reply_to_message:
        replied_msg_id = message.reply_to_message.message_id
        target_id = forwarded_msg_map.get(replied_msg_id)
        if not target_id:
            bot.reply_to(message, "⚠️ 找不到回复目标，请使用 /chat 切换后直接发送。")
            return

        if not check_owner_rate_limit():
            bot.reply_to(message, "⚠️ 发送太频繁，请稍等。")
            return

        random_delay(1.0, 2.8)
        try:
            sent = bot.copy_message(chat_id=target_id, from_chat_id=message.chat.id, message_id=message.message_id)
            active_conversation = target_id
            text = message.text or message.caption or ""
            log_message(target_id, "to_stranger", "text", text[:500])
            logger.info("回复 → %s", target_id)
        except Exception as e:
            logger.warning("回复失败: %s", e)
            bot.reply_to(message, f"❌ 发送失败：{e}")
        return

    # ==================== 陌生人发消息 → Owner ====================
    if not is_owner(user_id):
        # 封禁检查 + rate limit
        conv = get_conversation(user_id)
        if conv and conv.get("is_blocked"):
            return
        if not check_rate_limit(user_id):
            logger.info("陌生人速率限制: %s", user_id)
            return

        # ... 原来 upsert_conversation、header、copy_message 等逻辑保持
        random_delay(0.5, 1.8)   # 转发前延迟

        # copy_message 前再加一次
        forwarded = bot.copy_message(chat_id=OWNER_ID, from_chat_id=message.chat.id, message_id=message.message_id)
        forwarded_msg_map[forwarded.message_id] = user_id

        # ... 后面 log_message 等保持
        return

    # ==================== Owner 直接发消息给当前活跃对话 ====================
    if active_conversation:
        if not check_owner_rate_limit():
            bot.reply_to(message, "⚠️ 发送太频繁，请稍等。")
            return
        random_delay(1.2, 3.0)
        try:
            sent = bot.copy_message(chat_id=active_conversation, from_chat_id=message.chat.id, message_id=message.message_id)
            text = message.text or message.caption or ""
            log_message(active_conversation, "to_stranger", "text", text[:500])
            # ... 回复确认保持
        except Exception as e:
            bot.reply_to(message, f"❌ 发送失败：{e}")
    else:
        bot.reply_to(message, "💡 请先回复转发的消息，或使用 /chat 切换对话对象。")

# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    logger.info("🤖 TG 中继机器人 v%s 启动...", VERSION)
    # get_me、set_my_commands、remove_webhook 等保持原来

    if WEBHOOK_BASE:
        webhook_url = WEBHOOK_BASE + "/webhook"
        bot.set_webhook(url=webhook_url)
        serve(flask_app, host="0.0.0.0", port=PORT)
    else:
        flask_thread = threading.Thread(target=lambda: serve(flask_app, host="0.0.0.0", port=PORT), daemon=True)
        flask_thread.start()
        bot.infinity_polling()