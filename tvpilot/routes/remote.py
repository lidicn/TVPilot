"""TVPilot PWA 远程控制路由。

绞杀者模式阶段3-8：/remote, /remote-static/*。
"""


def handle_remote(handler, path, q):
    """统一分发：PWA 远程控制 GET 路由。返回 True 表示已处理。"""
    # /remote 或 /remote/ → PWA 主页面
    if path == '/remote' or path == '/remote/':
        handler._serve_static('remote.html')
        return True

    # /remote-static/* → 静态资源（JS/CSS/图标）
    if path.startswith('/remote-static/'):
        rel = path[len('/remote-static/'):]
        if '..' in rel:
            handler._json({'ok': False, 'error': 'invalid path'}, 400)
            return True
        handler._serve_static(rel, cache=True)
        return True

    return False


__all__ = ['handle_remote']
