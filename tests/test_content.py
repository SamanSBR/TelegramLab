import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon import functions, types
from telethon.errors import FloodWaitError

from telegramlab import cli
from telegramlab.telegram.content import (
    ContentSearchError, local_search, normalize_text, save_hits, search_content,
)
from telegramlab.telegram.inspector import FloodWaitInspectionError, InvalidSourceError


def channel(number=1, username="PublicNews", broadcast=True):
    return types.Channel(id=number, title="Public", photo=types.ChatPhotoEmpty(), date=datetime.now(UTC),
                         broadcast=broadcast, megagroup=not broadcast, access_hash=123,
                         username=username)


def message(number=1, text="VLESS proxy", source_id=1, media=None):
    return SimpleNamespace(id=number, raw_text=text, date=datetime(2026, 1, 2, tzinfo=UTC),
                           peer_id=types.PeerChannel(source_id), media=media)


class Client:
    def __init__(self, chats, messages, error=None):
        self.chats = chats
        self.messages = messages
        self.error = error
        self.requests = []
        self.lookups = []

    async def get_entity(self, target):
        self.lookups.append(target)
        if self.error:
            raise self.error
        return self.chats[0]

    async def __call__(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return SimpleNamespace(chats=self.chats, messages=self.messages)


def run(client, tmp_path, query="VLESS", **kwargs):
    return asyncio.run(search_content(client, query, index_path=tmp_path / "content.db", **kwargs))


def test_global_public_filter_dedup_and_evidence(tmp_path):
    public = channel()
    private = channel(2, None)
    group = channel(3, "PublicGroup", False)
    client = Client([public, private, group], [message(), message(), message(2, source_id=2),
                                                message(3, source_id=3)])
    response = run(client, tmp_path, limit=10)
    assert len(client.requests) == 1
    assert isinstance(client.requests[0], functions.messages.SearchGlobalRequest)
    assert client.requests[0].limit == 10
    assert client.lookups == []
    assert response.scanned == 4
    assert len(response.results) == 1
    hit = response.results[0]
    assert (hit.source_id, hit.message_id, hit.source_channel) == ("1", 1, "@publicnews")
    assert hit.message_url == "https://t.me/PublicNews/1"
    assert hit.message_date.startswith("2026-01-02")
    assert hit.search_query == "VLESS"
    assert hit.match_type == "exact_text"
    assert hit.content_type == "message"
    assert not (tmp_path / "content.db").exists()


def test_source_search_and_reject_nonpublic(tmp_path):
    client = Client([channel()], [message(4, "proxy")])
    response = run(client, tmp_path, "proxy", source="@PublicNews")
    assert response.results[0].message_id == 4
    assert client.lookups == ["publicnews"]
    assert isinstance(client.requests[0], functions.messages.SearchRequest)
    assert client.requests[0].limit == 20
    with pytest.raises(InvalidSourceError):
        run(Client([channel(2, None)], []), tmp_path, source="@PrivateNews")
    with pytest.raises(InvalidSourceError):
        run(client, tmp_path, source="-1001234567890")


def test_types_matching_and_unicode(tmp_path):
    doc = types.Document(id=1, access_hash=1, file_reference=b"", date=datetime.now(UTC),
                         mime_type="application/pdf", size=10, dc_id=1,
                         attributes=[types.DocumentAttributeFilename("Python_guide.pdf")])
    media = types.MessageMediaDocument(document=doc)
    client = Client([channel()], [message(1, "", media=media), message(2, "preVLESSpost"),
                                   message(3, "سامان صابري"), message(4, "Python tutorial")])
    docs = run(client, tmp_path, "Python", kind="document")
    assert len(docs.results) == 1
    assert docs.results[0].match_type == "filename"
    assert docs.results[0].filename == "Python_guide.pdf"
    assert isinstance(client.requests[0].filter, types.InputMessagesFilterDocument)
    all_hits = run(client, tmp_path, "VLESS")
    assert all_hits.results[1].match_type == "partial_text"
    persian = run(client, tmp_path, "سامان صابری")
    assert persian.results[2].match_type == "exact_text"
    assert persian.results[2].text == "سامان صابري"
    assert normalize_text("كِتاب يَك") == normalize_text("کتاب یک")


def test_save_local_dedup_no_network_and_protected_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAMLAB_CONTENT_DB_PATH", str(tmp_path / "content.db"))
    client = Client([channel()], [message(1, "VPN guide")])
    first = run(client, tmp_path, "VPN", save=True)
    second = run(client, tmp_path, "VPN", save=True)
    assert first.saved == 1 and second.saved == 0
    third = run(client, tmp_path, "guide", save=True)
    assert third.saved == 0
    with sqlite3.connect(tmp_path / "content.db") as db:
        assert db.execute("SELECT count(*) FROM content_hits").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM content_discoveries").fetchone()[0] == 2
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert local_search(tmp_path / "content.db", "VPN", "all", 20, None, "now")[0].text == "VPN guide"
    monkeypatch.setattr(cli, "create_client", lambda *_: pytest.fail("client constructed"))
    assert cli.main(["content", "search", "VPN", "--local", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["message_url"] == "https://t.me/PublicNews/1"
    assert payload["mode"] == "local"
    monkeypatch.setenv("TELEGRAMLAB_DB_PATH", str(tmp_path / "telegram_lab.db"))
    with pytest.raises(ContentSearchError):
        save_hits(tmp_path / "telegram_lab.db", first.results)
    monkeypatch.setenv("TELEGRAMLAB_CONTENT_DB_PATH", str(tmp_path / "missing.db"))
    assert cli.main(["content", "search", "VPN", "--local", "--json"]) == 0
    assert not (tmp_path / "missing.db").exists()


def test_media_types_and_local_persian(tmp_path):
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=1))
    video_doc = types.Document(id=2, access_hash=1, file_reference=b"", date=datetime.now(UTC),
                               mime_type="video/mp4", size=10, dc_id=1, attributes=[])
    audio_doc = types.Document(id=3, access_hash=1, file_reference=b"", date=datetime.now(UTC),
                               mime_type="audio/ogg", size=10, dc_id=1, attributes=[])
    client = Client([channel()], [message(1, "سامان صابري", media=photo),
                                   message(2, "سامان صابري", media=types.MessageMediaDocument(document=video_doc)),
                                   message(3, "سامان صابري", media=types.MessageMediaDocument(document=audio_doc))])
    for kind in ("photo", "video", "audio"):
        result = run(client, tmp_path, "سامان صابری", kind=kind, save=True)
        assert [hit.content_type for hit in result.results] == [kind]
        assert result.saved == 1
    local = run(None, tmp_path, "سامان صابری", local=True, source="@PublicNews")
    assert len(local.results) == 3
    assert all(hit.text == "سامان صابري" for hit in local.results)


def test_flood_wait_and_limits(tmp_path):
    with pytest.raises(FloodWaitInspectionError, match="12 seconds"):
        run(Client([channel()], [], FloodWaitError(None, capture=12)), tmp_path)
    with pytest.raises(ContentSearchError):
        run(Client([], []), tmp_path, limit=101)
    with pytest.raises(ContentSearchError):
        run(Client([], []), tmp_path, query=" ")
    assert not (tmp_path / "content.db").exists()
