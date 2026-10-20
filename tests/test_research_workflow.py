import asyncio
import json
import socket
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon import functions, types
from telethon.errors import FloodWaitError

from telegramlab import cli
from telegramlab.research import workflow
from telegramlab.sources.registry import add_source, get_source, initialize_database


def channel(number=1, name="PublicOne"):
    return types.Channel(id=number, title=f"Channel {number}", photo=None,
                         date=datetime(2026, 1, 1, tzinfo=UTC), broadcast=True, username=name)


def message(number=10, text="vless://candidate https://t.me/OtherChannel/25", source=1):
    return SimpleNamespace(id=number, raw_text=text, entities=[], date=datetime(2026, 1, 2, tzinfo=UTC),
                           peer_id=types.PeerChannel(source), media=None)


class Client:
    def __init__(self, channels=None, messages=None, *, fail_source=None, flood_source=None,
                 substitute_id=None, duplicate=False):
        self.channels = channels if channels is not None else [channel()]
        self.messages = messages if messages is not None else [message()]
        self.fail_source = fail_source
        self.flood_source = flood_source
        self.substitute_id = substitute_id
        self.duplicate = duplicate
        self.requests = []
        self.lookups = []
        self.iter_limits = []
        self.connected = False

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return True

    async def disconnect(self):
        self.connected = False

    async def get_entity(self, target):
        self.lookups.append(target)
        if target == self.flood_source:
            raise FloodWaitError(None, capture=9)
        if target == self.fail_source:
            raise TimeoutError()
        item = next((item for item in self.channels if item.username.casefold() == str(target).casefold()), self.channels[0])
        if self.substitute_id is not None:
            return channel(self.substitute_id, item.username)
        return item

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.contacts.SearchRequest):
            chats = self.channels * 2 if self.duplicate else self.channels
            return SimpleNamespace(chats=chats, users=[])
        if isinstance(request, functions.channels.GetFullChannelRequest):
            return SimpleNamespace(full_chat=SimpleNamespace(about="Public", participants_count=10,
                                                             linked_chat_id=None), chats=[])
        if isinstance(request, functions.messages.SearchGlobalRequest):
            return SimpleNamespace(chats=self.channels, messages=self.messages)
        raise AssertionError(f"Unexpected request: {request!r}")

    def iter_messages(self, entity, *, limit):
        self.iter_limits.append(limit)
        async def generate():
            for item in self.messages:
                if item.peer_id.channel_id == entity.id:
                    yield item
        return generate()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network call"))


@pytest.fixture
def paths(tmp_path):
    registry = tmp_path / "sources.db"
    initialize_database(registry)
    return registry, tmp_path / "content.db", tmp_path / "research.db"


def run(client, paths, query="VLESS", **kwargs):
    registry, content, history = paths
    return asyncio.run(workflow.run_research(client, query, registry_path=registry,
                                             content_path=content, history_path=history,
                                             progress=None, **kwargs))


def test_default_discovery_dedup_no_optional_reads_or_writes(paths):
    client = Client(duplicate=True)
    result = run(client, paths)
    assert len(result.sources) == 1
    assert len(result.sources[0].discovery_evidence) == 1
    assert result.sources[0].inspected is False
    assert result.sources[0].exploration is None
    assert result.evidence_summary["sources_discovered"] == 1
    assert len(client.requests) == 1
    assert len(client.iter_limits) == 0
    assert not paths[1].exists() and not paths[2].exists()
    with sqlite3.connect(paths[0]) as db:
        assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 0


@pytest.mark.parametrize("inspect,explore,content", [
    (False, False, False), (True, False, False), (False, True, False),
    (False, False, True), (True, True, False), (True, False, True),
    (False, True, True), (True, True, True),
])
def test_all_optional_stage_combinations(paths, inspect, explore, content):
    client = Client()
    result = run(client, paths, inspect=inspect, explore=explore, content=content)
    assert result.evidence_summary["sources_inspected"] == int(inspect)
    assert result.evidence_summary["sources_explored"] == int(explore)
    assert result.evidence_summary["content_results"] == int(content)
    assert len(client.iter_limits) == int(inspect) + int(explore)
    assert sum(isinstance(request, functions.messages.SearchGlobalRequest)
               for request in client.requests) == int(content)
    assert result.completion_status == "completed"


def test_inspection_exploration_content_and_bounded_sources(paths):
    client = Client(channels=[channel(1, "PublicOne"), channel(2, "PublicTwo")],
                    messages=[message(), message(11, "ss://candidate", 2)])
    result = run(client, paths, limit=10, max_sources=1, inspect=True, explore=True, content=True)
    assert len(result.sources) == 2
    first, second = result.sources
    assert first.inspected and first.inspection["metadata"]["telegram_id"] == "1"
    assert first.inspection["inspected_at"]
    assert first.exploration["references"][0]["provenance"][0]["origin_message_id"] == 10
    assert second.inspected is False and second.exploration is None
    assert result.evidence_summary["messages_observed"] == 1
    assert result.evidence_summary["vless_candidates"] == 1
    assert result.evidence_summary["reference_occurrences"] >= 1
    assert result.content_results[0].message_url == "https://t.me/PublicOne/10"
    assert client.iter_limits == [10, 10]
    assert sum(isinstance(x, functions.messages.SearchGlobalRequest) for x in client.requests) == 1
    assert result.completion_status == "completed"


def test_partial_failure_and_id_reassignment(paths):
    client = Client(substitute_id=99)
    result = run(client, paths, inspect=True, explore=True)
    assert result.completion_status == "partial"
    assert not result.sources[0].inspected
    assert result.sources[0].exploration is None
    assert len(result.errors) == 2
    assert client.iter_limits == []
    second = run(Client(fail_source="publicone"), paths, inspect=True, content=True)
    assert second.completion_status == "partial"
    assert second.evidence_summary["content_results"] == 1


def test_empty_sources_still_content_and_not_proof_of_absence(paths):
    result = run(Client(channels=[], messages=[]), paths, content=True)
    assert result.sources == []
    assert result.content_results == []
    assert any("does not prove" in value for value in result.warnings)
    assert result.completion_status == "completed"


def test_persistence_notes_history_and_local_evidence(paths):
    original, _ = add_source(paths[0], provider="telegram", source_type="channel",
                             identifier="@PublicOne", platform_id="1", notes="Keep this note",
                             status="active", display_name="Manual title")
    result = run(Client(), paths, save=True, inspect=True, explore=True, content=True)
    assert result.workflow_id == 1
    assert get_source(paths[0], original.id).notes == "Keep this note"
    assert get_source(paths[0], original.id).display_name == "Manual title"
    assert get_source(paths[0], original.id).status == "active"
    with sqlite3.connect(paths[0]) as db:
        assert db.execute("SELECT count(*) FROM inspection_runs").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM source_references").fetchone()[0] >= 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert workflow.read_history(paths[2])[0]["workflow_id"] == 1
    local = run(None, paths, "VLESS", local=True)
    assert local.evidence_summary["content_results"] == 1
    assert local.sources[0].inspected is False
    assert local.mode == "local"
    assert result.content_results[0].message_url == local.content_results[0].message_url


def test_flood_wait_stops_later_operations_and_saves_partial_history(paths):
    client = Client(flood_source="publicone")
    result = run(client, paths, inspect=True, explore=True, content=True, save=True)
    assert result.completion_status == "partial"
    assert "9 seconds" in result.errors[0]
    assert not any(isinstance(x, functions.messages.SearchGlobalRequest) for x in client.requests)
    assert result.workflow_id == 1
    assert workflow.read_history(paths[2])[0]["completion_status"] == "partial"


def test_validation_offline_no_recreation_and_cancellation(paths, tmp_path, monkeypatch):
    with pytest.raises(workflow.ResearchError):
        run(None, paths, local=True, inspect=True)
    with pytest.raises(workflow.ResearchError):
        run(None, paths, local=True, max_sources=11)
    absent = (tmp_path / "absent.db", tmp_path / "content.db", tmp_path / "history.db")
    with pytest.raises(workflow.ResearchError, match="will not recreate"):
        run(None, absent, local=True)
    assert not absent[0].exists()
    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(workflow, "search_public", cancel)
    with pytest.raises(asyncio.CancelledError):
        run(Client(), paths)
    assert not paths[2].exists()


def test_cli_json_and_missing_session_offline(paths, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAMLAB_SOURCES_DB_PATH", str(paths[0]))
    monkeypatch.setenv("TELEGRAMLAB_CONTENT_DB_PATH", str(paths[1]))
    monkeypatch.setenv("TELEGRAMLAB_RESEARCH_DB_PATH", str(paths[2]))
    monkeypatch.setenv("TELEGRAMLAB_SESSION_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(cli, "create_client", lambda *_: pytest.fail("client constructed"))
    assert cli.main(["research", "VLESS", "--local", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "local"
    assert cli.main(["research", "VLESS", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["completion_status"] == "failed"


def test_task7_progress_numbering(paths):
    from telegramlab.telegram.content import search_content
    progress = []
    asyncio.run(search_content(Client(), "VLESS", index_path=paths[1], progress=progress.append))
    assert [item[:5] for item in progress] == ["[1/4]", "[2/4]", "[3/4]", "[4/4]"]
    saved_progress = []
    def record(item):
        if item.startswith("[4/4]"):
            assert paths[1].exists()
        saved_progress.append(item)
    asyncio.run(search_content(Client(), "VLESS", index_path=paths[1], save=True, progress=record))
    assert "Saved 1" in saved_progress[-1]


def test_discovery_evidence_dedup_keeps_distinct_methods():
    from telegramlab.telegram.search import SearchResult
    base = dict(provider="telegram", telegram_id="1", username="publicone",
                display_name="Public", entity_type="channel", description=None,
                participant_count=None, verified=None, search_query="VLESS",
                discovered_at="2026-01-01T00:00:00+00:00")
    one = SearchResult(**base, discovery_method="telegram_public_search")
    two = SearchResult(**base, discovery_method="local_registry", origin="local")
    sources = workflow._deduplicate([one, two], 10)
    assert len(sources) == 1
    assert {x["discovery_method"] for x in sources[0].discovery_evidence} == {
        "telegram_public_search", "local_registry"}
    missing_id = SearchResult(**{**base, "telegram_id": None}, discovery_method="local_registry", origin="local")
    assert len(workflow._deduplicate([one, missing_id], 10)) == 1
    conflicting = SearchResult(**{**base, "telegram_id": "2"}, discovery_method="telegram_public_search")
    assert len(workflow._deduplicate([one, conflicting], 10)) == 2


def test_search_flood_wait_records_failed_history(paths, monkeypatch):
    async def flood(*args, **kwargs):
        from telegramlab.telegram.inspector import FloodWaitInspectionError
        raise FloodWaitInspectionError(17)
    monkeypatch.setattr(workflow, "search_public", flood)
    result = run(Client(), paths, save=True)
    assert result.completion_status == "failed"
    assert "17 seconds" in result.errors[0]
    assert workflow.read_history(paths[2])[0]["completion_status"] == "failed"


def test_history_path_protection_and_cli_live_json(paths, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAMLAB_SOURCES_DB_PATH", str(paths[0]))
    monkeypatch.setenv("TELEGRAMLAB_CONTENT_DB_PATH", str(paths[1]))
    monkeypatch.setenv("TELEGRAMLAB_RESEARCH_DB_PATH", str(paths[0]))
    with pytest.raises(workflow.ResearchError, match="separate"):
        asyncio.run(workflow.run_research(Client(), "VLESS", save=True,
                                          registry_path=paths[0], content_path=paths[1],
                                          history_path=paths[0]))
    monkeypatch.setenv("TELEGRAMLAB_RESEARCH_DB_PATH", str(paths[2]))
    session = tmp_path / "mock.session"
    session.touch()
    monkeypatch.setenv("TELEGRAMLAB_SESSION_PATH", str(session))
    monkeypatch.setenv("TG_API_ID", "123")
    monkeypatch.setenv("TG_API_HASH", "mock")
    client = Client()
    monkeypatch.setattr(cli, "create_client", lambda *_: client)
    assert cli.main(["research", "VLESS", "--inspect", "--explore", "--content",
                     "--limit", "10", "--max-sources", "1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sources"][0]["inspected"] is True
    assert payload["content_results"][0]["message_url"] == "https://t.me/PublicOne/10"
    assert client.connected is False
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["research", "VLESS", "--max-sources", "11"])
