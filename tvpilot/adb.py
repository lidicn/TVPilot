"""TVPilot ADB 基础层。

绞杀者模式阶段3迁移：所有工具的底层依赖。
自包含：ADB_HOST 从环境变量读，不依赖主文件 config。
优化：持久化 shell 会话，避免每次启动新 adb 进程。
"""
import os
import time
import shlex
import subprocess
import threading

ADB_HOST = os.environ.get('TVPILOT_TV_ADB', '192.168.2.238:5555')
ADB = ['adb', '-s', ADB_HOST]

_CMD_LOCK = threading.RLock()
_adb_state = {'connected': False, 'last_check': 0.0}

# 持久化 shell 会话
_shell_proc = None
_shell_lock = threading.Lock()
_SHELL_MARKER = '__TVPILOT_CMD_DONE__'


def _ensure_shell():
    """确保持久化 shell 进程已启动。"""
    global _shell_proc
    if _shell_proc and _shell_proc.poll() is None:
        return _shell_proc

    with _shell_lock:
        if _shell_proc and _shell_proc.poll() is None:
            return _shell_proc

        # 启动持久化 shell
        try:
            _shell_proc = subprocess.Popen(
                ADB + ['shell'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            # 等待 shell 就绪
            time.sleep(0.5)
        except Exception:
            _shell_proc = None
        return _shell_proc


def adb_ready(force=False):
    """确保 ADB 已连接。用 get-state 快速探测，已连则跳过 connect。"""
    now = time.time()
    if not force and _adb_state['connected'] and now - _adb_state['last_check'] < 60:
        return
    with _CMD_LOCK:
        ok = False
        try:
            st = subprocess.run(['adb', 'get-state'], capture_output=True, text=True, timeout=3)
            ok = 'device' in (st.stdout or '')
        except Exception:
            ok = False
        if not ok or force:
            try:
                r = subprocess.run(['adb', 'connect', ADB_HOST], capture_output=True,
                                   text=True, timeout=4)
                out = (r.stdout or '') + (r.stderr or '')
                ok = 'connected' in out.lower() or 'already' in out.lower()
            except Exception:
                ok = False
        _adb_state['connected'] = ok
        _adb_state['last_check'] = now

        # 如果连接成功，启动持久化 shell
        if ok:
            _ensure_shell()


def _adb_argv(cmd):
    """把命令字符串/列表转成设备 shell 参数。用 shlex 正确处理引号；容忍前导 'shell'。"""
    if isinstance(cmd, (list, tuple)):
        parts = [str(c) for c in cmd]
    else:
        parts = shlex.split(cmd, posix=True)
    if parts and parts[0] == 'shell':
        parts = parts[1:]
    return parts


def _run_shell_persistent(cmd, timeout=10):
    """通过持久化 shell 执行命令并返回输出。"""
    proc = _ensure_shell()
    if not proc:
        # 降级到普通方式
        return _run_shell_normal(cmd, timeout)

    try:
        # 写入命令 + 标记
        full_cmd = f"{cmd}; echo {_SHELL_MARKER}\n"
        proc.stdin.write(full_cmd.encode('utf-8', errors='replace'))
        proc.stdin.flush()

        # 读取输出，直到看到标记
        output = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue
            line_str = line.decode('utf-8', errors='replace')
            if _SHELL_MARKER in line_str:
                break
            output.append(line_str)

        return ''.join(output)
    except Exception:
        # 失败，重启 shell
        global _shell_proc
        try:
            if proc:
                proc.terminate()
        except Exception:
            pass
        _shell_proc = None
        return _run_shell_normal(cmd, timeout)


def _run_shell_normal(cmd, timeout=10):
    """普通方式执行命令（每次启动新进程）。"""
    r = subprocess.run(ADB + ['shell'] + _adb_argv(cmd),
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout or ''


def adb_cmd(cmd, timeout=30):
    """执行 adb shell 命令（串行化）。cmd 可含管道，由设备端 sh 解析。"""
    adb_ready()
    with _CMD_LOCK:
        _run_shell_persistent(cmd, timeout=timeout)
    _adb_state['last_check'] = time.time()


def adb_shell_output(cmd, timeout=10):
    """执行 adb shell 命令并返回输出文本。"""
    adb_ready()
    with _CMD_LOCK:
        result = _run_shell_persistent(cmd, timeout=timeout)
    _adb_state['last_check'] = time.time()
    return result


def tv_online():
    """检测电视是否在线（ADB 能否连接）。"""
    try:
        adb_ready(force=True)
        return bool(_adb_state['connected'])
    except Exception:
        return False


__all__ = [
    'ADB_HOST', 'ADB', '_CMD_LOCK', '_adb_state',
    'adb_ready', '_adb_argv', 'adb_cmd', 'adb_shell_output', 'tv_online',
]
