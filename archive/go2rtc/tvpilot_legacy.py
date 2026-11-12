"""TVPilot 遗留功能归档（不删，万一哪天要用）。

绞杀者模式阶段10迁移：go2rtc 预热/鉴权、mytv HTTP 代理。
用户明确要求"划掉归档不删"，故保留完整实现但不再是主链路。
自包含：所有配置从环境变量读。
"""
import os
import time
import base64
import urllib.request
import urllib.error

from tvpilot.errors import ERR_MYTV_API_ERROR

GO2RTC_BASE = os.environ.get('GO2RTC_BASE_URL', 'http://192.168.2.200:1984').rstrip('/')
GO2RTC_AUTH_USER = os.environ.get('GO2RTC_USER') or None
GO2RTC_AUTH_PASS = os.environ.get('GO2RTC_PASS') or None
MYTV_HTTP = os.environ.get('MYTV_HTTP', 'http://192.168.2.238:10482')


def go2rtc_basic_auth_header():
    """生成 go2rtc Basic Auth header（凭据来自环境变量 GO2RTC_USER/PASS）。
    不能在 URL 中嵌入用户名密码（会导致 DNS 解析失败），需用 header。
    返回 None 表示凭据未配置。"""
    if GO2RTC_AUTH_USER and GO2RTC_AUTH_PASS:
        token = base64.b64encode(f'{GO2RTC_AUTH_USER}:{GO2RTC_AUTH_PASS}'.encode()).decode()
        return f'Basic {token}'
    return None


def warmup(name, tries=3):
    """go2rtc HLS 预热（best-effort）。
    请求 go2rtc HLS 播放列表，触发 ffmpeg 进程启动。
    go2rtc exec 源启动 ffmpeg 需要1-3秒，预热可减少换台黑屏和3002错误。
    """
    stream_name = 'iptv_' + urllib.parse.quote(name, safe='')
    url = GO2RTC_BASE + '/api/stream.m3u8?src=' + stream_name
    auth_header = go2rtc_basic_auth_header()
    if not auth_header:
        return False  # go2rtc 凭据未配置，跳过预热
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'Authorization': auth_header})
            body = urllib.request.urlopen(req, timeout=2).read().decode(errors='replace')
            if '.ts' in body or '#EXTM3U' in body:
                return True
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(0.5)
    return False


def proxy_mytv(path_with_query, method='GET', body=None):
    """代理请求到 mytv，返回 (data_bytes, content_type, error)。
    注意：mytv 用 AsyncHttpServer，对 GET 请求带 Content-Type 头会 RemoteDisconnected。
    因此 GET 请求不加 Content-Type，仅 POST 有 body 时才加。
    """
    url = MYTV_HTTP + path_with_query
    try:
        req = urllib.request.Request(url, data=body, method=method)
        if method == 'POST' and body:
            req.add_header('Content-Type', 'application/json; charset=utf-8')
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read(), resp.headers.get('Content-Type', 'application/json'), None
    except urllib.error.HTTPError as e:
        return None, None, (ERR_MYTV_API_ERROR, f'mytv HTTP {e.code}: {e.reason}', 502)
    except Exception as e:
        return None, None, (ERR_MYTV_API_ERROR, f'mytv 不可达: {e}', 502)


__all__ = [
    'GO2RTC_BASE', 'GO2RTC_AUTH_USER', 'GO2RTC_AUTH_PASS', 'MYTV_HTTP',
    'go2rtc_basic_auth_header', 'warmup', 'proxy_mytv',
]
