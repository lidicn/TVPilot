path = '/app/tvpilot_server.py'
with open(path, encoding='utf-8') as f:
    src = f.read()

# 在 /api/users 路由之前插入 HDMI3 路由
anchor = "            # v0.5 用户配置管理\n            if path == '/api/users':"

if '/api/tv/hdmi3/status' not in src:
    hdmi3_block = """            # ===== HDMI3 时长监控接口（供小甜菜防沉迷系统） =====
            if path == '/api/tv/hdmi3/status':
                self._json({'code': 0, 'data': hdmi3_monitor.get_status()})
                return
            if path == '/api/tv/hdmi3/today':
                self._json({'code': 0, 'data': hdmi3_monitor.get_today()})
                return
            if path == '/api/tv/hdmi3/usage':
                date = q.get('date', [None])[0]
                self._json({'code': 0, 'data': hdmi3_monitor.get_usage(date)})
                return
            if path == '/api/tv/hdmi3/history':
                days = int(q.get('days', ['7'])[0])
                self._json({'code': 0, 'data': hdmi3_monitor.get_history(days)})
                return
            if path == '/api/tv/state':
                self._json({'code': 0, 'data': hdmi3_monitor.get_tv_state()})
                return

""" + anchor
    src = src.replace(anchor, hdmi3_block, 1)
    print('HDMI3 routes inserted')
else:
    print('HDMI3 routes already exist')

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done. File size:', len(src))
