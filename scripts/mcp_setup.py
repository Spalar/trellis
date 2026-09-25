"""Generate MCP client configuration for Trellis across providers and OSes.

Single entry point for wiring Trellis into any MCP client: picks the correct
config file for the OS, builds the stdio or HTTP server entry, and either
prints it or merges it into the client's config (with a .bak backup).

Usage:
    python scripts/mcp_setup.py                      # interactive
    python scripts/mcp_setup.py --client codex --transport stdio --write
    python scripts/mcp_setup.py --client kimi-code --transport http --print
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_NAME = "trellis-core"
DEFAULT_HTTP_URL = "http://127.0.0.1:17317/mcp"

STDIO_ENV = {
    "TRELLIS_ALLOW_NO_AUTH": "true",
    "FASTMCP_SHOW_SERVER_BANNER": "false",
    "FASTMCP_LOG_LEVEL": "ERROR",
}


def detect_os() -> str:
    """Return 'windows', 'macos', or 'linux'."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macos"
    return "linux"


# ------------------------------------------------------------------
# Server entries
# ------------------------------------------------------------------


def _venv_python() -> Path:
    if detect_os() == "windows":
        return REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return REPO_ROOT / ".venv" / "bin" / "python"


def find_stdio_servers() -> List[Dict[str, str]]:
    """Detect runnable Trellis servers on this machine, best first."""
    servers = []

    bundled = REPO_ROOT / "dist" / "trellis.exe"
    if bundled.exists():
        servers.append(
            {
                "id": "bundled",
                "label": f"Bundled exe ({bundled})",
                "command": str(bundled),
                "args": [],
                # The frozen exe defaults to HTTP mode + browser; force stdio
                # so MCP clients can spawn it as a child process.
                "extra_env": {"TRELLIS_TRANSPORT": "stdio"},
            }
        )

    dev_python = _venv_python()
    if dev_python.exists():
        servers.append(
            {
                "id": "dev",
                "label": f"Dev venv ({dev_python} + server.py)",
                "command": str(dev_python),
                "args": [str(REPO_ROOT / "server.py")],
            }
        )
    return servers


def build_stdio_entry(
    command: str,
    args: List[str],
    include_cwd: bool = False,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict:
    entry: Dict = {"command": command, "args": args, "env": {**STDIO_ENV, **(extra_env or {})}}
    if include_cwd:
        entry["cwd"] = str(REPO_ROOT)
    return entry


def build_http_entry(url: str = DEFAULT_HTTP_URL) -> Dict:
    return {"url": url}


# ------------------------------------------------------------------
# Clients
# ------------------------------------------------------------------


def _claude_desktop_path(os_name: str) -> Path:
    if os_name == "windows":
        return Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
    if os_name == "macos":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


CLIENTS: Dict[str, Dict] = {
    "claude-desktop": {
        "name": "Claude Desktop (UI app)",
        "kind": "mcpServers-json",
        "path": lambda os_name, scope: _claude_desktop_path(os_name),
        "scopes": ["user"],
    },
    "claude-code": {
        "name": "Claude Code (CLI)",
        "kind": "mcpServers-json",
        "path": lambda os_name, scope: (
            Path.home() / ".claude.json" if scope == "user" else Path.cwd() / ".mcp.json"
        ),
        "scopes": ["user", "project"],
    },
    "codex": {
        "name": "OpenAI Codex (CLI)",
        "kind": "codex-toml",
        "path": lambda os_name, scope: Path.home() / ".codex" / "config.toml",
        "scopes": ["user"],
    },
    "kimi-code": {
        "name": "Kimi Code (CLI)",
        "kind": "mcpServers-json",
        "path": lambda os_name, scope: (
            Path.home() / ".kimi-code" / "mcp.json"
            if scope == "user"
            else Path.cwd() / ".kimi-code" / "mcp.json"
        ),
        "scopes": ["user", "project"],
    },
    "vscode": {
        "name": "VS Code / GitHub Copilot (mcp.json)",
        "kind": "vscode-servers-json",
        "path": lambda os_name, scope: Path.cwd() / ".vscode" / "mcp.json",
        "scopes": ["project"],
    },
    "cursor": {
        "name": "Cursor (mcp.json)",
        "kind": "mcpServers-json",
        "path": lambda os_name, scope: Path.cwd() / ".cursor" / "mcp.json",
        "scopes": ["project"],
    },
}


# ------------------------------------------------------------------
# Unknown platforms: print everything needed to link manually
# ------------------------------------------------------------------


def build_linking_kit(os_name: str, stdio_entry: Optional[Dict], http_url: str) -> str:
    """All Trellis connection info for platforms we don't have a preset for."""
    stdio = stdio_entry or {
        "command": "<path to python or dist/trellis.exe>",
        "args": ["<path to server.py> (not needed for the bundled exe)"],
        "env": dict(STDIO_ENV),
    }
    generic = {"mcpServers": {SERVER_NAME: stdio}}
    lines = [
        "Trellis MCP server - connection info",
        "=" * 40,
        f"Server name : {SERVER_NAME}",
        f"OS          : {os_name}",
        f"Trellis root: {REPO_ROOT}",
        "",
        "Option A - stdio (client launches Trellis as a child process):",
        json.dumps(stdio, indent=2),
        "",
        "Option B - HTTP (connect to an already-running server):",
        json.dumps({"url": http_url}, indent=2),
        "Start it with: make run-http",
        f"Verify with  : curl {http_url.replace('/mcp', '/health')}",
        "",
        "Generic snippet for any client that accepts mcpServers JSON:",
        json.dumps(generic, indent=2),
        "",
        "Known client config locations (for reference):",
    ]
    for key, client in CLIENTS.items():
        path = client["path"](os_name, client["scopes"][0])
        lines.append(f"  {client['name']:<38} {path}")
    lines += [
        "",
        "IDE notes:",
        "  - VS Code extensions of Claude Code / Kimi Code / Codex reuse their CLI config",
        "    (generate the CLI entry above; the extension launches the same CLI).",
        "  - Kimi Code in Zed/JetBrains/Paseo runs `kimi acp` and reads ~/.kimi-code/mcp.json",
        "    or the project's .kimi-code/mcp.json.",
        "  - GitHub Copilot agent mode reads .vscode/mcp.json (the 'vscode' option).",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------
# Config merging
# ------------------------------------------------------------------


def merge_mcp_servers_json(
    existing_text: Optional[str], server_key: str, entry: Dict, vscode_style: bool = False
) -> str:
    """Merge/replace the server entry in an mcpServers-style JSON config."""
    try:
        config = json.loads(existing_text) if existing_text else {}
    except json.JSONDecodeError:
        config = {}

    if vscode_style:
        servers = config.setdefault("servers", {})
        servers[server_key] = entry
        config.setdefault("inputs", [])
    else:
        servers = config.setdefault("mcpServers", {})
        servers[server_key] = entry
    return json.dumps(config, indent=2) + "\n"


def build_codex_toml_section(entry: Dict) -> str:
    """Render a [mcp_servers.trellis-core] TOML section.

    Values are serialized with json.dumps: TOML basic strings and JSON strings
    share escaping rules, so this stays correct on Windows backslash paths.
    """
    lines = [f"[mcp_servers.{SERVER_NAME}]"]
    if "url" in entry:
        lines.append(f"url = {json.dumps(entry['url'])}")
    else:
        lines.append(f"command = {json.dumps(entry['command'])}")
        lines.append(f"args = {json.dumps(entry['args'])}")
        env_pairs = ", ".join(
            f"{key} = {json.dumps(value)}" for key, value in entry.get("env", {}).items()
        )
        lines.append(f"env = {{ {env_pairs} }}")
    return "\n".join(lines) + "\n"


def merge_codex_toml(existing_text: Optional[str], section: str) -> str:
    """Replace an existing trellis-core MCP section or append a new one."""
    header = f"[mcp_servers.{SERVER_NAME}]"
    text = existing_text or ""
    if header in text:
        start = text.index(header)
        rest = text[start:]
        next_section = rest.find("\n[", 1)
        end = start + next_section + 1 if next_section != -1 else len(text)
        text = text[:start] + section + text[end:]
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n" + section


# ------------------------------------------------------------------
# Apply / print
# ------------------------------------------------------------------


def generate(client_key: str, transport: str, os_name: str, scope: str, entry: Dict) -> Tuple[Path, str]:
    """Return (config path, new file content) for the given client."""
    client = CLIENTS[client_key]
    path = client["path"](os_name, scope)
    include_cwd = client_key in ("vscode", "kimi-code")
    if transport == "stdio" and "command" in entry and "env" not in entry:
        entry = build_stdio_entry(
            entry["command"], entry.get("args", []), include_cwd, entry.get("extra_env")
        )
    if include_cwd and transport == "stdio" and "command" in entry and "cwd" not in entry:
        entry = dict(entry, cwd=str(REPO_ROOT))
    if "url" in entry and client_key == "vscode":
        # VS Code requires an explicit transport for non-stdio servers
        entry = dict(entry, type="http")

    existing = path.read_text(encoding="utf-8") if path.exists() else None
    if client["kind"] == "codex-toml":
        content = merge_codex_toml(existing, build_codex_toml_section(entry))
    else:
        content = merge_mcp_servers_json(
            existing, SERVER_NAME, entry, vscode_style=(client["kind"] == "vscode-servers-json")
        )
    return path, content


def claude_code_command(transport: str, entry: Dict) -> str:
    """The `claude mcp add ...` command equivalent of the config entry."""
    if transport == "http":
        return f"claude mcp add --transport http {SERVER_NAME} {entry['url']}"
    env = entry.get("env", STDIO_ENV)
    env_flags = " ".join(f"--env {k}={v}" for k, v in env.items())
    args = " ".join(entry.get("args", []))
    return f"claude mcp add {SERVER_NAME} {env_flags} -- {entry['command']} {args}".strip()


def write_config(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(content, encoding="utf-8")


# ------------------------------------------------------------------
# Interactive prompts
# ------------------------------------------------------------------


def _prompt_choice(question: str, options: List[Tuple[str, str]]) -> str:
    print(question)
    for i, (key, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        raw = input(f"Enter 1-{len(options)} [{options[0][0]}]: ").strip()
        if not raw:
            return options[0][0]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        for key, _ in options:
            if raw.lower() == key:
                return key
        print("Invalid choice.")


def interactive_entry(transport: str) -> Dict:
    if transport == "http":
        url = input(f"Server URL [{DEFAULT_HTTP_URL}]: ").strip() or DEFAULT_HTTP_URL
        return build_http_entry(url)

    servers = find_stdio_servers()
    options = [(s["id"], s["label"]) for s in servers] + [("custom", "Custom command/path")]
    choice = _prompt_choice("How should the client launch Trellis?", options)
    if choice == "custom":
        command = input("Command (e.g. python or path to exe): ").strip()
        args_raw = input("Args (space separated, e.g. server.py): ").strip()
        return build_stdio_entry(command, args_raw.split() if args_raw else [])
    selected = next(s for s in servers if s["id"] == choice)
    return build_stdio_entry(
        selected["command"], selected["args"], extra_env=selected.get("extra_env")
    )


def resolve_entry(transport: str, args: argparse.Namespace) -> Dict:
    """Build the server entry from flags, detection, or prompts."""
    scripted = any([args.write, args.print_only, args.client, args.transport])

    if transport == "http":
        if scripted:
            return build_http_entry(args.url or DEFAULT_HTTP_URL)
        url = input(f"Server URL [{DEFAULT_HTTP_URL}]: ").strip()
        return build_http_entry(url or DEFAULT_HTTP_URL)

    if args.command:
        return build_stdio_entry(args.command, args.args or [])

    servers = find_stdio_servers()
    if scripted:
        if not servers:
            raise SystemExit(
                "error: no Trellis server found (no .venv, no dist/trellis.exe). "
                "Use --command to specify one."
            )
        return build_stdio_entry(
            servers[0]["command"], servers[0]["args"], extra_env=servers[0].get("extra_env")
        )
    return interactive_entry("stdio")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate MCP client config for Trellis.")
    parser.add_argument("--client", choices=list(CLIENTS.keys()) + ["all-print", "other"])
    parser.add_argument("--transport", choices=["stdio", "http"])
    parser.add_argument("--scope", choices=["user", "project"])
    parser.add_argument("--url", help="HTTP server URL (default http://127.0.0.1:17317/mcp)")
    parser.add_argument("--command", help="stdio server command (skip auto-detection)")
    parser.add_argument("--args", nargs="*", help="stdio server arguments")
    parser.add_argument("--write", action="store_true", help="Merge into the client config file")
    parser.add_argument("--print", dest="print_only", action="store_true", help="Only print the config")
    args = parser.parse_args(argv)

    os_name = detect_os()
    scripted = any([args.write, args.print_only, args.client, args.transport]) or not sys.stdin.isatty()
    transport = args.transport or _prompt_choice(
        "Server type?",
        [("stdio", "stdio - client launches Trellis locally"), ("http", "http - connect to a running server")],
    )
    entry = resolve_entry(transport, args)

    if args.client and args.client != "all-print":
        client_keys = [args.client]
    elif args.client == "all-print":
        client_keys = list(CLIENTS.keys())
        args.print_only = True
    else:
        choice = _prompt_choice(
            "Which MCP client?",
            [(key, c["name"]) for key, c in CLIENTS.items()]
            + [("other", "Unknown / other platform - print all server info"),
               ("all-print", "Print config for all known clients")],
        )
        if choice == "all-print":
            client_keys = list(CLIENTS.keys())
            args.print_only = True
        else:
            client_keys = [choice]

    if client_keys == ["other"]:
        stdio = entry if transport == "stdio" else None
        if stdio is None:
            detected = find_stdio_servers()
            if detected:
                stdio = build_stdio_entry(
                    detected[0]["command"],
                    detected[0]["args"],
                    extra_env=detected[0].get("extra_env"),
                )
        url = entry.get("url", DEFAULT_HTTP_URL) if transport == "http" else DEFAULT_HTTP_URL
        print("\n" + build_linking_kit(os_name, stdio, url))
        print("\nRestart your MCP client to pick up the new server.")
        return 0

    for client_key in client_keys:
        client = CLIENTS[client_key]
        if args.scope:
            scope = args.scope
        elif len(client["scopes"]) == 1 or len(client_keys) > 1 or scripted:
            scope = client["scopes"][0]
        else:
            scope = _prompt_choice(
                f"Scope for {client['name']}?", [(s, s) for s in client["scopes"]]
            )
        if scope not in client["scopes"]:
            print(f"error: {client['name']} does not support scope '{scope}'")
            return 1

        if client_key == "claude-code" and scope == "user" and not args.write:
            print("\n=== Claude Code (user scope) - run this command ===")
            print(claude_code_command(transport, entry))
            continue

        path, content = generate(client_key, transport, os_name, scope, dict(entry))
        if args.write and not args.print_only:
            write_config(path, content)
            print(f"\n=== {client['name']} ({scope}) => written to {path} ===")
        else:
            print(f"\n=== {client['name']} ({scope}) => {path} ===")
        print(content)

    print("\nRestart your MCP client to pick up the new server.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
