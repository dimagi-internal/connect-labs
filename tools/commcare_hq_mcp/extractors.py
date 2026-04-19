"""Extract structured app information from CommCare HQ API responses.

Functions:
- extract_app_structure: module → form → case type tree
- extract_form_questions: question tree with types and labels
- extract_form_json_paths: flat mapping of question → JSON path (for pipeline schemas)
"""

from __future__ import annotations


def extract_app_structure(app: dict) -> dict:
    """Extract a clean app structure tree from a raw HQ app definition.

    Returns:
        {
            "app_id": str,
            "app_name": str,
            "modules": [
                {
                    "name": str,
                    "case_type": str,
                    "forms": [
                        {"name": str, "xmlns": str, "question_count": int}
                    ]
                }
            ],
            "case_types": [{"name": str, "module": str}]
        }
    """
    modules = []
    case_types_seen: set[str] = set()
    case_types: list[dict] = []

    for module in app.get("modules", []):
        mod_name = _get_name(module)
        ct = module.get("case_type", "")

        forms = []
        for form in module.get("forms", []):
            forms.append(
                {
                    "name": _get_name(form),
                    "xmlns": form.get("xmlns", ""),
                    "question_count": len(form.get("questions", [])),
                }
            )

        modules.append(
            {
                "name": mod_name,
                "case_type": ct,
                "forms": forms,
            }
        )

        if ct and ct not in case_types_seen:
            case_types_seen.add(ct)
            case_types.append({"name": ct, "module": mod_name})

    return {
        "app_id": app.get("id", ""),
        "app_name": app.get("name", ""),
        "modules": modules,
        "case_types": case_types,
    }


def extract_form_questions(app: dict, xmlns: str) -> dict | None:
    """Extract the question tree for a specific form identified by xmlns.

    Returns:
        {
            "form_name": str,
            "module_name": str,
            "case_type": str,
            "xmlns": str,
            "questions": [
                {
                    "id": str,         # e.g. "weight"
                    "type": str,       # e.g. "Int"
                    "label": str,      # e.g. "Weight (grams)"
                    "path": str,       # e.g. "/data/weight"
                    "required": bool,
                    "constraint": str | None,
                    "relevant": str | None,
                    "calculate": str | None,
                    "options": [{"value": str, "label": str}] | None,
                    "children": [...] | None,  # for groups/repeats
                }
            ]
        }
    """
    for module in app.get("modules", []):
        for form in module.get("forms", []):
            if form.get("xmlns") == xmlns:
                questions = _process_questions(form.get("questions", []))
                return {
                    "form_name": _get_name(form),
                    "module_name": _get_name(module),
                    "case_type": module.get("case_type", ""),
                    "xmlns": xmlns,
                    "questions": questions,
                }
    return None


def extract_form_json_paths(app: dict, xmlns: str) -> dict | None:
    """Extract a flat mapping of form questions to their JSON submission paths.

    This is the key tool for building PIPELINE_SCHEMAS — it tells you exactly
    what path to use for each field.

    Returns:
        {
            "form_name": str,
            "xmlns": str,
            "case_type": str,
            "paths": [
                {
                    "json_path": "form.weight",        # use this in PIPELINE_SCHEMAS
                    "question_path": "/data/weight",   # original XForm path
                    "type": "Int",                     # CommCare data type
                    "label": "Weight (grams)",         # human-readable label
                }
            ]
        }
    """
    for module in app.get("modules", []):
        for form in module.get("forms", []):
            if form.get("xmlns") == xmlns:
                paths = _build_json_paths(form.get("questions", []))
                return {
                    "form_name": _get_name(form),
                    "xmlns": xmlns,
                    "case_type": module.get("case_type", ""),
                    "paths": paths,
                }
    return None


def _process_questions(questions: list[dict]) -> list[dict]:
    """Process raw HQ question list into a clean tree."""
    result = []
    for q in questions:
        processed = {
            "id": _question_id_from_path(q.get("value", "")),
            "type": q.get("type", ""),
            "label": _get_label(q),
            "path": q.get("value", ""),
            "required": q.get("required", False),
        }

        # Optional fields — only include if present
        if q.get("constraint"):
            processed["constraint"] = q["constraint"]
        if q.get("relevant"):
            processed["relevant"] = q["relevant"]
        if q.get("calculate"):
            processed["calculate"] = q["calculate"]

        # Options for select questions
        options = q.get("options")
        if options:
            processed["options"] = [{"value": o.get("value", ""), "label": _get_label(o)} for o in options]

        # Nested questions for groups/repeats
        children = q.get("children")
        if children:
            processed["children"] = _process_questions(children)

        result.append(processed)
    return result


def _build_json_paths(questions: list[dict], prefix: str = "form") -> list[dict]:
    """Build flat list of JSON paths from HQ question definitions.

    Maps each question's XForm path to its form submission JSON path.
    Rules:
        /data/weight           → form.weight
        /data/group/question   → form.group.question
        /data/repeat/question  → form.repeat[].question
    """
    paths: list[dict] = []

    for q in questions:
        q_path = q.get("value", "")
        q_type = q.get("type", "")
        label = _get_label(q)

        # Convert XForm path to JSON path
        json_path = _xform_path_to_json_path(q_path, prefix)

        # Skip group/repeat containers themselves — only include leaf questions
        if q_type in ("Group", "Repeat"):
            # Recurse into children with updated prefix
            children = q.get("children", [])
            if children:
                child_prefix = json_path
                if q_type == "Repeat":
                    child_prefix = f"{json_path}[]"
                paths.extend(_build_json_paths(children, prefix=child_prefix))
            continue

        if json_path:
            paths.append(
                {
                    "json_path": json_path,
                    "question_path": q_path,
                    "type": q_type,
                    "label": label,
                }
            )

    return paths


def _xform_path_to_json_path(xform_path: str, prefix: str = "form") -> str:
    """Convert an XForm question path to a form submission JSON path.

    /data/weight → form.weight
    /data/group/question → form.group.question

    When a non-default prefix is supplied (e.g. for children of groups/repeats),
    only the last path segment is appended since the prefix already encodes the
    parent path.
    """
    if not xform_path:
        return ""

    # Strip /data/ prefix
    parts = xform_path.strip("/").split("/")
    if parts and parts[0] == "data":
        parts = parts[1:]

    if not parts:
        return ""

    # If we have a custom prefix (inside a group/repeat), the prefix already
    # represents the parent path, so we only need the last segment (the
    # question's own ID).
    if prefix != "form":
        return f"{prefix}.{parts[-1]}"

    return f"{prefix}.{'.'.join(parts)}"


def _question_id_from_path(path: str) -> str:
    """Extract the question ID (last segment) from an XForm path."""
    if not path:
        return ""
    return path.rstrip("/").rsplit("/", 1)[-1]


def _get_name(obj: dict) -> str:
    """Extract display name from HQ object (handles dict/string name field)."""
    name = obj.get("name", "")
    if isinstance(name, dict):
        return name.get("en", next(iter(name.values()), ""))
    return str(name)


def _get_label(obj: dict) -> str:
    """Extract display label from a question object."""
    label = obj.get("label", "")
    if isinstance(label, dict):
        return label.get("en", next(iter(label.values()), ""))
    return str(label)
