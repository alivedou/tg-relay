#!/usr/bin/env python3
"""
TG 双向匿名中继机器人
- 陌生人 DM 机器人 → 转发给 owner
- owner 回复被转发的消息 → 自动回传给原发送者
- 双方互不知道对方真实身份
"""

import os
import telebot

TOKEN = os.environ["TG_BOT_TOKEN"]
OWNER_ID = int(os.environ["TG_OWNER_ID"])

bot = telebot.TeleBot(TOKEN)

pending_reply = {}  # owner_chat_id -> stranger_user_id


def is_owner(user_id):
    return user_id == OWNER_ID


@bot.message_handler(commands=["start", "help"])
def handle_start(message):
    if is_owner(message.from_user.id):
        bot.reply_to(message, "👋 你是 owner。陌生人发消息给你会通过我转发。\n\n"
                     "回复我转发的消息即可回复陌生人。\n"
                     "/who — 查看当前对话对象")
    else:
        bot.reply_to(message, "👋 你好！你的消息会匿名转发给 bot 主人。")


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
    bot.infinity_polling()
