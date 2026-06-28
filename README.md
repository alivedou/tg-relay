# TG 双向匿名中继机器人 v3.0.0

> 保护聊天隐私的 Telegram 中间层。陌生人只能通过 Bot 联系你，双方互不知道真实身份。

## 核心功能

- **匿名中继**：陌生人 DM Bot → 转发 Owner；Owner 回复 → 回传陌生人
- **多对话支持**：同时跟踪多位陌生人，`forwarded_msg_map` 按回复消息自动路由
- **SQLite 持久化**：对话状态、消息历史、封禁列表重启不丢失
- **Flask 健康检查**：内置 `GET /health` 端点，适配免费容器面板
- **Polling / Webhook 双模式**：设 `TG_WEBHOOK_URL` 自动切换
- **InlineKeyboard 交互**：链接浏览、对话切换支持按钮操作
- **速率限制**：可配置每用户消息频率限制
- **黑名单系统**：`/ban` `/unban` `/banlist`
- **消息模板**：转发格式可自定义（`{name}` `{username}` `{id}` 等占位符）
- **Web 管理面板**：`/admin?token=xxx` 查看统计和活跃对话

## 文件结构

```
tg-relay/
├── app.py          # 主程序
├── app.py               # Pterodactyl 入口（同 app.py）
├── requirements.txt     # Python 依赖
├── .env.example         # 环境变量模板
├── tg-relay.service     # systemd 服务文件
├── Dockerfile           # Docker 构建
├── docker-compose.yml   # Docker Compose
├── .dockerignore
├── relay.db             # SQLite 数据库（自动生成）
├── links.json           # 链接数据（自动生成）
├── test_tg_relay.py     # 模拟测试脚本
├── list.md              # 功能路线图
└── README.md
```

## 快速开始

### 方式一：Pterodactyl 面板（推荐免费容器）

1. 上传 `app.py` 和 `requirements.txt` 到面板
2. 设置环境变量：
   - `TG_BOT_TOKEN` — 从 [@BotFather](https://t.me/BotFather) 获取
   - `TG_OWNER_ID` — 从 [@userinfobot](https://t.me/userinfobot) 获取
3. 面板自动安装依赖并启动
4. Bot 通过 `/health` 端点（端口 8080）通过健康检查

### 方式二：VPS + systemd

```bash
mkdir /opt/tg-relay && cd /opt/tg-relay
cp .env.example .env && nano .env   # 填写 TG_BOT_TOKEN 和 TG_OWNER_ID
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp tg-relay.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable tg-relay --now
```

### 方式三：Docker

```bash
cp .env.example .env && nano .env
docker compose up -d
```

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|:--:|--------|------|
| `TG_BOT_TOKEN` | ✅ | - | Bot Token |
| `TG_OWNER_ID` | ✅ | - | 主人的 Telegram 用户 ID |
| `TG_PORT` | | `8080` | Flask 端口 |
| `TG_WEBHOOK_URL` | | - | 设置后走 Webhook 模式 |
| `TG_LOG_LEVEL` | | `INFO` | DEBUG/INFO/WARNING/ERROR |
| `TG_WELCOME_OWNER` | | - | Owner 的 /start 自定义内容 |
| `TG_WELCOME_STRANGER` | | - | 陌生人的 /start 自定义内容 |
| `TG_OWNER_CONTACT` | | - | /about 中显示的联系方式 |
| `TG_RATE_LIMIT` | | `0` | 每窗口最大消息数（0=关闭） |
| `TG_RATE_WINDOW` | | `10` | 速率限制窗口（秒） |
| `TG_MSG_HEADER` | | `默认格式` | 转发消息头部模板 |
| `TG_MSG_FOOTER` | | - | 转发消息尾部模板 |
| `TG_ADMIN_TOKEN` | | - | 管理面板访问 Token |

### 消息模板占位符

```
{name}      — 发送者名称
{username}  — 发送者用户名
{id}        — 发送者 ID
{queue}     — 队列位置
{total}     — 队列总数
```

示例：`TG_MSG_HEADER=📩 {name} (@{username})\nID: {id}\n队列: #{queue}/{total}`

## 全部命令

| 命令 | 说明 | 权限 |
|------|------|------|
| `/start` | 欢迎信息 | 所有人 |
| `/help` | 帮助 | 所有人 |
| `/ping` | 检查延迟 | 所有人 |
| `/id` | 获取自己的 ID | 所有人 |
| `/about` | Bot 信息 | 所有人 |
| `/links` | 查看链接（分页按钮） | 所有人 |
| `/linkcat <类别>` | 按类别查看 | 所有人 |
| `/linkfind <关键词>` | 搜索链接 | 所有人 |
| `/who` | 当前对话对象 | Owner |
| `/queue` | 待回复队列 | Owner |
| `/chat <序号/ID>` | 切换对话对象 | Owner |
| `/send <ID> <消息>` | 主动发消息 | Owner |
| `/note <ID> <备注>` | 用户备注 | Owner |
| `/history [ID]` | 消息记录 | Owner |
| `/export [ID]` | 导出对话 | Owner |
| `/ban <ID>` | 封禁用户 | Owner |
| `/unban <ID>` | 解封用户 | Owner |
| `/banlist` | 封禁列表 | Owner |
| `/stats` | 统计面板 | Owner |
| `/linkadd <n>;<u>;<c>` | 添加链接 | Owner |
| `/linkedit <旧>;<新>;<URL>;<类>` | 修改链接 | Owner |
| `/linkdel <序号/名称>` | 删除链接 | Owner |

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查，返回状态 JSON |
| `/webhook` | POST | Telegram Webhook 接收（Webhook 模式） |
| `/admin?token=xxx` | GET | Web 管理面板（需 TG_ADMIN_TOKEN） |

## 注意事项

- 多对话自动路由：回复被转发的消息时，Bot 自动识别目标用户
- 封禁用户的消息**静默丢弃**，对方不会收到任何提示
- SQLite 数据库 `relay.db` 和 `links.json` 需确保可写
- Webhook 模式下需配置反向代理（Nginx/Caddy）指向 `/webhook`

## License

MIT
