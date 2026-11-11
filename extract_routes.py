import re

path = '/app/tvpilot_server.py'
with open(path, encoding='utf-8') as f:
    lines = f.readlines()

routes = []
for i, line in enumerate(lines, 1):
    # GET 路由
    m = re.search(r"if path == '([^']+)'", line)
    if m and '#' not in line.split('if')[0]:
        routes.append({'method': 'GET', 'path': m.group(1), 'line': i})
    # path in (...) 多路径
    m = re.search(r"if path in \(([^)]+)\)", line)
    if m and '#' not in line.split('if')[0]:
        paths = re.findall(r"'([^']+)'", m.group(1))
        for p in paths:
            routes.append({'method': 'GET', 'path': p, 'line': i})
    # startswith
    m = re.search(r"if path\.startswith\('([^']+)'\)", line)
    if m and '#' not in line.split('if')[0]:
        routes.append({'method': 'GET', 'path': m.group(1) + '*', 'line': i})

# POST 路由（在 do_POST 中）
post_start = None
for i, line in enumerate(lines, 1):
    if 'def do_POST' in line:
        post_start = i
        break

if post_start:
    for i, line in enumerate(lines[post_start:], post_start + 1):
        m = re.search(r"if path == '([^']+)'", line)
        if m and '#' not in line.split('if')[0]:
            routes.append({'method': 'POST', 'path': m.group(1), 'line': i})

# 去重
seen = set()
unique = []
for r in routes:
    key = (r['method'], r['path'])
    if key not in seen:
        seen.add(key)
        unique.append(r)

# 按路径排序
unique.sort(key=lambda x: x['path'])

import json
print(json.dumps(unique, ensure_ascii=False, indent=2))
print('TOTAL:', len(unique))
