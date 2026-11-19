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

import os
import time

from tvpilot.af_morning_news import Frame, s7_state

IPTV_PKG = 'com.android.smart.terminal.iptv'
CCTV1_NO = 21
# 2026-11-19 链主改判：PID 过滤留作备用、默认关。今晚三轮全栽在「读不到日志」，
# 不赌「RTSP 行 100% 出自 IPTV 主进程」（tag 是 IPTVPlayer，但 [Send:400] 可能是 helper）。
# 第一次运行走全量流（30MB/32万行容器可承受、Python 解析 1-2s），记 bytes/lines/parse_ms；
# 只有实测证明体量/解析拖慢，第二次运行才置 TVPILOT_LIVEPATH_PID_FILTER=1 切 --pid。
LIVEPATH_PID_FILTER = os.environ.get('TVPILOT_LIVEPATH_PID_FILTER', '0') == '1'


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
        self._livepath_text = ''  # 2026-11-18：zap 成功后立刻抓的 logcat，供 read_frame 复用
        self._livepath_ms = 0.0   # 2026-11-19：reader 耗时(ms)，写进 evidence 报代价
        # 2026-11-19 第五笔：流式 logcat 会话句柄（Popen + 临时文件）。起流在 logcat -c
        # 之后、注入之前；收流在 zap 返回后。None ⇒ 未起流（注入式 reader/起流失败），
        # 收流退回默认快照 reader（fail-closed 由解析保证）。
        self._livepath_stream = None
        self._stream_was_used = False    # 本次是否走了真实流（evidence 留证）
        # 链主改判报数三量：全量流的体积/行数/解析耗时（初轮体量实测留证）
        self._stream_bytes = 0
        self._stream_lines = 0
        self._parse_ms = 0.0

    # 2026-11-19 第四笔（Chief 实测修正）：设备端过滤、压回传量。
    #  · 第三笔 `logcat -b all -d` 全文真机 15s/20s 拉不回 → 空串 → livepath_absent。
    #  · 链主原方案 `-b main` 实测 0 行——RTSP 行不在 main buffer（在 all/system），
    #    故退回 `-b all`；`-b all -t 3000|grep` 实测 716 字节/8.89s 能抓到。
    #  · zap 前 `logcat -c` 清缓冲 ⇒ 清后窗口小、必含本次换台行，用 -t 1500 足够。
    LIVEPATH_CMD = "logcat -b all -t 1500 | grep -E -i -e 'rtsp://' -e PLTV"
    LIVEPATH_CLEAR_CMD = 'logcat -c'
    LIVEPATH_TIMEOUT_S = 15
    # 2026-11-19 第五笔（Chief 实测三修）：独立 logcat 流 + --pid 过滤 + 动态窗口。
    # 起流 logcat -c 后、注入前（此时 launch 已完成、pidof 取得到 IPTV PID）；收流在
    # zap 返回后。总窗口 = max(TOTAL, zap耗时 + TAIL_MIN)——取代早先固定 25s 总窗
    # （守卫满 20s 时收尾被压到 5s 的问题），保证收尾至少 10s。STOP_WAIT 是 terminate→kill 宽限。
    STREAM_CMD_TAIL = ('logcat', '-b', 'all', '-v', 'threadtime')
    STREAM_TOTAL_WINDOW_S = 25
    STREAM_TAIL_MIN_S = 10
    STREAM_WINDOW_CAP_S = 60      # Chief 复核补：窗口上限，防 zap 异常拖长顶破 240s 预算
    STREAM_STOP_WAIT_S = 2

    def _read_livepath_logcat(self):
        """默认 livePath 读取器（2026-11-19 第四笔，Chief 实测定参）。

        - 命令 `logcat -b all -t 1500 | grep -E -i -e 'rtsp://' -e PLTV`，timeout 15s：
          `-b all`（不用 main——RTSP 行不在 main buffer）、设备端过滤只回几行、
          `-t 1500` 限窗；配合 zap 前的 `logcat -c` 清缓冲，必含本次换台行。
        - 代价记到 self._livepath_ms（毫秒）：evidence 报 ms/lines/chars 三数，
          ⛔ 不只报「有没有」。状态记到 self._livepath_status（按异常类型分，不按耗时猜）：
          TimeoutExpired⇒'reader_timeout'、其它异常⇒'reader_error:<type>'、正常返回⇒''（
          此时若空则由 zap 标 'reader_empty(no_match)'）。⛔ 不按 ms 阈值猜 timeout，
          避免 8.89s 空匹配被误标超时（Chain/Chief 定）。空串⇒S4 fail-closed。
        """
        import subprocess
        from tvpilot import adb
        t0 = time.time()
        self._livepath_ms = 0.0
        self._livepath_status = ''
        try:
            out = adb.adb_shell_output(self.LIVEPATH_CMD, timeout=self.LIVEPATH_TIMEOUT_S) or ''
            self._livepath_ms = round((time.time() - t0) * 1000, 1)
            return out
        except subprocess.TimeoutExpired:
            self._livepath_ms = round((time.time() - t0) * 1000, 1)
            self._livepath_status = 'reader_timeout'
            return ''
        except Exception as exc:
            self._livepath_ms = round((time.time() - t0) * 1000, 1)
            self._livepath_status = 'reader_error:%s' % type(exc).__name__
            return ''

    # 2026-11-19 第五笔：设备端 pidof（Chief 实测）——全量 logcat 12.7k 行/秒，
    # 25s 会写 30 万行/30MB；按 IPTV PID 过滤可小两个数量级。pidof 必须在设备端跑
    # （容器内无该进程）。取不到 PID ⇒ 回退不带 --pid（全量，靠收尾时限兜底）。
    def _iptv_pid(self):
        try:
            from tvpilot import adb
            out = adb.adb_shell_output('pidof %s' % IPTV_PKG, timeout=5) or ''
            for tok in out.split():
                if tok.isdigit():
                    return tok
        except Exception:
            pass
        return None

    def _gather_livepath_text(self):
        """第五笔统一取文本口：真流已起 ⇒ 收流读文件；否则退回注入 reader/快照 reader。

        fake reader 离线测试期没有真流（_start_livepath_stream 返回 False、_livepath_stream
        为 None），走 livepath_reader ⇒ D6/D7 的「zap 一次 + read_frame 再抓一次」语义不变。
        """
        if self._livepath_stream is not None:
            self._stream_was_used = True
            return self._collect_livepath_stream()
        reader = self.livepath_reader or self._read_livepath_logcat
        return reader() or ''

    def _start_livepath_stream(self):
        """第五笔：logcat -c 之后、tool_zap 之前起独立 logcat 流。

        * 独立 ``subprocess.Popen(['adb','-s',<serial>,'logcat','-b','all','-v','threadtime',
          '--pid=<pid>'])``，⛔ 不塞 `_run_shell_persistent`（持久 shell 会串行阻塞守卫的
          dumpsys 及其余 adb 命令）。
        * stdout 重定向到容器内临时文件，收完再整文件读回解析（⛔ 不边读边解析）。
        * 注入式 livepath_reader 存在时**不起流**（离线测试/假 reader 用原语义：收流阶段
          调 fake reader），保证既有 D1-D16 断言不破。
        返回 True 表示真流已起；False 表示未起（fake reader 或起流失败）。
        """
        if self.livepath_reader is not None:
            return False
        import subprocess
        import tempfile
        from tvpilot import adb
        path = None
        fh = None
        try:
            tmp = tempfile.NamedTemporaryFile(prefix='tvpilot_livepath_', suffix='.log',
                                              delete=False)
            path = tmp.name
            tmp.close()
            argv = list(adb.ADB) + list(self.STREAM_CMD_TAIL)
            # 链主改判：默认全量流（不过滤），PID 过滤仅 env 开启时启用、代码保留备用。
            pid = self._iptv_pid() if LIVEPATH_PID_FILTER else None
            if LIVEPATH_PID_FILTER and pid:
                argv.append('--pid=%s' % pid)
            fh = open(path, 'wb')
            proc = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT)
            self._livepath_stream = {'proc': proc, 'fh': fh, 'path': path,
                                     't0': time.time(), 'pid': pid}
            self._livepath_pid = pid
            return True
        except Exception:
            # 起流失败 ⇒ 清理句柄/临时文件，收流退回默认快照 reader（fail-closed 由解析保证）
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
            if path is not None:
                try:
                    os.remove(path)
                except Exception:
                    pass
            self._livepath_stream = None
            return False

    def _abort_livepath_stream(self):
        """第五笔：无条件收掉在途真实流（zap 失败/重入时调用），⛔ 不留残留占 adb 通道。"""
        s = self._livepath_stream
        if s is None:
            return
        self._livepath_stream = None
        try:
            s['proc'].terminate()
            try:
                s['proc'].wait(timeout=self.STREAM_STOP_WAIT_S)
            except Exception:
                s['proc'].kill()
                s['proc'].wait(timeout=1)
        except Exception:
            pass
        try:
            s['fh'].close()
        except Exception:
            pass
        try:
            os.remove(s['path'])
        except Exception:
            pass

    def _collect_livepath_stream(self):
        """第五笔：zap 返回后收流——动态窗口等待 → 可靠 terminate/kill → 读文件解析。

        动态窗口 = max(25s, 起流至今耗时 + 10s)，保证守卫满 20s 时收尾仍有 ≥10s。
        fail-closed 不变：读回文本无 URL ⇒ resolve 返回 livepath_absent。
        返回 logcat 文本（含 URL 行则 resolve_channel_from_livepath 能解 cid）。
        """
        s = self._livepath_stream
        if s is None:
            return ''
        self._livepath_stream = None
        proc, fh, path, t0 = s['proc'], s['fh'], s['path'], s['t0']
        try:
            elapsed = time.time() - t0
            # 动态窗口 max(25, elapsed+10)，但封顶 60s——zap 异常拖长时也不顶破 240s 预算
            window = min(max(self.STREAM_TOTAL_WINDOW_S, elapsed + self.STREAM_TAIL_MIN_S),
                         self.STREAM_WINDOW_CAP_S)
            remain = max(0.0, window - elapsed)
            time.sleep(remain)
        finally:
            # 可靠收尾：terminate→wait→kill，绝不留下占 adb 通道的 logcat 进程
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=self.STREAM_STOP_WAIT_S)
                except Exception:
                    proc.kill()
                    proc.wait(timeout=1)
            except Exception:
                pass
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass
        try:
            with open(path, 'rb') as f:
                raw = f.read()
            self._stream_bytes = len(raw)
            self._stream_lines = raw.count(b'\n')
            out = raw.decode('utf-8', errors='replace')
        except Exception:
            out = ''
            self._stream_bytes = 0
            self._stream_lines = 0
        finally:
            try:
                os.remove(path)
            except Exception:
                pass
        self._livepath_ms = round((time.time() - t0) * 1000, 1)
        return out

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
        """2026-11-19 链主修法①②：`network_reachable` 改真网络探针（ICMP ping），
        ⛔ 不再用 adb.tv_online()（ADB 关时必 false，分不出「ADB 关」vs「网断」）。
        ADB 不通但网通 ⇒ setting 未读，明确置 adbd_alive='stopped'／setting=None 交给
        classify 走 setting_unreadable/etiology_undetermined，由编排前置判 adb_off_tv_on。
        """
        from tvpilot import adb, af_probe, state
        quad = {'last_checked': time.time()}
        # 真网络探针（不依赖 adb）：ping 通电视 IP 才算网通
        net_ok = bool(adb.network_ping())
        quad['network_reachable'] = net_ok
        adb_ok = bool(adb.tv_online())
        if adb_ok:
            try:
                out = adb.adb_shell_output('pidof adbd', timeout=5) or ''
                quad['adbd_alive'] = 'running' if out.strip() else 'stopped'
                s = (adb.adb_shell_output('settings get global adb_enabled', timeout=5) or '').strip()
                quad['adb_enabled_setting'] = s or None
            except Exception as exc:
                quad['probe_error'] = type(exc).__name__
        else:
            # ADB 不通：Setting 读不到，如实留空；网通+ADB断 ≠ 网断（后者 net_ok=False）
            quad['adbd_alive'] = 'stopped'
            quad['adb_enabled_setting'] = None
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
        out['adb_reachable'] = adb_ok          # 与 network_reachable 分开留证，便于区分病因
        self.last_evidence['probe_adb'] = out
        return out

    # 3. IPTV 全屏：复用 tool_launch_app（monkey 起 LAUNCHER）
    def launch_iptv(self):
        """⑧ 短路（2026-11-19）：语义是「确保 app 在前台」。若前台已是 IPTV_PKG，
        直接 ok(method=already)、⛔ 不调 monkey——monkey 对已在前台的 app 不发
        `Events injected: 1`，会假失败（playing 与否都短路，播不播是后续步骤的事）。"""
        from tvpilot import state, tools
        fg = state.get_foreground_pkg(force_refresh=True)
        if fg and IPTV_PKG in fg:
            self.last_evidence['launch_iptv'] = {
                'ok': True, 'method': 'already', 'foreground': fg,
            }
            return True
        data, err, msg = tools.tool_launch_app(IPTV_PKG)
        ok = (err is None and msg is None and bool((data or {}).get('launched')))
        self.last_evidence['launch_iptv'] = {
            'ok': ok, 'err': err, 'foreground': (data or {}).get('foreground'),
            'method': (data or {}).get('method'),
        }
        return ok

    # 4. 换台：复用 tool_zap（逐位数字键注入 + 两级播放页守卫）
    def zap(self, channel_no):
        import subprocess
        from tvpilot import tools, iptv
        # 第五笔重入保护：上一次若异常中断留有在途真实流，先无条件收掉，⛔ 不让残留
        # logcat 进程占 adb 通道（Chief 硬要求）。
        self._abort_livepath_stream()
        # 2026-11-19 第四笔（Chief 建议）：注入前清 logcat 缓冲，保证随后的
        # livePath 抓取窗口必含本次换台行（URL 行流动、几分钟就被冲掉）。
        # 清缓冲是只读运维动作、不改变电视状态；失败也不阻断换台。
        try:
            from tvpilot import adb
            adb.adb_cmd(self.LIVEPATH_CLEAR_CMD)
        except Exception:
            pass
        # 第五笔：logcat -c 之后、注入之前起独立 logcat 流（覆盖守卫等待期+注入）。
        # 真流在 _collect_livepath_stream 里收；fake reader 模式本调用返回 False、不走流。
        self._start_livepath_stream()
        # Chief 复核补：tool_zap 若抛异常，真流已成孤儿（L343 无 try → _abort 不执行，
        # logcat 进程+临时文件留到下次 zap/重启）。异常路径先无条件收流再原样上抛。
        try:
            data, err, msg = tools.tool_zap(str(channel_no))
        except Exception:
            self._abort_livepath_stream()
            raise
        ok = (err is None and msg is None)
        self.last_evidence['zap'] = {
            'channel_no': channel_no, 'ok': ok, 'err': err,
            'method': (data or {}).get('method'),
        }
        # 2026-11-18 第三笔：换台成功后**立刻**抓 logcat 全文存下，缩小 RTSP URL
        # 行被播放日志冲出的窗口（原实现到 read_frame 才抓、已过 11s 被冲掉）。
        # ★ 链主复核补：logcat -d 全文必非空，「不空」不等于「有 URL 行」——只有
        #   真的解出 channel_id 才认这份缓存；解不出就留空，让 read_frame 现抓兜底，
        #   ⛔ 否则静默 FAIL（缓存看似有、实则无 URL）。
        self._livepath_text = ''
        if ok:
            txt = ''
            cid = None
            # 2026-11-19：reader 前重置代价/状态（注入式 fake reader 可能不写它们）
            self._livepath_ms = 0.0
            self._livepath_status = ''
            self._stream_bytes = 0
            self._stream_lines = 0
            self._parse_ms = 0.0
            try:
                txt = self._gather_livepath_text()
                _t_parse = time.time()
                _no, _r, cid = iptv.resolve_channel_from_livepath(txt)
                self._parse_ms = round((time.time() - _t_parse) * 1000, 1)
            except subprocess.TimeoutExpired:
                self._livepath_status = 'reader_timeout'
                cid = None
            except Exception:
                cid = None
            self._livepath_text = txt if cid else ''
            # 链主要求：evidence 带 ms/lines/chars 三数，⛔ 不只报有没有
            self.last_evidence['zap']['livepath_ms'] = self._livepath_ms
            self.last_evidence['zap']['livepath_lines'] = len([l for l in txt.splitlines() if l.strip()])
            self.last_evidence['zap']['livepath_chars'] = len(txt)
            self.last_evidence['zap']['livepath_cid'] = cid
            # 仍空 ⇒ 按异常类型分状态（不按耗时猜）：
            #   TimeoutExpired⇒reader_timeout、其它异常⇒reader_error、正常空⇒reader_empty(no_match)
            # fail-closed 的 S4 reason 由 resolve 给，这里只是证据措辞，别误标误导复盘。
            if not txt:
                self.last_evidence['zap']['livepath_reader_status'] = (
                    self._livepath_status or 'reader_empty(no_match)')
            # 第五笔留证：抓取载体/动态窗口/PID——成对报数时一并贴，便于复盘
            self.last_evidence['zap']['livepath_reader_kind'] = (
                'stream' if self._stream_was_used else 'snapshot')
            self.last_evidence['zap']['livepath_pid'] = getattr(self, '_livepath_pid', None)
            self.last_evidence['zap']['livepath_pid_filter'] = LIVEPATH_PID_FILTER
            # 链主改判：全量流首轮的体量/解析耗时三数——证明确没拖慢才决定要不要切 --pid
            self.last_evidence['zap']['stream_bytes'] = self._stream_bytes
            self.last_evidence['zap']['stream_lines'] = self._stream_lines
            self.last_evidence['zap']['parse_ms'] = self._parse_ms
        else:
            # zap 失败：流已起但未收 ⇒ 无条件收掉，⛔ 不留残留占 adb 通道
            self._abort_livepath_stream()
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

            # S4 可信台号：优先用 zap 成功后立刻抓的 logcat（窗口最小），
            # 没有才现抓兜底（如 ADB-off 腿未换台的情形）。
            logcat_text = self._livepath_text
            if not logcat_text:
                try:
                    logcat_text = self._gather_livepath_text() or ''
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
                # 第五笔 ③ 成对报数：cid→台号 与物理现场（LastchannelID/play_state/前台）
                # 同帧同 key 一次贴出，⛔ 不再只报一半（上次据此无法定性只写首位 vs 陈旧行）。
                'paired_report': {
                    'cid': cid, 'cid_to_no': s4_no, 's4_reason': s4_reason,
                    'lastchannel_id': last_id, 'play_state_playing': playing,
                    'foreground_app': fg,
                },
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
