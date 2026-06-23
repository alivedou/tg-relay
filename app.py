#!/usr/bin/env python3
"""
TG 双向匿名中继机器人
- 陌生人 DM 机器人 → 转发给 owner
- owner 回复被转发的消息 → 自动回传给原发送者
- 双方互不知道对方真实身份
"""

import os
import telebot
import json

# 环境变量优先，不存在时使用硬编码默认值（方便多环境部署）

TOKEN    = os.getenv("TG_BOT_TOKEN") or ""        # 字符串："abc123:xyz"
OWNER_ID = int(os.getenv("TG_OWNER_ID") or "0")   # 数字：123456789




bot = telebot.TeleBot(TOKEN)

pending_reply = {}  # owner_chat_id -> stranger_user_id

# 链接存储路径（与脚本同目录）
LINKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "links.json")

# 默认链接（示例）
DEFAULT_LINKS = [
    {"name": "GitHub", "url": "https://github.com", "category": "开发"},
    {"name": "StackOverflow", "url": "https://stackoverflow.com", "category": "开发"},
    {"name": "Python 文档", "url": "https://docs.python.org/zh-cn/", "category": "学习"},
    {"name": "Telegram 开发", "url": "https://core.telegram.org/bots", "category": "开发"},
    {"name": "维基百科", "url": "https://zh.wikipedia.org", "category": "常用"},
]


def load_links():
    """从文件加载链接数据，不存在则使用默认链接"""
    if os.path.exists(LINKS_FILE):
        try:
            with open(LINKS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_LINKS


def save_links(links):
    """保存链接到文件"""
    with open(LINKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(links, f, ensure_ascii=False, indent=2)


def get_links_by_category(category=None):
    """按类别筛选链接，如不指定则返回所有"""
    links = load_links()
    if category:
        return [link for link in links if link.get("category") == category]
    return links


def add_link(name, url, category="常用"):
    """添加新链接"""
    links = load_links()
    # 检查是否已存在
    if any(link["name"] == name for link in links):
        return False, "链接已存在"
    links.append({"name": name, "url": url, "category": category})
    save_links(links)
    return True, f"✅ 已添加链接：{name}"


def delete_link(name):
    """删除链接"""
    links = load_links()
    original_count = len(links)
    links = [link for link in links if link["name"] != name]
    if len(links) == original_count:
        return False, "链接不存在"
    save_links(links)
    return True, f"✅ 已删除链接：{name}"


def is_owner(user_id):
    return user_id == OWNER_ID


@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    if is_owner(message.from_user.id):
        bot.reply_to(message, "👋 你是 owner。陌生人发消息给你会通过我转发。\n\n"
                     "回复我转发的消息即可回复陌生人。\n"
                     "/who — 查看当前对话对象\n"
                     "/links — 查看可用链接\n"
                     "/linkadd — 添加新链接（Owner 专用）")
    else:
        bot.reply_to(message, "👋 你好！你的消息会匿名转发给 bot 主人。\n"
                     "/links — 查看可用链接")


@bot.message_handler(commands=["who"])
def handle_who(message):
    if not is_owner(message.from_user.id):
        return
    target = pending_reply.get(message.chat.id)
    if target:
        try:
            target_user = bot.get_chat(target)
            name = target_user.first_name or ""
            username = target_user.username or "(无用户名)"
            bot.reply_to(message, f"当前对话对象：{name} (@{username}) [ID: {target}]")
        except Exception:
            bot.reply_to(message, f"当前对话对象 ID：{target}（无法获取详细信息）")
    else:
        bot.reply_to(message, "当前没有活跃对话。请先等陌生人发消息，或回复我转发的消息来建立对话。")


@bot.message_handler(commands=["linkadd"])
def handle_linkadd(message):
    if not is_owner(message.from_user.id):
        bot.reply_to(message, "❌ 仅限 owner 使用")
        return

    if len(message.text.split()) < 2:
        bot.reply_to(message, "⚠️ 用法：\n"
                     "  /linkadd <name>;<url>              （默认类别：常用）\n"
                     "  /linkadd <name>;<url>;<category>   （指定类别）\n\n"
                     "示例：\n"
                     "  /linkadd VSCode;https://code.visualstudio.com\n"
                     "  /linkadd Python;https://www.python.org;学习")
        return

    # 使用分号分隔，避免空格问题
    rest = message.text[len("/linkadd") + 1:].strip()
    parts = rest.split(";", 2)  # 最多分成3部分

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
        bot.reply_to(message, "⚠️ 用法：\n"
                     "  /linkdel <序号>        （删除指定序号的链接）\n"
                     "  /linkdel <链接名>      （删除指定名称的链接）\n\n"
                     "示例：\n"
                     "  /linkdel 1              # 删除第 1 个链接\n"
                     "  /linkdel VSCode        # 删除名为 VSCode 的链接")
        return

    target = message.text.split(" ", 1)[1].strip()

    # 尝试解析为序号（纯数字）
    if target.isdigit():
        index = int(target) - 1  # 转为 0 基索引
        links = load_links()
        if index < 0 or index >= len(links):
            bot.reply_to(message, f"❌ 序号 {target} 超出范围（共 {len(links)} 个链接）")
            return

        deleted_name = links[index]["name"]
        links.pop(index)
        save_links(links)
        bot.reply_to(message, f"✅ 已删除链接：{deleted_name}")
        return

    # 按名称删除
    success, msg = delete_link(target)
    bot.reply_to(message, msg)


@bot.message_handler(commands=["links"])
def handle_links(message):
    category = None
    if len(message.text.split()) > 1:
        category = message.text.split(" ", 1)[1]

    links = get_links_by_category(category)
    if not links:
        bot.reply_to(message, "❌ 没有可用链接" if category else "❌ 没有链接")
        return

    # 按类别分组展示
    from collections import defaultdict
    grouped = defaultdict(list)
    for link in links:
        grouped[link["category"]].append(link)

    result = "🔗 可用链接（序号可用于删除：/linkdel <序号>）\n\n"
    for category, cat_links in sorted(grouped.items()):
        result += f"📁 **{category}**\n"
        for i, link in enumerate(cat_links, 1):
            result += f"  {i}. [{link['name']}]({link['url']})\n"
        result += "\n"

    bot.reply_to(message, result, parse_mode="Markdown")


@bot.message_handler(commands=["linkcat"])
def handle_linkcat(message):
    if len(message.text.split()) < 2:
        bot.reply_to(message, "⚠️ 用法：/linkcat <类别>\n\n"
                     "示例：/linkcat 开发")
        return

    category = message.text.split(" ", 1)[1]
    links = get_links_by_category(category)
    if not links:
        bot.reply_to(message, f"❌ 类别 '{category}' 中没有链接")
        return

    result = f"📁 类别：{category}\n\n"
    for i, link in enumerate(links, 1):
        result += f"  {i}. [{link['name']}]({link['url']})\n"

    bot.reply_to(message, result, parse_mode="Markdown")


@bot.message_handler(func=lambda m: True)
def handle_all(message):
    user_id = message.from_user.id

    # owner 回复被转发的消息
    if is_owner(user_id) and message.reply_to_message:
        target_id = None
        for owner_cid, stranger_id in list(pending_reply.items()):
            if stranger_id:
                target_id = stranger_id
                break

        if target_id:
            try:
                bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
                pending_reply[message.chat.id] = target_id
            except Exception as e:
                bot.reply_to(message, f"❌ 发送失败：{e}")
        else:
            bot.reply_to(message, "⚠️ 找不到回复目标。请先等陌生人发消息。")
        return

    # 陌生人发消息 → 转发给 owner
    if not is_owner(user_id):
        sender_name = message.from_user.first_name or "未知"
        sender_username = message.from_user.username
        sender_id = message.from_user.id

        header = f"📩 来自：{sender_name}"
        if sender_username:
            header += f" (@{sender_username})"
        header += f"\nID: `{sender_id}`"

        bot.send_message(OWNER_ID, header, parse_mode="Markdown")
        forwarded = bot.copy_message(
            chat_id=OWNER_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        pending_reply[OWNER_ID] = sender_id
        return

    # owner 直接发消息（非回复）
    bot.reply_to(message, "💡 回复我转发的消息即可回复陌生人。\n/who 查看当前对话对象。")


if __name__ == "__main__":
    print("🤖 TG 中继机器人启动中...")
    token_src = "环境变量" if os.getenv("TG_BOT_TOKEN") else "硬编码"
    print(f"🔑 Token 来源：{token_src}")
    print(f"📁 链接文件：{LINKS_FILE}")
    print(f"🔗 可用命令：/links, /linkadd, /linkdel")
    bot.infinity_polling()