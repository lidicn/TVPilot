"""TVPilot MCP Server（Model Context Protocol 2025-03-26 / JSON-RPC 2.0）。

零新依赖：仅标准库 + tvpilot 内部模块（延迟导入，测试可 mock）。
双传输：
* stdio：``python -m tvpilot.mcp --stdio``，换行分隔 JSON-RPC；
* HTTP/SSE：``python -m tvpilot.mcp``，POST /mcp 走 JSON-RPC，GET /mcp 开 SSE 心跳流。

省 token 设计：
* 工具收敛为 10 个，描述均 <= 80 字符；
* tools/call 直接回传工具三元组里的 data（剥掉 ok/cost_ms 包装），
  文本 content 用紧凑 JSON（无空格、ensure_ascii=False）；
* 截图只回 URL（默认 240p / 宽 426），不回 base64；
* 蓝图 steps 只留定位字段，丢弃 output/params 回显。

既有文件（tools.py / state.py / screenshot.py / blueprint.py）只读复用，不修改。

v1.1 整合改进：
* 延迟导入 tvpilot 内部模块（顶层仅标准库，测试可轻量 mock）；
* fail-closed：工具异常统一收敛为 isError，不炸 server；
* 截图 TTL 清理（默认 1 小时，后台线程定期清理）；
* capabilities.tools=true（按验收要求）。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import socket
import struct
import sys
import tempfile
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial

# --------------------------------------------------------------------------- #
# 延迟导入：tvpilot 内部模块只在首次使用时导入，测试可 mock
# --------------------------------------------------------------------------- #
_tvpilot_loaded = False
_tools = None
_state = None
_screenshot = None
_blueprint = None


def _ensure_tvpilot():
    """延迟导入 tvpilot 内部模块。测试可在 sys.modules 注入假模块。"""
    global _tvpilot_loaded, _tools, _state, _screenshot, _blueprint
    if _tvpilot_loaded:
        return
    from tvpilot import tools as _tools_mod
    from tvpilot import state as _state_mod
    from tvpilot import screenshot as _screenshot_mod
    from tvpilot import blueprint as _blueprint_mod
    _tools = _tools_mod
    _state = _state_mod
    _screenshot = _screenshot_mod
    _blueprint = _blueprint_mod
    _tvpilot_loaded = True


PROTOCOL_VERSION = '2025-03-26'
SERVER_INFO = {'name': 'tvpilot-mcp', 'version': '1.1.0'}
MAX_TOOLS = 30

# 截图配置
_SHOT_DIR = os.environ.get('TVPILOT_MCP_SHOT_DIR', '')  # 延迟初始化为 STATIC_DIR/shots
_SHOT_BASE = os.environ.get('TVPILOT_MCP_BASE_URL', 'http://192.168.2.200:8090/remote-static/shots').rstrip('/')
_SHOT_TTL = int(os.environ.get('TVPILOT_MCP_SHOT_TTL', '3600'))  # 默认 1 小时
_shot_server = None
_shot_lock = threading.Lock()
_shot_cleaner_started = False

_runner = None  # 懒加载 BlueprintRunner，测试可注入假 runner


# --------------------------------------------------------------------------- #
# 结果归一化（fail-closed：三元组 err/msg 非空即失败）
# --------------------------------------------------------------------------- #

def _unwrap(raw):
    """既有契约：仅三元组按 (data, err_code, err_message) 解释，其余视为成功。"""
    if isinstance(raw, (tuple, list)) and len(raw) == 3:
        return raw[0], raw[1], raw[2]
    return raw, None, None


def _clean(data):
    """去掉 ok/cost_ms 包装字段（防御性；正常 data 本就不含）。
    注意：蓝图汇总里的 ok 是业务语义，调用方需传 clean=False 保留。"""
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k not in ('ok', 'cost_ms')}
    return data


def _ok_content(data, clean=True):
    if clean:
        data = _clean(data)
    text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return {'content': [{'type': 'text', 'text': text}]}


def _err_content(message):
    return {'content': [{'type': 'text', 'text': str(message)}], 'isError': True}


def _call(fn, *args, **kwargs):
    """调既有工具，把 (data, err, msg) 翻成 MCP result。fail-closed。"""
    try:
        data, err, msg = _unwrap(fn(*args, **kwargs))
        if err is not None or msg is not None:
            return _err_content(msg or err)
        return _ok_content(data)
    except Exception as exc:
        return _err_content('%s: %s' % (type(exc).__name__, exc))


# --------------------------------------------------------------------------- #
# 截图：base64 -> 落盘 -> URL（纯标准库 PNG 降采样到 240p）
# --------------------------------------------------------------------------- #

def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _ensure_shot_dir():
    """确保截图目录存在（STATIC_DIR/shots），通过主服务 /remote-static/shots 访问。"""
    global _SHOT_DIR
    if not _SHOT_DIR:
        _ensure_tvpilot()
        from tvpilot.config import STATIC_DIR
        _SHOT_DIR = os.path.join(STATIC_DIR, 'shots')
    os.makedirs(_SHOT_DIR, exist_ok=True)
    _start_shot_cleaner()
    return _SHOT_DIR


def _start_shot_cleaner():
    """启动截图 TTL 清理后台线程（每 10 分钟扫一次）。"""
    global _shot_cleaner_started
    if _shot_cleaner_started:
        return
    _shot_cleaner_started = True

    def _clean_loop():
        while True:
            try:
                now = time.time()
                for fname in os.listdir(_SHOT_DIR):
                    fpath = os.path.join(_SHOT_DIR, fname)
                    if not os.path.isfile(fpath):
                        continue
                    if now - os.path.getmtime(fpath) > _SHOT_TTL:
                        try:
                            os.remove(fpath)
                        except OSError:
                            pass
            except Exception:
                pass
            time.sleep(600)  # 每 10 分钟扫一次

    threading.Thread(target=_clean_loop, daemon=True).start()


def _png_chunks(blob):
    if not blob.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('not png')
    pos, chunks = 8, []
    while pos + 8 <= len(blob):
        ln = struct.unpack('>I', blob[pos:pos + 4])[0]
        typ = blob[pos + 4:pos + 8]
        chunks.append((typ, blob[pos + 8:pos + 8 + ln]))
        pos += 12 + ln
    return chunks


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw, h, stride, bpp):
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(h):
        f = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 255
        out += line
        prev = line
    return bytes(out)


def _png_downscale(blob, target_w):
    """纯 stdlib 最近邻降采样。返回 (bytes, width, height, note)。"""
    try:
        chunks = _png_chunks(blob)
        ihdr = next(d for t, d in chunks if t == b'IHDR')
        w, h, depth, ctype, _, _, interlace = struct.unpack('>IIBBBBB', ihdr)
        channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
        if channels is None or depth != 8 or interlace != 0:
            return blob, w, h, 'unsupported_png_kept_original'
        if w <= target_w or target_w < 1:
            return blob, w, h, None
        idat = b''.join(d for t, d in chunks if t == b'IDAT')
        raw = zlib.decompress(idat)
        stride = w * channels
        pix = _unfilter(raw, h, stride, channels)
        nw = target_w
        nh = max(1, round(h * nw / w))
        rows = []
        for y in range(nh):
            sy = y * h // nh
            base = sy * stride
            row = bytearray()
            for x in range(nw):
                sx = x * w // nw
                row += pix[base + sx * channels: base + (sx + 1) * channels]
            rows.append(b'\x00' + bytes(row))

        def chunk(typ, data):
            c = typ + data
            return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

        out = b'\x89PNG\r\n\x1a\n'
        out += chunk(b'IHDR', struct.pack('>IIBBBBB', nw, nh, 8, ctype, 0, 0, 0))
        out += chunk(b'IDAT', zlib.compress(b''.join(rows), 6))
        out += chunk(b'IEND', b'')
        return out, nw, nh, None
    except Exception:
        return blob, None, None, 'downscale_failed_kept_original'


def _t_screenshot(args):
    _ensure_tvpilot()
    _ensure_shot_dir()
    inline = args.get('inline', False)
    highlight = args.get('highlight', False)
    try:
        width = int(args.get('width', 426))
    except (TypeError, ValueError):
        return _err_content('width 必须是整数')
    if not (16 <= width <= 4096):
        return _err_content('width 超出范围 (16~4096)')

    # highlight=true 时：先拿 UI 元素，截图用更高分辨率，再画编号
    ui_elements = None
    if highlight:
        try:
            ui_data, ui_err, _ = _unwrap(_tools.tool_ui_dump())
            if ui_err is None and isinstance(ui_data, dict):
                ui_elements = ui_data.get('elements', [])
            # 标注截图用更高分辨率，保证数字清晰
            width = max(width, 720)
        except Exception:
            ui_elements = None

    try:
        raw = _screenshot.tool_screenshot(width=width)
    except TypeError:
        raw = _screenshot.tool_screenshot()  # 老签名无 width 参数
    data, err, msg = _unwrap(raw)
    if err is not None or msg is not None:
        return _err_content(msg or err)
    # 优先取原始字节（tool_screenshot 默认 fmt='png' 返回 image_bytes）
    blob = None
    if isinstance(data, dict):
        raw_bytes = data.get('image_bytes')
        if isinstance(raw_bytes, bytes) and len(raw_bytes) > 100:
            blob = raw_bytes
        else:
            for k in ('base64', 'b64', 'image', 'data', 'image_base64', 'png_base64'):
                v = data.get(k)
                if v and isinstance(v, str):
                    try:
                        blob = base64.b64decode(v)
                        break
                    except (binascii.Error, ValueError):
                        continue
    elif isinstance(data, str):
        try:
            blob = base64.b64decode(data)
        except (binascii.Error, ValueError):
            pass
    if not blob or len(blob) < 100:
        return _err_content('截图结果里没有图像数据')

    # highlight=true：在截图上画元素编号
    if highlight and ui_elements:
        try:
            blob = _screenshot.draw_highlight(blob, ui_elements)
        except Exception:
            pass  # 画图失败就返回原图

    # 判断格式：JPEG 直接保存（已压缩），PNG 走降采样
    is_jpeg = blob[:3] == b'\xff\xd8\xff'
    is_png = blob[:8] == b'\x89PNG\r\n\x1a\n'
    os.makedirs(_SHOT_DIR, exist_ok=True)
    if is_jpeg:
        ext = 'jpg'
        out_blob = blob
        w = h = None
        note = 'jpeg_highlight' if highlight else 'jpeg_original'
        mime = 'image/jpeg'
    elif is_png:
        ext = 'png'
        out_blob, w, h, note = _png_downscale(blob, width)
        if highlight:
            note = note + '_highlight'
        mime = 'image/png'
    else:
        ext = 'bin'
        out_blob = blob
        w = h = None
        note = 'unknown_format'
        mime = 'image/png'
    name = 'shot_%s.%s' % (uuid.uuid4().hex[:12], ext)
    with open(os.path.join(_SHOT_DIR, name), 'wb') as f:
        f.write(out_blob)
    out = {'url': '%s/%s' % (_SHOT_BASE, name), 'width': w or width, 'format': ext}
    if h:
        out['height'] = h
    if note:
        out['note'] = note
    if highlight:
        out['highlight'] = True
        out['element_count'] = len(ui_elements) if ui_elements else 0
    # inline=true：压缩到小尺寸 JPEG，确保 base64 不会超限
    if inline:
        try:
            from PIL import Image as _PILImage
            import io as _io
            _img = _PILImage.open(_io.BytesIO(out_blob))
            _iw, _ih = _img.size
            _target_w = min(320, _iw)
            if _target_w < _iw:
                _img = _img.resize((_target_w, int(_ih * _target_w / _iw)), _PILImage.LANCZOS)
            _buf = _io.BytesIO()
            _img.convert('RGB').save(_buf, format='JPEG', quality=60)
            inline_blob = _buf.getvalue()
        except Exception:
            inline_blob = out_blob
        b64 = base64.b64encode(inline_blob).decode()
        out['inline_bytes'] = len(inline_blob)
        out['inline_size'] = f"{len(b64)//1024}KB"
        return {'content': [
            {'type': 'text', 'text': json.dumps(out, ensure_ascii=False, separators=(',', ':'))},
            {'type': 'image', 'data': b64, 'mimeType': 'image/jpeg'},
        ]}
    return _ok_content(out)


# --------------------------------------------------------------------------- #
# 蓝图
# --------------------------------------------------------------------------- #

def _get_runner():
    global _runner
    if _runner is None:
        _ensure_tvpilot()
        r = _blueprint.BlueprintRunner()
        r.register_builtins()
        _runner = r
        _load_custom_registry()
    return _runner


def _t_blueprint_run(args):
    name = args.get('name')
    if not name or not isinstance(name, str):
        return _err_content('name 不能为空')
    params = args.get('params') or {}
    if not isinstance(params, dict):
        return _err_content('params 必须是对象')
    res = _get_runner().run(name, params)
    d = res.to_dict()
    # 省 token：steps 只留定位字段，丢弃 output/params 回显
    body = {
        'name': d['name'], 'ok': d['ok'], 'error': d.get('error'),
        'failed_step': d.get('failed_step'), 'duration_ms': d.get('duration_ms'),
        'steps': [{'step': s['step'], 'ok': s['ok'], 'status': s['status'],
                   'error': s.get('error')} for s in d.get('steps', [])],
    }
    # 统一错误表达：蓝图不存在或执行失败时 isError=true
    if not d.get('ok', False):
        err_msg = d.get('error') or f'蓝图执行失败: {name}'
        # 把已注册蓝图列表附加到错误信息（方便调用方排查）
        if '未注册' in err_msg or 'not found' in err_msg.lower():
            try:
                registered = list(_get_runner()._registry.keys())
                err_msg += f'；已注册蓝图: {registered}'
            except Exception:
                pass
        return _err_content(err_msg)
    return _ok_content(body, clean=False)


# 自定义蓝图持久化（外挂卷 data/ 目录）
_CUSTOM_BP_FILE = '/app/data/custom_registry.json'


def _load_custom_registry():
    """启动时从磁盘加载 Agent 录制的自定义蓝图。"""
    import json as _json
    try:
        with open(_CUSTOM_BP_FILE, 'r', encoding='utf-8') as f:
            specs = _json.load(f)
    except Exception:
        return []
    runner = _get_runner()
    loaded = []
    for spec in specs:
        try:
            bp = _blueprint.Blueprint(
                name=spec['name'],
                steps=spec['steps'],
                description=spec.get('description', ''),
                params=spec.get('params', ()),
            )
            runner.register(bp, replace=True)
            loaded.append(spec['name'])
        except Exception as e:
            print(f'[blueprint] 加载自定义蓝图失败 {spec.get("name")}: {e}', flush=True)
    if loaded:
        print(f'[blueprint] 加载 {len(loaded)} 个自定义蓝图: {loaded}', flush=True)
    return loaded


def _save_custom_registry():
    """把当前所有自定义蓝图写入磁盘。"""
    import json as _json
    runner = _get_runner()
    specs = []
    for name, bp in runner._registry.items():
        # 只保存非内置蓝图
        if name in ('go_home', 'open_settings', 'open_app', 'zap_to', 'zap_to_cctv1', 'iptv_zap_safe'):
            continue
        specs.append({
            'name': bp.name,
            'description': bp.description,
            'params': getattr(bp, '_params', {}),
            'steps': [{'tool': s.tool, 'args': s.args, 'name': s.name,
                       'sleep': s.sleep, 'sleep_after': s.sleep_after,
                       'on_error': s.on_error, 'condition': s.condition}
                      for s in bp.steps],
        })
    try:
        with open(_CUSTOM_BP_FILE, 'w', encoding='utf-8') as f:
            _json.dump(specs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[blueprint] 保存自定义蓝图失败: {e}', flush=True)


def _t_blueprint_record(args):
    """录制蓝图：Agent 把成功执行的操作序列注册为可复用蓝图。"""
    name = args.get('name', '').strip()
    if not name:
        return _err_content('name 不能为空（蓝图名，如 feiniu_search_play）')
    if not re.match(r'^[a-z][a-z0-9_]{2,40}$', name):
        return _err_content('蓝图名需小写字母开头，3~40位，仅字母数字下划线')
    steps = args.get('steps')
    if not isinstance(steps, list) or len(steps) < 1:
        return _err_content('steps 不能为空（步骤数组）')
    description = args.get('description', '')
    params = args.get('params', ())
    try:
        bp = _blueprint.Blueprint(name=name, steps=steps,
                                   description=description, params=params)
        _get_runner().register(bp, replace=True)
        _save_custom_registry()
        return _ok_content({'name': name, 'steps': len(steps),
                           'description': description, 'saved': True})
    except Exception as e:
        return _err_content(f'蓝图注册失败: {e}')


def _t_blueprint_delete(args):
    """删除自定义蓝图（不能删内置蓝图）。"""
    name = args.get('name', '').strip()
    if not name:
        return _err_content('name 不能为空')
    builtin = {'go_home', 'open_settings', 'open_app', 'zap_to', 'zap_to_cctv1', 'iptv_zap_safe'}
    if name in builtin:
        return _err_content(f'内置蓝图 {name} 不可删除')
    runner = _get_runner()
    if name not in runner._registry:
        return _err_content(f'蓝图不存在: {name}')
    del runner._registry[name]
    _save_custom_registry()
    return _ok_content({'deleted': name, 'remaining': list(runner._registry.keys())})


# --------------------------------------------------------------------------- #
# 工具表（10 个，描述 <= 80 字符，inputSchema 规范化）
# --------------------------------------------------------------------------- #

def _t_zap(args):
    """换台：调用 tool_zap，成功后从缓存验证当前频道（轻量，不调用完整 system_state）。"""
    _ensure_tvpilot()
    channel = args.get('channel')
    if not channel:
        return _err_content('channel 不能为空')
    data, err, msg = _unwrap(_tools.tool_zap(channel))
    if err is not None or msg is not None:
        return _err_content(msg or err)
    # 换台成功，从频道缓存验证（tool_zap 内部已更新缓存）
    try:
        ch = _state.get_current_channel()
        data['verified'] = {
            'iptv_foreground': data.get('iptv_foreground', False),
            'current_channel': ch.get('name'),
            'current_channel_no': ch.get('no'),
            'playing': data.get('iptv_foreground', False),
            'state': 'playing' if data.get('iptv_foreground') else 'unknown',
        }
    except Exception:
        pass
    return _ok_content(data)


def _t_foreground(_args):
    _ensure_tvpilot()
    pkg = _state.get_foreground_pkg()
    if pkg is None:
        return _err_content('无法获取前台包名，电视可能离线')
    return _ok_content({'package': pkg})



def _t_ui_dump(_args):
    """UI dump：返回带索引的元素列表（index+text+pos+click），省 token。"""
    _ensure_tvpilot()
    data, err, msg = _unwrap(_tools.tool_ui_dump())
    if err is not None or msg is not None:
        return _err_content(msg or err)
    elements = data.get('elements', [])
    # 精简：只保留 index、text、pos、click
    slim = [{'index': e.get('index'), 'text': e.get('text', ''),
             'pos': e.get('center'), 'click': e.get('clickable', False)}
            for e in elements if e.get('text') or e.get('clickable')]
    return _ok_content({'count': len(slim), 'elements': slim})


def _t_tap_index(args):
    """按元素索引点击（索引来自 tv_ui_dump）。"""
    _ensure_tvpilot()
    idx = args.get('index')
    if idx is None:
        return _err_content('index 必填')
    return _call(_tools.tool_tap_index, int(idx))



def _t_theme_list(_args):
    """列出 NAS 侧主题（含文件与校验状态）。"""
    from tvpilot import theme
    return _ok_content({'dir': theme.THEMES_DIR, 'themes': theme.list_themes()})


def _t_theme_status(args):
    """查电视侧已装主题；可选 user。"""
    from tvpilot import theme
    return _ok_content({'dir_tv': theme.TV_DIR,
                        'themes': theme.tv_status(args.get('user'))})


def _t_theme_push(args):
    """下发主题：user 单发或 all=true 全发。"""
    from tvpilot import theme
    res = theme.push(user=args.get('user'), all_users=bool(args.get('all')))
    if not res.get('ok'):
        return _err_content(res.get('error') or 'push failed')
    return _ok_content(res)


def _build_tools():
    """构建工具表（延迟构建，确保 _ensure_tvpilot 后函数引用正确）。"""
    _ensure_tvpilot()
    return [
        {
            'name': 'tv_zap',
            'description': '换台：按频道名切台，如 CCTV1/中央一台/湖南卫视',
            'inputSchema': {
                'type': 'object',
                'properties': {'channel': {'type': 'string', 'description': '频道名或台号'}},
                'required': ['channel'],
            },
            'handler': _t_zap,
        },
        {
            'name': 'tv_keyevent',
            'description': '发送按键 KEYCODE_*，ADB 优先 6095 降级',
            'inputSchema': {
                'type': 'object',
                'properties': {'key': {'type': 'string', 'description': 'KEYCODE_HOME 等'}},
                'required': ['key'],
            },
            'handler': lambda a: _call(_tools.tool_keyevent, a.get('key')),
        },
        {
            'name': 'tv_tap',
            'description': '点击屏幕坐标 (x,y)，范围 0~4096',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'x': {'type': 'integer', 'minimum': 0, 'maximum': 4096},
                    'y': {'type': 'integer', 'minimum': 0, 'maximum': 4096},
                },
                'required': ['x', 'y'],
            },
            'handler': lambda a: _call(_tools.tool_tap, a.get('x'), a.get('y')),
        },
        {
            'name': 'tv_tap_index',
            'description': '按元素索引点击（索引来自 tv_ui_dump，30秒内有效）',
            'inputSchema': {
                'type': 'object',
                'properties': {'index': {'type': 'integer', 'minimum': 0, 'description': '元素索引'}},
                'required': ['index'],
            },
            'handler': _t_tap_index,
        },
        {
            'name': 'tv_swipe',
            'description': '滑动(x1,y1)→(x2,y2)，duration_ms控制快慢(50~可做长按)',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'x1': {'type': 'integer', 'minimum': 0, 'maximum': 4096},
                    'y1': {'type': 'integer', 'minimum': 0, 'maximum': 4096},
                    'x2': {'type': 'integer', 'minimum': 0, 'maximum': 4096},
                    'y2': {'type': 'integer', 'minimum': 0, 'maximum': 4096},
                    'duration_ms': {'type': 'integer', 'default': 300},
                },
                'required': ['x1', 'y1', 'x2', 'y2'],
            },
            'handler': lambda a: _call(_tools.tool_swipe, a.get('x1'), a.get('y1'),
                                        a.get('x2'), a.get('y2'), a.get('duration_ms', 300)),
        },
        {
            'name': 'tv_wait',
            'description': '等待N秒(0.1~10)，操作间留时间让界面响应',
            'inputSchema': {
                'type': 'object',
                'properties': {'seconds': {'type': 'number', 'minimum': 0.1, 'maximum': 10}},
                'required': ['seconds'],
            },
            'handler': lambda a: _call(_tools.tool_wait, a.get('seconds', 2)),
        },
        {
            'name': 'tv_wait_for_foreground',
            'description': '等待前台变为指定包名(轮询，超时10s)',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'package': {'type': 'string', 'description': '期望前台包名(模糊匹配)'},
                    'timeout': {'type': 'number', 'default': 10},
                },
                'required': ['package'],
            },
            'handler': lambda a: _call(_tools.tool_wait_for_foreground, a.get('package'), a.get('timeout', 10)),
        },
        {
            'name': 'tv_input_text',
            'description': '输入文字，支持中文（tvremoteime 广播）',
            'inputSchema': {
                'type': 'object',
                'properties': {'text': {'type': 'string'}},
                'required': ['text'],
            },
            'handler': lambda a: _call(_tools.tool_input_text, a.get('text')),
        },
        {
            'name': 'tv_launch_app',
            'description': '启动 App：按包名，可选 activity',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'package': {'type': 'string'},
                    'activity': {'type': 'string'},
                },
                'required': ['package'],
            },
            'handler': lambda a: _call(_tools.tool_launch_app, a.get('package'), a.get('activity')),
        },
        {
            'name': 'tv_go_home',
            'description': '回桌面：HOME 键 + 前台验证',
            'inputSchema': {'type': 'object', 'properties': {}},
            'handler': lambda a: _call(_tools.tool_combo_go_home),
        },
        {
            'name': 'tv_foreground',
            'description': '查询当前前台应用包名',
            'inputSchema': {'type': 'object', 'properties': {}},
            'handler': _t_foreground,
        },
        {
            'name': 'tv_screenshot',
            'description': '截图：inline=true时返回base64图片块；highlight=true时在图上画元素编号',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'width': {'type': 'integer', 'default': 426, 'minimum': 16, 'maximum': 4096},
                    'inline': {'type': 'boolean', 'default': False, 'description': 'true=返回base64图片块，多模态Agent可直接看到画面'},
                    'highlight': {'type': 'boolean', 'default': False, 'description': 'true=在截图上标出每个可交互元素的编号（配合ui_dump使用）'},
                },
            },
            'handler': _t_screenshot,
        },
        {
            'name': 'tv_system_state',
            'description': '查询电视系统状态（前台+播放+音频+网络）',
            'inputSchema': {'type': 'object', 'properties': {}},
            'handler': lambda a: _call(_state.tool_system_state),
        },
        {
            'name': 'tv_blueprint_run',
            'description': '执行预注册蓝图流程，如 go_home/zap_to/trim_search_play',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'params': {'type': 'object'},
                },
                'required': ['name'],
            },
            'handler': _t_blueprint_run,
        },
        {
            'name': 'tv_blueprint_record',
            'description': '录制蓝图：把成功的操作序列存为可复用流程',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'description': '蓝图名，如 feiniu_search_play'},
                    'description': {'type': 'string', 'description': '蓝图用途说明'},
                    'steps': {'type': 'array', 'items': {'type': 'object'},
                              'description': '步骤数组，每步 {tool,args,name?,sleep?,on_error?}'},
                    'params': {'type': 'object', 'description': '参数声明，如 {"keyword":""}'},
                },
                'required': ['name', 'steps'],
            },
            'handler': _t_blueprint_record,
        },
        {
            'name': 'tv_blueprint_delete',
            'description': '删除自定义蓝图（内置蓝图不可删）',
            'inputSchema': {
                'type': 'object',
                'properties': {'name': {'type': 'string'}},
                'required': ['name'],
            },
            'handler': _t_blueprint_delete,
        },
        {
            'name': 'tv_list_apps',
            'description': '列出已安装应用，可选 filter 按包名过滤',
            'inputSchema': {
                'type': 'object',
                'properties': {'filter': {'type': 'string', 'description': '过滤关键词，如 video/iqiyi'}},
            },
            'handler': lambda a: _call(_tools.tool_list_apps, a.get('filter')),
        },
        {
            'name': 'tv_app_state',
            'description': '查询应用状态：是否安装/运行/前台',
            'inputSchema': {
                'type': 'object',
                'properties': {'package': {'type': 'string'}},
                'required': ['package'],
            },
            'handler': lambda a: _call(_tools.tool_app_state, a.get('package')),
        },
        {
            'name': 'tv_force_stop',
            'description': '强制停止应用（点播前清理后台）',
            'inputSchema': {
                'type': 'object',
                'properties': {'package': {'type': 'string'}},
                'required': ['package'],
            },
            'handler': lambda a: _call(_tools.tool_force_stop, a.get('package')),
        },
        {
            'name': 'tv_ui_dump',
            'description': '获取界面可交互元素（带索引index，配合 tv_tap_index 使用）',
            'inputSchema': {'type': 'object', 'properties': {}},
            'handler': _t_ui_dump,
        },
        {
            'name': 'tv_click_text',
            'description': '按文字点击控件（先 ui_dump 找坐标再 tap）',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'text': {'type': 'string', 'description': '要点击的文字'},
                    'exact': {'type': 'boolean', 'default': False, 'description': '精确匹配'},
                },
                'required': ['text'],
            },
            'handler': lambda a: _call(_tools.tool_click_text, a.get('text'), a.get('exact', False)),
        },
        {
            'name': 'tv_tap_xpath',
            'description': '按 XPath 点击元素（xpath 来自 tv_ui_dump 返回）',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'xpath': {'type': 'string', 'description': '元素 xpath，来自 tv_ui_dump 返回'},
                },
                'required': ['xpath'],
            },
            'handler': lambda a: _call(_tools.tool_tap_xpath, a.get('xpath')),
        },
        {
            'name': 'tv_long_press',
            'description': '长按指定坐标（菜单/多选场景）',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'x': {'type': 'integer', 'minimum': 0, 'maximum': 4096},
                    'y': {'type': 'integer', 'minimum': 0, 'maximum': 4096},
                    'duration_ms': {'type': 'integer', 'default': 800, 'description': '长按时长，默认800ms'},
                },
                'required': ['x', 'y'],
            },
            'handler': lambda a: _call(_tools.tool_long_press, a.get('x'), a.get('y'), a.get('duration_ms', 800)),
        },
        {
            'name': 'tv_scroll',
            'description': '滚动列表（up/down/left/right）',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'direction': {'type': 'string', 'enum': ['up', 'down', 'left', 'right'], 'default': 'down'},
                },
            },
            'handler': lambda a: _call(_tools.tool_scroll, a.get('direction', 'down')),
        },
        {
            'name': 'tv_get_element',
            'description': '获取指定索引元素的完整信息（从ui_dump缓存读取）',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'index': {'type': 'integer', 'minimum': 0},
                },
                'required': ['index'],
            },
            'handler': lambda a: _call(_tools.tool_get_element, a.get('index')),
        },
        {
            'name': 'tv_batch',
            'description': '批量执行多个操作（点击→输入→回车），减少往返速度快',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'actions': {
                        'type': 'array',
                        'description': '操作列表，每个操作是dict：{action: tap/click_text/input_text/keyevent/wait/swipe/launch_app, ...}',
                        'items': {'type': 'object'},
                    },
                },
                'required': ['actions'],
            },
            'handler': lambda a: _call(_tools.tool_batch, a.get('actions')),
        },
        {
            'name': 'tv_theme_push',
            'description': '下发TV桌面主题到电视，user单发或all=true全发',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'user': {'type': 'string', 'description': '用户名 lidicn/Emily/Kevin'},
                    'all': {'type': 'boolean', 'description': 'true=下发全部用户'},
                },
            },
            'handler': _t_theme_push,
        },
        {
            'name': 'tv_theme_list',
            'description': '列出NAS侧TV桌面主题及文件校验状态',
            'inputSchema': {'type': 'object', 'properties': {}},
            'handler': _t_theme_list,
        },
        {
            'name': 'tv_theme_status',
            'description': '查电视侧已安装的TV桌面主题文件',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'user': {'type': 'string', 'description': '可选，只查某用户'},
                },
            },
            'handler': _t_theme_status,
        },
    ]


TOOLS = _build_tools()
assert len(TOOLS) <= MAX_TOOLS and all(len(t['description']) <= 80 for t in TOOLS)


# --------------------------------------------------------------------------- #
# JSON-RPC 2.0 分发
# --------------------------------------------------------------------------- #

def _resp(req_id, result):
    return {'jsonrpc': '2.0', 'id': req_id, 'result': result}


def _err(req_id, code, message):
    return {'jsonrpc': '2.0', 'id': req_id,
            'error': {'code': code, 'message': message}}


def handle_message(msg):
    """处理一条 JSON-RPC 消息；通知（无 id）返回 None。"""
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or 'method' not in msg:
        return _err(msg.get('id') if isinstance(msg, dict) else None,
                    -32600, 'Invalid Request')
    req_id = msg.get('id')
    method = msg['method']
    params = msg.get('params') or {}
    if req_id is None:  # 通知：initialized 等，不回包
        return None
    if method == 'initialize':
        return _resp(req_id, {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {'tools': True},  # 按验收要求
            'serverInfo': dict(SERVER_INFO),
        })
    if method == 'ping':
        return _resp(req_id, {})
    if method == 'tools/list':
        return _resp(req_id, {'tools': [
            {'name': t['name'], 'description': t['description'],
             'inputSchema': t['inputSchema']} for t in TOOLS]})
    if method == 'tools/call':
        name = params.get('name')
        args = params.get('arguments') or {}
        for t in TOOLS:
            if t['name'] == name:
                try:
                    return _resp(req_id, t['handler'](args))
                except Exception as exc:  # fail-closed：工具异常收敛为 isError
                    return _resp(req_id, _err_content('%s: %s' % (type(exc).__name__, exc)))
        return _err(req_id, -32602, '未知工具: %s' % name)
    return _err(req_id, -32601, 'Method not found: %s' % method)


def serve_stdio(inp=None, out=None):
    """stdio 传输：每行一个 JSON-RPC 消息，每行一个响应。日志只走 stderr。"""
    inp = inp if inp is not None else sys.stdin
    out = out if out is not None else sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = _err(None, -32700, 'Parse error')
        else:
            resp = handle_message(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False, separators=(',', ':')) + '\n')
            out.flush()


# --------------------------------------------------------------------------- #
# HTTP/SSE 传输（端点 /mcp）
# --------------------------------------------------------------------------- #

class MCPHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/healthz':
            self._send_json({'ok': True})
        elif path == '/mcp':
            # SSE 心跳流：server->client 通道（MCP 通知预留）
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                self.wfile.write(b'retry: 3000\n\n')
                while True:
                    self.wfile.write(b': ping\n\n')
                    self.wfile.flush()
                    time.sleep(15)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        else:
            self._send_json({'error': 'not found'}, 404)

    def do_POST(self):
        if self.path.split('?')[0] != '/mcp':
            self._send_json({'error': 'not found'}, 404)
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            msg = json.loads(self.rfile.read(length).decode() or '{}')
        except (ValueError, OSError):
            self._send_json(_err(None, -32700, 'Parse error'), 400)
            return
        resp = handle_message(msg)
        if resp is None:  # 通知：202 Accepted，无 body
            self.send_response(202)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self._send_json(resp)


def serve_http(host=None, port=None):
    host = host or os.environ.get('TVPILOT_MCP_HOST', '0.0.0.0')
    port = port if port is not None else int(os.environ.get('TVPILOT_MCP_PORT', '8765'))
    srv = ThreadingHTTPServer((host, port), MCPHandler)
    sys.stderr.write('tvpilot-mcp http://%s:%d/mcp\n' % (host, port))
    srv.serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(prog='tvpilot.mcp', description='TVPilot MCP Server')
    ap.add_argument('--stdio', action='store_true', help='stdio 传输模式')
    ap.add_argument('--host', default=None)
    ap.add_argument('--port', type=int, default=None)
    args = ap.parse_args(argv)
    if args.stdio:
        serve_stdio()
    else:
        serve_http(args.host, args.port)


if __name__ == '__main__':
    main()
