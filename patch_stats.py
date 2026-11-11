path = '/app/tvpilot_server.py'
with open(path, encoding='utf-8') as f:
    src = f.read()

# 1. 添加 import
if 'import stats' not in src:
    src = src.replace('import hdmi3_monitor\n', 'import hdmi3_monitor\nimport stats\n', 1)
    print('1. stats import added')

# 2. 在 /api/tv/state 路由之后添加统计路由
anchor = """            if path == '/api/tv/state':
                self._json({'code': 0, 'data': hdmi3_monitor.get_tv_state()})
                return"""

if '/api/stats' not in src:
    stats_routes = anchor + """

            # ===== v1.0 工具调用统计与监控 =====
            if path == '/api/stats':
                hours = int(q.get('hours', ['24'])[0])
                self._json({'code': 0, 'data': stats.get_stats(hours)})
                return
            if path == '/api/stats/tools':
                hours = int(q.get('hours', ['24'])[0])
                s = stats.get_stats(hours)
                self._json({'code': 0, 'data': {'period_hours': hours, 'by_tool': s['by_tool']}})
                return
            if path == '/api/stats/errors':
                hours = int(q.get('hours', ['24'])[0])
                s = stats.get_stats(hours)
                self._json({'code': 0, 'data': {'period_hours': hours, 'by_error': s['by_error'], 'fail_count': s['fail_count']}})
                return"""
    src = src.replace(anchor, stats_routes, 1)
    print('2. stats routes added')

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done. File size:', len(src))
