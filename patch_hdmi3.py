import re

path = '/app/tvpilot_server.py'
with open(path, encoding='utf-8') as f:
    src = f.read()

# 1. 添加 import
if 'import hdmi3_monitor' not in src:
    src = src.replace('import threading\n', 'import threading\nimport hdmi3_monitor\n', 1)
    print('1. import added')

# 2. 在主入口启动 hdmi3_monitor
if 'hdmi3_monitor.init' not in src:
    old = 'threading.Thread(target=heal_loop, daemon=True).start()'
    new = old + '\n    hdmi3_monitor.init(adb_shell_output)'
    src = src.replace(old, new, 1)
    print('2. init added')

# 3. 在 do_GET 的 /api/history 之后添加 HDMI3 路由
hist_block = """            if path == '/api/history':
                limit = int(params.get('limit', 50))
                req_id = params.get('request_id')
                ops = read_ops_history(limit=limit, request_id=req_id)
                self._json({'ok': True, 'tool': 'history', 'result': ops})
                return"""

if '/api/tv/hdmi3/status' not in src:
    hdmi3_routes = hist_block + """

            # ===== HDMI3 时长监控接口（供小甜菜防沉迷系统） =====
            if path == '/api/tv/hdmi3/status':
                self._json({'code': 0, 'data': hdmi3_monitor.get_status()})
                return
            if path == '/api/tv/hdmi3/today':
                self._json({'code': 0, 'data': hdmi3_monitor.get_today()})
                return
            if path == '/api/tv/hdmi3/usage':
                date = params.get('date')
                self._json({'code': 0, 'data': hdmi3_monitor.get_usage(date)})
                return
            if path == '/api/tv/hdmi3/history':
                days = int(params.get('days', 7))
                self._json({'code': 0, 'data': hdmi3_monitor.get_history(days)})
                return
            if path == '/api/tv/state':
                self._json({'code': 0, 'data': hdmi3_monitor.get_tv_state()})
                return"""
    src = src.replace(hist_block, hdmi3_routes, 1)
    print('3. routes added')

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done. File size:', len(src))
