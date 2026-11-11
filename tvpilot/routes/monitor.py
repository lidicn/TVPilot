"""TVPilot 监控告警路由。

v1.0-T2：/api/metrics, /api/alerts, /api/monitor/summary。
"""
from tvpilot.monitor import get_metrics, get_alert_summary, read_alerts


def handle_monitor_get(handler, path, q):
    """统一分发：所有监控告警 GET 路由。返回 True 表示已处理。"""
    # /api/metrics：工具调用统计
    if path == '/api/metrics':
        try:
            minutes = int(q.get('minutes', ['60'])[0])
        except (ValueError, TypeError):
            handler._json({'ok': False, 'error': 'invalid_parameter', 'message': 'minutes 必须是整数'}, 400)
            return True
        minutes = max(1, min(minutes, 1440))  # 1分钟~24小时
        metrics = get_metrics(minutes=minutes)
        handler._json({'ok': True, 'tool': 'metrics', 'result': metrics})
        return True

    # /api/alerts：告警历史
    if path == '/api/alerts':
        try:
            limit = int(q.get('limit', ['50'])[0])
        except (ValueError, TypeError):
            handler._json({'ok': False, 'error': 'invalid_parameter', 'message': 'limit 必须是整数'}, 400)
            return True
        limit = max(1, min(limit, 500))
        severity = q.get('severity', [None])[0]
        alerts = read_alerts(limit=limit, severity=severity)
        handler._json({'ok': True, 'tool': 'alerts',
                       'result': {'count': len(alerts), 'alerts': alerts,
                                  'limit': limit, 'severity': severity}})
        return True

    # /api/monitor/summary：监控摘要（活跃告警+最近告警+健康状态）
    if path == '/api/monitor/summary':
        from tvpilot.health import health_check
        summary = get_alert_summary()
        hc = health_check()
        handler._json({'ok': True, 'tool': 'monitor.summary',
                       'result': {
                           'health': hc,
                           'alerts': summary,
                       }})
        return True

    return False


__all__ = ['handle_monitor_get']
