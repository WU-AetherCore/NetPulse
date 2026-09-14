#!/bin/bash
# NetPulse 一键安装脚本
# 适用于 Ubuntu/Debian/Armbian 系统

set -e

echo "=========================================="
echo "  NetPulse 网脉 - 一键安装脚本"
echo "=========================================="
echo ""

# 检查是否为root
if [ "$EUID" -ne 0 ]; then
    echo "请使用 root 权限运行: sudo bash install.sh"
    exit 1
fi

# 配置变量
INSTALL_DIR="/opt/netpulse"
SERVICE_NAME="netpulse"
WEB_PORT=8081

echo "[1/6] 安装系统依赖..."
apt update -qq
apt install -y -qq python3 python3-pip python3-venv iptables iproute2 git curl > /dev/null
echo "  ✓ 系统依赖安装完成"

echo "[2/6] 下载 NetPulse..."
if [ -d "$INSTALL_DIR" ]; then
    echo "  检测到旧版本，备份中..."
    mv "$INSTALL_DIR" "${INSTALL_DIR}.backup.$(date +%Y%m%d%H%M%S)"
fi

git clone https://github.com/WU-AetherCore/NetPulse.git "$INSTALL_DIR"
cd "$INSTALL_DIR"
echo "  ✓ 下载完成"

echo "[3/6] 创建虚拟环境..."
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
echo "  ✓ 虚拟环境创建完成"

echo "[4/6] 自动检测网络配置..."
# 获取默认网关
GATEWAY_IP=$(ip route | grep default | awk '{print $3}' | head -1)
# 获取默认网口
MANAGE_IFACE=$(ip route | grep default | awk '{print $5}' | head -1)
# 获取本机IP
LOCAL_IP=$(ip addr show "$MANAGE_IFACE" | grep 'inet ' | awk '{print $2}' | cut -d/ -f1)
# 获取本机MAC
LOCAL_MAC=$(ip link show "$MANAGE_IFACE" | grep ether | awk '{print $2}')
# 获取网段
NETWORK_CIDR="${LOCAL_IP%.*}.0/24"

echo "  网关IP: $GATEWAY_IP"
echo "  网口: $MANAGE_IFACE"
echo "  本机IP: $LOCAL_IP"
echo "  本机MAC: $LOCAL_MAC"
echo "  网段: $NETWORK_CIDR"

# 写入配置文件
cat > config.py << EOF
"""
NetPulse - 网络设备管理系统
配置文件（自动生成）
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "netpulse.db")
LOG_PATH = os.path.join(BASE_DIR, "netpulse.log")

WEB_HOST = "0.0.0.0"
WEB_PORT = ${WEB_PORT}

SCAN_INTERVAL = 30
TRAFFIC_INTERVAL = 3
PING_TIMEOUT = 1
PING_COUNT = 1

NETWORK_CIDR = "${NETWORK_CIDR}"
NETWORK_GATEWAY = "${GATEWAY_IP}"
MONITOR_INTERFACE = "${MANAGE_IFACE}"

GATEWAY_IP = "${GATEWAY_IP}"
LOCAL_MAC = "${LOCAL_MAC}"
MANAGE_INTERFACE = "${MANAGE_IFACE}"

TRAFFIC_CHAIN = "NETPULSE"
HOURLY_RETENTION_DAYS = 7
DAILY_RETENTION_DAYS = 90

OFFLINE_THRESHOLD = 120

ADMIN_PASSWORD = "admin123"
EOF
echo "  ✓ 配置文件已生成"

echo "[5/6] 配置 systemd 服务..."
cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=NetPulse Network Device Management System
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl start ${SERVICE_NAME}
echo "  ✓ 服务已配置并启动"

echo "[6/6] 等待服务启动..."
sleep 5

if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo ""
    echo "=========================================="
    echo "  ✅ NetPulse 安装成功！"
    echo "=========================================="
    echo ""
    echo "  访问地址: http://${LOCAL_IP}:${WEB_PORT}"
    echo "  服务状态: systemctl status ${SERVICE_NAME}"
    echo "  查看日志: journalctl -u ${SERVICE_NAME} -f"
    echo "  重启服务: systemctl restart ${SERVICE_NAME}"
    echo "  停止服务: systemctl stop ${SERVICE_NAME}"
    echo ""
    echo "  安装目录: ${INSTALL_DIR}"
    echo "  配置文件: ${INSTALL_DIR}/config.py"
    echo "  数据库: ${INSTALL_DIR}/netpulse.db"
    echo ""
    echo "  首次使用请打开Web界面，"
    echo "  开启「全局流量监控模式」开始统计流量。"
    echo "=========================================="
else
    echo "  ❌ 服务启动失败，请查看日志:"
    echo "  journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
fi
