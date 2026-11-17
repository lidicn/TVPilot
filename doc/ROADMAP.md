# TVPilot 版本路线图 & 技术框架 & 开发计划
> 状态（2026-11-12 整理）：**已取代／过期**。本文是当时的记录，勿当作现状；现行口径见 `doc/README.md` 真源优先级表。

> **产品名**：TVPilot（电视领航员）
> **定位**：电视设备控制服务 — LLM Agent 的"手"
> **创建日期**：2026-09-07
> **前身**：zap-tv（换台脚本）
> **代码仓库**：`E:\NAS\TVPilot\`（已迁移）
> **NAS 部署路径**：`/vol1/1000/docker/tvpilot/`（容器名 `tvpilot`）

---

## 实际进度核实（2026-09-21，基于根仓代码逐行核对）

> 以下为根仓 `tvpilot_server.py`（149,158 B）的实际状态。**正在跑的容器仍是 0.7.0 旧版**，根仓代码默认 0.8.1 但未部署。

### 已完成（根仓代码里有，未部署到容器）

| 项 | 代码位置 | 说明 |
|---|---|---|
| 中文输入（tvremoteime 广播） | `:1018-1095` | 切换 IME → `am broadcast ADB_INPUT_TEXT` → 切回，支持中文 |
| 换台先切 mytv 前台 | `tool_zap() :1203` | `ensure_mytv_foreground()` 已调用，失败快速返回 |
| 台号单一真源 | `data/channel_numbers.json`（1862 B） | `channel_no_of()` 从单一文件读，不再三张表互抄 |
| docker.sock 防护 | `:378` `os.path.exists(DOCKER_SOCK)` | 拔 sock 后不碰 docker，只产 yaml 到 data 卷 |
| refresh_rtsp 令牌鉴权 | `:3441-3445` | `TVPILOT_ADMIN_TOKEN` 校验，`REFRESH_OPEN=1` 为临时逃生 |
| refresh 最小间隔 | `:183` `REFRESH_MIN_INTERVAL=120s` | 防连发 |
| wake 本地换台默认关 | `:186` `WAKE_LOCAL=0` | 换台决策归管家，TVPilot 不经 `wake` 主题本地执行 |
| 所有口令走 env | `:138-184` | MQTT_PASS / GO2RTC_USER/PASS / ADMIN_TOKEN 全部 `os.environ.get` |
| TTS 缓存 LRU 上限 | `:66` `TTS_CACHE_MAX=200` | 不再无限涨 |
| ops.jsonl 轮转 | `:423` `OPS_LOG_MAX_BYTES=5MB` | 超限滚动 |
| 失败截图保留上限 | `:424` `SHOT_KEEP=30` | 不再只写不删 |
| go2rtc yaml 写 data 卷 | `:372` | 不再写 /tmp 残留 |
| health 报 docker_sock 状态 | `:2219` | `docker_sock: true/false` |
| 版本号 env 化 | `:59` `TVPILOT_VERSION` | 默认 0.8.1 |

### 未完成 / 待验证

| 项 | 状态 |
|---|---|
| v0.4 无障碍服务 APK | 电视端未开发；TVPilot 侧 a11y 路由框架已预留 |
| v0.5 OCR / ui_state / play_state | 代码里有路由壳子，需实际验证准确率 |
| 端到端 5 场景联调 | 待电视开机 + 用户在场 |
| mytv HTTP API 换台 vs ADB 数字键 | mytv 现在是 IPTV 播放器有 `/api/zap`，TVPilot 仍走 ADB 数字键——需对齐（见 `跨项目协调清单-20260921.md`） |
| go2rtc 无 sock 重启方案 | 拔 sock 后 go2rtc 配置重载需另找路径（见协调清单） |

### 版本状态

| 层 | 版本 | 文件 |
|---|---|---|
| 正在跑的容器 | **0.7.0** | 热修层 131,965 B / md5 `676ccbaa` |
| 根仓工作版 | **0.8.1**（默认值） | 149,158 B / md5 `bf0732c3` |
| NAS 部署树（镜像来源） | 未知（旧） | 119,869 B / md5 `fa72eadf` |

---

## 一、产品定位

### 1.1 一句话定义

**TVPilot 是运行在 NAS 上的电视设备控制服务，把 ADB、无障碍、截图、换台等能力封装成标准化工具接口，供 LLM Agent（豆包管家）调用，实现自然语言控制电视。**

### 1.2 脑手分工

| 角色 | 产品 | 职责 |
|------|------|------|
| **脑** | 豆包管家 | 理解用户意图、规划操作步骤、调用工具、观察结果、错误恢复、经验沉淀 |
| **手** | **TVPilot** | 提供标准化工具接口，执行具体电视操作（按键/点击/截图/换台/启动App），不做决策 |
| **记忆** | memory-agent | 存储操作模板、观看历史、用户偏好，提供检索能力 |

### 1.3 设计原则

1. **工具不决策**：TVPilot 只执行，所有判断由豆包管家（LLM）做出
2. **标准化接口**：所有工具统一 HTTP JSON 接口，带标准化错误码
3. **可观察**：每步操作后可查询状态（截图/前台包名/当前频道）
4. **可复用**：成功的操作序列可沉淀为模板，供后续直接调用
5. **少崩稳跑**：遵循 NAS 开发规范，容器异常捕获+健康检查，避免崩溃循环触发 NAS 死锁

---

## 二、系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        用户交互层                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  语音遥控器   │  │  PWA 遥控器   │  │  豆包管家对话    │  │
│  │  (电视端)     │  │  (手机端)     │  │  (自然语言)      │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
└─────────┼───────────────────┼─────────────────────┼────────────┘
          │                   │                     │
          ▼                   ▼                     ▼
┌─────────────────────────────────────────────────────────────┐
│                      豆包管家（Agent 层）                      │
│  ReAct 循环：Thought → Action → Observation → Adjust        │
│  工具选择 / 多步规划 / 截图判断 / 错误恢复 / 模板检索         │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP 工具调用
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    TVPilot（工具/执行层）                      │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  API 网关（HTTP :8090）                                  │ │
│  │  /api/screenshot  /api/foreground  /api/keyevent       │ │
│  │  /api/tap  /api/swipe  /api/input_text  /api/launch_app│ │
│  │  /api/zap  /api/current  /api/channels  /api/health    │ │
│  │  /remote (PWA)  /remote-static/* (静态资源)             │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                             │                                  │
│  ┌──────────────────────────▼──────────────────────────────┐ │
│  │  执行引擎                                                │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │ │
│  │  │ ADB 原语  │ │ 场景动作  │ │ 状态查询  │ │ 模板引擎  │ │ │
│  │  │ key/tap/ │ │ zap/      │ │ screenshot│ │ 模板存储/ │ │ │
│  │  │ swipe/   │ │ fongmi/   │ │ foreground│ │ 参数化执行│ │ │
│  │  │ input/   │ │ launch    │ │ current   │ │           │ │ │
│  │  │ launch   │ │           │ │           │ │           │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └───────────┘ │ │
│  └──────────────────────────┬──────────────────────────────┘ │
└─────────────────────────────┼─────────────────────────────────┘
                              │ ADB (网络) / HTTP
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       电视端（红米 75寸）                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐ │
│  │  mytv    │ │ Arcface  │ │ 飞牛TV   │ │ 无障碍服务(待开发)│ │
│  │ 换台/播放 │ │ 人脸/截图 │ │ 视频搜索 │ │ 控件级操作       │ │
│  │ :10481   │ │ :8080    │ │          │ │                │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────────┘ │
└─────────────────────────────────────────────────────────────┘

         ┌──────────────────┐         ┌──────────────────┐
         │  MQTT Broker     │         │  memory-agent    │
         │  :1883           │◄────────│  :8086           │
         │  状态推送         │         │  模板/历史存储    │
         └──────────────────┘         └──────────────────┘
```

### 2.2 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **服务端语言** | Python 3.11+ | 当前 BaseHTTPRequestHandler，Phase 2 考虑升级 FastAPI |
| **执行层** | ADB (android-tools) | 网络 ADB 连接电视 `192.168.2.238:5555` |
| **无障碍层** | Android AccessibilityService | Phase 4 开发，电视端 APK，提供控件级操作 |
| **通信** | HTTP + MQTT | HTTP 工具调用（同步），MQTT 状态推送（异步） |
| **MQTT 客户端** | paho-mqtt | 已集成 |
| **PWA 前端** | HTML + CSS + Vanilla JS | 无构建依赖，嵌入服务端静态资源 |
| **容器化** | Docker + docker compose | alpine:3.19 基础镜像 |
| **HTTPS** | Tailscale Serve | Let's Encrypt 证书，模式 A（HTTP 后端） |
| **经验存储** | memory-agent | 模板/历史通过 HTTP API 存储检索 |

### 2.3 部署规范（遵循 NAS 开发安全规范）

- **端口绑定**：必须 `192.168.2.200:8090:8090`，禁止 `0.0.0.0`
- **HTTPS**：Tailscale Serve 模式 A，`https://fn7t.tailf314d3.ts.net:8090` → `http://192.168.2.200:8090`
- **重启策略**：`unless-stopped`，但应用层必须做好异常捕获，避免崩溃循环
- **健康检查**：容器内 HTTP `/api/health`，compose 配置 healthcheck
- **代码管理**：本地开发，scp/rsync 手动推送部署，不实时同步
- **日志**：json-file 驱动，保留崩溃日志便于定位

---

## 三、版本路线图

### v0.1 — 基础换台 & PWA（✅ 已完成）

**目标**：从 zap-tv 换台脚本升级为可托管 PWA 的基础服务

- [x] mytv 个性化版换台（HTTP API 代理）
- [x] MQTT 连接与状态推送
- [x] PWA 遥控器页面（常用/全部/节目单 三 Tab + 语音换台）
- [x] Tailscale HTTPS 部署
- [x] Docker compose 规范化部署（端口绑 LAN IP）
- [x] 容器名暂保留 `zap-tv`

**交付物**：
- 容器 `zap-tv`，端口 `192.168.2.200:8090`
- PWA：`https://fn7t.tailf314d3.ts.net:8090/remote`
- 换台 API：`POST /api/zap`

---

### v0.2 — 标准化工具接口（✅ 已完成，2026-09-07）

**目标**：把零散的 ADB 功能封装成标准化工具接口，供豆包管家 ReAct Agent 调用

- [x] **统一 API 前缀**：所有接口迁移到 `/api/`（当前 `/zap`、`/keyevent`、`/tap` 等保留兼容，新增 `/api/` 版本）
- [x] **补全原子工具**：
  - [ ] `GET /api/foreground` — 当前前台包名（内部函数已有，暴露为 API）
  - [ ] `POST /api/swipe` — 滑动（x1,y1,x2,y2,duration）
  - [ ] `POST /api/input_text` — 文字输入（ADB input text）
  - [ ] `POST /api/launch_app` — 通用 App 启动（当前只支持 mytv，需通用化）
  - [ ] `POST /api/wait` — 等待（供模板引擎使用）
- [x] **标准化响应格式**：统一 `{ok, tool, result, cost_ms, error}` 格式
- [x] **标准化错误码**：`tv_unreachable` / `app_not_responding` / `wrong_foreground` / `invalid_parameter` / `operation_timeout`
- [x] **截图 API 增强**：`GET /api/screenshot?fmt=json` 返回 base64，支持 `width`/`height` 压缩参数（减少 token）
- [x] **健康检查**：`GET /api/health` 返回 ADB 连接状态、电视在线状态、mytv 状态
- [x] **容器健康检查**：docker compose healthcheck 配置
- [x] **代码迁移**：从 `D:\Documents\WorkSpace\TV_CAM\tmp\zap_tv\` 迁移到 `E:\NAS\TVPilot\`，容器名逐步改为 `tvpilot`

**交付物**：
- 12 个标准化工具接口
- 对接文档：`E:\NAS\doubao-butler\doc\tvpilot_agent_handoff.md`
- 豆包管家可开始 ReAct 循环联调

---

### v0.3 — 场景组合动作 + 联调支撑（🔄 当前阶段，预计 1-2 周）

> **路线变更（2026-09-08）**：原「场景模板引擎」方案废止。模板的存储/检索/执行/版本/沙箱统一由**豆包管家技能系统**承担，TVPilot 不再实现模板引擎，不再对接 memory-agent 模板存储。
> 关联文档：`豆包管家_对接路线变更_20260908.md`

**目标**：提供高置信组合动作 + 中文输入能力 + 操作存档，支撑豆包管家 ReAct 联调

- [ ] **P0 中文输入方案**（联调硬门槛，「搜索播放」场景必需）：
  - [ ] 优先方案：剪贴板设置 + KEYCODE_PASTE 粘贴（`cmd clipboard set-clipboard` 或 `service call clipboard`）
  - [ ] 备选方案：电视端广播接收器设置剪贴板（集成到 Arcface App）
  - [ ] 降级方案：纯 ASCII 用 `input text`，中文返回明确错误提示
  - [ ] `POST /api/input_text` 支持中文，响应含输入方式（clipboard/ime/ascii）
- [ ] **高置信组合动作**（作为原子工具的补充，不承担编排/检索职责）：
  - [x] `ensure_mytv_foreground` + zap（已在 zap 工具内部实现）
  - [x] `fongmi_search`（飞牛搜索序列，已实现）
  - [ ] `POST /api/combo/fongmi_search_play` — 飞牛搜索并播放（参数化搜索词，含中文输入）
  - [ ] `POST /api/combo/go_home` — 回桌面（KEYCODE_HOME + 前台验证）
- [ ] **操作存档（JSONL）**：
  - [ ] 每次 HTTP 工具调用记录 `(ts, request_id, tool, params, ok, error, cost_ms, foreground_before, foreground_after)`
  - [ ] 截图可选存档（`?save_screenshot=true` 时保存截图路径）
  - [ ] 追加写入 `/app/data/ops.jsonl`（容器内，外挂 volume 持久化）
  - [ ] 串行写入，无锁竞争（HTTP 单进程 + _CMD_LOCK）
- [ ] **操作存档查询**：
  - [ ] `GET /api/history?limit=N` — 查询最近 N 条操作存档
  - [ ] `GET /api/history?request_id=xxx` — 按 request_id 查询
- [ ] **联调支撑约定**：
  - [x] 所有响应带 `cost_ms`
  - [x] zap / launch_app 响应含 `foreground_after`（结构化验证，省截图）
  - [x] 工具确定性、幂等、串行锁（`_CMD_LOCK`）
  - [ ] `request_id` 全链路贯穿（管家发起 → TVPilot 执行 → 存档 → 回执）

**交付物**：
- 中文输入可靠方案（P0）
- 2 个高置信组合动作 API
- 操作存档 JSONL + 查询 API
- 豆包管家可跑通「搜索播放」联调验收场景

**P0 阻塞项说明**：中文输入是「启动飞牛TV → 搜索 → 播放」场景的硬门槛。ADB `input text` 原生不支持中文。v0.3 必须提供可靠中文输入方案，否则该场景无法跑通。


### v0.4 — 无障碍服务（控件级操作）（预计 3-4 周）

**目标**：在电视端开发无障碍服务 APK，提供控件级操作，替代脆弱的坐标点击

- [ ] **电视端无障碍服务 APK**：
  - [ ] AccessibilityService 监听界面变化
  - [ ] 控件树查询（按 text / resource-id / class 查找控件）
  - [ ] 控件点击（performAction ACTION_CLICK）
  - [ ] 控件文本输入（ACTION_SET_TEXT）
  - [ ] 滚动（ACTION_SCROLL_FORWARD/BACKWARD）
  - [ ] 界面文字提取（OCR 替代，直接从控件树获取文字）
- [ ] **TVPilot 无障碍工具**：
  - [ ] `POST /api/a11y/find` — 查找控件（按 text/id/class），返回控件信息和坐标
  - [ ] `POST /api/a11y/click` — 点击控件（按 text/id/class，不需要坐标）
  - [ ] `POST /api/a11y/input` — 在指定输入框输入文字
  - [ ] `GET /api/a11y/tree` — 获取当前界面控件树（JSON，供 LLM 分析）
  - [ ] `GET /api/a11y/text` — 获取当前界面所有文字（轻量状态观察）
- [ ] **与 Arcface App 合并**：无障碍服务可集成到 Arcface App（电视端已有 APK），减少安装包数量
- [ ] **坐标点击降级**：无障碍服务不可用时，自动降级为 ADB 坐标点击

**交付物**：
- 电视端无障碍服务（集成到 Arcface App 或独立 APK）
- 5 个无障碍工具 API
- 控件树 JSON 格式规范

---

### v0.5 — 智能状态识别（预计 2-3 周）

**目标**：减少对多模态截图的依赖，用轻量方式提供结构化状态，降低 token 消耗和延迟

- [ ] **OCR 文字识别**：
  - [ ] 截图 → OCR → 返回界面文字 + 位置（可集成无障碍服务的文字提取，或用 Tesseract/PaddleOCR）
  - [ ] `GET /api/ocr` — 返回当前界面所有文字及坐标
  - [ ] `GET /api/ocr/find?text=xxx` — 查找指定文字的位置
- [ ] **界面状态分类**：
  - [ ] 预定义常见状态：首页 / 搜索页 / 播放页 / 设置页 / 弹窗 / 加载中 / 广告
  - [ ] `GET /api/ui_state` — 返回当前界面状态分类（轻量，无需 LLM 看图）
  - [ ] 实现方式：包名+Activity名判断 + 关键文字匹配 + 截图特征（可选）
- [ ] **智能截图压缩**：
  - [ ] 截图自动裁剪（只保留关键区域）
  - [ ] 分辨率压缩（1920x1080 → 480x270，减少多模态 token）
  - [ ] `GET /api/screenshot?fmt=json&width=480`
- [ ] **播放状态检测**：
  - [ ] `GET /api/play_state` — 是否在播放（通过 mytv API + 截图亮度变化检测）
  - [ ] 播放/暂停/缓冲/广告 状态区分

**交付物**：
- OCR API
- 界面状态分类 API
- 智能截图压缩
- 播放状态检测

---

### v1.0 — 完整 TVPilot（预计 v0.5 完成后 1-2 周整合）

**目标**：所有工具稳定可用，与豆包管家 Agent 全功能联调，支持复杂电视操作

- [ ] **所有工具稳定运行**：v0.2-v0.5 全部工具经过测试和优化
- [ ] **与豆包管家端到端联调**：
  - [ ] 换台场景（语音/文字指令）
  - [ ] 搜索播放电影/电视剧场景
  - [ ] 系统设置场景（调节音量、切换信号源）
  - [ ] 多 App 切换场景
- [ ] **与 memory-agent 经验闭环**：
  - [ ] 成功操作自动沉淀模板
  - [ ] 新任务优先检索模板
  - [ ] 模板执行失败回退 ReAct
- [ ] **文档完善**：
  - [ ] 完整 API 文档（OpenAPI 格式）
  - [ ] 工具使用最佳实践
  - [ ] 常见问题排查指南
- [ ] **监控与告警**：
  - [ ] 工具调用统计（成功率/延迟/失败原因）
  - [ ] 电视离线告警
  - [ ] 容器异常告警
- [ ] **容器名正式改为 `tvpilot`**，保留 `zap-tv` 兼容期

**交付物**：
- 稳定版 TVPilot v1.0
- 完整 API 文档
- 与豆包管家/memory-agent 全功能联调通过

---

### v1.1 — Agent 友好性增强（吸收 android-use 设计，预计 2-3 周）

> **来源**：调研开源项目 [languse-ai/android-use](https://github.com/languse-ai/android-use)（豆包手机开源版），其核心设计对 TVPilot 的 Agent 操控体验有直接借鉴价值。
> 调研笔记：`doc/调研-android-use借鉴分析-20260927.md`

**目标**：让 Agent 操控电视从"坐标猜测"升级为"索引精准点击"，大幅降低操作难度和 token 消耗，提升点播等复杂场景的成功率。

#### 1.1 元素索引机制（highlight_index）⭐ P0

**问题**：当前 `tv_ui_dump` 返回 `text+pos+click`，Agent 需要自己算坐标、判断点哪里，准确率低且耗 token。

**方案**：给屏幕上每个可交互元素分配数字索引，Agent 只需说"点击索引 5"。

- [ ] 改造 `tv_ui_dump`：返回 `[{index, text, pos, click, type, resource_id}]`
- [ ] 新增 `tv_tap_index(index)`：按索引点击，内部映射到坐标
- [ ] 索引分配规则：按从上到下、从左到右排序，可交互元素优先
- [ ] 索引缓存：同一界面索引稳定，界面变化时重新分配

**验收**：Agent 用 `tv_ui_dump` + `tv_tap_index` 完成点播流程，无需计算坐标。

#### 1.2 高亮截图 ⭐ P0

**问题**：当前截图是原始画面，Agent 需要自己对应坐标和元素。

**方案**：在截图上用数字标记可交互元素，Agent 看图直接知道点哪个。

- [ ] `tv_screenshot` 增加 `highlight=true` 参数
- [ ] 用 PIL 在每个可交互元素旁绘制半透明圆形 + 数字编号
- [ ] 颜色区分：可点击=红色、可输入=绿色、可滚动=蓝色
- [ ] 与 `tv_ui_dump` 的索引一一对应

**验收**：高亮截图 + 非视觉模型也能通过索引文字描述完成操作。

#### 1.3 DOM 树解析增强 + XPath 稳定定位 P1

**问题**：当前 XML 解析只提取 text/pos/click，丢失结构信息，界面变化后坐标失效。

**方案**：完整解析 uiautomator XML 为 DOM 树，支持 XPath 稳定定位。

- [ ] `DOMElementNode` 数据结构：parent/children/bounding_box/node_type/xpath/resource_id/class_name
- [ ] 节点类型分类：INTERACTIVE / NON_INTERACTIVE / TEXT
- [ ] `tv_ui_dump` 增加 `xpath` 字段
- [ ] 新增 `tv_tap_xpath(xpath)`：按 XPath 点击（界面布局变化时仍能定位）
- [ ] 元素历史记录：记录每次交互的元素 xpath，用于蓝图稳定执行

**验收**：同一 App 版本更新后，基于 XPath 的蓝图仍能执行成功。

#### 1.4 蓝图自动录制 P1

**问题**：当前蓝图需要手动编写步骤，效率低。

**方案**：Agent 操作一遍后，自动提取操作序列（索引/xpath/按键）生成蓝图。

- [ ] 操作录制模式：`POST /api/blueprint/record/start` 开始录制
- [ ] 录制期间所有工具调用自动记录（工具名、参数、结果、交互元素）
- [ ] `POST /api/blueprint/record/stop` 结束，自动生成蓝图 JSON
- [ ] 蓝图参数化：自动识别可参数化部分（搜索词、频道名等）
- [ ] 蓝图验证：录制后自动回放一次验证成功率

**验收**：点播流程手动操作一遍后，自动生成可复用的参数化蓝图。

#### 1.5 新增通用工具 P2

- [ ] `tv_swipe(x1,y1,x2,y2,duration)` — 滑动（电视部分界面需要）
- [ ] `tv_long_press(x,y,duration)` — 长按（菜单、多选等场景）
- [ ] `tv_scroll(direction)` — 滚动（up/down/left/right，基于 swipe 封装）
- [ ] `tv_get_element(index)` — 获取指定索引元素的详细信息

#### 1.6 Agent 引导优化 P2

- [ ] 参考 android-use 的 prompts.py，优化 MCP 工具描述
- [ ] 增加"操作最佳实践"系统提示：优先用索引、截图前先 ui_dump、失败重试策略
- [ ] MCP 工具描述中增加典型用例和注意事项

---

### v1.0+ — 未来方向（待定）

- [ ] **多电视支持**：控制多台电视（客厅/卧室），按设备 ID 路由
- [ ] **语音指令直连**：TVPilot 直接接收语音指令（不经过豆包管家文字中转），本地 ASR
- [ ] **场景自动化**：与 HA 联动，"看电视"场景自动调暗灯光、关闭窗帘
- [ ] **学习用户习惯**：基于观看历史和操作记录，主动推荐
- [ ] **远程控制**：通过 Tailscale 外网控制电视（已具备基础，需安全加固）
- [ ] **第三方 App 适配**：为常用视频 App（B站、腾讯视频、爱奇艺等）预置操作模板

---

## 四、开发计划

### 4.1 里程碑总览

| 版本 | 内容 | 预计周期 | 依赖 | 状态 |
|------|------|---------|------|------|
| v0.1 | 基础换台 & PWA | 已完成 | - | ✅ |
| v0.2 | 标准化工具接口 | 1-2 周 | 无 | ✅ |
| v0.3 | 场景组合动作+联调支撑 | 2-3 周 | v0.2 | ✅ |
| v0.4 | 无障碍服务 | 3-4 周 | v0.2 + 电视端 APK | ✅ |
| v0.5 | 智能状态识别 | 2-3 周 | v0.2 | ✅ |
| v0.6-v0.8 | HDMI3/监控/EPG/FaceID | 已完成 | - | ✅ |
| v1.0 | 整合与联调 | 1-2 周 | v0.3-v0.8 | 🔄 进行中 |
| v1.1 | Agent 友好性增强（android-use 借鉴） | 2-3 周 | v1.0 + MCP 稳定 | ⏳ 规划中 |

**总预计周期**：v0.2 到 v1.0 约 10-14 周（2.5-3.5 个月），可并行压缩。

### 4.2 v0.2 详细开发计划（当前阶段）

| 周 | 任务 | 交付物 |
|----|------|--------|
| 第1周 | 统一 `/api/` 前缀；补全 foreground/swipe/input_text/launch_app/wait 工具；标准化响应格式和错误码 | 11 个标准化工具接口 |
| 第2周 | 截图 API 增强（压缩参数）；健康检查；容器 healthcheck；代码迁移到 E:\NAS\TVPilot；与豆包管家联调测试 | 可联调的 v0.2 版本 |

**v0.2 验收标准**：
- 11 个工具接口全部可通过 HTTP 调用
- 每个工具有标准化响应和错误码
- 豆包管家可调用 screenshot + keyevent + tap + launch_app + zap_channel 完成 ReAct 循环
- 容器健康检查正常，崩溃后自动重启不产生循环

### 4.3 并行开发建议

v0.3（模板引擎）、v0.4（无障碍服务）、v0.5（智能识别）在 v0.2 完成后可**部分并行**：

```
v0.2 (1-2周)
  │
  ├─→ v0.3 模板引擎 (2-3周) ──┐
  │                              ├─→ v1.0 整合 (1-2周)
  ├─→ v0.4 无障碍服务 (3-4周) ──┤
  │                              │
  └─→ v0.5 智能识别 (2-3周) ────┘
```

- v0.3 依赖 v0.2（工具接口稳定）+ memory-agent 模板存储
- v0.4 依赖 v0.2（工具接口）+ 电视端 APK 开发（可独立进行）
- v0.5 依赖 v0.2（截图工具），可与 v0.3/v0.4 并行
- v1.0 依赖 v0.3+v0.4+v0.5 全部完成

### 4.4 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| 电视端无障碍服务开发复杂度高 | v0.4 延期 | 先用 ADB 坐标+截图验证 ReAct 循环，无障碍服务作为增强不阻塞 v1.0 核心功能 |
| memory-agent 模板存储接口未就绪 | v0.3 延期 | v0.3 先做本地模板存储，memory-agent 对接后置 |
| ADB 连接不稳定（电视休眠断连） | 工具调用失败 | 工具层自动重连（ADB connect 重试），健康检查监控连接状态 |
| 坐标点击因分辨率/布局变化失效 | 操作成功率低 | v0.4 无障碍服务解决；v0.2-v0.3 阶段用截图验证+重试缓解 |
| NAS 容器崩溃循环触发 trim 死锁 | NAS 整机冻结 | 严格遵循 NAS 规范：异常捕获+健康检查+重启退避，代码本地测试通过再部署 |
| 多模态截图 token 消耗大 | 成本高/延迟高 | v0.5 智能识别减少截图使用；模板执行不截图 |

---

## 五、API 接口规范（v0.2 目标）

### 5.1 通用规范

- **Base URL**：`http://192.168.2.200:8090/api`（局域网）/ `https://fn7t.tailf314d3.ts.net:8090/api`（Tailscale）
- **请求格式**：JSON（POST），Query 参数（GET）
- **响应格式**：统一 JSON
- **字符编码**：UTF-8

### 5.2 统一响应格式

```json
{
  "ok": true,
  "tool": "tool_name",
  "result": { },
  "cost_ms": 123,
  "error": null
}
```

### 5.3 工具接口一览

| 工具 | 方法 | 路径 | 参数 | 说明 | 版本 |
|------|------|------|------|------|------|
| screenshot | GET | `/screenshot` | `fmt=json\|png`, `width=480`, `highlight=true` | 截图（v1.1 支持高亮标记） | v0.2 / v1.1 |
| foreground | GET | `/foreground` | 无 | 当前前台包名 | v0.2 |
| keyevent | POST | `/keyevent` | `key: "KEYCODE_XXX"` | 发送按键 | v0.2 |
| tap | POST | `/tap` | `x, y` | 点击坐标 | v0.2 |
| tap_index | POST | `/tap_index` | `index` | 按元素索引点击 | v1.1 |
| tap_xpath | POST | `/tap_xpath` | `xpath` | 按 XPath 点击 | v1.1 |
| swipe | POST | `/swipe` | `x1,y1,x2,y2,duration_ms` | 滑动 | v0.2 / v1.1 |
| long_press | POST | `/long_press` | `x, y, duration` | 长按 | v1.1 |
| scroll | POST | `/scroll` | `direction: up/down/left/right` | 滚动 | v1.1 |
| input_text | POST | `/input_text` | `text` | 输入文字 | v0.2 |
| launch_app | POST | `/launch_app` | `package`, `activity`(可选) | 启动 App | v0.2 |
| wait | POST | `/wait` | `seconds` | 等待（模板用） | v0.2 |
| zap | POST | `/zap` | `channel` | IPTV 换台 | v0.1 |
| current | GET | `/current` | 无 | 当前频道 | v0.1 |
| channels | GET | `/channels` | 无 | 频道列表 | v0.1 |
| ui_dump | GET | `/ui_dump` | `format=index\|xml` | 界面元素（v1.1 带索引） | v0.5 / v1.1 |
| ui_state | GET | `/ui_state` | 无 | 界面状态分类 | v0.5 |
| play_state | GET | `/play_state` | 无 | 播放状态 | v0.5 |
| health | GET | `/health` | 无 | 服务健康检查 | v0.2 |
| blueprint_run | POST | `/blueprint/run` | `name, params` | 执行蓝图 | v0.3 |
| blueprint_record | POST | `/blueprint/record` | `action: start/stop` | 录制蓝图 | v1.1 |
| list_apps | GET | `/list_apps` | `filter` | 列出已安装应用 | v1.0 |
| app_state | GET | `/app_state` | `package` | 应用运行状态 | v1.0 |
| force_stop | POST | `/force_stop` | `package` | 强制停止应用 | v1.0 |
| click_text | POST | `/click_text` | `text` | 按文字点击 | v1.0 |

### 5.4 错误码

| 错误码 | 含义 | HTTP 状态 |
|--------|------|----------|
| `tv_unreachable` | 电视 ADB 连接失败 | 503 |
| `app_not_responding` | 目标 App 无响应 | 504 |
| `wrong_foreground` | 前台 App 不是预期 | 200（ok=false） |
| `invalid_parameter` | 参数错误 | 400 |
| `operation_timeout` | 操作超时 | 504 |
| `mytv_api_error` | mytv API 错误 | 502 |
| `not_implemented` | 工具未实现 | 501 |

---

## 六、项目文件结构（目标）

```
E:\NAS\TVPilot\
├── doc\
│   ├── ROADMAP.md              ← 本文档
│   ├── API_SPEC.md             ← 完整 API 文档（v0.2 完成后编写）
│   └── TEMPLATE_SPEC.md        ← 模板格式规范（v0.3 编写）
├── src\
│   ├── tvpilot\                ← 主包（从 zap_server.py 重构）
│   │   ├── __init__.py
│   │   ├── server.py           ← HTTP 服务器
│   │   ├── adb.py              ← ADB 封装
│   │   ├── tools\              ← 工具实现
│   │   │   ├── screenshot.py
│   │   │   ├── keyevent.py
│   │   │   ├── tap.py
│   │   │   ├── swipe.py
│   │   │   ├── input_text.py
│   │   │   ├── launch_app.py
│   │   │   ├── foreground.py
│   │   │   └── zap.py
│   │   ├── template.py         ← 模板引擎（v0.3）
│   │   ├── a11y.py             ← 无障碍服务客户端（v0.4）
│   │   ├── ocr.py              ← OCR 文字识别（v0.5）
│   │   ├── mqtt.py             ← MQTT 状态推送
│   │   └── config.py           ← 配置
│   ├── static\                 ← PWA 静态资源
│   │   ├── remote.html
│   │   ├── manifest.json
│   │   ├── sw.js
│   │   └── icon-512.png
│   ├── templates\              ← 内置模板库（v0.3）
│   │   ├── fongmi_search_play.json
│   │   └── ...
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   └── channels.json           ← 频道表（外挂只读）
└── README.md
```

---

## 七、与周边系统的关系

| 系统 | 关系 | 接口 |
|------|------|------|
| **豆包管家** | 调用方（Agent 脑） | HTTP 工具调用 + MQTT 状态订阅 |
| **mytv** | 换台/播放执行 | HTTP `:10481` 代理（/api/zap, /api/current, /api/channels） |
| **Arcface App** | 人脸/截图 | HTTP `:8080`（截图复用，v0.4 无障碍服务可能集成到此 APK） |
| **memory-agent** | 模板/历史存储 | HTTP `:8086`（模板存储/检索，v0.3） |
| **MQTT Broker** | 消息总线 | `:1883`，Topic 前缀 `tv/livingroom/` |
| **HA** | 智能家居联动 | MQTT / HTTP（v1.0+ 场景自动化） |
| **Node-RED** | 流程编排 | MQTT 订阅（现有 tv/livingroom/control 兼容） |

---

## 八、当前状态与下一步

### 已完成（v0.1）
- ✅ mytv 换台 API 代理
- ✅ MQTT 连接与状态推送
- ✅ PWA 遥控器（三 Tab + 语音换台）
- ✅ Tailscale HTTPS 部署
- ✅ Docker compose 规范化
- ✅ 豆包管家对接文档（`tvpilot_agent_handoff.md`）

### 当前阶段（v0.2）
- 🔄 标准化工具接口开发
- 🔄 代码迁移到 `E:\NAS\TVPilot\`

### 下一步行动
1. TVPilot 侧：完成 v0.2 全部 11 个标准化工具接口
2. 豆包管家侧：实现 ReAct 循环，接入 TVPilot 工具
3. 联调：用"打开飞牛TV搜索电影播放"场景端到端测试
4. memory-agent 侧：确认模板存储方案

---

*本文档为 TVPilot 产品的路线图与技术框架，随开发进展持续更新。*
*最后更新：2026-09-21（实际进度核实）*
