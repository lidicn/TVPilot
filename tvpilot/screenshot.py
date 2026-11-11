"""TVPilot 截图层。

v1.1：小米电视 6095 端口 capturescreen 优先（能抓视频硬件 overlay 画面），ADB screencap 降级。
截图直接返回 JPEG（6095 已压缩），不再重复转 PNG。
"""
import os
import io
import time
import base64
import subprocess
import hmac
import hashlib
import json
import urllib.parse
import urllib.request

from tvpilot.adb import _CMD_LOCK, adb_ready, ADB, ADB_HOST
from tvpilot.errors import ERR_TV_UNREACHABLE

# Pillow 可选导入（用于截图压缩/格式转换）
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# 电视 IP（从 ADB_HOST 提取，去掉 :5555）
TV_IP = ADB_HOST.split(':')[0] if ':' in ADB_HOST else ADB_HOST
MITV_PORT = 6095

# 小米电视 6095 端口固定密钥（逆向自小米电视助手 App）
MITV_API_KEY = os.environ.get("MITV_API_KEY", "")
MITV_HMAC_KEY = os.environ.get("MITV_HMAC_KEY", "")


def _mitv_opaque(params, token=None):
    """给参数加 timestamp + opaque (HMAC-SHA1) 签名。"""
    p = dict(params)
    p["timestamp"] = int(time.time() * 1000)
    p["token"] = token or MITV_API_KEY
    opaque = hmac.new(
        MITV_HMAC_KEY.encode(),
        urllib.parse.urlencode(p).encode(),
        hashlib.sha1,
    ).hexdigest()
    p.pop("token", None)
    p["opaque"] = opaque
    return p


def _mitv_get(params, timeout=10):
    """6095 端口 GET 请求，返回 JSON data 或 None。"""
    try:
        url = f"http://{TV_IP}:{MITV_PORT}/controller?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "MiTVRemote/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
        if data.get("msg") != "success":
            return None
        return data.get("data") or {}
    except Exception as e:
        print(f'mitv_get error: {e}', flush=True)
        return None


def screencap_mitv(compress=70):
    """小米电视 6095 端口截图（能抓视频画面）。

    返回 (jpeg_bytes, foreground_info) 或 (None, None)。
    foreground_info: {'label': 应用名, 'pkg': 包名, 'clz': activity}
    """
    try:
        # 第一步：请求截屏
        params = _mitv_opaque({"action": "capturescreen", "compressrate": compress})
        res = _mitv_get(params, timeout=10)
        if res is None:
            return None, None

        fg_info = {k: res.get(k) for k in ('label', 'pkg', 'clz')}

        # 第二步：下载截图
        raw_url = res["url"]
        parsed = urllib.parse.urlparse(raw_url)
        base = raw_url.replace("?" + parsed.query, "")
        base = base.replace("//null:", f"//{TV_IP}:").replace("//0.0.0.0:", f"//{TV_IP}:")
        res_params = _mitv_opaque(dict(urllib.parse.parse_qsl(parsed.query)), token=res.get("token"))
        final_url = base + "?" + urllib.parse.urlencode(res_params)
        req = urllib.request.Request(final_url, headers={"User-Agent": "MiTVRemote/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            img = r.read()

        if len(img) > 100:
            return img, fg_info
        return None, None
    except Exception as e:
        print(f'screencap_mitv error: {e}', flush=True)
        return None, None


def get_foreground_mitv(timeout=3):
    """通过 6095 端口获取前台应用信息（不下载截图，更快）。

    返回 {'label': 应用名, 'pkg': 包名, 'clz': activity} 或 None。
    v2：超时从 8s 降到 3s，超时后调用方应降级到 ADB dumpsys。
    """
    try:
        params = _mitv_opaque({"action": "capturescreen", "compressrate": 10})
        res = _mitv_get(params, timeout=timeout)
        if res is None:
            return None
        return {k: res.get(k) for k in ('label', 'pkg', 'clz')}
    except Exception as e:
        print(f'get_foreground_mitv error (timeout={timeout}s): {e}', flush=True)
        return None


def mitv_key(keycode):
    """6095 端口发送按键。keycode: power/up/down/left/right/enter/home/back/menu/volumeup/volumedown。"""
    valid = ("power", "up", "down", "left", "right", "enter",
             "home", "back", "menu", "volumeup", "volumedown")
    if keycode not in valid:
        return False
    res = _mitv_get({"action": "keyevent", "keycode": keycode}, timeout=8)
    return res is not None


def mitv_start_app(package):
    """6095 端口启动应用。"""
    res = _mitv_get({
        "action": "startapp",
        "type": "packagename",
        "packagename": package,
    }, timeout=10)
    return res is not None


def mitv_info():
    """6095 端口获取电视信息。"""
    try:
        url = f"http://{TV_IP}:{MITV_PORT}/request?action=isalive"
        req = urllib.request.Request(url, headers={"User-Agent": "MiTVRemote/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode('utf-8'))
        if data.get("msg") != "success":
            return None
        return data.get("data") or {}
    except Exception:
        return None


def screencap_raw():
    """截图返回字节（优先 6095 JPEG，降级 ADB PNG）。

    6095 端口能抓视频硬件 overlay 画面，ADB screencap 对视频黑屏。
    v2：6095 失败时重试一次，减少间歇性故障。
    """
    # 优先用 6095 端口截图（JPEG，能抓视频画面），失败重试一次
    for attempt in range(2):
        jpg, _ = screencap_mitv()
        if jpg:
            return jpg
        if attempt == 0:
            time.sleep(0.3)  # 短暂等待后重试

    # 降级：ADB screencap（PNG，视频界面会黑屏）
    ts = str(int(time.time() * 1000))
    local = f'/tmp/tvpilot_scr_{ts}.png'
    try:
        with _CMD_LOCK:
            adb_ready()
            with open(local, 'wb') as f:
                subprocess.run(ADB + ['exec-out', 'screencap', '-p'],
                               stdout=f, timeout=30)
        with open(local, 'rb') as f:
            raw = f.read()
        os.remove(local)
        start = raw.find(b'\x89PNG\r\n\x1a\n')
        if start < 0:
            return None
        end = raw.rfind(b'IEND\xaeB`\x82')
        raw = raw[start:end + 8] if end > start else raw[start:]
        return raw if len(raw) > 100 else None
    except Exception as e:
        print(f'screencap error: {e}', flush=True)
        try:
            if os.path.exists(local):
                os.remove(local)
        except Exception:
            pass
        return None


def compress_image(img_bytes, width=None):
    """压缩截图到指定宽度；无 Pillow 或失败则返回原图。"""
    if not width or not HAS_PIL:
        return img_bytes
    try:
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        if width >= w:
            return img_bytes
        new_h = int(h * width / w)
        img = img.resize((width, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        # 保持原格式（JPEG/PNG）
        fmt = 'JPEG' if img.format == 'JPEG' else 'PNG'
        img.save(buf, format=fmt, quality=80 if fmt == 'JPEG' else None)
        return buf.getvalue()
    except Exception as e:
        print(f'compress_image error: {e}', flush=True)
        return img_bytes


def tool_screenshot(fmt='png', width=None):
    """截图工具。返回 (result, error, message)。"""
    img = screencap_raw()
    if img is None:
        return None, ERR_TV_UNREACHABLE, '截图失败，电视可能离线'
    img = compress_image(img, width=width)
    if fmt == 'json':
        return {'bytes': len(img), 'image_base64': base64.b64encode(img).decode(),
                'width': width or 'original', 'format': 'jpeg' if img[:3] == b'\xff\xd8\xff' else 'png'}, None, None
    return {'image_bytes': img, 'bytes': len(img)}, None, None


def tool_screenshot_smart(fmt='json', width=480, quality='medium'):
    """智能截图：自动压缩，减少多模态 token 消耗。
    quality: low(240p) / medium(480p) / high(720p) / original
    """
    quality_map = {'low': 240, 'medium': 480, 'high': 720, 'original': None}
    target_width = quality_map.get(quality, width)

    img = screencap_raw()
    if img is None:
        return None, ERR_TV_UNREACHABLE, '截图失败，电视可能离线'

    original_size = len(img)
    img = compress_image(img, width=target_width)

    if fmt == 'json':
        return {
            'image_base64': base64.b64encode(img).decode(),
            'bytes': len(img),
            'original_bytes': original_size,
            'width': target_width or 'original',
            'quality': quality,
            'format': 'jpeg' if img[:3] == b'\xff\xd8\xff' else 'png',
            'compression_ratio': round(len(img) / original_size, 3) if original_size > 0 else 1.0
        }, None, None
    return {'image_bytes': img, 'bytes': len(img), 'original_bytes': original_size}, None, None


def draw_highlight(img_bytes, elements, max_elements=30):
    """在截图上给每个可交互元素画编号（红圈+白色数字）。

    Args:
        img_bytes: 原始截图字节（JPEG/PNG）
        elements: ui_dump 返回的元素列表，每个含 index/center/clickable
        max_elements: 最多画多少个元素（防止太多导致图太大）

    Returns:
        标注后的图片字节（JPEG），失败返回原图
    """
    if not HAS_PIL or not elements:
        return img_bytes
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
        draw = ImageDraw.Draw(img, 'RGBA')
        w, h = img.size

        # 尝试加载字体，失败用默认
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        except Exception:
            try:
                font = ImageFont.truetype("/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc", 20)
            except Exception:
                font = ImageFont.load_default()

        # 元素坐标是电视原生分辨率（通常1920x1080），按截图实际尺寸缩放
        # 我们需要知道电视分辨率，假设常见 1920x1080
        # 但更可靠的方式：用元素 bounds 的最大值推算
        # 简单方案：直接按比例缩放（假设元素坐标是 1080p）
        # 实际上 uiautomator dump 返回的就是当前屏幕分辨率的坐标
        # 截图可能被压缩了，需要按比例映射

        # 计算缩放比例：截图宽度 / 假设电视宽度
        # 从元素中取最大 x 坐标作为参考
        max_x = max((e.get('bounds', [0,0,0,0])[2] for e in elements), default=1920)
        max_y = max((e.get('bounds', [0,0,0,0])[3] for e in elements), default=1080)
        scale_x = w / max(max_x, 1)
        scale_y = h / max(max_y, 1)

        count = 0
        for el in elements:
            if count >= max_elements:
                break
            center = el.get('center')
            if not center:
                continue
            cx, cy = int(center[0] * scale_x), int(center[1] * scale_y)

            # 颜色：可点击=红色，其他=黄色
            color = (255, 60, 60, 200) if el.get('clickable') else (255, 200, 0, 180)
            radius = max(12, min(w, h) // 30)

            # 画半透明圆
            draw.ellipse([cx-radius, cy-radius, cx+radius, cy+radius],
                        fill=color, outline=(255, 255, 255, 255), width=2)

            # 写数字
            idx = el.get('index', count)
            text = str(idx)
            # 计算文字位置居中
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
            draw.text((cx - tw//2, cy - th//2 - 2), text,
                     fill=(255, 255, 255, 255), font=font)
            count += 1

        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        return buf.getvalue()
    except Exception as e:
        print(f'draw_highlight error: {e}', flush=True)
        return img_bytes


__all__ = [
    'HAS_PIL', 'screencap_raw', 'screencap_mitv', 'get_foreground_mitv',
    'mitv_key', 'mitv_start_app', 'mitv_info',
    'compress_image', 'tool_screenshot', 'tool_screenshot_smart',
    'draw_highlight',
]
