"""TVPilot 台标生成层。

绞杀者模式阶段20迁移：M3U tvg-logo 注入 + 频道台标 PNG 生成（CCTV 红蓝配色 + 卫视代表色 + 渐变圆角）。
"""
import io
import re
import hashlib
import urllib.parse

from tvpilot.screenshot import HAS_PIL

if HAS_PIL:
    from PIL import Image, ImageDraw, ImageFont

_logo_cache = {}

TVPILOT_LOGO_BASE = 'http://192.168.2.200:8090/iptv/logo'


def inject_tvg_logos(content_bytes):
    """为 M3U 中每个 #EXTINF 行注入 tvg-logo 属性（指向 TVPilot 台标代理）。"""
    try:
        text = content_bytes.decode('utf-8')
        lines = text.split('\n')
        result = []
        for line in lines:
            if line.startswith('#EXTINF'):
                comma_idx = line.rfind(',')
                if comma_idx > 0:
                    channel_name = line[comma_idx + 1:].strip()
                    logo_url = f'{TVPILOT_LOGO_BASE}?channel={urllib.parse.quote(channel_name)}'
                    if 'tvg-logo' not in line:
                        line = line.replace('#EXTINF:', f'#EXTINF: tvg-logo="{logo_url}" ', 1)
            result.append(line)
        return '\n'.join(result).encode('utf-8')
    except Exception as e:
        print(f'注入 tvg-logo 失败: {e}', flush=True)
        return content_bytes


def generate_channel_logo(channel_name, size=200):
    """生成频道台标 PNG。CCTV 系列用英文 'CCTV-X' 格式+经典红蓝配色；卫视频道用代表色+简称。"""
    global _logo_cache
    cache_key = f'{channel_name}_{size}'
    if cache_key in _logo_cache:
        return _logo_cache[cache_key]

    if not HAS_PIL:
        return None

    try:
        # CCTV 系列识别
        cctv_match = re.match(r'中央([一二三四五六七八九十]+)台', channel_name)
        cctv_num_map = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,
                        '十一':11,'十二':12,'十三':13,'十四':14,'十五':15}
        is_cctv = False
        cctv_num = 0
        cctv_text = ''
        if cctv_match and cctv_match.group(1) in cctv_num_map:
            is_cctv = True
            cctv_num = cctv_num_map[cctv_match.group(1)]
            cctv_text = f'CCTV-{cctv_num}'
        elif channel_name == 'CCTV4K超高清':
            is_cctv = True
            cctv_text = 'CCTV-4K'
        elif '4K' in channel_name and '中央' in channel_name:
            is_cctv = True
            cctv_text = 'CCTV-4K'

        # 卫视频道代表色
        satellite_colors = {
            '湖南卫视': (255, 102, 0), '浙江卫视': (0, 102, 204), '江苏卫视': (204, 0, 0),
            '东方卫视': (204, 0, 51), '北京卫视': (204, 0, 0), '广东卫视': (0, 153, 51),
            '深圳卫视': (0, 102, 204), '山东卫视': (204, 102, 0), '安徽卫视': (255, 102, 153),
            '天津卫视': (0, 102, 204), '重庆卫视': (204, 0, 0), '四川卫视': (0, 102, 204),
            '湖北卫视': (204, 0, 0), '河南卫视': (255, 102, 0), '河北卫视': (0, 102, 204),
            '辽宁卫视': (204, 102, 0), '吉林卫视': (0, 102, 204), '黑龙江卫视': (0, 51, 153),
            '江西卫视': (204, 0, 0), '云南卫视': (0, 153, 51), '贵州卫视': (0, 102, 204),
            '广西卫视': (0, 153, 51), '陕西卫视': (204, 0, 0), '甘肃卫视': (204, 102, 0),
            '青海卫视': (0, 153, 51), '宁夏卫视': (204, 0, 0), '新疆卫视': (0, 102, 204),
            '西藏卫视': (204, 0, 0), '内蒙古卫视': (0, 102, 204), '海南卫视': (0, 153, 204),
            '福建卫视': (0, 102, 204), '东南卫视': (0, 102, 204), '旅游卫视': (0, 153, 51),
        }

        # 确定背景色和文字
        if is_cctv:
            if cctv_num == 5:
                r1, g1, b1 = (0, 80, 180)
            else:
                r1, g1, b1 = (200, 30, 30)
            text = cctv_text
            font_size_ratio = 0.30
        elif channel_name in satellite_colors:
            r1, g1, b1 = satellite_colors[channel_name]
            text = channel_name.replace('卫视', '')
            font_size_ratio = 0.38
        else:
            h = int(hashlib.md5(channel_name.encode('utf-8')).hexdigest()[:6], 16)
            r1 = (h >> 16) & 0xFF
            g1 = (h >> 8) & 0xFF
            b1 = h & 0xFF
            text = channel_name[:2] if len(channel_name) >= 2 else channel_name
            font_size_ratio = 0.38

        r2, g2, b2 = max(0, r1 - 60), max(0, g1 - 60), max(0, b1 - 60)

        img = Image.new('RGB', (size, size), (r1, g1, b1))
        draw = ImageDraw.Draw(img)

        # 渐变
        for y in range(size):
            ratio = y / size
            r = int(r1 + (r2 - r1) * ratio)
            g = int(g1 + (g2 - g1) * ratio)
            b = int(b1 + (b2 - b1) * ratio)
            draw.line([(0, y), (size, y)], fill=(r, g, b))

        # 圆角遮罩
        mask = Image.new('L', (size, size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle([(0, 0), (size-1, size-1)], radius=size//5, fill=255)
        output = Image.new('RGB', (size, size), (0, 0, 0))
        output.paste(img, (0, 0), mask)
        draw = ImageDraw.Draw(output)

        # 字体
        font = None
        font_paths = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                      '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
                      '/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc',
                      '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc']
        for font_path in font_paths:
            try:
                font = ImageFont.truetype(font_path, size=int(size * font_size_ratio))
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()

        # 文字居中
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = (size - tw) // 2 - bbox[0]
        y = (size - th) // 2 - bbox[1]

        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 128))
        draw.text((x, y), text, font=font, fill=(255, 255, 255))

        buf = io.BytesIO()
        output.save(buf, format='PNG')
        png = buf.getvalue()
        _logo_cache[cache_key] = png
        return png
    except Exception as e:
        print(f'台标生成失败 {channel_name}: {e}', flush=True)
        return None


__all__ = [
    '_logo_cache', 'TVPILOT_LOGO_BASE',
    'inject_tvg_logos', 'generate_channel_logo',
]
