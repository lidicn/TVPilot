"""把契约校验器的 errors 投影成结构化 violations[{key, reason}]。

不修改 tvpilot/theme_contract.py：校验逻辑以
validate_manifest_keys(strict=True) 为唯一真源，本模块只做
「错误文本 -> 结构化键」的投影，供 tv_theme_push 报错体与 theme_lint 使用。

AF-PM 11-11 裁定：不许放宽 theme_contract.py；本模块为新增文件，
theme_contract.py 的 diff 应为空。
"""
from __future__ import annotations

import re

# 顺序即优先级：越具体的模式放前面。
_KEY_RULES = (
    (re.compile(r"^layers\[(\d+)\]\.type"), lambda m: "anim.layers[%s].type" % m.group(1)),
    (re.compile(r"^layers\[(\d+)\]\.paint\.blur"), lambda m: "anim.layers[%s].paint.blur" % m.group(1)),
    (re.compile(r"^layers\[(\d+)\] must be object"), lambda m: "anim.layers[%s]" % m.group(1)),
    (re.compile(r"^max_layers="), lambda m: "anim.performance.max_layers"),
    (re.compile(r"^max_particles="), lambda m: "anim.performance.max_particles"),
    (re.compile(r"^target_fps="), lambda m: "anim.performance.target_fps"),
    (re.compile(r"^实际 layers="), lambda m: "anim.layers"),
    (re.compile(r"^anim\.performance 缺失"), lambda m: "anim.performance"),
    (re.compile(r"^unknown top-level key '([^']+)'"), lambda m: m.group(1)),
    (re.compile(r"^colors\.'([^']+)'"), lambda m: "colors.%s" % m.group(1)),
    (re.compile(r"^unsupported version"), lambda m: "version"),
    (re.compile(r"^colors must be object"), lambda m: "colors"),
    (re.compile(r"^anim must be object"), lambda m: "anim"),
    (re.compile(r"^manifest must be object"), lambda m: "manifest"),
)


def key_for(message):
    """从一条契约错误文本推断结构化 key；推断不出时归到 manifest。"""
    for rx, fn in _KEY_RULES:
        m = rx.match(message)
        if m:
            return fn(m)
    return "manifest"


def violations_from_errors(errors):
    """errors -> [{'key': ..., 'reason': ...}]，顺序与校验器返回一致。"""
    return [{"key": key_for(e), "reason": e} for e in (errors or [])]


def validate(manifest, strict=True):
    """调用唯一真源，返回 (result, violations)。

    result 仍是 theme_contract.validate_manifest_keys 的原始返回；
    violations 只是给报错体用的结构化投影，不参与判定。
    """
    from tvpilot import theme_contract as tc

    result = tc.validate_manifest_keys(manifest, strict=strict)
    return result, violations_from_errors(result["errors"])


__all__ = ["key_for", "violations_from_errors", "validate"]
