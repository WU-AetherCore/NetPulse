"""
NetPulse - 网络设备管理系统
配置文件
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
SCAN_INTERVAL = 30          # 设备扫描间隔（秒）
TRAFFIC_INTERVAL = 3        # 流量统计间隔（秒，更精确的实时速率）
PING_TIMEOUT = 1             # ping超时（秒）
PING_COUNT = 1               # ping次数

# 网络配置
NETWORK_CIDR = "192.168.1.0/24"
NETWORK_GATEWAY = "192.168.1.1"
MONITOR_INTERFACE = "eth0"   # 主要监控网口

# 设备管理配置
GATEWAY_IP = "192.168.1.1"           # 网关IP
LOCAL_MAC = "f8:29:eb:6a:da:15"      # 本机wlan0 MAC地址
MANAGE_INTERFACE = "wlan0"            # 设备管理网口（流量经过的接口）

# 流量统计
TRAFFIC_CHAIN = "NETPULSE"   # iptables自定义链名
HOURLY_RETENTION_DAYS = 7    # 小时数据保留天数
DAILY_RETENTION_DAYS = 90    # 天数据保留天数

# 设备状态判定
OFFLINE_THRESHOLD = 120      # 超过多少秒未检测到视为离线（秒）

# 管理员密码（首次登录使用，可在界面修改）
ADMIN_PASSWORD = "admin123"
