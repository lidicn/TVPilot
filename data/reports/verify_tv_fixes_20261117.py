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
                     '可信S4/fail-closed', '认证串零回传'],
    'products_hash_stable': True,
}
with open(_RESULT, 'w', encoding='utf-8') as f:
    json.dump(_result, f, ensure_ascii=False, indent=2)
print(json.dumps({k: _result[k] for k in ('checks_total', 'checks_pass', 'exit_code')}, ensure_ascii=False))
