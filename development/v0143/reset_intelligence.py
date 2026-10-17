"""Public, attributed reset evidence. Never calls a model or controls execution."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from orchestration_models import epoch, iso, now_iso

SOURCE_WEIGHTS = {1: 1.0, 2: .8, 3: .45, 4: .15}
SOURCE_REGISTRY = [
    {"id": "openai-status", "tier": 1, "url": "https://status.openai.com", "fetch": "PUBLIC_JSON"},
    {"id": "openai-docs", "tier": 1, "url": "https://learn.chatgpt.com/docs/automations", "fetch": "MANUAL_PUBLIC_EXCERPT"},
    {"id": "openai-announcements", "tier": 1, "url": "https://openai.com", "fetch": "MANUAL_PUBLIC_EXCERPT"},
    {"id": "tibo-public", "tier": 2, "url": "https://x.com/thsottiaux", "fetch": "MANUAL_PUBLIC_EXCERPT"},
    {"id": "trusted-tracker", "tier": 3, "url": "https://github.com/yuenovaw/tibo-codex-reset-watcher", "fetch": "MANUAL_PUBLIC_EXCERPT"},
    {"id": "community", "tier": 4, "url": "https://www.reddit.com", "fetch": "MANUAL_PUBLIC_EXCERPT"},
]
SIGNAL_TYPES = {"NATURAL_WINDOW_RESET", "WEEKLY_NATURAL_RESET", "EXCEPTIONAL_GLOBAL_RESET",
                "BANKED_RESET", "PROMOTIONAL_RESET", "INCIDENT_COMPENSATION", "LIMIT_CHANGE", "UNKNOWN_RESET_SIGNAL"}


def canonical_url(value):
    parsed = urlsplit(str(value))
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("PUBLIC_HTTPS_SOURCE_REQUIRED")
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def source_tier(url, author):
    p = urlsplit(url)
    if p.hostname in {"openai.com", "status.openai.com", "help.openai.com", "developers.openai.com", "learn.chatgpt.com", "platform.openai.com"}:
        return 1
    if p.hostname in {"x.com", "twitter.com"} and p.path.lower().startswith("/thsottiaux/status/"):
        return 2
    if p.hostname == "github.com" and p.path.startswith(("/yuenovaw/tibo-codex-reset-watcher", "/RyuuMeow/Will-Tibo-Reset")):
        return 3
    return 4


def classify(text):
    s = text.casefold()
    if not any(word in s for word in ("codex", "quota", "usage limit", "rate limit", "额度")):
        return "UNRELATED"
    if re.search(r"\b(no|not|won't|will not|never)\b.{0,40}\breset|\breset\b.{0,20}\b(not|never)\b|不会.{0,8}重置", s):
        return "LIMIT_DISCUSSION"
    if re.search(r"\breset(?:ting)?\b|重置", s):
        if re.search(r"\b(may|might|maybe|could|if)\b|可能|也许", s):
            return "STRONG_HINT"
        if re.search(r"\b(will|within|tomorrow|in \d+|give us)\b|明天|小时后", s):
            return "SCHEDULED_RESET_ANNOUNCEMENT"
        if re.search(r"\b(have reset|has reset|limits (?:were|are) reset|reset (?:all|the|usage|codex))\b|已.{0,5}重置", s):
            return "EXPLICIT_RESET_ANNOUNCEMENT"
        return "STRONG_HINT"
    if re.search(r"incident|outage|degraded|故障", s):
        return "INCIDENT_RESPONSE"
    if re.search(r"coming|👀|hint|maybe|可能", s):
        return "WEAK_HINT"
    return "LIMIT_DISCUSSION"


class ResetSignalSource:
    def __init__(self, weights=None):
        self.weights = dict(SOURCE_WEIGHTS if weights is None else weights)

    def fetch(self):
        return []  # public excerpts are supplied explicitly, no logged-in scraping

    def normalize(self, record):
        text = str(record.get("rawText", "")).strip()[:8000]
        if not text:
            raise ValueError("PUBLIC_TEXT_REQUIRED")
        url = canonical_url(record.get("sourceUrl", ""))
        published = epoch(record.get("publishedAt"))
        author = str(record.get("sourceAuthor", "unknown"))[:160]
        tier = source_tier(url, author)
        classification = classify(text)
        reset_type = "UNKNOWN_RESET_SIGNAL"
        if classification in {"EXPLICIT_RESET_ANNOUNCEMENT", "SCHEDULED_RESET_ANNOUNCEMENT", "STRONG_HINT"}:
            reset_type = "EXCEPTIONAL_GLOBAL_RESET"
            if "banked" in text.lower(): reset_type = "BANKED_RESET"
            elif "promotion" in text.lower(): reset_type = "PROMOTIONAL_RESET"
            elif "compensat" in text.lower(): reset_type = "INCIDENT_COMPENSATION"
        elif classification == "LIMIT_DISCUSSION": reset_type = "LIMIT_CHANGE"
        effective = record.get("effectiveTimeEstimate")
        if not effective:
            match = re.search(r"(?:within|in|give us)\s+(\d+)\s+hours?", text, re.I)
            if match: effective = published + int(match[1]) * 3600
            elif classification == "EXPLICIT_RESET_ANNOUNCEMENT": effective = published
        raw_hash = hashlib.sha256(" ".join(text.casefold().split()).encode()).hexdigest()
        origin = record.get("originUrl")
        # Original-source URL merges reposts, but reposts cannot acquire its tier.
        canonical = canonical_url(origin) if origin else url
        confirmed = tier <= 2 and classification in {"EXPLICIT_RESET_ANNOUNCEMENT", "SCHEDULED_RESET_ANNOUNCEMENT"}
        return {"id": hashlib.sha256((url+str(published)+raw_hash).encode()).hexdigest()[:24],
                "signalType": reset_type, "publishedAt": iso(published), "observedAt": now_iso(), "sourceUrl": url,
                "sourceAuthor": author, "rawTextHash": raw_hash, "rawText": text,
                "classification": classification, "confidence": "HIGH" if confirmed else "LOW",
                "effectiveTimeEstimate": iso(effective) if effective is not None else None,
                "expiry": iso(record.get("expiry") or published+72*3600),
                "expiresAt": iso(record.get("expiry") or published+72*3600),
                "canonicalEventId":hashlib.sha256(canonical.encode()).hexdigest()[:24],
                "relevance":"UNRELATED" if classification=='UNRELATED' else 'QUOTA_RELATED',
                "lifecycleState":"EXPIRED" if epoch(record.get('expiry') or published+72*3600)<=epoch(now_iso()) else 'ACTIVE',
                "announcementClaim": confirmed, "officialConfirmation": False, "sourceVerification": "USER_SUPPLIED_UNVERIFIED", "sourceTier": tier,
                "canonicalOrigin": canonical, "ingestion": "USER_PUBLIC_EXCERPT"}

    def scoreFreshness(self, signal, now):
        if epoch(signal["publishedAt"]) > now or epoch(signal["expiry"]) <= now:
            return 0.0
        return max(0.0, 1 - (now-epoch(signal["publishedAt"]))/ (72*3600))

    def scoreReliability(self, signal):
        return float(self.weights[int(signal["sourceTier"])])

    def deduplicate(self, signals, now):
        # Exact excerpt hash can connect a repost without an origin link. No additive voting.
        groups, hashes = [], {}
        for signal in signals:
            group = next((g for g in groups if signal["canonicalOrigin"] in g["origins"] or signal["rawTextHash"] in g["hashes"]), None)
            if group is None:
                group = {"sources": [], "origins": set(), "hashes": set()}
                groups.append(group)
            group["origins"].add(signal["canonicalOrigin"])
            group["hashes"].add(signal["rawTextHash"])
            if not any(s["id"] == signal["id"] for s in group["sources"]):
                group["sources"].append(signal)
        # Connected repost chains may have bridged two existing groups.
        changed = True
        while changed:
            changed = False
            for i, a in enumerate(groups):
                for j in range(i+1, len(groups)):
                    b = groups[j]
                    if a["origins"] & b["origins"] or a["hashes"] & b["hashes"]:
                        a["origins"] |= b["origins"]; a["hashes"] |= b["hashes"]
                        a["sources"].extend(b["sources"]); groups.pop(j); changed = True; break
                if changed: break
        result = []
        for group in groups:
            best = max(group["sources"], key=lambda s: (self.scoreReliability(s), epoch(s["publishedAt"])))
            freshness = self.scoreFreshness(best, now)
            event = {**best, "canonicalEvent": hashlib.sha256(min(group["origins"]).encode()).hexdigest()[:24],
                     "sources": group["sources"], "reliabilityWeight": self.scoreReliability(best),
                     "freshness": freshness, "expired": freshness == 0,
                     "personalDelivery": "UNKNOWN", "independentEvidenceCount": 1}
            event["label"] = "Confirmed announcement" if best["officialConfirmation"] and freshness > 0 else "Prediction signal"
            event.update(expiresAt=best['expiry'],canonicalEventId=event['canonicalEvent'],
                         relevance='UNRELATED' if best['classification']=='UNRELATED' else 'QUOTA_RELATED',
                         lifecycleState='EXPIRED' if freshness==0 else 'ACTIVE',
                         supersededSourceIds=[s['id'] for s in group['sources'] if s['id']!=best['id']])
            event['sources']=[{**s,'observedAt':s.get('observedAt'),'expiresAt':s['expiry'],'canonicalEventId':event['canonicalEvent'],
                               'relevance':'UNRELATED' if s['classification']=='UNRELATED' else 'QUOTA_RELATED',
                               'lifecycleState':'EXPIRED' if epoch(s['expiry'])<=now else 'ACTIVE' if s['id']==best['id'] else 'SUPERSEDED'} for s in group['sources']]
            result.append(event)
        return sorted(result, key=lambda s: s["publishedAt"], reverse=True)


class TiboPublicSignalProvider(ResetSignalSource):
    """Public-excerpt ingestion only. X API credential collection deliberately unsupported."""


class OpenAIStatusSignalProvider(ResetSignalSource):
    URL = "https://status.openai.com/api/v2/incidents.json"

    def fetch(self):
        req = Request(self.URL, headers={"User-Agent": "QuotaLab/0.9 PublicSignals", "Accept": "application/json"})
        with urlopen(req, timeout=8) as response:
            payload = response.read(1024*1024+1)
        if len(payload) > 1024*1024:
            raise ValueError("SIGNAL_RESPONSE_TOO_LARGE")
        events = []
        for incident in json.loads(payload).get("incidents", [])[:100]:
            updates = incident.get("incident_updates", [])
            text = str(incident.get("name", "")) + "\n" + "\n".join(str(u.get("body", "")) for u in updates[:5])
            if not any(w in text.casefold() for w in ("codex", "quota", "rate limit", "额度")):
                continue
            signal = self.normalize({"rawText": text, "publishedAt": incident["created_at"],
                                     "sourceUrl": "https://status.openai.com/incidents/"+str(incident["id"]),
                                     "sourceAuthor": "OpenAI Status"})
            signal["ingestion"] = "PUBLIC_STATUS_JSON"
            signal["sourceVerification"] = "PUBLIC_SOURCE_FETCHED"
            signal["officialConfirmation"] = signal["announcementClaim"]
            events.append(signal)
        return events


class ResetSignalProvider(ResetSignalSource):
    def forecast(self, events, now):
        eligible = [e for e in events if self.scoreFreshness(e, now) > 0 and e["signalType"] in
                    {"EXCEPTIONAL_GLOBAL_RESET", "BANKED_RESET", "PROMOTIONAL_RESET", "INCIDENT_COMPENSATION"}
                    and (not e.get("effectiveTimeEstimate") or now <= epoch(e["effectiveTimeEstimate"]) <= now+86400)]
        supporting = [e["canonicalEvent"] for e in eligible if e["classification"] in
                      {"EXPLICIT_RESET_ANNOUNCEMENT", "SCHEDULED_RESET_ANNOUNCEMENT", "STRONG_HINT", "WEAK_HINT"}]
        strong = any(e.get("officialConfirmation") and e["classification"] == "SCHEDULED_RESET_ANNOUNCEMENT" for e in eligible)
        opposing = [e["canonicalEvent"] for e in events if e.get("freshness", 0)>0 and re.search(r"no.{0,20}reset|won't.{0,20}reset", e.get("rawText", ""), re.I)]
        return {"generatedAt": iso(now), "horizon": "NEXT_24_HOURS", "probability": None,
                "confidenceBand": None, "assessment": "CONFLICTING_EVIDENCE" if supporting and opposing else "HIGH_SIGNAL" if strong else "UNCONFIRMED_SIGNAL" if supporting else "INSUFFICIENT_EVIDENCE",
                "evidence": "Qualitative rules; no calibrated probability model", "supportingSignals": supporting,
                "opposingSignals": opposing,
                "freshness": max((e["freshness"] for e in eligible), default=0),
                "forecastTarget": "EXCEPTIONAL_WEEKLY_GLOBAL", "isPromise": False}


def calibrate_delivery(signal, before, after, natural_reset=None):
    """Observed personal delivery, not a causal assertion. Same bucket, bracket effective time."""
    try:
        effective = epoch(signal["effectiveTimeEstimate"])
        if before["bucketId"] != after["bucketId"] or not epoch(before["observedAt"]) <= effective <= epoch(after["observedAt"]):
            return "UNKNOWN"
        if epoch(after["observedAt"])-epoch(before["observedAt"]) > 3600 or natural_reset is None or abs(epoch(natural_reset)-effective)<3600:
            return "UNKNOWN"
        delta = float(after["remainingPercent"])-float(before["remainingPercent"])
        return "DELIVERED" if delta >= 5 else "NOT_OBSERVED"
    except (KeyError, ValueError, TypeError):
        return "UNKNOWN"
