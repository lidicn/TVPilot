# -*- coding: utf-8 -*-
"""B7 条件性单键探针（AF-PM 放行，仅当只读诊断没直接命中根因）。

一次 `input keyevent KEYCODE_5`（只发一个键、无 ENTER、零前台变更、不用 BACK）；
立即只读抓 logcat 看该键是否下发/被解析成什么 keyCode；轮询台号；测完回 21。
⛔ 不碰容器、不 push、不改 channel_numbers.json、不裸注入表外号。
用法：python data/reports/shots_20261112/B7_probe.py
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))
from tvpilot.config import ADB_HOST, IPTV_PKG   # noqa: E402
from tvpilot.iptv import get_current_channel_from_logcat as read_no  # noqa: E402
from tvpilot import tools as t                  # noqa: E402
from tvpilot.adb import adb_shell_output        # noqa: E402

OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B7_DIAG.json'
CRED = re.compile(r'://|token|password|passwd|auth|accountinfo|user:|apikey|secret|Bearer', re.I)
KEYLINE = re.compile(r'injectKeyEvent|KeyEvent|InputDispatcher|Dropping|Unhandled|interceptKey', re.I)


def redact(line):
    return '<REDACTED:possible-credential-line>' if CRED.search(line) else line


def run(args, timeout=30):
    try:
        r = subprocess.run(['adb', '-s', ADB_HOST] + args,
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout or '') + (r.stderr or ''), r.returncode
    except Exception as e:
        return repr(e), -1


def sh(cmd, timeout=8):
    return (adb_shell_output(cmd, timeout=timeout) or '').strip()


def poll_no(target=21, tries=8):
    got = None
    for _ in range(tries):
        time.sleep(1.0)
        got = read_no()
        if got == target:
            break
    return got


phase2 = {
    'probe': 'input keyevent KEYCODE_5 (single key, no ENTER)',
    'note': '一次单键探针；零前台变更（不发 am start）、不用 BACK；logcat 读取为只读',
}
phase2['before'] = {
    'no': read_no(),
    'fg': sh('dumpsys window | grep mCurrentFocus'),
    'wake': sh('dumpsys power | grep mWakefulness'),
}

if IPTV_PKG not in phase2['before']['fg']:
    phase2['skipped'] = '当前前台非 IPTV（疑似用户按了 HOME），零前台变更约束下不注入；探针会打到错误窗口，结果不可信'
else:
    raw_probe, rc = run(['shell', 'input', 'keyevent', 'KEYCODE_5'])
    phase2['probe_rc'] = rc
    phase2['probe_stdout_stderr'] = redact(raw_probe[:300])

    # 立刻只读抓 logcat（不额外注入任何按键）
    raw_log = run(['shell', 'logcat', '-d', '-v', 'time', '-t', '800'])[0]
    key_lines = [redact(l) for l in raw_log.splitlines() if KEYLINE.search(l)]
    inject_lines = [l for l in key_lines if 'injectKeyEvent' in l]
    keycodes = re.findall(r'keyCode=(\w+)', ' '.join(inject_lines))
    phase2['logcat_after_probe'] = {
        'cmd': 'adb shell logcat -d -v time -t 800',
        'total_key_lines': len(key_lines),
        'injectKeyEvent_lines': inject_lines[-20:],
        'keycodes_seen': keycodes[-20:],
        'key_lines_tail': key_lines[-30:],
    }

    readings = []
    for _ in range(6):
        time.sleep(1.0)
        readings.append(read_no())
    phase2['no_readings'] = readings
    no_after = readings[-1]
    phase2['no_after'] = no_after
    phase2['台号变5'] = (no_after == 5)

    # 收尾：轮询回 21（⛔ 不用 BACK）
    if no_after == 21:
        phase2['restore'] = {'skipped': '台号仍为 21，探针未改变台号，无需恢复'}
    else:
        res, err, msg = t.tool_zap('21')
        got = poll_no(21)
        phase2['restore'] = {'path': 'tv_zap(21)', 'zap_err': err, 'no': got}
        if got != 21:
            # 安全兜底：逐位单键（探针已证单键可下发），全程 ⛔ 不用 BACK
            for key in ('KEYCODE_2', 'KEYCODE_1', 'KEYCODE_ENTER'):
                run(['shell', 'input', 'keyevent', key])
                time.sleep(0.6)
            got = poll_no(21)
            phase2['restore_fallback'] = {
                'path': 'single-key 2/1/ENTER (safety restore, no BACK)', 'no': got}

phase2['final'] = {'no': read_no(), 'fg': sh('dumpsys window | grep mCurrentFocus')}
phase2['verdict'] = {
    'digit_inject_line_found': any('KEYCODE_5' in l or 'keyCode=12' in l
                                   for l in phase2.get('logcat_after_probe', {}).get('injectKeyEvent_lines', [])),
    'unknown_keycode_seen': any('KEYCODE_UNKNOWN' in str(k)
                                for k in phase2.get('logcat_after_probe', {}).get('keycodes_seen', [])),
}

data = json.loads(OUT.read_text(encoding='utf-8'))
data['phase2'] = phase2
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(phase2, ensure_ascii=False, indent=2)[:4500])
