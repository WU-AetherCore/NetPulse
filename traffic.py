"""
NetPulse - 流量统计模块
使用iptables内核级计数精确统计每台设备的上下行流量
"""
import subprocess
import time
import threading
import re
from config import TRAFFIC_CHAIN, TRAFFIC_INTERVAL
from database import (
    get_all_devices, update_traffic, update_current_rates,
    record_connection_event, update_period_traffic, check_and_reset_period
)


def run_iptables(cmd):
    """执行iptables命令（需要sudo）"""
    try:
        full_cmd = ["sudo", "-S"] + cmd
        result = subprocess.run(
            full_cmd,
            input="orangepi\n",
            capture_output=True, text=True, timeout=10
        )
        return result.stdout, result.returncode
    except Exception as e:
        print(f"iptables命令失败: {e}")
        return "", -1


def setup_iptables():
    """初始化iptables规则链"""
    run_iptables(["iptables", "-N", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-F", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-D", "FORWARD", "-j", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-I", "FORWARD", "1", "-j", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-D", "INPUT", "-j", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-I", "INPUT", "1", "-j", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-D", "OUTPUT", "-j", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-I", "OUTPUT", "1", "-j", TRAFFIC_CHAIN])
    print("[Traffic] iptables规则链初始化完成（计数规则已插入到最前面）")


def ensure_iptables_chain():
    """检查并修复iptables规则链（自动修复机制）"""
    try:
        output, code = run_iptables(["iptables", "-L", TRAFFIC_CHAIN, "-n"])
        if code != 0 or "No chain" in output:
            print("[HealthCheck] iptables链不存在，正在重建...")
            setup_iptables()
            return True
        output, code = run_iptables(["iptables", "-L", "FORWARD", "-n", "--line-numbers"])
        if TRAFFIC_CHAIN not in output:
            print("[HealthCheck] FORWARD链未引用NETPULSE，正在修复...")
            run_iptables(["iptables", "-I", "FORWARD", "1", "-j", TRAFFIC_CHAIN])
        output, code = run_iptables(["iptables", "-L", "INPUT", "-n", "--line-numbers"])
        if TRAFFIC_CHAIN not in output:
            print("[HealthCheck] INPUT链未引用NETPULSE，正在修复...")
            run_iptables(["iptables", "-I", "INPUT", "1", "-j", TRAFFIC_CHAIN])
        output, code = run_iptables(["iptables", "-L", "OUTPUT", "-n", "--line-numbers"])
        if TRAFFIC_CHAIN not in output:
            print("[HealthCheck] OUTPUT链未引用NETPULSE，正在修复...")
            run_iptables(["iptables", "-I", "OUTPUT", "1", "-j", TRAFFIC_CHAIN])
        return True
    except Exception as e:
        print(f"[HealthCheck] iptables链检查失败: {e}")
        return False


def add_device_rule(ip):
    """为设备添加流量计数规则（幂等：先检查是否已存在）"""
    if not ip or ip == "0.0.0.0":
        return
    output, code = run_iptables(["iptables", "-C", TRAFFIC_CHAIN, "-s", ip])
    if code != 0:
        run_iptables(["iptables", "-A", TRAFFIC_CHAIN, "-s", ip])
    output, code = run_iptables(["iptables", "-C", TRAFFIC_CHAIN, "-d", ip])
    if code != 0:
        run_iptables(["iptables", "-A", TRAFFIC_CHAIN, "-d", ip])


def ensure_all_device_rules():
    """确保所有在线设备都有iptables规则（自动修复机制）"""
    try:
        devices = get_all_devices()
        online_ips = [d['ip'] for d in devices if d['is_online'] and d['ip']]
        current_rules = read_iptables_counters()
        missing = [ip for ip in online_ips if ip not in current_rules]
        if missing:
            print(f"[HealthCheck] 发现 {len(missing)} 台在线设备缺少iptables规则，正在补全: {missing}")
            for ip in missing:
                add_device_rule(ip)
            return len(missing)
        return 0
    except Exception as e:
        print(f"[HealthCheck] 设备规则检查失败: {e}")
        return -1


def remove_device_rule(ip):
    """移除设备规则"""
    if not ip:
        return
    run_iptables(["iptables", "-D", TRAFFIC_CHAIN, "-s", ip])
    run_iptables(["iptables", "-D", TRAFFIC_CHAIN, "-d", ip])


def read_iptables_counters():
    """读取iptables计数器，返回 {ip: {upload: bytes, download: bytes}}"""
    counters = {}
    try:
        output, code = run_iptables(["iptables", "-L", TRAFFIC_CHAIN, "-n", "-v", "-x"])
        if code != 0:
            return counters
        for line in output.strip().split('\n'):
            parts = line.split()
            if len(parts) < 7 or parts[0] == 'pkts' or parts[0] == 'Chain':
                continue
            try:
                packets = int(parts[0])
                bytes_count = int(parts[1])
                source = parts[6] if len(parts) > 6 else ""
                dest = parts[7] if len(parts) > 7 else ""
                ip = source if source and source != "0.0.0.0/0" else dest
                if ip and '/' in ip:
                    ip = ip.split('/')[0]
                if ip and ip != "0.0.0.0":
                    if ip not in counters:
                        counters[ip] = {"upload": 0, "download": 0}
                    if source and source != "0.0.0.0/0":
                        counters[ip]["upload"] += bytes_count
                    elif dest and dest != "0.0.0.0/0":
                        counters[ip]["download"] += bytes_count
            except (ValueError, IndexError):
                continue
    except Exception as e:
        print(f"读取iptables计数器失败: {e}")
    return counters


class TrafficMonitor(threading.Thread):
    """流量监控线程"""
    def __init__(self, interval=TRAFFIC_INTERVAL):
        super().__init__(daemon=True)
        self.interval = interval
        self.running = True
        self.last_counters = {}
        self.last_read_time = time.time()
        self.monitored_ips = set()
        self.rate_history = {}
        self.max_history = 5
        self.period_check_counter = 0
        self.health_check_counter = 0

    def run(self):
        print(f"[Traffic] 流量监控线程启动，间隔{self.interval}秒")
        setup_iptables()
        while self.running:
            try:
                self.monitor_once()
            except Exception as e:
                print(f"[Traffic] 监控异常: {e}")
            time.sleep(self.interval)

    def monitor_once(self):
        """执行一次流量监控"""
        devices = get_all_devices()
        online_ips = {d['ip']: d['mac'] for d in devices if d['is_online'] and d['ip']}

        for ip in online_ips:
            if ip not in self.monitored_ips:
                add_device_rule(ip)
                self.monitored_ips.add(ip)
                print(f"[Traffic] 添加设备监控: {ip}")

        # 健康检查：每10次循环检查一次iptables完整性
        self.health_check_counter += 1
        if self.health_check_counter >= 10:
            self.health_check_counter = 0
            try:
                ensure_iptables_chain()
                missing = ensure_all_device_rules()
                if missing > 0:
                    print(f"[HealthCheck] 自动补全了 {missing} 台设备的iptables规则")
                    current_rules = read_iptables_counters()
                    self.monitored_ips = set(current_rules.keys())
            except Exception as e:
                print(f"[HealthCheck] 健康检查异常: {e}")

        current_counters = read_iptables_counters()
        now = time.time()
        time_delta = now - self.last_read_time

        if time_delta > 0:
            for ip, mac in online_ips.items():
                if ip in current_counters and ip in self.last_counters:
                    upload_delta = max(0, current_counters[ip]["upload"] - self.last_counters[ip]["upload"])
                    download_delta = max(0, current_counters[ip]["download"] - self.last_counters[ip]["download"])
                    if upload_delta > 0 or download_delta > 0:
                        update_traffic(mac, upload_delta, download_delta)
                    instant_upload_rate = upload_delta / time_delta / 1024
                    instant_download_rate = download_delta / time_delta / 1024
                    if mac not in self.rate_history:
                        self.rate_history[mac] = {'upload': [], 'download': []}
                    self.rate_history[mac]['upload'].append(instant_upload_rate)
                    self.rate_history[mac]['download'].append(instant_download_rate)
                    if len(self.rate_history[mac]['upload']) > self.max_history:
                        self.rate_history[mac]['upload'].pop(0)
                    if len(self.rate_history[mac]['download']) > self.max_history:
                        self.rate_history[mac]['download'].pop(0)
                    avg_upload = sum(self.rate_history[mac]['upload']) / len(self.rate_history[mac]['upload'])
                    avg_download = sum(self.rate_history[mac]['download']) / len(self.rate_history[mac]['download'])
                    update_current_rates(mac, avg_upload, avg_download)

        self.last_counters = current_counters
        self.last_read_time = now

        try:
            update_period_traffic()
        except Exception as e:
            pass

        self.period_check_counter += 1
        if self.period_check_counter >= 1200:
            self.period_check_counter = 0
            try:
                if check_and_reset_period():
                    print("[Traffic] 检测到新周期，已自动清零流量统计")
            except Exception as e:
                print(f"[Traffic] 周期检查异常: {e}")

    def stop(self):
        self.running = False
        run_iptables(["iptables", "-F", TRAFFIC_CHAIN])
        run_iptables(["iptables", "-D", "FORWARD", "-j", TRAFFIC_CHAIN])
        run_iptables(["iptables", "-X", TRAFFIC_CHAIN])
        print("[Traffic] 流量监控线程已停止，iptables规则已清理")
