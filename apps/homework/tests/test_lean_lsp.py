"""Unit tests for the live-Lean LSP helpers (apps/homework/lean_lsp.py): framing, document
assembly, and diagnostic line remapping. All pure (read_message uses an in-memory stream).
"""

import asyncio
import json

from django.test import SimpleTestCase

from apps.homework import lean_lsp


class FramingTests(SimpleTestCase):
    def test_frame_writes_a_content_length_header(self):
        framed = lean_lsp.frame({"jsonrpc": "2.0", "method": "ping"})
        header, _, body = framed.partition(b"\r\n\r\n")
        self.assertIn(b"Content-Length:", header)
        self.assertEqual(int(header.split(b":")[1]), len(body))
        self.assertEqual(json.loads(body)["method"], "ping")

    async def test_read_message_roundtrips_a_framed_message(self):
        reader = asyncio.StreamReader()
        reader.feed_data(lean_lsp.frame({"jsonrpc": "2.0", "id": 1}))
        reader.feed_eof()
        message = await lean_lsp.read_message(reader)
        self.assertEqual(message["id"], 1)

    async def test_read_message_without_content_length_returns_none(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"X-Header: 1\r\n\r\n")
        reader.feed_eof()
        self.assertIsNone(await lean_lsp.read_message(reader))


class AssembleAndRemapTests(SimpleTestCase):
    def test_assemble_document_records_line_offsets(self):
        layout = lean_lsp.assemble_document(
            "import A\n\n", "theorem t := by\n  rfl", "\n\nend"
        )
        self.assertEqual(layout.text, "import A\n\ntheorem t := by\n  rfl\n\nend")
        self.assertEqual(
            layout.prefix_lines, 2
        )  # two newlines before the editable text
        self.assertEqual(layout.editable_lines, 2)  # editable text spans two lines

    def test_to_lean_line_shifts_by_prefix(self):
        self.assertEqual(lean_lsp.to_lean_line(3, 10), 13)

    def test_remap_keeps_editable_diagnostics_and_drops_prefix_ones(self):
        params = {
            "diagnostics": [
                {
                    "range": {
                        "start": {"line": 12, "character": 4},
                        "end": {"line": 12, "character": 9},
                    },
                    "message": "in editable",
                },
                {
                    "range": {
                        "start": {"line": 2, "character": 0},
                        "end": {"line": 2, "character": 1},
                    },
                    "message": "in prefix",
                },
            ]
        }
        result = lean_lsp.remap_diagnostics(
            params, prefix_lines=10, editable_lines=5, client_uri="file:///x.lean"
        )
        self.assertEqual(result["uri"], "file:///x.lean")
        self.assertEqual(len(result["diagnostics"]), 1)  # prefix diagnostic dropped
        kept = result["diagnostics"][0]
        self.assertEqual(kept["message"], "in editable")
        self.assertEqual(kept["range"]["start"]["line"], 2)  # 12 - 10
        self.assertEqual(kept["range"]["start"]["character"], 4)  # column unchanged

    def test_shift_range_floors_at_zero(self):
        shifted = lean_lsp._shift_range(
            {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 2}},
            10,
        )
        self.assertEqual(shifted["start"]["line"], 0)  # clamped, not negative
