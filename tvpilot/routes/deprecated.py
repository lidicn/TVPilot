"""已停用链路的旧调用端点：路径保留，统一返 410 Gone。

AF-PM 2026-11-12 裁定：旧版兼容 GET 不能从路由表直接消失变 404
（等于对外假装「路径不存在」），必须与 POST 兜底一致，明确返 410 + deprecated 包络。
这些路径的实现已随旧版链路整体隔离归档（见 archive/README.md），本模块只是显式路标，
不 import 归档目录、不复活旧行为。
"""
DEPRECATED_BODY = {'ok': False, 'error': 'deprecated', 'see': 'archive/README.md'}

DEPRECATED_GET_PATHS = frozenset({
    '/health', '/channels', '/active', '/zap', '/screenshot',
    '/fongmi/search', '/keyevent', '/tap',
})


def deprecated_body():
    """返回 410 包络的副本（调用方可安全改写）。"""
    return dict(DEPRECATED_BODY)


def handle_deprecated_get(handler, path, q):
    """统一分发旧版兼容 GET；返回 True 表示已处理（410）。"""
    if path in DEPRECATED_GET_PATHS:
        handler._json(deprecated_body(), 410)
        return True
    return False


__all__ = [
    'DEPRECATED_BODY', 'DEPRECATED_GET_PATHS',
    'deprecated_body', 'handle_deprecated_get',
]
