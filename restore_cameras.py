#!/usr/bin/env python3
"""Restore xiaomi camera config to go2rtc.yaml

凭据纪律（AF-PM 11-11 裁定）：XIAOMI_* 三项环境变量必填，缺失即 fail-fast
报错退出；不以空值静默生成错误相机表。真值只从 .env 注入，不落盘、不打印。
"""
import os

_REQUIRED_ENV = ('XIAOMI_UID', 'XIAOMI_TOKEN', 'XIAOMI_AUTH')


def _require_env(names):
    missing = [k for k in names if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            '缺少必填环境变量 %s：拒绝以空值生成相机表；'
            '请在 .env 注入（键名见 secrets/README.md），本脚本不读取、不打印凭据值'
            % ', '.join(missing)
        )


_require_env(_REQUIRED_ENV)
XIAOMI_UID = os.environ['XIAOMI_UID']
XIAOMI_TOKEN = os.environ['XIAOMI_TOKEN']
XIAOMI_AUTH = os.environ['XIAOMI_AUTH']

CONFIG = '/vol1/1000/docker/go2rtc/go2rtc.yaml'

with open(CONFIG, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add xiaomi auth block after api block (before log:)
xiaomi_block = f'''xiaomi:
  "{XIAOMI_UID}": "{XIAOMI_TOKEN}"

'''

if 'xiaomi:' not in content:
    # Insert before "log:"
    content = content.replace('log:\n', xiaomi_block + 'log:\n', 1)
    print("Added xiaomi auth block")
else:
    print("xiaomi block already exists")

# 2. Add camera streams at the end of streams block
camera_streams = f'''
  cam_客厅:
    - xiaomi://{XIAOMI_AUTH}@192.168.2.185?did=1072229835&model=chuangmi.camera.051a01&subtype=hd
  cam_客厅标清:
    - xiaomi://{XIAOMI_AUTH}@192.168.2.185?did=1072229835&model=chuangmi.camera.051a01&subtype=sd
  cam_小黄人:
    - xiaomi://{XIAOMI_AUTH}@192.168.2.107?did=324684141&model=chuangmi.camera.ipc019e&subtype=hd
  cam_书房:
    - xiaomi://{XIAOMI_AUTH}@192.168.2.167?did=75876761&model=chuangmi.camera.ipc009&subtype=hd
'''

if 'cam_客厅' not in content:
    content = content.rstrip() + '\n' + camera_streams
    print("Added 4 camera streams")
else:
    print("camera streams already exist")

with open(CONFIG, 'w', encoding='utf-8') as f:
    f.write(content)

print("Done. Config size:", len(content))