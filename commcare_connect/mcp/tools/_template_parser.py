"""AST parser for template module source — extracts RENDER_CODE, DEFINITION, PIPELINE_SCHEMAS, TEMPLATE."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


class TemplateParseError(Exception):
    """Raised when the template source cannot be parsed under our grammar."""


@dataclass
class ParsedTemplate:
    render_code: str
    definition: dict
    pipeline_schemas: list = field(default_factory=list)
    template_key: str = ""


def parse_template_source(template_source: str, sidecar_files: dict[str, str]) -> ParsedTemplate:
    try:
        tree = ast.parse(template_source)
    except SyntaxError as e:
        raise TemplateParseError(f"template source is not valid Python: {e}") from e

    names: dict[str, object] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target = stmt.targets[0].id
            names[target] = _evaluate(stmt.value, names, sidecar_files)

    def require(name: str, kind: type) -> object:
        if name not in names:
            raise TemplateParseError(f"template source is missing top-level {name}")
        value = names[name]
        if not isinstance(value, kind):
            raise TemplateParseError(f"{name} must be a {kind.__name__}, got {type(value).__name__}")
        return value

    render_code = require("RENDER_CODE", str)
    definition = require("DEFINITION", dict)
    pipeline_schemas = names.get("PIPELINE_SCHEMAS", [])
    if not isinstance(pipeline_schemas, list):
        raise TemplateParseError(f"PIPELINE_SCHEMAS must be a list, got {type(pipeline_schemas).__name__}")
    template = require("TEMPLATE", dict)
    template_key = template.get("key")
    if not isinstance(template_key, str) or not template_key:
        raise TemplateParseError("TEMPLATE['key'] must be a non-empty string")

    return ParsedTemplate(
        render_code=render_code,
        definition=definition,
        pipeline_schemas=pipeline_schemas,
        template_key=template_key,
    )


def _match_sidecar_call(node: ast.AST) -> str | None:
    """Recognize `(Path(__file__).parent / "X").read_text(...)` and return X."""
    # Outer call: <expr>.read_text(...)
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "read_text"):
        return None
    # The object being read: Path(__file__).parent / "X"
    binop = node.func.value
    if not (isinstance(binop, ast.BinOp) and isinstance(binop.op, ast.Div)):
        return None
    # Right side: string literal sidecar name.
    if not (isinstance(binop.right, ast.Constant) and isinstance(binop.right.value, str)):
        return None
    # Left side: Path(__file__).parent
    left = binop.left
    if not (isinstance(left, ast.Attribute) and left.attr == "parent"):
        return None
    parent_call = left.value
    if not (
        isinstance(parent_call, ast.Call) and isinstance(parent_call.func, ast.Name) and parent_call.func.id == "Path"
    ):
        return None
    if not (
        len(parent_call.args) == 1
        and isinstance(parent_call.args[0], ast.Name)
        and parent_call.args[0].id == "__file__"
    ):
        return None
    return binop.right.value


def _evaluate(node: ast.AST, names: dict, sidecar_files: dict[str, str]) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise TemplateParseError(f"unknown name {node.id} at line {getattr(node, 'lineno', '?')}")
        return names[node.id]
    if isinstance(node, ast.List):
        return [_evaluate(elt, names, sidecar_files) for elt in node.elts]
    if isinstance(node, ast.Dict):
        result = {}
        for k, v in zip(node.keys, node.values):
            if k is None:
                raise TemplateParseError(f"dict unpacking (**) not supported at line {getattr(node, 'lineno', '?')}")
            result[_evaluate(k, names, sidecar_files)] = _evaluate(v, names, sidecar_files)
        return result
    if isinstance(node, ast.Tuple):
        return tuple(_evaluate(elt, names, sidecar_files) for elt in node.elts)
    if isinstance(node, ast.Set):
        return {_evaluate(elt, names, sidecar_files) for elt in node.elts}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        # `-1` parses as UnaryOp(USub, Constant(1)) in Python's AST.
        return -node.operand.value
    sidecar_name = _match_sidecar_call(node)
    if sidecar_name is not None:
        if sidecar_name not in sidecar_files:
            raise TemplateParseError(
                f"template references sidecar {sidecar_name!r} but no such file " "was supplied in sidecar_files"
            )
        return sidecar_files[sidecar_name]
    raise TemplateParseError(f"unsupported expression at line {getattr(node, 'lineno', '?')}: " f"{ast.dump(node)}")
