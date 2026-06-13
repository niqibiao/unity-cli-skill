"""Snippet validation: smoke-test runs example through a code runner."""

from cli.snippets.render import render_submission


class ValidationError(Exception):
    pass


def _extract_result_text(response):
    """Pull the REPL's last-expression result out of an exec response.

    The exec endpoint serializes the result via ``ToString()`` into
    ``data.text`` — it never emits structured JSON (``resultJson`` exists
    only on the command endpoint). ``expected`` is therefore defined as a
    string compared against this text.
    """
    data = response.get("data") or {}
    text = data.get("text")
    return text if isinstance(text, str) else None


def validate_snippet(snippet, code_runner, no_validate=False):
    """Validate a parsed snippet by running its example through *code_runner*.

    *code_runner* signature: ``code_runner(code: str) -> dict`` (matches the
    ConsoleSession `exec` return shape).

    Raises ValidationError on any failure.

    For ``safety == 'mutates'`` snippets, validation is refused unless
    *no_validate* is True.
    """
    if snippet["safety"] == "mutates":
        if not no_validate:
            raise ValidationError(
                "snippet has safety=mutates and cannot be auto-validated; "
                "pass --no-validate to register it as unverified"
            )
        return  # skipped, caller marks unverified in audit

    if no_validate:
        return  # explicit skip even for read-only

    try:
        submission = render_submission(
            snippet_id=snippet["id"],
            body=snippet["body"],
            args_schema=snippet["args"],
            arg_values=snippet["example"],
        )
    except ValueError as e:
        # example/default values that don't match the declared arg types
        # surface as ValueError from the renderer; turn them into a
        # validation failure so add / update --file / doctor --revalidate
        # emit an envelope instead of a traceback.
        raise ValidationError(f"example does not match arg schema: {e}")
    if code_runner is None:
        raise ValidationError(
            "internal error: validate_snippet reached runner with code_runner=None"
        )
    response = code_runner(submission)
    if not response.get("ok") or response.get("exitCode", 0) != 0:
        msg = response.get("summary") or response.get("error") or "validation runner failed"
        raise ValidationError(f"validation failed: {msg}")

    if "expected" in snippet and snippet["expected"] is not None:
        actual = _extract_result_text(response)
        if actual is None or actual.strip() != snippet["expected"].strip():
            raise ValidationError(
                f"expected mismatch: got {actual!r}, want {snippet['expected']!r}"
            )
