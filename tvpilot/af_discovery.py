# -*- coding: utf-8 -*-
"""AF ↔ TVPilot 联动 · D1 HA MQTT discovery 报文构造（契约 v2.1 §一/§二/§三/§九）。

**发布闸**：AF-PM 11-15 令「D1 discovery 代码可先行写好、发布开关留闸」。
本模块只负责「构造报文」，`PUBLISH_ENABLED` 恒 False（且额外 env 双闸）；
`publish_all()` 在闸关时返回 dry-run 清单，**⛔ 不 import paho、不连 broker、
不读凭据、不发布任何 MQTT 消息**。等出资人加完 HA 集成 + AF-PM 明确放行后
才由部署侧把闸打开，且闸门状态必须可审计。

注：2026-11-15 01:0x 由 TVPilot 从 `__pycache__/af_discovery.cpython-313.pyc`
恢复重建（此前被一次并行写覆盖）。接口与常量按 pyc 逐项还原，行为等价；
契约内容版本已由 v2.1 升 v2.2（result 6 值、一期四腿），枚举随 v2.2 为准。
"""
from __future__ import annotations

import os

# ---- 双发布闸：代码常量 + env，缺一即关 ----------------------------------
PUBLISH_ENABLED = False
PUBLISH_GATE_ENV = 'TVPILOT_AF_DISCOVERY_PUBLISH'

DISCOVERY_PREFIX = 'homeassistant'
NODE_ID = 'tvpilot_tv'
AVAILABILITY_TOPIC = 'tvpilot_tv/availability'

DEVICE = {
    'identifiers': ['tvpilot_tv_device'],
    'name': 'TVPilot',
    'manufacturer': 'v0.9',
    'model': 'TVPilot',
    'sw_version': '0.9.0-dev',
}

# stale 阈值（秒），按通道特性定，写入 discovery attributes（取数面可复算）
STALE_S = {
    's1_power_state': 120,
    's2_adb_health': 300,
    's3_foreground_app': 60,
    's4_current_channel': 90,
    's5_arcface_state': 600,
    's6_faceid_last': 600,
    's7_tvdesk_login_state': 600,
    's8_morning_news_stage': 600,
}

# unknown 必带 reason（契约 §三 通用语义 + DESIGN 文件 reason 枚举）
REASON_ENUM = (
    'tv_off', 'adb_off', 'network_unreachable', 'shell_unavailable',
    'setting_unreadable', 'etiology_undetermined', 'arcface_down', 'not_probed',
    'not_applicable_iptv_native', 'capture_failed', 'timeout', 'stale', 'probe_error',
)


def _base(object_id: str, name: str) -> dict:
    return {
        'unique_id': '%s_%s' % (NODE_ID, object_id),
        'object_id': object_id,
        'name': name,
        'device': DEVICE,
        'availability_topic': AVAILABILITY_TOPIC,
        'payload_available': 'online',
        'payload_not_available': 'offline',
        'retain': True,
        'qos': 1,
    }


def _state_topic(object_id: str) -> str:
    return '%s/%s/state' % (NODE_ID, object_id)


def build_discovery() -> list[dict]:
    """返回 [{component, object_id, topic, config}]，纯构造、零副作用。"""
    out = []

    # §一 1 枚 button
    c = _base('button_tv_morning_news', 'TVPilot 早间新闻')
    c.update({
        'command_topic': 'tvpilot_tv/button_tv_morning_news/set',
        'payload_press': 'PRESS',
        'icon': 'mdi:television-play',
        'entity_category': 'config',
    })
    out.append({
        'component': 'button', 'object_id': 'button_tv_morning_news',
        'topic': f'{DISCOVERY_PREFIX}/button/{NODE_ID}/config', 'config': c,
    })

    # §二 result 传感器（v2.2：6 值，一期 4 值活）
    c = _base('sensor_tv_morning_news_result', 'TVPilot 新闻结果')
    c.update({
        'state_topic': _state_topic('sensor_tv_morning_news_result'),
        'value_template': '{{ value_json.value }}',
        'json_attributes_topic': _state_topic('sensor_tv_morning_news_result'),
        'icon': 'mdi:message-text-clock',
        'attributes': {
            'enum': ['iptv_ok', 'adb_off_tv_on', 'wake_fail', 'iptv_fail',
                     'faceid_timeout', 'arcface_fail'],
            'phase1_active': ['iptv_ok', 'adb_off_tv_on', 'wake_fail', 'iptv_fail'],
            'phase1_dormant': ['faceid_timeout', 'arcface_fail'],
            'budget_s': 240,
        },
    })
    out.append({
        'component': 'sensor', 'object_id': 'sensor_tv_morning_news_result',
        'topic': f'{DISCOVERY_PREFIX}/sensor/{NODE_ID}/config', 'config': c,
    })

    # §三 8 枚状态面
    specs = [
        ('s1_power_state', 'TVPilot 电视电源', 'on/standby/off/unknown'),
        ('s2_adb_health', 'TVPilot ADB 健康', 'ok/off/unknown'),
        ('s3_foreground_app', 'TVPilot 前台应用', '包名/unknown'),
        ('s4_current_channel', 'TVPilot 当前频道', '频道标识/unknown'),
        ('s5_arcface_state', 'TVPilot ArcFace 状态', 'running/stopped/unknown'),
        ('s6_faceid_last', 'TVPilot 最近识别', '{who,at,source}/unknown'),
        ('s7_tvdesk_login_state', 'TVPilot tvdesk 登录态', 'logged_*/unknown'),
        ('s8_morning_news_stage', 'TVPilot 编排进度', 'idle…done/failed'),
    ]
    for oid, name, value_domain in specs:
        c = _base('sensor_' + oid, name)
        c.update({
            'state_topic': _state_topic('sensor_' + oid),
            'value_template': '{{ value_json.value }}',
            'json_attributes_topic': _state_topic('sensor_' + oid),
            'icon': 'mdi:television',
            'attributes': {
                'stale_s': STALE_S[oid],
                'value_domain': value_domain,
                'reason_enum': list(REASON_ENUM),
                'last_checked_required': True,
                'no_guess': True,
            },
        })
        out.append({
            'component': 'sensor', 'object_id': 'sensor_' + oid,
            'topic': f'{DISCOVERY_PREFIX}/sensor/{NODE_ID}/config', 'config': c,
        })

    # 按 object_id 补充各状态面的契约专属属性（§三/§五/§六/§十一）
    by_id = {it['object_id']: it['config'] for it in out}
    by_id['sensor_s2_adb_health']['attributes'].update({
        'quad': ['adb_enabled_setting', 'adbd_alive', 'network_reachable', 'last_checked'],
        'etiology_distinguishable': True,
        'probe_mode': 'orchestration_and_events_only',
    })
    by_id['sensor_s5_arcface_state']['attributes'].update({
        'phase1': 'no_orchestration_probe',
        'phase1_default': 'unknown(reason=not_probed)',
        'probe': 'GET {ARC_BASE}/api/health',
        'privacy': 'no_scan_latest',
    })
    by_id['sensor_s6_faceid_last']['attributes'].update({
        'retention': 'last',
        'fields': ['who', 'at', 'source'],
        'raw_face_data_forbidden': True,
        'persistence_forbidden': True,
    })
    by_id['sensor_s7_tvdesk_login_state']['attributes'].update({
        'phase1_default': 'unknown(reason=not_applicable_iptv_native)',
    })
    by_id['sensor_s8_morning_news_stage']['attributes'].update({
        'all_stages': ['idle', 'waking', 'probing', 'arcface_ensuring', 'tvdesk_opening',
                       'faceid_waiting', 'iptv_launching', 'channel_setting', 'done', 'failed'],
        'phase1_unused': ['arcface_ensuring', 'tvdesk_opening', 'faceid_waiting'],
        'terminal_stage': ['done', 'failed'],
    })

    return out


def gate_open() -> bool:
    """双闸：代码常量 + env。缺一即关。"""
    return PUBLISH_ENABLED and os.environ.get(PUBLISH_GATE_ENV) == '1'


def publish_all(publisher=None) -> dict:
    """发布闸关时返回 dry-run 清单（零副作用）；开时才调用注入的 publisher。

    publisher 协议：publish(topic:str, payload:dict, retain:bool) -> None。
    ⛔ 本函数不 import paho、不读 MQTT_PASS、不连 broker。
    """
    items = build_discovery()
    if not gate_open():
        return {
            'published': 0,
            'gate': 'closed',
            'reason': 'PUBLISH_ENABLED=False 或 env 闸未开；等 AF-PM 放行 + 出资人 HA 集成',
            'dry_run': [{'topic': it['topic'], 'unique_id': it['config']['unique_id']}
                        for it in items],
        }
    if publisher is None:
        return {'published': 0, 'gate': 'open', 'reason': 'publisher_not_injected'}
    for it in items:
        publisher(it['topic'], it['config'], True)
    return {'published': len(items), 'gate': 'open', 'reason': None}


__all__ = [
    'PUBLISH_ENABLED', 'PUBLISH_GATE_ENV', 'DISCOVERY_PREFIX', 'NODE_ID',
    'AVAILABILITY_TOPIC', 'DEVICE', 'STALE_S', 'REASON_ENUM',
    'build_discovery', 'gate_open', 'publish_all',
]
