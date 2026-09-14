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
    record_connection_event
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
    # 创建自定义链
    run_iptables(["iptables", "-N", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-F", TRAFFIC_CHAIN])

    # 关键：将FORWARD链流量引导到自定义链，必须用-I插入到最前面！
    # 否则ACCEPT RELATED,ESTABLISHED会先接受流量，导致计数丢失
    run_iptables(["iptables", "-D", "FORWARD", "-j", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-I", "FORWARD", "1", "-j", TRAFFIC_CHAIN])

    # 也监控INPUT和OUTPUT（本机流量）
    run_iptables(["iptables", "-D", "INPUT", "-j", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-I", "INPUT", "1", "-j", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-D", "OUTPUT", "-j", TRAFFIC_CHAIN])
    run_iptables(["iptables", "-I", "OUTPUT", "1", "-j", TRAFFIC_CHAIN])

    print("[Traffic] iptables规则链初始化完成（计数规则已插入到最前面）")


def add_device_rule(ip):
    """为设备添加流量计数规则"""
    if not ip or ip == "0.0.0.0":
        return
    # 源地址（上行/上传）
    run_iptables(["iptables", "-A", TRAFFIC_CHAIN, "-s", ip])
    # 目标地址（下行/下载）
    run_iptables(["iptables", "-A", TRAFFIC_CHAIN, "-d", ip])


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
                # iptables -L -n -v -x 输出格式:
                # pkts bytes target prot opt in out source destination
                # 当target为空时，parts[2]是prot，所以source=parts[6], dest=parts[7]
                source = parts[6] if len(parts) > 6 else ""
                dest = parts[7] if len(parts) > 7 else ""

                ip = source if source and source != "0.0.0.0/0" else dest
                if ip and '/' in ip:
                    ip = ip.split('/')[0]

                if ip and ip != "0.0.0.0":
                    if ip not in counters:
                        counters[ip] = {"upload": 0, "download": 0}
                    # 判断是上传还是下载
                    # 源IP不是0.0.0.0/0 -> 上传（设备发出的流量）
                    # 目标IP不是0.0.0.0/0 -> 下载（设备接收的流量）
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
        # 滑动窗口：保存最近5次的速率样本，用于计算平均速率
        self.rate_history = {}  # {mac: [upload_rates], [download_rates]}
        self.max_history = 5

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
        # 获取当前在线设备
        devices = get_all_devices()
        online_ips = {d['ip']: d['mac'] for d in devices if d['is_online'] and d['ip']}

        # 为新设备添加规则
        for ip in online_ips:
            if ip not in self.monitored_ips:
                add_device_rule(ip)
                self.monitored_ips.add(ip)
                print(f"[Traffic] 添加设备监控: {ip}")

        # 移除离线设备规则（延迟移除，保留数据）
        # 暂时不移除，避免规则频繁增删

        # 读取计数器
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

                    # 计算瞬时速率（字节/秒 -> KB/s）
                    instant_upload_rate = upload_delta / time_delta / 1024
                    instant_download_rate = download_delta / time_delta / 1024

                    # 滑动窗口平均速率（更平滑）
                    if mac not in self.rate_history:
                        self.rate_history[mac] = {'upload': [], 'download': []}

                    self.rate_history[mac]['upload'].append(instant_upload_rate)
                    self.rate_history[mac]['download'].append(instant_download_rate)

                    # 保持窗口大小
                    if len(self.rate_history[mac]['upload']) > self.max_history:
                        self.rate_history[mac]['upload'].pop(0)
                    if len(self.rate_history[mac]['download']) > self.max_history:
                        self.rate_history[mac]['download'].pop(0)

                    # 计算平均速率
                    avg_upload = sum(self.rate_history[mac]['upload']) / len(self.rate_history[mac]['upload'])
                    avg_download = sum(self.rate_history[mac]['download']) / len(self.rate_history[mac]['download'])

                    update_current_rates(mac, avg_upload, avg_download)

        self.last_counters = current_counters
        self.last_read_time = now

    def stop(self):
        self.running = False
        # 清理iptables规则
        run_iptables(["iptables", "-F", TRAFFIC_CHAIN])
        run_iptables(["iptables", "-D", "FORWARD", "-j", TRAFFIC_CHAIN])
        run_iptables(["iptables", "-X", TRAFFIC_CHAIN])
        print("[Traffic] 流量监控线程已停止，iptables规则已清理")
