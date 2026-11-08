#!/usr/bin/env python3
"""Check import direction without importing or starting the application."""
import ast
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = {'codex', 'claude', 'deepseek', 'zcode', 'antigravity'}


def dependencies(source, package):
    tree = ast.parse(source)
    importlib_names, import_functions = {'importlib'}, {'__import__'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for entry in node.names:
                if entry.name == 'importlib':
                    importlib_names.add(entry.asname or entry.name)
                yield node.lineno, entry.name
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ''
            if node.level:
                name = importlib.util.resolve_name('.' * node.level + name, package)
            for entry in node.names:
                if name == 'importlib' and entry.name == 'import_module':
                    import_functions.add(entry.asname or entry.name)
                yield node.lineno, name + '.' + entry.name
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        dynamic = (isinstance(function, ast.Name) and function.id in import_functions
                   or isinstance(function, ast.Attribute) and function.attr == 'import_module'
                   and isinstance(function.value, ast.Name) and function.value.id in importlib_names)
        if dynamic and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            name = node.args[0].value
            if name.startswith('.'):
                name = importlib.util.resolve_name(name, package)
            yield node.lineno, name


def rule_for(source, target):
    src, dst = source.split('.'), target.split('.')
    if len(src) < 2 or len(dst) < 2 or src[0] != 'bridge' or dst[0] != 'bridge':
        return None
    if src[1] == 'platforms' and dst[1] in {'app', 'api', 'features', 'clients'}:
        return 'platform-upward'
    if src[1] in {'features', 'clients'} and dst[1] in {'app', 'api'}:
        return 'implementation-upward'
    if (len(src) > 2 and len(dst) > 2 and src[1] == dst[1] == 'clients'
            and src[2] in PROVIDERS and dst[2] in PROVIDERS and src[2] != dst[2]):
        return 'provider-peer'
    return None


def violations(root):
    found = {}
    for path in sorted((root / 'bridge').rglob('*.py')):
        relative = path.relative_to(root).as_posix()
        module = '.'.join(path.relative_to(root).with_suffix('').parts)
        package = module.rsplit('.', 1)[0]
        for line, dependency in dependencies(path.read_text(encoding='utf-8'), package):
            rule = rule_for(module, dependency)
            if rule:
                key = (relative, dependency, rule)
                found.setdefault(key, []).append(line)
    return found


def check(root, exceptions):
    allowed = {}
    for row in exceptions:
        key = (row['file'], row['dependency'], row['rule'])
        if key in allowed or not row.get('reason', '').strip():
            raise ValueError('Each dependency exception needs a unique edge and a reason: ' + str(key))
        allowed[key] = row
    actual = violations(root)
    errors = [f'{file}:{actual[(file, dep, rule)][0]}: {rule}: {dep}'
              for file, dep, rule in sorted(actual.keys() - allowed.keys())]
    errors += [f'Remove obsolete exception: {file} -> {dep} ({rule})'
               for file, dep, rule in sorted(allowed.keys() - actual.keys())]
    return errors


def main():
    exceptions = json.loads((ROOT / 'docs/architecture/dependency-exceptions.json').read_text())
    errors = check(ROOT, exceptions)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(f'Module boundaries passed; {len(exceptions)} explicit legacy edges remain.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
