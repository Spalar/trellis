# Trellis Core - Makefile
.PHONY: install dev trellis-dev-vscode run run-http test lint format clean check

PYTHON ?= python
VENV := .venv
ifeq ($(OS),Windows_NT)
	BIN := $(VENV)\Scripts
	PYTHON_VENV := $(BIN)\python.exe
else
	BIN := $(VENV)/bin
	PYTHON_VENV := $(BIN)/python
endif

PIP := $(BIN)/pip

$(PYTHON_VENV):
	$(PYTHON) -m venv $(VENV)

install: $(PYTHON_VENV)
	$(PYTHON_VENV) -m pip install --upgrade pip
	$(PYTHON_VENV) -m pip install -e .[dev]
	@echo "Trellis installed. Run 'make dev' for stdio or 'make run-http' for HTTP."

dev:
	@echo "Copilot MCP stdio config:"
	@echo "  name: trellis-core"
	@echo "  type: stdio"
	@echo "  command: $(abspath $(PYTHON_VENV))"
	@echo "  args: [\"$(abspath server.py)\"]"
	@echo "  cwd: $(abspath .)"
	@echo "  env: { \"TRELLIS_ALLOW_NO_AUTH\": \"true\", \"FASTMCP_SHOW_SERVER_BANNER\": \"false\", \"FASTMCP_LOG_LEVEL\": \"ERROR\" }"
ifeq ($(OS),Windows_NT)
	set TRELLIS_ALLOW_NO_AUTH=true && set FASTMCP_SHOW_SERVER_BANNER=false && set FASTMCP_LOG_LEVEL=ERROR && $(PYTHON_VENV) -m server
else
	TRELLIS_ALLOW_NO_AUTH=true FASTMCP_SHOW_SERVER_BANNER=false FASTMCP_LOG_LEVEL=ERROR $(PYTHON_VENV) -m server
endif

trellis-dev-vscode:
	$(PYTHON_VENV) -c "import json, os; cfg={'servers': {'trellis-core': {'type': 'stdio', 'command': os.path.abspath(r'$(PYTHON_VENV)'), 'args': [os.path.abspath('server.py')], 'cwd': os.path.abspath('.'), 'env': {'TRELLIS_ALLOW_NO_AUTH': 'true', 'FASTMCP_SHOW_SERVER_BANNER': 'false', 'FASTMCP_LOG_LEVEL': 'ERROR'}}}, 'inputs': []}; print(json.dumps(cfg, indent=2))"

run: dev

run-http:
	@echo "Starting Trellis in HTTP mode on port 17317 (no-auth local dev)..."
ifeq ($(OS),Windows_NT)
	set TRELLIS_TRANSPORT=http && set TRELLIS_HOST=127.0.0.1 && set TRELLIS_PORT=17317 && set TRELLIS_ALLOW_NO_AUTH=true && $(PYTHON_VENV) server.py
else
	TRELLIS_TRANSPORT=http TRELLIS_HOST=127.0.0.1 TRELLIS_PORT=17317 TRELLIS_ALLOW_NO_AUTH=true $(PYTHON_VENV) server.py
endif

check:
	curl -sSf http://localhost:17317/health -o nul 2>&1 || echo Health check failed

test:
	$(PYTHON_VENV) -m pytest -v

lint:
	$(PYTHON_VENV) -m ruff check .
	$(PYTHON_VENV) -m ruff format --check .

format:
	$(PYTHON_VENV) -m ruff format .

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache *.egg-info dist build
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
