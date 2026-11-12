# -*- coding: utf-8 -*-
"""B8 最后一次真机 6 抽（用户 11-12 说「空了」放行；验逐位单键 + 前台守卫）。

口径（AF-PM B7 卡）：am start -n IPTV → 等 2s 轮询确认 fg=IPTV 且 no=21；
6 抽 50/401/121/261/21/50 全走 tv_zap，记录 input/read/name/cost_s；
任一不符即 ABORT 贴原始输出；收尾轮询回 21（⛔ 不用 BACK）；FINAL_STATE。
6/6 过才只读一次 logcat（InputDispatcher/KeyEvent，零额外注入）。
⛔ 不碰容器、不 push、不改 channel_numbers.json、不裸注入表外号、不用 BACK。
4 分钟硬停。证据 B8_REALDEVICE.json。
用法：python data/reports/shots_20261112/B8_realdevice.py
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

OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B8_REALDEVICE.json'
T0 = time.time()
DEADLINE = T0 + 230.0
records = []
aborted = None
started_zaps = False
zap_ok = 0
CRED = re.compile(r'://|token|password|passwd|auth|accountinfo|user:|apikey|secret|Bearer', re.I)


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


def poll_no(target=21, tries=8):
    got = None
    for _ in range(tries):
        time.sleep(1.0)
        got = read_no()
        if got == target:
            break
    return got


def restore():
    """轮询回 21（⛔ 不用 BACK）；必要时重断 IPTV 前台再试一次。"""
    res, err, msg = t.tool_zap('21')
    got = poll_no(21)
    out = {'path': 'tv_zap(21)', 'zap_err': err, 'zap_msg': msg, 'no': got}
    if got != 21:
        am_start()
        time.sleep(2.0)
        res, err, msg = t.tool_zap('21')
        got = poll_no(21)
        out['retry'] = {'am_start': True, 'zap_err': err, 'no': got}
    out['final_no'] = got
    return out


try:
    base = {
        'mCurrentFocus': sh('dumpsys window | grep mCurrentFocus'),
        'mFocusedApp': sh('dumpsys window | grep mFocusedApp'),
        'wake': sh('dumpsys power | grep mWakefulness'),
        'no': read_no(),
    }
    base['name'] = channel_name_of_no(base['no']) if base['no'] else None
    log('BASELINE', **base)

    log('AM_START', rc=am_start())      # 用户已明确放行「打开 IPTV」
    time.sleep(2.0)
    fg_ok = no_ok = False
    fg_after = no_after = None
    for _ in range(8):
        time.sleep(1.0)
        fg_after = sh('dumpsys window | grep mCurrentFocus')
        no_after = read_no()
        fg_ok = IPTV_PKG in fg_after
        no_ok = (no_after == 21)
        if fg_ok and no_ok:
            break
    log('BASELINE_CONFIRM', fg=fg_after, no=no_after, fg_ok=fg_ok, no_ok=no_ok)
    if not (fg_ok and no_ok):
        aborted = 'am start 后确认不了 fg=IPTV 且 no=21，按卡停止、只记录、不继续'
        log('ABORT', reason=aborted)
        raise SystemExit(2)

    started_zaps = True
    zaps = [50, 401, 121, 261, 21, 50]
    for i, no in enumerate(zaps):
        if time.time() > DEADLINE - 25:
            aborted = '时间预算不足，提前收尾'
            log('TIME_BUDGET', remaining_s=round(DEADLINE - time.time(), 1))
            break
        t0 = time.time()
        res, err, msg = t.tool_zap(str(no))
        if err:
            log('ZAP_FAIL', seq=i + 1, input=no, err=str(err), msg=msg)
            aborted = 'tv_zap 报错（含前台守卫 invalid_state）'
            raise SystemExit(3)
        got = None
        for _ in range(4):
            time.sleep(1.0)
            got = read_no()
            if got == no:
                break
        cost = round(time.time() - t0, 2)
        good = (got == no)
        if good:
            zap_ok += 1
        log('ZAP', seq=i + 1, input=no, read=got,
            name=channel_name_of_no(got) if got else None,
            cost_s=cost, ok=good,
            foreground_action=(res or {}).get('foreground_action'))
        if not good:
            aborted = '实读台号 != 输入'
            log('ABORT', reason=aborted, seq=i + 1, input=no, read=got)
            raise SystemExit(3)

    log('SUMMARY', zap_ok=zap_ok, pass_6of6=(zap_ok == 6), aborted=aborted)

    # 6/6 才只读一次 logcat（验证逐位单键到底有没有 injectKeyEvent，零额外注入）
    if zap_ok == 6 and aborted is None and time.time() < DEADLINE - 15:
        raw = sh('logcat -d -v time -t 400', timeout=12)
        lines = [redact(l) for l in raw.splitlines()
                 if re.search(r'injectKeyEvent|InputDispatcher|KeyEvent', l)]
        log('LOGCAT_KEYEVENTS', total_lines=len(lines), lines=lines[-30:])

finally:
    if started_zaps:
        r = restore()
    else:
        r = {'skipped': '基线确认失败，未开跑，不再动设备（§9.8）'}
    fin = {
        'fg': sh('dumpsys window | grep mCurrentFocus'),
        'wake': sh('dumpsys power | grep mWakefulness'),
        'no': read_no(),
        'restore': r,
        'aborted': aborted,
        'zap_ok': zap_ok,
        'elapsed_s': round(time.time() - T0, 2),
    }
    fin['name'] = channel_name_of_no(fin['no']) if fin['no'] else None
    log('FINAL_STATE', **fin)
    dump()
