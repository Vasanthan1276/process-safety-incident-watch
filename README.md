\
# Process Safety Incident Watch

A rolling 90-day incident-intelligence monitor for:

1. Semiconductor-related process safety incidents.
2. Significant cross-industry process safety incidents with potential relevance to semiconductor manufacturing.

## What the starter version does

- Searches GDELT's DOC API across the previous three months.
- Uses multiple discovery queries for semiconductor and cross-industry incidents.
- Retrieves source-page descriptions and article text where accessible.
- Applies transparent 0-5 scoring for:
  - Source reliability
  - Process safety relevance
  - Semiconductor relevance
  - Severity / potential severity
  - Confidence
  - Overall Watch Score
- Keeps a permanent incident database.
- Adds newly discovered corroborating sources to existing incidents.
- Creates a weekly change history.
- Moves incidents outside the rolling 90-day window to the archive.
- Publishes a searchable GitHub Pages dashboard.

## Important limitation of Version 0.1

Automated title matching is used for incident deduplication. It is intentionally conservative,
but it can still split one incident into multiple records or incorrectly merge similar stories.
The next development stage should improve entity/event matching and add AI-assisted factual
summarisation with citations.

## Repository structure

```text
.github/workflows/weekly_update.yml
config/settings.json
config/scoring.json
data/incidents.json
data/latest_report.json
history/
public/index.html
public/data/
src/main.py
requirements.txt
```

## First setup

1. Create a new GitHub repository, for example:
   `process-safety-incident-watch`

2. Upload all files from this starter project, preserving the folder structure.

3. Commit the files to the default branch.

4. Go to:
   **Settings → Pages → Build and deployment → Source**

5. Select:
   **GitHub Actions**

6. Go to:
   **Actions → Weekly Process Safety Incident Update → Run workflow**

7. When the workflow finishes, open:
   **Settings → Pages**
   to find the published site URL.

## Weekly schedule

The starter workflow is scheduled for Monday at 7:17 AM Singapore time.

You can also run it manually at any time from the Actions tab.

## Adjusting source scoring

Edit:

`config/scoring.json`

A reliability score of 3 or higher is required for publication by default.

## Adjusting search scope

Edit:

`config/settings.json`

The search queries are intentionally separated so that semiconductor events and cross-industry
events remain distinct in the database.

## Next planned development stages

1. Improve event deduplication and company/location extraction.
2. Add a manual review queue for reliability scores below 3.
3. Add regulator-specific feeds and targeted company searches.
4. Generate a polished weekly HTML/Markdown report.
5. Add subscriber management and weekly email distribution.
6. Add Copilot/AI-ready exports and optional AI-assisted summaries.
