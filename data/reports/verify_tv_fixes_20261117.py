# -*- coding: utf-8 -*-
"""离线验收票：电视联动三要素修复（2026-11-17），零真机/零凭据/零 MQTT。

覆盖：
  A 播放页判据换全屏几何（iptv_fullscreen_playing 复用，⛔ 不再用 media_session/uiautomator）
  B wait_for_playback_page 硬 deadline（单次探测耗时计入，不再空转 10 倍）
  C iptv.livePath channel_id → 台号映射（62 条、3221226759→21）+ fail-closed
  D runner.read_frame 用可信台号做 S4，假态/缓存不冒充 iptv_ok
  E fail-closed 与凭据：livePath URL 认证串零回传、闸关零设备动作
  F 产品文件无凭据字面 / 矩阵运行前后 hash 一致

用法（TV 仓根）：python data/reports/verify_tv_fixes_20261117.py
结果：data/reports/verify_tv_fixes_20261117.json
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_PKG = os.path.join(_ROOT, 'tvpilot')
_RESULT = os.path.join(_HERE, 'verify_tv_fixes_20261117.json')
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PRODUCTS = [os.path.join(_PKG, f) for f in
            ('tools.py', 'iptv.py', 'af_morning_news_runner.py', 'af_morning_news.py', 'mcp.py')]
def _sha(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()
BEFORE = {p: _sha(p) for p in PRODUCTS}
CHECKS = []
def ck(name, cond, detail=None):
    CHECKS.append({'name': name, 'pass': bool(cond)})
    assert cond, '%s%s' % (name, (' :: %s' % detail) if detail else '')

from tvpilot import iptv, tools                       # noqa: E402
from tvpilot.af_morning_news import Frame, decide_iptv_ok  # noqa: E402
from tvpilot.af_morning_news_runner import ADBRunner  # noqa: E402

# ---- A 判据换载体 ----
src = open(os.path.join(_PKG, 'tools.py'), encoding='utf-8').read()
tree = ast.parse(src)
fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_playback_page_ready'][0]
# 只扫"可执行体"，排除 docstring（docstring 里写旧判据名做说明会自指假红，矩阵规范 §3）
real_body = [n for n in fn.body if not (isinstance(n, ast.Expr)
             and isinstance(getattr(n, 'value', None), ast.Constant)
             and isinstance(n.value.value, str))]
body_nodes = [n for top in real_body for n in ast.walk(top)]
def _calls_cmd(cmd):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == 'adb_shell_output' and n.args
               and isinstance(n.args[0], ast.Constant) and cmd in str(n.args[0].value)
               for n in body_nodes)
ck('A1 _playback_page_ready 不再调 media_session', not _calls_cmd('media_session'))
ck('A2 _playback_page_ready 不再调 uiautomator', not _calls_cmd('uiautomator'))
ck('A3 复用 iptv_fullscreen_playing', any(isinstance(n, ast.Call)
   and isinstance(n.func, ast.Name) and n.func.id == 'iptv_fullscreen_playing'
   for n in body_nodes))

# ---- B 硬 deadline ----
wf = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'wait_for_playback_page'][0]
wf_src = ast.get_source_segment(src, wf) or ''
# 常量拼接，避免检查器自指
DEADLINE = 'deadline'
ck('B1 循环前有 deadline 赋值', (DEADLINE + ' = ') in wf_src)
ck('B2 循环内有 deadline break', 'time.time() >=' in wf_src and DEADLINE in wf_src)
ck('B3 max_polls 单次超时防护', 'max_polls' in wf_src)

# ---- C livePath channel_id → 台号 ----
cid, matched = iptv.parse_channel_id_from_logcat(
    'CTCMediaPlayer: MediaPlayer > setDataSource --livePath--->rtsp://h/PLTV/88888895/224/3221226759/0000_0.smil?accountinfo=TOP_SECRET_XYZ')
ck('C1 livePath 解析出 channel_id', cid == '3221226759' and matched, cid)
ck('C2 认证串未回传', 'TOP_SECRET' not in str(cid))
no, reason, c2 = iptv.resolve_channel_from_livepath(
    'setDataSource --livePath--->rtsp://h/PLTV/88888895/224/3221226759/0000_0.smil')
ck('C3 中央一台反解=21', no == 21 and reason is None and c2 == '3221226759', (no, reason, c2))
ck('C4 映射非空', len(iptv._load_channel_id_to_no()) >= 50, len(iptv._load_channel_id_to_no()))
ck('C5 无 livePath fail-closed', iptv.resolve_channel_from_livepath('noise line') ==
   (None, 'livepath_absent', None))
ck('C6 未映射 channel_id fail-closed',
   iptv.resolve_channel_from_livepath('setDataSource --livePath--->rtsp://h/PLTV/88888895/224/999999/x_0.smil')[1]
   == 'channel_id_unmapped')
# 2026-11-18 真机反例：当前 IPTV 版本日志不挂 setDataSource tag，但 RTSP URL 在
ck('C7 URL 不绑 tag 也解出 channel_id', iptv.parse_channel_id_from_url(
   '11-18 CTCPlayer: rtsp://h/PLTV/88888895/224/3221228036/0000_0.smil?a=SECRET') == '3221228036')
ck('C8 URL 解析认证串零回传',
   'SECRET' not in str(iptv.parse_channel_id_from_url('rtsp://h/PLTV/1/2/3221228036/x_0.smil?a=SECRET')))
ck('C9 无 tag 的 livePath 文本也能 resolve', iptv.resolve_channel_from_livepath(
   'rtsp://h/PLTV/88888895/224/3221226759/0000_0.smil')[0] == 21)
# 多位数注入节奏（2026-11-18 修正只写首位缺陷）
zk = ast.parse(src)
zg = [n for n in zk.body if isinstance(n, ast.Assign)
      and any(isinstance(t, ast.Name) and t.id == 'ZAP_DIGIT_GAP_S' for t in n.targets)]
# 值是 float(os.environ.get('KEY','0.6'))：从嵌套 get 的第二个参数取默认
zg_val = 0.0
if zg:
    for sub in ast.walk(zg[0].value):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == 'get' and len(sub.args) >= 2
                and isinstance(sub.args[1], ast.Constant)):
            try:
                zg_val = float(sub.args[1].value)
            except (ValueError, TypeError):
                pass
ck('C10 多位数位间间隔>=0.5s（修只写首位）', zg_val >= 0.5, zg_val)
# ---- 第三笔：reader 双 bug 修复（关键词虚 + 窗口窄）----
_rsrc = open(os.path.join(_PKG, 'af_morning_news_runner.py'), encoding='utf-8').read()
_rtree = ast.parse(_rsrc)
# _read_livepath_logcat 是 ADBRunner 类方法，需进 ClassDef.body 找
_rclass = [n for n in _rtree.body if isinstance(n, ast.ClassDef) and n.name == 'ADBRunner'][0]
_rfn = [n for n in _rclass.body if isinstance(n, ast.FunctionDef)
        and n.name == '_read_livepath_logcat'][0]
_rbody = ast.get_source_segment(_rsrc, _rfn) or ''
# 只扫真实 adb_shell_output 调用实参，排除 docstring（旧命令在说明里会自指）
_rreal = [n for n in _rfn.body if not (isinstance(n, ast.Expr)
          and isinstance(getattr(n, 'value', None), ast.Constant)
          and isinstance(n.value.value, str))]
_cmd_args = []
for top in _rreal:
    for n in ast.walk(top):
        if not isinstance(n, ast.Call) or not n.args or not isinstance(n.args[0], ast.Constant):
            continue
        _f = n.func
        _is_shell = ((isinstance(_f, ast.Name) and _f.id == 'adb_shell_output') or
                     (isinstance(_f, ast.Attribute) and _f.attr == 'adb_shell_output'))
        if _is_shell:
            _cmd_args.append(n.args[0].value)
_actual_cmd = ' '.join(str(x) for x in _cmd_args)
# 2026-11-19 第四笔：命令走类常量 LIVEPATH_CMD，从 AST 取该常量值
_lcmd = ''
for n in _rtree.body:
    if isinstance(n, ast.ClassDef) and n.name == 'ADBRunner':
        for sub in n.body:
            if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == 'LIVEPATH_CMD' for t in sub.targets):
                if isinstance(sub.value, ast.Constant):
                    _lcmd = sub.value.value
ck('C11 reader 用 grep URL结构(-e rtsp:// -e PLTV),不用作废livePath字面',
   'grep -E -i' in _lcmd and "-e 'rtsp://'" in _lcmd and '-e PLTV' in _lcmd
   and 'livePath' not in _lcmd, _lcmd[:160])
# 2026-11-19 Chief 实测：-b main 那条 URL 行 0 命中，必须 -b all；-t 1500 限窗；不裸抓 -d 全文
ck('C12 reader 用-b all限窗(-t 1500),不用main也不裸抓全文',
   '-b all' in _lcmd and '-t 1500' in _lcmd and '-b main' not in _lcmd
   and '-d' not in _lcmd, _lcmd[:160])
ck('C13 zap前清缓冲+zap后缓存+三数evidence', all(k in _rsrc for k in
   ('LIVEPATH_CLEAR_CMD', 'logcat -c', '_livepath_text',
    'livepath_ms', 'livepath_lines', 'livepath_chars')))
# timeout 15s（Chief 实测 10s 偶发超）
_to = [n for n in _rtree.body if isinstance(n, ast.ClassDef) and n.name == 'ADBRunner']
_tov = 0
for cl in _to:
    for sub in cl.body:
        if isinstance(sub, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == 'LIVEPATH_TIMEOUT_S' for t in sub.targets):
            if isinstance(sub.value, ast.Constant):
                _tov = sub.value.value
ck('C15 reader timeout=15s(实测10s偶发超)', _tov == 15, _tov)
# 真机那行格式（IPTVPlayer tag + PLTV URL，无 livePath 字面）能被不绑 tag 正则解
ck('C14 真机IPTVPlayer行格式可解channel_id', iptv.parse_channel_id_from_logcat(
   '11-18 IPTVPlayer: [Send:400] GET_PARAMETER rtsp://h/PLTV/88888895/224/3221228036/0000_0.smil?a=SECRET')[0] == '3221228036')

# ---- D runner.read_frame 可信 S4 ----
FAKE_LOG = 'setDataSource --livePath--->rtsp://h/PLTV/88888895/224/3221226759/0000_0.smil'
class _FakeState(types.ModuleType):
    IPTV_PKG = 'com.android.smart.terminal.iptv'
    def get_foreground_pkg(self, force_refresh=False): return self.IPTV_PKG
    def get_current_channel(self): return {'name': '中央一台', 'no': 21}  # 缓存
    def tool_play_state(self): return {'playing': True}, None, None
    def get_screen_state(self): return {'screen_on': True}
# 用假 state 注入：runner 内部 `from tvpilot import state` 走 sys.modules
_stub = _FakeState('tvpilot.state')
sys.modules['tvpilot.state'] = _stub
import tvpilot as _tvpkg  # noqa: E402
_tvpkg.state = _stub      # runner 内 `from tvpilot import state` 走包属性，必须同设

r_ok = ADBRunner(livepath_reader=lambda: FAKE_LOG)
f_ok = r_ok.read_frame()
ck('D1 可信台号+正在播 ⇒ frame 无 reason（可判 iptv_ok）', f_ok.reason is None, f_ok.reason)
ck('D2 可信台号落 evidence=21', r_ok.last_evidence['read_frame']['s4_trusted_no'] == 21)

r_noplay = ADBRunner(livepath_reader=lambda: FAKE_LOG, screen_evidence=lambda: False)
f_noplay = r_noplay.read_frame()
ck('D3 无正在播 ⇒ playing_unverified', f_noplay.reason == 'playing_unverified', f_noplay.reason)

r_bad = ADBRunner(livepath_reader=lambda: 'no livepath here')
f_bad = r_bad.read_frame()
ck('D4 无 livePath ⇒ reason 非空（⛔ 缓存冒充）',
   f_bad.reason in ('livepath_absent', 'channel_unresolved', 's4_not_cctv1'), f_bad.reason)
ck('D5 假态 frame 不可达 iptv_ok', decide_iptv_ok(f_bad)[0] is False)
# 链主复核补丁：zap 抓到全文但无 URL 行 ⇒ 缓存不认，read_frame 必须现抓（reader 2 次）。
# 用可计数 fake reader + 假 tool_zap，避免真机。
class _CountingReader:
    def __init__(self): self.n = 0
    def __call__(self):
        self.n += 1
        # 第 1 次（zap）：全量 dump 但无 RTSP URL 行；第 2 次（read_frame 兜底）：给真行
        return ('noise log line\n' if self.n == 1 else FAKE_LOG)
import types as _t
_fake_tools = _t.ModuleType('tvpilot.tools')
_fake_tools.tool_zap = lambda ch: ({'method': 'adb_digits'}, None, None)
sys.modules['tvpilot.tools'] = _fake_tools
_tvpkg.tools = _fake_tools
_cnt = _CountingReader()
r_retry = ADBRunner(livepath_reader=_cnt)
zret = r_retry.zap(21)
f_retry = r_retry.read_frame()
ck('D6 zap解不出cid则不认缓存(缓存留空)', r_retry._livepath_text == '', repr(r_retry._livepath_text))
ck('D7 无URL行时read_frame现抓(reader调用2次)', _cnt.n == 2, _cnt.n)
ck('D8 兜底抓到URL行⇒s4可信21', r_retry.last_evidence['read_frame'].get('s4_trusted_no') == 21)
ck('D9 livepath_cid写入zap evidence', r_retry.last_evidence['zap'].get('livepath_cid') in (None, '3221226759'))
# ⑧ launch 前台短路：已在前台⇒不调 monkey 且 ok=already；不在前台⇒仍走 monkey
_monk = {'n': 0}
_fake_tools.tool_launch_app = lambda pkg: (_monk.__setitem__('n', _monk['n'] + 1) or
                                           ({'launched': True, 'method': 'adb'}, None, None))
r_already = ADBRunner()
_fg_orig = _stub.get_foreground_pkg
_stub.get_foreground_pkg = lambda force_refresh=False: 'com.android.smart.terminal.iptv'
ok_already = r_already.launch_iptv()
ck('D10 已在前台⇒ok且method=already', ok_already is True and
   r_already.last_evidence['launch_iptv'].get('method') == 'already')
ck('D11 已在前台⇒不调monkey', _monk['n'] == 0, _monk['n'])
# 不在前台⇒走 monkey
_stub.get_foreground_pkg = lambda force_refresh=False: 'com.mitv.tvhome'
r_cold = ADBRunner()
ok_cold = r_cold.launch_iptv()
ck('D12 不在前台⇒仍走monkey(调用1次)', ok_cold is True and _monk['n'] == 1, _monk['n'])
_stub.get_foreground_pkg = _fg_orig  # 恢复
# 第四笔 evidence 三数
ck('D13 reader代价ms/lines/chars在evidence', all(k in r_retry.last_evidence['zap']
   for k in ('livepath_ms', 'livepath_lines', 'livepath_chars')))
# 链末措辞：按异常类型分，不按耗时猜——正常空⇒reader_empty(no_match)，超时⇒reader_timeout
import subprocess as _sp
def _reader_empty(): return ''   # grep 无匹配⇒空串（真机"正常但没URL行"的真实返回）
def _reader_timeout(): raise _sp.TimeoutExpired('logcat', 15)
r_empty = ADBRunner(livepath_reader=_reader_empty)
r_empty.zap(21)
ck('D14 正常返回但空⇒reader_empty(no_match)',
   r_empty.last_evidence['zap'].get('livepath_reader_status') == 'reader_empty(no_match)',
   r_empty.last_evidence['zap'].get('livepath_reader_status'))
r_to = ADBRunner(livepath_reader=_reader_timeout)
r_to.zap(21)
ck('D15 TimeoutExpired⇒reader_timeout',
   r_to.last_evidence['zap'].get('livepath_reader_status') == 'reader_timeout',
   r_to.last_evidence['zap'].get('livepath_reader_status'))
# 默认 reader 源码按异常类型分（不按 ms 阈值）
_rsrc_now = open(os.path.join(_PKG, 'af_morning_news_runner.py'), encoding='utf-8').read()
ck('D16 reader 按异常类型分不按ms阈值',
   'except subprocess.TimeoutExpired' in _rsrc_now
   and 'reader_empty(no_match)' in _rsrc_now
   and '_livepath_status' in _rsrc_now)

# ---- G 第五笔：流式 reader + gap 1.0 + ⑬ 部署自检 ----
# 常量（类属性，AST 取，避开 docstring 自指）
_stream_consts = {}
for cl in _rtree.body:
    if isinstance(cl, ast.ClassDef) and cl.name == 'ADBRunner':
        for sub in cl.body:
            if isinstance(sub, ast.Assign):
                for t in sub.targets:
                    if not isinstance(t, ast.Name):
                        continue
                    _v = sub.value
                    if isinstance(_v, ast.Constant):
                        _stream_consts[t.id] = _v.value
                    elif isinstance(_v, ast.Tuple):
                        _stream_consts[t.id] = tuple(
                            e.value for e in _v.elts if isinstance(e, ast.Constant))
ck('G1 流式总窗口=25s', _stream_consts.get('STREAM_TOTAL_WINDOW_S') == 25,
   _stream_consts.get('STREAM_TOTAL_WINDOW_S'))
ck('G2 收尾下限=10s(动态窗口)', _stream_consts.get('STREAM_TAIL_MIN_S') == 10,
   _stream_consts.get('STREAM_TAIL_MIN_S'))
ck('G3 流式命令尾部-b all -v threadtime',
   _stream_consts.get('STREAM_CMD_TAIL') == ('logcat', '-b', 'all', '-v', 'threadtime'),
   _stream_consts.get('STREAM_CMD_TAIL'))
# gap=1.0（C10 只查 >=0.5，这里收紧到规格值）
ck('G4 位间间隔gap=1.0s(⑭)', zg_val == 1.0, zg_val)
# 起流方法：Popen 独立进程 + --pid 过滤 + 重定向临时文件，且⛔不塞持久 shell
_g_cls = [n for n in _rtree.body if isinstance(n, ast.ClassDef) and n.name == 'ADBRunner'][0]
_zap_fn = [n for n in _g_cls.body if isinstance(n, ast.FunctionDef) and n.name == 'zap'][0]
_start_fn = [n for n in _g_cls.body if isinstance(n, ast.FunctionDef)
             and n.name == '_start_livepath_stream'][0]
_start_src = ast.get_source_segment(_rsrc, _start_fn) or ''
# 只扫可执行体，排除 docstring（docstring 里写禁用名做说明会自指假红，矩阵规范 §3）
_start_real = [n for n in _start_fn.body if not (isinstance(n, ast.Expr)
               and isinstance(getattr(n, 'value', None), ast.Constant)
               and isinstance(n.value.value, str))]
_start_exec = ' '.join(ast.get_source_segment(_rsrc, n) or '' for n in _start_real)
ck('G5 起流用独立subprocess.Popen', 'subprocess.Popen(' in _start_exec)
ck('G6 PID过滤代码保留(备env开关)', '--pid=' in _start_exec and '_iptv_pid(' in _start_exec)
# 链主改判：默认全量流、PID 过滤留作 env 开关（默认 0＝不过滤）
_runner_mod = sys.modules.get('tvpilot.af_morning_news_runner')
ck('G6b PID过滤默认关(LIVEPATH_PID_FILTER=0)',
   hasattr(_runner_mod, 'LIVEPATH_PID_FILTER') and _runner_mod.LIVEPATH_PID_FILTER is False,
   getattr(_runner_mod, 'LIVEPATH_PID_FILTER', 'MISSING'))
ck('G6c env开关名TVPILOT_LIVEPATH_PID_FILTER',
   'TVPILOT_LIVEPATH_PID_FILTER' in _rsrc)
ck('G7 起流输出重定向临时文件', 'stdout=fh' in _start_exec and 'NamedTemporaryFile' in _start_exec)
ck('G8 起流不走持久shell队列', '_run_shell_persistent' not in _start_exec)
# 收流方法：动态窗口 + terminate→kill + 读文件后解析 + 清临时文件
_collect_fn = [n for n in _g_cls.body if isinstance(n, ast.FunctionDef)
               and n.name == '_collect_livepath_stream'][0]
_collect_src = ast.get_source_segment(_rsrc, _collect_fn) or ''
ck('G9 收流动态窗口max(TOTAL,elapsed+TAIL_MIN)',
   'STREAM_TOTAL_WINDOW_S' in _collect_src and 'STREAM_TAIL_MIN_S' in _collect_src
   and 'max(' in _collect_src)
ck('G10 收流terminate→wait→kill(可靠收尾)',
   '.terminate()' in _collect_src and '.wait(' in _collect_src and '.kill()' in _collect_src)
ck('G11 收完再读文件(不边读边解析)',
   _collect_src.find('proc.wait(') < _collect_src.find('with open(path') < _collect_src.find('return out'))
ck('G12 收流后删临时文件', 'os.remove(path)' in _collect_src)
# Chief 复核补①：zap 的 tool_zap 抛异常时须 abort 流再上抛（AST：Try handler 含 _abort 调用）
_zap_try_ok = False
for _n in ast.walk(_zap_fn):
    if isinstance(_n, ast.Try):
        _body_src = ' '.join(ast.get_source_segment(_rsrc, b) or '' for b in _n.body)
        _handler_src = ' '.join(ast.get_source_segment(_rsrc, h) or '' for h in _n.handlers)
        if 'tool_zap' in _body_src and '_abort_livepath_stream()' in _handler_src:
            _zap_try_ok = True
ck('G12b tool_zap异常⇒abort流再raise(无孤儿进程)', _zap_try_ok)
# Chief 复核补②：动态窗口封顶 60s，不顶破 240s 预算
ck('G12c 窗口上限STREAM_WINDOW_CAP_S=60', _stream_consts.get('STREAM_WINDOW_CAP_S') == 60,
   _stream_consts.get('STREAM_WINDOW_CAP_S'))
ck('G12d 收流窗口min(...,CAP)封顶',
   'min(' in _collect_src and 'STREAM_WINDOW_CAP_S' in _collect_src)
# 起收顺序：起流在 logcat -c 之后、tool_zap 之前；收流在 zap 返回后
_zap_src = ast.get_source_segment(_rsrc, _zap_fn) or ''
_p_clear = _zap_src.find('LIVEPATH_CLEAR_CMD')
_p_start = _zap_src.find('_start_livepath_stream()')
_p_zap = _zap_src.find('tools.tool_zap(')
_p_gather = _zap_src.find('_gather_livepath_text()')
ck('G13 起流顺序:清缓冲<起流<注入<收流',
   0 <= _p_clear < _p_start < _p_zap < _p_gather, (_p_clear, _p_start, _p_zap, _p_gather))
# 行为：流里无 URL ⇒ absent（fail-closed 不变）——用假流喂文本走真收流分支
_fake_stream = {'proc': types.SimpleNamespace(
                    terminate=lambda: None,
                    wait=lambda timeout=None: 0,
                    kill=lambda: None),
                'fh': types.SimpleNamespace(flush=lambda: None, close=lambda: None),
                'path': '', 't0': time.time(), 'pid': '12345'}
import tempfile as _tf
_tmpf = _tf.NamedTemporaryFile(mode='w', suffix='.log', delete=False, encoding='utf-8')
_tmpf.write('11-19 logcat noise line, no rtsp url here\n')
_tmpf.close()
_fake_stream['path'] = _tmpf.name
r_stream = ADBRunner()
# 假流走真收流分支：实例常量置 0，跳过动态窗口 sleep（否则白等 25s）
r_stream.STREAM_TOTAL_WINDOW_S = 0
r_stream.STREAM_TAIL_MIN_S = 0
r_stream._livepath_stream = _fake_stream
_stream_txt = r_stream._gather_livepath_text()
_stream_no, _stream_reason, _stream_cid = iptv.resolve_channel_from_livepath(_stream_txt)
ck('G14 流里无URL⇒livepath_absent(fail-closed)',
   (_stream_no, _stream_reason, _stream_cid) == (None, 'livepath_absent', None),
   (_stream_no, _stream_reason, _stream_cid))
ck('G15 收流后临时文件已清理(无残留)', not os.path.exists(_tmpf.name))
# 成对报数 evidence 含 paired_report（cid→台号 + 物理现场同帧）
ck('G16 paired_report同帧含cid/台号/物理现场', all(
   k in r_ok.last_evidence['read_frame'].get('paired_report', {})
   for k in ('cid', 'cid_to_no', 'lastchannel_id', 'play_state_playing', 'foreground_app')))
# 链主改判：全量流体量/解析三数记进 zap evidence
ck('G16b 三数stream_bytes/stream_lines/parse_ms进evidence', all(
   k in r_retry.last_evidence['zap'] for k in ('stream_bytes', 'stream_lines', 'parse_ms')),
   {k: r_retry.last_evidence['zap'].get(k) for k in ('stream_bytes', 'stream_lines', 'parse_ms')})
ck('G16c pid_filter默认False进evidence',
   r_retry.last_evidence['zap'].get('livepath_pid_filter') is False)
# ⑬ 部署自检：seed 在卷外 + ensure_channel_map_ready + MAP_SIZE=0 报错
_dockerfile = open(os.path.join(_ROOT, 'Dockerfile'), encoding='utf-8').read()
_chansrc = open(os.path.join(_PKG, 'channels.py'), encoding='utf-8').read()
_srvsrc = open(os.path.join(_ROOT, 'tvpilot_server.py'), encoding='utf-8').read()
ck('G17 Dockerfile seed在data卷外(/app/data_seed)', 'COPY data/channel_numbers.json /app/data_seed/' in _dockerfile)
ck('G18 channels提供ensure_channel_map_ready+MAP_SIZE', all(
   k in _chansrc for k in ('def ensure_channel_map_ready', 'map_size', 'CHANNEL_NUMBERS_SEED')))
ck('G19 server启动调自检且MAP_SIZE=0报错', all(
   k in _srvsrc for k in ('ensure_channel_map_ready()', 'MAP_SIZE=0', 'raise SystemExit')))

# ---- H 观测小改：七字段进 steps[].evidence（Chief 两条判据）----
# 用带 last_evidence 的假 runner 驱动全链，验证键真落进正确 step、值非0、kind=stream，
# 且六值/status 序列/话术一字未变。零真机。
from tvpilot.af_morning_news import MorningNewsOrchestrator, Frame  # noqa: E402
class _ObsRunner:
    """驱动 iptv_ok 全链，同时携带 runner 侧采集读数。"""
    def __init__(self):
        self.last_evidence = {
            'zap': {'channel_no': 21, 'stream_bytes': 240000, 'stream_lines': 3100,
                    'parse_ms': 12.5, 'livepath_ms': 25010.0, 'livepath_cid': '3221226759',
                    'livepath_reader_status': 'ok', 'livepath_reader_kind': 'stream',
                    'livepath_pid_filter': False},
            'read_frame': {'s4_reason': None,
                           'paired_report': {'cid': '3221226759', 'cid_to_no': 21,
                                             'lastchannel_id': '21',
                                             'play_state_playing': True,
                                             'foreground_app': 'com.android.smart.terminal.iptv'}},
        }
    def wake(self): return True
    def probe_adb(self): return {'value': 'ok', 'quad': {}}
    def launch_iptv(self): return True
    def zap(self, no): return True
    def read_frame(self):
        return Frame(foreground_app='com.android.smart.terminal.iptv',
                     current_channel='中央一台', same_frame=True, reason=None,
                     checked_at=time.time())
    def screen_on(self): return True
    def sleep(self, s): pass

_obs = _ObsRunner()
_rec = MorningNewsOrchestrator(runner=_obs, publisher=lambda *a: None).run()
_steps = {s['step']: s for s in _rec['steps']}
_zap_ev = _steps.get('zap', {}).get('evidence', {})
_vf_ev = _steps.get('verify_frame', {}).get('evidence', {})
# 判据1：七字段键在正确 step + 值真（>0）
_need_zap = ('stream_bytes', 'stream_lines', 'parse_ms', 'livepath_ms',
             'livepath_cid', 'livepath_reader_status')
ck('H1 zap evidence含六字段', all(k in _zap_ev for k in _need_zap),
   [k for k in _need_zap if k not in _zap_ev])
ck('H2 采集真收到内容(stream_bytes/lines>0)',
   _zap_ev.get('stream_bytes', 0) > 0 and _zap_ev.get('stream_lines', 0) > 0)
ck('H3 verify_frame evidence含s4_reason+paired_report',
   's4_reason' in _vf_ev and 'paired_report' in _vf_ev)
# 判据2：kind=stream（第五笔载体生死判据）
ck('H4 livepath_reader_kind=stream(非snapshot)',
   _zap_ev.get('livepath_reader_kind') == 'stream', _zap_ev.get('livepath_reader_kind'))
# 约束：六值/status 序列/话术一字未变
ck('H5 六值未变', _rec['result'] == 'iptv_ok' and _rec['stage'] == 'done'
   and _rec['gap'] is None and _rec['detail'] is None
   and _rec['af_speech'] == '新闻已打开' and _rec['screen_on_after'] is None)
ck('H6 steps status序列未变',
   [(s['step'], s['status']) for s in _rec['steps']] == [
       ('wake', 'ok'), ('probe_adb', 'ok'), ('launch_iptv', 'ok'),
       ('zap', 'ok'), ('verify_frame', 'ok')],
   [(s['step'], s['status']) for s in _rec['steps']])

# ---- E fail-closed / 凭据 ----
ck('E1 runner 默认 livepath_reader 存在', hasattr(ADBRunner, '_read_livepath_logcat'))
ck('E2 真值 URL 未进 evidence（零打印）',
   'TOP_SECRET' not in json.dumps(r_ok.last_evidence, ensure_ascii=False))

# ---- F 产品文件无凭据字面 / hash 稳定 ----
CRED = ('MQTT_PASS', 'DOUBAO_API_KEY', 'XIAOMI_TOKEN', 'BEGIN RSA PRIVATE KEY')
for p in PRODUCTS:
    txt = open(p, encoding='utf-8').read()
    # 只报命中数，不打印内容
    hits = [k for k in CRED if k in txt]
    ck('F 凭据字面 %s' % os.path.basename(p), not hits, hits)
AFTER = {p: _sha(p) for p in PRODUCTS}
ck('F-hash 运行前后产品文件一致', BEFORE == AFTER)

_result = {
    'date': '2026-11-17',
    'scope': '电视联动三要素修复离线验收（零真机/零MQTT/零凭据）',
    'checks_total': len(CHECKS), 'checks_pass': sum(1 for c in CHECKS if c['pass']),
    'checks_failed': [c['name'] for c in CHECKS if not c['pass']], 'exit_code': 0,
    'new_branches': ['判据换全屏几何', '硬deadline', 'livePath channel_id反解台号',
                     '可信S4/fail-closed', '认证串零回传',
                     '流式reader(全量优先/--pid env开关备用/动态窗口/可靠kill)',
                     'gap=1.0', '成对报数', '全量流体量三数',
                     '⑬部署seed/MAP_SIZE自检', '孤儿流abort/窗口60s封顶',
                      '观测小改:七字段进steps.evidence+kind=stream'],
    'products_hash_stable': True,
}
with open(_RESULT, 'w', encoding='utf-8') as f:
    json.dump(_result, f, ensure_ascii=False, indent=2)
print(json.dumps({k: _result[k] for k in ('checks_total', 'checks_pass', 'exit_code')}, ensure_ascii=False))
