"""go2rtc 归档防回流闸（离线，不碰真机 / 容器）。

口径（AF-PM 2026-11-12 归档卡）：
1. `tvpilot/**` 与 `tvpilot_server.py` 里 `go2rtc`（大小写不敏感）命中 **0 行**；
2. `/api/health` 结果不含 `go2rtc_creds_configured`；
3. `/api/channels` 的 `count == 80`；
4. `archive/**/*.py` 全部可 `py_compile`；
5. 旧链路实现与旧活符号不得回流到主服务（无 shim）。

隔离纪律（AF-PM 11-12）：tvpilot 子模块一律函数内 importlib 导入，
不在模块级 `from tvpilot import ...`，避免被 test_mcp 的假模块注入污染。

用法：pytest tests/test_deprecated_surface.py -q
"""
import importlib
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _live_py_files():
    yield ROOT / 'tvpilot_server.py'
    yield from sorted((ROOT / 'tvpilot').rglob('*.py'))


def _live_text(p):
    return p.read_text(encoding='utf-8-sig')


def test_no_go2rtc_in_live_surface():
    """活面里一个 go2rtc 字样都不许有（archive/、doc/ 不在扫描范围）。"""
    hits = []
    for p in _live_py_files():
        for i, line in enumerate(_live_text(p).splitlines(), 1):
            if 'go2rtc' in line.lower():
                hits.append('%s:%d: %s' % (p.relative_to(ROOT), i, line.strip()))
    assert not hits, 'go2rtc 回流到活面：\n' + '\n'.join(hits)


def test_no_archived_symbols_in_live_surface():
    """旧实现 / 旧活符号不许残留在主服务（不留 shim）。"""
    banned = ('_go2rtc_opener', '_do_refresh_rtsp', 'proxy_mytv',
              'handle_legacy_get', 'go2rtc_basic_auth_header', 'warmup')
    hits = []
    for p in _live_py_files():
        for i, line in enumerate(_live_text(p).splitlines(), 1):
            for name in banned:
                if name in line:
                    hits.append('%s:%d: %s' % (p.relative_to(ROOT), i, name))
    assert not hits, '归档符号残留：\n' + '\n'.join(hits)


def test_deprecated_endpoints_return_410():
    """旧调用端点保留但明确返 410，不静默 500 / 不假 404。"""
    srv = _live_text(ROOT / 'tvpilot_server.py')
    routes = _live_text(ROOT / 'tvpilot' / 'routes' / 'iptv.py')
    for text, label in ((srv, 'tvpilot_server.py'), (routes, 'routes/iptv.py')):
        assert "'error': 'deprecated'" in text, '%s 缺 deprecated 包络' % label
        assert "'see': 'archive/README.md'" in text, '%s 缺 see 指针' % label
        assert '410' in text, '%s 缺 410 状态码' % label
    assert "/api/iptv/refresh_rtsp" in srv
    assert "/api/iptv/refresh_status" in routes

    # 运行期核一次状态查询端点（GET 侧可离线直接调 handler）
    routes_iptv = importlib.import_module('tvpilot.routes.iptv')
    captured = {}

    class FakeHandler:
        def _json(self, obj, code=200):
            captured['obj'] = obj
            captured['code'] = code

    assert routes_iptv.handle_iptv_routes(FakeHandler(), '/api/iptv/refresh_status', {}) is True
    assert captured['code'] == 410
    assert captured['obj'] == {'ok': False, 'error': 'deprecated', 'see': 'archive/README.md'}


def test_legacy_get_paths_return_410():
    """旧版 8 条 GET 统一 410（与 POST 同包络），不许从路由表消失变 404。"""
    dep = importlib.import_module('tvpilot.routes.deprecated')

    expected = {'ok': False, 'error': 'deprecated', 'see': 'archive/README.md'}
    paths = {'/health', '/channels', '/active', '/zap', '/screenshot',
             '/fongmi/search', '/keyevent', '/tap'}
    assert set(dep.DEPRECATED_GET_PATHS) == paths
    srv = _live_text(ROOT / 'tvpilot_server.py')
    assert 'handle_deprecated_get' in srv, '主服务未注册旧版 GET 的 410 handler'
    assert 'GET_HANDLERS' in srv

    class FakeHandler:
        def __init__(self):
            self.calls = []

        def _json(self, obj, code=200):
            self.calls.append((obj, code))

    for path in sorted(paths):
        f = FakeHandler()
        assert dep.handle_deprecated_get(f, path, {}) is True, '%s 未被 410 接管' % path
        assert f.calls == [(expected, 410)], '%s -> %r' % (path, f.calls)
    assert dep.handle_deprecated_get(FakeHandler(), '/api/health', {}) is False


def test_health_has_no_go2rtc_field(monkeypatch):
    """health 面板不再并列两代链路字段；运行期也核一次（全离线）。"""
    src = _live_text(ROOT / 'tvpilot' / 'health.py')
    assert 'go2rtc_creds_configured' not in src

    h = importlib.import_module('tvpilot.health')

    def _offline(*a, **k):
        raise OSError('offline: no device / no network in unit test')

    monkeypatch.setattr(h, 'adb_ready', _offline)
    monkeypatch.setattr(h.urllib.request, 'urlopen', _offline)
    result = h.health_check()
    assert 'go2rtc_creds_configured' not in result
    assert result['channels_count'] == 80


def test_channels_count_is_80(tmp_path, monkeypatch):
    """/api/channels 的 count 统一到 80（channels.json 权威口径），旧字段名不再出现。"""
    iptv = importlib.import_module('tvpilot.iptv')
    routes_iptv = importlib.import_module('tvpilot.routes.iptv')

    fake_m3u = tmp_path / 'gd_iptv_source.m3u'
    fake_m3u.write_text('#EXTM3U\n#EXTINF:-1 tvg-name="中央一台",中央一台\n', encoding='utf-8')
    monkeypatch.setattr(iptv, 'IPTV_LOCAL_FILE', str(fake_m3u))

    captured = {}

    class FakeHandler:
        def _json(self, obj, code=200):
            captured['obj'] = obj
            captured['code'] = code

    assert routes_iptv.handle_channels(FakeHandler(), '/api/channels', {}) is True
    assert captured.get('code') == 200, 'handle_channels 抛异常: %r' % captured.get('obj')
    assert captured['obj']['count'] == 80
    assert captured['obj']['zappable_count'] == 60
    assert 'go2rtc_count' not in captured['obj']


def test_archive_py_compile(tmp_path):
    """归档代码原样保留且语法完整（编译到临时目录，不污染工作树）。"""
    files = sorted((ROOT / 'archive').rglob('*.py'))
    assert files, 'archive/ 下没有 .py'
    for p in files:
        py_compile.compile(str(p), cfile=str(tmp_path / (p.name + '.pyc')), doraise=True)
