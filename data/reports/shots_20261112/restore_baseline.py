# -*- coding: utf-8 -*-
"""真机验收后的安全恢复：IPTV 前台 + 中央一台/21。

只解析 Service/LastchannelID 一个整数，凭据零打印；不碰容器。
用法：python data/reports/shots_20261112/restore_baseline.py
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))

from tvpilot.iptv import get_current_channel_from_logcat as read_no  # noqa: E402
from tvpilot.config import ADB_HOST, IPTV_PKG, IPTV_ACT  # noqa: E402


def adb(args, timeout=15):
    try:
        return subprocess.run(['adb', '-s', ADB_HOST] + args,
                              capture_output=True, timeout=timeout).returncode
    except Exception as e:
        return repr(e)


def inject(no, gap='0.35'):
    keys = ['input keyevent KEYCODE_%s' % d for d in str(no)]
    keys.append('input keyevent KEYCODE_ENTER')
    cmd = (' && sleep %s && ' % gap).join(keys)
    return adb(['shell', cmd])


print('before =', read_no(), flush=True)
adb(['shell', 'input keyevent KEYCODE_BACK'])      # 清掉可能残留的数字输入条 / 异常框
time.sleep(1.0)
adb(['shell', 'am', 'start', '-n', '%s/%s' % (IPTV_PKG, IPTV_ACT)])
time.sleep(2.0)

final = None
for attempt in range(3):
    rc = inject(21)
    for _ in range(6):
        time.sleep(1.0)
        final = read_no()
        if final == 21:
            break
    print('attempt %d: inject_rc=%s no=%s' % (attempt + 1, rc, final), flush=True)
    if final == 21:
        break
    time.sleep(2.0)

print('final =', final, flush=True)
