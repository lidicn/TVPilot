"""TVPilot 操作存档（JSONL）。

绞杀者模式阶段2迁移：从 tvpilot_server.py 独立出来。
完全自包含：DATA_DIR 从环境变量读，默认项目根/data，不依赖主文件。
"""
import os
import json
import time
import threading
from collections import deque

# 项目根 = tvpilot/ 的上一级
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get('TVPILOT_DATA_DIR', os.path.join(_PROJECT_ROOT, 'data'))
OPS_LOG = os.path.join(DATA_DIR, 'ops.jsonl')
OPS_LOG_LOCK = threading.Lock()
OPS_LOG_MAX_BYTES = int(os.environ.get('TVPILOT_OPS_MAX_MB', '5')) * 1024 * 1024
SHOT_KEEP = int(os.environ.get('TVPILOT_SHOT_KEEP', '30'))

try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception as _e:
    print(f'[ops_log] data 目录不可用: {_e}', flush=True)


def _local_iso_ts():
    """带本地时区偏移的 ISO 时间戳。"""
    t = time.localtime()
    off = -time.altzone if (time.daylight and t.tm_isdst) else -time.timezone
    sign = '+' if off >= 0 else '-'
    off = abs(off)
    return time.strftime('%Y-%m-%dT%H:%M:%S', t) + f'{sign}{off // 3600:02d}:{off % 3600 // 60:02d}'


def _rotate_ops_if_needed():
    """ops.jsonl 超过上限则滚动为 .1（保留一代）。"""
    try:
        if os.path.exists(OPS_LOG) and os.path.getsize(OPS_LOG) > OPS_LOG_MAX_BYTES:
            bak = OPS_LOG + '.1'
            if os.path.exists(bak):
                os.remove(bak)
            os.rename(OPS_LOG, bak)
    except Exception:
        pass


def _trim_shots(keep=None):
    """失败截图只保留最新 N 张。"""
    try:
        keep = SHOT_KEEP if keep is None else int(keep)
        shots = [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR)
                 if f.startswith('shot_') and f.endswith('.png')]
        if len(shots) > keep:
            shots.sort(key=lambda p: os.path.getmtime(p))
            for p in shots[:len(shots) - keep]:
                try:
                    os.remove(p)
                except Exception:
                    pass
    except Exception:
        pass


def log_operation(tool, params, ok, error, cost_ms, request_id=None,
                  foreground_before=None, foreground_after=None, screenshot_path=None):
    """追加写入一条操作存档到 JSONL。"""
    try:
        record = {
            'ts': _local_iso_ts(),
            'ts_epoch': round(time.time(), 3),
            'request_id': request_id,
            'tool': tool,
            'params': params,
            'ok': ok,
            'error': error,
            'cost_ms': cost_ms,
            'foreground_before': foreground_before,
            'foreground_after': foreground_after,
        }
        if screenshot_path:
            record['screenshot'] = screenshot_path
        line = json.dumps(record, ensure_ascii=False)
        with OPS_LOG_LOCK:
            _rotate_ops_if_needed()
            with open(OPS_LOG, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
                f.flush()
                os.fsync(f.fileno())
    except Exception as e:
        print(f'[ops_log] write failed: {e}', flush=True)


def read_ops_history(limit=50, request_id=None):
    """读取操作存档，支持按 request_id 过滤和 limit 限制。"""
    try:
        if not os.path.exists(OPS_LOG):
            return []
        records = []
        if request_id:
            with open(OPS_LOG, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get('request_id') == request_id:
                        records.append(rec)
        else:
            _limit = max(1, min(int(limit or 50), 500))
            tail = max(_limit * 2 + 50, 100)
            with open(OPS_LOG, 'r', encoding='utf-8') as f:
                for line in deque(f, maxlen=tail):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        continue
        if limit and limit > 0:
            records = records[-int(limit):]
        return records
    except Exception as e:
        print(f'[ops_log] read failed: {e}', flush=True)
        return []


__all__ = [
    'DATA_DIR', 'OPS_LOG', 'OPS_LOG_LOCK', 'OPS_LOG_MAX_BYTES', 'SHOT_KEEP',
    '_local_iso_ts', '_rotate_ops_if_needed', '_trim_shots',
    'log_operation', 'read_ops_history',
]
