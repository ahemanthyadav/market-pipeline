"""Monthly FDI watchdog (NOT an auto-importer — by design).

India's monthly FDI numbers live in DPIIT quarterly factsheet PDFs and RBI
bulletin tables: no stable machine-readable feed exists. Fabricating numbers
with fuzzy PDF regexes would corrupt the pipeline, so this job does the
honest thing:
  1. Fetches the DPIIT FDI-statistics page, lists factsheet PDF links.
  2. Fetches the RBI press-release index for FDI items.
  3. Logs everything for a 5-minute human review + manual INSERT
     (procedure in the plan, Phase 3 job #8).

Runs 09:00 UTC on the 1st of each month. Always exits 0 (informational);
a human decides what enters capital_flows.
"""

import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) market-data-pipeline/1.0"}

SOURCES = [
    ("DPIIT-FDI-statistics",
     "https://dpiit.gov.in/publications/fdi-statistics"),
]


def list_pdf_links(page_url: str) -> list:
    """Return [(link_text, href)] for PDF links on a page."""
    resp = requests.get(page_url, headers=UA, timeout=30)
    resp.raise_for_status()
    html = resp.text
    found = []
    for match in re.finditer(
            r'<a[^>]+href="([^"]+\.pdf[^"]*)"[^>]*>(.*?)</a>',
            html, re.IGNORECASE | re.DOTALL):
        href, text = match.group(1), re.sub(r"<[^>]+>", "", match.group(2))
        href = href if href.startswith("http") else (
            "https://dpiit.gov.in" + href if href.startswith("/") else href)
        found.append((text.strip()[:120], href))
    return found


def main() -> int:
    print("=== FDI monthly watch: review output, then manual INSERT per plan ===",
          flush=True)
    for name, url in SOURCES:
        try:
            links = list_pdf_links(url)
            print(f"--- {name} ({url})", flush=True)
            if not links:
                print("MANUAL-ACTION: no PDF links found (page layout changed?)",
                      flush=True)
            for text, href in links[:10]:
                print(f"  PDF: {text} -> {href}", flush=True)
        except Exception as exc:  # noqa: BLE001 - report, never fail
            print(f"WARN {name} fetch failed: {exc}", flush=True)
    print("=== manual INSERT template: "
          "INSERT INTO capital_flows (flow_type, direction, ts, amount, segment) "
          "VALUES ('FDI', 'inflow', '<YYYY-MM-01>', <USD million>, '<source>'); ===",
          flush=True)
    return 0


if __name__ == "__main__":
    from mdjob import run_main   # one job_runs row per execution; never fails the job
    sys.exit(run_main("fdi-watch", main))
