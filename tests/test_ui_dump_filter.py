"""P0 ui_dump 过滤谓词窄面单测（纯离线，不碰真机 / 容器）。

喂一份「全空 text + 只有容器 bounds」的假 uiautomator XML，并混入一个
visible-to-user="false" 的隐形节点，断言：
1. count > 0（IPTV WebView 播放页的容器节点被保留，不再 count=0）；
2. 不含隐形节点（visible-to-user="false" 必须被排除）；
3. el_type 的 region 兜底保留；有 text 的节点仍按 text 归类；
4. 活代码里不再残留把过滤关掉的 `if True:`。

隔离纪律（AF-PM 11-12）：tvpilot 子模块一律函数内 importlib 导入，
不在模块级 `from tvpilot import tools`，避免被 test_mcp 的假模块注入污染。

用法：pytest tests/test_ui_dump_filter.py -q
"""
import importlib
import subprocess
from pathlib import Path

_FAKE_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" class="android.widget.FrameLayout" bounds="[0,0][1920,1080]"
        text="" content-desc="" clickable="false" editable="false"
        visible-to-user="true" resource-id="android:id/content"/>
  <node index="1" class="android.webkit.WebView" bounds="[10,20][1900,1000]"
        text="" content-desc="" clickable="false" editable="false"
        visible-to-user="true" resource-id="com.example/webview"/>
  <node index="2" class="android.view.View" bounds="[100,100][600,300]"
        text="" content-desc="" clickable="false" editable="false"
        visible-to-user="true" resource-id="com.example/container"/>
  <node index="3" class="android.view.View" bounds="[0,0][1920,80]"
        text="" content-desc="" clickable="false" editable="false"
        visible-to-user="false" resource-id="com.example/INVISIBLE_OVERLAY"/>
  <node index="4" class="android.widget.TextView" bounds="[100,900][500,960]"
        text="中央一台" content-desc="" clickable="false" editable="false"
        visible-to-user="true" resource-id="com.example/channel_banner"/>
  <node index="5" class="android.view.View" bounds="[0,0][2,2]"
        text="" content-desc="" clickable="false" editable="false"
        visible-to-user="true" resource-id="com.example/tiny"/>
</hierarchy>
'''


def test_container_nodes_kept_and_invisible_nodes_dropped(monkeypatch):
    """假 XML 全空 text + 容器 bounds：count>0、无隐形节点、region 兜底在。"""
    t = importlib.import_module('tvpilot.tools')
    monkeypatch.setattr(
        t, 'adb_shell_output',
        lambda *a, **k: 'UI hierchary dumped to: /sdcard/ui_dump.xml')
    monkeypatch.setattr(t, 'adb_ready', lambda *a, **k: None)
    monkeypatch.setattr(
        t, 'get_foreground_pkg',
        lambda *a, **k: 'com.android.smart.terminal.iptv')

    def fake_run(argv, **kw):
        Path(argv[-1]).write_text(_FAKE_XML, encoding='utf-8')
        return subprocess.CompletedProcess(argv, 0, b'', b'')

    monkeypatch.setattr(t.subprocess, 'run', fake_run)
    t._UI_INDEX_CACHE.update({'elements': [], 'fg_pkg': None, 'ts': 0})

    data, err, msg = t.tool_ui_dump()
    assert err is None, 'tool_ui_dump 报错: %s' % msg
    elements = data['elements']
    assert data['count'] > 0
    assert len(elements) == 4, '预期 3 个容器 + 1 个有 text，实得 %d' % len(elements)
    assert all('INVISIBLE_OVERLAY' not in (e.get('resource_id') or '')
               for e in elements), '隐形节点被收进来了'
    assert all('tiny' not in (e.get('resource_id') or '')
               for e in elements), '零尺寸节点被收进来了'
    assert any(e['type'] == 'region' for e in elements), 'region 兜底丢了'
    assert any(e['text'] == '中央一台' for e in elements), '有 text 的节点丢了'


def test_no_if_true_in_ui_dump_source():
    """`if True:` 必须换成显式谓词，不许把过滤关掉蒙混过闸。"""
    src = (Path(__file__).resolve().parents[1] / 'tvpilot' / 'tools.py').read_text(
        encoding='utf-8')
    assert 'if True:' not in src
    assert 'keep_node' in src
    assert 'visible_to_user' in src
