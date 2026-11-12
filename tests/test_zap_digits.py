"""逐位单键换台 + 工具级前台守卫离线单测（不碰真机）。

隔离纪律：tvpilot 子模块函数内 importlib 导入（AF-PM 11-12，避免假模块污染）。
用法：pytest tests/test_zap_digits.py -q
"""
import importlib
from unittest import mock


def test_zap_digits_one_command_per_key_with_sleep_and_enter_last():
    """每个数字一条独立命令、共 N+1 条、注入前 sleep(0.5)、ENTER 最后一条。"""
    t = importlib.import_module('tvpilot.tools')
    sleeps = []
    with mock.patch.object(t.time, 'sleep', side_effect=lambda s: sleeps.append(s)), \
            mock.patch.object(t, 'adb_cmd') as adb:
        for no, n_cmds in ((50, 3), (401, 4), (21, 3)):
            adb.reset_mock()
            sleeps.clear()
            ok, msg = t.zap_via_adb_digits(no)
            assert ok, '台号 %s: %s' % (no, msg)
            seq = [c.args[0] for c in adb.call_args_list]
            assert len(seq) == n_cmds, '台号 %s -> %r' % (no, seq)
            assert all(c.startswith('input keyevent KEYCODE_') for c in seq)
            assert all(c.count('KEYCODE_') == 1 and '&&' not in c for c in seq), seq
            assert seq[-1] == 'input keyevent KEYCODE_ENTER'
            assert [c.rsplit('_', 1)[-1] for c in seq[:-1]] == list(str(no))
            assert len(sleeps) == n_cmds and all(s == 0.5 for s in sleeps), sleeps


def test_tool_zap_rejects_wrong_foreground_without_injection():
    """fg 非 IPTV → invalid_state 且零注入（守卫在工具层）。"""
    t = importlib.import_module('tvpilot.tools')
    with mock.patch.object(t, 'get_foreground_pkg',
                           return_value='com.mitv.tvhome/com.mitv.videoplayer.VideoPlayerActivity'), \
            mock.patch.object(t, 'zap_via_adb_digits', return_value=(True, 'ok')) as zap, \
            mock.patch.object(t, 'adb_cmd') as adb:
        res, err, msg = t.tool_zap('50')
    assert res is None
    assert err == 'invalid_state', (err, msg)
    assert 'IPTV 不在前台' in msg
    assert zap.call_count == 0
    assert adb.call_count == 0


def test_tool_zap_injects_when_iptv_foreground():
    """fg 是 IPTV → 守卫放行，走逐位单键（N+1 条），不误拦。"""
    t = importlib.import_module('tvpilot.tools')
    with mock.patch.object(t, 'get_foreground_pkg',
                           return_value='com.android.smart.terminal.iptv/com.amt.app.IPTVActivity'), \
            mock.patch.object(t.time, 'sleep'), \
            mock.patch.object(t, 'set_current_channel'), \
            mock.patch.object(t, 'adb_cmd') as adb:
        res, err, msg = t.tool_zap('50')
    assert err is None, msg
    assert res['channel_no'] == 50
    seq = [c.args[0] for c in adb.call_args_list]
    assert len(seq) == 3, seq
    assert [c.rsplit('_', 1)[-1] for c in seq[:-1]] == ['5', '0']
    assert seq[-1] == 'input keyevent KEYCODE_ENTER'
