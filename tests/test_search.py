import asyncio
import json
import socket
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, User

from telegramlab import cli
from telegramlab.sources.registry import add_relationship, add_source, get_source
from telegramlab.telegram.inspector import FloodWaitInspectionError, NetworkInspectionError
from telegramlab.telegram.search import SearchError, search_public


def channel(id=1, username="AlphaNews", title="Alpha News", group=False):
    return Channel(id=id, title=title, photo=None, date=datetime(2025, 1, 1, tzinfo=UTC),
                   broadcast=not group, megagroup=group, username=username)


class Client:
    def __init__(self, chats=(), entity=None, error=None):
        self.chats = chats
        self.entity = entity
        self.error = error
        self.calls = []

    async def get_entity(self, username):
        self.calls.append(("entity", username))
        if self.error:
            raise self.error
        return self.entity

    async def __call__(self, request):
        self.calls.append(("search", request.q, request.limit))
        if self.error:
            raise self.error
        return SimpleNamespace(chats=self.chats, users=[User(id=99, first_name="Private")])


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network request"))


def run(tmp_path, client=None, query="Alpha", **kwargs):
    return asyncio.run(search_public(client, query, registry_path=tmp_path / "sources.db",
                                     progress=None, **kwargs))


def test_exact_username_and_user_classification(tmp_path):
    client = Client(entity=User(id=5, first_name="Ada", username="AdaUser"))
    response = run(tmp_path, client, "@AdaUser", save=True)
    assert client.calls == [("entity", "adauser")]
    assert response.results[0].entity_type == "user"
    assert response.results[0].telegram_id == "5"
    assert response.candidates_saved == 0
    with sqlite3.connect(tmp_path / "sources.db") as db:
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0


@pytest.mark.parametrize("query", ["Alpha", "Alph", "VPN Iran"])
def test_literal_name_partial_and_multiword(tmp_path, query):
    client = Client(chats=[channel(title="Alpha News")])
    response = run(tmp_path, client, query)
    assert client.calls == [("search", query, 20)]
    assert response.results[0].search_query == query
    assert response.results[0].description is None
    assert response.results[0].participant_count is None


def test_group_and_channel_filtering(tmp_path):
    client = Client(chats=[channel(), channel(2, "AlphaGroup", "Alpha Group", True)])
    assert [x.entity_type for x in run(tmp_path, client, entity_type="group").results] == ["group"]
    assert [x.entity_type for x in run(tmp_path, client, entity_type="channel").results] == ["channel"]


def test_keyword_results_exclude_chat_without_public_username(tmp_path):
    client = Client(chats=[channel(1, None, "Private Group", True),
                           channel(2, "PublicGroup", "Public Group", True)])
    result = run(tmp_path, client, "Group")
    assert [item.username for item in result.results] == ["publicgroup"]


def test_local_search_offline_fields_and_type(tmp_path):
    path = tmp_path / "sources.db"
    add_source(path, provider="telegram", source_type="channel", identifier="@AlphaNews",
               display_name="AlPhA", description="Network", research_category="vpn")
    add_source(path, provider="website", source_type="website", identifier="https://example.org",
               display_name="Other")
    for term in ("alpha", "@ALPHANEWS", "network", "vpn", "telegram"):
        response = run(tmp_path, None, term, mode="local", entity_type="channel")
        assert len(response.results) == 1
        assert response.results[0].origin == "local"


def test_local_progress_has_no_telegram_stage(tmp_path):
    messages = []
    asyncio.run(search_public(None, "Alpha", mode="local", registry_path=tmp_path / "sources.db",
                              progress=messages.append))
    assert [message[:5] for message in messages] == ["[1/3]", "[2/3]", "[3/3]"]
    assert not any("Telegram" in message for message in messages)


def test_both_merges_by_id_or_normalized_username(tmp_path):
    path = tmp_path / "sources.db"
    saved, _ = add_source(path, provider="telegram", source_type="channel",
                          identifier="@ALPHANEWS", platform_id=1, display_name="Local")
    result = run(tmp_path, Client(chats=[channel()]), mode="both")
    assert len(result.results) == 1
    assert result.results[0].origin == "both"
    assert result.results[0].registry_source_id == saved.id


def test_dedup_by_stable_id_and_ambiguous_name(tmp_path):
    result = run(tmp_path, Client(chats=[channel(1, "FirstOne", "Same"),
                                         channel(1, "SecondOne", "Same"),
                                         channel(2, "ThirdOne", "Same")]))
    assert len(result.results) == 2
    assert {x.telegram_id for x in result.results} == {"1", "2"}


def test_ambiguous_username_without_id_is_not_merged(tmp_path):
    items = [channel(1, "SameName"), channel(2, "SameName"), channel(None, "SameName")]
    result = run(tmp_path, Client(chats=items))
    assert len(result.results) == 3
    assert "ambiguous" in result.results[-1].warnings[0]


def test_username_conflict_is_reported_and_not_saved(tmp_path):
    path = tmp_path / "sources.db"
    existing, _ = add_source(path, provider="telegram", source_type="channel",
                             identifier="@AlphaNews", platform_id=999, notes="keep")
    result = run(tmp_path, Client(chats=[channel()]), save=True)
    assert result.candidates_saved == 0
    assert "conflicts" in result.results[0].warnings[0]
    assert get_source(path, existing.id).notes == "keep"


def test_registered_id_with_different_type_is_not_saved(tmp_path):
    path = tmp_path / "sources.db"
    add_source(path, provider="telegram", source_type="group",
               identifier="@OldGroup", platform_id=1)
    result = run(tmp_path, Client(chats=[channel()]), save=True)
    assert result.candidates_saved == 0
    assert result.results[0].warnings


def test_empty_invalid_limits(tmp_path):
    assert run(tmp_path, Client()).results == []
    for query in ("", "   ", "a\nboom", "x" * 257):
        with pytest.raises(SearchError):
            run(tmp_path, Client(), query)
    for limit in (0, 101):
        with pytest.raises(SearchError):
            run(tmp_path, Client(), limit=limit)


def test_save_candidate_provenance_duplicate_and_existing_metadata(tmp_path):
    path = tmp_path / "sources.db"
    existing, _ = add_source(path, provider="telegram", source_type="channel",
                             identifier="@AlphaNews", display_name="Manual", notes="keep",
                             status="active", platform_id=1)
    client = Client(chats=[channel(), channel(2, "SecondNews", "Second")])
    first = run(tmp_path, client, "Alpha", save=True)
    second = run(tmp_path, client, "Alpha", save=True)
    assert first.candidates_saved == 1
    assert second.candidates_saved == 0
    assert get_source(path, existing.id).display_name == "Manual"
    assert get_source(path, existing.id).notes == "keep"
    assert get_source(path, existing.id).status == "active"
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM search_discoveries").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM sources WHERE status='candidate'").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_search_migration_preserves_existing_relationships(tmp_path):
    path = tmp_path / "sources.db"
    first, _ = add_source(path, provider="telegram", source_type="channel", identifier="@AlphaNews")
    second, _ = add_source(path, provider="telegram", source_type="channel", identifier="@OtherNews")
    relation, _ = add_relationship(path, source_id=first.id, related_source_id=second.id,
                                   relationship_type="mentions", evidence_reference="manual:1")
    run(tmp_path, Client(chats=[channel(3, "ThirdNews")]), save=True)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT evidence_reference FROM source_relationships WHERE id=?",
                          (relation.id,)).fetchone()[0] == "manual:1"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_flood_wait_and_timeout(tmp_path):
    with pytest.raises(FloodWaitInspectionError, match="12 seconds"):
        run(tmp_path, Client(error=FloodWaitError(None, capture=12)))
    with pytest.raises(NetworkInspectionError):
        run(tmp_path, Client(error=TimeoutError()))


def test_missing_session_and_offline_local(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAMLAB_SOURCES_DB_PATH", str(tmp_path / "sources.db"))
    monkeypatch.setenv("TELEGRAMLAB_SESSION_PATH", str(tmp_path / "missing.session"))
    monkeypatch.setattr(cli, "create_client", lambda *a: pytest.fail("client constructed"))
    assert cli.main(["search", "Alpha", "--local", "--json"]) == 0
    assert cli.main(["search", "Alpha", "--json"]) == 1


def test_cli_json_and_limit(monkeypatch, tmp_path, capsys):
    async def fake(*args, **kwargs):
        return await search_public(Client(chats=[channel()]), "Alpha",
                                   registry_path=tmp_path / "sources.db", progress=None)
    monkeypatch.setattr(cli, "_search_with_existing_session", fake)
    assert cli.main(["search", "Alpha", "--json", "--limit", "10"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["username"] == "alphanews"
    assert payload["results"][0]["search_query"] == "Alpha"
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["search", "Alpha", "--limit", "101"])
