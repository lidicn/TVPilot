# archive/ — 已停用链路隔离区

本目录只放**已停用、整体隔离、代码原样保留、可复活**的链路。
目录**不参与主服务 import**，主服务对旧调用端点一律返 `410 Gone`：

```json
{"ok": false, "error": "deprecated", "see": "archive/README.md"}
```

| 链路 | 目录 | 停用日期 | 现用替代 |
|---|---|---|---|
| go2rtc 换台 / RTSP 刷新 | [go2rtc/README.md](go2rtc/README.md) | 2026-11-12 | 电信 IPTV（`com.android.smart.terminal.iptv`）+ ADB 数字键换台 |

> 归档卡：AF-PM 2026-11-12；执行：TVPilot。用户口径：「隔离出来归档，以后可能会继续开发」——**不删、不改历史代码**。
> 纪律：本目录任何 `.py` 都不得被 `tvpilot/**` 或 `tvpilot_server.py` import；复活必须把代码放回主服务，而不是把本目录加进 `sys.path`。
