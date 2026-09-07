"""Domain checks with adversarial mutations, independent fixture expectations and fork isolation."""

import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build import build, business_due, local_instant
from engine import (
    Rejected,
    cohort,
    connect,
    evidence,
    policies,
    release_hold,
    timeline,
    what_if_drop,
)
from validate import validate


class WorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = build(Path(cls.directory.name))
        cls.scenarios = {
            s["id"]: s
            for s in json.loads((cls.path.parent / "oracle.json").read_text())
        }

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        with connect(self.path) as source:
            source.backup(self.db)
        self.db.execute("PRAGMA foreign_keys=ON")

    def tearDown(self):
        self.db.close()

    def sid(self, key):
        return self.scenarios[key]["student_id"]

    def payload(self):
        actor = self.db.execute(
            "SELECT id FROM staff WHERE office_id='SA' AND status='active' ORDER BY id LIMIT 1"
        ).fetchone()[0]
        return dict(
            hold_id="ready-release",
            actor_id=actor,
            expected_version=1,
            confirmed=True,
            idempotency_key="test-release-0001",
        )

    def test_world_is_coherent(self):
        self.assertEqual(validate(self.db), [])

    def test_rebuild_same_seed_and_other_seed(self):
        other = build(Path(self.directory.name) / "rebuild")
        self.assertEqual(self.path.read_bytes(), other.read_bytes())
        different = build(Path(self.directory.name) / "different", seed=17)
        with connect(different) as db:
            self.assertEqual(validate(db), [])
            self.assertEqual(
                db.execute(
                    "SELECT id FROM student ORDER BY external_ref LIMIT 1"
                ).fetchone()[0],
                self.sid("clear-control"),
            )
        self.assertNotEqual(self.path.read_bytes(), different.read_bytes())

    def test_no_future_completed_facts_and_term_deadline_timezone(self):
        self.assertEqual(local_instant("2026-09-11"), "2026-09-12T03:59:00Z")
        self.assertEqual(local_instant("2027-01-29"), "2027-01-30T04:59:00Z")
        self.assertEqual(business_due("2026-09-03", 3), "2026-09-09T21:00:00Z")
        self.db.execute(
            "UPDATE ledger SET posted_at='2027-09-01T00:00:00Z' WHERE id=(SELECT id FROM ledger LIMIT 1)"
        )
        self.assertTrue(any("after clock" in e for e in validate(self.db)))

    def test_pending_failed_and_exact_threshold(self):
        for case, balance, hold_count in [
            ("pending-is-not-paid", 100000, 1),
            ("failed-payment", 100000, 1),
            ("threshold-at", 25000, 0),
            ("threshold-over", 25001, 1),
        ]:
            d = evidence(self.db, self.sid(case))
            self.assertEqual(d["balances"][0]["balance_cents"], balance)
            self.assertEqual(
                len([h for h in d["holds"] if not h["released_at"]]), hold_count
            )
        pending = self.db.execute(
            "SELECT * FROM payment WHERE status='pending' LIMIT 1"
        ).fetchone()
        self.db.execute(
            "INSERT INTO ledger VALUES ('bad-credit',?,'2026FA','payment',-100000,'2026-09-08T14:00:00Z',NULL,?,NULL,NULL,'incorrect pending credit')",
            (pending["student_id"], pending["id"]),
        )
        self.assertTrue(
            any("unsettled payment credited" in e for e in validate(self.db))
        )

    def test_grade_correction_has_two_times(self):
        sid = self.sid("late-arriving-fact")
        before = [
            e
            for e in timeline(self.db, sid, known_at="2026-09-01T00:00:00Z")
            if e["entity_id"] == "late-grade"
        ]
        after = [e for e in timeline(self.db, sid) if e["entity_id"] == "late-grade"]
        self.assertEqual([e["to_state"] for e in before], ["F"])
        self.assertEqual([e["to_state"] for e in after], ["F", "B"])
        self.assertEqual(after[0]["effective_at"], after[1]["effective_at"])

    def test_policy_precedence_and_audience(self):
        past = policies(self.db, "Account settlement", at="2026-08-31T16:00:00Z")
        now = policies(self.db, "Account settlement")
        self.assertEqual([p["version"] for p in past], ["2026.1"])
        self.assertEqual([p["version"] for p in now], ["2026.2"])
        self.assertFalse(
            any(p["audience"] == "internal" for p in policies(self.db, role="student"))
        )
        self.assertTrue(
            any(p["audience"] == "internal" for p in policies(self.db, role="staff"))
        )
        scoped = policies(self.db, "", student_id=self.sid("clear-control"))
        self.assertTrue(any(p["applicability"] == "does_not_apply" for p in scoped))

    def test_agent_evidence_excludes_oracle_and_internal_work(self):
        d = evidence(self.db, self.sid("verification-chain"))
        self.assertNotIn("workflows", d)
        self.assertNotIn("expected", json.dumps(d))
        self.assertNotIn("forbidden", json.dumps(d))
        self.assertTrue(
            all(
                e["visibility"] == "student"
                for e in timeline(self.db, self.sid("verification-chain"))
            )
        )
        self.assertIn(
            "workflows", evidence(self.db, self.sid("verification-chain"), role="staff")
        )

    def test_drop_what_if_and_scoped_exception_do_not_write(self):
        before = self.db.total_changes
        for key, has_exception in [("f1-drop-risk", False), ("approved-rcl", True)]:
            sid = self.sid(key)
            d = evidence(self.db, sid)
            course = next(e for e in d["enrollments"] if e["code"] == "CS 101")
            result = what_if_drop(self.db, sid, course["id"])
            self.assertEqual(result["before_credits"], 12)
            self.assertEqual(result["after_credits"], 8)
            self.assertEqual(bool(result["exception_id"]), has_exception)
            self.assertFalse(result["mutated"])
        self.assertEqual(before, self.db.total_changes)
        with self.assertRaises(Rejected):
            what_if_drop(self.db, self.sid("threshold-at"), course["id"])

    def test_cohort_uses_intersection_and_correct_denominator(self):
        result = cohort(self.db)
        self.assertEqual(
            {r["id"] for r in result["members"]},
            {self.sid("pending-is-not-paid"), self.sid("stale-advice")},
        )
        self.assertGreater(result["denominator"], 1000)
        self.assertEqual(result["count"], 2)

    def test_refund_and_return_conserve_money(self):
        self.assertEqual(
            evidence(self.db, self.sid("payment-reversal"))["balances"][0][
                "balance_cents"
            ],
            0,
        )
        self.assertEqual(
            evidence(self.db, self.sid("refund-complete"))["balances"][0][
                "balance_cents"
            ],
            0,
        )
        self.assertEqual(
            evidence(self.db, self.sid("credit-balance"))["balances"][0][
                "balance_cents"
            ],
            -50000,
        )

    def test_sap_has_attempt_evidence_and_suspension_holds_aid(self):
        row = self.db.execute(
            "SELECT * FROM sap_evaluation WHERE status='suspension' AND term_id='2026SP' LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertGreaterEqual(row["gpa"], 2)
        self.assertLess(row["completion_rate"], 0.67)
        self.assertGreater(row["attempted_credits"], 0)
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM disbursement d JOIN award a ON a.id=d.award_id WHERE a.student_id=? AND d.term_id='2026FA' AND d.status='posted'",
                (row["student_id"],),
            ).fetchone()[0],
            0,
        )
        self.db.execute(
            "UPDATE sap_evaluation SET attempted_credits=attempted_credits+1 WHERE id=?",
            (row["id"],),
        )
        self.assertTrue(
            any("SAP attempted credits" in error for error in validate(self.db))
        )

    def test_registration_clearance_and_refund_debt_are_checked(self):
        sid = self.sid("clear-control")
        self.db.execute("DELETE FROM appointment WHERE student_id=?", (sid,))
        self.assertTrue(
            any("completed advising" in error for error in validate(self.db))
        )
        self.db.execute(
            "UPDATE ledger SET amount_cents=60000 WHERE id='refund-complete'"
        )
        self.assertTrue(
            any("refund creates a debt" in error for error in validate(self.db))
        )

    def test_foreign_keys_occupancy_and_checks_reject_corruption(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "UPDATE housing SET bed_id='missing' WHERE id=(SELECT id FROM housing LIMIT 1)"
            )
        beds = self.db.execute(
            "SELECT id,bed_id FROM housing WHERE status='assigned' LIMIT 2"
        ).fetchall()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "UPDATE housing SET bed_id=? WHERE id=?",
                (beds[0]["bed_id"], beds[1]["id"]),
            )
        self.db.execute(
            "UPDATE award SET offered_cents=999999999 WHERE id=(SELECT id FROM award LIMIT 1)"
        )
        self.assertTrue(any("annual award exceeds cap" in e for e in validate(self.db)))

    def test_workflow_cannot_skip_dependency(self):
        self.db.execute(
            "UPDATE workflow_step SET status='complete',evidence='wrongly completed' WHERE id='case-verification-3'"
        )
        self.assertTrue(any("prerequisites" in e for e in validate(self.db)))

    def test_action_requires_actor_confirmation_version_and_posted_funds(self):
        payload = self.payload()
        for changes in [
            {"confirmed": False},
            {"expected_version": 0},
            {"actor_id": "missing"},
            {"hold_id": "health-independent"},
            {
                "hold_id": self.db.execute(
                    "SELECT id FROM hold WHERE student_id=?",
                    (self.sid("pending-is-not-paid"),),
                ).fetchone()[0]
            },
        ]:
            with self.assertRaises(Rejected):
                release_hold(self.db, payload | changes)
        self.assertIsNone(
            self.db.execute(
                "SELECT released_at FROM hold WHERE id='ready-release'"
            ).fetchone()[0]
        )
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM action_receipt").fetchone()[0], 0
        )

    def test_action_is_atomic_idempotent_and_preserves_history(self):
        payload = self.payload()
        receipt = release_hold(self.db, payload)
        self.assertEqual(receipt, release_hold(self.db, payload))
        self.assertEqual(receipt["version"], 2)
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM action_receipt").fetchone()[0], 1
        )
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM event WHERE entity_id='ready-release' AND to_state='released'"
            ).fetchone()[0],
            1,
        )
        with self.assertRaises(Rejected):
            release_hold(self.db, payload | {"expected_version": 2})
        with connect(self.path) as source:
            self.assertIsNone(
                source.execute(
                    "SELECT released_at FROM hold WHERE id='ready-release'"
                ).fetchone()[0]
            )

    def test_concurrent_duplicate_release_has_one_effect(self):
        fork = Path(self.directory.name) / "concurrency.sqlite"
        with sqlite3.connect(fork) as dest:
            self.db.backup(dest)
        payload = self.payload()

        def run(_):
            with connect(fork, True) as db:
                return release_hold(db, payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(results[0], results[1])
        with connect(fork) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM action_receipt").fetchone()[0], 1
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
