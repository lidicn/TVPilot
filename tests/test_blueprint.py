"""TVPilot 蓝图系统单元测试。

对应 §8 验收 2~6 与 §2.1 核心目标（换台 / 打开设置 / 囨桌面 复合操作）：
注册执行、失败上报、sleep 延迟、if 条件、参数化蓝图、StepResult 契约、内置蓝图。

说明：蓝图系统本体**不打桩**——每条测试都真的走
「参数解析 -> sleep -> 工具调用 -> 三元组归一化 -> 结果汇总」全链路；
只有"工具"是记录型桩（RecordTool），因此不需要真机、不产生 ADB 副作用。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tvpilot.blueprint import (  # noqa: E402
    SETTINGS_PKG, Blueprint, BlueprintContext, BlueprintRunner, BlueprintSpecError,
    Step, StepResult, builtin_go_home, builtin_open_settings,
    builtin_zap_to, builtin_zap_to_cctv1,
)


class RecordTool:
    """记录调用参数、返回固定结果的桩工具（走 §1 三元组返回契约）。"""

    def __init__(self, data: Any = None, err_code: Optional[str] = None,
                 err_msg: Optional[str] = None, exc: Optional[BaseException] = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._data: Any = {} if data is None else data
        self._err_code = err_code
        self._err_msg = err_msg
        self._exc = exc

    def __call__(self, **kwargs: Any) -> Tuple[Any, Optional[str], Optional[str]]:
        """记录一次调用并按配置返回三元组（或抛异常）。"""
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._data, self._err_code, self._err_msg


def _ctx(tools: Dict[str, Callable[..., Any]], params: Optional[Dict[str, Any]] = None,
         sleep: Optional[Callable[[float], None]] = None) -> Dict[str, Any]:
    """构造结构化执行上下文（sleep 可注入假实现保持测试飞快）。"""
    out: Dict[str, Any] = {'params': dict(params or {}), 'tools': tools}
    if sleep is not None:
        out['sleep_fn'] = sleep
    return out


# --------------------------------------------------------------------------- #
# 结果契约
# --------------------------------------------------------------------------- #

def test_step_result_contract_fields_and_status() -> None:
    """StepResult 同时支持属性/字典访问，契约四字段齐全且状态可判别。"""
    ok = StepResult.success('s1', output={'x': 1}, duration_ms=12)
    for key in ('ok', 'step', 'duration_ms', 'error'):
        assert key in ok.to_dict()
        assert ok[key] == ok.to_dict()[key]
    assert ok.ok is True and ok.step == 's1' and ok.duration_ms == 12 and ok.error is None
    assert ok.status == 'ok' and ok.skipped is False

    bad = StepResult.failure('s2', error='boom', error_code='tool_error')
    assert bad.ok is False and bad.error == 'boom' and bad.status == 'failed'
    assert bad['error_code'] == 'tool_error'

    skipped = StepResult.skip('s3', 'condition', note='条件未满足')
    assert skipped.ok is True and skipped.skipped is True and skipped.status == 'skipped'
    assert skipped.skip_reason == 'condition' and skipped.error is None


def test_blueprint_result_dict_access_and_to_dict() -> None:
    """BlueprintResult 的字典访问与 to_dict 一致，steps 可直接序列化。"""
    res = BlueprintResult if False else None  # noqa: F841 - 保持导入面清晰
    from tvpilot.blueprint import BlueprintResult
    r = BlueprintResult(name='demo', ok=False, error='步骤 "s1" 失败: 坏了',
                       failed_step='s1', duration_ms=8,
                       steps=[StepResult.failure('s1', '坏了')], params={'a': 1})
    assert r['ok'] is False and r['failed_step'] == 's1'
    assert r.get('missing', 'x') == 'x' and 'error' in r
    dump = r.to_dict()
    assert dump['steps'][0]['step'] == 's1' and dump['params'] == {'a': 1}
    assert dump['duration_ms'] == 8


# --------------------------------------------------------------------------- #
# §2.1 目标 1：执行链路 / §8 验收 2、5
# --------------------------------------------------------------------------- #

def test_blueprint_run_executes_steps_in_order_with_param_substitution() -> None:
    """多步骤按序执行，${param} 被替换，save_as 输出被收集进结果。"""
    calls: List[Tuple[str, Any]] = []

    def fake_zap(channel: str) -> Tuple[Any, None, None]:
        calls.append(('zap', channel))
        return {'channel': channel, 'channel_no': 21}, None, None

    def fake_keyevent(key: str) -> Tuple[Any, None, None]:
        calls.append(('keyevent', key))
        return {'key': key}, None, None

    bp = Blueprint(name='zap_then_key', description='换台后按确认键', params=['channel_name'], steps=[
        {'name': 'to_channel', 'tool': 'zap', 'args': {'channel': '${channel_name}'}, 'save_as': 'zap'},
        {'name': 'confirm', 'tool': 'keyevent', 'args': {'key': 'KEYCODE_ENTER'}},
    ])
    res = bp.run(_ctx({'zap': fake_zap, 'keyevent': fake_keyevent}, {'channel_name': '中央一台'}))

    assert res.ok is True and res.error is None and res.failed_step is None
    assert calls == [('zap', '中央一台'), ('keyevent', 'KEYCODE_ENTER')]
    assert [s.step for s in res.steps] == ['to_channel', 'confirm']
    assert all(s.status == 'ok' for s in res.steps)
    assert res.steps[0].output == {'channel': '中央一台', 'channel_no': 21}
    assert res.params == {'channel_name': '中央一台'}


def test_blueprint_run_accepts_flat_params_and_callable_tool() -> None:
    """便捷写法：扁平 dict 直接当 params，step 也可直接传可调用对象。"""
    seen: Dict[str, Any] = {}

    def local_tool(channel: str) -> Dict[str, Any]:
        seen['channel'] = channel
        return {'channel': channel}

    bp = Blueprint(name='flat', params=['name'],
                   steps=[{'tool': local_tool, 'args': {'channel': '${name}'}}])
    res = bp.run({'name': '湖南卫视'})

    assert res.ok is True
    assert seen['channel'] == '湖南卫视'
    assert res.steps[0].output == {'channel': '湖南卫视'}
    assert res.steps[0].error is None


def test_runner_register_and_run_zap_to_cctv1() -> None:
    """验收#2：BlueprintRunner 注册并执行 zap_to_cctv1，返回 ok=True 且真的调用了 zap。"""
    zap = RecordTool(data={'channel': '中央一台', 'channel_no': 21, 'method': 'adb_digits'})
    sleeps: List[float] = []
    runner = BlueprintRunner(tools={'zap': zap}, sleep=sleeps.append)

    assert runner.register(builtin_zap_to_cctv1()).name == 'zap_to_cctv1'
    assert 'zap_to_cctv1' in runner.list()

    res = runner.run('zap_to_cctv1', {})

    assert res.ok is True and res['ok'] is True
    assert zap.calls == [{'channel': '中央一台'}]
    assert res.steps[0].output['channel_no'] == 21
    assert res.steps[1].step == 'settle' and res.steps[1].status == 'ok'
    assert sleeps == [0.5]  # 内置蓝图的稳定等待确实被请求


def test_runner_register_tool_extends_tool_table() -> None:
    """Runner 级 register_tool 可以补充/覆盖工具表（不影响全局默认表）。"""
    zap = RecordTool(data={'ok': 1})
    runner = BlueprintRunner(tools={}, sleep=lambda s: None)
    runner.register_tool('zap', zap)
    runner.register(builtin_zap_to_cctv1(), replace=True)

    res = runner.run('zap_to_cctv1', {})

    assert res.ok is True
    assert zap.calls == [{'channel': '中央一台'}]
    assert 'zap' in runner.tool_table()


def test_runner_list_and_summary_follow_registration_order() -> None:
    """list() 按注册顺序返回名字；summary() 暴露参数与步骤元数据。"""
    runner = BlueprintRunner(tools={'zap': RecordTool()}, sleep=lambda s: None)
    runner.register(builtin_zap_to())
    runner.register(builtin_zap_to_cctv1())

    assert runner.list() == ['zap_to', 'zap_to_cctv1']
    assert runner.has('zap_to') and not runner.has('nope')
    info = runner.summary()
    assert info[0]['name'] == 'zap_to'
    assert info[0]['params'] == {'channel_name': '<required>'}
    assert info[0]['steps'][0]['args'] == {'channel': '${channel_name}'}
    assert info[1]['steps'][0]['tool'] == 'zap'


def test_runner_register_duplicate_raises_and_replace_overwrites() -> None:
    """同名注册 fail-fast 抛 BlueprintSpecError；replace=True 才覆盖。"""
    runner = BlueprintRunner(tools={'zap': RecordTool()}, sleep=lambda s: None)
    first = builtin_zap_to()
    runner.register(first)
    dup = builtin_zap_to()
    try:
        runner.register(dup)
    except BlueprintSpecError as exc:
        assert 'zap_to' in str(exc)
    else:
        raise AssertionError('同名注册应当抛出 BlueprintSpecError')
    assert runner.get('zap_to') is first

    runner.register(dup, replace=True)
    assert runner.get('zap_to') is dup


# --------------------------------------------------------------------------- #
# §8 验收 3：失败上报
# --------------------------------------------------------------------------- #

def test_failure_reports_failed_step_and_error() -> None:
    """验收#3：失败时 ok=False，且带上具体步骤名 + 错误信息 + 错误码。"""
    zap = RecordTool(err_code='ERR_MYTV_API_ERROR', err_msg='ADB 换台失败: 数字键输入超时')
    key = RecordTool(data={'key': 'KEYCODE_ENTER'})
    bp = Blueprint(name='zap_flow', steps=[
        {'name': 'to_channel', 'tool': 'zap', 'args': {'channel': '中央一台'}},
        {'name': 'confirm', 'tool': 'keyevent', 'args': {'key': 'KEYCODE_ENTER'}},
    ])
    res = bp.run(_ctx({'zap': zap, 'keyevent': key}))

    assert res.ok is False
    assert res.failed_step == 'to_channel'
    assert 'ADB 换台失败: 数字键输入超时' in res.error
    assert 'to_channel' in res.error
    assert res.steps[0].status == 'failed' and res.steps[0].error_code == 'ERR_MYTV_API_ERROR'
    assert res['failed_step'] == 'to_channel'


def test_tool_exception_becomes_step_failure() -> None:
    """工具抛异常被收敛为步骤失败（含异常类型与信息），不向上冒泡。"""
    boom = RecordTool(exc=RuntimeError('设备掉线'))
    bp = Blueprint(name='boom', steps=[{'name': 'act', 'tool': 'act', 'args': {}}])
    res = bp.run(_ctx({'act': boom}))

    assert res.ok is False and res.failed_step == 'act'
    assert res.steps[0].error_code == 'tool_exception'
    assert 'RuntimeError' in res.steps[0].error and '设备掉线' in res.steps[0].error


def test_tool_error_triple_marks_failed_with_code() -> None:
    """返回 (None, code, msg) 的工具按契约判失败，错误码原样透传。"""
    tool = RecordTool(err_code='ERR_INVALID_PARAMETER', err_msg='非法按键名: enter')
    bp = Blueprint(name='badkey', steps=[{'name': 'key', 'tool': 'keyevent', 'args': {'key': 'enter'}}])
    res = bp.run(_ctx({'keyevent': tool}))

    assert res.ok is False
    assert res.steps[0].error == '非法按键名: enter'
    assert res.steps[0].error_code == 'ERR_INVALID_PARAMETER'


def test_runner_run_unknown_blueprint_returns_ok_false() -> None:
    """未知蓝图 fail-closed：返回 ok=False 结果而不是抛 KeyError。"""
    runner = BlueprintRunner(tools={}, sleep=lambda s: None)
    runner.register(builtin_zap_to())

    res = runner.run('zap_to_cctv9', {'channel_name': 'x'})

    assert res.ok is False and res.failed_step is None
    assert 'zap_to_cctv9' in res.error and 'zap_to' in res.error
    assert res.steps == []


# --------------------------------------------------------------------------- #
# §8 验收 4：失败策略 / sleep / if 条件
# --------------------------------------------------------------------------- #

def test_default_stop_aborts_and_marks_remaining_skipped() -> None:
    """默认 on_error=stop（fail-closed）：失败即中止，剩余步骤标记 skipped/aborted。"""
    first = RecordTool(err_code='ERR_TV_UNREACHABLE', err_msg='电视离线')
    later = RecordTool(data={'ok': 1})
    bp = Blueprint(name='chain', steps=[
        {'name': 's1', 'tool': 'a', 'args': {}},
        {'name': 's2', 'tool': 'b', 'args': {}},
        {'name': 's3', 'tool': 'b', 'args': {}},
    ])
    res = bp.run(_ctx({'a': first, 'b': later}))

    assert res.ok is False and res.failed_step == 's1'
    assert [s.status for s in res.steps] == ['failed', 'skipped', 'skipped']
    assert res.steps[1].skip_reason == 'aborted' and 's1' in res.steps[1].note
    assert later.calls == []  # 后续工具确实没有被调用


def test_on_error_continue_runs_next_and_overall_ok_false() -> None:
    """on_error='continue' 是局部 fail-open：继续执行，但整体仍 ok=False。"""
    bad = RecordTool(err_code='ERR_X', err_msg='第一步坏了')
    good = RecordTool(data={'done': True})
    bp = Blueprint(name='degrade', steps=[
        {'name': 's1', 'tool': 'a', 'args': {}, 'on_error': 'continue'},
        {'name': 's2', 'tool': 'b', 'args': {}},
    ])
    res = bp.run(_ctx({'a': bad, 'b': good}))

    assert [s.status for s in res.steps] == ['failed', 'ok']
    assert len(good.calls) == 1
    assert res.ok is False and res.failed_step == 's1' and '第一步坏了' in res.error


def test_step_sleep_and_sleep_after_real_timing() -> None:
    """验收#4（sleep）：sleep/sleep_after 真实等待，计入 delay_ms 与总耗时。"""
    calls: List[str] = []

    def act(name: str) -> Tuple[Any, None, None]:
        calls.append(name)
        return {'name': name}, None, None

    bp = Blueprint(name='timing', steps=[
        Step(tool='act', args={'name': 'a'}, name='a', sleep_after=0.1),
        Step(tool='act', args={'name': 'b'}, name='b', sleep=0.12),
    ])
    t0 = time.monotonic()
    res = bp.run({'tools': {'act': act}})
    elapsed = time.monotonic() - t0

    assert res.ok is True and calls == ['a', 'b']
    assert res.steps[0].delay_ms >= 100   # sleep_after
    assert res.steps[1].delay_ms >= 100   # 前置 sleep
    assert elapsed >= 0.2 and res.duration_ms >= 200


def test_step_sleep_uses_injected_sleep_fn() -> None:
    """sleep 走上下文注入的 sleep_fn（Runner 传入），请求秒数精确。"""
    sleeps: List[float] = []
    zap = RecordTool(data={'channel_no': 21})
    runner = BlueprintRunner(tools={'zap': zap}, sleep=sleeps.append)
    runner.register(builtin_zap_to_cctv1())

    res = runner.run('zap_to_cctv1', {})

    assert res.ok is True
    assert sleeps == [0.5]
    assert res.steps[0].duration_ms < 50  # 工具耗时不含等待


def test_pure_wait_step_needs_no_tool() -> None:
    """纯等待步骤（只有 sleep，无 tool）合法，等待计入 duration_ms。"""
    bp = Blueprint(name='hold', steps=[{'name': 'hold', 'sleep': 0.05}])
    res = bp.run({})

    assert res.ok is True
    assert res.steps[0].status == 'ok' and res.steps[0].skipped is False
    assert res.steps[0].duration_ms >= 40 and res.steps[0].delay_ms == 0
    assert res.steps[0].output == {'waited_s': 0.05}


def test_if_prev_ok_skips_when_previous_failed() -> None:
    """验收#4（if）：前一步失败时 if='prev_ok' 的步骤被跳过，工具不被调用。"""
    bad = RecordTool(err_code='ERR_X', err_msg='坏了')
    guard = RecordTool(data={'ok': 1})
    bp = Blueprint(name='guarded', steps=[
        {'name': 's1', 'tool': 'a', 'args': {}, 'on_error': 'continue'},
        {'name': 's2', 'tool': 'b', 'args': {}, 'if': 'prev_ok'},
    ])
    res = bp.run(_ctx({'a': bad, 'b': guard}))

    assert res.steps[1].status == 'skipped' and res.steps[1].skipped is True
    assert res.steps[1].skip_reason == 'condition' and 'prev_ok' in res.steps[1].note
    assert guard.calls == []
    assert res.ok is False and res.failed_step == 's1'


def test_if_prev_ok_executes_when_previous_ok() -> None:
    """前一步成功时 if='prev_ok' 正常执行。"""
    a = RecordTool(data={'a': 1})
    b = RecordTool(data={'b': 2})
    bp = Blueprint(name='guarded_ok', steps=[
        {'name': 's1', 'tool': 'a', 'args': {}},
        {'name': 's2', 'tool': 'b', 'args': {}, 'if': 'prev_ok'},
    ])
    res = bp.run(_ctx({'a': a, 'b': b}))

    assert res.ok is True
    assert [s.status for s in res.steps] == ['ok', 'ok']
    assert len(b.calls) == 1 and res.steps[1].output == {'b': 2}


def test_if_prev_ok_skip_propagates_along_chain() -> None:
    """被跳过的步骤不算成功：后续 if='prev_ok' 链式一并跳过。"""
    bad = RecordTool(err_code='ERR_X', err_msg='坏了')
    guard = RecordTool(data={'ok': 1})
    bp = Blueprint(name='chain_guard', steps=[
        {'name': 's1', 'tool': 'a', 'args': {}, 'on_error': 'continue'},
        {'name': 's2', 'tool': 'b', 'args': {}, 'if': 'prev_ok'},
        {'name': 's3', 'tool': 'b', 'args': {}, 'if': 'prev_ok'},
    ])
    res = bp.run(_ctx({'a': bad, 'b': guard}))

    assert [s.status for s in res.steps] == ['failed', 'skipped', 'skipped']
    assert guard.calls == []


def test_if_prev_failed_runs_only_after_failure() -> None:
    """if='prev_failed' 用于补偿/降级链：只在前一步失败时执行。"""
    ok_tool = RecordTool(data={'ok': 1})
    bad_tool = RecordTool(err_code='ERR_X', err_msg='坏了')
    rescue = RecordTool(data={'rescued': True})

    happy = Blueprint(name='happy', steps=[
        {'name': 's1', 'tool': 'a', 'args': {}},
        {'name': 's2', 'tool': 'r', 'args': {}, 'if': 'prev_failed'},
    ])
    res_ok = happy.run(_ctx({'a': ok_tool, 'r': rescue}))
    assert res_ok.steps[1].status == 'skipped' and rescue.calls == []

    rescue.calls.clear()
    sad = Blueprint(name='sad', steps=[
        {'name': 's1', 'tool': 'a', 'args': {}, 'on_error': 'continue'},
        {'name': 's2', 'tool': 'r', 'args': {}, 'if': 'prev_failed'},
    ])
    res_bad = sad.run(_ctx({'a': bad_tool, 'r': rescue}))
    assert res_bad.steps[1].status == 'ok' and len(rescue.calls) == 1
    assert res_bad.ok is False and res_bad.failed_step == 's1'


def test_first_step_prev_ok_runs_without_previous_step() -> None:
    """首步带 if='prev_ok' 时按真空真处理（无前序约束），照常执行。"""
    a = RecordTool(data={'a': 1})
    bp = Blueprint(name='first_guard', steps=[
        {'name': 's1', 'tool': 'a', 'args': {}, 'if': 'prev_ok'},
    ])
    res = bp.run(_ctx({'a': a}))

    assert res.ok is True and len(a.calls) == 1
    assert res.steps[0].status == 'ok'


# --------------------------------------------------------------------------- #
# §8 验收 5：参数化蓝图 / 边界
# --------------------------------------------------------------------------- #

def test_parametrized_zap_to_uses_channel_name() -> None:
    """验收#5：参数化蓝图 zap_to(channel_name) 把参数透传给 zap 工具。"""
    zap = RecordTool(data={'channel_no': 51})
    runner = BlueprintRunner(tools={'zap': zap}, sleep=lambda s: None)
    runner.register(builtin_zap_to())

    res = runner.run('zap_to', {'channel_name': '湖南卫视'})

    assert res.ok is True
    assert zap.calls == [{'channel': '湖南卫视'}]
    assert res.params == {'channel_name': '湖南卫视'}
    assert res.steps[0].output == {'channel_no': 51}


def test_missing_required_param_fails_before_running_steps() -> None:
    """缺必填参数 fail-closed：一条工具都不调用，错误里点名缺哪个参数。"""
    zap = RecordTool(data={})
    bp = builtin_zap_to()
    res = bp.run(_ctx({'zap': zap}, {}))

    assert res.ok is False and res.failed_step is None and res.steps == []
    assert 'channel_name' in res.error
    assert zap.calls == []


def test_param_default_and_typed_placeholder() -> None:
    """映射式 params 提供默认值；整串占位符保类型（int 不被字符串化）。"""
    rec = RecordTool(data={'ok': 1})
    bp = Blueprint(name='typed', params={'count': 3, 'label': 'x'}, steps=[
        {'name': 's1', 'tool': 't', 'args': {'n': '${count}', 'tag': '${label}'}},
    ])

    res_default = bp.run(_ctx({'t': rec}, {}))
    assert res_default.ok is True and rec.calls[-1] == {'n': 3, 'tag': 'x'}

    res_given = bp.run(_ctx({'t': rec}, {'count': 7}))
    assert rec.calls[-1] == {'n': 7, 'tag': 'x'}


def test_embedded_placeholder_renders_string() -> None:
    """内嵌占位符做字符串拼接，未命中部分原样保留。"""
    rec = RecordTool(data={})
    bp = Blueprint(name='embed', params=['name'], steps=[
        {'name': 's1', 'tool': 't', 'args': {'label': 'ch-${name}-done'}},
    ])
    res = bp.run(_ctx({'t': rec}, {'name': '湖南卫视'}))

    assert res.ok is True
    assert rec.calls == [{'label': 'ch-湖南卫视-done'}]


def test_unresolved_placeholder_fails_closed() -> None:
    """无法解析的占位符 fail-closed：步骤失败、工具不被调用、错误点名缺失项。"""
    rec = RecordTool(data={})
    bp = Blueprint(name='missing', steps=[
        {'name': 's1', 'tool': 't', 'args': {'channel': '${nope}'}},
    ])
    res = bp.run(_ctx({'t': rec}))

    assert res.ok is False and res.failed_step == 's1'
    assert res.steps[0].error_code == 'param_error' and 'nope' in res.steps[0].error
    assert rec.calls == []


def test_save_as_output_visible_to_later_steps() -> None:
    """save_as 把上一步输出暴露给后续步骤（${var.key} 点路径，保类型）。"""
    def first(no: int) -> Dict[str, Any]:
        return {'no': no, 'name': '中央一台'}

    rec = RecordTool(data={})
    bp = Blueprint(name='vars', steps=[
        {'name': 's1', 'tool': 'first', 'args': {'no': 21}, 'save_as': 'prev'},
        {'name': 's2', 'tool': 'rec', 'args': {'channel_no': '${prev.no}', 'label': '${prev.name}'}},
    ])
    res = bp.run(_ctx({'first': first, 'rec': rec}))

    assert res.ok is True
    assert rec.calls == [{'channel_no': 21, 'label': '中央一台'}]
    assert res.steps[0].output['name'] == '中央一台'


# --------------------------------------------------------------------------- #
# §2.1 目标 2/3：打开设置 / 回桌面 复合操作
# --------------------------------------------------------------------------- #

def test_builtin_go_home_blueprint() -> None:
    """§2.1『回桌面』：内置 go_home 蓝图串起 go_home 工具 + 稳定等待。"""
    home = RecordTool(data={'is_home': True, 'foreground_after': 'com.mitv.tvhome'})
    sleeps: List[float] = []
    bp = builtin_go_home()
    res = bp.run(_ctx({'go_home': home}, sleep=sleeps.append))

    assert res.ok is True
    assert home.calls == [{}]
    assert [s.step for s in res.steps] == ['home', 'settle']
    assert res.steps[0].output['is_home'] is True
    assert sleeps == [0.3]


def test_builtin_open_settings_blueprint() -> None:
    """§2.1『打开设置』：回桌面(fail-open) -> 启动设置 App -> 条件性稳定等待。"""
    home = RecordTool(err_code='ERR_WRONG_FOREGROUND', err_msg='回桌面后前台不是桌面')
    launch = RecordTool(data={'package': SETTINGS_PKG, 'foreground': True})
    bp = builtin_open_settings()
    res = bp.run(_ctx({'go_home': home, 'launch_app': launch}, sleep=lambda s: None))

    assert [s.status for s in res.steps] == ['failed', 'ok', 'ok']  # 首跳失败但不阻断
    assert res.steps[0].note is None and res.steps[0].error_code == 'ERR_WRONG_FOREGROUND'
    assert launch.calls == [{'package': SETTINGS_PKG}]
    assert res.ok is False and res.failed_step == 'to_home'  # 首跳失败如实上报


# --------------------------------------------------------------------------- #
# 直跑入口（无 pytest 时也能报 total/collectable/ran）
# --------------------------------------------------------------------------- #

if __name__ == '__main__':  # pragma: no cover
    items = sorted((k, v) for k, v in list(globals().items())
                   if k.startswith('test_') and callable(v))
    collectable = len(items)
    ran = failed = 0
    for _name, _fn in items:
        ran += 1
        try:
            _fn()
            print(f'  ok    {_name}')
        except Exception as _exc:  # noqa: BLE001
            failed += 1
            print(f'  FAIL  {_name}: {type(_exc).__name__}: {_exc}')
    print(f'total={collectable} collectable={collectable} ran={ran} failed={failed}')
    sys.exit(1 if failed else 0)
