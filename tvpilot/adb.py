"""TVPilot ADB 基础层。

绞杀者模式阶段3迁移：所有工具的底层依赖。
自包含：ADB_HOST 从环境变量读，不依赖主文件 config。
优化：持久化 shell 会话，避免每次启动新 adb 进程。

P0-A 分帧修复（2026-11-12）：
- 每命令唯一 marker（uuid4hex），整行精确匹配，杜绝固定串子串误判；
- 独立读线程 + queue.Queue + get(timeout=...) 真超时（不再用 readline 阻塞当超时判据）；
- 超时/异常一律 terminate + 排空 + 重启 shell（fail-fast，不把污染留给下一条）。
公开 API 保持不变：ADB / ADB_HOST / _CMD_LOCK / _adb_state /
adb_ready / _adb_argv / adb_cmd / adb_shell_output / tv_online。
"""
import os
import time
import shlex
import subprocess
import threading
import queue
import uuid

ADB_HOST = os.environ.get('TVPILOT_TV_ADB', '192.168.2.238:5555')
ADB = ['adb', '-s', ADB_HOST]

_CMD_LOCK = threading.RLock()
_adb_state = {'connected': False, 'last_check': 0.0}

# 持久化 shell 会话：一个进程 + 一个读线程 + 一个队列。
_shell_lock = threading.Lock()
_shell_session = None


def _new_marker():
    """每命令唯一 marker，整行精确比较，避免历史固定串或输出子串误判。"""
    return '__TVPILOT_%s__' % uuid.uuid4().hex


class _ShellSession:
    """一次持久 shell 会话；读线程只负责把字节行塞进队列，超时判定交给调用方。"""

    def __init__(self, proc):
        self.proc = proc
        self.q = queue.Queue()
        self.reader = threading.Thread(target=self._pump, name='adb-shell-reader', daemon=True)
        self.reader.start()

    def _pump(self):
        try:
            while True:
                raw = self.proc.stdout.readline()
                if not raw:
                    break
                self.q.put(raw)
        except Exception:
            pass
        finally:
            self.q.put(None)  # EOF 哨兵

    def alive(self):
        return self.proc.poll() is None

    def close(self):
        """terminate + 等待读线程退出 + 排空队列。"""
        try:
            self.proc.terminate()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=1)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self.reader.join(timeout=1)
        except Exception:
            pass
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass


def _ensure_session():
    """确保一个干净的持久会话；进程已死或超时被弃用则重建。"""
    global _shell_session
    with _shell_lock:
        s = _shell_session
        if s is not None and s.alive():
            return s
        if s is not None:
            _shell_session = None
            s.close()
        try:
            proc = subprocess.Popen(
                ADB + ['shell'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except Exception:
            return None
        _shell_session = _ShellSession(proc)
        return _shell_session


def _restart_session():
    """超时/异常后的 fail-fast 处置：弃用旧会话（terminate + 排空），并立即重建一个干净会话。"""
    global _shell_session
    with _shell_lock:
        old = _shell_session
        _shell_session = None
    if old is not None:
        old.close()
    return _ensure_session()


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

        # 如果连接成功，预热一个干净会话
        if ok:
            _ensure_session()


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
    """通过持久化 shell 执行命令并返回输出。

    分帧：写入 "<cmd>; echo <unique-marker>"，读线程把行塞进队列，调用方用
    q.get(timeout=remaining) 做真超时；超时/异常 -> terminate + 排空 + 重启。
    """
    s = _ensure_session()
    if s is None:
        return _run_shell_normal(cmd, timeout)

    marker = _new_marker()
    payload = ('%s; echo %s\n' % (cmd, marker)).encode('utf-8', errors='replace')
    deadline = time.time() + timeout
    out = []
    try:
        s.proc.stdin.write(payload)
        s.proc.stdin.flush()
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                _restart_session()
                raise subprocess.TimeoutExpired(cmd, timeout)
            try:
                raw = s.q.get(timeout=remaining)
            except queue.Empty:
                _restart_session()
                raise subprocess.TimeoutExpired(cmd, timeout)
            if raw is None:  # shell 意外退出
                _restart_session()
                raise OSError('adb shell closed unexpectedly')
            line = raw.decode('utf-8', errors='replace')
            if line.rstrip('\r\n') == marker:
                return ''.join(out)
            out.append(line)
    except (subprocess.TimeoutExpired, OSError):
        raise
    except Exception:
        _restart_session()
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
        try:
            _run_shell_persistent(cmd, timeout=timeout)
        except subprocess.TimeoutExpired:
            # fail-fast 已 terminate + 排空 + 重启；fire-and-forget 不向上抛。
            pass
    _adb_state['last_check'] = time.time()


def adb_shell_output(cmd, timeout=10):
    """执行 adb shell 命令并返回输出文本。超时 fail-fast 抛 TimeoutExpired。"""
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
