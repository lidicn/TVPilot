"""tests/test_planner.py —— tvpilot.planner 单测（注入假执行器，零设备、零 ADB）。

覆盖：任务拆解、PlanStep 契约、StepResult 逐位返回、失败即停并报告、
目标解析、异常与错误三元组、期望/条件语义、JSON 往返、ops 记录。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tvpilot.planner import (  # noqa: E402
    ERR_PLAN_EXPECT_MISMATCH, ERR_PLAN_STEP_FAILED, ERR_PLAN_TARGET_NOT_FOUND,
    ERR_PLAN_UNSUPPORTED_ACTION, ERR_PLAN_UNKNOWN_INTENT, STATUS_FAILED,
    STATUS_NOT_RUN, STATUS_SKIPPED, STATUS_SUCCESS, SUPPORTED_ACTIONS, _TOOL_SPECS,
    ExecutionReport, Plan, PlanStep, StepResult, TaskPlanner, tool_runner,
)
from tvpilot.ui_map import UiMap  # noqa: E402


class FakeRunner:
    """假执行器：记录调用，可按序号注入错误三元组或异常。"""

    def __init__(self, fail_at=None, fail_with=('ERR_TEST', 'boom'), raise_at=None):
        self.calls = []
        self.fail_at = fail_at
        self.fail_with = fail_with
        self.raise_at = raise_at

    def __call__(self, action, target, params):
        self.calls.append((action, target, dict(params)))
        index = len(self.calls) - 1
        if self.raise_at == index:
            raise RuntimeError('runner boom')
        if self.fail_at == index:
            return None, self.fail_with[0], self.fail_with[1]
        return {'action': action}, None, None


def make_ui(pairs):
    """用 (text, bounds) 列表构造 UiMap。"""
    return UiMap.build(None, [{'text': t, 'bounds': b} for t, b in pairs])


SETTINGS_UI = [('设置', [100, 100, 200, 140]), ('WLAN', [300, 200, 400, 240])]


def test_plan_wifi_settings_yields_at_least_three_steps():
    """验收4：'打开设置里的WiFi' -> >=3 个 PlanStep，且契约字段齐备。"""
    plan = TaskPlanner().plan('打开设置里的WiFi')
    assert isinstance(plan, Plan)
    assert len(plan) >= 3, f'实际 {len(plan)} 步'
    for step in plan:
        assert isinstance(step, PlanStep)
        for attr in ('action', 'target', 'params', 'expected'):
            assert hasattr(step, attr)
        assert step.params.__class__ is dict
    assert [s.id for s in plan] == [f's{i}' for i in range(1, len(plan) + 1)]


def test_plan_wifi_steps_match_open_enter_verify_semantics():
    """WiFi 计划语义：开设置 -> 进 WiFi 项 -> 验证页面。"""
    plan = TaskPlanner().plan('打开设置里的WiFi')
    first = plan[0]
    assert first.action in ('tap', 'launch')
    assert ('设置' in (first.target or '')) or 'settings' in str(first.params.get('package', ''))
    assert any('WiFi' in (s.target or '') or 'WLAN' in (s.target or '') for s in plan)
    assert plan[-1].action == 'verify'
    assert all(s.action in SUPPORTED_ACTIONS for s in plan)


def test_planstep_contract_positional_construction():
    """契约字段顺序：action, target, params, expected 位置传参可用。"""
    step = PlanStep('tap', '设置', {'x': 1, 'y': 2}, 'contains:设置')
    assert (step.action, step.target, step.params, step.expected) == ('tap', '设置', {'x': 1, 'y': 2}, 'contains:设置')
    restored = PlanStep.from_dict(json.loads(json.dumps(step.to_dict())))
    assert (restored.action, restored.target, restored.expected) == ('tap', '设置', 'contains:设置')


def test_execute_returns_step_result_for_every_step():
    """验收5前半：每步一个 StepResult，全部成功。"""
    fake = FakeRunner()
    planner = TaskPlanner(runner=fake, ui_provider=lambda: make_ui(SETTINGS_UI))
    plan = planner.plan('打开设置里的WiFi')
    report = planner.execute(plan)
    assert isinstance(report, ExecutionReport)
    assert len(report) == len(plan)
    for result in report:
        assert isinstance(result, StepResult)
        assert result.status == STATUS_SUCCESS and result.ok is True
        for attr in ('ok', 'status', 'error', 'error_code', 'detail', 'cost_ms'):
            assert hasattr(result, attr)
    assert report.ok is True and report.counts()[STATUS_SUCCESS] == len(plan)
    json.dumps(report.to_dict())


def test_execute_stops_at_failure_and_marks_rest_not_run():
    """验收5后半：失败即停，剩余步骤标记 not_run 并在报告中指出停止位置。"""
    fake = FakeRunner(fail_at=1, fail_with=('ERR_TEST', 'boom'))
    planner = TaskPlanner(runner=fake, ui_provider=lambda: make_ui(SETTINGS_UI))
    plan = planner.plan('打开设置里的WiFi')
    report = planner.execute(plan)
    assert report[1].status == STATUS_FAILED
    assert report[1].error_code == 'ERR_TEST' and report[1].error == 'boom'
    assert report.stopped_at == 1
    assert report.ok is False and report.error_code == 'ERR_TEST'
    assert [r.status for r in report[2:]] == [STATUS_NOT_RUN] * (len(plan) - 2)
    assert len(fake.calls) == 2, '失败后不得继续下发指令'


def test_optional_step_failure_is_skipped_and_run_continues():
    """optional 步失败降级 skipped，后续步骤照常执行。"""
    steps = [
        PlanStep('wait', None, {'seconds': 0.01}, None, id='s1'),
        PlanStep('tap', '幽灵按钮', {}, None, id='s2', optional=True),
        PlanStep('key', 'KEYCODE_BACK', {'key': 'KEYCODE_BACK'}, None, id='s3'),
    ]
    report = TaskPlanner(runner=FakeRunner()).execute(Plan(steps))
    assert [r.status for r in report] == [STATUS_SUCCESS, STATUS_SKIPPED, STATUS_SUCCESS]
    assert report.ok is True
    assert report[1].error_code == ERR_PLAN_TARGET_NOT_FOUND


def test_unknown_action_fails_closed_and_stops():
    """动作白名单外的动作 fail-closed。"""
    steps = [PlanStep('nuke', 'x', {}, None, id='s1'),
             PlanStep('key', 'KEYCODE_BACK', {'key': 'KEYCODE_BACK'}, None, id='s2')]
    fake = FakeRunner()
    report = TaskPlanner(runner=fake).execute(Plan(steps))
    assert report[0].status == STATUS_FAILED
    assert report[0].error_code == ERR_PLAN_UNSUPPORTED_ACTION
    assert report[1].status == STATUS_NOT_RUN
    assert fake.calls == []


def test_tap_target_resolves_to_node_center_from_ui():
    """tap 目标经 UiMap 文本定位 -> 中心点坐标下发。"""
    fake = FakeRunner()
    planner = TaskPlanner(runner=fake, ui_provider=lambda: make_ui([('设置', [100, 100, 200, 140])]))
    report = planner.execute(Plan([PlanStep('tap', '设置', {}, None, id='s1')]))
    assert report[0].status == STATUS_SUCCESS
    action, target, params = fake.calls[0]
    assert (action, target) == ('tap', '设置')
    assert (params['x'], params['y']) == (150, 120)
    assert report[0].detail['resolved_target'] == '设置'


def test_tap_without_resolvable_target_fails_closed():
    """无 UI、无坐标、无 fallback -> 找不到目标即失败。"""
    report = TaskPlanner(runner=FakeRunner()).execute(Plan([PlanStep('tap', '设置', {}, None, id='s1')]))
    assert report[0].status == STATUS_FAILED
    assert report[0].error_code == ERR_PLAN_TARGET_NOT_FOUND
    assert report.ok is False


def test_tap_falls_back_to_launch_when_target_missing():
    """params.fallback 兜底：目标找不到时改用 fallback 动作（设置包可启动）。"""
    fake = FakeRunner()
    step = PlanStep('tap', '设置', {'fallback': {'action': 'launch', 'package': 'com.android.tv.settings'}},
                    None, id='s1')
    report = TaskPlanner(runner=fake).execute(Plan([step]))
    assert report[0].status == STATUS_SUCCESS
    assert fake.calls[0][0] == 'launch'
    assert fake.calls[0][2] == {'package': 'com.android.tv.settings'}


def test_runner_exception_becomes_reported_failure():
    """runner 抛异常 -> 该步失败并带错误信息，不崩执行器。"""
    fake = FakeRunner(raise_at=0)
    report = TaskPlanner(runner=fake).execute(
        Plan([PlanStep('key', 'KEYCODE_BACK', {'key': 'KEYCODE_BACK'}, None, id='s1')]))
    assert report[0].status == STATUS_FAILED
    assert report[0].error_code == ERR_PLAN_STEP_FAILED
    assert 'boom' in report[0].error


def test_runner_error_triple_passthrough_keeps_code():
    """执行器三元组契约：err_code 原样透传到 StepResult.error_code。"""
    fake = FakeRunner(fail_at=0, fail_with=('ERR_WRONG_FOREGROUND', '前台不对'))
    report = TaskPlanner(runner=fake).execute(
        Plan([PlanStep('key', 'KEYCODE_BACK', {'key': 'KEYCODE_BACK'}, None, id='s1')]))
    assert report[0].error_code == 'ERR_WRONG_FOREGROUND'
    assert report[0].error == '前台不对'


def test_expected_met_is_three_valued_and_strict_mode_fails():
    """expected 语义：满足/不满足/无法判定；strict 模式不满足算失败。"""
    ok_ui = lambda: make_ui([('WiFi 设置', [0, 0, 100, 40])])       # noqa: E731
    bad_ui = lambda: make_ui([('关于', [0, 0, 100, 40])])           # noqa: E731
    step = PlanStep('verify', None, {}, 'contains:WiFi|WLAN', id='s1')

    r1 = TaskPlanner(runner=FakeRunner(), ui_provider=ok_ui).execute(Plan([step]))
    assert r1[0].expected_met is True and r1[0].ok is True

    r2 = TaskPlanner(runner=FakeRunner(), ui_provider=bad_ui).execute(Plan([step]))
    assert r2[0].expected_met is False and r2[0].ok is True          # 默认 fail-open

    r3 = TaskPlanner(runner=FakeRunner(), ui_provider=bad_ui, strict_expect=True).execute(Plan([step]))
    assert r3[0].status == STATUS_FAILED
    assert r3[0].error_code == ERR_PLAN_EXPECT_MISMATCH

    r4 = TaskPlanner(runner=FakeRunner()).execute(Plan([step]))      # 无 UI -> 无法判定
    assert r4[0].expected_met is None and r4[0].ok is True


def test_condition_false_skips_step_without_touching_runner():
    """条件不满足 -> 跳过该步且不下发指令，后续步骤继续。"""
    fake = FakeRunner()
    planner = TaskPlanner(runner=fake, ui_provider=lambda: make_ui([('已连接', [0, 0, 80, 30])]))
    steps = [
        PlanStep('key', 'KEYCODE_BACK', {'key': 'KEYCODE_BACK'}, None, id='s1'),
        PlanStep('tap', '开关', {'x': 10, 'y': 10}, None, id='s2',
                 condition='not_contains:已连接|已开启'),
        PlanStep('wait', None, {'seconds': 0.01}, None, id='s3'),
    ]
    report = planner.execute(Plan(steps))
    assert report[1].status == STATUS_SKIPPED
    assert report[1].detail['condition']['value'] is False
    assert [r.status for r in report] == [STATUS_SUCCESS, STATUS_SKIPPED, STATUS_SUCCESS]
    assert len(fake.calls) == 2


def test_verify_step_never_calls_runner():
    """verify 为纯校验步，不产生任何外部动作。"""
    fake = FakeRunner()
    planner = TaskPlanner(runner=fake, ui_provider=lambda: make_ui([('WLAN', [0, 0, 60, 20])]))
    steps = [PlanStep('tap', 'WLAN', {'x': 5, 'y': 5}, None, id='s1'),
             PlanStep('verify', None, {}, 'contains:WLAN', id='s2')]
    report = planner.execute(Plan(steps))
    assert len(fake.calls) == 1
    assert report[1].status == STATUS_SUCCESS and report[1].expected_met is True


def test_unknown_task_fails_closed_with_empty_plan():
    """无法解析的任务 -> 空计划 + ok=False + 错误码（不猜）。"""
    plan = TaskPlanner().plan('阐述量子纠缠的哲学意涵')
    assert plan.ok is False
    assert len(plan) == 0
    assert plan.error_code == ERR_PLAN_UNKNOWN_INTENT
    assert TaskPlanner().plan('   ').error_code == 'ERR_PLAN_EMPTY'


def test_multi_clause_task_composes_steps_in_order():
    """多子任务串联：「回到桌面然后打开设置里的WiFi」。"""
    plan = TaskPlanner().plan('回到桌面然后打开设置里的WiFi')
    assert len(plan) >= 5
    assert any(s.action == 'home' for s in plan)
    assert any('WiFi' in (s.target or '') or 'WLAN' in (s.target or '') for s in plan)
    assert plan[0].action == 'home'
    assert plan.meta['planner_source'] == 'rules'


def test_search_and_zap_plans_have_expected_shapes():
    """搜索计划 >=3 步且含输入；换台计划含 zap 步并带频道目标。"""
    planner = TaskPlanner()
    search = planner.plan('搜索甄嬛传')
    assert len(search) >= 3
    assert any(s.action == 'text' and s.params.get('text') == '甄嬛传' for s in search)

    zap = planner.plan('换台到CCTV1')
    assert any(s.action == 'zap' and s.target == 'CCTV1' for s in zap)
    assert zap[-1].action == 'verify'


def test_launch_app_plan_avoids_guessing_packages():
    """未配置包名的应用走「回桌面 + 点图标 + 验证」，不猜包名。"""
    plan = TaskPlanner().plan('打开爱奇艺')
    assert len(plan) >= 3
    assert plan[0].action == 'home'
    assert any(s.action == 'tap' and '爱奇艺' in (s.target or '') for s in plan)
    assert plan[-1].action == 'verify'

    with_pkg = TaskPlanner(apps={'爱奇艺': 'com.example.iqiyi'}).plan('打开爱奇艺')
    assert any(s.action == 'launch' and s.params.get('package') == 'com.example.iqiyi' for s in with_pkg)


def test_plan_json_roundtrip_is_executable():
    """计划 -> JSON -> 还原 -> 可执行，步 id 保持不变。"""
    planner = TaskPlanner(runner=FakeRunner(), ui_provider=lambda: make_ui(SETTINGS_UI))
    plan = planner.plan('打开设置里的WiFi')
    restored = Plan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert [s.id for s in restored] == [s.id for s in plan]
    report = planner.execute(restored)
    assert report.ok is True and len(report) == len(plan)


def test_ops_logger_receives_one_record_per_step():
    """操作存档：每步一条，参数对齐 ops_log.log_operation 关键字。"""
    logged = []
    planner = TaskPlanner(runner=FakeRunner(), ui_provider=lambda: make_ui(SETTINGS_UI),
                          ops_logger=lambda **kw: logged.append(kw))
    plan = planner.plan('打开设置里的WiFi')
    planner.execute(plan)
    assert len(logged) == len(plan)
    assert logged[0]['tool'].startswith('plan.')
    assert set(logged[0]) == {'tool', 'params', 'ok', 'error', 'cost_ms', 'request_id'}


def test_empty_plan_execute_reports_error():
    """空计划执行 -> 明确报告 ERR_PLAN_EMPTY，而不是静默成功。"""
    report = TaskPlanner(runner=FakeRunner()).execute([])
    assert report.ok is False
    assert report.error_code == 'ERR_PLAN_EMPTY'
    assert len(report) == 0


def test_stop_on_error_false_runs_remaining_steps():
    """stop_on_error=False 时失败不中断，但整体 ok 仍为 False。"""
    fake = FakeRunner(fail_at=0, fail_with=('ERR_TEST', 'x'))
    steps = [PlanStep('key', 'KEYCODE_BACK', {'key': 'KEYCODE_BACK'}, None, id='s1'),
             PlanStep('wait', None, {'seconds': 0.01}, None, id='s2')]
    report = TaskPlanner(runner=fake, stop_on_error=False).execute(Plan(steps))
    assert [r.status for r in report] == [STATUS_FAILED, STATUS_SUCCESS]
    assert report.ok is False and report.stopped_at is None


def test_tool_specs_cover_supported_actions_and_reject_unknown():
    """动作->工具映射完备；未知动作在 import 工具前即 fail-closed。"""
    assert set(_TOOL_SPECS) == SUPPORTED_ACTIONS - {'verify'}
    data, err, msg = tool_runner('teleport', None, {})
    assert data is None and err is not None and '不支持的动作' in msg
    data, err, msg = tool_runner('tap', None, {})
    assert err is not None and 'x/y' in msg


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
