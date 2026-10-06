# AutoIntern

Internship monitor for ~720 companies. Every 15 minutes it fetches each company's job board, keeps US software / data / ML / quant internships, and posts the ones it has not seen before to Discord. Top-priority postings also go to your phone. There is no persistent server: GitHub Actions runs the scans and Cloudflare KV remembers what was already sent.

## How it works

1. **Timer.** A LaunchAgent on the Mac runs `gh workflow run internship-monitor.yml` every 15 minutes while the Mac is awake. GitHub's own hourly `schedule` (`17 * * * *`) is the backup when the laptop sleeps; it can run late.
2. **Cloud scan, six shards.** The `scan` job runs as a matrix of six parallel GitHub runners (`SCAN_SHARD=i/6`). Workday boards are split round-robin across the shards because Workday blocks bursts from one IP, so each shard fetches its Workday tenants one at a time. Shard 0 also scans every non-Workday board; within it, adapters run in parallel and multi-company adapters fetch four boards at a time. A tick takes about five minutes.
3. **Laptop scan.** The `tesla` job runs on this Mac's self-hosted runner for companies whose sites block GitHub's IPs: Tesla (read from the open Chrome tab), LinkedIn, Citadel, Citadel Securities, and Palo Alto Networks.
4. **Adapters** in `adapters/` turn each board into `Job`s, including structured country codes when the board provides them.
5. **Filters** (`core/filters.py`) keep internships with a tech title in the US, Summer 2027 or unstated term, not PhD-only. Ambiguous locations are kept and flagged rather than dropped.
6. **State** (`core/kv.py`) lives in Cloudflare KV: one `seen:<company>` doc per company, one health doc per scan scope, and small bookkeeping docs. It is designed for the free tier (1,000 writes and 100,000 reads a day).
7. **Discord** (`core/discord.py`) gets one embed per new posting; companies with more than 5 new postings get a summary plus a forum thread. A company's first scan posts a one-time "first look" recap of everything currently open.

### What each ping shows

- 🚨 a posting that is new, or 🕓 a **catch-up**: first seen 7+ days after its posted date (a newly added board or a filter change made it visible, so it is not newly posted).
- **Typically open:** the company's median posting lifetime once 3+ of its postings have closed, with a warning when it is a week or less.
- Flags: `location_unknown`, degree hints, term hints.

### Alerts in `#issues`

- A board that fails to fetch (one message per board per 6 hours; a provider outage across 5+ boards is one message).
- **Possible missed postings:** a board parsed fewer postings than its own API reports (3+ missing and under 90%).
- **Laptop runner looks offline:** the laptop timer is dispatching runs but none of their laptop jobs ran.
- **Cloudflare KV writes blocked:** if the daily write quota is ever hit, the run stops posting (so nothing repeats) and the postings ping after the reset.

### Weekly coverage audit

`.github/workflows/coverage-audit.yml` runs Mondays 15:00 UTC and compares against the community [SimplifyJobs Summer 2027 list](https://github.com/SimplifyJobs/Summer2027-Internships): companies with relevant listings that are not scanned (with the job board each uses), listings that pass the filters but were never pinged, and the fastest-closing companies. The summary goes to `#issues`; the full table is on the run page.

### Phone push

Tier-1 companies' Summer 2027, non-PhD postings are also published to an [ntfy](https://ntfy.sh) topic stored in the `NTFY_TOPIC` secret (at most 8 a run, the rest summarized). Install the ntfy app and subscribe to that topic name. Catch-up postings push at lower priority.

## Setup

### 1. Discord webhooks

Create webhooks for the alerts channel, a forum channel (full lists for large batches), an `#issues` channel, and a defense channel. Posts use `?wait=true` so Discord returns message IDs for KV.

### 2. Cloudflare KV

Create a Workers KV namespace and an API token with Workers KV Storage read/write.

### 3. GitHub secrets

| Secret | Purpose |
| --- | --- |
| `DISCORD_WEBHOOK_URL` | Main alerts channel |
| `DISCORD_FORUM_WEBHOOK_URL` | Forum channel for companies with more than 5 new roles |
| `DISCORD_ISSUES_WEBHOOK_URL` | `#issues` channel |
| `DISCORD_DEFENSE_WEBHOOK_URL` | Defense channel (companies with `channel: defense`) |
| `CF_ACCOUNT_ID`, `CF_KV_NAMESPACE_ID`, `CF_API_TOKEN` | Cloudflare KV |
| `NTFY_TOPIC` | Phone push topic (long random name; anyone who knows it can read it) |
| `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID` | Optional: reading ✅ reactions (off unless `CHECK_DISMISS_REACTIONS=1`) |

### 4. Laptop runner

1. Keep `~/Desktop/actions-runner/run.sh` running (or install it as a service: `./svc.sh install && ./svc.sh start`).
2. Keep Chrome open with a Tesla Careers tab, and enable **View → Developer → Allow JavaScript from Apple Events** for that window's profile.
3. Install the 15-minute dispatcher once: `./scripts/install_scan_timer.sh`.

## The whitelist

`config/whitelist.yaml` lists every company with its adapter and board identifiers. Useful fields:

- `tier`: `"1"` (red embeds, phone push) or `"2"`.
- `aliases`: other names the company goes by, used by the coverage audit.
- `dedupe_group`: boards that list the same postings (Amazon/AWS, Kensho/SPGI) ping each posting once.
- `channel: defense`: the company's pings, first look and full lists go to the defense channel instead of the main one (every posting inline, no forum thread) and never push to the phone. Used for the large defense contractors whose roles mostly require US citizenship (L3Harris, Northrop Grumman, RTX, General Dynamics, Booz Allen, CACI, ...); Leidos, Boeing and Textron stay in the main channel because of their commercial divisions.
- `include_keywords` / `exclude_keywords`, `include_phd`, `include_intl`: per-company filter overrides.
- `host`, `site`, `search_keywords`: board location for the listing-style adapters (`site` is the Paylocity company GUID, Taleo career section, Yello board id, Jobvite company path, or SelectMinds site path; `search_keywords` is a comma-separated keyword list such as `"intern,co-op"`).

Laptop-only companies must be named in both `SCAN_ONLY_COMPANIES` (tesla job) and `SCAN_SKIP_COMPANIES` (scan job) in the workflow.

### Adapters

| Adapter | Source |
| --- | --- |
| `greenhouse`, `ashby`, `lever`, `workable`, `smartrecruiters`, `rippling`, `gem` | Public job-board APIs by slug (Ashby boards with the API turned off are read through the hosted page's GraphQL) |
| `workday` | `https://{host}/wday/cxs/{tenant}/{site}/jobs`: text search plus the tenant's own intern facet; also `wdN.myworkdaysite.com` hosts |
| `oracle` | Oracle Recruiting Cloud requisitions API (newest first) |
| `eightfold` | Eightfold `pcsx` / `apply` APIs |
| `phenom` | Phenom `widgets` and `get` (`/api/jobs`); iCIMS Jibe sites use `get` |
| `icims` | Classic iCIMS portals (`/jobs/search` HTML) |
| `avature` | Avature portals (Bloomberg, Two Sigma, EA, Synopsys, Deloitte, ...) |
| `successfactors` | SAP SuccessFactors career sites (table and tile layouts) |
| `radancy` | Radancy / TalentBrew sites |
| `citadel` | Citadel's careers search, falling back to its career sitemap when challenged |
| `sitemap` | Career sitemaps of job pages (Shopify) |
| `taleo` | Oracle Taleo career sections (REST keyword search) |
| `selectminds` | Oracle Taleo Social Sourcing (SelectMinds) sites |
| `jobvite`, `paylocity`, `jazzhr`, `bamboohr`, `pinpoint`, `applicantpro` | Small-company job boards (shared `ListingAdapter` flow in `adapters/listing.py`) |
| `yello` | Yello / recsolu job boards, filtered to the United States |
| `deutschebank` | careers.db.com student-programme search |
| `goldman` | higher.gs.com campus GraphQL |
| `google`, `apple`, `amazon`, `meta`, `tiktok`, `bytedance`, `ibm`, `snap`, `optiver`, `atlassian`, `deshaw`, `linkedin`, `tesla` | Company-specific |

## Local debugging

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make scan-local                       # dry run of everything, no Discord, no KV
SCAN_SHARD=1/6 make scan-local        # one shard
SCAN_ONLY_COMPANIES=google make scan-local
```

## Tests

```bash
make test
```

Adapter tests use saved fixtures in `tests/fixtures/` and never hit the network.
