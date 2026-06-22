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
├── tg-relay.py      # 主程序（~180 行）
├── tg-relay.service # systemd 服务文件
├── .env.example     # 环境变量模板
├── README.md        # 使用说明
└── links.json       # 链接数据文件（自动生成）
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

| 命令 | 说明 | 权限 |
|------|------|------|
| `/start` | 欢迎信息 | 所有人 |
| `/who` | 查看当前对话对象 | Owner |
| `/links` | 查看可用链接 | 所有人 |
| `/linkadd <name>;<url>` | 添加新链接（分号分隔，默认类别"常用"） | Owner |
| `/linkadd <name>;<url>;<category>` | 添加新链接（指定类别） | Owner |
| `/linkdel <序号>` | 删除指定序号的链接（如 `/linkdel 1`） | Owner |
| `/linkdel <链接名>` | 删除指定名称的链接（如 `/linkdel VSCode`） | Owner |
| `/linkcat <category>` | 按类别查看链接 | 所有人 |

### 链接管理示例

```
# owner 添加链接（使用分号分隔）
/linkadd VSCode;https://code.visualstudio.com

# owner 添加带类别的链接
/linkadd Python;https://www.python.org;学习

# 查看所有链接（带序号，可用于删除）
/links

# 删除链接（两种方式任选）
/linkdel 1              # 通过序号删除
/linkdel VSCode         # 通过名称删除

# 查看开发类链接
/links 开发

# 查看特定类别
/linkcat 开发
```

# 🛑 停止运行

### 方式 1：前台运行时（Ctrl+C）
```bash
# 在运行机器人的终端中
Ctrl + C
# 然后输入 y 确认退出
```

### 方式 2：systemd 服务（已配置）
```bash
sudo systemctl stop tg-relay
sudo systemctl status tg-relay  # 查看状态
```

## 📊 资源占用

在 512MB VPS 上实测：

| 指标 | 数值 |
|:---|:---|
| cgroup Memory | ~26MB |
| RSS | ~38MB |
| 占比 | ~5% |

### 查看内存占用

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
- Bot 被 Block 后消息发送会失败（`bot was blocked by the user`）
- Token 泄露 → 去 @BotFather `/revoke` 换新
- Debian 13 必须用 venv（`externally-managed-environment` 限制）
- 链接数据保存在 `links.json`（与脚本同目录），机器人重启后不会丢失

## 📄 License

MIT