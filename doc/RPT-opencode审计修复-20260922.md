# RPT · opencode 20260922 审计 TVPilot 功能性/稳定性修复
> 状态（2026-11-12 整理）：**历史记录**。本文记录当时的事实，不代表当前状态；现行口径见 `doc/README.md` 真源优先级表。

- **日期**：2026-09-22
- **基线**：0.8.1（`bf0732c3…` / 149158B）→ 本次修后 **`f23bf991…` / 154938B**
- **范围**：仅 `E:\NAS\TVPilot\tvpilot_server.py` 内影响功能性/稳定性的 bug。安全类（路径遍历、命令注入、全 API 面无鉴权、bind、人脸端点鉴权）按交接约定**本轮不动**，留待安全批次。
- **备份**：`backup/tvpilot_server.0.8.1_20260922.py`（改前已存）；原版 `676ccbaa` 快照保留未动。
- **生效性**：本表所有改动都是盘上 `tvpilot_server.py`。该文件由 `Dockerfile:5 COPY` 进镜像，**改盘上 ≠ 运行容器生效**；当前跑的仍是旧码，**需重建窗口**才上线（且重建前必须先对容器可写层热修做快照，红线不变：不自行 up/restart/build）。

## 已修清单

| 编号 | 级别 | 问题（审计出处） | 盘上锚点 / 改法 | 需窗口 |
|---|---|---|---|---|
| HV-1 | High | 换台"拉前台-验证"组合动作无互斥，语音/MQTT 两路 HOME+am start 互相打断，偶发换台失败/误判 wrong_foreground（B3-HV-1） | 新增全局 `_FG_LOCK=threading.RLock()`；`ensure_mytv_foreground` 改为薄 wrapper，`with _FG_LOCK:` 内调原函数（改名 `_ensure_mytv_foreground_inner`），函数体不动 | 是 |
| HV-2 | High | `tool_wait` 在 HTTP 请求线程同步 sleep 上限 30s，长占 worker、放大 ADB RLock 排队（B3-HV-2） | 上限 `min(seconds,30)` → `min(seconds,10)`（落在管家 HTTP 10s 超时内） | 是 |
| HV-3 | Med | EPG 缓存命中只回 `'cache'`，content 与 source/时间可能不一致，前端无法判时效（B3-HV-3） | 缓存命中 source 改 `cache@<epoch>`；两个 EPG 路由加响应头 `X-Cache-Ts` | 是 |
| MV-1 | Med | 无 docker.sock 时只产出 yaml 不重启 go2rtc，却仍置 `last_success=True`，调用方误判链路已恢复（B3-MV-1） | 该分支 `last_success=bool(docker_used)`，新增 `requires_reload` 标记，日志写明"流尚未生效" | 是 |
| MV-2 | Med | MQTT 换台去重 check-and-set 非原子，双线程下可能重复执行或漏判（B3-MV-2） | 新增 `_DEDUP_LOCK`，判断+写 `_last_action` 包进同一把锁；publish/入队留锁外 | 是 |
| MV-3 | Med | 截图走 POST tool（非 png 直出）时 result 含 `png_bytes`，`json.dumps(bytes)` 抛 TypeError（B3-MV-3） | 新增 `_json_safe()` 递归把 bytes→base64，`make_response` 出口统一清洗（GET `/api/screenshot?fmt=png` 仍走 `_send_png` 直发二进制，不受影响） | 是 |
| MV-4 | Med | `dumpsys audio` 音量正则 `(speaker):\s*` 对固件空白写法敏感，取不到音量（B3-MV-4） | 正则放宽为 `\(\s*speaker\s*\)\s*:\s*(\d+)`，hdmi_arc 同理 | 是 |
| MV-5 | Med | a11y scroll 只认 forward/backward，传 up/down 被本服务直接拒（B3-MV-5） | 合法集合扩为 forward/backward/up/down/left/right，透传 | 是 |
| LV-3 | Low | M3U 一次性 `readlines()` 无大小上限，上游膨胀会 OOM（B3-LV-3） | 读前 `getsize`，>50MB 拒绝读入并记日志，lines=[] | 是 |
| LV-4 | Low | ops.jsonl `write` 后无 flush/fsync，kill -9 丢末行（B3-LV-4） | 每行写后 `flush()+os.fsync()`（审计存档低频，开销可忽略） | 是 |

## 验证

- `py_compile` 通过；本地 stub paho 内存 import 冒烟全绿：
  - 新符号 `_FG_LOCK/_DEDUP_LOCK/_json_safe/_ensure_mytv_foreground_inner` 均就位；
  - `tool_wait(99)` 被夹到 `10.0`；
  - `_json_safe({'a':b'\x01\x02'})` 正确转 base64；
  - `tool_a11y_scroll('left')` 不再返回 invalid_parameter。

## 本轮明确不做 / 已决策项

- **安全类**（B2-TV-01 `/remote-static` 路径遍历、B2-TV-02 ADB shell 注入、B2-TV-07 整 API 面无鉴权、HI-55 bind 0.0.0.0、HI-56 `/api/face/backup|restore` 无鉴权）：按"只修功能/稳定性"交接约定，留待安全批次。人脸/备份端点建议下一批统一接 `TVPILOT_ADMIN_TOKEN`。
- **CR-13 剩余明文**在仓库脚本/文档（`kill_8090.sh`、`patch_cameras_m3u.py`、`restore_cameras.py`、`data/dbg.py`、`doc/MA接口标准_v1.0…:208`），非 `tvpilot_server.py`；本轮未动，列入凭据轮换批次。
- **hdmi3_monitor/stats/alerts 死模块**：HDMI3 时长已切 memory-agent 通道，TVPilot 侧按先前决策保留开关、不再扩功能，不在本批。
- **A-TV-04（文档承诺 hdmi3/stats/alerts 端点缺失）**：数据源已迁移，非功能回归问题，不改。

## 待 PM/出资人排期

全部 10 项需**重建窗口**才生效；重建前先对容器可写层 `docker cp` 热修那份做快照。
