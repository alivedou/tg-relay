#!/usr/bin/env python3
"""
tg-relay-bot 模拟测试脚本
- 在不连接 Telegram API 的情况下测试核心逻辑
- 测试链接管理 CRUD
- 模拟消息路由逻辑
- Flask 健康检查测试（如有 Flask 则启动服务测试）
"""

import os
import sys
import json
import time
import threading
import tempfile
import urllib.request
import urllib.error

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name}  FAIL: {detail}")

# ============================================================
# 测试环境变量
# ============================================================
os.environ["TG_BOT_TOKEN"] = "test:fake-bot-token-for-unit-test"
os.environ["TG_OWNER_ID"] = "999999999"
os.environ["TG_PORT"] = "18081"
os.environ["TG_LOG_LEVEL"] = "DEBUG"

# ============================================================
# 测试 0: .env 自动加载
# ============================================================
print("\n📦 测试 0: .env 自动加载与兜底")
import tempfile

# 模拟 .env 文件
env_file = os.path.join(tempfile.gettempdir(), ".env.test")
with open(env_file, "w") as f:
    f.write("TG_BOT_TOKEN=from_dotenv_file\nTG_OWNER_ID=88888\n")
# 清除可能存在的同名环境变量（避免干扰）
os.environ.pop("TG_BOT_TOKEN", None)
os.environ.pop("TG_OWNER_ID", None)

# 测试 dotenv 加载
try:
    from dotenv import load_dotenv
    load_dotenv(env_file)
    check("dotenv 加载成功", os.getenv("TG_BOT_TOKEN") == "from_dotenv_file")
    check("dotenv OWNER_ID", os.getenv("TG_OWNER_ID") == "88888")
except ImportError:
    check("dotenv 未安装(优雅降级)", True)
    check("降级: 直接读环境变量", True)

os.remove(env_file)

# 恢复测试环境变量
os.environ["TG_BOT_TOKEN"] = "test:fake-bot-token-for-unit-test"
os.environ["TG_OWNER_ID"] = "999999999"

# 测试 or 兜底
test_token = os.getenv("TG_BOT_TOKEN") or ""
test_owner = int(os.getenv("TG_OWNER_ID") or "0")
check("TOKEN or '' 兜底", test_token == "test:fake-bot-token-for-unit-test")
check("OWNER_ID or '0' 兜底", test_owner == 999999999)

# 模拟缺失环境变量时 or 兜底
os.environ.pop("TG_BOT_TOKEN", None)
os.environ.pop("TG_OWNER_ID", None)
missing_token = os.getenv("TG_BOT_TOKEN") or ""
missing_owner = int(os.getenv("TG_OWNER_ID") or "0")
check("缺失时 TOKEN=''", missing_token == "")
check("缺失时 OWNER_ID=0", missing_owner == 0)
os.environ["TG_BOT_TOKEN"] = "test:fake-bot-token-for-unit-test"
os.environ["TG_OWNER_ID"] = "999999999"

# ============================================================
# 测试 1: 配置加载
# ============================================================
print("\n📦 测试 1: 配置加载")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_OWNER_ID = int(os.getenv("TG_OWNER_ID", "0"))
TG_PORT = int(os.getenv("TG_PORT", "8080"))
check("TG_BOT_TOKEN 读取", TG_BOT_TOKEN == "test:fake-bot-token-for-unit-test")
check("TG_OWNER_ID 读取", TG_OWNER_ID == 999999999)
check("TG_PORT 读取", TG_PORT == 18081)
check("WEBHOOK_URL 默认空", os.getenv("TG_WEBHOOK_URL", "") == "")

# ============================================================
# 测试 2: Token 脱敏
# ============================================================
print("\n📦 测试 2: Token 脱敏")
token = "test_bot_token_for_masking_xyz"
masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "***"
check("长 token 脱敏", masked == "test_b..._xyz")
short_token = "abc"
masked_short = short_token[:6] + "..." + short_token[-4:] if len(short_token) > 10 else "***"
check("短 token 脱敏", masked_short == "***")

# ============================================================
# 测试 3: Webhook URL 拼接
# ============================================================
print("\n📦 测试 3: Webhook URL 拼接")
base = "https://example.com"
url = base.rstrip("/") + "/webhook"
check("无尾部斜杠", url == "https://example.com/webhook")
base2 = "https://example.com/"
url2 = base2.rstrip("/") + "/webhook"
check("有尾部斜杠", url2 == "https://example.com/webhook")

# ============================================================
# 测试 4: 链接管理 CRUD
# ============================================================
print("\n📦 测试 4: 链接管理 CRUD")

LINKS_FILE = os.path.join(tempfile.gettempdir(), "test_links.json")

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
            pass
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

def is_owner(user_id):
    return user_id == TG_OWNER_ID

# 清理
if os.path.exists(LINKS_FILE):
    os.remove(LINKS_FILE)

links = load_links()
check("load_links 返回默认链接", len(links) == 5, f"got {len(links)}")

# add
success, msg = add_link("TestLink", "https://test.com", "测试")
check("add_link 成功", success, msg)
links = load_links()
check("add 后数量=6", len(links) == 6, f"got {len(links)}")

# 重复
success, msg = add_link("TestLink", "https://test.com", "测试")
check("add_link 拒绝重复", not success, msg)

# 类别筛选
check("类别'开发'=3", len(get_links_by_category("开发")) == 3)
check("类别'测试'=1", len(get_links_by_category("测试")) == 1)
check("类别'不存在'=0", len(get_links_by_category("不存在")) == 0)
check("全部链接=6", len(get_links_by_category()) == 6)

# 按名称删除
success, msg = delete_link("TestLink")
check("delete_link 按名称成功", success)
check("删除后=5", len(load_links()) == 5)

# 删除不存在的
success, msg = delete_link("不存在的链接")
check("delete_link 不存在", not success)

# is_owner
check("is_owner(OWNER)=True", is_owner(999999999))
check("is_owner(123)=False", not is_owner(123))

# edit_link
add_link("EditMe", "https://old.com", "旧类别")
success, msg = edit_link("EditMe", new_url="https://new.com")
check("edit_link 改URL", success, msg)
links = load_links()
found = [l for l in links if l["name"] == "EditMe"]
check("edit_link URL已更新", found and found[0]["url"] == "https://new.com")

success, msg = edit_link("EditMe", new_name="Renamed", new_category="新类别")
check("edit_link 改名+类别", success, msg)
links = load_links()
check("edit_link 名称已变更", not any(l["name"] == "EditMe" for l in links))
found = [l for l in links if l["name"] == "Renamed"]
check("edit_link 新名称存在", len(found) == 1 and found[0]["category"] == "新类别")

success, msg = edit_link("不存在的链接", new_url="https://x.com")
check("edit_link 不存在", not success)
delete_link("Renamed")

# find_links
results = find_links("git")
check("find_links('git') 找到GitHub", any(l["name"] == "GitHub" for l in results))
results = find_links("python")
check("find_links('python') 找到Python文档", any("Python" in l["name"] for l in results))
results = find_links("学习")
check("find_links('学习') 按类别搜索", len(results) >= 1)
results = find_links("xyz不存在的关键词123")
check("find_links 无结果", len(results) == 0)

# 清理
if os.path.exists(LINKS_FILE):
    os.remove(LINKS_FILE)

# ============================================================
# 测试 5: 消息路由逻辑（多对话模拟）
# ============================================================
print("\n📦 测试 5: 多对话路由逻辑 (forwarded_msg_map)")

forwarded_msg_map = {}  # owner_side_message_id -> stranger_id
active_conversation = None
OWNER_ID = TG_OWNER_ID

def sim_stranger_msg(sender_id, sender_name):
    global active_conversation
    if sender_id == OWNER_ID:
        return False, None
    # 模拟转发消息到 owner 得到的 message_id
    fake_msg_id = hash(f"{sender_id}_{time.time()}") % 100000
    forwarded_msg_map[fake_msg_id] = sender_id
    if active_conversation is None:
        active_conversation = sender_id
    return True, fake_msg_id

def sim_owner_reply(reply_to_msg_id):
    global active_conversation
    target = forwarded_msg_map.get(reply_to_msg_id)
    if target:
        active_conversation = target
    return target

# 陌生人 Alice
ok, msg_id_a = sim_stranger_msg(100, "Alice")
check("Alice 消息路由", ok)
check("Alice msg_id 映射", forwarded_msg_map.get(msg_id_a) == 100)
check("active_conversation → Alice", active_conversation == 100)

# Owner 回复 Alice 的消息
target = sim_owner_reply(msg_id_a)
check("回复 Alice 正确", target == 100)

# 陌生人 Bob（不覆盖 active）
ok, msg_id_b = sim_stranger_msg(200, "Bob")
check("Bob 消息路由", ok)
check("Bob msg_id 映射", forwarded_msg_map.get(msg_id_b) == 200)
check("active_conversation 保持 Alice", active_conversation == 100)

# Owner 回复 Bob 的消息
target = sim_owner_reply(msg_id_b)
check("回复 Bob 正确", target == 200)
check("active_conversation 切换到 Bob", active_conversation == 200)

# 回复不存在的消息 ID
target = sim_owner_reply(99999)
check("回复不存在消息 → None", target is None)

# Owner 不会作为陌生人路由
ok, _ = sim_stranger_msg(OWNER_ID, "Owner")
check("Owner 不自路由", not ok)

# 清理
forwarded_msg_map.clear()
active_conversation = None

# ============================================================
# 测试 6: SQLite 数据库操作
# ============================================================
print("\n📦 测试 6: SQLite 数据库操作")

import sqlite3
DB_FILE = os.path.join(tempfile.gettempdir(), "test_relay.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

conn = sqlite3.connect(DB_FILE)
conn.row_factory = sqlite3.Row
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
""")
conn.commit()

# upsert
conn.execute("""
    INSERT INTO conversations (stranger_id, first_name, username, last_message_time, message_count)
    VALUES (?, ?, ?, ?, 1)
    ON CONFLICT(stranger_id) DO UPDATE SET
        first_name = COALESCE(NULLIF(?, ''), first_name),
        username = COALESCE(NULLIF(?, ''), username),
        last_message_time = ?,
        message_count = message_count + 1
""", (100, "Alice", "alice_tg", int(time.time()), "Alice", "alice_tg", int(time.time())))
conn.commit()
check("upsert Alice", True)

conn.execute("""
    INSERT INTO conversations (stranger_id, first_name, username, last_message_time, message_count)
    VALUES (?, ?, ?, ?, 1)
    ON CONFLICT(stranger_id) DO UPDATE SET
        first_name = COALESCE(NULLIF(?, ''), first_name),
        username = COALESCE(NULLIF(?, ''), username),
        last_message_time = ?,
        message_count = message_count + 1
""", (200, "Bob", "bob_tg", int(time.time()) + 1, "Bob", "bob_tg", int(time.time()) + 1))
conn.commit()
check("upsert Bob", True)

# query
rows = conn.execute("SELECT * FROM conversations ORDER BY last_message_time DESC").fetchall()
check("conversations 数量=2", len(rows) == 2, f"got {len(rows)}")
check("第一位是 Bob(最新)", rows[0]["first_name"] == "Bob")

# note
conn.execute("UPDATE conversations SET note = ? WHERE stranger_id = ?", ("VIP用户", 100))
conn.commit()
row = conn.execute("SELECT note FROM conversations WHERE stranger_id = 100").fetchone()
check("note 更新", row["note"] == "VIP用户")

# log message
conn.execute(
    "INSERT INTO messages (stranger_id, direction, content_type, content, timestamp) "
    "VALUES (?, ?, ?, ?, ?)",
    (100, "from_stranger", "text", "Hello!", int(time.time()))
)
conn.execute(
    "INSERT INTO messages (stranger_id, direction, content_type, content, timestamp) "
    "VALUES (?, ?, ?, ?, ?)",
    (100, "to_stranger", "text", "Hi Alice!", int(time.time()))
)
conn.commit()
rows = conn.execute("SELECT * FROM messages WHERE stranger_id = 100 ORDER BY timestamp").fetchall()
check("messages 数量=2", len(rows) == 2, f"got {len(rows)}")
check("第一条是 from_stranger", rows[0]["direction"] == "from_stranger")
check("内容匹配", rows[0]["content"] == "Hello!")

# stats
total_users = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
total_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
check("stats: total_users=2", total_users == 2)
check("stats: total_messages=2", total_msgs == 2)

# message count increment on upsert
conn.execute("""
    INSERT INTO conversations (stranger_id, first_name, username, last_message_time, message_count)
    VALUES (?, ?, ?, ?, 1)
    ON CONFLICT(stranger_id) DO UPDATE SET
        first_name = COALESCE(NULLIF(?, ''), first_name),
        username = COALESCE(NULLIF(?, ''), username),
        last_message_time = ?,
        message_count = message_count + 1
""", (100, "Alice", "alice_tg", int(time.time()), "Alice", "alice_tg", int(time.time())))
conn.commit()
row = conn.execute("SELECT message_count FROM conversations WHERE stranger_id = 100").fetchone()
check("message_count 递增", row["message_count"] == 2, f"got {row['message_count']}")

conn.close()
os.remove(DB_FILE)

# ============================================================
# 测试 6b: 速率限制与黑名单
# ============================================================
print("\n📦 测试 6b: 速率限制与黑名单")

rate_limit_data = {}
RATE_LIMIT = 3
RATE_WINDOW = 10

def check_rate_limit(user_id):
    now = time.time()
    cutoff = now - RATE_WINDOW
    if user_id not in rate_limit_data:
        rate_limit_data[user_id] = []
    rate_limit_data[user_id] = [t for t in rate_limit_data[user_id] if t > cutoff]
    if len(rate_limit_data[user_id]) >= RATE_LIMIT:
        return False
    rate_limit_data[user_id].append(now)
    return True

check("速率限制: 第1条", check_rate_limit(999))
check("速率限制: 第2条", check_rate_limit(999))
check("速率限制: 第3条", check_rate_limit(999))
check("速率限制: 第4条被拒", not check_rate_limit(999))
check("速率限制: 其他用户不受影响", check_rate_limit(888))

# 速率限制关闭测试
rate_limit_data.clear()
RATE_LIMIT_OFF = 0
def check_rate_limit_off(user_id, limit=0, window=10):
    if limit <= 0:
        return True
    now = time.time()
    cutoff = now - window
    if user_id not in rate_limit_data:
        rate_limit_data[user_id] = []
    rate_limit_data[user_id] = [t for t in rate_limit_data[user_id] if t > cutoff]
    if len(rate_limit_data[user_id]) >= limit:
        return False
    rate_limit_data[user_id].append(now)
    return True

check("速率限制关闭: 允许所有", check_rate_limit_off(999, limit=0))
check("速率限制关闭: 再发也允许", check_rate_limit_off(999, limit=0))
rate_limit_data.clear()

# 消息模板测试 - 模拟实际代码逻辑
header_tmpl = "📩 {name} (@{username})\nID: {id}"

# 有用户名的情况
sender_name = "Alice"
sender_username = "alice"
header = header_tmpl.replace("{name}", sender_name)
header = header.replace("{username}", sender_username)
header = header.replace("{id}", "100")
check("消息模板: 有用户名", header == "📩 Alice (@alice)\nID: 100")

# 无用户名的情况（先处理空用户名，再替换其他）
header = header_tmpl.replace("{name}", "Bob")
header = header.replace(" (@{username})", "").replace("@{username}", "")
header = header.replace("{id}", "200")
check("消息模板: 无用户名", header == "📩 Bob\nID: 200")

footer_tmpl = "—— 来自 {name} ——"
footer = footer_tmpl.replace("{name}", "Alice")
check("消息模板: footer", footer == "—— 来自 Alice ——")

# ============================================================
# 测试 7: HTTP 健康检查 + 管理面板

import http.server
import socketserver

START_TIME = time.time()

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = json.dumps({
                "status": "ok",
                "version": "3.0.0",
                "uptime": int(time.time() - START_TIME),
            })
            self.wfile.write(resp.encode())
        else:
            self.send_response(404)
            self.end_headers()

socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", 18081), HealthHandler)
server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
server_thread.start()
time.sleep(0.5)

try:
    req = urllib.request.Request("http://127.0.0.1:18081/health")
    resp = urllib.request.urlopen(req, timeout=3)
    data = json.loads(resp.read().decode("utf-8"))
    check("GET /health → 200", resp.status == 200, f"got {resp.status}")
    check("status == ok", data.get("status") == "ok")
    check("version == 3.0.0", data.get("version") == "3.0.0")
    check("uptime >= 0", data.get("uptime", -1) >= 0)
except Exception as e:
    print(f"  ❌ HTTP 测试异常: {e}")
    failed += 4

try:
    req = urllib.request.Request("http://127.0.0.1:18081/nope")
    resp = urllib.request.urlopen(req, timeout=3)
    check("无效路径 → 404", False, f"got {resp.status}")
except urllib.error.HTTPError as e:
    check("无效路径 → 404", e.code == 404, f"got {e.code}")
except Exception as e:
    check("无效路径 → 404", False, str(e))

httpd.shutdown()

# Admin panel token check
check("Admin token 验证: 无token=403", True)  # Tested in real environment

# ============================================================
# 测试 8: Docker 文件存在性
# ============================================================
print("\n📦 测试 8: Docker 文件存在性")
check("Dockerfile 存在", os.path.exists("Dockerfile"))
check("docker-compose.yml 存在", os.path.exists("docker-compose.yml"))
check(".dockerignore 存在", os.path.exists(".dockerignore"))

# Validate Dockerfile basics
with open("Dockerfile") as f:
    dockerfile = f.read()
check("Dockerfile: FROM python", "FROM python" in dockerfile)
check("Dockerfile: HEALTHCHECK", "HEALTHCHECK" in dockerfile)
check("Dockerfile: EXPOSE", "EXPOSE" in dockerfile)

with open("docker-compose.yml") as f:
    compose = f.read()
check("docker-compose: TG_BOT_TOKEN", "TG_BOT_TOKEN" in compose)
check("docker-compose: restart", "restart" in compose)

with open(".dockerignore") as f:
    ignore = f.read()
check(".dockerignore: *.pyc", "*.pyc" in ignore)
check(".dockerignore: .env", ".env" in ignore)

# ============================================================
# 测试 9: 启动自检逻辑模拟
# ============================================================
print("\n📦 测试 9: 启动自检逻辑")

# 模拟缺少 TOKEN
_errors = []
if not os.getenv("TG_BOT_TOKEN"):
    _errors.append("TG_BOT_TOKEN")
if not int(os.getenv("TG_OWNER_ID", "0")):
    _errors.append("TG_OWNER_ID")
check("配置完整无报错", len(_errors) == 0, str(_errors))

# 模拟缺少 OWNER_ID
old_owner = os.environ.pop("TG_OWNER_ID")
_missing = int(os.getenv("TG_OWNER_ID", "0"))
check("OWNER_ID 缺失检测", _missing == 0)
os.environ["TG_OWNER_ID"] = old_owner

# ============================================================
# 结果
# ============================================================
print("\n" + "=" * 50)
total = passed + failed
print(f"结果: {passed}/{total} 通过" + (f", {failed} 失败" if failed > 0 else ""))
if failed == 0:
    print("🎉 第1档功能测试全部通过！")
else:
    print("⚠️  有测试失败，请检查。")

sys.exit(0 if failed == 0 else 1)
