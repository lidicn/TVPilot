"""TVPilot 主题 lint：对 theme.json 做可机判的硬校验。

用法：
    python -m tvpilot.theme_lint <path/to/theme.json> [...]
    python -m tvpilot.theme_lint --dir data/themes

退出码：0 = 全部合格；1 = 存在不合格；2 = 用法/文件错误。
验收口径以 tvpilot/theme_contract.py 的 validate_manifest_keys(strict=True) 为准。
"""
from __future__ import annotations

import json
import os
import sys

from tvpilot import theme_contract as tc


def lint_file(path: str) -> dict:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return {'path': path, 'ok': False, 'errors': ['JSON 解析失败: %s' % e],
                'warnings': [], 'unknown': [], 'budget_checked': False}
    r = tc.validate_manifest_keys(data, strict=True)
    r['path'] = path
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
        results = lint_dir(argv[1])
    else:
        results = [lint_file(p) for p in argv]

    all_ok = True
    for r in results:
        ok = r['ok']
        all_ok = all_ok and ok
        print('%s  %s  budget_checked=%s' %
              ('PASS' if ok else 'FAIL', r['path'], r.get('budget_checked')))
        for e in r['errors']:
            print('   ERROR  %s' % e)
        for w in r.get('warnings', []):
            print('   WARN   %s' % w)
    print('\n合计 %d 份，合格 %d 份' % (len(results), sum(1 for r in results if r['ok'])))
    return 0 if all_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
