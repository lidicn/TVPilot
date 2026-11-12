# -*- coding: utf-8 -*-
"""真机验收（AF-PM 11-12 放行）：zap 6 抽 + ui_dump/get_element + 回基线。

边界：≤3 分钟硬预算；跑前记基线；跑完回 IPTV 前台 + 中央一台/21；
任一 FAIL 就停、贴原始输出（finally 里只做安全恢复，不继续跑测试）。
全程 adb 直连 + 工作区代码，不碰容器；只读 LastchannelID 一个整数，凭据零打印。
用法：python data/reports/shots_20261112/B4_realdevice.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))

from tvpilot import tools as t                      # noqa: E402
from tvpilot import iptv as ip                     # noqa: E402
from tvpilot.adb import adb_shell_output           # noqa: E402
from tvpilot.channels import channel_name_of_no, channel_numbers_map  # noqa: E402
from tvpilot.config import ADB_HOST, IPTV_PKG, IPTV_ACT  # noqa: E402

OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B4_REALDEVICE.json'
T0 = time.time()
DEADLINE = T0 + 165.0
records = []
aborted = None


def log(tag, **kw):
    rec = {'t_s': round(time.time() - T0, 2), 'tag': tag}
    rec.update(kw)
    records.append(rec)
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def dump():
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')


def sh(cmd, timeout=8):
    return (adb_shell_output(cmd, timeout=timeout) or '').strip()


def read_no():
    # 只解析 Service/LastchannelID 一个整数，绝不回显 SharedPreferences 原文
    return ip.get_current_channel_from_logcat()


def state():
    return {'fg': sh('dumpsys window | grep mCurrentFocus'),
            'wake': sh('dumpsys power | grep mWakefulness'),
            'no': read_no()}


def bring_iptv_front():
    subprocess.run(['adb', '-s', ADB_HOST, 'shell', 'am', 'start', '-n',
                    '%s/%s' % (IPTV_PKG, IPTV_ACT)],
                   capture_output=True, timeout=10)


def restore():
    """安全恢复：必要时唤起 IPTV，再注入台号 21。"""
    fg = sh('dumpsys window | grep mCurrentFocus')
    if IPTV_PKG not in fg:
        bring_iptv_front()
        time.sleep(0.6)
    ok, msg = t.zap_via_adb_digits(21)
    got = None
    for _ in range(5):
        time.sleep(1.0)
        got = read_no()
        if got == 21:
            break
    return {'zap_ok': ok, 'zap_msg': msg, 'no': got}


def do_ui_dump(tag):
    d, err, msg = t.tool_ui_dump()
    if err:
        log('UIDUMP_FAIL', stage=tag, err=str(err), msg=msg)
        return None
    els = d['elements']
    texts = [(e.get('text') or '') for e in els]
    log(tag, count=d['count'], texts=texts[:20],
        types=[e.get('type') for e in els][:20])
    if d['count'] <= 0:
        return None
    idx = next((i for i, x in enumerate(texts) if x), 0)
    ge, gerr, gmsg = t.tool_get_element(idx)
    if gerr:
        log('GETELEMENT_FAIL', stage=tag, err=str(gerr), msg=gmsg)
        return d
    log('GETELEMENT', stage=tag, index=idx, text=ge.get('text'),
        bounds=ge.get('bounds'), has_real_text=bool(texts[idx]),
        xpath=ge.get('xpath'))
    return d


try:
    b = state()
    b['name'] = channel_name_of_no(b['no']) if b['no'] else None
    log('BASELINE', **b)
    if IPTV_PKG not in b['fg'] or 'Awake' not in b['wake']:
        aborted = '前台非 IPTV 或屏幕非 Awake，按纪律立刻停'
        log('ABORT', reason=aborted)
        raise SystemExit(2)

    # 先做一次 dump/get_element，确保任一 zap FAIL 时也有这份证据
    if do_ui_dump('UIDUMP_BASELINE') is None:
        aborted = 'ui_dump count=0 或报错'
        raise SystemExit(4)

    has_name = {int(v) for v in channel_numbers_map().values()}
    zaps = [50, 401, 100, 50, 401, 100]
    for i, no in enumerate(zaps):
        if time.time() > DEADLINE - 35:
            aborted = '时间预算不足，提前收尾'
            log('TIME_BUDGET', remaining_s=round(DEADLINE - time.time(), 1))
            break
        t0 = time.time()
        if no in has_name:
            res, err, msg = t.tool_zap(str(no))
            path = 'tv_zap'
            if err:
                log('ZAP_FAIL', seq=i + 1, input=no, path=path, err=str(err), msg=msg)
                aborted = 'tv_zap 报错'
                raise SystemExit(3)
        else:
            # 台号表 60 条里没有 100；走 06c4ef5 修的裸注入函数，实读仍要求 ==100
            ok, msg = t.zap_via_adb_digits(no)
            path = 'zap_via_adb_digits(raw)'
            if not ok:
                log('ZAP_FAIL', seq=i + 1, input=no, path=path, msg=msg)
                aborted = '裸注入报错'
                raise SystemExit(3)
        got = None
        for _ in range(3):
            time.sleep(1.0)
            got = read_no()
            if got == no:
                break
        cost = round(time.time() - t0, 2)
        good = (got == no)
        log('ZAP', seq=i + 1, input=no, read=got,
            name=channel_name_of_no(got) if got else None,
            path=path, cost_s=cost, ok=good)
        if not good:
            aborted = '实读台号 != 输入'
            log('ABORT', reason=aborted, seq=i + 1, input=no, read=got)
            raise SystemExit(3)

    # 最后一抽刚切完，频道信息条可能带真实 text，再 dump 一次争取「真实 text 命中」
    if aborted is None and time.time() < DEADLINE - 25:
        do_ui_dump('UIDUMP_AFTER_ZAP')

finally:
    r = restore()
    fin = state()
    fin['name'] = channel_name_of_no(fin['no']) if fin['no'] else None
    fin['restore'] = r
    fin['aborted'] = aborted
    fin['elapsed_s'] = round(time.time() - T0, 2)
    log('FINAL_STATE', **fin)
    dump()
