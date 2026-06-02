# Daily Valuation Method Watch

GitHub Actions bot for finding Korean equity research reports where the target-price valuation method appears to move from `BPS x PBR` to `EPS x PER`, or from PBR to another method such as SOTP or EV/EBITDA.

## What It Does

- Runs every day at 06:00 Asia/Seoul.
- Searches public web results for Korean brokerage reports and related public PDFs/articles.
- Detects valuation-method signals such as `BPS`, `PBR`, `EPS`, `PER`, `Target PBR`, `Target PER`, `PBR 대신 PER`, and `밸류에이션 변경`.
- Compares each stock's latest detected method against the previous state in `state/valuation_methods.json`.
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

## Important Limits

This does not bypass login, paid terminals, or restricted brokerage sites. It only scans public pages and public PDFs returned by the configured search provider or explicitly listed in `REPORT_WATCH_URLS`.

The workflow uses `cron: "0 6 * * *"` with `timezone: "Asia/Seoul"`.

Scheduled workflows run from the default branch. Put these files on the repository default branch before expecting the daily run.

## Manual Run

Go to the workflow page in GitHub Actions and run **Daily valuation method watch** with `workflow_dispatch`.
