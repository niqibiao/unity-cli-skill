import unittest

from cli.snippets.render import render_literal


class RenderLiteralTests(unittest.TestCase):
    def test_string_basic(self):
        self.assertEqual(render_literal("string", "hello"), '"hello"')

    def test_string_escapes_quotes(self):
        self.assertEqual(render_literal("string", 'he said "hi"'),
                         '"he said \\"hi\\""')

    def test_string_escapes_newline(self):
        self.assertEqual(render_literal("string", "a\nb"), '"a\\nb"')

    def test_int(self):
        self.assertEqual(render_literal("int", 42), "42")

    def test_float_with_suffix(self):
        self.assertEqual(render_literal("float", 3.14), "3.14f")

    def test_float_integer_value(self):
        self.assertEqual(render_literal("float", 5), "5f")

    def test_bool_true(self):
        self.assertEqual(render_literal("bool", True), "true")

    def test_bool_false(self):
        self.assertEqual(render_literal("bool", False), "false")

    def test_vector2(self):
        self.assertEqual(render_literal("vector2", [1, 2]),
                         "new UnityEngine.Vector2(1f, 2f)")

    def test_vector3(self):
        self.assertEqual(render_literal("vector3", [1.5, 2, 3]),
                         "new UnityEngine.Vector3(1.5f, 2f, 3f)")

    def test_vector4(self):
        self.assertEqual(render_literal("vector4", [1, 2, 3, 4]),
                         "new UnityEngine.Vector4(1f, 2f, 3f, 4f)")

    def test_color_rgb_defaults_alpha(self):
        self.assertEqual(render_literal("color", [1, 0, 0]),
                         "new UnityEngine.Color(1f, 0f, 0f, 1f)")

    def test_color_rgba(self):
        self.assertEqual(render_literal("color", [1, 0, 0, 0.5]),
                         "new UnityEngine.Color(1f, 0f, 0f, 0.5f)")

    def test_string_array(self):
        self.assertEqual(render_literal("string[]", ["a", "b"]),
                         'new string[] { "a", "b" }')

    def test_int_array(self):
        self.assertEqual(render_literal("int[]", [1, 2, 3]),
                         "new int[] { 1, 2, 3 }")

    def test_float_array(self):
        self.assertEqual(render_literal("float[]", [1.0, 2.5]),
                         "new float[] { 1f, 2.5f }")

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            render_literal("expr", "Camera.main")

    def test_vector_wrong_arity_raises(self):
        with self.assertRaises(ValueError):
            render_literal("vector3", [1, 2])

    def test_float_array_rejects_bool(self):
        with self.assertRaises(ValueError):
            render_literal("float[]", [True])

    def test_empty_string_array(self):
        self.assertEqual(render_literal("string[]", []), "new string[] { }")

    def test_empty_int_array(self):
        self.assertEqual(render_literal("int[]", []), "new int[] { }")


if __name__ == "__main__":
    unittest.main()
