"""Unit tests: canvas.toml config loading."""
from __future__ import annotations

from pathlib import Path

import pytest

from markdown_to_canvas.cli import _resolve_repo
from markdown_to_canvas.config import load


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


# ---------------------------------------------------------------------------
# _resolve_repo: optional REPO argument for update/publish
# ---------------------------------------------------------------------------


def _make_course_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "course_settings").mkdir(parents=True)
    (repo / "course_settings" / "course_settings.toml").write_text("")
    return repo


class TestResolveRepo:
    def test_explicit_path_is_used_verbatim(self, tmp_path: Path) -> None:
        """An explicit path never walks up, so a wrong path still reports its own
        missing config rather than silently acting on the parent repo."""
        repo = _make_course_repo(tmp_path)
        subdir = repo / "pages" / "worksheets"
        subdir.mkdir(parents=True)
        assert _resolve_repo(subdir) == subdir

    def test_omitted_walks_up_from_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make_course_repo(tmp_path)
        subdir = repo / "pages" / "worksheets"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)
        assert _resolve_repo(None) == repo

    def test_omitted_at_repo_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make_course_repo(tmp_path)
        monkeypatch.chdir(repo)
        assert _resolve_repo(None) == repo

    def test_omitted_outside_any_repo_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            _resolve_repo(None)
