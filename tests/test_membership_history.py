"""Unit tests for the EV-1 membership reconstruction (no network, no DB).

    python3 tests/test_membership_history.py

mdjob and requests are stubbed BEFORE import; the parsers run against small
in-memory copies of the two Wikipedia tables (real lxml, as in CI). Proves the
interval sweep (multi-stint symbols, pre-records removals, unrecorded re-adds
never colliding on the primary key, dangling adds dropped), ticker
normalization, and main()'s refuse-to-write guards.
"""
import importlib.util
import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

try:
    import lxml.html  # noqa: F401
except ImportError as exc:
    print(f"SKIP: {exc}")
    sys.exit(0)

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "manifests/stock-screener/scripts/membership_history.py"

RUNS = []


class _Run:
    rows = 0


class _Ctx:
    def __init__(self, name):
        RUNS.append(name)
        self.run = _Run()

    def __enter__(self):
        return self.run

    def __exit__(self, *exc):
        return False


CURRENT_HTML = """
<table id="constituents">
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>GICS Sub-Industry</th>
<th>Headquarters Location</th><th>Date added</th><th>CIK</th><th>Founded</th></tr>
<tr><td>AAA</td><td>Alpha</td><td>IT</td><td>x</td><td>y</td><td>1999-05-03</td><td>1</td><td>1900</td></tr>
<tr><td>BRK.B</td><td>Berkshire</td><td>Fin</td><td>x</td><td>y</td><td>2010-02-16</td><td>2</td><td>1900</td></tr>
<tr><td>EEE</td><td>Evergreen</td><td>Ind</td><td>x</td><td>y</td><td></td><td>3</td><td>1900</td></tr>
</table>"""

CHANGES_HTML = """
<table id="changes">
<tr><th>Effective Date</th><th>Added</th><th>Removed</th><th>Reason</th><th>Refs</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td>May 3, 1999</td><td>AAA</td><td>Alpha</td><td>OLD</td><td>Oldco</td><td>r</td><td>[1]</td></tr>
<tr><td>June 1, 2001</td><td></td><td></td><td>GON</td><td>Goner</td><td>r</td><td>[2]</td></tr>
<tr><td>July 2, 2005</td><td>GON</td><td>Goner</td><td></td><td></td><td>r</td><td>[3]</td></tr>
<tr><td>August 3, 2009</td><td></td><td></td><td>GON</td><td>Goner</td><td>r</td><td>[4]</td></tr>
<tr><td>March 4, 2015</td><td>DANG</td><td>Dangler</td><td></td><td></td><td>r</td><td>[5]</td></tr>
<tr><td>April 1, 2020</td><td></td><td></td><td>TWICE</td><td>Twostints</td><td>r</td><td>[6]</td></tr>
<tr><td>May 1, 2024</td><td></td><td></td><td>TWICE</td><td>Twostints</td><td>r</td><td>[7]</td></tr>
</table>"""

MDJOB = types.ModuleType("mdjob")
MDJOB.job_run = _Ctx
MDJOB.connect = lambda: None  # replaced per-test
REQUESTS = types.ModuleType("requests")
REQUESTS.get = lambda url, headers=None, timeout=None: types.SimpleNamespace(
    content=(CURRENT_HTML if "List_of" in url else CHANGES_HTML).encode())
sys.modules["mdjob"] = MDJOB
sys.modules["requests"] = REQUESTS

spec = importlib.util.spec_from_file_location("membership_history", SCRIPT)
mh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mh)
FLOOR = mh.FLOOR


class Parsers(unittest.TestCase):
    def test_norm_maps_class_shares_to_yahoo_form(self):
        self.assertEqual(mh.norm("BRK.B"), "BRK-B")

    def test_current_members_reads_date_added_with_floor_fallback(self):
        cur = mh.current_members(mh.html.fromstring(CURRENT_HTML))
        self.assertEqual(cur["AAA"], date(1999, 5, 3))
        self.assertEqual(cur["BRK-B"], date(2010, 2, 16))
        self.assertEqual(cur["EEE"], FLOOR)   # blank Date added

    def test_change_events_skip_header_rows(self):
        events = mh.change_events(mh.html.fromstring(CHANGES_HTML))
        self.assertIn((date(1999, 5, 3), "add", "AAA"), events)
        self.assertIn((date(1999, 5, 3), "remove", "OLD"), events)
        self.assertEqual(len([e for e in events if e[2] == "GON"]), 3)


class Sweep(unittest.TestCase):
    def intervals(self):
        cur = mh.current_members(mh.html.fromstring(CURRENT_HTML))
        ev = mh.change_events(mh.html.fromstring(CHANGES_HTML))
        iv, dangling = mh.build_intervals(cur, ev)
        return {s: [(a, r) for s2, a, r in iv if s2 == s] for s in {x[0] for x in iv}}, dangling

    def test_multi_stint_symbol(self):
        iv, _ = self.intervals()
        self.assertEqual(iv["GON"], [(FLOOR, date(2001, 6, 1)),
                                     (date(2005, 7, 2), date(2009, 8, 3))])

    def test_unrecorded_re_add_never_collides_on_the_key(self):
        iv, _ = self.intervals()
        self.assertEqual(iv["TWICE"], [(FLOOR, date(2020, 4, 1)),
                                       (date(2020, 4, 2), date(2024, 5, 1))])
        added = [a for a, _r in iv["TWICE"]]
        self.assertEqual(len(added), len(set(added)))

    def test_current_members_get_one_open_interval(self):
        iv, _ = self.intervals()
        self.assertEqual(iv["AAA"], [(date(1999, 5, 3), None)])
        self.assertEqual(iv["BRK-B"], [(date(2010, 2, 16), None)])
        self.assertEqual(iv["EEE"], [(FLOOR, None)])

    def test_dangling_add_is_reported_not_written(self):
        iv, dangling = self.intervals()
        self.assertEqual(dangling, ["DANG"])
        self.assertNotIn("DANG", iv)


class MainGuards(unittest.TestCase):
    def test_small_tables_refuse_to_write(self):
        RUNS.clear()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = mh.main()   # canned tables are far below the 400/300 floors
        self.assertEqual(rc, 1)
        self.assertIn("implausibly small", buf.getvalue())
        self.assertEqual(RUNS, ["membership-history"])


if __name__ == "__main__":
    unittest.main()
