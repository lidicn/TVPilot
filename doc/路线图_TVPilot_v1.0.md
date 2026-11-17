# TVPilot 版本路线图 v1.0
> 状态（2026-11-12 整理）：**已取代／过期**。本文是当时的记录，勿当作现状；现行口径见 `doc/README.md` 真源优先级表。

> 版本：v1.0 修订2 | 日期：2026-09-09 | 状态：v0.8 完成，v1.0 整合中
> 上一版：路线图_TVPilot_v1.0.md（修订1）

---

## 一、版本完成状态

| 版本 | 名称 | 状态 | 完成日期 | 说明 |
|------|------|------|---------|------|
| v0.1 | 基础换台 & PWA | ✅ | 2026-09-07 | mytv换台/MQTT/PWA遥控器/HTTPS |
| v0.2 | 标准化工具接口 | ✅ | 2026-09-07 | 12个工具+/api/统一/错误码/health |
| v0.3 | 场景组合动作+联调支撑 | ✅ | 2026-09-08 | 中文输入(tvremoteime)/combo动作/操作存档 |
| v0.4 | 无障碍服务（控件级操作） | ✅ | 2026-09-08 | 集成到Arcface App :8081，TVPilot代理/api/a11y/* |
| v0.5 | 智能状态识别 | ✅ | 2026-09-08 | OCR/界面分类/播放状态/智能截图 |
| v0.6 | 系统状态与音频控制 | ✅ | 2026-09-08 | system_state/音量调节/静音 |
| v0.7 | IPTV/EPG/用户管理/FaceID | ✅ | 2026-09-09 | 97频道M3U/EPG/用户切换/MQTT人脸/go2rtc音频修复 |
| v0.8 | HDMI3时长监控+镜像重建 | ✅ | 2026-09-09 | HDMI3检测API/5分钟轮询/SQLite/memory-agent对接单 |
| v0.9 | 模块化重构+MCP Server | ✅ | 2026-09-24 | tvpilot包拆分/routes子包/15个MCP工具/5个新工具(list_apps等) |
| **v1.0** | **完整TVPilot整合** | **🔄 进行中** | **预计2026-09-28** | 监控告警✅/API文档✅/点播流程验证✅/待端到端联调 |
| **v1.1** | **Agent友好性增强** | **⏳ 规划中** | **预计2026-10-05** | 元素索引/高亮截图/DOM树XPath/蓝图自动录制（吸收android-use设计） |

---

## 二、v0.8 详情（当前里程碑）

### 已完成 ✅
| 任务 | 说明 |
|------|------|
| HDMI3状态检测 | 前台应用+LastCurrentSource=25双重验证 |
| 5分钟轮询 | 后台线程，对电视零影响（<50ms/次） |
| SQLite本地存储 | usage表+raw_checks表，保留7天 |
| 5个HTTP API | /api/tv/hdmi3/{status,today,usage,history} + /api/tv/state |
| 小甜菜接口标准 | E:\NAS\xiaotiancai\docs\TVPilot_HDMI3时长统计接口标准_v1.0.md |
| memory-agent对接单 | /vol1/1000/docker/memory-agent/docs/memory-agent对接单_HDMI3使用时长_v1.0.md |
| Arcface v0.9跟踪算法 | 锁头跟踪/STABLE锁名/LEAVING重连/匈牙利匹配优化 |

### 进行中 🔄
| 任务 | 说明 |
|------|------|
| 容器镜像重建 | Dockerfile添加hdmi3_monitor.py，同步最新tvpilot_server.py，构建中 |

### 待完成 ⏳
| ID | 任务 | 优先级 | 说明 |
|----|------|--------|------|
| v0.8-T1 | 镜像重建后验证 | P0 | 重启容器，验证所有API+HDMI3监控正常 |
| v0.8-T2 | 路线图文档更新 | P1 | 本文档 |

---

## 三、v1.1 规划（Agent 友好性增强，吸收 android-use 设计）

> **来源**：调研开源项目 [languse-ai/android-use](https://github.com/languse-ai/android-use)（豆包手机开源版），核心设计对 TVPilot Agent 操控体验有直接借鉴价值。
> 调研笔记：`doc/调研-android-use借鉴分析-20260927.md`

### 背景与动机

当前 Agent 操控电视的痛点：
1. `tv_ui_dump` 返回 text+pos+click，Agent 需要自己算坐标、判断点哪里，准确率低
2. 点播等复杂场景需要多次截图+坐标计算，token 消耗大
3. 界面变化后坐标失效，蓝图不稳定
4. 蓝图需要手动编写，效率低

android-use 的核心解决方案：**元素索引机制** — 给每个可交互元素分配数字编号，Agent 只需说"点击 5"。

### v1.1 任务清单

| ID | 任务 | 优先级 | 说明 | 验收方式 |
|----|------|--------|------|---------|
| v1.1-T1 | 元素索引机制 | P0 | tv_ui_dump 返回带 index 的元素列表；新增 tv_tap_index | Agent 用索引完成点播，无需算坐标 |
| v1.1-T2 | 高亮截图 | P0 | tv_screenshot 增加 highlight=true，截图上标记元素编号 | 高亮截图与 ui_dump 索引一一对应 |
| v1.1-T3 | DOM树解析+XPath | P1 | 完整解析XML为DOM树，支持xpath稳定定位 | 界面布局变化后xpath蓝图仍可执行 |
| v1.1-T4 | 蓝图自动录制 | P1 | 操作一遍自动生成参数化蓝图 | 点播流程录制后可回放成功 |
| v1.1-T5 | 新增通用工具 | P2 | tv_swipe/tv_long_press/tv_scroll/tv_get_element | 工具可正常调用 |
| v1.1-T6 | Agent引导优化 | P2 | 优化MCP工具描述，增加最佳实践提示 | opencode实测通过率提升 |

### 与 android-use 的对应关系

| android-use 设计 | TVPilot v1.1 对应 | 借鉴程度 |
|------------------|-------------------|---------|
| highlight_index 元素索引 | v1.1-T1 元素索引 | 直接借鉴 |
| 高亮截图（红圈/数字） | v1.1-T2 高亮截图 | 直接借鉴 |
| DOMElementNode + XPath | v1.1-T3 DOM树+XPath | 借鉴架构 |
| 操作历史记录 | v1.1-T4 蓝图录制基础 | 借鉴思路 |
| Agent ReAct 循环 | 不搬（我们是MCP工具，由外部agent调用） | 仅参考prompt |
| 手机触摸交互（swipe/long_press） | v1.1-T5 部分借鉴（电视遥控器为主） | 选择性借鉴 |

---

## 四、v1.0 待办（下一里程碑）

### 必须完成（P0）
| ID | 任务 | 说明 | 验收方式 | 状态 |
|----|------|------|---------|------|
| v1.0-T1 | 端到端场景全链路测试 | 8大联调场景逐项验收 | 用户在场联调 | ⏳ 待用户配合 |
| v1.0-T2 | 监控与告警 | 工具调用统计(成功率/延迟)/电视离线告警/容器异常告警 | PM验收 | ✅ 2026-09-24完成 |
| v1.0-T3 | 完整API文档 | 所有端点+参数+响应+错误码 | PM验收 | ✅ 2026-09-24完成 |

### 应该完成（P1）
| ID | 任务 | 说明 |
|----|------|------|
| v1.0-T4 | v1.0整合联调报告 | 8场景测试结果+已知问题+性能数据 |
| v1.0-T5 | 错误码重试矩阵验证 | 所有错误场景返回正确错误码 |
| v1.0-T6 | Arcface多人注册验证 | Kevin/Emily注册，多人场景跟踪算法验证 |
| v1.0-T7 | **MA 接口对接确认** | HDMI3 数据已由 MA 统一采集查询，确认 TVPilot 侧 ADB 查询可下线；MA 接口标准见 `doc/MA接口标准_v1.0_生态共享.md` |

### 可选（P2）
| ID | 任务 | 说明 | 状态 |
|----|------|------|------|
| v1.0-T7 | mytv台标图标修复 | 根因：logoReady延迟+remember状态丢失；去掉延迟后97频道全部加载成功 | ✅ 完成 |
| v1.0-T8 | mytv冷启动优化 | stale-while-revalidate模式，有缓存时零网络等待；LazyList加唯一key缓解崩溃 | ✅ 完成 |
| v1.0-T9 | 电信IPTV方案B完成 | clientId逆向，程序化刷新RTSP | ⏳ 阻塞（clientId未找到） |
| v1.0-T10 | 米家摄像头集成 | go2rtc恢复4路摄像头+M3U监控分组，mytv可查监控 | ✅ 完成 |

---

## 四、v1.0 联调场景（8大场景）

| # | 场景 | 核心工具 | 状态 |
|---|------|---------|------|
| 1 | 语音/文字换台 | zap/current/screenshot | ✅ 已验证 |
| 2 | 飞牛TV搜索播放 | combo_search_play/input_text | ✅ 已验证 |
| 3 | 系统控制（音量/回桌面/启动App） | keyevent/combo_go_home/launch_app | ✅ 已验证 |
| 4 | 多App切换 | launch_app/combo_go_home | ✅ 已验证 |
| 5 | FaceID个性化切换 | MQTT人脸/users/switch | ⚠️ lidicn已验证，多人待验证 |
| 6 | 观看历史/HDMI3时长 | history/hdmi3 API | ✅ HDMI3已完成，观看历史待memory-agent对接 |
| 7 | 智能观察（OCR/界面分类） | ocr/ui_state/play_state | ✅ 已验证 |
| 8 | 错误恢复与重试 | 错误码矩阵 | ✅ 已验证 |

---

## 五、已知问题与风险

| # | 问题 | 等级 | 状态 | 应对 |
|---|------|------|------|------|
| 1 | mytv台标显示文字而非图标 | P2 | 未修复 | Coil加载问题，待排查 |
| 2 | mytv冷启动选台菜单慢(10秒) | P2 | 部分优化 | v0.6已优化EPG异步，深度优化待反馈 |
| 3 | 电信IPTV RTSP约4天过期 | P2 | 方案A手动刷新 | 方案B clientId逆向阻塞，待完成 |
| 4 | Kevin/Emily未注册人脸 | P1 | 待用户配合 | Arcface注册界面已修复 |
| 5 | memory-agent HDMI3对接未完成 | P1 | 对接单已交付 | 待memory-agent开发者实现采集 |
| 6 | 容器镜像未重建(修改临时) | P0 | 🔄 构建中 | v0.8完成 |

---

## 六、子项目状态

| 子项目 | 版本 | 状态 | 说明 |
|--------|------|------|------|
| TVPilot服务端 | v0.8 | 🔄 镜像重建中 | 容器 tvpilot :8090 |
| mytv个性化版 | v0.8 | ✅ | 包名 com.tvcam.mytv :10481 |
| Arcface | v0.9 | ✅ | 包名 com.example.arcfaceandroid :8080(HTTP)+:8081(无障碍) |
| PWA遥控器 | v1.0 | ✅ | /remote，语音换台+台标+节目单 |
| go2rtc | 稳定 | ✅ | 容器 go2rtc :1984，97频道AAC转码 |

---

## 七、Memory-Agent 对接（v0.6 接口标准）

> 接口标准文档：`doc/MA接口标准_v1.0_生态共享.md`
> MA 地址：`http://192.168.2.200:8086`

### 7.1 HDMI3 使用时长：MA 统一查询，ADB 查询可下线

**重大变更**：HDMI3/Xbox 使用时长现在由 MA 统一采集和查询，**TVPilot 不再需要通过 ADB 查询电视状态来获取使用时长**。

- 小甜菜防沉迷模块直接调用 MA 的 `POST /api/insights/query`（template_id=xbox_daily_usage）
- MA 已从 TVPilot 采集 HDMI3 数据并持久化，提供统一查询接口
- TVPilot 侧的 HDMI3 监控 API（`/api/tv/hdmi3/*`）保留作为数据源，但消费方应优先走 MA

### 7.2 TVPilot 需要对接的 MA 接口

| 接口 | 用途 | 优先级 | 状态 |
|---|---|---|---|
| `POST /api/insights/query` | HDMI3 数据查询（消费方走 MA，TV 侧无需对接） | P0 | ✅ MA 已就绪 |
| `POST /api/agent/memories` | 写入电视端事件记忆（source=vision，需创建专属 app_token） | P1 | ⏳ 待创建令牌 |
| `ma/presence` MQTT | ArcFace 识别结果发布（MA 已从 TVPilot 采集，TV 无需订阅） | — | ✅ 已有 |

### 7.3 TVPilot 侧待办

1. **确认 HDMI3 ADB 查询可下线**：验证 MA 查询结果与 TVPilot 本地 API 一致后，标记 TVPilot `/api/tv/hdmi3/*` 为"数据源保留，消费方走 MA"
2. **创建 vision app_token**：在 MA WebUI 创建 source=vision 的专属令牌，用于 TVPilot 写入电视端事件记忆
3. **ArcFace 统一 FaceID API**：已完成（工单 20260909_001），小甜菜/mytv 调用 ArcFace APK 的统一 FaceID 接口

---

*TVPilot 路线图 | 2026-09-27 | 修订4（新增 v1.1 Agent友好性增强，吸收 android-use 设计）*
