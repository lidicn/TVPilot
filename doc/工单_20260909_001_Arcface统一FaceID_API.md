# 工单：Arcface APK 增加统一 FaceID 检测 API（供小甜菜/mytv 调用）
> 状态（2026-11-12 整理）：**历史记录**。本文记录当时的事实，不代表当前状态；现行口径见 `doc/README.md` 真源优先级表。

> 工单编号：TVP-20260909-001
> 下发方：豆包管家（PM）
> 承接方：TVPilot（TP）
> 日期：2026-09-09
> 优先级：P0
> 状态：待接手
> 关联：小甜菜 v1.0 客厅 Vibe Coding（依赖本工单）、mytv 人脸登录（后续依赖）

---

## 一、背景与目标

Arcface APK 已部署到电视（`192.168.2.238:5555`），具备人脸检测/识别能力，3 个已注册用户（lidicn/Kevin/Emily）。

**当前问题**：Arcface 的 FaceID 能力没有暴露为统一的 HTTP API，其他应用（小甜菜、mytv）无法调用。每个应用都自己集成 Arcface 会导致：
- 重复开发（小甜菜已集成一份，但会被杀进程无法持续检测）
- 模型重复加载（多个 APK 各加载一份 Arcface 模型，占内存）
- 识别结果不一致（不同应用的阈值/预处理可能不同）

**目标**：Arcface APK 作为电视端统一的 FaceID 服务，常驻运行（前台服务保活，不被杀进程），提供 HTTP API 供小甜菜、mytv、豆包管家等调用。

---

## 二、Arcface APK 定位

```
Arcface APK = 电视端统一 FaceID 服务（常驻）
  ├── 持续人脸检测（后台运行，识别当前谁在电视前）
  ├── HTTP API 服务（本地端口，供其他应用调用）
  ├── 人脸注册管理（增删改查注册用户）
  └── 识别结果缓存（最近一次检测结果，降低调用延迟）

调用方：
  ├── 小甜菜（查询当前用户 → 切换 Vibe/儿童 UI）
  ├── mytv（人脸登录 → 个性化推荐/观看历史）
  └── 豆包管家（查询当前用户 → 决策弹窗权限判断）
```

---

## 三、API 设计（需实现）

### 3.1 当前用户查询（核心，小甜菜 v1.0 依赖）

```
GET http://127.0.0.1:8098/arcface/current_user

说明：返回最近一次人脸检测识别到的用户。Arcface 后台持续检测（建议每 3-5 秒一次），缓存最近结果。

Response 200:
{
  "user": "lidicn",          // "lidicn" | "kevin" | "emily" | null（无人/未识别）
  "confidence": 0.95,         // 识别置信度 0-1，低于阈值返回 null
  "detected_at": "2026-09-09T20:30:00",  // 检测时间
  "face_count": 1,            // 检测到的人脸数量
  "all_users": ["lidicn"]     // 所有人脸对应的用户（多人时返回列表）
}

Response 503:
{ "error": "Arcface service not ready", "code": "SERVICE_NOT_READY" }
```

**关键要求**：
- 后台持续检测，不依赖调用方触发
- 检测间隔可配置（默认 3-5 秒，平衡功耗和实时性）
- 结果缓存，API 调用直接返回缓存（<10ms 响应）
- 无人时 `user=null`，但 `detected_at` 仍更新
- 多人时 `user` 返回置信度最高/距离最近的人，`all_users` 返回全部

### 3.2 人脸验证（mytv 登录用）

```
POST http://127.0.0.1:8098/arcface/verify
Content-Type: application/json

Body:
{
  "image": "base64...",       // 待验证的人脸图片（base64 编码 JPEG）
  "user_id": "lidicn"         // 可选，指定验证某个用户；不填则识别最匹配的用户
}

Response 200:
{
  "user": "lidicn",
  "confidence": 0.92,
  "verified": true,            // confidence > 阈值（默认 0.8）
  "threshold": 0.8
}

Response 400:
{ "error": "No face detected in image", "code": "NO_FACE" }
```

### 3.3 人脸检测（返回人脸位置，供 UI 标注）

```
POST http://127.0.0.1:8098/arcface/detect
Content-Type: application/json

Body:
{
  "image": "base64..."
}

Response 200:
{
  "face_count": 1,
  "faces": [
    {
      "rect": {"x": 100, "y": 200, "width": 150, "height": 150},
      "user": "lidicn",        // 识别到的用户，未知为 null
      "confidence": 0.90
    }
  ]
}
```

### 3.4 用户管理（注册/查询/删除）

```
GET /arcface/users
Resp: {users: [{id: "lidicn", name: "lidicn", registered_at: "...", face_count: 5}]}

POST /arcface/users/register
Body: {user_id: "lidicn", name: "爸爸", images: ["base64...", "base64..."]}
Resp: {ok: true, user_id: "lidicn"}

DELETE /arcface/users/{user_id}
Resp: {ok: true}
```

### 3.5 服务状态

```
GET /arcface/health
Resp: {
  "status": "ok",
  "model_loaded": true,
  "registered_users": 3,
  "last_detect_at": "2026-09-09T20:30:00",
  "last_detect_user": "lidicn",
  "uptime_sec": 3600,
  "detect_interval_sec": 5
}
```

---

## 四、技术要求

### 4.1 服务常驻保活

- Arcface APK 以前台服务（Foreground Service）运行，显示常驻通知（"Arcface 人脸服务运行中"）
- 开机自启（BOOT_COMPLETED 广播接收器）
- 进程被杀后自动重启（Service.START_STICKY）
- 监听用户切换（USER_PRESENT），锁屏时暂停检测节省功耗，解锁后恢复

### 4.2 HTTP 服务

- 使用 NanoHTTPD（轻量级，无外部依赖）或 Android 内置 HttpServer
- 监听 `127.0.0.1:8098`（仅本地访问，不暴露外网）
- 端口可配置（SharedPreferences）
- 并发处理（多线程，避免检测阻塞 API 响应）

### 4.3 持续检测

- 后台线程每 `detect_interval_sec`（默认 5 秒）从摄像头取一帧
- 调用 Arcface SDK 检测+识别
- 更新缓存（current_user / confidence / detected_at）
- 检测失败（摄像头被占用/无权限）不崩溃，记录错误，下次重试
- 可配置检测阈值（confidence_threshold，默认 0.8）

### 4.4 性能与功耗

- 模型加载一次，常驻内存
- 检测间隔可配置（3-10 秒），默认 5 秒
- 锁屏时暂停检测，解锁后恢复
- 摄像头取帧用最低可用分辨率（640x480 足够人脸识别），降低 CPU 占用
- API 调用返回缓存，不触发实时检测（除 verify/detect 主动调用外）

### 4.5 错误处理

| 错误码 | 说明 | HTTP 状态 |
|--------|------|----------|
| SERVICE_NOT_READY | 模型未加载完成 | 503 |
| NO_FACE | 图片中未检测到人脸 | 400 |
| CAMERA_ERROR | 摄像头不可用 | 500 |
| INVALID_IMAGE | 图片格式错误 | 400 |
| USER_NOT_FOUND | 用户不存在 | 404 |

---

## 五、与现有 Arcface 的关系

### 当前状态
- Arcface 已编译部署到电视，3 用户注册
- 可能是作为独立 APK 或 mytv 的一部分运行
- 编译环境文档在 `E:\NAS\xiaotiancai\doc\`（TP 输出）

### 需要确认/改造
1. **当前 Arcface 的运行形态**：是独立 APK 还是 mytv 的一部分？如果是 mytv 的一部分，需要拆分为独立 APK（或至少独立服务）
2. **当前是否有 HTTP API**：如果已有部分 API，在此基础上扩展；如果没有，新增 NanoHTTPD 服务
3. **摄像头权限**：Arcface 是否有摄像头权限？持续检测需要 CAMERA 权限
4. **模型文件位置**：Arcface 模型文件在电视上的路径，确保服务能加载

### 改造建议
- 如果当前 Arcface 是 mytv 的一部分：**拆分为独立 APK `com.arcface.service`**，mytv 通过 HTTP API 调用
- 如果当前已是独立 APK：在现有基础上增加前台服务保活 + NanoHTTPD API + 持续检测
- 保持向后兼容：现有 mytv 的 Arcface 调用方式（如果是本地 SDK 调用）可以保留，同时新增 HTTP API 供其他应用

---

## 六、验收标准

### 基础服务
- [ ] Arcface APK 安装后以前台服务运行，通知栏显示"Arcface 人脸服务运行中"
- [ ] 开机自启（重启电视后服务自动启动）
- [ ] 进程被杀后 5 秒内自动重启
- [ ] `GET /arcface/health` 返回 status=ok，model_loaded=true

### current_user API（核心）
- [ ] lidicn 面对电视时，`GET /arcface/current_user` 返回 user="lidicn"，confidence>0.8
- [ ] 无人时返回 user=null，detected_at 仍更新
- [ ] Emily 面对电视时返回 user="emily"
- [ ] API 响应时间 <50ms（返回缓存）
- [ ] 检测到结果后 5 秒内缓存更新

### verify API
- [ ] POST lidicn 照片 → 返回 user="lidicn", verified=true
- [ ] POST 陌生人照片 → 返回 verified=false
- [ ] POST 无人脸照片 → 返回 400 NO_FACE

### 用户管理
- [ ] GET /arcface/users 返回 3 个用户（lidicn/Kevin/Emily）
- [ ] POST 注册新用户 → 注册成功，users 列表新增
- [ ] DELETE 用户 → 删除成功

### 功耗与稳定性
- [ ] 连续运行 1 小时无崩溃，内存占用稳定（<200MB）
- [ ] 锁屏时检测暂停，解锁后 5 秒内恢复检测
- [ ] 摄像头被其他应用占用时不崩溃，记录错误，释放后自动恢复

### 联调验收（必须用户在场）
- [ ] 小甜菜调用 current_user API，lidicn 在时切换 Vibe UI，Emily 在时切换儿童 UI
- [ ] mytv 调用 verify API 实现人脸登录

---

## 七、依赖与风险

### 依赖
- Arcface 编译环境（TP 已有，`E:\NAS\xiaotiancai\doc\`）
- 电视设备 `192.168.2.238:5555`（ADB 连接）
- 摄像头权限（电视端需授权）

### 风险
| 风险 | 影响 | 缓解 |
|------|------|------|
| Arcface 当前是 mytv 的一部分，拆分工作量大 | 工期延长 | 评估拆分成本；如果太大，先在 mytv 内增加 HTTP API（不拆分），后续再拆 |
| 电视摄像头被 mytv/小甜菜占用，Arcface 无法持续取帧 | current_user 不更新 | 检测失败时记录错误，释放后自动恢复；考虑与 mytv 协调摄像头使用 |
| 前台服务在某些电视上被系统限制 | 服务被杀不重启 | 测试目标电视的保活机制；必要时申请系统应用权限或用无障碍服务保活 |
| Arcface SDK 与 Android 版本兼容性 | 编译/运行失败 | 使用 TP 已验证的编译环境，不升级 SDK |

---

## 八、参考文档

- Arcface 编译环境：`E:\NAS\xiaotiancai\doc\`（TP 输出）
- 小甜菜 v1.0 路线图：`E:\NAS\xiaotiancai\docs\路线图_小甜菜_v1.0.md`
- 小甜菜工单：`E:\NAS\xiaotiancai\docs\工单_20260909_001_v1.0客厅VibeCoding.md`
- 电视设备：`192.168.2.238:5555`
- 已注册用户：lidicn / Kevin / Emily

---

## 九、交接确认

- [ ] TP 已接手本工单
- [ ] 确认当前 Arcface 运行形态（独立 APK / mytv 一部分）
- [ ] 确认是否已有 HTTP API
- [ ] 基础服务（前台保活+NanoHTTPD+health）完成
- [ ] current_user API（核心）完成
- [ ] verify/detect/users API 完成
- [ ] 与小甜菜联调通过
- [ ] 与 mytv 联调通过（或 mytv 侧排期）

> 接手后请回复本工单（在同目录创建 `回复_工单_20260909_001.md`），说明：
> 1. 当前 Arcface 运行形态确认结果
> 2. 是否已有 HTTP API
> 3. 预计完成时间
> 4. 拆分/改造方案
>
> 完成后标注状态为"已完成"，输出交付报告。
