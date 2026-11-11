# TVPilot MCP 专项审计报告

- 审计日期: 2026-10-09
- 审计对象: TVPilot MCP(16 工具: tv_system_state / tv_foreground / tv_screenshot / tv_ui_dump / tv_list_apps / tv_app_state / tv_keyevent / tv_launch_app / tv_zap / tv_tap_index / tv_click_text / tv_blueprint_run / tv_input_text / tv_force_stop / tv_tap / tv_go_home)
- 后端: 小米电视 192.168.2.238, 控制端 192.168.2.200:8090
- 方法: 只读基线 + 写入动作 + 非法输入错误路径 + 成功/失败对照

## 一、缺陷清单

### BUG-1 [严重] tv_keyevent 静默接受非法键名, 无法区分成败

- 输入: tv_keyevent(key="KEYCODE_BOGUS_KEY")  — 一个不存在的 Android 键名
- 实际返回: {"ok": true, "key": "KEYCODE_BOGUS_KEY", "channel": "adb"}
- 对照: 合法键 tv_keyevent(key="KEYCODE_HOME") 返回 {"ok": true, "key": "KEYCODE_HOME", "channel": "adb"}
- 问题: 两者返回结构完全一致且均 ok=true, 调用方无法判断按键是否真的被电视接受/执行
- 期望: 对非法 KEYCODE 返回错误, 或返回真实下发结果(如 adb 返回码/是否识别)
- 影响: 上层自动化会以为按键成功, 实际电视可能忽略; 静默失败

### BUG-2 [严重] tv_force_stop 对未安装包谎报成功

- 输入: tv_force_stop(package="com.not.installed.pkg")
- 实际返回: {"ok": true, "package": "com.not.installed.pkg", "stopped": true}
- 对照: 对同一未安装包, 同族工具 tv_launch_app 正确报错 "包未安装: com.not.installed.app"
- 问题: 不存在的包不可能被停止, 却返回 stopped=true; 同族工具契约不一致
- 期望: 未安装包应返回错误, 或明确返回 stopped=false / skipped
- 影响: 无法确认后台是否真被清理, 违背该工具 "点播前清理后台" 的设计意图

### BUG-3 [中] tv_go_home 把 "验证失败" 误报为 "操作失败"

- 输入: tv_go_home()
- 实际返回: ok=false, "回桌面后前台不是桌面，当前: com.xiaomi.tv.gallery"
- 对照证据:
  - tv_keyevent(KEYCODE_HOME) 返回 ok=true(ADB 已下发 HOME 键)
  - 随后 tv_app_state 显示 foreground_package 已是 com.mitv.tvhome(桌面)
  - 即 HOME 动作其实生效了, 桌面已回; 但 tv_go_home 在验证瞬间仍读到旧前台 com.xiaomi.tv.gallery
- 问题: 验证存在竞态/延迟, 桌面切换未完成即判负; 且把 "未验证通过" 与 "操作失败" 混为一谈
- 期望: 验证应带重试/等待(如轮询前台 N 次直到 tvhome 或超时); 失败信息应区分 "按键未下发" 与 "已下发但验证超时"
- 影响: 正常的回桌面被误判为失败, 上层重试逻辑会重复发键

### BUG-4 [低] tv_tap 越界由扩展层 Schema 拦截, 错误信息不规范

- 输入: tv_tap(x=5000, y=5000)  — 超出 0~4096
- 实际返回: Validation failed for tool "mcp_t_2bc53e81_..._tv_tap": - x: must be <= 4096 - y: must be <= 4096 (英文)
- 问题: 与工具层其它中文错误(如 "索引 999 超出范围")风格不一致; 泄漏内部带随机前缀的完整工具名; 属扩展校验而非工具校验, 文案未本地化
- 期望: 统一错误格式与语言, 或由工具层自行校验并返回中文提示
- 影响: 调用方需解析两种错误格式; 轻微

## 二、正确报错项(正面)

| 工具 | 非法输入 | 返回 | 评价 |
| --- | --- | --- | --- |
| tv_launch_app | 未安装包 | 包未安装: com.not.installed.app | 正确 |
| tv_zap | 不存在频道 | 频道表里没有 "不存在的频道XYZ123" | 正确 |
| tv_tap_index | 索引 999 | 索引 999 超出范围（当前共 4 个元素） | 正确, 含范围 |
| tv_click_text | 不存在文字 | 未找到文字为 "..." 的控件 | 正确 |
| tv_blueprint_run | 未注册蓝图 | 未注册的蓝图: "..."（已注册: go_home, open_settings, open_app, zap_to, zap_to_cctv1, iptv_zap_safe） | 正确, 列出可选项 |

## 三、通过项

| 工具 | 结果 |
| --- | --- |
| tv_system_state | 完整: 前台/UI 状态/播放/音频(HDMI eARC 音量22)/屏幕/网络(ethernet 192.168.2.238) |
| tv_foreground | {"package":"com.xiaomi.tv.gallery"} |
| tv_screenshot | 返回 URL http://192.168.2.200:8090/remote-static/shots/shot_*.png, 240x135 png |
| tv_ui_dump | 4 个可交互元素, 含 index/text/pos/click |
| tv_list_apps | 全量 142 包; filter="dangbei" 正确返回 2 条 |
| tv_app_state | 含 installed/running/foreground/pid/foreground_package |
| tv_input_text | 走 tvremoteime_broadcast, 返回 length=6, ime_switched=true |

## 四、附带观察(非工具缺陷)

- 批量并行调用时 TVPilot 工具的 detail/output 会整体缺失(仅剩 {ok,summary,windowed,truncated}), 单独调用则完整。属扩展结果透传/续跑窗口行为, 非 TVPilot 工具本身缺陷, 但会妨碍批量审计。
- tv_keyevent 返回固定 channel="adb", 未体现文档所述 "ADB 优先 6095 降级" 的实际降级路径。

## 五、修复优先级

1. BUG-1 tv_keyevent 静默接受非法键(静默失败)
2. BUG-2 tv_force_stop 谎报成功(契约不一致)
3. BUG-3 tv_go_home 验证竞态误报失败
4. BUG-4 tv_tap 越界错误文案不统一

---
报告生成: 2026-10-09
