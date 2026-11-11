"""TVPilot FaceID 个性化层。

绞杀者模式阶段16迁移：用户配置管理 + 个性化频道列表 + Arcface 人脸识别消息处理。
"""
import os
import json
import time
import urllib.request
import urllib.parse

from tvpilot.adb import ADB_HOST

# FL-02 修复：ADB_HOST 含 :5555，mytv HTTP 用纯 IP
_TV_IP = ADB_HOST.split(':')[0] if ':' in ADB_HOST else ADB_HOST
MYTV_HTTP_BASE = f'http://{_TV_IP}:10482'

USER_CONFIG_FILE = os.environ.get('TVPILOT_USER_PROFILES', '/app/data/user_profiles.json')
_current_user = 'default'
_face_last_user = None
_face_last_ts = 0
FACE_DEDUP_SECONDS = 5


def load_user_profiles():
    """加载用户配置（用户名 → 收藏频道列表）。"""
    try:
        if os.path.exists(USER_CONFIG_FILE):
            with open(USER_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {'default': {'favorites': [], 'display_name': '默认'}}


def save_user_profiles(profiles):
    """保存用户配置。"""
    try:
        os.makedirs(os.path.dirname(USER_CONFIG_FILE), exist_ok=True)
        with open(USER_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'保存用户配置失败: {e}', flush=True)


def apply_user_profile(username):
    """切换到指定用户的收藏列表（调用 mytv API）。"""
    global _current_user
    profiles = load_user_profiles()
    profile = profiles.get(username, profiles.get('default', {'favorites': []}))
    favorites = profile.get('favorites', [])

    try:
        req = urllib.request.Request(f'{MYTV_HTTP_BASE}/api/favorites')
        with urllib.request.urlopen(req, timeout=5) as r:
            current = json.loads(r.read()).get('favorites', [])
    except Exception:
        current = []

    to_add = [c for c in favorites if c not in current]
    to_remove = [c for c in current if c not in favorites]

    for ch in to_add:
        try:
            data = json.dumps({'channel': ch}).encode('utf-8')
            req = urllib.request.Request(
                f'{MYTV_HTTP_BASE}/api/favorites',
                data=data, headers={'Content-Type': 'application/json'}, method='POST')
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    for ch in to_remove:
        try:
            # FL-05 修复：删除用 DELETE 方法，与添加（POST）区分
            req = urllib.request.Request(
                f'{MYTV_HTTP_BASE}/api/favorites?channel={urllib.parse.quote(ch)}',
                headers={'Content-Type': 'application/json'}, method='DELETE')
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    _current_user = username
    print(f'[FaceID] 已切换到用户 {username}，收藏 {len(favorites)} 个频道', flush=True)


def handle_face_recognition(payload):
    """处理 Arcface 人脸识别 MQTT 消息。"""
    global _face_last_user, _face_last_ts
    try:
        d = json.loads(payload)
        name = d.get('name', '')
        similarity = d.get('similarity', 0)
        if not name or name == '未知' or similarity < 0.5:
            return

        now = time.time()
        if name == _face_last_user and (now - _face_last_ts) < FACE_DEDUP_SECONDS:
            return
        _face_last_user = name
        _face_last_ts = now

        print(f'[FaceID] 识别到用户: {name} (相似度: {similarity:.2f})', flush=True)
        apply_user_profile(name)
    except Exception as e:
        print(f'[FaceID] 处理识别消息失败: {e}', flush=True)


__all__ = [
    'USER_CONFIG_FILE', '_current_user', 'FACE_DEDUP_SECONDS',
    'load_user_profiles', 'save_user_profiles',
    'apply_user_profile', 'handle_face_recognition',
]
