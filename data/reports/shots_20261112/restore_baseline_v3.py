# -*- coding: utf-8 -*-
"""B5 之后的安全恢复：回 IPTV 前台 + 中央一台/21。

纪律：不用 KEYCODE_BACK；不直读 SharedPreferences 原文；不注入任何表外号。
主路径走 tv_zap('21')（21 在台号表内）；若被拆键，则用更长位间的直连注入兜底
（安全恢复动作，不是测试注入，不改被测代码的注入时序）。
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))

from tvpilot import tools as t                       # noqa: E402
from tvpilot.iptv import get_current_channel_from_logcat as read_no  # noqa: E402
from tvpilot.adb import adb_shell_output            # noqa: E402
from tvpilot.config import ADB_HOST, IPTV_PKG, IPTV_ACT  # noqa: E402
from tvpilot.channels import channel_name_of_no     # noqa: E402


def sh(cmd, timeout=10):
    return (adb_shell_output(cmd, timeout=timeout) or '').strip()


def am_start():
    return subprocess.run(
        ['adb', '-s', ADB_HOST, 'shell', 'am', 'start', '-n',
         '%s/%s' % (IPTV_PKG, IPTV_ACT)],
        capture_output=True, timeout=10).returncode


def direct_inject(no, gap='0.6'):
    keys = ['input keyevent KEYCODE_%s' % d for d in str(no)]
    keys.append('input keyevent KEYCODE_ENTER')
    cmd = (' && sleep %s && ' % gap).join(keys)
    return subprocess.run(['adb', '-s', ADB_HOST, 'shell', cmd],
                          capture_output=True, timeout=15).returncode


def poll(target=21, tries=8):
    got = None
    for _ in range(tries):
        time.sleep(1.0)
        got = read_no()
        if got == target:
            break
    return got


print('s0 no=', read_no(), flush=True)
am_start()
time.sleep(3.0)
res, err, msg = t.tool_zap('21')
got = poll()
print('tv_zap(21): err=%s no=%s' % (err, got), flush=True)

if got != 21:
    print('fallback direct_inject(21, gap=0.6) rc=', direct_inject(21), flush=True)
    got = poll()
    print('after fallback no=', got, flush=True)

print('FINAL fg=', sh('dumpsys window | grep mCurrentFocus'),
      '| wake=', sh('dumpsys power | grep mWakefulness').splitlines()[0],
      '| no=', got, '| name=', channel_name_of_no(got) if got else None, flush=True)
