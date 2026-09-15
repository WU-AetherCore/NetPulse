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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import WEB_HOST, WEB_PORT, ADMIN_PASSWORD
from database import (
    init_db, get_all_devices, get_device_by_mac,
    get_hourly_traffic, get_daily_traffic, get_connection_events,
    update_device_name, get_summary, cleanup_old_data,
    get_period_summary, get_period_settings, set_period_settings, check_and_reset_period, update_period_traffic,
    set_device_blocked, set_device_limit, get_managed_devices,
    set_device_wifi_band, get_band_stats, get_traffic_ranking
)
from scanner import Scanner
from traffic import TrafficMonitor
from device_manager import (
    init_device_manager, cleanup_device_manager,
    block_device, unblock_device, limit_device, unlimit_device,
    get_blocked_devices, get_limited_devices,
    start_global_spoof, stop_global_spoof, get_global_spoof_status,
    ensure_global_spoof_running, ensure_nat_and_forwarding
)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

scanner = None
traffic_monitor = None

# 缓存机制
_browsing_cache = {'data': None, 'time': 0, 'limit': 0}
_adguard_stats_cache = {'data': None, 'time': 0}
CACHE_TTL = 5


def format_bytes(bytes_val):
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
    if rate_kbps is None:
        return "0 KB/s"
    if rate_kbps < 1024:
        return f"{rate_kbps:.1f} KB/s"
    else:
        return f"{rate_kbps/1024:.2f} MB/s"


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/summary')
def api_summary():
    summary = get_summary()
    summary['formatted'] = {
        'total_upload': format_bytes(summary['total_upload']),
        'total_download': format_bytes(summary['total_download']),
        'avg_upload_rate': format_rate(summary['avg_upload_rate']),
        'avg_download_rate': format_rate(summary['avg_download_rate']),
    }
    summary['timestamp'] = int(time.time())
    return jsonify(summary)


@app.route('/api/period-summary')
def api_period_summary():
    check_and_reset_period()
    update_period_traffic()
    period_data = get_period_summary()
    for p in period_data['history']:
        p['total_upload_str'] = format_bytes(p.get('total_upload', 0))
        p['total_download_str'] = format_bytes(p.get('total_download', 0))
        p['total_str'] = format_bytes(p.get('total_upload', 0) + p.get('total_download', 0))
        from datetime import datetime
        if p.get('start_time'):
            p['start_time_str'] = datetime.fromtimestamp(p['start_time']).strftime('%Y-%m-%d')
        if p.get('end_time'):
            p['end_time_str'] = datetime.fromtimestamp(p['end_time']).strftime('%Y-%m-%d')
    period_data['current']['total_upload_str'] = format_bytes(period_data['current']['total_upload'])
    period_data['current']['total_download_str'] = format_bytes(period_data['current']['total_download'])
    period_data['current']['total_str'] = format_bytes(period_data['current']['total'])
    from datetime import datetime
    period_data['current']['start_time_str'] = datetime.fromtimestamp(period_data['current']['start_time']).strftime('%Y-%m-%d')
    return jsonify(period_data)


@app.route('/api/period-settings', methods=['GET', 'POST'])
def api_period_settings():
    if request.method == 'POST':
        data = request.get_json() or {}
        period_type = data.get('period_type', 'monthly')
        custom_days = int(data.get('custom_days', 30))
        auto_reset = data.get('auto_reset', True)
        settings = set_period_settings(period_type, custom_days, auto_reset)
        return jsonify({'success': True, 'settings': settings})
    else:
        return jsonify(get_period_settings())


@app.route('/api/period-reset', methods=['POST'])
def api_period_reset():
    settings = get_period_settings()
    result = set_period_settings(settings['period_type'], settings['custom_days'])
    return jsonify({'success': True, 'message': '周期已重置，流量已清零', 'current': result})


@app.route('/api/devices')
def api_devices():
    devices = get_all_devices()
    for dev in devices:
        dev['total_upload_str'] = format_bytes(dev.get('total_upload', 0))
        dev['total_download_str'] = format_bytes(dev.get('total_download', 0))
        dev['upload_rate_str'] = format_rate(dev.get('current_upload_rate', 0))
        dev['download_rate_str'] = format_rate(dev.get('current_download_rate', 0))
        dev['last_seen_str'] = datetime.fromtimestamp(dev['last_seen']).strftime('%Y-%m-%d %H:%M:%S') if dev.get('last_seen') else '未知'
        dev['first_seen_str'] = datetime.fromtimestamp(dev['first_seen']).strftime('%Y-%m-%d %H:%M:%S') if dev.get('first_seen') else '未知'
        if dev.get('is_online') and dev.get('last_seen'):
            online_seconds = int(time.time()) - dev['last_seen']
            dev['online_duration'] = f"{online_seconds//3600}小时{(online_seconds%3600)//60}分"
        else:
            dev['online_duration'] = '离线'
    return jsonify(devices)


@app.route('/api/device/<mac>')
def api_device_detail(mac):
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404
    device['total_upload_str'] = format_bytes(device.get('total_upload', 0))
    device['total_download_str'] = format_bytes(device.get('total_download', 0))
    device['upload_rate_str'] = format_rate(device.get('current_upload_rate', 0))
    device['download_rate_str'] = format_rate(device.get('current_download_rate', 0))
    hourly = get_hourly_traffic(mac, 24)
    daily = get_daily_traffic(mac, 30)
    events = get_connection_events(limit=50, mac=mac)
    for evt in events:
        evt['time_str'] = datetime.fromtimestamp(evt['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
    return jsonify({"device": device, "hourly_traffic": hourly, "daily_traffic": daily, "events": events})


@app.route('/api/device/<mac>/rename', methods=['POST'])
def api_rename_device(mac):
    data = request.get_json()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"error": "名称不能为空"}), 400
    update_device_name(mac, name)
    return jsonify({"success": True})


@app.route('/api/traffic/hourly/<mac>')
def api_hourly_traffic(mac):
    hours = request.args.get('hours', 24, type=int)
    data = get_hourly_traffic(mac, hours)
    return jsonify(data)


@app.route('/api/traffic/daily/<mac>')
def api_daily_traffic(mac):
    days = request.args.get('days', 30, type=int)
    data = get_daily_traffic(mac, days)
    return jsonify(data)


@app.route('/api/events')
def api_events():
    limit = request.args.get('limit', 100, type=int)
    mac = request.args.get('mac', None)
    events = get_connection_events(limit=limit, mac=mac)
    for evt in events:
        evt['time_str'] = datetime.fromtimestamp(evt['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
    return jsonify(events)


@app.route('/api/scan', methods=['POST'])
def api_scan_now():
    if scanner:
        from scanner import scan_devices
        devices = scan_devices()
        return jsonify({"success": True, "devices_found": len(devices)})
    return jsonify({"error": "扫描器未启动"}), 500


@app.route('/api/cleanup', methods=['POST'])
def api_cleanup():
    cleanup_old_data()
    return jsonify({"success": True})


@app.route('/api/device/<mac>/block', methods=['POST'])
def api_block_device(mac):
    device = get_device_by_mac(mac)
    if not device:
        return jsonify({"error": "设备不存在"}), 404
    ip = device.get('ip', '')
    if not ip:
        return jsonify({"error": "设备IP未知，无法封禁"}), 400
    try:
        block_device(ip, mac)
        set_device_blocked(mac, True)
        from database import record_connection_event
        record_connection_event(mac, ip, 'blocked', '设备被管理员封禁，已踢出网络')
        return jsonify({"success": True, "message": f"设备 {ip} 已被封禁"})
    except Exception as e:
        return jsonify({"error": f"封禁失败: {str(e)}"}), 500


@app.route('/api/device/<mac>/unblock', methods=['POST'])
def api_unblock_device(mac):
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
        record_connection_event(mac, ip, 'limited', f'设备已限速: 上传{upload_kbps}kbps / 下载{download_kbps}kbps')
        return jsonify({"success": True, "message": f"设备 {ip} 已限速"})
    except Exception as e:
        return jsonify({"error": f"限速失败: {str(e)}"}), 500


@app.route('/api/device/<mac>/unlimit', methods=['POST'])
def api_unlimit_device(mac):
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
    devices = get_managed_devices()
    blocked = get_blocked_devices()
    limited = get_limited_devices()
    return jsonify({"managed_devices": devices, "blocked_ips": blocked, "limited_classes": limited})


@app.route('/api/device/<mac>/band', methods=['POST'])
def api_set_device_band(mac):
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
    stats = get_band_stats()
    result = {
        '2.4g': stats.get('2.4g', {'total': 0, 'online': 0}),
        '5g': stats.get('5g', {'total': 0, 'online': 0}),
        'wired': stats.get('wired', {'total': 0, 'online': 0}),
        'unknown': stats.get('unknown', {'total': 0, 'online': 0}),
    }
    return jsonify(result)


@app.route('/api/traffic-ranking')
def api_traffic_ranking():
    days = request.args.get('days', 7, type=int)
    sort = request.args.get('sort', 'total')
    if days not in [1, 7, 30]:
        days = 7
    ranking = get_traffic_ranking(days=days, limit=50)
    for item in ranking:
        item['total_upload_str'] = format_bytes(item.get('total_upload', 0))
        item['total_download_str'] = format_bytes(item.get('total_download', 0))
        item['total_str'] = format_bytes(item.get('total_upload', 0) + item.get('total_download', 0))
    if sort == 'download':
        ranking.sort(key=lambda x: x['total_download'], reverse=True)
    elif sort == 'upload':
        ranking.sort(key=lambda x: x['total_upload'], reverse=True)
    else:
        ranking.sort(key=lambda x: x['total_upload'] + x['total_download'], reverse=True)
    return jsonify({'days': days, 'sort': sort, 'ranking': ranking})


# ============================================================
# 浏览记录（DNS查询日志）+ AdGuard Home 统计
# ============================================================

@app.route('/api/browsing-history')
def api_browsing_history():
    """获取设备浏览记录（从AdGuard Home查询日志，包含所有访问域名，带缓存）"""
    import urllib.request
    limit = request.args.get('limit', 3000, type=int)
    device_ip = request.args.get('ip', None)

    now = time.time()
    if not device_ip and _browsing_cache['data'] and _browsing_cache['limit'] == limit and (now - _browsing_cache['time']) < CACHE_TTL:
        return jsonify(_browsing_cache['data'])

    try:
        url = f"http://127.0.0.1:3000/control/querylog?limit={limit}"
        if device_ip:
            url += f"&client={device_ip}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return jsonify({"error": f"获取AdGuard日志失败: {str(e)}"}), 500

    devices = {}
    for item in data.get('data', []):
        client = item.get('client', 'unknown')
        domain = item.get('question', {}).get('name', '')
        qtype = item.get('question', {}).get('type', '')
        status = item.get('status', '')
        reason = item.get('reason', '')
        blocked = reason.startswith('Filtered') or status == 'REFUSED'
        timestamp = item.get('time', '')
        answer_ips = []
        for ans in item.get('answer', []):
            if ans.get('type') in ('A', 'AAAA'):
                answer_ips.append(ans.get('value', ''))
                if len(answer_ips) >= 2:
                    break
        elapsed = item.get('elapsedMs', 0)

        if client not in devices:
            devices[client] = {'ip': client, 'total_queries': 0, 'blocked_count': 0, 'domains': {}, 'recent': []}
        devices[client]['total_queries'] += 1
        if blocked:
            devices[client]['blocked_count'] += 1

        clean_domain = domain.rstrip('.').lower()
        if clean_domain:
            if clean_domain not in devices[client]['domains']:
                devices[client]['domains'][clean_domain] = {'count': 0, 'blocked': 0, 'last_time': '', 'types': set(), 'ips': set()}
            devices[client]['domains'][clean_domain]['count'] += 1
            if blocked:
                devices[client]['domains'][clean_domain]['blocked'] += 1
            devices[client]['domains'][clean_domain]['last_time'] = timestamp
            devices[client]['domains'][clean_domain]['types'].add(qtype)
            for ip in answer_ips:
                if ip:
                    devices[client]['domains'][clean_domain]['ips'].add(ip)

        if len(devices[client]['recent']) < 30:
            devices[client]['recent'].append({
                'domain': clean_domain, 'type': qtype, 'blocked': blocked,
                'time': timestamp, 'status': status, 'reason': reason,
                'answer_ips': answer_ips, 'elapsed_ms': elapsed
            })

    result = []
    total_q = 0
    for ip, info in devices.items():
        total_q += info['total_queries']
        sorted_domains = sorted(info['domains'].items(), key=lambda x: x[1]['count'], reverse=True)
        info['top_domains'] = []
        for d, stats in sorted_domains[:80]:
            info['top_domains'].append({
                'domain': d, 'count': stats['count'], 'blocked': stats['blocked'],
                'last_time': stats['last_time'], 'types': list(stats['types']),
                'ips': list(stats['ips'])[:3]
            })
        del info['domains']
        result.append(info)

    result.sort(key=lambda x: x['total_queries'], reverse=True)
    resp_data = {'total_devices': len(result), 'total_queries': total_q, 'devices': result}

    if not device_ip:
        _browsing_cache['data'] = resp_data
        _browsing_cache['time'] = now
        _browsing_cache['limit'] = limit

    return jsonify(resp_data)


@app.route('/api/adguard-stats')
def api_adguard_stats():
    """获取 AdGuard Home 统计数据（带缓存）"""
    import urllib.request
    now = time.time()
    if _adguard_stats_cache['data'] and (now - _adguard_stats_cache['time']) < CACHE_TTL:
        return jsonify(_adguard_stats_cache['data'])

    try:
        req = urllib.request.Request("http://127.0.0.1:3000/control/stats")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return jsonify({"error": f"获取AdGuard统计失败: {str(e)}"}), 500

    result = {
        'num_dns_queries': data.get('num_dns_queries', 0),
        'num_blocked_filtering': data.get('num_blocked_filtering', 0),
        'num_replaced_safebrowsing': data.get('num_replaced_safebrowsing', 0),
        'num_replaced_parental': data.get('num_replaced_parental', 0),
        'num_replaced_safesearch': data.get('num_replaced_safesearch', 0),
        'avg_processing_time': data.get('avg_processing_time', 0),
        'top_queried_domains': [],
        'top_clients': [],
        'top_blocked_domains': [],
    }

    for item in data.get('top_queried_domains', []):
        for domain, count in item.items():
            result['top_queried_domains'].append({'domain': domain, 'count': count})
    for item in data.get('top_clients', []):
        for client, count in item.items():
            result['top_clients'].append({'client': client, 'count': count})
    for item in data.get('top_blocked_domains', []):
        for domain, count in item.items():
            result['top_blocked_domains'].append({'domain': domain, 'count': count})

    if result['num_dns_queries'] > 0:
        result['block_rate'] = round(result['num_blocked_filtering'] / result['num_dns_queries'] * 100, 1)
    else:
        result['block_rate'] = 0

    _adguard_stats_cache['data'] = result
    _adguard_stats_cache['time'] = now
    return jsonify(result)


# ============================================================
# 全局流量监控模式（全局ARP欺骗）
# ============================================================

@app.route('/api/global-spoof/status')
def api_global_spoof_status():
    return jsonify(get_global_spoof_status())


@app.route('/api/global-spoof/enable', methods=['POST'])
def api_global_spoof_enable():
    try:
        success = start_global_spoof()
        if success:
            return jsonify({"success": True, "message": "全局流量监控模式已开启", "status": get_global_spoof_status()})
        else:
            return jsonify({"error": "开启失败"}), 500
    except Exception as e:
        return jsonify({"error": f"开启失败: {str(e)}"}), 500


@app.route('/api/global-spoof/disable', methods=['POST'])
def api_global_spoof_disable():
    try:
        success = stop_global_spoof()
        if success:
            return jsonify({"success": True, "message": "全局流量监控模式已关闭", "status": get_global_spoof_status()})
        else:
            return jsonify({"error": "关闭失败"}), 500
    except Exception as e:
        return jsonify({"error": f"关闭失败: {str(e)}"}), 500


@app.route('/api/system')
def api_system():
    import platform
    return jsonify({
        "name": "NetPulse", "version": "1.0.0",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "uptime": int(time.time() - app_start_time),
        "scanner_running": scanner is not None and scanner.is_alive(),
        "traffic_monitor_running": traffic_monitor is not None and traffic_monitor.is_alive(),
    })


app_start_time = time.time()


def health_check_loop():
    """健康检查线程"""
    import threading
    check_count = 0
    while True:
        try:
            check_count += 1
            if check_count % 3 == 0:
                ensure_global_spoof_running()
                ensure_nat_and_forwarding()
            if check_count % 6 == 0:
                status = get_global_spoof_status()
                print(f"[HealthCheck] 状态: ARP欺骗={status['enabled']}, 线程存活={status.get('thread_alive')}, 心跳={status.get('heartbeat_age')}s")
            time.sleep(10)
        except Exception as e:
            print(f"[HealthCheck] 健康检查异常: {e}")
            time.sleep(10)


def main():
    global scanner, traffic_monitor
    print("=" * 60)
    print("  NetPulse - 网络设备管理系统")
    print("=" * 60)
    print("[Init] 初始化数据库...")
    init_db()
    print("[Init] 启动设备扫描线程...")
    scanner = Scanner(interval=30)
    scanner.start()
    print("[Init] 启动流量监控线程...")
    traffic_monitor = TrafficMonitor(interval=10)
    traffic_monitor.start()
    print("[Init] 初始化设备管理系统...")
    init_device_manager()
    print("[Init] 自动开启全局流量监控...")
    start_global_spoof()
    print("[Init] 启动健康检查线程（自动修复）...")
    import threading
    health_thread = threading.Thread(target=health_check_loop, daemon=True)
    health_thread.start()
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
