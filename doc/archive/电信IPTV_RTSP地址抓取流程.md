# 电信 IPTV RTSP 地址抓取流程
> 状态（2026-11-12 整理）：**历史记录**。本文记录当时的事实，不代表当前状态；现行口径见 `doc/README.md` 真源优先级表。

## 概述

广东电信 IPTV 的直播流地址（RTSP）带有认证 token，时效性约 1-7 天。过期后 mytv/go2rtc 无法播放，需要重新抓取。

本文档记录通过 `adb logcat` 抓取电信 IPTV 应用播放地址的完整流程。

## 前置条件

- 电视已开启 ADB 调试（`adb connect 192.168.2.238:5555`）
- 电视已安装电信 IPTV 应用（包名：`com.android.smart.terminal.iptv`，主 Activity：`com.amt.app.IPTVActivity`）
- 电信 IPTV 应用已登录账号（认证信息存储在 `/data/data/com.android.smart.terminal.iptv/shared_prefs/iptv_prefs.xml`）
- 电脑已安装 Python 3 和 adb 工具

## 抓取原理

电信 IPTV 应用换台时，会在 logcat 中输出 RTSP 播放地址：

```
CTCMediaPlayer: MediaPlayer > setDataSource --livePath--->rtsp://125.88.104.40/PLTV/...
CTCMediaPlayer: CTCPlayer > joinChannel :rtsp://125.88.104.40/PLTV/...
```

关键 log tag：
- `CTCMediaPlayer` — 播放器设置数据源时输出 `setDataSource --livePath--->`
- `IPTVPlayer` — RTSP 控制层输出 `Play Ready:`、`Send:`（包含完整 RTSP URL）
- `CTCPLAYER` — 底层播放器输出 `setDataSource2: path:`

## 抓取步骤

### 1. 停掉 TVPilot 容器（防止换台时跳到 mytv）

```bash
ssh lidicn@192.168.2.200 "docker stop tvpilot"
```

### 2. 启动电信 IPTV 应用到前台

```bash
adb -s 192.168.2.238:5555 shell am start -n com.android.smart.terminal.iptv/com.amt.app.IPTVActivity
```

### 3. 清空 logcat

```bash
adb -s 192.168.2.238:5555 logcat -b all -c
```

### 4. 输入频道号换台

```bash
# 输入频道号 1（多位数字逐个输入）
adb -s 192.168.2.238:5555 shell input keyevent KEYCODE_1
```

### 5. 等待播放开始（7-12 秒）

### 6. 抓取 logcat 并提取 RTSP URL

```bash
adb -s 192.168.2.238:5555 logcat -b all -d | grep "setDataSource --livePath--->"
```

### 7. 重复步骤 3-6，抓取所有频道

## 自动化脚本

脚本位置：`D:\Documents\WorkSpace\TV_CAM\tmp\capture_all_rtsp.py`

功能：
- 自动循环 1-97 频道号
- 每个频道清空 logcat → 输入频道号 → 等待 7 秒 → 提取 RTSP URL
- 通过旧 go2rtc.yaml 中的频道 ID 匹配频道名
- 生成新的 go2rtc.yaml

补抓脚本：`D:\Documents\WorkSpace\TV_CAM\tmp\recapture_failed.py`
- 对首次抓取失败的频道增加等待时间到 12 秒重新抓取

## RTSP URL 结构

```
rtsp://125.88.104.40/PLTV/88888895/224/3221226706/00000100000000060000000000334083_0.smil
  ?rrsip=125.88.104.40          # 重定向服务器 IP
  &zoneoffset=480                # 时区偏移
  &icpid=szmg                    # 区域 ID（深圳）
  &tenantId=8601                 # 租户 ID
  &accountinfo=...               # 认证信息（加密，含时效性）
  &GuardEncType=2                # 加密类型
  &from=339                      # 来源
  &hms_devid=72791               # 设备 ID
  &online=1788885560             # 在线时间戳（用于判断地址时效性）
```

注意：
- RTSP URL 会经过 302 重定向：`125.88.104.40` → `183.59.160.198` → `183.59.167.37`（最终边缘节点）
- ffmpeg 使用 `-rtsp_transport tcp` 可自动跟随 302 重定向
- `online=` 时间戳可用于判断地址是否过期（转换为北京时间，超过 7 天大概率失效）

## 部署新配置

### 1. 生成完整 go2rtc.yaml

脚本：`D:\Documents\WorkSpace\TV_CAM\tmp\gen_go2rtc_config.py`
- 保留旧配置的 log/api/rtsp/webrtc 等配置
- 替换 streams 部分为新抓取的频道

### 2. 部署到 go2rtc 容器

```bash
scp go2rtc_new.yaml lidicn@192.168.2.200:/tmp/
ssh lidicn@192.168.2.200 "docker cp /tmp/go2rtc_new.yaml go2rtc:/config/go2rtc.yaml && docker restart go2rtc"
```

### 3. 重启 TVPilot（刷新频道列表缓存）

```bash
ssh lidicn@192.168.2.200 "docker restart tvpilot"
```

### 4. 刷新 mytv IPTV 源

```python
import urllib.request, json
settings = {
    "iptvSourceUrl": "http://192.168.2.200:8090/iptv/source.m3u?v=YYYYMMDD",
    "epgXmlUrl": "http://192.168.2.200:8090/iptv/epg.xml",
}
req = urllib.request.Request(
    'http://192.168.2.238:10481/api/settings',
    data=json.dumps(settings).encode('utf-8'),
    headers={'Content-Type': 'application/json'}
)
urllib.request.urlopen(req, timeout=10)
```

## 验证

### 验证 RTSP URL 可播放

```bash
ssh lidicn@192.168.2.200 "docker exec go2rtc timeout 10 ffmpeg -rtsp_transport tcp -i 'RTSP_URL' -t 3 -f null -"
```

有 `frame=` 输出即表示可播放。

### 验证 mytv 播放

```python
import urllib.request, json
# 换台
zap = urllib.request.Request(
    'http://192.168.2.238:10481/api/zap',
    data=json.dumps({"channel": "中央一台"}).encode('utf-8'),
    headers={'Content-Type': 'application/json'}
)
urllib.request.urlopen(zap, timeout=15)
# 检查播放状态
time.sleep(5)
r = urllib.request.urlopen('http://192.168.2.238:10481/api/current', timeout=5)
print(json.loads(r.read()))  # playing=True 表示成功
```

## RTSP URL 结构分析（关键发现）

通过对比两次抓取（9月5日 vs 9月9日）的同一个频道 URL，发现以下规律：

### 完全固定的部分（永不过期）

| 部分 | 说明 |
|------|------|
| 路径 `/PLTV/88888895/224/{channel_id}/{stream_id}_0.smil` | channel_id 和 stream_id 完全固定 |
| rrsip/zoneoffset/icpid/tenantId/limitflux/limitdur/GuardEncType | 全部固定 |
| accountinfo 段3（频道级 token） | 每频道不同，但永不变化 |

### 动态变化的部分（导致过期）

| 部分 | 说明 | 关键发现 |
|------|------|----------|
| accountinfo 段4（用户级 token） | 认证 token | **所有频道完全相同！全局共享** |
| accountinfo 段5[0/3]（认证时间戳） | 如 `20260909015200` | 每次认证变化 |
| 主机 IP | 入口 vs 边缘节点 | ffmpeg 自动跟随 302 重定向 |

### accountinfo 结构

```
~~V2.0~{段3:频道级token}~{段4:用户级token}~{段5:ExtInfoPC{base64}:{时间戳},{用户ID},{IP},{时间戳},{频道代码},...}
```

### 核心结论

**不需要每次抓 97 个频道！只需要抓 1 个频道获取最新的用户 token（段4）和时间戳，即可批量更新所有频道。**

抓取时间从 15 分钟缩短到 15 秒，可以高频定期更新。

---

## 自动化定期刷新（方案 A，已部署）

### 部署位置

- NAS（192.168.2.200）`/opt/tvpilot/`
- 脚本：`auto_refresh_rtsp.py`
- 频道模板：`channel_template.json`（57 个频道的固定字段）
- 日志：`refresh_rtsp.log`、`cron.log`

### 工作流程（全自动化，约 40 秒）

1. 连接电视 ADB
2. 暂停 TVPilot 容器（防止换台跳到 mytv）
3. 启动电信 IPTV 应用到前台
4. 换台到频道 1（中央一台）
5. 等待 10 秒，从 logcat 提取 RTSP URL
6. 解析 accountinfo，提取用户 token（段4）和认证时间戳
7. 读取频道模板，批量生成 57 个频道的完整 URL
8. 生成 go2rtc.yaml（匹配旧格式：2空格缩进+空行）
9. 部署到 go2rtc 容器并重启
10. 验证 go2rtc 流加载（API 返回 >1000 字节）
11. 恢复 TVPilot 容器
12. 切回 mytv 到前台

### 定时任务

```bash
# 每 3 天凌晨 3 点执行（RTSP 约 4 天过期，留 1 天余量）
0 3 */3 * * /usr/bin/python3 /opt/tvpilot/auto_refresh_rtsp.py >> /opt/tvpilot/cron.log 2>&1
```

### 手动执行

```bash
ssh lidicn@192.168.2.200
cd /opt/tvpilot
python3 auto_refresh_rtsp.py
```

### 验证结果

- 2026-09-09 首次测试：42 秒完成，57 频道全部更新，go2rtc 加载 44349 字节
- mytv 播放验证：5/5 频道播放成功，换台 1-4 秒

### 注意事项

- 抓取过程中电视会被自动控制（切换到电信 IPTV 再切回 mytv），建议在深夜执行
- 电视必须保持开机且 ADB 可用
- 电信 IPTV 应用必须保持登录状态
- 如果抓取失败，脚本会自动恢复 TVPilot 和 mytv，不会影响现有服务

---

## 常见问题

### Q: 某些频道抓取失败怎么办？
A: 增加等待时间到 12 秒重新抓取。仍失败的可能是：
- 电信 IPTV 应用中该频道号不存在（跳号）
- 收费频道，未授权无法播放
- 频道加载超时

### Q: RTSP URL 有效期多久？
A: 约 4 天（实测）。超过 4 天大概率失效。已设置每 3 天自动刷新一次。

### Q: 能否自动化定期更新？
A: 已实现。见上方"自动化定期刷新"章节。NAS 上 `/opt/tvpilot/auto_refresh_rtsp.py`，cron 每 3 天凌晨 3 点执行，全流程约 40 秒。

### Q: 为什么只抓 1 个频道就能更新所有频道？
A: 因为 accountinfo 段4（用户级认证 token）在所有频道中完全相同，段5中只有时间戳和频道代码变化（频道代码也是固定的）。只需抓 1 个频道获取最新的用户 token 和时间戳，即可批量替换所有频道的 accountinfo。

### Q: 过期的 RTSP 会拖垮 mytv 吗？
A: 不会直接崩溃，但会导致：mytv 请求 HLS → go2rtc ffmpeg 连不上 → HLS 返回空 → mytv 播放器持续重试缓冲 → 换台卡住、界面卡顿。自动刷新机制可避免此问题。

### Q: 为什么不用网络抓包（Wireshark）？
A: logcat 方式更简单，不需要额外的抓包设备或网络配置。电信 IPTV 应用的 native 层会在 logcat 中输出完整的 RTSP URL。

## 相关文件

### 自动化刷新（NAS 上）
- 自动刷新脚本：`/opt/tvpilot/auto_refresh_rtsp.py`（NAS）
- 频道模板：`/opt/tvpilot/channel_template.json`（NAS，57 频道固定字段）
- 日志：`/opt/tvpilot/refresh_rtsp.log`、`/opt/tvpilot/cron.log`（NAS）

### 手动抓取（Windows 上）
- 批量抓取脚本：`D:\Documents\WorkSpace\TV_CAM\tmp\capture_all_rtsp.py`
- 补抓脚本：`D:\Documents\WorkSpace\TV_CAM\tmp\recapture_failed.py`
- 配置生成脚本：`D:\Documents\WorkSpace\TV_CAM\tmp\gen_go2rtc_config.py`
- 模板提取脚本：`D:\Documents\WorkSpace\TV_CAM\tmp\extract_template.py`
- URL 对比分析：`D:\Documents\WorkSpace\TV_CAM\tmp\compare_rtsp_urls.py`
- 抓取结果：`D:\Documents\WorkSpace\TV_CAM\tmp\rtsp_all_result.json`
- 频道模板：`D:\Documents\WorkSpace\TV_CAM\tmp\channel_template.json`
- 新配置：`D:\Documents\WorkSpace\TV_CAM\tmp\go2rtc_new.yaml`
- 旧配置备份：`D:\Documents\WorkSpace\TV_CAM\tmp\go2rtc_current.yaml`
