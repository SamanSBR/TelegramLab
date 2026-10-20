import asyncio
import json
import socket
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, MessageEntityTextUrl, MessageEntityUrl

from telegramlab import cli
from telegramlab.cli import build_parser
from telegramlab.research.references import extract_message_references, normalize_reference_url
from telegramlab.sources.registry import add_source, get_source
from telegramlab.telegram.explorer import explore_source
from telegramlab.telegram.inspector import FloodWaitInspectionError, InspectionError, NetworkInspectionError


def _channel():
    entity = Channel(id=444, title="Reference Source", photo=None,
                     date=datetime(2025, 1, 1, tzinfo=UTC), broadcast=True)
    entity.username = "ReferenceSource"
    return entity


def _message(message_id, text, entities=None):
    return SimpleNamespace(id=message_id, raw_text=text, entities=entities or [])


class FakeClient:
    def __init__(self, messages=(), *, entity=None, entity_error=None, message_error=None):
        self.entity = entity or _channel()
        self.messages = list(messages)
        self.entity_error = entity_error
        self.message_error = message_error
        self.request_limit = None
        self.consumed = 0
        self.resolved = None

    async def get_entity(self, target):
        self.resolved = target
        if self.entity_error:
            raise self.entity_error
        return self.entity

    def iter_messages(self, entity, *, limit):
        self.request_limit = limit
        async def generate():
            for message in self.messages:
                if self.message_error:
                    raise self.message_error
                self.consumed += 1
                yield message
        return generate()


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("reference exploration must not make external requests")
    monkeypatch.setattr(socket, "create_connection", no_network)


def test_reference_normalization_classifies_links_and_redacts_secrets():
    assert normalize_reference_url("https://t.me/ChannelName/123") == (
        "message_permalink", "https://t.me/channelname/123", "telegram", "telegram_message_permalink"
    )


def test_telegram_reference_shapes_do_not_assume_username_type():
    assert normalize_reference_url("https://t.me/ExampleChannel/123") == (
        "message_permalink", "https://t.me/examplechannel/123", "telegram", "telegram_message_permalink"
    )
    assert normalize_reference_url("https://t.me/s/ExampleChannel") == (
        "channel_reference", "https://t.me/s/examplechannel", "telegram", "telegram_channel_reference"
    )
    assert normalize_reference_url("https://t.me/ExampleChannel") == (
        "unknown_reference", "https://t.me/examplechannel", "telegram", "telegram_unknown_reference"
    )


def test_reference_extraction_counts_duplicates_and_preserves_types():
    text = (
        "@ExampleUser @ExampleUser https://t.me/ExampleChannel "
        "https://t.me/s/ExampleChannel https://t.me/ExampleChannel/99 "
        "https://github.com/OpenAI/Repo/issues/4 https://gitlab.com/team/project/-/tree/main "
        "https://example.org/sub?token=do-not-leak"
    )
    observations, omitted = extract_message_references(
        text, None, origin_message_id=11, discovered_at="2026-01-01T12:00:00+00:00"
    )
    grouped = {(item.reference_type, item.normalized_target): item for item in observations}
    assert grouped[("user_mention", "@exampleuser")].occurrence_count == 2
    assert ("unknown_reference", "https://t.me/examplechannel") in grouped
    assert ("channel_reference", "https://t.me/s/examplechannel") in grouped
    assert ("message_permalink", "https://t.me/examplechannel/99") in grouped
    assert ("github_repository", "https://github.com/openai/repo") in grouped
    assert ("gitlab_repository", "https://gitlab.com/team/project") in grouped
    subscription = next(item for item in observations if item.reference_type == "subscription_url_candidate")
    assert "do-not-leak" not in subscription.normalized_target
    assert "%5BREDACTED%5D" in subscription.normalized_target
    assert omitted == 0


def test_ambiguous_telegram_username_references_remain_distinct():
    observations, _ = extract_message_references(
        "@ExampleChannel https://t.me/ExampleChannel", None,
        origin_message_id=3, discovered_at="2026-01-01T12:00:00+00:00",
    )
    mention = next(item for item in observations if item.reference_type == "user_mention")
    link = next(item for item in observations if item.reference_type == "unknown_reference")
    assert mention.normalized_target == link.normalized_target.rsplit("/", 1)[-1].join(("@", ""))
    assert mention.candidate_source_type != link.candidate_source_type


def test_url_entities_unicode_and_entity_text_links_are_explored():
    text = "🔎 https://github.com/Owner/Repo then hidden"
    visible_url = "https://github.com/Owner/Repo"
    offset = len("🔎 ".encode("utf-16-le")) // 2
    length = len(visible_url.encode("utf-16-le")) // 2
    entities = [
        MessageEntityUrl(offset=offset, length=length),
        MessageEntityTextUrl(offset=0, length=2, url="https://gitlab.com/team/project"),
    ]
    observations, _ = extract_message_references(
        text, entities, origin_message_id=4, discovered_at="2026-01-01T12:00:00+00:00"
    )
    assert {item.normalized_target for item in observations} == {
        "https://github.com/owner/repo", "https://gitlab.com/team/project"
    }


def test_private_invite_links_are_omitted():
    observations, omitted = extract_message_references(
        "Invite https://t.me/+SecretInviteCode", None,
        origin_message_id=7, discovered_at="2026-01-01T12:00:00+00:00",
    )
    assert observations == []
    assert omitted == 1


def test_explore_counts_occurrences_and_provenance_without_following_urls():
    messages = [
        _message(10, "https://github.com/OpenAI/Repo https://github.com/openai/repo"),
        _message(11, "Again @ExampleUser and https://t.me/ExampleChannel/22"),
    ]
    client = FakeClient(messages)
    result = asyncio.run(explore_source(client, "@ReferenceSource", progress=None))
    github = next(item for item in result.references if item["reference_type"] == "github_repository")
    assert github["occurrence_count"] == 2
    assert [item["origin_message_id"] for item in github["provenance"]] == [10]
    assert result.messages_inspected == 2
    assert result.source_identity["telegram_id"] == "444"
    assert result.total_occurrences == 4
    assert client.request_limit == 30
    assert not hasattr(client, "join_channel")


def test_exploration_limit_is_hard_capped():
    messages = [_message(i + 1, "nothing here") for i in range(130)]
    client = FakeClient(messages)
    with pytest.raises(InspectionError, match="between 1 and 100"):
        asyncio.run(explore_source(client, "@ReferenceSource", limit=101, progress=None))
    result = asyncio.run(explore_source(client, "@ReferenceSource", limit=100, progress=None))
    assert result.messages_inspected == 100
    assert client.request_limit == 100
    assert client.consumed == 100


def test_progress_reports_four_real_stages():
    progress = []
    asyncio.run(explore_source(FakeClient([_message(1, "@ExampleUser")]),
                               "@ReferenceSource", progress=progress.append))
    assert progress == [
        "[1/4] Resolving Telegram source",
        "[2/4] Fetching recent accessible messages",
        "[3/4] Extracting and normalizing references",
        "[4/4] Summarizing and optionally saving candidates",
    ]


def test_registry_migration_and_reference_save_preserve_metadata(tmp_path):
    from telegramlab.sources.registry import save_reference_discoveries

    path = tmp_path / "sources.db"
    origin, _ = add_source(path, provider="telegram", source_type="channel",
                           identifier="@ReferenceSource", platform_id="444",
                           display_name="Manual source", notes="origin notes")
    repo, _ = add_source(path, provider="github", source_type="repository",
                         identifier="https://github.com/openai/repo", notes="manual repo notes")
    discovery = {
        "reference_type": "github_repository",
        "normalized_target": "https://github.com/openai/repo",
        "origin_message_id": 25,
        "discovered_at": "2026-01-01T12:00:00+00:00",
        "occurrence_count": 2,
        "candidate_provider": "github",
        "candidate_source_type": "repository",
        "candidate_identifier": "https://github.com/openai/repo",
    }
    saved = save_reference_discoveries(
        path, origin_source_id="444", origin_identifier="@ReferenceSource",
        origin_source_type="channel",
        origin_title="New upstream title", discoveries=[discovery],
    )
    assert saved["origin_source_registry_id"] == origin.id
    assert saved["candidate_sources_created"] == 0
    assert saved["provenance_records_saved"] == 1
    assert get_source(path, origin.id).display_name == "Manual source"
    assert get_source(path, origin.id).notes == "origin notes"
    assert get_source(path, repo.id).notes == "manual repo notes"
    with sqlite3.connect(path) as db:
        versions = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
        assert versions == {1, 2}
        rows = db.execute("SELECT origin_source_id, origin_message_id, reference_type, normalized_target, occurrence_count, evidence_reference FROM source_references").fetchall()
        assert rows == [("444", 25, "github_repository", "https://github.com/openai/repo", 2, "telegram:444/25")]
        before = db.execute("SELECT discovered_at FROM source_references").fetchone()[0]

    discovery["occurrence_count"] = 3
    discovery["discovered_at"] = "2026-01-02T12:00:00+00:00"
    save_reference_discoveries(
        path, origin_source_id="444", origin_identifier="@ReferenceSource",
        origin_source_type="channel",
        origin_title="Changed upstream title", discoveries=[discovery],
    )
    with sqlite3.connect(path) as db:
        count, latest_discovery = db.execute(
            "SELECT COUNT(*), discovered_at FROM source_references"
        ).fetchone()
        occurrence_count = db.execute("SELECT occurrence_count FROM source_references").fetchone()[0]
    assert count == 1
    assert latest_discovery == before
    assert occurrence_count == 3
    assert get_source(path, origin.id).display_name == "Manual source"


def test_saving_ambiguous_username_kinds_does_not_merge_candidates(tmp_path):
    from telegramlab.sources.registry import save_reference_discoveries

    path = tmp_path / "sources.db"
    rows = []
    for ref_type, candidate_type in (("user_mention", "telegram_user_mention"),
                                     ("unknown_reference", "telegram_unknown_reference")):
        rows.append({
            "reference_type": ref_type, "normalized_target": "@ExampleChannel" if ref_type == "user_mention" else "https://t.me/examplechannel",
            "origin_message_id": 1, "discovered_at": "2026-01-01T00:00:00+00:00", "occurrence_count": 1,
            "candidate_provider": "telegram", "candidate_source_type": candidate_type,
            "candidate_identifier": "@ExampleChannel",
        })
    save_reference_discoveries(path, origin_source_id="444", origin_identifier="@ReferenceSource",
                               origin_source_type="channel",
                               origin_title="Source", discoveries=rows)
    with sqlite3.connect(path) as db:
        candidates = db.execute("SELECT source_type, identifier, status FROM sources WHERE id != 1 ORDER BY id").fetchall()
    assert candidates == [
        ("telegram_user_mention", "@examplechannel", "candidate"),
        ("telegram_unknown_reference", "@examplechannel", "candidate"),
    ]


def test_registry_explorer_cli_json(monkeypatch, tmp_path, capsys):
    result = asyncio.run(explore_source(FakeClient([_message(8, "@ExampleUser")]),
                                        "@ReferenceSource", progress=None))
    async def mocked(*args, **kwargs):
        return result
    monkeypatch.setattr(cli, "_explore_with_existing_session", mocked)
    monkeypatch.setenv("TELEGRAMLAB_SOURCES_DB_PATH", str(tmp_path / "sources.db"))
    args = build_parser().parse_args(["explore", "@ReferenceSource", "--json"])
    assert cli._run_explore(args) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["completion_status"] == "completed"
    assert document["references"][0]["provenance"][0]["origin_message_id"] == 8


def test_explore_save_registry_integration(tmp_path):
    client = FakeClient([_message(32, "@ExampleUser https://github.com/OpenAI/Repo")])
    result = asyncio.run(explore_source(client, "@ReferenceSource", progress=None,
                                        registry_path=tmp_path / "sources.db", save=True))
    assert result.registry_summary["candidate_sources_created"] == 2
    assert result.registry_summary["provenance_records_saved"] == 2
    with sqlite3.connect(tmp_path / "sources.db") as db:
        candidates = db.execute("SELECT status, discovery_method, notes FROM sources WHERE id != 1 ORDER BY id").fetchall()
        assert candidates == [
            ("candidate", "referenced_by_message", None),
            ("candidate", "referenced_by_message", None),
        ]
        assert db.execute("SELECT COUNT(*) FROM source_references").fetchone()[0] == 2


def test_explore_maps_network_and_flood_wait_errors():
    with pytest.raises(NetworkInspectionError, match="network request failed"):
        asyncio.run(explore_source(FakeClient(entity_error=TimeoutError()), "@ReferenceSource", progress=None))
    with pytest.raises(FloodWaitInspectionError, match="12 seconds"):
        asyncio.run(explore_source(FakeClient([_message(1, "text")], message_error=FloodWaitError(None, capture=12)),
                                   "@ReferenceSource", progress=None))


def test_external_references_are_never_fetched(monkeypatch):
    import urllib.request
    def denied(*args, **kwargs):
        raise AssertionError("external URLs must never be followed")
    monkeypatch.setattr(urllib.request, "urlopen", denied)
    result = asyncio.run(explore_source(
        FakeClient([_message(1, "https://example.org/page https://github.com/Org/Repo")]),
        "@ReferenceSource", progress=None,
    ))
    assert result.unique_references == 2


def test_json_and_output_never_include_private_invite_or_query_secret():
    result = asyncio.run(explore_source(
        FakeClient([_message(1, "https://t.me/+InviteSecret https://example.org/sub?token=DoNotPrint")]),
        "@ReferenceSource", progress=None,
    ))
    serialized = json.dumps(result.as_dict())
    assert "InviteSecret" not in serialized
    assert "DoNotPrint" not in serialized
    assert "%5BREDACTED%5D" in serialized
    assert result.warnings
