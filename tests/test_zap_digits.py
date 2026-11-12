"""恢复 v0.8.1 逐位注入 + 播放页守卫离线单测（不碰真机，全程零注入）。

AF-PM 11-12 判决：① zap_via_adb_digits 回到 v0.8.1（逐位独立 adb_cmd + 位间
sleep(0.15) + 末尾 sleep(0.3) + 单独 ENTER，⛔ && 链、⛔ 单次多参）；② tool_zap
两级守卫（fg 是 IPTV + 播放页就绪），播放页未就绪 fail-closed 返回 invalid_state
且零注入；③ 离线单测覆盖命令序列、位间 sleep、守卫零注入。

隔离纪律：tvpilot 子模块函数内 importlib 导入（AF-PM 11-12，避免假模块污染）。
用法：pytest tests/test_zap_digits.py -q
"""
import importlib
from unittest import mock


def test_zap_digits_v081_one_command_per_key_with_sleep_and_enter_last():
    """v0.8.1：N 条逐位独立 adb_cmd + 位间 sleep(0.15) + 末尾 sleep(0.3) + 单独 ENTER。"""
    t = importlib.import_module('tvpilot.tools')
    for no, n_cmds in ((50, 3), (401, 4), (21, 3)):
        sleeps = []
        with mock.patch.object(t.time, 'sleep', side_effect=lambda s: sleeps.append(s)), \
                mock.patch.object(t, 'adb_cmd') as adb:
            ok, msg = t.zap_via_adb_digits(no)
        assert ok, '台号 %s: %s' % (no, msg)
        seq = [c.args[0] for c in adb.call_args_list]
        assert len(seq) == n_cmds, '台号 %s -> %r' % (no, seq)
        assert all(c.startswith('input keyevent KEYCODE_') for c in seq), seq
        assert all(c.count('KEYCODE_') == 1 and '&&' not in c for c in seq), seq
        assert seq[-1] == 'input keyevent KEYCODE_ENTER'
        assert [c.rsplit('_', 1)[-1] for c in seq[:-1]] == list(str(no))
        n = len(str(no))
        assert sleeps == [0.15] * n + [0.3], '台号 %s sleeps=%r' % (no, sleeps)


def test_playback_page_ready_media_session_playing():
    """只读判据①：dumpsys media_session 出现 PlaybackState {state=3} 即判播放页就绪。"""
    t = importlib.import_module('tvpilot.tools')
    with mock.patch.object(t, 'adb_shell_output',
                           return_value='  state=PlaybackState {state=3, position=0}\n') as sh, \
            mock.patch.object(t.time, 'sleep') as sl:
        ok, info = t.wait_for_playback_page(timeout=20, poll_interval=1)
    assert ok is True, info
    assert info['signal'] == 'media_session:PLAYING'
    assert sl.call_count == 0, '首次探测就就绪，不该 sleep'
    assert sh.call_args_list[0].args[0] == 'dumpsys media_session'


def test_playback_page_ready_ui_dump_fallback():
    """只读判据②：media_session 无播放态时，ui_dump 出现播放控件也判就绪。"""
    t = importlib.import_module('tvpilot.tools')
    with mock.patch.object(t, 'adb_shell_output',
                           side_effect=['(no active session)',
                                        '<node text="暂停" resource-id="prog_progress"/>']):
        ok, signal = t._playback_page_ready()
    assert ok is True and signal == 'ui_dump:playback_controls'


def test_playback_page_ready_never_raises_on_adb_failure():
    """adb 异常一律按未就绪处理（fail-closed），不许把异常抛进换台流程。"""
    t = importlib.import_module('tvpilot.tools')
    with mock.patch.object(t, 'adb_shell_output', side_effect=OSError('adb offline')):
        assert t._playback_page_ready() == (False, 'no_playback_page_signal')


def test_wait_for_playback_page_times_out_without_injection():
    """等满 20s 仍无播放页 → (False, info)，且全程只读、零注入。"""
    t = importlib.import_module('tvpilot.tools')
    with mock.patch.object(t, '_playback_page_ready',
                           return_value=(False, 'no_playback_page_signal')), \
            mock.patch.object(t.time, 'sleep'), \
            mock.patch.object(t, 'adb_cmd') as adb, \
            mock.patch.object(t, 'adb_shell_output') as sh:
        ok, info = t.wait_for_playback_page(timeout=2, poll_interval=1)
    assert ok is False, info
    assert info['ready'] is False
    assert adb.call_count == 0, '播放页守卫不许注入任何按键'
    assert sh.call_count == 0, '本用例已在 _playback_page_ready 打桩'


def test_tool_zap_rejects_wrong_foreground_without_injection():
    """第 1 级守卫：fg 非 IPTV → invalid_state 且零注入（播放页探测都不该跑）。"""
    t = importlib.import_module('tvpilot.tools')
    with mock.patch.object(t, 'get_foreground_pkg',
                           return_value='com.mitv.tvhome/com.mitv.videoplayer.VideoPlayerActivity'), \
            mock.patch.object(t, 'wait_for_playback_page') as page, \
            mock.patch.object(t, 'zap_via_adb_digits', return_value=(True, 'ok')) as zap, \
            mock.patch.object(t, 'adb_cmd') as adb:
        res, err, msg = t.tool_zap('50')
    assert res is None
    assert err == 'invalid_state', (err, msg)
    assert 'IPTV 不在前台' in msg
    assert page.call_count == 0
    assert zap.call_count == 0
    assert adb.call_count == 0


def test_tool_zap_playback_page_not_ready_zero_injection():
    """第 2 级守卫（AF-PM 判决核心）：fg 是 IPTV 但播放页未就绪 → invalid_state、零注入。"""
    t = importlib.import_module('tvpilot.tools')
    info = {'ready': False, 'signal': 'no_playback_page_signal', 'polls': 21,
            'timeout_s': 20, 'wait_s': 20.0}
    with mock.patch.object(t, 'get_foreground_pkg',
                           return_value='com.android.smart.terminal.iptv/com.amt.app.IPTVActivity'), \
            mock.patch.object(t, 'wait_for_playback_page', return_value=(False, info)) as page, \
            mock.patch.object(t, 'zap_via_adb_digits', return_value=(True, 'ok')) as zap, \
            mock.patch.object(t, 'adb_cmd') as adb, \
            mock.patch.object(t, 'adb_shell_output') as sh:
        res, err, msg = t.tool_zap('50')
    assert res is None
    assert err == 'invalid_state', (err, msg)
    assert '播放页' in msg
    assert page.call_count == 1
    assert zap.call_count == 0
    assert adb.call_count == 0
    assert sh.call_count == 0


def test_tool_zap_injects_when_playback_page_ready():
    """两级守卫都过 → 放行 v0.8.1 逐位单键（N+1 条），不误拦。"""
    t = importlib.import_module('tvpilot.tools')
    info = {'ready': True, 'signal': 'media_session:PLAYING', 'wait_s': 0.4, 'polls': 1}
    with mock.patch.object(t, 'get_foreground_pkg',
                           return_value='com.android.smart.terminal.iptv/com.amt.app.IPTVActivity'), \
            mock.patch.object(t, 'wait_for_playback_page', return_value=(True, info)), \
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
