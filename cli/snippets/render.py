"""Type-safe substitution of JSON arg values into C# literals."""

import hashlib
import json
import re


def _float_lit(v):
    """Render a float literal, dropping the decimal point only when integral."""
    if isinstance(v, bool):
        raise ValueError("bool not accepted as float")
    f = float(v)
    if f == int(f):
        return f"{int(f)}f"
    return f"{f}f"


def _check_arity(name, value, expected):
    if not isinstance(value, list) or len(value) != expected:
        raise ValueError(
            f"{name} expects a list of {expected} numbers, got {value!r}"
        )


def render_literal(type_name, value):
    """Convert a JSON-decoded *value* into a C# literal expression for *type_name*.

    Generated identifiers are fully qualified (UnityEngine.Vector3 etc.) so the
    call line does not depend on what `using` directives the snippet body has.
    """
    if type_name == "string":
        if not isinstance(value, str):
            raise ValueError(f"string expects a JSON string, got {value!r}")
        return json.dumps(value, ensure_ascii=False)

    if type_name == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"int expects an integer, got {value!r}")
        return str(value)

    if type_name == "float":
        return _float_lit(value)

    if type_name == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"bool expects true/false, got {value!r}")
        return "true" if value else "false"

    if type_name == "vector2":
        _check_arity("vector2", value, 2)
        return f"new UnityEngine.Vector2({_float_lit(value[0])}, {_float_lit(value[1])})"

    if type_name == "vector3":
        _check_arity("vector3", value, 3)
        parts = ", ".join(_float_lit(v) for v in value)
        return f"new UnityEngine.Vector3({parts})"

    if type_name == "vector4":
        _check_arity("vector4", value, 4)
        parts = ", ".join(_float_lit(v) for v in value)
        return f"new UnityEngine.Vector4({parts})"

    if type_name == "color":
        if not isinstance(value, list) or len(value) not in (3, 4):
            raise ValueError(f"color expects [r,g,b] or [r,g,b,a], got {value!r}")
        rgba = list(value) + ([1] if len(value) == 3 else [])
        parts = ", ".join(_float_lit(v) for v in rgba)
        return f"new UnityEngine.Color({parts})"

    if type_name == "string[]":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"string[] expects a list of strings, got {value!r}")
        items = ", ".join(json.dumps(v, ensure_ascii=False) for v in value)
        inner = f" {items} " if items else " "
        return f"new string[] {{{inner}}}"

    if type_name == "int[]":
        if not isinstance(value, list) or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in value
        ):
            raise ValueError(f"int[] expects a list of ints, got {value!r}")
        items = ", ".join(str(v) for v in value)
        inner = f" {items} " if items else " "
        return f"new int[] {{{inner}}}"

    if type_name == "float[]":
        if not isinstance(value, list) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
        ):
            raise ValueError(f"float[] expects a list of numbers, got {value!r}")
        items = ", ".join(_float_lit(v) for v in value)
        inner = f" {items} " if items else " "
        return f"new float[] {{{inner}}}"

    raise ValueError(f"unsupported snippet arg type: {type_name!r}")


_USING_RE = re.compile(r"^\s*using\s+[^;]+;\s*$", re.MULTILINE)


def _split_usings(body):
    """Return (using_lines, remaining_body) by extracting top-level usings."""
    usings = _USING_RE.findall(body)
    remaining = _USING_RE.sub("", body).strip("\n")
    return [u.strip() for u in usings], remaining


def _wrapper_class_name(snippet_id, body):
    digest = hashlib.sha1(f"{snippet_id}\n{body}".encode("utf-8")).hexdigest()
    return f"__Snip_{digest[:16]}"


def render_submission(snippet_id, body, args_schema, arg_values):
    """Build the cs exec submission for a single snippet invocation.

    Output shape:

        <usings hoisted from body>
        static class __Snip_<hash16> {
            <body without usings>
        }
        __Snip_<hash16>.Run(<typed-literal>, ...)
    """
    rendered_args = []
    for spec in args_schema:
        name = spec["name"]
        type_name = spec["type"]
        if name in arg_values:
            value = arg_values[name]
        elif "default" in spec:
            value = spec["default"]
        else:
            raise ValueError(f"missing required arg: {name}")
        rendered_args.append(render_literal(type_name, value))

    usings, body_no_usings = _split_usings(body)
    cls = _wrapper_class_name(snippet_id, body)
    parts = []
    for u in usings:
        parts.append(u)
    if usings:
        parts.append("")
    parts.append(f"static class {cls} {{")
    parts.append(body_no_usings)
    parts.append("}")
    parts.append(f"{cls}.Run({', '.join(rendered_args)})")
    return "\n".join(parts)
