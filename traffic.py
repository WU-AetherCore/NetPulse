"""
NetPulse - 流量监控模块
通过iptables统计每台设备的流量
"""
import subprocess
import threading
import time
from database import update_traffic, update_current_rates, get_all_devices


class TrafficMonitor(threading.Thread):
    """流量监控线程"""
    
    def __init__(self, interval=10):
        super().__init__(daemon=True)
        self.interval = interval
        self.running = True
        self.last_counters = {}  # {mac: {upload: bytes, download: bytes}}
    
    def run(self):
        while self.running:
            try:
                self.monitor_traffic()
            except Exception as e:
                print(f"流量监控错误: {e}")
            time.sleep(self.interval)
    
    def get_iptables_counters(self):
        """获取iptables计数器"""
        try:
            result = subprocess.run(
                ['iptables', '-L', 'NETPULSE_TRAFFIC', '-v', '-n', '-x'],
                capture_output=True, text=True, timeout=10
            )
            counters = {}
            for line in result.stdout.split('\n'):
                parts = line.split()
                if len(parts) >= 8 and parts[0].isdigit():
                    bytes_count = int(parts[1])
                    # 解析源IP或目的IP
                    if parts[7].startswith('192.168.1.'):
                        ip = parts[7]
                        direction = 'upload'  # 源IP = 上传
                    elif len(parts) >= 9 and parts[8].startswith('192.168.1.'):
                        ip = parts[8]
                        direction = 'download'  # 目的IP = 下载
                    else:
                        continue
                    if ip not in counters:
                        counters[ip] = {'upload': 0, 'download': 0}
                    counters[ip][direction] = bytes_count
            return counters
        except Exception as e:
            print(f"获取iptables计数器失败: {e}")
            return {}
    
    def ensure_iptables_rules(self):
        """确保iptables规则存在（自动修复机制）"""
        try:
            # 检查链是否存在
            result = subprocess.run(
                ['iptables', '-L', 'NETPULSE_TRAFFIC', '-n'],
                capture_output=True, text=True, timeout=5
            )
            if 'No chain' in result.stderr or result.returncode != 0:
                # 创建链
                subprocess.run(['iptables', '-N', 'NETPULSE_TRAFFIC'], capture_output=True, timeout=5)
                subprocess.run(['iptables', '-I', 'FORWARD', '-j', 'NETPULSE_TRAFFIC'], capture_output=True, timeout=5)
                print("[Traffic] 自动创建iptables流量统计链")
                
                # 为所有已知设备添加规则
                devices = get_all_devices()
                for dev in devices:
                    ip = dev.get('ip')
                    if ip and ip.startswith('192.168.1.'):
                        subprocess.run(
                            ['iptables', '-A', 'NETPULSE_TRAFFIC', '-s', ip, '-j', 'RETURN'],
                            capture_output=True, timeout=5
                        )
                        subprocess.run(
                            ['iptables', '-A', 'NETPULSE_TRAFFIC', '-d', ip, '-j', 'RETURN'],
                            capture_output=True, timeout=5
                        )
        except Exception as e:
            print(f"确保iptables规则失败: {e}")
    
    def monitor_traffic(self):
        """监控流量"""
        # 确保规则存在
        self.ensure_iptables_rules()
        
        devices = get_all_devices()
        ip_to_mac = {d['ip']: d['mac'] for d in devices if d.get('ip')}
        
        counters = self.get_iptables_counters()
        
        for ip, counter in counters.items():
            mac = ip_to_mac.get(ip)
            if not mac:
                continue
            
            if mac in self.last_counters:
                last = self.last_counters[mac]
                upload_delta = counter['upload'] - last['upload']
                download_delta = counter['download'] - last['download']
                
                if upload_delta > 0 or download_delta > 0:
                    update_traffic(mac, upload_delta, download_delta)
                    # 计算速率（KB/s）
                    upload_rate = upload_delta / 1024 / self.interval
                    download_rate = download_delta / 1024 / self.interval
                    update_current_rates(mac, upload_rate, download_rate)
            
            self.last_counters[mac] = counter
        
        # 为没有流量的设备清零速率
        for dev in devices:
            if dev['is_online'] and dev['mac'] not in self.last_counters:
                update_current_rates(dev['mac'], 0, 0)
    
    def stop(self):
        self.running = False
