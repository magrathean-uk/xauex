import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
XAUEX_ROOT = REPO_ROOT / "xauex"
COMPATIBILITY_ROOTS = ("auth", "backtester", "bot", "config", "main")


def test_xauex_production_modules_do_not_import_compatibility_packages() -> None:
    violations: list[str] = []
    for path in sorted(XAUEX_ROOT.rglob("*.py")):
        if "tests" in path.relative_to(XAUEX_ROOT).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported = str(alias.name or "")
                    if imported in COMPATIBILITY_ROOTS or imported.startswith(
                        tuple(f"{root}." for root in COMPATIBILITY_ROOTS)
                    ):
                        violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {imported}")
                continue
            if module in COMPATIBILITY_ROOTS or module.startswith(
                tuple(f"{root}." for root in COMPATIBILITY_ROOTS)
            ):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} imports {module}")

    assert violations == []


def test_backtester_make_target_uses_canonical_package_from_repo_root() -> None:
    makefile = (XAUEX_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "cd .. && $(PYTHON) -m xauex.backtester" in makefile
    assert "$(PYTHON) -m backtester" not in makefile
