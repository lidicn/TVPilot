"""TVPilot 经验复用与自进化。

把历史操作沉淀为 Experience，交给 ExperienceStore 持久化/检索/修剪，
再由 ExperienceReplayer 把成功路径重放与推荐给后续任务，形成自进化闭环。

组成：
- Experience           一条操作经验（task / steps / success / duration_ms / timestamp）
                       + 去重合并后的聚合统计（attempts / successes / first_timestamp）
- ExperienceStore      记录、相关度查询、Top-K 排序、(task+steps) 去重、时间窗修剪，
                       落盘 data/experience.json
- ExperienceReplayer   经验重放（参数模板替换 + 逐步执行）与经验推荐
- experience_from_ops  从 ops_log.read_ops_history() 的记录桥接出一条 Experience

约束：
- 只用标准库，不引入新依赖；
- 不修改 tvpilot/tools.py / tvpilot/ops_log.py，只在其之上做组合（默认执行器惰性调用
  tools 的工具函数）；
- 不做任何真实 LLM 调用；执行环节由调用方注入 executor，便于离线测试。
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from tvpilot.ops_log import DATA_DIR as _OPS_DATA_DIR, _local_iso_ts

__all__ = [
    'DEFAULT_MAX_AGE_DAYS', 'EXPERIENCE_FILE_NAME',
    'ExperienceError', 'ReplayError', 'MissingParameterError',
    'Step', 'Experience',
    'experience_path', 'make_experience_key',
    'ExperienceStore', 'ExperienceReplayer', 'default_executor', 'experience_from_ops',
]

DEFAULT_MAX_AGE_DAYS: int = 90
EXPERIENCE_FILE_NAME: str = 'experience.json'

Step = Dict[str, Any]
ExecutorType = Callable[[Step], Any]

_PLACEHOLDER_RE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')
_TOKEN_RE = re.compile(r'[0-9A-Za-z_\u4e00-\u9fff]+')


# ========== 异常 ==========

class ExperienceError(ValueError):
    """经验数据非法（fail-closed）。"""


class ReplayError(Exception):
    """经验重放失败基类。"""


class MissingParameterError(ReplayError):
    """步骤模板缺少参数（fail-closed，整段不执行）。"""


# ========== 时间 / 归一化工具 ==========

def _iso_to_epoch(ts: Union[str, float, int, None]) -> float:
    """把 ISO 字符串或 epoch 秒数解析为 float epoch；解析失败按当前时间处理。"""
    if ts is None:
        return time.time()
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except Exception:
        try:
            return float(ts)
        except Exception:
            return time.time()


def _ensure_iso(ts: Union[str, float, int, None]) -> str:
    """把任意时间表示统一成带本地时区偏移的 ISO 字符串。"""
    if isinstance(ts, str) and ts.strip():
        return ts.strip()
    return datetime.fromtimestamp(_iso_to_epoch(ts)).astimezone().isoformat(timespec='seconds')


def _normalize_task(task: Any) -> str:
    """任务描述归一化：小写 + 空白折叠（仅用于匹配与去重键，不改写展示值）。"""
    return re.sub(r'\s+', ' ', str(task or '')).strip().lower()


def _normalize_steps(steps: Any) -> str:
    """步骤序列归一化为稳定 JSON：按键排序、值转字符串并去首尾空白。

    不做大小写折叠：`input_text "Hello"` 与 `"hello"` 是不同经验。
    """
    norm: List[Any] = []
    for step in steps or []:
        if isinstance(step, Mapping):
            norm.append({str(k): (str(v).strip() if isinstance(v, str) else v)
                         for k, v in sorted(step.items(), key=lambda kv: str(kv[0]))})
        else:
            norm.append(str(step).strip())
    return json.dumps(norm, ensure_ascii=False, sort_keys=True)


def make_experience_key(task: Any, steps: Any) -> str:
    """经验去重键：sha1(归一化 task + 归一化 steps)。"""
    payload = f'{_normalize_task(task)}\u0000{_normalize_steps(steps)}'
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()


def experience_path() -> str:
    """经验文件路径：默认 data/experience.json。

    优先级：TVPILOT_EXPERIENCE_FILE > TVPILOT_DATA_DIR/experience.json
             > ops_log.DATA_DIR/experience.json。
    """
    override = os.environ.get('TVPILOT_EXPERIENCE_FILE')
    if override:
        return override
    data_dir = os.environ.get('TVPILOT_DATA_DIR') or _OPS_DATA_DIR
    return os.path.join(data_dir, EXPERIENCE_FILE_NAME)


# ========== Experience ==========

@dataclass
class Experience:
    """一条操作经验。

    契约字段（§3.1）：
        task        任务描述，例如 "换台到CCTV1"
        steps       步骤序列，元素形如 {"tool": "zap", "channel": "CCTV1"}
        success     本次是否成功
        duration_ms 本次耗时（毫秒）
        timestamp   本次发生时间（ISO-8601，带本地时区偏移）

    去重合并后的聚合字段（同 task+steps 只保留最新一条明细，统计累计）：
        attempts        累计尝试次数
        successes       累计成功次数（success_rate = successes / attempts）
        first_timestamp 首次出现时间
        exp_id          最新一次的 UUID
        params          最新一次的参数快照
        note            备注
    """

    task: str
    steps: List[Step] = field(default_factory=list)
    success: bool = False
    duration_ms: int = 0
    timestamp: str = field(default_factory=_local_iso_ts)
    exp_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    params: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 1
    successes: Optional[int] = None
    first_timestamp: Optional[str] = None
    note: str = ''

    def __post_init__(self) -> None:
        """构造即归一化 + 校验（非法即抛，fail-closed）。"""
        self.task = re.sub(r'\s+', ' ', str(self.task or '')).strip()
        self.timestamp = _ensure_iso(self.timestamp)
        if self.first_timestamp:
            self.first_timestamp = _ensure_iso(self.first_timestamp)
        try:
            self.duration_ms = int(self.duration_ms)
        except (TypeError, ValueError) as exc:
            raise ExperienceError(f'duration_ms 必须是整数: {exc}') from exc
        self.success = bool(self.success)
        try:
            self.attempts = max(1, int(self.attempts))
        except (TypeError, ValueError) as exc:
            raise ExperienceError(f'attempts 必须是整数: {exc}') from exc
        if self.successes is None:
            self.successes = 1 if self.success else 0
        else:
            try:
                self.successes = int(self.successes)
            except (TypeError, ValueError) as exc:
                raise ExperienceError(f'successes 必须是整数: {exc}') from exc
        self.validate()

    def validate(self) -> None:
        """校验经验合法性；非法抛 ExperienceError（ValueError 子类）。"""
        if not self.task:
            raise ExperienceError('task 不能为空')
        if not isinstance(self.steps, list):
            raise ExperienceError('steps 必须是 list[dict]')
        for step in self.steps:
            if not isinstance(step, Mapping):
                raise ExperienceError(f'每个步骤必须是 dict: {step!r}')
        if self.duration_ms < 0:
            raise ExperienceError(f'duration_ms 不能为负: {self.duration_ms}')
        if not (0 <= self.successes <= self.attempts):
            raise ExperienceError(
                f'successes({self.successes}) 必须落在 [0, attempts({self.attempts})]')

    def key(self) -> str:
        """本条经验的去重键。"""
        return make_experience_key(self.task, self.steps)

    @property
    def epoch(self) -> float:
        """timestamp 对应的 epoch 秒数。"""
        return _iso_to_epoch(self.timestamp)

    @property
    def success_rate(self) -> float:
        """累计成功率 successes / attempts。"""
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def score(self) -> float:
        """排序分：成功率为主，耗时只占 1% 以内的扰动（60s 封顶）。"""
        duration_penalty = min(max(self.duration_ms, 0), 60000) / 60000 * 0.01
        return round(self.success_rate - duration_penalty, 6)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可 JSON 化的 dict。"""
        return {
            'exp_id': self.exp_id,
            'task': self.task,
            'steps': copy.deepcopy(self.steps),
            'success': self.success,
            'duration_ms': self.duration_ms,
            'timestamp': self.timestamp,
            'params': copy.deepcopy(self.params),
            'attempts': self.attempts,
            'successes': self.successes,
            'first_timestamp': self.first_timestamp,
            'note': self.note,
        }

    @classmethod
    def from_dict(cls, data: Any) -> 'Experience':
        """从 dict 还原；缺字段用默认值，结构非法抛异常（由调用方 fail-open 处理）。"""
        if not isinstance(data, Mapping):
            raise ExperienceError(f'经验条目必须是 dict: {data!r}')
        return cls(
            task=data.get('task', ''),
            steps=copy.deepcopy(list(data.get('steps') or [])),
            success=bool(data.get('success', False)),
            duration_ms=int(data.get('duration_ms', 0) or 0),
            timestamp=data.get('timestamp') or _local_iso_ts(),
            exp_id=str(data.get('exp_id') or uuid.uuid4().hex),
            params=copy.deepcopy(dict(data.get('params') or {})),
            attempts=int(data.get('attempts', 1) or 1),
            successes=int(data['successes']) if data.get('successes') is not None else None,
            first_timestamp=data.get('first_timestamp'),
            note=str(data.get('note') or ''),
        )


# ========== ExperienceStore ==========

class ExperienceStore:
    """经验存储：JSON 持久化 + 去重合并 + 相关度查询 + Top-K 排序 + 时间窗修剪。

    去重语义：相同 (task, steps) 只保留最新一条明细（含 timestamp/duration_ms/
    success），同时累计 attempts/successes/first_timestamp。
    """

    def __init__(self, path: Optional[str] = None,
                 max_age_days: Optional[int] = None,
                 autosave: bool = True) -> None:
        """初始化并加载已有经验文件（文件损坏时 fail-open 为空库）。

        Args:
            path: 经验文件路径，默认 experience_path()（data/experience.json）。
            max_age_days: 修剪窗口（天），默认 90，可被 TVPILOT_EXP_MAX_AGE_DAYS 覆盖。
            autosave: record()/prune() 后是否立即落盘。
        """
        self.path: str = path or experience_path()
        if max_age_days is None:
            env_days = os.environ.get('TVPILOT_EXP_MAX_AGE_DAYS', '')
            max_age_days = int(env_days) if env_days.isdigit() else DEFAULT_MAX_AGE_DAYS
        self.max_age_days: int = int(max_age_days)
        self.autosave: bool = bool(autosave)
        self._lock = threading.RLock()
        self._entries: Dict[str, Experience] = {}
        self._load()

    # ---------- 基础 ----------

    def __len__(self) -> int:
        """当前经验条目数（去重后）。"""
        return len(self._entries)

    def all(self) -> List[Experience]:
        """全部经验，按时间倒序返回。"""
        with self._lock:
            return sorted(self._entries.values(), key=lambda e: -e.epoch)

    def stats(self) -> Dict[str, Any]:
        """聚合概览：条目数 / 总尝试 / 总成功 / 平均成功率 / 平均耗时。"""
        items = self.all()
        attempts = sum(e.attempts for e in items)
        successes = sum(e.successes for e in items)
        return {
            'entries': len(items),
            'attempts': attempts,
            'successes': successes,
            'success_rate': (successes / attempts) if attempts else 0.0,
            'avg_duration_ms': int(sum(e.duration_ms for e in items) / len(items)) if items else 0,
            'path': self.path,
        }

    # ---------- 记录（含去重合并）----------

    def record(self, exp: Experience) -> Experience:
        """记录一条经验；同 (task, steps) 去重合并（只留最新明细，统计累计）。

        Args:
            exp: 待记录经验。

        Returns:
            合并后真正入库的 Experience。

        Raises:
            TypeError: 入参不是 Experience。
            ExperienceError: 经验数据非法（fail-closed）。
        """
        if not isinstance(exp, Experience):
            raise TypeError(f'experience 必须是 Experience 实例，收到 {type(exp).__name__}')
        exp.validate()
        key = exp.key()
        with self._lock:
            merged = copy.deepcopy(exp)
            prev = self._entries.get(key)
            if prev is not None:
                merged.attempts = prev.attempts + exp.attempts
                merged.successes = prev.successes + exp.successes
                merged.first_timestamp = prev.first_timestamp or prev.timestamp
            else:
                merged.first_timestamp = exp.first_timestamp or exp.timestamp
            self._entries[key] = merged
            if self.autosave:
                self.save()
        return merged

    # ---------- 查询 ----------

    def query(self, task: str, limit: int = 20) -> List[Experience]:
        """按任务描述查询相关经验（子串 + 分词 + steps 文本相关度）。

        Args:
            task: 任务描述；为空表示不做过滤。
            limit: 返回上限，<=0 表示不限制。

        Returns:
            相关度倒序（同分按时间倒序）的 Experience 列表。
        """
        q_norm = _normalize_task(task)
        tokens = _TOKEN_RE.findall(q_norm)
        scored: List[Tuple[float, Experience]] = []
        with self._lock:
            candidates = list(self._entries.values())
        for exp in candidates:
            score = self._relevance(exp, q_norm, tokens)
            if score > 0:
                scored.append((score, exp))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].epoch, pair[1].task))
        hits = [exp for _, exp in scored]
        if limit and limit > 0:
            hits = hits[:int(limit)]
        return hits

    def top_k(self, task: str, k: int = 5) -> List[Experience]:
        """推荐 Top-K 经验：成功率降序优先，同率耗时升序，再按新近程度。

        Args:
            task: 任务描述；为空表示从全库排序。
            k: 返回条数。

        Returns:
            排序后的 Experience 列表，长度 <= k。
        """
        cands = self.query(task, limit=0)
        cands.sort(key=lambda e: (-e.success_rate, e.duration_ms, -e.epoch, e.task))
        return cands[:max(0, int(k))]

    @staticmethod
    def _relevance(exp: Experience, q_norm: str, tokens: Sequence[str]) -> float:
        """相关度打分：精确 100 / 互相包含 60 / 分词命中 40×比例 / steps 命中 +10。"""
        if not q_norm:
            return 1.0
        t_norm = _normalize_task(exp.task)
        score = 0.0
        if t_norm == q_norm:
            score += 100.0
        elif q_norm in t_norm or t_norm in q_norm:
            score += 60.0
        if tokens:
            hits = sum(1 for tok in tokens if tok in t_norm)
            score += 40.0 * hits / len(tokens)
        try:
            body = _normalize_steps(exp.steps).lower()
        except Exception:
            body = ''
        if q_norm and q_norm in body:
            score += 10.0
        return score

    # ---------- 修剪 ----------

    def prune(self, max_age_days: Optional[int] = None) -> int:
        """清理「最后触达时间」早于时间窗的经验。

        Args:
            max_age_days: 覆盖默认窗口（天）；默认 self.max_age_days（90 天）。

        Returns:
            被删除的条目数。
        """
        days = self.max_age_days if max_age_days is None else int(max_age_days)
        cutoff = time.time() - days * 86400.0
        with self._lock:
            stale = [key for key, exp in self._entries.items() if exp.epoch < cutoff]
            for key in stale:
                del self._entries[key]
            if stale and self.autosave:
                self.save()
        return len(stale)

    # ---------- 持久化 ----------

    def save(self) -> None:
        """原子落盘到 self.path（tmp + os.replace）；写失败只告警不抛（fail-open）。"""
        with self._lock:
            payload = {
                'version': 1,
                'saved_at': _local_iso_ts(),
                'experiences': [exp.to_dict() for exp in sorted(
                    self._entries.values(), key=lambda e: -e.epoch)],
            }
            try:
                parent = os.path.dirname(os.path.abspath(self.path))
                os.makedirs(parent, exist_ok=True)
                tmp_path = self.path + '.tmp'
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.path)
            except Exception as exc:
                print(f'[experience] 写入失败（fail-open）: {exc}', flush=True)

    def _load(self) -> None:
        """加载经验文件；文件缺失/损坏/条目非法均 fail-open。"""
        try:
            if not os.path.exists(self.path):
                return
            with open(self.path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception as exc:
            print(f'[experience] 读取失败（fail-open，按空库处理）: {exc}', flush=True)
            return
        items = payload.get('experiences') if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            print('[experience] 文件结构异常（fail-open，按空库处理）', flush=True)
            return
        for item in items:
            try:
                exp = Experience.from_dict(item)
            except Exception as exc:
                print(f'[experience] 跳过非法经验条目: {exc}', flush=True)
                continue
            self._entries[exp.key()] = exp


# ========== 步骤模板 ==========

def _resolve_value(value: Any, params: Mapping[str, Any]) -> Any:
    """递归替换步骤里的 {name} 占位符；缺参数抛 MissingParameterError（fail-closed）。"""
    if isinstance(value, str):
        def _repl(match: 're.Match[str]') -> str:
            key = match.group(1)
            if key not in params:
                raise MissingParameterError(f'缺少参数: {key}')
            return str(params[key])

        return _PLACEHOLDER_RE.sub(_repl, value)
    if isinstance(value, Mapping):
        return {k: _resolve_value(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, params) for v in value]
    return value


def _interpret(raw: Any) -> Tuple[bool, Any, Optional[str]]:
    """把执行器返回值归一化为 (ok, result, error)。

    支持三种形态：
    - tools.py 契约三元组 (data, err_code, err_msg)：err_code 为 None 即成功；
    - dict：含 'ok' 时以其为准，否则 'error' 为空即成功；
    - None / 其它标量：视为成功（fire-and-forget 步骤）。
    """
    if isinstance(raw, (tuple, list)) and len(raw) == 3:
        data, err_code, err_msg = raw
        if err_code is None:
            return True, data, None
        return False, data, (str(err_msg) if err_msg else str(err_code)) if not err_msg else f'{err_code}: {err_msg}'
    if isinstance(raw, Mapping):
        if 'ok' in raw:
            ok = bool(raw['ok'])
            return ok, raw, None if ok else (str(raw.get('error') or '步骤失败'))
        if raw.get('error'):
            return False, raw, str(raw['error'])
        return True, raw, None
    return True, raw, None


# ========== 默认执行器（对接 tools.py 工具函数）==========

_DISPATCH: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    'keyevent': ('tool_keyevent', ('key',)),
    'tool_keyevent': ('tool_keyevent', ('key',)),
    'tap': ('tool_tap', ('x', 'y')),
    'tool_tap': ('tool_tap', ('x', 'y')),
    'swipe': ('tool_swipe', ('x1', 'y1', 'x2', 'y2', 'duration_ms')),
    'tool_swipe': ('tool_swipe', ('x1', 'y1', 'x2', 'y2', 'duration_ms')),
    'wait': ('tool_wait', ('seconds',)),
    'tool_wait': ('tool_wait', ('seconds',)),
    'text': ('tool_input_text', ('text',)),
    'input_text': ('tool_input_text', ('text',)),
    'tool_input_text': ('tool_input_text', ('text',)),
    'go_home': ('tool_combo_go_home', ()),
    'combo_go_home': ('tool_combo_go_home', ()),
    'tool_combo_go_home': ('tool_combo_go_home', ()),
    'launch_app': ('tool_launch_app', ('package', 'activity')),
    'tool_launch_app': ('tool_launch_app', ('package', 'activity')),
    'zap': ('tool_zap', ('channel',)),
    'tool_zap': ('tool_zap', ('channel',)),
    'foreground': ('tool_foreground', ()),
    'tool_foreground': ('tool_foreground', ()),
}


def default_executor(step: Step) -> Any:
    """默认执行器：把经验步骤分发到 tvpilot.tools 的工具函数。

    步骤 schema（两种等价写法）：
        {"tool": "keyevent", "key": "KEYCODE_HOME"}
        {"tool": "keyevent", "params": {"key": "KEYCODE_HOME"}}

    Returns:
        tools.py 契约三元组 (data, err_code, err_msg)。

    未知工具名直接返回 ERR_INVALID_PARAMETER（不触碰任何外部副作用）。
    """
    if not isinstance(step, Mapping):
        return None, 'ERR_INVALID_PARAMETER', f'非法步骤（必须是 dict）: {step!r}'
    tool = str(step.get('tool') or step.get('action') or '').strip()
    mapping = _DISPATCH.get(tool)
    if mapping is None:
        return None, 'ERR_INVALID_PARAMETER', f'未知工具: {tool or step!r}（支持 {sorted(set(_DISPATCH))}）'

    func_name, arg_names = mapping
    src: Dict[str, Any] = dict(step)
    nested = step.get('params')
    if isinstance(nested, Mapping):
        src.update(nested)
    kwargs = {name: src[name] for name in arg_names if name in src and src[name] is not None}

    try:
        tools = importlib.import_module('tvpilot.tools')
        func = getattr(tools, func_name)
        return func(**kwargs)
    except Exception as exc:  # TypeError(缺参) / ImportError / 工具内部异常
        try:
            from tvpilot.errors import ERR_INVALID_PARAMETER  # 常量模块，惰性复用
            code = ERR_INVALID_PARAMETER
        except Exception:
            code = 'ERR_INVALID_PARAMETER'
        return None, code, f'{tool} 执行失败: {exc}'


# ========== ExperienceReplayer ==========

class ExperienceReplayer:
    """经验重放与推荐。

    replay(): 步骤模板参数化 → 逐步执行（默认 fail-closed：失败即中止）→
              结果回写 store（成败都更新统计，形成自进化）。
    suggest(): 只推荐「至少成功过一次」的经验，按 top_k 排序。
    """

    def __init__(self, store: Optional[ExperienceStore] = None,
                 executor: Optional[ExecutorType] = None,
                 record_replay: bool = True) -> None:
        """初始化重放器。

        Args:
            store: 经验库；默认新建 ExperienceStore()（读 data/experience.json）。
            executor: 步骤执行器 callable(step) -> 任意可解释返回值；默认 default_executor。
            record_replay: 重放结果是否回写 store（自进化开关）。
        """
        self.store = store if store is not None else ExperienceStore()
        self.executor = executor if executor is not None else default_executor
        self.record_replay = bool(record_replay)

    def replay(self, exp: Experience, params: Optional[Dict[str, Any]] = None,
               record: Optional[bool] = None) -> Dict[str, Any]:
        """重放一条经验。

        Args:
            exp: 待重放经验。
            params: 步骤模板参数，替换步骤里的 {name} 占位符。
            record: 覆盖 self.record_replay，决定结果是否回写 store。

        Returns:
            {'ok', 'task', 'steps_total', 'steps_run', 'results', 'duration_ms',
             'error', 'recorded'}；results 是逐步执行明细。

        失败语义（fail-closed）：缺参数一步不执行；某步失败立即中止。
        """
        t0 = time.time()
        params = dict(params or {})
        result: Dict[str, Any] = {
            'ok': False, 'task': getattr(exp, 'task', ''), 'steps_total': 0, 'steps_run': 0,
            'results': [], 'duration_ms': 0, 'error': None, 'recorded': False,
        }
        if not isinstance(exp, Experience):
            result['error'] = f'experience 必须是 Experience 实例，收到 {type(exp).__name__}'
            return result
        steps = list(exp.steps or [])
        result['steps_total'] = len(steps)
        if not steps:
            result['error'] = '经验没有可重放的步骤'
            return result

        try:
            resolved = [_resolve_value(step, params) for step in steps]
        except ReplayError as exc:
            result['error'] = f'参数解析失败（未执行任何步骤）: {exc}'
            result['duration_ms'] = int((time.time() - t0) * 1000)
            return result

        for step in resolved:
            outcome = self._run_step(step)
            result['results'].append(outcome)
            if not outcome['ok']:
                result['error'] = outcome['error']
                break
            result['steps_run'] += 1

        result['ok'] = result['steps_total'] > 0 and result['steps_run'] == result['steps_total']
        result['duration_ms'] = int((time.time() - t0) * 1000)

        should_record = self.record_replay if record is None else bool(record)
        if should_record:
            try:
                self.store.record(Experience(
                    task=exp.task,
                    steps=copy.deepcopy(exp.steps),
                    success=result['ok'],
                    duration_ms=result['duration_ms'],
                    params=params,
                    note=f'replay of {exp.exp_id}',
                ))
                result['recorded'] = True
            except Exception as exc:  # 回写失败不掩盖重放结果（fail-open）
                print(f'[experience] 重放结果回写失败（fail-open）: {exc}', flush=True)
        return result

    def _run_step(self, step: Any) -> Dict[str, Any]:
        """执行单步；执行器异常被捕获，绝不外抛。"""
        entry: Dict[str, Any] = {'step': step, 'ok': False, 'result': None, 'error': None}
        if not isinstance(step, Mapping):
            entry['error'] = f'非法步骤（必须是 dict）: {step!r}'
            return entry
        tool = str(step.get('tool') or step.get('action') or '').strip()
        if not tool:
            entry['error'] = f'步骤缺少 tool 字段: {step!r}'
            return entry
        try:
            raw = self.executor(dict(step))
        except Exception as exc:
            entry['error'] = f'{tool} 执行异常: {exc}'
            return entry
        ok, data, err = _interpret(raw)
        entry['ok'] = ok
        entry['result'] = data
        entry['error'] = err
        return entry

    def suggest(self, task: str, k: int = 3) -> List[Dict[str, Any]]:
        """推荐可复用的成功经验（只推荐至少成功过一次的）。

        Args:
            task: 任务描述。
            k: 推荐条数。

        Returns:
            [{'exp_id', 'task', 'steps', 'success_rate', 'attempts', 'successes',
              'duration_ms', 'timestamp', 'score', 'reason'}]
        """
        tips: List[Dict[str, Any]] = []
        for exp in self.store.top_k(task, k=max(k * 3, k)):
            if exp.successes <= 0:
                continue
            tips.append({
                'exp_id': exp.exp_id,
                'task': exp.task,
                'steps': copy.deepcopy(exp.steps),
                'success_rate': round(exp.success_rate, 4),
                'attempts': exp.attempts,
                'successes': exp.successes,
                'duration_ms': exp.duration_ms,
                'timestamp': exp.timestamp,
                'score': exp.score,
                'reason': (f'{exp.task}：成功率 {exp.success_rate:.0%}'
                           f'（{exp.successes}/{exp.attempts}），耗时 {exp.duration_ms}ms'),
            })
            if len(tips) >= max(0, int(k)):
                break
        return tips


# ========== ops.jsonl 桥接 ==========

def experience_from_ops(task: str,
                        records: Sequence[Mapping[str, Any]],
                        success: Optional[bool] = None) -> Experience:
    """把 ops_log.read_ops_history() 的记录桥接成一条 Experience。

    Args:
        task: 任务描述。
        records: ops_log 记录序列（含 tool/params/ok/cost_ms/ts）。
        success: 覆盖成功判定；默认 = 所有记录都 ok 且非空。

    Returns:
        组装好的 Experience（可直接交给 ExperienceStore.record()）。
    """
    steps: List[Step] = []
    duration = 0
    last_ts: Optional[str] = None
    all_ok = bool(records)
    for rec in records or []:
        rec = dict(rec)
        params = dict(rec.get('params') or {})
        step: Step = {'tool': rec.get('tool')}
        step.update(params)
        steps.append(step)
        duration += int(rec.get('cost_ms') or 0)
        all_ok = all_ok and bool(rec.get('ok'))
        if rec.get('ts'):
            last_ts = str(rec['ts'])
    return Experience(
        task=task,
        steps=steps,
        success=all_ok if success is None else bool(success),
        duration_ms=duration,
        timestamp=last_ts or _local_iso_ts(),
    )
