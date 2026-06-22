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
├── tg-relay.py      # 主程序（~100 行）
├── tg-relay.service # systemd 服务文件
├── .env.example     # 环境变量模板
└── README.md
```

## 🚀 部署

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

这样别人只能通过 @username 搜索到你，发消息会走 Bot 中继。

## 🛠️ 命令

| 命令 | 说明 |
|:---|:---|
| `/start` | 欢迎信息 |
| `/who` | 查看当前对话对象（仅 owner） |

## 📊 资源占用

在 512MB VPS 上实测：

| 指标 | 数值 |
|:---|:---|
| cgroup Memory | ~26MB |
| RSS | ~38MB |
| 占比 | ~5% |

## ⚠️ 注意事项

- 仅支持**一对一**对话（同一时间维护一个活跃对话）
- Bot 被 Block 后消息发送会失败（`bot was blocked by the user`）
- Token 泄露 → 去 @BotFather `/revoke` 换新
- Debian 13 必须用 venv（`externally-managed-environment` 限制）

## 📄 License

MIT
