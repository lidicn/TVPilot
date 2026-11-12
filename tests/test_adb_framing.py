"""P0-A 分帧窄面单测（离线，不碰真机/容器）。

用本机 Python 进程冒充 `adb shell`：把 tvpilot.adb.ADB 换成假 shell，
验证持久化 shell 的四条契约：
1. 超时命令不污染下一条（旧实现的 POLLUTED=True 应变为 False）；
2. 输出里出现历史固定 marker 子串不误判为帧结束（唯一 marker + 整行精确匹配）；
3. 并发下各命令各回各的，不串台；
4. 超时后会话被弃用并重建（fail-fast）。

用法：pytest tests/test_adb_framing.py -q
"""
import subprocess
import sys
import threading
import time

import pytest

from tvpilot import adb as adb_mod

# 假 shell：读 "<cmd>; echo <marker>"，按 cmd 前缀产生输出，最后回显 marker。
# SLOW:<秒> 故意睡过超时时间再吐一行 LATE_OUTPUT —— 旧实现会把这行留给下一条。
_FAKE_SHELL = r'''
import sys, time
for raw in sys.stdin:
    line = raw.rstrip("\r\n")
    if "; echo " not in line:
        continue
    cmd, marker = line.rsplit("; echo ", 1)
    if cmd.startswith("SLOW:"):
        time.sleep(float(cmd.split(":", 1)[1]))
        sys.stdout.write("LATE_OUTPUT\n")
    elif cmd.startswith("OUT:"):
        sys.stdout.write(cmd[4:] + "\n")
    sys.stdout.write(marker + "\n")
    sys.stdout.flush()
'''


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """每个用例都隔离：假 ADB、adb_ready 空操作、会话置空。"""
    monkeypatch.setattr(adb_mod, 'ADB', [sys.executable, '-u', '-c', _FAKE_SHELL])
    monkeypatch.setattr(adb_mod, 'adb_ready', lambda *a, **k: None)
    with adb_mod._shell_lock:
        old = adb_mod._shell_session
        adb_mod._shell_session = None
    if old is not None:
        old.close()
    yield
    with adb_mod._shell_lock:
        old = adb_mod._shell_session
        adb_mod._shell_session = None
    if old is not None:
        old.close()


def test_timeout_does_not_pollute_next_call():
    """call1 超时 -> call2 必读到 REAL_OUTPUT，且无 LATE_OUTPUT 残留。"""
    with pytest.raises(subprocess.TimeoutExpired):
        adb_mod.adb_shell_output('SLOW:2', timeout=0.3)
    out2 = adb_mod.adb_shell_output('OUT:REAL_OUTPUT', timeout=3)
    assert 'REAL_OUTPUT' in out2
    assert 'LATE_OUTPUT' not in out2
    assert '__TVPILOT_' not in out2  # marker 不应出现在返回体里


def test_old_fixed_marker_substring_is_not_a_frame_end():
    """输出行里含历史固定 marker 子串不能提前截断帧。"""
    out = adb_mod.adb_shell_output('OUT:__TVPILOT_CMD_DONE__', timeout=3)
    assert '__TVPILOT_CMD_DONE__' in out


def test_unique_marker_substring_is_not_a_frame_end():
    """唯一 marker 的子串也不能提前截断（整行精确匹配）。"""
    out = adb_mod.adb_shell_output('OUT:PREFIX__TVPILOT_deadbeef__SUFFIX', timeout=3)
    assert 'PREFIX__TVPILOT_deadbeef__SUFFIX' in out


def test_session_restarted_after_timeout():
    """超时 fail-fast：旧会话被弃用，重建的是新对象。"""
    before = adb_mod._ensure_session()
    assert before is not None
    with pytest.raises(subprocess.TimeoutExpired):
        adb_mod.adb_shell_output('SLOW:2', timeout=0.3)
    after = adb_mod._ensure_session()
    assert after is not before


def test_concurrent_calls_do_not_crosstalk():
    """并发下每条命令只拿到自己的输出。"""
    results = {}
    errors = []

    def worker(i):
        try:
            results[i] = adb_mod.adb_shell_output('OUT:TOKEN_%d' % i, timeout=5)
        except Exception as exc:  # 并发下任何异常都记下，避免线程静默吞掉
            errors.append((i, repr(exc)))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    for i in range(6):
        assert 'TOKEN_%d' % i in results.get(i, ''), 'missing TOKEN_%d' % i
    for i in range(6):
        for j in range(6):
            if i != j:
                assert 'TOKEN_%d' % j not in results[i], 'crosstalk %d<-%d' % (i, j)
    assert not errors


def test_normal_command_returns_clean_output():
    """正常命令输出不含 marker、不含残留。"""
    out = adb_mod.adb_shell_output('OUT:HELLO', timeout=3)
    assert out.strip() == 'HELLO'
