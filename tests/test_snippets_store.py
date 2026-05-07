import unittest

from cli.snippets.store import parse_snippet_file, SnippetParseError


SAMPLE = '''---
id: scene.find_active_in_layer
summary: Find active GameObjects in a specific layer
safety: read-only
args:
  - name: layerName
    type: string
example:
  layerName: "Default"
---

```csharp
using System.Linq;

static List<string> Run(string layerName) {
    return UnityEngine.Object.FindObjectsOfType<GameObject>()
        .Where(g => LayerMask.LayerToName(g.layer) == layerName)
        .Select(g => g.name).ToList();
}
```
'''


class ParseSnippetTests(unittest.TestCase):
    def test_parses_valid_snippet(self):
        snip = parse_snippet_file(SAMPLE)
        self.assertEqual(snip["id"], "scene.find_active_in_layer")
        self.assertEqual(snip["safety"], "read-only")
        self.assertEqual(len(snip["args"]), 1)
        self.assertEqual(snip["args"][0]["name"], "layerName")
        self.assertEqual(snip["args"][0]["type"], "string")
        self.assertEqual(snip["example"], {"layerName": "Default"})
        self.assertIn("static List<string> Run(string layerName)", snip["body"])

    def test_optional_arg_with_default(self):
        text = SAMPLE.replace(
            "  - name: layerName\n    type: string\n",
            "  - name: layerName\n    type: string\n"
            "  - name: limit\n    type: int\n    default: 10\n",
        )
        snip = parse_snippet_file(text)
        self.assertEqual(len(snip["args"]), 2)
        self.assertEqual(snip["args"][1]["default"], 10)

    def test_expected_field_optional(self):
        text = SAMPLE.replace(
            'example:\n  layerName: "Default"\n',
            'example:\n  layerName: "Default"\nexpected: ["A", "B"]\n',
        )
        snip = parse_snippet_file(text)
        self.assertEqual(snip["expected"], ["A", "B"])

    def test_missing_id_raises(self):
        text = SAMPLE.replace("id: scene.find_active_in_layer\n", "")
        with self.assertRaises(SnippetParseError):
            parse_snippet_file(text)

    def test_bad_id_raises(self):
        text = SAMPLE.replace(
            "id: scene.find_active_in_layer", "id: BadID")
        with self.assertRaises(SnippetParseError):
            parse_snippet_file(text)

    def test_unknown_safety_raises(self):
        text = SAMPLE.replace("safety: read-only", "safety: maybe")
        with self.assertRaises(SnippetParseError):
            parse_snippet_file(text)

    def test_missing_csharp_block_raises(self):
        text = SAMPLE.replace("```csharp", "```python")
        with self.assertRaises(SnippetParseError):
            parse_snippet_file(text)

    def test_missing_run_method_raises(self):
        text = SAMPLE.replace("static List<string> Run", "static List<string> Other")
        with self.assertRaises(SnippetParseError):
            parse_snippet_file(text)

    def test_example_missing_required_arg_raises(self):
        text = SAMPLE.replace('example:\n  layerName: "Default"\n', "example: {}\n")
        with self.assertRaises(SnippetParseError):
            parse_snippet_file(text)

    def test_crlf_line_endings_parse(self):
        crlf = SAMPLE.replace("\n", "\r\n")
        snip = parse_snippet_file(crlf)
        self.assertEqual(snip["id"], "scene.find_active_in_layer")

    def test_comment_in_args_block_is_skipped(self):
        text = SAMPLE.replace(
            "args:\n  - name: layerName\n    type: string\n",
            "args:\n  # the layer to filter by\n  - name: layerName\n    type: string\n",
        )
        snip = parse_snippet_file(text)
        self.assertEqual(len(snip["args"]), 1)
        self.assertEqual(snip["args"][0]["name"], "layerName")

    def test_inline_comment_stripped_from_scalar(self):
        text = SAMPLE.replace(
            "summary: Find active GameObjects in a specific layer\n",
            "summary: Find active GameObjects # was: GOs\n",
        )
        snip = parse_snippet_file(text)
        self.assertEqual(snip["summary"], "Find active GameObjects")

    def test_commented_out_run_is_rejected(self):
        body = (
            "// static int Run() { return 1; }\n"
            "static int Other() { return 2; }"
        )
        text = SAMPLE.replace(
            "static List<string> Run(string layerName) {\n"
            "    return UnityEngine.Object.FindObjectsOfType<GameObject>()\n"
            "        .Where(g => LayerMask.LayerToName(g.layer) == layerName)\n"
            "        .Select(g => g.name).ToList();\n"
            "}",
            body,
        )
        with self.assertRaises(SnippetParseError):
            parse_snippet_file(text)


import tempfile
from pathlib import Path

from cli.snippets.store import (
    snippets_dir, snippet_path,
    write_snippet_file, read_snippet_file, list_snippet_ids,
)


class StorageIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_snippets_dir_uses_tilde(self):
        d = snippets_dir(self.root)
        self.assertTrue(d.name.endswith("snippets~"))
        self.assertEqual(d.parent.name, ".unity-cli")

    def test_snippet_path_for_id(self):
        p = snippet_path(self.root, "scene.find_active_in_layer")
        self.assertEqual(p.name, "scene.find_active_in_layer.md")
        self.assertEqual(p.parent, snippets_dir(self.root))

    def test_write_then_read(self):
        write_snippet_file(self.root, "scene.find_active_in_layer", SAMPLE)
        text = read_snippet_file(self.root, "scene.find_active_in_layer")
        self.assertEqual(text, SAMPLE)

    def test_read_missing_returns_none(self):
        self.assertIsNone(read_snippet_file(self.root, "no.such.snippet"))

    def test_list_ids_alphabetical(self):
        write_snippet_file(self.root, "b.x", SAMPLE.replace("scene.find_active_in_layer", "b.x"))
        write_snippet_file(self.root, "a.y", SAMPLE.replace("scene.find_active_in_layer", "a.y"))
        ids = list_snippet_ids(self.root)
        self.assertEqual(ids, ["a.y", "b.x"])

    def test_list_ids_empty_when_dir_missing(self):
        self.assertEqual(list_snippet_ids(self.root), [])


if __name__ == "__main__":
    unittest.main()
