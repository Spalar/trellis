# trellis-client

Python client SDK for the [Trellis](../docs/TRELLIS_OVERVIEW.md) REST API
(`src/trellis/api.py`).

## Install

```bash
pip install .
# or, from the repo root:
pip install -e client
```

Requires Python 3.10+ and `httpx>=0.27`.

## Quickstart

```python
from trellis_client import TrellisClient, TrellisError

client = TrellisClient(
    base_url="http://127.0.0.1:17317",  # default
    api_key="...",  # or set TRELLIS_API_KEY in the environment
)

print(client.health())
print(client.list_projects())

try:
    graph = client.graph("my-project")
except TrellisError as e:
    print(f"HTTP {e.status_code}: {e.message}")

client.close()
```

Context-manager usage closes the underlying HTTP connection automatically:

```python
with TrellisClient() as client:
    report = client.feature_impact("my-project", "MyClass.my_method")
```

The API key is sent as `Authorization: Bearer <key>` on every request. When
`api_key` is omitted, the `TRELLIS_API_KEY` environment variable is read once
at construction.

## API surface

See the interactive server docs at `http://127.0.0.1:17317/docs` (FastAPI
Swagger UI) for request/response schemas. The client methods mirror the REST
endpoints one-to-one: `health`, `list_projects`, `graph`, `sync`,
`sync_status`, `tour`, `project_health`, `impact`, `feature_impact`,
`feature_pointers`, `feature_context`, `feature_divergence`, `get_spec`,
`save_spec`, `spec_alignment`, `knowledge_graph`, `list_notes`, `get_note`,
`create_note`, `delete_note`.

Non-2xx responses raise `TrellisError` with the server's `error` message and
the HTTP status code.
