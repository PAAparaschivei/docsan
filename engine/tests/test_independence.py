"""The verifier must not share code with the sanitizer."""

import ast
import sys
from pathlib import Path

import docsan.verify


def test_verifier_imports_only_the_standard_library():
    tree = ast.parse(Path(docsan.verify.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import in the verifier"
            names = [node.module.split(".")[0]]
        elif isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        else:
            continue
        for name in names:
            assert name == "__future__" or name in sys.stdlib_module_names, f"verifier imports {name}"
