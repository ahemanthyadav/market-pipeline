# 0031. Strategy Lab (curated, evidence-graded) + automated research watcher

- Date: 2026-10-27
- Status: accepted (deployed: marketview v11 `/strategies`, `research-fetcher:v1` weekly CronJob, schema 21)

## Context
The owner asked for the strategies with long peer-reviewed backing to live in
the UI — references, mechanisms, live examples on our data, and a way to test
each against the database — plus an automated job that keeps the list current
with new research, explicitly restricted to "widely acclaimed, peer-reviewed,
well-accepted" work.

## Decision
1. **Curated tiers are hand-reviewed and code-reviewed only.** The Strategy Lab
   content (`marketview app/strategies.py`) cites top-journal classics (JF,
   JFE, RAS, JFQA, academic presses), carries each strategy's mechanism,
   catches, live example query, EV-0 verdict (§5a/§5b) and the EV-0 test
   setup. Every page carries McLean & Pontiff (2016): post-publication decay.
2. **The automated job NEVER edits the curated tiers.** `research-watcher`
   (weekly, Mondays) collects paper metadata into `research_items`
   (append-only, ON CONFLICT DO NOTHING) from, by evidence tier:
   tier 1 Journal of Finance + Journal of Financial Economics RSS
   (peer-reviewed), tier 2 NBER working papers, tier 3 arXiv q-fin
   (preprints, kept only on a factor-keyword match). Scoring is DETERMINISTIC
   (venue weight + keyword hits) — no LLM in the loop. The Lab shows them
   under "Research radar — UNVETTED"; promotion requires a reviewed commit
   and, where testable, an EV-0 run. Review of Financial Studies (OUP) is
   bot-walled and deliberately skipped — we do not masquerade past bot checks.
3. **Testability is part of the page.** Each strategy shows the exact EV-0
   §5 criteria and the copy-paste harness commands; testable-but-untested
   strategies get roadmap entries with acceptance tests written BEFORE testing
   (EV-19 PEAD, EV-20 fundamental quality/value panels), per the house rule.

## Consequences
- 35 CronJobs; one more tiny image (`research-fetcher`, requests + stdlib XML).
- A journal feed dying (or more bot walls) degrades per-source with a WARN,
  like the news RSS feeds (F13 pattern).
- The radar is metadata-only (titles/authors/links) — abstracts and full text
  stay with the publishers; nothing is scraped past access controls.
