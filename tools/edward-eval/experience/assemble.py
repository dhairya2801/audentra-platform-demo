"""Assemble a role-complete matched comparison without selecting favorable turns.

The final changes were staff-only, so reuse the frozen student run and rerun
ALL staff cases. Raw transcripts stay ignored; the manifest documents origins.
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def assemble(student_batch, staff_batch, output_batch):
    base = ROOT / "artifacts/edward-experience"
    sources = {"student": student_batch, "staff": staff_batch}
    records = []
    cases = []
    for role, batch in sources.items():
        bank = json.loads((base / batch / "cases.json").read_text())
        selected = [case for case in bank if case["role"] == role]
        ids = {case["id"] for case in selected}
        cases.extend(selected)
        records.extend(
            record
            for record in json.loads((base / batch / "transcript.json").read_text())
            if record["case"] in ids
        )
    original = json.loads((base / "baseline/transcript.json").read_text())
    keys = [(record["case"], record["turn"]) for record in records]
    assert len(keys) == len(set(keys))
    assert set(keys) == {(r["case"], r["turn"]) for r in original}
    target = base / output_batch
    target.mkdir(parents=True, exist_ok=True)
    (target / "transcript.json").write_text(json.dumps(records, indent=2))
    (target / "cases.json").write_text(json.dumps(cases, indent=2))
    manifest = {
        "batch": output_batch,
        "sourcesByRole": sources,
        "turns": len(records),
        "selection": "Every baseline case and turn, selected by actor role only; no best-of retries",
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--student-batch", required=True)
    parser.add_argument("--staff-batch", required=True)
    parser.add_argument("--output-batch", required=True)
    args = parser.parse_args()
    assemble(args.student_batch, args.staff_batch, args.output_batch)
