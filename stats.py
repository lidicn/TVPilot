# -*- coding: utf-8 -*-
"""TVPilot 工具调用统计与监控模块

从 ops.jsonl 计算工具调用统计：
- 总调用次数/成功率/平均延迟
- 按工具维度统计
- 按错误维度统计
- 时间趋势（最近24小时）
"""
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

OPS_LOG = os.environ.get('OPS_LOG_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ops.jsonl'))


def _load_ops(limit=None, since_hours=None):
    """加载 ops.jsonl，支持按条数限制和时间范围过滤。"""
    if not os.path.exists(OPS_LOG):
        return []
    ops = []
    since_ts = None
    if since_hours:
        since_ts = (datetime.now() - timedelta(hours=since_hours)).timestamp()

    with open(OPS_LOG, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if since_ts and rec.get('ts_epoch', 0) < since_ts:
                    continue
                ops.append(rec)
            except json.JSONDecodeError:
                continue
    if limit:
        ops = ops[-limit:]
    return ops


def get_stats(hours=24):
    """获取最近 N 小时的工具调用统计。

    返回：
    - total: 总调用次数
    - success_count / fail_count: 成功/失败次数
    - success_rate: 成功率（0-1）
    - avg_cost_ms: 平均延迟
    - by_tool: 按工具统计 {tool: {count, success, fail, avg_cost}}
    - by_error: 按错误统计 {error: count}
    - hourly: 最近24小时每小时调用量 [{"hour": "HH", "count": N, "success": N}]
    """
    ops = _load_ops(since_hours=hours)
    total = len(ops)
    if total == 0:
        return {
            "period_hours": hours,
            "total": 0, "success_count": 0, "fail_count": 0,
            "success_rate": 0, "avg_cost_ms": 0,
            "by_tool": {}, "by_error": {}, "hourly": []
        }

    success_count = sum(1 for o in ops if o.get('ok'))
    fail_count = total - success_count
    costs = [o.get('cost_ms', 0) for o in ops if o.get('cost_ms') is not None]
    avg_cost = sum(costs) / len(costs) if costs else 0

    # 按工具统计
    by_tool = defaultdict(lambda: {"count": 0, "success": 0, "fail": 0, "total_cost": 0})
    for o in ops:
        tool = o.get('tool', 'unknown')
        by_tool[tool]["count"] += 1
        if o.get('ok'):
            by_tool[tool]["success"] += 1
        else:
            by_tool[tool]["fail"] += 1
        by_tool[tool]["total_cost"] += o.get('cost_ms', 0)

    by_tool_out = {}
    for tool, s in by_tool.items():
        by_tool_out[tool] = {
            "count": s["count"],
            "success": s["success"],
            "fail": s["fail"],
            "success_rate": round(s["success"] / s["count"], 3) if s["count"] else 0,
            "avg_cost_ms": round(s["total_cost"] / s["count"], 1) if s["count"] else 0,
        }

    # 按错误统计
    by_error = defaultdict(int)
    for o in ops:
        if not o.get('ok') and o.get('error'):
            by_error[o['error']] += 1
    by_error_out = dict(sorted(by_error.items(), key=lambda x: -x[1]))

    # 每小时趋势（最近24小时）
    hourly = []
    now = datetime.now()
    for i in range(23, -1, -1):
        hour_dt = now - timedelta(hours=i)
        hour_key = hour_dt.strftime("%H")
        hour_start = hour_dt.replace(minute=0, second=0, microsecond=0).timestamp()
        hour_end = hour_start + 3600
        hour_ops = [o for o in ops if hour_start <= o.get('ts_epoch', 0) < hour_end]
        hourly.append({
            "hour": hour_key,
            "count": len(hour_ops),
            "success": sum(1 for o in hour_ops if o.get('ok')),
        })

    return {
        "period_hours": hours,
        "total": total,
        "success_count": success_count,
        "fail_count": fail_count,
        "success_rate": round(success_count / total, 3),
        "avg_cost_ms": round(avg_cost, 1),
        "by_tool": by_tool_out,
        "by_error": by_error_out,
        "hourly": hourly,
    }


def get_tv_status():
    """获取电视和服务状态（用于监控告警）。

    需要在主服务中注入 adb 检查函数。
    """
    # 这个函数由主服务覆盖，提供实时电视状态
    return {"tv_online": None, "note": "not initialized"}
