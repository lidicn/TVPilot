# -*- coding: utf-8 -*-
"""AF ↔ TVPilot 联动 · D2 ADB 四元探针判定 + S5 判定面（契约 v2.2 §三/§五/§六）。

纯函数：所有取数结果由调用方注入（quad dict / HTTP 结果），⛔ 本模块不连
设备、不建 ADB 会话、不发 HTTP、不读凭据、不 sleep、无循环、无后台线程。
判定矩阵来源：doc/DESIGN-AF联动D1D2-C2四元探针与S5判定面-20261114.md §二/§三。

契约硬约束：
- unknown 必带 reason，⛔ 缺省/旧值冒充现值（§三通用语义）
- 基线只作分型证据，绝不冒充现值（§2.2）
- 探针无周期探测：一次调用只读一次四元、只写一个 last_checked（§2.3）
- S5 隐私：⛔ 读 /api/scan/latest，只判 /api/health（§3.3）
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# 四元（契约 §三 S2）
QUAD_FIELDS = ('adb_enabled_setting', 'adbd_alive', 'network_reachable', 'last_checked')

# 主值域（契约 §三 S2）
VALUE_OK = 'ok'
VALUE_OFF = 'off'
VALUE_UNKNOWN = 'unknown'

# reason 枚举（DESIGN §2.4，不扩契约）
REASON_SETTING_OFF = 'setting_off'
REASON_ADBD_DOWN = 'adbd_down'
REASON_SETTING_UNREADABLE = 'setting_unreadable'
REASON_NETWORK_UNREACHABLE = 'network_unreachable'
REASON_SHELL_UNAVAILABLE = 'shell_unavailable'
REASON_ETIOLOGY_UNDETERMINED = 'etiology_undetermined'
REASON_TV_OFF = 'tv_off'
REASON_TIMEOUT = 'timeout'
REASON_PROBE_ERROR = 'probe_error'

ADBD_RUNNING = 'running'
ADBD_STOPPED = 'stopped'


def classify_adb_health(quad: Optional[Dict[str, Any]],
                        power: str = 'on',
                        baseline: Optional[Dict[str, Any]] = None,
                        probe_error: Optional[str] = None) -> Dict[str, Any]:
    """四元病因判定矩阵（DESIGN §2.2 九条路径），返回 S2 主值 + attributes。

    :param quad: 现读四元 {network_reachable, adbd_alive, adb_enabled_setting, last_checked}
    :param power: S1 电源态；off/standby ⇒ 全 unknown(reason=tv_off)（§五）
    :param baseline: 息屏前四元基线，仅当 shell 不可用时作分型证据（⛔ 不冒充现值）
    :param probe_error: 探测异常/超时标记 ⇒ unknown(reason=timeout/probe_error) fail-closed
    :return: {'value': ok/off/unknown, 'reason': str|None, 'from_baseline': bool,
              'etiology_distinguishable': bool, 'quad': {...}}
    """
    empty = {
        'value': VALUE_UNKNOWN, 'reason': None, 'from_baseline': False,
        'etiology_distinguishable': False, 'quad': {},
    }

    # #9 探测异常／超时 ⇒ fail-closed
    if probe_error:
        r = dict(empty, reason=(REASON_TIMEOUT if probe_error == 'timeout'
                                else REASON_PROBE_ERROR))
        return r

    # #8 息屏 ⇒ 全状态 unknown(reason=tv_off)，不触发探针
    if power in ('off', 'standby'):
        return dict(empty, reason=REASON_TV_OFF)

    q = dict(quad or {})
    out = dict(empty, quad=q)
    net = q.get('network_reachable')
    adbd = q.get('adbd_alive')
    setting = q.get('adb_enabled_setting')

    # #7 网络不可达 ⇒ 病因不可分，⛔ 猜
    if net is False:
        return dict(out, reason=REASON_NETWORK_UNREACHABLE)

    # 以下 net is True 或未知；net 未知本身即病因不可分
    if net is not True:
        return dict(out, reason=REASON_ETIOLOGY_UNDETERMINED)

    def _from_baseline(o):
        """§2.2 可区分路径二：shell 不可用时依赖息屏前四元基线分型。
        基线只作证据、⛔ 冒充现值；无基线 ⇒ unknown(etiology_undetermined)。
        """
        b = baseline or {}
        b_setting = b.get('adb_enabled_setting')
        if b_setting is not None and str(b_setting) == '0':
            return dict(o, value=VALUE_OFF,
                        reason='%s(from_baseline)' % REASON_SETTING_OFF,
                        from_baseline=True, etiology_distinguishable=True)
        if b_setting is not None and str(b_setting) == '1':
            return dict(o, value=VALUE_OFF,
                        reason='%s(from_baseline)' % REASON_ADBD_DOWN,
                        from_baseline=True, etiology_distinguishable=True)
        return dict(o, reason=REASON_ETIOLOGY_UNDETERMINED)

    # adbd 仍在跑（shell 可用）：可直接分型；setting 读不出只在此处判 #6
    if adbd == ADBD_RUNNING:
        if str(setting) == '1':                       # #1 基线正常
            return dict(out, value=VALUE_OK, etiology_distinguishable=True)
        if str(setting) == '0':                       # #2 设置被关
            return dict(out, value=VALUE_OFF, reason=REASON_SETTING_OFF,
                        etiology_distinguishable=True)
        if setting is None or str(setting) == 'null':  # #6 setting 读不出
            return dict(out, reason=REASON_SETTING_UNREADABLE)
        return dict(out, reason=REASON_ETIOLOGY_UNDETERMINED)

    # adbd 死了且 setting 明确=1 ⇒ #3 直接分型；否则 shell 不可用 ⇒ 基线分型 #4
    if adbd == ADBD_STOPPED:
        if str(setting) == '1':
            return dict(out, value=VALUE_OFF, reason=REASON_ADBD_DOWN,
                        etiology_distinguishable=True)
        return _from_baseline(out)

    # adbd 不明：#5 明确 ⛔ 猜；其余（含 setting 不明）落基线分型
    if str(setting) == '1':
        return dict(out, reason=REASON_ETIOLOGY_UNDETERMINED)
    return _from_baseline(out)


# ---- S5 ArcFace 判定面（DESIGN §3.2）------------------------------------
S5_RUNNING = 'running'
S5_STOPPED = 'stopped'
S5_UNKNOWN = 'unknown'


def classify_arcface(http_result: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """S5 主判定：HTTP 结果由调用方注入，⛔ 本函数不发请求、不读 /api/scan/latest。

    http_result: {'status': int|None, 'body_ok': bool, 'error': str|None}
    error 取值：'refused' / 'timeout' / 'probe_error' / 'network_error'
    """
    r = dict(http_result or {})
    err = r.get('error')
    if err == 'probe_error' or err == 'network_error':
        return {'value': S5_UNKNOWN, 'reason': 'arcface_probe_error'}
    if err == 'timeout':
        return {'value': S5_STOPPED, 'reason': 'arcface_timeout'}
    if err == 'refused':
        return {'value': S5_STOPPED, 'reason': 'arcface_not_listening'}
    if r.get('status') == 200:
        if r.get('body_ok'):
            return {'value': S5_RUNNING, 'reason': ''}
        return {'value': S5_STOPPED, 'reason': 'arcface_unhealthy'}
    return {'value': S5_UNKNOWN, 'reason': 'arcface_probe_error'}


__all__ = [
    'QUAD_FIELDS', 'VALUE_OK', 'VALUE_OFF', 'VALUE_UNKNOWN',
    'REASON_SETTING_OFF', 'REASON_ADBD_DOWN', 'REASON_SETTING_UNREADABLE',
    'REASON_NETWORK_UNREACHABLE', 'REASON_SHELL_UNAVAILABLE',
    'REASON_ETIOLOGY_UNDETERMINED', 'REASON_TV_OFF', 'REASON_TIMEOUT',
    'REASON_PROBE_ERROR', 'ADBD_RUNNING', 'ADBD_STOPPED',
    'S5_RUNNING', 'S5_STOPPED', 'S5_UNKNOWN',
    'classify_adb_health', 'classify_arcface',
]
