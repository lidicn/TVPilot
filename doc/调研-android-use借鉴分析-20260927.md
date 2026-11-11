# android-use 借鉴分析报告

> 日期：2026-09-27
> 项目：https://github.com/languse-ai/android-use
> 定位：豆包手机开源版，基于 LLM 的安卓手机自动化操作

---

## 一、项目概述

android-use 是一个开源版的"豆包手机"项目，通过自然语言驱动 Android 手机自动化操作。核心能力：
- 自动截取屏幕 + 解析 UI 层级
- LLM 分析界面，决定下一步操作
- 执行点击、输入、滑动等动作
- 支持微信、抖音、小红书等复杂 App 的多步操作

**技术栈**：Python + ADB + uiautomator + LLM（Deepseek/Kimi/GLM）

---

## 二、核心设计分析

### 2.1 元素索引机制（highlight_index）⭐⭐⭐⭐⭐

**设计**：给屏幕上每个可交互元素分配一个数字索引，Agent 只需说"点击索引 5"。

```
返回给 Agent 的格式：
[0] 搜索按钮 [172,39] clickable
[1] 首页 [781,135] clickable
[2] 电视剧 [951,134] clickable
...
```

**优势**：
- Agent 无需计算坐标，直接用索引操作
- 减少 token 消耗（索引比坐标+文字描述更短）
- 降低操作错误率（不需要 Agent 猜坐标）

**实现位置**：`android_use/android/views.py` — DOMElementNode + SelectorMap

### 2.2 DOM 树解析 + XPath ⭐⭐⭐⭐

**设计**：将 uiautomator XML 解析为完整 DOM 树，每个节点有：
- `bounding_box`：坐标范围
- `node_type`：INTERACTIVE / NON_INTERACTIVE / TEXT
- `xpath`：绝对路径（用于稳定定位）
- `resource_id`、`class_name`、`package`
- 父子关系、兄弟关系

**优势**：
- XPath 实现"稳定定位"（界面变化后，xpath 不变的元素仍能找到）
- 节点类型分类帮助 Agent 判断哪些元素可操作
- 父子关系用于"获取按钮附近的文字"等场景

### 2.3 高亮截图 ⭐⭐⭐⭐

**设计**：在截图上用视觉标记可交互元素：
- 点击：红色 X
- 输入：绿色圆圈
- 长按：紫色圆圈+X
- 滑动：黄色箭头
- 按键：中央文字提示

**实现位置**：`android_use/tools/action_highlight.py` — PIL 绘制

**优势**：
- 配合视觉模型，Agent 看图直接知道点哪里
- 非视觉模型也能通过索引+文字描述操作
- 操作可追溯（截图记录了每次操作的位置）

### 2.4 工具集设计 ⭐⭐⭐

| 工具 | 说明 | TVPilot 对应 |
|------|------|-------------|
| tap | 点击索引或坐标 | tv_tap / tv_tap_index(v1.1) |
| input_text | 输入文字 | tv_input_text |
| swipe | 滑动 | tv_swipe(v1.1) |
| long_press | 长按 | tv_long_press(v1.1) |
| press_key | 按键 | tv_keyevent |
| launch_app | 启动应用 | tv_launch_app |
| shell_command | 执行 shell | 未提供（安全考虑） |

### 2.5 Agent 循环 ⭐⭐⭐

**设计**：观察（截图+XML）→ 思考 → 行动 → 反馈 的 ReAct 循环

**实现位置**：`android_use/agent/service.py` + `prompts.py`

**与 TVPilot 的差异**：
- android-use 内置 Agent，自己调用 LLM
- TVPilot 是 MCP 工具层，由外部 Agent（豆包管家/opencode）调用
- **不搬 Agent 循环**，仅参考 prompt 设计和工具描述

---

## 三、对 TVPilot 的借鉴价值

### 3.1 直接借鉴（高价值、低成本）

| 借鉴项 | 对应 TVPilot 任务 | 预计工作量 |
|--------|-------------------|-----------|
| 元素索引机制 | v1.1-T1 | 1-2 天 |
| 高亮截图 | v1.1-T2 | 1 天 |
| DOM 树数据结构 | v1.1-T3 | 2-3 天 |
| 操作历史记录结构 | v1.1-T4 基础 | 1 天 |

### 3.2 选择性借鉴（中等成本）

| 借鉴项 | 说明 |
|--------|------|
| XPath 稳定定位 | 电视界面变化少，优先级低于手机，但对蓝图稳定性有帮助 |
| swipe/long_press 工具 | 电视以遥控器为主，部分界面（列表滚动）需要 |
| Agent prompt 优化 | 参考其 prompts.py 优化 MCP 工具描述 |

### 3.3 不借鉴（场景差异）

| 项 | 原因 |
|----|------|
| 内置 Agent 循环 | TVPilot 是 MCP 工具层，外部 Agent 调用 |
| 手机触摸交互优化 | 电视是遥控器+方向键，交互方式不同 |
| WebUI/CLI | TVPilot 是 NAS 容器服务 |
| 微信/抖音特定逻辑 | 场景不同 |

---

## 四、TVPilot 现状对比

| 维度 | android-use | TVPilot 当前 | TVPilot v1.1 目标 |
|------|-------------|-------------|-------------------|
| 元素定位 | 索引+XPath | 坐标+文字 | 索引+XPath+坐标 |
| 截图 | 高亮标记 | 原始截图 | 可选高亮 |
| 工具数量 | 7 个通用工具 | 15 个 MCP 工具 | 19+（新增4个） |
| Agent 模式 | 内置 ReAct | MCP 外部调用 | MCP 外部调用 |
| 蓝图/模板 | 无（每次重新推理） | 手动编写蓝图 | 自动录制蓝图 |
| 适用设备 | 手机（触摸） | 电视（遥控器） | 电视（遥控器） |

**TVPilot 的独特优势**：
- 电视场景更聚焦（IPTV换台、视频点播、系统控制）
- 已有 MCP 架构，支持任意 Agent 接入
- 蓝图机制支持经验复用
- 与 ArcFace/MA 等生态系统已打通

---

## 五、落地建议

### 第一阶段（v1.1，2-3 周）
1. **元素索引**（P0）：改造 tv_ui_dump，新增 tv_tap_index
2. **高亮截图**（P0）：tv_screenshot 增加 highlight 参数
3. **DOM 树增强**（P1）：XPath + resource_id + class_name
4. **蓝图自动录制**（P1）：操作录制→自动生成蓝图

### 第二阶段（v1.2，待定）
5. 新增 swipe/long_press/scroll 工具
6. Agent 引导优化（MCP 工具描述 + 最佳实践）
7. 元素稳定性检测（界面变化时自动更新索引映射）

### 验证方式
- 用 opencode 实测：点播流程用索引 vs 用坐标，对比成功率和 token 消耗
- 蓝图稳定性测试：同一 App 版本更新后，XPath 蓝图是否仍可执行

---

## 六、参考文件

- android-use 核心：`android_use/android/views.py`（DOM 解析）
- 高亮实现：`android_use/tools/action_highlight.py`
- Agent 循环：`android_use/agent/service.py`
- 工具注册：`android_use/tools/registry/`

---

*本报告为 TVPilot v1.1 规划的参考依据，随调研深入持续更新。*
