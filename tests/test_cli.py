import json
import zipfile
from pathlib import Path

from telegram_conversation_exporter.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_dry_run_prints_summary(capsys, tmp_path):
    exit_code = main([
        "export",
        "--source",
        str(FIXTURES / "media_chat.json"),
        "--chat-ref",
        "chat_media",
        "--output-dir",
        str(tmp_path),
        "--dry-run",
    ])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["chat_ref"] == "chat_media"
    assert out["message_count"] == 6
    assert not (tmp_path / ".tce_cache").exists()


def test_cli_export_writes_files(capsys, tmp_path):
    exit_code = main([
        "export",
        "--source",
        str(FIXTURES / "simple_private_chat.json"),
        "--chat-ref",
        "chat_simple",
        "--output-dir",
        str(tmp_path),
    ])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert Path(out["conversation_json"]).exists()
    assert Path(out["conversation_md"]).exists()


def test_cli_list_chats_prints_available_chat_refs(capsys):
    exit_code = main([
        "list-chats",
        "--source",
        str(FIXTURES / "full_export.json"),
    ])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert {item["chat_ref"] for item in out} == {"chat_one", "chat_two"}


def test_cli_accepts_zip_source(capsys, tmp_path):
    zip_path = tmp_path / "telegram-export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(FIXTURES / "media_chat.json", arcname="result.json")
        archive.write(FIXTURES / "media" / "voice_note.ogg", arcname="media/voice_note.ogg")
        archive.write(FIXTURES / "media" / "screenshot.jpg", arcname="media/screenshot.jpg")

    exit_code = main([
        "export",
        "--source",
        str(zip_path),
        "--chat-ref",
        "chat_media",
        "--output-dir",
        str(tmp_path / "out"),
        "--dry-run",
    ])

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["chat_ref"] == "chat_media"
