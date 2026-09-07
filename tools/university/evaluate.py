"""Export evidence or inspect machine-checkable world outcomes; never grade prose by keyword alone."""

import argparse
import json
from pathlib import Path

from build import DEFAULT_OUTPUT
from engine import cohort, connect, evidence, timeline


def export(world, output, split, role):
    cases = json.loads((world / "oracle.json").read_text())
    selected = [c for c in cases if c["split"] == split]
    with connect(world / "university.sqlite") as db, output.open("w") as stream:
        for case in selected:
            packet = {
                "case_id": case["id"],
                "student_id": case["student_id"],
                "prompt": case["prompt"],
                "as_of": case["as_of"],
                "role": role,
                "evidence": evidence(db, case["student_id"], role),
                "timeline": timeline(db, case["student_id"], role=role),
            }
            stream.write(json.dumps(packet) + "\n")
    print(f"Exported {len(selected)} {split} packets without rubrics to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--split", choices=["development", "holdout"], default="development"
    )
    parser.add_argument("--role", choices=["student", "staff"], default="student")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cohort", action="store_true")
    args = parser.parse_args()
    if args.cohort:
        with connect(args.world / "university.sqlite") as db:
            print(json.dumps(cohort(db), indent=2))
    elif args.output:
        export(args.world, args.output, args.split, args.role)
    else:
        parser.error(
            "Use --output for JSONL evidence packets or --cohort for the saved cohort"
        )
