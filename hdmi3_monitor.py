# -*- coding: utf-8 -*-
"""HDMI3 使用时长监控模块（供小甜菜防沉迷系统调用）

检测原理：
  - 前台应用为 com.xiaomi.mitv.tvplayer/.ExternalSourceActivity
  - settings global LastCurrentSource = 25 (HDMI3)
  - TvInputManager mainSessionToken 非空

轮询频率：5 分钟，对电视零影响。
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

# ========== 配置 ==========
HDMI3_POLL_INTERVAL = 300  # 5 分钟
HDMI3_RETENTION_DAYS = 7   # TVPilot 本地保留 7 天，长期数据交 memory-agent
HDMI3_FOREGROUND = 'com.xiaomi.mitv.tvplayer/com.xiaomi.mitv.tvplayer.ExternalSourceActivity'
HDMI3_SOURCE_VALUE = 25

DATA_DIR = os.environ.get('TVPILOT_DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
DB_PATH = os.path.join(DATA_DIR, 'hdmi3_usage.db')

# ========== 全局状态 ==========
_db_lock = threading.Lock()
_monitor_thread = None
_monitor_running = False
_last_status = {
    'is_active': False,
    'current_source': 'unknown',
    'foreground_app': '',
    'last_check': None,
    'last_source_value': None,
}
_adb_cmd_func = None  # 由主服务注入 adb_shell_output


def init(adb_shell_output_func):
    """初始化：注入 ADB 命令函数，启动后台轮询线程。
    开关：环境变量 TVPILOT_HDMI3_ENABLED=true 才启动，默认关闭（已移交 memory-agent）。
    """
    global _adb_cmd_func, _monitor_thread, _monitor_running
    _adb_cmd_func = adb_shell_output_func
    _init_db()
    # 开关：默认关闭，HDMI3 监控已移交 memory-agent 通道
    if os.environ.get('TVPILOT_HDMI3_ENABLED', 'false').lower() != 'true':
        print('[HDMI3] monitor disabled (set TVPILOT_HDMI3_ENABLED=true to enable)')
        return
    if not _monitor_running:
        _monitor_running = True
        _monitor_thread = threading.Thread(target=_poll_loop, daemon=True)
        _monitor_thread.start()
        print('[HDMI3] monitor started, poll interval=%ds' % HDMI3_POLL_INTERVAL)


def _init_db():
    """初始化 SQLite 数据库。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('''CREATE TABLE IF NOT EXISTS usage (
            date TEXT PRIMARY KEY,
            total_seconds INTEGER DEFAULT 0,
            sessions TEXT DEFAULT '[]',
            last_check TEXT,
            is_active INTEGER DEFAULT 0
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS raw_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            is_active INTEGER,
            source_value INTEGER,
            foreground TEXT
        )''')
        conn.commit()
        conn.close()


def _poll_loop():
    """后台轮询线程：每 5 分钟检测一次 HDMI3 状态。"""
    global _last_status
    while _monitor_running:
        try:
            status = _check_hdmi3()
            _last_status.update(status)
            _record_check(status)
        except Exception as e:
            print('[HDMI3] poll error: %s' % e)
        time.sleep(HDMI3_POLL_INTERVAL)


def _check_hdmi3():
    """检测当前是否在 HDMI3 输入。"""
    if _adb_cmd_func is None:
        return {'is_active': False, 'current_source': 'unknown', 'foreground_app': '',
                'last_check': datetime.now().isoformat(), 'last_source_value': None}

    # 1. 前台应用
    fg = ''
    try:
        out = _adb_cmd_func('dumpsys window 2>/dev/null | grep mCurrentFocus')
        if out and 'Window{' in out:
            fg = out.split('u0 ', 1)[-1].strip().rstrip('}') if 'u0 ' in out else out.strip()
    except:
        pass

    # 2. LastCurrentSource
    source_val = None
    try:
        out = _adb_cmd_func('settings get global LastCurrentSource')
        if out and out.strip().isdigit():
            source_val = int(out.strip())
    except:
        pass

    # 判定
    is_hdmi3 = (HDMI3_FOREGROUND in fg) and (source_val == HDMI3_SOURCE_VALUE)
    current_source = 'hdmi3' if is_hdmi3 else ('other_hdmi' if 'ExternalSourceActivity' in fg else 'app')

    return {
        'is_active': is_hdmi3,
        'current_source': current_source,
        'foreground_app': fg,
        'last_check': datetime.now().isoformat(),
        'last_source_value': source_val,
    }


def _record_check(status):
    """记录一次检测结果，累计使用时长。"""
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    is_active = 1 if status['is_active'] else 0

    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('INSERT OR IGNORE INTO usage (date, total_seconds, sessions, last_check, is_active) VALUES (?, 0, ?, ?, ?)',
                     (today, '[]', now.isoformat(), 0))
        row = conn.execute('SELECT total_seconds, sessions, is_active FROM usage WHERE date=?', (today,)).fetchone()
        total_seconds, sessions_json, prev_active = row

        # 如果上一次是 active，本次也是 active，累计间隔时长
        if prev_active == 1 and is_active == 1:
            total_seconds += HDMI3_POLL_INTERVAL

        # 更新 session 列表
        sessions = json.loads(sessions_json) if sessions_json else []
        if is_active == 1:
            if not sessions or sessions[-1].get('end') is not None:
                sessions.append({'start': now.isoformat(), 'end': None, 'seconds': 0})
            else:
                sessions[-1]['seconds'] = total_seconds
        else:
            if sessions and sessions[-1].get('end') is None:
                sessions[-1]['end'] = now.isoformat()
                sessions[-1]['seconds'] = total_seconds

        conn.execute('UPDATE usage SET total_seconds=?, sessions=?, last_check=?, is_active=? WHERE date=?',
                     (total_seconds, json.dumps(sessions, ensure_ascii=False), now.isoformat(), is_active, today))

        conn.execute('INSERT INTO raw_checks (ts, is_active, source_value, foreground) VALUES (?, ?, ?, ?)',
                     (now.isoformat(), is_active, status.get('last_source_value'), status.get('foreground_app', '')))
        conn.execute('DELETE FROM raw_checks WHERE id NOT IN (SELECT id FROM raw_checks ORDER BY id DESC LIMIT 1000)')

        cutoff = (now - timedelta(days=HDMI3_RETENTION_DAYS)).strftime('%Y-%m-%d')
        conn.execute('DELETE FROM usage WHERE date < ?', (cutoff,))

        conn.commit()
        conn.close()


def get_status():
    """获取当前 HDMI3 状态 + 今日时长。"""
    today = datetime.now().strftime('%Y-%m-%d')
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute('SELECT total_seconds, sessions FROM usage WHERE date=?', (today,)).fetchone()
        conn.close()

    total_seconds = row[0] if row else 0
    sessions = json.loads(row[1]) if row and row[1] else []

    return {
        'is_active': _last_status['is_active'],
        'current_source': _last_status['current_source'],
        'foreground_app': _last_status['foreground_app'],
        'last_check': _last_status['last_check'],
        'last_source_value': _last_status['last_source_value'],
        'today_seconds': total_seconds,
        'today_minutes': total_seconds // 60,
        'today_hours': round(total_seconds / 3600, 2),
        'today_sessions': sessions,
    }


def get_today():
    """简化版：只返回今日时长。"""
    s = get_status()
    return {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'seconds': s['today_seconds'],
        'minutes': s['today_minutes'],
        'hours': s['today_hours'],
        'is_active': s['is_active'],
    }


def get_usage(date=None):
    """查询指定日期的使用时长。"""
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute('SELECT total_seconds, sessions FROM usage WHERE date=?', (date,)).fetchone()
        conn.close()

    if row is None:
        return {'date': date, 'seconds': 0, 'minutes': 0, 'hours': 0, 'sessions': []}

    total_seconds, sessions_json = row
    return {
        'date': date,
        'seconds': total_seconds,
        'minutes': total_seconds // 60,
        'hours': round(total_seconds / 3600, 2),
        'sessions': json.loads(sessions_json) if sessions_json else [],
    }


def get_history(days=7):
    """查询最近 N 天的统计。"""
    days = max(1, min(days, 30))
    end = datetime.now()
    daily = []
    total_seconds = 0
    for i in range(days - 1, -1, -1):
        d = (end - timedelta(days=i)).strftime('%Y-%m-%d')
        u = get_usage(d)
        daily.append({'date': d, 'seconds': u['seconds'], 'minutes': u['minutes']})
        total_seconds += u['seconds']

    active_days = sum(1 for d in daily if d['seconds'] > 0)
    return {
        'days': days,
        'total_seconds': total_seconds,
        'total_hours': round(total_seconds / 3600, 2),
        'avg_minutes_per_day': (total_seconds // 60) // days if days > 0 else 0,
        'active_days': active_days,
        'daily': daily,
    }


def get_tv_state():
    """电视通用状态（辅助接口）。"""
    s = get_status()
    return {
        'is_on': bool(s['foreground_app']) or s['is_active'],
        'foreground_app': s['foreground_app'],
        'current_source': s['current_source'],
        'last_source_value': s['last_source_value'],
        'hdmi3_active': s['is_active'],
        'last_check': s['last_check'],
    }
