# TVPilot 主题标准 v1

> 状态：v1（2026-11-04）· 契约文档 · 服务端与电视端共同遵守
>
> 目标：让 NAS 上的 TVPilot 服务可以**下发/替换**每个用户的登录动画配色、桌面壁纸、
> 头像、应用图标，而电视端在运行时**完全离线、秒开、零网络依赖**。

## 1. 设计原则

| 原则 | 说明 |
|---|---|
| **离线优先** | 电视只读本地文件；网络只用于「NAS → 电视」的下发环节，不参与开机渲染 |
| **三级回退** | 外部主题文件 → APK 内置主题 → 代码默认渐变；任何缺失/损坏都不白屏、不崩溃 |
| **按用户隔离** | 目录名 = Arcface 人脸库里的 `name`（精确匹配）：`lidicn` / `Emily` / `Kevin` |
| **增量可选** | `theme.json` 每个字段都可省略，省略即用内置值；先只换壁纸也合法 |
| **弱机友好** | 壁纸降采样到渲染分辨率 1920×1080，绝不加载 4K 原图（low_ram） |

## 2. 目录约定

### NAS 侧（源）
```
/vol1/1000/docker/tvpilot/data/themes/
  <user>/
    theme.json          # 必需，主题清单
    wallpaper.webp      # 可选，桌面壁纸
    avatar.png          # 可选，登录头像
```
> `data/` 已挂载到容器 `/app/data`（见 docker-compose.yml），因此主题文件放这里
> **无需改 compose、无需重建镜像**；服务内对应路径 `/app/data/themes/`。

### 电视侧（目标，由服务端 adb push）
```
/sdcard/TVPilot/themes/
  <user>/
    theme.json
    wallpaper.webp
    avatar.png
```

`<user>` 取值必须与 Arcface 注册名一致；未注册的目录会被忽略。

## 3. `theme.json` 字段

所有字段**可选**。类型与取值如下：

| 字段 | 类型 | 说明 | 缺省 |
|---|---|---|---|
| `version` | int | 标准版本，当前为 `1` | `1` |
| `user` | string | 用户名，应与目录名一致 | 目录名 |
| `display_name` | string | 登录动画/问候语显示名 | `user` |
| `wallpaper` | string | 壁纸文件名，相对本目录 | 用内置渐变 |
| `avatar` | string | 头像文件名，相对本目录 | 用内置头像 |
| `anim_style` | int \| string | **向后兼容**：内置基线 id 或自定义动画 id；不限制枚举 | 内置用户基线 |
| `anim` | object | **声明式动画描述**（见 §3.1）；存在时优先于 `anim_style` | 内置基线 |
| `colors.bg_top` | string `#RRGGBB`/`#AARRGGBB` | 桌面渐变上色 | 内置 |
| `colors.bg_bottom` | string | 桌面渐变下色 | 内置 |
| `colors.glow` | string | 桌面装饰光晕 | 内置 |
| `colors.anim_primary` | string | 动画主色（环/弧/网格） | 内置 |
| `colors.anim_secondary` | string | 动画辅色（波纹/成功环/标签） | 内置 |
| `colors.anim_bg_top` | string | 动画背景渐变中心色 | 内置 |
| `colors.anim_bg_mid` | string | 动画背景渐变中间色 | 内置 |
| `colors.anim_bg_bottom` | string | 动画背景渐变边缘色 | 内置 |
| `updated_at` | number | Unix 秒，仅用于比对，电视端不强依赖 | — |

**示例（完整）**
```json
{
  "version": 1,
  "user": "Emily",
  "display_name": "Emily",
  "wallpaper": "wallpaper.webp",
  "avatar": "avatar.png",
  "anim_style": 2,
  "colors": {
    "bg_top": "#7A3A6E", "bg_bottom": "#2A0E28", "glow": "#FF8AD2",
    "anim_primary": "#FF8AD2", "anim_secondary": "#FFC4E0",
    "anim_bg_top": "#7A3A6E", "anim_bg_mid": "#3B1F4E", "anim_bg_bottom": "#2A0E28"
  },
  "updated_at": 1730700000
}
```

**示例（最小，只换壁纸）**
```json
{ "version": 1, "wallpaper": "wallpaper.webp" }
```

### 3.1 登录动画契约（开放，不是枚举）

**内置三套只是回退基线，不是可选上限。** MiMo / 主题作者可自由设计全新动画。

`anim` 是声明式描述，由「**时间线 + 渲染原子 + 粒子系统 + 关键帧变换**」组合而成：

```
anim: {
  id, name, palette{}, background{},
  layers[]    : { type:<原子>, params{}, color, alpha, phase, easing, keyframes[] }
  particles[] : { system:<粒子系统>, count, color, speed, size, life, phase }
  timeline{}  : { scan|success|shrink|<custom>: { duration_ms, layers, particles, transitions } }
  avatar{}, text{}, audio{}, shrink_target, performance{}
}
```

**渲染原子**（可任意组合，均为 Canvas 2D 单 Pass 可实现）：
`radial_gradient / linear_gradient / starfield / aurora / grid / ring / arc /
orbit_ellipse / polygon / spiral / wave / constellation / brackets / scanline /
meteor / petal / heart / ripple / flash / glow / data_stream / dash_ring /
particle_converge|burst|orbit|rain|swarm / avatar_bitmap|geometric /
text_primary|label|sub / transform / easing / blend`

**能力边界（硬约束，机器可判，违反即判不合格）**：
- ✅ 准予：仅 `ANIM_PRIMITIVES` 白名单内约 40 个 Canvas 2D 单 Pass 图元
  （含 `gradient_rect` / `path` / `text` / `image` / `line` / `vignette`）；禁止自造 `layer.type`
- ❌ **禁用算子**：`blur_blob` / `glitch` / `gpu_shader` / `lottie` / `rive` / `video_texture`，
  以及任何 `paint.blur`（高斯模糊）
- 弱机预算（`tvpilot/theme_contract.py:RENDERER_CAPABILITIES.budget`）：
  `max_layers ≤ 24`、`max_particles ≤ 240`、**`target_fps ≥ 45`**（不接受 30fps 自评，宁可减特效保帧率）、
  `colors` 键名须在 `COLOR_FIELDS` 内、`theme.json` 顶层键须在 `THEME_FIELDS` 内

**验收闸（唯一落地判据）**：`python -m tvpilot.theme_lint --dir data/themes`
对每份 theme.json 跑 `validate_manifest_keys(strict=True)`，退出码 1 = 不合格。
lever-hub2 的 gate 只查引用/节/尾标，**不查设计是否违反能力边界**，
故 MiMo 必须在「验收」节贴出该 lint 的实跑输出，三份全 PASS 才算交付合格。

**回退链**：`外部 anim 描述` → `BUILTIN_ANIM_BASELINES[user]` → `硬编码扫描环渲染器` → 代码渐变。
`anim.id` 开放（可自由命名），但原子/键名/预算全部硬校验，不再以 warning 放行。

**⚠️ 落地责任**：`anim` 声明式描述要生效，电视端需新增「动画描述解释器」（把 layers/particles/timeline 翻译成 Canvas 调用）。这是本单 MiMo 设计稿之后的实现工作，须在「决策」节列出改动文件清单与解释器设计。

## 4. 素材规格

| 素材 | 格式 | 尺寸 | 备注 |
|---|---|---|---|
| `wallpaper` | WebP（推荐）/ PNG / JPG | **1920×1080** | 与渲染分辨率一致；更大尺寸会被降采样，浪费内存 |
| `avatar` | PNG（透明底）/ WebP | 正方形 ≥256×256 | 电视端裁成圆形；非正方形按短边裁切 |

- 单张壁纸建议 < 1.5 MB（WebP 有损 q=80 足够）
- 文件名只允许 `[A-Za-z0-9._-]`，禁止路径穿越（`..`、绝对路径）
- 电视端对每个文件做大小上限保护（壁纸 8 MB、头像 4 MB），超限即回退

## 5. 内置主题（回退基准）

| user | 内置动画基线(仅回退) | 桌面上/下色 | 主色 / 辅色 |
|---|---|---|---|
| `lidicn` | scan-ring | `#18305C` / `#05070C` | `#5BE6FF` / `#62F0C8` |
| `Emily` | fairy | `#7A3A6E` / `#2A0E28` | `#FF8AD2` / `#FFC4E0` |
| `Kevin` | space | `#0A1A3F` / `#02040F` | `#6FB8FF` / `#FFFFFF` |

## 6. 下发接口（TVPilot MCP，`192.168.2.200:8090` 的 `POST /mcp`）

主题下发走 **MCP 工具**（JSON-RPC 2.0），**不提供 REST 接口**（v1 早期 REST 已移除）。

| 工具 | 作用 | 参数 |
|---|---|---|
| `tv_theme_push` | 下发主题到电视 | `{"user":"Emily"}` 或 `{"all":true}` |
| `tv_theme_list` | 列出 NAS 侧主题 + 文件校验状态 | 无 |
| `tv_theme_status` | 查电视侧已装主题 | 可选 `user` |

调用示例：
```json
{"jsonrpc":"2.0","id":1,"method":"tools/call",
 "params":{"name":"tv_theme_push","arguments":{"user":"Emily"}}}
```

`tv_theme_push` 流程：`tvpilot/theme.py` 的 `validate()` 校验 → `adb push` → 回传每个文件字节数。
下发后电视端在**下次进入桌面/登录**时生效（无需重启 App）。

## 7. 电视端加载时机与生命周期

1. `HostActivity.onCreate` → 识别到人 → `ThemeLoader.resolve(user) / wallpaper(user) / avatar(user)`（子线程，一次性）
2. 登录动画：`anim` 声明式描述（或 `anim_style` 基线）/ `colors` / `avatar` 覆盖内置值
3. 进入桌面：`wallpaper` 文件存在则作为静态 Bitmap 铺底；否则用渐变
4. 切换用户时回收上一张壁纸 Bitmap，避免三份常驻
5. 任何解析异常 → 静默回退内置主题，写入 logcat，不抛给用户

## 8. 版本演进

- v1：壁纸 / 头像 / 配色 / 开放动画描述（本版）
- **v1.1 待补全：桌面图标契约（本单由 MiMo 产出设计，见 goal）**
- 预留（v2）：`icon.png` 应用图标、`anim_bg` 动画背景图、`sound` 音效包
- `version` 字段供未来兼容；电视端遇到更高版本仍按已知字段尽力解析，不整体丢弃

## 9. 手动下发（不依赖服务重启）

```bash
# 在任意能连 adb 的机器上
adb connect 192.168.2.238:5555
adb push data/themes/Emily/ /sdcard/TVPilot/themes/Emily/
```
改完主题，下次进桌面即生效。这是接口未部署前的兜底通道。
