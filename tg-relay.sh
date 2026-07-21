#!/bin/bash

# =========================================================================
# TG Relay 专属 Docker 部署运维工具 (tg-relay.sh)
# 运行方式: chmod +x tg-relay.sh && ./tg-relay.sh
# 一键: curl -sSfL https://raw.githubusercontent.com/alivedou/tg-relay/main/tg-relay.sh -o tg-relay.sh && chmod +x tg-relay.sh && ./tg-relay.sh
# 纯 Docker 命令，零依赖 compose（对齐 fe.sh 菜单风格）
# =========================================================================

# 安装根目录：优先 TG_HOME，否则 /opt/tg-relay，兜底脚本所在目录
if [ -n "$TG_HOME" ]; then
    BASE_DIR="$TG_HOME"
elif [ -d "/opt" ] && [ -w "/opt" ] || [ "$(id -u)" = "0" ]; then
    BASE_DIR="/opt/tg-relay"
else
    BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
mkdir -p "$BASE_DIR"
ENV_FILE="$BASE_DIR/.env"
DATA_DIR="$BASE_DIR/data"
IMAGE_CACHE="$BASE_DIR/.image_cache"
CACHE_IP_FILE="$BASE_DIR/.external_ip"
CONTAINER_NAME="tg-relay-bot"
DEFAULT_IMAGE="ghcr.io/alivedou/tg-relay:latest"
CONTAINER_PORT=8080

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# ---- 工具函数 ----

generate_random_secret() {
    if command -v openssl &>/dev/null; then
        openssl rand -hex 16
    else
        cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 32 | head -n 1
    fi
}

load_env() {
    if [ -f "$ENV_FILE" ]; then
        # shellcheck disable=SC2046
        export $(grep -v '^#' "$ENV_FILE" | grep -v '^\s*$' | xargs) 2>/dev/null || true
    fi
}

save_env_var() {
    local key=$1 value=$2
    touch "$ENV_FILE"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        if [ "$(uname)" = "Darwin" ]; then
            sed -i "" "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
        else
            sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
        fi
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

mask_secret() {
    local s="$1"
    if [ -z "$s" ]; then
        echo "未设置"
        return
    fi
    local n=${#s}
    if [ "$n" -le 8 ]; then
        echo "***"
    else
        echo "${s:0:4}...${s: -4}"
    fi
}

generate_default_env() {
    if [ ! -f "$ENV_FILE" ]; then
        echo -e "${YELLOW}未检测到现有环境配置，正在生成 .env 模板...${NC}"
        local admin_tok
        admin_tok=$(generate_random_secret)
        cat <<EOF > "$ENV_FILE"
# TG Relay — 由 tg-relay.sh 管理
# 必填
TG_BOT_TOKEN=
TG_OWNER_ID=

# 宿主机映射端口（脚本用；容器内固定 8080）
HOST_PORT=8080

# 可选
TG_LOG_LEVEL=INFO
TG_ADMIN_TOKEN=$admin_tok
TG_WEBHOOK_URL=
TG_WELCOME_OWNER=
TG_WELCOME_STRANGER=
TG_OWNER_CONTACT=
TG_RATE_LIMIT=0
TG_RATE_WINDOW=10
TG_MSG_HEADER=
TG_MSG_FOOTER=

# 镜像（安装时也可改）
IMAGE=$DEFAULT_IMAGE
EOF
        chmod 600 "$ENV_FILE" 2>/dev/null || true
        echo -e "${GREEN}✓ .env 已生成: ${YELLOW}$ENV_FILE${NC}"
        echo -e "${YELLOW}⚠ 请先在「项目配置」填写 TG_BOT_TOKEN 与 TG_OWNER_ID${NC}"
        sleep 1
    fi
}

ensure_docker_env() {
    if ! command -v docker &>/dev/null; then
        echo -e "${YELLOW}Docker 未安装，正在自动安装...${NC}"
        if curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun; then
            echo -e "${GREEN}✓ Docker 安装完成${NC}"
        else
            echo -e "${RED}❌ 自动安装失败，请手动安装 Docker${NC}"
            echo "  curl -fsSL https://get.docker.com | bash"
            exit 1
        fi
    fi

    if ! docker info &>/dev/null; then
        echo -e "${YELLOW}Docker daemon 未运行，尝试启动...${NC}"
        if command -v systemctl &>/dev/null; then
            systemctl start docker 2>/dev/null || sudo systemctl start docker 2>/dev/null || true
        elif command -v service &>/dev/null; then
            service docker start 2>/dev/null || sudo service docker start 2>/dev/null || true
        fi
        sleep 2
        if ! docker info &>/dev/null; then
            echo -e "${RED}❌ Docker daemon 无法启动${NC}"
            exit 1
        fi
    fi
    echo -e "${GREEN}✓ Docker 引擎运行正常${NC}"
}

save_image() { echo "$1" > "$IMAGE_CACHE"; }

get_image() {
    if [ -f "$IMAGE_CACHE" ]; then
        cat "$IMAGE_CACHE"
        return
    fi
    docker inspect "$CONTAINER_NAME" --format '{{.Config.Image}}' 2>/dev/null && return
    load_env
    if [ -n "${IMAGE:-}" ]; then
        echo "$IMAGE"
        return
    fi
    echo "$DEFAULT_IMAGE"
}

ensure_data_dirs() {
    mkdir -p "$DATA_DIR"
    chmod 755 "$DATA_DIR" 2>/dev/null || true
}

container_running() {
    docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER_NAME"
}

container_exists() {
    docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER_NAME"
}

refresh_public_ip() {
    if command -v curl &>/dev/null; then
        local ip
        ip=$(curl -s --max-time 2 ifconfig.me 2>/dev/null || curl -s --max-time 2 ip.sb 2>/dev/null)
        [ -n "$ip" ] && echo "$ip" > "$CACHE_IP_FILE"
    fi
}

host_port() {
    load_env
    echo "${HOST_PORT:-8080}"
}

check_required_env() {
    load_env
    local ok=0
    if [ -z "${TG_BOT_TOKEN:-}" ]; then
        echo -e "${RED}❌ 未设置 TG_BOT_TOKEN${NC}"
        ok=1
    fi
    if [ -z "${TG_OWNER_ID:-}" ]; then
        echo -e "${RED}❌ 未设置 TG_OWNER_ID${NC}"
        ok=1
    fi
    if [ "$ok" -ne 0 ]; then
        echo -e "${YELLOW}请先选菜单 1「项目配置」填写必填项${NC}"
        return 1
    fi
    return 0
}

# 统一 docker run（与 README 手动部署一致）
run_container() {
    local img="$1"
    load_env
    local port
    port=$(host_port)

    docker run -d \
        --name "$CONTAINER_NAME" \
        --restart unless-stopped \
        -p "${port}:${CONTAINER_PORT}" \
        --env-file "$ENV_FILE" \
        -e TG_PORT="${CONTAINER_PORT}" \
        -e TG_DATA_DIR=/app/data \
        -v "${DATA_DIR}:/app/data" \
        "$img"
}

show_access_urls() {
    load_env
    local port
    port=$(host_port)

    if ! container_running; then
        echo -e "应用状态          : ${YELLOW}⚠ 未部署/未运行（选菜单 2 部署）${NC}"
        return
    fi

    echo -e "应用状态          : ${GREEN}● 运行中${NC}"
    local local_ip
    local_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -z "$local_ip" ] && local_ip="127.0.0.1"
    echo -e "健康检查(本机)    : ${GREEN}http://127.0.0.1:${port}/health${NC}"
    echo -e "应用内网访问地址  : ${GREEN}http://${local_ip}:${port}${NC}"

    if [ -f "$CACHE_IP_FILE" ]; then
        echo -e "应用公网访问地址  : ${GREEN}http://$(cat "$CACHE_IP_FILE"):${port}${NC}"
    else
        refresh_public_ip
        [ -f "$CACHE_IP_FILE" ] && echo -e "应用公网访问地址  : ${GREEN}http://$(cat "$CACHE_IP_FILE"):${port}${NC}"
    fi

    if [ -n "${TG_ADMIN_TOKEN:-}" ]; then
        echo -e "管理面板          : ${GREEN}http://${local_ip}:${port}/admin?token=***${NC}"
    fi
    if [ -n "${TG_WEBHOOK_URL:-}" ]; then
        echo -e "Webhook URL       : ${GREEN}${TG_WEBHOOK_URL}${NC}"
    else
        echo -e "模式              : ${BLUE}Polling（未设 TG_WEBHOOK_URL）${NC}"
    fi

    if [ "$port" != "80" ] && [ "$port" != "443" ]; then
        echo -e "${YELLOW}📌 如无法访问，请检查安全组/防火墙是否放行端口 ${port}${NC}"
        echo -e "${YELLOW}📌 本机 80 若已被 nginx 占用，请用自定义 HOST_PORT，勿强占 80${NC}"
    fi
}

health_probe() {
    local port
    port=$(host_port)
    if command -v curl &>/dev/null; then
        curl -s --max-time 3 "http://127.0.0.1:${port}/health" 2>/dev/null || echo ""
    fi
}

# ---- 菜单 1：项目配置 ----

project_config() {
    generate_default_env
    while true; do
        load_env
        clear
        echo -e "${BLUE}====================================================="
        echo -e "         🐳 TG Relay 配置中心                         "
        echo -e "=====================================================${NC}"
        echo -e "配置文件: ${YELLOW}$ENV_FILE${NC}"
        echo -e "数据目录: ${YELLOW}$DATA_DIR${NC}"
        echo -e "-----------------------------------------------------"
        echo -e " 1. Bot Token     : ${GREEN}$(mask_secret "${TG_BOT_TOKEN:-}")${NC}"
        echo -e " 2. Owner ID      : ${GREEN}${TG_OWNER_ID:-未设置}${NC}"
        echo -e " 3. 对外端口      : ${GREEN}${HOST_PORT:-8080}${NC}  (容器内固定 ${CONTAINER_PORT})"
        echo -e " 4. 日志级别      : ${GREEN}${TG_LOG_LEVEL:-INFO}${NC}"
        echo -e " 5. 管理 Token    : ${GREEN}$(mask_secret "${TG_ADMIN_TOKEN:-}")${NC}"
        echo -e " 6. Webhook URL   : ${GREEN}${TG_WEBHOOK_URL:-未设置}${NC}"
        echo -e " 7. Owner 欢迎语  : ${GREEN}${TG_WELCOME_OWNER:-默认}${NC}"
        echo -e " 8. 陌生人欢迎语  : ${GREEN}${TG_WELCOME_STRANGER:-默认}${NC}"
        echo -e " 9. 镜像地址      : ${GREEN}${IMAGE:-$(get_image)}${NC}"
        echo -e "-----------------------------------------------------"
        echo -e " a. 随机生成管理 Token（完整显示，回车再继续）"
        echo -e " v. 查看完整管理 Token / 管理面板链接"
        echo -e " s. 保存返回 | q. 返回"
        echo -e "-----------------------------------------------------"

        read -r -p "请输入编号 (1-9, a/v/s/q): " cfg_choice
        case $cfg_choice in
            1)
                read -r -p "TG_BOT_TOKEN: " v
                [ -n "$v" ] && save_env_var "TG_BOT_TOKEN" "$v"
                ;;
            2)
                read -r -p "TG_OWNER_ID (数字): " v
                [ -n "$v" ] && save_env_var "TG_OWNER_ID" "$v"
                ;;
            3)
                read -r -p "宿主机对外端口 [当前 ${HOST_PORT:-8080}]: " v
                if [ -n "$v" ]; then
                    save_env_var "HOST_PORT" "$v"
                    echo -e "\n${YELLOW}⚠ 端口已更新为 $v，需重建容器生效${NC}"
                    if container_exists; then
                        read -r -p "是否立即重建？(Y/n): " rc
                        if [ "$rc" != "n" ] && [ "$rc" != "N" ]; then
                            if ! check_required_env; then
                                sleep 2
                            else
                                local img
                                img=$(get_image)
                                docker stop "$CONTAINER_NAME" 2>/dev/null || true
                                docker rm "$CONTAINER_NAME" 2>/dev/null || true
                                if run_container "$img"; then
                                    save_image "$img"
                                    echo -e "${GREEN}✓ 容器已用新端口重建${NC}"
                                    refresh_public_ip
                                    echo ""
                                    show_access_urls
                                    sleep 2
                                else
                                    echo -e "${RED}❌ 重建失败${NC}"
                                    sleep 2
                                fi
                            fi
                        fi
                    fi
                fi
                ;;
            4)
                read -r -p "日志级别 DEBUG/INFO/WARNING/ERROR: " v
                [ -n "$v" ] && save_env_var "TG_LOG_LEVEL" "$v"
                ;;
            5)
                echo -e "当前完整 Token: ${YELLOW}${TG_ADMIN_TOKEN:-未设置}${NC}"
                read -r -p "新 TG_ADMIN_TOKEN（回车不改）: " v
                [ -n "$v" ] && save_env_var "TG_ADMIN_TOKEN" "$v"
                ;;
            6)
                read -r -p "TG_WEBHOOK_URL (空则 Polling): " v
                save_env_var "TG_WEBHOOK_URL" "$v"
                ;;
            7)
                read -r -p "TG_WELCOME_OWNER: " v
                save_env_var "TG_WELCOME_OWNER" "$v"
                ;;
            8)
                read -r -p "TG_WELCOME_STRANGER: " v
                save_env_var "TG_WELCOME_STRANGER" "$v"
                ;;
            9)
                read -r -p "镜像地址: " v
                if [ -n "$v" ]; then
                    save_env_var "IMAGE" "$v"
                    save_image "$v"
                fi
                ;;
            a|A)
                local tok
                tok=$(generate_random_secret)
                save_env_var "TG_ADMIN_TOKEN" "$tok"
                load_env
                local port
                port=$(host_port)
                echo -e "${GREEN}✓ 已生成并写入 .env${NC}"
                echo -e "完整管理 Token : ${YELLOW}${tok}${NC}"
                echo -e "管理面板链接   : ${YELLOW}http://127.0.0.1:${port}/admin?token=${tok}${NC}"
                echo -e "${RED}⚠ 改 Token 后若容器已在跑，需菜单 2 重新部署才生效${NC}"
                read -r -p "复制好后按 Enter 继续..." _
                ;;
            v|V)
                load_env
                local port
                port=$(host_port)
                echo -e "-----------------------------------------------------"
                if [ -z "${TG_ADMIN_TOKEN:-}" ]; then
                    echo -e "${YELLOW}TG_ADMIN_TOKEN 未设置${NC}"
                else
                    echo -e "完整管理 Token : ${YELLOW}${TG_ADMIN_TOKEN}${NC}"
                    echo -e "本机面板       : ${GREEN}http://127.0.0.1:${port}/admin?token=${TG_ADMIN_TOKEN}${NC}"
                    local lip
                    lip=$(hostname -I 2>/dev/null | awk '{print $1}')
                    [ -n "$lip" ] && echo -e "内网面板       : ${GREEN}http://${lip}:${port}/admin?token=${TG_ADMIN_TOKEN}${NC}"
                fi
                echo -e "配置文件       : ${YELLOW}${ENV_FILE}${NC}"
                echo -e "-----------------------------------------------------"
                read -r -p "按 Enter 返回配置页..." _
                ;;
            s|q|S|Q) break ;;
            *) echo -e "${RED}❌ 无效编号${NC}"; sleep 1 ;;
        esac
    done
}

# ---- 菜单 2：安装部署 ----

deploy_version() {
    clear
    echo -e "${BLUE}====================================================="
    echo -e "       ⚡ TG Relay 一键部署                           "
    echo -e "=====================================================${NC}"

    ensure_docker_env
    generate_default_env
    load_env
    ensure_data_dirs

    if ! check_required_env; then
        sleep 2
        return
    fi

    local default_image
    default_image=$(get_image)
    [ -z "$default_image" ] && default_image="$DEFAULT_IMAGE"

    echo -e "数据目录: ${GREEN}$DATA_DIR${NC}"
    echo -e "对外端口: ${GREEN}$(host_port)${NC} → 容器 ${CONTAINER_PORT}"
    echo -e "当前镜像默认值: ${GREEN}$default_image${NC}"
    read -r -p "输入镜像地址 [回车使用默认]: " custom_image
    local target_image="${custom_image:-$default_image}"

    echo -e "\n${BLUE}>>> 拉取镜像: $target_image${NC}"
    if ! docker pull "$target_image"; then
        echo -e "${RED}❌ 镜像拉取失败${NC}"
        echo -e "  1. 检查镜像名是否正确（替换 OWNER/REPO）"
        echo -e "  2. 私有包需: echo PAT | docker login ghcr.io -u USER --password-stdin"
        echo -e "  3. 确认 GitHub Actions 已构建推送"
        sleep 3
        return
    fi
    echo -e "${GREEN}✓ 镜像拉取完成${NC}"

    echo -e "${YELLOW}停旧容器、启动新容器...${NC}"
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" 2>/dev/null || true

    if ! run_container "$target_image"; then
        echo -e "${RED}❌ 容器启动失败！${NC}"
        echo -e "查看日志: docker logs $CONTAINER_NAME"
        sleep 3
        return
    fi

    save_image "$target_image"
    save_env_var "IMAGE" "$target_image"

    sleep 2
    local health
    health=$(health_probe)
    if [ -n "$health" ]; then
        echo -e "${GREEN}✓ 健康检查: $health${NC}"
    else
        echo -e "${YELLOW}⚠ 健康检查暂无响应，可稍后菜单 3/4 查看${NC}"
    fi

    echo -e "\n${GREEN}★ 部署成功！${NC}"
    refresh_public_ip
    echo ""
    show_access_urls
    echo -e "-----------------------------------------------------"
    echo -e "日志: ${YELLOW}docker logs -f $CONTAINER_NAME${NC}"
    read -r -p "按 Enter 返回主菜单..." dummy
}

# ---- 菜单 3：运行状态 ----

check_status() {
    clear
    echo -e "${BLUE}====================================================="
    echo -e "         🔍 容器运行状态                              "
    echo -e "=====================================================${NC}"

    if ! container_running; then
        echo -e "${YELLOW}容器未运行${NC}"
        echo -e "-----------------------------------------------------"
        docker ps -a --filter "name=^/${CONTAINER_NAME}$" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "(无记录)"
    else
        echo -e "${GREEN}● 运行中${NC}"
        echo -e "-----------------------------------------------------"
        docker ps --filter "name=^/${CONTAINER_NAME}$" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
        echo -e "-----------------------------------------------------"
        echo -e "资源占用:"
        docker stats --no-stream --filter "name=$CONTAINER_NAME" --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}" 2>/dev/null
        echo -e "-----------------------------------------------------"
        local health
        health=$(health_probe)
        if [ -n "$health" ]; then
            echo -e "健康检查: ${GREEN}$health${NC}"
        else
            echo -e "健康检查: ${YELLOW}无响应${NC}"
        fi
    fi

    echo -e "-----------------------------------------------------"
    show_access_urls
    echo -e "-----------------------------------------------------"
    read -r -p "按 Enter 返回主菜单..." dummy
}

# ---- 菜单 4：实时日志 ----

view_logs() {
    clear
    echo -e "${BLUE}====================================================="
    echo -e "         📋 实时日志 (Ctrl+C 退出)                    "
    echo -e "=====================================================${NC}"

    if ! container_exists; then
        echo -e "${YELLOW}容器不存在${NC}"
        read -r -p "按 Enter 返回..." dummy
        return
    fi

    if ! container_running; then
        echo -e "${YELLOW}容器未运行，显示最近日志:${NC}"
        docker logs --tail=50 "$CONTAINER_NAME" 2>/dev/null || echo "(无日志)"
        read -r -p "按 Enter 返回..." dummy
    else
        docker logs -f --tail=100 "$CONTAINER_NAME"
    fi
}

# ---- 菜单 5：数据重置 ----

data_reset() {
    clear
    echo -e "${RED}====================================================="
    echo -e "             🚨 数据重置（不可撤销）                   "
    echo -e "=====================================================${NC}"
    echo -e "此操作将清空:"
    echo -e "  - 数据库与链接数据 ($DATA_DIR/)"
    echo -e "${GREEN}保留: .env 配置（Token、端口等）${NC}"
    echo -e "-----------------------------------------------------"

    read -r -p "确认重置？输入大写 CONFIRM: " cf
    if [ "$cf" != "CONFIRM" ]; then
        echo -e "${BLUE}已取消${NC}"
        sleep 1
        return
    fi

    echo -e "\n${YELLOW}停止容器...${NC}"
    docker stop "$CONTAINER_NAME" 2>/dev/null || true

    echo -e "${YELLOW}清空数据...${NC}"
    rm -rf "${DATA_DIR:?}/"*
    mkdir -p "$DATA_DIR"

    echo -e "${YELLOW}重启容器...${NC}"
    if container_exists; then
        docker start "$CONTAINER_NAME" 2>/dev/null || {
            local img
            img=$(get_image)
            docker rm "$CONTAINER_NAME" 2>/dev/null || true
            run_container "$img" 2>/dev/null || true
        }
    fi

    echo -e "\n${GREEN}✓ 数据已重置${NC}"
    sleep 2
}

# ---- 菜单 6：彻底卸载 ----

uninstall_all() {
    clear
    echo -e "${RED}====================================================="
    echo -e "             🌋 彻底卸载                              "
    echo -e "=====================================================${NC}"
    echo -e "此操作将:"
    echo -e "  1. 停止并删除容器 $CONTAINER_NAME"
    echo -e "  2. 删除缓存镜像记录对应镜像（尽力）"
    echo -e "  3. 删除数据目录 $DATA_DIR"
    echo -e "  4. 删除配置 $ENV_FILE 及缓存文件"
    echo -e "-----------------------------------------------------"

    read -r -p "输入 UNINSTALL 确认: " cf
    if [ "$cf" != "UNINSTALL" ]; then
        echo -e "${BLUE}已取消${NC}"
        sleep 1
        return
    fi

    echo -e "\n${YELLOW}停止并删除容器...${NC}"
    docker stop "$CONTAINER_NAME" 2>/dev/null || true
    docker rm "$CONTAINER_NAME" 2>/dev/null || true

    local img
    img=$(get_image)
    if [ -n "$img" ] && [ "$img" != "$DEFAULT_IMAGE" ]; then
        echo -e "${YELLOW}删除镜像 $img ...${NC}"
        docker rmi "$img" 2>/dev/null || true
    fi
    docker image prune -f 2>/dev/null || true

    echo -e "${YELLOW}删除数据与配置...${NC}"
    [ -d "$DATA_DIR" ] && rm -rf "$DATA_DIR"
    [ -f "$ENV_FILE" ] && rm -f "$ENV_FILE"
    [ -f "$IMAGE_CACHE" ] && rm -f "$IMAGE_CACHE"
    [ -f "$CACHE_IP_FILE" ] && rm -f "$CACHE_IP_FILE"

    echo -e "\n${RED}✓ 已彻底卸载${NC}"
    sleep 3
    exit 0
}

# ---- 主菜单 ----

while true; do
    clear
    echo -e "${GREEN}====================================================="
    echo -e "      🐳 TG Relay 运维控制台                          "
    echo -e "      双向匿名中继机器人 Docker 部署                   "
    echo -e "=====================================================${NC}"
    echo -e "路径: ${YELLOW}$BASE_DIR${NC}"
    echo -e "-----------------------------------------------------"
    show_access_urls
    echo -e "-----------------------------------------------------"
    echo -e "1. ⚙️  项目配置"
    echo -e "2. ⚡ 安装部署"
    echo -e "3. 🔍 运行状态"
    echo -e "4. 📋 实时日志"
    echo -e "5. 🧹 数据重置"
    echo -e "6. ❌ 彻底卸载"
    echo -e "7. 🚪 退出"
    echo -e "-----------------------------------------------------"
    read -r -p "请选择 (1-7): " mc

    case $mc in
        1) project_config ;;
        2) deploy_version ;;
        3) check_status ;;
        4) view_logs ;;
        5) data_reset ;;
        6) uninstall_all ;;
        7) echo -e "\n${BLUE}再见！${NC}"; exit 0 ;;
        *) echo -e "${RED}❌ 无效选项${NC}"; sleep 1 ;;
    esac
done
