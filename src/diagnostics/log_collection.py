"""Bounded, record-oriented diagnostic log selection with source references."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

MAX_SCAN_BYTES = 32 * 1024 * 1024
MAX_SOURCE_FILES = 8
MAX_DIGEST_BYTES = 64 * 1024
LOOKBACK_SECONDS = 24 * 3600
HEADER = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d[,.]\d+) (DEBUG|INFO|WARNING|ERROR|CRITICAL) "
)


def timestamp(value):
    return datetime.fromisoformat(value.replace(",", ".")).timestamp()


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def redact_tree(value, redactor):
    if isinstance(value, dict):
        return {
            redactor.redact(key): (
                "<REDACTED>"
                if "<REDACTED>" in redactor.redact(f'{key}="diagnostic_value"')
                else redact_tree(item, redactor)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_tree(item, redactor) for item in value]
    return redactor.redact(value) if isinstance(value, str) else value


def sources_for(root: Path):
    candidates = [
        root / "logs" / name for name in ("ok-script.log", "ok-bd2.log", "ok-bd2_error.log")
    ]
    try:
        from ok.util import logger

        active = getattr(getattr(logger, "_file_handler", None), "baseFilename", None)
        if active:
            candidates.insert(0, Path(active))
    except ImportError:
        pass
    paths = set()
    for active in candidates:
        if active.is_file():
            paths.add(active.resolve())
        if active.parent.is_dir():
            family = re.compile(
                re.escape(active.stem) + r"(?:\.\d{4}-\d\d-\d\d\.log|\.log\.\d{4}-\d\d-\d\d)$"
            )
            for path in active.parent.glob(active.stem + ".*"):
                if path.is_file() and family.fullmatch(path.name):
                    paths.add(path.resolve())
    return sorted(paths, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)


def parse_records(raw: bytes, source: str, offset=0):
    """Offsets are original UTF-8 bytes; line numbers are relative when tail-scanning."""
    records = []
    current = None
    cursor = offset
    for line_number, line in enumerate(raw.splitlines(keepends=True), 1):
        text = line.decode("utf-8", errors="replace")
        match = HEADER.match(text)
        if match or current is None:
            if current:
                records.append(current)
            try:
                moment = timestamp(match[1]) if match else None
            except ValueError:
                moment = None
            current = {
                "source": source,
                "start": cursor,
                "end": cursor,
                "line": line_number,
                "last_line": line_number,
                "line_base_byte": offset,
                "time": moment,
                "level": match[2] if match else "UNKNOWN",
                "text": "",
            }
        current["text"] += text
        current["end"] = cursor + len(line)
        current["last_line"] = line_number
        cursor += len(line)
    if current:
        records.append(current)
    return records


def scan(paths, cutoff, *, scan_bytes=MAX_SCAN_BYTES, file_limit=MAX_SOURCE_FILES):
    records, coverage, omissions = [], [], []
    if len(paths) > file_limit:
        omissions.append(f"source_file_limit:{len(paths) - file_limit}")
    selected = paths[:file_limit]
    remaining = scan_bytes
    for index, path in enumerate(selected):
        source = f"{index + 1}:{path.name}"
        try:
            with path.open("rb") as stream:
                size = stream.seek(0, 2)
                # Fair allocation keeps a large active file from starving rotations.
                allowance = remaining // (len(selected) - index)
                start = max(0, size - allowance)
                stream.seek(start)
                raw = stream.read(allowance)
            remaining -= len(raw)
            if start:
                omissions.append(f"scan_byte_limit:{source}:before_byte={start}")
                # Discard a potentially incomplete first multiline record.
                heads = list(
                    re.finditer(
                        rb"(?m)^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d[,.]\d+ "
                        rb"(?:DEBUG|INFO|WARNING|ERROR|CRITICAL) ",
                        raw,
                    )
                )
                skip = heads[0].start() if heads else len(raw)
                start += skip
                raw = raw[skip:]
            parsed = parse_records(raw, source, start)
            kept = [
                r
                for r in parsed
                if r["time"] is None or cutoff - LOOKBACK_SECONDS <= r["time"] <= cutoff
            ]
            times = [r["time"] for r in kept if r["time"] is not None]
            coverage.append(
                {
                    "source": source,
                    "file": path.name,
                    "size": size,
                    "read_start": start,
                    "read_end": start + len(raw),
                    "line_numbers": "absolute" if start == 0 else "relative_to_read_start",
                    "first_time": min(times) if times else None,
                    "last_time": max(times) if times else None,
                    "records": len(kept),
                    "outside_time_window": len(parsed) - len(kept),
                }
            )
            if any(r["time"] is None for r in kept):
                omissions.append(f"unparsed_time:{source}")
            records.extend(kept)
        except OSError as exc:
            omissions.append(f"source_unreadable:{source}:{type(exc).__name__}")
    records.sort(key=lambda r: (r["time"] or 0, r["source"], r["start"]))
    return records, coverage, omissions


def collect(
    paths, cutoff, redactor, *, budget=4 * 1024 * 1024, scan_bytes=MAX_SCAN_BYTES, anchors=()
):
    records, coverage, omissions = scan(paths, cutoff, scan_bytes=scan_bytes)
    candidates = [
        i
        for i, r in enumerate(records)
        if r["time"] is not None and r["level"] in ("WARNING", "ERROR", "CRITICAL")
    ]
    groups = []
    for index in candidates:
        moment = records[index]["time"]
        if groups and moment - groups[-1]["last"] <= 40 and moment - groups[-1]["first"] <= 120:
            groups[-1]["indices"].append(index)
            groups[-1]["last"] = moment
        else:
            groups.append({"first": moment, "last": moment, "indices": [index]})
    omitted_groups = max(0, len(groups) - 5)
    if omitted_groups:
        omissions.append(f"candidate_group_limit:{omitted_groups}")
    groups = groups[-5:]
    # Explicit runtime failures take precedence over severity-only candidates.
    for anchor in anchors[-5:]:
        moment = anchor["time"]
        matched = next((g for g in groups if g["first"] - 30 <= moment <= g["last"] + 10), None)
        if matched is None:
            groups.append({"first": moment, "last": moment, "indices": [], "runtime": anchor})
        else:
            matched["runtime"] = anchor
    groups = groups[-5:]
    digest = {
        "version": 1,
        "cutoff": cutoff,
        "sources": coverage,
        "record_count": len(records),
        "candidate_groups_omitted": omitted_groups,
        "incidents": [],
        "omissions": omissions,
        "limits": {
            "scan_bytes": scan_bytes,
            "source_files": MAX_SOURCE_FILES,
            "lookback_seconds": LOOKBACK_SECONDS,
            "output_bytes": budget,
        },
        "minute_columns": ["epoch_minute", "level", "records", "original_bytes"],
    }
    minute_counts = Counter(
        (int(r["time"] // 60), r["level"]) for r in records if r["time"] is not None
    )
    minute_bytes = Counter()
    for record in records:
        if record["time"] is not None:
            minute_bytes[int(record["time"] // 60), record["level"]] += (
                record["end"] - record["start"]
            )
    digest["minutes"] = [
        [minute, level, count, minute_bytes[minute, level]]
        for (minute, level), count in sorted(minute_counts.items())
    ]
    repeats = Counter(r["text"].split(" ", 3)[-1].rstrip() for r in records)
    digest["repeats"] = [
        {"message": redactor.redact(message[:1000]), "count": count}
        for message, count in repeats.most_common(20)
        if count > 1
    ]
    for item, (message, count) in zip(
        digest["repeats"], ((m, n) for m, n in repeats.most_common(20) if n > 1)
    ):
        matches = [r for r in records if r["text"].split(" ", 3)[-1].rstrip() == message]
        item["first_time"] = matches[0]["time"]
        item["last_time"] = matches[-1]["time"]
    digest = redact_tree(digest, redactor)
    omissions = digest["omissions"]
    reserve = min(MAX_DIGEST_BYTES, budget // 4)
    digest_limit = reserve - 4096
    incident_budget = (budget - reserve) // 2
    outputs = {"incidents.log": [], "recent.log": []}
    line_counts = {name: 0 for name in outputs}
    used, refs = set(), {}
    skipped = 0

    def emit(index, target, remaining):
        nonlocal skipped
        if index in used:
            return remaining
        record = records[index]
        heading = (
            f"===== {record['source']} lines {record['line']}-{record['last_line']} "
            f"base_byte={record['line_base_byte']} bytes={record['start']}-{record['end']} =====\n"
        )
        text = redactor.redact(heading + record["text"]).rstrip() + "\n"
        size = len(text.encode("utf-8"))
        if size > remaining:
            skipped += 1
            return remaining
        start_line = line_counts[target] + 1
        line_counts[target] += text.count("\n")
        refs[index] = {
            "file": target,
            "line": start_line,
            "last_line": line_counts[target],
            "source": record["source"],
            "source_bytes": [record["start"], record["end"]],
        }
        outputs[target].append((index, text))
        used.add(index)
        return remaining - size

    for group in reversed(groups):
        indices = [
            i
            for i, r in enumerate(records)
            if r["time"] is not None and group["first"] - 30 <= r["time"] <= group["last"] + 10
        ]
        group["context_indices"] = indices
        allowance = incident_budget // max(1, len(groups))
        # Keep first failure and terminal abort before spending space on context.
        essential = group["indices"][:1] + group["indices"][-1:]
        ordered = list(dict.fromkeys(essential + indices))
        for index in ordered:
            allowance = emit(index, "incidents.log", allowance)
        digest["incidents"].append(
            {
                "first_time": group["first"],
                "last_time": group["last"],
                "classification": "runtime_failure" if "runtime" in group else "candidate_only",
                "event_count": len(group["indices"]),
                "context_records": len(indices),
                "retained_records": sum(i in used for i in indices),
                "first_event": refs.get(essential[0]) if essential else None,
                "last_event": refs.get(essential[-1]) if essential else None,
                "context_first": next((refs[i] for i in indices if i in refs), None),
                "context_last": next((refs[i] for i in reversed(indices) if i in refs), None),
                "context_start_missing": not any(
                    r["time"] is not None and r["time"] <= group["first"] - 30 for r in records
                ),
            }
        )
    spent = sum(len(t.encode("utf-8")) for _, t in outputs["incidents.log"])
    remaining = budget - reserve - spent
    for index in reversed(range(len(records))):
        remaining = emit(index, "recent.log", remaining)
    for entry, group in zip(digest["incidents"], reversed(groups)):
        essential = group["indices"]
        indices = group["context_indices"]
        entry.update(
            first_event=refs.get(essential[0]) if essential else None,
            last_event=refs.get(essential[-1]) if essential else None,
            context_first=next((refs[i] for i in indices if i in refs), None),
            context_last=next((refs[i] for i in reversed(indices) if i in refs), None),
            retained_records=sum(i in refs for i in indices),
        )
    digest["retained_records"] = len(used)
    digest["omitted_records"] = len(records) - len(used)
    if skipped:
        omissions.append(f"output_budget_whole_records_omitted:{len(records) - len(used)}")
    digest["ordering"] = "chronological; incidents and recent contain disjoint source records"
    for name, chunks in outputs.items():
        chunks.sort(key=lambda pair: pair[0])
        line = 1
        for index, text in chunks:
            refs[index].update(line=line, last_line=line + text.count("\n") - 1)
            line += text.count("\n")
    while len(encode_json(digest)) > digest_limit and digest["minutes"]:
        digest["minutes"].pop(0)
        digest["minute_statistics_truncated"] = True
    while len(encode_json(digest)) > digest_limit and digest["repeats"]:
        digest["repeats"].pop()
        digest["repeat_statistics_truncated"] = True
    files = {name: "".join(text for _, text in chunks) for name, chunks in outputs.items()}
    files["recent-digest.json"] = encode_json(digest).decode("utf-8")
    return {"files": files, "sources": [c["file"] for c in coverage], "omissions": omissions}
