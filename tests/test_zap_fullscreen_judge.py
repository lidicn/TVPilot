"""阶段 2 判据 `iptv_fullscreen_playing()` 离线单测（不碰真机、零注入）。

AF-PM 11-12：IPTV app 不注册媒体会话，`dumpsys media_session` 恒为空，
⛔ 不能用它判播放态；改用 `dumpsys window windows` 的
`mCurrentFocus`（IPTV 前台）+ `mFrame` / `mDisplayFrame`（窗口全屏）。
ADB 已离线，按令**只留离线单测**，负向一律 mock，⛔ 不注入 HOME 去测它。

隔离纪律：tvpilot 子模块函数内 importlib 导入（AF-PM 11-12）。
用法：pytest tests/test_zap_fullscreen_judge.py -q
"""
import importlib
from unittest import mock

IPTV = 'com.android.smart.terminal.iptv'

# 合成 dumpsys window windows 文本（非真机读数，仅用于离线判据验证）
FULLSCREEN_IPTV = """WINDOW MANAGER WINDOWS (dumpsys window windows)
  mCurrentFocus=Window{abc123 u0 %s/com.amt.app.IPTVActivity}
  mFocusedApp=AppWindowToken{def456}
  Window #3 Window{ghi789 u0 %s/com.amt.app.IPTVActivity}:
    mDisplayId=0 stackId=1 type=1
    mFrame=[0,0][1920,1080] mLastFrame=[0,0][1920,1080]
    mDisplayFrame=[0,0][1920,1080]
""" % (IPTV, IPTV)


def test_positive_iptv_foreground_and_fullscreen():
    """正向：mCurrentFocus 含 IPTV 且 mFrame == mDisplayFrame → True，三行结构化读数齐。"""
    t = importlib.import_module('tvpilot.tools')
    with mock.patch.object(t, 'adb_shell_output', return_value=FULLSCREEN_IPTV) as sh, \
            mock.patch.object(t, 'adb_cmd') as adb:
        ok, detail = t.iptv_fullscreen_playing()
    assert ok is True, detail
    assert detail['iptv_foreground'] is True
    assert detail['mFrame'] == [0, 0, 1920, 1080]
    assert detail['mDisplayFrame'] == [0, 0, 1920, 1080]
    assert detail['fullscreen'] is True
    assert adb.call_count == 0, '判据只读，不许注入任何按键'
    assert sh.call_count == 1
    assert 'media_session' not in sh.call_args_list[0].args[0], '⛔ 不得用 media_session'


def test_negative_not_iptv_foreground():
    """负向：前台是 tvhome（非 IPTV）→ False；用离线 mock，不注入 HOME。"""
    t = importlib.import_module('tvpilot.tools')
    text = """WINDOW MANAGER WINDOWS (dumpsys window windows)
  mCurrentFocus=Window{aaa u0 com.mitv.tvhome/.MainActivityUserMode}
  Window #1 Window{bbb u0 com.mitv.tvhome/.MainActivityUserMode}:
    mFrame=[0,0][1920,1080] mDisplayFrame=[0,0][1920,1080]
"""
    with mock.patch.object(t, 'adb_shell_output', return_value=text), \
            mock.patch.object(t, 'adb_cmd') as adb:
        ok, detail = t.iptv_fullscreen_playing()
    assert ok is False
    assert detail['iptv_foreground'] is False
    assert adb.call_count == 0


def test_negative_not_fullscreen():
    """负向：IPTV 在前台但 mFrame != mDisplayFrame（导航页/分屏）→ False。"""
    t = importlib.import_module('tvpilot.tools')
    text = FULLSCREEN_IPTV.replace('mFrame=[0,0][1920,1080]',
                                   'mFrame=[0,0][960,1080]')
    with mock.patch.object(t, 'adb_shell_output', return_value=text), \
            mock.patch.object(t, 'adb_cmd') as adb:
        ok, detail = t.iptv_fullscreen_playing()
    assert ok is False
    assert detail['iptv_foreground'] is True
    assert detail['fullscreen'] is False
    assert adb.call_count == 0


def test_negative_frames_missing():
    """负向：dumpsys 里没有 mFrame / mDisplayFrame → False（fail-closed）。"""
    t = importlib.import_module('tvpilot.tools')
    text = ('WINDOW MANAGER WINDOWS (dumpsys window windows)\n'
            '  mCurrentFocus=Window{abc u0 %s/com.amt.app.IPTVActivity}\n' % IPTV)
    with mock.patch.object(t, 'adb_shell_output', return_value=text):
        ok, detail = t.iptv_fullscreen_playing()
    assert ok is False
    assert detail['mFrame'] is None and detail['mDisplayFrame'] is None


def test_negative_adb_failure_failclosed():
    """负向：adb_shell_output 抛异常 → False + reason，不把异常抛进换台流程。"""
    t = importlib.import_module('tvpilot.tools')
    with mock.patch.object(t, 'adb_shell_output', side_effect=OSError('device offline')), \
            mock.patch.object(t, 'adb_cmd') as adb:
        ok, detail = t.iptv_fullscreen_playing()
    assert ok is False
    assert detail['reason'] == 'dumpsys_window_unavailable'
    assert detail['error'] == 'OSError'
    assert adb.call_count == 0


def test_media_session_never_queried_even_when_present():
    """即便注入 media_session 文本，判据也只读 dumpsys window，且不被它带偏。"""
    t = importlib.import_module('tvpilot.tools')
    text = FULLSCREEN_IPTV + '\n  state=PlaybackState {state=3}\n'
    with mock.patch.object(t, 'adb_shell_output', return_value=text) as sh:
        ok, detail = t.iptv_fullscreen_playing()
    assert ok is True
    assert all('media_session' not in c.args[0] for c in sh.call_args_list)
    assert sh.call_args_list[0].args[0] == 'dumpsys window windows'
