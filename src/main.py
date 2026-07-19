from __future__ import annotations

import email.utils
import json
import random
import re
import shutil
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]

SETTINGS_PATH = ROOT / "config" / "settings.json"
SCORING_PATH = ROOT / "config" / "scoring.json"

INCIDENTS_PATH = ROOT / "data" / "incidents.json"
REPORT_PATH = ROOT / "data" / "latest_report.json"
DIAGNOSTICS_PATH = ROOT / "data" / "run_diagnostics.json"
UNKNOWN_SOURCES_PATH = ROOT / "data" / "unknown_sources.json"

HISTORY_DIR = ROOT / "history"
PUBLIC_DATA_DIR = ROOT / "public" / "data"

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_RSS_ENDPOINT = "https://news.google.com/rss/search"

USER_AGENT = (
    "ProcessSafetyIncidentWatch/0.4 "
    "(public process-safety incident monitoring project)"
)

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.8",
    }
)


class DiscoveryError(RuntimeError):
    """Raised when a discovery provider fails."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def load_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return default.copy() if default else {}

    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def canonicalize_url(url: str) -> str:
    try:
        parts = urlparse(url)
        filtered_query = [
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
                urlencode(filtered_query),
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
    configured_domain = configured_domain.lower().strip()
    return domain == configured_domain or domain.endswith("." + configured_domain)


def is_government_domain(domain: str) -> bool:
    government_markers = (
        ".gov",
        ".gov.",
        ".gob",
        ".gob.",
        ".go.kr",
        ".go.jp",
        ".go.th",
        ".go.id",
        ".gov.sg",
        ".gov.my",
        ".gov.tw",
        ".gov.au",
        ".gov.nz",
        ".gc.ca",
    )

    return any(marker in domain for marker in government_markers)


def score_reliability(domain: str, scoring: dict) -> int:
    if not domain:
        return 1

    for score_text in ("5", "4", "3"):
        for configured_domain in scoring["reliability"].get(score_text, []):
            if domain_matches(domain, configured_domain):
                return int(score_text)

    if is_government_domain(domain):
        return 5

    return 2


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"https?://\S+", " ", value)
    value = re.sub(r"[^a-z0-9\s-]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_html_text(value: str) -> str:
    if not value:
        return ""

    soup = BeautifulSoup(value, "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    return sorted(
        {
            keyword
            for keyword in keywords
            if keyword.lower() in lowered
        }
    )


def bounded_score(
    hit_count: int,
    thresholds: tuple[int, int, int, int],
) -> int:
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
    return bounded_score(len(hits), (1, 2, 4, 7)), hits


def score_process_safety(text: str, scoring: dict) -> tuple[int, list[str]]:
    hits = keyword_hits(text, scoring["keywords"]["process_safety"])
    return bounded_score(len(hits), (1, 2, 4, 8)), hits


def score_severity(text: str, scoring: dict) -> tuple[int, list[str]]:
    high = keyword_hits(text, scoring["keywords"]["severity_high"])
    medium = keyword_hits(text, scoring["keywords"]["severity_medium"])

    if len(high) >= 3:
        score = 5
    elif high:
        score = 4
    elif len(medium) >= 5:
        score = 3
    elif len(medium) >= 2:
        score = 2
    elif medium:
        score = 1
    else:
        score = 0

    return score, sorted(set(high + medium))


def parse_gdelt_date(value: str | None) -> str | None:
    if not value:
        return None

    value = str(value).strip()

    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return (
                datetime.strptime(value, fmt)
                .replace(tzinfo=timezone.utc)
                .isoformat()
            )
        except ValueError:
            continue

    return value


def parse_rss_date(value: str | None) -> str | None:
    if not value:
        return None

    try:
        dt = email.utils.parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc).isoformat()

    except Exception:
        return value


def fetch_gdelt(
    query: str,
    query_name: str,
    max_records: int,
) -> list[dict]:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": min(max_records, 50),
        "timespan": "3months",
        "sort": "datedesc",
    }

    retry_delays = [10, 25]
    total_attempts = len(retry_delays) + 1

    for attempt in range(total_attempts):
        print(
            f"GDELT {query_name}: attempt {attempt + 1}/{total_attempts}",
            flush=True,
        )

        try:
            response = SESSION.get(
                GDELT_ENDPOINT,
                params=params,
                timeout=30,
            )

        except requests.RequestException as exc:
            if attempt == total_attempts - 1:
                raise DiscoveryError(
                    f"GDELT {query_name}: network failure: {exc}"
                ) from exc

            delay = retry_delays[attempt] + random.uniform(1, 5)
            print(
                f"GDELT network error. Retrying in {delay:.0f}s.",
                flush=True,
            )
            time.sleep(delay)
            continue

        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError as exc:
                raise DiscoveryError(
                    f"GDELT {query_name}: invalid JSON response."
                ) from exc

            articles = payload.get("articles", [])

            if not isinstance(articles, list):
                raise DiscoveryError(
                    f"GDELT {query_name}: unexpected response structure."
                )

            print(
                f"GDELT {query_name}: {len(articles)} articles.",
                flush=True,
            )

            normalized = []

            for article in articles:
                url = canonicalize_url(article.get("url", ""))

                normalized.append(
                    {
                        "title": article.get("title", "").strip(),
                        "url": url,
                        "source_domain": domain_from_url(url),
                        "source_name": article.get("domain", ""),
                        "published_at": parse_gdelt_date(article.get("seendate")),
                        "description": "",
                        "discovery_source": "GDELT",
                        "source_country": article.get("sourcecountry"),
                        "language": article.get("language"),
                    }
                )

            return normalized

        if response.status_code == 429 and attempt < total_attempts - 1:
            retry_after = response.headers.get("Retry-After")

            try:
                delay = float(retry_after) if retry_after else retry_delays[attempt]
            except ValueError:
                delay = retry_delays[attempt]

            delay += random.uniform(1, 5)

            print(
                f"GDELT HTTP 429. Retrying in {delay:.0f}s.",
                flush=True,
            )

            time.sleep(delay)
            continue

        raise DiscoveryError(
            f"GDELT {query_name}: HTTP {response.status_code}"
        )

    raise DiscoveryError(
        f"GDELT {query_name}: exhausted retries."
    )


def fetch_google_news_rss(
    query: str,
    query_name: str,
    max_records: int,
) -> list[dict]:
    params = {
        "q": f"{query} when:90d",
        "hl": "en-SG",
        "gl": "SG",
        "ceid": "SG:en",
    }

    try:
        response = SESSION.get(
            GOOGLE_NEWS_RSS_ENDPOINT,
            params=params,
            timeout=30,
        )

        response.raise_for_status()

    except requests.RequestException as exc:
        raise DiscoveryError(
            f"Google News RSS {query_name}: {exc}"
        ) from exc

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise DiscoveryError(
            f"Google News RSS {query_name}: invalid XML."
        ) from exc

    results = []

    for item in root.findall(".//item")[:max_records]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description = clean_html_text(item.findtext("description") or "")

        source_node = item.find("source")
        source_name = ""
        source_url = ""

        if source_node is not None:
            source_name = (source_node.text or "").strip()
            source_url = (source_node.attrib.get("url") or "").strip()

        source_domain = domain_from_url(source_url)

        results.append(
            {
                "title": title,
                "url": canonicalize_url(link),
                "source_domain": source_domain,
                "source_name": source_name,
                "published_at": parse_rss_date(pub_date),
                "description": description,
                "discovery_source": "Google News RSS",
                "source_country": None,
                "language": "English",
            }
        )

    print(
        f"Google News RSS {query_name}: {len(results)} articles.",
        flush=True,
    )

    return results


def article_key(article: dict) -> str:
    title = normalize_text(article.get("title", ""))
    domain = article.get("source_domain", "")

    if title:
        return f"{title}|{domain}"

    return canonicalize_url(article.get("url", ""))


def merge_discovered_articles(
    articles: list[dict],
) -> list[dict]:
    merged = {}

    for article in articles:
        key = article_key(article)

        if not key:
            continue

        if key not in merged:
            merged[key] = article
            merged[key]["discovery_sources"] = [
                article.get("discovery_source", "Unknown")
            ]
            continue

        existing = merged[key]

        discovery_source = article.get(
            "discovery_source",
            "Unknown",
        )

        if discovery_source not in existing["discovery_sources"]:
            existing["discovery_sources"].append(discovery_source)

        if (
            len(article.get("description", ""))
            > len(existing.get("description", ""))
        ):
            existing["description"] = article.get("description", "")

        if (
            not existing.get("source_domain")
            and article.get("source_domain")
        ):
            existing["source_domain"] = article["source_domain"]

        if (
            not existing.get("source_name")
            and article.get("source_name")
        ):
            existing["source_name"] = article["source_name"]

    return list(merged.values())


def fetch_article_context(url: str) -> dict:
    result = {
        "description": "",
        "text": "",
        "final_url": url,
    }

    if not url:
        return result

    try:
        response = SESSION.get(
            url,
            timeout=8,
            allow_redirects=True,
        )

        result["final_url"] = response.url
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")

        if "html" not in content_type.lower():
            return result

        soup = BeautifulSoup(response.text, "html.parser")

        for attrs in (
            {"property": "og:description"},
            {"name": "description"},
            {"name": "twitter:description"},
        ):
            tag = soup.find("meta", attrs=attrs)

            if tag and tag.get("content"):
                result["description"] = " ".join(
                    tag["content"].split()
                )
                break

        paragraphs = []

        for paragraph in soup.find_all("p"):
            text = " ".join(
                paragraph.get_text(
                    " ",
                    strip=True,
                ).split()
            )

            if len(text) >= 60:
                paragraphs.append(text)

            if len(" ".join(paragraphs)) >= 4500:
                break

        result["text"] = " ".join(paragraphs)[:5000]

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


def make_summary(
    title: str,
    description: str,
    body: str,
) -> str:
    source = description.strip() or body.strip()

    if not source:
        return title

    sentences = split_sentences(source)

    if not sentences:
        return source[:650].strip()

    chosen = []

    for sentence in sentences:
        if normalize_text(sentence) == normalize_text(title):
            continue

        chosen.append(sentence)

        if len(chosen) == 2:
            break

    return (
        " ".join(chosen)
        if chosen
        else sentences[0]
    )[:650].strip()


def process_safety_concern(
    process_hits: list[str],
    severity_hits: list[str],
) -> str:
    hits = set(process_hits + severity_hits)
    concerns = []

    if hits & {
        "toxic gas",
        "gas leak",
        "gas release",
        "chemical leak",
        "chemical release",
        "hydrogen fluoride",
        "hydrofluoric acid",
        "ammonia",
        "chlorine",
        "fluorine",
        "arsine",
        "phosphine",
        "toxic plume",
    }:
        concerns.append(
            "toxic-gas or hazardous-material containment, detection and emergency isolation"
        )

    if hits & {
        "fire",
        "flash fire",
        "explosion",
        "blast",
        "flammable",
        "hydrogen",
        "silane",
        "pyrophoric",
    }:
        concerns.append(
            "fire and explosion prevention, ignition control and emergency response"
        )

    if hits & {
        "rupture",
        "overpressure",
        "pressure vessel",
        "pipe failure",
        "piping failure",
    }:
        concerns.append(
            "mechanical integrity, overpressure protection and loss-of-containment prevention"
        )

    if hits & {
        "runaway reaction",
        "thermal runaway",
        "reactive chemistry",
    }:
        concerns.append(
            "reactive-chemistry and thermal-runaway controls"
        )

    if hits & {
        "scrubber",
        "abatement",
        "exhaust",
        "duct",
    }:
        concerns.append(
            "exhaust and abatement-system reliability"
        )

    if not concerns:
        concerns.append(
            "loss-of-containment prevention and emergency response"
        )

    return "; ".join(concerns[:3]).capitalize() + "."


def weighted_watch_score(
    reliability: int,
    process_score: int,
    semiconductor_score: int,
    severity: int,
    confidence: int,
    stream: str,
) -> float:
    if stream == "Semiconductor":
        score = (
            reliability * 0.20
            + process_score * 0.30
            + semiconductor_score * 0.20
            + severity * 0.20
            + confidence * 0.10
        )
    else:
        score = (
            reliability * 0.20
            + process_score * 0.45
            + semiconductor_score * 0.05
            + severity * 0.20
            + confidence * 0.10
        )

    return round(
        max(0.0, min(5.0, score)),
        2,
    )


def qualifies_for_monitoring(
    stream: str,
    process_score: int,
    semiconductor_score: int,
    settings: dict,
) -> bool:
    if process_score < settings["minimum_process_safety_relevance"]:
        return False

    if (
        stream == "Semiconductor"
        and semiconductor_score
        < settings["minimum_semiconductor_relevance"]
    ):
        return False

    return True


def review_status_for(
    reliability: int,
    settings: dict,
) -> str:
    if reliability >= settings["minimum_confirmed_reliability"]:
        return "Confirmed"

    return "Under Review"


def record_unknown_source(
    unknown_sources: dict,
    domain: str,
    title: str,
    source_name: str,
    run_time: str,
) -> None:
    if not domain:
        return

    records = unknown_sources.setdefault("sources", {})
    record = records.setdefault(
        domain,
        {
            "domain": domain,
            "source_name": source_name,
            "first_seen": run_time,
            "last_seen": run_time,
            "times_seen": 0,
            "sample_titles": [],
        },
    )

    record["last_seen"] = run_time
    record["times_seen"] += 1

    if title and title not in record["sample_titles"]:
        record["sample_titles"].append(title)
        record["sample_titles"] = record["sample_titles"][:5]


def build_candidate(
    article: dict,
    stream: str,
    scoring: dict,
    settings: dict,
    context: dict,
    run_time: str,
    unknown_sources: dict,
    pre_semiconductor: int,
    pre_process: int,
) -> dict | None:
    title = article.get("title", "").strip()

    description = (
        context.get("description", "")
        or article.get("description", "")
    )

    body = context.get("text", "")

    source_domain = article.get("source_domain", "").strip()
    final_url = canonicalize_url(context.get("final_url", ""))

    final_domain = domain_from_url(final_url)

    if (
        final_domain
        and final_domain not in {
            "news.google.com",
            "google.com",
        }
    ):
        source_domain = final_domain

    reliability = score_reliability(
        source_domain,
        scoring,
    )

    combined = " ".join(
        [
            title,
            description,
            body,
        ]
    )

    semiconductor_score, semiconductor_hits = score_semiconductor(
        combined,
        scoring,
    )

    process_score, process_hits = score_process_safety(
        combined,
        scoring,
    )

    severity_score, severity_hits = score_severity(
        combined,
        scoring,
    )

    # The discovery queries are already hazard-targeted. These floors prevent
    # a valid item from disappearing solely because a publisher blocks scraping.
    if stream == "Semiconductor":
        semiconductor_score = max(
            semiconductor_score,
            1 if pre_semiconductor > 0 else 0,
        )

        process_score = max(
            process_score,
            1 if pre_process > 0 else 0,
        )

    else:
        process_score = max(
            process_score,
            1 if pre_process > 0 else 0,
        )

    if not qualifies_for_monitoring(
        stream,
        process_score,
        semiconductor_score,
        settings,
    ):
        return None

    review_status = review_status_for(
        reliability,
        settings,
    )

    if reliability < settings["minimum_confirmed_reliability"]:
        record_unknown_source(
            unknown_sources,
            source_domain,
            title,
            article.get("source_name", ""),
            run_time,
        )

    confidence = max(
        1,
        min(
            5,
            reliability,
        ),
    )

    watch_score = weighted_watch_score(
        reliability,
        process_score,
        semiconductor_score,
        severity_score,
        confidence,
        stream,
    )

    source_url = canonicalize_url(
        article.get("url", "")
    )

    source = {
        "title": title,
        "url": source_url,
        "domain": source_domain,
        "source_name": article.get("source_name", ""),
        "published_at": article.get("published_at"),
        "source_country": article.get("source_country"),
        "language": article.get("language"),
        "reliability": reliability,
        "discovery_sources": article.get(
            "discovery_sources",
            [article.get("discovery_source", "Unknown")],
        ),
        "description": description[:1200],
    }

    return {
        "stream": stream,
        "title": title,
        "reported_at": article.get("published_at"),
        "summary": make_summary(
            title,
            description,
            body,
        ),
        "process_safety_concern": process_safety_concern(
            process_hits,
            severity_hits,
        ),
        "review_status": review_status,
        "scores": {
            "source_reliability": reliability,
            "process_safety_relevance": process_score,
            "semiconductor_relevance": semiconductor_score,
            "severity_potential": severity_score,
            "confidence": confidence,
            "watch_score": watch_score,
        },
        "keyword_evidence": {
            "semiconductor": semiconductor_hits,
            "process_safety": process_hits,
            "severity": severity_hits,
        },
        "sources": [source],
    }


def token_set(value: str) -> set[str]:
    stop_words = {
        "the",
        "a",
        "an",
        "at",
        "in",
        "on",
        "of",
        "to",
        "for",
        "after",
        "as",
        "and",
        "or",
        "with",
        "from",
        "by",
        "is",
        "are",
        "was",
        "were",
        "says",
        "report",
    }

    return {
        token
        for token in normalize_text(value).split()
        if token not in stop_words
        and len(token) > 2
    }


def title_similarity(first: str, second: str) -> float:
    sequence_score = SequenceMatcher(
        None,
        normalize_text(first),
        normalize_text(second),
    ).ratio()

    first_set = token_set(first)
    second_set = token_set(second)

    if not first_set or not second_set:
        jaccard = 0.0
    else:
        jaccard = len(first_set & second_set) / len(first_set | second_set)

    return max(sequence_score, jaccard)


def source_urls(incident: dict) -> set[str]:
    return {
        canonicalize_url(source.get("url", ""))
        for source in incident.get("sources", [])
        if source.get("url")
    }


def same_incident(candidate: dict, incident: dict) -> bool:
    if source_urls(candidate) & source_urls(incident):
        return True

    similarity = title_similarity(
        candidate.get("title", ""),
        incident.get("title", ""),
    )

    if similarity < 0.78:
        return False

    candidate_hazards = set(
        candidate.get(
            "keyword_evidence",
            {},
        ).get(
            "process_safety",
            [],
        )
    )

    incident_hazards = set(
        incident.get(
            "keyword_evidence",
            {},
        ).get(
            "process_safety",
            [],
        )
    )

    return similarity >= 0.91 or bool(
        candidate_hazards & incident_hazards
    )


def next_incident_id(
    incidents: list[dict],
    year: int,
) -> str:
    prefix = f"PSI-{year}-"
    numbers = []

    for incident in incidents:
        incident_id = incident.get("incident_id", "")

        if not incident_id.startswith(prefix):
            continue

        try:
            numbers.append(
                int(
                    incident_id.rsplit("-", 1)[1]
                )
            )
        except ValueError:
            continue

    return f"{prefix}{max(numbers, default=0) + 1:04d}"


def recompute_incident_scores(
    incident: dict,
) -> None:
    scores = incident.setdefault("scores", {})
    sources = incident.get("sources", [])

    best_reliability = max(
        (
            source.get("reliability", 0)
            for source in sources
        ),
        default=scores.get(
            "source_reliability",
            0,
        ),
    )

    scores["source_reliability"] = best_reliability

    source_count = len(sources)

    scores["confidence"] = min(
        5,
        max(
            best_reliability,
            1 + min(source_count, 4),
        ),
    )

    scores["watch_score"] = weighted_watch_score(
        scores.get("source_reliability", 0),
        scores.get("process_safety_relevance", 0),
        scores.get("semiconductor_relevance", 0),
        scores.get("severity_potential", 0),
        scores.get("confidence", 0),
        incident.get("stream", "Cross-Industry"),
    )


def merge_candidate(
    candidate: dict,
    incidents: list[dict],
    run_time: str,
    settings: dict,
) -> tuple[str, dict]:
    for incident in incidents:
        if not same_incident(candidate, incident):
            continue

        changes = []
        known_urls = source_urls(incident)

        for source in candidate.get("sources", []):
            url = canonicalize_url(source.get("url", ""))

            if url and url not in known_urls:
                incident.setdefault("sources", []).append(source)
                known_urls.add(url)
                changes.append(
                    f"Added source: {source.get('domain') or source.get('source_name') or 'unknown'}"
                )

        for score_key in (
            "process_safety_relevance",
            "semiconductor_relevance",
            "severity_potential",
        ):
            old_value = incident.setdefault(
                "scores",
                {},
            ).get(
                score_key,
                0,
            )

            new_value = candidate.get(
                "scores",
                {},
            ).get(
                score_key,
                0,
            )

            if new_value > old_value:
                incident["scores"][score_key] = new_value
                changes.append(
                    f"Increased {score_key.replace('_', ' ')} score"
                )

        existing_summary = incident.get("summary", "")
        new_summary = candidate.get("summary", "")

        if new_summary and len(new_summary) > len(existing_summary):
            incident["summary"] = new_summary
            changes.append(
                "Expanded incident summary from a newly retrieved source"
            )

        evidence = incident.setdefault("keyword_evidence", {})

        for category, hits in candidate.get(
            "keyword_evidence",
            {},
        ).items():
            merged_hits = sorted(
                set(evidence.get(category, []))
                | set(hits)
            )

            if merged_hits != evidence.get(category, []):
                evidence[category] = merged_hits
                changes.append(
                    f"Added {category.replace('_', ' ')} evidence"
                )

        previous_review_status = incident.get(
            "review_status",
            "Under Review",
        )

        recompute_incident_scores(incident)

        new_review_status = review_status_for(
            incident["scores"].get(
                "source_reliability",
                0,
            ),
            settings,
        )

        incident["review_status"] = new_review_status

        if new_review_status != previous_review_status:
            changes.append(
                f"Review status changed from {previous_review_status} to {new_review_status}"
            )

        if changes:
            incident["last_updated"] = run_time
            incident["status"] = "Updated"
            incident.setdefault(
                "change_history",
                [],
            ).append(
                {
                    "timestamp": run_time,
                    "type": "updated",
                    "changes": sorted(set(changes)),
                }
            )

            return "updated", incident

        return "unchanged", incident

    incident = {
        "incident_id": next_incident_id(
            incidents,
            utc_now().year,
        ),
        "stream": candidate["stream"],
        "title": candidate["title"],
        "incident_date": None,
        "reported_at": candidate.get("reported_at"),
        "first_detected": run_time,
        "last_updated": run_time,
        "status": "New",
        "review_status": candidate["review_status"],
        "summary": candidate["summary"],
        "process_safety_concern": candidate["process_safety_concern"],
        "scores": candidate["scores"],
        "keyword_evidence": candidate["keyword_evidence"],
        "sources": candidate["sources"],
        "change_history": [
            {
                "timestamp": run_time,
                "type": "new",
                "changes": [
                    "Incident first detected by monitoring workflow"
                ],
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
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None


def archive_old_incidents(
    incidents: list[dict],
    lookback_days: int,
    run_time: str,
) -> list[str]:
    cutoff = utc_now() - timedelta(days=lookback_days)
    archived_ids = []

    for incident in incidents:
        dt = reported_datetime(incident)

        if (
            dt
            and dt < cutoff
            and incident.get("status") != "Archived"
        ):
            incident["status"] = "Archived"
            incident["last_updated"] = run_time
            incident.setdefault(
                "change_history",
                [],
            ).append(
                {
                    "timestamp": run_time,
                    "type": "archived",
                    "changes": [
                        f"Moved outside the rolling {lookback_days}-day window"
                    ],
                }
            )
            archived_ids.append(
                incident["incident_id"]
            )

    return archived_ids


def migrate_existing_incidents(
    incidents: list[dict],
    settings: dict,
) -> None:
    for incident in incidents:
        scores = incident.setdefault("scores", {})

        if "review_status" not in incident:
            incident["review_status"] = review_status_for(
                scores.get(
                    "source_reliability",
                    0,
                ),
                settings,
            )


def discover_query(
    query_config: dict,
    settings: dict,
    diagnostics: dict,
) -> list[dict]:
    query_name = query_config["name"]
    query = query_config["query"]

    discovered = []

    if settings.get("gdelt_enabled", True):
        try:
            results = fetch_gdelt(
                query,
                query_name,
                settings.get(
                    "gdelt_max_records_per_query",
                    50,
                ),
            )

            discovered.extend(results)

            diagnostics["providers"]["GDELT"]["queries_succeeded"] += 1
            diagnostics["providers"]["GDELT"]["articles_returned"] += len(results)

        except DiscoveryError as exc:
            diagnostics["providers"]["GDELT"]["queries_failed"] += 1
            diagnostics["providers"]["GDELT"]["failures"].append(str(exc))
            print(f"WARNING: {exc}", flush=True)

    if settings.get("google_news_rss_enabled", True):
        try:
            results = fetch_google_news_rss(
                query,
                query_name,
                settings.get(
                    "google_news_max_records_per_query",
                    50,
                ),
            )

            discovered.extend(results)

            diagnostics["providers"]["Google News RSS"]["queries_succeeded"] += 1
            diagnostics["providers"]["Google News RSS"]["articles_returned"] += len(results)

        except DiscoveryError as exc:
            diagnostics["providers"]["Google News RSS"]["queries_failed"] += 1
            diagnostics["providers"]["Google News RSS"]["failures"].append(str(exc))
            print(f"WARNING: {exc}", flush=True)

    return merge_discovered_articles(discovered)


def collect_candidates(
    settings: dict,
    scoring: dict,
    run_time: str,
    unknown_sources: dict,
) -> tuple[list[dict], dict]:
    diagnostics = {
        "generated_at": run_time,
        "queries_total": len(settings["queries"]),
        "articles_discovered": 0,
        "unique_articles": 0,
        "quick_screened_in": 0,
        "selected_for_enrichment": 0,
        "article_pages_enriched": 0,
        "confirmed_candidates": 0,
        "under_review_candidates": 0,
        "rejected_after_analysis": 0,
        "providers": {
            "GDELT": {
                "queries_succeeded": 0,
                "queries_failed": 0,
                "articles_returned": 0,
                "failures": [],
            },
            "Google News RSS": {
                "queries_succeeded": 0,
                "queries_failed": 0,
                "articles_returned": 0,
                "failures": [],
            },
        },
    }

    candidates = []
    global_seen = set()

    for query_config in settings["queries"]:
        print(
            f"\n=== {query_config['name']} ===",
            flush=True,
        )

        articles = discover_query(
            query_config,
            settings,
            diagnostics,
        )

        diagnostics["articles_discovered"] += len(articles)

        stream = query_config["stream"]
        shortlisted = []

        for article in articles:
            key = article_key(article)

            if key in global_seen:
                continue

            global_seen.add(key)

            quick_text = " ".join(
                [
                    article.get("title", ""),
                    article.get("description", ""),
                    article.get("source_name", ""),
                ]
            )

            pre_semiconductor, _ = score_semiconductor(
                quick_text,
                scoring,
            )

            pre_process, _ = score_process_safety(
                quick_text,
                scoring,
            )

            # Do not use source reliability as an early discard.
            # The targeted query provides a modest floor so blocked publishers
            # can still reach the Under Review queue.
            if stream == "Semiconductor":
                if pre_semiconductor == 0 and pre_process == 0:
                    continue
            elif pre_process == 0:
                continue

            reliability = score_reliability(
                article.get("source_domain", ""),
                scoring,
            )

            shortlisted.append(
                {
                    "article": article,
                    "pre_semiconductor": pre_semiconductor,
                    "pre_process": pre_process,
                    "reliability": reliability,
                }
            )

        diagnostics["quick_screened_in"] += len(shortlisted)

        shortlisted.sort(
            key=lambda item: (
                item["pre_process"],
                item["pre_semiconductor"],
                item["reliability"],
                item["article"].get("published_at") or "",
            ),
            reverse=True,
        )

        enrichment_limit = settings.get(
            "enrichment_limit_per_query",
            20,
        )

        selected = shortlisted[:enrichment_limit]

        diagnostics["selected_for_enrichment"] += len(selected)

        print(
            f"{len(articles)} unique provider results; "
            f"{len(shortlisted)} passed quick relevance; "
            f"{len(selected)} selected for enrichment.",
            flush=True,
        )

        contexts = {}

        if selected:
            with ThreadPoolExecutor(
                max_workers=settings.get(
                    "article_fetch_workers",
                    6,
                )
            ) as executor:
                future_map = {
                    executor.submit(
                        fetch_article_context,
                        item["article"].get("url", ""),
                    ): item
                    for item in selected
                }

                for future in as_completed(future_map):
                    item = future_map[future]
                    key = article_key(item["article"])

                    try:
                        context = future.result()
                    except Exception:
                        context = {
                            "description": "",
                            "text": "",
                            "final_url": item["article"].get(
                                "url",
                                "",
                            ),
                        }

                    contexts[key] = context

                    if context.get("description") or context.get("text"):
                        diagnostics["article_pages_enriched"] += 1

        for item in selected:
            article = item["article"]
            context = contexts.get(
                article_key(article),
                {
                    "description": "",
                    "text": "",
                    "final_url": article.get("url", ""),
                },
            )

            candidate = build_candidate(
                article,
                stream,
                scoring,
                settings,
                context,
                run_time,
                unknown_sources,
                item["pre_semiconductor"],
                item["pre_process"],
            )

            if candidate is None:
                diagnostics["rejected_after_analysis"] += 1
                continue

            candidates.append(candidate)

            if candidate["review_status"] == "Confirmed":
                diagnostics["confirmed_candidates"] += 1
            else:
                diagnostics["under_review_candidates"] += 1

    diagnostics["unique_articles"] = len(global_seen)

    provider_successes = sum(
        provider["queries_succeeded"]
        for provider in diagnostics["providers"].values()
    )

    if provider_successes == 0:
        raise RuntimeError(
            "All discovery providers failed. "
            "Existing incident data has been preserved."
        )

    candidates.sort(
        key=lambda item: (
            item["review_status"] == "Confirmed",
            item["scores"]["watch_score"],
            item.get("reported_at") or "",
        ),
        reverse=True,
    )

    return candidates, diagnostics


def create_report(
    incidents: list[dict],
    run_time: str,
    lookback_days: int,
    new_ids: list[str],
    updated_ids: list[str],
    archived_ids: list[str],
) -> dict:
    active = [
        incident
        for incident in incidents
        if incident.get("status") != "Archived"
    ]

    confirmed = [
        incident
        for incident in active
        if incident.get("review_status") == "Confirmed"
    ]

    under_review = [
        incident
        for incident in active
        if incident.get("review_status") == "Under Review"
    ]

    high_priority = [
        incident
        for incident in active
        if incident.get(
            "scores",
            {},
        ).get(
            "watch_score",
            0,
        ) >= 4.0
    ]

    return {
        "generated_at": run_time,
        "window_days": lookback_days,
        "counts": {
            "active": len(active),
            "confirmed": len(confirmed),
            "under_review": len(under_review),
            "new": len(new_ids),
            "updated": len(updated_ids),
            "archived": len(archived_ids),
            "high_priority": len(high_priority),
        },
        "new_incident_ids": sorted(set(new_ids)),
        "updated_incident_ids": sorted(set(updated_ids)),
        "archived_incident_ids": sorted(set(archived_ids)),
    }


def snapshot_history(
    database: dict,
    report: dict,
    diagnostics: dict,
    run_time: str,
) -> None:
    HISTORY_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    date_key = run_time[:10]

    save_json(
        HISTORY_DIR / f"{date_key}-incidents.json",
        database,
    )

    save_json(
        HISTORY_DIR / f"{date_key}-report.json",
        report,
    )

    save_json(
        HISTORY_DIR / f"{date_key}-diagnostics.json",
        diagnostics,
    )


def copy_public_data() -> None:
    PUBLIC_DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    for source_path, target_name in (
        (INCIDENTS_PATH, "incidents.json"),
        (REPORT_PATH, "latest_report.json"),
        (DIAGNOSTICS_PATH, "run_diagnostics.json"),
        (UNKNOWN_SOURCES_PATH, "unknown_sources.json"),
    ):
        if source_path.exists():
            shutil.copy2(
                source_path,
                PUBLIC_DATA_DIR / target_name,
            )


def main() -> None:
    settings = load_json(SETTINGS_PATH)
    scoring = load_json(SCORING_PATH)

    database = load_json(
        INCIDENTS_PATH,
        {
            "schema_version": 4,
            "last_run": None,
            "incidents": [],
        },
    )

    unknown_sources = load_json(
        UNKNOWN_SOURCES_PATH,
        {
            "schema_version": 1,
            "last_run": None,
            "sources": {},
        },
    )

    incidents = database.setdefault(
        "incidents",
        [],
    )

    migrate_existing_incidents(
        incidents,
        settings,
    )

    run_time = iso_now()

    print(
        "Process Safety Incident Watch v0.4",
        flush=True,
    )

    print(
        f"Run started: {run_time}",
        flush=True,
    )

    for incident in incidents:
        if incident.get("status") in {
            "New",
            "Updated",
        }:
            incident["status"] = "Active"

    # Collect everything before changing the stored database.
    # A total provider failure therefore cannot wipe or reset the dashboard.
    candidates, diagnostics = collect_candidates(
        settings,
        scoring,
        run_time,
        unknown_sources,
    )

    print(
        "\nDiscovery diagnostics:",
        flush=True,
    )

    print(
        json.dumps(
            diagnostics,
            indent=2,
        ),
        flush=True,
    )

    new_ids = []
    updated_ids = []

    for candidate in candidates:
        result, incident = merge_candidate(
            candidate,
            incidents,
            run_time,
            settings,
        )

        if result == "new":
            new_ids.append(
                incident["incident_id"]
            )

        elif result == "updated":
            updated_ids.append(
                incident["incident_id"]
            )

    archived_ids = archive_old_incidents(
        incidents,
        settings["lookback_days"],
        run_time,
    )

    incidents.sort(
        key=lambda item: (
            item.get("status") != "Archived",
            item.get("review_status") == "Confirmed",
            item.get(
                "scores",
                {},
            ).get(
                "watch_score",
                0,
            ),
            item.get("reported_at") or "",
        ),
        reverse=True,
    )

    database["schema_version"] = 4
    database["last_run"] = run_time

    unknown_sources["last_run"] = run_time

    report = create_report(
        incidents,
        run_time,
        settings["lookback_days"],
        new_ids,
        updated_ids,
        archived_ids,
    )

    save_json(
        INCIDENTS_PATH,
        database,
    )

    save_json(
        REPORT_PATH,
        report,
    )

    save_json(
        DIAGNOSTICS_PATH,
        diagnostics,
    )

    save_json(
        UNKNOWN_SOURCES_PATH,
        unknown_sources,
    )

    snapshot_history(
        database,
        report,
        diagnostics,
        run_time,
    )

    copy_public_data()

    print(
        "\nFinal report counts:",
        flush=True,
    )

    print(
        json.dumps(
            report["counts"],
            indent=2,
        ),
        flush=True,
    )

    print(
        "\nRun completed successfully.",
        flush=True,
    )


if __name__ == "__main__":
    main()
