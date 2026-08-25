from __future__ import annotations

import argparse
import ast
import struct
from pathlib import Path


def read_catalog(path: Path) -> dict[str, str]:
    messages: dict[str, str] = {}
    msgid: list[str] | None = None
    msgstr: list[str] | None = None
    active: list[str] | None = None

    def finish_entry() -> None:
        nonlocal msgid, msgstr, active
        if msgid is not None and msgstr is not None:
            messages["".join(msgid)] = "".join(msgstr)
        msgid = None
        msgstr = None
        active = None

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            finish_entry()
            continue
        if line.startswith("#"):
            continue
        if line.startswith("msgid_plural") or line.startswith("msgstr["):
            raise ValueError(f"plural entries are not supported: {path}:{line_number}")
        if line.startswith("msgid "):
            finish_entry()
            msgid = [_parse_quoted(line[6:], path, line_number)]
            active = msgid
            continue
        if line.startswith("msgstr "):
            if msgid is None:
                raise ValueError(f"msgstr without msgid: {path}:{line_number}")
            msgstr = [_parse_quoted(line[7:], path, line_number)]
            active = msgstr
            continue
        if line.startswith('"'):
            if active is None:
                raise ValueError(f"orphaned continuation: {path}:{line_number}")
            active.append(_parse_quoted(line, path, line_number))
            continue
        raise ValueError(f"unsupported PO syntax: {path}:{line_number}")

    finish_entry()
    return messages


def _parse_quoted(value: str, path: Path, line_number: int) -> str:
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"invalid PO string: {path}:{line_number}") from exc
    if not isinstance(parsed, str):
        raise ValueError(f"PO value is not a string: {path}:{line_number}")
    return parsed


def compile_catalog(messages: dict[str, str]) -> bytes:
    encoded = sorted(
        (msgid.encode("utf-8"), msgstr.encode("utf-8"))
        for msgid, msgstr in messages.items()
    )
    count = len(encoded)
    original_table_offset = 28
    translated_table_offset = original_table_offset + count * 8
    string_offset = translated_table_offset + count * 8

    original_table = bytearray()
    translated_table = bytearray()
    original_data = bytearray()
    translated_data = bytearray()

    for msgid, _ in encoded:
        original_table.extend(struct.pack("<II", len(msgid), string_offset + len(original_data)))
        original_data.extend(msgid + b"\0")

    translated_data_offset = string_offset + len(original_data)
    for _, msgstr in encoded:
        translated_table.extend(
            struct.pack("<II", len(msgstr), translated_data_offset + len(translated_data))
        )
        translated_data.extend(msgstr + b"\0")

    header = struct.pack(
        "<7I",
        0x950412DE,
        0,
        count,
        original_table_offset,
        translated_table_offset,
        0,
        0,
    )
    return bytes(header + original_table + translated_table + original_data + translated_data)


def compile_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(compile_catalog(read_catalog(source)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile a singular gettext PO catalog.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    compile_file(args.source, args.destination)


if __name__ == "__main__":
    main()
