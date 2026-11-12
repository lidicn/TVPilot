# -*- coding: utf-8 -*-
"""B7 只读诊断（零注入、零前台变更、零容器操作）：logcat + getevent -p。

凭据零打印：命中凭据特征的行整行替换为 <REDACTED:possible-credential-line>。
原始（脱敏）输出落 B7_DIAG.json；phase2（单键探针）由后续按证据决定是否执行。
用法：python data/reports/shots_20261112/B7_diag.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))
from tvpilot.config import ADB_HOST                 # noqa: E402
from tvpilot.iptv import get_current_channel_from_logcat as read_no  # noqa: E402

OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B7_DIAG.json'
CRED = re.compile(r'://|token|password|passwd|auth|accountinfo|user:|apikey|secret|Bearer', re.I)
KEY = re.compile(r'InputDispatcher|WindowManager|KeyEvent|Dropping event|ANR|channel|input keyevent', re.I)
WINDOW = re.compile(r'11:2\d:')          # B7 时间窗约 11:26，放宽到 11:20–11:29


def redact(line):
    return '<REDACTED:possible-credential-line>' if CRED.search(line) else line


def run(args, timeout=25):
    try:
        r = subprocess.run(['adb', '-s', ADB_HOST] + args,
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout or '') + (r.stderr or ''), r.returncode
    except Exception as e:
        return repr(e), -1


log_raw, rc1 = run(['shell', 'logcat', '-d', '-v', 'time', '-t', '3000'])
log_lines = log_raw.splitlines()
matched_raw = [l for l in log_lines if KEY.search(l)]
matched = [redact(l) for l in matched_raw]
n_red = sum(1 for l in matched_raw if CRED.search(l))
window_lines = [l for l in matched if WINDOW.search(l)]
hits = {k: sum(1 for l in matched if k.lower() in l.lower())
        for k in ('Dropping event', 'ANR', 'channel', 'KeyEvent',
                  'InputDispatcher', 'WindowManager', 'input keyevent')}

ge_raw, rc2 = run(['shell', 'getevent', '-p'])
ge_lines = [redact(l) for l in ge_raw.splitlines()]
dev_names = [l.strip() for l in ge_lines
             if 'name:' in l.lower() or l.strip().startswith('add device')]

phase1 = {
    'logcat': {
        'cmd': 'adb shell logcat -d -v time -t 3000', 'rc': rc1,
        'total_lines': len(log_lines), 'matched_lines': len(matched),
        'window_lines_11_2x': len(window_lines), 'keyword_hits': hits,
        'redacted_lines': n_red, 'lines': matched[-60:],
        'window_sample': window_lines[-40:],
    },
    'getevent': {
        'cmd': 'adb shell getevent -p', 'rc': rc2,
        'total_lines': len(ge_lines), 'device_names': dev_names[:30],
        'lines': ge_lines[:120],
    },
    'current_no_readonly': read_no(),
}
data = {
    'note': 'B7 只读诊断：零注入、零前台变更、零容器操作、凭据零打印（可疑行整行脱敏）',
    'phase1': phase1,
}
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

print(json.dumps({
    'logcat_total': len(log_lines), 'matched': len(matched),
    'window_11_2x': len(window_lines), 'keyword_hits': hits,
    'redacted': n_red, 'getevent_rc': rc2,
    'getevent_devices': dev_names[:20],
    'current_no': phase1['current_no_readonly'],
}, ensure_ascii=False, indent=2))
print('--- matched (redacted, last 40) ---')
for l in matched[-40:]:
    print(l)
