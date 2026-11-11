"""TVPilot 蓝图系统（Blueprint / BlueprintRunner / StepResult）。

把既有原子工具（换台 / 按键 / 点击 / 滑动 / 中文输入 / 启动 App / 回桌面）
编排成可复用的复合操作流程（"换台"、"打开设置"、"回桌面"），供 Agent 一次调用。

设计约束（严格遵守提单要求）
--------------------------------
* 不修改任何现有模块：``tvpilot/tools.py`` / ``channels.py`` / ``state.py`` / ``ops_log.py``
  仅被**只读复用**（默认工具表按函数名绑定它们的既有函数）。
* 零新依赖：只用标准库（dataclasses / os / re / threading / time / typing）。
* 不实现真实 LLM 调用：步骤是静态声明（dict / :class:`Step`），不做任何动态生成。
* 既有工具返回契约 ``(data, err_code, err_message)`` 三元组原样沿用；
  二元组返回值**不**按 ``(data, err)`` 解释（既有模块里存在 ``(ok, msg)`` 二元组，
  如 ``zap_via_adb_digits``），以免把成功误判为失败。

失败语义
--------
* 步骤级默认 **fail-closed**：``on_error='stop'``（默认）时步骤失败即中止，剩余步骤标记
  ``status='skipped'`` / ``skip_reason='aborted'``；
* 单步可显式 **fail-open**：``on_error='continue'``，失败后继续跑后续步骤（降级/补偿链）；
* 参数缺失 / 占位符无法解析 / 未注册蓝图 / 未注册工具 全部 **fail-closed**：
  返回 ``ok=False`` 的结果对象而不是抛异常；
* 被条件跳过（``if`` 不满足）的步骤不计为失败：``ok=True``、``skipped=True``、
  ``status='skipped'``；条件链按 ``status == 'ok'`` 严格判断，跳过会沿链传播。
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

#: 单步 sleep 上限（秒），防止蓝图写出失控等待
MAX_STEP_SLEEP: float = float(os.environ.get('TVPILOT_BLUEPRINT_MAX_SLEEP', '60'))

#: 内置 open_settings 蓝图的目标包名（沿用 §1 的环境变量风格；真机包名可覆盖）
SETTINGS_PKG: str = os.environ.get('TVPILOT_SETTINGS_PKG', 'com.xiaomi.mitv.settings')

#: 内置 zap_to_cctv1 蓝图的目标频道名（与 channels.NAMES / 台号表口径一致）
ZAP_TO_CCTV1_CHANNEL: str = os.environ.get('TVPILOT_CCTV1_NAME', '中央一台')

#: 工具可调用对象类型
ToolFn = Callable[..., Any]

CONDITION_ALWAYS = 'always'
CONDITION_PREV_OK = 'prev_ok'
CONDITION_PREV_FAILED = 'prev_failed'
CONDITION_NEVER = 'never'
CONDITIONS: Tuple[str, ...] = (
    CONDITION_ALWAYS, CONDITION_PREV_OK, CONDITION_PREV_FAILED, CONDITION_NEVER,
)

ON_ERROR_STOP = 'stop'
ON_ERROR_CONTINUE = 'continue'
ON_ERROR_CHOICES: Tuple[str, ...] = (ON_ERROR_STOP, ON_ERROR_CONTINUE)

_CONDITION_ALIASES: Dict[str, str] = {
    'previous_ok': CONDITION_PREV_OK,
    'previous_failed': CONDITION_PREV_FAILED,
}
_ON_ERROR_ALIASES: Dict[str, str] = {'abort': ON_ERROR_STOP, 'ignore': ON_ERROR_CONTINUE}

SKIP_ABORTED = 'aborted'
SKIP_CONDITION = 'condition'

_STATUS_OK = 'ok'
_STATUS_FAILED = 'failed'
STATUS_SKIPPED = 'skipped'

_FULL_PLACEHOLDER_RE = re.compile(r'^\$\{([^{}]+)\}$')
_PLACEHOLDER_RE = re.compile(r'\$\{([^{}]+)\}')

_STEP_SPEC_KEYS = frozenset({
    'tool', 'args', 'name', 'sleep', 'delay', 'sleep_after',
    'if', 'condition', 'on_error', 'save_as', 'description',
})
_CONTEXT_KEYS = frozenset({'params', 'tools', 'vars', 'audit', 'sleep_fn', 'runner'})


class BlueprintSpecError(ValueError):
    """蓝图/步骤定义非法（定义期 fail-fast）。"""


class UnresolvedParameter(LookupError):
    """参数占位符无法解析（缺参数或缺变量）。"""


class _Required:
    """params 映射里的"必填"标记（也接受 ``...``/Ellipsis）。"""

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return '<required>'


#: 在 ``params={'name': REQUIRED}`` 中表示必填参数
REQUIRED: Any = _Required()


# --------------------------------------------------------------------------- #
# 结果对象（属性 + 字典两种访问）
# --------------------------------------------------------------------------- #

class _ResultMapping:
    """结果对象的只读字典视图：``result['ok']`` / ``result.get('error')`` / ``'ok' in result``。"""

    def to_dict(self) -> Dict[str, Any]:
        """返回纯 dict 快照（可直接 JSON 序列化）。"""
        raise NotImplementedError

    def __getitem__(self, key: str) -> Any:
        data = self.to_dict()
        if key not in data:
            raise KeyError(key)
        return data[key]

    def get(self, key: str, default: Any = None) -> Any:
        """按字典语义取值，缺省返回 default。"""
        return self.to_dict().get(key, default)

    def keys(self) -> List[str]:
        """返回字段名列表。"""
        return list(self.to_dict().keys())

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()


@dataclass
class StepResult(_ResultMapping):
    """单步执行结果。

    契约字段（§3.1）：``ok`` / ``step`` / ``duration_ms`` / ``error``；
    补充字段：``output``（工具返回数据）、``skipped``、``skip_reason``、``error_code``、
    ``delay_ms``（该步骤 sleep + sleep_after 总时长）、``note``、``status``。

    ``ok`` 的语义是"本步骤未产生错误"：条件跳过（skipped）同样 ok=True，
    是否真的执行看 ``skipped`` / ``status``。
    """

    step: str
    ok: bool
    duration_ms: int = 0
    error: Optional[str] = None
    output: Any = None
    skipped: bool = False
    skip_reason: Optional[str] = None
    error_code: Optional[str] = None
    delay_ms: int = 0
    note: Optional[str] = None
    status: str = ''

    def __post_init__(self) -> None:
        """根据 ok/skipped 推导 status（未显式指定时）。"""
        if not self.status:
            if self.skipped:
                self.status = STATUS_SKIPPED
            elif self.ok:
                self.status = _STATUS_OK
            else:
                self.status = _STATUS_FAILED

    @classmethod
    def success(cls, step: str, output: Any = None,
                duration_ms: int = 0, delay_ms: int = 0) -> 'StepResult':
        """构造成功结果。"""
        return cls(step=step, ok=True, duration_ms=int(duration_ms),
                   output=output, delay_ms=int(delay_ms), status=_STATUS_OK)

    @classmethod
    def failure(cls, step: str, error: str, error_code: Optional[str] = None,
                output: Any = None, duration_ms: int = 0, delay_ms: int = 0) -> 'StepResult':
        """构造失败结果。"""
        return cls(step=step, ok=False, duration_ms=int(duration_ms), error=error,
                   output=output, error_code=error_code, delay_ms=int(delay_ms),
                   status=_STATUS_FAILED)

    @classmethod
    def skip(cls, step: str, reason: str, note: Optional[str] = None) -> 'StepResult':
        """构造跳过结果（条件不满足或前序失败中止）。"""
        return cls(step=step, ok=True, duration_ms=0, skipped=True,
                   skip_reason=reason, note=note, status=STATUS_SKIPPED)

    def to_dict(self) -> Dict[str, Any]:
        """返回纯 dict 快照。"""
        return {
            'step': self.step,
            'ok': self.ok,
            'duration_ms': self.duration_ms,
            'error': self.error,
            'output': self.output,
            'skipped': self.skipped,
            'skip_reason': self.skip_reason,
            'error_code': self.error_code,
            'delay_ms': self.delay_ms,
            'note': self.note,
            'status': self.status,
        }


@dataclass
class BlueprintResult(_ResultMapping):
    """一次蓝图执行的汇总结果。"""

    name: str
    ok: bool
    steps: List[StepResult] = field(default_factory=list)
    error: Optional[str] = None
    failed_step: Optional[str] = None
    duration_ms: int = 0
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """返回纯 dict 快照（steps 已展开为 dict）。"""
        return {
            'name': self.name,
            'ok': self.ok,
            'error': self.error,
            'failed_step': self.failed_step,
            'duration_ms': self.duration_ms,
            'params': dict(self.params),
            'steps': [s.to_dict() for s in self.steps],
        }


# --------------------------------------------------------------------------- #
# 工具返回值归一化 / 参数占位符
# --------------------------------------------------------------------------- #

def normalize_tool_result(raw: Any) -> Tuple[Any, Optional[str], Optional[str]]:
    """把工具返回值归一化为 ``(data, err_code, err_message)``。

    只有**三元组**才按 §1 既有契约解释；dict / str / bool / None / 二元组
    一律视为成功数据，避免把 ``zap_via_adb_digits`` 这类 ``(ok, msg)`` 误判为失败。
    自定义工具要表达失败，请返回 ``(None, code, message)`` 或直接抛异常。
    """
    if isinstance(raw, (tuple, list)) and len(raw) == 3:
        return raw[0], raw[1], raw[2]
    return raw, None, None


def _lookup(scope: Mapping[str, Any], path: str) -> Any:
    """按点路径从 scope 取值；取不到抛 :class:`UnresolvedParameter`。"""
    cur: Any = scope
    for part in path.split('.'):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        else:
            raise UnresolvedParameter(f'缺少参数/变量: "{path}"')
    return cur


def substitute(value: Any, scope: Mapping[str, Any]) -> Any:
    """递归替换 ``${name}`` / ``${var.key}`` 占位符。

    整串匹配（``'${count}'``）返回原对象类型（int/None/...）；内嵌于字符串时做字符串化；
    映射与序列递归处理。解析失败抛 :class:`UnresolvedParameter`（fail-closed）。
    """
    if isinstance(value, str):
        m = _FULL_PLACEHOLDER_RE.match(value)
        if m:
            return _lookup(scope, m.group(1))
        return _PLACEHOLDER_RE.sub(lambda mm: str(_lookup(scope, mm.group(1))), value)
    if isinstance(value, Mapping):
        return {k: substitute(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, scope) for v in value]
    if isinstance(value, tuple):
        return tuple(substitute(v, scope) for v in value)
    return value


# --------------------------------------------------------------------------- #
# Step / BlueprintContext / Blueprint
# --------------------------------------------------------------------------- #

@dataclass
class Step:
    """蓝图中的单个步骤。

    :param tool: 逻辑工具名（查上下文工具表）或直接传入可调用对象；纯等待步骤可为 None
    :param args: 传给工具的关键字参数，支持 ``${...}`` 占位符
    :param name: 步骤名（缺省 step_N），用于失败上报 ``failed_step``
    :param sleep: 执行前等待秒数（0 <= sleep <= MAX_STEP_SLEEP）
    :param sleep_after: 执行后等待秒数
    :param condition: ``always`` / ``prev_ok`` / ``prev_failed`` / ``never``
    :param on_error: ``stop``（默认 fail-closed）或 ``continue``（fail-open）
    :param save_as: 把工具输出存入上下文变量，后续步骤可用 ``${var.key}`` 引用
    """

    tool: Optional[Union[str, ToolFn]] = None
    args: Mapping[str, Any] = field(default_factory=dict)
    name: str = ''
    sleep: float = 0.0
    sleep_after: float = 0.0
    condition: str = CONDITION_ALWAYS
    on_error: str = ON_ERROR_STOP
    save_as: Optional[str] = None
    description: str = ''

    def __post_init__(self) -> None:
        """把 args 固化为 dict。"""
        self.args = dict(self.args or {})

    @classmethod
    def from_spec(cls, spec: Union['Step', Mapping[str, Any]]) -> 'Step':
        """从 dict 规格构造 Step，未知字段/非法取值抛 :class:`BlueprintSpecError`。"""
        if isinstance(spec, Step):
            return spec
        if not isinstance(spec, Mapping):
            raise BlueprintSpecError(f'步骤必须是 dict 或 Step，收到: {type(spec).__name__}')
        unknown = set(spec) - _STEP_SPEC_KEYS
        if unknown:
            raise BlueprintSpecError(f'步骤包含未知字段: {sorted(unknown)}')

        tool = spec.get('tool')
        if tool is not None and not callable(tool):
            if not isinstance(tool, str) or not tool.strip():
                raise BlueprintSpecError(f'非法 tool: {tool!r}')
            tool = tool.strip()
        args = spec.get('args') or {}
        if not isinstance(args, Mapping):
            raise BlueprintSpecError(f'步骤 args 必须是映射: {type(args).__name__}')

        sleep = spec.get('sleep', spec.get('delay', 0.0)) or 0.0
        sleep_after = spec.get('sleep_after', 0.0) or 0.0
        for label, val in (('sleep', sleep), ('sleep_after', sleep_after)):
            try:
                val = float(val)
            except (TypeError, ValueError):
                raise BlueprintSpecError(f'{label} 必须是数字: {spec.get(label)!r}')
            if not (0.0 <= val <= MAX_STEP_SLEEP):
                raise BlueprintSpecError(f'{label} 必须在 0~{MAX_STEP_SLEEP} 秒之间: {val}')
            if label == 'sleep':
                sleep = val
            else:
                sleep_after = val

        if tool is None and sleep <= 0.0:
            raise BlueprintSpecError('步骤必须指定 tool，或给出 sleep 作为纯等待步骤')

        cond = spec.get('if', spec.get('condition'))
        condition = CONDITION_ALWAYS if cond is None else _CONDITION_ALIASES.get(str(cond), str(cond))
        if condition not in CONDITIONS:
            raise BlueprintSpecError(f'非法 if 条件: {cond!r}（可选 {CONDITIONS}）')

        on_error = spec.get('on_error')
        on_error = ON_ERROR_STOP if on_error is None else _ON_ERROR_ALIASES.get(str(on_error), str(on_error))
        if on_error not in ON_ERROR_CHOICES:
            raise BlueprintSpecError(f'非法 on_error: {spec.get("on_error")!r}（可选 {ON_ERROR_CHOICES}）')

        save_as = spec.get('save_as')
        if save_as is not None and (not isinstance(save_as, str) or not save_as.strip()):
            raise BlueprintSpecError(f'非法 save_as: {save_as!r}')

        return cls(tool=tool, args=dict(args), name=str(spec.get('name') or ''),
                   sleep=sleep, sleep_after=sleep_after, condition=condition,
                   on_error=on_error, save_as=save_as,
                   description=str(spec.get('description') or ''))

    def condition_met(self, prev_status: Optional[str]) -> bool:
        """判断本步骤的 ``if`` 条件是否满足。

        ``prev_ok`` / ``prev_failed`` 严格看上一步 ``status``；首步（prev_status=None）
        视为无前序约束，``prev_ok`` 判定为满足（真空真）。
        """
        if self.condition == CONDITION_ALWAYS:
            return True
        if self.condition == CONDITION_NEVER:
            return False
        if self.condition == CONDITION_PREV_OK:
            return prev_status is None or prev_status == _STATUS_OK
        return self.condition == CONDITION_PREV_FAILED and prev_status == _STATUS_FAILED


@dataclass
class BlueprintContext:
    """蓝图执行上下文。

    :param params: 运行参数（占位符解析的来源）
    :param tools: 逻辑工具名 -> 可调用对象；None 表示用全局默认工具表
    :param vars: 步骤输出变量（``save_as`` 写入），可用 ``${var.key}`` 引用
    :param audit: 是否把每步写入 ops.jsonl（默认 False，避免测试产生文件副作用）
    :param sleep_fn: 等待实现（可注入假 sleep 做快测）
    """

    params: Dict[str, Any] = field(default_factory=dict)
    tools: Optional[Dict[str, ToolFn]] = None
    vars: Dict[str, Any] = field(default_factory=dict)
    audit: bool = False
    sleep_fn: Callable[[float], None] = time.sleep
    blueprint: Optional['Blueprint'] = None
    runner: Optional['BlueprintRunner'] = None

    def get_tool(self, name: str) -> Optional[ToolFn]:
        """按逻辑名取工具；未注册返回 None。"""
        table = self.tools if self.tools is not None else default_tools()
        return table.get(name)


def coerce_context(context: Optional[Union[BlueprintContext, Mapping[str, Any]]]) -> BlueprintContext:
    """把 dict / None 归一化为 :class:`BlueprintContext`。

    含 ``params`` / ``tools`` / ``vars`` / ``audit`` / ``sleep_fn`` / ``runner`` 任一键的 dict
    视为结构化上下文；否则整个 dict 视为 params（便捷写法）。
    """
    if context is None:
        return BlueprintContext()
    if isinstance(context, BlueprintContext):
        return context
    if isinstance(context, Mapping):
        if _CONTEXT_KEYS & set(context):
            return BlueprintContext(
                params=dict(context.get('params') or {}),
                tools=dict(context['tools']) if context.get('tools') is not None else None,
                vars=dict(context.get('vars') or {}),
                audit=bool(context.get('audit', False)),
                sleep_fn=context.get('sleep_fn') or time.sleep,
                runner=context.get('runner'),
            )
        return BlueprintContext(params=dict(context))
    raise BlueprintSpecError(f'context 必须是 BlueprintContext 或 dict: {type(context).__name__}')


def _normalize_params(params: Union[Sequence[str], Mapping[str, Any], None]) -> Dict[str, Any]:
    """把 params 声明归一化为 {名字: 默认值}（REQUIRED 表示必填）。"""
    if params is None:
        return {}
    if isinstance(params, Mapping):
        out: Dict[str, Any] = {}
        for key, val in params.items():
            out[str(key)] = REQUIRED if (val is REQUIRED or val is Ellipsis) else val
        return out
    if isinstance(params, (list, tuple, set, frozenset)):
        return {str(k): REQUIRED for k in params}
    raise BlueprintSpecError(f'params 声明必须是序列或映射: {type(params).__name__}')


@dataclass
class Blueprint:
    """一个预定义操作流程。

    :param name: 蓝图名（Runner 的注册键，如 ``zap_to_cctv1``）
    :param steps: 步骤列表（dict 规格或 :class:`Step`）
    :param description: 供 Agent 选择蓝图时阅读的说明
    :param params: 参数声明——名字序列表示必填；映射表示 ``{名字: 默认值}``，
                   值为 ``REQUIRED``/``...`` 仍表示必填
    """

    name: str
    steps: Sequence[Union[Step, Mapping[str, Any]]]
    description: str = ''
    params: Union[Sequence[str], Mapping[str, Any]] = ()

    def __post_init__(self) -> None:
        """构造期校验并固化步骤与参数声明（fail-fast）。"""
        if not isinstance(self.name, str) or not self.name.strip():
            raise BlueprintSpecError(f'蓝图名非法: {self.name!r}')
        self.name = self.name.strip()
        raw_steps = list(self.steps or [])
        if not raw_steps:
            raise BlueprintSpecError(f'蓝图 "{self.name}" 至少需要 1 个步骤')
        steps: List[Step] = []
        seen: Dict[str, int] = {}
        for i, spec in enumerate(raw_steps):
            step = Step.from_spec(spec)
            if not step.name:
                step.name = f'step_{i + 1}'
            if step.name in seen:  # 名字重复时自动去重，保证 failed_step 可定位
                step.name = f'{step.name}_{i + 1}'
            seen[step.name] = i
            steps.append(step)
        self.steps = steps
        self._params: Dict[str, Any] = _normalize_params(self.params)

    # -- 参数 -------------------------------------------------------------- #

    def resolve_params(self, given: Optional[Mapping[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
        """合并运行参数与默认值；返回 ``(resolved, missing_required)``。"""
        resolved: Dict[str, Any] = dict(given or {})
        missing: List[str] = []
        for key, default in self._params.items():
            if key in resolved:
                continue
            if default is REQUIRED:
                missing.append(key)
            else:
                resolved[key] = default
        return resolved, missing

    def describe(self) -> Dict[str, Any]:
        """返回蓝图元数据（名称/说明/参数/步骤），供 Agent 或调试查看。"""
        return {
            'name': self.name,
            'description': self.description,
            'params': {k: ('<required>' if v is REQUIRED else v) for k, v in self._params.items()},
            'steps': [
                {
                    'name': s.name,
                    'tool': s.tool if isinstance(s.tool, str) else '<callable>',
                    'args': dict(s.args),
                    'sleep': s.sleep,
                    'sleep_after': s.sleep_after,
                    'if': s.condition,
                    'on_error': s.on_error,
                }
                for s in self.steps
            ],
        }

    # -- 执行 -------------------------------------------------------------- #

    def run(self, context: Optional[Union[BlueprintContext, Mapping[str, Any]]] = None) -> BlueprintResult:
        """执行整个流程，返回 :class:`BlueprintResult`（执行期不抛异常）。

        context 可以是 :class:`BlueprintContext`、结构化 dict（``{'params':..., 'tools':...}``），
        或扁平参数 dict。
        """
        ctx = coerce_context(context)
        ctx.blueprint = self
        started = time.monotonic()
        resolved, missing = self.resolve_params(ctx.params)
        if missing:
            return BlueprintResult(
                name=self.name, ok=False,
                error=f'缺少必需参数: {", ".join(missing)}',
                failed_step=None,
                duration_ms=_elapsed_ms(started),
                params=dict(ctx.params or {}),
            )

        scope: Dict[str, Any] = dict(ctx.vars or {})
        scope.update(resolved)

        results: List[StepResult] = []
        prev_status: Optional[str] = None
        failed_step: Optional[str] = None
        error: Optional[str] = None
        aborted = False

        for step in self.steps:  # type: ignore[assignment]
            if aborted:
                results.append(StepResult.skip(
                    step.name, SKIP_ABORTED,
                    note=f'前序步骤 "{failed_step}" 失败，已中止剩余步骤'))
                prev_status = STATUS_SKIPPED
                continue

            if not step.condition_met(prev_status):
                results.append(StepResult.skip(
                    step.name, SKIP_CONDITION,
                    note=f'条件 "{step.condition}" 未满足（前一步状态: {prev_status or "无"}）'))
                prev_status = STATUS_SKIPPED
                continue

            pre_ms = _sleep_ms(ctx, step.sleep) if step.sleep > 0 else 0

            if step.tool is None:  # 纯等待步骤
                result = StepResult.success(step.name, output={'waited_s': step.sleep},
                                            duration_ms=pre_ms, delay_ms=0)
            else:
                result = self._exec_step(step, scope, ctx)
                post_ms = _sleep_ms(ctx, step.sleep_after) if step.sleep_after > 0 else 0
                result.delay_ms = pre_ms + post_ms

            if ctx.audit:
                _audit_log(f'blueprint:{self.name}.{step.name}', dict(step.args),
                           result.ok, result.error, result.duration_ms)

            results.append(result)
            if result.status == _STATUS_OK:
                if step.save_as:
                    scope[step.save_as] = result.output
                    ctx.vars[step.save_as] = result.output
                prev_status = _STATUS_OK
            elif result.status == _STATUS_FAILED:
                prev_status = _STATUS_FAILED
                if failed_step is None:
                    failed_step = step.name
                    error = f'步骤 "{step.name}" 失败: {result.error}'
                if step.on_error == ON_ERROR_STOP:
                    aborted = True
            else:
                prev_status = STATUS_SKIPPED

        return BlueprintResult(
            name=self.name,
            ok=failed_step is None,
            steps=results,
            error=error,
            failed_step=failed_step,
            duration_ms=_elapsed_ms(started),
            params=dict(resolved),
        )

    def _exec_step(self, step: Step, scope: Mapping[str, Any], ctx: BlueprintContext) -> StepResult:
        """执行单个工具步骤：参数解析 -> 工具调用 -> 返回归一化结果。"""
        t0 = time.monotonic()
        try:
            args = substitute(dict(step.args), scope)
        except UnresolvedParameter as exc:
            return StepResult.failure(step.name, error=f'参数解析失败：{exc}',
                                     error_code='param_error',
                                     duration_ms=_elapsed_ms(t0))

        fn: Optional[ToolFn]
        if callable(step.tool):
            fn = step.tool
        else:
            fn = ctx.get_tool(str(step.tool))
        if fn is None:
            return StepResult.failure(step.name, error=f'未注册的工具: {step.tool}',
                                     error_code='unknown_tool',
                                     duration_ms=_elapsed_ms(t0))

        try:
            raw = fn(**args)
        except Exception as exc:  # noqa: BLE001 - 工具异常一律收敛为步骤失败
            return StepResult.failure(step.name, error=f'{type(exc).__name__}: {exc}',
                                     error_code='tool_exception',
                                     duration_ms=_elapsed_ms(t0))

        data, err_code, err_msg = normalize_tool_result(raw)
        duration = _elapsed_ms(t0)
        if err_code is not None or err_msg is not None:
            return StepResult.failure(step.name,
                                     error=err_msg or f'工具返回错误码: {err_code}',
                                     error_code=err_code or 'tool_error',
                                     output=data, duration_ms=duration)
        return StepResult.success(step.name, output=data, duration_ms=duration)


# --------------------------------------------------------------------------- #
# 内置工具表（只读绑定 §1 既有函数）
# --------------------------------------------------------------------------- #

#: 逻辑工具名 -> 既有模块中的函数名（只纳入符合三元组契约的工具）
DEFAULT_TOOL_MAP: Dict[str, str] = {
    'foreground': 'tool_foreground',
    'keyevent': 'tool_keyevent',
    'tap': 'tool_tap',
    'swipe': 'tool_swipe',
    'wait': 'tool_wait',
    'input_text': 'tool_input_text',
    'go_home': 'tool_combo_go_home',
    'launch_app': 'tool_launch_app',
    'zap': 'tool_zap',
    'ensure_mytv_foreground': 'ensure_mytv_foreground',
    'ui_state': 'tool_ui_state',
    'play_state': 'tool_play_state',
    'system_state': 'tool_system_state',
    'screen_state': 'get_screen_state',
    'network_state': 'get_network_state',
    'foreground_pkg': 'get_foreground_pkg',
    'mytv_alive': 'mytv_alive',
}

_DEFAULT_TOOLS_CACHE: Optional[Dict[str, ToolFn]] = None


def default_tools() -> Dict[str, ToolFn]:
    """返回默认工具表（惰性加载 + 缓存）。

    通过**函数名**只读绑定 ``tvpilot.tools`` / ``tvpilot.state`` 的既有函数；
    任一模块不可用只降级打日志，不抛异常。
    """
    global _DEFAULT_TOOLS_CACHE
    if _DEFAULT_TOOLS_CACHE is not None:
        return _DEFAULT_TOOLS_CACHE
    table: Dict[str, ToolFn] = {}
    sources: List[Any] = []
    try:
        from tvpilot import tools as _tools_mod
        sources.append(_tools_mod)
    except Exception as exc:  # pragma: no cover - 环境异常
        print(f'[blueprint] 默认工具表: tvpilot.tools 不可用: {exc}', flush=True)
    try:
        from tvpilot import state as _state_mod
        sources.append(_state_mod)
    except Exception as exc:  # pragma: no cover - 环境异常
        print(f'[blueprint] 默认工具表: tvpilot.state 不可用: {exc}', flush=True)

    for logic_name, attr in DEFAULT_TOOL_MAP.items():
        for src in sources:
            fn = getattr(src, attr, None)
            if callable(fn):
                table[logic_name] = fn
                break
    _DEFAULT_TOOLS_CACHE = table
    return table


def register_tool(name: str, fn: ToolFn) -> None:
    """向全局默认工具表注册/覆盖一个工具（供 Agent 扩展蓝图可用动作）。"""
    if not isinstance(name, str) or not name.strip():
        raise BlueprintSpecError(f'非法工具名: {name!r}')
    if not callable(fn):
        raise BlueprintSpecError(f'工具必须可调用: {name}')
    default_tools()[name.strip()] = fn


def _audit_log(tool: str, params: Mapping[str, Any], ok: bool,
               error: Optional[str], cost_ms: int) -> None:
    """把一次步骤写入 ops.jsonl（懒加载 ops_log，失败只打日志）。"""
    try:
        from tvpilot.ops_log import log_operation
        log_operation(tool=tool, params=dict(params), ok=ok, error=error, cost_ms=cost_ms)
    except Exception as exc:  # pragma: no cover - 审计失败不影响执行
        print(f'[blueprint] audit log failed: {exc}', flush=True)


def _elapsed_ms(started: float) -> int:
    """把 monotonic 起点换算成整数毫秒耗时。"""
    return int(round((time.monotonic() - started) * 1000))


def _sleep_ms(ctx: BlueprintContext, seconds: float) -> int:
    """执行等待并返回实际等待毫秒数（走 ctx.sleep_fn，便于测试注入）。"""
    t0 = time.monotonic()
    ctx.sleep_fn(seconds)
    return _elapsed_ms(t0)


# --------------------------------------------------------------------------- #
# 内置蓝图（换台 / 打开设置 / 回桌面 / 参数化）
# --------------------------------------------------------------------------- #

def builtin_go_home() -> Blueprint:
    """回桌面：KEYCODE_HOME 组合动作 + 短暂稳定等待。"""
    return Blueprint(
        name='go_home',
        description='回到电视桌面（KEYCODE_HOME + 前台验证）',
        steps=[
            {'name': 'home', 'tool': 'go_home'},
            {'name': 'settle', 'sleep': 0.3, 'if': 'prev_ok'},
        ],
    )


def builtin_open_settings() -> Blueprint:
    """打开设置：先尝试回桌面，再拉起系统设置 App（首跳失败不阻断，fail-open）。"""
    return Blueprint(
        name='open_settings',
        description='打开系统设置（先回桌面，再启动设置 App）',
        steps=[
            {'name': 'to_home', 'tool': 'go_home', 'on_error': 'continue'},
            {'name': 'launch_settings', 'tool': 'launch_app', 'args': {'package': SETTINGS_PKG}},
            {'name': 'settle', 'sleep': 0.5, 'if': 'prev_ok'},
        ],
    )


def builtin_open_app() -> Blueprint:
    """打开任意 App：参数化（package 必填，activity 可选）。"""
    return Blueprint(
        name='open_app',
        description='启动指定 App（参数：package 必填，activity 可选）',
        params={'package': REQUIRED, 'activity': None},
        steps=[
            {'name': 'launch', 'tool': 'launch_app',
             'args': {'package': '${package}', 'activity': '${activity}'},
             'save_as': 'launch'},
            {'name': 'settle', 'sleep': 0.5, 'if': 'prev_ok'},
        ],
    )


def builtin_zap_to() -> Blueprint:
    """换台到指定频道：参数化 ``zap_to(channel_name)``。"""
    return Blueprint(
        name='zap_to',
        description='换台到指定频道（参数：channel_name）',
        params=['channel_name'],
        steps=[
            {'name': 'zap', 'tool': 'zap', 'args': {'channel': '${channel_name}'}, 'save_as': 'zap'},
        ],
    )


def builtin_zap_to_cctv1() -> Blueprint:
    """换台到 CCTV-1（中央一台）。"""
    return Blueprint(
        name='zap_to_cctv1',
        description='换台到 CCTV-1（中央一台）',
        steps=[
            {'name': 'zap', 'tool': 'zap',
             'args': {'channel': ZAP_TO_CCTV1_CHANNEL}, 'save_as': 'zap'},
            {'name': 'settle', 'sleep': 0.5, 'if': 'prev_ok'},
        ],
    )


def builtin_iptv_zap_safe() -> Blueprint:
    """IPTV 安全换台：先连按 BACK 退出导航层，再换台。

    解决 IPTV 应用在导航/菜单界面时直接输台号无效的问题。
    参数：channel_name（频道名）
    v2：BACK 次数 3→2，等待 1s→0.5s，减少耗时。
    """
    return Blueprint(
        name='iptv_zap_safe',
        description='IPTV 安全换台（先退出导航层再换台，参数：channel_name）',
        params=['channel_name'],
        steps=[
            # 连按 2 次 BACK 退出导航层/菜单（fail-open，按不到也继续）
            {'name': 'back_1', 'tool': 'keyevent', 'args': {'key': 'KEYCODE_BACK'}, 'on_error': 'continue'},
            {'name': 'back_2', 'tool': 'keyevent', 'args': {'key': 'KEYCODE_BACK'}, 'on_error': 'continue'},
            {'name': 'settle', 'sleep': 0.5},
            # 换台（tool_zap 内部已包含 ensure_iptv_foreground + 数字键 + 验证）
            {'name': 'zap', 'tool': 'zap', 'args': {'channel': '${channel_name}'}, 'save_as': 'zap'},
        ],
    )


#: 内置蓝图工厂清单（顺序即注册顺序）
BUILTIN_FACTORIES: Tuple[Callable[[], Blueprint], ...] = (
    builtin_go_home, builtin_open_settings, builtin_open_app,
    builtin_zap_to, builtin_zap_to_cctv1, builtin_iptv_zap_safe,
)


def builtin_blueprints() -> List[Blueprint]:
    """构造一份全新的内置蓝图列表（每次调用都是新对象，可自由改名/改步骤后注册）。"""
    return [factory() for factory in BUILTIN_FACTORIES]


# --------------------------------------------------------------------------- #
# BlueprintRunner
# --------------------------------------------------------------------------- #

class BlueprintRunner:
    """蓝图注册与执行器。

    契约方法：``register(bp)`` / ``run(name, params)`` / ``list()``。

    :param tools: 自定义工具表（逻辑名 -> 可调用）；None 表示用全局默认工具表
    :param audit: 是否把每步写入 ops.jsonl（默认 False）
    :param sleep: 等待实现（默认 time.sleep，测试可注入假 sleep）
    """

    def __init__(self, tools: Optional[Mapping[str, ToolFn]] = None,
                 *, audit: bool = False,
                 sleep: Optional[Callable[[float], None]] = None) -> None:
        self._tools: Optional[Dict[str, ToolFn]] = dict(tools) if tools is not None else None
        self._tool_overrides: Dict[str, ToolFn] = {}
        self._registry: Dict[str, Blueprint] = {}
        self._audit = bool(audit)
        self._sleep: Callable[[float], None] = sleep or time.sleep
        self._run_lock = threading.Lock()

    # -- 注册 -------------------------------------------------------------- #

    def register(self, blueprint: Blueprint, *, replace: bool = False) -> Blueprint:
        """注册蓝图；同名默认抛 :class:`BlueprintSpecError`，``replace=True`` 才覆盖。"""
        if not isinstance(blueprint, Blueprint):
            raise BlueprintSpecError(f'只能注册 Blueprint 实例: {type(blueprint).__name__}')
        if blueprint.name in self._registry and not replace:
            raise BlueprintSpecError(f'蓝图名重复: "{blueprint.name}"（如需覆盖请 replace=True）')
        self._registry[blueprint.name] = blueprint
        return blueprint

    def register_builtins(self, *, replace: bool = True) -> List[str]:
        """注册内置蓝图（go_home / open_settings / open_app / zap_to / zap_to_cctv1）。"""
        names: List[str] = []
        for bp in builtin_blueprints():
            self.register(bp, replace=replace)
            names.append(bp.name)
        return names

    def register_tool(self, name: str, fn: ToolFn) -> None:
        """在本 Runner 的工具表里注册/覆盖工具（优先级高于默认表）。"""
        if not isinstance(name, str) or not name.strip():
            raise BlueprintSpecError(f'非法工具名: {name!r}')
        if not callable(fn):
            raise BlueprintSpecError(f'工具必须可调用: {name}')
        self._tool_overrides[name.strip()] = fn

    def tool_table(self) -> Dict[str, ToolFn]:
        """返回本 Runner 生效的完整工具表快照。"""
        base = dict(self._tools) if self._tools is not None else default_tools()
        base.update(self._tool_overrides)
        return base

    # -- 查询 -------------------------------------------------------------- #

    def list(self) -> List[str]:
        """返回已注册蓝图名列表（注册顺序）。"""
        return list(self._registry.keys())

    def get(self, name: str) -> Optional[Blueprint]:
        """按名取蓝图；未注册返回 None。"""
        return self._registry.get(name)

    def has(self, name: str) -> bool:
        """蓝图是否已注册。"""
        return name in self._registry

    def summary(self) -> List[Dict[str, Any]]:
        """返回全部蓝图的元数据（name/description/params/steps）。"""
        return [bp.describe() for bp in self._registry.values()]

    # -- 执行 -------------------------------------------------------------- #

    def run(self, name: str, params: Optional[Mapping[str, Any]] = None, *,
            tools: Optional[Mapping[str, ToolFn]] = None,
            context: Optional[Union[BlueprintContext, Mapping[str, Any]]] = None) -> BlueprintResult:
        """执行指定蓝图。

        未知蓝图 / 缺参数 / 步骤失败一律返回 ``ok=False`` 的 :class:`BlueprintResult`
        （含 ``failed_step`` 与 ``error``），不抛异常；同 Runner 内的执行被锁串行化，
        避免多路并发操控同一台电视。
        """
        blueprint = self._registry.get(name)
        if blueprint is None:
            known = ', '.join(self._registry.keys()) or '无'
            return BlueprintResult(
                name=name, ok=False,
                error=f'未注册的蓝图: "{name}"（已注册: {known}）',
                failed_step=None, params=dict(params or {}))

        ctx = coerce_context(context)
        ctx.runner = self
        if params:
            ctx.params.update(dict(params))
        table = self.tool_table()
        if tools:
            table.update(dict(tools))
        ctx.tools = table
        ctx.audit = self._audit
        ctx.sleep_fn = self._sleep

        with self._run_lock:
            return blueprint.run(ctx)


__all__ = [
    'MAX_STEP_SLEEP', 'SETTINGS_PKG', 'ZAP_TO_CCTV1_CHANNEL',
    'ToolFn', 'REQUIRED', 'CONDITIONS', 'ON_ERROR_CHOICES',
    'SKIP_ABORTED', 'SKIP_CONDITION', 'STATUS_SKIPPED',
    'BlueprintSpecError', 'UnresolvedParameter',
    'StepResult', 'BlueprintResult', 'Step', 'BlueprintContext', 'Blueprint', 'BlueprintRunner',
    'coerce_context', 'substitute', 'normalize_tool_result',
    'DEFAULT_TOOL_MAP', 'default_tools', 'register_tool',
    'builtin_go_home', 'builtin_open_settings', 'builtin_open_app',
    'builtin_zap_to', 'builtin_zap_to_cctv1', 'builtin_blueprints', 'BUILTIN_FACTORIES',
]
