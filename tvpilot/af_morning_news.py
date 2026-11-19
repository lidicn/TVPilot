# -*- coding: utf-8 -*-
"""AF ↔ TVPilot 联动 · D3 早间新闻全链编排（一期纯 A 版 / 契约 v2.2）。

设计约束（2026-11-15 AF-PM 放行「离线范围」）：
  * 纯 A 链路：唤醒 → ADB 探测 → IPTV 全屏 → 中央一台 → result。一期无 faceid / tvdesk / ArcFace 段。
  * 命令面 = HA `button.tv_morning_news`（MQTT），编排内聚本模块内部函数；
    ⛔ 不做 `POST /tv/morning_news`（契约 §十三 一期范围外）。
  * 对外只提供 `last_run()` 只读快照，供排障端点 `GET /af/v1/morning_news/last` 消费；
    **本模块不挂 HTTP 路由**（路由文件正被并行会话编辑，挂载另行派卡）。
  * 所有真机动作经 ``runner`` 注入；runner=None 时任何设备动作 raise DeviceActionBlocked，
    保证离线判定矩阵零真机副作用。⛔ 本模块不连 MQTT、不读凭据、不碰设备、不改已有模块。

v2.2（AF-PM 2026-11-15 裁定）：追加 `iptv_fail`，IPTV 启动失败／选台失败／
  同帧判据不通过三路径共用一值（⛔ 不拆三值）；S3/S4 如实、读不到即 unknown+reason。
  原 CONTRACT_GAPS[0] 已闭合，保留为 RESOLVED_GAPS 记档。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

# ---- 契约常量（v2.2 §二 / §三 / §四） ----
IPTV_PKG = 'com.android.smart.terminal.iptv'
CCTV1_NAME = '中央一台'
CCTV1_NO = 21
BUDGET_S = 240.0
S4_SETTLE_MAX_S = 6.0          # AGENTS.md：/api/current 换台后 LastchannelID 最长约 6s 写回
S4_POLL_INTERVAL_S = 1.0

IPTV_OK = 'iptv_ok'
ADB_OFF_TV_ON = 'adb_off_tv_on'
WAKE_FAIL = 'wake_fail'
IPTV_FAIL = 'iptv_fail'           # v2.2 追加：三路径共用
FACEID_TIMEOUT = 'faceid_timeout'   # 一期休眠（枚举保留）
ARCFACE_FAIL = 'arcface_fail'       # 一期休眠（枚举保留）
RESULTS = (IPTV_OK, ADB_OFF_TV_ON, WAKE_FAIL, IPTV_FAIL,
           FACEID_TIMEOUT, ARCFACE_FAIL)
ACTIVE_RESULTS = (IPTV_OK, ADB_OFF_TV_ON, WAKE_FAIL, IPTV_FAIL)
DORMANT_RESULTS = (FACEID_TIMEOUT, ARCFACE_FAIL)

# v2.2 §二：iptv_fail 的 AF 话术（TVPilot 只发准 result，话术由 AF 侧定稿）。
AF_SPEECH = {
    IPTV_OK: '新闻已打开',
    ADB_OFF_TV_ON: '电视已打开；ADB 未开启，新闻请打开 tvdesk 手动观看',
    WAKE_FAIL: '电视唤醒失败',
    IPTV_FAIL: '电视已打开，新闻没能自动打开，请手动观看',
    FACEID_TIMEOUT: None,   # 一期休眠；二期静默不播报
    ARCFACE_FAIL: None,
}

STAGE_IDLE = 'idle'
STAGE_WAKING = 'waking'
STAGE_PROBING = 'probing'
STAGE_IPTV_LAUNCHING = 'iptv_launching'
STAGE_CHANNEL_SETTING = 'channel_setting'
STAGE_DONE = 'done'
STAGE_FAILED = 'failed'
# 一期不使用的三个 stage（枚举保留，二期 faceid 支线复用）
RESERVED_STAGES = ('arcface_ensuring', 'tvdesk_opening', 'faceid_waiting')
ALL_STAGES = (STAGE_IDLE, STAGE_WAKING, STAGE_PROBING) + RESERVED_STAGES + (
    STAGE_IPTV_LAUNCHING, STAGE_CHANNEL_SETTING, STAGE_DONE, STAGE_FAILED)

# result -> stage。默认取值 + 回退口径：adb_off_tv_on 记 done（降级终态、电视已亮，
# 非失败）；iptv_fail 记 failed（v2.2 追加，三路径共用）；若 AF-PM 要改只需改本表一行。
RESULT_TO_STAGE = {
    IPTV_OK: STAGE_DONE,
    ADB_OFF_TV_ON: STAGE_DONE,
    WAKE_FAIL: STAGE_FAILED,
    IPTV_FAIL: STAGE_FAILED,
    FACEID_TIMEOUT: STAGE_FAILED,
    ARCFACE_FAIL: STAGE_FAILED,
}

S7_UNKNOWN = {
    'value': 'unknown',
    'reason': 'not_applicable_iptv_native',
}


class DeviceActionBlocked(RuntimeError):
    """离线模式：runner 未注入时禁止任何真机动作。"""


class ContractGap(Exception):
    """契约缺口占位（v2.2 已无活跃缺口，保留供二期新增缺口时停手报裁用）。

    v2.2 之前用于「纯 A 失败路径无诚实可落值」；AF-PM 追加 `iptv_fail` 后闭合，
    见模块末 RESOLVED_GAPS。run() 的兜底分支仍会把它落成 iptv_fail，⛔ 不悬挂。
    """


@dataclass
class Frame:
    """S3 / S4 / S7 的一帧快照。same_frame=False 表示三格不是同一次采集。"""
    foreground_app: str | None = None      # S3
    current_channel: str | None = None     # S4
    tvdesk_login_state: dict = field(default_factory=lambda: dict(S7_UNKNOWN))
    same_frame: bool = True
    checked_at: float = 0.0
    reason: str | None = None              # capture_failed / timeout / ...

    def s3_is_iptv(self) -> bool:
        return (self.foreground_app or '').lower() in (IPTV_PKG, 'iptv')

    def s4_is_cctv1(self) -> bool:
        return (self.current_channel or '') in (CCTV1_NAME, 'cctv1', 'CCTV1', str(CCTV1_NO))


def decide_iptv_ok(frame: Frame | None) -> tuple[bool, str | None]:
    """§四 状态一致性：S3=IPTV ∧ S4=中央一台 ∧ 同帧，**三者都成立**才允许 iptv_ok。

    ⛔ 猜：frame 缺失、跨帧、读不到、或任一一格不符，一律拒绝并给出 reason。
    """
    if frame is None:
        return False, 'frame_missing'
    if frame.reason:
        return False, frame.reason
    if not frame.same_frame:
        return False, 's3s4_not_same_frame'
    if not frame.s3_is_iptv():
        return False, 's3_not_iptv'
    if not frame.s4_is_cctv1():
        return False, 's4_not_cctv1'
    return True, None


def s7_state(checked_at: float | None = None) -> dict:
    """一期 S7 恒定 unknown(not_applicable_iptv_native)，不参与同帧一致性。"""
    d = dict(S7_UNKNOWN)
    d['last_checked'] = checked_at if checked_at is not None else time.time()
    return d


class _NoRunner:
    def __getattr__(self, _name):
        def _blocked(*_a, **_kw):
            raise DeviceActionBlocked('runner=None：离线模式禁止真机动作')
        return _blocked


class MorningNewsOrchestrator:
    """一次编排一个实例；幂等、终态必达、steps 全留痕。

    runner 协议（duck typing，全部由调用方注入）：
        wake() -> bool                      唤醒电视（miIO → WoL → 插座）
        probe_adb() -> dict                 四元，至少含 network_reachable / value
        launch_iptv() -> bool               am start IPTV 全屏
        zap(channel_no:int) -> bool         数字键换台
        read_frame() -> Frame               同帧采 S3/S4/S7
        screen_on() -> bool                 失败处置回读（电视是否仍亮）
        sleep(seconds)                      可注入时钟
    publisher 协议：publish(topic:str, payload:dict) -> None；默认只记录不真发。
    """

    def __init__(self, runner=None, publisher=None, clock=time.time, sleeper=time.sleep):
        self.runner = runner if runner is not None else _NoRunner()
        self._clock = clock
        self._sleep = sleeper
        self._pub_log: list[dict] = []
        self._publisher = publisher or (lambda topic, payload: self._pub_log.append(
            {'topic': topic, 'payload': payload}))
        self._lock = threading.Lock()
        self._running = False
        self._last: dict = {}
        self._steps: list[dict] = []

    # ---- 对外只读面（供 GET /af/v1/morning_news/last） ----
    def last_run(self) -> dict:
        with self._lock:
            return dict(self._last)

    def published(self) -> list[dict]:
        return list(self._pub_log)

    # ---- 内部 ----
    def _step(self, name: str, status: str, evidence: dict | None = None):
        self._steps.append({'step': name, 'status': status,
                            'evidence': evidence or {}, 'at': self._clock()})

    def _set_stage(self, stage: str):
        self._publish('stage', {'value': stage, 'last_checked': self._clock()})

    def _publish(self, kind: str, payload: dict):
        topic = 'tvpilot_tv/%s/state' % kind
        self._publisher(topic, payload)

    def _publish_result_states(self, frame: Frame | None, reason: str | None) -> dict:
        """§四：result=iptv_ok / iptv_fail 时 S3/S4 如实发布。

        读得到且无异常 ⇒ 发实际值；读不到 / 超时 / 跨帧 ⇒ ``unknown``＋reason，
        ⛔ 猜、⛔ 把未达成冒充成 iptv_ok。
        """
        checked = self._clock()
        frame_reason = reason or (frame.reason if frame else None)
        if frame is not None and frame.same_frame is False and frame_reason is None:
            frame_reason = 's3s4_not_same_frame'

        def _one(value):
            if value is not None and frame_reason is None:
                return {'value': value, 'last_checked': checked}
            return {'value': 'unknown',
                    'reason': frame_reason or 'capture_failed',
                    'last_checked': checked}

        s3 = _one(frame.foreground_app if frame else None)
        s4 = _one(frame.current_channel if frame else None)
        self._publisher('tvpilot_tv/tv_foreground_app/state', s3)
        self._publisher('tvpilot_tv/tv_current_channel/state', s4)
        return {'s3_tv_foreground_app': s3, 's4_tv_current_channel': s4}

    def _terminal(self, result: str, *, gap: str | None = None,
                  detail: str | None = None, frame: Frame | None = None,
                  frame_reason: str | None = None) -> dict:
        stage = RESULT_TO_STAGE[result]
        self._set_stage(stage)
        states = self._publish_result_states(frame, frame_reason) \
            if result in (IPTV_OK, IPTV_FAIL) else {}
        speech = AF_SPEECH.get(result)
        rec = {
            'result': result,
            'stage': stage,
            'gap': gap,
            'detail': detail,
            'af_speech': speech,
            's3_s4': states,
            'screen_on_after': None,
            'steps': list(self._steps),
            'total_ms': None,
        }
        # AF 按 result 边沿播报；话术随边沿带出，便于验收逐字比对
        self._publisher('tvpilot_tv/morning_news_result/state',
                        {'value': result, 'af_speech': speech,
                         'last_checked': self._clock()})
        with self._lock:
            self._last = rec
            self._running = False
        return rec

    def trigger(self, request_id: str | None = None) -> dict:
        """button 按下入口。执行中重复按 ⇒ 忽略（自选的幂等口径），result 仍终态必达。"""
        with self._lock:
            if self._running:
                return {'accepted': False, 'reason': 'already_running',
                        'request_id': request_id, 'last': dict(self._last)}
            self._running = True
            self._steps = []
        return {'accepted': True, 'request_id': request_id}

    def run(self, request_id: str | None = None) -> dict:
        """执行全链。**任何路径（含异常）都必落终态**，⛔ 悬挂。"""
        ack = self.trigger(request_id)
        if not ack['accepted']:
            return ack
        t0 = self._clock()
        self._set_stage(STAGE_IDLE)
        try:
            return self._run_chain(t0)
        except DeviceActionBlocked as e:
            self._step('device_action_blocked', 'error', {'message': str(e)})
            return self._finish(self._terminal(WAKE_FAIL, detail='device_action_blocked'), t0)
        except ContractGap as e:
            # v2.2 后正常路径不再抛；残留缺口也不悬挂，如实落 iptv_fail。
            self._step('contract_gap', 'error', {'message': str(e)})
            return self._finish(self._terminal(
                IPTV_FAIL, detail='contract_gap', frame_reason='contract_gap'), t0)
        except Exception as e:  # noqa: BLE001 - 终态必达要求兜住一切
            self._step('unexpected', 'error', {'type': type(e).__name__, 'message': str(e)})
            return self._finish(self._terminal(WAKE_FAIL, detail='unexpected_error'), t0)

    def _finish(self, rec: dict, t0: float) -> dict:
        rec['total_ms'] = round((self._clock() - t0) * 1000, 2)
        with self._lock:
            self._last = rec
        return rec

    # ---- 纯 A 主链 ----
    def _run_chain(self, t0: float) -> dict:
        r = self.runner
        # 1. 唤醒（失败处置：电视保持开着 ⛔ 不关回 —— 编排末尾不追加关回动作）
        self._set_stage(STAGE_WAKING)
        if not r.wake():
            self._step('wake', 'failed', {'channels': 'miIO/WoL/outlet'})
            # 2026-11-19 链主修法②③：wake 失败先探明是不是「ADB 关但网通」——
            # 是则如实落 adb_off_tv_on（四元可区分病因），⛔ 遮成 wake_fail。
            # wake 依赖 adb.tv_online()，电视把 ADB 开关关掉时必 false，原先在此直接
            # 终态 wake_fail 导致 probe 永远到不了、adb_off_tv_on 结构上出不来。
            _wp = r.probe_adb() or {}
            self._step('probe_adb', 'ok', {'quad': _wp})
            if (_wp.get('value') == 'off' or
                    (_wp.get('adb_reachable') is False and _wp.get('network_reachable') is True)):
                return self._finish(self._terminal(
                    ADB_OFF_TV_ON, detail='adb_unavailable'), t0)
            # 真·唤醒失败（网断/电视关机）⇒ 维持 wake_fail；四元 reason 已如实留在
            # probe_adb step（network_unreachable / tv_off 不遮成同一个），⛔ 不冒充 ADB 关。
            return self._finish(self._terminal(WAKE_FAIL, detail='all_wake_channels_failed'), t0)
        self._step('wake', 'ok')

        # 2. ADB 四元探测（只在编排时调用，⛔ 周期探测 / 契约 §五）
        self._set_stage(STAGE_PROBING)
        quad = r.probe_adb() or {}
        self._step('probe_adb', 'ok', {'quad': quad})
        # 2026-11-19 Chief 复核：network_reachable 现在是 ICMP，语义≠ADB；能走到这里
        # wake 已成功、ADB 必通，⛔ 再用 ping 当 ADB 判据——否则 ping 抖一下就把正常
        # 腿误降级成 adb_off_tv_on（新探针造假降级）。正常路径的 ADB 判据用 adb_reachable。
        if quad.get('value') == 'off' or quad.get('adb_reachable') is False:
            # 降级终态：电视已亮、ADB 不可用、新闻未执行。AF 播降级话术、⛔ 不关回。
            return self._finish(self._terminal(ADB_OFF_TV_ON, detail='adb_unavailable'), t0)

        # 3. IPTV 全屏（一期无 ArcFace / tvdesk / faceid 步）
        self._set_stage(STAGE_IPTV_LAUNCHING)
        if not r.launch_iptv():
            self._step('launch_iptv', 'failed')
            # v2.2：三路径共用 iptv_fail；S3/S4 未取得 ⇒ unknown+capture_failed
            return self._finish(self._terminal(
                IPTV_FAIL, detail='iptv_launch_failed',
                frame_reason='capture_failed'), t0)
        self._step('launch_iptv', 'ok')

        # 4. 选台 CCTV1
        self._set_stage(STAGE_CHANNEL_SETTING)
        if not r.zap(CCTV1_NO):
            self._step('zap', 'failed', {'channel_no': CCTV1_NO})
            return self._finish(self._terminal(
                IPTV_FAIL, detail='zap_failed', frame_reason='capture_failed'), t0)
        # 观测小改（2026-11-19）：把 runner 采集的流式读数摊进 step evidence，只加键，
        # ⛔ 不改六值/status 序列/话术。fake runner 无 last_evidence ⇒ 走空字典，离线矩阵不回退。
        _zap_ev = dict(getattr(r, 'last_evidence', {}).get('zap', {}))
        # 成功路径 reader 正常且已解出 cid ⇒ 状态真值为 ok（runner 仅在空串时写该键）
        _zap_ev.setdefault('livepath_reader_status', 'ok')
        _zap_ev['channel_no'] = CCTV1_NO
        self._step('zap', 'ok', _zap_ev)

        # 5. S4 慢一拍：等 STB 写回并交叉确认，⛔ 拿换台后立刻读到的值当判据
        frame = self._settle_s4()
        ok, reason = decide_iptv_ok(frame)
        if not ok:
            self._step('verify_frame', 'failed', {'reason': reason})
            # v2.2：同帧判据不通过 ⇒ iptv_fail；S3/S4 如实（读到的值带 reason，
            # 读不到即 unknown+reason），⛔ 冒充 iptv_ok
            return self._finish(self._terminal(
                IPTV_FAIL, detail='frame_verify_failed(%s)' % reason,
                frame=frame, frame_reason=reason), t0)
        # 观测小改：把 read_frame 的 s4_reason/paired_report 等真值一并摊入（只加键）
        _vf_ev = dict(getattr(r, 'last_evidence', {}).get('read_frame', {}))
        _vf_ev.update({
            's3': frame.foreground_app, 's4': frame.current_channel,
            'same_frame': frame.same_frame, 'checked_at': frame.checked_at})
        self._step('verify_frame', 'ok', _vf_ev)

        # 6. iptv_ok：S3/S4 同帧已验证；S7 如实 unknown(not_applicable_iptv_native)
        self._publisher('tvpilot_tv/tvdesk_login_state/state', s7_state(self._clock()))
        return self._finish(self._terminal(IPTV_OK, frame=frame), t0)

    def _settle_s4(self) -> Frame:
        """轮询等 STB `LastchannelID` 写回，最多 S4_SETTLE_MAX_S；超时返回带 reason 的 Frame。

        跨帧（same_frame=False）是**同帧判据不通过**，不是「还没写回」，
        立即带 s3s4_not_same_frame 返回，⛔ 不轮询到超时把它吞成 timeout
        （契约 v2.2 §四：iptv_fail 的 evidence 要如实区分失败原因）。
        """
        deadline = self._clock() + S4_SETTLE_MAX_S
        last = None
        while True:
            last = self.runner.read_frame()
            if last is None:
                return Frame(reason='capture_failed', checked_at=self._clock())
            if last.reason:
                return last
            if last.same_frame is False:
                return Frame(foreground_app=last.foreground_app,
                             current_channel=last.current_channel,
                             same_frame=False, checked_at=self._clock(),
                             reason='s3s4_not_same_frame')
            if last.s3_is_iptv() and last.s4_is_cctv1():
                return last
            if self._clock() >= deadline:
                f = Frame(foreground_app=last.foreground_app,
                          current_channel=last.current_channel,
                          same_frame=last.same_frame, checked_at=self._clock(),
                          reason='timeout')
                return f
            self._sleep(min(S4_POLL_INTERVAL_S, max(0.0, deadline - self._clock())))


# v2.2 已闭合的缺口记档（⛔ 不再自行变更接口形状/枚举）
RESOLVED_GAPS = [
    {
        'gap': '纯 A 失败路径无诚实可落 result',
        'resolution': 'AF-PM 2026-11-15 裁定追加 `iptv_fail`（契约 v2.2）：'
                      'IPTV 启动失败／选台失败／同帧判据不通过三路径共用一值，'
                      'steps 带失败步 evidence，S3/S4 如实、读不到即 unknown+reason。',
        'result': IPTV_FAIL,
        'stage': RESULT_TO_STAGE[IPTV_FAIL],
        'af_speech': AF_SPEECH[IPTV_FAIL],
        'owner': 'AF-PM',
    },
]
CONTRACT_GAPS = []     # 无活跃缺口；保留名字兼容既有 import


__all__ = [
    'IPTV_PKG', 'CCTV1_NAME', 'CCTV1_NO', 'BUDGET_S', 'S4_SETTLE_MAX_S',
    'IPTV_FAIL', 'AF_SPEECH', 'RESULTS', 'ACTIVE_RESULTS', 'DORMANT_RESULTS',
    'ALL_STAGES', 'RESERVED_STAGES', 'RESULT_TO_STAGE', 'S7_UNKNOWN',
    'Frame', 'decide_iptv_ok', 's7_state', 'MorningNewsOrchestrator',
    'ContractGap', 'DeviceActionBlocked', 'RESOLVED_GAPS', 'CONTRACT_GAPS',
]
