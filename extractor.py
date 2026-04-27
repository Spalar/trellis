from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from models import ExtractedFunction


class ExtractionResult:
    """Result of extracting functions from a repository."""
    def __init__(self) -> None:
        self.functions: List[ExtractedFunction] = []
        self.file_contents: Dict[str, str] = {}  # file_path -> source content

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

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    def extract_repo(self, repo_path: str) -> ExtractionResult:
        repo = Path(repo_path)
        result = ExtractionResult()
        for file_path in self._iter_source_files(repo):
            file_funcs, content = self.extract_file_with_content(repo, file_path)
            result.functions.extend(file_funcs)
            if content:
                rel_path = str(file_path.relative_to(repo))
                result.file_contents[rel_path] = content
        return result

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
        excludes = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", "dist", "build", ".next", ".nuxt"}
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
