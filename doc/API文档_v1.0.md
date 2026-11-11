# TVPilot API 文档 v1.0

> 版本：v0.8.1 | 日期：2026-09-24
> 基础地址：`http://192.168.2.200:8090`
> 统一响应格式：`{ok, tool, result, cost_ms, error, message}`

---

## 一、统一响应格式

### 成功响应
```json
{
  "ok": true,
  "tool": "zap",
  "result": { },
  "cost_ms": 1234
}
```

### 失败响应
```json
{
  "ok": false,
  "tool": "zap",
  "error": "invalid_parameter",
  "message": "频道不存在",
  "cost_ms": 12
}
```

### 错误码矩阵

| 错误码 | 含义 | HTTP状态码 |
|---|---|---|
| `tv_unreachable` | 电视/ADB 不可达 | 503 |
| `app_not_responding` | 目标应用无响应 | 504 |
| `wrong_foreground` | 前台应用不匹配 | 409 |
| `invalid_parameter` | 参数无效 | 400 |
| `operation_timeout` | 操作超时 | 504 |
| `mytv_api_error` | mytv HTTP API 错误 | 502 |
| `not_implemented` | 功能未实现 | 501 |
| `channel_no_number` | 频道无台号映射 | 404 |
| `unauthorized` | 未授权 | 401 |
| `rate_limited` | 触发限流 | 429 |
| `already_running` | 任务已在运行 | 409 |

---

## 二、状态查询（GET）

### 2.1 GET /api/health
服务健康检查。`?strict=1` 时 degraded 返回 503。

### 2.2 GET /api/foreground
查询当前前台包名。

### 2.3 GET /api/current
查询当前播放频道（台号来自电信机顶盒 App SharedPreferences）。

### 2.4 GET /api/ui_state
智能界面状态识别（桌面/播放/设置等）。

### 2.5 GET /api/play_state
播放状态识别（播放中/暂停/缓冲/黑屏）。

### 2.6 GET /api/system_state
系统状态聚合（网络/存储/内存/前台应用）。

---

## 三、核心控制（POST）

### 3.1 POST /api/zap
换台（ADB 数字键注入电信机顶盒）。
**Body**：`{"channel": "CCTV-1", "request_id": "xxx"}`

### 3.2 POST /api/keyevent
发送按键。**Body**：`{"key": "KEYCODE_ENTER"}`

### 3.3 POST /api/tap
点击坐标。**Body**：`{"x": 540, "y": 180}`

### 3.4 POST /api/swipe
滑动。**Body**：`{"x1": 100, "y1": 500, "x2": 100, "y2": 200, "duration_ms": 300}`

### 3.5 POST /api/input_text
输入文字（支持中文，tvremoteime 广播）。**Body**：`{"text": "你好"}`

### 3.6 POST /api/launch_app
启动应用。**Body**：`{"package": "com.tvcam.mytv", "activity": ".activities.LeanbackActivity"}`

### 3.7 POST /api/wait
等待。**Body**：`{"seconds": 2}`

### 3.8 POST /api/combo/go_home
组合动作：回桌面+前台验证。

### 3.9 POST /api/combo/trim_search_play
组合动作：飞牛TV搜索播放。**Body**：`{"keyword": "流浪地球"}`

---

## 四、媒体（GET+POST）

### 4.1 GET/POST /api/screenshot
截图。`fmt=png|json`, `width=480`

### 4.2 GET /api/analyze
截图多模态分析（doubao2api）。`prompt=描述画面`, `quality=low|high`

### 4.3 GET /api/ocr
OCR 文字识别（tesseract）。`text=过滤关键词`

### 4.4 GET /api/tts
EdgeTTS 语音合成。`text=你好`, 返回 `audio/mpeg`

### 4.5 GET /api/audio
查询音频状态（音量/静音/音频设备）。

### 4.6 POST /api/audio/volume
设置音量。**Body**：`{"level": 50}`

### 4.7 POST /api/audio/mute
静音/取消静音。

### 4.8 POST /api/audio/volume_up / volume_down
音量±1。

### 4.9 GET /api/screenshot_smart
智能截图。`fmt=json|png`, `width=480`, `quality=low|medium|high`

---

## 五、无障碍（GET+POST，19个端点）

> 集成到 Arcface App :8081，TVPilot 代理。uiautomator dump 不依赖电视端无障碍 APK。

| 方法 | 端点 | 说明 |
|---|---|---|
| GET | /api/a11y/health | 无障碍服务健康检查 |
| GET | /api/a11y/tree | 节点树（`depth=20`） |
| GET | /api/a11y/text | 屏幕所有文字 |
| GET | /api/a11y/focus | 当前焦点节点 |
| GET | /api/a11y/dump | uiautomator dump |
| POST | /api/a11y/find | 查找节点（`by=text|id|class|desc`） |
| POST | /api/a11y/click | 点击节点 |
| POST | /api/a11y/input | 输入文字 |
| POST | /api/a11y/scroll | 滚动（`forward|backward`） |
| POST | /api/a11y/key | 发送按键 |
| POST | /api/a11y/tap | 点击坐标 |
| POST | /api/a11y/focus | 焦点移动（`up|down|left|right`） |
| POST | /api/a11y/long_click | 长按节点 |
| POST | /api/a11y/wait | 等待节点出现/消失 |
| POST | /api/a11y/global | 全局动作（`back|home|recents|notifications|quick_settings`） |
| POST | /api/a11y/node | 节点详细信息 |
| POST | /api/a11y/wait_text | uiautomator 轮询等待文字 |
| POST | /api/a11y/tap_text | uiautomator 按文字点击 |

---

## 六、IPTV/EPG（GET）

| 端点 | 说明 |
|---|---|
| GET /api/channels | 频道列表（80个，60个可换台） |
| GET /api/epg | EPG 节目单代理 |
| GET /iptv/source.m3u | IPTV 直播源 M3U |
| GET /iptv/logo | 台标 SVG/PNG |
| GET /api/iptv/refresh_status | RTSP 刷新状态（归档） |
| POST /api/iptv/refresh_rtsp | 触发 RTSP 刷新（需 Token，归档） |

---

## 七、用户/FaceID（GET+POST）

| 方法 | 端点 | 说明 |
|---|---|---|
| GET | /api/users | 列出用户配置 |
| POST | /api/users | 保存用户配置 |
| GET | /api/users/switch | 切换用户（`?user=lidicn`） |
| GET | /api/face/restore | 拉取人脸特征备份 |
| POST | /api/face/backup | 保存人脸特征备份 |
| GET | /api/history | 操作存档查询（`limit=50`, `request_id=xxx`） |

---

## 八、远场语音（POST，转发到 Arcface :8080）

| 端点 | 说明 |
|---|---|
| POST /api/ask/listen | 启动聆听（`{"duration": 30}`） |
| POST /api/ask/stop | 停止聆听 |
| POST /api/ask/beep | 播放提示音 |

---

## 九、监控告警（GET，v1.0-T2 新增）

### 9.1 GET /api/metrics
工具调用统计。`minutes=60`（1~1440）
**响应**：总调用数、成功率、按工具分组（次数/成功率/avg/P95/P99延迟/最近错误）

### 9.2 GET /api/alerts
告警历史。`limit=50`, `severity=info|warning|critical`

### 9.3 GET /api/monitor/summary
监控摘要（健康状态+活跃告警+最近告警）

---

## 十、PWA（GET）

| 端点 | 说明 |
|---|---|
| GET /remote | PWA 遥控器页面 |
| GET /remote-static/* | PWA 静态资源 |

---

## 十一、旧版兼容（GET，逐步废弃）

| 端点 | 替代 |
|---|---|
| GET /health | /api/health |
| GET /channels | /api/channels |
| GET /active | /api/current |
| GET /zap?channel=xxx | POST /api/zap |
| GET /screenshot | /api/screenshot |
| GET /fongmi/search | POST /api/combo/trim_search_play |
| GET /keyevent?key=xxx | POST /api/keyevent |
| GET /tap?x=&y= | POST /api/tap |

---

## 十二、MQTT 接口

- **订阅**：`tv/livingroom/control` — `{"action": "zap", "channel": "CCTV-1"}`
- **订阅**：`tv/livingroom/trim/control` — 飞牛TV控制
- **发布**：`tv/livingroom/result` — 执行结果

---

## 十三、重要说明

1. **换台机制**：ADB 数字键注入电信机顶盒，不依赖 mytv。
2. **台号映射**：60 个频道有台号映射（channels.json 外挂卷）。
3. **中文输入**：tvremoteime 广播，需电视端安装该输入法。
4. **无障碍服务**：集成在 Arcface App（:8081），uiautomator dump 不依赖无障碍 APK。
5. **RTSP 刷新**：已归档保留，需 X-Admin-Token 鉴权。
6. **HDMI3 时长**：已切换到 memory-agent 通道，TVPilot 侧 ADB 查询可通过开关关闭。

---

*TVPilot API 文档 | 2026-09-24 | v1.0*
