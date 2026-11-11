# -*- coding: utf-8 -*-
"""TVPilot 告警模块

监控电视在线状态，状态变化时记录告警并通过 MQTT 推送。
- 每30秒检查一次电视 ADB 连接状态
- 在线→离线 / 离线→在线 时触发告警
- 告警记录到 SQLite，提供查询 API
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime

DATA_DIR = os.environ.get('TVPILOT_DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
DB_PATH = os.path.join(DATA_DIR, 'alerts.db')

# 全局状态
_db_lock = threading.Lock()
_alert_thread = None
_alert_running = False
_last_tv_online = None  # None=未知, True=在线, False=离线
_adb_check_func = None  # 由主服务注入
_mqtt_publish_func = None  # 由主服务注入


def init(adb_check_func, mqtt_publish_func=None):
    """初始化告警模块，启动后台监控线程。"""
    global _adb_check_func, _mqtt_publish_func, _alert_thread, _alert_running
    _adb_check_func = adb_check_func
    _mqtt_publish_func = mqtt_publish_func
    _init_db()
    if not _alert_running:
        _alert_running = True
        _alert_thread = threading.Thread(target=_alert_loop, daemon=True)
        _alert_thread.start()
        print('[ALERTS] monitor started')


def _init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            type TEXT NOT NULL,
            message TEXT NOT NULL,
            resolved INTEGER DEFAULT 0,
            resolved_at TEXT
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts)')
        conn.commit()
        conn.close()


def _alert_loop():
    """后台监控线程：每30秒检查电视状态。"""
    global _last_tv_online
    check_interval = 30  # 秒
    while _alert_running:
        try:
            if _adb_check_func:
                is_online = _adb_check_func()
                if _last_tv_online is not None and is_online != _last_tv_online:
                    if not is_online:
                        _add_alert('warning', 'tv_offline', '电视离线（ADB 连接失败）')
                        _publish_mqtt('tv_offline', {'message': '电视离线'})
                    else:
                        _add_alert('info', 'tv_online', '电视恢复在线')
                        _publish_mqtt('tv_online', {'message': '电视恢复在线'})
                        # 标记最近的 tv_offline 告警为已解决
                        _resolve_recent('tv_offline')
                _last_tv_online = is_online
        except Exception as e:
            print('[ALERTS] check error: %s' % e)
        time.sleep(check_interval)


def _add_alert(level, alert_type, message):
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('INSERT INTO alerts (ts, level, type, message) VALUES (?, ?, ?, ?)',
                     (datetime.now().isoformat(), level, alert_type, message))
        conn.commit()
        conn.close()
    print('[ALERTS] %s %s: %s' % (level.upper(), alert_type, message))


def _resolve_recent(alert_type):
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('UPDATE alerts SET resolved=1, resolved_at=? WHERE type=? AND resolved=0',
                     (datetime.now().isoformat(), alert_type))
        conn.commit()
        conn.close()


def _publish_mqtt(event_type, data):
    if _mqtt_publish_func:
        try:
            payload = {'event': event_type, 'ts': datetime.now().isoformat(), **data}
            _mqtt_publish_func('tv/livingroom/alert', json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            print('[ALERTS] mqtt publish error: %s' % e)


def get_alerts(limit=20, unresolved_only=False):
    """查询最近告警。"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        if unresolved_only:
            rows = conn.execute('SELECT id, ts, level, type, message, resolved, resolved_at FROM alerts WHERE resolved=0 ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        else:
            rows = conn.execute('SELECT id, ts, level, type, message, resolved, resolved_at FROM alerts ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        conn.close()

    alerts = []
    for r in rows:
        alerts.append({
            'id': r[0], 'ts': r[1], 'level': r[2], 'type': r[3],
            'message': r[4], 'resolved': bool(r[5]), 'resolved_at': r[6]
        })
    return alerts


def get_alert_summary():
    """告警汇总：未解决告警数 + 最近24小时各类告警数。"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        unresolved = conn.execute('SELECT COUNT(*) FROM alerts WHERE resolved=0').fetchone()[0]
        since = (datetime.now().timestamp() - 86400)
        # SQLite 没有直接的时间戳比较，用字符串比较（ISO格式可排序）
        since_str = datetime.fromtimestamp(since).isoformat()[:19]
        by_type = conn.execute('SELECT type, COUNT(*) FROM alerts WHERE ts >= ? GROUP BY type', (since_str,)).fetchall()
        conn.close()

    return {
        'unresolved_count': unresolved,
        'last_24h_by_type': {t: c for t, c in by_type},
        'tv_currently_online': _last_tv_online,
    }
