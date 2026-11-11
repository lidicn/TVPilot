"""TVPilot 媒体路由。

绞杀者模式阶段3-3：/api/screenshot, /api/analyze, /api/ocr, /api/tts, /api/audio/*, /api/screenshot_smart。
v1.1：截图支持 JPEG（6095 端口），自动检测格式发送。
"""
import time

from tvpilot import make_response
from tvpilot.tts import edge_tts_synthesize
from tvpilot.screenshot import tool_screenshot, tool_screenshot_smart
from tvpilot.vision import tool_analyze, tool_ocr
from tvpilot.audio import tool_audio_get, tool_audio_set_volume, tool_audio_mute, tool_audio_volume_up, tool_audio_volume_down


def _send_image(handler, img_bytes):
    """发送图片，自动检测 JPEG/PNG 格式。"""
    if img_bytes[:3] == b'\xff\xd8\xff':
        ctype = 'image/jpeg'
    else:
        ctype = 'image/png'
    handler.send_response(200)
    handler.send_header('Content-Type', ctype)
    handler.send_header('Content-Length', str(len(img_bytes)))
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.end_headers()
    handler.wfile.write(img_bytes)


def _img_bytes(result):
    """从 result 中提取图片字节（兼容 image_bytes/png_bytes）。"""
    return result.get('image_bytes', result.get('png_bytes'))


def handle_tts(handler, path, q):
    """/api/tts：EdgeTTS 语音合成，返回 audio/mpeg。"""
    if path != '/api/tts':
        return False
    text = q.get('text', [''])[0]
    voice = q.get('voice', ['zh-CN-XiaoxiaoNeural'])[0]
    if not text:
        handler._json({'ok': False, 'error': 'text required'}, 400)
        return True
    t0 = time.time()
    audio_bytes, err = edge_tts_synthesize(text, voice)
    if err:
        resp, code = make_response('tts', error='tts_failed', message=err, start_time=t0)
        handler._json(resp, code)
        return True
    handler.send_response(200)
    handler.send_header('Content-Type', 'audio/mpeg')
    handler.send_header('Content-Length', str(len(audio_bytes)))
    handler.send_header('Cache-Control', 'public, max-age=86400')
    handler.end_headers()
    handler.wfile.write(audio_bytes)
    return True


def handle_screenshot_get(handler, path, q):
    """/api/screenshot GET：截图，支持 fmt=png/json。"""
    if path != '/api/screenshot':
        return False
    fmt = q.get('fmt', ['json'])[0]
    width = int(q['width'][0]) if 'width' in q else None
    t0 = time.time()
    result, error, msg = tool_screenshot(fmt=fmt, width=width)
    if error:
        resp, code = make_response('screenshot', error=error, message=msg, start_time=t0)
        handler._json(resp, code)
    elif fmt == 'json':
        resp, code = make_response('screenshot', result=result, start_time=t0)
        handler._json(resp, code)
    else:
        _send_image(handler, _img_bytes(result))
    return True


def handle_analyze(handler, path, q):
    """/api/analyze：截图多模态分析（doubao2api）。"""
    if path != '/api/analyze':
        return False
    prompt = q.get('prompt', [None])[0]
    quality = q.get('quality', ['low'])[0]
    t0 = time.time()
    result, error, msg = tool_analyze(prompt=prompt, quality=quality)
    resp, code = make_response('analyze', result=result, error=error, message=msg, start_time=t0)
    handler._json(resp, code)
    return True


def handle_ocr(handler, path, q):
    """/api/ocr：OCR 文字识别（tesseract）。"""
    if path != '/api/ocr':
        return False
    t0 = time.time()
    text_filter = q.get('text', [None])[0]
    result, error, msg = tool_ocr(text_filter=text_filter)
    resp, code = make_response('ocr', result=result, error=error, message=msg, start_time=t0)
    handler._json(resp, code)
    return True


def handle_screenshot_smart(handler, path, q):
    """/api/screenshot_smart：智能截图（多质量+格式）。"""
    if path != '/api/screenshot_smart':
        return False
    t0 = time.time()
    fmt = q.get('fmt', ['json'])[0]
    width = int(q.get('width', ['480'])[0])
    quality = q.get('quality', ['medium'])[0]
    result, error, msg = tool_screenshot_smart(fmt=fmt, width=width, quality=quality)
    if error:
        resp, code = make_response('screenshot_smart', error=error, message=msg, start_time=t0)
        handler._json(resp, code)
    elif fmt == 'json':
        resp, code = make_response('screenshot_smart', result=result, start_time=t0)
        handler._json(resp, code)
    else:
        _send_image(handler, _img_bytes(result))
    return True


def handle_audio_get(handler, path, q):
    """/api/audio GET：查询音频状态。"""
    if path != '/api/audio':
        return False
    t0 = time.time()
    result, error, msg = tool_audio_get()
    resp, code = make_response('audio', result=result, error=error, message=msg, start_time=t0)
    handler._json(resp, code)
    return True


def handle_media_get(handler, path, q):
    """统一分发：所有媒体 GET 路由。"""
    if handle_tts(handler, path, q):
        return True
    if handle_screenshot_get(handler, path, q):
        return True
    if handle_analyze(handler, path, q):
        return True
    if handle_ocr(handler, path, q):
        return True
    if handle_screenshot_smart(handler, path, q):
        return True
    if handle_audio_get(handler, path, q):
        return True
    return False


def handle_media_post(handler, path, body):
    """统一分发：所有媒体 POST 路由。返回 True 表示已处理。"""
    # /api/screenshot POST
    if path == '/api/screenshot':
        fmt = body.get('fmt', 'json')
        width = body.get('width')
        t0 = time.time()
        result, error, msg = tool_screenshot(fmt=fmt, width=width)
        if error:
            resp, code = make_response('screenshot', error=error, message=msg, start_time=t0)
            handler._json(resp, code)
        elif fmt == 'json':
            resp, code = make_response('screenshot', result=result, start_time=t0)
            handler._json(resp, code)
        else:
            _send_image(handler, _img_bytes(result))
        return True

    # /api/audio/volume
    if path == '/api/audio/volume':
        handler._handle_tool('audio.volume', tool_audio_set_volume, level=body.get('level'))
        return True

    # /api/audio/mute
    if path == '/api/audio/mute':
        handler._handle_tool('audio.mute', tool_audio_mute)
        return True

    # /api/audio/volume_up
    if path == '/api/audio/volume_up':
        handler._handle_tool('audio.volume_up', tool_audio_volume_up)
        return True

    # /api/audio/volume_down
    if path == '/api/audio/volume_down':
        handler._handle_tool('audio.volume_down', tool_audio_volume_down)
        return True

    return False


__all__ = [
    'handle_tts', 'handle_screenshot_get', 'handle_analyze', 'handle_ocr',
    'handle_screenshot_smart', 'handle_audio_get',
    'handle_media_get', 'handle_media_post',
]
