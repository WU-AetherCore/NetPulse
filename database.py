"""
NetPulse - 数据库模块
SQLite数据库操作
"""
import sqlite3
import time
from datetime import datetime, timedelta
from config import DB_PATH, HOURLY_RETENTION_DAYS, DAILY_RETENTION_DAYS


def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_db()
    c = conn.cursor()

    # 设备表
    c.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mac TEXT UNIQUE NOT NULL,
            ip TEXT,
            name TEXT,
            vendor TEXT,
            first_seen INTEGER,
            last_seen INTEGER,
            is_online INTEGER DEFAULT 0,
            total_upload INTEGER DEFAULT 0,
            total_download INTEGER DEFAULT 0,
            current_upload_rate REAL DEFAULT 0,
            current_download_rate REAL DEFAULT 0,
            connection_count INTEGER DEFAULT 0,
            notes TEXT,
            is_ignored INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            upload_limit INTEGER DEFAULT 0,
            download_limit INTEGER DEFAULT 0
        )
    """)

    # 为旧数据库添加新字段（如果不存在）
    try:
        c.execute("ALTER TABLE devices ADD COLUMN is_blocked INTEGER DEFAULT 0")
    except:
        pass
    try:
        c.execute("ALTER TABLE devices ADD COLUMN upload_limit INTEGER DEFAULT 0")
    except:
        pass
    try:
        c.execute("ALTER TABLE devices ADD COLUMN download_limit INTEGER DEFAULT 0")
    except:
        pass
    try:
        c.execute("ALTER TABLE devices ADD COLUMN wifi_band TEXT DEFAULT 'unknown'")
    except:
        pass

    # 小时流量表
    c.execute("""
        CREATE TABLE IF NOT EXISTS traffic_hourly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_mac TEXT NOT NULL,
            hour TEXT NOT NULL,
            upload INTEGER DEFAULT 0,
            download INTEGER DEFAULT 0,
            UNIQUE(device_mac, hour)
        )
    """)

    # 天流量表
    c.execute("""
        CREATE TABLE IF NOT EXISTS traffic_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_mac TEXT NOT NULL,
            date TEXT NOT NULL,
            upload INTEGER DEFAULT 0,
            download INTEGER DEFAULT 0,
            UNIQUE(device_mac, date)
        )
    """)

    # 连接事件表
    c.execute("""
        CREATE TABLE IF NOT EXISTS connection_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_mac TEXT NOT NULL,
            device_ip TEXT,
            event_type TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            details TEXT
        )
    """)

    # 系统统计表
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            total_devices INTEGER,
            online_devices INTEGER,
            total_upload INTEGER,
            total_download INTEGER,
            avg_upload_rate REAL,
            avg_download_rate REAL
        )
    """)

    # 索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_devices_mac ON devices(mac)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_devices_online ON devices(is_online)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_traffic_hourly_mac ON traffic_hourly(device_mac)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_traffic_hourly_hour ON traffic_hourly(hour)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_traffic_daily_mac ON traffic_daily(device_mac)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_traffic_daily_date ON traffic_daily(date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_mac ON connection_events(device_mac)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON connection_events(timestamp)")

    conn.commit()
    conn.close()


def upsert_device(mac, ip, vendor=None, name=None):
    """插入或更新设备"""
    conn = get_db()
    c = conn.cursor()
    now = int(time.time())

    c.execute("SELECT id, first_seen, connection_count FROM devices WHERE mac=?", (mac,))
    row = c.fetchone()

    if row:
        c.execute("""
            UPDATE devices SET ip=?, last_seen=?, is_online=1
            WHERE mac=?
        """, (ip, now, mac))
        if vendor:
            c.execute("UPDATE devices SET vendor=? WHERE mac=? AND (vendor IS NULL OR vendor='')", (vendor, mac))
        if name:
            c.execute("UPDATE devices SET name=? WHERE mac=? AND (name IS NULL OR name='')", (name, mac))
    else:
        c.execute("""
            INSERT INTO devices (mac, ip, name, vendor, first_seen, last_seen, is_online, connection_count)
            VALUES (?, ?, ?, ?, ?, ?, 1, 1)
        """, (mac, ip, name, vendor, now, now))
        # 记录连接事件
        c.execute("""
            INSERT INTO connection_events (device_mac, device_ip, event_type, timestamp, details)
            VALUES (?, ?, 'first_seen', ?, '设备首次发现')
        """, (mac, ip, now))

    conn.commit()
    conn.close()


def mark_device_offline(mac):
    """标记设备离线"""
    conn = get_db()
    c = conn.cursor()
    now = int(time.time())
    c.execute("SELECT is_online, ip FROM devices WHERE mac=?", (mac,))
    row = c.fetchone()
    if row and row['is_online']:
        c.execute("UPDATE devices SET is_online=0 WHERE mac=?", (mac,))
        c.execute("""
            INSERT INTO connection_events (device_mac, device_ip, event_type, timestamp, details)
            VALUES (?, ?, 'disconnect', ?, '设备离线')
        """, (mac, row['ip'], now))
        conn.commit()
    conn.close()


def record_connection_event(mac, ip, event_type, details=""):
    """记录连接事件"""
    conn = get_db()
    c = conn.cursor()
    now = int(time.time())
    c.execute("""
        INSERT INTO connection_events (device_mac, device_ip, event_type, timestamp, details)
        VALUES (?, ?, ?, ?, ?)
    """, (mac, ip, event_type, now, details))
    conn.commit()
    conn.close()


def update_traffic(mac, upload_delta, download_delta):
    """更新流量统计"""
    if upload_delta <= 0 and download_delta <= 0:
        return

    conn = get_db()
    c = conn.cursor()
    now = datetime.now()
    hour_str = now.strftime("%Y-%m-%d %H:00:00")
    date_str = now.strftime("%Y-%m-%d")

    # 更新设备总流量
    c.execute("""
        UPDATE devices SET total_upload=total_upload+?, total_download=total_download+?
        WHERE mac=?
    """, (upload_delta, download_delta, mac))

    # 更新小时流量
    c.execute("""
        INSERT INTO traffic_hourly (device_mac, hour, upload, download)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(device_mac, hour) DO UPDATE SET
            upload=upload+excluded.upload,
            download=download+excluded.download
    """, (mac, hour_str, upload_delta, download_delta))

    # 更新天流量
    c.execute("""
        INSERT INTO traffic_daily (device_mac, date, upload, download)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(device_mac, date) DO UPDATE SET
            upload=upload+excluded.upload,
            download=download+excluded.download
    """, (mac, date_str, upload_delta, download_delta))

    conn.commit()
    conn.close()


def update_current_rates(mac, upload_rate, download_rate):
    """更新当前速率"""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        UPDATE devices SET current_upload_rate=?, current_download_rate=?
        WHERE mac=?
    """, (upload_rate, download_rate, mac))
    conn.commit()
    conn.close()


def get_all_devices():
    """获取所有设备"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM devices ORDER BY is_online DESC, last_seen DESC")
    devices = [dict(row) for row in c.fetchall()]
    conn.close()
    return devices


def get_device_by_mac(mac):
    """根据MAC获取设备"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM devices WHERE mac=?", (mac,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_hourly_traffic(mac, hours=24):
    """获取小时流量数据"""
    conn = get_db()
    c = conn.cursor()
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:00:00")
    c.execute("""
        SELECT hour, upload, download FROM traffic_hourly
        WHERE device_mac=? AND hour>=?
        ORDER BY hour
    """, (mac, since))
    data = [dict(row) for row in c.fetchall()]
    conn.close()
    return data


def get_daily_traffic(mac, days=30):
    """获取天流量数据"""
    conn = get_db()
    c = conn.cursor()
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    c.execute("""
        SELECT date, upload, download FROM traffic_daily
        WHERE device_mac=? AND date>=?
        ORDER BY date
    """, (mac, since))
    data = [dict(row) for row in c.fetchall()]
    conn.close()
    return data


def get_connection_events(limit=100, mac=None):
    """获取连接事件"""
    conn = get_db()
    c = conn.cursor()
    if mac:
        c.execute("""
            SELECT * FROM connection_events WHERE device_mac=?
            ORDER BY timestamp DESC LIMIT ?
        """, (mac, limit))
    else:
        c.execute("""
            SELECT * FROM connection_events
            ORDER BY timestamp DESC LIMIT ?
        """, (limit,))
    events = [dict(row) for row in c.fetchall()]
    conn.close()
    return events


def update_device_name(mac, name):
    """更新设备名称"""
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE devices SET name=? WHERE mac=?", (name, mac))
    conn.commit()
    conn.close()


def cleanup_old_data():
    """清理过期数据"""
    conn = get_db()
    c = conn.cursor()
    hourly_cutoff = (datetime.now() - timedelta(days=HOURLY_RETENTION_DAYS)).strftime("%Y-%m-%d %H:00:00")
    daily_cutoff = (datetime.now() - timedelta(days=DAILY_RETENTION_DAYS)).strftime("%Y-%m-%d")
    events_cutoff = int(time.time()) - 86400 * 90  # 90天

    c.execute("DELETE FROM traffic_hourly WHERE hour < ?", (hourly_cutoff,))
    c.execute("DELETE FROM traffic_daily WHERE date < ?", (daily_cutoff,))
    c.execute("DELETE FROM connection_events WHERE timestamp < ?", (events_cutoff,))

    conn.commit()
    conn.close()


def get_summary():
    """获取系统概览统计"""
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) as total FROM devices")
    total = c.fetchone()['total']

    c.execute("SELECT COUNT(*) as online FROM devices WHERE is_online=1")
    online = c.fetchone()['online']

    c.execute("SELECT COALESCE(SUM(total_upload),0) as up, COALESCE(SUM(total_download),0) as down FROM devices")
    row = c.fetchone()

    c.execute("SELECT COALESCE(SUM(current_upload_rate),0) as up_rate, COALESCE(SUM(current_download_rate),0) as down_rate FROM devices WHERE is_online=1")
    rates = c.fetchone()

    conn.close()
    return {
        "total_devices": total,
        "online_devices": online,
        "total_upload": row['up'],
        "total_download": row['down'],
        "avg_upload_rate": rates['up_rate'],
        "avg_download_rate": rates['down_rate'],
    }


# ============================================================
# 设备管理相关函数
# ============================================================

def set_device_blocked(mac, blocked):
    """设置设备封禁状态"""
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE devices SET is_blocked=? WHERE mac=?", (1 if blocked else 0, mac))
    conn.commit()
    conn.close()


def set_device_limit(mac, upload_kbps=0, download_kbps=0):
    """设置设备限速"""
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE devices SET upload_limit=?, download_limit=? WHERE mac=?",
              (upload_kbps, download_kbps, mac))
    conn.commit()
    conn.close()


def get_managed_devices():
    """获取所有被管理的设备（封禁或限速）"""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT mac, ip, name, vendor, is_blocked, upload_limit, download_limit
        FROM devices WHERE is_blocked=1 OR upload_limit>0 OR download_limit>0
    """)
    devices = [dict(row) for row in c.fetchall()]
    conn.close()
    return devices


def set_device_wifi_band(mac, band):
    """设置设备WiFi频段（2.4g/5g/wired/unknown）"""
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE devices SET wifi_band=? WHERE mac=?", (band, mac))
    conn.commit()
    conn.close()


def get_band_stats():
    """获取各频段设备统计"""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT wifi_band, COUNT(*) as count, COUNT(CASE WHEN is_online=1 THEN 1 END) as online
        FROM devices GROUP BY wifi_band
    """)
    stats = {row['wifi_band']: {'total': row['count'], 'online': row['online']} 
             for row in c.fetchall()}
    conn.close()
    return stats


def get_traffic_ranking(days=7, limit=20):
    """获取设备流量排行榜
    days: 统计天数（1=今天，7=一周，30=一个月）
    """
    conn = get_db()
    c = conn.cursor()
    since = (datetime.now() - timedelta(days=days-1)).strftime("%Y-%m-%d")
    
    c.execute("""
        SELECT 
            d.mac, d.ip, d.name, d.vendor, d.is_online,
            COALESCE(SUM(t.upload), 0) as total_upload,
            COALESCE(SUM(t.download), 0) as total_download
        FROM devices d
        LEFT JOIN traffic_daily t ON d.mac = t.device_mac AND t.date >= ?
        GROUP BY d.mac
        HAVING total_upload > 0 OR total_download > 0
        ORDER BY (total_upload + total_download) DESC
        LIMIT ?
    """, (since, limit))
    
    ranking = [dict(row) for row in c.fetchall()]
    conn.close()
    return ranking
