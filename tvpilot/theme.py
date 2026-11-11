"""TVPilot 主题逻辑层 —— TVPilot Theme Spec v1。

供 MCP 工具（tvpilot/mcp.py）调用，不含 HTTP 路由包装。
源目录 /app/data/themes（data/ 已挂载），目标 /sdcard/TVPilot/themes。

设计：离线优先、三级回退由电视端负责；本层只做「校验 + 列举 + adb push」。
P1（AF-PM 11-11 裁定 ④）：下发前接 validate_manifest_keys(strict=True)，
fail-closed；不合格 manifest 不下发，报错体带 violations[{key, reason}]。
"""
import os
import json
import re
import subprocess
import time

from tvpilot.adb import ADB, adb_shell_output
from tvpilot import theme_violations

THEMES_DIR = os.environ.get('TVPILOT_THEMES_DIR', '/app/data/themes')
TV_DIR = '/sdcard/TVPilot/themes'

# 与 Arcface 人脸名一致；未知用户拒绝，避免路径乱写
KNOWN_USERS = ('lidicn', 'Emily', 'Kevin')

ALLOWED_NAMES = re.compile(r'^[A-Za-z0-9._-]+$')
WALLPAPER_NAMES = ('wallpaper.webp', 'wallpaper.png', 'wallpaper.jpg', 'wallpaper.jpeg')
AVATAR_NAMES = ('avatar.png', 'avatar.webp')

MAX_WALLPAPER = 8 * 1024 * 1024
MAX_AVATAR = 4 * 1024 * 1024
MAX_JSON = 64 * 1024

SPEC_SUMMARY = {
    'version': 1,
    'doc': 'THEME_SPEC.md',
    'dir_nas': THEMES_DIR,
    'dir_tv': TV_DIR,
    'users': list(KNOWN_USERS),
    'files': {
        'theme.json': 'required, JSON manifest',
        'wallpaper': 'optional, 1920x1080 webp/png/jpg, <=8MB',
        'avatar': 'optional, square >=256px png/webp, <=4MB',
    },
    'anim_styles': {'0': 'scan-ring', '1': 'space', '2': 'fairy'},
    'color_fields': ['bg_top', 'bg_bottom', 'glow', 'anim_primary',
                     'anim_secondary', 'anim_bg_top', 'anim_bg_mid', 'anim_bg_bottom'],
    'fallback': 'external -> builtin UserTheme -> code gradient',
}


def _safe_name(name):
    return bool(name) and bool(ALLOWED_NAMES.match(name)) and '..' not in name


def _user_dir(user):
    return os.path.join(THEMES_DIR, user)


def _read_manifest(user):
    """读一份 theme.json，返回 (data, violations)。只做基础/契约校验，不碰设备。"""
    if not _safe_name(user):
        return None, [{'key': 'user', 'reason': 'invalid user name'}]
    d = _user_dir(user)
    if not os.path.isdir(d):
        return None, [{'key': 'theme_dir', 'reason': 'theme dir not found: %s' % d}]
    manifest = os.path.join(d, 'theme.json')
    if not os.path.isfile(manifest):
        return None, [{'key': 'theme.json', 'reason': 'theme.json missing'}]
    if os.path.getsize(manifest) > MAX_JSON:
        return None, [{'key': 'theme.json', 'reason': 'theme.json too large'}]
    try:
        with open(manifest, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError('not an object')
        if int(data.get('version', 1)) != 1:
            return None, [{'key': 'version', 'reason': 'unsupported theme version'}]
    except Exception as e:
        return None, [{'key': 'theme.json', 'reason': 'bad theme.json: %s' % e}]

    # 契约硬闸：fail-closed，唯一真源在 tvpilot/theme_contract.py。
    res, violations = theme_violations.validate(data, strict=True)
    if not res['ok']:
        return None, violations
    return data, []


def _asset_files(user, data):
    """按文件名白名单挑出实际存在的素材，返回 (ok, files, violations)。"""
    d = _user_dir(user)
    files = ['theme.json']
    declared = []
    for key in ('wallpaper', 'avatar'):
        v = data.get(key)
        if isinstance(v, str) and _safe_name(v):
            declared.append(v)
    for n in WALLPAPER_NAMES + AVATAR_NAMES:
        if n in declared or os.path.isfile(os.path.join(d, n)):
            if n not in files:
                files.append(n)
    for n in files[1:]:
        p = os.path.join(d, n)
        if not os.path.isfile(p):
            continue
        limit = MAX_AVATAR if n.startswith('avatar') else MAX_WALLPAPER
        if os.path.getsize(p) > limit:
            return False, [], [{'key': n, 'reason': '%s exceeds size limit' % n}]
    files = [n for n in files if os.path.isfile(os.path.join(d, n))]
    return True, files, []


def validate(user):
    """校验一个主题目录，返回 (ok, files, error)。

    契约不合格时 fail-closed：ok=False，且不下发任何文件。
    """
    data, violations = _read_manifest(user)
    if data is None:
        return False, [], '; '.join(
            '%s: %s' % (v['key'], v['reason']) for v in violations)
    ok, files, violations = _asset_files(user, data)
    if not ok:
        return False, [], '; '.join(
            '%s: %s' % (v['key'], v['reason']) for v in violations)
    return True, files, None


def validate_manifest(user):
    """返回 (ok, violations[{key, reason}])；供 push 报错体结构化输出。"""
    data, violations = _read_manifest(user)
    return data is not None, violations


def list_themes():
    """列出 NAS 侧主题及文件状态。"""
    out = {}
    if not os.path.isdir(THEMES_DIR):
        return out
    for user in sorted(os.listdir(THEMES_DIR)):
        if not _safe_name(user):
            continue
        d = _user_dir(user)
        if not os.path.isdir(d):
            continue
        ok, _files, err = validate(user)
        out[user] = {
            'ok': ok, 'error': err,
            'files': [{'name': n, 'bytes': os.path.getsize(os.path.join(d, n))}
                      for n in sorted(os.listdir(d))
                      if _safe_name(n) and os.path.isfile(os.path.join(d, n))],
        }
    return out


def tv_status(user=None):
    """通过 adb 读电视侧主题目录。"""
    adb_shell_output('mkdir -p %s' % TV_DIR, timeout=8)
    users = [user] if user else list(KNOWN_USERS)
    out = {}
    for u in users:
        if not _safe_name(u):
            continue
        d = '%s/%s' % (TV_DIR, u)
        raw = adb_shell_output('ls -l %s 2>/dev/null' % d, timeout=8) or ''
        names = []
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) >= 5 and _safe_name(parts[-1]):
                try:
                    names.append({'name': parts[-1], 'bytes': int(parts[-2])})
                except ValueError:
                    names.append({'name': parts[-1]})
        out[u] = names
    return out


def _push_one(user):
    """下发单个用户：契约闸在最前，不合格即拒，绝不调用 adb。"""
    data, violations = _read_manifest(user)
    if data is None:
        return {'user': user, 'ok': False,
                'error': 'manifest validation failed', 'violations': violations}
    ok, files, violations = _asset_files(user, data)
    if not ok:
        return {'user': user, 'ok': False,
                'error': 'asset validation failed', 'violations': violations}

    d = _user_dir(user)
    tv_dir = '%s/%s' % (TV_DIR, user)
    adb_shell_output('mkdir -p %s' % tv_dir, timeout=8)
    pushed = []
    for n in files:
        src = os.path.join(d, n)
        dst = '%s/%s' % (tv_dir, n)
        try:
            r = subprocess.run(ADB + ['push', src, dst],
                               capture_output=True, text=True, timeout=60)
            good = (r.returncode == 0)
            pushed.append({'name': n, 'bytes': os.path.getsize(src), 'ok': good,
                           'detail': (r.stdout or r.stderr or '').strip()[-200:]})
        except Exception as e:
            pushed.append({'name': n, 'ok': False, 'detail': repr(e)})
    return {'user': user, 'ok': all(x['ok'] for x in pushed),
            'pushed': pushed, 'ts': int(time.time())}


def push(user=None, all_users=False):
    """下发主题。user 指定单个，或 all_users=True 下发全部。fail-closed。"""
    if all_users:
        users = list(KNOWN_USERS)
    elif user:
        users = [user]
    else:
        return {'ok': False, 'error': 'user or all=true is required', 'results': []}
    results = [_push_one(u) for u in users]
    out = {'ok': all(r['ok'] for r in results), 'results': results}
    violations = [v for r in results for v in r.get('violations', [])]
    if violations:
        out['violations'] = violations
        out['error'] = 'manifest validation failed'
    return out


def spec():
    """返回主题标准摘要。"""
    return dict(SPEC_SUMMARY)


__all__ = ['validate', 'validate_manifest', 'list_themes', 'tv_status', 'push',
           'spec', 'SPEC_SUMMARY', 'KNOWN_USERS']
