"""Secret scrubber + fingerprint utility (v2 plan §5 Stage 8).

The QlikView estate routinely contains password-bearing connection strings,
cloud account keys, OAuth bearer tokens, and PEM blocks pasted directly into
``LIB CONNECT TO`` literals or ``$(vConnString)`` variables. Anything that
ships to Neo4j, the diagnostics JSON, or the logs MUST run through
``scrub()`` first.

Rules:

- Never persist secret material. We mask in-place and replace with
  ``***REDACTED***``.
- For change-detection, callers may compute ``fingerprint(secret, salt)`` —
  a salted SHA-256 truncated to 32 chars. Comparing fingerprints across
  runs detects rotated secrets without ever storing the plaintext.
- For real credential lookup, callers should resolve a ``secret_ref`` (a
  vault path like ``vault://kv/qlik/conn/<name>``) at use-time. The vault
  itself is out of scope here.

The CI gate in ``tests/integration/test_secret_leak.py`` greps every JSON
output for known secret-shaped patterns and fails the build if any are
found. Add new patterns here as the corpus grows.
"""
from __future__ import annotations

import hashlib
import re

from .models import Diagnostic

REDACTED = "***REDACTED***"


# Regex patterns matching secret-bearing tokens in connection strings,
# YAML/JSON, or free script text. Each one captures the secret in group 1
# (or group 2 for prefixed forms) so we can fingerprint it before masking.
#
# Order matters — more specific patterns first so generic ``Password=`` doesn't
# eat a more specific provider key.
_PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    # (code, pattern, secret-group-index)
    ("QV-SECRET-PEM", re.compile(
        r"(-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+PRIVATE KEY-----)",
        re.MULTILINE,
    ), 1),
    ("QV-SECRET-AWS-AKID", re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), 1),
    ("QV-SECRET-AWS-SECRET", re.compile(
        r"(?i)(?:aws[_-]?secret[_-]?access[_-]?key)\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?",
    ), 1),
    ("QV-SECRET-BEARER", re.compile(
        r"(?i)\b(?:bearer)\s+([A-Za-z0-9._\-+/=]{16,})",
    ), 1),
    ("QV-SECRET-AZURE-SAS", re.compile(
        r"(?i)(SharedAccessSignature|sig)=([A-Za-z0-9%+/=._\-]{16,})",
    ), 2),
    ("QV-SECRET-AZURE-ACCKEY", re.compile(
        r"(?i)AccountKey=([A-Za-z0-9+/=]{32,})",
    ), 1),
    ("QV-SECRET-SNOWFLAKE-OAUTH", re.compile(
        r"(?i)(?:oauth[_-]?token|access[_-]?token)\s*[=:]\s*['\"]?([A-Za-z0-9._\-+/=]{16,})['\"]?",
    ), 1),
    # Generic password / pwd in connection strings. Must be last so the
    # provider-specific patterns above get first crack.
    ("QV-SECRET-PASSWORD", re.compile(
        r"(?i)(?:password|pwd)\s*=\s*([^;\s\"']+)",
    ), 1),
]


def scrub(text: str, *, artifact: str = "") -> tuple[str, list[Diagnostic]]:
    """Mask every secret-shaped token in ``text``. Returns the scrubbed text
    plus a list of structured ``Diagnostic`` records (level=info) noting
    each redaction — never the secret itself, only its position + code.

    Idempotent: scrubbing an already-scrubbed string returns it unchanged.
    """
    diagnostics: list[Diagnostic] = []
    if not text:
        return text, diagnostics
    out = text
    for code, pat, group_idx in _PATTERNS:
        def _repl(m: re.Match[str], _code: str = code) -> str:
            secret = m.group(group_idx)
            if not secret or secret == REDACTED:
                return m.group(0)
            full = m.group(0)
            # Replace just the secret group, preserving the surrounding key
            # (so debugging "what kind of secret was here" stays possible).
            replaced = full.replace(secret, REDACTED)
            diagnostics.append(Diagnostic(
                level="info",
                code=_code,
                message=f"Redacted secret-shaped token ({_code})",
                artifact=artifact,
                line=None,
            ))
            return replaced

        out = pat.sub(_repl, out)
    return out, diagnostics


def fingerprint(secret: str, salt: bytes) -> str:
    """Return a salted SHA-256 of ``secret`` truncated to 32 chars.

    Used by ``DataConnection.secret_fingerprint`` so the graph can detect
    secret rotation across runs WITHOUT ever storing the plaintext. The
    caller supplies the salt (typically from environment, hashed at boot)
    so fingerprints from different deployments don't collide.
    """
    if not secret:
        return ""
    h = hashlib.sha256()
    h.update(salt)
    h.update(secret.encode("utf-8"))
    return h.hexdigest()[:32]


def looks_like_secret(text: str) -> bool:
    """Return True if any of the secret-shaped patterns match AND the
    matched secret is not the redacted-marker. Used by the CI secret-leak
    test to scan parser output for **unredacted** material."""
    if not text:
        return False
    for _code, pat, group_idx in _PATTERNS:
        for m in pat.finditer(text):
            captured = m.group(group_idx)
            if captured and captured != REDACTED:
                return True
    return False
