"""Unit tests: canvas.toml config loading."""
from __future__ import annotations

from pathlib import Path

import pytest

from github_to_canvas.config import Config, load


def _write_toml(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_load_basic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_API_TOKEN", "tok123")
    cfg_path = _write_toml(
        tmp_path / "canvas.toml",
        'base_url = "https://school.instructure.com"\ncourse_id = 42\n',
    )
    cfg = load(cfg_path)
    assert cfg.base_url == "https://school.instructure.com"
    assert cfg.course_id == 42
    assert cfg.api_token == "tok123"


def test_trailing_slash_stripped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_API_TOKEN", "tok")
    cfg_path = _write_toml(
        tmp_path / "canvas.toml",
        'base_url = "https://school.instructure.com/"\ncourse_id = 1\n',
    )
    cfg = load(cfg_path)
    assert cfg.base_url == "https://school.instructure.com"


def test_token_from_toml_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)
    cfg_path = _write_toml(
        tmp_path / "canvas.toml",
        'base_url = "https://school.instructure.com"\ncourse_id = 1\n[auth]\napi_token = "toml_tok"\n',
    )
    cfg = load(cfg_path)
    assert cfg.api_token == "toml_tok"


def test_env_var_takes_precedence_over_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_API_TOKEN", "env_tok")
    cfg_path = _write_toml(
        tmp_path / "canvas.toml",
        'base_url = "https://school.instructure.com"\ncourse_id = 1\n[auth]\napi_token = "toml_tok"\n',
    )
    cfg = load(cfg_path)
    assert cfg.api_token == "env_tok"


def test_missing_token_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)
    cfg_path = _write_toml(
        tmp_path / "canvas.toml",
        'base_url = "https://school.instructure.com"\ncourse_id = 1\n',
    )
    with pytest.raises(ValueError, match="API token"):
        load(cfg_path)


def test_missing_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_API_TOKEN", "tok")
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nonexistent.toml")


def test_missing_base_url_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_API_TOKEN", "tok")
    cfg_path = _write_toml(tmp_path / "canvas.toml", "course_id = 1\n")
    with pytest.raises(KeyError):
        load(cfg_path)


def test_missing_course_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_API_TOKEN", "tok")
    cfg_path = _write_toml(
        tmp_path / "canvas.toml",
        'base_url = "https://school.instructure.com"\n',
    )
    with pytest.raises(KeyError):
        load(cfg_path)


def test_course_id_coerced_to_int(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_API_TOKEN", "tok")
    cfg_path = _write_toml(
        tmp_path / "canvas.toml",
        'base_url = "https://school.instructure.com"\ncourse_id = 99\n',
    )
    cfg = load(cfg_path)
    assert isinstance(cfg.course_id, int)
    assert cfg.course_id == 99


def test_config_is_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_API_TOKEN", "tok")
    cfg_path = _write_toml(
        tmp_path / "canvas.toml",
        'base_url = "https://school.instructure.com"\ncourse_id = 1\n',
    )
    cfg = load(cfg_path)
    with pytest.raises(Exception):
        cfg.course_id = 999  # type: ignore[misc]
