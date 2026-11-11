"""TVPilot 界面地图学习器（UiMap）。

从 OCR 结果构建界面控件节点树，支持文本定位与 JSON 导出，供 ``tvpilot.planner``
在执行期把「文本目标」解析成可点击坐标。

设计与边界
----------
* 零新依赖：仅标准库；本模块**不做**真实 OCR / 视觉识别，``screenshot`` 只用于取分辨率与留档。
* 解析 fail-open：单条 OCR 记录解析失败只写入 ``issues``，不影响整棵树；查询未命中返回 None。
* 结构：``root(屏幕) -> 容器(按包含关系) -> 行(按纵向分带) -> 文本叶子``。
* ``id`` 为构建后先序位置（``n0, n1, ...``），同输入必得同 id，便于快照对比。
* 兼容边界：不 import / 不修改 tvpilot.tools、tvpilot.state、tvpilot.ops_log。
"""
import json
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

MAP_VERSION: int = 1
DEFAULT_SCREEN: Tuple[int, int, int, int] = (0, 0, 1280, 720)
_ROW_BAND_RATIO: float = 0.6
_MIN_ROW_BAND: int = 8

_TEXT_KEYS = ('text', 'txt', 'label', 'word', 'content', 'name', 'title',
              'transcription', 'caption', 'value')
_CONF_KEYS = ('confidence', 'conf', 'score', 'prob', 'probability')
_BOX_KEYS = ('bounds', 'box', 'rect', 'bbox', 'points', 'points2d', 'polygon', 'region')

_WS_RE = re.compile(r'\s+')
_COMPACT_RE = re.compile(r'[\W_]+', re.UNICODE)


# ========== 文本工具 ==========

def clean_text(text: Any) -> str:
    """清洗文本（保留大小写与语义字符）：去零宽/全角空格、压缩空白、去首尾空白。"""
    if text is None:
        return ''
    s = str(text).replace('\u200b', '').replace('\ufeff', '').replace('\u3000', ' ')
    return _WS_RE.sub(' ', s).strip()


def normalize_text(text: Any) -> str:
    """匹配用归一化：清洗 + 大小写折叠（小写化用于比较，不回写到节点）。"""
    return clean_text(text).casefold()


def compact_text(text: Any) -> str:
    """强模糊归一化：在 normalize 基础上再去掉所有非字母数字/汉字字符（Wi-Fi == wifi）。"""
    return _COMPACT_RE.sub('', normalize_text(text))


def _is_number(value: Any) -> bool:
    """是否为实数（排除 bool）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_point(value: Any) -> bool:
    """是否为坐标点：``[x, y]`` 或 ``{'x':.., 'y':..}``。"""
    if isinstance(value, Mapping):
        return _is_number(value.get('x')) and _is_number(value.get('y'))
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and _is_number(value[0]) and _is_number(value[1]))


def _point_xy(value: Any) -> Tuple[float, float]:
    """取坐标点的 (x, y)。"""
    if isinstance(value, Mapping):
        return float(value['x']), float(value['y'])
    return float(value[0]), float(value[1])


def _json_safe(value: Any) -> Any:
    """把任意值收敛成 JSON 可序列化形态（未知对象降级为字符串）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _brief(entry: Any) -> Dict[str, Any]:
    """给 issues 用的条目摘要（保持 JSON 可序列化且体积可控）。"""
    return {'type': type(entry).__name__, 'repr': repr(entry)[:80]}


# ========== Bounds ==========

@dataclass(frozen=True)
class Bounds:
    """矩形区域（左上/右下坐标）。用于取中心点、包含判断与 IoU。"""

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        try:
            l, t, r, b = (int(round(float(v))) for v in (self.left, self.top, self.right, self.bottom))
        except (TypeError, ValueError):
            l, t, r, b = 0, 0, 0, 0
        if r < l:
            l, r = r, l
        if b < t:
            t, b = b, t
        object.__setattr__(self, 'left', l)
        object.__setattr__(self, 'top', t)
        object.__setattr__(self, 'right', r)
        object.__setattr__(self, 'bottom', b)

    @property
    def width(self) -> int:
        """宽度（像素）。"""
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        """高度（像素）。"""
        return max(0, self.bottom - self.top)

    @property
    def area(self) -> int:
        """面积（像素）。"""
        return self.width * self.height

    @property
    def center(self) -> Tuple[int, int]:
        """中心点 (x, y)，直接对接 ``tool_tap``。"""
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    def as_list(self) -> List[int]:
        """导出为 ``[left, top, right, bottom]``（JSON 友好）。"""
        return [self.left, self.top, self.right, self.bottom]

    def to_dict(self) -> List[int]:
        """同 :meth:`as_list`，用于 dump。"""
        return self.as_list()

    def contains(self, other: 'Bounds', tol: int = 0) -> bool:
        """是否包含另一个矩形（``tol`` 为像素容差）。"""
        return (self.left - tol <= other.left and self.top - tol <= other.top
                and self.right + tol >= other.right and self.bottom + tol >= other.bottom)

    def intersects(self, other: 'Bounds') -> bool:
        """是否与另一个矩形相交（面积为 0 的退化矩形按不相交处理）。"""
        return not (other.left >= self.right or other.right <= self.left
                    or other.top >= self.bottom or other.bottom <= self.top)

    def iou(self, other: 'Bounds') -> float:
        """与另一个矩形的 IoU。"""
        if not self.intersects(other):
            return 0.0
        l = max(self.left, other.left)
        t = max(self.top, other.top)
        r = min(self.right, other.right)
        b = min(self.bottom, other.bottom)
        inter = max(0, r - l) * max(0, b - t)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def union(self, other: 'Bounds') -> 'Bounds':
        """与另一个矩形求并集外接框。"""
        return Bounds(min(self.left, other.left), min(self.top, other.top),
                      max(self.right, other.right), max(self.bottom, other.bottom))

    @classmethod
    def union_all(cls, items: Sequence['Bounds']) -> Optional['Bounds']:
        """求一组矩形的并集外接框；空集合返回 None。"""
        acc = None
        for b in items:
            acc = b if acc is None else acc.union(b)
        return acc

    @classmethod
    def from_any(cls, value: Any) -> 'Bounds':
        """从任意常见形态解析矩形；解析失败抛 ValueError。"""
        b = parse_bounds(value)
        if b is None:
            raise ValueError(f'无法解析 bounds: {value!r}')
        return b


def parse_bounds(value: Any) -> Optional[Bounds]:
    """宽松解析矩形（fail-open：失败返回 None，不抛异常）。

    支持：``Bounds``、``[l, t, r, b]``、四点/两点/八数多边形、
    ``{'left','top','right','bottom'}``、``{'x1','y1','x2','y2'}``、
    ``{'x','y','width','height'}``，以及 ``{'box'|'points'|...}`` 嵌套。
    """
    if value is None or isinstance(value, (str, bytes, bool)):
        return None
    if isinstance(value, Bounds):
        return value
    if isinstance(value, Mapping):
        m = {str(k).lower(): v for k, v in value.items()}
        for key in _BOX_KEYS:
            if key in m and not _is_number(m[key]):
                nested = parse_bounds(m[key])
                if nested is not None:
                    return nested
        if all(k in m for k in ('left', 'top', 'right', 'bottom')) \
                and all(_is_number(m[k]) for k in ('left', 'top', 'right', 'bottom')):
            return Bounds(m['left'], m['top'], m['right'], m['bottom'])
        if all(k in m for k in ('x1', 'y1', 'x2', 'y2')) \
                and all(_is_number(m[k]) for k in ('x1', 'y1', 'x2', 'y2')):
            return Bounds(m['x1'], m['y1'], m['x2'], m['y2'])
        if _is_number(m.get('x')) and _is_number(m.get('y')):
            w = m.get('width', m.get('w'))
            h = m.get('height', m.get('h'))
            if _is_number(w) and _is_number(h):
                x, y = float(m['x']), float(m['y'])
                return Bounds(x, y, x + float(w), y + float(h))
        return None
    if isinstance(value, (list, tuple)):
        seq = list(value)
        if len(seq) == 4 and all(_is_number(x) for x in seq):
            l, t, r, b = (float(x) for x in seq)
            return Bounds(l, t, r, b)
        if len(seq) in (2, 4) and all(_is_point(p) for p in seq):
            xs: List[float] = []
            ys: List[float] = []
            for p in seq:
                x, y = _point_xy(p)
                xs.append(x)
                ys.append(y)
            return Bounds(min(xs), min(ys), max(xs), max(ys))
        if len(seq) == 8 and all(_is_number(x) for x in seq):
            xs = [float(seq[i]) for i in range(0, 8, 2)]
            ys = [float(seq[i]) for i in range(1, 8, 2)]
            return Bounds(min(xs), min(ys), max(xs), max(ys))
        return None
    return None


# ========== UiNode ==========

@dataclass
class UiNode:
    """界面控件节点。

    契约字段（§0）：``id`` / ``text`` / ``bounds`` / ``children``；
    附加字段用于分类与回溯：``kind``（screen|row|group|text）、``confidence``、
    ``source``、``clickable``、``parent_id``、``attrs``。
    """

    id: str
    text: str = ''
    bounds: Bounds = field(default_factory=lambda: Bounds(0, 0, 0, 0))
    children: List['UiNode'] = field(default_factory=list)
    kind: str = 'text'
    confidence: float = 1.0
    source: str = 'ocr'
    clickable: bool = False
    parent_id: Optional[str] = None
    attrs: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator['UiNode']:
        """支持 ``for child in node``。"""
        return iter(self.children)

    @property
    def center(self) -> Tuple[int, int]:
        """中心点 (x, y)。"""
        return self.bounds.center

    def add_child(self, node: 'UiNode') -> 'UiNode':
        """挂载子节点并回写 ``parent_id``。"""
        node.parent_id = self.id
        self.children.append(node)
        return node

    def walk(self) -> Iterator['UiNode']:
        """先序遍历（含自身）。"""
        yield self
        for child in self.children:
            yield from child.walk()

    def leaves(self) -> List['UiNode']:
        """所有叶子节点（先序）。"""
        return [n for n in self.walk() if not n.children]

    def to_dict(self) -> Dict[str, Any]:
        """导出 JSON 可序列化字典（递归含 children）。"""
        return {
            'id': self.id,
            'kind': self.kind,
            'text': self.text,
            'bounds': self.bounds.as_list(),
            'confidence': round(float(self.confidence), 4),
            'source': self.source,
            'clickable': bool(self.clickable),
            'parent_id': self.parent_id,
            'attrs': _json_safe(self.attrs),
            'children': [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> 'UiNode':
        """从 :meth:`to_dict` 的产物还原节点（递归）。"""
        conf = data.get('confidence', 1.0)
        node = cls(
            id=str(data.get('id') or ''),
            text=str(data.get('text') or ''),
            bounds=parse_bounds(data.get('bounds')) or Bounds(0, 0, 0, 0),
            children=[],
            kind=str(data.get('kind') or 'text'),
            confidence=float(conf) if _is_number(conf) else 1.0,
            source=str(data.get('source') or 'ocr'),
            clickable=bool(data.get('clickable', False)),
            parent_id=(str(data['parent_id']) if data.get('parent_id') is not None else None),
            attrs=dict(data.get('attrs') or {}),
        )
        for child in data.get('children') or []:
            if isinstance(child, Mapping):
                node.add_child(cls.from_dict(child))
        return node


# ========== OCR 解析 ==========

@dataclass
class _OcrItem:
    """OCR 解析后的中间条目。"""

    text: str
    bounds: Bounds
    confidence: float
    source: str
    attrs: Dict[str, Any]
    order: int


def _first_of(mapping: Mapping, keys: Sequence[str]) -> Any:
    """大小写不敏感地取第一个存在的键值。"""
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for k in keys:
        if k in lowered and lowered[k] is not None:
            return lowered[k]
    return None


def _as_text_conf(value: Any) -> Optional[Tuple[str, float]]:
    """把 ``'text'`` 或 ``('text', 0.9)`` / ``['text', 0.9]`` 收敛成 (text, conf)。"""
    if isinstance(value, str):
        return value, 1.0
    if isinstance(value, (list, tuple)) and value:
        head = value[0]
        if isinstance(head, str):
            conf = value[1] if len(value) > 1 and _is_number(value[1]) else 1.0
            return head, float(conf)
    return None


def _parse_entry(entry: Any, index: int, issues: List[Dict[str, Any]]) -> Optional[_OcrItem]:
    """解析单条 OCR 记录（多形态宽容解析）；失败写 issues 并返回 None。"""
    text: Any = None
    conf: float = 1.0
    bounds: Optional[Bounds] = None
    attrs: Dict[str, Any] = {}
    source = 'ocr'

    if isinstance(entry, Mapping):
        text = _first_of(entry, _TEXT_KEYS)
        raw_conf = _first_of(entry, _CONF_KEYS)
        if _is_number(raw_conf):
            conf = float(raw_conf)
        bounds = parse_bounds(entry)
        attrs = {str(k): v for k, v in entry.items()
                 if str(k).lower() not in _TEXT_KEYS + _CONF_KEYS + _BOX_KEYS + ('source', 'from')
                 and (_is_number(v) or isinstance(v, (str, bool)))}
        source = str(entry.get('source') or entry.get('from') or 'ocr')
    elif isinstance(entry, (list, tuple)):
        seq = list(entry)
        if len(seq) == 2 and not _is_number(seq[0]):
            b = parse_bounds(seq[0])
            tc = _as_text_conf(seq[1])
            if b is not None and tc is not None:          # [box, (text, conf)]
                text, conf = tc[0], tc[1]
                bounds = b
            else:
                tc2 = _as_text_conf(seq[0])
                if tc2 is not None:                        # (text, box)
                    text, conf = tc2
                    bounds = parse_bounds(seq[1])
                elif isinstance(seq[1], str):              # [box, text]
                    text = seq[1]
                    bounds = b
        elif len(seq) == 3 and isinstance(seq[1], str):    # [box, text, conf]
            text = seq[1]
            bounds = parse_bounds(seq[0])
            if _is_number(seq[2]):
                conf = float(seq[2])
        elif len(seq) in (5, 6) and isinstance(seq[4], str) and all(_is_number(x) for x in seq[:4]):
            l, t, w, h = (float(x) for x in seq[:4])       # tsv: left, top, width, height, text[, conf]
            text = seq[4]
            bounds = Bounds(l, t, l + w, t + h)
            if len(seq) == 6 and _is_number(seq[5]):
                conf = float(seq[5])
        elif len(seq) == 4 and all(_is_number(x) for x in seq):
            bounds = parse_bounds(seq)                     # 裸框（无文本）
    elif isinstance(entry, str):
        text = entry                                       # 纯文本（无 bounds）
    else:
        issues.append({'index': index, 'reason': 'unrecognized_entry', 'entry': _brief(entry)})
        return None

    if text is None:
        issues.append({'index': index, 'reason': 'unrecognized_entry', 'entry': _brief(entry)})
        return None
    cleaned = clean_text(text)
    if not cleaned:
        issues.append({'index': index, 'reason': 'empty_text', 'entry': _brief(entry)})
        return None
    if bounds is None:
        issues.append({'index': index, 'reason': 'missing_bounds', 'text': cleaned})
        bounds = Bounds(0, 0, 0, 0)
    return _OcrItem(text=cleaned, bounds=bounds,
                    confidence=max(0.0, min(1.0, conf)), source=source,
                    attrs=attrs, order=index)


def _parse_ocr(ocr_result: Any, issues: List[Dict[str, Any]]) -> List[_OcrItem]:
    """把任意形态的 ``ocr_result`` 收敛成条目列表（fail-open）。"""
    raw = ocr_result
    if raw is None:
        return []
    if isinstance(raw, bytes):
        try:
            raw = raw.decode('utf-8')
        except Exception:
            issues.append({'reason': 'ocr_result_bytes_not_utf8'})
            return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception as exc:
            issues.append({'reason': f'ocr_result_json_invalid: {exc}'})
            return []
    if isinstance(raw, Mapping):
        for key in ('lines', 'items', 'results', 'data', 'blocks', 'texts', 'nodes', 'regions'):
            value = raw.get(key)
            if isinstance(value, (list, tuple)):
                raw = value
                break
        else:
            raw = [raw]
    if not isinstance(raw, (list, tuple)):
        issues.append({'reason': 'ocr_result_unsupported_type', 'type': type(raw).__name__})
        return []

    items: List[_OcrItem] = []
    for idx, entry in enumerate(raw):
        item = _parse_entry(entry, idx, issues)
        if item is not None:
            items.append(item)
    return items


# ========== 树构建 ==========

def _make_order_key(band: int) -> Any:
    """生成阅读序排序键：先按纵向分带（top//band），再按 left。"""

    def _key(node: UiNode) -> Tuple[int, int]:
        b = band if band > 0 else 1
        return (node.bounds.top // b, node.bounds.left)

    return _key


def _median(values: Sequence[int]) -> int:
    """简易中位数（避免引入 statistics）。"""
    if not values:
        return 0
    data = sorted(values)
    mid = len(data) // 2
    return int(data[mid]) if len(data) % 2 else int((data[mid - 1] + data[mid]) / 2)


def _same_band(a: Bounds, b: Bounds) -> bool:
    """两个矩形是否处于同一行（纵向分带）。"""
    band = max(_MIN_ROW_BAND, int(max(a.height, b.height) * _ROW_BAND_RATIO))
    return abs(a.center[1] - b.center[1]) <= band


def _group_rows(children: Sequence[UiNode], order_key: Any) -> List[UiNode]:
    """把同层叶子按行聚合，插入 ``kind='row'`` 的合成节点（≥2 才成行）。"""
    items = list(children)
    if len(items) < 2:
        return items
    leaves = sorted([c for c in items if not c.children], key=order_key)
    others = [c for c in items if c.children]
    if len(leaves) < 2:
        return sorted(items, key=order_key)

    rows: List[List[UiNode]] = []
    for node in leaves:
        target = None
        for row in rows:
            if _same_band(node.bounds, row[0].bounds):
                target = row
                break
        if target is None:
            rows.append([node])
        else:
            target.append(node)

    merged: List[UiNode] = []
    for members in rows:
        if len(members) == 1:
            merged.append(members[0])
            continue
        union = Bounds.union_all([m.bounds for m in members]) or Bounds(0, 0, 0, 0)
        row_node = UiNode(id='', text='', bounds=union, kind='row', source='map')
        for member in members:
            row_node.add_child(member)
        merged.append(row_node)
    merged.extend(others)
    merged.sort(key=order_key)
    return merged


def _assign_ids(root: UiNode) -> None:
    """按先序重排 id（``n0, n1, ...``）并回写 ``parent_id``。"""
    for index, node in enumerate(root.walk()):
        node.id = f'n{index}'
        for child in node.children:
            child.parent_id = node.id


def _is_clickable(item: _OcrItem) -> bool:
    """可点击性启发：显式属性优先，否则短文本叶子视作可点击（点击目标候选）。"""
    flag = item.attrs.get('clickable')
    if isinstance(flag, bool):
        return flag
    return 0 < len(item.text) <= 12


def _build_tree(items: Sequence[_OcrItem], size: Optional[Tuple[int, int]],
                layout: str, order_key: Any) -> UiNode:
    """构建 root -> 容器 -> 行 -> 叶子 的节点树。"""
    if size and size[0] and size[1]:
        root_bounds = Bounds(0, 0, int(size[0]), int(size[1]))
    else:
        root_bounds = (Bounds.union_all([i.bounds for i in items if i.bounds.area > 0])
                       or Bounds(*DEFAULT_SCREEN))
    root = UiNode(id='root', text='', bounds=root_bounds, kind='screen', source='map')

    nodes = [UiNode(id='', text=i.text, bounds=i.bounds, kind='text',
                    confidence=i.confidence, source=i.source,
                    clickable=_is_clickable(i),
                    attrs=dict(i.attrs, _order=i.order))
             for i in items]
    nodes.sort(key=lambda n: (-n.bounds.area, n.attrs.get('_order', 0)))

    for node in nodes:                       # 包含关系定父子（面积最小的容器胜出）
        parent: UiNode = root
        for cand in nodes:
            if cand is node:
                continue
            if cand.bounds.area > node.bounds.area and cand.bounds.contains(node.bounds, tol=2):
                if parent is root or cand.bounds.area < parent.bounds.area:
                    parent = cand
        parent.add_child(node)

    if layout in ('auto', 'rows'):
        for node in list(root.walk()):
            node.children = _group_rows(node.children, order_key)

    for node in root.walk():                 # 去掉内部排序键
        node.attrs.pop('_order', None)
    return root


# ========== 截图元信息 ==========

def _image_size(data: bytes) -> Optional[Tuple[int, int]]:
    """从图片文件头解析 (width, height)；只支持 PNG / JPEG / BMP，其他返回 None。"""
    if not data or len(data) < 24:
        return None
    if data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR':
        width, height = struct.unpack('>II', data[16:24])
        return int(width), int(height)
    if data[:2] == b'\xff\xd8':
        pos = 2
        sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while pos + 9 < len(data):
            if data[pos] != 0xFF:
                pos += 1
                continue
            marker = data[pos + 1]
            if marker in sof:
                height, width = struct.unpack('>HH', data[pos + 5:pos + 9])
                return int(width), int(height)
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                pos += 2
                continue
            seg_len = struct.unpack('>H', data[pos + 2:pos + 4])[0]
            pos += 2 + seg_len
        return None
    if data[:2] == b'BM':
        width, height = struct.unpack('<ii', data[18:26])
        return int(width), abs(int(height))
    return None


def _screenshot_meta(screenshot: Any) -> Tuple[Optional[int], Optional[int], Optional[str]]:
    """从 ``screenshot`` 取 (width, height, 引用描述)；任何异常都 fail-open。"""
    if screenshot is None:
        return None, None, None
    if isinstance(screenshot, Mapping):
        w = screenshot.get('width')
        h = screenshot.get('height')
        ref = screenshot.get('path') or screenshot.get('screenshot') or '<mapping>'
        if _is_number(w) and _is_number(h):
            return int(w), int(h), str(ref)
        path = screenshot.get('path') or screenshot.get('screenshot')
        if isinstance(path, (str, os.PathLike)):
            w2, h2, ref2 = _screenshot_meta(path)
            return w2, h2, ref2
        return None, None, str(ref)
    if isinstance(screenshot, (bytes, bytearray)):
        size = _image_size(bytes(screenshot))
        ref = f'<bytes:{len(screenshot)}>'
        return (size if size else (None, None)) + (ref,) if size else (None, None, ref)
    if isinstance(screenshot, (str, os.PathLike)):
        path = os.fspath(screenshot)
        try:
            if os.path.isfile(path):
                with open(path, 'rb') as fh:
                    head = fh.read(65536)
                size = _image_size(head)
                return (size[0], size[1], path) if size else (None, None, path)
        except Exception:
            pass
        return None, None, path
    return None, None, type(screenshot).__name__


# ========== UiMap ==========

class UiMap:
    """界面地图：OCR 条目 -> 节点树，支持文本查询与 JSON 导出。

    查询语义（分级匹配，同级按「置信度↓、阅读序↑」取最优）：
    tier0 精确 → tier1 前缀 → tier2 包含 → tier3 紧凑包含。
    ``find`` / ``pick`` / ``resolve`` 同时支持 ``A|B|C`` 别名选择器（左优先）。
    """

    VERSION: int = MAP_VERSION

    def __init__(self, root: Optional[UiNode] = None,
                 meta: Optional[Dict[str, Any]] = None,
                 issues: Optional[List[Dict[str, Any]]] = None) -> None:
        self.root: UiNode = root if root is not None else UiNode(
            id='n0', text='', bounds=Bounds(*DEFAULT_SCREEN), kind='screen', source='map')
        self.meta: Dict[str, Any] = dict(meta or {})
        self.issues: List[Dict[str, Any]] = list(issues or [])
        self._nodes: List[UiNode] = []
        self._order: Dict[int, int] = {}
        self._index: Dict[str, List[UiNode]] = {}
        self._reindex()

    # ---------- 构建 ----------

    @classmethod
    def build(cls, screenshot: Any = None, ocr_result: Any = None,
              size: Optional[Tuple[int, int]] = None, layout: str = 'auto') -> 'UiMap':
        """从截图与 OCR 结果构建界面地图（契约入口）。

        :param screenshot: 截图路径 / bytes / ``{'width','height'}`` / None（只取尺寸与留档）
        :param ocr_result: 多形态 OCR 结果（见模块 docstring）
        :param size: 显式覆盖 (width, height)
        :param layout: ``'auto'``/``'rows'`` 成行分带；``'flat'`` 不插行节点
        """
        issues: List[Dict[str, Any]] = []
        width, height, ref = _screenshot_meta(screenshot)
        if size and _is_number(size[0]) and _is_number(size[1]):
            width, height = int(size[0]), int(size[1])
        items = _parse_ocr(ocr_result, issues)
        band = max(_MIN_ROW_BAND, int(_median([i.bounds.height for i in items]) * 0.7) or 24)
        order_key = _make_order_key(band)
        root = _build_tree(items, (width, height), layout, order_key)
        if layout in ('auto', 'rows'):
            root.children = _group_rows(root.children, order_key)
        _assign_ids(root)
        meta = {
            'source': 'ocr',
            'screenshot': ref,
            'width': width,
            'height': height,
            'node_count': sum(1 for _ in root.walk()),
            'text_count': sum(1 for n in root.walk() if n.text),
            'ocr_items': len(items),
            'skipped': sum(1 for i in issues if i.get('reason') != 'missing_bounds'),
        }
        return cls(root=root, meta=meta, issues=issues)

    @classmethod
    def from_texts(cls, texts: Sequence[str], size: Optional[Tuple[int, int]] = None) -> 'UiMap':
        """从纯文本列表构建地图（**无几何信息**，bounds 为退化矩形，不能用于取坐标）。"""
        return cls.build(None, [str(t) for t in texts], size=size)

    @classmethod
    def from_dump(cls, data: Union[str, bytes, Mapping]) -> 'UiMap':
        """从 :meth:`dump` / :meth:`to_json` 的产物还原地图。"""
        if isinstance(data, (str, bytes)):
            data = json.loads(data if isinstance(data, str) else data.decode('utf-8'))
        tree = data.get('tree') or data.get('root') or {}
        root = UiNode.from_dict(tree) if isinstance(tree, Mapping) else None
        return cls(root=root, meta=dict(data.get('meta') or {}),
                   issues=list(data.get('issues') or []))

    def _reindex(self) -> None:
        """重建节点列表、阅读序与精确索引。"""
        self._nodes = list(self.root.walk())
        for index, node in enumerate(self._nodes):
            if not node.id:
                node.id = f'n{index}'
        for node in self._nodes:
            for child in node.children:
                child.parent_id = node.id
        self._order = {id(node): i for i, node in enumerate(self._nodes)}
        self._index: Dict[str, List[UiNode]] = {}
        for node in self._nodes:
            key = normalize_text(node.text)
            if key:
                self._index.setdefault(key, []).append(node)

    # ---------- 查询 ----------

    def _candidates(self, query: str, fuzzy: bool = True) -> List[Tuple[UiNode, int]]:
        """按匹配分级返回候选 (node, tier)。"""
        key = normalize_text(query)
        if not key:
            return []
        ckey = compact_text(query)
        buckets: Dict[int, List[UiNode]] = {}
        for node in self._nodes:
            if not node.text:
                continue
            n_text = normalize_text(node.text)
            n_compact = compact_text(node.text)
            tier: Optional[int] = None
            if n_text == key or (ckey and n_compact == ckey):
                tier = 0
            elif fuzzy and n_text.startswith(key):
                tier = 1
            elif fuzzy and key in n_text:
                tier = 2
            elif fuzzy and ckey and ckey in n_compact:
                tier = 3
            if tier is not None:
                buckets.setdefault(tier, []).append(node)
        out: List[Tuple[UiNode, int]] = []
        for tier in sorted(buckets):
            nodes = sorted(buckets[tier], key=lambda n: (-n.confidence, self._order.get(id(n), 1 << 30)))
            out.extend((n, tier) for n in nodes)
        return out

    def resolve(self, selector: Any, fuzzy: bool = True) -> Tuple[Optional[UiNode], Optional[str]]:
        """解析 ``A|B|C`` 选择器，返回 ``(节点, 命中的别名)``；未命中 ``(None, None)``。"""
        if selector is None:
            return None, None
        for alias in (a.strip() for a in str(selector).split('|')):
            if not alias:
                continue
            cands = self._candidates(alias, fuzzy=fuzzy)
            if cands:
                return cands[0][0], alias
        return None, None

    def find(self, text: Any, fuzzy: bool = True) -> Optional[UiNode]:
        """按文本找最优节点（契约入口）；未命中返回 None。"""
        return self.resolve(text, fuzzy=fuzzy)[0]

    def find_all(self, text: Any, fuzzy: bool = True) -> List[UiNode]:
        """返回所有匹配候选（跨别名去重，按「匹配等级、置信度、阅读序」排序）。"""
        aliases = [a.strip() for a in str(text or '').split('|') if a.strip()]
        seen: Dict[int, Tuple[UiNode, int]] = {}
        for alias in aliases:
            for node, tier in self._candidates(alias, fuzzy=fuzzy):
                key = (tier, -node.confidence, self._order.get(id(node), 1 << 30))
                if id(node) not in seen or key < seen[id(node)][1]:
                    seen[id(node)] = (node, key)
        ordered = sorted(seen.values(), key=lambda pair: pair[1])
        return [pair[0] for pair in ordered]

    def pick(self, selector: Any, fuzzy: bool = True) -> Optional[UiNode]:
        """选择器入口（同 :meth:`find`，语义化别名）。"""
        return self.find(selector, fuzzy=fuzzy)

    def texts(self) -> List[str]:
        """所有非空文本（先序 ≈ 阅读序）。"""
        return [n.text for n in self._nodes if n.text]

    def nodes(self) -> List[UiNode]:
        """所有节点快照（先序）。"""
        return list(self._nodes)

    def dump(self) -> Dict[str, Any]:
        """导出 JSON 可序列化的地图结构（契约入口）。"""
        return {
            'version': self.VERSION,
            'kind': 'uimap',
            'meta': _json_safe(self.meta),
            'issues': _json_safe(self.issues),
            'tree': self.root.to_dict(),
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        """导出 JSON 字符串。"""
        return json.dumps(self.dump(), ensure_ascii=False, indent=indent)

    def __len__(self) -> int:
        """节点总数。"""
        return len(self._nodes)

    def __contains__(self, text: object) -> bool:
        """``'设置' in ui_map`` 语义。"""
        return self.find(text) is not None

    def __repr__(self) -> str:
        """调试用简述。"""
        return f'<UiMap nodes={len(self._nodes)} texts={len(self.texts())}>'


__all__ = [
    'MAP_VERSION', 'DEFAULT_SCREEN',
    'clean_text', 'normalize_text', 'compact_text', 'parse_bounds',
    'Bounds', 'UiNode', 'UiMap',
]
