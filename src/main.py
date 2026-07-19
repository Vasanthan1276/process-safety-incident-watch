\
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "config" / "settings.json"
SCORING_PATH = ROOT / "config" / "scoring.json"
INCIDENTS_PATH = ROOT / "data" / "incidents.json"
REPORT_PATH = ROOT / "data" / "latest_report.json"
HISTORY_DIR = ROOT / "history"
PUBLIC_DATA_DIR = ROOT / "public" / "data"

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = (
    "ProcessSafetyIncidentWatch/0.1 "
    "(research and incident-monitoring project; contact via repository)"
)

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "mc_cid", "mc_eid"
}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def parse_gdelt_date(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    formats = ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return value


def canonicalize_url(url: str) -> str:
    try:
        parts = urlparse(url)
        filtered = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in TRACKING_PARAMS
        ]
        return urlunparse(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path.rstrip("/"),
                "",
                urlencode(filtered),
                "",
            )
        )
    except Exception:
        return url


def domain_from_url(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def domain_matches(domain: str, configured_domain: str) -> bool:
    configured_domain = configured_domain.lower()
    return domain == configured_domain or domain.endswith("." + configured_domain)


def score_reliability(domain: str, scoring: dict) -> int:
    for score_text in ("5", "4", "3"):
        for configured_domain in scoring["reliability"].get(score_text, []):
            if domain_matches(domain, configured_domain):
                return int(score_text)

    # Generic government domains get a strong score, but are kept below a
    # specifically curated regulator unless they match the configured lists.
    if (
        domain.endswith(".gov")
        or ".gov." in domain
        or domain.endswith(".gob")
        or ".gob." in domain
    ):
        return 4

    return 2


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^a-z0-9\s-]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return sorted({kw for kw in keywords if kw.lower() in lowered})


def bounded_score(hit_count: int, thresholds: tuple[int, int, int, int]) -> int:
    if hit_count <= 0:
        return 0
    if hit_count <= thresholds[0]:
        return 1
    if hit_count <= thresholds[1]:
        return 2
    if hit_count <= thresholds[2]:
        return 3
    if hit_count <= thresholds[3]:
        return 4
    return 5


def score_semiconductor(text: str, scoring: dict) -> tuple[int, list[str]]:
    hits = keyword_hits(text, scoring["keywords"]["semiconductor"])
    return bounded_score(len(hits), (1, 2, 4, 6)), hits


def score_process_safety(text: str, scoring: dict) -> tuple[int, list[str]]:
    hits = keyword_hits(text, scoring["keywords"]["process_safety"])
    return bounded_score(len(hits), (1, 2, 4, 7)), hits


def score_severity(text: str, scoring: dict) -> tuple[int, list[str]]:
    high = keyword_hits(text, scoring["keywords"]["severity_high"])
    medium = keyword_hits(text, scoring["keywords"]["severity_medium"])

    if len(high) >= 3:
        score = 5
    elif high:
        score = 4
    elif len(medium) >= 4:
        score = 3
    elif len(medium) >= 2:
        score = 2
    elif medium:
        score = 1
    else:
        score = 0

    return score, sorted(set(high + medium))


def fetch_gdelt(query: str, max_records: int) -> list[dict]:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": max_records,
        "timespan": "3months",
        "sort": "datedesc",
    }
    response = requests.get(
        GDELT_ENDPOINT,
        params=params,
        timeout=45,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("articles", [])


def fetch_article_context(url: str) -> dict:
    result = {"description": "", "text": ""}
    try:
        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.8",
            },
            allow_redirects=True,
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower():
            return result

        soup = BeautifulSoup(response.text, "html.parser")

        meta_candidates = [
            ("meta", {"property": "og:description"}),
            ("meta", {"name": "description"}),
            ("meta", {"name": "twitter:description"}),
        ]
        for tag_name, attrs in meta_candidates:
            tag = soup.find(tag_name, attrs=attrs)
            if tag and tag.get("content"):
                result["description"] = " ".join(tag["content"].split())
                break

        paragraphs = []
        for p in soup.find_all("p"):
            text = " ".join(p.get_text(" ", strip=True).split())
            if len(text) >= 60:
                paragraphs.append(text)
            if len(" ".join(paragraphs)) >= 5000:
                break

        result["text"] = " ".join(paragraphs)[:6000]
        return result
    except Exception:
        return result


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if len(sentence.strip()) >= 35
    ]


def make_summary(title: str, description: str, body: str) -> str:
    source = description.strip() or body.strip()
    if not source:
        return title

    sentences = split_sentences(source)
    if not sentences:
        return source[:500].strip()

    chosen = []
    for sentence in sentences:
        if normalize_text(sentence) == normalize_text(title):
            continue
        chosen.append(sentence)
        if len(chosen) == 2:
            break

    summary = " ".join(chosen) if chosen else sentences[0]
    return summary[:650].strip()


def process_safety_concern(process_hits: list[str], severity_hits: list[str]) -> str:
    all_hits = set(process_hits + severity_hits)
    concerns = []

    if all_hits & {"gas leak", "gas release", "toxic", "toxic plume", "ammonia",
                   "chlorine", "fluorine", "arsine", "phosphine",
                   "hydrogen fluoride", "hydrofluoric acid", "hf"}:
        concerns.append("toxic-gas or hazardous-material containment and detection")

    if all_hits & {"fire", "explosion", "explosive", "flammable", "hydrogen", "silane"}:
        concerns.append("fire and explosion prevention, isolation and emergency response")

    if all_hits & {"rupture", "overpressure", "pressure vessel"}:
        concerns.append("mechanical integrity, overpressure protection and loss of containment")

    if all_hits & {"runaway reaction", "thermal runaway"}:
        concerns.append("reactive-chemistry and thermal-runaway controls")

    if all_hits & {"scrubber", "abatement"}:
        concerns.append("abatement and exhaust-system reliability")

    if not concerns:
        concerns.append("loss-of-containment prevention and emergency response")

    return "; ".join(concerns[:3]).capitalize() + "."


def source_record(article: dict, reliability: int, context: dict) -> dict:
    url = canonicalize_url(article.get("url", ""))
    return {
        "title": article.get("title", "").strip(),
        "url": url,
        "domain": domain_from_url(url),
        "published_at": parse_gdelt_date(article.get("seendate")),
        "source_country": article.get("sourcecountry"),
        "language": article.get("language"),
        "reliability": reliability,
        "description": context.get("description", "")[:1000],
    }


def weighted_watch_score(
    reliability: int,
    process_score: int,
    semiconductor_score: int,
    severity: int,
    confidence: int,
    stream: str,
) -> float:
    semiconductor_weight = 0.20 if stream == "Semiconductor" else 0.05
    remaining_relevance_weight = 0.30 + (0.20 - semiconductor_weight)

    score = (
        reliability * 0.20
        + process_score * remaining_relevance_weight
        + semiconductor_score * semiconductor_weight
        + severity * 0.20
        + confidence * 0.10
    )
    return round(max(0.0, min(5.0, score)), 2)


def candidate_from_article(article: dict, stream: str, scoring: dict) -> dict | None:
    title = article.get("title", "").strip()
    url = canonicalize_url(article.get("url", ""))

    if not title or not url:
        return None

    domain = domain_from_url(url)
    reliability = score_reliability(domain, scoring)

    # First-stage score using metadata so weak candidates do not all require
    # downloading the source page.
    metadata_text = " ".join(
        [
            title,
            str(article.get("domain", "")),
            str(article.get("sourcecountry", "")),
        ]
    )
    pre_semiconductor, _ = score_semiconductor(metadata_text, scoring)
    pre_process, _ = score_process_safety(metadata_text, scoring)

    if stream == "Semiconductor" and pre_semiconductor == 0 and pre_process == 0:
        return None
    if stream == "Cross-Industry" and pre_process == 0:
        return None

    context = fetch_article_context(url)
    combined = " ".join(
        [
            title,
            context.get("description", ""),
            context.get("text", ""),
        ]
    )

    semiconductor_score, semiconductor_hits = score_semiconductor(combined, scoring)
    process_score, process_hits = score_process_safety(combined, scoring)
    severity, severity_hits = score_severity(combined, scoring)

    # Confidence is deliberately conservative. Multiple corroborating sources
    # can raise this during the merge stage.
    confidence = max(1, min(5, reliability))

    watch_score = weighted_watch_score(
        reliability,
        process_score,
        semiconductor_score,
        severity,
        confidence,
        stream,
    )

    return {
        "stream": stream,
        "title": title,
        "reported_at": parse_gdelt_date(article.get("seendate")),
        "summary": make_summary(title, context.get("description", ""), context.get("text", "")),
        "process_safety_concern": process_safety_concern(process_hits, severity_hits),
        "scores": {
            "source_reliability": reliability,
            "process_safety_relevance": process_score,
            "semiconductor_relevance": semiconductor_score,
            "severity_potential": severity,
            "confidence": confidence,
            "watch_score": watch_score,
        },
        "keyword_evidence": {
            "semiconductor": semiconductor_hits,
            "process_safety": process_hits,
            "severity": severity_hits,
        },
        "sources": [source_record(article, reliability, context)],
    }


def token_set(value: str) -> set[str]:
    stop = {
        "the", "a", "an", "at", "in", "on", "of", "to", "for", "after", "as",
        "and", "or", "with", "from", "by", "is", "are", "was", "were"
    }
    return {token for token in normalize_text(value).split() if token not in stop and len(token) > 2}


def title_similarity(a: str, b: str) -> float:
    seq = SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()
    set_a, set_b = token_set(a), token_set(b)
    if not set_a or not set_b:
        jaccard = 0.0
    else:
        jaccard = len(set_a & set_b) / len(set_a | set_b)
    return max(seq, jaccard)


def source_urls(incident: dict) -> set[str]:
    return {
        canonicalize_url(source.get("url", ""))
        for source in incident.get("sources", [])
        if source.get("url")
    }


def same_incident(candidate: dict, incident: dict) -> bool:
    candidate_urls = source_urls(candidate)
    if candidate_urls & source_urls(incident):
        return True

    similarity = title_similarity(candidate["title"], incident.get("title", ""))
    if similarity < 0.80:
        return False

    candidate_hits = set(candidate.get("keyword_evidence", {}).get("process_safety", []))
    incident_hits = set(incident.get("keyword_evidence", {}).get("process_safety", []))

    # Require at least one common hazard term when title matching is not nearly exact.
    return similarity >= 0.92 or bool(candidate_hits & incident_hits)


def next_incident_id(incidents: list[dict], year: int) -> str:
    prefix = f"PSI-{year}-"
    existing = []
    for incident in incidents:
        incident_id = incident.get("incident_id", "")
        if incident_id.startswith(prefix):
            try:
                existing.append(int(incident_id.rsplit("-", 1)[1]))
            except ValueError:
                pass
    sequence = max(existing, default=0) + 1
    return f"{prefix}{sequence:04d}"


def merge_scores(existing: dict, candidate: dict) -> bool:
    changed = False
    old_scores = existing.setdefault("scores", {})
    new_scores = candidate.get("scores", {})

    for key in (
        "source_reliability",
        "process_safety_relevance",
        "semiconductor_relevance",
        "severity_potential",
    ):
        old = old_scores.get(key, 0)
        new = new_scores.get(key, 0)
        if new > old:
            old_scores[key] = new
            changed = True

    source_count = len(existing.get("sources", []))
    best_reliability = old_scores.get("source_reliability", 0)
    confidence = min(5, max(best_reliability, 2 + min(source_count, 3)))
    if confidence != old_scores.get("confidence"):
        old_scores["confidence"] = confidence
        changed = True

    new_watch = weighted_watch_score(
        old_scores.get("source_reliability", 0),
        old_scores.get("process_safety_relevance", 0),
        old_scores.get("semiconductor_relevance", 0),
        old_scores.get("severity_potential", 0),
        old_scores.get("confidence", 0),
        existing.get("stream", "Cross-Industry"),
    )
    if new_watch != old_scores.get("watch_score"):
        old_scores["watch_score"] = new_watch
        changed = True

    return changed


def merge_candidate(candidate: dict, incidents: list[dict], run_time: str) -> tuple[str, dict]:
    for incident in incidents:
        if same_incident(candidate, incident):
            changes = []
            known_urls = source_urls(incident)
            for source in candidate.get("sources", []):
                if canonicalize_url(source.get("url", "")) not in known_urls:
                    incident.setdefault("sources", []).append(source)
                    known_urls.add(canonicalize_url(source.get("url", "")))
                    changes.append(f"Added source: {source.get('domain', 'unknown source')}")

            if candidate.get("summary") and candidate["summary"] != incident.get("summary"):
                if len(candidate["summary"]) > len(incident.get("summary", "")):
                    incident["summary"] = candidate["summary"]
                    changes.append("Expanded incident summary from newly retrieved source material")

            old_evidence = incident.setdefault("keyword_evidence", {})
            for category, hits in candidate.get("keyword_evidence", {}).items():
                merged = sorted(set(old_evidence.get(category, [])) | set(hits))
                if merged != old_evidence.get(category, []):
                    old_evidence[category] = merged
                    changes.append(f"Added {category.replace('_', ' ')} evidence")

            if merge_scores(incident, candidate):
                changes.append("Updated incident scoring")

            if changes:
                incident["last_updated"] = run_time
                incident["status"] = "Updated"
                incident.setdefault("change_history", []).append(
                    {
                        "timestamp": run_time,
                        "type": "updated",
                        "changes": sorted(set(changes)),
                    }
                )
                return "updated", incident

            return "unchanged", incident

    year = utc_now().year
    incident = {
        "incident_id": next_incident_id(incidents, year),
        "stream": candidate["stream"],
        "title": candidate["title"],
        "incident_date": None,
        "reported_at": candidate.get("reported_at"),
        "first_detected": run_time,
        "last_updated": run_time,
        "status": "New",
        "summary": candidate["summary"],
        "process_safety_concern": candidate["process_safety_concern"],
        "scores": candidate["scores"],
        "keyword_evidence": candidate["keyword_evidence"],
        "sources": candidate["sources"],
        "change_history": [
            {
                "timestamp": run_time,
                "type": "new",
                "changes": ["Incident first detected by monitoring workflow"],
            }
        ],
    }
    incidents.append(incident)
    return "new", incident


def reported_datetime(incident: dict) -> datetime | None:
    value = incident.get("reported_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def archive_old_incidents(incidents: list[dict], lookback_days: int, run_time: str) -> list[str]:
    cutoff = utc_now() - timedelta(days=lookback_days)
    archived = []

    for incident in incidents:
        dt = reported_datetime(incident)
        if dt and dt < cutoff and incident.get("status") != "Archived":
            incident["status"] = "Archived"
            incident["last_updated"] = run_time
            incident.setdefault("change_history", []).append(
                {
                    "timestamp": run_time,
                    "type": "archived",
                    "changes": [f"Moved outside the rolling {lookback_days}-day window"],
                }
            )
            archived.append(incident["incident_id"])

    return archived


def publishable(candidate: dict, settings: dict) -> bool:
    scores = candidate["scores"]
    if scores["source_reliability"] < settings["minimum_publish_reliability"]:
        return False
    if scores["process_safety_relevance"] < settings["minimum_process_safety_relevance"]:
        return False
    if (
        candidate["stream"] == "Semiconductor"
        and scores["semiconductor_relevance"] < settings["minimum_semiconductor_relevance"]
    ):
        return False
    return True


def collect_candidates(settings: dict, scoring: dict) -> list[dict]:
    candidates = []
    seen_urls = set()

    for query_config in settings["queries"]:
        print(f"Searching: {query_config['name']}")
        try:
            articles = fetch_gdelt(
                query_config["query"],
                settings["gdelt_max_records_per_query"],
            )
        except Exception as exc:
            print(f"WARNING: GDELT query failed: {exc}")
            continue

        for index, article in enumerate(articles, start=1):
            url = canonicalize_url(article.get("url", ""))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            candidate = candidate_from_article(
                article,
                query_config["stream"],
                scoring,
            )
            if candidate and publishable(candidate, settings):
                candidates.append(candidate)

            # Be polite to source websites and reduce burst traffic.
            if index % 10 == 0:
                time.sleep(0.5)

    candidates.sort(
        key=lambda item: (
            item["scores"]["watch_score"],
            item.get("reported_at") or "",
        ),
        reverse=True,
    )
    return candidates


def create_report(
    incidents: list[dict],
    run_time: str,
    lookback_days: int,
    new_ids: list[str],
    updated_ids: list[str],
    archived_ids: list[str],
) -> dict:
    active = [i for i in incidents if i.get("status") != "Archived"]
    unchanged = [
        i for i in active
        if i["incident_id"] not in set(new_ids)
        and i["incident_id"] not in set(updated_ids)
    ]

    return {
        "generated_at": run_time,
        "window_days": lookback_days,
        "counts": {
            "active": len(active),
            "new": len(new_ids),
            "updated": len(updated_ids),
            "unchanged": len(unchanged),
            "archived": len(archived_ids),
        },
        "new_incident_ids": new_ids,
        "updated_incident_ids": updated_ids,
        "archived_incident_ids": archived_ids,
    }


def snapshot_history(database: dict, run_time: str) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    date_key = run_time[:10]
    save_json(HISTORY_DIR / f"{date_key}.json", database)


def copy_public_data() -> None:
    PUBLIC_DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(INCIDENTS_PATH, PUBLIC_DATA_DIR / "incidents.json")
    shutil.copy2(REPORT_PATH, PUBLIC_DATA_DIR / "latest_report.json")


def main() -> None:
    settings = load_json(SETTINGS_PATH)
    scoring = load_json(SCORING_PATH)
    database = load_json(INCIDENTS_PATH) or {
        "schema_version": 1,
        "last_run": None,
        "incidents": [],
    }

    incidents = database.setdefault("incidents", [])
    run_time = iso_now()

    # Reset transient labels before processing the latest run.
    for incident in incidents:
        if incident.get("status") in {"New", "Updated"}:
            incident["status"] = "Active"

    candidates = collect_candidates(settings, scoring)
    print(f"Publishable candidates found: {len(candidates)}")

    new_ids = []
    updated_ids = []

    for candidate in candidates:
        result, incident = merge_candidate(candidate, incidents, run_time)
        if result == "new":
            new_ids.append(incident["incident_id"])
        elif result == "updated":
            updated_ids.append(incident["incident_id"])

    archived_ids = archive_old_incidents(
        incidents,
        settings["lookback_days"],
        run_time,
    )

    # Sort newest reports first, then by watch score.
    incidents.sort(
        key=lambda item: (
            item.get("reported_at") or "",
            item.get("scores", {}).get("watch_score", 0),
        ),
        reverse=True,
    )

    database["last_run"] = run_time
    database["schema_version"] = 1

    report = create_report(
        incidents,
        run_time,
        settings["lookback_days"],
        sorted(set(new_ids)),
        sorted(set(updated_ids)),
        sorted(set(archived_ids)),
    )

    save_json(INCIDENTS_PATH, database)
    save_json(REPORT_PATH, report)
    snapshot_history(database, run_time)
    copy_public_data()

    print(json.dumps(report["counts"], indent=2))


if __name__ == "__main__":
    main()
