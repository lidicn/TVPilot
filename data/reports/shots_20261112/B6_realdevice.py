# -*- coding: utf-8 -*-
"""B6 真机重跑（AF-PM 11-12 卡）：6 抽 50→401→121→261→21→50，全走 tv_zap。

本轮换台已改为单次 `input keyevent` 多键（无 sleep / 无 && 链）。
纪律：A 不裸注入、表外号记拒绝；B 不用 KEYCODE_BACK，清态统一 am start -n IPTV；
C 不直读 SharedPreferences 原文，只读 get_current_channel_from_logcat() 一个整数。
跑前基线 IPTV 前台 + Awake + 21，不符即停；硬预算 4 分钟；
任一抽实读 != 输入即停、贴原始输出；跑完轮询回 21，贴 FINAL_STATE。
⛔ 不碰容器、不 push、不改 channel_numbers.json。
用法：python data/reports/shots_20261112/B6_realdevice.py
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
from tvpilot.errors import ERR_INVALID_PARAMETER    # noqa: E402

OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B6_REALDEVICE.json'
T0 = time.time()
DEADLINE = T0 + 230.0            # 4 分钟硬预算，留 10s 余量
records = []
aborted = None
zap_ok = 0
zap_reject = 0


def log(event, **kw):
    rec = {'t_s': round(time.time() - T0, 2), 'tag': event}
    rec.update(kw)
    records.append(rec)
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def dump():
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')


def sh(cmd, timeout=8):
    return (adb_shell_output(cmd, timeout=timeout) or '').strip()


def state():
    return {'fg': sh('dumpsys window | grep mCurrentFocus'),
            'wake': sh('dumpsys power | grep mWakefulness'),
            'no': read_no()}


def am_start():
    """纪律 B：清输入态 / 抢回前台统一 am start，禁用 KEYCODE_BACK。"""
    try:
        return subprocess.run(
            ['adb', '-s', ADB_HOST, 'shell', 'am', 'start', '-n',
             '%s/%s' % (IPTV_PKG, IPTV_ACT)],
            capture_output=True, timeout=10).returncode
    except Exception as e:
        return repr(e)


def restore():
    """跑完轮询回 21：am start 抢回前台 + tv_zap('21') + 轮询 LastchannelID。"""
    am_start()
    time.sleep(2.0)
    res, err, msg = t.tool_zap('21')
    got = None
    for _ in range(8):
        time.sleep(1.0)
        got = read_no()
        if got == 21:
            break
    return {'zap_err': err, 'zap_msg': msg, 'no': got}


try:
    b = state()
    b['name'] = channel_name_of_no(b['no']) if b['no'] else None
    log('BASELINE', **b)
    if IPTV_PKG not in b['fg'] or 'Awake' not in b['wake'] or b['no'] != 21:
        aborted = '基线不符（需 IPTV 前台 + Awake + 21），按纪律立刻停'
        log('ABORT', reason=aborted)
        raise SystemExit(2)

    zaps = [50, 401, 121, 261, 21, 50]
    for i, no in enumerate(zaps):
        if time.time() > DEADLINE - 25:
            aborted = '时间预算不足，提前收尾'
            log('TIME_BUDGET', remaining_s=round(DEADLINE - time.time(), 1))
            break
        t0 = time.time()
        # 纪律 A：每抽都走 tv_zap，绝不调 zap_via_adb_digits 裸注入
        res, err, msg = t.tool_zap(str(no))
        if err == ERR_INVALID_PARAMETER:
            zap_reject += 1
            log('ZAP_REJECT', seq=i + 1, input=no, err=err, msg=msg,
                note='fail-closed 拒绝，按纪律 A 不算 FAIL、不注入任何按键')
            continue
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
            path='tv_zap', cost_s=cost, ok=good)
        if not good:
            aborted = '实读台号 != 输入'
            log('ABORT', reason=aborted, seq=i + 1, input=no, read=got)
            raise SystemExit(3)

    log('SUMMARY', zap_ok=zap_ok, zap_reject=zap_reject,
        pass_6of6=(zap_ok == 6 and zap_reject == 0), aborted=aborted)

finally:
    # 基线守卫失败时没有做过任何测试动作，禁止自动恢复（可能打扰在场用户）
    if aborted and aborted.startswith('基线不符'):
        r = {'skipped': '基线守卫失败，未做任何测试动作，不再自动动设备'}
    else:
        r = restore()
    fin = state()
    fin['name'] = channel_name_of_no(fin['no']) if fin['no'] else None
    fin['restore'] = r
    fin['aborted'] = aborted
    fin['zap_ok'] = zap_ok
    fin['zap_reject'] = zap_reject
    fin['elapsed_s'] = round(time.time() - T0, 2)
    log('FINAL_STATE', **fin)
    dump()
