"""
NetPulse - 配置文件
"""
import os

# Web服务配置
WEB_HOST = '0.0.0.0'
WEB_PORT = 8081
ADMIN_PASSWORD = 'admin'

# 数据库路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'netpulse.db')

# 数据保留天数
HOURLY_RETENTION_DAYS = 7
DAILY_RETENTION_DAYS = 90

# 网络配置
GATEWAY_IP = '192.168.1.1'
LOCAL_MAC = '02:81:05:09:f7:8c'
MANAGE_INTERFACE = 'eth0'

# ARP欺骗白名单（这些设备不被欺骗，例如摄像头等需要稳定连接的设备）
SPOOF_WHITELIST = [
    '44:37:0b:1a:58:57',  # 小米智能摄像机 C500双摄版
]
