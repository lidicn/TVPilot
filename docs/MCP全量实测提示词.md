# TVPilot MCP 全量实测提示词

## 任务目标

通过 MCP 协议对 TVPilot 的 10 个工具进行全量实测，记录每个工具的成功/失败情况。

## 连接信息

- MCP 端点：`http://192.168.2.200:8090/mcp`
- 协议：HTTP POST，JSON-RPC 2.0
- 无需认证

## 硬约束（必须遵守）

1. **只能使用 MCP 工具**完成所有测试，禁止使用 ADB 命令、SSH、curl 直连 API、Python 脚本等任何其他手段
2. **失败即记录**，不要尝试用其他方式绕过或修复，不要重试超过 2 次
3. 每个工具测试完立即记录结果，最后输出完整测试报告
4. 测试顺序按下方清单执行，不要跳过
5. 换台类测试之间间隔至少 3 秒，避免 IPTV 应用未响应

## 测试工具与用例

### 1. tv_system_state（系统状态）
- 用例 1.1：无参数调用，检查返回字段完整性
- 验证点：foreground.package、ui_state.state、play_state.state、play_state.playing、play_state.channel、play_state.channel_no

### 2. tv_foreground（前台包名）
- 用例 2.1：无参数调用
- 验证点：返回 package 字段非空

### 3. tv_go_home（回桌面）
- 用例 3.1：无参数调用
- 验证点：返回 ok=true，随后用 tv_foreground 确认前台是桌面（含 tvhome/launcher/home）

### 4. tv_zap（换台）- 核心功能
- 用例 4.1：换台到 "CCTV1"（中央一台，台号 21）
- 用例 4.2：换台到 "CCTV4"（中央四台，台号 24）
- 用例 4.3：换台到 "湖南卫视"
- 用例 4.4：换台到不存在的频道 "XYZ不存在"（预期失败，返回错误）
- 验证点：每次换台后检查返回的 channel、channel_no、iptv_foreground、verified.current_channel

### 5. tv_keyevent（按键）
- 用例 5.1：发送 KEYCODE_VOLUME_UP
- 用例 5.2：发送 KEYCODE_VOLUME_DOWN
- 用例 5.3：发送 KEYCODE_MENU
- 用例 5.4：发送无效按键 "INVALID_KEY"（预期失败）
- 验证点：返回 ok=true，按键已发送

### 6. tv_input_text（文字输入）
- 用例 6.1：输入英文 "test123"
- 用例 6.2：输入中文 "测试"（如果当前界面有输入框）
- 验证点：返回 ok=true，method 字段正确（ascii_input / tvremoteime_broadcast）
- 注意：如果当前界面没有输入框，输入可能无视觉效果，但工具应返回成功

### 7. tv_tap（点按屏幕）
- 用例 7.1：点按屏幕中心 (500, 500)
- 用例 7.2：点按无效坐标 (-1, -1)（预期失败或无效果）
- 验证点：返回 ok=true

### 8. tv_launch_app（启动应用）
- 用例 8.1：启动设置 `com.android.settings`
- 用例 8.2：启动不存在的包名 `com.nonexistent.app`（预期失败）
- 验证点：返回 ok=true，随后用 tv_foreground 确认

### 9. tv_screenshot（截图）
- 用例 9.1：默认参数截图
- 用例 9.2：指定 width=640 截图
- 验证点：返回 url 字段，url 可访问（返回 200），图片非空
- 注意：只需检查 url 字段存在且格式正确，不需要实际下载图片

### 10. tv_blueprint_run（蓝图执行）
- 用例 10.1：执行 `go_home` 蓝图
- 用例 10.2：执行 `zap_to_cctv1` 蓝图
- 用例 10.3：执行 `iptv_zap_safe` 蓝图，参数 `{"channel_name": "CCTV4"}`
- 用例 10.4：执行不存在的蓝图 `nonexistent_blueprint`（预期失败）
- 验证点：返回 ok=true，steps 数组中每个步骤有 ok/status 字段

## 输出格式

测试完成后，按以下格式输出报告：

```
# TVPilot MCP 实测报告

## 汇总
- 总用例数：X
- 成功：X
- 失败：X
- 通过率：X%

## 详细结果

### 1. tv_system_state
| 用例 | 结果 | 耗时 | 备注 |
|------|------|------|------|
| 1.1 无参数调用 | ✅/❌ | Xs | ... |

### 2. tv_foreground
...

## 失败用例详情
（每个失败用例记录：工具名、用例编号、输入参数、返回的错误信息、isError 字段值）

## 异常发现
（测试过程中发现的非预期行为，如返回字段缺失、数据不一致等）
```

## 注意事项

1. 测试 tv_launch_app 启动设置后，后续换台测试前需要先执行 tv_go_home 或 tv_zap 回到 IPTV
2. tv_input_text 中文输入需要 tvremoteime 服务，如果失败记录即可
3. tv_screenshot 返回的 url 是 `http://192.168.2.200:8090/remote-static/shots/xxx.png` 格式
4. 所有时间记录用秒，保留 1 位小数
5. 如果某个工具连续 2 次超时（>30s），标记为失败并跳过该工具的剩余用例
