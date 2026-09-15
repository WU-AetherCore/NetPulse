"""
NetPulse - 设备扫描模块
扫描局域网内所有设备
"""
import subprocess
import threading
import time
import re
from database import upsert_device, mark_device_offline, get_all_devices


def scan_devices():
    """扫描局域网设备（使用nmap或arp-scan）"""
    devices = []
    
    # 方法1: 使用nmap快速扫描
    try:
        result = subprocess.run(
            ['nmap', '-sn', '192.168.1.0/24', '-T4', '--min-parallelism', '50'],
            capture_output=True, text=True, timeout=60
        )
        # 解析nmap输出
        lines = result.stdout.split('\n')
        current_ip = None
        for line in lines:
            if 'Nmap scan report for' in line:
                match = re.search(r'192\.168\.1\.(\d+)', line)
                if match:
                    current_ip = f'192.168.1.{match.group(1)}'
            elif 'MAC Address:' in line and current_ip:
                mac_match = re.search(r'([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})', line)
                if mac_match:
                    mac = mac_match.group(1).lower()
                    vendor = ''
                    vendor_match = re.search(r'MAC Address:.*\((.*)\)', line)
                    if vendor_match:
                        vendor = vendor_match.group(1)
                    devices.append({'ip': current_ip, 'mac': mac, 'vendor': vendor})
                    current_ip = None
    except Exception as e:
        print(f"nmap扫描失败: {e}")
    
    # 方法2: 如果nmap没找到，用arp-scan
    if not devices:
        try:
            result = subprocess.run(
                ['arp-scan', '--localnet', '--interface=eth0'],
                capture_output=True, text=True, timeout=30
            )
            for line in result.stdout.split('\n'):
                parts = line.split()
                if len(parts) >= 2 and re.match(r'([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', parts[1]):
                    ip = parts[0]
                    mac = parts[1].lower()
                    vendor = parts[2] if len(parts) > 2 else ''
                    devices.append({'ip': ip, 'mac': mac, 'vendor': vendor})
        except Exception as e:
            print(f"arp-scan失败: {e}")
    
    # 方法3: 从ARP表补充
    try:
        result = subprocess.run(['arp', '-n'], capture_output=True, text=True, timeout=10)
        arp_ips = set(d['ip'] for d in devices)
        for line in result.stdout.split('\n')[1:]:
            parts = line.split()
            if len(parts) >= 3 and '192.168.1.' in parts[0]:
                ip = parts[0]
                mac = parts[2].lower()
                if ip not in arp_ips and mac != '(incomplete)':
                    devices.append({'ip': ip, 'mac': mac, 'vendor': ''})
    except Exception as e:
        print(f"ARP表读取失败: {e}")
    
    # 更新数据库
    for dev in devices:
        upsert_device(dev['mac'], dev['ip'], dev.get('vendor', ''))
    
    # 标记离线设备
    all_devs = get_all_devices()
    online_macs = set(d['mac'] for d in devices)
    for dev in all_devs:
        if dev['mac'] not in online_macs and dev['is_online']:
            # 只有超过5分钟没更新的才标记离线
            if time.time() - dev['last_seen'] > 300:
                mark_device_offline(dev['mac'])
    
    return devices


class Scanner(threading.Thread):
    """后台扫描线程"""
    
    def __init__(self, interval=30):
        super().__init__(daemon=True)
        self.interval = interval
        self.running = True
    
    def run(self):
        while self.running:
            try:
                scan_devices()
            except Exception as e:
                print(f"扫描错误: {e}")
            time.sleep(self.interval)
    
    def stop(self):
        self.running = False
