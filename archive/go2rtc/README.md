# go2rtc 换台链路 — 隔离归档（2026-11-12）

> 状态：**已停用 / 整体隔离 / 代码原样保留 / 可复活**。⛔ 不删、不参与主服务 import、不留 shim。
> 归档卡：AF-PM 2026-11-12；执行：TVPilot。复活步骤见 §4，边界表见 §5。

## 1. 为什么归档

用户 11-12 口径：「go2rtc 换台那套已经没在使用了，现在直接用电信 IPTV，adb 换台」；
「哪个 go2rtc 那套，隔离出来归档，以后可能会继续开发」。

- **现用链路（保留）**：`tv_zap` → `tvpilot/tools.py:tool_zap` → `channel_no_of()` →
  `zap_via_adb_digits()` → `adb shell input keyevent KEYCODE_<n> … KEYCODE_ENTER`，
  前台目标 `com.android.smart.terminal.iptv`（电信 IPTV）。**与 go2rtc / RTSP 无关**。
- **停用链路（本目录）**：go2rtc `:1984` 把电信 RTSP 流转 HLS/RTSP 给 `com.tvcam.mytv` 播放器，
  外加「抓 logcat RTSP → 提取 accountinfo token → 批量改 go2rtc.yaml → `docker cp` + `docker restart go2rtc`」后台刷新链。
  `docker.sock` 早已拔除，刷新链实际只产 `data/go2rtc_refreshed.yaml`、流不生效（历史 G1 未解决）。
- 归档动因：两代链路在仓里以「活代码／归档／文档／死代码」四态混放，`/api/health` 同时报
  `go2rtc_creds_configured` 与 `iptv_source_present`，是「分不清」的结构性原因（见 `doc/RPT-全功能实测-20261112.md` §1）。

## 2. 目录内容（全部原样搬入）

| 文件 | 原位置 | 内容 |
|---|---|---|
| `server_rtsp_refresh.py` | `tvpilot_server.py:188-401` | `_go2rtc_opener()` + `_do_refresh_rtsp()` 整段（起 IPTV → 清 logcat → 切台号 1 → 抓 `CTCMediaPlayer` RTSP → 解析 accountinfo → 拉 `/api/streams` → 生成 yaml → docker cp/restart → 无 sock 降级） |
| `tvpilot_legacy.py` | `tvpilot/legacy.py` | `GO2RTC_*` / `MYTV_HTTP` 常量、`go2rtc_basic_auth_header()`、`warmup()`、`proxy_mytv()`（旧 mytv HTTP 代理） |
| `routes_legacy.py` | `tvpilot/routes/legacy.py` | 旧版兼容 GET 分发：`/health`、`/channels`、`/active`、`/zap`、`/screenshot`、`/fongmi/search`、`/keyevent`、`/tap` |
| `scripts/check_cameras.sh` | 仓根 | 一次性：读 `go2rtc.yaml.bak.*` 找非 iptv 流 |
| `scripts/patch_cameras_m3u.py` | 仓根 | 一次性：给 `tvpilot_server.py` 注入摄像头 M3U（缺 `GO2RTC_AUTH` 即 `RuntimeError`） |
| `scripts/restore_cameras.py` | 仓根 | 一次性：往 `go2rtc.yaml` 写米家摄像头流 + xiaomi 鉴权（缺 `XIAOMI_*` 即 `SystemExit`） |
| `.env.example` | 仓根 `.env.example:17` | `GO2RTC_*` 键占位模板 + 复活所需 RTSP 端点键 |

> **行尾说明**：搬入的 `.py` / `.sh` 按仓规统一为 LF / 无 BOM；源 `tvpilot_server.py` 为 CRLF+BOM，
> 仅做行尾归一化，**代码逐字未改**（可用 `git show HEAD:tvpilot_server.py` 抽 188-401 行比对）。
> `server_rtsp_refresh.py` 是**片段**不是可独立运行模块：它引用 `adb_cmd` / `IPTV_PKG` / `_refresh_rtsp_state` 等主服务全局名，
> 只做语法编译校验，不 import、不执行。

## 2.1 逐文件文件头说明（AF-PM 11-12 裁定 ② 补齐）

**为何说明集中在本 README、而不写进每个归档文件的头部**：用户 11-12 决定「go2rtc 那套**代码原样保留不删**」，
`6c0a54b` 的 5 个搬移已验收为 `R100` 纯重命名。若逐个文件插入注释头，会破坏「代码逐字未改」与 R100，
故三项要素以**集中式文件头说明**落在本节，逐文件一一对应，效果等同、且不触碰历史实现。

| 要素 | 值 |
|---|---|
| **来源 commit** | 归档动作 `6c0a54b chore(archive): go2rtc 换台链路隔离归档`；其父提交 `6c0a54b^` 为「归档前」基线。各文件在父提交中的原位置见 §2 表格第 2 列 |
| **归档原因** | 用户 11-12：「go2rtc 换台那套已停用，现用电信 IPTV + ADB 数字键换台」「隔离出来归档、专门设文件夹存档，以后可能继续开发」；两代链路四态混放、`/api/health` 并列报两代字段，是「分不清」的结构性原因（详见 §1） |
| **当前主链路替代物** | `tv_zap` → `tvpilot/tools.py:tool_zap` → `channel_no_of()` → `zap_via_adb_digits()` → `adb shell input keyevent KEYCODE_<n> … KEYCODE_ENTER`，前台目标 `com.android.smart.terminal.iptv`；RTSP 刷新端点 `POST /api/iptv/refresh_rtsp` 与 `GET /api/iptv/refresh_status` 均保留为 **`410 Gone`** 显式路标，不再有实现 |

| 归档文件 | 来源 commit 中原位置 | 归档原因（对应链路） | 现用替代物 |
|---|---|---|---|
| `server_rtsp_refresh.py` | `tvpilot_server.py:188-401` | RTSP 刷新链（抓 logcat → 改 go2rtc.yaml → docker cp/restart），sock 已拔、流不生效 | `410 Gone` 路标 + 电信 IPTV 直连，无刷新链 |
| `tvpilot_legacy.py` | `tvpilot/legacy.py` | go2rtc 预热/鉴权 + 旧 mytv HTTP 代理 | ADB 数字键换台，不再经 mytv / go2rtc |
| `routes_legacy.py` | `tvpilot/routes/legacy.py` | 旧版兼容 GET 分发（`/zap` 等 8 条） | `tvpilot/routes/deprecated.py` 统一 410 包络 |
| `scripts/check_cameras.sh` | 仓根 | 一次性摄像头流核查 | 无（现用链路不涉及摄像头 M3U） |
| `scripts/patch_cameras_m3u.py` | 仓根 | 一次性注入摄像头 M3U | 无 |
| `scripts/restore_cameras.py` | 仓根 | 一次性写回摄像头流 + xiaomi 鉴权 | 无 |
| `.env.example` | 仓根 `.env.example:17` | `GO2RTC_*` 键占位 | 仓根 `.env.example` 7 键（`DOUBAO_API_KEY` / `MITV_*` / `XIAOMI_*`） |

> 边界：`git grep -in go2rtc -- tvpilot/ tvpilot_server.py` 归档前 **54** 行 → 归档后 **0** 行；
> 归档后全仓 `*.py` 命中只在 `archive/go2rtc/`（53 行）与防回流闸/证据脚本（20 行，属「指向归档的引用」），
> 产品活面彻底清零。防回流闸 `tests/test_deprecated_surface.py` 7 例保证不回流。

## 3. 复活依赖（不含在本目录，需另备）

| 依赖 | 说明 |
|---|---|
| go2rtc 容器 `:1984` | 需在 `192.168.2.200` 服役，能读 `/config/go2rtc.yaml` |
| 配置热重载 | 拔 sock 后 TVPilot 不能再 `docker restart go2rtc`；需 go2rtc 侧支持 watch / HTTP API / SIGHUP（历史 G1 未解决，见 `doc/跨项目协调清单-20260921.md`） |
| `docker.sock` | 要恢复 `docker cp` + `docker restart` 须重新挂载。**安全 P0-3 风险**：等于给 TVPilot 整机容器控制权，默认不建议（见 `doc/审计-生态链体检-20260921.md` §3） |
| mytv 播放器 + `MYTV_HTTP` | `proxy_mytv` 目标 `192.168.2.238:10482`；现用链路已不用 mytv |
| `GO2RTC_BASE_URL` / `GO2RTC_USER` / `GO2RTC_PASS` | Basic Auth 凭据，env 注入，凭据零打印 |
| `data/gd_iptv_source.m3u` | 97 条 M3U 中历史内嵌明文 `user:pass@192.168.2.200:1984`（存量）。复活前须按 AF-PM 窗口轮换 / 去凭据 |
| `channels.json` / `data/channel_numbers.json` | 80 频道 / 60 台号；旧口径 `count=97` 与现用 `count=80` 的差异见 §5 |

## 4. 复活步骤（仅在 AF-PM + 用户明确点头后；会改主服务）

1. `tvpilot_legacy.py` → 放回 `tvpilot/legacy.py`；`routes_legacy.py` → 放回 `tvpilot/routes/legacy.py`。
2. `tvpilot_server.py` 恢复两处 import：`from tvpilot.legacy import (...)`、
   `from tvpilot.routes.legacy import handle_legacy_get`，并把 `handle_legacy_get` 加回 `GET_HANDLERS`。
3. 把 `server_rtsp_refresh.py` 的两函数整段放回 `tvpilot_server.py` 原 `:188-401` 位置；
   `tvpilot/config.py` 只需补回本单摘掉的 `GO2RTC_BASE` / `GO2RTC_AUTH_USER` /
   `GO2RTC_AUTH_PASS` / `GW_ACTIVE` 四项（含 `__all__` 条目）——
   `ADMIN_TOKEN` / `DEVICE_TOKEN` / `REFRESH_OPEN` / `REFRESH_MIN_INTERVAL` / `DOCKER_SOCK` /
   `WAKE_LOCAL` / `_refresh_rtsp_state` 等 RTSP 刷新常量按「归档保留」注释**原样留在 config.py**，
   主服务只是不再 import 它们，复活时可直接复用。
4. `tvpilot_server.py` 的 `POST /api/iptv/refresh_rtsp` 与 `do_POST` 的 `/api/*` 兜底、
   `tvpilot/routes/iptv.py` 的 `/api/iptv/refresh_status` 从 `410 Gone` 换回原实现。
5. `tvpilot/health.py` 若确实需要 `go2rtc_creds_configured` 字段再恢复（默认不建议，避免两代链路并列误导）。
6. `.env.example` 的 `GO2RTC_AUTH` 从本目录 `.env.example` 移回仓根，填真值（不入库、不打印）。
7. `docker-compose.yml` 按 §3 决策决定是否挂回 `docker.sock`；`restart` 取舍另按规范 §2-7（`on-failure:5`）。
8. 跑验收：`pytest tests/test_deprecated_surface.py -q` 此时**应当失败**（防回流闸会报 go2rtc 回流）——
   这是预期信号，按新口径改写该闸；真机复测 `/api/health`、`/api/channels`、换台 6 抽后再开部署窗口。

⚠️ 复活是**把代码搬回主服务**，不是把本目录加进 `sys.path`；归档目录永远不参与 import。

## 5. 与现用链路的边界表

| 面 | 现用（保留） | 归档（本目录） |
|---|---|---|
| 换台 | ADB 数字键注入 `zap_via_adb_digits` | go2rtc 流切换 / mytv HTTP `/api/zap` |
| 前台包 | `com.android.smart.terminal.iptv` | `com.tvcam.mytv`（播放器） |
| 直播源 | 电信 IPTV 机顶盒 + `gd_iptv_source.m3u` 代理 | go2rtc `:1984` RTSP→HLS/RTSP 转码 |
| 频道数口径 | `/api/channels count=80`（channels.json）、`zappable_count=60`、`m3u_entry_count=97` | `count=97`（M3U 条目）、`go2rtc_count=80`（旧名，已弃用） |
| health 字段 | `iptv_source_present` / `channels_count=80` | `go2rtc_creds_configured`（已删） |
| RTSP 刷新端点 | `POST /api/iptv/refresh_rtsp` → `410 Gone`、状态查询同 | 起线程跑 `_do_refresh_rtsp` |
| 旧版兼容 GET | 主服务不再注册（随 `routes_legacy.py` 归档） | `/health /channels /active /zap /screenshot /fongmi/search /keyevent /tap` |
| `/api/*` POST 兜底 | `410 Gone`（旧 mytv HTTP 代理兜底摘除） | `proxy_mytv()` 代理到 `:10482` |
| 配置键 | `DOUBAO_API_KEY` / `MITV_*`（仓根 `.env.example`） | `GO2RTC_*`（本目录 `.env.example`） |
| 一次性脚本 | 仓根不再保留 | `scripts/` 三个脚本（原样保留） |

## 6. 历史文档索引（doc/ 只索引、不改）

以下为 `doc/**/*.md` 中含 `go2rtc` 字样的历史文档，均为决策 / 整改 / 实测记录，
**按纪律原样保留、不改内容**。派单卡记「19 份」；现读全仓为 **21 份 .md**，全列于此：

| # | 文档 | 记的内容 |
|---|---|---|
| 1 | `doc/archive/电信IPTV_RTSP地址抓取流程.md` | RTSP 抓取 → 生成 go2rtc.yaml → 部署重启全流程（最完整） |
| 2 | `doc/TVPilot_项目交接文档_20260909.md` | 交接时的 go2rtc 容器 / 97 频道 / 认证表 |
| 3 | `doc/审计-生态链体检-20260921.md` | P0-3 docker.sock 风险、明文口令 S1–S8 |
| 4 | `doc/DEV日志/回执-TP-20260921-003.md` | `docker cp/restart` 报错被吞、静默报成功 |
| 5 | `doc/DEV日志/回执-TP-20260921-006.md` | go2rtc Basic Auth 假阳性核查 |
| 6 | `doc/DEV日志/回执-TV-20260921-TV-103.md` | 口令走 env / yaml 写 data 卷 |
| 7 | `doc/跨项目协调清单-20260921.md` | G1 拔 sock 后配置重载方案（未解决） |
| 8 | `doc/RPT-生态链体检整改-20260921.md` | 97/60/80 三值口径、health 字段、去凭据整改 |
| 9 | `doc/跨项目对接清单-20260922.md` | 给 go2rtc 维护者的热重载诉求 |
| 10 | `doc/DEV日志/部署回执-20260922-0.8.1上线.md` | 0.8.1 上线时 go2rtc 凭据与环境段 |
| 11 | `doc/RPT-opencode审计修复-20260922.md` | MV-1：`last_success` 假阳性修复 |
| 12 | `doc/ROADMAP_进度核实_20260922.md` | 配置热重载待办核实 |
| 13 | `doc/已知bug清单-20260922.md` | 写盘 `except: pass`、无 sock 重启方案 |
| 14 | `doc/v1.0_整合联调清单.md` | v0.2 / 97 频道音频修复联调 |
| 15 | `doc/ROADMAP.md` | 口令 env 化 / yaml 落 data 卷 / 热重载路线 |
| 16 | `doc/RPT-模块化重构阶段1-20260923.md` | `legacy.py` 归档不删（用户「万一哪天要用」） |
| 17 | `doc/RPT-模块化重构收尾-20260924.md` | 目录树与归档常量清单 |
| 18 | `doc/审计-全面审计-20260924.md` | RA-08 常量分叉、MQ-20 三口径混乱 |
| 19 | `doc/路线图_TVPilot_v1.0.md` | v0.7 / T10 摄像头集成 / go2rtc 状态 |
| 20 | `doc/RPT-现状盘点-20261111.md` | 11-11 现状：明文口令 8 类 111 处、首提交洗密 |
| 21 | `doc/RPT-全功能实测-20261112.md` | 11-12 全功能实测：go2rtc 命中面定级、B3 复测结论（本次只加 §8 一节） |

> 另有 `doc/snap_tvpilot_server_20260921T0815Z.py`、`backup/tvpilot_server.676ccbaa_20260921.py`
> 等历史快照（非 `.md`），含旧版明文凭据，按 11-11 洗密口径单独处理，本归档单不碰。
