#!/usr/bin/env python3
"""Every CronJob carries the AR-1 safeguards and the house pod conventions (AR-6/7 intent:
"safeguards live in one place" — enforced here instead of a kustomize base, 2026-10-18).

    python3 tests/test_manifest_safeguards.py
"""
import glob
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def cronjobs():
    for path in sorted(glob.glob(str(ROOT / "manifests" / "**" / "*.yaml"), recursive=True)):
        with open(path) as f:
            docs = list(yaml.safe_load_all(f))
        for doc in docs:
            if isinstance(doc, dict) and doc.get("kind") == "CronJob":
                yield Path(path).relative_to(ROOT), doc


class Safeguards(unittest.TestCase):
    def test_found_all(self):
        self.assertEqual(len(list(cronjobs())), 36)  # … +research-watcher (ADR 0031) 10-27, +ml1-anomalies +ml3-volatility 10-31, +ml7b-per-symbol 11-03

    def test_each_cronjob(self):
        for path, cj in cronjobs():
            name = f"{path}:{cj['metadata']['name']}"
            spec, job = cj["spec"], cj["spec"]["jobTemplate"]["spec"]
            with self.subTest(name):
                self.assertEqual(spec.get("concurrencyPolicy"), "Forbid", name)
                self.assertIsInstance(spec.get("startingDeadlineSeconds"), int, name)
                self.assertLessEqual(spec.get("successfulJobsHistoryLimit", 99), 3, name)
                self.assertLessEqual(spec.get("failedJobsHistoryLimit", 99), 1, name)
                self.assertIsInstance(job.get("activeDeadlineSeconds"), int, name)
                self.assertIsInstance(job.get("ttlSecondsAfterFinished"), int, name)
                self.assertIn("backoffLimit", job, name)
                for c in job["template"]["spec"]["containers"]:
                    if c["image"].startswith("local/"):
                        self.assertEqual(c.get("imagePullPolicy"), "Never", name)
                    self.assertIn("limits", c.get("resources", {}), name)
                    for env in c.get("env", []):
                        sensitive = any(k in env["name"] for k in ("PASS", "TOKEN", "SECRET", "KEY"))
                        self.assertFalse(sensitive and "value" in env,
                                         f"{name}: {env['name']} must come from a Secret")


class ExplicitTimeZone(unittest.TestCase):
    """2026-11-19: a CronJob without spec.timeZone follows the control plane's clock.
    When the control plane moved from pi2 (America/Los_Angeles) to the N100 (UTC) on
    2026-11-16 every schedule moved 8 h. The price writers then ran in the Asian
    pre-open hours, where Yahoo no longer serves the previous session's daily bar:
    Indian stocks and Asian indices landed a day late, score dates split by market,
    and the scorecard recorded no Indian names for three days. The schedules are
    tuned to Pacific time (sessions.py), so each CronJob names that zone itself."""

    def test_every_cronjob_names_its_time_zone(self):
        missing = [f"{path}:{doc['metadata']['name']}" for path, doc in cronjobs()
                   if not doc["spec"].get("timeZone")]
        self.assertEqual(missing, [])

    def test_only_the_two_known_zones(self):
        zones = {doc["spec"].get("timeZone") for _path, doc in cronjobs()}
        self.assertEqual(zones, {"America/Los_Angeles", "Asia/Kolkata"})


class SchemaReapplySafety(unittest.TestCase):
    """schema-job re-applies EVERY key on EVERY run (2026-11-15 review: key 22 broke
    re-runs after key 25 widened its views; key 23 granted to a role no key created)."""

    @classmethod
    def setUpClass(cls):
        import re
        cls.re = re
        cm = yaml.safe_load(open(ROOT / "manifests" / "timescaledb" / "schema-configmap.yaml"))
        cls.keys = dict(sorted(cm["data"].items()))
        cls.job = (ROOT / "manifests" / "timescaledb" / "schema-job.yaml").read_text()

    def test_each_view_has_one_unguarded_definition(self):
        # A second CREATE OR REPLACE with fewer columns fails "cannot drop columns
        # from view" on re-run; earlier keys must bootstrap behind to_regclass().
        owners = {}
        for key, sql in self.keys.items():
            for view in self.re.findall(r"CREATE\s+OR\s+REPLACE\s+VIEW\s+([\w.]+)", sql, self.re.I):
                owners.setdefault(view, set()).add(key)
        multi = {v: sorted(k) for v, k in owners.items() if len(k) > 1}
        self.assertEqual(multi, {})

    def test_granted_roles_are_created_in_an_earlier_or_same_key(self):
        created = {}
        for key, sql in self.keys.items():
            for role in self.re.findall(r"CREATE\s+ROLE\s+(\w+)", sql, self.re.I):
                created.setdefault(role, key)
        for key, sql in self.keys.items():
            for grantees in self.re.findall(r"\bGRANT\b[^;]*?\bTO\s+([\w,\s]+?);", sql, self.re.I | self.re.S):
                for role in (r.strip() for r in grantees.split(",")):
                    with self.subTest(key=key, role=role):
                        self.assertIn(role, created, f"{key} grants to {role}, which no key creates")
                        self.assertLessEqual(created[role], key)

    def test_a_failed_key_fails_the_job(self):
        self.assertRegex(self.job, r'-f "\$f" \\\s*\n\s*\|\| \{ echo "SCHEMA-FAIL \$f"; exit 1; \}')


if __name__ == "__main__":
    unittest.main(verbosity=1)
