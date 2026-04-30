from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ast_cache import ASTCache
from models import ExtractedFunction


class ExtractionResult:
    """Result of extracting functions from a repository."""
    def __init__(self) -> None:
        self.functions: List[ExtractedFunction] = []
        self.file_contents: Dict[str, str] = {}  # file_path -> source content


def _parse_file_worker(repo_path: str, file_path_str: str) -> Tuple[List[dict], str]:
    """Worker function for parallel parsing (must be picklable).
    
    Returns serialized ExtractedFunction dicts to avoid pickling tree-sitter objects.
    """
    from pathlib import Path
    
    file_path = Path(file_path_str)
    repo = Path(repo_path)
    
    # Create a fresh extractor in each process
    extractor = UnifiedExtractor()
    file_funcs, content = extractor.extract_file_with_content(repo, file_path)
    
    # Serialize to plain dicts for pickling
    serialized = []
    for func in file_funcs:
        serialized.append({
            "function_path": func.function_path,
            "file_path": func.file_path,
            "start_line": func.start_line,
            "end_line": func.end_line,
            "docstring": func.docstring,
            "source": func.source,
            "raw_calls": func.raw_calls,
        })
    
    return serialized, content

try:
    import tree_sitter_javascript as tsjs
    import tree_sitter_python as tspython
    import tree_sitter_typescript as tstypescript
    from tree_sitter import Language, Parser
except ImportError as exc:
    raise ImportError(
        "tree_sitter and language packs are required. "
        "Install with: pip install tree-sitter tree-sitter-python "
        "tree-sitter-javascript tree-sitter-typescript"
    ) from exc


class UnifiedExtractor:
    """Extract functions/methods from Python, JavaScript, and TypeScript source."""

    def __init__(self) -> None:
        self.parsers: dict[str, Parser] = {
            "python": Parser(Language(tspython.language())),
            "javascript": Parser(Language(tsjs.language())),
            "typescript": Parser(Language(tstypescript.language_typescript())),
        }
        self.ast_cache = ASTCache()

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    def extract_repo(self, repo_path: str, changed_files_only: bool = False, 
                     file_hashes: Dict[str, tuple] = None) -> ExtractionResult:
        """Extract functions from a repository.
        
        Uses AST cache for incremental syncs to skip re-parsing unchanged files.
        
        Args:
            repo_path: Path to the repository
            changed_files_only: If True, only extract files that have changed
            file_hashes: Dict of file_path -> (hash, mtime) for change detection
        """
        repo = Path(repo_path)
        result = ExtractionResult()
        
        # Always compute git hashes for consistent cache keys between full and incremental syncs
        git_hashes = self._compute_file_hashes_git(repo_path)
        use_git = bool(git_hashes)
        
        parsed_count = 0
        skipped_count = 0
        
        for file_path in self._iter_source_files(repo):
            rel_path = file_path.relative_to(repo).as_posix()
            
            # Compute file hash for cache key (prefer git hash for consistency)
            if use_git and rel_path in git_hashes:
                file_hash = git_hashes[rel_path]
            else:
                file_hash = self._compute_file_hash(file_path)
            
            # Check if file changed (for incremental sync)
            if changed_files_only and file_hashes is not None:
                stored = file_hashes.get(rel_path)
                if stored and stored[0] == file_hash:
                    # File unchanged - skip entirely. Caller loads from DB.
                    skipped_count += 1
                    continue
            
            # Parse file
            file_funcs, content = self.extract_file_with_content(repo, file_path)
            result.functions.extend(file_funcs)
            if content:
                result.file_contents[rel_path] = content
            
            parsed_count += 1
            
            if changed_files_only and file_hashes is not None:
                file_hashes[rel_path] = (file_hash, 0)
        
        if changed_files_only:
            print(f"[Trellis] Incremental sync: {parsed_count} files parsed, {skipped_count} files skipped")
        
        return result
    
    def extract_repo_parallel(self, repo_path: str, max_workers: int = None) -> ExtractionResult:
        """Extract functions using parallel parsing for faster full syncs.
        
        Uses ProcessPoolExecutor for true parallelism (bypasses GIL).
        
        Args:
            repo_path: Path to the repository
            max_workers: Number of parallel processes (default: CPU count)
        """
        import os
        from concurrent.futures import ProcessPoolExecutor
        from multiprocessing import get_context
        
        if max_workers is None:
            max_workers = min(os.cpu_count() or 2, 8)  # Cap at 8 to avoid overwhelming the system
        
        repo = Path(repo_path)
        result = ExtractionResult()
        
        # Collect all files first
        files = list(self._iter_source_files(repo))
        total_files = len(files)
        print(f"[Trellis] Parsing {total_files} files with {max_workers} workers...")
        
        # Use spawn context to avoid issues with thread locks
        ctx = get_context("spawn")
        
        # Parse files in parallel using processes
        # Each process gets a fresh parser instance, avoiding GIL contention
        with ProcessPoolExecutor(max_workers=max_workers, mp_context=ctx) as executor:
            futures = {
                executor.submit(_parse_file_worker, repo_path, str(file_path)): file_path
                for file_path in files
            }
            
            completed = 0
            for future in futures:
                file_path = futures[future]
                rel_path = str(file_path.relative_to(repo))
                try:
                    serialized_funcs, content = future.result(timeout=30)  # 30s timeout per file
                    # Reconstruct ExtractedFunction objects
                    for func_dict in serialized_funcs:
                        result.functions.append(ExtractedFunction(**func_dict))
                    if content:
                        result.file_contents[rel_path] = content
                except Exception as e:
                    print(f"[Trellis] Warning: Failed to parse {rel_path}: {e}")
                    continue
                
                completed += 1
                if completed % 10 == 0 or completed == total_files:
                    print(f"[Trellis] Progress: {completed}/{total_files} files ({len(result.functions)} functions)")
        
        print(f"[Trellis] Extraction complete: {len(result.functions)} functions from {total_files} files")
        return result
    
    def _compute_file_hashes_git(self, repo_path: str) -> Dict[str, str]:
        """Compute hashes for all tracked files using git hash-object.
        
        This is 10x faster than reading and hashing files individually
        because git uses its internal object database.
        """
        import subprocess
        try:
            result = subprocess.run(
                ["git", "-C", repo_path, "ls-files", "--stage"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return {}
            
            hashes = {}
            for line in result.stdout.strip().split("\n"):
                # Format: <mode> <hash> <stage>\t<file_path>
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    meta, file_path = parts
                    hash_value = meta.split()[1]
                    hashes[file_path] = hash_value
            return hashes
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return {}
    
    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute MD5 hash of file contents. Fallback when git is not available."""
        import hashlib
        return hashlib.md5(file_path.read_bytes()).hexdigest()

    def extract_file_with_content(self, repo_root: Path, file_path: Path) -> Tuple[List[ExtractedFunction], str]:
        """Extract functions and return source content to avoid re-reading."""
        source_bytes = file_path.read_bytes()
        source_text = source_bytes.decode("utf-8", errors="replace")
        lang = self._detect_language(file_path)
        parser = self.parsers.get(lang)
        if parser is None:
            return [], source_text
        tree = parser.parse(source_bytes)
        module_name = self._module_name(repo_root, file_path)
        output: List[ExtractedFunction] = []
        self._walk(tree.root_node, source_bytes, file_path, module_name, lang, output, class_stack=[])
        return output, source_text

    def extract_file(self, repo_root: Path, file_path: Path) -> List[ExtractedFunction]:
        funcs, _ = self.extract_file_with_content(repo_root, file_path)
        return funcs

    # ------------------------------------------------------------------
    # File discovery
    # ------------------------------------------------------------------
    def _detect_language(self, file_path: Path) -> str:
        ext = file_path.suffix.lower()
        if ext == ".py":
            return "python"
        if ext in {".js", ".jsx", ".mjs", ".cjs"}:
            return "javascript"
        if ext in {".ts", ".tsx", ".mts", ".cts"}:
            return "typescript"
        return ""

    def _iter_source_files(self, repo: Path) -> Iterable[Path]:
        excludes = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", "dist", "build", ".next", ".nuxt", ".trellis", ".dart_tool", "ephemeral"}
        exts = {"*.py", "*.js", "*.jsx", "*.mjs", "*.cjs", "*.ts", "*.tsx", "*.mts", "*.cts"}
        for pattern in exts:
            for path in repo.rglob(pattern):
                if any(part in excludes for part in path.parts):
                    continue
                if path.is_file():
                    yield path

    def _module_name(self, repo_root: Path, file_path: Path) -> str:
        relative = file_path.relative_to(repo_root)
        no_suffix = relative.with_suffix("")
        # Strip double extension like .d.ts
        if no_suffix.suffix in {".d"}:
            no_suffix = no_suffix.with_suffix("")
        parts = [part for part in no_suffix.parts if part != "__init__"]
        return "/".join(parts) if parts else "root"

    # ------------------------------------------------------------------
    # Generic AST walker
    # ------------------------------------------------------------------
    def _walk(
        self,
        node,
        source_bytes: bytes,
        file_path: Path,
        module_name: str,
        lang: str,
        output: List[ExtractedFunction],
        class_stack: List[str],
    ) -> None:
        """Depth-first walk that handles class-like and function-like nodes."""
        if lang == "python":
            self._walk_python(node, source_bytes, file_path, module_name, lang, output, class_stack)
        else:
            self._walk_js_like(node, source_bytes, file_path, module_name, lang, output, class_stack)

    # ---- Python -------------------------------------------------------
    def _walk_python(
        self,
        node,
        source_bytes: bytes,
        file_path: Path,
        module_name: str,
        lang: str,
        output: List[ExtractedFunction],
        class_stack: List[str],
    ) -> None:
        if node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            class_name = self._node_text(name_node, source_bytes) if name_node else "UnknownClass"
            for child in node.children:
                self._walk(child, source_bytes, file_path, module_name, lang, output, class_stack + [class_name])
            return

        if node.type == "function_definition":
            output.append(
                self._build_function(node, source_bytes, file_path, module_name, class_stack)
            )

        for child in node.children:
            self._walk(child, source_bytes, file_path, module_name, lang, output, class_stack)

    # ---- JS / TS ------------------------------------------------------
    def _walk_js_like(
        self,
        node,
        source_bytes: bytes,
        file_path: Path,
        module_name: str,
        lang: str,
        output: List[ExtractedFunction],
        class_stack: List[str],
    ) -> None:
        # Class declarations
        if node.type in {"class_declaration", "class"}:
            name_node = node.child_by_field_name("name")
            class_name = self._node_text(name_node, source_bytes) if name_node else "UnknownClass"
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    self._walk(child, source_bytes, file_path, module_name, lang, output, class_stack + [class_name])
            return

        # Function declarations
        if node.type == "function_declaration":
            output.append(
                self._build_function_js(node, source_bytes, file_path, module_name, class_stack)
            )

        # Method definitions inside classes
        if node.type == "method_definition":
            output.append(
                self._build_function_js(node, source_bytes, file_path, module_name, class_stack)
            )

        # Arrow functions stored as variables: const x = () => {}
        if node.type in {"arrow_function", "function"}:
            parent = node.parent
            # Try to find a variable name or property name
            fn_name = None
            if parent and parent.type == "variable_declarator":
                name_node = parent.child_by_field_name("name")
                fn_name = self._node_text(name_node, source_bytes) if name_node else None
            elif parent and parent.type == "assignment_expression":
                left = parent.child_by_field_name("left")
                if left:
                    fn_name = self._extract_callable_name(left, source_bytes)
            elif parent and parent.type == "pair":
                key = parent.child_by_field_name("key")
                fn_name = self._node_text(key, source_bytes) if key else None

            if fn_name:
                output.append(
                    self._build_function_js(node, source_bytes, file_path, module_name, class_stack, override_name=fn_name)
                )

        for child in node.children:
            self._walk(child, source_bytes, file_path, module_name, lang, output, class_stack)

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------
    def _build_function(
        self,
        fn_node,
        source_bytes: bytes,
        file_path: Path,
        module_name: str,
        class_stack: List[str],
    ) -> ExtractedFunction:
        name_node = fn_node.child_by_field_name("name")
        fn_name = self._node_text(name_node, source_bytes) if name_node else "unknown"
        full_path_parts = [module_name] + class_stack + [fn_name]
        function_path = ".".join([part for part in full_path_parts if part])
        body_node = fn_node.child_by_field_name("body")
        raw_calls = self._collect_calls_python(body_node, source_bytes) if body_node else []
        docstring = self._extract_docstring_python(body_node, source_bytes) if body_node else ""
        source = self._node_text(fn_node, source_bytes)
        return ExtractedFunction(
            function_path=function_path,
            file_path=file_path.as_posix(),
            start_line=fn_node.start_point[0] + 1,
            end_line=fn_node.end_point[0] + 1,
            docstring=docstring,
            source=source,
            raw_calls=raw_calls,
        )

    def _build_function_js(
        self,
        fn_node,
        source_bytes: bytes,
        file_path: Path,
        module_name: str,
        class_stack: List[str],
        override_name: Optional[str] = None,
    ) -> ExtractedFunction:
        if override_name:
            fn_name = override_name
        else:
            name_node = fn_node.child_by_field_name("name")
            fn_name = self._node_text(name_node, source_bytes) if name_node else "unknown"
        full_path_parts = [module_name] + class_stack + [fn_name]
        function_path = ".".join([part for part in full_path_parts if part])
        body_node = fn_node.child_by_field_name("body")
        raw_calls = self._collect_calls_js(body_node, source_bytes) if body_node else []
        docstring = self._extract_docstring_js(fn_node, source_bytes)
        source = self._node_text(fn_node, source_bytes)
        return ExtractedFunction(
            function_path=function_path,
            file_path=file_path.as_posix(),
            start_line=fn_node.start_point[0] + 1,
            end_line=fn_node.end_point[0] + 1,
            docstring=docstring,
            source=source,
            raw_calls=raw_calls,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _node_text(self, node, source_bytes: bytes) -> str:
        return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")

    # ---- Python call / docstring --------------------------------------
    def _collect_calls_python(self, node, source_bytes: bytes) -> List[str]:
        if node is None:
            return []
        calls: List[str] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current.type == "call":
                fn_node = current.child_by_field_name("function")
                callee = self._extract_callable_name(fn_node, source_bytes)
                if callee:
                    calls.append(callee)
            stack.extend(list(current.children))
        return calls

    def _extract_docstring_python(self, body_node, source_bytes: bytes) -> str:
        if body_node is None:
            return ""
        children = list(body_node.children)
        if not children:
            return ""
        first = children[0]
        if first.type != "expression_statement":
            return ""
        text = self._node_text(first, source_bytes).strip()
        if text.startswith('"""') or text.startswith("'''"):
            return text.strip("\"'")
        return ""

    # ---- JS/TS call / docstring ---------------------------------------
    def _collect_calls_js(self, node, source_bytes: bytes) -> List[str]:
        if node is None:
            return []
        calls: List[str] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current.type == "call_expression":
                fn_node = current.child_by_field_name("function")
                callee = self._extract_callable_name(fn_node, source_bytes)
                if callee:
                    calls.append(callee)
            stack.extend(list(current.children))
        return calls

    def _extract_docstring_js(self, fn_node, source_bytes: bytes) -> str:
        """Look for a JSDoc comment immediately preceding the function."""
        # Tree-sitter comments are usually siblings at the top level, not inside the function node.
        # We scan for the first comment in the file that ends right before this node starts.
        # Simpler heuristic: grab the line immediately above and check for /* or //
        start_line = fn_node.start_point[0]
        lines = source_bytes.decode("utf-8", errors="ignore").splitlines()
        docs = []
        i = start_line - 1
        # JSDoc block: lines above ending with */  OR single-line // comments
        while i >= 0:
            stripped = lines[i].strip()
            if stripped.startswith("//"):
                docs.insert(0, stripped.lstrip("/ ").strip())
                i -= 1
                continue
            if stripped.startswith("/*") or stripped.startswith("*") or stripped.endswith("*/"):
                clean = stripped.lstrip("/* ").rstrip(" */").strip()
                if clean and not clean.startswith("eslint"):
                    docs.insert(0, clean)
                i -= 1
                continue
            break
        return " ".join(docs)

    def _extract_callable_name(self, node, source_bytes: bytes) -> Optional[str]:
        if node is None:
            return None
        if node.type in {"identifier", "property_identifier", "shorthand_property_identifier", "shorthand_property_identifier_pattern"}:
            return self._node_text(node, source_bytes)
        if node.type == "member_expression":
            attr = node.child_by_field_name("property")
            value = node.child_by_field_name("object")
            attr_name = self._node_text(attr, source_bytes) if attr else ""
            obj_name = self._node_text(value, source_bytes) if value else ""
            return f"{obj_name}.{attr_name}" if obj_name and attr_name else attr_name or obj_name
        if node.type == "call_expression":
            return self._extract_callable_name(node.child_by_field_name("function"), source_bytes)
        return self._node_text(node, source_bytes)


# Keep backward-compatible alias
PythonTreeSitterExtractor = UnifiedExtractor
