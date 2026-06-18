"""Snippet file IO: frontmatter parsing, on-disk layout."""

import json
from pathlib import Path
import re

ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
VALID_SAFETY = {"read-only", "mutates"}
VALID_TYPES = {
    "string", "int", "float", "bool",
    "vector2", "vector3", "vector4", "color",
    "string[]", "int[]", "float[]",
}

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_CODE_BLOCK_RE = re.compile(r"```csharp\n(.*?)\n```", re.DOTALL)
_RUN_METHOD_RE = re.compile(r"\bstatic\s+[\w<>\[\],\s\.]+?\s+Run\s*\(")


class SnippetParseError(ValueError):
    pass


def _parse_yaml_subset(text):
    """Parse the limited YAML we accept in frontmatter.

    Supports:
      - `key: scalar`
      - `key: [..]` / `key: {..}` (parsed as JSON)
      - `key:` followed by indented `- name: ...` blocks (lists of dicts)
      - `key:` followed by indented `subkey: value` blocks (mappings)
    """
    out = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        if not line.lstrip().startswith("- "):
            if ":" not in line:
                raise SnippetParseError(f"malformed frontmatter line: {line!r}")
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            if not rest:
                block_lines = []
                i += 1
                while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                    block_lines.append(lines[i])
                    i += 1
                out[key] = _parse_block(block_lines)
                continue
            out[key] = _parse_scalar_or_inline(rest)
        else:
            raise SnippetParseError(f"top-level list not allowed: {line!r}")
        i += 1
    return out


def _parse_block(block_lines):
    stripped = [ln for ln in block_lines if ln.strip()]
    if not stripped:
        return {}
    # Skip leading comment lines when detecting the block type.
    non_comment = [ln for ln in stripped if not ln.lstrip().startswith("#")]
    if not non_comment:
        return {}
    first = non_comment[0].lstrip()
    if first.startswith("- "):
        return _parse_list_of_dicts(stripped)
    return _parse_mapping_block(stripped)


def _parse_list_of_dicts(lines):
    items = []
    current = None
    for ln in lines:
        s = ln.lstrip()
        if s.startswith("#"):
            continue
        if s.startswith("- "):
            if current is not None:
                items.append(current)
            current = {}
            s = s[2:]
            if ":" in s:
                k, _, v = s.partition(":")
                current[k.strip()] = _parse_scalar_or_inline(v.strip())
        else:
            if current is None:
                raise SnippetParseError(f"unexpected indent: {ln!r}")
            if ":" not in s:
                raise SnippetParseError(f"malformed list item line: {ln!r}")
            k, _, v = s.partition(":")
            current[k.strip()] = _parse_scalar_or_inline(v.strip())
    if current is not None:
        items.append(current)
    return items


def _parse_mapping_block(lines):
    out = {}
    for ln in lines:
        s = ln.strip()
        if s.startswith("#"):
            continue
        if ":" not in s:
            raise SnippetParseError(f"malformed mapping line: {ln!r}")
        k, _, v = s.partition(":")
        out[k.strip()] = _parse_scalar_or_inline(v.strip())
    return out


def _parse_scalar_or_inline(text):
    if text == "":
        return None
    # JSON-shaped values (quoted strings, lists, objects) handle their own escaping
    # and don't get comment-stripped — `json.loads` rejects trailing garbage.
    if text.startswith("[") or text.startswith("{") or text.startswith('"'):
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            # Surface as SnippetParseError so write/maintenance paths (add,
            # update --file, doctor) that only catch SnippetParseError around
            # parse_snippet_file produce a clean envelope/finding instead of a
            # raw traceback. Common cause: a trailing YAML comment after a
            # JSON-shaped value.
            raise SnippetParseError(
                f"invalid JSON value in frontmatter: {text!r} ({e})"
            )
    # For bare scalars, strip a trailing `# comment` if present.
    if "#" in text:
        # Naive: anything after the first ` #` is a comment.
        # We require space-hash-space or hash-at-EOL so identifiers containing
        # `#` (none in practice) wouldn't be split. Simplest correct rule:
        # split on ` #` (space + hash), keep the left side stripped.
        text = text.split(" #", 1)[0].rstrip()
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _validate(snip):
    for required in ("id", "summary", "safety", "args", "example"):
        if required not in snip:
            raise SnippetParseError(f"frontmatter missing required field: {required}")
    if not ID_RE.match(snip["id"]):
        raise SnippetParseError(f"invalid id: {snip['id']!r}")
    if snip["safety"] not in VALID_SAFETY:
        raise SnippetParseError(
            f"unknown safety class: {snip['safety']!r} "
            f"(must be one of {sorted(VALID_SAFETY)})"
        )
    if not isinstance(snip["args"], list):
        raise SnippetParseError("args must be a list")
    seen_names = set()
    for spec in snip["args"]:
        if not isinstance(spec, dict):
            raise SnippetParseError(f"each arg must be a mapping, got {spec!r}")
        for k in ("name", "type"):
            if k not in spec:
                raise SnippetParseError(f"arg missing {k}: {spec!r}")
        if spec["type"] not in VALID_TYPES:
            raise SnippetParseError(
                f"arg {spec['name']!r}: unknown type {spec['type']!r}"
            )
        if spec["name"] in seen_names:
            raise SnippetParseError(f"duplicate arg name: {spec['name']!r}")
        seen_names.add(spec["name"])
    if not isinstance(snip["example"], dict):
        raise SnippetParseError("example must be a mapping")
    if snip.get("expected") is not None and not isinstance(snip["expected"], str):
        raise SnippetParseError(
            "expected must be a string — it is compared against the textual "
            "REPL result (the ToString of Run's return value); have Run "
            "return a formatted string for structured assertions"
        )
    for spec in snip["args"]:
        if "default" not in spec and spec["name"] not in snip["example"]:
            raise SnippetParseError(
                f"example missing required arg: {spec['name']!r}"
            )


def parse_snippet_file(text):
    """Parse a snippet markdown file's full contents.

    Returns a dict with keys: id, summary, safety, args, example,
    expected (optional), body. Raises SnippetParseError on any issue.
    """
    # Normalize line endings — snippet files saved on Windows / by git with
    # core.autocrlf=true contain \r\n; the regexes are written against \n.
    text = text.replace("\r\n", "\n")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise SnippetParseError("missing frontmatter (--- ... ---) block")
    fm_text, after = m.group(1), m.group(2)
    snip = _parse_yaml_subset(fm_text)
    _validate(snip)
    code_match = _CODE_BLOCK_RE.search(after)
    if not code_match:
        raise SnippetParseError("missing ```csharp code block")
    body = code_match.group(1).strip("\n")
    # Strip single-line comments before checking for Run declaration
    # to avoid matching `// static T Run(...)` commented-out signatures.
    body_no_line_comments = re.sub(r"//[^\n]*", "", body)
    if not _RUN_METHOD_RE.search(body_no_line_comments):
        raise SnippetParseError("snippet body must declare a `static Run(...)` method")
    snip["body"] = body
    return snip


from cli.snippets import DATA_DIR_NAME

SNIPPETS_SUBDIR = "snippets~"


def snippets_dir(project_root):
    return Path(project_root) / DATA_DIR_NAME / SNIPPETS_SUBDIR


def snippet_path(project_root, snippet_id):
    if not ID_RE.match(snippet_id):
        raise ValueError(f"invalid snippet id: {snippet_id!r}")
    return snippets_dir(project_root) / f"{snippet_id}.md"


def write_snippet_file(project_root, snippet_id, text):
    p = snippet_path(project_root, snippet_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def read_snippet_file(project_root, snippet_id):
    p = snippet_path(project_root, snippet_id)
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8-sig")


def list_snippet_ids(project_root):
    d = snippets_dir(project_root)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.md"))
