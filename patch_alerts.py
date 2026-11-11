path = '/app/tvpilot_server.py'
with open(path, encoding='utf-8') as f:
    src = f.read()

# 1. 添加 import
if 'import alerts' not in src:
    src = src.replace('import stats\n', 'import stats\nimport alerts\n', 1)
    print('1. alerts import added')

# 2. 在 hdmi3_monitor.init 之后添加 alerts.init
if 'alerts.init' not in src:
    old = '    hdmi3_monitor.init(adb_shell_output)'
    new = old + '\n    alerts.init(tv_online, mqtt_publish_alert)'
    src = src.replace(old, new, 1)
    print('2. alerts init added')

# 3. 添加 mqtt_publish_alert 辅助函数（在 mqtt 相关函数附近）
if 'def mqtt_publish_alert' not in src:
    helper = '''
def mqtt_publish_alert(topic, payload):
    """告警 MQTT 推送（供 alerts 模块调用）。"""
    try:
        if mqtt_client and mqtt_client.is_connected():
            mqtt_client.publish(topic, payload, qos=0)
    except Exception as e:
        print('[ALERTS] mqtt error: %s' % e)

'''
    # 在 make_response 函数之前插入
    src = src.replace('# ========== 统一响应 ==========', helper + '# ========== 统一响应 ==========', 1)
    print('3. mqtt_publish_alert helper added')

# 4. 在 /api/stats/errors 之后添加告警路由
anchor = """            if path == '/api/stats/errors':
                hours = int(q.get('hours', ['24'])[0])
                s = stats.get_stats(hours)
                self._json({'code': 0, 'data': {'period_hours': hours, 'by_error': s['by_error'], 'fail_count': s['fail_count']}})
                return"""

if '/api/alerts' not in src:
    alert_routes = anchor + """

            # ===== v1.0 告警查询 =====
            if path == '/api/alerts':
                limit = int(q.get('limit', ['20'])[0])
                unresolved = q.get('unresolved', ['0'])[0] == '1'
                self._json({'code': 0, 'data': alerts.get_alerts(limit, unresolved)})
                return
            if path == '/api/alerts/summary':
                self._json({'code': 0, 'data': alerts.get_alert_summary()})
                return"""
    src = src.replace(anchor, alert_routes, 1)
    print('4. alerts routes added')

with open(path, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done. File size:', len(src))
