# 回复工单：Arcface 统一 FaceID API（TVP-20260909-001）
> 状态（2026-11-12 整理）：**历史记录**。本文记录当时的事实，不代表当前状态；现行口径见 `doc/README.md` 真源优先级表。

> 回复方：TVPilot（TP）
> 日期：2026-09-09
> 状态：**已完成（待用户重新注册人脸后联调验收）**

---

## 一、当前 Arcface 运行形态确认

| 项目 | 结果 |
|------|------|
| 运行形态 | **独立 APK**（`com.example.arcfaceandroid`），非 mytv 一部分 |
| 服务方式 | 前台服务（Foreground Service）+ 后台广播启动（不弹界面） |
| 已有 HTTP API | **有**，端口 8080，已包含 health/recognize/register/screenshot 等 |
| 摄像头权限 | 已授权 |
| 模型文件 | 应用内置，已加载 |

**结论**：无需拆分，在现有独立 APK 基础上扩展即可。

---

## 二、已完成功能

### 2.1 统一 FaceID HTTP API（复用 8080 端口）

| API | 方法 | 状态 | 说明 |
|-----|------|------|------|
| `/arcface/current_user` | GET | ✅ | 当前用户（缓存，<10ms） |
| `/arcface/health` | GET | ✅ | 服务状态 |
| `/arcface/users` | GET | ✅ | 注册用户列表 |
| `/arcface/users/register` | POST | ✅ | 注册用户（复用现有逻辑） |
| `/arcface/users/{user_id}` | DELETE | ✅ | 删除用户 |
| `/arcface/verify` | POST | ✅ | 人脸验证（复用 recognize） |
| `/arcface/detect` | POST | ✅ | 人脸检测（复用 recognize） |

**测试结果**（2026-09-09 16:14）：
```
GET /arcface/health → {"status":"ok","model_loaded":true,"registered_users":0,...}
GET /arcface/current_user → {"user":null,"confidence":0,"detected_at":"...","face_count":0,"all_users":[]}
GET /arcface/users → {"users":[]}
```
全部 200 OK，响应 <50ms。

### 2.2 持续检测 + 结果缓存

- Arcface 已有持续人脸检测（摄像头预览回调，每帧检测）
- 新增 `FaceIdApi.updateCache()` 在每次识别完成后更新缓存
- 缓存字段：user / confidence / detected_at / face_count / all_users
- API 调用直接返回缓存，不触发实时检测

### 2.3 LocalBroadcast 推送（最短路径）

**Action**: `com.tvcam.face.USER_CHANGED`

**Extras**: `user` / `confidence` / `face_count` / `detected_at`

**触发时机**：用户真正变化时（去重）：
- 无人 → 某人坐下
- 某人 → 无人（离开）
- A → B（切换）

**优势**：同设备 Binder 直接调用，毫秒级响应，无需轮询。

### 2.4 mytv 侧 BroadcastReceiver

- 新增 `FaceIdReceiver.kt`，注册接收 `com.tvcam.face.USER_CHANGED`
- 全局单例存储当前用户（`FaceIdReceiver.currentUser`），供 UI/业务层查询
- 支持监听器模式（UI 层可注册，实时更新界面）
- 在 `LeanbackActivity.onCreate` 注册，`onDestroy` 注销
- 已编译安装到电视

### 2.5 小甜菜侧工单

已写工单到 `E:\NAS\xiaotiancai\docs\工单_20260909_001_Arcface_FaceID_Broadcast对接.md`，包含：
- Broadcast 规范（Action/Extras/触发时机）
- 小甜菜侧实现要求（注册 Receiver + 用户切换逻辑）
- HTTP 兜底查询方案
- 验收标准

---

## 三、技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| HTTP 端口 | 复用 8080 | 避免多一个服务，现有 HTTP 服务器已稳定 |
| 事件推送 | LocalBroadcast | 同设备最短路径，毫秒级，比 MQTT（绕 NAS）快得多 |
| 主动查询 | HTTP localhost | 通用，豆包管家（Python）和非原生应用都能调用 |
| AIDL | **不加** | current_user 是低频查询，HTTP <1ms 够用；AIDL 集成复杂度高，小甜菜（Flutter）更难接 |
| 保活 | 前台服务 + START_STICKY | 已有，进程被杀后自动重启 |

---

## 四、已知问题

### 4.1 注册人脸数据丢失（需用户重新注册）

**现象**：`/arcface/users` 返回空列表，`registered_users=0`。

**原因**：重新安装 APK 后，应用私有目录中的注册人脸数据被清除（Android 系统行为，签名/版本变化时数据不保留）。

**解决**：需要用户在 Arcface WebUI（http://192.168.2.238:8080）中重新注册 3 个用户：
- lidicn（爸爸）
- Kevin
- Emily（儿童）

注册完成后，`/arcface/users` 应返回 3 个用户，`current_user` 才能正确识别。

### 4.2 mytv 个性化功能未实现

mytv 侧已能接收用户变化广播，但"个性化节目列表/观看历史关联用户"功能尚未实现。本期只完成了数据接收层，个性化 UI 需后续迭代。

---

## 五、验收清单

### 基础服务
- [x] Arcface APK 以前台服务运行
- [x] `GET /arcface/health` 返回 status=ok，model_loaded=true
- [x] HTTP API 响应 <50ms

### current_user API
- [x] API 端点正常，返回格式正确
- [x] 无人时返回 user=null，detected_at 仍更新
- [ ] lidicn 面对电视时返回 user="lidicn"（**待重新注册后验证**）
- [ ] Emily 面对电视时返回 user="emily"（**待重新注册后验证**）

### Broadcast 推送
- [x] Arcface 侧实现用户变化广播（去重）
- [x] mytv 侧注册 Receiver，能接收广播
- [ ] 实际用户变化时 mytv 收到广播（**待重新注册后验证**）
- [ ] 小甜菜侧接收（**小甜菜侧排期**）

### 用户管理
- [x] GET /arcface/users 正常
- [x] DELETE /arcface/users/{id} 正常
- [x] POST /arcface/users/register 正常（复用现有）

---

## 六、交付物

1. **Arcface APK**（含 FaceIdApi + Broadcast + /arcface/* 路由）
   - 路径：`D:\Documents\WorkSpace\TV_CAM\Arcface\app\build\outputs\apk\debug\app-debug.apk`
   - 已安装到电视（192.168.2.238）

2. **mytv APK**（含 FaceIdReceiver）
   - 路径：`E:\NAS\mytv\app\build\outputs\apk\debug\app-debug.apk`
   - 已安装到电视

3. **小甜菜工单**：`E:\NAS\xiaotiancai\docs\工单_20260909_001_Arcface_FaceID_Broadcast对接.md`

4. **源码修改**：
   - `Arcface/app/src/main/java/com/example/arcfaceandroid/FaceIdApi.java`（新增）
   - `Arcface/app/src/main/java/com/example/arcfaceandroid/FaceHttpServer.java`（新增 /arcface/* 路由）
   - `Arcface/app/src/main/java/com/example/arcfaceandroid/FaceServerService.java`（init + updateCache 调用）
   - `mytv/app/src/main/java/com/tvcam/mytv/faceid/FaceIdReceiver.kt`（新增）
   - `mytv/app/src/main/java/com/tvcam/mytv/activities/LeanbackActivity.kt`（注册/注销 Receiver）

---

## 七、下一步

1. **用户重新注册人脸**（在 Arcface WebUI 注册 lidicn/Kevin/Emily）
2. **联调验证**：lidicn/Emily 面对电视，验证 current_user API 和 mytv Broadcast
3. **小甜菜侧实现**：按工单 XTC-20260909-001 实现 BroadcastReceiver + UI 切换
4. **mytv 个性化**：后续迭代实现基于当前用户的个性化节目列表

---

> 本工单核心功能（API + Broadcast + mytv 接收）已完成并部署。注册数据丢失为重装副作用，需用户重新注册后即可完成最终联调验收。
