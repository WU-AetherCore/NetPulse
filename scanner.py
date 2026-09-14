"""
NetPulse - 设备扫描模块
通过ARP扫描和ping扫描发现局域网设备
"""
import subprocess
import re
import time
import threading
from config import NETWORK_CIDR, PING_TIMEOUT, PING_COUNT, OFFLINE_THRESHOLD
from database import upsert_device, mark_device_offline, record_connection_event


# OUI厂商识别（常用前缀）
OUI_DATABASE = {
    "4c:d2:fb": "中国移动",
    "44:f7:70": "小米路由器",
    "f8:29:eb": "Orange Pi",
    "02:00:ab": "Orange Pi",
    "d2:41:30": "Android设备",
    "de:ef:0e": "Android设备",
    "42:b3:9a": "Android设备",
    "4c:03:4f": "Android设备",
    "e6:f8:95": "Android设备",
    "c8:75:f4": "华为设备",
    "a4:50:46": "小米手机",
    "64:09:80": "小米手机",
    "9c:99:a0": "小米手机",
    "f0:99:bf": "OPPO手机",
    "94:65:2d": "VIVO手机",
    "68:54:5a": "苹果设备",
    "3c:22:fb": "苹果设备",
    "a4:83:e7": "苹果设备",
    "00:11:32": "群晖NAS",
    "00:17:88": "飞利浦Hue",
    "b8:27:eb": "树莓派",
    "dc:a6:32": "树莓派4",
    "e4:5f:01": "树莓派400",
}


def get_vendor(mac):
    """根据MAC地址前3字节识别厂商"""
    if not mac or len(mac) < 8:
        return "未知设备"
    oui = mac.lower()[:8]
    return OUI_DATABASE.get(oui, "未知设备")


def read_arp_table():
    """读取ARP表"""
    devices = {}
    try:
        result = subprocess.run(
            ["ip", "neigh", "show"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().split('\n'):
            if not line or 'FAILED' in line:
                continue
            parts = line.split()
            if len(parts) >= 4 and 'lladdr' in line:
                ip = parts[0]
                mac_idx = parts.index('lladdr') + 1
                if mac_idx < len(parts):
                    mac = parts[mac_idx]
                    if mac and mac != '00:00:00:00:00:00':
                        # 优先使用IPv4地址，如果已经有IPv4就不用IPv6覆盖
                        is_ipv4 = '.' in ip and ':' not in ip
                        if mac not in devices or is_ipv4:
                            devices[mac] = ip
    except Exception as e:
        print(f"读取ARP表失败: {e}")
    return devices


def ping_scan():
    """ping扫描整个网段（快速唤醒设备）"""
    try:
        # 用arping或者ping扫描
        result = subprocess.run(
            ["bash", "-c",
             f"for i in $(seq 1 254); do ping -c 1 -W {PING_TIMEOUT} 192.168.1.$i >/dev/null 2>&1 & done; wait"],
            capture_output=True, text=True, timeout=15
        )
    except Exception as e:
        print(f"ping扫描失败: {e}")


def add_self_device():
    """自动把自己（Orange Pi）添加到设备列表"""
    try:
        import socket
        # 获取本机IP和MAC
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        
        # 从/sys/class/net获取MAC
        mac = None
        for iface in ['eth0', 'wlan0']:
            try:
                with open(f'/sys/class/net/{iface}/address', 'r') as f:
                    mac = f.read().strip()
                    break
            except:
                continue
        
        if local_ip and mac:
            from database import upsert_device
            upsert_device(mac, local_ip, name='Orange Pi Zero2', vendor='Orange Pi')
            print(f"[Scanner] 已添加本机设备: {local_ip} ({mac})")
    except Exception as e:
        print(f"[Scanner] 添加本机设备失败: {e}")


def scan_devices():
    """扫描所有设备"""
    # 先添加自己
    add_self_device()
    
    # 先ping扫描唤醒设备
    ping_scan()
    time.sleep(1)

    # 读取ARP表
    arp_devices = read_arp_table()

    # 更新数据库
    now = int(time.time())
    for mac, ip in arp_devices.items():
        vendor = get_vendor(mac)
        upsert_device(mac, ip, vendor=vendor)

    # 检查离线设备
    check_offline_devices()

    return arp_devices


def check_offline_devices():
    """检查并标记离线设备"""
    from database import get_all_devices
    now = int(time.time())
    devices = get_all_devices()
    for dev in devices:
        if dev['is_online'] and (now - dev['last_seen']) > OFFLINE_THRESHOLD:
            mark_device_offline(dev['mac'])


class Scanner(threading.Thread):
    """后台扫描线程"""
    def __init__(self, interval=30):
        super().__init__(daemon=True)
        self.interval = interval
        self.running = True

    def run(self):
        print(f"[Scanner] 设备扫描线程启动，间隔{self.interval}秒")
        while self.running:
            try:
                devices = scan_devices()
                print(f"[Scanner] 扫描完成，发现{len(devices)}台设备")
            except Exception as e:
                print(f"[Scanner] 扫描异常: {e}")
            time.sleep(self.interval)

    def stop(self):
        self.running = False
