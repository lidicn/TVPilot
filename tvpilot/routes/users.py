"""TVPilot 用户/FaceID 路由。

绞杀者模式阶段3-4：/api/users, /api/users/switch, /api/face/restore, /api/face/backup, /api/history。
"""
import os
import json

from tvpilot.faceid import load_user_profiles, save_user_profiles, apply_user_profile, _current_user
from tvpilot.ops_log import read_ops_history

DATA_DIR = os.environ.get('TVPILOT_DATA_DIR', '/app/data')


def handle_users_get(handler, path, q):
    """/api/users GET：列出用户配置。"""
    if path != '/api/users':
        return False
    profiles = load_user_profiles()
    handler._json({'ok': True, 'current_user': _current_user,
                   'users': list(profiles.keys()), 'profiles': profiles})
    return True


def handle_users_switch(handler, path, q):
    """/api/users/switch：切换用户。"""
    if path != '/api/users/switch':
        return False
    username = q.get('user', ['default'])[0]
    apply_user_profile(username)
    handler._json({'ok': True, 'switched_to': username})
    return True


def handle_face_restore(handler, path, q):
    """/api/face/restore：TV 端启动时拉取人脸备份。"""
    if path != '/api/face/restore':
        return False
    backup_path = os.path.join(DATA_DIR, 'face_backup.json')
    if not os.path.exists(backup_path):
        handler._json({'ok': True, 'faces': [], 'message': 'no backup'})
        return True
    try:
        with open(backup_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        handler._json({'ok': True, 'faces': data.get('faces', []),
                       'updated_at': data.get('updated_at', 0)})
    except Exception as e:
        handler._json({'ok': False, 'error': str(e)}, 500)
    return True


def handle_history(handler, path, q):
    """/api/history：操作历史查询。"""
    if path != '/api/history':
        return False
    limit = max(1, min(int(q.get('limit', ['50'])[0]), 500))
    request_id = q.get('request_id', [None])[0]
    records = read_ops_history(limit=limit, request_id=request_id)
    handler._json({'ok': True, 'tool': 'history',
                   'result': {'count': len(records), 'records': records,
                              'limit': limit, 'request_id': request_id},
                   'cost_ms': 0})
    return True


def handle_users_get_all(handler, path, q):
    """统一分发：所有用户/FaceID GET 路由。"""
    if handle_users_get(handler, path, q):
        return True
    if handle_users_switch(handler, path, q):
        return True
    if handle_face_restore(handler, path, q):
        return True
    if handle_history(handler, path, q):
        return True
    return False


def handle_users_post(handler, path, body):
    """统一分发：所有用户/FaceID POST 路由。"""
    # /api/users POST：保存用户配置
    if path == '/api/users':
        username = body.get('user', '')
        favorites = body.get('favorites', [])
        display_name = body.get('display_name', username)
        if not username:
            handler._json({'ok': False, 'error': 'user is required'}, 400)
            return True
        profiles = load_user_profiles()
        profiles[username] = {'favorites': favorites, 'display_name': display_name}
        save_user_profiles(profiles)
        handler._json({'ok': True, 'user': username, 'favorites': favorites,
                       'total_users': len(profiles)})
        return True

    # /api/face/backup POST：保存人脸备份
    if path == '/api/face/backup':
        import time
        faces = body.get('faces', [])
        if not isinstance(faces, list):
            handler._json({'ok': False, 'error': 'faces must be a list'}, 400)
            return True
        backup_path = os.path.join(DATA_DIR, 'face_backup.json')
        with open(backup_path, 'w', encoding='utf-8') as f:
            json.dump({'faces': faces, 'updated_at': time.time()}, f, ensure_ascii=False)
        handler._json({'ok': True, 'count': len(faces), 'file': backup_path})
        return True

    return False


__all__ = [
    'handle_users_get', 'handle_users_switch', 'handle_face_restore',
    'handle_history', 'handle_users_get_all', 'handle_users_post',
]
