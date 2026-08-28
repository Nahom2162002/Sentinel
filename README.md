# Sentinel

A tiny watcher: point it at a page, it polls, and it pings a Discord webhook
when the content changes.

There are two ways to run it:

- **`cli.py`** — local script + cron, state stored as JSON files on disk. No
  deploy, no dashboard, just a script.
- **`app.py`** — a Flask web dashboard (add/remove/check targets in the
  browser) deployed to Vercel, with state stored in Redis since Vercel
  Functions have no persistent disk. This is the one with a URL.

Both share the same fetch/diff/notify logic in `sentinel/`.

## Local CLI (`cli.py`)

```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your Discord webhook URL in
cp config.example.json config.json   # then edit with your real target(s)
```

Getting a Discord webhook URL: Server Settings -> Integrations -> Webhooks ->
New Webhook -> Copy Webhook URL.

### config.json

```json
{
  "targets": [
    {
      "name": "RemoteOK Software Jobs",
      "url": "https://remoteok.com/remote-software-jobs.json",
      "json_fields": ["position", "company", "tags"],
      "webhook_url": null
    },
    {
      "name": "Newegg Microsoft Surface",
      "url": "https://www.newegg.com/Microsoft-Surface/Store/ID-2142354",
      "selector": ".item-cells-wrap",
      "webhook_url": null
    }
  ]
}
```

- `selector` (optional): a CSS selector narrowing an HTML page to the block
  you care about, so unrelated page churn (ads, footers, timestamps) doesn't
  trigger false positives. Omit/`null` to watch the whole `<body>`.
- `json_fields` (optional): if `url` points at a `.json` feed (a list of
  objects) instead of an HTML page, list which fields to pull out of each
  object — one line per item, e.g. `["position", "company", "tags"]` for a
  job-postings API. Each new item in the feed then shows up as an added line
  in the diff. Items missing every listed field (e.g. a feed's leading
  metadata object) are skipped. Ignored for HTML targets.
- `webhook_url` (optional): override the default `DISCORD_WEBHOOK_URL` from
  `.env` on a per-target basis. Leave `null` to use the default.

### A note on picking targets

Not every page can be watched by a plain HTTP fetch:

- **Login-walled or heavily JS-rendered pages** (e.g. LinkedIn job search
  results, RemoteOK's own HTML tag pages) return an empty shell to `requests`
  — there's no login and no JavaScript execution here. Look for a public
  page, or a `.json`/API feed the site exposes instead (many job boards
  have one — see `json_fields` above; that's how the RemoteOK target works).
- **Aggressively bot-protected sites** (e.g. Amazon, Best Buy) either serve a
  bot-check interstitial or silently hang the connection. This isn't fixable
  with a better selector — pick a different source (smaller retailers and
  Shopify-based stores are typically fine; Newegg worked cleanly in testing)
  or accept that target won't work.

Always do a one-off `--once` run against a brand-new target and read the
resulting `state/<name>.json` before trusting it — if the logged content is
suspiciously short or reads like a CAPTCHA/interstitial page, the target
isn't actually being watched.

### Running

Single check pass (first run per target just records a baseline, no ping):

```bash
python cli.py --once
```

Continuous polling loop:

```bash
python cli.py --loop --interval 300
```

Check only one target:

```bash
python cli.py --once --target "RemoteOK Software Jobs"
```

### Running on a schedule (cron)

```cron
*/5 * * * * cd /path/to/Sentinel && /path/to/venv/bin/python cli.py --once >> sentinel.log 2>&1
```

## Web dashboard (`app.py`) — deploy to Vercel

The dashboard lets you add/remove/check targets from a browser instead of
hand-editing `config.json`, and gives you a public URL. It needs two things
`cli.py` doesn't: a Redis database (Vercel Functions have no local disk to
write JSON state files to) and a couple of extra env vars.

### 1. Provision Redis

In the Vercel dashboard: your project -> **Storage** -> **Create Database**
-> **Upstash Redis** (via the Marketplace integration) -> connect it to this
project. This automatically injects `KV_REST_API_URL` and
`KV_REST_API_TOKEN` (or `UPSTASH_REDIS_REST_URL`/`UPSTASH_REDIS_REST_TOKEN`,
depending on how the integration names them — `sentinel/store.py` checks
both) into your project's environment variables.

### 2. Set environment variables

In Vercel project settings -> Environment Variables, set:

| Variable | Required | Purpose |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | yes | default webhook for targets without their own |
| `SECRET_KEY` | yes | random string, signs the login session cookie |
| `DASHBOARD_PASSWORD` | recommended | password-gates the dashboard — without it, **anyone with the URL can add targets, trigger fetches, and redirect notifications to their own webhook**, since the add-target form accepts an arbitrary `webhook_url` |
| `CRON_SECRET` | recommended | Vercel automatically sends this as a bearer token when it calls `/api/cron-check` on schedule; set it to stop anyone else from hitting that endpoint and mass-triggering checks |

Generate `SECRET_KEY`/`CRON_SECRET` with `python -c "import secrets; print(secrets.token_hex(32))"`.

### 3. Deploy

```bash
npm i -g vercel      # if you don't have the CLI
vercel login
vercel link           # connect this directory to a Vercel project
vercel env pull .env.local   # optional, to test locally with real Redis
vercel --prod
```

Or connect the GitHub repo in the Vercel dashboard for auto-deploy on push.

### 4. Automatic polling (cron)

`vercel.json` schedules `/api/cron-check` once a day (`0 13 * * *`, UTC) —
that's the fastest cadence the free Hobby plan allows; any more frequent
cron expression is rejected at deploy time. On Pro you can tighten the
schedule. Either way, the **"Check now" / "Check all now" buttons on the
dashboard run the same check logic on demand**, so the demo doesn't have to
wait for cron.

### Local testing of the web app

```bash
vercel dev
```

or run Flask directly against a real (or local) Redis REST endpoint:

```bash
KV_REST_API_URL=... KV_REST_API_TOKEN=... DISCORD_WEBHOOK_URL=... SECRET_KEY=dev python app.py
```

## How it works

1. `sentinel/fetcher.py` fetches the URL — either pulling text out of a
   `selector` (or the whole `<body>`) via BeautifulSoup for HTML pages, or
   extracting `json_fields` from each item for `.json` feeds.
2. `sentinel/differ.py` hashes the normalized text; if the hash matches the
   last-seen hash, nothing happens. If it differs, `difflib` produces an
   added/removed line summary.
3. `sentinel/notifier.py` posts an embed to the Discord webhook with that
   summary (added/removed counts + a few sample lines).
4. State is persisted so the next run has something to diff against —
   `sentinel/state.py` (local JSON files) for `cli.py`, `sentinel/store.py`
   (Redis via the Upstash REST API) for `app.py`.

## Stretch ideas (not yet built)

- Email/SMS notification backends alongside Discord.
- Per-target polling intervals instead of one global interval/cron schedule.
- Retry/backoff on transient fetch failures instead of an immediate error ping.
- Basic rate limiting on the dashboard's add-target/check endpoints.
