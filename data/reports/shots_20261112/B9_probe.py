# -*- coding: utf-8 -*-
"""B9 决定性探针（AF-PM 11-12 放行）：证明「每键 3 秒」是不是稳定值。

零容器操作、不 push、不改 channel_numbers.json；凭据零打印（只读整数回读，不打印 SharedPreferences 原文）。
4 分钟内强制停止；探针阶段 90 秒预算。证据 B9_PROBE.json。

探针1：单次 KEYCODE_5，等 3s，回读 —— 预期 5；不变=注入没到 App。
探针2：单次 KEYCODE_0，等 3s，回读 —— 预期 0/输入框；探针1成而探针2败=0 键码问题。
探针3：5 → sleep 3.0 → 0 → sleep 3.0 → ENTER，回读 —— 预期 50；成=位间隔需 3s。
收尾：tv_zap('21') 回 21（⛔ 不用 BACK）。
用法：python data/reports/shots_20261112/B9_probe.py
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))

from tvpilot import tools as t                       # noqa: E402
from tvpilot.iptv import get_current_channel_from_logcat as read_no  # noqa: E402
from tvpilot.adb import adb_shell_output            # noqa: E402
from tvpilot.channels import channel_name_of_no     # noqa: E402
from tvpilot.config import ADB_HOST, IPTV_PKG, IPTV_ACT  # noqa: E402

OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B9_PROBE.json'
T0 = time.time()
DEADLINE = T0 + 225.0            # 4 分钟硬停（留 15s 余量）
PROBE_BUDGET = 90.0              # 探针阶段 90 秒
CRED = re.compile(r'://|token|password|passwd|auth|accountinfo|user:|apikey|secret|Bearer', re.I)
records = []
aborted = None
probe_start = None


def redact(line):
    return '<REDACTED:possible-credential-line>' if CRED.search(line) else line


def log(event, **kw):
    rec = {'t_s': round(time.time() - T0, 2), 'tag': event}
    rec.update(kw)
    records.append(rec)
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def dump():
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')


def sh(cmd, timeout=8):
    return (adb_shell_output(cmd, timeout=timeout) or '').strip()


def am_start():
    try:
        return subprocess.run(
            ['adb', '-s', ADB_HOST, 'shell', 'am', 'start', '-n',
             '%s/%s' % (IPTV_PKG, IPTV_ACT)],
            capture_output=True, timeout=10).returncode
    except Exception as e:
        return repr(e)


def inject(key, timeout=10):
    """单次 ADB 按键注入；返回 (rc, out)。"""
    try:
        r = subprocess.run(['adb', '-s', ADB_HOST, 'shell', 'input', 'keyevent', key],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, redact(((r.stdout or '') + (r.stderr or ''))[:200])
    except Exception as e:
        return -1, redact(repr(e))


def readback(tag, settle=3.0, tries=3, gap=0.5):
    """等 settle 后短轮询回读（只读整数，滞后已知），记录每次读数。"""
    time.sleep(settle)
    readings = []
    for _ in range(tries):
        readings.append(read_no())
        if len(readings) < tries:
            time.sleep(gap)
    last = readings[-1]
    log('READBACK', probe=tag, readings=readings, no=last,
        name=channel_name_of_no(last) if last else None)
    return last


def snap_logcat(tag):
    """只读抓 logcat（零注入），看键是否到达 App / 是否被 Dropping。"""
    raw = sh('logcat -d -v time -t 400', timeout=12)
    lines = [redact(l) for l in raw.splitlines()
             if re.search(r'injectKeyEvent|KeyEvent|InputDispatcher|Dropping', l)]
    keylines = [l for l in lines if 'injectKeyEvent' in l]
    log('LOGCAT', probe=tag, total_lines=len(lines),
        inject_lines=keylines[-8:], lines=lines[-14:])


def snap_ui(tag):
    """只读 ui_dump（零注入），看 OSD/输入框有没有文本，判断「是否进入输入框」。"""
    try:
        data, err, msg = t.tool_ui_dump()
        if err:
            log('UI_DUMP', probe=tag, err=str(err), msg=msg)
            return
        els = data.get('elements', []) if isinstance(data, dict) else []
        texts = [e.get('text') for e in els if e.get('text')]
        log('UI_DUMP', probe=tag, count=data.get('count'),
            texts=texts[:20], types=[e.get('type') for e in els[:10]])
    except Exception as e:
        log('UI_DUMP', probe=tag, exception=repr(e))


def guard():
    if time.time() > DEADLINE - 20 or (probe_start and time.time() > probe_start + PROBE_BUDGET):
        return '时间预算耗尽，硬停'
    return None


try:
    base = {
        'mCurrentFocus': sh('dumpsys window | grep mCurrentFocus'),
        'mFocusedApp': sh('dumpsys window | grep mFocusedApp'),
        'wake': sh('dumpsys power | grep mWakefulness'),
        'no': read_no(),
    }
    base['name'] = channel_name_of_no(base['no']) if base['no'] else None
    log('BASELINE', **base)

    log('AM_START', rc=am_start())
    time.sleep(2.0)
    fg_ok = False
    fg_after = None
    for _ in range(8):
        time.sleep(1.0)
        fg_after = sh('dumpsys window | grep mCurrentFocus')
        fg_ok = IPTV_PKG in fg_after
        if fg_ok:
            break
    log('BASELINE_CONFIRM', fg=fg_after, fg_ok=fg_ok, no=read_no())
    if not fg_ok:
        aborted = 'am start 后确认不了 fg=IPTV，按卡停止、只记录、不继续'
        log('ABORT', reason=aborted)
        raise SystemExit(2)

    probe_start = time.time()

    # 探针1：单次 5，等 3s
    if guard():
        aborted = guard(); log('TIME_BUDGET', reason=aborted); raise SystemExit(4)
    rc1, out1 = inject('KEYCODE_5')
    log('PROBE1', key='KEYCODE_5', rc=rc1, out=out1, note='单次注入，随后等 3s')
    p1 = readback('probe1', settle=3.0)
    snap_logcat('probe1')
    log('PROBE1_RESULT', no=p1, key_landed=(p1 == 5))

    # 探针2：单次 0，等 3s
    if guard():
        aborted = guard(); log('TIME_BUDGET', reason=aborted); raise SystemExit(4)
    rc2, out2 = inject('KEYCODE_0')
    log('PROBE2', key='KEYCODE_0', rc=rc2, out=out2, note='单次注入，随后等 3s')
    p2 = readback('probe2', settle=3.0)
    snap_logcat('probe2')
    snap_ui('probe2')
    log('PROBE2_RESULT', no=p2, zero_key_landed=(p2 != p1))

    # 探针3：5 → 3.0 → 0 → 3.0 → ENTER
    if guard():
        aborted = guard(); log('TIME_BUDGET', reason=aborted); raise SystemExit(4)
    log('PROBE3_START', note='5 -> sleep 3.0 -> 0 -> sleep 3.0 -> ENTER')
    rc31, o31 = inject('KEYCODE_5')
    time.sleep(3.0)
    rc32, o32 = inject('KEYCODE_0')
    time.sleep(3.0)
    rc33, o33 = inject('KEYCODE_ENTER')
    log('PROBE3', steps=[('KEYCODE_5', rc31, o31), ('KEYCODE_0', rc32, o32),
                         ('KEYCODE_ENTER', rc33, o33)])
    p3 = readback('probe3', settle=1.0, tries=5, gap=0.5)
    snap_logcat('probe3')
    log('PROBE3_RESULT', no=p3, name=channel_name_of_no(p3) if p3 else None,
        expected_50=(p3 == 50))

    log('SUMMARY', probe1_no=p1, probe2_no=p2, probe3_no=p3, aborted=aborted)

finally:
    # 收尾：tv_zap('21') 回 21（⛔ 不用 BACK）
    r = {'path': 'tv_zap(21)'}
    if IPTV_PKG not in sh('dumpsys window | grep mCurrentFocus'):
        am_start(); time.sleep(2.0)
    try:
        res, err, msg = t.tool_zap('21')
        r.update(zap_err=err, zap_msg=msg)
    except Exception as e:
        r['zap_exception'] = repr(e)
    got = None
    for _ in range(6):
        time.sleep(1.0)
        got = read_no()
        if got == 21:
            break
    # 兜底：仍不是 21 则用 3 秒间隔逐位单键回 21（⛔ 不用 BACK），同时是「3s 间隔」旁证
    if got != 21:
        r['fallback_3s'] = []
        for key in ('KEYCODE_2', 'KEYCODE_1', 'KEYCODE_ENTER'):
            rc, out = inject(key)
            r['fallback_3s'].append((key, rc))
            time.sleep(3.0)
        for _ in range(4):
            time.sleep(1.0)
            got = read_no()
            if got == 21:
                break
        r['fallback_final_no'] = got
    r['no'] = got
    fin = {
        'fg': sh('dumpsys window | grep mCurrentFocus'),
        'wake': sh('dumpsys power | grep mWakefulness'),
        'restore': r,
        'aborted': aborted,
        'elapsed_s': round(time.time() - T0, 2),
    }
    fin['no'] = got
    fin['name'] = channel_name_of_no(got) if got else None
    log('FINAL_STATE', **fin)
    dump()
