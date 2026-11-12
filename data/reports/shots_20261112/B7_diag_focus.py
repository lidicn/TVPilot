# -*- coding: utf-8 -*-
"""B7 只读诊断 聚焦：Dropping event 原文 + 11:2x 全部 injectKeyEvent + window_sample。"""
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
dropping = [redact(l) for l in lines if re.search(r'Dropping event', l, re.I)]
injects = [redact(l) for l in lines
           if 'injectKeyEvent' in l and re.search(r'11:2\d:', l)]

data = json.loads(OUT.read_text(encoding='utf-8'))
ws = data['phase1_deep']['logcat_deep']['window_sample']
data['phase1_focus'] = {
    'dropping_lines': dropping,
    'injectkeyevent_11_2x': injects,
    'window_sample_all': ws,
}
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

print('--- Dropping event lines (%d) ---' % len(dropping))
for l in dropping:
    print(l)
print('--- injectKeyEvent in 11:2x (%d) ---' % len(injects))
for l in injects:
    print(l)
print('--- window_sample all (%d) ---' % len(ws))
for l in ws:
    print(l)
