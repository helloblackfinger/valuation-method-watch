# Daily Valuation Method Watch

GitHub Actions bot for finding Korean equity research reports where the target-price valuation method appears to move from `BPS x PBR` to `EPS x PER`, or from PBR to another method such as SOTP or EV/EBITDA.

## What It Does

- Runs every day at 06:00 Asia/Seoul.
- Searches public web results for Korean brokerage reports and related public PDFs/articles.
- Detects valuation-method signals such as `BPS`, `PBR`, `EPS`, `PER`, `Target PBR`, `Target PER`, `PBR 대신 PER`, and `밸류에이션 변경`.
- Optionally ingests Telegram messages/PDFs that the bot can access, so forwarded analyst-channel posts can enter the same report pipeline.
- Compares each stock's latest detected method against the previous state in `state/valuation_methods.json`.
- Keeps confirmed transition stocks in a follow-up registry so Telegram can show their valuation-method history over time.
- Writes a Markdown report under `reports/YYYY-MM-DD.md`.
- Commits the new report and updated state back to the repository.

## Required Setup

Add at least one search API key as a GitHub Actions secret:

- `BRAVE_SEARCH_API_KEY`
- `SERPAPI_API_KEY`
- `TAVILY_API_KEY`

Optional secrets and variables:

- `OPENAI_API_KEY`: enables a short AI-written Korean summary section.
- `OPENAI_MODEL`: repository variable for the model name. Defaults to `gpt-4.1-mini` if unset.
- `REPORT_WATCH_URLS`: repository variable with extra URLs to scan, separated by commas or new lines.
- `TELEGRAM_COLLECT_UPDATES`: repository variable. Set to `1` to collect Telegram bot updates.
- `TELEGRAM_SOURCE_CHAT_IDS`: optional repository variable with allowed Telegram chat IDs or `@channelusername` values, separated by commas or new lines.
- `TELEGRAM_UPDATES_LIMIT`: optional repository variable for each run's Telegram update limit. Defaults to `50`.
- `TELEGRAM_PUBLIC_CHANNELS`: optional repository variable with public Telegram channel names such as `@channel1,@channel2`. The bot scans recent web-preview posts from `t.me/s/...`.
- `TELEGRAM_PUBLIC_POSTS_LIMIT`: optional repository variable for recent posts scanned per public channel. Defaults to `20`.

Telegram collection has two practical modes:

1. Collection room mode: create a Telegram group/channel, add the bot, set `TELEGRAM_COLLECT_UPDATES=1`, and forward analyst-channel posts or PDFs there. Use `TELEGRAM_SOURCE_CHAT_IDS` to restrict ingestion to that room.
2. Public channel mode: add public channel names to `TELEGRAM_PUBLIC_CHANNELS`. This reads only public web-preview posts. Private or paid channels are not bypassed.

## Important Limits

This does not bypass login, paid terminals, or restricted brokerage sites. It only scans public pages and public PDFs returned by the configured search provider or explicitly listed in `REPORT_WATCH_URLS`.

The workflow uses `cron: "0 6 * * *"` with `timezone: "Asia/Seoul"`.

Scheduled workflows run from the default branch. Put these files on the repository default branch before expecting the daily run.

## Manual Run

Go to the workflow page in GitHub Actions and run **Daily valuation method watch** with `workflow_dispatch`.
