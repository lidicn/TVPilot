#!/usr/bin/env python3
FILE = '/app/tvpilot_server.py'
with open(FILE, 'r', encoding='utf-8') as f:
    content = f.read()

old = "content = content.rstrip() + '\\n' + generate_camera_m3u()"
new = "content = content.rstrip() + b'\\n' + generate_camera_m3u().encode('utf-8')"

if old in content:
    content = content.replace(old, new, 1)
    with open(FILE, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Fixed: bytes concat")
else:
    print("Pattern not found, checking current state...")
    import re
    matches = re.findall(r'content = content\.rstrict\(\).*', content)
    for m in matches:
        print("  ", m)
