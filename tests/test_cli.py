from pathlib import Path

import pytest

from telegramlab import __version__
from telegramlab.cli import main


def test_help(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "status" in output


def test_offline_status_does_not_need_credentials(tmp_path, monkeypatch, capsys):
    database = tmp_path / "sample.db"
    session = tmp_path / "telegram_lab"
    database.touch()
    session.with_suffix(".session").touch()
    monkeypatch.setenv("TELEGRAMLAB_DB_PATH", str(database))
    monkeypatch.setenv("TELEGRAMLAB_SESSION_PATH", str(session))
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)

    assert main(["status"]) == 0
    output = capsys.readouterr().out
    assert __version__ in output
    assert str(database) in output
    assert "Database exists: yes" in output
    assert "Session file exists: yes" in output
    assert "TG_API" not in output


def test_status_reports_missing_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAMLAB_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setenv("TELEGRAMLAB_SESSION_PATH", str(tmp_path / "missing"))
    assert main(["status"]) == 0
    output = capsys.readouterr().out
    assert "Database exists: no" in output
    assert "Session file exists: no" in output
