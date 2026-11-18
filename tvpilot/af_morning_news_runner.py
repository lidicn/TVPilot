# -*- coding: utf-8 -*-
"""af_morning_news 真机 runner：把编排 runner 协议映射到 TVPilot 既有 ADB 工具。

契约 v3 §1.1 / v2.2 §四；2026-11-16 出资人 A 窗口备料（纯离线改码，零真机副作用）。

设计约束
--------
* **fail-closed 默认**：本模块顶层不 import 任何会连设备的 tvpilot 子模块；所有设备
  动作经方法内延迟导入。是否注入本 runner 由 ``mcp.py`` 的 ``TVPILOT_MN_RUNNER=adb``
  开关决定（默认 off ⇒ MorningNewsOrchestrator 用 _NoRunner，任何设备动作抛
  DeviceActionBlocked，编排兜底落 wake_fail）。本机离线验证 / 未授权调用绝不碰设备。
* **唤醒诚实**：容器无电源唤醒实现（miIO token 未下发、无 WoL/插座通道，见链主
  17:5x 窗口口径）。``wake()`` 只做「电视是否已在线/亮屏」校验，不发送电源动作；
  息屏 ⇒ 返回 False ⇒ 编排如实落 wake_fail，⛔ 假装唤醒成功。需唤醒时由出资人手动。
* **S4 证据强度（链主验收卡定死）**：台号取自机顶盒 ``LastchannelID`` / 本地频道缓存
  是「上次记录」，不是「正在播」证据。``read_frame()`` 单次调用内连续采集 S3(前台)
  + S4(频道) + 播放旁证，共用同一 checked_at；无「正在播」旁证时把 ``Frame.reason``
  置为 ``playing_unverified``，使 decide_iptv_ok 拒绝 iptv_ok（如实落 iptv_fail），
  ⛔ 拿记录冒充已播。LastchannelID 仅作留证，不参与同帧判定。
* **不改契约**：本文件不增删 result 六值、不改 Frame 字段、不改回执键形；PARTIAL 是
  验收卡判据，不进编排 result。screen_on_after 一期仍恒 null（G6 二期）。
"""
from __future__ import annotations

import time

from tvpilot.af_morning_news import Frame, s7_state

IPTV_PKG = 'com.android.smart.terminal.iptv'
CCTV1_NO = 21


class ADBRunner:
    """MorningNewsOrchestrator 的真机 runner（duck typing，方法见编排 L145-152）。

    只在本类被显式注入、且电视/ADB 可达时才产生真机动作；所有失败如实返回
    False / 带 reason 的 Frame，⛔ 不伪装成功、⛔ 不把异常冒泡成设备上误判。
    """

    def __init__(self, screen_evidence=None, livepath_reader=None):
        # 可选硬旁证注入口（截屏 / UI 树判定「正在播」）；None ⇒ 用 play_state 弱旁证
        self.screen_evidence = screen_evidence
        # 2026-11-17：换台后读 logcat 文本的接口（供 livePath channel_id 解台号）。
        # 注入式，离线测试可喂假 logcat；None ⇒ 默认走 _read_livepath_logcat()。
        self.livepath_reader = livepath_reader
        self.last_evidence = {}   # 每次采集的原始读数，供验收窗留证

    @staticmethod
    def _read_livepath_logcat():
        """默认 livePath 读取器：抓保留的最近 logcat，按 CTCMediaPlayer 过滤。

        ⚠ livePath 只在换台瞬间输出；编排里 read_frame 紧接 zap 之后调用，此时
        该行应仍在环形缓冲中。若已被冲掉 ⇒ 解不出 channel_id ⇒ S4 unknown+reason
        （fail-closed，⛔ 不回退到不可信的 LastchannelID/缓存）。
        """
        try:
            from tvpilot import adb
            return adb.adb_shell_output('logcat -b all -d -t 2000 | grep -F "livePath" | tail -5',
                                        timeout=10) or ''
        except Exception:
            return ''

    # 1. 唤醒：只校验已在线/亮屏，⛔ 不做电源唤醒
    def wake(self):
        try:
            from tvpilot import adb, state
            online = bool(adb.tv_online())
            scr = state.get_screen_state() or {}
            on = scr.get('screen_on')
            self.last_evidence['wake'] = {
                'adb_online': online, 'screen_on': on,
                'wakefulness': scr.get('wakefulness'),
                'power_action': 'none',  # 一期不实现电源唤醒，如实留痕
            }
            # 已在线即视为「就绪」（不触发任何电源动作）；息屏则如实返回 False
            return bool(online and (on is not False))
        except Exception as exc:
            self.last_evidence['wake'] = {'error': type(exc).__name__}
            return False

    # 2. ADB 四元探针：接 af_probe.classify_adb_health 做病因分型
    def probe_adb(self):
        from tvpilot import adb, af_probe, state
        quad = {'last_checked': time.time()}
        try:
            quad['network_reachable'] = bool(adb.tv_online())
            out = adb.adb_shell_output('pidof adbd', timeout=5) or ''
            quad['adbd_alive'] = 'running' if out.strip() else 'stopped'
            s = (adb.adb_shell_output('settings get global adb_enabled', timeout=5) or '').strip()
            quad['adb_enabled_setting'] = s or None
        except Exception as exc:
            quad['probe_error'] = type(exc).__name__
        power = 'on'
        try:
            scr = state.get_screen_state() or {}
            if scr.get('wakefulness') == 'Asleep':
                power = 'standby'
        except Exception:
            pass
        res = af_probe.classify_adb_health(quad, power=power)
        out = dict(res)
        out['network_reachable'] = quad.get('network_reachable')
        self.last_evidence['probe_adb'] = out
        return out

    # 3. IPTV 全屏：复用 tool_launch_app（monkey 起 LAUNCHER）
    def launch_iptv(self):
        from tvpilot import tools
        data, err, msg = tools.tool_launch_app(IPTV_PKG)
        ok = (err is None and msg is None and bool((data or {}).get('launched')))
        self.last_evidence['launch_iptv'] = {
            'ok': ok, 'err': err, 'foreground': (data or {}).get('foreground'),
        }
        return ok

    # 4. 换台：复用 tool_zap（逐位数字键注入 + 两级播放页守卫）
    def zap(self, channel_no):
        from tvpilot import tools
        data, err, msg = tools.tool_zap(str(channel_no))
        ok = (err is None and msg is None)
        self.last_evidence['zap'] = {
            'channel_no': channel_no, 'ok': ok, 'err': err,
            'method': (data or {}).get('method'),
        }
        return ok

    # 5. 单次调用内连续采集 S3/S4/S7 + 播放旁证（同帧近似，共用 checked_at）
    def read_frame(self):
        """2026-11-17 修复：S4 台号改由换台瞬间 livePath 的 channel_id 反解（可信），
        ⛔ 不再用 LastchannelID（多位数只写首位）/play_state 缓存（旧值）冒充现值。
        全屏/正在播旁证仍需成立；两者缺一 ⇒ Frame.reason 非空 ⇒ decide_iptv_ok 拒绝
        冒充 iptv_ok（fail-closed）。"""
        from tvpilot import state, iptv
        checked = time.time()
        try:
            fg = state.get_foreground_pkg(force_refresh=True)        # S3
            ch = state.get_current_channel() or {}                   # 缓存，仅留证不判据
            play, perr, _ = state.tool_play_state()
            playing = bool(perr is None and (play or {}).get('playing'))
            if self.screen_evidence is not None:
                try:
                    playing = bool(self.screen_evidence())
                except Exception:
                    playing = False

            # S4 可信台号：换台瞬间 livePath → channel_id → 台号
            reader = self.livepath_reader or self._read_livepath_logcat
            try:
                logcat_text = reader()
            except Exception:
                logcat_text = ''
            s4_no, s4_reason, cid = iptv.resolve_channel_from_livepath(logcat_text)

            last_id = None
            try:
                last_id = iptv.get_current_channel_from_logcat()      # 仅旁证/留痕
            except Exception:
                pass
            self.last_evidence['read_frame'] = {
                's3_foreground_app': fg,
                's4_trusted_no': s4_no, 's4_reason': s4_reason, 'channel_id': cid,
                's4_cached_name': ch.get('name'), 's4_cached_no': ch.get('no'),
                'lastchannel_id': last_id,          # 已知不可信，仅留证
                'playing_witness': playing,
                'witness_kind': ('screen_evidence' if self.screen_evidence is not None
                                 else 'play_state'),
                'checked_at': checked,
            }
            # 我正在播 + 可信台号两者都要；缺一即 reason 非空 → 拒绝冒充 iptv_ok
            if not playing:
                reason = 'playing_unverified'
            elif s4_no is None:
                reason = s4_reason or 'channel_unresolved'
            elif s4_no != CCTV1_NO:
                reason = 's4_not_cctv1'
            else:
                reason = None
            return Frame(
                foreground_app=fg,
                current_channel=ch.get('name') if s4_no == CCTV1_NO else None,
                same_frame=True,          # 单次调用内采集（非跨帧）
                checked_at=checked,
                reason=reason,           # 无「正在播」旁证/台号不可信 ⇒ 拒绝 iptv_ok
                tvdesk_login_state=s7_state(checked),
            )
        except Exception as exc:
            self.last_evidence['read_frame'] = {'error': type(exc).__name__}
            return Frame(reason='capture_failed', checked_at=checked)

    # 6. 失败处置回读（G6 二期用；一期编排不调用，screen_on_after 恒 null）
    def screen_on(self):
        from tvpilot import state
        try:
            return bool((state.get_screen_state() or {}).get('screen_on'))
        except Exception:
            return False

    # 7. 可注入时钟（编排 _settle_s4 用自身 sleeper，此处保留协议完整）
    def sleep(self, seconds):
        time.sleep(seconds)
