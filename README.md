# AutoIntern

Cron-only internship monitor that runs from GitHub Actions every 15 minutes, checks whitelisted career sites, generates a resume tailoring recommendation with Claude, and posts new intern roles to Discord through a webhook. It has no persistent server and scales to zero between ticks.

## How It Works

1. GitHub Actions runs `python -m scripts.scan` on `*/15 * * * *`.
2. Adapters in `adapters/` fetch company job boards and normalize postings into `Job`.
3. `core.filters` applies the whitelist rules from `config/whitelist.yaml`.
4. `core.kv` stores seen jobs, Discord message IDs, and dismissals in Cloudflare KV.
5. `core.classifier` calls Claude with `config/skill_context.md`.
6. `core.discord` posts a Discord embed with `?wait=true` and stores the returned message ID.
7. The next tick checks stored messages for a ✅ reaction and marks those jobs dismissed.

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

### 3. Configure Anthropic

Set `ANTHROPIC_API_KEY`. The default model is `claude-sonnet-4-6`; override with `ANTHROPIC_MODEL` if needed.

Paste your internship-hunting skill into `config/skill_context.md`. The checked-in file is a minimal placeholder with the expected output shape.

### 4. Set GitHub Secrets

Required:

- `DISCORD_WEBHOOK_URL`
- `CF_ACCOUNT_ID`
- `CF_KV_NAMESPACE_ID`
- `CF_API_TOKEN`
- `ANTHROPIC_API_KEY`

Optional:

- `DISCORD_BOT_TOKEN`
- `DISCORD_CHANNEL_ID`

`DISCORD_CHANNEL_ID` is only needed for the bot-token fallback.

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

- Requires `intern` in the title.
- Excludes `PhD` unless `include_phd: true`.
- Excludes non-US locations unless `include_intl: true`.
- Applies per-company `include_keywords` and `exclude_keywords`.

Tiers control Discord embed color:

- `S`: red
- `A`: blue
- `B`: gray

## Local Debugging

Install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run one dry scan without posting to Discord or calling Claude:

```bash
make scan-local
```

Run a dry scan that calls Claude when `ANTHROPIC_API_KEY` is set:

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

Adapter tests use saved JSON fixtures in `tests/fixtures/` and do not hit the network. The pipeline test mocks Claude and Discord.

## Adapter Notes

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`
- Ashby: `https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`
- Workday: `https://{host}/wday/cxs/{tenant}/{site}/jobs`
- Google, Microsoft, Amazon, and Apple use their public JSON endpoints with defensive parsing.

Some company board slugs change over time. The Greenhouse adapter logs a failed slug and continues scanning the rest of the whitelist. OpenAI and Perplexity are configured for Ashby because their Greenhouse slugs returned 404 during the live dry-run verification.
