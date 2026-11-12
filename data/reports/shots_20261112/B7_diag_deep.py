# -*- coding: utf-8 -*-
"""B7 只读诊断 深挖：更大 logcat 缓冲 + getevent 输入源分析。零注入、零前台变更。"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))
from tvpilot.config import ADB_HOST  # noqa: E402

OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B7_DIAG.json'
CRED = re.compile(r'://|token|password|passwd|auth|accountinfo|user:|apikey|secret|Bearer', re.I)
KEY = re.compile(r'InputDispatcher|KeyEvent|Dropping event|ANR|WindowManager|input keyevent|injectInput|InputReader', re.I)
WIN = re.compile(r'11:(1[5-9]|2\d):')          # 11:15–11:29，覆盖 B7 窗口


def redact(line):
    return '<REDACTED:possible-credential-line>' if CRED.search(line) else line


def run(args, timeout=45):
    try:
        r = subprocess.run(['adb', '-s', ADB_HOST] + args,
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout or '') + (r.stderr or ''), r.returncode
    except Exception as e:
        return repr(e), -1


raw, rc = run(['shell', 'logcat', '-d', '-v', 'time', '-t', '30000'])
lines = raw.splitlines()
ts = [l[:18] for l in lines if re.match(r'\d\d-\d\d \d\d:\d\d:\d\d', l)]
matched = [redact(l) for l in lines if KEY.search(l)]
win = [l for l in matched if WIN.search(l)]
hits = {k: sum(1 for l in matched if k.lower() in l.lower())
        for k in ('InputDispatcher', 'InputReader', 'KeyEvent', 'Dropping event',
                  'ANR', 'WindowManager', 'input keyevent', 'injectInput')}

data = json.loads(OUT.read_text(encoding='utf-8'))
ge_lines = data['phase1']['getevent']['lines']
src = [l for l in ge_lines
       if re.search(r'name:|classes:|KEY_|DPAD|KEYBOARD|INPUT_PROP|PROPERTY|\bkey\b', l, re.I)]

data['phase1_deep'] = {
    'logcat_deep': {
        'cmd': 'adb shell logcat -d -v time -t 30000', 'rc': rc,
        'total_lines': len(lines),
        'oldest_ts': ts[0] if ts else None, 'newest_ts': ts[-1] if ts else None,
        'matched_key_lines': len(matched), 'b7_window_lines': len(win),
        'keyword_hits': hits, 'lines': matched[-60:], 'window_sample': win[-40:],
    },
    'getevent_source_lines': src[:80],
}
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

print(json.dumps({
    'rc': rc, 'total': len(lines),
    'oldest': ts[0] if ts else None, 'newest': ts[-1] if ts else None,
    'matched': len(matched), 'b7_window': len(win), 'hits': hits,
    'source_lines': len(src),
}, ensure_ascii=False, indent=2))
print('--- matched key lines (redacted, last 40) ---')
for l in matched[-40:]:
    print(l)
print('--- getevent source lines (first 60) ---')
for l in src[:60]:
    print(l)
