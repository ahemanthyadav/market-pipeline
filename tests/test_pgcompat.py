"""pgcompat — psql -At -F, rendering used by the in-cluster ML jobs (ml1/ml3)."""
import datetime as dt
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "manifests", "ml"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ops-tools"))
import pgcompat  # noqa: E402


class Render(unittest.TestCase):
    def test_rows_like_psql_unaligned(self):
        rows = [("AAPL", dt.date(2026, 11, 12), Decimal("1.50"), None, True),
                ("MSFT", dt.date(2026, 11, 11), Decimal("2"), 3, False)]
        out = pgcompat.render(object(), rows, "SELECT 2")
        self.assertEqual(out, "AAPL,2026-11-12,1.50,,t\nMSFT,2026-11-11,2,3,f\n")

    def test_empty_select_is_empty_string(self):
        self.assertEqual(pgcompat.render(object(), [], "SELECT 0"), "")

    def test_non_select_returns_status(self):
        # ml1/ml3 count writes by parsing "INSERT 0 n"
        self.assertEqual(pgcompat.render(None, None, "INSERT 0 3").strip(), "INSERT 0 3")

    def test_json_rendered_as_json(self):
        self.assertEqual(pgcompat.fmt_value({"a": 1}), '{"a": 1}')


class Dispatch(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in ("PSQL_CMD", "PGHOST")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is not None:
                os.environ[k] = v

    def test_no_env_exits(self):
        with self.assertRaises(SystemExit):
            pgcompat.psql("SELECT 1")

    def test_workstation_path_shells_out(self):
        os.environ["PSQL_CMD"] = "echo"
        self.assertIn("-At -F,", pgcompat.psql("SELECT 1"))


class SqlQuoting(unittest.TestCase):
    """ml1/ml3 build SQL literals with _q; it must be SQL quoting, not shell quoting."""

    def test_apostrophe_doubled(self):
        import ml1
        import ml3
        for m in (ml1, ml3):
            self.assertEqual(m._q("O'Reilly"), "'O''Reilly'")


if __name__ == "__main__":
    unittest.main()
