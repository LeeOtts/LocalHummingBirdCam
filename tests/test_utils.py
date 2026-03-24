"""Tests for utils.py — safe JSON read/write utilities."""

import json
from pathlib import Path

import pytest

from utils import safe_read_json, safe_write_json


class TestSafeReadJson:
    """Test safe_read_json with various file states."""

    def test_reads_valid_json(self, tmp_path):
        path = tmp_path / "data.json"
        path.write_text(json.dumps({"key": "value"}))
        assert safe_read_json(path) == {"key": "value"}

    def test_returns_default_for_missing_file(self, tmp_path):
        path = tmp_path / "missing.json"
        assert safe_read_json(path, default={"fallback": True}) == {"fallback": True}

    def test_returns_empty_dict_default(self, tmp_path):
        path = tmp_path / "missing.json"
        assert safe_read_json(path) == {}

    def test_returns_default_for_corrupt_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{invalid json!!!")
        result = safe_read_json(path, default=[])
        assert result == []

    def test_backs_up_corrupt_file(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        safe_read_json(path, default={})
        # Original should be renamed
        assert not path.exists()
        backups = list(tmp_path.glob("bad.corrupt.*"))
        assert len(backups) == 1

    def test_returns_default_for_empty_file(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("")
        result = safe_read_json(path, default={"x": 1})
        assert result == {"x": 1}

    def test_reads_list_json(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text(json.dumps([1, 2, 3]))
        assert safe_read_json(path, default=[]) == [1, 2, 3]

    def test_returns_default_on_permission_error(self, tmp_path, monkeypatch):
        path = tmp_path / "locked.json"
        path.write_text('{"a": 1}')
        monkeypatch.setattr(Path, "read_text", lambda self: (_ for _ in ()).throw(PermissionError("denied")))
        result = safe_read_json(path, default={"safe": True})
        assert result == {"safe": True}


class TestSafeWriteJson:
    """Test safe_write_json atomic writes."""

    def test_writes_valid_json(self, tmp_path):
        path = tmp_path / "out.json"
        safe_write_json(path, {"hello": "world"})
        assert json.loads(path.read_text()) == {"hello": "world"}

    def test_atomic_write_no_tmp_left(self, tmp_path):
        path = tmp_path / "out.json"
        safe_write_json(path, [1, 2, 3])
        tmp_file = path.with_suffix(".tmp")
        assert not tmp_file.exists()
        assert path.exists()

    def test_overwrites_existing_file(self, tmp_path):
        path = tmp_path / "out.json"
        path.write_text('{"old": true}')
        safe_write_json(path, {"new": True})
        assert json.loads(path.read_text()) == {"new": True}

    def test_writes_with_indent(self, tmp_path):
        path = tmp_path / "out.json"
        safe_write_json(path, {"a": 1}, indent=4)
        text = path.read_text()
        assert "    " in text  # 4-space indent

    def test_handles_write_error_gracefully(self, tmp_path, monkeypatch):
        path = tmp_path / "fail.json"
        # Make write_text raise
        monkeypatch.setattr(Path, "write_text", lambda self, t: (_ for _ in ()).throw(PermissionError("denied")))
        # Should not raise
        safe_write_json(path, {"data": True})
