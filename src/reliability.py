"""Conservative screening and review projections; never rewrite source history.

All automated findings are candidates. Publisher reputation is not verification,
and a repeated/syndicated article is not independent corroboration.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

VERSION = 1
BOILERPLATE = ("comprehensive up-to-date news coverage", "aggregated from sources all over the world")
COMPANIES = ("SK hynix", "Samsung Electronics", "Texas Instruments", "GlobalFoundries",
             "Micron", "TSMC", "Intel", "UMC", "SMIC", "Infineon", "Nexperia", "onsemi")
# Explicit mentions only. This is intentionally incomplete, not geocoding or a
# country inference from a publisher. Unlisted locations remain unknown.
LOCATIONS = ("Kiryat Gat", "Cheongju", "Icheon", "Hsinchu", "Taichung", "Tainan",
             "Greenville", "Mississippi", "Athens", "Saratov", "Afipsky", "Afipsk",
             "Krasnodar", "Oryol", "Tehran", "Hokkaido", "Oregon", "Singapore",
             "Israel", "Taiwan", "South Korea", "Japan", "Libya")
SEMI = (r"\b(?:semiconductors?|wafer(?:s| fab)?|fabs?|chipmakers?|chip (?:plant|factory)|"
        r"microelectronics|cleanroom|wet bench|optoelectronics)\b")
FACILITY = (r"\b(?:plant|factory|refinery|petrochemical|chemical (?:facility|site)|industrial|"
            r"gas (?:cabinet|room)|sub[- ]?fab|cleanroom|wet bench|laborator(?:y|ies)|university|"
            r"battery (?:plant|factory)|smelter|steelworks|metal foundry|foundry)\b")
EVENT = (r"\b(?:fires?|explosions?|blasts?|gas leak(?:s|ed)?|chemical leak(?:s|ed)?|"
         r"toxic (?:gas )?(?:release|leak)|gas release|chemical release|ruptur(?:e|ed)|"
         r"runaway reaction|thermal runaway|overpressure|evacuat(?:ion|ed)|"
         r"poison(?:ing|ed)|expos(?:ure|ed) to (?:toxic|gas|acid)|"
         r"(?:acid|fluorine|ammonia|chlorine|silane|NF3) (?:leak|exposure))\b")
BUSINESS = (r"\b(?:stocks?|shares?|etfs?|invest(?:ment|ing|ors?)|earnings|buyback|"
            r"market|price target|revenue|orders|capacity|portfolios?|returns?|bets?|buys?|xetfs?|index|acquisition|"
            r"announces? awards|supply chain|talent pipeline|income strategy|rebound|press release|tech exposure|semiconductor exposure|semiconductor boom)\b")
NONPHYSICAL = (r"\b(?:data|technology|intelligence|design|document|information|ip) leaks?\b|"
               r"\bfire[- ]sale\b|\b(?:fired|fires) (?:ceo|workers|employees)\b|"
               r"\b(?:cloud|ai|software|font|type) foundry\b|\bfoundry (?:vtt|game)\b|"
               r"\bfire (?:hundreds|dozens|drones|missiles)\b")
UNCERTAIN = r"\b(?:suspect(?:ed)?|alleg(?:ed|edly)|unconfirmed|preliminary|may|might|reportedly|possible|investigat\w*)\b"
DENIAL = r"\b(?:no (?:fire|leak|explosion)|den(?:y|ies|ied)|disputed|conflicting|false alarm)\b"
CONSEQUENCE = r"\b(?:kill(?:ed)?|died|dead|fatal\w*|injur\w*|hospital\w*|evacuat\w*|shutdown|shut down|shelter.in.place|off.site|damage\w*)\b"


def hits(text, terms):
    return [term for term in terms if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text or "", re.I)]


def clean_content(text):
    text = " ".join((text or "").split())
    return "" if any(x in text.lower() for x in BOILERPLATE) else text


def normalized_title(title):
    # Strip RSS publisher suffix, not meaningful incident wording.
    title = re.split(r"\s[-|]\s", title or "")[0]
    return " ".join(re.findall(r"\w+", title.lower()))


def safe_url(url):
    try:
        p = urlsplit(url or "")
        return bool(p.scheme in {"http", "https"} and p.hostname and not p.username and not p.password)
    except ValueError:
        return False


def canonical_url(url):
    if not safe_url(url):
        return ""
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "oc"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(q)), ""))


def parse_date(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def screen(title, description="", body=""):
    """Title/lead-first screening. Body/sidebar keywords cannot rescue business news.

    An explicit physical event plus industrial context is retained even when the
    article discusses financial consequences. Uncertain/denied events stay in
    review. No physical event is inferred solely from a chemical name.
    """
    lead = " ".join((title or "", clean_content(description)))
    companies = hits(lead, COMPANIES)
    semi = bool(re.search(SEMI, lead, re.I) or companies)
    facility = bool(re.search(FACILITY, lead, re.I))
    physical = bool(re.search(EVENT, lead, re.I))
    nonphysical = bool(re.search(NONPHYSICAL, lead, re.I))
    # Ambiguous financial "exposure", tech leaks, and figurative fire are not events.
    if re.search(r"\bfoundry\b", lead, re.I) and re.search(r"\b(?:restaurant|bar|nightclub|theatre|theater|art gallery|cafe|gaming|game)\b", lead, re.I):
        return {"decision": "Out of scope", "reason": "Unrelated foundry name; no industrial process context", "stream": None}
    if nonphysical and not re.search(r"\b(?:explosion|gas leak|chemical leak|toxic release|plant fire)\b", lead, re.I):
        return {"decision": "Out of scope", "reason": "Non-physical use of an incident term", "stream": None}
    if re.search(r"\b(?:vacant|abandoned|former|old|historic)\b.{0,35}\b(?:foundry|building)\b", lead, re.I) and not re.search(r"\b(?:toxic|hazmat|chemical release)\b", lead, re.I):
        return {"decision": "Out of scope", "reason": "Disused building fire; no industrial process event identified", "stream": None}
    if physical and (semi or facility):
        return {"decision": "Retain", "reason": "Physical incident signal with industrial context; verification required",
                "stream": "Semiconductor" if semi else "Cross-Industry"}
    if re.search(BUSINESS, lead, re.I) and not physical:
        return {"decision": "Out of scope", "reason": "Business/investment coverage without a physical incident", "stream": None}
    # A blocked or short title may omit context. Keep ambiguous event reports for review.
    if physical or ((semi or facility) and re.search(r"\b(?:leak|release|exposure|incident|accident|hazmat|toxic|hazard|killed|injured|dies|fatal)\b", lead, re.I)):
        return {"decision": "Needs context", "reason": "Insufficient evidence of event or industrial context", "stream": "Semiconductor" if semi else "Cross-Industry"}
    return {"decision": "Out of scope", "reason": "No physical process-safety event identified in title/lead", "stream": None}


def source_identity(source):
    return canonical_url(source.get("original_url") or source.get("url"))


def incident_urls(record):
    return {canonical_url(s.get(k)) for s in record.get("sources", []) for k in ("url", "original_url", "discovery_url") if canonical_url(s.get(k))}


def incident_entities(record):
    title = record.get("title", "")
    return set(hits(title, COMPANIES)), set(hits(title, LOCATIONS))


def match_records(a, b):
    """Only identical URLs auto-merge. Cross-publisher matches require review.

    Explicitly conflicting incident dates block even URL matches (a publisher may
    reuse a live page). Publication proximity is a candidate signal, never an
    incident date. Distinctive titles/entities prevent generic fire over-merging.
    """
    ad, bd = parse_date(a.get("incident_date")), parse_date(b.get("incident_date"))
    if ad and bd and ad.date() != bd.date():
        return None
    if incident_urls(a) & incident_urls(b):
        return {"kind": "same_report", "reason": "Same canonical source URL"}
    ap, bp = parse_date(a.get("reported_at")), parse_date(b.get("reported_at"))
    if not (ad and bd) and (not ap or not bp or abs((ap - bp).total_seconds()) > 7 * 86400):
        return None
    ac, al = incident_entities(a)
    bc, bl = incident_entities(b)
    if (ac and bc and ac.isdisjoint(bc)) or (al and bl and al.isdisjoint(bl)):
        return None
    at, bt = normalized_title(a.get("title")), normalized_title(b.get("title"))
    # A new leak after an explosion is not evidence of one incident.
    if re.search(r"\b(?:another|second|new|again|less than a week|days after)\b", at + " " + bt):
        return None
    shared_entity = bool(ac & bc or al & bl)
    shared_hazard = bool(set(re.findall(EVENT, at, re.I)) & set(re.findall(EVENT, bt, re.I)))
    similarity = SequenceMatcher(None, at, bt).ratio()
    if shared_entity and shared_hazard and (similarity >= .55 or (ac & bc and al & bl)):
        return {"kind": "possible_duplicate", "reason": "Shared named entity and hazard with nearby report dates; check event dates and source independence"}
    return None


def evidence_projection(record):
    """Keep claims verbatim with their source. Never turn an extract into a fact."""
    sources = []
    claims, consequences, uncertainties = [], [], []
    companies, locations = [], []
    for source in record.get("sources", []):
        s = dict(source)
        s["original_url"] = source.get("original_url") or (source.get("url") if "news.google.com" not in (source.get("url") or "") else None)
        s["link_type"] = "publisher" if s["original_url"] else "Google News redirect — publisher URL unresolved"
        s["retrieval_status"] = source.get("retrieval_status") or "Legacy: article retrieval not established"
        s["published_at"] = source.get("published_at")
        s["date_basis"] = source.get("date_basis") or ("Legacy GDELT date: discovery/publication uncertain" if "GDELT" in source.get("discovery_sources", []) else "Feed publication date")
        sources.append(s)
        text = source.get("title") or record.get("title", "")
        url = s.get("original_url") or source.get("url")
        claim = {"text": text, "source_url": url, "status": "Source-reported; not independently verified"}
        if text and claim not in claims:
            claims.append(claim)
        description = clean_content(source.get("description"))
        if description and normalized_title(description) != normalized_title(text):
            extract = {"text": description, "source_url": url, "status": "Source description; not independently verified"}
            claims.append(extract)
            if re.search(UNCERTAIN + "|" + DENIAL, description, re.I):
                uncertainties.append(extract)
            if re.search(CONSEQUENCE, description, re.I):
                consequences.append(extract)
        if re.search(CONSEQUENCE, text, re.I):
            consequences.append(claim)
        if re.search(UNCERTAIN + "|" + DENIAL, text, re.I):
            uncertainties.append(claim)
        for name in hits(text, COMPANIES):
            if not any(c["value"] == name for c in companies):
                companies.append({"value": name, "source_url": url, "basis": "Title mention; role unverified"})
        for name in hits(text, LOCATIONS):
            if not any(c["value"] == name for c in locations):
                locations.append({"value": name, "source_url": url, "basis": "Title mention; incident location unverified"})
    return {"sources": sources, "reported_claims": claims, "reported_consequences": consequences,
            "preliminary_or_disputed": uncertainties, "company_mentions": companies, "location_mentions": locations,
            "confirmed_facts": [], "tier": "Not assigned — methodology and evidence required"}


def validate_reviews(payload, records):
    """Fail closed for malformed review files; citations must belong to the record."""
    reviews = payload.get("reviews", {})
    by_id = {r["incident_id"]: r for r in records}
    if not isinstance(reviews, dict):
        raise ValueError("reviews must be an object keyed by incident ID")
    for ident, review in reviews.items():
        if ident not in by_id:
            raise ValueError(f"Unknown review incident ID: {ident}")
        if review.get("status") not in {"Needs review", "In review", "Reviewed", "Excluded"}:
            raise ValueError(f"Invalid review status for {ident}")
        if not review.get("reviewed_by") or not parse_date(review.get("reviewed_at")) or not review.get("notes"):
            raise ValueError(f"Review needs reviewer, timestamp and notes: {ident}")
        urls = incident_urls(by_id[ident])
        for field in ("confirmed_facts", "preliminary_or_disputed", "consequences"):
            for claim in review.get(field, []):
                if not claim.get("text") or canonical_url(claim.get("source_url")) not in urls:
                    raise ValueError(f"Invalid/missing evidence for {ident}: {field}")
        if review.get("confirmed_facts") and review.get("status") != "Reviewed":
            raise ValueError(f"Confirmed facts require a completed review: {ident}")
        for field in ("incident_date", "company", "location"):
            if review.get(field):
                if canonical_url(review.get(field + "_source_url")) not in urls:
                    raise ValueError(f"Missing evidence URL for {ident}: {field}")
                if field == "incident_date" and not parse_date(review[field]):
                    raise ValueError(f"Invalid incident date for {ident}")
    return reviews


def build_review_index(database, diagnostics, reviews=None, generated_at=None):
    records = database.get("incidents", [])
    manual = validate_reviews(reviews or {}, records)
    projected = []
    for record in records:
        item = {key: record.get(key) for key in ("incident_id", "title", "status", "reported_at", "incident_date", "first_detected", "last_updated", "change_history", "scores")}
        candidates = [screen(s.get("title", ""), s.get("description", "")) for s in record.get("sources", [])]
        candidates.append(screen(record.get("title", ""), clean_content(record.get("summary"))))
        decision = min(candidates, key=lambda s: {"Retain": 0, "Needs context": 1, "Out of scope": 2}[s["decision"]])
        item.update(evidence_projection(record))
        item["screening"] = decision
        item["stream"] = decision["stream"] or record.get("stream", "Cross-Industry")
        item["review_status"] = "Needs review"
        item["legacy_review_status"] = record.get("legacy_review_status", record.get("review_status"))
        item["legacy_status_note"] = "Legacy Confirmed was a publisher-score threshold, not independent verification" if item["legacy_review_status"] == "Confirmed" else None
        item["potential_lessons"] = potential_lessons(record.get("title", "")) if decision["decision"] != "Out of scope" else []
        item["possible_duplicates"] = []
        item["manual_review"] = manual.get(record["incident_id"])
        if item["manual_review"]:
            review = item["manual_review"]
            item["review_status"] = review["status"]
            for field in ("confirmed_facts", "incident_date", "company", "location"):
                if field in review:
                    item[field] = review[field]
            item["preliminary_or_disputed"] += review.get("preliminary_or_disputed", [])
            item["reported_consequences"] += review.get("consequences", [])
        item["window_basis"] = "Incident date" if item.get("incident_date") else "Publication/discovery date proxy — incident date unknown"
        projected.append(item)
    for i, a in enumerate(projected):
        if a["screening"]["decision"] == "Out of scope":
            continue
        for b in projected[i + 1:]:
            if b["screening"]["decision"] == "Out of scope":
                continue
            match = match_records(a, b)
            if match:
                for first, second in ((a, b), (b, a)):
                    first["possible_duplicates"].append({"incident_id": second["incident_id"], **match})
    return {"schema_version": VERSION, "generated_at": generated_at,
            "data_as_of": database.get("last_run"), "stale_after_days": 8,
            "raw_record_count": len(records), "incidents": projected,
            "diagnostics_as_of": diagnostics.get("generated_at"),
            "input_fingerprint": hashlib.sha256(str([(r["incident_id"], r.get("last_updated")) for r in records]).encode()).hexdigest()}


def potential_lessons(title):
    lessons = []
    if re.search(r"\b(?:leak|release|toxic|fluorine|acid|gas)\b", title, re.I):
        lessons.append("Review whether containment, gas detection, isolation and emergency arrangements address a comparable scenario in the fab.")
    if re.search(r"\b(?:fire|explosion|blast)\b", title, re.I):
        lessons.append("Consider the relevance of ignition control, hazardous inventory, equipment integrity and emergency response to fab operations.")
    if re.search(r"\b(?:runaway|reaction)\b", title, re.I):
        lessons.append("Consider whether comparable reactive-chemistry scenarios are covered in process hazard reviews.")
    return lessons or ["Establish the event mechanism and fab applicability before drawing lessons."]


def provider_health(diagnostics, previous=None):
    previous = previous or {}
    success = sum(p.get("queries_succeeded", 0) for p in diagnostics.get("providers", {}).values())
    failures = sum(p.get("queries_failed", 0) for p in diagnostics.get("providers", {}).values())
    status = "degraded" if success and failures else "healthy" if success else "unavailable" if failures else "disabled"
    for name, p in diagnostics.get("providers", {}).items():
        p["last_success_at"] = diagnostics["generated_at"] if p.get("queries_succeeded") else (previous.get("providers", {}).get(name, {}).get("last_success_at") or (previous.get("generated_at") if previous.get("providers", {}).get(name, {}).get("queries_succeeded") else None))
        p["status"] = "degraded" if p.get("queries_succeeded") and p.get("queries_failed") else "healthy" if p.get("queries_succeeded") else "unavailable" if p.get("queries_failed") else "disabled"
    diagnostics["health"] = status
    diagnostics["last_success_at"] = diagnostics["generated_at"] if success else (previous.get("last_success_at") or (previous.get("generated_at") if any(p.get("queries_succeeded") for p in previous.get("providers", {}).values()) else None))
    return diagnostics
