# Changelog

## v0.5.0 — 2026-11-03

Benchmarks below: a Python/Django project with 875 Python files and ~11k
functions (1,402 indexed files in all), and a JavaScript project with ~100
files (184 indexed files). Sizes are characters of MCP tool output, the text
an agent reads; ~4 characters are one token.

### Engine: code-graph-mcp 0.164.0 → 0.184.0
Fork merge of 369 upstream commits. What changes for Trellis users:
- **Python call binding.** A call through an imported name no longer binds
  a same-named definition in the caller's own file (0.184.0). `C.m()` on a
  class of the same file binds the method Python's MRO runs (0.183.0).
  `import pkg` + `pkg.X()` binds what the module exports (0.176.0), and
  `from pkg import X` follows re-exports in `__init__.py` (0.175.0).
  Incremental indexing follows more import moves: a module that appears
  later, a new `__init__.py`, a function that became a method (0.169.0 to
  0.176.0).
- **TypeScript:** a call into an `interface` or `type` alias is `ambiguous`
  (it exists only at compile time), so impact leaves it out (0.175.0).
- **C++ / Rust:** a class whose constructor is defined in another file is
  answered instead of reported ambiguous (0.174.0); more Rust test code is
  recognised as test code (0.168.0, 0.169.0).
- **Index integrity.** A symlinked `index.db`, or a symlinked or non-regular
  `-wal`/`-shm`/`-journal` beside it, is refused (0.180.0); every CLI command
  refuses a symlinked `.code-graph` (0.170.1); a path through a symlink no
  longer adds a second row for a file (0.166.0); an index run that died is
  reported by `health-check` (0.171.0); queries made while the server builds
  the index wait for it instead of answering from a partial index (0.171.0).
- **Dependencies:** zstd 0.14.3 (four RUSTSEC-2026 advisories), rustls
  0.23.46, rustls-webpki 0.103.16, webpki-roots 1.0.10. Build-time bumps on
  top: anyhow, crossbeam-epoch, memmap2, tar, rand 0.9.5.
- **Fork patches retired.** Both Trellis security patches are upstream now,
  in stronger form (D#363 also guards the sidecars; D#240 checks every path
  segment), and were dropped in favour of upstream's code. The fork keeps
  its two freshness tests as regression guards; `FORK_PATCHES.md` updated.
- Rust tests: 479 storage and indexer-pipeline tests pass, including the
  fork's regression guards.
- Measured on the Django project: a full `trellis sync` (engine index plus
  Trellis call edges) takes 15 s. Trellis's own Python edges (3,085) agree
  with the engine's: none duplicates a confident engine edge, and the
  engine still misses calls Trellis resolves, such as aliased imports
  inside a function body (`from m import f as g; g()`).

### Fixed — incomplete or wrong answers
- **`trellis_list_modules` returned at most 15 modules** ("List all code
  modules"): the engine's MCP `project_map` trims its arrays to fit its token
  budget and says so only in `_array_truncations`. Trellis now reads the
  CLI's `map --json`, which is complete. Django project: 15 → 71 modules;
  JavaScript project: 15 → 23.
- **`trellis_get_boundary_map` never returned a dependency**: it read
  `dependencies`, and the engine calls the field `module_dependencies`.
  Django project: 0 → 209 dependencies; JavaScript project: 0 → 22. The
  visualizer's module nodes had the same problem with `symbol_count`.
- **`trellis_analyze_impact` counted every other function in the changed
  function's file as affected.** `addIcon` in the JavaScript project has 3
  callers and reported `affected_functions: 90`, 89 of them "unassigned".
  `affected_functions` is now direct plus indirect callers (3), and the
  "unassigned" feature entry is replaced by an `unmapped_functions` count.
  The visualizer still shows same-file functions, labelled as such.
- Feature risk flags listed the feature's own dependencies ("Feature has 12
  dependencies: …") on every impact; changing a feature does not affect what
  it depends on. Removed. Development pointers no longer start with a stray
  newline and indented sub-lines.
- **`trellis_trace_http_route` with an empty route** (list all Django routes)
  exceeded the 50,000-character response budget on 200 routes and returned
  only `response_too_large`. Routes are now one line each
  (`/path  METHOD handler, …`): 26,397 characters for all 200.
- **`trellis_trace_path` listed edges inside one file** when a file belongs
  to both features (`action.js -> action.js`). They are left out;
  `shared_files` lists such files.
- **`trellis_project_health` ranked test code as chokepoints**: the engine
  does not treat Django's `tests.py` as test code, so a `save` method in a
  `tests.py` with 246 callers ranked fifth. Chokepoints and surprising
  connections in test files are now dropped.
- **`trellis_project_health` caller counts included guesses**: the engine
  counts by-name (`ambiguous`) edges and test callers, so a serializer
  `update` method showed 151 callers. Each chokepoint now gives the
  confident production count, with the engine's figure beside it when the
  two differ (`4 callers; engine counts 151 incl. by-name guesses and
  tests`). The ranking itself is still the engine's.
- While the engine builds or rebuilds an index (once after this upgrade),
  CLI-backed tools (`trellis_list_modules`, `trellis_tour`, health) returned
  empty results with no explanation. They now return the engine's "index is
  still being built … retry" message as an error.
- A repository with `.code-graph` as a regular file (a committed symlink
  checked out as text, as git does on Windows without `core.symlinks`) made
  every call fail with `FileExistsError`. The error now names the file and
  the fix.
- `trellis_sync_status` guidance told agents to run `trellis_sync`, which is
  not an agent tool since v0.4.1; it now says to ask the operator to run
  `trellis sync`. Same for the unregistered-project error.
- `trellis_module_overview` matched `module_path` anywhere in a path
  (`ui` also matched `build/` and `guide/`) and treated `_` as a wildcard. It
  now takes the path itself and everything below it, falling back to a
  substring match only when nothing matches (`match` says which). It also
  ran SQLite on the event loop; it now runs in a thread after the index
  freshness check, and skips by-name (`ambiguous`) edges.
- `pyproject.toml` said version 0.3.0 while the release said 0.4.1.

### Added
- **`trellis_get_function` returns callers and callees** for functions and
  methods (`path:line name` lines; up to 15 callers and 30 callees, with
  counts). One call now covers a function's source and its neighbours,
  which used to take `trellis_get_function` plus `trellis_analyze_impact`,
  and callees were not available from any tool. Callees from production code
  into test files are dropped: on the Django project, Django queryset calls
  `qs.first()` / `qs.exclude()` were bound by the engine to `SSETests.first`
  and `HistoricalLaneTests.exclude`.

### Changed — smaller tool output
MCP tools return one line per item instead of a JSON object per item, and
leave out fields an agent does not use. The REST API is unchanged.

| Tool (call) | Before | After |
|---|---:|---:|
| `trellis_search_code` (5 results, JavaScript) | 3,088 | 399 |
| `trellis_search_code` (5 results, Django) | 1,695 | 300 |
| `trellis_ast_search` (10 classes) | 1,925 | 673 |
| `trellis_module_overview` (12 files) | 17,676 | 6,950 |
| `trellis_feature_info` (Icons) | 7,059 | 3,522 |
| `trellis_feature_info` (Agent Runs, Django) | 6,781 | 3,367 |
| `trellis_analyze_impact` (`addIcon`) | 1,234 | 923 |
| `trellis_tour` (Django, 71 modules) | 16,681 | 10,916 |
| `trellis_tour` (JavaScript) | 4,570 | 2,612 |
| `trellis_project_health` (Django) | 11,571 | 5,005 |
| `trellis_knowledge_graph` (10 notes) | 13,386 | 7,903 |

- Search results no longer embed each function's source; they read
  `path:start-end type name(signature)`. The source is one
  `trellis_get_function` call away.
- `trellis_module_overview`: `line kind name` per symbol; outgoing edges as
  `target_file relation xN`.
- `trellis_feature_info`: `functions_sample` and `hot_functions` as
  `path:line name` lines; the note no longer repeats `related_notes`.
- `trellis_trace_path`: `src_file:caller -> dst_file:callee (relation)`;
  it used to name only the caller.
- `trellis_project_health`: chokepoints as one line with betweenness
  rounded to 4 places; each import cycle as its loop, size and up to 8
  files (a cycle listed 132 files); surprising connections as one line.
- `trellis_tour`: `path [role] uses: … | used by N | key: …`; modules with
  no symbols and no imports either way (13 of 71 on the Django project, all
  `migrations/` folders) are counted in `skipped_empty`.
- `trellis_get_boundary_map`: `from -> to (N imports)`, most imports first,
  at most 150 (`dependency_count`, `dependencies_truncated`).
- `trellis_knowledge_graph`: note excerpts of 300 characters (was 2,000);
  `trellis_get_note` returns the full text.
- Shorter `trellis_sync_status` description (every agent pays for tool
  descriptions in its context).
- The skill document describes the new output forms and the "index is still
  being built" message.

### Changed — build and release
- `scripts/security_scan.py`: `cargo audit` skips the four zip 7.2.0
  advisories (RUSTSEC-2026-0367/0368/0369/0429) only after `cargo tree`
  confirms zip is not in the `--no-default-features` build Trellis ships
  (it comes only with the `embed-model` feature, through candle-core;
  upstream CI ignores the same four). Upstream's developer kits (`tasks/`,
  `evals/`, Python under `scripts/`) are not scanned: they are run by hand
  and are not part of the crate. A test-only `mkfifo` call is allowlisted.
- `scripts/build_bridge.py` updates every version of a crate the lockfile
  holds more than once; `cargo update -p rand` used to fail and leave rand
  0.9.2 in the build.
- `django_routes`: the urls.py evaluator method is `evaluate`, not `eval`
  (it never called Python's `eval`, but the security scan could not tell).
- Tests run with a private `TRELLIS_DATA_DIR`; integration tests used to add
  project entries to the real `~/.trellis`. With a `tui-image-editor-fixture`
  checkout present, all 212 tests pass on engine 0.184.0 (200 without it).

### Breaking changes (read before upgrading)
1. **One-time full re-index** on first use of each project: the engine's
   index version goes 113 → 126. Until it finishes, CLI-backed tools return
   "index is still being built"; retry. (Django project: 15 s.)
2. **MCP output shapes changed** for the tools in the table above: lists of
   objects became lists of strings. Agents read either; scripts that parse
   MCP tool JSON must be updated. The REST API and the `trellis-client` SDK
   are unchanged.
3. **`trellis_analyze_impact`**: `affected_functions` no longer includes
   same-file functions; `feature_impacts` no longer has an `"unassigned"`
   entry (see `unmapped_functions`).
4. The bundled engine is 0.184.0. The linux engine assets for docker
   consumers must be rebuilt for this tag (`make engine-release`).
5. **The bundled release requires a bearer token over HTTP** (see "Changed
   — bundled release" below). HTTP MCP clients of the bundle must send
   `Authorization: Bearer <token>`; stdio MCP clients are unaffected.

### Fixed — call graph accuracy
- **Python calls follow imports.** Calls were matched by name only: same
  file first, else the first function anywhere with that name, and
  `obj.method()` kept only `method`. The indexer now resolves
  `from x import y`, `import x as m` + `m.f()`, relative imports, `__init__`
  re-exports, `self`/`cls`/`super()` through project base classes, and nested
  functions (including ones called before they are defined). Calls it cannot
  pin to one definition, and calls to parameters or local variables that
  shadow a module function, are skipped instead of guessed. Each run removes
  Trellis's own edges that no longer resolve.
  - Trellis-owned call edges: 14,153 → 2,932 (13,911 wrong edges removed);
    total edges 86,134 → 74,913.
  - Example: a helper defined in four modules had 33 callers attributed,
    11 of them confident; it now has 1. Its real callers in other modules,
    previously missing, are found.
- **Decorated functions were never indexed as callers** (the engine records
  them at the decorator line): 686 functions in the Python benchmark,
  including most API views.
- **Callers matched by node id, not by name.** `trellis_analyze_impact`
  returns the `callers` list (`file:line name`), plus `test_callers` and
  `ambiguous_callers`. Engine by-name guesses marked ambiguous are reported
  separately and dropped when the same caller is confidently bound to
  another same-named function; on the benchmark every one of them pointed
  at the wrong function. Risk uses confirmed non-test callers, and is no
  longer LOW when only unconfirmed callers exist (`risk_note`).
- `file.py:function`, `file.py:Class.method` and `package.module.function`
  keep the file part in `trellis_analyze_impact` and `trellis_get_function`
  (it used to be dropped, returning a same-named function from another
  file). Other matches are listed in `other_definitions`. Unknown symbols
  return an error instead of risk LOW with 0 callers.
- **Edits re-link immediately.** Re-indexing a file deletes every edge
  into it, including Trellis edges from unchanged files, and the repair ran
  at most once a minute and only when a file had lost all its edges.
  Changes are now detected on each query from `files.indexed_at`, node ids
  and edge counts (a same-second re-index, or one that lands during a
  re-link, is caught too). A failed re-link retries after 60 s instead of
  on every call. The saved state is two hashes instead of a per-file map.
- Feature `hot_functions` and `trellis_detect_hotspots` ranked test helpers
  first (e.g. `create`/`get` in test classes with 1,000+ edges). They now
  count distinct confident callers from non-test code (hotspots also count
  importing modules, as `used_by`).
- Feature function lists dropped same-named functions in different files.
- project.md file patterns had three different matchers; the main one
  turned `**` into `.[^/]*` and matched anywhere in a path. One anchored
  matcher is used everywhere.
- Affected decisions were listed once per matching constraint.

### Fixed — JavaScript augmentation
- The pattern indexer walked the whole repository (virtualenvs, build
  output, nested checkouts) and parsed each file four times. It now scans
  the `.js` files the index contains, parsing each once: 600+ s → 0.3 s on
  the Python benchmark (80 `.js` files); 8.4 s → 0.6 s on the JavaScript
  benchmark, same edges except those inside built bundles under `dist/`.
- Only the last `getComponent()` call site per component got an edge;
  every call site does now (+51 caller edges on the JavaScript benchmark).
- Class lookup used hard-coded file names. It now picks the class in the
  caller's own file, else the file the caller's `import` names (relative
  and alias specifiers), else the nearest directory, preferring non-test
  code. All previous correct edges are kept.
- Augmented edges are marked and replaced on each run. Edges left by older
  versions are removed when they are cross-file (the engine labels every
  cross-file call `inferred`/`ambiguous`, so unmarked `extracted` ones can
  only be leftovers); same-file ones are correct and kept.
- An engine `ambiguous` guess for a pair no longer blocks a confirmed edge.

### Fixed — other
- Note updates dropped tags, title and created date when they were not
  passed again (MCP and REST). A title with no ASCII letters made every
  unknown `[[link]]` resolve to it; partial link matches need whole words.
- Two ids for one repository started two engine servers on one index,
  which could kill each other; they now share one bridge.
- A CLI fallback stopped the engine server (and its file watcher) and slept
  0.5 s; only index-writing commands do that now. CLI timeouts are reported
  as failures instead of raising.
- `trellis sync --incremental` and incremental MCP sync only ran a status
  check; they now re-index changed files and re-link edges.
- Wrappers returned shapes that no longer matched the engine:
  `find_references` (dict, not list), `get_call_graph` (no `symbol`/`nodes`),
  `module_overview` (no `files`), CLI impact (`total_callers` never parsed;
  nested callers flattened). The visualizer impact graph was always empty;
  it is now built from the impact analysis.
- `trellis_trace_path` ran SQLite on the event loop, could exceed SQLite's
  parameter limit on large features, matched module names case-sensitively
  and counted ambiguous edges.
- Diff analysis refreshes the index before finding changed functions,
  deduplicates by file + name, and reports `overall_risk` (it was always
  `unknown`: risk levels were compared case-sensitively).
- `trellis_get_function` with `file:name` for a non-function symbol
  (constant, interface, type) asks the engine and keeps only a hit in that
  file.
- The project.md template used a layout the parser ignores; it now
  produces a parseable feature section. Loading a spec no longer creates
  directories or reads the server's working directory.
- Telemetry writes moved off the event loop, with a per-process temp file.
- Zombie-process cleanup kept the wrong PIDs and deleted `index.lock`.
- Visualizer risk colour was always "low"; symbol API routes accept `/`.
- `launcher` resolved the repository root to `src/` outside frozen builds.

### Changed — bundled release
- **The bundled release requires a token over HTTP.** It used to set
  `TRELLIS_ALLOW_NO_AUTH=true`, so any local program could call the API.
  It now uses `TRELLIS_API_KEY`, or a token kept in
  `<TRELLIS_DATA_DIR>/api-token` (created on first start, printed to
  stderr). The UI needs no change (the served page carries the token);
  HTTP MCP clients connecting to the bundle must send
  `Authorization: Bearer <token>`. stdio MCP is unaffected.
- JavaScript integration tests: 7 of 18 failed (index read before it was
  built, and wrapper shapes above); all 18 pass.

### Added — Django routes and impact
- **Django / Django REST Framework route tracing** in
  `trellis_trace_http_route`: `path()`/`re_path()`/`url()`, `include()`,
  `as_view()` with action mappings, routers with `@action`, module constants
  and simple loops. Dispatch follows Django (first match, typed converters);
  placeholder ids fall back to a loose match (flagged in `note`) instead of
  a catch-all route. An empty `route_path` lists all routes. When no Django
  route matches, the engine's tracer is tried (projects can mix
  frameworks). Benchmark: 200 routes found; cold trace 2.3 s, cached 0.04 s.
- `trellis_analyze_impact`: classes (callers of the class and its methods)
  and `depth_mode="deep"` (`indirect_callers`, up to 3 levels).

### Performance
- Impact analysis: four engine round-trips (each could fall back to a CLI
  process) → one SQLite read, ~5 ms. Unchanged repeat queries ~10 ms.
- Python call indexing: 6.0 s → 3.0 s cold, 0.2 s warm (files cached by
  mtime/size).
- Hot functions: one SQLite connection per function → one query (10–30 ms
  per feature).
- `project.md` parsing and index-path lookup are cached.
- Removed 30 MB of stray server logs from the repository.

### Artifacts
- `out/trellis-v0.5.0-windows-amd64.zip` (engine 0.184.0 exe bundled inside).

## v0.4.1 — 2026-10-11

### Added
- **3-way component split** — `trellis [mcp|api|ui|sync]` subcommands; each
  component starts alone. No-subcommand still runs the MCP stdio server, so
  existing MCP client configs work unchanged.
- **REST API admin surface** — `GET /projects` (all projects + index/sync
  status), `GET /projects/{id}` (nodes/files/modules, spec status, notes
  total + per-tag counts), `DELETE /projects/{id}?delete_data=` (unregister
  and evict; never touches the repo), `GET /stats/usage` (MCP call telemetry:
  totals, per-tool/error counts, last 50 calls, persisted across restarts).
- **REST search** — `GET /projects/{id}/search?q=&limit=` (the MCP tool
  `trellis_search_code` worked all along; REST-only integrations had no route).
- **`trellis-client` Python SDK** (`client/`) — 22 methods, `TrellisError`,
  context-manager support; installable with `pip install ./client`.
- **Docker sidecar** — multi-stage `Dockerfile` (Rust engine + slim Python
  runtime) serving REST + UI + `/mcp` on one port; reference
  `docker-compose.yml`; `.dockerignore`.
- **UI in every mode** — the API process serves the visualizer at `/` by
  default; `TRELLIS_UI=off` for headless deployments. No separate UI
  deployment needed.
- **Trusted-network mode** — non-loopback binds (`TRELLIS_HOST=0.0.0.0`)
  relax Host/Origin checks but always require the bearer token; server
  refuses to start on a network interface without `TRELLIS_API_KEY`.
  `TRELLIS_TRUSTED_HOSTS` allowlists extra peers. Browser access to the UI
  on a network bind: `http://host:17317/?token=<key>`.
- Engine **0.156.0 → 0.164.0** (fork merge, 153 upstream commits): Python
  import/decorator/`@overload` fixes, JS/TS member-function nodes,
  route→handler cross-file tracing, running-server index integrity, CLI
  `impact`/`callgraph --node-id`, `fts_only` reporting.
- `scripts/build_engine_assets.py` + `make engine-release` — builds
  linux/amd64 + linux/arm64 engine binaries in Docker and attaches them
  (plus `checksums.sha256`) to the GitHub release for downstream docker
  consumers (Windows-only consumers can ignore this; the Windows zip
  bundles the engine exe).

### Security
- **Closed token leak on network binds** — `/` previously served the
  token-embedded UI page without credentials in every mode; on a network
  bind that exposed the API key to anyone reaching the port. `/` now requires
  credentials in trusted mode.
- **Guard now honors `TRELLIS_ALLOW_NO_AUTH`** (previously parsed but
  ignored — loopback binds only, no effect in trusted mode).
- **DELETE endpoint hardening** — URL-supplied project ids validated before
  use as registry keys/paths (path-traversal defense).
- Engine fork: retained both `TRELLIS FORK` patches (symlinked `index.db`
  refusal, freshness no-follow-symlinks) — still unpatched upstream;
  upstream's own `.git`-symlink fix (`bf1148c8`) included.
- Security scan passes clean; `cargo audit` clean.

### Changed
- **`trellis_sync` is no longer an MCP tool** (operator-only; index via the
  `trellis sync` CLI, monitor via `trellis_sync_status`). Opt in with
  `TRELLIS_ENABLE_MCP_SYNC=1`. Server instructions, bundled fallback, and
  the skill document updated to match; `trellis_sync_status` now also
  reports the `project_md` status so agents can still discover the
  mandatory project.md step.
- `server.py` is a thin CLI dispatcher; code lives in
  `src/trellis/{core,mcp_server,api,ui}.py`. The 20 remaining MCP tools are
  unchanged.
- **Impact risk for no-caller symbols is now `unknown`** (engine behavior;
  previously `low`). UI/clients parsing `risk_level` see the new value.
- **One-time full re-index** on first query after upgrade (engine index
  schema 72 → 113); slower first call per project, then normal.
- `visualizer.html` uses same-origin API by default; `?api=<url>` override.
- Project registry data now also stores usage stats (`usage.json`).
- Pydantic floor raised to `>=2.10.0`.

### Removed
- `start_server.py` (stale dev launcher — use `trellis ui`/`trellis api`).
- `auth.py` (dead code; folded into the guard in `core.py`).
- `from src.trellis import app` export (referenced the deleted prototype).

### Breaking changes (read before upgrading)
1. **`start_server.py` is gone.** Deployments using it must switch to
   `trellis ui` (same port, same behavior).
2. **`trellis_sync` is no longer an MCP tool.** Agents must not be told to
   call it; indexing is the operator's job (`trellis sync` CLI). Enable
   `TRELLIS_ENABLE_MCP_SYNC=1` to restore the old behavior.
3. **`TRELLIS_ALLOW_NO_AUTH` now actually disables the token check** on
   loopback binds. Dev setups relying on the old (accidental) enforcement
   should set an explicit `TRELLIS_API_KEY`.
4. **UI on a network bind requires credentials** — open it as
   `/?token=<key>`; plain `/` returns 401. Loopback UIs are unchanged.
5. **`risk_level` may be `"unknown"`** where it was `"low"` (engine).
6. **First query per indexed project triggers a full rebuild** after the
   engine upgrade; expect a slow first call (minutes for large repos).
7. `GET /projects` response shape enriched (added `index_exists`, `nodes`,
   `files`, `sync` fields); clients should tolerate new fields.

### Artifacts
- `out/trellis-v0.4.1-windows-amd64.zip` (engine exe bundled inside).

---

## v0.3.0 and earlier

See `git log` (no changelog was maintained before v0.4.1).
