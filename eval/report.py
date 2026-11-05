"""Summarizes a batch of evaluation runs into tables, printed and saved as report.md in the batch folder.
Usage: python3 report.py [batch]   (default: the latest batch)"""
import json
import sys
from pathlib import Path

from run import ARMS, RUNS, load_suite

LABEL = {"none": "No memory", "notes": "CLAUDE.md notes", "mnemolith": "Mnemolith"}


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def fmt(value, digits=1):
    return "–" if value is None else f"{value:,.{digits}f}"


def main():
    batches = sorted(p for p in RUNS.glob("*") if p.is_dir())
    if not batches:
        sys.exit("No batches yet. Run python3 run.py first.")
    batch = RUNS / sys.argv[1] if len(sys.argv) > 1 else batches[-1]
    suite = load_suite(json.loads((batch / "batch.json").read_text()).get("suite", "v1"))
    TASKS = suite.TASKS
    rows = [json.loads(line) for line in (batch / "results.jsonl").read_text().splitlines() if line.strip()]

    out = [f"# Evaluation {batch.name}", "", f"{len(rows)} runs.", ""]
    out += ["| Setup | Tasks passed | Stale-fact or two-session tasks passed | Avg turns | Avg tool calls | Avg seconds | Avg tokens |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    for arm in ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        if not mine:
            continue
        stale = [r for r in mine if r["stale_task"] or r.get("cross_session")]
        out.append(
            f"| {LABEL[arm]} | {sum(r['passed'] for r in mine)}/{len(mine)} "
            f"| {sum(r['passed'] for r in stale)}/{len(stale)} "
            f"| {fmt(mean([r['turns'] for r in mine]))} | {fmt(mean([r['tool_calls'] for r in mine]))} "
            f"| {fmt(mean([r['seconds'] for r in mine]))} | {fmt(mean([r['tokens'] for r in mine]), 0)} |")

    out += ["", "## By task", "", "| Task | " + " | ".join(LABEL[a] for a in ARMS) + " |",
            "| --- |" + " --- |" * len(ARMS)]
    for task in TASKS:
        cells = []
        for arm in ARMS:
            mine = [r for r in rows if r["task"] == task["id"] and r["arm"] == arm]
            cells.append(f"{sum(r['passed'] for r in mine)}/{len(mine)}" if mine else "–")
        kind = " (stale fact)" if task.get("stale") else " (two sessions)" if task.get("cross_session") else ""
        out.append(f"| {task['id']}{kind} | " + " | ".join(cells) + " |")

    failures = [r for r in rows if not r["passed"]]
    if failures:
        out += ["", "## Failures", ""]
        out += [f"- {r['task']} / {LABEL[r['arm']]} (run {r['rep']}): {r['note']}" for r in failures]

    text = "\n".join(out) + "\n"
    (batch / "report.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
