"""TVPilot 经验复用与自进化 · 单元测试。

覆盖：记录(成功/失败)、相关度查询、Top-K 排序、(task+steps) 去重、90 天修剪、
持久化到 experience.json、损坏文件 fail-open、非法入参 fail-closed、
重放参数替换 / 缺参 / 失败中止 / 结果回写、经验推荐、默认执行器分发、ops 桥接。

全部使用 tmp_path，不写真实 data/，不发起任何 ADB 调用。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import types
from datetime import datetime, timedelta

import pytest

from tvpilot.experience import (
    DEFAULT_MAX_AGE_DAYS,
    Experience,
    ExperienceError,
    ExperienceReplayer,
    ExperienceStore,
    default_executor,
    experience_from_ops,
    experience_path,
    make_experience_key,
)


# ---------- 测试工具 ----------

def _iso_ago(days: float = 0.0) -> str:
    """生成 days 天前的本地 ISO 时间戳。"""
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec='seconds')


def _exp(task: str = '换台到CCTV1', steps=None, success: bool = True,
         duration_ms: int = 1200, ts: str = None, **kw) -> Experience:
    """构造一条经验的便捷函数。"""
    return Experience(
        task=task,
        steps=steps if steps is not None else [{'tool': 'zap', 'channel': 'CCTV1'}],
        success=success,
        duration_ms=duration_ms,
        timestamp=ts or _iso_ago(),
        **kw,
    )


@pytest.fixture()
def store(tmp_path):
    """临时目录里的经验库。"""
    return ExperienceStore(path=str(tmp_path / 'experience.json'))


# ---------- 2.1 目标 1：记录成功/失败 + query("换台") 返回相关经验 ----------

def test_record_success_experience_and_query_fields(store):
    saved = store.record(_exp())
    assert saved.attempts == 1 and saved.successes == 1 and saved.success_rate == 1.0
    hits = store.query('换台')
    assert len(hits) == 1
    hit = hits[0]
    assert hit.task == '换台到CCTV1'
    assert hit.steps == [{'tool': 'zap', 'channel': 'CCTV1'}]
    assert hit.success is True
    assert hit.duration_ms == 1200
    assert hit.timestamp and hit.first_timestamp == hit.timestamp


def test_record_failure_experience_keeps_failure_semantics(store):
    exp = _exp('打开设置', [{'tool': 'launch_app', 'package': 'com.x.settings'}],
               success=False, duration_ms=8000)
    store.record(exp)
    hits = store.query('打开设置')
    assert len(hits) == 1
    assert hits[0].success is False
    assert hits[0].successes == 0 and hits[0].attempts == 1
    assert hits[0].success_rate == 0.0
    assert hits[0].duration_ms == 8000


def test_query_returns_related_experiences_only(store):
    store.record(_exp('换台到CCTV1', [{'tool': 'zap', 'channel': 'CCTV1'}]))
    store.record(_exp('换台到CCTV5', [{'tool': 'zap', 'channel': 'CCTV5'}]))
    store.record(_exp('打开设置', [{'tool': 'launch_app', 'package': 'com.x'}]))
    hits = store.query('换台')
    assert sorted(h.task for h in hits) == ['换台到CCTV1', '换台到CCTV5']
    assert all('换台' in h.task for h in hits)


def test_query_no_match_returns_empty(store):
    store.record(_exp('打开设置', [{'tool': 'launch_app', 'package': 'com.x'}]))
    assert store.query('换台') == []


def test_query_empty_task_returns_all_newest_first(store):
    store.record(_exp('任务甲', [{'tool': 'wait', 'seconds': 1}], ts=_iso_ago(days=3)))
    store.record(_exp('任务乙', [{'tool': 'wait', 'seconds': 2}], ts=_iso_ago(days=1)))
    hits = store.query('')
    assert [h.task for h in hits] == ['任务乙', '任务甲']


# ---------- 2.1 目标 2：top_k 按成功率+耗时排序 ----------

def test_top_k_success_rate_dominates_duration(store):
    steps_a = [{'tool': 'zap', 'channel': 'A1'}]
    steps_b = [{'tool': 'zap', 'channel': 'B1'}]
    # A：慢但全成（1.0）；B：快但半失败（0.5）
    store.record(_exp('换台A', steps_a, success=True, duration_ms=5000))
    store.record(_exp('换台A', steps_a, success=True, duration_ms=5000))
    store.record(_exp('换台B', steps_b, success=True, duration_ms=200))
    store.record(_exp('换台B', steps_b, success=False, duration_ms=200))
    ranked = store.top_k('换台', k=5)
    assert [e.task for e in ranked] == ['换台A', '换台B']
    assert ranked[0].success_rate == 1.0
    assert ranked[1].success_rate == 0.5
    assert ranked[0].score > ranked[1].score


def test_top_k_tie_prefers_faster_duration(store):
    store.record(_exp('换台慢', [{'tool': 'tap', 'x': 1, 'y': 1}], duration_ms=3000))
    store.record(_exp('换台快', [{'tool': 'tap', 'x': 2, 'y': 2}], duration_ms=300))
    ranked = store.top_k('换台', k=2)
    assert [e.task for e in ranked] == ['换台快', '换台慢']


def test_top_k_respects_k_limit(store):
    for i in range(3):
        store.record(_exp(f'换台{i}', [{'tool': 'zap', 'channel': f'C{i}'}],
                          success=True, duration_ms=100 * (i + 1)))
    top1 = store.top_k('换台', k=1)
    assert len(top1) == 1
    assert top1[0].duration_ms == 100  # 同成功率下最快者胜出
    assert len(store.top_k('换台', k=10)) == 3


# ---------- 2.1 目标 3：经验去重（task+steps 只留最新）----------

def test_dedup_same_task_steps_keeps_latest_and_aggregates(store):
    steps = [{'tool': 'zap', 'channel': 'CCTV1'}]
    old = _exp('换台到CCTV1', steps, success=True, duration_ms=1000, ts=_iso_ago(days=2))
    new = _exp('换台到CCTV1', steps, success=False, duration_ms=2500, ts=_iso_ago(days=1))
    store.record(old)
    merged = store.record(new)
    assert len(store) == 1
    assert merged.attempts == 2 and merged.successes == 1
    assert merged.success_rate == 0.5
    assert merged.timestamp == new.timestamp          # 只保留最新
    assert merged.duration_ms == 2500 and merged.success is False
    assert merged.first_timestamp == old.timestamp    # 保留首见时间


def test_dedup_different_steps_not_merged(store):
    store.record(_exp('换台', [{'tool': 'zap', 'channel': 'CCTV1'}]))
    store.record(_exp('换台', [{'tool': 'zap', 'channel': 'CCTV5'}]))
    assert len(store) == 2


def test_experience_key_ignores_task_case_whitespace_and_key_order():
    k1 = make_experience_key('换台', [{'tool': 'zap', 'channel': 'CCTV1'}])
    k2 = make_experience_key('  换台 ', [{'channel': 'CCTV1', 'tool': 'zap'}])
    k3 = make_experience_key('换台', [{'tool': 'zap', 'channel': 'CCTV5'}])
    k4 = make_experience_key('换台', [{'tool': 'zap', 'channel': 'cctv1'}])
    assert k1 == k2
    assert k1 != k3   # 不同步骤
    assert k1 != k4   # 值大小写不折叠（input_text 场景需要）


# ---------- 2.1 目标 4：prune() 清理超过 90 天的旧经验 ----------

def test_prune_removes_only_experiences_older_than_90_days(store):
    store.record(_exp('旧经验', [{'tool': 'tap', 'x': 9, 'y': 9}], ts=_iso_ago(days=100)))
    store.record(_exp('临界内', [{'tool': 'tap', 'x': 8, 'y': 8}], ts=_iso_ago(days=89)))
    store.record(_exp('新经验', [{'tool': 'tap', 'x': 7, 'y': 7}], ts=_iso_ago()))
    assert DEFAULT_MAX_AGE_DAYS == 90 and store.max_age_days == 90
    removed = store.prune()
    assert removed == 1
    assert {e.task for e in store.all()} == {'新经验', '临界内'}
    assert store.prune() == 0  # 幂等


def test_prune_supports_custom_window_and_returns_count(tmp_path):
    store = ExperienceStore(path=str(tmp_path / 'experience.json'), max_age_days=10)
    store.record(_exp('过期', [{'tool': 'wait', 'seconds': 1}], ts=_iso_ago(days=15)))
    store.record(_exp('有效', [{'tool': 'wait', 'seconds': 2}], ts=_iso_ago(days=5)))
    assert store.prune() == 1
    assert [e.task for e in store.all()] == ['有效']
    assert store.prune(max_age_days=1) == 1


# ---------- 2.1 目标 5/6：持久化 data/experience.json + 容错 ----------

def test_persistence_roundtrip_and_default_path(tmp_path, monkeypatch):
    monkeypatch.delenv('TVPILOT_EXPERIENCE_FILE', raising=False)
    monkeypatch.delenv('TVPILOT_DATA_DIR', raising=False)
    path = tmp_path / 'experience.json'
    store = ExperienceStore(path=str(path))
    store.record(_exp())
    assert path.exists()
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['version'] == 1
    assert isinstance(raw['experiences'], list) and len(raw['experiences']) == 1
    assert raw['experiences'][0]['task'] == '换台到CCTV1'

    reloaded = ExperienceStore(path=str(path))
    assert len(reloaded) == 1
    assert reloaded.query('换台')[0].steps == [{'tool': 'zap', 'channel': 'CCTV1'}]

    default = experience_path()
    assert os.path.basename(default) == 'experience.json'
    assert os.path.basename(os.path.dirname(default)) == 'data'


def test_corrupt_or_bad_entries_fail_open(tmp_path):
    bad = tmp_path / 'bad.json'
    bad.write_text('{ 这不是 JSON', encoding='utf-8')
    assert len(ExperienceStore(path=str(bad))) == 0

    mixed = tmp_path / 'mixed.json'
    mixed.write_text(json.dumps({
        'version': 1,
        'experiences': [_exp().to_dict(), 42, {'task': ''}, 'x'],
    }, ensure_ascii=False), encoding='utf-8')
    assert len(ExperienceStore(path=str(mixed))) == 1


def test_invalid_experience_fail_closed(store):
    with pytest.raises(ExperienceError):
        Experience(task='   ')
    with pytest.raises(ExperienceError):
        Experience(task='ok', steps='不是列表')
    with pytest.raises(ExperienceError):
        Experience(task='ok', steps=[{'tool': 'wait'}], duration_ms=-5)
    with pytest.raises(TypeError):
        store.record({'task': 'x'})
    assert len(store) == 0


# ---------- 2.1 目标 7/8：重放（参数化、fail-closed、自进化回写）----------

def test_replay_resolves_params_and_runs_in_order(store):
    calls = []

    def executor(step):
        calls.append(step)
        return {'echo': step}, None, None

    replayer = ExperienceReplayer(store=store, executor=executor, record_replay=False)
    exp = _exp('换台到{channel}',
               steps=[{'tool': 'keyevent', 'key': '{key}'}, {'tool': 'zap', 'channel': '{channel}'}])
    result = replayer.replay(exp, {'key': 'KEYCODE_HOME', 'channel': 'CCTV1'})
    assert result['ok'] is True
    assert result['steps_total'] == 2 and result['steps_run'] == 2
    assert calls == [{'tool': 'keyevent', 'key': 'KEYCODE_HOME'},
                     {'tool': 'zap', 'channel': 'CCTV1'}]
    assert [r['ok'] for r in result['results']] == [True, True]
    assert result['recorded'] is False and result['error'] is None
    assert result['duration_ms'] >= 0


def test_replay_missing_param_is_fail_closed(store):
    calls = []

    def executor(step):
        calls.append(step)
        return None, None, None

    replayer = ExperienceReplayer(store=store, executor=executor, record_replay=False)
    exp = _exp('换台到{channel}', steps=[{'tool': 'zap', 'channel': '{channel}'}])
    result = replayer.replay(exp, {})
    assert result['ok'] is False
    assert result['steps_run'] == 0
    assert '缺少参数' in result['error']
    assert calls == []   # 一步都没执行


def test_replay_aborts_after_failed_step(store):
    calls = []

    def executor(step):
        calls.append(step['tool'])
        if step['tool'] == 'swipe':
            return None, 'ERR_OPERATION_TIMEOUT', '滑动超时'
        return {'tool': step['tool']}, None, None

    replayer = ExperienceReplayer(store=store, executor=executor, record_replay=False)
    exp = _exp('滑动流程', steps=[{'tool': 'keyevent', 'key': 'KEYCODE_HOME'},
                                {'tool': 'swipe', 'x1': 0, 'y1': 0, 'x2': 1, 'y2': 1},
                                {'tool': 'wait', 'seconds': 1}])
    result = replayer.replay(exp, {})
    assert result['ok'] is False
    assert calls == ['keyevent', 'swipe']              # 第三步未执行
    assert result['steps_run'] == 1
    assert len(result['results']) == 2
    assert result['results'][-1]['ok'] is False
    assert 'ERR_OPERATION_TIMEOUT' in result['error']


def test_replay_records_outcome_back_to_store(store):
    def executor(step):
        return {'done': True}, None, None

    replayer = ExperienceReplayer(store=store, executor=executor)
    exp = _exp('换台到CCTV1', steps=[{'tool': 'zap', 'channel': 'CCTV1'}])
    result = replayer.replay(exp, {})
    assert result['recorded'] is True
    saved = store.query('换台')
    assert len(saved) == 1
    assert saved[0].attempts == 1 and saved[0].successes == 1
    assert saved[0].success is True and saved[0].duration_ms >= 0
    assert saved[0].note.startswith('replay of')


# ---------- 2.1 目标 9：经验推荐 ----------

def test_suggest_ranks_proven_paths_and_excludes_never_successful(store):
    steps_a = [{'tool': 'zap', 'channel': 'A1'}]
    steps_b = [{'tool': 'zap', 'channel': 'B1'}]
    steps_c = [{'tool': 'zap', 'channel': 'C1'}]
    store.record(_exp('换台A', steps_a, success=True, duration_ms=5000))
    store.record(_exp('换台A', steps_a, success=True, duration_ms=5000))
    store.record(_exp('换台B', steps_b, success=True, duration_ms=300))
    store.record(_exp('换台B', steps_b, success=False, duration_ms=300))
    store.record(_exp('换台C', steps_c, success=False, duration_ms=100))

    tips = ExperienceReplayer(store=store, executor=lambda step: None).suggest('换台', k=3)
    assert [t['task'] for t in tips] == ['换台A', '换台B']
    assert tips[0]['success_rate'] == 1.0 and tips[1]['success_rate'] == 0.5
    assert tips[0]['score'] > tips[1]['score']
    assert tips[0]['reason'] and '成功率' in tips[0]['reason']
    assert tips[0]['steps'] == steps_a


# ---------- 2.1 目标 10：默认执行器对接 tools.py ----------

def test_default_executor_dispatches_to_tools_contract(monkeypatch):
    import tvpilot
    calls = []
    fake = types.ModuleType('tvpilot.tools')

    def tool_keyevent(key):
        calls.append(('keyevent', key))
        return {'key': key}, None, None

    def tool_tap(x, y):
        calls.append(('tap', int(x), int(y)))
        return {'x': int(x), 'y': int(y)}, None, None

    fake.tool_keyevent = tool_keyevent
    fake.tool_tap = tool_tap
    monkeypatch.setitem(sys.modules, 'tvpilot.tools', fake)
    monkeypatch.setattr(tvpilot, 'tools', fake, raising=False)

    out = default_executor({'tool': 'keyevent', 'key': 'KEYCODE_HOME'})
    assert out == ({'key': 'KEYCODE_HOME'}, None, None)
    out2 = default_executor({'tool': 'tap', 'params': {'x': 10, 'y': 20}})
    assert out2 == ({'x': 10, 'y': 20}, None, None)
    assert calls == [('keyevent', 'KEYCODE_HOME'), ('tap', 10, 20)]

    bad = default_executor({'tool': 'tap', 'x': 1})          # 缺 y → TypeError 捕获
    assert bad[0] is None and bad[1] is not None and '执行失败' in bad[2]


def test_replay_rejects_unknown_tool_fail_closed(store):
    replayer = ExperienceReplayer(store=store, executor=default_executor, record_replay=False)
    result = replayer.replay(_exp('未知工具', [{'tool': 'warp_drive'}]), {})
    assert result['ok'] is False
    assert result['results'][0]['ok'] is False
    assert '未知工具' in result['results'][0]['error']
    assert result['results'][0]['error'].startswith('ERR_INVALID_PARAMETER')


# ---------- 与 ops_log 的兼容边界：ops.jsonl → Experience ----------

def test_experience_from_ops_bridges_history(store):
    records = [
        {'tool': 'zap', 'params': {'channel': 'CCTV1'}, 'ok': True, 'cost_ms': 900,
         'ts': _iso_ago(days=0)},
        {'tool': 'keyevent', 'params': {'key': 'KEYCODE_BACK'}, 'ok': True, 'cost_ms': 300,
         'ts': _iso_ago(days=0)},
    ]
    exp = experience_from_ops('换台并返回', records)
    assert exp.success is True
    assert exp.duration_ms == 1200
    assert exp.steps == [{'tool': 'zap', 'channel': 'CCTV1'},
                         {'tool': 'keyevent', 'key': 'KEYCODE_BACK'}]
    assert exp.timestamp == records[-1]['ts']
    store.record(exp)
    assert store.query('换台')[0].task == '换台并返回'

    failed = experience_from_ops('换台失败', [
        {'tool': 'zap', 'params': {'channel': 'CCTV9'}, 'ok': False, 'cost_ms': 400}])
    assert failed.success is False and failed.duration_ms == 400
