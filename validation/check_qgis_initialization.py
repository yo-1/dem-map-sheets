"""AST gate for direct QGIS lifecycle calls in test Python files.

Not a sandbox: dynamic imports, indirect wrappers and runtime-generated code
need review. The sole lifecycle implementation is the exact helper path.
"""
import ast
from pathlib import Path
import sys

ALLOWED = 'dem_map_sheets/tests/qgis_runtime.py'


def violations(source):
    tree = ast.parse(source)
    constructors = {'QgsApplication'}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == 'QgsApplication':
                    constructors.add(alias.asname or alias.name)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ''
        if name in constructors or name in ('initQgis', 'exitQgis'):
            hits.append((node.lineno, name))
    return sorted(hits)


def check(root):
    errors = []
    for folder in ('dem_map_sheets/tests', 'validation'):
        for path in sorted((root / folder).rglob('*.py')):
            relative = path.relative_to(root).as_posix()
            if relative == ALLOWED:
                continue
            for line, name in violations(path.read_text(encoding='utf-8')):
                errors.append(f'{relative}:{line}: direct {name} call; use the shared test runtime')
    return errors


if __name__ == '__main__':
    errors = check(Path(__file__).resolve().parents[1])
    print('\n'.join(errors) if errors else 'QGIS lifecycle AST gate: OK')
    sys.exit(bool(errors))
