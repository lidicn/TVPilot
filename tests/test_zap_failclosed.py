"""B4 复盘新增纪律 A 的离线闸：表外号必须 fail-closed，且不注入任何按键。

背景（AF-PM 11-12）：B4 第 3 抽绕开 `tv_zap` 直接调 `zap_via_adb_digits(100)`
把 STB 弄进异常态；台号表 60 条里没有 100，属无效测试数据。
本单测锁死：表外号（及 `tv_zap` 判定「无对应频道」的号）只返回
`invalid_parameter`，绝不触发任何 ADB 按键注入。

用法：pytest tests/test_zap_failclosed.py -q
"""
from tvpilot import tools as t
from tvpilot.channels import NAMES, channel_no_of
from tvpilot.errors import ERR_INVALID_PARAMETER


def test_offtable_number_rejected_without_any_key_injection(monkeypatch):
    """100 不在台号表 / 260 无对应频道：都拒绝，且一个按键都不许下发。"""
    injected = []

    def _forbidden_injection(*a, **k):
        injected.append(a)
        raise AssertionError('表外号不应注入任何按键（纪律 A）')

    monkeypatch.setattr(t, 'zap_via_adb_digits', _forbidden_injection)
    monkeypatch.setattr(
        t, 'get_foreground_pkg',
        lambda *a, **k: 'com.android.smart.terminal.iptv')

    for no in ('100', '260'):
        res, err, msg = t.tool_zap(no)
        assert res is None, '%s 不应返回结果' % no
        assert err == ERR_INVALID_PARAMETER, '%s -> %r %s' % (no, err, msg)
        assert '无对应频道' in msg

    assert injected == [], 'fail-closed 路径竟然下发了按键: %r' % injected


def test_rerun_numbers_are_all_reachable_via_tv_zap():
    """重跑卡 50/401/121/21 必须在 NAMES 里能查到台号（不靠裸注入）。"""
    want = {'50': '广东卫视', '401': '广东4K超高清',
            '121': '广东体育', '21': '中央一台'}
    for no, expected_name in want.items():
        matched = [n for n in NAMES if str(channel_no_of(n)) == no]
        assert matched, '台号 %s 在 NAMES 里无对应频道' % no
        assert expected_name in matched, '%s -> %r' % (no, matched)
