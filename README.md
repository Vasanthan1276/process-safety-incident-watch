# Process Safety Incident Watch

Public-source screening for semiconductor and significant industrial process-safety reports. Discovery is automated; incident verification and any formal Tier classification require human review.

## Current workflow

1. Search GDELT and Google News RSS using the existing five configured query groups.
2. Screen titles and leads with word boundaries, physical-event context and industrial relevance. Preserve uncertain event reports for review; record rejected discoveries and their reasons.
3. Retrieve article metadata where available. Failed requests and unresolved Google redirect pages stay explicit; a blocked article does not discard an otherwise relevant headline.
4. Update the permanent incident database. Merge identical source URLs, retaining old source versions; suggest cross-publisher duplicate matches for human review without deleting or merging historical IDs.
5. Build a review projection with attributed claims, explicit missing data, tentative company/location mentions, potential fab assessment questions and human review annotations.
6. Publish the static dashboard through the existing weekly workflow. The schedule remains Monday 07:17 Asia/Singapore; actual starts can be delayed by GitHub.

The new dashboard provides searchable, paginated review candidates, screened-out records, all historical dates, duplicate links, source health, local review drafts and exports. `public/classic.html` preserves the previous card layout with corrected legacy status wording and a link back.

## Run and check locally

Python 3.12 and Node 22 are used in CI. No paid API, AI service, analytics or secret is required.

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
node --check public/app.js
node tests/test_dashboard.cjs
python src/build_review.py
python -m http.server 8765 --directory public
```

Open `http://localhost:8765/`. `build_review.py` reads the stored database and creates `data/review_index.json` plus its public copy. It does **not** discover new incidents or refresh the source collection date.

```bash
# Live discovery: changes local data and creates new history snapshots.
python -u src/main.py

# Portable, embedded-data preview: no server or live feeds required.
python src/make_preview.py docs/incident-watch-preview.html
```

The portable preview requires a modern browser supporting `DecompressionStream`. Review draft storage may be unavailable for downloaded HTML in some browsers; an explicit message is shown if saving fails. Its Existing layout link opens the currently published dashboard. The served application links to the preserved `classic.html`.

## Review and evidence semantics

- **Needs review / In review:** incomplete human assessment.
- **Reviewed:** a reviewer recorded a decision and evidence notes. It does not certify every claim.
- **Excluded:** a reviewer excluded a record; it remains in the database.
- **Retain / Needs context / Out of scope:** automated relevance screening, separate from review state.
- **Possible duplicate:** a suggested match, not an established shared event or independent corroboration.
- **Legacy Confirmed:** originally a publisher-rating threshold, never proof of independent verification. The raw historical label is preserved. A later successful collector run migrates operational labels to Needs review while retaining `legacy_review_status`.

Company and location matching is a limited dictionary of explicit title mentions, with source links and an unverified label. The publisher's country is never used as the incident location. Unknown entities are left unknown until reviewed. Source titles and usable descriptions are attributed reporting, not confirmed facts. Lessons are assessment prompts, not findings of root cause or safeguard failure. No definitive Tier 1 or Tier 2 assignment is generated.

### Save a review

Use each record's evidence panel to save a browser-only draft, then Export review drafts. This does not update GitHub. Review exported notes for public suitability, merge the selected entries into `config/reviews.json` (do not replace unrelated entries), rebuild and submit for repository review.

Each entry is keyed by an existing incident ID and requires `status`, `reviewed_by`, `reviewed_at` (ISO timestamp), and `notes`. Optional `confirmed_facts`, `preliminary_or_disputed`, and `consequences` are lists of `{ "text": "…", "source_url": "…" }`. Confirmed facts require status Reviewed. Optional `incident_date`, `company`, and `location` each require a corresponding `incident_date_source_url`, `company_source_url` or `location_source_url`. Evidence URLs must match a source already attached to that record. Add a missing public source through normal repository review first; do not invent a URL.

The schema validator enforces provenance fields, not the truth of a claim or the quality of a review. Browser exports contain workflow decisions and notes; they do not automatically populate confirmed facts. Only public-source information belongs in this public repository.

## Dates and health

Incident date, publication date, source update date, discovery date and database update time are separate. The dashboard uses an incident date when supplied, otherwise publication/discovery time as a labelled proxy for its rolling 90-day filter. The original records have no established incident dates. GDELT `seendate` is treated as discovery time in new records; legacy GDELT date ambiguity is labelled.

Source health is independent of the GitHub job result. Partial source failures produce a degraded state and retain successful feed results. A total discovery failure preserves database/report bytes and their collection dates, updates diagnostics, and keeps the dashboard usable. Healthy empty responses are distinct from outages. Staleness is shown after eight days from the stored data collection time, not from a rebuild of the review projection. A clean run does not guarantee complete coverage.

The weekly workflow is restricted to `main` for collection and deployment. A separate read-only CI workflow runs offline tests for pull requests and non-main pushes. No branch preview deployment is configured. GitHub workflow execution has not been triggered as part of this proposed change.

## Preservation and limitations

All existing raw data and history files are unchanged in this proposal. Re-screening is a derived index; it never deletes old records or rewrites snapshots. New same-day history snapshots use unique filenames instead of overwriting earlier runs.

The rules favor transparent, conservative decisions but are English-focused and imperfect. Duplicate suggestions depend on a limited entity vocabulary and nearby report dates; they can miss follow-up coverage or suggest unrelated events. Existing erroneous historical merges are not silently split. Source independence, actual event dates, consequences and causes still require source checking. Some original publisher URLs cannot be resolved from Google News. Query caps and article fetch limits leave coverage incomplete; diagnostics expose those limits.

See [the change review and validation record](docs/review-2026-11-13.md) for the baseline findings, test results and remaining review steps.
