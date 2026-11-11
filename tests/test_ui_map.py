"""tests/test_ui_map.py —— tvpilot.ui_map 单测（纯离线、零依赖、零设备）。

覆盖：节点树构建、find/find_all/pick、dump 可序列化、异常与边界、roundtrip。
无 pytest 也能跑：``python tests/test_ui_map.py`` 会自检并打印 total/collectable/ran。
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tvpilot.ui_map import (  # noqa: E402
    Bounds, UiMap, UiNode, compact_text, normalize_text, parse_bounds,
)

OCR_SAMPLE = [
    [[[100, 40], [300, 40], [300, 90], [100, 90]], ('设置', 0.98)],
    [[[320, 40], [520, 40], [520, 90], [320, 90]], ('通用设置', 0.95)],
    [[[100, 300], [300, 300], [300, 350], [100, 350]], ('确定', 0.90)],
]


def _depth(node):
    return 1 + max((_depth(c) for c in node.children), default=0)


def test_build_from_ocr_yields_three_level_tree():
    """验收2前半：OCR -> 节点树，且契约字段齐备、层级 >= 3。"""
    m = UiMap.build(None, OCR_SAMPLE)
    assert isinstance(m.root, UiNode)
    assert m.root.kind == 'screen'
    leaf = m.find('设置')
    assert isinstance(leaf, UiNode)
    for attr in ('id', 'text', 'bounds', 'children'):
        assert hasattr(leaf, attr)
    assert leaf.text == '设置'
    assert leaf.bounds.as_list() == [100, 40, 300, 90]
    assert leaf.bounds.center == (200, 65)
    assert _depth(m.root) >= 3, '应为 root -> row -> leaf 的树'
    kinds = {n.kind for n in m.root.walk()}
    assert 'row' in kinds
    assert m.meta['text_count'] == 3


def test_find_exact_hit_prefers_exact_node():
    """find('设置') 在「设置/通用设置」并存时必须命中精确项。"""
    m = UiMap.build(None, OCR_SAMPLE)
    hit = m.find('设置')
    assert hit is not None and hit.text == '设置'
    assert m.find('通用设置').text == '通用设置'


def test_find_fuzzy_prefix_and_contains_tiers():
    """模糊分级：前缀命中 tier1、包含命中 tier2，都能返回节点。"""
    m = UiMap.build(None, [[[[0, 0], [200, 0], [200, 40], [0, 40]], ('通用设置', 0.9)]])
    assert m.find('通用').text == '通用设置'          # 前缀
    assert m.find('设置').text == '通用设置'          # 包含
    assert m.find('通用设置', fuzzy=False) is not None  # 精确
    assert m.find('设置', fuzzy=False) is None         # 关闭模糊即未命中


def test_find_miss_and_empty_return_none():
    """未命中 / 空查询 / None 都返回 None，不抛异常。"""
    m = UiMap.build(None, OCR_SAMPLE)
    assert m.find('不存在的控件') is None
    assert m.find('') is None
    assert m.find(None) is None
    assert UiMap.build().find('设置') is None


def test_find_all_returns_every_match_in_reading_order():
    """find_all 返回全部候选（同文本多处），顺序为阅读序。"""
    entries = [
        {'text': '确定', 'bounds': [400, 300, 500, 340]},
        {'text': '确定', 'bounds': [100, 300, 200, 340]},
    ]
    m = UiMap.build(None, entries)
    hits = m.find_all('确定')
    assert len(hits) == 2
    assert hits[0].bounds.left == 100 and hits[1].bounds.left == 400


def test_dump_is_json_serializable_and_structured():
    """验收3：dump() 是 JSON 可序列化结构。"""
    m = UiMap.build(None, OCR_SAMPLE)
    payload = m.dump()
    text = json.dumps(payload, ensure_ascii=False)
    assert isinstance(text, str) and text
    assert set(payload) == {'version', 'kind', 'meta', 'issues', 'tree'}
    tree = payload['tree']
    assert isinstance(tree['bounds'], list) and len(tree['bounds']) == 4
    assert all(isinstance(v, int) for v in tree['bounds'])
    assert isinstance(tree['children'], list) and tree['children']
    assert isinstance(m.to_json(), str)


def test_build_tolerates_malformed_entries_and_records_issues():
    """解析 fail-open：坏条目跳过并记 issues，好条目照常进树。"""
    bad = [None, 123, [], '纯文本', [[ [0, 0], [50, 0], [50, 30], [0, 30] ], ('好', 0.8)]]
    m = UiMap.build(None, bad)
    assert sorted(m.texts()) == sorted(['纯文本', '好'])
    reasons = {i['reason'] for i in m.issues}
    assert 'unrecognized_entry' in reasons
    assert 'missing_bounds' in reasons
    assert m.find('纯文本') is not None


def test_build_with_none_inputs_yields_empty_map():
    """空输入：只有根节点、可 dump、查询未命中。"""
    m = UiMap.build()
    assert len(m) == 1
    assert m.texts() == []
    assert m.find('任意') is None
    json.dumps(m.dump())
    assert m.meta['ocr_items'] == 0


def test_from_dump_roundtrip_preserves_query_and_ids():
    """dump -> from_dump 往返后文本、id、查询能力保持一致。"""
    m = UiMap.build(None, OCR_SAMPLE)
    clone = UiMap.from_dump(m.dump())
    assert clone.texts() == m.texts()
    assert [n.id for n in clone.nodes()] == [n.id for n in m.nodes()]
    assert clone.find('设置').bounds.as_list() == m.find('设置').bounds.as_list()
    assert json.dumps(clone.dump()) is not None


def test_parse_bounds_variants_and_fail_open():
    """bounds 多形态解析 + 非法输入返回 None。"""
    assert parse_bounds([1, 2, 3, 4]).as_list() == [1, 2, 3, 4]
    assert parse_bounds([[0, 0], [10, 0], [10, 20], [0, 20]]).as_list() == [0, 0, 10, 20]
    assert parse_bounds({'x': 5, 'y': 6, 'width': 2, 'height': 3}).as_list() == [5, 6, 7, 9]
    assert parse_bounds({'left': 0, 'top': 0, 'right': 4, 'bottom': 4}).width == 4
    assert parse_bounds({'box': [0, 0, 9, 9]}).area == 81
    assert parse_bounds('abc') is None
    assert parse_bounds([1, 2, 3]) is None
    assert parse_bounds(None) is None
    try:
        Bounds.from_any('nope')
    except ValueError:
        pass
    else:
        raise AssertionError('非法 bounds 应抛 ValueError')


def test_bounds_geometry_helpers():
    """Bounds 几何方法：包含/相交/IoU/并集。"""
    a = Bounds(0, 0, 10, 10)
    b = Bounds(5, 5, 15, 15)
    assert a.contains(Bounds(1, 1, 9, 9)) is True
    assert a.contains(b) is False
    assert a.intersects(b) is True
    assert 0.14 < a.iou(b) < 0.15
    assert a.union(b).as_list() == [0, 0, 15, 15]
    assert Bounds.union_all([a, b]).as_list() == [0, 0, 15, 15]
    assert Bounds.union_all([]) is None


def test_pick_supports_alternative_selector():
    """pick/resolve 支持 A|B|C 别名选择器（左优先）。"""
    m = UiMap.build(None, [[[[300, 200], [400, 200], [400, 240], [300, 240]], ('WLAN', 0.9)]])
    node = m.pick('WiFi|WLAN|无线网络')
    assert node is not None and node.text == 'WLAN'
    hit, alias = m.resolve('WiFi|WLAN|无线网络')
    assert hit is node and alias == 'WLAN'
    assert m.pick('蓝牙|HDMI') is None


def test_reading_order_texts_top_down_left_right():
    """texts()/nodes() 按阅读序输出（与输入顺序无关）。"""
    entries = [
        {'text': '底部', 'bounds': [100, 400, 200, 440]},
        {'text': '右上', 'bounds': [400, 40, 500, 80]},
        {'text': '左上', 'bounds': [100, 40, 200, 80]},
    ]
    m = UiMap.build(None, entries)
    assert m.texts() == ['左上', '右上', '底部']


def test_container_nesting_from_bounding_box():
    """外框条目成为内含条目的容器节点（结构可表达分组）。"""
    entries = [
        {'text': '分组框', 'bounds': [0, 0, 400, 300]},
        {'text': '条目A', 'bounds': [20, 20, 120, 60]},
        {'text': '条目B', 'bounds': [20, 80, 120, 120]},
    ]
    m = UiMap.build(None, entries)
    group = m.find('分组框')
    inner = {n.text for n in group.walk() if n.text}
    assert {'分组框', '条目A', '条目B'} <= inner
    assert m.find('条目A').parent_id is not None


def test_ocr_result_json_string_and_wrapped_dict():
    """ocr_result 允许 JSON 字符串与 {'lines': [...]} 包裹形态。"""
    m1 = UiMap.build(None, json.dumps(OCR_SAMPLE))
    m2 = UiMap.build(None, {'lines': OCR_SAMPLE})
    assert m1.texts() == m2.texts() == ['设置', '通用设置', '确定']
    assert UiMap.build(None, '{not json').issues


def test_dict_entries_with_score_and_text_keys():
    """dict 形态条目（text/score/points 别名）同样可解析。"""
    entries = [{'transcription': '首页', 'points': [[0, 0], [80, 0], [80, 30], [0, 30]], 'score': 0.7}]
    m = UiMap.build(None, entries)
    node = m.find('首页')
    assert node is not None and node.confidence == 0.7
    assert node.bounds.width == 80


def test_screenshot_png_header_sets_root_bounds():
    """PNG 头解析出分辨率 -> root bounds 取屏幕尺寸。"""
    png = (b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\x0d' + b'IHDR' + struct.pack('>II', 1280, 720)
           + b'\x00' * 8)
    m = UiMap.build(png, OCR_SAMPLE)
    assert (m.meta['width'], m.meta['height']) == (1280, 720)
    assert m.root.bounds.as_list() == [0, 0, 1280, 720]


def test_text_only_entries_stay_queryable_but_not_clickable():
    """无 bounds 的纯文本条目：可查到，但 area==0 不可用于取坐标。"""
    m = UiMap.build(None, [{'text': '仅文字'}])
    node = m.find('仅文字')
    assert node is not None and node.bounds.area == 0
    assert node.bounds.center == (0, 0)


def test_contains_and_normalization_helpers():
    """文本归一化与 ``in`` 语义。"""
    assert normalize_text('  Wi-Fi  ') == 'wi-fi'
    assert compact_text('Wi-Fi') == 'wifi'
    m = UiMap.build(None, [[[[0, 0], [60, 0], [60, 30], [0, 30]], ('WiFi', 0.9)]])
    assert 'wifi' in m and 'WiFi' in m and '蓝牙' not in m


def _selftest() -> int:
    """无 pytest 环境的自检入口：收集并执行本模块全部 test_*。"""
    import traceback
    names = sorted(n for n in list(globals()) if n.startswith('test_') and callable(globals()[n]))
    total = len(names)
    ran = 0
    failed = []
    for name in names:
        ran += 1
        try:
            globals()[name]()
            print(f'  PASS {name}')
        except Exception:
            failed.append(name)
            print(f'  FAIL {name}')
            traceback.print_exc()
    print(f'total={total} collectable={total} ran={ran} '
          f'passed={ran - len(failed)} failed={len(failed)}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(_selftest())
