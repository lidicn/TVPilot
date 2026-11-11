"""TVPilot 电视端主题契约 v1（机器可读，供 extract / MiMo 引用）。

这是 THEME_SPEC.md 的可执行投影。电视端实现：tvdesk 的 ThemeLoader.kt /
UserTheme.kt / FaceScanView.java（Kotlin/Java，lever-hub2 的 extract 不支持
.kt/.java，故以本文件作为契约面）。

设计原则（v1.1 修订）：
- 登录动画**不是枚举选择**，而是「声明式描述 + 渲染原子能力」的组合空间。
  MiMo 可自由设计全新动画；内置三套只是回退基线，不是上限。
- 桌面图标契约（v1.1）由本单 MiMo 设计稿补全。
"""

SCHEMA_VERSION = 1

# ---- theme.json 顶层字段（全部可选，缺省回退内置） ----
THEME_FIELDS = {
    'version': 'int, 当前 1',
    'schema': 'string, 契约标识 "tvpilot.theme/1.1"（可选）',
    'user': 'string, 须等于目录名与 Arcface 人脸名',
    'name': 'string, 主题显示名（可选）',
    'display_name': 'string, 登录动画/问候语显示名',
    'wallpaper': 'string, 壁纸文件名（相对主题目录）',
    'avatar': 'string, 头像文件名（相对主题目录）',
    'anim_style': 'int|string, 可选；指向内置基线或自定义动画 id（向后兼容）',
    'anim': 'object, 声明式动画描述，见 ANIM_DESCRIPTION；存在时优先于 anim_style',
    'colors': 'object, 见 COLOR_FIELDS（anim.palette 可覆盖）',
    'icons': 'object, 桌面图标包，见 ICON_CONTRACT_FIELDS（v1.1）',
    'updated_at': 'number, Unix 秒，仅比对用',
}

# 桌面图标契约字段（v1.1，由 MiMo 设计稿补全后正式纳入）
ICON_CONTRACT_FIELDS = {
    'dir': '图标目录名，默认 "icons"',
    'manifest': '索引文件名，默认 "icons/index.json"',
    'base_size': '基础边长 px，默认 96',
    'hd_size': '高清边长 px，默认 192',
    'shape': 'squircle|circle|rounded',
    'corner_radius': '圆角比例 0..1',
    'fallback': 'letter|system（缺失图标时的兜底）',
    'label': 'object, 图标文字 size/color/max_lines',
    'grid': 'object, cols/rows',
    'gap': 'int, 间距 dp',
}

# colors 子字段（#RRGGBB / #AARRGGBB）
# 第一组为契约原名（推荐）；第二组为语义别名（v1.1 兼容，渲染器按 ALIAS→原名映射）
COLOR_FIELDS = {
    'bg_top': '桌面渐变上色',
    'bg_bottom': '桌面渐变下色',
    'glow': '桌面装饰光晕',
    'anim_primary': '动画主色',
    'anim_secondary': '动画辅色',
    'anim_bg_top': '动画背景渐变中心色',
    'anim_bg_mid': '动画背景渐变中间色',
    'anim_bg_bottom': '动画背景渐变边缘色',
    'background': '别名 → bg_top（底色）',
    'surface': '别名 → anim_bg_mid（面板色）',
    'primary': '别名 → anim_primary',
    'accent': '别名 → anim_secondary',
    'text': '别名 → 主文字色',
    'text_secondary': '别名 → 次文字色',
    'highlight': '别名 → glow',
    'scrim': '别名 → 遮罩色',
    'line': '别名 → 线框色',
}
COLOR_ALIAS_MAP = {
    'background': 'bg_top', 'surface': 'anim_bg_mid', 'primary': 'anim_primary',
    'accent': 'anim_secondary', 'text': 'text', 'text_secondary': 'text_secondary',
    'highlight': 'glow', 'scrim': 'scrim', 'line': 'line',
}

# =========================================================================== #
# 登录动画契约 —— 开放的组合空间（不是枚举）
# =========================================================================== #

# 时间线三阶段：登录语义固定（识别中→成功→收进桌面），但每阶段时长/动作全可配。
# MiMo 可在每阶段内自由编排 layers / particles / 变换，也可扩展自定义阶段。
ANIM_TIMELINE_PHASES = {
    'scan': '识别中：0 → resultAt；默认 ~2600ms，MiMo 可改时长与动作',
    'success': '认证成功定格；默认 ~1900ms，可设计聚拢/绽放/解算等任意转场',
    'shrink': '收进桌面角落；默认 ~680ms，目标位置 configurable',
    'custom': '允许 MiMo 声明额外阶段（如 pre_intro / outro），由渲染器按序播放',
}

# 渲染原子：每个都是电视端 2D Canvas 单 Pass 可实现的图元（弱机 1080p 友好）。
# MiMo 可任意组合、叠加、参数化，产出全新视觉；不限于内置三套。
# 禁用算子：MT9652 / low_ram / 2.7GB 上实测不可用，出现即判不合格（不是警告）
FORBIDDEN_PRIMITIVES = {
    'blur_blob': '高斯模糊，弱机实测掉帧，禁止',
    'glitch': '需 RenderEffect/shader，Canvas 2D 不可实现',
    'gpu_shader': 'GPU shader 不可用',
    'lottie': '无 Lottie 运行时',
    'rive': '无 Rive 运行时',
    'video_texture': '视频贴图不可用',
}

# Canvas 2D 单 Pass 可实现、准予使用的图元（MiMo 只能从此集合选，别自造名字）
ANIM_PRIMITIVES = {
    # 背景
    'gradient_rect': '线性/径向渐变矩形底（angle 参数）',
    'radial_gradient': '径向渐变底',
    'linear_gradient': '线性渐变底',
    'starfield': '星点场（数量/闪烁/视差）',
    'aurora': '流动色带（用逐帧重绘路径近似，非 shader）',
    'grid': '数据网格/透视网格',
    'vignette': '暗角压边（径向渐变叠加）',
    # 几何层
    'ring': '圆环（可虚线/刻度）',
    'arc': '旋转弧',
    'orbit_ellipse': '椭圆轨道（可多层交叉）',
    'polygon': '正多边形/星形',
    'spiral': '螺旋',
    'wave': '波形/声波',
    'constellation': '星座连线',
    'brackets': '四角取景框',
    'scanline': '扫描线/光带',
    'meteor': '流星拖尾',
    'petal': '花瓣',
    'heart': '爱心',
    'ripple': '扩散波纹',
    'flash': '全屏闪光',
    'glow': '辉光描边（用多层半透明描边近似，非 shader blur）',
    'data_stream': '数据流/字符雨',
    'dash_ring': '虚线进度环',
    'path': '任意 2D 路径（贝塞尔/折线），stroke 或 fill',
    'line': '线段',
    'image': '位图绘制（圆形裁切头像/装饰图，需预解码缓存）',
    'text': '文字层（主名/标签/小字，Paint 绘制）',
    # 粒子系统（可多套叠加）
    'particle_converge': '粒子向心汇聚',
    'particle_burst': '粒子爆发放射',
    'particle_orbit': '粒子绕轨',
    'particle_rain': '粒子飘落',
    'particle_swarm': '群集/柏林噪声流动',
    # 头像与文字
    'avatar_bitmap': '圆形位图头像（可加环/描边/光晕）',
    'avatar_geometric': '程序化几何头像（无需图片素材）',
    'text_primary': '主名（字距/字号/颜色/入场动画）',
    'text_label': '副标签（身份已确认等，可自定义文案）',
    'text_sub': '辅助小字（可自定义文案）',
    # 变换与合成
    'transform': '位移/缩放/旋转/透明度关键帧',
    'easing': '缓动曲线（linear/ease-in/out/in-out/spring/overshoot/custom-bezier）',
    'blend': '图层混合（add/screen/multiply/alpha）',
}

# 声明式动画描述格式：theme.json 的 anim 字段遵循此结构。
# 所有子项可选；未给出的由渲染器取内置默认或按 palette 推导。
ANIM_DESCRIPTION = {
    'id': 'string, 任意唯一 id（不限制枚举）',
    'name': 'string, 动画显示名',
    'palette': 'object, primary/secondary/bg_top/bg_mid/bg_bottom/accent[] — 可覆盖 colors',
    'background': 'object, {type: <primitive>, ...params}',
    'layers': 'array<object>, 每项 {type:<primitive>, params:{}, color, alpha, phase, easing, keyframes:[]}',
    'particles': 'array<object>, 每项 {system:<particle_*>, count, color, speed, size, life, phase}',
    'timeline': 'object, {scan|success|shrink|<custom>: {duration_ms, layers[], particles[], transitions[]}}',
    'avatar': 'object, {source: bitmap|geometric, shape, ring{}, entrance{}}',
    'text': 'object, {primary{}, label{}, sub{}} — 文案可自定义',
    'audio': 'object, {enabled, style: sweep|chime|pulse|sequence, notes[], duration_ms}',
    'shrink_target': 'string, top-left|top-right|custom:{x,y} — 收进桌面的落点',
    'performance': 'object, {max_layers, max_particles, target_fps} — 弱机预算，MiMo 需自评',
}

# 内置基线：仅作三级回退，不是可选上限。MiMo 完全可以设计第 4 套、第 5 套。
BUILTIN_ANIM_BASELINES = {
    'lidicn': {'id': 'scan-ring', 'summary': '青色扫描圆环 + 汇聚粒子 + 花体 L 头像'},
    'Emily': {'id': 'fairy', 'summary': '粉紫花瓣 + 爱心波纹 + 兔子头像'},
    'Kevin': {'id': 'space', 'summary': '深空恒星 + 流星 + 原子星环 + 星云聚拢'},
}

# 渲染器需要支持的原子能力边界（诚实声明，避免 MiMo 产出不可落地的效果）
RENDERER_CAPABILITIES = {
    'engine': 'Android Canvas 2D，单 View 单 Pass onDraw，postInvalidateOnAnimation 驱动',
    'resolution': '1920x1080 @ density 2.0（渲染分辨率，物理面板 3840x2160）',
    'can': ['2D 图元', '路径', '渐变', '位图圆形裁切', '粒子(预分配固定上限)',
            '关键帧变换', '程序化 PCM 音效', '背景位图预渲染缓存'],
    'cannot': ['GPU shader / OpenGL / Rive / Lottie', '高斯模糊(弱机禁用)',
               '视频贴图', '3D 变换', '逐帧大图解码'],
    'budget': {'max_layers': 24, 'max_particles': 240, 'target_fps': 45,
               'wallpaper_decode': 'RGB_565 1920x1080 一次性'},
}

# ---- 内置主题基准（桌面配色回退） ----
BUILTIN_THEMES = {
    'lidicn': {'anim_id': 'scan-ring', 'bg_top': '#18305C', 'bg_bottom': '#05070C',
               'glow': '#4C7DFF', 'anim_primary': '#5BE6FF', 'anim_secondary': '#62F0C8'},
    'Emily': {'anim_id': 'fairy', 'bg_top': '#7A3A6E', 'bg_bottom': '#2A0E28',
              'glow': '#FF8AD2', 'anim_primary': '#FF8AD2', 'anim_secondary': '#FFC4E0'},
    'Kevin': {'anim_id': 'space', 'bg_top': '#0A1A3F', 'bg_bottom': '#02040F',
              'glow': '#5FA8FF', 'anim_primary': '#6FB8FF', 'anim_secondary': '#FFFFFF'},
}

# ---- 素材规格 ----
ASSET_SPECS = {
    'wallpaper': {'formats': ['webp', 'png', 'jpg', 'jpeg'], 'size': '1920x1080',
                  'max_bytes': 8 * 1024 * 1024, 'note': '与渲染分辨率一致，禁止 4K 原图'},
    'avatar': {'formats': ['png', 'webp'], 'size': 'square>=256',
               'max_bytes': 4 * 1024 * 1024, 'note': '电视端裁圆，非正方形按短边裁'},
}
FILENAME_PATTERN = r'^[A-Za-z0-9._-]+$'
MAX_JSON_BYTES = 64 * 1024

DIR_NAS = '/app/data/themes/<user>/'
DIR_TV = '/sdcard/TVPilot/themes/<user>/'
KNOWN_USERS = ('lidicn', 'Emily', 'Kevin')

# 三级回退（动画未知/损坏时逐级降级，绝不白屏）
FALLBACK_CHAIN = ('external anim description', 'BUILTIN_ANIM_BASELINES[user]',
                  'hardcoded scan-ring renderer', 'code gradient')

DELIVERABLE_FORMAT = (
    'MiMo 产出：每用户一份 theme.json（含自由设计的 anim 声明式描述）+ '
    '壁纸/头像视觉设计说明 + 桌面图标契约设计 + 渲染器改动清单与性能自评。'
    'MiMo 不产出图片二进制。'
)

# v1.1 待补全：桌面图标契约，由本单 MiMo 设计稿补全
DESKTOP_ICON_CONTRACT = None


def validate_manifest_keys(manifest: dict, strict: bool = True) -> dict:
    """契约级硬校验：动画 id 开放，但原子/键名/硬件预算全部可机判。

    strict=True（默认）：越界原子、禁用算子、自造键名、超预算 -> errors（判不合格）。
    strict=False：上述降级为 warnings（仅用于兼容旧稿，不作为验收口径）。

    返回 {'ok', 'unknown', 'errors', 'warnings', 'budget_checked'}。
    """
    errors, unknown, warnings = [], [], []
    rec = errors if strict else warnings

    def bad(msg):
        rec.append(msg)

    if not isinstance(manifest, dict):
        return {'ok': False, 'unknown': [], 'errors': ['manifest must be object'],
                'warnings': [], 'budget_checked': False}
    if manifest.get('version', 1) != SCHEMA_VERSION:
        errors.append('unsupported version')
    for k in manifest:
        if k not in THEME_FIELDS:
            bad('unknown top-level key %r（必须用 THEME_FIELDS 里的名字）' % k)
    colors = manifest.get('colors') or {}
    if not isinstance(colors, dict):
        errors.append('colors must be object')
    else:
        for k in colors:
            if k not in COLOR_FIELDS:
                bad('colors.%r 不在 COLOR_FIELDS，禁止自造键名' % k)

    anim = manifest.get('anim')
    budget_checked = False
    if anim is not None:
        if not isinstance(anim, dict):
            errors.append('anim must be object')
        else:
            for i, layer in enumerate(anim.get('layers', []) or []):
                if not isinstance(layer, dict):
                    bad('layers[%d] must be object' % i)
                    continue
                t = layer.get('type')
                if t in FORBIDDEN_PRIMITIVES:
                    bad('layers[%d].type=%r 禁用：%s' % (i, t, FORBIDDEN_PRIMITIVES[t]))
                elif t and t not in ANIM_PRIMITIVES:
                    bad('layers[%d].type=%r 不在 ANIM_PRIMITIVES 白名单' % (i, t))
                paint = layer.get('paint') or {}
                if isinstance(paint, dict) and paint.get('blur'):
                    bad('layers[%d].paint.blur 禁止（高斯模糊弱机不可用）' % i)
            perf = anim.get('performance') or {}
            if isinstance(perf, dict) and perf:
                budget_checked = True
                b = RENDERER_CAPABILITIES['budget']
                ml = perf.get('max_layers', 0)
                mp = perf.get('max_particles', 0)
                fps = perf.get('target_fps', 0)
                if ml > b['max_layers']:
                    bad('max_layers=%d 超出 %d' % (ml, b['max_layers']))
                if mp > b['max_particles']:
                    bad('max_particles=%d 超出 %d' % (mp, b['max_particles']))
                if fps and fps < b['target_fps']:
                    bad('target_fps=%d 低于弱机最低要求 %d（宁可减特效也要保帧率）'
                        % (fps, b['target_fps']))
                actual = len(anim.get('layers', []) or [])
                if actual > b['max_layers']:
                    bad('实际 layers=%d 超出 %d' % (actual, b['max_layers']))
            else:
                bad('anim.performance 缺失：必须给出 max_layers/max_particles/target_fps 自评')
    return {'ok': not errors, 'unknown': unknown, 'errors': errors,
            'warnings': warnings, 'budget_checked': budget_checked}


def builtin_theme(user: str) -> dict:
    """返回某用户内置桌面基准；未命中回退 lidicn。"""
    return dict(BUILTIN_THEMES.get(user, BUILTIN_THEMES['lidicn']))


def asset_spec(kind: str) -> dict:
    """返回素材规格。"""
    return dict(ASSET_SPECS.get(kind, {}))


def renderer_capabilities() -> dict:
    """返回渲染器能力边界，供 MiMo 自评可落地性。"""
    import copy
    return copy.deepcopy(RENDERER_CAPABILITIES)


def spec_summary() -> dict:
    """契约摘要：供 MCP / MiMo 引用。含全部开放能力空间。"""
    return {
        'schema_version': SCHEMA_VERSION,
        'theme_fields': THEME_FIELDS,
        'color_fields': COLOR_FIELDS,
        'anim_timeline_phases': ANIM_TIMELINE_PHASES,
        'anim_primitives': ANIM_PRIMITIVES,
        'anim_description': ANIM_DESCRIPTION,
        'builtin_anim_baselines': BUILTIN_ANIM_BASELINES,
        'renderer_capabilities': RENDERER_CAPABILITIES,
        'builtin_themes': BUILTIN_THEMES,
        'asset_specs': ASSET_SPECS,
        'known_users': list(KNOWN_USERS),
        'fallback_chain': list(FALLBACK_CHAIN),
        'desktop_icon_contract': DESKTOP_ICON_CONTRACT,
        'deliverable_format': DELIVERABLE_FORMAT,
    }
