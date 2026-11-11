"""TVPilot 视觉分析层。

绞杀者模式阶段18迁移：截图多模态分析（doubao2api）+ OCR 文字识别（tesseract）。
"""
import os
import io
import json
import time
import subprocess
import urllib.request as ulib_req

from tvpilot.screenshot import tool_screenshot_smart, screencap_raw, HAS_PIL
from tvpilot.errors import (
    ERR_INVALID_PARAMETER, ERR_MYTV_API_ERROR,
    ERR_NOT_IMPLEMENTED, ERR_TV_UNREACHABLE, ERR_APP_NOT_RESPONDING,
)

if HAS_PIL:
    from PIL import Image

DOUBAO_API_URL = os.environ.get('DOUBAO_API_URL', 'http://192.168.2.200:9090/v1/chat/completions')
DOUBAO_API_KEY = os.environ.get('DOUBAO_API_KEY', '')
DOUBAO_MODEL = os.environ.get('DOUBAO_MODEL', 'doubao-seed-1-6')


def tool_analyze(prompt=None, quality='low'):
    """截图分析：截图 → doubao2api 多模态 → 返回分析结果。"""
    if not DOUBAO_API_KEY:
        return None, ERR_INVALID_PARAMETER, 'DOUBAO_API_KEY 未配置'

    shot, error, msg = tool_screenshot_smart(fmt='json', quality=quality)
    if error:
        return None, error, msg

    if not prompt:
        prompt = '请描述这个电视屏幕上的内容，包括：当前在哪个应用、正在播放什么节目、屏幕上有什么文字或按钮。简洁回答。'

    payload = {
        'model': DOUBAO_MODEL,
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': f"data:image/png;base64,{shot['png_base64']}"}}
            ]
        }],
        'max_tokens': 500
    }

    try:
        req = ulib_req.Request(
            DOUBAO_API_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {DOUBAO_API_KEY}'
            },
            method='POST'
        )
        with ulib_req.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            answer = data['choices'][0]['message']['content']
            return {
                'answer': answer,
                'quality': quality,
                'image_bytes': shot['bytes'],
                'model': DOUBAO_MODEL
            }, None, None
    except Exception as e:
        return None, ERR_MYTV_API_ERROR, f'doubao2api 调用失败: {e}'


def tool_ocr(text_filter=None):
    """OCR 文字识别（备选方案，优先使用 a11y/text）。"""
    try:
        subprocess.run(['tesseract', '--version'], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, ERR_NOT_IMPLEMENTED, 'OCR 未安装（tesseract 不可用），请使用 /api/a11y/text 无障碍文字提取'

    png = screencap_raw()
    if png is None:
        return None, ERR_TV_UNREACHABLE, '截图失败'

    try:
        if not HAS_PIL:
            return None, ERR_NOT_IMPLEMENTED, 'PIL 未安装，OCR 不可用'
        img = Image.open(io.BytesIO(png))
        w, h = img.size
        if w < 1920:
            img = img.resize((1920, int(h * 1920 / w)), Image.LANCZOS)

        ts = str(int(time.time() * 1000))
        tmp_img = f'/tmp/tvpilot_ocr_{ts}.png'
        tmp_out = f'/tmp/tvpilot_ocr_{ts}'
        img.save(tmp_img, format='PNG')

        cmd = ['tesseract', tmp_img, tmp_out, '-l', 'chi_sim+eng', '--psm', '6']
        subprocess.run(cmd, capture_output=True, timeout=30)

        with open(tmp_out + '.txt', 'r', encoding='utf-8') as f:
            raw_text = f.read()

        for p in (tmp_img, tmp_out + '.txt'):
            try:
                os.remove(p)
            except Exception:
                pass

        lines = [line.strip() for line in raw_text.split('\n') if line.strip()]

        if text_filter:
            lines = [l for l in lines if text_filter in l]

        return {'count': len(lines), 'lines': lines, 'raw_length': len(raw_text)}, None, None

    except Exception as e:
        return None, ERR_APP_NOT_RESPONDING, f'OCR 识别失败: {e}'


__all__ = [
    'DOUBAO_API_URL', 'DOUBAO_API_KEY', 'DOUBAO_MODEL',
    'tool_analyze', 'tool_ocr',
]
