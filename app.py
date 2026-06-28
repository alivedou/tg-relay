#!/usr/bin/env python3
"""
TG 双向匿名中继机器人
- Flask 内嵌 HTTP 健康检查服务器
- Polling / Webhook 双模式自适应
- 多对话支持 + SQLite 持久化
- 全配置环境变量驱动
"""

import os
import sys
import time
import json
import logging
import sqlite3
import threading

from flask import Flask, request, jsonify
from waitress import serve
import telebot
from telebot import types

# 自动加载 .env 文件（如果存在）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# ============================================================
# 配置（全部从环境变量读取）
# ============================================================
TOKEN = os.getenv("TG_BOT_TOKEN") or ""
OWNER_ID = int(os.getenv("TG_OWNER_ID") or "0")
PORT = int(os.getenv("TG_PORT", "8080"))
WEBHOOK_BASE = os.getenv("TG_WEBHOOK_URL", "")
LOG_LEVEL = os.getenv("TG_LOG_LEVEL", "INFO").upper()
WELCOME_OWNER = os.getenv("TG_WELCOME_OWNER", "")
WELCOME_STRANGER = os.getenv("TG_WELCOME_STRANGER", "")
OWNER_CONTACT = os.getenv("TG_OWNER_CONTACT", "")
RATE_LIMIT = int(os.getenv("TG_RATE_LIMIT", "0"))
RATE_WINDOW = int(os.getenv("TG_RATE_WINDOW", "10"))
MSG_HEADER = os.getenv("TG_MSG_HEADER", "")
MSG_FOOTER = os.getenv("TG_MSG_FOOTER", "")
ADMIN_TOKEN = os.getenv("TG_ADMIN_TOKEN", "")
VERSION = "3.0.0"

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tg-relay")

# ============================================================
# 启动自检
# ============================================================
if not TOKEN:
    logger.error("TG_BOT_TOKEN 未设置，退出")
    sys.exit(1)
if not OWNER_ID:
    logger.error("TG_OWNER_ID 未设置或为 0，退出")
    sys.exit(1)

_token_masked = TOKEN[:6] + "..." + TOKEN[-4:] if len(TOKEN) > 10 else "***"
logger.info("配置: owner_id=%s, port=%s, log_level=%s, mode=%s",
            OWNER_ID, PORT, LOG_LEVEL, "webhook" if WEBHOOK_BASE else "polling")
logger.info("Token: %s", _token_masked)

START_TIME = time.time()

# ============================================================
# 数据目录和数据库
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
            timestamp INTEGER NOT NULL,
            FOREIGN KEY (stranger_id) REFERENCES conversations(stranger_id)
        );
        CREATE INDEX IF NOT EXISTS idx_messages_stranger ON messages(stranger_id, timestamp);
    """)
    conn.commit()
    conn.close()

init_db()

# ============================================================
# 速率限制（内存）
# ============================================================
rate_limit_data = {}  # user_id -> [timestamps]

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

def block_user(stranger_id):
    conn = get_db()
    conn.execute("UPDATE conversations SET is_blocked = 1 WHERE stranger_id = ?", (stranger_id,))
    conn.commit()
    conn.close()

def unblock_user(stranger_id):
    conn = get_db()
    conn.execute("UPDATE conversations SET is_blocked = 0 WHERE stranger_id = ?", (stranger_id,))
    conn.commit()
    conn.close()

def get_blocked_users():
    conn = get_db()
    rows = conn.execute("SELECT * FROM conversations WHERE is_blocked = 1").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def export_history(stranger_id):
    conv = get_conversation(stranger_id)
    msgs = get_history(stranger_id, limit=1000)
    if not conv:
        return None
    name = conv["first_name"] or "未知"
    username = f" (@{conv['username']})" if conv["username"] else ""
    lines = [f"对话记录: {name}{username} (ID: {stranger_id})", "=" * 40]
    for m in reversed(msgs):
        arrow = "<-" if m["direction"] == "from_stranger" else "->"
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m["timestamp"]))
        content = m["content"] or f"[{m['content_type']}]"
        lines.append(f"[{ts}] {arrow} {content}")
    return "\n".join(lines)

# ============================================================
# 对话状态（内存 + DB）
# ============================================================
forwarded_msg_map = {}  # owner_side_msg_id -> stranger_id
active_conversation = None  # 当前活跃的 stranger_id
conversation_lock = threading.Lock()

def upsert_conversation(stranger_id, first_name="", username=""):
    conn = get_db()
    conn.execute("""
        INSERT INTO conversations (stranger_id, first_name, username, last_message_time)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(stranger_id) DO UPDATE SET
            first_name = COALESCE(NULLIF(?, ''), first_name),
            username = COALESCE(NULLIF(?, ''), username),
            last_message_time = ?,
            message_count = message_count + 1
    """, (stranger_id, first_name, username, int(time.time()),
          first_name, username, int(time.time())))
    conn.commit()
    conn.close()

def get_conversations():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE is_blocked = 0 ORDER BY last_message_time DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_conversation(stranger_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM conversations WHERE stranger_id = ?", (stranger_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def set_note(stranger_id, note):
    conn = get_db()
    conn.execute("UPDATE conversations SET note = ? WHERE stranger_id = ?", (note, stranger_id))
    conn.commit()
    conn.close()

def log_message(stranger_id, direction, content_type, content, owner_msg_id=0):
    conn = get_db()
    conn.execute(
        "INSERT INTO messages (stranger_id, direction, content_type, content, owner_msg_id, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (stranger_id, direction, content_type, content, owner_msg_id, int(time.time()))
    )
    conn.commit()
    conn.close()

def get_history(stranger_id, limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE stranger_id = ? ORDER BY timestamp DESC LIMIT ?",
        (stranger_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_stats():
    conn = get_db()
    total_users = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    today_start = int(time.time()) - (int(time.time()) % 86400)
    today_messages = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE timestamp >= ?", (today_start,)
    ).fetchone()[0]
    conn.close()
    return {
        "total_users": total_users,
        "total_messages": total_messages,
        "today_messages": today_messages,
    }

def is_owner(user_id):
    return user_id == OWNER_ID

# ============================================================
# Bot 实例
# ============================================================
bot = telebot.TeleBot(TOKEN, threaded=False)

# ============================================================
# Flask 健康检查服务器
# ============================================================
flask_app = Flask(__name__)

@flask_app.route("/health", methods=["GET"])
def health():
    mode = "webhook" if WEBHOOK_BASE else "polling"
    stats = get_stats()
    convos = get_conversations()
    return jsonify({
        "status": "ok",
        "version": VERSION,
        "mode": mode,
        "uptime": int(time.time() - START_TIME),
        "active_conversations": len(convos),
        "total_users": stats["total_users"],
        "total_messages": stats["total_messages"],
        "today_messages": stats["today_messages"],
    })

if WEBHOOK_BASE:
    @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        if request.headers.get("content-type") == "application/json":
            json_string = request.get_data().decode("utf-8")
            update = types.Update.de_json(json_string)
            bot.process_new_updates([update])
            return ""
        return "Bad Request", 400

@flask_app.route("/admin")
def admin_panel():
    if ADMIN_TOKEN and request.args.get("token") != ADMIN_TOKEN:
        return "Unauthorized", 403
    stats = get_stats()
    convos = get_conversations()
    uptime_sec = int(time.time() - START_TIME)
    hours = uptime_sec // 3600
    mins = (uptime_sec % 3600) // 60
    convos_html = ""
    for c in convos[:20]:
        name = c["first_name"] or "未知"
        username = f" @{c['username']}" if c["username"] else ""
        note = f" [{c['note']}]" if c["note"] else ""
        convos_html += f"<tr><td>{c['stranger_id']}</td><td>{name}{username}{note}</td><td>{c['message_count']}</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><title>TG Relay Bot v{VERSION}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:system-ui,sans-serif;max-width:800px;margin:20px auto;padding:0 16px;background:#111;color:#eee}}
.card{{background:#1a1a1a;border-radius:8px;padding:16px;margin:12px 0}}
h1,h2{{color:#4fc3f7}} .stat{{font-size:24px;font-weight:bold;color:#81c784}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:8px;text-align:left;border-bottom:1px solid #333}}
th{{color:#4fc3f7}} .bar{{display:flex;gap:16px;flex-wrap:wrap}}
</style></head><body>
<h1>🤖 TG Relay Bot <small>v{VERSION}</small></h1>
<div class="bar">
<div class="card"><div>运行时间</div><div class="stat">{hours}h {mins}m</div></div>
<div class="card"><div>总用户</div><div class="stat">{stats['total_users']}</div></div>
<div class="card"><div>总消息</div><div class="stat">{stats['total_messages']}</div></div>
<div class="card"><div>今日消息</div><div class="stat">{stats['today_messages']}</div></div>
<div class="card"><div>活跃对话</div><div class="stat">{len(convos)}</div></div>
<div class="card"><div>模式</div><div class="stat">{'Webhook' if WEBHOOK_BASE else 'Polling'}</div></div>
</div>
<h2>活跃对话 TOP 20</h2>
<table><tr><th>ID</th><th>用户</th><th>消息数</th></tr>{convos_html}</table>
<p style="color:#666;margin-top:20px">TG Relay Bot - 消息中继代理</p>
</body></html>"""

# ============================================================
# 链接管理
# ============================================================
LINKS_FILE = os.path.join(DATA_DIR, "links.json")

DEFAULT_LINKS = [
    {"name": "GitHub", "url": "https://github.com", "category": "开发"},
    {"name": "StackOverflow", "url": "https://stackoverflow.com", "category": "开发"},
    {"name": "Python 文档", "url": "https://docs.python.org/zh-cn/", "category": "学习"},
    {"name": "Telegram 开发", "url": "https://core.telegram.org/bots", "category": "开发"},
    {"name": "维基百科", "url": "https://zh.wikipedia.org", "category": "常用"},
]

def load_links():
    if os.path.exists(LINKS_FILE):
        try:
            with open(LINKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.warning("加载 links.json 失败，使用默认链接")
    return DEFAULT_LINKS

def save_links(links):
    with open(LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=2)

def get_links_by_category(category=None):
    links = load_links()
    if category:
        return [link for link in links if link.get("category") == category]
    return links

def add_link(name, url, category="常用"):
    links = load_links()
    if any(link["name"] == name for link in links):
        return False, "链接已存在"
    links.append({"name": name, "url": url, "category": category})
    save_links(links)
    return True, f"✅ 已添加链接：{name}"

def delete_link(name):
    links = load_links()
    original_count = len(links)
    links = [link for link in links if link["name"] != name]
    if len(links) == original_count:
        return False, "链接不存在"
    save_links(links)
    return True, f"✅ 已删除链接：{name}"

def edit_link(old_name, new_name=None, new_url=None, new_category=None):
    links = load_links()
    for link in links:
        if link["name"] == old_name:
            if new_name:
                link["name"] = new_name
            if new_url:
                link["url"] = new_url
            if new_category:
                link["category"] = new_category
            save_links(links)
            return True, f"✅ 已更新链接：{link['name']}"
    return False, "链接不存在"

def find_links(keyword):
    keyword_lower = keyword.lower()
    links = load_links()
    results = []
    for link in links:
        if keyword_lower in link["name"].lower() or keyword_lower in link["url"].lower() or keyword_lower in link.get("category", "").lower():
            results.append(link)
    return results

# ============================================================
# 命令处理
# ============================================================
@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    if is_owner(message.from_user.id):
        if WELCOME_OWNER:
            bot.reply_to(message, WELCOME_OWNER)
        else:
            bot.reply_to(message,
                "👋 你是 owner。陌生人发消息给你会通过我转发。\n\n"
                "回复我转发的消息即可回复陌生人。\n"
                "/who — 查看当前对话对象\n"
                "/queue — 查看待回复队列\n"
                "/chat — 切换对话对象\n"
                "/links — 查看可用链接\n"
                "/linkadd — 添加新链接")
    else:
        if WELCOME_STRANGER:
            bot.reply_to(message, WELCOME_STRANGER)
        else:
            bot.reply_to(message,
                "👋 你好！你的消息会匿名转发给 bot 主人。\n"
                "/links — 查看可用链接")

@bot.message_handler(commands=["ping"])
def handle_ping(message):
    latency_ms = int((time.time() - message.date) * 1000)
    bot.reply_to(message, f"🏓 Pong!\n响应: {latency_ms}ms")

@bot.message_handler(commands=["id"])
def handle_id(message):
    user = message.from_user
    username = f" (@{user.username})" if user.username else ""
    bot.reply_to(message, f"🆔 {user.first_name or '未知'}{username}\n"
                 f"ID: `{user.id}`", parse_mode="Markdown")

@bot.message_handler(commands=["about"])
def handle_about(message):
    uptime_sec = int(time.time() - START_TIME)
    hours = uptime_sec // 3600
    mins = (uptime_sec % 3600) // 60
    secs = uptime_sec % 60
    mode = "Webhook" if WEBHOOK_BASE else "Polling"
    contact = OWNER_CONTACT or "(未设置)"
    convos = get_conversations()
    bot.reply_to(message,
        f"🤖 TG 中继机器人 v{VERSION}\n"
        f"模式: {mode}\n"
        f"运行时间: {hours}h {mins}m {secs}s\n"
        f"活跃对话: {len(convos)}\n"
        f"联系方式: {contact}")

@bot.message_handler(commands=["stats"])
def handle_stats(message):
    if not is_owner(message.from_user.id):
        return
    stats = get_stats()
    convos = get_conversations()
    top_users = sorted(convos, key=lambda c: c["message_count"], reverse=True)[:5]
    top_text = ""
    for i, c in enumerate(top_users, 1):
        name = c["first_name"] or "未知"
        top_text += f"  {i}. {name} ({c['message_count']}条)\n"
    bot.reply_to(message,
        f"📊 统计面板\n\n"
        f"总用户: {stats['total_users']}\n"
        f"总消息: {stats['total_messages']}\n"
        f"今日消息: {stats['today_messages']}\n"
        f"活跃对话: {len(convos)}\n"
        f"速率限制: {'关闭' if RATE_LIMIT <= 0 else f'{RATE_LIMIT}条/{RATE_WINDOW}s'}\n"
        f"\nTop 5 活跃用户:\n{top_text or '  (暂无)'}")

@bot.message_handler(commands=["who"])
def handle_who(message):
    if not is_owner(message.from_user.id):
        return
    global active_conversation
    if active_conversation:
        conv = get_conversation(active_conversation)
        if conv:
            name = conv["first_name"] or "未知"
            username = f" (@{conv['username']})" if conv["username"] else ""
            note = f"\n备注: {conv['note']}" if conv["note"] else ""
            count = conv["message_count"]
            bot.reply_to(message,
                f"当前对话: {name}{username}\n"
                f"ID: `{active_conversation}`\n"
                f"消息数: {count}{note}",
                parse_mode="Markdown")
            return
    bot.reply_to(message, "当前没有活跃对话。陌生人发消息会自动设为活跃。\n"
                 "使用 /queue 查看所有对话，/chat <序号> 切换。")

@bot.message_handler(commands=["queue"])
def handle_queue(message):
    if not is_owner(message.from_user.id):
        return
    convos = get_conversations()
    if not convos:
        bot.reply_to(message, "📭 当前没有待回复的对话。")
        return
    result = f"📋 待回复队列（共 {len(convos)} 人）\n\n"
    for i, c in enumerate(convos, 1):
        name = c["first_name"] or "未知"
        username = f" (@{c['username']})" if c["username"] else ""
        note = f" 📝{c['note']}" if c["note"] else ""
        active = " ⬅ 当前" if c["stranger_id"] == active_conversation else ""
        count = c["message_count"]
        result += f"{i}. {name}{username} [{count}条]{note}{active}\n"
    result += "\n回复此消息或使用 /chat <序号> 切换对话对象"
    bot.reply_to(message, result)

@bot.message_handler(commands=["chat"])
def handle_chat(message):
    if not is_owner(message.from_user.id):
        return
    global active_conversation
    if len(message.text.split()) < 2:
        convos = get_conversations()
        if not convos:
            bot.reply_to(message, "📭 没有对话可切换。")
            return
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        buttons = []
        for i, c in enumerate(convos[:10], 1):
            name = c["first_name"] or "未知"
            username = f" @{c['username']}" if c["username"] else ""
            label = f"{i}. {name}{username}"[:40]
            buttons.append(types.InlineKeyboardButton(
                label, callback_data=f"chat_{c['stranger_id']}"))
        keyboard.add(*buttons)
        bot.reply_to(message, "选择要切换的对话对象：", reply_markup=keyboard)
        return
    target = message.text.split(" ", 1)[1].strip()
    if target.isdigit():
        convos = get_conversations()
        index = int(target) - 1
        if 0 <= index < len(convos):
            active_conversation = convos[index]["stranger_id"]
            name = convos[index]["first_name"] or "未知"
            bot.reply_to(message, f"✅ 已切换到: {name} (ID: {active_conversation})")
        else:
            bot.reply_to(message, f"❌ 序号 {target} 超出范围（共 {len(convos)} 人）")
    else:
        try:
            sid = int(target)
        except ValueError:
            bot.reply_to(message, "❌ 请输入有效序号或用户 ID")
            return
        conv = get_conversation(sid)
        if conv:
            active_conversation = sid
            bot.reply_to(message, f"✅ 已切换到: {conv['first_name'] or '未知'} (ID: {sid})")
        else:
            bot.reply_to(message, f"❌ 未找到用户 ID: {sid}")

@bot.message_handler(commands=["note"])
def handle_note(message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split(" ", 2)
    if len(parts) < 3:
        bot.reply_to(message, "⚠️ 用法：/note <user_id> <备注内容>")
        return
    try:
        sid = int(parts[1])
    except ValueError:
        bot.reply_to(message, "❌ 无效的用户 ID")
        return
    note_text = parts[2].strip()
    if not note_text:
        bot.reply_to(message, "❌ 备注不能为空")
        return
    set_note(sid, note_text)
    bot.reply_to(message, f"✅ 已为用户 {sid} 添加备注: {note_text}")

@bot.message_handler(commands=["history"])
def handle_history(message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split(" ", 1)
    sid = active_conversation
    if len(parts) > 1:
        target = parts[1].strip()
        if target.isdigit():
            convos = get_conversations()
            index = int(target) - 1
            if 0 <= index < len(convos):
                sid = convos[index]["stranger_id"]
            else:
                try:
                    sid = int(target)
                except ValueError:
                    pass
    if not sid:
        bot.reply_to(message, "❌ 没有可查看的对话。先等陌生人发消息吧。")
        return
    msgs = get_history(sid, limit=10)
    if not msgs:
        bot.reply_to(message, "📭 暂无消息记录。")
        return
    conv = get_conversation(sid)
    name = conv["first_name"] if conv else "未知"
    result = f"📜 {name} (ID: {sid}) 最近消息:\n\n"
    for m in reversed(msgs):
        arrow = "⬅" if m["direction"] == "from_stranger" else "➡"
        ts = time.strftime("%m-%d %H:%M", time.localtime(m["timestamp"]))
        content = m["content"][:50] + ("..." if len(m.get("content", "")) > 50 else "")
        if not content:
            content = f"[{m['content_type']}]"
        result += f"{arrow} [{ts}] {content}\n"
    bot.reply_to(message, result)

@bot.message_handler(commands=["ban"])
def handle_ban(message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split(" ", 1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ 用法：/ban <user_id>\n示例：/ban 123456789")
        return
    try:
        sid = int(parts[1].strip())
    except ValueError:
        bot.reply_to(message, "❌ 无效的用户 ID")
        return
    block_user(sid)
    name = get_conversation(sid)
    name_str = (name["first_name"] if name else "未知") or "未知"
    bot.reply_to(message, f"🚫 已封禁: {name_str} (ID: {sid})")
    logger.info("封禁用户: %s (%s)", sid, name_str)

@bot.message_handler(commands=["unban"])
def handle_unban(message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split(" ", 1)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ 用法：/unban <user_id>\n示例：/unban 123456789")
        return
    try:
        sid = int(parts[1].strip())
    except ValueError:
        bot.reply_to(message, "❌ 无效的用户 ID")
        return
    unblock_user(sid)
    bot.reply_to(message, f"✅ 已解封用户 ID: {sid}")

@bot.message_handler(commands=["banlist"])
def handle_banlist(message):
    if not is_owner(message.from_user.id):
        return
    blocked = get_blocked_users()
    if not blocked:
        bot.reply_to(message, "📭 没有被封禁的用户。")
        return
    result = "🚫 封禁列表:\n\n"
    for b in blocked:
        name = b["first_name"] or "未知"
        username = f" (@{b['username']})" if b["username"] else ""
        result += f"  • {name}{username} (ID: {b['stranger_id']})\n"
    bot.reply_to(message, result)

@bot.message_handler(commands=["send"])
def handle_send(message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split(" ", 2)
    if len(parts) < 3:
        bot.reply_to(message, "⚠️ 用法：/send <user_id> <消息内容>")
        return
    try:
        sid = int(parts[1])
    except ValueError:
        bot.reply_to(message, "❌ 无效的用户 ID")
        return
    text = parts[2]
    try:
        bot.send_message(sid, text)
        upsert_conversation(sid)
        log_message(sid, "to_stranger", "text", text[:500])
        bot.reply_to(message, f"✅ 已发送给 ID: {sid}")
    except Exception as e:
        bot.reply_to(message, f"❌ 发送失败：{e}")

@bot.message_handler(commands=["export"])
def handle_export(message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split(" ", 1)
    target_sid = None
    if len(parts) > 1:
        arg = parts[1].strip()
        if arg.isdigit():
            convos = get_conversations()
            index = int(arg) - 1
            if 0 <= index < len(convos):
                target_sid = convos[index]["stranger_id"]
            else:
                try:
                    target_sid = int(arg)
                except ValueError:
                    pass
    if not target_sid:
        target_sid = active_conversation
    if not target_sid:
        bot.reply_to(message, "❌ 没有可导出的对话。请指定用户 ID 或序号。")
        return
    text = export_history(target_sid)
    if not text:
        bot.reply_to(message, "❌ 未找到该用户的对话记录。")
        return
    if len(text) > 4000:
        text = text[:4000] + "\n...(已截断)"
    bot.reply_to(message, text)

@bot.message_handler(commands=["linkadd"])
def handle_linkadd(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ 仅限 owner 使用")
        return
    if len(message.text.split()) < 2:
        bot.reply_to(message,
            "⚠️ 用法：\n"
            "  /linkadd <name>;<url>              （默认类别：常用）\n"
            "  /linkadd <name>;<url>;<category>   （指定类别）\n\n"
            "示例：\n"
            "  /linkadd VSCode;https://code.visualstudio.com\n"
            "  /linkadd Python;https://www.python.org;学习")
        return
    rest = message.text[len("/linkadd") + 1:].strip()
    parts = rest.split(";", 2)
    if len(parts) < 2:
        bot.reply_to(message, "❌ 格式错误，请使用分号分隔")
        return
    name = parts[0].strip()
    url = parts[1].strip()
    category = parts[2].strip() if len(parts) > 2 else "常用"
    if not name or not url:
        bot.reply_to(message, "❌ 链接名和 URL 不能为空")
        return
    success, msg = add_link(name, url, category)
    bot.reply_to(message, msg)

@bot.message_handler(commands=["linkdel"])
def handle_linkdel(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ 仅限 owner 使用")
        return
    if len(message.text.split()) < 2:
        bot.reply_to(message,
            "⚠️ 用法：\n"
            "  /linkdel <序号>        （删除指定序号的链接）\n"
            "  /linkdel <链接名>      （删除指定名称的链接）\n\n"
            "示例：\n"
            "  /linkdel 1              # 删除第 1 个链接\n"
            "  /linkdel VSCode        # 删除名为 VSCode 的链接")
        return
    target = message.text.split(" ", 1)[1].strip()
    if target.isdigit():
        index = int(target) - 1
        links = load_links()
        if index < 0 or index >= len(links):
            bot.reply_to(message, f"❌ 序号 {target} 超出范围（共 {len(links)} 个链接）")
            return
        deleted_name = links[index]["name"]
        links.pop(index)
        save_links(links)
        bot.reply_to(message, f"✅ 已删除链接：{deleted_name}")
        return
    success, msg = delete_link(target)
    bot.reply_to(message, msg)

def build_links_keyboard(page=0, category=None):
    links = get_links_by_category(category)
    per_page = 6
    total_pages = (len(links) + per_page - 1) // per_page
    start = page * per_page
    page_links = links[start:start + per_page]
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for i, link in enumerate(page_links, start + 1):
        keyboard.add(types.InlineKeyboardButton(
            f"{i}. {link['name']}", url=link["url"]))
    nav_row = []
    if page > 0:
        nav_row.append(types.InlineKeyboardButton(
            "◀ 上一页", callback_data=f"links_{page-1}_{category or ''}"))
    if page < total_pages - 1:
        nav_row.append(types.InlineKeyboardButton(
            "下一页 ▶", callback_data=f"links_{page+1}_{category or ''}"))
    if nav_row:
        keyboard.row(*nav_row)
    return keyboard

@bot.message_handler(commands=["links"])
def handle_links(message):
    category = None
    if len(message.text.split()) > 1:
        category = message.text.split(" ", 1)[1]
    links = get_links_by_category(category)
    if not links:
        bot.reply_to(message, "❌ 没有可用链接" if category else "❌ 没有链接")
        return
    keyboard = build_links_keyboard(0, category)
    bot.reply_to(message, f"🔗 可用链接（共 {len(links)} 条）", reply_markup=keyboard)

@bot.message_handler(commands=["linkcat"])
def handle_linkcat(message):
    if len(message.text.split()) < 2:
        bot.reply_to(message, "⚠️ 用法：/linkcat <类别>\n\n示例：/linkcat 开发")
        return
    category = message.text.split(" ", 1)[1]
    links = get_links_by_category(category)
    if not links:
        bot.reply_to(message, f"❌ 类别 '{category}' 中没有链接")
        return
    keyboard = build_links_keyboard(0, category)
    bot.reply_to(message, f"📁 类别：{category}（共 {len(links)} 条）", reply_markup=keyboard)

@bot.message_handler(commands=["linkedit"])
def handle_linkedit(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ 仅限 owner 使用")
        return
    if len(message.text.split()) < 2:
        bot.reply_to(message,
            "⚠️ 用法：\n"
            "  /linkedit <旧名称>;<新名称>;<新URL>;<新类别>\n"
            "可以只改部分字段（留空表示不修改）\n\n"
            "示例：\n"
            "  /linkedit GitHub;;https://github.com/new;\n"
            "  /linkedit GitHub;NewName;;")
        return
    rest = message.text[len("/linkedit") + 1:].strip()
    parts = rest.split(";", 3)
    if len(parts) < 1 or not parts[0].strip():
        bot.reply_to(message, "❌ 必须指定要修改的链接名称")
        return
    old_name = parts[0].strip()
    new_name = parts[1].strip() if len(parts) > 1 else ""
    new_url = parts[2].strip() if len(parts) > 2 else ""
    new_category = parts[3].strip() if len(parts) > 3 else ""
    if not new_name and not new_url and not new_category:
        bot.reply_to(message, "❌ 至少需要指定一个新值")
        return
    success, msg = edit_link(old_name,
        new_name=new_name or None,
        new_url=new_url or None,
        new_category=new_category or None)
    bot.reply_to(message, msg)

@bot.message_handler(commands=["linkfind"])
def handle_linkfind(message):
    if len(message.text.split()) < 2:
        bot.reply_to(message, "⚠️ 用法：/linkfind <关键词>\n\n示例：/linkfind python")
        return
    keyword = message.text.split(" ", 1)[1].strip()
    results = find_links(keyword)
    if not results:
        bot.reply_to(message, f"❌ 未找到包含 '{keyword}' 的链接")
        return
    result = f"🔍 搜索 '{keyword}'（共 {len(results)} 条）\n\n"
    for i, link in enumerate(results, 1):
        result += f"  {i}. [{link['name']}]({link['url']}) — {link['category']}\n"
    bot.reply_to(message, result, parse_mode="Markdown")

# ============================================================
# InlineKeyboard 回调
# ============================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("chat_"))
def callback_chat(call):
    global active_conversation
    if not is_owner(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ 仅限 owner")
        return
    sid = int(call.data.split("_", 1)[1])
    conv = get_conversation(sid)
    if conv:
        active_conversation = sid
        name = conv["first_name"] or "未知"
        bot.answer_callback_query(call.id, f"✅ 已切换到: {name}")
        bot.edit_message_text(
            f"✅ 当前对话: {name} (ID: {sid})",
            call.message.chat.id, call.message.message_id)
    else:
        bot.answer_callback_query(call.id, "❌ 用户不存在")

@bot.callback_query_handler(func=lambda call: call.data.startswith("links_"))
def callback_links(call):
    parts = call.data.split("_")
    page = int(parts[1])
    category = parts[2] if len(parts) > 2 and parts[2] else None
    keyboard = build_links_keyboard(page, category)
    links = get_links_by_category(category)
    label = f"🔗 可用链接（共 {len(links)} 条）"
    if category:
        label = f"📁 类别：{category}（共 {len(links)} 条）"
    bot.edit_message_text(label, call.message.chat.id, call.message.message_id,
                          reply_markup=keyboard)
    bot.answer_callback_query(call.id)

# ============================================================
# 核心消息路由（多对话）
# ============================================================
@bot.message_handler(func=lambda m: True)
def handle_all(message):
    global active_conversation
    user_id = message.from_user.id

    # owner 回复被转发的消息 → 回传给对应陌生人
    if is_owner(user_id) and message.reply_to_message:
        replied_msg_id = message.reply_to_message.message_id
        target_id = forwarded_msg_map.get(replied_msg_id)
        if not target_id:
            bot.reply_to(message, "⚠️ 找不到回复目标。该消息可能不是通过我转发的。\n"
                         "使用 /chat 选择对话对象后直接发消息。")
            return
        try:
            sent = bot.copy_message(
                chat_id=target_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            active_conversation = target_id
            upsert_conversation(target_id)
            text = message.text or message.caption or ""
            log_message(target_id, "to_stranger", "text", text[:500])
            logger.info("回复: owner -> %s", target_id)
        except Exception as e:
            logger.warning("回复转发失败: %s", e)
            bot.reply_to(message, f"❌ 发送失败：{e}")
        return

    # 陌生人发消息 → 转发给 owner
    if not is_owner(user_id):
        sender_name = message.from_user.first_name or "未知"
        sender_username = message.from_user.username
        sender_id = message.from_user.id

        # 检查是否被封禁
        conv = get_conversation(sender_id)
        if conv and conv.get("is_blocked"):
            return  # 静默丢弃

        # 速率限制
        if not check_rate_limit(sender_id):
            logger.info("速率限制: %s (%s)", sender_name, sender_id)
            return  # 静默丢弃

        upsert_conversation(sender_id, sender_name, sender_username or "")

        convos = get_conversations()
        queue_pos = None
        for i, c in enumerate(convos, 1):
            if c["stranger_id"] == sender_id:
                queue_pos = i
                break
        if not active_conversation:
            active_conversation = sender_id

        if MSG_HEADER:
            header = MSG_HEADER.replace("{name}", sender_name)
            if sender_username:
                header = header.replace("{username}", sender_username)
            else:
                header = header.replace(" (@{username})", "").replace("@{username}", "")
            header = header.replace("{id}", str(sender_id))
            header = header.replace("{queue}", str(queue_pos))
            header = header.replace("{total}", str(len(convos)))
        else:
            header = f"📩 来自：{sender_name}"
            if sender_username:
                header += f" (@{sender_username})"
            header += f"\nID: `{sender_id}`"
            header += f"\n队列: #{queue_pos}/{len(convos)}  "
            header += "⬅ 当前" if active_conversation == sender_id else ""

        bot.send_message(OWNER_ID, header, parse_mode="Markdown")
        forwarded = bot.copy_message(
            chat_id=OWNER_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        forwarded_msg_map[forwarded.message_id] = sender_id

        if MSG_FOOTER:
            footer = MSG_FOOTER.replace("{name}", sender_name).replace("{id}", str(sender_id))
            bot.send_message(OWNER_ID, footer, parse_mode="Markdown")

        text = message.text or message.caption or ""
        log_message(sender_id, "from_stranger",
                    "text" if message.text else message.content_type,
                    text[:500],
                    owner_msg_id=forwarded.message_id)
        logger.info("转发: %s (%s) -> owner, 队列 #%s", sender_name, sender_id, queue_pos)
        return

    # owner 直接发消息（非回复） → 发给当前活跃对话对象
    if active_conversation:
        try:
            sent = bot.copy_message(
                chat_id=active_conversation,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            text = message.text or message.caption or ""
            log_message(active_conversation, "to_stranger", "text", text[:500])
            conv = get_conversation(active_conversation)
            name = (conv["first_name"] if conv else "未知") or "未知"
            bot.reply_to(message,
                f"✅ 已发送给 {name} "
                f"(ID: {active_conversation})")
        except Exception as e:
            logger.warning("发送失败: %s", e)
            bot.reply_to(message, f"❌ 发送失败：{e}")
    else:
        bot.reply_to(message, "💡 回复我转发的消息即可回复陌生人。\n"
                     "/queue 查看队列  /chat 切换对话  /who 查看当前对象。")


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    logger.info("🤖 TG 中继机器人 v%s 启动中...", VERSION)

    # 启动自检：验证 Token
    try:
        me = bot.get_me()
        logger.info("Bot 身份: @%s (ID: %s)", me.username, me.id)
    except Exception as e:
        logger.error("Token 验证失败: %s", e)
        sys.exit(1)

    # 注册命令菜单
    try:
        bot.set_my_commands([
            types.BotCommand("start", "欢迎信息"),
            types.BotCommand("help", "帮助"),
            types.BotCommand("ping", "检查延迟"),
            types.BotCommand("id", "获取你的 ID"),
            types.BotCommand("about", "机器人信息"),
            types.BotCommand("stats", "统计面板(Owner)"),
            types.BotCommand("who", "当前对话对象"),
            types.BotCommand("queue", "待回复队列"),
            types.BotCommand("chat", "切换对话"),
            types.BotCommand("send", "主动发消息(Owner)"),
            types.BotCommand("note", "备注用户(Owner)"),
            types.BotCommand("history", "消息记录(Owner)"),
            types.BotCommand("export", "导出对话(Owner)"),
            types.BotCommand("ban", "封禁用户(Owner)"),
            types.BotCommand("unban", "解封用户(Owner)"),
            types.BotCommand("banlist", "封禁列表(Owner)"),
            types.BotCommand("links", "可用链接"),
            types.BotCommand("linkcat", "按类别查看链接"),
            types.BotCommand("linkfind", "搜索链接"),
            types.BotCommand("linkadd", "添加链接(Owner)"),
            types.BotCommand("linkedit", "修改链接(Owner)"),
            types.BotCommand("linkdel", "删除链接(Owner)"),
        ])
        logger.info("命令菜单已注册")
    except Exception as e:
        logger.warning("命令菜单注册失败: %s", e)

    # 启动自检：验证 Owner ID
    try:
        bot.get_chat(OWNER_ID)
        logger.info("Owner ID 验证通过: %s", OWNER_ID)
    except Exception as e:
        logger.error("Owner ID 验证失败: %s", e)
        sys.exit(1)

    # 确保 polling 前没有残留 webhook
    if not WEBHOOK_BASE:
        try:
            bot.remove_webhook()
        except Exception:
            pass

    if WEBHOOK_BASE:
        logger.info("Webhook 模式")
        webhook_url = WEBHOOK_BASE.rstrip("/") + "/webhook"
        try:
            bot.remove_webhook()
            bot.set_webhook(url=webhook_url)
            logger.info("Webhook 已设置: %s", webhook_url)
        except Exception as e:
            logger.error("Webhook 设置失败: %s", e)
            sys.exit(1)
        serve(flask_app, host="0.0.0.0", port=PORT)
    else:
        logger.info("Polling 模式，健康检查端口: %s", PORT)
        flask_thread = threading.Thread(
            target=lambda: serve(flask_app, host="0.0.0.0", port=PORT),
            daemon=True,
        )
        flask_thread.start()
        logger.info("Flask 已启动: http://0.0.0.0:%s/health", PORT)
        bot.infinity_polling()
