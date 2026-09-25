"""Tests for scripts/mcp_setup.py config generation and merging."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_MODULE_PATH = Path(__file__).parent.parent / "scripts" / "mcp_setup.py"
_spec = importlib.util.spec_from_file_location("mcp_setup", _MODULE_PATH)
mcp_setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcp_setup)

STDIO_ENTRY = {
    "command": "C:\\repos\\trellis\\.venv\\Scripts\\python.exe",
    "args": ["C:\\repos\\trellis\\server.py"],
    "env": {"TRELLIS_ALLOW_NO_AUTH": "true"},
}
HTTP_ENTRY = {"url": "http://127.0.0.1:17317/mcp"}


# === JSON merging ===


def test_merge_creates_new_config():
    out = mcp_setup.merge_mcp_servers_json(None, "trellis-core", STDIO_ENTRY)
    config = json.loads(out)
    assert config["mcpServers"]["trellis-core"]["command"] == STDIO_ENTRY["command"]


def test_merge_preserves_other_servers():
    existing = json.dumps({"mcpServers": {"other": {"command": "x"}}, "global": {"theme": "dark"}})
    out = mcp_setup.merge_mcp_servers_json(existing, "trellis-core", STDIO_ENTRY)
    config = json.loads(out)
    assert config["mcpServers"]["other"]["command"] == "x"
    assert config["global"]["theme"] == "dark"
    assert "trellis-core" in config["mcpServers"]


def test_merge_replaces_existing_entry():
    existing = json.dumps({"mcpServers": {"trellis-core": {"command": "old"}}})
    out = mcp_setup.merge_mcp_servers_json(existing, "trellis-core", STDIO_ENTRY)
    config = json.loads(out)
    assert config["mcpServers"]["trellis-core"]["command"] == STDIO_ENTRY["command"]


def test_merge_vscode_style():
    out = mcp_setup.merge_mcp_servers_json(None, "trellis-core", STDIO_ENTRY, vscode_style=True)
    config = json.loads(out)
    assert config["servers"]["trellis-core"]["command"] == STDIO_ENTRY["command"]
    assert config["inputs"] == []


def test_merge_invalid_existing_json_starts_fresh():
    out = mcp_setup.merge_mcp_servers_json("{not json", "trellis-core", STDIO_ENTRY)
    assert "trellis-core" in json.loads(out)["mcpServers"]


# === Codex TOML ===


def test_codex_toml_stdio_escapes_windows_paths():
    section = mcp_setup.build_codex_toml_section(STDIO_ENTRY)
    assert 'command = "C:\\\\repos\\\\trellis\\\\.venv\\\\Scripts\\\\python.exe"' in section
    assert 'TRELLIS_ALLOW_NO_AUTH = "true"' in section
    # Valid TOML
    import tomllib

    parsed = tomllib.loads(section)
    server = parsed["mcp_servers"]["trellis-core"]
    assert server["command"] == STDIO_ENTRY["command"]
    assert server["args"] == STDIO_ENTRY["args"]


def test_codex_toml_http():
    section = mcp_setup.build_codex_toml_section(HTTP_ENTRY)
    import tomllib

    server = tomllib.loads(section)["mcp_servers"]["trellis-core"]
    assert server["url"] == HTTP_ENTRY["url"]
    assert "command" not in server


def test_merge_codex_appends_then_replaces():
    section_v1 = mcp_setup.build_codex_toml_section({"command": "v1", "args": [], "env": {}})
    section_v2 = mcp_setup.build_codex_toml_section({"command": "v2", "args": [], "env": {}})

    appended = mcp_setup.merge_codex_toml("[mcp_servers.other]\ncommand = 'x'\n", section_v1)
    assert appended.count("[mcp_servers.trellis-core]") == 1
    assert 'command = "v1"' in appended
    assert "[mcp_servers.other]" in appended

    replaced = mcp_setup.merge_codex_toml(appended, section_v2)
    assert replaced.count("[mcp_servers.trellis-core]") == 1
    assert 'command = "v2"' in replaced
    assert 'command = "v1"' not in replaced
    assert "[mcp_servers.other]" in replaced


# === generate() ===


def test_generate_kimi_http(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_setup.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mcp_setup, "REPO_ROOT", tmp_path)
    path, content = mcp_setup.generate(
        "kimi-code", "http", "linux", "user", dict(HTTP_ENTRY)
    )
    assert path == tmp_path / ".kimi-code" / "mcp.json"
    assert json.loads(content)["mcpServers"]["trellis-core"]["url"] == HTTP_ENTRY["url"]


def test_generate_stdio_cwd_only_for_supporting_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_setup.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mcp_setup, "REPO_ROOT", tmp_path)

    _, vscode_content = mcp_setup.generate(
        "vscode", "stdio", "linux", "project", dict(STDIO_ENTRY)
    )
    assert "cwd" in json.loads(vscode_content)["servers"]["trellis-core"]

    _, claude_content = mcp_setup.generate(
        "claude-desktop", "stdio", "linux", "user", dict(STDIO_ENTRY)
    )
    assert "cwd" not in json.loads(claude_content)["mcpServers"]["trellis-core"]


def test_generate_vscode_http_adds_type(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_setup.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mcp_setup, "REPO_ROOT", tmp_path)
    _, content = mcp_setup.generate("vscode", "http", "linux", "project", dict(HTTP_ENTRY))
    assert json.loads(content)["servers"]["trellis-core"]["type"] == "http"


# === Bundled exe stdio handling ===


def test_bundled_exe_entry_forces_stdio(tmp_path, monkeypatch):
    """The frozen exe defaults to HTTP mode; the stdio config must override it."""
    monkeypatch.setattr(mcp_setup, "REPO_ROOT", tmp_path)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "trellis.exe").write_text("exe")

    servers = mcp_setup.find_stdio_servers()
    bundled = next(s for s in servers if s["id"] == "bundled")
    entry = mcp_setup.build_stdio_entry(
        bundled["command"], bundled["args"], extra_env=bundled.get("extra_env")
    )
    assert entry["env"]["TRELLIS_TRANSPORT"] == "stdio"
    assert entry["env"]["TRELLIS_ALLOW_NO_AUTH"] == "true"


def test_bundled_extra_env_survives_generate(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_setup.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mcp_setup, "REPO_ROOT", tmp_path)
    entry = mcp_setup.build_stdio_entry(
        "K:\\dist\\trellis.exe", [], extra_env={"TRELLIS_TRANSPORT": "stdio"}
    )
    _, content = mcp_setup.generate("kimi-code", "stdio", "windows", "user", dict(entry))
    assert json.loads(content)["mcpServers"]["trellis-core"]["env"]["TRELLIS_TRANSPORT"] == "stdio"


# === write_config ===


def test_write_config_backs_up_existing(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text('{"old": true}', encoding="utf-8")
    mcp_setup.write_config(path, '{"new": true}')
    assert path.read_text(encoding="utf-8") == '{"new": true}'
    assert (tmp_path / "mcp.json.bak").read_text(encoding="utf-8") == '{"old": true}'


# === claude-code CLI equivalent ===


def test_claude_code_command_stdio():
    cmd = mcp_setup.claude_code_command("stdio", STDIO_ENTRY)
    assert cmd.startswith("claude mcp add trellis-core")
    assert "--env TRELLIS_ALLOW_NO_AUTH=true" in cmd
    assert "-- C:\\repos\\trellis\\.venv\\Scripts\\python.exe" in cmd


def test_claude_code_command_http():
    cmd = mcp_setup.claude_code_command("http", HTTP_ENTRY)
    assert cmd == "claude mcp add --transport http trellis-core http://127.0.0.1:17317/mcp"


# === Unknown platform linking kit ===


def test_linking_kit_contains_all_connection_info():
    kit = mcp_setup.build_linking_kit("windows", STDIO_ENTRY, "http://127.0.0.1:17317/mcp")
    assert "trellis-core" in kit
    assert "windows" in kit
    assert "python.exe" in kit  # JSON-escaped path of STDIO_ENTRY command
    assert "http://127.0.0.1:17317/mcp" in kit
    assert '"mcpServers"' in kit
    assert "TRELLIS_ALLOW_NO_AUTH" in kit
    # References every known client config location
    for client in mcp_setup.CLIENTS.values():
        path = client["path"]("windows", client["scopes"][0])
        assert str(path) in kit


def test_linking_kit_works_without_detected_stdio():
    kit = mcp_setup.build_linking_kit("linux", None, "http://127.0.0.1:17317/mcp")
    assert "<path to python or dist/trellis.exe>" in kit


def test_main_other_client_prints_kit(capsys):
    rc = mcp_setup.main(["--client", "other", "--transport", "http", "--print"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "connection info" in out
    assert "http://127.0.0.1:17317/mcp" in out
