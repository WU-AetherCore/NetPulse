"""
NetPulse - 网络设备管理系统
配置文件（开源仓库模板）

注意：一键安装脚本 install.sh 会自动检测网关/网口/本机 MAC 并重新生成本文件。
手动部署时，请按实际网络环境修改下面的 GATEWAY_IP / LOCAL_MAC / MANAGE_INTERFACE。
敏感值（如管理员密码）建议通过环境变量注入，不要把真实密码提交到代码仓库。
"""
import os

# 基础配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "netpulse.db")
LOG_PATH = os.path.join(BASE_DIR, "netpulse.log")

# Web服务
WEB_HOST = "0.0.0.0"
WEB_PORT = 8081

# 扫描配置
SCAN_INTERVAL = 60          # 设备扫描间隔（秒）
TRAFFIC_INTERVAL = 3        # 流量统计间隔（秒，更精确的实时速率）
PING_TIMEOUT = 1            # ping超时（秒）
PING_COUNT = 1              # ping次数

# 网络配置（请按实际网络修改；install.sh 会自动检测）
NETWORK_CIDR = "192.168.1.0/24"
NETWORK_GATEWAY = "192.168.1.1"
MONITOR_INTERFACE = "eth0"  # 主要监控网口

# 设备管理配置
GATEWAY_IP = "192.168.1.1"            # 网关IP（install.sh 自动检测）
LOCAL_MAC = "00:00:00:00:00:00"       # 本机管理网口 MAC（install.sh 自动检测，手动部署请填实际MAC）
MANAGE_INTERFACE = "eth0"             # 设备管理网口（流量经过的接口）

# 流量统计
TRAFFIC_CHAIN = "NETPULSE"   # iptables自定义链名
HOURLY_RETENTION_DAYS = 7    # 小时数据保留天数
DAILY_RETENTION_DAYS = 90     # 天数据保留天数

# 设备状态判定
OFFLINE_THRESHOLD = 120      # 超过多少秒未检测到视为离线（秒）

# 管理员密码：优先读取环境变量 NETPULSE_ADMIN_PASSWORD；未设置时使用出厂默认 admin123。
# 生产环境请务必通过环境变量设置强密码，或首次登录后在界面修改。
ADMIN_PASSWORD = os.environ.get("NETPULSE_ADMIN_PASSWORD", "admin123")

# ARP 欺骗白名单：不希望被全局流量监控劫持的设备 MAC 列表（例如摄像头/IoT设备）。
# install.sh 默认留空；如需排除设备，按下面格式填写其 MAC 地址。
# SPOOF_WHITELIST = ["aa:bb:cc:dd:ee:ff"]
SPOOF_WHITELIST = []
