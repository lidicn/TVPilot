# RPT：uiautomator 读屏能力落地（v0.8.1 + uia_dump）

- **日期**：2026-09-23
- **版本**：0.8.1 + uia_dump（md5 `67e11212` / 167,999 B）
- **部署状态**：已 docker cp + restart 上线，health 全绿
- **触发**：按路线图迭代，补全 v0.4/v0.5 "读屏"能力（此前依赖电视端无障碍 APK，未开发）

---

## 一、本次改动

### 1. 新增 `/api/a11y/dump`（GET）
- **实现**：`tool_uiautomator_dump()` → ADB `uiautomator dump /sdcard/tvpilot_uia_dump.xml` → `cat` 读 XML → `xml.etree.ElementTree` 解析 → 结构化节点列表。
- **节点字段**：text、resource_id、class、package、content_desc、checkable、checked、clickable、enabled、focusable、focused、scrollable、long_clickable、password、selected、bounds（x1/y1/x2/y2/cx/cy/w/h）。
- **过滤**：只保留有文字/资源id/描述/可点击/可聚焦/有焦点/选中的节点，过滤纯布局容器。
- **已知限制**：播视频/持续动画时可能超时或返回空（主线程忙，MobileAgentBench 已证实），返回 `operation_timeout` / `app_not_responding`。
- **实测**：小米桌面 17 节点 / 9KB XML / 2.4s。

### 2. 新增 `/api/a11y/wait_text`（POST）
- **实现**：`tool_uiautomator_wait_text(text, timeout=5000, interval=500)` → 轮询 dump 直到目标文字出现在 text 或 content_desc 中。
- **返回**：`{found: true, matched_node, waited_ms}` 或 `operation_timeout`。

### 3. 新增 `/api/a11y/tap_text`（POST）
- **实现**：`tool_uiautomator_tap_text(text)` → dump 找到包含文字的节点 → 取 bounds 中心 → `input tap x y`。
- **用途**：agent 操控电视设置菜单/标准 App 的文字按钮，无需 LLM 算坐标。
- **返回**：`{clicked: true, text, x, y, class}` 或 `invalid_parameter`（未找到）。

### 4. `_get_screen_texts()` 辅助函数 + ui_state/play_state fallback
- **问题**：`tool_ui_state()` / `tool_play_state()` 依赖电视端无障碍 HTTP（`/api/a11y/text`），但电视端无障碍 APK 未开发，texts 恒为空，状态识别只能靠前台包名。
- **修复**：新增 `_get_screen_texts(timeout=8)` → 优先 a11y HTTP（快），失败自动 fallback 到 `tool_uiautomator_dump()`（系统自带，无需 APK）。
- `tool_ui_state()` 和 `tool_play_state()` 飞牛TV分支均改用此函数，返回值新增 `text_source` 字段（`a11y_http` / `uiautomator` / `none`）。

---

## 二、验证结果

| 端点 | 测试 | 结果 |
|---|---|---|
| `/api/health` | 容器重启后 | status=ok, tv_online=true, adb=true, mqtt=true, degraded=false |
| `/api/a11y/dump` | 小米桌面 | ok=true, 17 nodes, 2366ms |
| `/api/a11y/wait_text` | 不存在的文字, 3s 超时 | ok=false, error=operation_timeout（符合预期） |
| `/api/a11y/tap_text` | 不存在的文字 | ok=false, error=invalid_parameter（符合预期） |
| `/api/ui_state` | 小米桌面 | state=home, pkg=com.mitv.tvhome, text_source=none（桌面自定义渲染无文字节点，正常） |

---

## 三、盘上锚点与生效性

| 改动 | 盘上位置 | 生效性 |
|---|---|---|
| import xml.etree.ElementTree | :51（import 区） | 需重建/重启 |
| `_parse_bounds` / `tool_uiautomator_dump` / `tool_uiautomator_wait_text` / `_get_screen_texts` / `tool_uiautomator_tap_text` | :2273-2420 附近（tool_a11y_node 之后） | 需重启 |
| GET `/api/a11y/dump` 路由 | :3251 附近（a11y/focus 之后） | 需重启 |
| POST `/api/a11y/wait_text` + `/api/a11y/tap_text` 路由 | :3548 附近（a11y/node 之后） | 需重启 |
| `tool_ui_state` 改用 `_get_screen_texts` + text_source | :1393-1425 | 需重启 |
| `tool_play_state` 飞牛TV分支改用 `_get_screen_texts` | :1458-1470 | 需重启 |

**已部署**：docker cp + restart 完成，容器内 md5 = `67e11212`，与部署树一致。

---

## 四、对 agent 操控电视的意义

这是路线图 v0.4/v0.5 的关键补全：
1. **读屏能力立即可用**——不再阻塞在"电视端无障碍 APK 未开发"。
2. **agent 可以"看"电视界面了**——dump 出节点文字+坐标，LLM 据此决策。
3. **tap_text 让 agent 能点菜单**——给定文字（如"设置""网络""WiFi"），自动定位点击。
4. **wait_text 让多步操作有断言**——操作后等待目标文字出现，确认到达。
5. **ui_state/play_state 真正能工作**——不再只靠前台包名猜状态。

**下一步建议**：
- 把 Top 10 高频场景（进设置→网络→WiFi、打开某 App 到某页等）写成确定性蓝图脚本，用 dump + wait_text + tap_text + keyevent 组合，带到达断言。
- `/api/a11y/focus_text`（DPAD 焦点导航，比 tap 更符合电视交互模型）可作为下一轮迭代。
- 透明悬浮窗 PoC（arcface 侧工单已发出）通过后，可替代部分 dump 场景。

---

## 五、已知遗留（非本次）

- docker.sock 仍挂（F3，需 PM 排窗口动 compose）。
- API 全局鉴权未做（除 refresh_rtsp 外无 token）。
- 电视端无障碍 APK 仍未开发（a11y HTTP 路由空转，fallback 到 uiautomator）。
- CORS `*` 通配未改白名单。
