"""Streaming terminal filter regression tests."""

import unittest

from ttyd_cli.output import TerminalOutputFilter


class TestTerminalOutputFilter(unittest.TestCase):
    def test_exact_queries_are_removed(self):
        for query in (
            b"\x1b]10;?\x07",
            b"\x1b]11;?\x1b\\",
            b"\x1b]12;?\x07",
            b"\x1b]4;0;?\x07",
            b"\x1b[>0c",
            b"\x1b[c",
        ):
            with self.subTest(query=query):
                self.assertEqual(
                    TerminalOutputFilter().feed(b"before" + query + b"after"), b"beforeafter"
                )

    def test_queries_split_at_every_byte(self):
        for query in (b"\x1b]10;?\x07", b"\x1b]11;?\x1b\\", b"\x1b[>0c"):
            for split in range(1, len(query)):
                with self.subTest(query=query, split=split):
                    output_filter = TerminalOutputFilter()
                    output = output_filter.feed(b"before" + query[:split]) + output_filter.feed(
                        query[split:] + b"after"
                    )
                    self.assertEqual(output, b"beforeafter")

    def test_display_sequences_are_preserved(self):
        sequences = (
            b"\x1b]10;#ffffff\x07",
            b"\x1b]4;0;rgb:ffff/0000/0000\x1b\\",
            b"\x1bPqSIXEL\x1b\\",
            b"\x1b_Ga=T;IMAGE\x1b\\",
            b"\x1b[31mred\x1b[0m",
            b"\x1b[?1;2c",
            b"\x1b[1;2R",
        )
        for sequence in sequences:
            with self.subTest(sequence=sequence):
                self.assertEqual(TerminalOutputFilter().feed(sequence), sequence)

    def test_dcs_does_not_filter_embedded_queries(self):
        sequence = b"\x1bPtmux;\x1b\x1b]10;?\x07\x1b\\"
        output_filter = TerminalOutputFilter()
        output = b"".join(output_filter.feed(bytes([byte])) for byte in sequence)
        self.assertEqual(output, sequence)

    def test_incomplete_sequence_is_flushed(self):
        output_filter = TerminalOutputFilter()
        self.assertEqual(output_filter.feed(b"text\x1b]10;"), b"text")
        self.assertEqual(output_filter.flush(), b"\x1b]10;")

    def test_pending_buffer_is_bounded(self):
        output_filter = TerminalOutputFilter()
        sequence = b"\x1b]0;" + b"title" * 14000
        output = output_filter.feed(sequence)
        self.assertLessEqual(len(output_filter.pending), output_filter.MAX_PENDING)
        self.assertEqual(output + output_filter.feed(b"\x07after"), sequence + b"\x07after")
