"""TVPilot 主题 lint：对 theme.json 做可机判的硬校验。

用法：
    python -m tvpilot.theme_lint <dir>               # 位置参数：遍历目录下 <user>/theme.json
    python -m tvpilot.theme_lint <a.json> [<b.json>]  # 位置参数：指定文件
    python -m tvpilot.theme_lint --dir <dir>          # 兼容旧写法

退出码：0 = 全部合格；1 = 存在不合格；2 = 用法/文件错误。
验收口径以 tvpilot/theme_contract.py 的 validate_manifest_keys(strict=True) 为准。
"""
from __future__ import annotations

import json
import os
import sys

from tvpilot import theme_contract as tc
from tvpilot import theme_violations


def lint_file(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        reason = 'JSON 解析失败: %s' % e
        return {'path': path, 'ok': False, 'errors': [reason],
                'violations': [{'key': 'theme.json', 'reason': reason}],
                'warnings': [], 'unknown': [], 'budget_checked': False}
    r = tc.validate_manifest_keys(data, strict=True)
    r['path'] = path
    r['violations'] = theme_violations.violations_from_errors(r['errors'])
    return r


def lint_dir(root: str) -> list:
    out = []
    for user in sorted(os.listdir(root)):
        p = os.path.join(root, user, 'theme.json')
        if os.path.isfile(p):
            out.append(lint_file(p))
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == '--dir':
        if len(argv) < 2:
            print('需要目录参数', file=sys.stderr)
            return 2
        if not os.path.isdir(argv[1]):
            print('目录不存在: %s' % argv[1], file=sys.stderr)
            return 2
        results = lint_dir(argv[1])
    elif len(argv) == 1 and os.path.isdir(argv[0]):
        results = lint_dir(argv[0])
    else:
        results = [lint_file(p) for p in argv]

    if not results:
        print('未找到任何 theme.json', file=sys.stderr)
        return 2

    all_ok = True
    for r in results:
        ok = r['ok']
        all_ok = all_ok and ok
        print('%s  %s  budget_checked=%s' %
              ('PASS' if ok else 'FAIL', r['path'], r.get('budget_checked')))
        for v in r.get('violations', []):
            print('   VIOLATION  key=%s  reason=%s' % (v['key'], v['reason']))
        for w in r.get('warnings', []):
            print('   WARN   %s' % w)
    print('\n合计 %d 份，合格 %d 份' % (len(results), sum(1 for r in results if r['ok'])))
    return 0 if all_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
