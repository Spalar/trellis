"""FastAPI backend for Trellis.

REST API exposing the graph, feature, spec, and knowledge-graph endpoints.
Path-for-path compatible with the custom routes that previously lived on the
FastMCP HTTP transport, so the visualizer works unchanged against either.
All shared state lives in src.trellis.core; the local-HTTP guard middleware
is installed here. The UI entry point (src/trellis/ui.py) mounts this app.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.responses import FileResponse, JSONResponse

from src.trellis import core


def create_api_app(version: str) -> FastAPI:
    """Build the FastAPI backend app with every REST endpoint registered."""
    app = FastAPI(title="trellis-api", version=version)
    app.add_middleware(core.LocalHttpGuard)

    @app.get("/health")
    async def health():
        """Health check."""
        return JSONResponse({"status": "ok", "version": version})

    @app.get("/vendor/{filename}")
    async def vendor_assets(filename: str):
        """Serve vendored JS libraries (d3, marked) — local, no CDN.

        Loaded via <script src> from the UI page, which cannot send custom
        headers, so these public static assets are exempt from the token check
        (the guard middleware whitelists the /vendor/ prefix too).
        """
        if not re.fullmatch(r"[A-Za-z0-9._-]+", filename):
            return JSONResponse({"error": "Invalid filename"}, status_code=400)
        asset = core.TRELLIS_ROOT / "vendor" / filename
        if not asset.is_file():
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(asset)

    @app.get("/graph/{project_id}")
    async def graph_get(project_id: str):
        """Get graph data."""
        try:
            bridge = core.get_bridge(project_id)
            graph = await asyncio.to_thread(bridge.get_graph_for_visualizer)
            return JSONResponse(graph)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/graph/{project_id}/sync")
    async def graph_sync(project_id: str):
        """Full index rebuild. Heavy — edits are otherwise indexed automatically
        by the server-side file watcher (armed by the bridge on every spawn), so
        this is only needed after large external changes or when stale."""
        try:
            bridge = core.get_bridge(project_id)
            result = await asyncio.to_thread(bridge.sync_project)
            return JSONResponse(
                {
                    "success": result.get("success", False),
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                }
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/graph/{project_id}/status")
    async def graph_status(project_id: str):
        """Index status including whether the file watcher is active."""
        try:
            bridge = core.get_bridge(project_id)
            return JSONResponse(await asyncio.to_thread(bridge.health_check))
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/graph/{project_id}/tour")
    async def graph_tour(project_id: str, path: str = ""):
        """Dependency-ordered reading tour (foundational -> entry-point modules)."""
        try:
            bridge = core.get_bridge(project_id)
            result = await asyncio.to_thread(bridge.tour, path or None)
            return JSONResponse(result)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/graph/{project_id}/health")
    async def graph_health(project_id: str, limit: int = 0):
        """Architecture-health snapshot (chokepoints, import cycles, surprising edges)."""
        try:
            bridge = core.get_bridge(project_id)
            result = await asyncio.to_thread(
                bridge.project_health, limit if limit else 15
            )
            return JSONResponse(result)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/graph/{project_id}/impact/{symbol}")
    async def graph_impact(project_id: str, symbol: str):
        """Get impact graph."""
        try:
            bridge = core.get_bridge(project_id)
            graph = await asyncio.to_thread(bridge.get_impact_graph, symbol)
            return JSONResponse(graph)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/feature/{project_id}/impact/{symbol}")
    async def feature_impact(project_id: str, symbol: str):
        """Get feature impact report."""
        try:
            bridge = core.get_bridge(project_id)
            report = await asyncio.to_thread(bridge.get_feature_impact, symbol)
            return JSONResponse(report)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/feature/{project_id}/pointers/{symbol}")
    async def feature_pointers(project_id: str, symbol: str):
        """Get development pointers."""
        try:
            bridge = core.get_bridge(project_id)
            pointers = await asyncio.to_thread(bridge.get_development_pointers, symbol)
            return JSONResponse({"pointers": pointers})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/feature/{project_id}/context/{symbol}")
    async def feature_context(project_id: str, symbol: str):
        """Get feature context for a symbol."""
        try:
            bridge = core.get_bridge(project_id)
            context = await asyncio.to_thread(bridge.get_feature_context, symbol)
            if context:
                return JSONResponse(context)
            return JSONResponse({"error": "No feature context found"}, status_code=404)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/feature/{project_id}/divergence/{symbol}")
    async def feature_divergence(project_id: str, symbol: str):
        """Check feature divergence for a symbol."""
        try:
            bridge = core.get_bridge(project_id)
            warnings = await asyncio.to_thread(bridge.check_feature_divergence, symbol)
            return JSONResponse(
                {
                    "symbol": symbol,
                    "divergence_warnings": warnings,
                    "has_divergence": len(warnings) > 0,
                }
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/spec/{project_id}")
    async def spec_get(project_id: str):
        """Return the project spec, or a template when none exists yet."""
        spec = await asyncio.to_thread(core.spec_manager.load_spec, project_id)
        repo_spec_path = str(Path(core.resolve_project_path(project_id)) / "project.md")
        if spec is None:
            template = await asyncio.to_thread(
                core.spec_manager.create_template, project_id
            )
            return JSONResponse(
                {
                    "project_id": project_id,
                    "status": "no_spec",
                    "content": template,
                    "path": repo_spec_path,
                }
            )
        return JSONResponse(
            {
                "project_id": project_id,
                "status": "ok",
                "content": spec.content,
                "path": spec.source_path,
            }
        )

    @app.post("/spec/{project_id}")
    async def spec_save(project_id: str, request: Request):
        """Save the project spec and refresh auto-generated feature notes."""
        body = await request.json()
        content = body.get("content", "")
        path = await asyncio.to_thread(core.spec_manager.save_spec, project_id, content)

        # Refresh the auto-generated feature notes so the doc graph follows
        # the spec the agent just wrote.
        try:
            from src.trellis.spec_note_sync import sync_feature_notes

            notes_sync = await asyncio.to_thread(
                sync_feature_notes, core.resolve_project_path(project_id)
            )
        except Exception:
            notes_sync = None

        return JSONResponse(
            {
                "project_id": project_id,
                "status": "ok",
                "path": str(path),
                "feature_notes": notes_sync,
            }
        )

    @app.get("/spec/{project_id}/alignment")
    async def spec_alignment(project_id: str):
        """Verify how well project.md features align with the synced code graph.

        For each parsed feature: which indexed files its glob patterns match, how
        many functions map to it, and what is wrong or missing (no patterns,
        patterns matching nothing, zero functions, missing description).
        Also reports indexed files not covered by any feature.
        """
        try:
            from src.trellis.feature_impact import ProjectContextParser
            from src.trellis.utils import resolve_code_graph_db

            bridge = core.get_bridge(project_id)
            parser = await asyncio.to_thread(
                ProjectContextParser, str(bridge.project_path)
            )
            features = parser.get_all_features()

            # All indexed source files from the code graph DB
            files = []
            db_path = resolve_code_graph_db(str(bridge.project_path))
            if db_path.exists():
                import sqlite3

                conn = sqlite3.connect(str(db_path))
                files = [row[0] for row in conn.execute("SELECT path FROM files")]
                conn.close()

            def match_files(patterns):
                matched = []
                for path in files:
                    for pattern in patterns:
                        regex = pattern.replace("**", ".*").replace("*", "[^/]*")
                        if re.search(regex, path):
                            matched.append(path)
                            break
                return matched

            feature_rows = []
            covered_files = set()
            for name, feature in features.items():
                matched = match_files(feature.file_patterns)
                covered_files.update(matched)
                functions = await asyncio.to_thread(bridge.get_feature_functions, name)
                issues = []
                if not feature.file_patterns:
                    issues.append("no file patterns - add a '### Files' section")
                elif not matched:
                    issues.append("file patterns match no indexed files")
                if not functions:
                    issues.append("no functions mapped to this feature")
                if not feature.description:
                    issues.append("no description")
                feature_rows.append(
                    {
                        "name": name,
                        "description": feature.description,
                        "file_patterns": feature.file_patterns,
                        "matched_files": matched,
                        "matched_file_count": len(matched),
                        "function_count": len(functions),
                        "decision_count": len(feature.decisions),
                        "constraints_count": len(feature.constraints),
                        "dependencies": feature.dependencies,
                        "issues": issues,
                    }
                )

            unmapped_files = [f for f in files if f not in covered_files]
            return JSONResponse(
                {
                    "project_id": project_id,
                    "spec_status": core.project_md_status(bridge.project_path),
                    "total_files": len(files),
                    "covered_file_count": len(covered_files),
                    "unmapped_files": unmapped_files,
                    "features": feature_rows,
                    "summary": {
                        "feature_count": len(feature_rows),
                        "features_with_issues": sum(
                            1 for f in feature_rows if f["issues"]
                        ),
                        "fully_aligned": all(not f["issues"] for f in feature_rows)
                        and not unmapped_files,
                    },
                }
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/knowledge-graph/{project_id}")
    async def knowledge_graph_get(project_id: str):
        """Get knowledge graph data (notes + code)."""
        try:
            from src.trellis.knowledge_graph import NoteGraph

            resolved = core.resolve_project_path(project_id)
            graph = await asyncio.to_thread(NoteGraph, resolved)
            data = await asyncio.to_thread(graph.build_graph, include_code=True)
            return JSONResponse(data)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/note/{project_id}/{note_id}")
    async def note_get(project_id: str, note_id: str):
        """Get a knowledge note."""
        try:
            from src.trellis.knowledge_graph import NoteGraph

            resolved = core.resolve_project_path(project_id)
            graph = await asyncio.to_thread(NoteGraph, resolved)
            note = await asyncio.to_thread(graph.get_note, note_id)
            if not note:
                return JSONResponse(
                    {"error": f"Note '{note_id}' not found"}, status_code=404
                )
            return JSONResponse(
                {
                    "id": note.id,
                    "title": note.title,
                    "content": note.content,
                    "tags": note.tags,
                    "links": note.links,
                    "mentions": note.mentions,
                    "backlinks": await asyncio.to_thread(graph.get_backlinks, note_id),
                }
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/note/{project_id}/{note_id}")
    async def note_save(project_id: str, note_id: str, request: Request):
        """Save a knowledge note."""
        try:
            from src.trellis.knowledge_graph import NoteGraph

            body = await request.json()
            content = body.get("content", "")
            title = body.get("title", note_id)
            tags = body.get("tags", [])
            resolved = core.resolve_project_path(project_id)
            graph = await asyncio.to_thread(NoteGraph, resolved)
            note = await asyncio.to_thread(
                graph.save_note, note_id, content, title=title, tags=tags
            )
            return JSONResponse(
                {"status": "ok", "note_id": note.id, "title": note.title}
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.delete("/note/{project_id}/{note_id}")
    async def note_delete(project_id: str, note_id: str):
        """Delete a knowledge note."""
        try:
            from src.trellis.knowledge_graph import NoteGraph

            resolved = core.resolve_project_path(project_id)
            graph = await asyncio.to_thread(NoteGraph, resolved)
            success = await asyncio.to_thread(graph.delete_note, note_id)
            if not success:
                return JSONResponse(
                    {"error": f"Note '{note_id}' not found"}, status_code=404
                )
            return JSONResponse(
                {"status": "ok", "message": f"Note '{note_id}' deleted"}
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/notes/{project_id}")
    async def notes_list(project_id: str):
        """List all knowledge notes."""
        try:
            from src.trellis.knowledge_graph import NoteGraph

            resolved = core.resolve_project_path(project_id)
            graph = await asyncio.to_thread(NoteGraph, resolved)
            notes = [
                {
                    "id": n.id,
                    "title": n.title,
                    "tags": n.tags,
                    "updated": n.updated_at,
                }
                for n in graph.notes.values()
            ]
            return JSONResponse({"notes": notes, "count": len(notes)})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/projects")
    async def list_projects():
        """List registered projects with per-project sync summaries.

        Projects register themselves in ~/.trellis/projects/<id>/project.json
        when synced; the code graph itself lives in each project's own
        .code-graph directory. Each entry reports whether the index exists,
        its node/file counts (null when missing or unreadable), and the last
        known sync state.
        """
        from src.trellis.utils import list_registered_projects

        registered = await asyncio.to_thread(list_registered_projects)
        projects = []
        for pid in registered:
            summary = await asyncio.to_thread(core.project_sync_summary, pid)
            if "error" in summary:
                continue
            projects.append(
                {
                    "id": pid,
                    "name": pid,
                    "path": summary["repo_path"],
                    "index_exists": summary["index_exists"],
                    "nodes": summary["nodes"],
                    "files": summary["files"],
                    "sync": summary["sync"],
                }
            )

        return JSONResponse({"projects": projects})

    @app.get("/projects/{project_id}")
    async def project_detail(project_id: str):
        """Full detail for one project: index, sync, spec, and notes overview."""
        from src.trellis.utils import list_registered_projects

        registered = await asyncio.to_thread(list_registered_projects)
        if project_id not in registered:
            return JSONResponse(
                {"error": f"Unknown project '{project_id}'"}, status_code=404
            )
        summary = await asyncio.to_thread(core.project_sync_summary, project_id)
        if "error" in summary:
            return JSONResponse({"error": summary["error"]}, status_code=404)

        # Module count comes from the bridge's project map; only when the
        # index exists, and lazily (no subprocess at app-construction time).
        modules = None
        if summary["index_exists"]:
            try:
                bridge = core.get_bridge(project_id)
                pmap = await asyncio.to_thread(bridge.project_map)
                modules = len(pmap.get("modules", []))
            except Exception:
                modules = None

        notes = {"total": 0, "by_tag": {}}
        try:
            from src.trellis.knowledge_graph import NoteGraph

            resolved = core.resolve_project_path(project_id)
            graph = await asyncio.to_thread(NoteGraph, resolved)
            by_tag: dict[str, int] = {}
            for note in graph.notes.values():
                for tag in note.tags:
                    by_tag[tag] = by_tag.get(tag, 0) + 1
            notes = {"total": len(graph.notes), "by_tag": by_tag}
        except Exception:
            # Notes must never fail the detail payload — report an empty set.
            pass

        return JSONResponse(
            {
                "id": project_id,
                "name": project_id,
                "path": summary["repo_path"],
                "index": {
                    "exists": summary["index_exists"],
                    "nodes": summary["nodes"],
                    "files": summary["files"],
                    "modules": modules,
                },
                "sync": summary["sync"],
                "spec": core.project_md_status(summary["repo_path"]),
                "notes": notes,
            }
        )

    @app.delete("/projects/{project_id}")
    async def project_delete(project_id: str, delete_data: bool = False):
        """Unregister a project and evict its cached bridge.

        With delete_data=true the project's trellis data dir (notes, sync
        status, registry) is removed too. The repository path itself is NEVER
        touched — no .code-graph or repo files are deleted.
        """
        import shutil

        from src.trellis.utils import (
            get_trellis_data_dir,
            list_registered_projects,
            unregister_project,
        )

        registered = await asyncio.to_thread(list_registered_projects)
        if project_id not in registered:
            return JSONResponse(
                {"error": f"Unknown project '{project_id}'"}, status_code=404
            )

        await asyncio.to_thread(unregister_project, project_id)
        await asyncio.to_thread(core.evict_bridge, project_id)
        core.sync_jobs.pop(project_id, None)

        data_deleted = False
        if delete_data:
            project_dir = get_trellis_data_dir() / "projects" / project_id
            data_deleted = project_dir.exists()
            await asyncio.to_thread(shutil.rmtree, project_dir, ignore_errors=True)

        return JSONResponse(
            {"removed": True, "id": project_id, "data_deleted": data_deleted}
        )

    @app.get("/stats/usage")
    async def stats_usage():
        """MCP tool-call telemetry: totals, per-tool counts, recent calls."""
        from src.trellis import usage

        return JSONResponse(await asyncio.to_thread(usage.get_usage))

    return app
