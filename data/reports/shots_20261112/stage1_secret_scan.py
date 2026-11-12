# -*- coding: utf-8 -*-
"""阶段 1 推送前全量敏感面扫描（凭据零打印：只输出 file:line + 命中类别）。

分层：
  A 路径层  — git ls-files 里是否跟踪了真实凭据/运行时文件
  B 赋值层  — 键名 = 引号包裹的长实值（排除占位符/样例）
  C URL 层  — user:pass@ 真实内嵌认证（排除 example.invalid / 占位）
  D 私钥层  — PEM 私钥头
  E 硬编码 IP 层 — 记录 192.168.2.238 / 192.168.2.200 出现点（非凭据，供 AF-PM 复核）
输出 STAGE1_SECRET_SCAN.json，绝不打印任何匹配到的值本身。
用法：python data/reports/shots_20261112/stage1_secret_scan.py
"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
OUT = ROOT / 'data' / 'reports' / 'shots_20261112'

files = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True,
                       text=True, encoding='utf-8').stdout.splitlines()

# ---- A. 路径层 ----
SENSITIVE_PATHS = re.compile(
    r'(^|/)\.env(\..*)?$|^secrets/[^/]+$|\.m3u$|^data/ops\.jsonl$|^data/dbg\.py$'
    r'|^doc/snap_tvpilot_server_|^backup/|^adbkey(\.pub)?$')
ALLOWED_SAMPLES = re.compile(r'\.env\.example$|\.example\.m3u$|^secrets/README\.md$')
path_hits = [f for f in files if SENSITIVE_PATHS.search(f) and not ALLOWED_SAMPLES.search(f)]
path_samples = [f for f in files if SENSITIVE_PATHS.search(f) and ALLOWED_SAMPLES.search(f)]

# ---- B/C/D. 内容层 ----
KEY_ASSIGN = re.compile(
    r'(?i)(api[_-]?key|access[_-]?token|token|secret|passwd|password|hmac|salt|auth[_-]?key)'
    r'\s*[:=]\s*[\'"]([^\'"]{8,})[\'"]')
URL_USERPASS = re.compile(r'://[^/@\s]+:[^/@\s]+@[^\s\'"<>]+')
PRIVATE_KEY = re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----')
PLACEHOLDER = re.compile(
    r'(?i)your|xxx|placeholder|changeme|example|dummy|sample|redact|secret_here|'
    r'\{\{|\}\}|<[^>]+>|^[=_\-\.]+$|todo|fixme|none|null|false|true|'
    r'os\.environ|getenv|\$\{|^%s$|^test|^aaa|^0000')

assign_hits, url_hits, key_hits = [], [], []
for f in files:
    p = ROOT / f
    try:
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        text = p.read_text(encoding='utf-8', errors='replace')
    except OSError:
        continue
    for i, line in enumerate(text.splitlines(), 1):
        m = KEY_ASSIGN.search(line)
        if m and not PLACEHOLDER.search(m.group(2)):
            assign_hits.append({'file': f, 'line': i, 'kind': 'credential_assignment',
                                'value_len': len(m.group(2))})
        for u in URL_USERPASS.finditer(line):
            raw = u.group(0)
            if PLACEHOLDER.search(raw) or 'example.invalid' in raw:
                continue
            url_hits.append({'file': f, 'line': i, 'kind': 'url_embedded_credentials',
                             'value_len': len(raw)})
        if PRIVATE_KEY.search(line):
            key_hits.append({'file': f, 'line': i, 'kind': 'pem_private_key'})

# ---- E. 硬编码 IP 层（非凭据，仅登记） ----
ip_hits = []
for f in files:
    p = ROOT / f
    try:
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        text = p.read_text(encoding='utf-8', errors='replace')
    except OSError:
        continue
    for i, line in enumerate(text.splitlines(), 1):
        if '192.168.2.238' in line or '192.168.2.200' in line:
            ip_hits.append({'file': f, 'line': i, 'kind': 'hardcoded_lan_ip'})

result = {
    'tracked_files': len(files),
    'A_sensitive_paths': path_hits,
    'A_allowed_samples': path_samples,
    'B_credential_assignments': assign_hits,
    'C_url_embedded_credentials': url_hits,
    'D_pem_private_keys': key_hits,
    'E_hardcoded_lan_ip_count': len(ip_hits),
    'E_hardcoded_lan_ip_files': sorted({h['file'] for h in ip_hits}),
}
(OUT / 'STAGE1_SECRET_SCAN.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')

print('TRACKED_FILES =', len(files))
print('A_SENSITIVE_PATHS =', len(path_hits), path_hits)
print('A_ALLOWED_SAMPLES =', len(path_samples), path_samples)
print('B_CREDENTIAL_ASSIGNMENTS =', len(assign_hits))
for h in assign_hits:
    print('   %s:%d %s (len=%d, value NOT printed)' % (h['file'], h['line'], h['kind'], h['value_len']))
print('C_URL_EMBEDDED_CREDENTIALS =', len(url_hits))
for h in url_hits:
    print('   %s:%d %s (len=%d, value NOT printed)' % (h['file'], h['line'], h['kind'], h['value_len']))
print('D_PEM_PRIVATE_KEYS =', len(key_hits), key_hits)
print('E_HARDCODED_LAN_IP_COUNT =', len(ip_hits), '(非凭据，登记供复核)')
print('E_FILES =', sorted({h['file'] for h in ip_hits}))
real = len(path_hits) + len(assign_hits) + len(url_hits) + len(key_hits)
print('REAL_CREDENTIAL_HITS =', real)
print('WROTE STAGE1_SECRET_SCAN.json')
