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


if __name__ == "__main__":
    unittest.main(verbosity=1)
