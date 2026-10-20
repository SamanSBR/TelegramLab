import hashlib
import sqlite3

import pytest

from telegramlab.sources.registry import (
    RegistryError,
    add_relationship,
    add_source,
    get_source,
    initialize_database,
    list_sources,
    normalize_telegram_identifier,
    pause_source,
    resume_source,
    search_sources,
    source_stats,
    update_source,
)


def test_database_initialization_creates_schema(tmp_path):
    path = tmp_path / "nested" / "sources.db"
    initialize_database(path)
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "source_relationships"} <= tables


def test_source_insertion_defaults_to_candidate(tmp_path):
    source, created = add_source(tmp_path / "sources.db", provider="telegram", source_type="channel",
                                 identifier="@ExampleChannel", research_category="configurations")
    assert created
    assert source.status == "candidate"
    assert source.identifier == "@examplechannel"
    assert source.canonical_url == "https://t.me/examplechannel"
    assert source.discovery_method == "manually_added"


def test_duplicate_insertion_preserves_metadata(tmp_path):
    path = tmp_path / "sources.db"
    first, inserted = add_source(path, provider="telegram", source_type="channel", identifier="@ExampleChannel",
                                display_name="Original", notes="keep this")
    second, inserted_again = add_source(path, provider="telegram", source_type="channel",
                                        identifier="https://t.me/examplechannel", display_name="Replacement")
    assert inserted and not inserted_again
    assert first.id == second.id
    assert second.display_name == "Original"
    assert second.notes == "keep this"


def test_telegram_username_normalization_variants_match():
    variants = ("@ExampleChannel", "https://t.me/ExampleChannel", "t.me/ExampleChannel")
    normalized = [normalize_telegram_identifier(value) for value in variants]
    assert len({item[0] for item in normalized}) == 1
    assert len({item[1] for item in normalized}) == 1
    assert len({item[2] for item in normalized}) == 1


def test_identity_keeps_provider_and_type_namespaces_distinct(tmp_path):
    path = tmp_path / "sources.db"
    channel, _ = add_source(path, provider="telegram", source_type="channel", identifier="@ExampleChannel")
    group, created = add_source(path, provider="telegram", source_type="group", identifier="@ExampleChannel")
    other, other_created = add_source(path, provider="github", source_type="repository", identifier="@ExampleChannel")
    assert created and other_created
    assert len({channel.id, group.id, other.id}) == 3


def test_listing_filters_sources(tmp_path):
    path = tmp_path / "sources.db"
    add_source(path, provider="telegram", source_type="channel", identifier="@FirstChannel", research_category="technology")
    add_source(path, provider="github", source_type="repository", identifier="owner/project", status="active")
    assert [s.provider for s in list_sources(path)] == ["telegram", "github"]
    assert [s.status for s in list_sources(path, status="active")] == ["active"]


def test_search_finds_metadata_case_insensitively(tmp_path):
    path = tmp_path / "sources.db"
    add_source(path, provider="telegram", source_type="channel", identifier="@NewsChannel",
               display_name="Config Research", notes="reference list")
    assert len(search_sources(path, "CONFIG")) == 1
    assert len(search_sources(path, "reference")) == 1


def test_update_source_changes_allowed_metadata(tmp_path):
    path = tmp_path / "sources.db"
    source, _ = add_source(path, provider="website", source_type="website", identifier="example.org")
    updated = update_source(path, source.id, display_name="Example", research_category="market_research")
    assert updated.display_name == "Example"
    assert updated.research_category == "market_research"


def test_pause_and_resume_source(tmp_path):
    path = tmp_path / "sources.db"
    source, _ = add_source(path, provider="telegram", source_type="group", identifier="@ExampleGroup")
    assert pause_source(path, source.id).status == "paused"
    assert resume_source(path, source.id).status == "active"


def test_source_statistics(tmp_path):
    path = tmp_path / "sources.db"
    add_source(path, provider="telegram", source_type="channel", identifier="@FirstChannel", research_category="configurations")
    add_source(path, provider="github", source_type="repository", identifier="owner/project", status="active")
    stats = source_stats(path)
    assert stats["sources"] == 2
    assert stats["by_status"] == {"active": 1, "candidate": 1}
    assert stats["by_provider"] == {"github": 1, "telegram": 1}


def test_relationship_creation_requires_evidence(tmp_path):
    path = tmp_path / "sources.db"
    source, _ = add_source(path, provider="telegram", source_type="channel", identifier="@FirstChannel")
    related, _ = add_source(path, provider="github", source_type="repository", identifier="owner/project")
    relationship, created = add_relationship(path, source_id=source.id, related_source_id=related.id,
                                             relationship_type="references", evidence_reference="message:123")
    assert created
    assert relationship.evidence_reference == "message:123"
    assert source_stats(path)["relationships"] == 1


def test_relationship_duplicate_is_idempotent_and_keeps_original_evidence(tmp_path):
    path = tmp_path / "sources.db"
    source, _ = add_source(path, provider="telegram", source_type="channel", identifier="@FirstChannel")
    related, _ = add_source(path, provider="github", source_type="repository", identifier="owner/project")
    first, created = add_relationship(path, source_id=source.id, related_source_id=related.id,
                                     relationship_type="references", evidence_reference="message:123")
    second, duplicate_created = add_relationship(path, source_id=source.id, related_source_id=related.id,
                                                relationship_type="references", evidence_reference="message:456")
    assert created and not duplicate_created
    assert first.id == second.id
    assert second.evidence_reference == "message:123"


def test_invalid_source_inputs_are_actionable(tmp_path):
    with pytest.raises(RegistryError, match="username"):
        add_source(tmp_path / "sources.db", provider="telegram", source_type="channel", identifier="@x")
    with pytest.raises(RegistryError, match="status"):
        add_source(tmp_path / "sources.db", provider="website", source_type="website",
                   identifier="example.org", status="verified")


def test_registry_initialization_does_not_change_existing_database(tmp_path):
    original = tmp_path / "telegram_lab.db"
    original.write_bytes(b"protected-existing-research-db")
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    registry = tmp_path / "data" / "sources.db"
    add_source(registry, provider="telegram", source_type="channel", identifier="@ExampleChannel")
    after = hashlib.sha256(original.read_bytes()).hexdigest()
    assert before == after
    assert registry != original


def test_registry_refuses_legacy_database_path(tmp_path, monkeypatch):
    protected = tmp_path / "telegram_lab.db"
    protected.write_bytes(b"protected")
    before = hashlib.sha256(protected.read_bytes()).hexdigest()
    monkeypatch.setenv("TELEGRAMLAB_DB_PATH", str(protected))
    with pytest.raises(RegistryError, match="separate database"):
        initialize_database(protected)
    assert hashlib.sha256(protected.read_bytes()).hexdigest() == before


def test_get_missing_source_has_user_error(tmp_path):
    with pytest.raises(RegistryError, match="No source found"):
        get_source(tmp_path / "sources.db", 987)
