# Telegram Bot — As-Built & Update Catalog

> **Purpose:** everything about the Marketfredbot Telegram channel — how the
> deployed digest + alert pipeline is wired, how to operate and change it, and
> the tiered menu of two-way/next-step upgrades with their real constraints.
> **Repo state 2026-10-31 (v37):** `local/telegram-digest:v37` — symbol-aware routing
> (`/status cub`, `/movers cub`, `/top tcs stock` → `/stock`) with a live-universe lookup
> layer, longest-nickname-prefix matching, and nickname entries realigned to the current
> tickers (CUB.NS, ETERNAL.NS, TMPV/TMCV, POLICYBZR.NS …). v34 — v33's review fixes plus
> index quant values in /sp500 & friends (P/E, P/B, market cap from stock_metrics_daily) and
> the /sp500 command itself (36e6529). The v33 image was built before 36e6529 landed and was
> never deployed (stale stocks.py/bot.py — caught by an in-image md5 check); the tag is burned,
> do not reuse it. v33 (never live): clearer
> labels on every derived number (repriced P/E/P/B say so; sentiment, screener scores and
> track columns carry how-they-are-computed legends), 52-week range from daily closes (same
> basis as pos_52w), phone-width fixes in the digest facts block and /track table, one shared
> SCREENER_NOTE. Earlier: v29 registers
> the new commands in the Telegram "/" menu (MENU list updated + a menu-sync test so the
> menu can never drift from COMMANDS again). v28 adds
> /earnings (watchlist ★ + core top-25, 14d), /scorecard (ADR 0028 summary), and fixes
> /sectors universe parsing ('sp' substring sent sp600 to SP500; now the UNIVERSES alias
> map with an explicit error). v27 added the ADR 0029 universe aliases
> (sp400/sp600/midcap/smallcap/microcap) + core/extended tier separation (movers and the
> digest earnings section stay flagship; valuation-watch lines tag their universe).
> v26 added the REGIME
> line in the digest + /regime command (breadth / benchmark-trend / factor-momentum traffic
> light per market, descriptive only), the "Earnings this week" digest section (watchlist +
> top-25, schema 19), and a new valuation-watch gate: entries reporting within 3 days are
> suppressed (`VAL_EARNINGS_GAP_DAYS`, ADR 0028). No-send validation 10-27: digest 1383 chars
> with Regime + Earnings sections present, 3 live watch lines, regime_text renders.
> Earlier v25 (2026-10-26): VALUATION
> WATCH alert (ADR 0026 amendment: entering names with rank<=50, upside>=25%, >=3 methods above
> close — reasoning in the line, max 5), /sectors command, and the fair-value line on /stock.
> No-send validation same day: digest 954 chars, 4 real watch lines, sectors card renders.
> Earlier v24 (deployment + both
> CronJobs): HTML-safe truncation, getMe retry, rule-9 bounds, `/tmp/bot-alive` liveness
> heartbeat + exec probe, non-root securityContext (the per-image canary — passed).
> Deploy-verified same day: bot polling on pi1 (owner + 2 members), heartbeat fresh,
> uid 1000, and a no-send validation job built the digest from the live DB
> (`DIGEST-BUILD-OK 873 chars`). First scheduled v24 digest/alerts: 2026-10-27 cycle.
> Commands still unverified from the phone since v8 — see pending-tasks.
> **Last live-verified: 2026-10-12 08:46 UTC** (live: Deployment + both CronJobs on `local/telegram-digest:v8` — multi-user, ADR 0012;
> bot Running on pi1; `bot_users` holds 2 member rows added by the owner at 08:31/08:32 UTC — one private chat, one group chat, same
> name; `watchlist` still empty; `telegram-alerts` first scheduled run 09:45 UTC OK (`ALERTS-OK 3 message(s) for 3 user(s)` — owner + both member rows). Index cards validated
> against the live DB with 40 years of history; commands not yet checked from the phone).
> **v2 verification:** (digest live-fire-delivered; alert chain
> validated end-to-end with a real FII firing; two-way listener deployed and
> command-validated from the phone — /status /nifty /digest /scores answered).
> Related: ADR 0009 (the decision record) · finds §1.5 F29–F32 (the rollout
> scars) · ops manual §3/§6 (workload table, image cycle) · pending-tasks.md
> (what's still open).

---

## 1. What exists (deployed 2026-10-09; two-way added same day)

One bot — **@Marketfredbot** — one daily digest, one alert channel, and one
two-way listener, all sharing the same Secret and the same image:

```
CronJob telegram-digest ──08:00 IST──▶ ┌──────────────┐
                                        │ Telegram API │ ──▶ your chat
Grafana contact point "telegram" ◀───── └──────────────┘
        ▲            (alerts, 10m eval, policy tree → telegram)
pipeline_heartbeat / index_prices /            ▲
capital_flows / news_sentiment (read-only)     │ sendMessage only
                                    Deployment telegram-bot (pi1, always-on)
                                    getUpdates long-poll ←── your commands
                                    /status /vix /nifty /sensex /gold /fii
                                    /scores /digest /help
```

| Piece | Manifest | Notes |
|---|---|---|
| Digest script | `manifests/telegram/digest.py` | Read-only DB; exits 1 only on send failure; 4096-char guard; HTML parse mode |
| Two-way listener | `manifests/telegram/bot.py` | Long-poll `getUpdates` (offset in-memory, backlog drained on start); ONLY getUpdates consumer — Telegram 409s a second poller; single-user allow-list (strangers logged+ignored); DB connection per query (F26) |
| Image (one, two entrypoints) | `manifests/telegram/Dockerfile` | `local/telegram-digest:v2`, python:3.12-slim + psycopg/requests (versions in `requirements.lock`); CronJob uses CMD (digest.py), listener sets `args: [python, bot.py]` |
| Digest schedule | `manifests/telegram/cronjob.yaml` | `0 8 * * *`, **the only explicit-`timeZone` job** (Asia/Kolkata), pinned **pi1** (one-node import burden, roadmap §5) |
| Listener Deployment | `manifests/telegram/deployment.yaml` | 1×, pinned pi1, ~32 MiB; restarts on exit; command menu registered via `setMyCommands` |
| Alert contact point + rules | `manifests/grafana/provisioning.yaml` (`alerting.yaml` key) | Telegram contact point, policy tree → `telegram` receiver, 3 rules @ 10m |
| Grafana env | `manifests/grafana/deployment.yaml` | `$TELEGRAM_*` from Secret — same expansion pattern as `$POSTGRES_PASSWORD` |
| Secret | applied out-of-band, NOT a manifest | `telegram-bot` (`bot-token`, `chat-id`) — AGENTS.md §7 rule; rotation in §3.5 below |

Digest content (one message, ~900 chars): Nifty/Sensex/S&P/VIX/India-VIX/DXY/
Gold closes **with day-change %**, USD/INR, 7-day sentiment average, latest
FII/DII net, stale-feed warnings from `pipeline_heartbeat`, 3 newest scored
headlines.

## 1a. v3 — stock, watchlist and screener commands (deployed 2026-10-10)

Image `local/telegram-digest:v3` adds `stocks.py` (shared view layer, no DB
driver import — unit-tested with a fake in `tests/test_stocks.py`) and
`alerts.py` (third entrypoint). Live tags verified and the alerts canary passed
(`ALERTS-OK 3 line(s) sent`) on 2026-10-10; commands not yet exercised from the phone.

| Command | Answers from | Notes |
|---|---|---|
| `/stock TCS` | `stock_prices`, `stock_metrics_daily`, `stock_scores`, `stock_fundamentals`, `headline_*` | symbol or name fragment (bare NSE symbols get `.NS` tried); close, day %, 1m/6m/12m/YTD, 50/200DMA, 52w position, RSI14 (computed in Python — `technical_indicators` covers indices only), factor scores, valuation, 3 headlines |
| `/valuation PLTR` | `v_latest_valuations` (Schema 25, ADR 0026/0033) | all 15 intrinsic valuation methods (Graham, PEG, DCF, DDM, EPV, residual income, Damodaran FCFF, owner earnings, …), median fair value + upside vs close, practitioner vs academic medians, methods flagged above close; descriptive only — not advice |
| `/calibration` | `v_latest_calibration` | factor ICs, archetype excess returns, multibagger gate marginal value, shrunk factor weights (classifier loop, ADR 0033) |
| `/methods` | `v_valuation_methods` | valuation & scientific method catalog — formulas, lens, peer-reviewed sources (Schema 25) |
| `/news TCS` | `headline_entities` → `raw_headlines`, `news_sentiment`, `headline_events` | last 14 d, sentiment per headline, event tags |
| `/top growth sp500` | `stock_scores` | best 10 eligible by `value\|quality\|growth\|momentum\|risk\|composite`; default composite / NIFTY500 |
| `/movers` | `stock_scores` | entered / left the top-20 between the last two FULL runs (≥40 eligible rows — canary runs ignored) |
| `/track` | `stock_scores` × `stock_prices` | top-20 picks vs equal-weight universe, 1/5/21/63 trading days forward; says "not enough history" until a horizon completes |
| `/watch`, `/watch add TCS [qty cost] [note]`, `/watch rm TCS` | `watchlist` | **the bot's one write path**; holdings P&L totalled per currency |
| `/world [region]` | `index_prices`, `ticker_names` | every index + gold/silver/oil/DXY: close, day, 1m, YTD; best/worst YTD |
| `/fx` (`/usdinr`) | `fx_rates` | all pairs, INR pairs first; YTD appears once history reaches 1 Jan |
| `/macro` | `index_prices`, `fx_rates`, `bond_yields`, `capital_flows` | gold in INR/10 g (parity, ex-duty), 5 yield spreads, FII-vs-Nifty correlation |

- **CronJob `telegram-alerts`** (02:45 node-local, after the screener chain):
  top-20 entries/exits, watchlist day moves ≥5 %, 50/200-day crossings, tagged
  news events in 24 h. Stateless — fires only on data dated today/yesterday
  UTC, so weekends and stalled feeds cannot repeat an alert. Silent when
  nothing changed (`ALERTS-OK nothing changed`).
- **Digest** gains a compact watchlist section; skipped while the table is
  empty or absent, so image v3 is safe to roll before the schema Job runs.
- **Menu:** `bot.py` now calls `setMyCommands` on start (it was documented
  but never coded).
- **Gotcha — chunk pruning:** `stock_prices` has ~3,400 chunks. Time bounds
  must be parameters or `now()` arithmetic; a `date_trunc()` or sub-select
  bound planned for 11 s / ran 79 s. Both fixed in `stocks.py`; keep it so.

### v4 (deployed 2026-10-10, same day) — layout, depth, candidates, confidence

- **Layout:** every list is a fixed-width `<pre>` table ≤ 38 characters
  (`stocks.table`), every single instrument a bold headline plus label/value
  blocks (`stocks.kv`). Tests assert the width (`PHONE_WIDTH`).
- **`/index <name>`** (and `/nifty` `/sensex` `/gold`): 1w/1m/3m/YTD/1y, 52-week
  high/low and distance from the high, 50/200-day averages, RSI — one
  380-day read of `index_prices`.
- **YTD everywhere:** `/world`, `/vix`, digest index table, `/top`, `/scores`,
  `/watch`, movers.
- **Valuation at the latest close:** `stock_fundamentals` holds P/E, P/B and
  market cap as Yahoo reported them on the weekly fetch (Saturdays). `/stock`
  rescales those three by `close_now / close_at_snapshot` (`stocks.reprice`);
  EV/EBITDA is shown with its snapshot date. **The screener's value factor
  still uses the weekly snapshot** — up to a week stale by design of
  `stock_fundamentals.py`; fixing that is a screener change, not a bot one.
- **`/stock`** also shows revenue/EPS/net-income growth of the latest fiscal
  year, debt/equity, FCF yield and daily traded value.
- **`/multibagger [nifty|sp500]`** (`/mb`): gates then a 0-100 score. Gates —
  revenue AND earnings CAGR ≥ 15 % over the 2-3 fiscal years on file (with a
  terminal-year collapse guard rejecting series with ≥3 points where the most
  recent fiscal year contracted >15% vs the prior year, ADR 0033), ROE ≥
  15 %, debt/equity ≤ 1 (financials exempt across both NSE `"Financial Services"`
  and GICS `"Financials"`, ADR 0033), outside the largest 30 % by
  market cap, PEG ≤ 2, price above the 200-day average. Score — growth 35,
  quality 25, valuation 15, momentum 15, size 10. The message prints how many
  names each gate dropped. First live run: 16 of 495 (NIFTY500), 1 of 503
  (SP500). **Continuous gate validation (ADR 0033):** `ops-tools/classifier_loop.py`
  evaluates each gate's Marginal Value (GMV) against single-gate near-misses on
  resolved 30/91/182d outcomes.
- **Confidence (0-100) on every recommendation** — `/top`, `/scores`,
  `/stock`, `/multibagger`. It measures evidence quality, never the chance of
  a gain, and every message says so. Screener picks: data coverage 40, factor
  agreement 30, rank stability over the last 10 days 30. Multibagger: years
  of history 30, EPS and net income both confirming 20, margin over the gates
  15, positive FCF 15, reported ROE 10, upper half of the 52-week range 10.
  ≥ 70 high · ≥ 45 medium · else low.

### v5 / v6 (2026-10-10) — long-term reference on the index card

`/index`, `/nifty`, `/sensex`, `/gold` now end with a **Long-term reference**
block: the 1-, 3-, 5- and 10-year average level with today's distance from each, the
high on file, and a reading — very low / low / normal / high / very high.

- **How the reading is made** (`stocks.long_term_block`): a rising index is
  almost always above its long average, so the level alone says nothing. The
  reading ranks today's *gap to the 5-year average* (3-year below 7.5 years of history) against that same gap on
  every earlier day (mid-rank percentile; bands at 10/25/75/90). Volatility
  indices (`^VIX`, `^INDIAVIX`) mean-revert, so their level is ranked directly.
- **History:** `index_prices` holds everything Yahoo has from **1985** for all
  38 tickers (292,387 rows, no duplicates; deep backfill 2026-10-10). Depth per
  index is what the feed carries — S&P 500, Nikkei, FTSE 40 years (the card
  reads at most 40); Sensex from 1997; Nifty 500 2005; Nifty 50 / Bank / IT
  2007; India VIX 2008; gold/silver/crude futures 2000; Midcap 150 only 2019.
  So every card except Midcap 150 now shows the 10-year average and bases its
  reading on the 5-year average across at least two bear markets (Nifty 50:
  "since 2012"; S&P 500: "since 1991"). Verified against the live DB: Nifty 50
  very low, Sensex very low, Nifty 500 low, S&P 500 high, gold high, Nikkei
  very high.
- **How the depth became possible:** the first 5-year backfill wrote one
  transaction across 7-day chunks and held ~2,900 of ~6,400 lock slots for 26
  minutes. Two changes fixed that: the fetchers (`yfinance-fetcher:v5`,
  `nse-fetcher:v4`) commit one calendar year per transaction, and
  `index_prices` creates one-year chunks from now on (approved schema change,
  `01-schema.sql`). The 40-year run took 3.5 minutes and added 37 chunks.
- **Evidence:** distance from a long-run average is context, not a timing
  signal. Valuation measures (earnings-based, e.g. CAPE) have better backing
  for "expensive or cheap" but need index earnings, which no feed here carries.
- **Deployed state:** `v5` is live on the bot and both CronJobs and was
  validated inside the pod against the live DB. `v6` only rewords the reading
  sentence ("Relative to that average it was lower on N% of days"); its image
  is imported on pi1 and the manifests carry the tag, but **the apply did not
  happen — pi2's API went down mid-rollout** (see pending-tasks).

## 2. Decisions already paid for (ADR 0009 — don't re-litigate)

- **One bot, two roles.** A dead token kills BOTH channels silently — job
  history (`kubectl get jobs -n market-data`) is the tell.
- **Token never in a file.** Grafana needs the chat *id* as a literal in the
  rendered file (F29), but the repo copy of `grafana/provisioning.yaml` carries the
  placeholder `__TELEGRAM_CHAT_ID__` (no personal id in git, 2026-10-10);
  `ops-tools/apply-grafana-provisioning.sh` renders the real one from Secret
  `telegram-bot` at apply time. Never `kubectl apply -f` that file directly.
- **CGNAT (F10) ⇒ webhooks are impossible.** Any "push" design is dead on
  arrival; everything must be outbound (this is what forces long-polling for
  §4's two-way tier).
- **Egress single point:** api.telegram.org via pi0's Pi-hole DNS. If an
  ad-list ever eats the domain, the stale-feed alert cannot announce itself
  through the same dead channel — `kubectl get jobs` + the 01:45 heartbeat
  rows remain ground truth.

## 3. Operating it

### 3.1 Fire the digest now (test or manual resend)
```bash
kubectl create job -n market-data telegram-digest-test --from=cronjob/telegram-digest
kubectl logs -n market-data job/telegram-digest-test --tail=5   # expect: "digest sent ..."
```

### 3.2 Change digest content or bot commands
Edit `digest.py`/`bot.py` → bump the tag in `cronjob.yaml` **and**
`deployment.yaml` → full ops-manual §6 cycle, **pi1 import only** (pinned; no
pi2/pi3 import needed). Then `kubectl rollout restart -n market-data
deployment/telegram-bot` — the listener is always-on and must re-read the
image; the digest CronJob needs nothing (its pods are created per run).
Update `requirements.lock` per §6 after any dependency-touching rebuild.

### 3.3 Tune or add alert rules
Edit `grafana/provisioning.yaml` (`alerting.yaml` key) →
`bash ops-tools/apply-grafana-provisioning.sh` (`--dry-run` first; it renders the
chat id from the Secret — never plain `kubectl apply -f`) →
**wait ~60 s before deleting the pod** (F32: a pod recreated too fast can
mount the previous ConfigMap revision and crash-loop on the old error).
Alerting is **all-or-nothing per file** — one bad rule takes the contact point
down and crash-loops Grafana (F29–F31). Current thresholds (retuned 2026-10-12 from the data on hand; live):

| Rule | Trigger | Tuning note |
|---|---|---|
| Stale pipeline feed | `pipeline_heartbeat` fresh=false count > 0 | solid as-is |
| VIX above 30 | `^VIX` close > 30 | raised from 25: since 2016 VIX closed > 25 on 14 % of days, > 30 on 5.7 %, > 35 on 2.3 % |
| Large FII outflow | latest FII net < −5000 Rs cr | raised from −3000 (7 of the 8 recorded days were below it; fired at −12,944 on day one). Only 8 days of history — **re-tune at ~60 days** |
| Sentiment drop | 7-day mean `sentiment_score` < −0.15 | added to the live config 2026-10-12 (it was in the repo but never applied); daily means so far: min −0.146, avg 0.02, sd 0.12 over ~2 weeks (source mix changed 10-12) — re-tune from real firings |

### 3.4 Validate the alert chain end-to-end (no test endpoint exists, F31)
Channel sanity check first — token + chat id + pi0 egress DNS in one shot
(`@@TELEGRAM_*@@` resolve from `.secrets.env` at run time; no values on the line):
```bash
python3 ops-tools/runcmd.py pi0 pi0 'curl -s https://api.telegram.org/bot@@TELEGRAM_BOT_TOKEN@@/getMe'
python3 ops-tools/runcmd.py pi0 pi0 'curl -s -d chat_id=@@TELEGRAM_CHAT_ID@@ -d text="alert-chain test" https://api.telegram.org/bot@@TELEGRAM_BOT_TOKEN@@/sendMessage'
```
Temporarily: group `interval: 10m → 1m` AND stale-feed threshold `gt 0 → gt -1`
→ apply → fresh pod → rule goes `firing` within ~2 min → message arrives →
**revert both** → rule resolves (resolve message = the return path validated).
Check states live:
```bash
# policy tree:      GET /api/v1/provisioning/policies        (kebab-case! F31)
# rules + states:   GET /api/prometheus/grafana/api/v1/rules
# contact points:   GET /api/v1/provisioning/contact-points   (nested receivers list)
```

### 3.5 Rotate the token / Secret
BotFather `/revoke` → `kubectl -n market-data delete secret telegram-bot` →
recreate with `--from-literal` (out-of-band, never a file) → restart Grafana
pod (digest pods pick it up per-run automatically). Remember: the chat-id
literal in `provisioning.yaml` is unaffected by token rotation.

### 3.6 Operate the two-way listener
```bash
kubectl logs -n market-data deploy/telegram-bot -f
# "listening as @Marketfredbot …"   startup OK
# "drained N backlog update(s)"     the pre-restart queue, skipped by design
# "answered /status"                a command round-trip (→ phone)
# "IGNORED chat=…"                  an unknown chat messaged the bot — expected; the owner also gets one access notice
```
- **Restart semantics:** the offset is in-memory; on start the backlog is
  drained without replying, so a restart never replays old commands. A
  command sent while the pod is down is dropped — acceptable for idempotent
  read-only replies.
- **Only one poller, ever.** Telegram answers 409 to a second `getUpdates`
  consumer on the same token — including a local test script left running.
  The digest CronJob and Grafana only `sendMessage`; they are safe.
- **Quick health check:** send `/status` from the phone → expect
  `answered /status` in logs within a second (the poll holds ~25 s server-side,
  so replies are near-instant).

## 4. Update catalog (tiers; pick, then check pending-tasks discipline)

### Tier 1 — quick wins (days, no new infrastructure)

1. **Sentiment-drop rule** — the one candidate from finds Tier 1 #1 still
   unwritten (e.g., 7-day mean < −0.15). Pure addition to `alerting.yaml`.
2. **Threshold tuning pass** — §3.3 table; two weeks of real firings first.
3. **Watchlist section in the digest** — ✅ written 2026-10-10 (§1a; deploy
   pending). Original note: a `watchlist` table (ticker, note)
   joined to `stock_prices`/`stock_scores`; manual-INSERT pattern like FDI.
   First step toward the portfolio layer riding the same channel.
4. **Weekly chart digest** — Grafana render-to-PNG → `sendPhoto`. Needs the
   image-renderer plugin investigated first (headless Chromium on ARM64 = not
   trivial; treat as research item).

### Tier 2 — two-way interface — ✅ DEPLOYED 2026-10-09 (see §1)

As built: exactly the design below, plus three as-built decisions —
`/start` maps to help, unknown commands get the help text back, and the
backlog is drained on start so a restart never replays old commands.
Validated from the phone 2026-10-09: /status /nifty /digest /scores all
answered; zero errors, zero restarts, strangers' messages never answered.

**Architecture (forced by F10):** an always-on **Deployment** running a
`getUpdates` long-poll loop (offset-tracked, `timeout=25 s`) — NOT a webhook.
Outbound-only, survives CGNAT, no VPS.

- New `bot.py` entrypoint alongside `digest.py`; same base image contents
  (requests + psycopg already in v1) → tag `v2`; still pi1-only.
- **Security model: owner + members (multi-user since 2026-10-12, ADR 0012; was single-user).** The owner
  (the Secret's chat id) plus the active `bot_users` rows are answered, each with their OWN watchlist, digest and
  alerts. Other senders are logged and never answered. Telegram bots are public — anyone who guesses
  @Marketfredbot can message it; the allow-list is the door. The owner is told once per unknown chat per day, with a
  ready `/adduser <id> Name` line. See "Managing users" below.
- Command surface (all read-only SELECTs — house rule):

| Command | Answers from | Notes |
|---|---|---|
| `/status` | `pipeline_heartbeat` | the /health checklist, in your pocket |
| `/vix` `/nifty` `/sensex` `/gold` | `index_prices` | latest close + day change |
| `/fii` | `capital_flows` | latest net, 5-day trend |
| `/scores` | `stock_scores` | current top-25 shortlist digest |
| `/digest` | (re-runs digest logic) | on-demand resend |

- Grafana alerts keep flowing untouched — the contact point and the listener
  share the bot token; Telegram allows concurrent getUpdates + sendMessage.
- One process, ~30–50 MB RAM on pi1; restartPolicy Always, liveness = log
  heartbeats in a `bot_state` row (or reuse `pipeline_heartbeat`).

### Forgiving symbol lookup (2026-10-18, ADR 0023, image v13)
`/stock`, `/news`, `/watch` accept typos: fuzzy correction first ("infosis" → INFY.NS, shown with a 🔎 note; text under
5 letters is never auto-picked), AI (shared gateway, `general`) for nicknames — its ticker AND company name must agree
with the universe (live: facebook → META, big blue → IBM, hul → HINDUNILVR.NS, coke → KO); `/watch` never writes on a corrected symbol — it asks for
the exact one. Switch: `BOT_AI_RESOLVE` on the Deployment.

### Symbol-aware routing (2026-10-31, image v37)
Every command now understands a stock name, not just `/stock`:

- **A no-argument command followed by a stock** means that stock —
  `/status cub`, `/movers cub`, `/digest tcs` → `/stock CUB.NS`. Commands that genuinely take
  free text keep it (`/news tcs` is news *about* TCS, `/watch cub` adds CUB, `/top growth sp500`
  keeps its factor/universe); that list is `router.ARG_COMMANDS`.
- **A command whose parse fails** falls back to the router with the command word stripped, so
  `/top tcs stock` and `/top cub` → `/stock TCS.NS` / `/stock CUB.NS` instead of a usage error.
- **Unknown commands are read as text** (`/statsus cub` → `/stock CUB.NS`) — `/statsus` is not a
  command, so the args go through the normal nickname/keyword/lookup path.
- **The router's last layer is the live universe, not the static nickname map.**
  `bot.symbol_lookup` calls `stocks.pick_one_ex`, i.e. the same layers as `/stock`
  (exact symbol/name → fuzzy → AI when `BOT_AI_RESOLVE=1`), so symbols missing from
  `nicknames.py` still resolve: `cub` → CUB.NS, `karnataka bank` → KTKBANK.NS,
  `tata motors pv` → TMPV.NS. `/route cub` now reports `universe lookup`; why this matters —
  the hardcoded map had drifted (CITYUNION.NS, ZOMATO.NS, PBFINTECH.NS, TATAMOTORS.NS,
  UTIAM.NS, UJJIVAN.NS, L&TFH.NS, FSN ECOMM.NS, BURGERKING.NS, KVTBANK.NS were all dead
  symbols; repco/suryoday/esaf/capital-small are no longer in the universe and were dropped).
  Chosen over "add more nicknames" because the map will always lag: the universe table is the
  truth (ADR 0029), and the lookup is one cached query per message.
- **Longest nickname prefix wins**, so a 3-word name is never truncated to its first two
  (`tata motors pv` → TMPV.NS, not TMCV.NS).
- Tests: `tests/test_router.py` (fake lookup — no DB) covers all of the above; the live
  resolver was verified inside the v37 pod with `kubectl exec … python -c "import bot; …"`.

### /ask — questions answered from stored facts only (2026-10-19, ML-7d family, image v14–v23)
`/ask TCS outlook`, `/ask news infosys`, `/ask usdinr`, `/ask fii flows`. Flow: find the subject (stock via the forgiving
resolver — word-by-word, so "TCS outlook" is about TCS; index/FX/flows/news by keyword), gather a small facts JSON (public
data only; news via semantic search over `headline_embeddings`, ADR 0024), gateway `general` answers, and the answer ships
only if every number and direction checks out (`factcheck.py`, shared with ML-7a; percent forms of given values allowed,
rounding/truncation to ≤0.01 tolerated, metric-key numbers like "12-month" allowed). Two failed checks or gateway down →
the bot shows the facts table instead; a question mapping to no facts is refused, never guessed. 12–60 s per answer
(NVIDIA queueing varies); never a scoring input.

###AI market note in the digest (2026-10-18, ML-7a, image v9+; first real note 2026-10-19)
`llm-narrative` (07:40 IST) writes `llm_narratives`; the 08:00 digest adds a "Summary (AI-written; every number checked)"
section only when today's note passed the check. Switch: `DIGEST_NARRATIVE` on CronJob `telegram-digest` and Deployment `telegram-bot`.

### Deterministic-first cross-command routing & universal AI integration (2026-11-08, ADR 0033)
- **Precedence rule:** Deterministic command parsers and universe/keyword routing always execute first. Only when deterministic parsing fails (or a command cannot handle the given arguments) does the router redirect or invoke AI fallback (`router.ai_route`), always notifying the user with a `↪️` banner before running the target command:
  - **Command typo correction (`router.fuzzy_command`):** e.g., `/stck AAPL` or `/sectrs sp500` notifies `↪️ /stck is not a command — running /stock AAPL` and executes `/stock AAPL`.
  - **Cross-type redirect (`/stock` ↔ `/index`):** `/stock nifty` detects an index and redirects to `/index nifty`; `/index tcs` detects an equity and redirects to `/stock TCS.NS`.
  - **Mismatched command + argument fallback:** e.g., `/top tcs` or `/sectors what is the market regime` strips the mismatched command, routes the argument deterministically (with `router.ai_route` via `llm-gateway` as fallback when `BOT_AI_RESOLVE=1`), notifies the user which command can fulfill the request, and runs it.
  - **Implicit `/watch <company>`:** `/watch tesla` or `/watch TCS` (without `add`) resolves the symbol via `pick_one_ex` and adds it directly on an exact match while preserving multi-word notes (`/watch add TCS core holding`).
- **Universal fact-checked AI enrichment (`ask.py`, `stocks.py`):**
  - `/stock` and `/news` surface verified `per_symbol` (ML-7b) and `event_brief` (ML-7c) notes from `llm_narratives` (`stocks.symbol_narrative`).
  - `/ask` now builds structured facts from `screener_facts`, `sector_facts`, `regime_facts`, `scorecard_facts`, `earnings_facts`, and enriched `stock_facts` (intrinsic fair value, upside %, 5-factor scores, archetype, next earnings).
  - Appending `ai`, `explain`, `why`, or `?` to any command (e.g., `/top growth sp500 explain`, `/regime ai`) strips the trigger token, renders the deterministic card, and appends a `factcheck.py`-verified AI takeaway (`ask.summarize_card`).

### Tier 3 — Strata-backed chat — ❌ DROPPED 2026-10-18 (ADR 0022: hosted API only, no local GPU; free-text questions = ML-7d `/ask`)

Free-text messages → the workstation's Strata server (Qwen3.8-Flash-Next) →
AI answers in Telegram, streamed by **message-edit** every few seconds
(`editMessageText`), thinking folded via `reasoning_effort` (map `/think
low|medium|high`).

**Blockers, verified 2026-10-09:**
- Strata binds `127.0.0.1:8080` **only** — unreachable from the cluster.
  Needs a Strata serve flag for 0.0.0.0 (check `serve --host`), or an SSH
  tunnel/Tailscale serve front; workstation LAN IP is `<workstation-lan-ip>`.
- Workstation uptime becomes a dependency of a cluster service — WoL or
  "accept it's best-effort" are the only honest options.
- Latency reality: ~1.5–2K tok/s prefill locally ⇒ seconds-to-minutes to
  first token on long chats. Acceptable for async Q&A, not for chat-as-UX.
- Conversation memory: keep last-N turns per chat in a small table, or send
  Strata's `--conversation-cache` a session key (its prompt cache makes
  re-reads cheap — see Strata docs).

### Icebox (deliberately not now)

- Inline keyboards / alert acks (callback state, heavier parsing).
- ~~Multi-user allow-list + per-user watchlists~~ ✅ done 2026-10-12 (ADR 0012). Still open: per-user digest time / alert threshold.
- Voice notes → whisper → command (fun, zero homelab value).

## 5. Gotcha index (don't re-pay these)

F29 digit-typing in env expansion · F30 nonzero relativeTimeRange ·
F31 receiver-before-route + moved API paths · F32 ConfigMap cache lag — all
in `market-data-finds-and-projects.md` §1.5; ADR 0009 for the decision
record; cluster-mutation skill for any apply/restart/import discipline.

### Managing users (2026-10-12, ADR 0012)
All from the OWNER's own chat with the bot — no Secret edit, no restart:

| Command | Effect |
|---|---|
| `/users` | who has access + how many symbols each watches (never their holdings) |
| `/adduser 123456789 Alice` | give a chat access: own `/watch`, own daily digest, own change alerts |
| `/rmuser 123456789` | remove access (their watchlist is kept; `/adduser` restores it) |

**Adding a person:** ask them to message `@Marketfredbot` (anything). You get an *Access request* notice containing
`/adduser <their id> Name` — reply with that line. Then ask them to send `/start` once (Telegram only lets a bot
message people who have started it). Or get their id from `@userinfobot` and run `/adduser` directly.
**Isolation:** each chat sees only its own `/watch`, holdings, digest watchlist section and watchlist alerts; market data,
screener and FX are shared read-only. Members cannot run `/adduser|/rmuser|/users`. A *group* chat can be added but then
shares one watchlist among its members — prefer private chats. The DB superuser can still read every watchlist (AR-8).
**Failure behaviour:** a member who blocked the bot only logs a WARN in the digest/alert Jobs; the Jobs fail only when the
owner's send fails or every send fails. If `bot_users` is missing the bot runs owner-only.
**Live state (2026-10-12 08:46 UTC):** 2 member rows, both named `sailu` — one private chat, one group chat (the group would share one
watchlist). Both were added by the owner at 08:31/08:32 UTC; whether each has sent `/start` is not verified (the log shows one `/start`
answered). Consider `/rmuser` on the group row if separate watchlists are wanted.
**Owner id:** the Secret `telegram-bot` key `chat-id` is the owner and must be a *private* chat id (positive). A previous
workaround pointed it at a group (negative id) — that makes the whole group the owner; don't.
