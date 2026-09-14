"""
NetPulse - 设备管理模块
实现设备踢出网络（ARP欺骗+iptables DROP）和设备限速（tc HTB）
"""
import subprocess
import time
import threading
import struct
import socket
import os
from config import GATEWAY_IP, LOCAL_MAC, MANAGE_INTERFACE


def run_cmd(cmd, timeout=10):
    """执行shell命令"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.returncode
    except Exception as e:
        return str(e), -1


def run_sudo(cmd, timeout=10):
    """执行sudo命令"""
    full_cmd = f"echo orangepi | sudo -S {cmd}"
    return run_cmd(full_cmd, timeout)


# ============================================================
# ARP欺骗模块
# ============================================================

def send_arp_reply(target_ip, target_mac, spoof_ip, spoof_mac, interface=MANAGE_INTERFACE):
    """发送ARP响应包"""
    try:
        eth_dest = bytes.fromhex(target_mac.replace(':', ''))
        eth_src = bytes.fromhex(spoof_mac.replace(':', ''))
        eth_type = struct.pack('!H', 0x0806)
        arp_htype = struct.pack('!H', 0x0001)
        arp_ptype = struct.pack('!H', 0x0800)
        arp_hlen = struct.pack('!B', 6)
        arp_plen = struct.pack('!B', 4)
        arp_op = struct.pack('!H', 0x0002)
        arp_sender_mac = bytes.fromhex(spoof_mac.replace(':', ''))
        arp_sender_ip = socket.inet_aton(spoof_ip)
        arp_target_mac = bytes.fromhex(target_mac.replace(':', ''))
        arp_target_ip = socket.inet_aton(target_ip)
        packet = (eth_dest + eth_src + eth_type +
                  arp_htype + arp_ptype + arp_hlen + arp_plen + arp_op +
                  arp_sender_mac + arp_sender_ip + arp_target_mac + arp_target_ip)
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
        sock.bind((interface, 0))
        sock.send(packet)
        sock.close()
        return True
    except Exception as e:
        print(f"发送ARP包失败: {e}")
        return False


def get_mac_by_ip(ip):
    """通过IP获取MAC地址（从ARP表）"""
    out, _ = run_cmd(f"arp -n {ip} 2>/dev/null | grep {ip} | awk '{{print $3}}'")
    if out and ':' in out:
        return out.lower()
    run_cmd(f"ping -c 1 -W 1 {ip} > /dev/null 2>&1")
    time.sleep(0.5)
    out, _ = run_cmd(f"arp -n {ip} 2>/dev/null | grep {ip} | awk '{{print $3}}'")
    if out and ':' in out:
        return out.lower()
    return None


class ArpSpoofer:
    """ARP欺骗管理器"""
    def __init__(self):
        self.targets = {}
        self.lock = threading.Lock()
        self.running = False
        self.thread = None

    def add_target(self, ip, mac):
        with self.lock:
            self.targets[mac] = {'ip': ip, 'target_mac': mac, 'active': True}
        print(f"[ArpSpoofer] 添加欺骗目标: {ip} ({mac})")

    def remove_target(self, mac):
        with self.lock:
            if mac in self.targets:
                del self.targets[mac]
        print(f"[ArpSpoofer] 移除欺骗目标: {mac}")

    def get_targets(self):
        with self.lock:
            return list(self.targets.keys())

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._spoof_loop, daemon=True)
        self.thread.start()
        print("[ArpSpoofer] ARP欺骗线程已启动")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
        print("[ArpSpoofer] ARP欺骗线程已停止")

    def _spoof_loop(self):
        while self.running:
            try:
                with self.lock:
                    targets = list(self.targets.values())
                for target in targets:
                    if not target['active']:
                        continue
                    ip = target['ip']
                    mac = target['target_mac']
                    send_arp_reply(ip, mac, GATEWAY_IP, LOCAL_MAC, MANAGE_INTERFACE)
                    gateway_mac = get_mac_by_ip(GATEWAY_IP)
                    if gateway_mac:
                        send_arp_reply(GATEWAY_IP, gateway_mac, ip, LOCAL_MAC, MANAGE_INTERFACE)
                time.sleep(2)
            except Exception as e:
                print(f"[ArpSpoofer] 欺骗循环错误: {e}")
                time.sleep(2)


arp_spoofer = ArpSpoofer()


# ============================================================
# iptables 设备封禁模块
# ============================================================

BLOCK_CHAIN = "NETPULSE_BLOCK"


def init_block_chain():
    run_sudo(f"iptables -N {BLOCK_CHAIN} 2>/dev/null")
    run_sudo(f"iptables -C FORWARD -j {BLOCK_CHAIN} 2>/dev/null || iptables -A FORWARD -j {BLOCK_CHAIN}")
    print(f"[Block] 封禁链 {BLOCK_CHAIN} 已初始化")


def block_device(ip, mac):
    run_sudo(f"iptables -A {BLOCK_CHAIN} -s {ip} -j DROP")
    run_sudo(f"iptables -A {BLOCK_CHAIN} -d {ip} -j DROP")
    arp_spoofer.add_target(ip, mac)
    print(f"[Block] 设备已封禁: {ip} ({mac})")
    return True


def unblock_device(ip, mac):
    run_sudo(f"iptables -D {BLOCK_CHAIN} -s {ip} -j DROP 2>/dev/null")
    run_sudo(f"iptables -D {BLOCK_CHAIN} -d {ip} -j DROP 2>/dev/null")
    arp_spoofer.remove_target(mac)
    gateway_mac = get_mac_by_ip(GATEWAY_IP)
    if gateway_mac:
        send_arp_reply(ip, mac, GATEWAY_IP, gateway_mac, MANAGE_INTERFACE)
        send_arp_reply(GATEWAY_IP, gateway_mac, ip, mac, MANAGE_INTERFACE)
    print(f"[Block] 设备已解封: {ip} ({mac})")
    return True


def get_blocked_devices():
    out, _ = run_sudo(f"iptables -L {BLOCK_CHAIN} -n 2>/dev/null | grep DROP")
    blocked = set()
    for line in out.split('\n'):
        parts = line.split()
        if len(parts) >= 4 and parts[0] == 'DROP' and '/' in parts[3]:
            blocked.add(parts[3].split('/')[0])
    return list(blocked)


# ============================================================
# tc 限速模块
# ============================================================

LIMIT_IFACE = MANAGE_INTERFACE
LIMIT_ROOT_HANDLE = "1:"


def init_tc():
    run_sudo(f"tc qdisc del dev {LIMIT_IFACE} root 2>/dev/null")
    run_sudo(f"tc qdisc add dev {LIMIT_IFACE} root handle {LIMIT_ROOT_HANDLE} htb default 999")
    run_sudo(f"tc class add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} classid 1:999 htb rate 1000mbit ceil 1000mbit")
    print(f"[TC] tc qdisc已初始化 on {LIMIT_IFACE}")


def limit_device(ip, mac, upload_kbps, download_kbps):
    ip_parts = ip.split('.')
    class_id = int(ip_parts[3])
    if class_id < 2 or class_id > 254:
        class_id = 100 + (hash(ip) % 100)
    handle = f"1:{class_id}"
    run_sudo(f"tc class del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} classid {handle} 2>/dev/null")
    run_sudo(f"tc filter del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} prio {class_id} 2>/dev/null")
    total_rate = max(upload_kbps, download_kbps)
    total_ceil = total_rate
    run_sudo(f"tc class add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} classid {handle} "
             f"htb rate {total_rate}kbit ceil {total_ceil}kbit burst 15k")
    run_sudo(f"tc qdisc add dev {LIMIT_IFACE} parent {handle} handle {class_id * 10}: sfq perturb 10 2>/dev/null")
    run_sudo(f"tc filter add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} protocol ip prio {class_id} "
             f"u32 match ip src {ip}/32 flowid {handle}")
    run_sudo(f"tc filter add dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} protocol ip prio {class_id + 1000} "
             f"u32 match ip dst {ip}/32 flowid {handle}")
    arp_spoofer.add_target(ip, mac)
    print(f"[TC] 设备已限速: {ip} -> {total_rate} kbps (class {handle})")
    return True


def unlimit_device(ip, mac):
    ip_parts = ip.split('.')
    class_id = int(ip_parts[3])
    if class_id < 2 or class_id > 254:
        class_id = 100 + (hash(ip) % 100)
    handle = f"1:{class_id}"
    run_sudo(f"tc filter del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} prio {class_id} 2>/dev/null")
    run_sudo(f"tc filter del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} prio {class_id + 1000} 2>/dev/null")
    run_sudo(f"tc qdisc del dev {LIMIT_IFACE} parent {handle} 2>/dev/null")
    run_sudo(f"tc class del dev {LIMIT_IFACE} parent {LIMIT_ROOT_HANDLE} classid {handle} 2>/dev/null")
    blocked = get_blocked_devices()
    if ip not in blocked:
        arp_spoofer.remove_target(mac)
        gateway_mac = get_mac_by_ip(GATEWAY_IP)
        if gateway_mac:
            send_arp_reply(ip, mac, GATEWAY_IP, gateway_mac, MANAGE_INTERFACE)
            send_arp_reply(GATEWAY_IP, gateway_mac, ip, mac, MANAGE_INTERFACE)
    print(f"[TC] 设备已取消限速: {ip}")
    return True


def get_limited_devices():
    out, _ = run_sudo(f"tc class show dev {LIMIT_IFACE} 2>/dev/null")
    limited = []
    for line in out.split('\n'):
        if 'htb' in line and '1:' in line and '1:999' not in line:
            parts = line.split()
            for p in parts:
                if p.startswith('rate'):
                    rate = p.replace('rate', '')
                    limited.append({'class': parts[2], 'rate': rate})
    return limited


# ============================================================
# 系统初始化
# ============================================================

def init_device_manager():
    print("=" * 60)
    print("  NetPulse 设备管理系统初始化")
    print("=" * 60)
    run_sudo("sysctl -w net.ipv4.ip_forward=1")
    print("[Init] IP转发已开启")
    init_block_chain()
    init_tc()
    arp_spoofer.start()
    print("[Init] 设备管理系统初始化完成")
    print("=" * 60)


def cleanup_device_manager():
    print("[Cleanup] 停止设备管理系统...")
    if global_spoof_enabled:
        stop_global_spoof()
    arp_spoofer.stop()
    run_sudo(f"iptables -F {BLOCK_CHAIN} 2>/dev/null")
    run_sudo(f"iptables -D FORWARD -j {BLOCK_CHAIN} 2>/dev/null")
    run_sudo(f"iptables -X {BLOCK_CHAIN} 2>/dev/null")
    run_sudo(f"tc qdisc del dev {LIMIT_IFACE} root 2>/dev/null")
    run_sudo("sysctl -w net.ipv4.ip_forward=0")
    print("[Cleanup] 设备管理系统已清理")


# ============================================================
# 全局ARP欺骗模块 - 让所有设备流量自动经过Orange Pi
# ============================================================

global_spoof_enabled = False
global_spoof_thread = None
global_spoof_running = False
global_spoof_heartbeat = 0


def restore_arp_for_device(ip, mac):
    try:
        gateway_mac = get_mac_by_ip(GATEWAY_IP)
        if gateway_mac and mac and ip:
            send_arp_reply(ip, mac, GATEWAY_IP, gateway_mac, MANAGE_INTERFACE)
            send_arp_reply(GATEWAY_IP, gateway_mac, ip, mac, MANAGE_INTERFACE)
    except Exception as e:
        print(f"恢复ARP失败: {e}")


def _global_spoof_loop():
    """全局欺骗线程 - 定期扫描在线设备并发送ARP欺骗包"""
    global global_spoof_running, global_spoof_heartbeat
    print("[GlobalSpoof] 全局ARP欺骗线程已启动")
    while global_spoof_running:
        try:
            global_spoof_heartbeat = time.time()
            try:
                from database import get_all_devices
                devices = get_all_devices()
                online_devices = [d for d in devices if d.get('is_online') == 1
                                  and d.get('ip') and d.get('mac')
                                  and d['ip'].startswith('192.168.1.')
                                  and d['ip'] != GATEWAY_IP
                                  and d['ip'] != '192.168.1.10']
            except Exception as e:
                print(f"[GlobalSpoof] 获取设备列表失败: {e}")
                online_devices = []
            gateway_mac = get_mac_by_ip(GATEWAY_IP)
            for dev in online_devices:
                ip = dev['ip']
                mac = dev['mac']
                send_arp_reply(ip, mac, GATEWAY_IP, LOCAL_MAC, MANAGE_INTERFACE)
                if gateway_mac:
                    send_arp_reply(GATEWAY_IP, gateway_mac, ip, LOCAL_MAC, MANAGE_INTERFACE)
            time.sleep(5)
        except Exception as e:
            print(f"[GlobalSpoof] 欺骗循环错误: {e}")
            time.sleep(5)
    print("[GlobalSpoof] 全局ARP欺骗线程已停止")


def ensure_nat_and_forwarding():
    """确保IP转发和NAT规则正常（自动修复机制）"""
    try:
        result = subprocess.run(["cat", "/proc/sys/net/ipv4/ip_forward"],
                                capture_output=True, text=True, timeout=5)
        if result.stdout.strip() != "1":
            print("[HealthCheck] IP转发未开启，正在开启...")
            run_sudo("sysctl -w net.ipv4.ip_forward=1")
        output, code = run_sudo("iptables -t nat -C POSTROUTING -s 192.168.1.0/24 -o eth0 -j MASQUERADE 2>/dev/null")
        if code != 0:
            print("[HealthCheck] eth0 NAT规则缺失，正在添加...")
            run_sudo("iptables -t nat -A POSTROUTING -s 192.168.1.0/24 -o eth0 -j MASQUERADE")
        output, code = run_sudo("iptables -t nat -C POSTROUTING -s 192.168.1.0/24 -o wlan0 -j MASQUERADE 2>/dev/null")
        if code != 0:
            print("[HealthCheck] wlan0 NAT规则缺失，正在添加...")
            run_sudo("iptables -t nat -A POSTROUTING -s 192.168.1.0/24 -o wlan0 -j MASQUERADE")
        return True
    except Exception as e:
        print(f"[HealthCheck] NAT检查失败: {e}")
        return False


def ensure_global_spoof_running():
    """检查全局ARP欺骗线程是否存活，死亡则自动重启（自动修复机制）"""
    global global_spoof_enabled, global_spoof_thread, global_spoof_running, global_spoof_heartbeat
    if not global_spoof_enabled:
        return False
    thread_alive = global_spoof_thread is not None and global_spoof_thread.is_alive()
    heartbeat_ok = (time.time() - global_spoof_heartbeat) < 30 if global_spoof_heartbeat > 0 else False
    if thread_alive and heartbeat_ok:
        return True
    print(f"[HealthCheck] 检测到ARP欺骗异常！线程存活={thread_alive}, 心跳正常={heartbeat_ok}，正在自动重启...")
    global_spoof_running = False
    if global_spoof_thread:
        global_spoof_thread.join(timeout=3)
    global_spoof_thread = None
    global_spoof_enabled = False
    time.sleep(1)
    success = start_global_spoof()
    if success:
        print("[HealthCheck] ARP欺骗线程已自动重启成功")
    else:
        print("[HealthCheck] ARP欺骗线程重启失败")
    return success


def start_global_spoof():
    """开启全局ARP欺骗模式"""
    global global_spoof_enabled, global_spoof_thread, global_spoof_running, global_spoof_heartbeat
    if global_spoof_enabled:
        print("[GlobalSpoof] 全局欺骗已在运行中")
        return True
    ensure_nat_and_forwarding()
    global_spoof_running = True
    global_spoof_heartbeat = time.time()
    global_spoof_thread = threading.Thread(target=_global_spoof_loop, daemon=True)
    global_spoof_thread.start()
    global_spoof_enabled = True
    print("[GlobalSpoof] 全局ARP欺骗模式已开启")
    print("[GlobalSpoof] 所有设备流量将自动经过Orange Pi")
    return True


def stop_global_spoof():
    """关闭全局ARP欺骗模式"""
    global global_spoof_enabled, global_spoof_running
    if not global_spoof_enabled:
        return True
    print("[GlobalSpoof] 正在关闭全局ARP欺骗...")
    global_spoof_running = False
    if global_spoof_thread:
        global_spoof_thread.join(timeout=5)
    try:
        from database import get_all_devices
        devices = get_all_devices()
        gateway_mac = get_mac_by_ip(GATEWAY_IP)
        for dev in devices:
            ip = dev.get('ip')
            mac = dev.get('mac')
            if ip and mac and ip.startswith('192.168.1.') and ip != GATEWAY_IP and ip != '192.168.1.10':
                for _ in range(3):
                    if gateway_mac:
                        send_arp_reply(ip, mac, GATEWAY_IP, gateway_mac, MANAGE_INTERFACE)
                        send_arp_reply(GATEWAY_IP, gateway_mac, ip, mac, MANAGE_INTERFACE)
                    time.sleep(0.2)
    except Exception as e:
        print(f"[GlobalSpoof] 恢复ARP时出错: {e}")
    global_spoof_enabled = False
    print("[GlobalSpoof] 全局ARP欺骗已关闭，设备网络已恢复")
    return True


def get_global_spoof_status():
    """获取全局欺骗状态"""
    thread_alive = global_spoof_thread is not None and global_spoof_thread.is_alive()
    heartbeat_age = time.time() - global_spoof_heartbeat if global_spoof_heartbeat > 0 else -1
    return {
        "enabled": global_spoof_enabled,
        "running": global_spoof_running,
        "thread_alive": thread_alive,
        "heartbeat_age": round(heartbeat_age, 1),
        "description": "全局流量监控模式" if global_spoof_enabled else "未开启"
    }
