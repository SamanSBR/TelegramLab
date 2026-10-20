import asyncio
import hashlib
import json
import socket
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.errors import ApiIdInvalidError, ChannelPrivateError, FloodWaitError, UsernameNotOccupiedError
from telethon.tl.types import Channel, Chat, MessageEntityTextUrl, MessageEntityUrl

from telegramlab.cli import _run_inspect, build_parser, main
from telegramlab.extractors.links import extract_urls
from telegramlab.sources.registry import RegistryError, add_source, get_source, save_inspection_record
from telegramlab.telegram.client import MissingSessionError, create_client, require_existing_session, session_file_path
from telegramlab.telegram.inspector import (
    FloodWaitInspectionError,
    InspectionError,
    InvalidSourceError,
    NetworkInspectionError,
    inspect_source,
    normalize_target,
    validate_limit,
)


def _channel(**attrs):
    entity = Channel(id=12345, title="Research Channel", photo=None,
                     date=datetime(2025, 1, 1, tzinfo=UTC), broadcast=True,
                     verified=True, participants_count=321)
    entity.username = "ExampleChannel"
    for key, value in attrs.items():
        setattr(entity, key, value)
    return entity


def _message(text, *, entities=None, date=None):
    return SimpleNamespace(raw_text=text, entities=entities or [],
                           date=date or datetime(2025, 1, 2, tzinfo=UTC))


class FakeClient:
    def __init__(self, entity=None, messages=(), full_chat=None, entity_error=None, full_error=None):
        self.entity = entity or _channel()
        self.messages = list(messages)
        self.full_chat = full_chat or SimpleNamespace(about="Public description", participants_count=321,
                                                     linked_chat_id=None)
        self.entity_error = entity_error
        self.full_error = full_error
        self.request_limit = None
        self.consumed = 0

    async def get_entity(self, username):
        self.resolved_username = username
        if self.entity_error:
            raise self.entity_error
        return self.entity

    async def __call__(self, request):
        if self.full_error:
            raise self.full_error
        linked = SimpleNamespace(full_chat=self.full_chat, chats=[])
        return linked

    def iter_messages(self, entity, *, limit):
        self.request_limit = limit

        async def generate():
            for message in self.messages:
                self.consumed += 1
                yield message
        return generate()


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("tests must not make network requests")
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_valid_public_channel_and_aggregates():
    messages = [
        _message("Visit https://example.org and https://t.me/OtherChannel; vless://secret-value"),
        _message("ss://another-secret", date=datetime(2025, 1, 3, tzinfo=UTC)),
    ]
    client = FakeClient(messages=messages)
    progress = []
    result = asyncio.run(inspect_source(client, "@ExampleChannel", progress=progress.append))
    assert client.resolved_username == "examplechannel"
    assert result.metadata["telegram_id"] == "12345"
    assert result.metadata["title"] == "Research Channel"
    assert result.metadata["source_type"] == "channel"
    assert result.findings["messages_inspected"] == 2
    assert result.findings["messages_containing_urls"] == 1
    assert result.findings["messages_containing_telegram_references"] == 1
    assert result.findings["messages_containing_external_urls"] == 1
    assert result.findings["messages_containing_vless_candidates"] == 1
    assert result.findings["messages_containing_shadowsocks_candidates"] == 1
    assert len(progress) == 4
    assert all("secret-value" not in item for item in json.dumps(result.as_dict()).splitlines())
    assert "secret-value" not in json.dumps(result.as_dict())


def test_invalid_username_and_non_public_target():
    for target in ("@x", "https://t.me/+invitecode", "https://example.org/channel"):
        with pytest.raises(InvalidSourceError):
            normalize_target(target)


def test_unknown_username_is_mapped_to_clear_error():
    client = FakeClient(entity_error=UsernameNotOccupiedError(None))
    with pytest.raises(InvalidSourceError, match="unknown or invalid"):
        asyncio.run(inspect_source(client, "@ExampleChannel"))


def test_inaccessible_source_has_clear_error():
    client = FakeClient(entity_error=ChannelPrivateError(None))
    from telegramlab.telegram.inspector import InaccessibleSourceError
    with pytest.raises(InaccessibleSourceError, match="inaccessible"):
        asyncio.run(inspect_source(client, "@ExampleChannel"))


def test_invalid_telegram_credentials_have_clear_error():
    from telegramlab.telegram.inspector import InvalidCredentialsError
    client = FakeClient(entity_error=ApiIdInvalidError(None))
    with pytest.raises(InvalidCredentialsError, match="credentials"):
        asyncio.run(inspect_source(client, "@ExampleChannel"))


def test_missing_session_is_reported_and_client_not_created(tmp_path, monkeypatch):
    missing = tmp_path / "missing-session"
    with pytest.raises(MissingSessionError, match="session file is missing"):
        require_existing_session(missing)
    monkeypatch.setenv("TELEGRAMLAB_SESSION_PATH", str(missing))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    assert main(["inspect", "@ExampleChannel"]) == 1


def test_session_resolution_appends_suffix_without_replacing_base_suffix(tmp_path):
    assert session_file_path(tmp_path / "telegram_lab.v2") == tmp_path / "telegram_lab.v2.session"


def test_create_client_refuses_missing_session_before_telethon_construction(tmp_path, monkeypatch):
    import telegramlab.telegram.client as client_module
    called = False
    def forbidden_constructor(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not construct a replacement session")
    monkeypatch.setattr(client_module, "TelegramClient", forbidden_constructor)
    from telegramlab.core.config import Settings
    with pytest.raises(MissingSessionError):
        create_client(Settings(database_path=tmp_path / "old.db", sources_database_path=tmp_path / "sources.db",
                               session_path=tmp_path / "missing", api_id=1, api_hash="hidden"))
    assert not called
    assert not (tmp_path / "missing.session").exists()


def test_unauthorized_session_stops_without_interactive_start(tmp_path, monkeypatch):
    from telegramlab import cli
    session_base = tmp_path / "existing"
    session_base.with_suffix(".session").touch()
    monkeypatch.setenv("TELEGRAMLAB_SESSION_PATH", str(session_base))
    monkeypatch.setenv("TG_API_ID", "123")
    monkeypatch.setenv("TG_API_HASH", "not-printed")

    class UnauthorizedClient:
        async def connect(self):
            return None
        async def is_user_authorized(self):
            return False
        async def start(self):
            raise AssertionError("interactive start must never be called")
        async def disconnect(self):
            return None

    monkeypatch.setattr(cli, "create_client", lambda settings: UnauthorizedClient())
    with pytest.raises(InspectionError, match="interactive login is disabled"):
        asyncio.run(cli._inspect_with_existing_session("@ExampleChannel", 30, False,
                                                       tmp_path / "sources.db", lambda _line: None))


def test_metadata_extraction_and_unavailable_values_are_unknown():
    client = FakeClient(messages=[])
    result = asyncio.run(inspect_source(client, "@ExampleChannel"))
    assert result.metadata["verified"] is True
    assert result.metadata["participant_count"] == 321
    assert result.metadata["linked_discussion"] is None

    entity = _channel()
    entity.username = None
    entity.verified = None
    entity.participants_count = None
    client = FakeClient(entity=entity, full_error=RuntimeError("not available"))
    result = asyncio.run(inspect_source(client, "@ExampleChannel"))
    assert result.metadata["username"] is None
    assert result.metadata["verified"] is None
    assert result.metadata["participant_count"] is None
    assert result.metadata["description"] is None
    assert result.warnings


def test_linked_discussion_metadata_is_reported():
    linked = SimpleNamespace(id=765, username="Discussion", title="Discussion Group")
    class LinkedClient(FakeClient):
        async def __call__(self, request):
            return SimpleNamespace(
                full_chat=SimpleNamespace(about="Public description", participants_count=321,
                                          linked_chat_id=765),
                chats=[linked],
            )
    result = asyncio.run(inspect_source(LinkedClient(messages=[]), "@ExampleChannel"))
    assert result.metadata["linked_discussion"] == {
        "telegram_id": "765", "username": "Discussion", "title": "Discussion Group"
    }


def test_authorized_group_is_classified_as_group():
    entity = _channel(broadcast=False, megagroup=True)
    result = asyncio.run(inspect_source(FakeClient(entity=entity, messages=[]), "@ExampleChannel"))
    assert result.metadata["source_type"] == "group"


def test_authorized_group_can_be_selected_by_existing_numeric_dialog_id():
    entity = Chat(id=678, title="Authorized Group", photo=None, participants_count=14,
                  date=datetime(2025, 1, 1, tzinfo=UTC), version=1)
    client = FakeClient(entity=entity, messages=[])
    result = asyncio.run(inspect_source(client, "-100678"))
    assert client.resolved_username == -100678
    assert result.metadata["source_type"] == "group"
    assert result.source_identity["username"] is None


def test_link_detection_handles_hidden_and_unicode_entity_offsets():
    text = "🔗 https://unicode.example/path and visible"
    url = "https://unicode.example/path"
    offset = len("🔗 ".encode("utf-16-le")) // 2
    length = len(url.encode("utf-16-le")) // 2
    entities = [MessageEntityUrl(offset=offset, length=length),
                MessageEntityTextUrl(offset=0, length=7, url="https://hidden.example")]
    found = extract_urls(text, entities)
    assert url in found
    assert "https://hidden.example" in found


def test_url_detection_deduplicates_text_and_entity_urls():
    url = "https://example.org/page"
    text = f"See {url}"
    offset = len("See ".encode("utf-16-le")) // 2
    length = len(url.encode("utf-16-le")) // 2
    assert extract_urls(text, [MessageEntityUrl(offset=offset, length=length)]) == [url]


def test_configuration_prefix_candidates_are_counted_but_redacted():
    client = FakeClient(messages=[_message("vless://user:password@host?secret=private ss://encoded-secret")])
    result = asyncio.run(inspect_source(client, "@ExampleChannel"))
    serialized = json.dumps(result.as_dict())
    assert result.findings["messages_containing_vless_candidates"] == 1
    assert result.findings["messages_containing_shadowsocks_candidates"] == 1
    assert "password@host" not in serialized
    assert "encoded-secret" not in serialized


def test_sensitive_uri_in_public_description_is_redacted():
    client = FakeClient(full_chat=SimpleNamespace(
        about="Research vless://secret@host details", participants_count=10, linked_chat_id=None
    ))
    result = asyncio.run(inspect_source(client, "@ExampleChannel"))
    assert "secret@host" not in json.dumps(result.as_dict())
    assert "[REDACTED VLESS URI]" in result.metadata["description"]


def test_inspection_limit_is_capped_and_hard_enforced():
    with pytest.raises(InspectionError, match="between 1 and 100"):
        validate_limit(101)
    messages = [_message(f"message {i}") for i in range(150)]
    client = FakeClient(messages=messages)
    result = asyncio.run(inspect_source(client, "@ExampleChannel", limit=5))
    assert result.findings["messages_inspected"] == 5
    assert client.request_limit == 5
    assert client.consumed == 5


def test_inspector_json_cli_output(monkeypatch, tmp_path, capsys):
    from telegramlab import cli

    result = asyncio.run(inspect_source(FakeClient(messages=[]), "@ExampleChannel", progress=None))
    async def fake_run(*args, **kwargs):
        return result
    monkeypatch.setattr(cli, "_inspect_with_existing_session", fake_run)
    monkeypatch.setenv("TELEGRAMLAB_SOURCES_DB_PATH", str(tmp_path / "sources.db"))
    args = build_parser().parse_args(["inspect", "@ExampleChannel", "--json"])
    assert _run_inspect(args) == 0
    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed["completion_status"] == "completed"
    assert parsed["limit"] == 30
    assert parsed["findings"]["messages_inspected"] == 0


def test_registry_save_updates_source_without_overwriting_notes(tmp_path):
    path = tmp_path / "sources.db"
    source, _ = add_source(path, provider="telegram", source_type="channel", identifier="@ExampleChannel",
                           display_name="Manual display name", notes="manual research notes")
    result = asyncio.run(inspect_source(FakeClient(messages=[_message("private full text")]),
                                        "@ExampleChannel", registry_path=path, save=True, progress=None))
    saved = get_source(path, source.id)
    assert result.registry_source_id == source.id
    assert saved.platform_id == "12345"
    assert saved.last_checked_at == result.inspected_at
    assert saved.display_name == "Manual display name"
    assert saved.notes == "manual research notes"
    with sqlite3.connect(path) as db:
        table_names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "inspection_runs" in table_names
        assert "schema_migrations" in table_names
        all_values = " ".join(str(tuple(row)) for row in db.execute("SELECT * FROM inspection_runs"))
        assert "private full text" not in all_values
        assert db.execute("SELECT COUNT(*) FROM inspection_runs").fetchone()[0] == 1
        columns = {row[1] for row in db.execute("PRAGMA table_info(inspection_runs)")}
        assert "message_text" not in columns and "full_text" not in columns
        assert "text" not in columns


def test_registry_save_creates_candidate_source_when_missing(tmp_path):
    path = tmp_path / "sources.db"
    result = asyncio.run(inspect_source(FakeClient(messages=[]), "@ExampleChannel",
                                        registry_path=path, save=True, progress=None))
    saved = get_source(path, result.registry_source_id)
    assert saved.status == "candidate"
    assert saved.platform_id == "12345"


def test_registry_save_supports_numeric_id_without_username(tmp_path):
    path = tmp_path / "sources.db"
    entity = Chat(id=678, title="Authorized Group", photo=None, participants_count=14,
                  date=datetime(2025, 1, 1, tzinfo=UTC), version=1)
    result = asyncio.run(inspect_source(FakeClient(entity=entity, messages=[]), "-100678",
                                        registry_path=path, save=True, progress=None))
    saved = get_source(path, result.registry_source_id)
    assert saved.identifier == "telegram-id:-100678"
    assert saved.platform_id == "678"
    assert saved.canonical_url is None


def test_registry_identity_conflict_is_not_merged(tmp_path):
    path = tmp_path / "sources.db"
    add_source(path, provider="telegram", source_type="channel", identifier="@ExampleChannel", platform_id="999")
    with pytest.raises(RegistryError, match="Identity conflict"):
        save_inspection_record(path, identifier="@ExampleChannel", source_type="channel",
                               metadata={"telegram_id": "12345", "username": "ExampleChannel"},
                               inspected_at="2025-01-01T00:00:00+00:00", limit=1,
                               findings={"messages_inspected": 0, "messages_containing_urls": 0,
                                         "messages_containing_telegram_references": 0,
                                         "messages_containing_external_urls": 0,
                                         "messages_containing_vless_candidates": 0,
                                         "messages_containing_shadowsocks_candidates": 0,
                                         "earliest_inspected_message_date": None,
                                         "latest_inspected_message_date": None}, warnings=[])


def test_flood_wait_stops_without_retry():
    client = FakeClient(full_error=FloodWaitError(None, capture=43))
    with pytest.raises(FloodWaitInspectionError, match="43 seconds") as caught:
        asyncio.run(inspect_source(client, "@ExampleChannel"))
    assert caught.value.seconds == 43


def test_network_failure_is_mapped_without_retry():
    client = FakeClient(entity_error=TimeoutError())
    with pytest.raises(NetworkInspectionError, match="timed out"):
        asyncio.run(inspect_source(client, "@ExampleChannel"))


def test_existing_database_and_session_artifacts_are_not_touched(tmp_path):
    protected = tmp_path / "telegram_lab.db"
    protected.write_bytes(b"existing-research-data")
    before = hashlib.sha256(protected.read_bytes()).hexdigest()
    registry = tmp_path / "data" / "sources.db"
    asyncio.run(inspect_source(FakeClient(messages=[]), "@ExampleChannel", save=True,
                               registry_path=registry, progress=None))
    assert hashlib.sha256(protected.read_bytes()).hexdigest() == before


def test_inspector_help_has_requested_options(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["inspect", "--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "--limit" in output and "--json" in output and "--save" in output
