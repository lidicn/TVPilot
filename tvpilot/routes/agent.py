"""TVPilot Agent 高级能力路由。

蓝图系统、经验复用、界面地图、任务规划器的 HTTP API。
"""
import json
import os
import time

from tvpilot.blueprint import Blueprint, BlueprintRunner
from tvpilot.experience import Experience, ExperienceStore, ExperienceReplayer
from tvpilot.ui_map import UiMap
from tvpilot.planner import TaskPlanner
from tvpilot.a11y import (
    tool_a11y_click, tool_a11y_find, tool_a11y_input, tool_a11y_scroll,
    tool_a11y_key, tool_a11y_wait, tool_a11y_tree, tool_a11y_text,
    tool_a11y_long_click, tool_a11y_tap,
)

# 全局单例
_runner = None
_experience_store = None
_planner = None


def _get_runner():
    """获取蓝图执行器单例。"""
    global _runner
    if _runner is None:
        _runner = BlueprintRunner()
        _register_a11y_tools(_runner)
        _register_builtin_blueprints(_runner)
    return _runner


def _register_a11y_tools(runner):
    """注册无障碍工具到蓝图执行器。"""
    runner.register_tool('a11y_click', tool_a11y_click)
    runner.register_tool('a11y_find', tool_a11y_find)
    runner.register_tool('a11y_input', tool_a11y_input)
    runner.register_tool('a11y_scroll', tool_a11y_scroll)
    runner.register_tool('a11y_key', tool_a11y_key)
    runner.register_tool('a11y_wait', tool_a11y_wait)
    runner.register_tool('a11y_tree', tool_a11y_tree)
    runner.register_tool('a11y_text', tool_a11y_text)
    runner.register_tool('a11y_long_click', tool_a11y_long_click)
    runner.register_tool('a11y_tap', tool_a11y_tap)


def _get_experience_store():
    """获取经验存储单例。"""
    global _experience_store
    if _experience_store is None:
        data_dir = os.environ.get('TVPILOT_DATA_DIR', '/app/data')
        os.makedirs(data_dir, exist_ok=True)
        _experience_store = ExperienceStore(path=os.path.join(data_dir, 'experience.json'))
    return _experience_store


def _get_planner():
    """获取任务规划器单例。"""
    global _planner
    if _planner is None:
        _planner = TaskPlanner(runner=_get_runner())
    return _planner


def _register_builtin_blueprints(runner):
    """注册内置蓝图。"""
    # 回桌面
    runner.register(Blueprint(
        name='go_home',
        description='回到电视桌面',
        steps=[
            {'tool': 'keyevent', 'args': {'key': 'KEYCODE_HOME'}, 'name': 'home'},
            {'tool': 'wait', 'args': {'seconds': 1.0}, 'name': 'settle'},
        ],
    ))

    # 换台（参数化）
    runner.register(Blueprint(
        name='zap_to',
        description='换台到指定频道',
        params={'channel': {'type': 'str', 'required': True}},
        steps=[
            {'tool': 'zap', 'args': {'channel': '${channel}'}, 'name': 'zap'},
            {'tool': 'wait', 'args': {'seconds': 2.0}, 'name': 'settle'},
        ],
    ))

    # 打开设置
    runner.register(Blueprint(
        name='open_settings',
        description='打开电视设置',
        steps=[
            {'tool': 'keyevent', 'args': {'key': 'KEYCODE_SETTINGS'}, 'name': 'settings'},
            {'tool': 'wait', 'args': {'seconds': 1.5}, 'name': 'settle'},
        ],
    ))


def _json_response(handler, data, status=200):
    """发送 JSON 响应。"""
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    body = json.dumps(data, ensure_ascii=False, default=str).encode('utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_agent_get(handler, path, q):
    """处理 Agent 相关 GET 请求。返回 True 表示已处理。"""
    # 列出所有蓝图
    if path == '/api/blueprint/list':
        runner = _get_runner()
        blueprints = []
        for name in runner.list():
            bp = runner._registry.get(name)
            blueprints.append({
                'name': name,
                'description': getattr(bp, 'description', ''),
                'steps_count': len(bp.steps) if bp else 0,
                'params': getattr(bp, 'params', {}),
            })
        _json_response(handler, {'ok': True, 'blueprints': blueprints, 'count': len(blueprints)})
        return True

    # 查询经验
    if path == '/api/experience/query':
        task = q.get('task', [''])[0] if q else ''
        k = int(q.get('k', ['5'])[0]) if q else 5
        store = _get_experience_store()
        results = store.top_k(task, k=k) if task else store.query('')
        _json_response(handler, {
            'ok': True,
            'task': task,
            'results': [r.to_dict() if hasattr(r, 'to_dict') else dict(r) for r in results],
            'count': len(results),
        })
        return True

    return False


def handle_agent_post(handler, path, body):
    """处理 Agent 相关 POST 请求。返回 True 表示已处理。"""
    req_id = body.get('request_id', '')

    # 执行蓝图
    if path == '/api/blueprint/run':
        name = body.get('name', '')
        params = body.get('params', {})
        runner = _get_runner()

        t0 = time.time()
        try:
            result = runner.run(name, params=params)
            elapsed = int((time.time() - t0) * 1000)

            # 记录成功经验
            if result.ok:
                try:
                    store = _get_experience_store()
                    store.record(Experience(
                        task=f'blueprint:{name}',
                        steps=[{'name': s.step, 'ok': s.ok} for s in result.steps],
                        success=True,
                        duration_ms=elapsed,
                    ))
                except Exception:
                    pass

            _json_response(handler, {
                'ok': result.ok,
                'tool': 'blueprint.run',
                'request_id': req_id,
                'result': {
                    'name': name,
                    'ok': result.ok,
                    'steps': [
                        {
                            'step': s.step,
                            'ok': s.ok,
                            'status': getattr(s, 'status', ''),
                            'duration_ms': s.duration_ms,
                            'error': s.error,
                        }
                        for s in result.steps
                    ],
                    'total_duration_ms': elapsed,
                    'error': result.error if hasattr(result, 'error') else None,
                },
                'cost_ms': elapsed,
            })
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            _json_response(handler, {
                'ok': False,
                'tool': 'blueprint.run',
                'request_id': req_id,
                'error': str(e),
                'cost_ms': elapsed,
            }, status=500)
        return True

    # 注册蓝图
    if path == '/api/blueprint/register':
        name = body.get('name', '')
        steps = body.get('steps', [])
        description = body.get('description', '')
        params = body.get('params', {})

        if not name or not steps:
            _json_response(handler, {'ok': False, 'error': 'name and steps are required'}, status=400)
            return True

        runner = _get_runner()
        try:
            bp = Blueprint(name=name, steps=steps, description=description, params=params)
            runner.register(bp, replace=True)
            _json_response(handler, {'ok': True, 'name': name, 'steps_count': len(steps)})
        except Exception as e:
            _json_response(handler, {'ok': False, 'error': str(e)}, status=400)
        return True

    # 任务规划执行
    if path == '/api/planner/run':
        task = body.get('task', '')
        if not task:
            _json_response(handler, {'ok': False, 'error': 'task is required'}, status=400)
            return True

        planner = _get_planner()
        t0 = time.time()
        try:
            report = planner.run(task)
            elapsed = int((time.time() - t0) * 1000)
            _json_response(handler, {
                'ok': report.ok,
                'tool': 'planner.run',
                'request_id': req_id,
                'result': {
                    'task': task,
                    'ok': report.ok,
                    'steps_count': len(report.steps),
                    'stopped_at': report.stopped_at,
                    'error': report.error,
                    'steps': [
                        {
                            'step_id': getattr(s, 'step_id', ''),
                            'action': getattr(s, 'action', ''),
                            'target': getattr(s, 'target', ''),
                            'ok': s.ok,
                            'status': getattr(s, 'status', ''),
                            'error': s.error,
                        }
                        for s in report.steps
                    ],
                },
                'cost_ms': elapsed,
            })
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            _json_response(handler, {
                'ok': False,
                'tool': 'planner.run',
                'request_id': req_id,
                'error': str(e),
                'cost_ms': elapsed,
            }, status=500)
        return True

    # 记录经验
    if path == '/api/experience/record':
        task = body.get('task', '')
        steps = body.get('steps', [])
        success = body.get('success', True)
        duration_ms = body.get('duration_ms', 0)

        if not task:
            _json_response(handler, {'ok': False, 'error': 'task is required'}, status=400)
            return True

        store = _get_experience_store()
        try:
            exp = Experience(task=task, steps=steps, success=success, duration_ms=duration_ms)
            store.record(exp)
            _json_response(handler, {'ok': True, 'task': task, 'success': success})
        except Exception as e:
            _json_response(handler, {'ok': False, 'error': str(e)}, status=400)
        return True

    # 清理旧经验
    if path == '/api/experience/prune':
        max_age_days = body.get('max_age_days', 90)
        store = _get_experience_store()
        try:
            removed = store.prune(max_age_days=max_age_days)
            _json_response(handler, {'ok': True, 'removed': removed, 'max_age_days': max_age_days})
        except Exception as e:
            _json_response(handler, {'ok': False, 'error': str(e)}, status=500)
        return True

    return False
