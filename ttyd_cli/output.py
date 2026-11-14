"""Incremental filtering of terminal queries without dropping display sequences."""

import re


class TerminalOutputFilter:
    MAX_PENDING = 65536
    OSC_QUERY = re.compile(rb"\x1b\](?:1[012];\?|4;\d+;\?)(?:\x07|\x1b\\)\Z")
    CSI_QUERY = re.compile(rb"\x1b\[(?:[>=]?[0-9;]*c|>[0-9;]*q)\Z")
    CSI_FINAL = re.compile(rb"[\x40-\x7e]")

    def __init__(self):
        self.pending = b""
        self.passthrough = False
        self.allow_bell = False

    @staticmethod
    def string_end(data, start, allow_bell):
        terminator = data.find(b"\x1b\\", start)
        bell = data.find(b"\x07", start) if allow_bell else -1
        if bell >= 0 and (terminator < 0 or bell < terminator):
            return bell + 1
        return terminator + 2 if terminator >= 0 else None

    def feed(self, data):
        data = self.pending + data
        self.pending = b""
        output = []
        position = 0
        while position < len(data):
            if self.passthrough:
                end = self.string_end(data, position, self.allow_bell)
                if end is None:
                    if data.endswith(b"\x1b"):
                        output.append(data[position:-1])
                        self.pending = b"\x1b"
                    else:
                        output.append(data[position:])
                    break
                output.append(data[position:end])
                self.passthrough = False
                position = end
                continue
            escape = data.find(b"\x1b", position)
            if escape < 0:
                output.append(data[position:])
                break
            output.append(data[position:escape])
            if escape + 1 == len(data):
                self.pending = data[escape:]
                break
            kind = data[escape + 1 : escape + 2]
            if kind in (b"P", b"_", b"^", b"X"):
                output.append(data[escape : escape + 2])
                self.passthrough = True
                self.allow_bell = False
                position = escape + 2
                continue
            if kind == b"]":
                end = self.string_end(data, escape + 2, True)
            elif kind == b"[":
                match = self.CSI_FINAL.search(data, escape + 2)
                end = match.end() if match else None
            else:
                output.append(data[escape : escape + 2])
                position = escape + 2
                continue
            if end is None:
                remaining = data[escape:]
                if len(remaining) <= self.MAX_PENDING:
                    self.pending = remaining
                elif kind == b"]":
                    self.passthrough = True
                    self.allow_bell = True
                    if remaining.endswith(b"\x1b"):
                        output.append(remaining[:-1])
                        self.pending = b"\x1b"
                    else:
                        output.append(remaining)
                else:
                    output.append(remaining)
                break
            sequence = data[escape:end]
            query = self.OSC_QUERY if kind == b"]" else self.CSI_QUERY
            if not query.fullmatch(sequence):
                output.append(sequence)
            position = end
        return b"".join(output)

    def flush(self):
        pending = self.pending
        self.pending = b""
        self.passthrough = False
        return pending
