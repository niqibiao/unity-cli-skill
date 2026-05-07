"""Snippet validation: smoke-test runs example through a code runner."""

import json

from cli.snippets.render import render_submission


class ValidationError(Exception):
    pass


def _extract_return_value(response):
    """Pull the REPL's last-expression result out of a session response.

    csharpconsole returns either ``data.resultJson`` (string-encoded JSON) for
    serializable values, or omits it for void / unsupported. Caller should
    handle None.
    """
    data = response.get("data") or {}
    rj = data.get("resultJson")
    if rj is None:
        return None
    if isinstance(rj, str):
        try:
            return json.loads(rj)
        except (ValueError, TypeError):
            return rj
    return rj


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

    submission = render_submission(
        snippet_id=snippet["id"],
        body=snippet["body"],
        args_schema=snippet["args"],
        arg_values=snippet["example"],
    )
    if code_runner is None:
        raise ValidationError(
            "internal error: validate_snippet reached runner with code_runner=None"
        )
    response = code_runner(submission)
    if not response.get("ok") or response.get("exitCode", 0) != 0:
        msg = response.get("summary") or response.get("error") or "validation runner failed"
        raise ValidationError(f"validation failed: {msg}")

    if "expected" in snippet and snippet["expected"] is not None:
        actual = _extract_return_value(response)
        if actual != snippet["expected"]:
            raise ValidationError(
                f"expected mismatch: got {actual!r}, want {snippet['expected']!r}"
            )
