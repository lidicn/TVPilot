# Arcface Android App 交接单
> 状态（2026-11-12 整理）：**历史记录**。本文记录当时的事实，不代表当前状态；现行口径见 `doc/README.md` 真源优先级表。

> 交接日期：2026-09-23
> 项目位置：`D:\Documents\WorkSpace\TV_CAM\Arcface`
> 包名：`com.example.arcfaceandroid`
> 目标设备：小米 MIUI TV（MiTV-MFTP0，Android 9，ARM32 armeabi-v7a）
> 电视地址：`192.168.2.238:5555`（ADB over network）

---

## 一、项目定位

Arcface 是运行在电视上的常驻 Android App，承担四个职责：

1. **人脸识别**：USB 摄像头实时采集，虹软 ArcFace 3.0 SDK 检测+识别，判断谁在电视前
2. **语音指令捕获**：通过无障碍服务监听小爱同学的语音转文字结果，抓取用户说了什么
3. **HTTP 服务**：`:8080` 提供 REST API，供 TVPilot / zap-tv / doubao-butler 调用
4. **MQTT 客户端**：连接 NAS 上的 MQTT broker，收发状态和命令

它是 TVPilot 架构中"电视端常驻代理"的角色——TVPilot 在 NAS 侧做决策，Arcface 在电视侧执行和感知。

---

## 二、技术栈与关键依赖

| 组件 | 版本/说明 |
|---|---|
| 虹软 ArcFace SDK | 3.0 Android，闭源，native libs 含 arm64-v8a + armeabi-v7a |
| HTTP 服务 | `com.sun.net.httpserver.HttpServer`（JDK 内置，无第三方依赖） |
| MQTT | Eclipse Paho `org.eclipse.paho.client.mqttv3` |
| 摄像头 | Camera2 API（`CameraCaptureSource`） |
| 无障碍 | `TvAccessibilityService`（监听小爱界面节点变化） |
| 构建 | Gradle 8.4，JDK 17（`D:\jdk17\jdk-17.0.20.1+1`） |
| minSdk | 21（Android 5.0），targetSdk 28 |

APP_ID / SDK_KEY 从 `AppConfig` SharedPreferences 读取，首次启动需在设置页填入或通过 ADB 写入。

---

## 三、核心模块说明

### 3.1 人脸识别引擎（FaceServer.java）

- 单例，持有 `FaceEngine` 实例
- **当前模式：VIDEO 模式**（`ASF_DETECT_MODE_VIDEO`，scaleVal=16，`ASF_OP_0_ONLY`）
- 关键发现：前任开发者认为 VIDEO 模式在本设备不可用，实际根因是**帧采样间隔 350ms 太大**，VIDEO 模式的内部跟踪器需要连续帧。降到 100ms 后 VIDEO 模式正常工作，faceId 跨帧稳定
- `doRecognize()`：完整识别流程（检测+提特征+比对），含 VIDEO 空帧自动回退 IMAGE 保护（仅 HTTP 上传识别走此路径）
- `detectFacesOnly()`：仅检测（主流程用，无回退保护——VIDEO 模式已验证稳定，不需要）
- `featureAndCompare()`：对已知人脸框提特征并比对人脸库

### 3.2 识别服务（FaceServerService.java）

- 前台 Service，保活
- 摄像头帧回调中节流分析（当前间隔 **100ms**）
- 流程：`detectFacesOnly`（ROI/全图）→ 质量门控 → 跨帧特征缓存 → `featureAndCompare` → `RecognitionState` 状态机
- ROI 热点加速：已知人脸位置后只检测 ROI 区域，每 6 帧强制全图兜底
- 特征缓存：faceId 精确命中时 TTL=3000ms，中心距离回退时 TTL=800ms
- `RecognitionState`：四状态机（candidate → confirmed → stable → leaving），防抖

### 3.3 HTTP 服务（FaceHttpServer.java）

- 端口 **8080**，主线程启动
- 主要端点（完整列表见源码 `handle()` 方法的 switch）：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/health` | GET | 健康检查 |
| `/api/stats` | GET | 识别统计（含 detect_mode、检测率、匹配率、当前轨迹数） |
| `/api/recognize` | POST | 上传图片识别 |
| `/api/register` | POST | 注册人脸 |
| `/api/faces` | GET | 人脸库列表 |
| `/api/faces/reload` | POST | 重新加载人脸库 |
| `/api/scan` | POST | 触发扫描（息屏时临时开相机） |
| `/api/scan/result` | GET | 扫描结果 |
| `/api/snapshot` | GET | 当前摄像头快照 |
| `/api/hotspots` | GET | 人脸热点分布 |
| `/api/overlay/status_show` | POST | 显示状态悬浮窗 `{text, duration_ms}` |
| `/api/overlay/hide` | POST | 隐藏悬浮窗 |
| `/api/overlay/status` | GET | 悬浮窗状态 |
| `/api/tts/play` | POST | TTS 播放 |
| `/api/notify` | POST | 弹窗通知 |
| `/api/iptv/channel` | POST | IPTV 换台 |
| `/api/iptv/channels` | GET | IPTV 频道列表 |
| `/api/player/play` | POST | 播放器控制 |
| `/api/fongmi/play` | POST | 小甜菜播放 |
| `/api/xiaoai/keywords` | GET/POST | 小爱关键词配置 |
| `/poc/overlay/*` | 多种 | PoC 实验端点（可删除） |

### 3.4 MQTT 客户端（MqttManager.java）

- Broker：`tcp://192.168.2.200:1883`，user=butler
- client_id：`tv-livingroom`
- 主题前缀：`tv/livingroom`

**订阅（命令入站）：** `tv/livingroom/cmd/#`

| 子主题 | 载荷 | 说明 |
|---|---|---|
| `cmd/tts` | `{text}` | TTS 播放 |
| `cmd/notify` | `{title, text, duration_ms}` | 弹窗通知 |
| `cmd/overlay` | `{text, duration_ms}` 或 `{action:"hide"}` | 状态悬浮窗 |
| `cmd/player` | `{action, ...}` | 播放器控制 |
| `cmd/reboot` | - | 重启 App |
| `cmd/xiaoai_monitor` | `{enable}` | 小爱监听开关 |
| `cmd/xiaoai_interrupt` | - | 打断小爱 |
| `cmd/xiaoai_set_keywords` | `{keywords[]}` | 设置关键词 |

**发布（状态出站）：**

| 主题 | QoS | Retain | 说明 |
|---|---|---|---|
| `status` | 1 | true | online/offline（LWT） |
| `state` | 1 | true | 当前识别状态（谁在、状态） |
| `face` | 0 | false | 单次识别结果 |
| `face/fused` | 0 | false | 融合后的识别事件 |
| `stats` | 0 | false | 统计数据 |
| `control` | 1 | false | 控制请求（转发给 zap-tv） |
| `fongmi/control` | 1 | false | 小甜菜控制 |
| `wake` | 1 | false | 唤醒事件 |
| `tts_done` | 0 | false | TTS 完成回调 |
| `experiment` | 0 | false | 实验数据（可删除） |

### 3.5 无障碍服务（TvAccessibilityService.java）

- 1681 行，最脆弱的模块
- 监听小爱同学界面的节点树变化，提取语音转文字结果
- 通过 `AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED` 触发
- 关键词匹配后通过 MQTT 发布 `control` 主题，zap-tv 收到后执行 ADB 操作
- **风险**：小爱 App 更新后节点结构可能变化，需要重新适配

### 3.6 状态悬浮窗（overlay/）

- `OverlayManager`：单例，顶部半透明黑底白字横幅
- `OverlayService`：前台 Service 保活
- `OverlayHttp`：HTTP 处理器
- 关键属性：`FLAG_NOT_FOCUSABLE | FLAG_NOT_TOUCHABLE`，**不抢焦点不碰遥控器**
- 默认 3 秒自动消失，可通过 `duration_ms` 调整
- 用途：TVPilot 执行操作时在电视上显示"AI 正在操作：XXX"，让用户知道 agent 在干活

### 3.7 PoC 实验代码（experiment/overlaypoc/）

- 独立包，不随主进程启动
- 包含 PoC 悬浮窗、控制器、HTTP 端点
- **可一键删除**：删除 `experiment/` 目录 + Manifest 中 `OverlayPocService` 注册 + `FaceHttpServer` 中 `/poc/overlay/` 路由

---

## 四、构建与部署

### 4.1 编译

```powershell
# 编译脚本（已配置 JDK 和 Gradle 路径）
D:\Documents\WorkSpace\TV_CAM\build_apk.ps1
# 日志：build_log2.txt
# 输出：Arcface\app\build\outputs\apk\debug\app-debug.apk
```

### 4.2 部署到电视

```bash
# 确保 ADB 连接（电视重启后网络 ADB 默认关闭，需手动开）
adb connect 192.168.2.238:5555

# 安装
adb -s 192.168.2.238:5555 install -r app-debug.apk

# 重启 App
adb -s 192.168.2.238:5555 shell am force-stop com.example.arcfaceandroid
adb -s 192.168.2.238:5555 shell am start -n com.example.arcfaceandroid/.TvMainActivity
```

### 4.3 悬浮窗权限（首次安装后）

```bash
adb shell appops set com.example.arcfaceandroid SYSTEM_ALERT_WINDOW allow
```

### 4.4 日志查看

```bash
# 全部日志
adb -s 192.168.2.238:5555 logcat

# 过滤识别日志
adb -s 192.168.2.238:5555 logcat -s FaceServer:V FaceServerService:V

# 识别结果行（最常用）
adb -s 192.168.2.238:5555 logcat | findstr RECOGNIZED
```

---

## 五、关键技术发现（接手必读）

### 5.1 VIDEO 模式根因（重要）

**前任结论"SDK 不支持 VIDEO 模式"是误诊。**

- 现象：VIDEO 模式初始化成功，但 `detectFaces` 连续返回空
- 根因：帧采样间隔 350ms（3fps），VIDEO 模式内部跟踪器需要连续帧，间隔太大跟丢
- 解决：分析间隔降到 100ms（10fps），VIDEO 模式立即正常
- 验证数据：检测率 97.4%，匹配率 100%，faceId 连续 20 帧不变，平均相似度 0.945
- 当前状态：已切换到 VIDEO 模式并部署，运行稳定

### 5.2 悬浮窗 PoC 结论

三项实测全部通过：
1. **视频层置顶**：透明悬浮窗能盖在 IPTV 硬件解码播放画面上（用户现场拍照确认）
2. **焦点劫持**：抢/放遥控器焦点正常，不卡遥控器（生产版设为 NOT_FOCUSABLE，不抢）
3. **后台存活**：普通 Service 30 分钟不杀，前台服务可起

### 5.3 已知限制

- `adb screencap` 在 IPTV 硬件视频层上抓出全黑（硬件 surface 不参与截屏），验证视频上悬浮窗需用户现场拍照
- 电视重启后网络 ADB 默认关闭（5555 refused 时就是这个原因）
- ARM32 设备，部分新库可能不兼容

---

## 六、与 TVPilot 侧的集成点

### 6.1 TVPilot → Arcface（控制）

TVPilot / zap-tv / doubao-butler 通过以下方式控制 Arcface：

1. **HTTP 调用**：`http://192.168.2.238:8080/api/*`
2. **MQTT 发命令**：发布到 `tv/livingroom/cmd/*`

### 6.2 Arcface → TVPilot（感知）

Arcface 通过 MQTT 发布状态，TVPilot 订阅：

- `tv/livingroom/state`：谁在电视前、识别状态
- `tv/livingroom/face/fused`：识别事件（人进入/离开/稳定）
- `tv/livingroom/control`：小爱语音指令转成的控制请求
- `tv/livingroom/wake`：唤醒事件

### 6.3 悬浮窗集成（推荐）

TVPilot 执行任何电视操作前，先发一条 MQTT 命令弹状态提示：

```bash
# 主题：tv/livingroom/cmd/overlay
# 载荷：{"text": "AI 正在操作：换到 湖南卫视", "duration_ms": 3000}
```

操作完成后可自动消失（默认 3s），或发 `{"action":"hide"}` 主动隐藏。

集成说明详见：`D:\Documents\WorkSpace\TV_CAM\docs\悬浮窗-状态提示集成说明-20260922.md`

---

## 七、后续方向与未完成项

### 7.1 高优先级

1. **TVPilot 侧集成悬浮窗状态提示**：agent 执行换台/按键/搜片时发 MQTT 弹提示。zap-tv 源码在 `D:\Documents\WorkSpace\TV_CAM\tmp\zap_tv\`
2. **多人场景实测**：VIDEO 模式 faceId 在两人交叉/遮挡时是否稳定，目前只验证了单人
3. **设计文档更新**：`docs/设计-透明悬浮窗完整开发-20260923.md` 仍含菜单/EPG 内容，与最终决策（纯状态浮层）不符

### 7.2 中优先级（调优）

1. **分析间隔**：100ms 是实验值，可测试 150ms 看 VIDEO 跟踪是否仍稳定，省 CPU
2. **全图扫描频率**：`FULL_SCAN_EVERY=6`，faceId 稳定后可调到 10
3. **CPU/内存监控**：100ms 间隔 + VIDEO 模式的实际资源占用需长期观察

### 7.3 低优先级（清理）

1. **删除 PoC 实验代码**：`experiment/overlaypoc/` 包 + `/poc/overlay/` 路由 + Manifest 注册
2. **清理 MQTT experiment 主题**
3. **无障碍服务重构**：1681 行过于臃肿，小爱节点解析逻辑可抽离

---

## 八、应急恢复

```bash
# 遥控器失灵/悬浮窗卡住：强制停止 Arcface
adb -s 192.168.2.238:5555 shell am force-stop com.example.arcfaceandroid

# 电视无响应：重启
adb -s 192.168.2.238:5555 reboot

# ADB 连不上（5555 refused）：电视重启后网络 ADB 默认关闭，需在电视设置里手动开启
```

---

## 九、项目文档索引

| 文档 | 路径 |
|---|---|
| 原始工单 | `docs/工单-Arcface透明悬浮窗PoC实验-20260922.md` |
| PoC 实验报告 | `docs/报告-Arcface透明悬浮窗PoC实验-20260922.md` |
| 悬浮窗集成说明 | `docs/悬浮窗-状态提示集成说明-20260922.md` |
| 语音控制交接文档 | `docs/电视语音控制_IPTV_交接文档.md` |
| 设计文档（待更新） | `docs/设计-透明悬浮窗完整开发-20260923.md` |

---

*交接完毕。有问题先看 `FaceHttpServer.java` 的端点 switch 和 `MqttManager.java` 的 handleCommand，这两个是对外接口的总入口。*
