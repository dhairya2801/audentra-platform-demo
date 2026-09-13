"""The legacy fixture importer cannot target source or interactive databases."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from import_legacy_eval import checked


class LegacyEvaluationIsolationTests(unittest.TestCase):
    def test_requires_own_loopback_cluster_and_explicit_legacy_test_name(self):
        for url in (
            "postgresql://vv@127.0.0.1:5433/vv_enrollment_staffdb_eval",
            "postgresql://vv@127.0.0.1:55487/audentra_university_test_vnext_legacy",
            "postgresql://vv@127.0.0.1:55591/audentra_university_vnext",
            "postgresql://vv@db.example:55591/audentra_university_test_vnext_legacy",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                checked(url)

    def test_accepts_only_the_intended_separate_fixture_namespace(self):
        url = "postgresql://vv@127.0.0.1:55591/audentra_university_test_vnext_legacy_current"
        self.assertEqual(checked(url), url)
