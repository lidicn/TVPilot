# -*- coding: utf-8 -*-
"""安全恢复 v2：不再按 BACK（会退出 IPTV），用 am start / monkey 抢回前台 + 重注 21。"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))

from tvpilot.iptv import get_current_channel_from_logcat as read_no  # noqa: E402
from tvpilot.config import ADB_HOST, IPTV_PKG, IPTV_ACT  # noqa: E402


def sh(cmd, timeout=15):
    try:
        r = subprocess.run(['adb', '-s', ADB_HOST, 'shell', cmd],
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout or '').strip()[:160]
    except Exception as e:
        return repr(e)


def fg():
    return sh('dumpsys window | grep mCurrentFocus')


def inject(no):
    keys = ['input keyevent KEYCODE_%s' % d for d in str(no)]
    keys.append('input keyevent KEYCODE_ENTER')
    return sh(' && sleep 0.35 && '.join(keys))


print('s0 fg=', fg(), 'no=', read_no(), flush=True)
print('am=', sh('am start -n %s/%s' % (IPTV_PKG, IPTV_ACT)), flush=True)
time.sleep(3)
print('s1 fg=', fg(), 'no=', read_no(), flush=True)
if IPTV_PKG not in fg():
    print('monkey=', sh('monkey -p %s -c android.intent.category.LAUNCHER 1' % IPTV_PKG), flush=True)
    time.sleep(3)
    print('s2 fg=', fg(), 'no=', read_no(), flush=True)

# 让可能残留的数字输入态自然超时，再注入 21
time.sleep(4)
final = read_no()
for k in range(4):
    inject(21)
    for _ in range(6):
        time.sleep(1.0)
        final = read_no()
        if final == 21:
            break
    print('inject %d -> no=%s fg=%s' % (k + 1, final, fg()), flush=True)
    if final == 21:
        break
    time.sleep(2)
print('FINAL fg=', fg(), 'no=', final, flush=True)
