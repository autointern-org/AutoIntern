# AutoIntern

Internship monitor. While this Mac is awake, a LaunchAgent starts a GitHub Actions run every 15 minutes. GitHub also runs an hourly backup if the laptop is asleep. Tesla is scanned on the laptop through Chrome; every other company scans on GitHub-hosted Ubuntu.

It generates a resume tailoring recommendation with a configurable LLM (default: Gemini 3.1 Flash Lite) and posts new intern roles to Discord. There is no persistent server.

## How It Works

1. The laptop timer runs `gh workflow run internship-monitor.yml` every 15 minutes. GitHub's own `schedule` is an hourly backup (`17 * * * *`).
2. The **scan** job (Ubuntu) fetches every whitelisted company except Tesla. The **tesla** job (this Mac's self-hosted runner) fetches Tesla from the open Chrome tab.
3. Adapters in `adapters/` normalize postings into `Job`.
4. `core.filters` applies the whitelist rules from `config/whitelist.yaml`.
5. `core.kv` stores seen jobs, Discord message IDs, and dismissals in Cloudflare KV.
6. `core.classifier` calls the configured LLM with `config/skill_context.md`.
7. `core.discord` posts a Discord embed with `?wait=true` and stores the returned message ID.
8. The next tick checks stored messages for a ✅ reaction and marks those jobs dismissed.

## Setup

### 1. Create a Discord webhook

Create a webhook in the target Discord channel and copy its URL. The scanner posts with `?wait=true` so Discord returns the message ID for KV state.

Dismissals use the webhook message endpoint first:

`GET /webhooks/{webhook.id}/{webhook.token}/messages/{message.id}`

If that cannot read reactions in your server setup, create a Discord bot with access to the channel and set `DISCORD_BOT_TOKEN` plus `DISCORD_CHANNEL_ID`. The fallback uses:

`GET /channels/{channel.id}/messages/{message.id}`

### 2. Create Cloudflare KV

Create a Workers KV namespace and note:

- Cloudflare account ID
- KV namespace ID
- API token with Workers KV Storage edit/read access

The script stores:

- `job:{job_id}` for seen notifications and Discord message metadata
- `dismissed:{job_id}` for dismissed postings

### 3. Configure resume LLM

Default provider is Gemini (`gemini-3.1-flash-lite`). Set `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey).

To use Anthropic instead, set GitHub secret `ANTHROPIC_API_KEY` and repository variable `RESUME_LLM_PROVIDER` to `anthropic`.

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RESUME_LLM_PROVIDER` | `gemini` | `gemini` or `anthropic` |
| `RESUME_LLM_MODEL` | provider default | Override model id |
| `GEMINI_API_KEY` | — | Gemini API key (`GOOGLE_API_KEY` also works) |
| `ANTHROPIC_API_KEY` | — | Only when provider is `anthropic` |

If no API key is set for the active provider, the scanner still posts to Discord with a placeholder resume block.

Paste your internship-hunting skill into `config/skill_context.md`. The checked-in file is a minimal placeholder with the expected output shape.

### 4. Set GitHub Secrets (minimum viable)

**Required** (repo → Settings → Secrets and variables → Actions → Secrets):

| Secret | Where to get it |
| --- | --- |
| `DISCORD_WEBHOOK_URL` | Main alerts channel webhook |
| `DISCORD_FORUM_WEBHOOK_URL` | Forum channel webhook (full list when a company has more than 5 new roles) |
| `DISCORD_ISSUES_WEBHOOK_URL` | `#issues` channel webhook (broken fetches) |
| `CF_ACCOUNT_ID` | Cloudflare dashboard → account ID in URL/sidebar |
| `CF_KV_NAMESPACE_ID` | Workers → KV → your namespace → ID |
| `CF_API_TOKEN` | Cloudflare API token with Workers KV Storage read/write |
| `GEMINI_API_KEY` | Google AI Studio API key |

**Optional secrets:**

- `ANTHROPIC_API_KEY` — only if `RESUME_LLM_PROVIDER=anthropic`
- `DISCORD_BOT_TOKEN` — reaction fallback if webhook cannot read ✅
- `DISCORD_CHANNEL_ID` — required with bot-token fallback

**Optional variables** (Settings → Actions → Variables):

- `RESUME_LLM_PROVIDER` — `gemini` (default) or `anthropic`
- `RESUME_LLM_MODEL` — e.g. `gemini-3.1-flash-lite-preview`

### 5. Customize the whitelist

Edit `config/whitelist.yaml`.

Example:

```yaml
companies:
  - name: anthropic
    adapter: greenhouse
    slug: anthropic
    tier: S
    include_keywords: []
    exclude_keywords: ["new grad"]

  - name: google
    adapter: google
    tier: S
    include_keywords: ["software", "research", "machine learning", "STEP"]
```

Filtering rules:

- Requires an intern-like title (`intern`, `campus`, `student`, `co-op`, …) **and** a tech function (SWE/ML/research/quant/SRE/data). Recruiter/ambassador titles are dropped. `new grad` is not enough.
- Drops PhD-only internships unless the JD opens to undergrads. Drops winter/spring/fall internships unless they are part-time; Summer 2027 and unstated terms are kept.
- Drops clearly non-US locations using the title plus location field (US state wins; empty location is kept). Does not read the JD for country.
- Applies per-company `include_keywords` and `exclude_keywords`.

Tiers control Discord embed color:

- `S`: red
- `A`: blue
- `B`: gray

### 6. Laptop runner (Tesla + 15-minute timer)

Tesla cannot be fetched from GitHub-hosted Ubuntu (Akamai). The **tesla** job runs on a self-hosted runner on this Mac and reads listings from Chrome.

1. Keep `~/Desktop/actions-runner/run.sh` running.
2. Keep Chrome open with a Tesla Careers tab. Enable **View → Developer → Allow JavaScript from Apple Events**.
3. Install the 15-minute dispatcher once:

```bash
./scripts/install_scan_timer.sh
```

That LaunchAgent calls `gh workflow run internship-monitor.yml` every 15 minutes while the Mac is awake. The Ubuntu scan still runs from GitHub's hourly schedule if the laptop is asleep. Tesla ticks while commuting are dropped.

## Local Debugging

Install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run one dry scan without posting to Discord or calling the LLM:

```bash
make scan-local
```

Run a dry scan that calls the LLM when `GEMINI_API_KEY` (or provider key) is set:

```bash
PYTHONPATH=. python -m scripts.scan --dry-run
```

Run the real scanner locally:

```bash
PYTHONPATH=. python -m scripts.scan
```

## Tests

```bash
make test
```

Adapter tests use saved JSON fixtures in `tests/fixtures/` and do not hit the network. The pipeline test mocks the LLM and Discord.

## Adapter Notes

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`
- Workday: `https://{host}/wday/cxs/{tenant}/{site}/jobs`
- Google, Microsoft, Amazon, and Apple use their public JSON endpoints with defensive parsing.

Some company board slugs change over time. The Greenhouse adapter logs a failed slug and continues scanning the rest of the whitelist. OpenAI and Perplexity are configured for Ashby because their Greenhouse slugs returned 404 during the live dry-run verification.
