"""AST guard against QGIS lifecycle calls in individual test modules.

Run: python -m unittest dem_map_sheets.tests.test_qgis_init_guard
"""
import ast
from pathlib import Path
import unittest


LIFECYCLE_METHODS = {"initQgis", "exitQgis"}
APP_CLASS = "QgsApplication"
ALLOWED_HELPER = "qgis_test_runtime.py"


def find_direct_lifecycle_calls(source):
    """Return (line, call) for explicit lifecycle operations.

    This is a conservative static check, not a proof of runtime safety.
    """
    tree = ast.parse(source)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if ((isinstance(func, ast.Name) and func.id == APP_CLASS) or
                (isinstance(func, ast.Attribute) and func.attr == APP_CLASS)):
            findings.append((node.lineno, APP_CLASS))
        elif isinstance(func, ast.Attribute) and func.attr in LIFECYCLE_METHODS:
            findings.append((node.lineno, func.attr))
    return findings


class QGISInitializationGuardTests(unittest.TestCase):
    def test_rejects_direct_constructor(self):
        self.assertEqual(find_direct_lifecycle_calls("x = QgsApplication([], False)"),
                         [(1, "QgsApplication")])

    def test_rejects_qualified_constructor(self):
        self.assertEqual(find_direct_lifecycle_calls(
            'x = qgis.core.QgsApplication([], False)'), [(1, 'QgsApplication')])

    def test_rejects_init_and_exit(self):
        self.assertEqual(find_direct_lifecycle_calls("app.initQgis()\napp.exitQgis()"),
                         [(1, "initQgis"), (2, "exitQgis")])

    def test_allows_comments_strings_and_instance_lookup(self):
        self.assertEqual(find_direct_lifecycle_calls(
            '# QgsApplication([], False)\ns = "initQgis()"\n'
            'app = QgsApplication.instance()'), [])

    def test_repository_test_modules(self):
        root = Path(__file__).resolve().parent
        violations = []
        for path in sorted(root.rglob("*.py")):
            if path.name == ALLOWED_HELPER:
                continue
            for line, call in find_direct_lifecycle_calls(
                    path.read_text(encoding="utf-8")):
                violations.append(f"{path.relative_to(root)}:{line}: {call}")
        self.assertEqual(violations, [], "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
