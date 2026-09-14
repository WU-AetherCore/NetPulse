"""
NetPulse - 网络设备管理系统
Flask主程序
"""
import os
import sys
import time
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory

# 确保能导入本地模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WEB_HOST, WEB_PORT, ADMIN_PASSWORD
from database import (
    init_db, get_all_devices, get_device_by_mac,
    get_hourly_traffic, get_daily_traffic, get_connection_events,
    update_device_name, get_summary, cleanup_old_data,
    set_device_blocked, set_device_limit, get_managed_devices,
    set_device_wifi_band, get_band_stats, get_traffic_ranking
)
from scanner import Scanner
from traffic import TrafficMonitor
from device_manager import (
    init_device_manager, cleanup_device_manager,
    block_device, unblock_device, limit_device, unlimit_device,
    get_blocked_devices, get_limited_devices,
    start_global_spoof, stop_global_spoof, get_global_spoof_status
)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# 全局变量
scanner = None
traffic_monitor = None


def format_bytes(bytes_val):
    """格式化字节数"""
    if bytes_val is None:
        return "0 B"
    bytes_val = float(bytes_val)
    if bytes_val < 1024:
        return f"{bytes_val:.0f} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val/1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val/(1024*1024):.1f} MB"
    else:
        return f"{bytes_val/(1024*1024*1024):.2f} GB"


def format_rate(rate_kbps):
    """格式化速率"""
    if rate_kbps is None:
        return "0 KB/s"
    if rate_kbps < 1024:
        return f"{rate_kbps:.1f} KB/s"
    else:
        return f"{rate_kbps/1024:.2f} MB/s"


@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/api/summary')
def api_summary():
    """系统概览"""
    summary = get_summary()
    summary['formatted'] = {
        'total_upload': format_bytes(summary['total_upload']),
        'total_download': format_bytes(summary['total_download']),
        'avg_upload_rate': format_rate(summary['avg_upload_rate']),
        'avg_download_rate': format_rate(summary['avg_download_rate']),
    }
    summary['timestamp'] = int(time.time())
    return jsonify(summary)


@app.route('/api/devices')
def api_devices():
    """获取所有设备列表"""
    devices = get_all_devices()
    for dev in devices:
        dev['total_upload_str'] = format_bytes(dev.get('total_upload', 0))
        dev['total_download_str'] = format_bytes(dev.get('total_download', 0))
        dev['upload_rate_str'] = format_rate(dev.get('current_upload_rate', 0))
        dev['download_rate_str'] = format_rate(dev.get('current_download_rate', 0))
        dev['last_seen_str'] = datetime.fromtimestamp(dev['last_seen']).strftime('%Y-%m-%d %H:%M:%S') if dev.get('last_seen') else '未知'
        dev['first_seen_str'] = datetime.fromtimestamp(dev['first_seen']).strftime('%Y-%m-%d %H:%M:%S') if dev.get('first_seen') else '未知'
        # 在线时长
        if dev.get('is_online') and dev.get('last_seen'):
            online_seconds = int(time.time()) - dev['last_seen']
            dev['online_duration'] = f"{online_seconds//3600}小时{(online_seconds%3600)//60}分"
        else:
            dev['online_duration'] = '离线'
    return jsonify(devices)


@app.route('/api/device/<mac>')
def api_device_detail(mac):
    """获取设备详情"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    device['total_upload_str'] = format_bytes(device.get('total_upload', 0))
    device['total_download_str'] = format_bytes(device.get('total_download', 0))
    device['upload_rate_str'] = format_rate(device.get('current_upload_rate', 0))
    device['download_rate_str'] = format_rate(device.get('current_download_rate', 0))

    # 获取流量数据
    hourly = get_hourly_traffic(mac, 24)
    daily = get_daily_traffic(mac, 30)

    # 获取连接事件
    events = get_connection_events(limit=50, mac=mac)
    for evt in events:
        evt['time_str'] = datetime.fromtimestamp(evt['timestamp']).strftime('%Y-%m-%d %H:%M:%S')

    return jsonify({
        "device": device,
        "hourly_traffic": hourly,
        "daily_traffic": daily,
        "events": events
    })


@app.route('/api/device/<mac>/rename', methods=['POST'])
def api_rename_device(mac):
    """重命名设备"""
    data = request.get_json()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"error": "名称不能为空"}), 400
    update_device_name(mac, name)
    return jsonify({"success": True})


@app.route('/api/traffic/hourly/<mac>')
def api_hourly_traffic(mac):
    """获取小时流量"""
    hours = request.args.get('hours', 24, type=int)
    data = get_hourly_traffic(mac, hours)
    return jsonify(data)


@app.route('/api/traffic/daily/<mac>')
def api_daily_traffic(mac):
    """获取天流量"""
    days = request.args.get('days', 30, type=int)
    data = get_daily_traffic(mac, days)
    return jsonify(data)


@app.route('/api/events')
def api_events():
    """获取连接事件"""
    limit = request.args.get('limit', 100, type=int)
    mac = request.args.get('mac', None)
    events = get_connection_events(limit=limit, mac=mac)
    for evt in events:
        evt['time_str'] = datetime.fromtimestamp(evt['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
    return jsonify(events)


@app.route('/api/scan', methods=['POST'])
def api_scan_now():
    """立即扫描"""
    if scanner:
        from scanner import scan_devices
        devices = scan_devices()
        return jsonify({"success": True, "devices_found": len(devices)})
    return jsonify({"error": "扫描器未启动"}), 500


@app.route('/api/cleanup', methods=['POST'])
def api_cleanup():
    """清理过期数据"""
    cleanup_old_data()
    return jsonify({"success": True})


# ============================================================
# 设备管理 API
# ============================================================

@app.route('/api/device/<mac>/block', methods=['POST'])
def api_block_device(mac):
    """封禁设备（踢出网络）"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    ip = device.get('ip', '')
    if not ip:
        return jsonify({"error": "设备IP未知，无法封禁"}), 400

    try:
        block_device(ip, mac)
        set_device_blocked(mac, True)
        # 记录事件
        from database import record_connection_event
        record_connection_event(mac, ip, 'blocked', '设备被管理员封禁，已踢出网络')
        return jsonify({"success": True, "message": f"设备 {ip} 已被封禁"})
    except Exception as e:
        return jsonify({"error": f"封禁失败: {str(e)}"}), 500


@app.route('/api/device/<mac>/unblock', methods=['POST'])
def api_unblock_device(mac):
    """解除设备封禁"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    ip = device.get('ip', '')
    try:
        unblock_device(ip, mac)
        set_device_blocked(mac, False)
        from database import record_connection_event
        record_connection_event(mac, ip, 'unblocked', '设备封禁已解除')
        return jsonify({"success": True, "message": f"设备 {ip} 已解除封禁"})
    except Exception as e:
        return jsonify({"error": f"解除封禁失败: {str(e)}"}), 500


@app.route('/api/device/<mac>/limit', methods=['POST'])
def api_limit_device(mac):
    """限速设备"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    data = request.get_json()
    upload_kbps = data.get('upload_kbps', 0)
    download_kbps = data.get('download_kbps', 0)

    if upload_kbps <= 0 and download_kbps <= 0:
        return jsonify({"error": "限速值必须大于0"}), 400

    ip = device.get('ip', '')
    if not ip:
        return jsonify({"error": "设备IP未知，无法限速"}), 400

    try:
        limit_device(ip, mac, upload_kbps, download_kbps)
        set_device_limit(mac, upload_kbps, download_kbps)
        from database import record_connection_event
        record_connection_event(mac, ip, 'limited',
                                f'设备已限速: 上传{upload_kbps}kbps / 下载{download_kbps}kbps')
        return jsonify({"success": True, "message": f"设备 {ip} 已限速"})
    except Exception as e:
        return jsonify({"error": f"限速失败: {str(e)}"}), 500


@app.route('/api/device/<mac>/unlimit', methods=['POST'])
def api_unlimit_device(mac):
    """取消设备限速"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    ip = device.get('ip', '')
    try:
        unlimit_device(ip, mac)
        set_device_limit(mac, 0, 0)
        from database import record_connection_event
        record_connection_event(mac, ip, 'unlimited', '设备限速已取消')
        return jsonify({"success": True, "message": f"设备 {ip} 已取消限速"})
    except Exception as e:
        return jsonify({"error": f"取消限速失败: {str(e)}"}), 500


@app.route('/api/managed')
def api_managed_devices():
    """获取被管理的设备列表（封禁/限速）"""
    devices = get_managed_devices()
    blocked = get_blocked_devices()
    limited = get_limited_devices()
    return jsonify({
        "managed_devices": devices,
        "blocked_ips": blocked,
        "limited_classes": limited
    })


# ============================================================
# WiFi频段管理 API
# ============================================================

@app.route('/api/device/<mac>/band', methods=['POST'])
def api_set_device_band(mac):
    """设置设备WiFi频段（2.4g/5g/wired/unknown）"""
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404

    data = request.get_json()
    band = data.get('band', 'unknown').strip().lower()

    valid_bands = ['2.4g', '5g', 'wired', 'unknown']
    if band not in valid_bands:
        return jsonify({"error": f"无效的频段，可选: {', '.join(valid_bands)}"}), 400

    set_device_wifi_band(mac, band)
    band_names = {'2.4g': '2.4GHz', '5g': '5GHz', 'wired': '有线连接', 'unknown': '未知'}
    return jsonify({"success": True, "message": f"设备频段已设置为 {band_names.get(band, band)}"})


@app.route('/api/band-stats')
def api_band_stats():
    """获取各频段设备统计"""
    stats = get_band_stats()
    # 确保所有频段都有数据
    result = {
        '2.4g': stats.get('2.4g', {'total': 0, 'online': 0}),
        '5g': stats.get('5g', {'total': 0, 'online': 0}),
        'wired': stats.get('wired', {'total': 0, 'online': 0}),
        'unknown': stats.get('unknown', {'total': 0, 'online': 0}),
    }
    return jsonify(result)


@app.route('/api/traffic-ranking')
def api_traffic_ranking():
    """获取设备流量排行榜
    参数: days=1/7/30, sort=total/download/upload
    """
    days = request.args.get('days', 7, type=int)
    sort = request.args.get('sort', 'total')
    
    if days not in [1, 7, 30]:
        days = 7
    
    ranking = get_traffic_ranking(days=days, limit=50)
    
    # 格式化数据
    for item in ranking:
        item['total_upload_str'] = format_bytes(item.get('total_upload', 0))
        item['total_download_str'] = format_bytes(item.get('total_download', 0))
        item['total_str'] = format_bytes(item.get('total_upload', 0) + item.get('total_download', 0))
    
    # 按指定方式排序
    if sort == 'download':
        ranking.sort(key=lambda x: x['total_download'], reverse=True)
    elif sort == 'upload':
        ranking.sort(key=lambda x: x['total_upload'], reverse=True)
    else:
        ranking.sort(key=lambda x: x['total_upload'] + x['total_download'], reverse=True)
    
    return jsonify({
        'days': days,
        'sort': sort,
        'ranking': ranking
    })


# ============================================================
# 全局流量监控模式（全局ARP欺骗）
# ============================================================

@app.route('/api/global-spoof/status')
def api_global_spoof_status():
    """获取全局欺骗状态"""
    return jsonify(get_global_spoof_status())


@app.route('/api/global-spoof/enable', methods=['POST'])
def api_global_spoof_enable():
    """开启全局流量监控模式"""
    try:
        success = start_global_spoof()
        if success:
            return jsonify({
                "success": True,
                "message": "全局流量监控模式已开启，所有设备流量将经过Orange Pi",
                "status": get_global_spoof_status()
            })
        else:
            return jsonify({"error": "开启失败"}), 500
    except Exception as e:
        return jsonify({"error": f"开启失败: {str(e)}"}), 500


@app.route('/api/global-spoof/disable', methods=['POST'])
def api_global_spoof_disable():
    """关闭全局流量监控模式"""
    try:
        success = stop_global_spoof()
        if success:
            return jsonify({
                "success": True,
                "message": "全局流量监控模式已关闭，设备网络已恢复",
                "status": get_global_spoof_status()
            })
        else:
            return jsonify({"error": "关闭失败"}), 500
    except Exception as e:
        return jsonify({"error": f"关闭失败: {str(e)}"}), 500


@app.route('/api/system')
def api_system():
    """系统信息"""
    import platform
    return jsonify({
        "name": "NetPulse",
        "version": "1.0.0",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "uptime": int(time.time() - app_start_time),
        "scanner_running": scanner is not None and scanner.is_alive(),
        "traffic_monitor_running": traffic_monitor is not None and traffic_monitor.is_alive(),
    })


app_start_time = time.time()


def main():
    """主函数"""
    global scanner, traffic_monitor

    print("=" * 60)
    print("  NetPulse - 网络设备管理系统")
    print("=" * 60)

    # 初始化数据库
    print("[Init] 初始化数据库...")
    init_db()

    # 启动扫描线程
    print("[Init] 启动设备扫描线程...")
    scanner = Scanner(interval=30)
    scanner.start()

    # 启动流量监控线程
    print("[Init] 启动流量监控线程...")
    traffic_monitor = TrafficMonitor(interval=10)
    traffic_monitor.start()

    # 初始化设备管理系统
    print("[Init] 初始化设备管理系统...")
    init_device_manager()

    # 自动开启全局流量监控（默认开启）
    print("[Init] 自动开启全局流量监控...")
    start_global_spoof()

    # 启动Web服务
    print(f"[Init] Web服务启动: http://{WEB_HOST}:{WEB_PORT}")
    print("=" * 60)

    try:
        app.run(host=WEB_HOST, port=WEB_PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n[Shutdown] 正在停止服务...")
        if scanner:
            scanner.stop()
        if traffic_monitor:
            traffic_monitor.stop()
        cleanup_device_manager()
        print("[Shutdown] 服务已停止")


if __name__ == '__main__':
    main()
