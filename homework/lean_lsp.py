"""Helpers for the live Lean feedback proxy (homework/consumers.py).

The browser's editor only holds the student's *editable* block, but Lean must compile the
*assembled* document (imported source files + fixed code + editable code) -- the same ordering
as ``assemble_lean_submission_source`` in homework/views/problems.py. This module owns:

* LSP ``Content-Length`` framing (read/write JSON-RPC over a subprocess pipe),
* assembling the full Lean document from a fixed prefix + live editable text + fixed suffix,
* remapping ``publishDiagnostics`` line numbers from full-document space back to the editor's,
  dropping any diagnostics that land in the fixed prefix/suffix.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_CONTENT_LENGTH_RE = re.compile(rb"Content-Length:\s*(\d+)", re.IGNORECASE)


def frame(message: dict) -> bytes:
    """Serialize a JSON-RPC message with an LSP ``Content-Length`` header."""
    body = json.dumps(message).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n%s" % (len(body), body)


async def read_message(reader) -> dict | None:
    """Read one ``Content-Length`` framed JSON-RPC message from an asyncio StreamReader.

    Returns the parsed object, or ``None`` if the headers lacked a Content-Length.
    Raises ``asyncio.IncompleteReadError`` at EOF (caller treats that as "process gone").
    """
    headers = b""
    while not headers.endswith(b"\r\n\r\n"):
        headers += await reader.readuntil(b"\r\n")
    match = _CONTENT_LENGTH_RE.search(headers)
    if not match:
        return None
    body = await reader.readexactly(int(match.group(1)))
    return json.loads(body.decode("utf-8"))


@dataclass
class DocumentLayout:
    """The assembled Lean document plus where the editable region landed in it."""

    text: str
    prefix_lines: int  # 0-based line index where the editable region starts
    editable_lines: int  # number of lines the editable text occupies


def assemble_document(prefix: str, editable_text: str, suffix: str) -> DocumentLayout:
    """Concatenate ``prefix + editable_text + suffix`` into the full Lean document.

    The caller is responsible for separators: ``prefix`` should end with its trailing
    blank line(s) and ``suffix`` should begin with a separator. We only concatenate and
    record line offsets so diagnostics/goal positions can be translated.
    """
    return DocumentLayout(
        text=prefix + editable_text + suffix,
        prefix_lines=prefix.count("\n"),
        editable_lines=editable_text.count("\n") + 1,
    )


def to_lean_line(editor_line: int, prefix_lines: int) -> int:
    """Editor line -> full-document line (used to forward $/lean/plainGoal positions)."""
    return editor_line + prefix_lines


def remap_diagnostics(
    params: dict, prefix_lines: int, editable_lines: int, client_uri: str
) -> dict:
    """Translate a publishDiagnostics payload from full-document to editor coordinates.

    Shifts every range up by ``prefix_lines`` and keeps only diagnostics whose start line
    falls inside the editable region, so errors in the fixed prefix/suffix never leak to the
    student. Returns a new params dict carrying the client's document URI.
    """
    kept = []
    for diagnostic in params.get("diagnostics", []):
        start_line = diagnostic.get("range", {}).get("start", {}).get("line", 0)
        if 0 <= start_line - prefix_lines < editable_lines:
            kept.append(
                {
                    **diagnostic,
                    "range": _shift_range(diagnostic.get("range", {}), prefix_lines),
                }
            )
    return {"uri": client_uri, "diagnostics": kept}


def _shift_range(rng: dict, prefix_lines: int) -> dict:
    def shift(pos: dict) -> dict:
        return {
            "line": max(0, pos.get("line", 0) - prefix_lines),
            "character": pos.get("character", 0),
        }

    return {"start": shift(rng.get("start", {})), "end": shift(rng.get("end", {}))}
