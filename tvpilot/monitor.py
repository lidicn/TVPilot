"""TVPilot 监控与告警。

v1.0-T2：工具调用统计、电视离线告警、容器异常告警。
"""
import os
import json
import time
import threading
from collections import defaultdict

from tvpilot.ops_log import OPS_LOG, read_ops_history
from tvpilot.health import health_check

DATA_DIR = os.environ.get('TVPILOT_DATA_DIR', '/app/data')
ALERTS_FILE = os.path.join(DATA_DIR, 'alerts.jsonl')
ALERTS_LOCK = threading.Lock()
ALERTS_MAX = 500  # 最多保留 500 条告警

# 告警状态（内存）
_alert_state = {
    'tv_offline_since': None,
    'last_tv_online': time.time(),
    'last_health_check': 0,
    'last_degraded_since': None,
}


def _local_iso_ts():
    """带本地时区偏移的 ISO 时间戳。"""
    t = time.localtime()
    off = -time.altzone if (time.daylight and t.tm_isdst) else -time.timezone
    sign = '+' if off >= 0 else '-'
    off = abs(off)
    return time.strftime('%Y-%m-%dT%H:%M:%S', t) + f'{sign}{off // 3600:02d}:{off % 3600 // 60:02d}'


def _write_alert(alert_type, severity, message, details=None):
    """写入一条告警到 alerts.jsonl。"""
    try:
        record = {
            'ts': _local_iso_ts(),
            'ts_epoch': round(time.time(), 3),
            'type': alert_type,
            'severity': severity,  # info / warning / critical
            'message': message,
            'details': details or {},
        }
        with ALERTS_LOCK:
            with open(ALERTS_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        # 滚动：超过上限则保留最新 ALERTS_MAX 条
        _trim_alerts()
    except Exception as e:
        print(f'[monitor] write alert failed: {e}', flush=True)


def _trim_alerts():
    """告警文件超过上限则保留最新 ALERTS_MAX 条。"""
    try:
        if not os.path.exists(ALERTS_FILE):
            return
        with open(ALERTS_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) > ALERTS_MAX:
            with open(ALERTS_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines[-ALERTS_MAX:])
    except Exception:
        pass


def read_alerts(limit=50, severity=None):
    """读取告警历史。"""
    try:
        if not os.path.exists(ALERTS_FILE):
            return []
        records = []
        with open(ALERTS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if severity and rec.get('severity') != severity:
                    continue
                records.append(rec)
        if limit and limit > 0:
            records = records[-int(limit):]
        return records
    except Exception as e:
        print(f'[monitor] read alerts failed: {e}', flush=True)
        return []


def check_tv_alert():
    """检查电视在线状态，离线/恢复时触发告警。由 heal_loop 定期调用。"""
    try:
        hc = health_check()
        now = time.time()
        if hc.get('tv_online'):
            _alert_state['last_tv_online'] = now
            if _alert_state['tv_offline_since'] is not None:
                # 电视恢复在线
                offline_duration = int(now - _alert_state['tv_offline_since'])
                _write_alert('tv_recovered', 'info',
                             f'电视恢复在线（离线 {offline_duration}s）',
                             {'offline_seconds': offline_duration})
                _alert_state['tv_offline_since'] = None
        else:
            if _alert_state['tv_offline_since'] is None:
                # 电视刚离线
                _alert_state['tv_offline_since'] = now
                _write_alert('tv_offline', 'critical',
                             '电视离线（ADB 不可达）',
                             {'last_online': _alert_state['last_tv_online']})
    except Exception as e:
        print(f'[monitor] tv alert check failed: {e}', flush=True)


def check_health_alert():
    """检查服务健康状态，degraded 时触发告警。"""
    try:
        hc = health_check()
        now = time.time()
        _alert_state['last_health_check'] = now
        if hc.get('degraded'):
            if _alert_state['last_degraded_since'] is None:
                _alert_state['last_degraded_since'] = now
                _write_alert('service_degraded', 'warning',
                             f'服务降级: {", ".join(hc.get("problems", []))}',
                             {'problems': hc.get('problems', [])})
        else:
            if _alert_state['last_degraded_since'] is not None:
                degraded_duration = int(now - _alert_state['last_degraded_since'])
                _write_alert('service_recovered', 'info',
                             f'服务恢复正常（降级 {degraded_duration}s）',
                             {'degraded_seconds': degraded_duration})
                _alert_state['last_degraded_since'] = None
    except Exception as e:
        print(f'[monitor] health alert check failed: {e}', flush=True)


def get_metrics(minutes=60):
    """工具调用统计：从 ops.jsonl 读取最近 N 分钟的记录。

    返回按工具名分组的统计：调用次数、成功率、平均延迟、P95延迟、最近错误。
    """
    try:
        # 读取最近 1000 条记录（足够覆盖 60 分钟）
        records = read_ops_history(limit=1000)
        cutoff = time.time() - minutes * 60
        # 按工具名分组
        stats = defaultdict(lambda: {
            'count': 0, 'success': 0, 'fail': 0,
            'costs': [], 'last_error': None, 'last_error_ts': None,
        })
        for rec in records:
            ts = rec.get('ts_epoch', 0)
            if ts < cutoff:
                continue
            tool = rec.get('tool', 'unknown')
            s = stats[tool]
            s['count'] += 1
            if rec.get('ok'):
                s['success'] += 1
            else:
                s['fail'] += 1
                s['last_error'] = rec.get('error')
                s['last_error_ts'] = rec.get('ts')
            cost = rec.get('cost_ms')
            if cost is not None:
                s['costs'].append(cost)
        # 计算统计指标
        result = {}
        for tool, s in stats.items():
            costs = sorted(s['costs'])
            avg_cost = round(sum(costs) / len(costs), 1) if costs else 0
            p95_cost = costs[int(len(costs) * 0.95)] if costs else 0
            p99_cost = costs[int(len(costs) * 0.99)] if costs else 0
            success_rate = round(s['success'] / s['count'] * 100, 1) if s['count'] > 0 else 0
            result[tool] = {
                'count': s['count'],
                'success': s['success'],
                'fail': s['fail'],
                'success_rate': success_rate,
                'avg_cost_ms': avg_cost,
                'p95_cost_ms': p95_cost,
                'p99_cost_ms': p99_cost,
                'last_error': s['last_error'],
                'last_error_ts': s['last_error_ts'],
            }
        # 汇总
        total_count = sum(r['count'] for r in result.values())
        total_success = sum(r['success'] for r in result.values())
        total_fail = sum(r['fail'] for r in result.values())
        return {
            'window_minutes': minutes,
            'total_calls': total_count,
            'total_success': total_success,
            'total_fail': total_fail,
            'overall_success_rate': round(total_success / total_count * 100, 1) if total_count > 0 else 0,
            'by_tool': result,
            'generated_at': _local_iso_ts(),
        }
    except Exception as e:
        print(f'[monitor] metrics failed: {e}', flush=True)
        return {'error': str(e)}


def get_alert_summary():
    """告警摘要：当前活跃告警 + 最近告警列表。"""
    alerts = read_alerts(limit=20)
    active = []
    if _alert_state['tv_offline_since'] is not None:
        active.append({
            'type': 'tv_offline',
            'severity': 'critical',
            'since': _alert_state['tv_offline_since'],
            'duration_seconds': int(time.time() - _alert_state['tv_offline_since']),
        })
    if _alert_state['last_degraded_since'] is not None:
        active.append({
            'type': 'service_degraded',
            'severity': 'warning',
            'since': _alert_state['last_degraded_since'],
            'duration_seconds': int(time.time() - _alert_state['last_degraded_since']),
        })
    return {
        'active_alerts': active,
        'recent_alerts': alerts,
        'alerts_total': len(read_alerts(limit=ALERTS_MAX)),
    }


__all__ = [
    'get_metrics', 'get_alert_summary', 'read_alerts',
    'check_tv_alert', 'check_health_alert',
    'ALERTS_FILE',
]
