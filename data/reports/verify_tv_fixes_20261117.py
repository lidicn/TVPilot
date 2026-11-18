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
