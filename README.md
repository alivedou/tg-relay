# TG 双向匿名中继机器人

> 保护聊天隐私的 Telegram 中间层。陌生人只能通过 Bot 联系你，双方互不知道真实身份。

## 🎯 做什么的

你在 TG 上设了公开用户名，但不想直接暴露私人账号。这个 Bot 作为中间人：

```
陌生人 → DM Bot → [转发] → 你的私聊
你 → 回复 Bot → [回传] → 陌生人
```

- 陌生人看不到你的真实账号（只看到 Bot）
- 你也看不到对方手机号（只看到昵称/用户名）
- 双向消息全通过 Bot 中继，身份隔离

## 📦 文件结构

```
tg-relay/
├── tg-relay.py        # 主程序（~290 行）
├── app.py             # Pterodactyl 入口（内容同 tg-relay.py）
├── tg-relay.service   # systemd 服务文件
├── requirements.txt   # Python 依赖
├── .env.example       # 环境变量模板
├── README.md          # 使用说明
└── links.json         # 链接数据文件（自动生成）
```

## 🔑 配置方式

支持双模式，**环境变量优先**，不存在时走硬编码默认值：

```python
TOKEN    = os.getenv("TG_BOT_TOKEN") or ""
OWNER_ID = int(os.getenv("TG_OWNER_ID") or "0")
```

同一份代码，三种环境通吃。

## 🚀 部署方式一：VPS + systemd

### 1. 获取 Bot Token

去 [@BotFather](https://t.me/BotFather) 创建机器人，拿到 token。

### 2. 获取你的 TG User ID

去 [@userinfobot](https://t.me/userinfobot) 发条消息，拿到数字 ID。

### 3. 上传到 VPS

```bash
mkdir -p /opt/tg-relay
# 把 tg-relay.py 和 .env 放进去
```

### 4. 配置环境变量

```bash
cp .env.example .env
nano .env
```

填写：
```
TG_BOT_TOKEN=123456:ABC-DEF1234gh
TG_OWNER_ID=987654321
```

> ⚠️ 不要加 `export`，不要加引号。systemd `EnvironmentFile` 只认纯 `KEY=VALUE`。

### 5. 创建虚拟环境装依赖

```bash
# Debian 13 需要先装 python3-venv
apt install python3.12-venv -y

cd /opt/tg-relay
python3 -m venv venv
./venv/bin/pip install pyTelegramBotAPI
```

### 6. 注册 systemd 服务

```bash
cp tg-relay.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable tg-relay --now
systemctl status tg-relay
```

### 7. 配置 TG 隐私（推荐）

TG 设置 → 隐私与安全：
- 手机号码 → 谁可以看到？→ **没有人**
- 手机号码 → 谁可以通过号码找到我？→ **我的联系人**

## 🐣 部署方式二：Pterodactyl 面板（免费容器）

适用于免费 Pterodactyl 面板，无需 SSH，纯网页操作。

### 1. 准备文件

把 `app.py` 和 `requirements.txt` 下载到本地。

### 2. 改硬编码配置

用任意文本编辑器打开 `app.py`，改以下两行，**不要公开上传**：

```python
TOKEN    = os.getenv("TG_BOT_TOKEN") or "你的token"
OWNER_ID = int(os.getenv("TG_OWNER_ID") or "123456789")
```

### 3. 上传到面板

面板文件管理器 → 进入 `/home/container/` → 拖入 `app.py` 和 `requirements.txt`。

### 4. 启动

点 Start。面板自动识别 `requirements.txt` 装依赖，然后跑 `app.py`。

### 5. 注意事项

- 免费容器通常需**每 7 天手动续期**，过期数据丢失
- `links.json` 会自动保存，容器重启不丢
- 硬编码版**不要上传到 GitHub**——token 明文写在代码里
- 如果面板提供了环境变量编辑器，可以不改 `app.py`，直接设变量

## 🛠️ 命令

| 命令 | 说明 | 权限 |
|------|------|------|
| `/start` | 欢迎信息 | 所有人 |
| `/who` | 查看当前对话对象 | Owner |
| `/links` | 查看可用链接 | 所有人 |
| `/linkadd <name>;<url>` | 添加新链接（默认类别"常用"） | Owner |
| `/linkadd <name>;<url>;<category>` | 添加新链接（指定类别） | Owner |
| `/linkdel <序号>` | 删除指定序号的链接 | Owner |
| `/linkdel <链接名>` | 删除指定名称的链接 | Owner |
| `/linkcat <category>` | 按类别查看链接 | 所有人 |

## 📊 资源占用

在 512MB VPS 上实测：

| 指标 | 数值 |
|:---|:---|
| cgroup Memory | ~26MB |
| RSS | ~38MB |
| 占比 | ~5% |

### vps查看内存占用

```bash
# 方法 1：查看 systemd 服务状态（推荐）
systemctl status tg-relay | grep Memory

# 方法 2：查看 Python 进程内存
ps aux | grep tg-relay.py

# 输出示例：
# USER       PID %CPU %MEM    VSZ   RSS TTY  STAT START   TIME COMMAND
# nobody    436116  0.1  2.1  312456  30752 ?    Sl   12:31   0:01 /opt/tg-relay/venv/bin/python /opt/tg-relay/tg-relay.py
# RSS 列显示实际内存占用（KB）
```

## ⚠️ 注意事项

- 仅支持**一对一**对话（同一时间维护一个活跃对话）
- Bot 被 Block 后消息发送会失败
- Token 泄露 → 去 @BotFather `/revoke` 换新
- Debian 13 必须用 venv
- 链接数据保存在 `links.json`，重启后不会丢失

## 📄 License

MIT
