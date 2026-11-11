#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小米电视 6095 端口 HTTP API 工具（MiTV Assistant Server）

整合了免鉴权接口 + 需签名接口，纯 Python 标准库，零依赖。

免鉴权接口:
    info    获取电视基础信息 (设备名/平台/版本)
    apps    获取已安装应用列表
    start   启动指定应用 (按包名)
    key     发送按键 (power/up/down/left/right/enter/home/back/menu/volumeup/volumedown)

需签名接口:
    shot    截屏 (HMAC-SHA1 opaque 签名, 两步下载)
    play    播放网络视频/图片 (MD5 sign)
    icon    下载应用图标 (getResource, 复用截屏签名)

用法:
    python mitv.py info
    python mitv.py apps
    python mitv.py start com.android.settings
    python mitv.py key home
    python mitv.py shot -o tv.jpg -q 70
    python mitv.py play http://example.com/video.mp4
    python mitv.py icon com.xiaomi.mitv.tvvideocall -o icon.png

所有命令默认 --ip 192.168.2.238，可用 --ip 覆盖。
"""
import os
import hmac
import hashlib
import time
import json
import argparse
import urllib.parse
import urllib.request
from pathlib import Path

# === 固定密钥（来自小米电视助手 App / hass-xiaomi-miot 逆向） ===
API_KEY  = os.environ.get("MITV_API_KEY", "")
HMAC_KEY = os.environ.get("MITV_HMAC_KEY", "")
PLAY_SALT = os.environ.get("MITV_PLAY_SALT", "")

DEFAULT_TIMEOUT = 10
VALID_KEYCODES = [
    "power", "up", "down", "left", "right", "enter",
    "home", "back", "menu", "volumeup", "volumedown",
]


# ---------------------------------------------------------------------------
# 底层工具
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "MiTVRemote/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_get_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    return json.loads(http_get(url, timeout))


def with_opaque(params: dict, token: str | None = None) -> dict:
    """给参数加 timestamp + opaque (HMAC-SHA1)，签名后移除 token。"""
    p = dict(params)
    p["timestamp"] = int(time.time() * 1000)
    p["token"] = token or API_KEY
    opaque = hmac.new(
        HMAC_KEY.encode(),
        urllib.parse.urlencode(p).encode(),
        hashlib.sha1,
    ).hexdigest()
    p.pop("token", None)
    p["opaque"] = opaque
    return p


def controller_url(tv_ip: str, params: dict) -> str:
    return f"http://{tv_ip}:6095/controller?" + urllib.parse.urlencode(params)


def request_url(tv_ip: str, params: dict) -> str:
    return f"http://{tv_ip}:6095/request?" + urllib.parse.urlencode(params)


def expect_success(data: dict, ctx: str = "") -> dict:
    if data.get("msg") != "success":
        raise RuntimeError(f"[{ctx}] 电视返回错误: {json.dumps(data, ensure_ascii=False)}")
    return data.get("data") or {}


# ---------------------------------------------------------------------------
# 免鉴权接口
# ---------------------------------------------------------------------------

def cmd_info(tv_ip: str) -> None:
    """获取电视基础信息。"""
    data = expect_success(
        http_get_json(request_url(tv_ip, {"action": "isalive"})),
        "isalive",
    )
    print(f"设备名  : {data.get('devicename')}")
    print(f"IP      : {data.get('ip')}")
    print(f"平台    : {data.get('platform')}")
    print(f"Build   : {data.get('build')}")
    print(f"版本    : {data.get('version')}")
    print(f"特性    : {', '.join(data.get('feature') or [])}")


def cmd_apps(tv_ip: str) -> None:
    """获取已安装应用列表。"""
    data = expect_success(
        http_get_json(controller_url(tv_ip, {
            "action": "getinstalledapp",
            "count": 999,
            "changeIcon": 1,
        })),
        "getinstalledapp",
    )
    apps = data.get("AppInfo") or []
    print(f"共 {len(apps)} 个应用:\n")
    print(f"{'Order':>5}  {'AppName':<20}  PackageName")
    print("-" * 70)
    for a in sorted(apps, key=lambda x: x.get("Order", 999)):
        print(f"{a.get('Order', 0):>5}  {a.get('AppName',''):<20}  {a.get('PackageName','')}")


def cmd_start(tv_ip: str, package: str) -> None:
    """启动应用。"""
    data = expect_success(
        http_get_json(controller_url(tv_ip, {
            "action": "startapp",
            "type": "packagename",
            "packagename": package,
        })),
        "startapp",
    )
    print(f"已启动: {package}")


def cmd_key(tv_ip: str, keycode: str) -> None:
    """发送按键。"""
    if keycode not in VALID_KEYCODES:
        raise SystemExit(f"未知 keycode: {keycode}\n可用: {', '.join(VALID_KEYCODES)}")
    expect_success(
        http_get_json(controller_url(tv_ip, {
            "action": "keyevent",
            "keycode": keycode,
        })),
        "keyevent",
    )
    print(f"按键已发送: {keycode}")


# ---------------------------------------------------------------------------
# 需签名接口
# ---------------------------------------------------------------------------

def capture_screenshot(tv_ip: str, compress: int = 50) -> dict:
    """第一步：请求截屏，返回资源描述(url/token/前台应用)。"""
    params = with_opaque({"action": "capturescreen", "compressrate": compress})
    data = expect_success(http_get_json(controller_url(tv_ip, params)), "capturescreen")
    return data


def download_resource(tv_ip: str, resource: dict) -> bytes:
    """第二步：用返回的 token 重新签名资源 URL，下载二进制内容。"""
    raw_url = resource["url"]
    parsed = urllib.parse.urlparse(raw_url)
    base = raw_url.replace("?" + parsed.query, "")
    base = base.replace("//null:", f"//{tv_ip}:").replace("//0.0.0.0:", f"//{tv_ip}:")
    res_params = dict(urllib.parse.parse_qsl(parsed.query))
    res_params = with_opaque(res_params, token=resource.get("token"))
    final_url = base + "?" + urllib.parse.urlencode(res_params)
    return http_get(final_url, timeout=15)


def cmd_shot(tv_ip: str, output: str, quality: int, info_only: bool) -> None:
    """截屏。"""
    print("[1/2] 请求截屏 ...")
    res = capture_screenshot(tv_ip, quality)
    print(f"      前台应用: {res.get('label')} ({res.get('pkg')})")
    print(f"      Activity: {res.get('clz')}")
    if info_only:
        print(f"      资源地址: {res.get('url')}")
        return
    print("[2/2] 下载截屏 ...")
    img = download_resource(tv_ip, res)
    out = Path(output).resolve()
    out.write_bytes(img)
    print(f"      已保存 {len(img)} 字节 -> {out}")
    if img[:3] != b"\xff\xd8\xff":
        print("      警告: 文件头不是 JPEG")


def cmd_play(tv_ip: str, url: str, media_type: str) -> None:
    """播放网络媒体（视频/图片），需 MD5 签名。"""
    ts = str(int(time.time() * 1000))
    sign = hashlib.md5(
        f"{PLAY_SALT}{url}{API_KEY}{ts[-5:]}".encode()
    ).hexdigest()
    params = {
        "action": "play",
        "type": media_type,
        "url": url,
        "apikey": API_KEY,
        "ts": ts,
        "sign": sign,
    }
    data = expect_success(http_get_json(controller_url(tv_ip, params)), "play")
    print(f"已请求播放: {url}")
    print(f"返回: {json.dumps(data, ensure_ascii=False)}")


def cmd_icon(tv_ip: str, package: str, output: str) -> None:
    """下载应用图标（先查 apps 拿 IconURL，再用 getResource 下载）。"""
    apps_data = expect_success(
        http_get_json(controller_url(tv_ip, {
            "action": "getinstalledapp", "count": 999, "changeIcon": 1,
        })),
        "getinstalledapp",
    )
    app = next((a for a in apps_data.get("AppInfo", [])
                if a.get("PackageName") == package), None)
    if not app:
        raise SystemExit(f"未找到应用: {package}（用 apps 命令查看列表）")
    icon_url = app.get("IconURL", "")
    print(f"应用: {app.get('AppName')} ({package})")
    print(f"图标地址: {icon_url}")
    # IconURL 是 /request?action=getResource&name=xxx.png，需要签名才能下载
    parsed = urllib.parse.urlparse(icon_url)
    base = icon_url.replace("?" + parsed.query, "")
    base = base.replace("//null:", f"//{tv_ip}:").replace("//0.0.0.0:", f"//{tv_ip}:")
    res_params = with_opaque(dict(urllib.parse.parse_qsl(parsed.query)))
    final_url = base + "?" + urllib.parse.urlencode(res_params)
    img = http_get(final_url, timeout=15)
    out = Path(output).resolve()
    out.write_bytes(img)
    print(f"已保存 {len(img)} 字节 -> {out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="小米电视 6095 端口工具（信息/应用/按键/截屏/播放/图标）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--ip", default="192.168.2.238", help="电视 IP (默认 192.168.2.238)")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="获取电视基础信息")

    sub.add_parser("apps", help="列出已安装应用")

    p_start = sub.add_parser("start", help="启动应用")
    p_start.add_argument("package", help="应用包名，如 com.android.settings")

    p_key = sub.add_parser("key", help="发送按键")
    p_key.add_argument("keycode", help=f"按键: {'/'.join(VALID_KEYCODES)}")

    p_shot = sub.add_parser("shot", help="截屏")
    p_shot.add_argument("-o", "--output", default="tv_shot.jpg", help="输出路径")
    p_shot.add_argument("-q", "--quality", type=int, default=50, help="压缩率 1-100")
    p_shot.add_argument("--info", action="store_true", help="只查前台应用，不下载图片")

    p_play = sub.add_parser("play", help="播放网络媒体")
    p_play.add_argument("url", help="媒体 URL")
    p_play.add_argument("--type", default="video", help="媒体类型: video/image (默认 video)")

    p_icon = sub.add_parser("icon", help="下载应用图标")
    p_icon.add_argument("package", help="应用包名")
    p_icon.add_argument("-o", "--output", default="icon.png", help="输出路径")

    return ap


def main():
    args = build_parser().parse_args()
    ip = args.ip

    if args.command == "info":
        cmd_info(ip)
    elif args.command == "apps":
        cmd_apps(ip)
    elif args.command == "start":
        cmd_start(ip, args.package)
    elif args.command == "key":
        cmd_key(ip, args.keycode)
    elif args.command == "shot":
        cmd_shot(ip, args.output, args.quality, args.info)
    elif args.command == "play":
        cmd_play(ip, args.url, args.type)
    elif args.command == "icon":
        cmd_icon(ip, args.package, args.output)


if __name__ == "__main__":
    main()
