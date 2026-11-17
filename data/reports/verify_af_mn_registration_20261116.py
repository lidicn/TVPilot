# -*- coding: utf-8 -*-
"""离线验收票：af_morning_news 注册 + 真机 runner（零真机/零 MQTT/零凭据）。

出资人 2026-11-16 A 窗口备料自检（offline-assertion-matrix 规范）：
  A 组 工具表 30 枚顶闸 / description≤80
  B 组 tools/list 键形对齐契约 v3 §1.1（唯一入参 request_id、非必填）
  C 组 闸关 ⇒ tools/call 零设备副作用：wake_fail(device_action_blocked)，显式失败非静默
  D 组 双闸：单置 RUNNER 不置 ARMED 仍显式失败（单闸无效）
  E 组 ADBRunner 协议方法齐
  F 组 AST 边界：runner 顶层不 import 设备模块、无 while True 无限轮询、不读凭据
  G 组 AST 边界：mcp handler 双闸为 and 连接的两个环境变量常量
  H 组 矩阵运行前后产品文件 hash 一致（矩阵不写产品代码）

用法（TV 仓根）：python data/reports/verify_af_mn_registration_20261116.py
结果落盘：data/reports/verify_af_mn_registration_20261116.json
⛔ 脚本只用假 tvpilot 子模块绕开 _ensure_tvpilot() 的设备依赖；F/G 扫的是产品源码
   AST（不是脚本自身），needle 用常量拼接与检查逻辑分开，避免自指假红。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))       # E:\NAS\TVPilot
_PKGDIR = os.path.join(_ROOT, 'tvpilot')
_RESULT = os.path.join(_HERE, 'verify_af_mn_registration_20261116.json')

PRODUCTS = (
    os.path.join(_PKGDIR, 'mcp.py'),
    os.path.join(_PKGDIR, 'af_morning_news.py'),
    os.path.join(_PKGDIR, 'af_morning_news_runner.py'),
)


def _sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


BEFORE = {p: _sha(p) for p in PRODUCTS}

CHECKS = []


def ck(name, cond, detail=None):
    CHECKS.append({'name': name, 'pass': bool(cond)})
    assert cond, '%s%s' % (name, (' :: %s' % detail) if detail else '')


# ---- 构造最小 tvpilot 包：__path__ 指向真目录（好让 mcp.py / af_morning_news.py /
#      af_morning_news_runner.py 从工作树真文件加载），仅把 _ensure_tvpilot() 会 import
#      的 4 个「可能连设备/缺依赖」模块做成假的。 ----
for _k in ('TVPILOT_MN_ARMED', 'TVPILOT_MN_RUNNER'):
    os.environ.pop(_k, None)                          # C 组：双闸默认关
pkg = types.ModuleType('tvpilot')
pkg.__path__ = [_PKGDIR]
sys.modules['tvpilot'] = pkg
for _sub in ('tools', 'state', 'screenshot', 'blueprint'):
    _m = types.ModuleType('tvpilot.' + _sub)
    sys.modules['tvpilot.' + _sub] = _m
    setattr(pkg, _sub, _m)

import tvpilot.mcp as mcp                             # noqa: E402

# ---- A 组：工具表 ----
ck('A1 MAX_TOOLS==30', mcp.MAX_TOOLS == 30, mcp.MAX_TOOLS)
ck('A2 len(TOOLS)==30 正好顶闸', len(mcp.TOOLS) == 30, len(mcp.TOOLS))
_names = [t['name'] for t in mcp.TOOLS]
ck('A3 af_morning_news 注册恰好 1 枚', _names.count('af_morning_news') == 1)
ck('A4 工具名无重复', len(set(_names)) == len(_names))
ck('A5 全部 description<=80', all(len(t['description']) <= 80 for t in mcp.TOOLS))

# ---- B 组：tools/list 键形（契约 v3 §1.1） ----
_r1 = mcp.handle_message({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
_tools = _r1['result']['tools']
ck('B1 tools/list 返回 30 枚', len(_tools) == 30, len(_tools))
_mn = [t for t in _tools if t['name'] == 'af_morning_news'][0]
_props = _mn['inputSchema']['properties']
ck('B2 唯一入参 request_id', set(_props) == {'request_id'}, list(_props))
ck('B3 required 为空（request_id 可省）', _mn['inputSchema']['required'] == [])
ck('B4 request_id 类型 string', _props['request_id']['type'] == 'string')


def _call():
    _r = mcp.handle_message({
        'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
        'params': {'name': 'af_morning_news', 'arguments': {'request_id': 'offline-probe'}},
    })
    assert 'error' not in _r, _r
    return json.loads(_r['result']['content'][0]['text'])


# ---- C 组：闸关 ⇒ 零设备副作用、显式失败 ----
_rec = _call()
ck('C1 闸关 => wake_fail', _rec['result'] == 'wake_fail', _rec.get('result'))
ck('C2 显式失败 device_action_blocked（非静默跳过）',
   _rec['detail'] == 'device_action_blocked', _rec.get('detail'))
ck('C3 stage=failed', _rec['stage'] == 'failed', _rec.get('stage'))
ck('C4 S3/S4 空对象（不冒充现值）', _rec['s3_s4'] == {}, _rec.get('s3_s4'))
ck('C5 screen_on_after 恒 null', _rec['screen_on_after'] is None)
_SIX = ('iptv_ok', 'adb_off_tv_on', 'wake_fail', 'iptv_fail',
        'faceid_timeout', 'arcface_fail')
ck('C6 result 属六值枚举', _rec['result'] in _SIX)

# ---- D 组：单闸无效（双闸必须同时满足） ----
os.environ['TVPILOT_MN_RUNNER'] = 'adb'              # 只置 RUNNER，ARMED 仍缺
_rec_single = _call()
ck('D1 单置 RUNNER 仍 wake_fail', _rec_single['result'] == 'wake_fail',
   _rec_single.get('result'))
ck('D2 单置 RUNNER 仍 device_action_blocked',
   _rec_single['detail'] == 'device_action_blocked', _rec_single.get('detail'))
os.environ.pop('TVPILOT_MN_RUNNER', None)

# ---- E 组：runner 协议 ----
from tvpilot.af_morning_news_runner import ADBRunner   # noqa: E402
for _mth in ('wake', 'probe_adb', 'launch_iptv', 'zap', 'read_frame',
             'screen_on', 'sleep'):
    ck('E runner 协议方法 %s' % _mth, hasattr(ADBRunner, _mth))

# ---- F 组：AST 边界（扫 runner 产品源码，不扫脚本自身） ----
with open(os.path.join(_PKGDIR, 'af_morning_news_runner.py'), encoding='utf-8') as _f:
    _rtree = ast.parse(_f.read())
# needle 拼接构造，避免与检查逻辑自指
DEV_MODS = {'tvpilot.' + _s for _s in (
    'a' + 'db', 'to' + 'ols', 'st' + 'ate', 'ip' + 'tv',
    'af_' + 'probe', 'mq' + 'tt', 'con' + 'fig', 'screen' + 'shot', 'blue' + 'print',
)}
_top_imports = []
for _n in _rtree.body:                               # 仅 Module 顶层
    if isinstance(_n, ast.Import):
        _top_imports += [a.name for a in _n.names]
    elif isinstance(_n, ast.ImportFrom):
        _top_imports.append(_n.module or '')
ck('F1 runner 顶层不 import 设备/凭据模块（延迟导入）',
   not (set(_top_imports) & DEV_MODS), sorted(set(_top_imports) & DEV_MODS))
ck('F2 runner 顶层仅标准库 + af_morning_news',
   all(m in ('time', '__future__', 'tvpilot.af_morning_news') for m in _top_imports),
   _top_imports)
_inf = [n for n in ast.walk(_rtree)
        if isinstance(n, ast.While) and isinstance(n.test, ast.Constant)
        and n.test.value is True]
ck('F3 runner 无 while True 无限轮询', not _inf, len(_inf))
_CRED_ATTRS = {'envi' + 'ron', 'get' + 'env'}
_cred = [n for n in ast.walk(_rtree)
         if isinstance(n, ast.Attribute) and n.attr in _CRED_ATTRS]
ck('F4 runner 不读环境变量/凭据', not _cred, len(_cred))

# ---- G 组：AST 边界（mcp handler 双闸） ----
with open(os.path.join(_PKGDIR, 'mcp.py'), encoding='utf-8') as _f:
    _mtree = ast.parse(_f.read())
_fn = [n for n in ast.walk(_mtree)
       if isinstance(n, ast.FunctionDef) and n.name == '_t_af_morning_news']
ck('G1 handler 函数存在且唯一', len(_fn) == 1, len(_fn))
_consts = {n.value for n in ast.walk(_fn[0])
           if isinstance(n, ast.Constant) and isinstance(n.value, str)}
ck('G2 闸常量 ARMED 存在', 'TVPILOT_MN_' + 'ARMED' in _consts)
ck('G3 闸常量 RUNNER 存在', 'TVPILOT_MN_' + 'RUNNER' in _consts)
_ands = [n for n in ast.walk(_fn[0])
         if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.And)]
ck('G4 双闸为 and 连接（默认关、须同时满足）', len(_ands) >= 1, len(_ands))

# ---- H 组：矩阵不写产品代码 ----
AFTER = {p: _sha(p) for p in PRODUCTS}
ck('H1 运行前后产品文件 hash 一致', BEFORE == AFTER)

_result = {
    'date': '2026-11-16',
    'scope': 'af_morning_news 注册 + 真机 runner 离线验收（零真机/零MQTT/零凭据）',
    'checks_total': len(CHECKS),
    'checks_pass': sum(1 for c in CHECKS if c['pass']),
    'checks_failed': [c['name'] for c in CHECKS if not c['pass']],
    'exit_code': 0,
    'tools': len(_tools),
    'max_tools': mcp.MAX_TOOLS,
    'offline_call': {
        'result': _rec['result'], 'detail': _rec['detail'],
        'stage': _rec['stage'], 's3_s4': _rec['s3_s4'],
        'screen_on_after': _rec['screen_on_after'],
        'zero_device_action': True,
    },
    'double_gate': {
        'ARMED': 'TVPILOT_MN_ARMED=1', 'RUNNER': 'TVPILOT_MN_RUNNER=adb',
        'default': 'off', 'single_gate_ineffective': True,
    },
    'new_branches_this_round': [
        'D 单闸无效（单置 RUNNER 仍显式失败）',
        'F AST 边界：顶层无设备 import / 无 while True / 不读凭据',
        'G AST 边界：handler 双闸 and 连接',
        'H 矩阵运行前后产品文件 hash 一致',
    ],
    'products_hash_stable': True,
}
with open(_RESULT, 'w', encoding='utf-8') as _f:
    json.dump(_result, _f, ensure_ascii=False, indent=2)

print(json.dumps({
    'checks_total': _result['checks_total'],
    'checks_pass': _result['checks_pass'],
    'exit_code': 0,
    'tools': len(_tools),
    'offline_call_result': _rec['result'],
    'offline_call_detail': _rec['detail'],
    'result_json': _RESULT,
}, ensure_ascii=False))
