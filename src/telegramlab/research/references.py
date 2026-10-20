"""Normalize Telegram and external references without following their URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from telegramlab.extractors.links import extract_url_occurrences

_TELEGRAM_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me", "www.telegram.me"})
_MENTION_RE = re.compile(r"(?<![\w])@([A-Za-z0-9_]{5,32})\b")
_SUBSCRIPTION_PATH_RE = re.compile(r"(?:^|/)(?:sub|subscribe|subscription)(?:/|$)", re.IGNORECASE)
_SENSITIVE_QUERY_RE = re.compile(r"(?i)(?:token|secret|password|passwd|credential|auth|api[_-]?key|api[_-]?hash|access[_-]?key|signature|^sig$|^key$|^user(?:name)?$|^start(?:app|group)?$|^payload$)")


@dataclass(frozen=True, slots=True)
class ReferenceOccurrence:
    reference_type: str
    normalized_target: str
    origin_message_id: int
    discovered_at: str
    occurrence_count: int
    candidate_provider: str
    candidate_source_type: str
    candidate_identifier: str

    def as_dict(self) -> dict[str, object]:
        return {
            "reference_type": self.reference_type,
            "normalized_target": self.normalized_target,
            "origin_message_id": self.origin_message_id,
            "discovered_at": self.discovered_at,
            "occurrence_count": self.occurrence_count,
            "candidate_provider": self.candidate_provider,
            "candidate_source_type": self.candidate_source_type,
            "candidate_identifier": self.candidate_identifier,
        }


def _safe_url(raw: str, *, drop_query: bool = False) -> str | None:
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.casefold()
    host = (parts.hostname or "").casefold()
    if not scheme or not host:
        return None
    port_text = f":{port}" if port else ""
    netloc = host + port_text  # omits URL user/password fields
    query = ""
    if not drop_query:
        safe_pairs = [
            (key, "[REDACTED]" if _SENSITIVE_QUERY_RE.search(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        query = urlencode(safe_pairs, doseq=True)
    return urlunsplit((scheme, netloc, parts.path.rstrip("/"), query, ""))


def _telegram_reference(url: str) -> tuple[str, str, str, str] | None:
    """Return reference type, normalized target, candidate source type and ID."""
    safe = _safe_url(url, drop_query=True)
    if safe is None:
        return None
    parts = urlsplit(safe)
    if (parts.hostname or "").casefold() not in _TELEGRAM_HOSTS:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments:
        return None
    first = segments[0]
    if first.casefold() == "joinchat" or first.startswith("+"):
        return ("private_invite_omitted", "", "", "")
    if first.casefold() == "c" and len(segments) >= 3 and segments[1].isdigit() and segments[2].isdigit():
        target = f"https://t.me/c/{segments[1]}/{segments[2]}"
        return ("message_permalink", target, "telegram", "telegram_message_permalink")
    if first.casefold() == "s" and len(segments) >= 2 and re.fullmatch(r"[A-Za-z0-9_]{5,32}", segments[1]):
        username = segments[1].casefold()
        if len(segments) >= 3 and segments[2].isdigit():
            target = f"https://t.me/s/{username}/{segments[2]}"
            return ("message_permalink", target, "telegram", "telegram_message_permalink")
        target = f"https://t.me/s/{username}"
        return ("channel_reference", target, "telegram", "telegram_channel_reference")
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", first):
        username = first.casefold()
        if len(segments) >= 2 and segments[1].isdigit():
            target = f"https://t.me/{username}/{segments[1]}"
            return ("message_permalink", target, "telegram", "telegram_message_permalink")
        target = f"https://t.me/{username}"
        # A username URL alone does not tell us whether it is a user or channel.
        return ("unknown_reference", target, "telegram", "telegram_unknown_reference")
    target = safe.replace(parts.netloc, "t.me", 1)
    return ("unknown_reference", target, "telegram", "telegram_unknown_reference")


def normalize_reference_url(raw: str) -> tuple[str, str, str, str] | None:
    """Classify a URL and return type, safe target, provider and candidate type."""
    telegram = _telegram_reference(raw)
    if telegram is not None:
        return telegram
    safe = _safe_url(raw)
    if safe is None:
        return None
    parts = urlsplit(safe)
    if parts.scheme.casefold() == "tg":
        return ("unknown_reference", safe, "telegram", "telegram_unknown_reference")
    host = (parts.hostname or "").casefold()
    path_segments = [segment for segment in parts.path.split("/") if segment]
    query_keys = {key.casefold() for key, _ in parse_qsl(urlsplit(raw if "://" in raw else f"https://{raw}").query)}
    if host in {"github.com", "www.github.com"} and len(path_segments) >= 2:
        owner = path_segments[0].casefold()
        repository = path_segments[1].casefold().removesuffix(".git")
        target = f"https://github.com/{owner}/{repository}"
        return ("github_repository", target, "github", "repository",)  # candidate identifier is target
    if host in {"gitlab.com", "www.gitlab.com"} and len(path_segments) >= 2:
        project_path = path_segments
        if "-" in project_path:
            project_path = project_path[:project_path.index("-")]
        else:
            project_path = project_path[:2]
        target = "https://gitlab.com/" + "/".join(segment.casefold() for segment in project_path)
        return ("gitlab_repository", target, "gitlab", "repository")
    if _SUBSCRIPTION_PATH_RE.search(parts.path) or query_keys.intersection({"sub", "subscribe", "subscription"}):
        return ("subscription_url_candidate", safe, "website", "subscription_url")
    if parts.scheme.casefold() in {"http", "https"}:
        return ("external_website", safe, "website", "external_website")
    return ("unknown_reference", safe, "website", "unknown_reference")


def _candidate_identifier(reference_type: str, normalized_target: str) -> str:
    if reference_type == "user_mention":
        return normalized_target
    if reference_type == "channel_reference":
        return "@" + normalized_target.rsplit("/", 1)[-1]
    if reference_type in {"github_repository", "gitlab_repository", "external_website",
                          "subscription_url_candidate", "message_permalink"}:
        return normalized_target
    return normalized_target


def extract_message_references(
    text: str | None,
    entities: list[object] | None,
    *,
    origin_message_id: int,
    discovered_at: str,
) -> tuple[list[ReferenceOccurrence], int]:
    """Extract occurrences from one message; URLs are never visited.

    Returns observations grouped by type/target for that message and a count of
    private invitation links omitted from output and persistence.
    """
    text = text or ""
    url_occurrences = extract_url_occurrences(text, entities)
    counts: dict[tuple[str, str, str, str, str], int] = {}
    url_spans: list[tuple[int, int]] = []
    omitted_invites = 0
    for raw_url, start, end in url_occurrences:
        url_spans.append((start, end))
        classified = normalize_reference_url(raw_url)
        if classified is None:
            continue
        reference_type, target, provider, candidate_type = classified
        if reference_type == "private_invite_omitted":
            omitted_invites += 1
            continue
        key = (reference_type, target, provider, candidate_type, _candidate_identifier(reference_type, target))
        counts[key] = counts.get(key, 0) + 1

    for match in _MENTION_RE.finditer(text):
        start = len(text[:match.start()].encode("utf-16-le")) // 2
        end = len(text[:match.end()].encode("utf-16-le")) // 2
        if any(start < url_end and end > url_start for url_start, url_end in url_spans):
            continue
        username = match.group(1).casefold()
        target = "@" + username
        key = ("user_mention", target, "telegram", "telegram_user_mention", target)
        counts[key] = counts.get(key, 0) + 1

    observations = [
        ReferenceOccurrence(
            reference_type=reference_type,
            normalized_target=target,
            origin_message_id=origin_message_id,
            discovered_at=discovered_at,
            occurrence_count=count,
            candidate_provider=provider,
            candidate_source_type=candidate_type,
            candidate_identifier=identifier,
        )
        for (reference_type, target, provider, candidate_type, identifier), count in sorted(counts.items())
    ]
    return observations, omitted_invites
