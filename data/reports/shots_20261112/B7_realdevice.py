# -*- coding: utf-8 -*-
"""B7 真机验收（AF-PM 11-12 开窗）：基线 + am start + 6 抽 + 回 21 + 只读 logcat。

边界：4 分钟硬停；跑前记录 my_test 窗口与 com.xiaomi.tv.gallery 现状；
am start 后轮询确认 fg=IPTV 且 no=21，确认不了即停、只记录、不继续；
6 抽 50/401/121/261/21/50 全走 tv_zap，记录 input/read/name/cost_s；
任一抽实读!=输入即 ABORT 贴原始输出；收尾轮询回 21（⛔ 不用 BACK）；
6/6 过才只读一次 logcat InputDispatcher/KeyEvent（⛔ 不额外注入任何按键）。
⛔ 不碰容器、不 push、不改 channel_numbers.json、不裸注入表外号。
用法：python data/reports/shots_20261112/B7_realdevice.py
"""
import json
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

OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B7_REALDEVICE.json'
T0 = time.time()
DEADLINE = T0 + 230.0            # 4 分钟硬停，留 10s 余量
records = []
aborted = None
started_zaps = False
zap_ok = 0


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
    """AF-PM 已放行的前置动作：把 IPTV 切到前台。"""
    try:
        return subprocess.run(
            ['adb', '-s', ADB_HOST, 'shell', 'am', 'start', '-n',
             '%s/%s' % (IPTV_PKG, IPTV_ACT)],
            capture_output=True, timeout=10).returncode
    except Exception as e:
        return repr(e)


def poll_no(target, tries=8):
    got = None
    for _ in range(tries):
        time.sleep(1.0)
        got = read_no()
        if got == target:
            break
    return got


def restore():
    """收尾：tv_zap('21') + 轮询，⛔ 不用 BACK。"""
    res, err, msg = t.tool_zap('21')
    got = poll_no(21)
    return {'zap_err': err, 'zap_msg': msg, 'no': got}


try:
    # 1) 基线现状：mCurrentFocus / mFocusedApp / my_test 窗口 / Awake / 台号
    base = {
        'mCurrentFocus': sh('dumpsys window | grep mCurrentFocus'),
        'mFocusedApp': sh('dumpsys window | grep mFocusedApp'),
        'my_test_window': sh('dumpsys window | grep -i my_test'),
        'wake': sh('dumpsys power | grep mWakefulness'),
        'no': read_no(),
    }
    base['name'] = channel_name_of_no(base['no']) if base['no'] else None
    log('BASELINE', **base)

    # 2) am start + 轮询确认 fg=IPTV 且 no=21
    log('AM_START', rc=am_start())
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

    # 3) 6 抽
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
            aborted = 'tv_zap 报错'
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
            cost_s=cost, ok=good)
        if not good:
            aborted = '实读台号 != 输入'
            log('ABORT', reason=aborted, seq=i + 1, input=no, read=got)
            raise SystemExit(3)

    log('SUMMARY', zap_ok=zap_ok, pass_6of6=(zap_ok == 6), aborted=aborted)

    # 4) 6/6 才只读一次 logcat（不额外注入任何按键）
    if zap_ok == 6 and aborted is None and time.time() < DEADLINE - 15:
        raw = sh('logcat -d -t 400', timeout=12)
        lines = [l for l in raw.splitlines()
                 if 'InputDispatcher' in l or 'KeyEvent' in l]
        log('LOGCAT_KEYEVENTS', total_lines=len(lines), lines=lines[-30:])

finally:
    # 收尾：确认失败/守卫失败时不再动设备（§9.8）；开跑后才恢复
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
