"""TVPilot 任务规划器（规则 + 模板，预留 LLM 接口）。

把自然语言任务描述拆成多步 :class:`PlanStep`，执行时逐步产生 :class:`StepResult`，
失败即停并把未执行步骤标记 ``not_run`` 一并报告。

设计与边界
----------
* 零新依赖：仅标准库；**不实现真实 LLM 调用**（§2.2），但保留 ``llm=`` 接口，
  其输出经校验后可用，非法/异常一律降级回规则模板。
* 执行器可注入（``runner=``），默认实现才惰性 import ``tvpilot.tools`` 并保持
  ``(data, err_code, err_msg)`` 三元组契约；因此单测可完全离线跑。
* fail-open / fail-closed 划分见模块内各函数 docstring 与交付说明 §7.2。
* 兼容边界：不修改 tvpilot.tools / tvpilot.state / tvpilot.ops_log。
"""
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from tvpilot.ui_map import Bounds, UiMap, clean_text, compact_text, parse_bounds

try:  # 防御式导入：离线/无设备环境也能 import 本模块
    from tvpilot.errors import (
        ERR_INVALID_PARAMETER, ERR_MYTV_API_ERROR,
    )
except Exception:  # pragma: no cover - 仅在 errors 不可达时触发
    ERR_INVALID_PARAMETER = 'ERR_INVALID_PARAMETER'
    ERR_MYTV_API_ERROR = 'ERR_MYTV_API_ERROR'

# 规划器自有错误码（与 tvpilot.errors 的设备侧错误码区分开）
ERR_PLAN_EMPTY = 'ERR_PLAN_EMPTY'
ERR_PLAN_UNKNOWN_INTENT = 'ERR_PLAN_UNKNOWN_INTENT'
ERR_PLAN_TOO_LONG = 'ERR_PLAN_TOO_LONG'
ERR_PLAN_UNSUPPORTED_ACTION = 'ERR_PLAN_UNSUPPORTED_ACTION'
ERR_PLAN_TARGET_NOT_FOUND = 'ERR_PLAN_TARGET_NOT_FOUND'
ERR_PLAN_STEP_FAILED = 'ERR_PLAN_STEP_FAILED'
ERR_PLAN_EXPECT_MISMATCH = 'ERR_PLAN_EXPECT_MISMATCH'

# 动作白名单（执行期 fail-closed）
ACTION_TAP = 'tap'
ACTION_SWIPE = 'swipe'
ACTION_KEY = 'key'
ACTION_TEXT = 'text'
ACTION_WAIT = 'wait'
ACTION_LAUNCH = 'launch'
ACTION_HOME = 'home'
ACTION_ZAP = 'zap'
ACTION_VERIFY = 'verify'

SUPPORTED_ACTIONS = frozenset({ACTION_TAP, ACTION_SWIPE, ACTION_KEY, ACTION_TEXT,
                               ACTION_WAIT, ACTION_LAUNCH, ACTION_HOME, ACTION_ZAP,
                               ACTION_VERIFY})

_ACTION_ALIASES = {
    'click': ACTION_TAP, 'press': ACTION_KEY, 'keyevent': ACTION_KEY,
    'input_text': ACTION_TEXT, 'type': ACTION_TEXT, 'input': ACTION_TEXT,
    'launch_app': ACTION_LAUNCH, 'open_app': ACTION_LAUNCH, 'start_app': ACTION_LAUNCH,
    'go_home': ACTION_HOME,
    'channel': ACTION_ZAP, 'switch_channel': ACTION_ZAP,
    'observe': ACTION_VERIFY, 'check': ACTION_VERIFY, 'assert_ui': ACTION_VERIFY,
    'sleep': ACTION_WAIT,
}

# 动作 -> tvpilot.tools 既有工具函数名（一一对应，见 §7.3）
_TOOL_SPECS: Dict[str, str] = {
    ACTION_TAP: 'tool_tap',
    ACTION_SWIPE: 'tool_swipe',
    ACTION_KEY: 'tool_keyevent',
    ACTION_TEXT: 'tool_input_text',
    ACTION_WAIT: 'tool_wait',
    ACTION_LAUNCH: 'tool_launch_app',
    ACTION_HOME: 'tool_combo_go_home',
    ACTION_ZAP: 'tool_zap',
}

# 步骤状态
STATUS_SUCCESS = 'success'
STATUS_FAILED = 'failed'
STATUS_SKIPPED = 'skipped'
STATUS_NOT_RUN = 'not_run'

RunnerFn = Callable[[str, Optional[str], Dict[str, Any]], Tuple[Any, Any, Optional[str]]]
UiProviderFn = Callable[[], Any]
OpsLoggerFn = Callable[..., None]

DEFAULT_SETTINGS_PKG = os.environ.get('TVPILOT_SETTINGS_PKG', 'com.android.tv.settings')

# 功能项别名（真机 UI 文案差异的兜底）；key 为 compact_text 归一化形式
FEATURE_ALIASES: Dict[str, List[str]] = {
    'wifi': ['WiFi', 'WLAN', 'Wi-Fi', '无线网络', '无线', '网络设置'],
    'wlan': ['WLAN', 'WiFi', '无线网络', '无线'],
    '无线': ['无线网络', 'WLAN', 'WiFi'],
    '无线网络': ['无线网络', 'WLAN', 'WiFi'],
    '网络': ['网络', '以太网', '有线网络', 'WLAN', 'WiFi', '网络设置'],
    '蓝牙': ['蓝牙', 'Bluetooth', '蓝牙设置'],
    '声音': ['声音', '音量', '音频', '声音设置'],
    '显示': ['显示', '画面', '图像', '显示设置'],
    '通用': ['通用', '通用设置', '系统'],
    '关于': ['关于', '关于本机', '系统信息', '版本信息'],
    '存储': ['存储', '内存', '存储空间'],
    '账号': ['账号', '账户', '登录'],
    '无障碍': ['无障碍', '辅助功能'],
    '护眼': ['护眼', '护眼模式'],
    '勿扰': ['勿扰', '勿扰模式'],
    '热点': ['热点', '个人热点'],
    '定位': ['定位', '位置信息', 'GPS'],
    'hdr': ['HDR', '高动态范围'],
    '字幕': ['字幕', '字幕设置'],
}

SWITCH_ALIASES: Dict[str, List[str]] = {
    'wifi': ['WLAN开关', 'WiFi开关', '无线网络开关', '使用WLAN', '开启WLAN', '开关', '启用'],
    'wlan': ['WLAN开关', 'WiFi开关', '开关', '启用'],
    '蓝牙': ['蓝牙开关', '使用蓝牙', '开关', '启用'],
    '热点': ['热点开关', '开关', '启用'],
    '护眼': ['护眼模式开关', '开关', '启用'],
    '勿扰': ['勿扰模式开关', '开关', '启用'],
}

_KEY_ALIASES_RAW: Dict[str, str] = {
    '返回': 'KEYCODE_BACK', '后退': 'KEYCODE_BACK', '退出': 'KEYCODE_BACK',
    '主页': 'KEYCODE_HOME', '桌面': 'KEYCODE_HOME', '首页': 'KEYCODE_HOME',
    '菜单': 'KEYCODE_MENU',
    '确定': 'KEYCODE_DPAD_CENTER', '确认': 'KEYCODE_DPAD_CENTER', 'ok': 'KEYCODE_DPAD_CENTER',
    '好': 'KEYCODE_DPAD_CENTER',
    '上': 'KEYCODE_DPAD_UP', '往上': 'KEYCODE_DPAD_UP',
    '下': 'KEYCODE_DPAD_DOWN', '往下': 'KEYCODE_DPAD_DOWN',
    '左': 'KEYCODE_DPAD_LEFT', '右': 'KEYCODE_DPAD_RIGHT',
    '音量加': 'KEYCODE_VOLUME_UP', '音量增加': 'KEYCODE_VOLUME_UP',
    '音量上调': 'KEYCODE_VOLUME_UP', '声音大': 'KEYCODE_VOLUME_UP',
    '音量减': 'KEYCODE_VOLUME_DOWN', '音量减少': 'KEYCODE_VOLUME_DOWN',
    '音量下调': 'KEYCODE_VOLUME_DOWN', '声音小': 'KEYCODE_VOLUME_DOWN',
    '静音': 'KEYCODE_VOLUME_MUTE',
    '播放': 'KEYCODE_MEDIA_PLAY_PAUSE', '暂停': 'KEYCODE_MEDIA_PLAY_PAUSE',
    '播放暂停': 'KEYCODE_MEDIA_PLAY_PAUSE',
    '上一个': 'KEYCODE_MEDIA_PREVIOUS', '下一个': 'KEYCODE_MEDIA_NEXT',
}
_KEY_ALIASES: Dict[str, str] = {compact_text(k): v for k, v in _KEY_ALIASES_RAW.items()}
_KEYCODE_RE = re.compile(r'KEYCODE_[A-Z0-9_]+$')


def _normalize_action(action: Any) -> str:
    """动作名归一化（小写 + 别名折叠）。"""
    text = clean_text(action).lower()
    return _ACTION_ALIASES.get(text, text)


def _is_int(value: Any) -> bool:
    """是否为整数（接受 int / 整数值 float / 数字字符串）。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    if isinstance(value, str):
        return bool(re.fullmatch(r'-?\d+', value.strip()))
    return False


def _split_aliases(expr: str) -> List[str]:
    """把 ``A|B|C`` 形式的断言拆成别名列表。"""
    return [a.strip() for a in re.split(r'[|｜、/]', expr or '') if a.strip()]


def _parse_expectation(expr: str) -> Tuple[str, List[str]]:
    """解析断言表达式 -> (mode, aliases)；mode ∈ {contains, not_contains}。"""
    text = clean_text(expr)
    mode = 'contains'
    for prefix, mapped in (('not_contains:', 'not_contains'), ('missing:', 'not_contains'),
                           ('contains:', 'contains'), ('has:', 'contains')):
        if text.lower().startswith(prefix):
            mode = mapped
            text = text[len(prefix):]
            break
    return mode, _split_aliases(text)


def _hit_alias(alias: str, texts: Sequence[str]) -> bool:
    """别名是否命中任一界面文本（紧凑归一化后的相等或包含）。"""
    needle = compact_text(alias)
    if not needle:
        return False
    for text in texts:
        hay = compact_text(text)
        if hay and (hay == needle or needle in hay):
            return True
    return False


def _check_expected(expected: Optional[str], ui: Optional[UiMap]) -> Optional[bool]:
    """校验 ``expected`` 断言：True/False，无法判定返回 None（fail-open）。"""
    if not expected or ui is None:
        return None
    mode, aliases = _parse_expectation(expected)
    if not aliases:
        return None
    texts = ui.texts()
    hit = any(_hit_alias(a, texts) for a in aliases)
    return hit if mode == 'contains' else (not hit)


def _eval_condition(condition: Optional[str], ui: Optional[UiMap]) -> Optional[bool]:
    """评估步骤前置条件：True/False，无 UI 时返回 None（无法判定）。"""
    if not condition:
        return True
    return _check_expected(condition, ui)


def _coerce_ui(obj: Any) -> Optional[UiMap]:
    """把 UiMap / dump 字典 / 文本列表 / JSON 字符串收敛成 UiMap（fail-open）。"""
    if obj is None:
        return None
    if isinstance(obj, UiMap):
        return obj
    if isinstance(obj, Mapping):
        if obj.get('kind') == 'uimap' or 'tree' in obj or 'root' in obj:
            try:
                return UiMap.from_dump(obj)
            except Exception:
                return None
        if isinstance(obj.get('texts'), (list, tuple)):
            return UiMap.from_texts([str(t) for t in obj['texts']])
        return None
    if isinstance(obj, (str, bytes)):
        try:
            return UiMap.from_dump(obj)
        except Exception:
            return None
    if isinstance(obj, (list, tuple)):
        return UiMap.from_texts([str(t) for t in obj])
    return None


# ========== 计划数据结构 ==========

@dataclass
class PlanStep:
    """规划单步（契约字段：``action`` / ``target`` / ``params`` / ``expected``）。

    附加字段：``id``（计划内编号）、``description``、``optional``（失败降级为 skipped）、
    ``condition``（``contains:A|B`` / ``not_contains:A|B`` 前置条件）、``intent``（来源意图）。
    """

    action: str
    target: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    expected: Optional[str] = None
    id: str = ''
    description: str = ''
    optional: bool = False
    condition: Optional[str] = None
    intent: str = ''

    def to_dict(self) -> Dict[str, Any]:
        """导出 JSON 可序列化字典。"""
        return {
            'id': self.id, 'action': self.action, 'target': self.target,
            'params': dict(self.params or {}), 'expected': self.expected,
            'description': self.description, 'optional': bool(self.optional),
            'condition': self.condition, 'intent': self.intent,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> 'PlanStep':
        """从字典还原（未知键忽略，兼容 LLM 输出）。"""
        params = data.get('params')
        return cls(
            action=clean_text(data.get('action')),
            target=(clean_text(data.get('target')) or None),
            params=dict(params) if isinstance(params, Mapping) else {},
            expected=(clean_text(data.get('expected')) or None),
            id=clean_text(data.get('id')),
            description=clean_text(data.get('description')),
            optional=bool(data.get('optional', False)),
            condition=(clean_text(data.get('condition')) or None),
            intent=clean_text(data.get('intent')),
        )


@dataclass
class StepResult:
    """单步执行结果。

    ``status`` ∈ success / failed / skipped / not_run；``expected_met`` 三值
    （True/False/None=无法判定）。
    """

    step_id: str = ''
    action: str = ''
    target: Optional[str] = None
    ok: bool = False
    status: str = STATUS_NOT_RUN
    params: Dict[str, Any] = field(default_factory=dict)
    expected: Optional[str] = None
    expected_met: Optional[bool] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)
    cost_ms: int = 0
    started_at: float = 0.0

    @classmethod
    def not_run(cls, step: PlanStep, reason: str = '前置步骤失败，未执行') -> 'StepResult':
        """构造「未执行」结果（失败即停时用于报告剩余步骤）。"""
        return cls(step_id=step.id, action=step.action, target=step.target, ok=False,
                   status=STATUS_NOT_RUN, params=dict(step.params or {}),
                   expected=step.expected, error=reason)

    def to_dict(self) -> Dict[str, Any]:
        """导出 JSON 可序列化字典。"""
        return {
            'step_id': self.step_id, 'action': self.action, 'target': self.target,
            'ok': bool(self.ok), 'status': self.status, 'params': dict(self.params or {}),
            'expected': self.expected, 'expected_met': self.expected_met,
            'error': self.error, 'error_code': self.error_code,
            'detail': dict(self.detail or {}), 'cost_ms': int(self.cost_ms),
            'started_at': float(self.started_at),
        }


class Plan(list):
    """计划：``list[PlanStep]`` + 元数据（``task``/``plan_id``/``ok``/``error``…）。

    继承 list，``len(plan)`` / ``plan[0]`` / 迭代 与 ``plan.steps`` / ``plan.ok`` 皆可用。
    """

    def __init__(self, steps: Optional[Sequence[PlanStep]] = None, *,
                 task: str = '', plan_id: str = '', intent: str = '', ok: bool = True,
                 error: Optional[str] = None, error_code: Optional[str] = None,
                 meta: Optional[Dict[str, Any]] = None, created_at: Optional[float] = None) -> None:
        super().__init__(steps or [])
        self.task = task
        self.plan_id = plan_id or f'plan-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}'
        self.intent = intent
        self.ok = bool(ok)
        self.error = error
        self.error_code = error_code
        self.meta: Dict[str, Any] = dict(meta or {})
        self.created_at = created_at if created_at is not None else time.time()

    @property
    def steps(self) -> 'Plan':
        """计划步骤（即自身，保持 list 语义）。"""
        return self

    def to_dict(self) -> Dict[str, Any]:
        """导出 JSON 可序列化计划。"""
        return {
            'plan_id': self.plan_id, 'task': self.task, 'intent': self.intent,
            'ok': bool(self.ok), 'error': self.error, 'error_code': self.error_code,
            'meta': dict(self.meta), 'created_at': self.created_at,
            'steps': [s.to_dict() for s in self],
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> 'Plan':
        """从字典/JSON 还原计划。"""
        steps = [PlanStep.from_dict(s) for s in (data.get('steps') or []) if isinstance(s, Mapping)]
        return cls(steps, task=clean_text(data.get('task')), plan_id=clean_text(data.get('plan_id')),
                   intent=clean_text(data.get('intent')), ok=bool(data.get('ok', True)),
                   error=data.get('error'), error_code=data.get('error_code'),
                   meta=dict(data.get('meta') or {}), created_at=data.get('created_at'))


class ExecutionReport(list):
    """执行报告：``list[StepResult]`` + ``ok``/``stopped_at``/``error``/``counts``。"""

    def __init__(self, results: Optional[Sequence[StepResult]] = None, *,
                 plan_id: str = '', task: str = '', ok: bool = True,
                 error: Optional[str] = None, error_code: Optional[str] = None,
                 stopped_at: Optional[int] = None) -> None:
        super().__init__(results or [])
        self.plan_id = plan_id
        self.task = task
        self.ok = bool(ok)
        self.error = error
        self.error_code = error_code
        self.stopped_at = stopped_at

    @property
    def steps(self) -> 'ExecutionReport':
        """逐步结果（即自身，保持 list 语义）。"""
        return self

    def counts(self) -> Dict[str, int]:
        """各状态计数。"""
        out = {STATUS_SUCCESS: 0, STATUS_FAILED: 0, STATUS_SKIPPED: 0, STATUS_NOT_RUN: 0}
        for r in self:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        """导出 JSON 可序列化报告。"""
        return {
            'plan_id': self.plan_id, 'task': self.task, 'ok': bool(self.ok),
            'error': self.error, 'error_code': self.error_code, 'stopped_at': self.stopped_at,
            'counts': self.counts(),
            'results': [r.to_dict() for r in self],
        }


# ========== 默认执行器（对接 §1 现有工具） ==========

def _build_tool_args(action: str, target: Optional[str], params: Mapping) -> Tuple[Tuple[Any, ...], Dict[str, Any], Optional[str]]:
    """把 (action, target, params) 翻译成 tvpilot.tools 既有工具的位置参数。"""
    p = dict(params or {})
    if action == ACTION_TAP:
        if not (_is_int(p.get('x')) and _is_int(p.get('y'))):
            return (), {}, 'tap 需要整数 x/y'
        return (int(p['x']), int(p['y'])), {}, None
    if action == ACTION_SWIPE:
        keys = ('x1', 'y1', 'x2', 'y2')
        if not all(_is_int(p.get(k)) for k in keys):
            return (), {}, 'swipe 需要整数 x1/y1/x2/y2'
        args = tuple(int(p[k]) for k in keys) + (int(p.get('duration_ms', 300)),)
        return args, {}, None
    if action == ACTION_KEY:
        key = p.get('key') or target
        if not isinstance(key, str) or not key:
            return (), {}, 'key 需要 KEYCODE_XXX'
        return (key,), {}, None
    if action == ACTION_TEXT:
        text = p.get('text') if p.get('text') is not None else target
        if text is None or text == '':
            return (), {}, 'text 不能为空'
        return (str(text),), {}, None
    if action == ACTION_WAIT:
        return (float(p.get('seconds', 2)),), {}, None
    if action == ACTION_LAUNCH:
        package = p.get('package') or target
        if not isinstance(package, str) or not package:
            return (), {}, 'launch 需要 package'
        return (package, p.get('activity')), {}, None
    if action == ACTION_HOME:
        return (), {}, None
    if action == ACTION_ZAP:
        channel = p.get('channel') if p.get('channel') is not None else target
        if channel is None or channel == '':
            return (), {}, 'zap 需要 channel'
        return (str(channel),), {}, None
    return (), {}, f'不支持的动作: {action}'


def tool_runner(action: str, target: Optional[str] = None,
                params: Optional[Mapping] = None) -> Tuple[Any, Any, Optional[str]]:
    """默认动作执行器：分发到 ``tvpilot.tools`` 既有工具。

    返回值严格沿用 §1 的 ``(data, err_code, err_msg)`` 三元组；未知动作在
    import 之前即 fail-closed 返回错误码，保证离线可测。
    """
    act = _normalize_action(action)
    fn_name = _TOOL_SPECS.get(act)
    if not fn_name:
        return None, ERR_INVALID_PARAMETER, f'不支持的动作: {action}'
    args, kwargs, err = _build_tool_args(act, target, params or {})
    if err:
        return None, ERR_INVALID_PARAMETER, err
    from tvpilot import tools as _tools  # 延迟导入：无设备/无 adb 环境也能 import 本模块
    fn = getattr(_tools, fn_name, None)
    if fn is None:
        return None, ERR_INVALID_PARAMETER, f'缺少工具实现: {fn_name}'
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 任何工具异常都要变成可报告的失败
        return None, ERR_MYTV_API_ERROR, f'{fn_name} 异常: {exc}'


def make_default_ops_logger() -> OpsLoggerFn:
    """构造默认操作存档记录器（惰性 import ``tvpilot.ops_log.log_operation``）。"""

    def _log(**kwargs: Any) -> None:
        from tvpilot.ops_log import log_operation
        log_operation(**kwargs)

    return _log


# ========== 意图规则（规则 + 模板） ==========

@dataclass
class _Ctx:
    """规划上下文：当前 UI（用于烘焙坐标）与可配置项。"""

    ui: Optional[UiMap]
    apps: Mapping[str, str]
    settings_pkg: str = DEFAULT_SETTINGS_PKG

    def is_feature(self, name: str) -> bool:
        """是否为已知功能项。"""
        return compact_text(name) in FEATURE_ALIASES

    def is_app(self, name: str) -> bool:
        """是否为已配置包名的应用。"""
        return compact_text(name) in self.apps

    def app_package(self, name: str) -> Optional[str]:
        """取应用包名（未配置返回 None，规划器不猜包名）。"""
        return self.apps.get(compact_text(name))

    def item_aliases(self, item: str) -> List[str]:
        """功能项的目标别名列表（去重保序）。"""
        key = compact_text(item)
        base = list(FEATURE_ALIASES.get(key, []))
        for alias in (item, f'{item}设置', f'{item}开关'):
            if alias and alias not in base:
                base.append(alias)
        return base

    def switch_aliases(self, item: str) -> List[str]:
        """功能项开关的别名列表。"""
        key = compact_text(item)
        base = list(SWITCH_ALIASES.get(key, []))
        for alias in (f'{item}开关', '开关', '启用', '开启'):
            if alias and alias not in base:
                base.append(alias)
        return base

    def tap_params(self, aliases: Sequence[str]) -> Dict[str, Any]:
        """若当前 UI 能定位到别名节点，则烘焙出 ``{'x','y'}``，否则返回空字典。"""
        if self.ui is None:
            return {}
        for alias in aliases:
            node, _hit = self.ui.resolve(alias)
            if node is not None and node.bounds.area > 0:
                x, y = node.bounds.center
                return {'x': x, 'y': y}
        return {}


def _open_settings_step(ctx: _Ctx) -> PlanStep:
    """生成「打开设置」步骤：优先点 UI 上的"设置"，找不到则回退 ``am start`` 设置页。"""
    params = ctx.tap_params(['设置'])
    params['fallback'] = {'action': ACTION_LAUNCH, 'package': ctx.settings_pkg}
    return PlanStep(action=ACTION_TAP, target='设置', params=params,
                    expected='contains:设置|通用|网络|显示|声音|关于|WLAN|蓝牙',
                    description='打开"设置"')


def _wait_step(seconds: float = 0.6) -> PlanStep:
    """生成等待步骤（TV 界面切换较慢，避免下一步抢跑）。"""
    return PlanStep(action=ACTION_WAIT, target=None, params={'seconds': seconds},
                    expected=None, description='等待界面切换')


def _verify_step(target: Optional[str], expected: Optional[str], desc: str) -> PlanStep:
    """生成纯校验步骤（不下发任何指令）。"""
    return PlanStep(action=ACTION_VERIFY, target=target, params={}, expected=expected,
                    description=desc)


def _settings_flow(item: str, ctx: _Ctx, state: Optional[str] = None) -> List[PlanStep]:
    """设置项通用流程：打开设置 -> 等待 -> 进入<item> -> [开/关开关] -> 验证。

    ``state`` 为 None 表示只导航到该项（「打开设置里的WiFi」按此语义解释）；
    ``'on'``/``'off'`` 表示还要动开关（「打开WiFi / 关闭WiFi」）。
    """
    aliases = ctx.item_aliases(item)
    selector = '|'.join(aliases)
    expected = 'contains:' + '|'.join(aliases[:4])
    steps = [
        _open_settings_step(ctx),
        _wait_step(),
        PlanStep(action=ACTION_TAP, target=selector, params=ctx.tap_params(aliases),
                 expected=expected, description=f'进入"{item}"设置项'),
    ]
    if state is not None:
        switch_selector = '|'.join(ctx.switch_aliases(item))
        condition = 'not_contains:已连接|已开启' if state == 'on' else 'contains:已连接|已开启'
        steps.append(PlanStep(
            action=ACTION_TAP, target=switch_selector,
            params={**ctx.tap_params(ctx.switch_aliases(item)), 'state': state},
            expected=None, condition=condition, optional=True,
            description=f'{"开启" if state == "on" else "关闭"}{item}开关'))
    steps.append(_verify_step(selector, expected, f'确认进入{item}设置页'))
    return steps


def _h_open_settings_item(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：打开「设置里的 X」（默认解释为进入 X 设置页）。"""
    item = clean_text(m.group('item')).strip(' 的了')
    if not item:
        return None
    return _settings_flow(item, ctx, state=None)


def _h_open_settings(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：打开设置。"""
    return [_open_settings_step(ctx), _wait_step()]


def _h_settings_page(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：进入「X 设置」页。"""
    item = clean_text(m.group('item'))
    if not item:
        return None
    return _settings_flow(item, ctx, state=None)


def _h_toggle_feature(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：开/关某功能（仅限已知功能项或「模式/开关/功能」结尾）。"""
    item = clean_text(m.group('item'))
    verb = clean_text(m.group('verb'))
    if not item or ctx.is_app(item):
        return None
    if not ctx.is_feature(item) and not re.search(r'(模式|开关|功能)$', item):
        return None
    state = 'off' if any(t in verb for t in ('关', '断', '禁')) else 'on'
    return _settings_flow(item, ctx, state=state)


def _h_go_home(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：回到桌面（工具自带前台验证）。"""
    return [
        PlanStep(action=ACTION_HOME, target=None, params={}, expected=None, description='回到桌面'),
        _verify_step(None, 'contains:首页|推荐|应用|我的|桌面|设置', '确认已在桌面'),
    ]


def _h_back(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：返回上一级。"""
    return [PlanStep(action=ACTION_KEY, target='KEYCODE_BACK', params={'key': 'KEYCODE_BACK'},
                     expected=None, description='返回')]


def _h_volume(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：调音量（按格数重复按键）。"""
    direction = clean_text(m.group('dir'))
    times = int(m.group('num') or 1)
    times = max(1, min(times, 20))
    up = any(t in direction for t in ('加', '增', '大', '高', '升', '提'))
    key = 'KEYCODE_VOLUME_UP' if up else 'KEYCODE_VOLUME_DOWN'
    return [PlanStep(action=ACTION_KEY, target=key, params={'key': key}, expected=None,
                     description=f'{"调高" if up else "调低"}音量 x{times}') for _ in range(times)]


def _h_press_key(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：按某键（中文键名映射 KEYCODE_*；未知键名让位给点击语义）。"""
    key = clean_text(m.group('key')).removesuffix('按钮').removesuffix('键')
    code = _KEY_ALIASES.get(compact_text(key))
    if code is None and _KEYCODE_RE.fullmatch(key or ''):
        code = key
    if code is None:
        return None
    return [PlanStep(action=ACTION_KEY, target=code, params={'key': code}, expected=None,
                     description=f'按键 {code}')]


def _h_search(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：搜索关键词。"""
    kw = clean_text(m.group('kw'))
    if not kw:
        return None
    search_aliases = ['搜索', '查找', '搜索栏', '请输入搜索词']
    params = ctx.tap_params(search_aliases)
    params['fallback'] = {'action': ACTION_KEY, 'key': 'KEYCODE_SEARCH'}
    return [
        PlanStep(action=ACTION_TAP, target='|'.join(search_aliases), params=params,
                 expected='contains:搜索|查找|请输入', description='进入搜索'),
        PlanStep(action=ACTION_TEXT, target=kw, params={'text': kw}, expected=None,
                 description=f'输入关键词"{kw}"'),
        _wait_step(1.0),
        _verify_step(kw, f'contains:{kw}', '确认搜索结果包含关键词'),
    ]


def _h_input_text(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：输入文本。"""
    text = clean_text(m.group('text'))
    if not text:
        return None
    return [
        PlanStep(action=ACTION_TEXT, target=text, params={'text': text}, expected=None,
                 description=f'输入"{text}"'),
        _verify_step(text, f'contains:{text}', '确认文本已进入界面'),
    ]


def _looks_like_channel(name: str) -> bool:
    """启发式判断是否像频道名（避免「看XXX」被误判成换台）。"""
    return bool(re.search(r'台$|频道|卫视|cctv|\d', normalize_key(name))) or '台' in name


def normalize_key(text: str) -> str:
    """小写归一化（内部辅助）。"""
    return clean_text(text).lower()


def _h_zap(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：换台（`看XXX` 需要像频道名才认）。"""
    channel = clean_text(m.group('ch'))
    if not channel:
        return None
    if clean_text(m.group(0)).startswith('看') and not _looks_like_channel(channel):
        return None
    return [
        PlanStep(action=ACTION_ZAP, target=channel, params={'channel': channel}, expected=None,
                 description=f'换台到 {channel}'),
        _verify_step(channel, f'contains:{channel}', '确认当前频道'),
    ]


def _h_launch_app(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：打开应用（有包名则 ``am start``，否则回桌面点图标，不猜包名）。"""
    app = clean_text(m.group('app'))
    if not app:
        return None
    package = ctx.app_package(app)
    steps = [PlanStep(action=ACTION_HOME, target=None, params={}, expected=None,
                      description='回到桌面便于定位图标')]
    if package:
        steps.append(PlanStep(action=ACTION_LAUNCH, target=package,
                              params={'package': package}, expected=None,
                              description=f'启动 {app}（{package}）'))
    else:
        aliases = [app, f'{app}TV', f'{app} HD']
        steps.append(PlanStep(action=ACTION_TAP, target='|'.join(aliases),
                              params=ctx.tap_params(aliases), expected=None,
                              description=f'点击"{app}"图标'))
    steps.append(_verify_step(app, f'contains:{app}', f'确认{app}已打开'))
    return steps


def _h_click_object(m: Any, clause: str, ctx: _Ctx) -> Optional[List[PlanStep]]:
    """意图：点击/选择某对象（通用兜底，靠 UI 文本定位）。"""
    obj = clean_text(m.group('obj'))
    if not obj:
        return None
    return [PlanStep(action=ACTION_TAP, target=obj, params=ctx.tap_params([obj]),
                     expected=None, description=f'点击"{obj}"')]


_INTENT_RULES: List[Tuple[str, Any]] = [
    ('open_settings_item', re.compile(r'^(?:请|帮我|麻烦)?(?:打开|进入|开启|去|找到)?(?:系统)?设置'
                                      r'(?:里|中|内|里面)?的\s*(?P<item>[^，,；;。]+?)\s*$')),
    ('open_settings', re.compile(r'^(?:请|帮我|麻烦)?(?:打开|进入|开启|去)?(?:系统)?设置$')),
    ('settings_page', re.compile(r'^(?:请|帮我|麻烦)?(?:打开|进入|开启|去)\s*'
                                 r'(?P<item>[\w\u4e00-\u9fff\-]{1,10})\s*设置(?:页|界面|项)?$')),
    ('toggle_feature', re.compile(r'^(?:请|帮我|麻烦)?(?P<verb>打开|开启|启用|关掉|关闭|断开|禁用)'
                                  r'\s*(?P<item>[\w\u4e00-\u9fff\-]{1,10})(?:功能|开关|设置)?$')),
    ('go_home', re.compile(r'^(?:请|帮我|麻烦)?(?:回到|返回|回)?(?:桌面|主页|首页)$')),
    ('back', re.compile(r'^(?:请|帮我|麻烦)?(?:按)?返回(?:键)?$')),
    ('volume', re.compile(r'^(?:请)?(?:把)?(?:音量|声音)(?:调|变|往)?'
                          r'(?P<dir>加大|增大|上调|调大|调高|提升|减小|降低|下调|调小|调低|加|大|高|减|小|低)'
                          r'(?:\s*(?P<num>\d+)\s*(?:格|级)?)?$')),
    ('press_key', re.compile(r'^(?:请|帮我)?(?:按一下|点按|按下|按)\s*'
                             r'(?P<key>[\w\u4e00-\u9fff\-+]{1,12})(?:键|按钮)?$')),
    ('search', re.compile(r'^(?:请|帮我)?(?:搜索|查找|搜|找)(?:一下)?(?P<kw>[^，,；;。]+)$')),
    ('input_text', re.compile(r'^(?:请|帮我)?(?:输入|键入|填写|敲入)\s*(?P<text>[^，,；;。]+)$')),
    ('zap', re.compile(r'^(?:请|帮我)?(?:换台到|换到|切换到|转到|看|播)\s*'
                       r'(?P<ch>[^，,；;。]+?)(?:台|频道)?$')),
    ('launch_app', re.compile(r'^(?:请|帮我|麻烦)?(?:打开|启动|运行|开启)\s*(?P<app>[^，,；;。]+?)\s*$')),
    ('click_object', re.compile(r'^(?:请|帮我)?(?:点击|点一下|点|选择|选中|进入|按下|按)\s*'
                                r'(?P<obj>[^，,；;。]+)$')),
]

_INTENT_HANDLERS: Dict[str, Any] = {
    'open_settings_item': _h_open_settings_item,
    'open_settings': _h_open_settings,
    'settings_page': _h_settings_page,
    'toggle_feature': _h_toggle_feature,
    'go_home': _h_go_home,
    'back': _h_back,
    'volume': _h_volume,
    'press_key': _h_press_key,
    'search': _h_search,
    'input_text': _h_input_text,
    'zap': _h_zap,
    'launch_app': _h_launch_app,
    'click_object': _h_click_object,
}

_CLAUSE_SPLIT = re.compile(r'[，,；;。]|然后|接着|随后|之后')


def _split_clauses(task: str) -> List[str]:
    """把任务描述切成子任务（按顺序执行）；不做「和/再」切分以避免误伤词内字。"""
    parts = [clean_text(p) for p in _CLAUSE_SPLIT.split(task)]
    return [p for p in parts if p]


def _match_intent(clause: str, ctx: _Ctx) -> Tuple[Optional[str], Optional[List[PlanStep]]]:
    """按规则表顺序匹配意图；handler 可返回 None 表示让位给下一条规则。"""
    for name, pattern in _INTENT_RULES:
        m = pattern.match(clause)
        if not m:
            continue
        produced = _INTENT_HANDLERS[name](m, clause, ctx)
        if produced:
            return name, produced
    return None, None


# ========== TaskPlanner ==========

class TaskPlanner:
    """自然语言 -> 多步 PlanStep；执行逐步产生 StepResult，失败即停并报告。

    :param runner: 动作执行器 ``(action, target, params) -> (data, err_code, err_msg)``；
        默认 :func:`tool_runner`（惰性调用 ``tvpilot.tools``）
    :param ui_provider: 返回当前 UiMap（或 dump/文本列表）的回调，用于坐标解析与断言
    :param ops_logger: 关键字参数形式的操作记录器（与 ``ops_log.log_operation`` 对齐）
    :param llm: 可选 LLM 规划器（需实现 ``generate(task_desc, ui_dump) -> Sequence[Mapping]``）；
        输出非法/异常一律降级回规则模板
    :param strict_expect: True 时期望不满足算失败（默认 fail-open）
    :param stop_on_error: True 时失败即停（默认），False 继续执行剩余步骤
    """

    def __init__(self,
                 runner: Optional[RunnerFn] = None,
                 ui_provider: Optional[UiProviderFn] = None,
                 ops_logger: Optional[OpsLoggerFn] = None,
                 llm: Optional[Any] = None,
                 apps: Optional[Mapping[str, str]] = None,
                 settings_pkg: Optional[str] = None,
                 strict_expect: bool = False,
                 stop_on_error: bool = True,
                 step_pause_s: float = 0.0,
                 max_steps: int = 24) -> None:
        self._runner: RunnerFn = runner or tool_runner
        self._ui_provider = ui_provider
        self._ops_logger = ops_logger
        self._llm = llm
        self.strict_expect = bool(strict_expect)
        self.stop_on_error = bool(stop_on_error)
        self.step_pause_s = float(step_pause_s)
        self.max_steps = int(max_steps)
        self.settings_pkg = settings_pkg or DEFAULT_SETTINGS_PKG
        merged: Dict[str, str] = {}
        try:
            merged.update(json.loads(os.environ.get('TVPILOT_APP_ALIASES', '{}') or '{}'))
        except Exception:
            pass
        if apps:
            merged.update({str(k): str(v) for k, v in apps.items()})
        self.apps: Dict[str, str] = merged
        self.last_plan: Optional[Plan] = None
        self.last_report: Optional[ExecutionReport] = None

    # ---------- UI 快照 ----------

    def _peek_ui(self, fallback: Optional[UiMap] = None) -> Optional[UiMap]:
        """取最新 UI 快照（fail-open：provider 不可用时退回已知快照）。"""
        if self._ui_provider is not None:
            try:
                fresh = _coerce_ui(self._ui_provider())
                if fresh is not None:
                    return fresh
            except Exception:
                pass
        return fallback

    # ---------- 规划 ----------

    def plan(self, task_desc: str, current_ui: Any = None) -> Plan:
        """把自然语言任务拆成 :class:`PlanStep` 列表（契约入口）。

        :param task_desc: 任务描述（支持「然后/接着/，/；」多子任务串联）
        :param current_ui: 当前界面（UiMap / dump 字典 / 文本列表 / JSON 字符串），用于烘焙坐标
        :returns: :class:`Plan`（即 ``list[PlanStep]``）；无法解析时 ``plan.ok=False`` 且为空
        """
        task = clean_text(task_desc)
        if not task:
            return Plan([], task=task, ok=False, error='任务描述为空', error_code=ERR_PLAN_EMPTY)

        source = 'rules'
        steps: List[PlanStep] = []
        intents: List[str] = []
        if self._llm is not None:                      # 预留 LLM 接口：非法输出降级规则
            produced, ok = self._plan_with_llm(task, current_ui)
            if ok and produced:
                steps, intents, source = produced, ['llm'], 'llm'

        if not steps:
            ctx = _Ctx(ui=_coerce_ui(current_ui), apps=self.apps, settings_pkg=self.settings_pkg)
            for clause in _split_clauses(task):
                name, produced_steps = _match_intent(clause, ctx)
                if produced_steps is None:
                    return Plan([], task=task, ok=False,
                                error=f'无法解析子任务: {clause}', error_code=ERR_PLAN_UNKNOWN_INTENT,
                                meta={'planner_source': source, 'clause': clause})
                steps.extend(produced_steps)
                intents.append(name or 'unknown')

        if not steps:
            return Plan([], task=task, ok=False, error='未产出任何步骤', error_code=ERR_PLAN_EMPTY)
        if len(steps) > self.max_steps:
            return Plan([], task=task, ok=False, error=f'计划步数超限: {len(steps)} > {self.max_steps}',
                        error_code=ERR_PLAN_TOO_LONG, meta={'planner_source': source})
        for index, step in enumerate(steps, 1):
            if not step.id:
                step.id = f's{index}'
        plan = Plan(steps, task=task, intent='+'.join(intents), ok=True,
                    meta={'planner_source': source})
        self.last_plan = plan
        return plan

    def _plan_with_llm(self, task: str, current_ui: Any) -> Tuple[List[PlanStep], bool]:
        """调用外部 LLM 规划器并校验输出（fail-open 到规则：返回 ([], False)）。"""
        try:
            ui_dump = current_ui.dump() if isinstance(current_ui, UiMap) else current_ui
            raw = self._llm.generate(task, ui_dump)
        except Exception:
            return [], False
        if not isinstance(raw, (list, tuple)) or not raw:
            return [], False
        steps: List[PlanStep] = []
        for item in raw:
            if not isinstance(item, Mapping):
                return [], False
            step = PlanStep.from_dict(item)
            if _normalize_action(step.action) not in SUPPORTED_ACTIONS:
                return [], False
            steps.append(step)
        return steps, True

    # ---------- 执行 ----------

    def execute(self, plan: Union[Plan, Sequence[PlanStep], Mapping, str], *,
                ui: Any = None, stop_on_error: Optional[bool] = None,
                refresh_ui: bool = True) -> ExecutionReport:
        """执行计划（契约入口）：每步返回 :class:`StepResult`，失败即停并报告。

        :param plan: :class:`Plan` / ``[PlanStep]`` / ``{'steps': [...]}`` / JSON 字符串
        :param ui: 执行起点 UI（缺省用 ``ui_provider``）
        :param stop_on_error: 覆盖构造时的失败即停开关
        :param refresh_ui: 每步前后刷新 UI 快照（用于解析坐标与校验期望）
        :returns: :class:`ExecutionReport`（即 ``list[StepResult]``）
        """
        steps = _coerce_steps(plan)
        plan_id = str(getattr(plan, 'plan_id', '') or '')
        task = str(getattr(plan, 'task', '') or '')
        if not steps:
            report = ExecutionReport([], plan_id=plan_id, task=task, ok=False,
                                     error='计划为空', error_code=ERR_PLAN_EMPTY)
            self.last_report = report
            return report

        stop = self.stop_on_error if stop_on_error is None else bool(stop_on_error)
        current: Optional[UiMap] = _coerce_ui(ui) if ui is not None else self._peek_ui()
        report = ExecutionReport([], plan_id=plan_id, task=task, ok=True)
        aborted = False

        for index, step in enumerate(steps):
            if aborted and stop:
                report.append(StepResult.not_run(step))
                continue
            result, current = self._run_step(step, current, refresh_ui=refresh_ui)
            report.append(result)
            self._log_step(result, plan_id)
            if result.status == STATUS_FAILED:
                aborted = True
                report.ok = False
                if stop:
                    report.stopped_at = index
                report.error = result.error
                report.error_code = result.error_code
            if self.step_pause_s > 0:
                time.sleep(self.step_pause_s)

        self.last_report = report
        return report

    def run(self, task_desc: str, current_ui: Any = None) -> ExecutionReport:
        """便捷入口：先 :meth:`plan` 再 :meth:`execute`。"""
        return self.execute(self.plan(task_desc, current_ui), ui=current_ui)

    # ---------- 内部 ----------

    def _log_step(self, result: StepResult, plan_id: str) -> None:
        """写操作存档（fail-open：记录器异常不影响执行）。"""
        if self._ops_logger is None:
            return
        try:
            self._ops_logger(tool=f'plan.{result.action}', params=result.params,
                             ok=bool(result.ok), error=result.error,
                             cost_ms=int(result.cost_ms), request_id=plan_id or None)
        except Exception:
            pass

    def _run_step(self, step: PlanStep, ui: Optional[UiMap],
                  refresh_ui: bool = True) -> Tuple[StepResult, Optional[UiMap]]:
        """执行单步；返回 (结果, 最新 UI 快照)。"""
        t0 = time.time()
        action = _normalize_action(step.action)
        detail: Dict[str, Any] = {}
        ui_now = self._peek_ui(ui) if refresh_ui else ui

        def finish(status: str, *, error: Optional[str] = None,
                   error_code: Optional[str] = None,
                   expected_met: Optional[bool] = None,
                   payload: Any = None,
                   ui_after: Optional[UiMap] = None) -> Tuple[StepResult, Optional[UiMap]]:
            if status == STATUS_FAILED and step.optional:      # optional 步：失败降级 skipped
                status = STATUS_SKIPPED
            if payload is not None:
                detail['result'] = payload
            result = StepResult(step_id=step.id, action=step.action, target=step.target,
                                ok=(status == STATUS_SUCCESS), status=status,
                                params=dict(step.params or {}), expected=step.expected,
                                expected_met=expected_met, error=error, error_code=error_code,
                                detail=detail, cost_ms=int((time.time() - t0) * 1000),
                                started_at=t0)
            return result, (ui_after if ui_after is not None else ui_now)

        # 1) 前置条件
        if step.condition:
            verdict = _eval_condition(step.condition, ui_now)
            detail['condition'] = {'expr': step.condition, 'value': verdict}
            if verdict is False:
                return finish(STATUS_SKIPPED, error=f'条件不满足: {step.condition}')
            if verdict is None and step.optional:
                return finish(STATUS_SKIPPED, error=f'条件无法判定: {step.condition}')

        # 2) 动作白名单（fail-closed）
        if action not in SUPPORTED_ACTIONS:
            return finish(STATUS_FAILED, error=f'不支持的动作: {step.action}',
                          error_code=ERR_PLAN_UNSUPPORTED_ACTION)

        # 3) verify：纯校验，不下发指令
        if action == ACTION_VERIFY:
            ui_after = self._peek_ui(ui_now) if refresh_ui else ui_now
            met = _check_expected(step.expected, ui_after)
            if met is False and self.strict_expect:
                return finish(STATUS_FAILED, error=f'期望未满足: {step.expected}',
                              error_code=ERR_PLAN_EXPECT_MISMATCH, expected_met=met, ui_after=ui_after)
            return finish(STATUS_SUCCESS, expected_met=met, ui_after=ui_after)

        run_action = action
        run_params: Dict[str, Any] = {k: v for k, v in (step.params or {}).items() if k != 'fallback'}

        # 4) 几何解析（tap 需要坐标；解析失败可用 params['fallback'] 兜底）
        if action == ACTION_TAP:
            geom, info = self._resolve_geometry(step, ui_now)
            if geom is None:
                fallback = (step.params or {}).get('fallback')
                if isinstance(fallback, Mapping) and fallback.get('action'):
                    run_action = _normalize_action(str(fallback['action']))
                    run_params = {k: v for k, v in fallback.items() if k != 'action'}
                    detail['fallback'] = dict(fallback)
                else:
                    return finish(STATUS_FAILED, error=f'找不到目标: {step.target}',
                                  error_code=ERR_PLAN_TARGET_NOT_FOUND)
            else:
                run_params.update(geom)
                detail.update(info)
        elif action == ACTION_SWIPE:
            if not all(_is_int(run_params.get(k)) for k in ('x1', 'y1', 'x2', 'y2')):
                return finish(STATUS_FAILED, error='滑动缺少整数 x1/y1/x2/y2',
                              error_code=ERR_INVALID_PARAMETER)

        # 5) 必需参数校验（fail-closed）
        _args, _kwargs, arg_err = _build_tool_args(run_action, step.target, run_params)
        if arg_err:
            return finish(STATUS_FAILED, error=arg_err, error_code=ERR_INVALID_PARAMETER)

        # 6) 下发动作
        try:
            data, err_code, err_msg = self._runner(run_action, step.target, run_params)
        except Exception as exc:  # noqa: BLE001 - runner 异常必须变成可报告的失败
            return finish(STATUS_FAILED, error=f'动作 {action} 抛出异常: {exc}',
                          error_code=ERR_PLAN_STEP_FAILED)
        if err_code:
            return finish(STATUS_FAILED, error=err_msg or str(err_code),
                          error_code=str(err_code), payload=data)

        # 7) 期望校验（默认 fail-open）
        ui_after = self._peek_ui(ui_now) if refresh_ui else ui_now
        met = _check_expected(step.expected, ui_after)
        if met is False and self.strict_expect:
            return finish(STATUS_FAILED, error=f'期望未满足: {step.expected}',
                          error_code=ERR_PLAN_EXPECT_MISMATCH, expected_met=met,
                          payload=data, ui_after=ui_after)
        return finish(STATUS_SUCCESS, expected_met=met, payload=data, ui_after=ui_after)

    def _resolve_geometry(self, step: PlanStep,
                          ui: Optional[UiMap]) -> Tuple[Optional[Dict[str, int]], Dict[str, Any]]:
        """把 ``target`` 解析成 ``{'x','y'}``；优先 params 显式坐标，其次 UI 文本定位。"""
        params = dict(step.params or {})
        if _is_int(params.get('x')) and _is_int(params.get('y')):
            return {'x': int(params['x']), 'y': int(params['y'])}, {'coord_source': 'params'}
        bounds = parse_bounds(params.get('bounds'))
        if bounds is not None and bounds.area > 0:
            x, y = bounds.center
            return {'x': x, 'y': y}, {'coord_source': 'params.bounds'}
        target = clean_text(step.target)
        if not target:
            return None, {}
        m = re.fullmatch(r'(\d+)\s*[,，xX]\s*(\d+)', target)
        if m:
            return {'x': int(m.group(1)), 'y': int(m.group(2))}, {'coord_source': 'target_xy'}
        if ui is not None:
            node, alias = ui.resolve(target)
            if node is not None and node.bounds.area > 0:
                x, y = node.bounds.center
                return {'x': x, 'y': y}, {'coord_source': 'ui_text',
                                          'resolved_target': alias, 'node_id': node.id}
        return None, {}


def _coerce_steps(plan: Any) -> List[PlanStep]:
    """把 Plan / 步骤列表 / dump 字典 / JSON 字符串收敛成 ``[PlanStep]``。"""
    if plan is None:
        return []
    if isinstance(plan, Plan):
        return [s for s in plan]
    if isinstance(plan, Mapping):
        return [PlanStep.from_dict(s) for s in (plan.get('steps') or []) if isinstance(s, Mapping)]
    if isinstance(plan, (str, bytes)):
        try:
            return _coerce_steps(json.loads(plan if isinstance(plan, str) else plan.decode('utf-8')))
        except Exception:
            return []
    if isinstance(plan, (list, tuple)):
        out: List[PlanStep] = []
        for item in plan:
            if isinstance(item, PlanStep):
                out.append(item)
            elif isinstance(item, Mapping):
                out.append(PlanStep.from_dict(item))
        return out
    return []


__all__ = [
    'ERR_PLAN_EMPTY', 'ERR_PLAN_UNKNOWN_INTENT', 'ERR_PLAN_TOO_LONG',
    'ERR_PLAN_UNSUPPORTED_ACTION', 'ERR_PLAN_TARGET_NOT_FOUND',
    'ERR_PLAN_STEP_FAILED', 'ERR_PLAN_EXPECT_MISMATCH',
    'ACTION_TAP', 'ACTION_SWIPE', 'ACTION_KEY', 'ACTION_TEXT', 'ACTION_WAIT',
    'ACTION_LAUNCH', 'ACTION_HOME', 'ACTION_ZAP', 'ACTION_VERIFY',
    'SUPPORTED_ACTIONS', 'STATUS_SUCCESS', 'STATUS_FAILED', 'STATUS_SKIPPED', 'STATUS_NOT_RUN',
    'FEATURE_ALIASES', 'SWITCH_ALIASES',
    'PlanStep', 'StepResult', 'Plan', 'ExecutionReport', 'TaskPlanner',
    'tool_runner', 'make_default_ops_logger',
]
