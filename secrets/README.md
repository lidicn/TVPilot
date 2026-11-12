# secrets/

ADB 私钥运行时挂载目录（本目录整体被 .gitignore 排除，绝不入库）。

- 把已授权的 `adbkey` / `adbkey.pub` 放到本目录
- `docker-compose.yml` 把它们只读挂到 `/root/.android/`
- Dockerfile 只 `mkdir -p /root/.android && chmod 700`，镜像内不含任何密钥
- 凭据零打印：不要在别处复制或回显私钥内容

密钥目前仍在仓根 `E:\NAS\TVPilot\adbkey`（已被 gitignore 排除）。
部署前由在场人员手工放入本目录；本 Bot 不搬运、不读取、不打印其内容。

## 环境变量（.env）必填项（AF-PM 11-11 裁定）

仓根 `.env.example` 的 8 个键**全部必填**（本轮新增 7 项）；缺任一项，
对应脚本/服务 fail-fast 报错退出，不以空值静默运行：

| 键名 | 消费方 | 缺失后果 |
|---|---|---|
| `DOUBAO_API_KEY` | `docker-compose.yml` → `tvpilot/config.py` / `vision.py` | env 为空，多模态分析不可用 |
| `MITV_API_KEY` | `tvpilot/mitv.py` / `tvpilot/screenshot.py` | 6095 签名 token 为空 |
| `MITV_HMAC_KEY` | 同上 | HMAC 签名失败 |
| `MITV_PLAY_SALT` | `tvpilot/mitv.py` | 播放签名失败 |
| `GO2RTC_AUTH` | `patch_cameras_m3u.py` 注入的 `generate_camera_m3u()` | 明确 `RuntimeError`，拒绝生成无凭据相机 M3U |
| `XIAOMI_UID` | `restore_cameras.py` | `SystemExit`，拒绝生成错误相机表 |
| `XIAOMI_TOKEN` | `restore_cameras.py` | 同上 |
| `XIAOMI_AUTH` | `restore_cameras.py` | 同上 |

> **状态（2026-11-12，AF-PM 裁定，加一行、不删历史段落）**：上表 `GO2RTC_AUTH`、`XIAOMI_UID`、`XIAOMI_TOKEN`、`XIAOMI_AUTH` 四行是历史记录（保留不改）；其消费脚本 `patch_cameras_m3u.py` 与 `restore_cameras.py` 已随 go2rtc 换台链路整体隔离归档，原样保留于 `archive/go2rtc/scripts/`，复活依赖／步骤与 `.env.example` 占位模板见 **`archive/go2rtc/README.md`**；`GO2RTC_AUTH` 已移出仓根 `.env.example`（现为 **7 键**、不再消费），`XIAOMI_*` 三键仍在 7 键清单内但当前主链路无活消费方，仅归档脚本复活时使用。

- `.env` 与真值一律不入库、不打印；本文件只列键名，不含任何值。
- 本地口令轮换（go2rtc / NAS + DB 侧 key）由 AF-PM 另开窗口协调，需设备在场。