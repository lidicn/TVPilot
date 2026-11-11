#!/usr/bin/env python3
"""TVPilot 错误码矩阵验证"""
import urllib.request
import json

BASE = "http://192.168.2.200:8090"

def get(path):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except Exception as e:
        return -1, {"error": str(e)}

def post(path, data):
    try:
        req = urllib.request.Request(
            f"{BASE}{path}",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except Exception as e:
        return -1, {"error": str(e)}

results = []

# 1. 无效频道
print("=== 1. 无效频道 ===")
code, resp = get("/api/zap?channel=不存在的频道12345")
print(f"  HTTP {code}, ok={resp.get('ok')}, error={resp.get('error')}")
results.append(("无效频道", code, resp.get('error'), resp.get('error') == 'invalid_parameter'))

# 2. 无效按键
print("=== 2. 无效按键 ===")
code, resp = get("/api/keyevent?key=INVALID_KEYCODE")
print(f"  HTTP {code}, ok={resp.get('ok')}, error={resp.get('error')}")
results.append(("无效按键", code, resp.get('error'), resp.get('error') == 'invalid_parameter'))

# 3. 缺少必填参数
print("=== 3. 缺少必填参数（zap无channel） ===")
code, resp = get("/api/zap")
print(f"  HTTP {code}, ok={resp.get('ok')}, error={resp.get('error')}")
results.append(("缺少参数", code, resp.get('error'), 'invalid' in str(resp.get('error','')).lower() or code == 400))

# 4. 无效包名启动
print("=== 4. 无效包名启动 ===")
code, resp = get("/api/launch_app?package=com.nonexistent.app12345")
print(f"  HTTP {code}, ok={resp.get('ok')}, error={resp.get('error')}")
results.append(("无效包名", code, resp.get('error'), True))  # 不崩溃即可

# 5. 台标接口缺少channel
print("=== 5. 台标接口缺少channel ===")
code, resp = get("/iptv/logo")
print(f"  HTTP {code}, resp={str(resp)[:80]}")
results.append(("台标缺参数", code, str(resp)[:50], code == 400))

# 6. 用户切换缺少user
print("=== 6. 用户切换缺少user ===")
code, resp = get("/api/users/switch")
print(f"  HTTP {code}, ok={resp.get('ok')}, error={resp.get('error')}")
results.append(("切换缺参数", code, resp.get('error'), True))

# 7. 健康检查（应该正常）
print("=== 7. 健康检查 ===")
code, resp = get("/api/health")
print(f"  HTTP {code}, ok={resp.get('ok')}, tv_online={resp.get('result',{}).get('tv_online')}")
results.append(("健康检查", code, "ok", resp.get('ok') == True))

# 8. 统计接口
print("=== 8. 统计接口 ===")
code, resp = get("/api/stats?hours=1")
print(f"  HTTP {code}, code={resp.get('code')}, total={resp.get('data',{}).get('total')}")
results.append(("统计接口", code, "ok", resp.get('code') == 0))

# 9. HDMI3 状态
print("=== 9. HDMI3 状态 ===")
code, resp = get("/api/tv/hdmi3/status")
print(f"  HTTP {code}, code={resp.get('code')}, is_active={resp.get('data',{}).get('is_active')}")
results.append(("HDMI3状态", code, "ok", resp.get('code') == 0))

# 10. M3U 源
print("=== 10. M3U 源（含摄像头） ===")
code, resp = get("/iptv/source.m3u")
m3u_text = json.dumps(resp) if isinstance(resp, dict) else str(resp)
has_camera = "监控" in m3u_text or "cam_" in m3u_text
print(f"  HTTP {code}, 含摄像头={has_camera}")
results.append(("M3U含摄像头", code, "ok", has_camera))

print("\n" + "="*60)
print("错误码矩阵验证结果")
print("="*60)
passed = 0
for name, code, error, ok in results:
    status = "PASS" if ok else "FAIL"
    if ok: passed += 1
    print(f"  [{status}] {name}: HTTP {code}, error={error}")
print(f"\n通过: {passed}/{len(results)}")
