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


def _mark_disconnected():
    """标记 ADB 断连：清掉 60s 信任窗口，强制 adb_ready() 下次走真实探测/重连。

    收口在 _run_shell_persistent 的异常出口，覆盖 adb_cmd / adb_shell_output 两条
    上层路径。⛔ 只改状态，不发任何设备命令（重连由 adb_ready 负责）。
    """
    _adb_state['connected'] = False
    _adb_state['last_check'] = 0.0


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
    except subprocess.TimeoutExpired:
        # 命令级超时：fail-fast 已 terminate + 排空 + 重启会话，连接仍在，
        # 不改 connected（超时≠掉线）。由调用方决定吞掉还是上抛。
        raise
    except OSError:
        # 持久 shell 意外退出（电视断电/重启/Wi-Fi 重连）：标记断连，清掉 60s
        # 信任窗口。收口在此，adb_cmd / adb_shell_output 两条上层路径同时受益。
        _mark_disconnected()
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
        except OSError:
            # 会话意外退出：_run_shell_persistent 已中心标记断连
            # （connected=False / last_check=0.0）。此处立即强制重探重连，
            # 让掉线后第一次调用就恢复链路；fire-and-forget 不把 OSError 冒泡
            # 到工具层，也不刷新 last_check 覆盖刚标记的断连状态。
            adb_ready(force=True)
            return
    _adb_state['last_check'] = time.time()


def adb_shell_output(cmd, timeout=10):
    """执行 adb shell 命令并返回输出文本。超时 fail-fast 抛 TimeoutExpired。"""
    adb_ready()
    try:
        with _CMD_LOCK:
            result = _run_shell_persistent(cmd, timeout=timeout)
    except OSError:
        # 读输出类工具必须知道读取失败 ⇒ 如实上抛；但状态已由
        # _run_shell_persistent 中心标记断连（connected=False / last_check=0.0），
        # 下一次调用 adb_ready() 会真实探测重连，链路自动恢复、不再卡死。
        # 立即 force 重探，缩短恢复窗口；仍 re-raise，不伪装成读成功。
        adb_ready(force=True)
        raise
    _adb_state['last_check'] = time.time()
    return result


def tv_online():
    """检测电视是否在线（ADB 能否连接）。"""
    try:
        adb_ready(force=True)
        return bool(_adb_state['connected'])
    except Exception:
        return False


def adb_host_ip():
    """从 ADB_HOST（host:port）取纯 IP，供真网络探针用。无端口则原样返回。"""
    host = ADB_HOST or ''
    return host.rsplit(':', 1)[0] if ':' in host else host


def network_ping(timeout=2):
    """真网络探针（2026-11-19 链主修法①）：ICMP ping 电视 IP，⛔ 不依赖 adb。

    与 tv_online() 的区别：tv_online 测的是「ADB 通不通」，电视把 ADB 开关关掉时
    必然 False，无法区分「ADB 关」与「网断/电视关机」。本函数只看网络层——只要
    电视 IP 能 ping 通就返回 True（含 ADB 关闭、电视开机的情形）。
    :return: bool。ping 不可用/异常一律 False（fail-closed，不假装网通）。
    """
    ip = adb_host_ip()
    if not ip:
        return False
    try:
        r = subprocess.run(['ping', '-c', '1', '-W', str(timeout), ip],
                           capture_output=True, timeout=timeout + 2)
        return r.returncode == 0
    except Exception:
        return False


__all__ = [
    'ADB_HOST', 'ADB', '_CMD_LOCK', '_adb_state',
    'adb_ready', '_adb_argv', 'adb_cmd', 'adb_shell_output', 'tv_online',
    'adb_host_ip', 'network_ping',
]
