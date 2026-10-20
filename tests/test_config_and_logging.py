import logging

from telegramlab.cli import SecretRedactionFilter
from telegramlab.core.config import Settings


def test_config_path_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAMLAB_DB_PATH", str(tmp_path / "data.db"))
    monkeypatch.setenv("TELEGRAMLAB_SESSION_PATH", str(tmp_path / "account"))
    settings = Settings.from_env()
    assert settings.database_path == tmp_path / "data.db"
    assert settings.session_path == tmp_path / "account"


def test_relative_session_path_resolves_from_project_root(monkeypatch, tmp_path):
    from telegramlab.core.config import PROJECT_ROOT
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAMLAB_SESSION_PATH", "sessions/research")
    assert Settings.from_env().session_path == PROJECT_ROOT / "sessions/research"


def test_secret_redaction_filter():
    record = logging.LogRecord("test", logging.INFO, "", 0, "api_hash=%s", ("do-not-print",), None)
    assert SecretRedactionFilter().filter(record)
    assert "do-not-print" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()
