# -*- coding: utf-8 -*-
"""补抓 dumpsys package 的 Resolver Table 明细（零注入、零前台变更、只读）。

第一版正则按 action= 等号匹配，漏了 dumpsys 的 `Action: "..."` 冒号格式；
本版保留整段 Activity/Receiver/Service Resolver Table 的缩进明细行，
判断 IPTVActivity 有没有暴露 --es channel / --ei channelNo 之类的显式换台入口。
凭据零打印：可疑行整行脱敏。输出落 B8_CHANNEL_DIAG.json 的 phase2。
用法：python data/reports/shots_20261112/B8_channel_probe2.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))
from tvpilot.config import ADB_HOST, IPTV_PKG  # noqa: E402

OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B8_CHANNEL_DIAG.json'
CRED = re.compile(
    r'://|token|password|passwd|auth|accountinfo|user:|apikey|secret|Bearer|'
    r'KeyPassphrase|IPTVpassword|stbid|account', re.I)


def redact(line):
    return '<REDACTED:possible-credential-line>' if CRED.search(line) else line


def run(args, timeout=45):
    try:
        r = subprocess.run(['adb', '-s', ADB_HOST] + args,
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout or '') + (r.stderr or ''), r.returncode
    except Exception as e:
        return repr(e), -1


raw, rc = run(['shell', 'dumpsys', 'package', IPTV_PKG])
lines = raw.splitlines()

# 切出三个 Resolver Table 段落（含缩进明细行）
sections = {}
starts = {}
ends = {}
markers = ['Activity Resolver Table:', 'Receiver Resolver Table:',
           'Service Resolver Table:', 'Registered ContentProviders:',
           'Permissions:', 'Packages:']
idx = {m: None for m in markers}
for i, l in enumerate(lines):
    s = l.strip()
    for m in markers:
        if s == m and idx[m] is None:
            idx[m] = i
bounds = sorted((i, m) for m, i in idx.items() if i is not None)
for k, (i, name) in enumerate(bounds):
    j = bounds[k + 1][0] if k + 1 < len(bounds) else len(lines)
    sections[name] = [redact(l.rstrip()) for l in lines[i:j]]

# 关键判定：明细里是否出现 channel / channelNo / --es / --ei 等换台参数
all_detail = [l for seg in sections.values() for l in seg]
param_rx = re.compile(r'channel|--es|--ei|channelNo|channel_no|zap|tune', re.I)
channel_hits = [l.strip() for l in all_detail if param_rx.search(l)]
action_hits = [l.strip() for l in all_detail
               if re.search(r'Action:|Category:|Scheme:|Type:|Authority:', l)]

data = json.loads(OUT.read_text(encoding='utf-8'))
data['phase2_resolver_detail'] = {
    'cmd': 'adb shell dumpsys package ' + IPTV_PKG, 'rc': rc,
    'total_lines': len(lines),
    'channel_param_hits': channel_hits,
    'intent_filter_actions': action_hits[:120],
    'activity_resolver_table': sections.get('Activity Resolver Table:', [])[:120],
    'service_resolver_table': sections.get('Service Resolver Table:', [])[:80],
}
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

print('--- channel/--es/--ei 命中 (%d) ---' % len(channel_hits))
for l in channel_hits:
    print(l)
print('--- Activity Resolver Table 明细 ---')
for l in sections.get('Activity Resolver Table:', [])[:120]:
    print(l)
print('--- intent-filter Action/Category/Scheme 明细 (%d) ---' % len(action_hits))
for l in action_hits[:120]:
    print(l)
print('--- Service Resolver Table ---')
for l in sections.get('Service Resolver Table:', [])[:80]:
    print(l)
