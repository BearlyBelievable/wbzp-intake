import ast
import pathlib
import string


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def source_files():
    paths = [*REPO_ROOT.glob("*.py"), *(REPO_ROOT / "intake").glob("*.py"), *(REPO_ROOT / "generator").glob("*.py")]
    return [path for path in paths if path.name != "errors.py"]


def parsed(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def placeholders(template):
    return {name.split("[")[0] for _, name, _, _ in string.Formatter().parse(template) if name}


def called_name(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def calls_to(names):
    for path in source_files():
        for node in ast.walk(parsed(path)):
            if isinstance(node, ast.Call) and called_name(node) in names:
                yield path.name, node
