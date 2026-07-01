"""JavaScript pattern indexer for dynamic-dispatch codebases.

The Rust code-graph-mcp engine traces direct function calls. In many JS
codebases (like tui.image-editor) commands, components, and event handlers are
wired up through string keys and factory maps. This module scans the AST for
those patterns and stores synthetic edges that Trellis can use to augment
impact analysis, reference search, and dead-code detection.

Patterns detected:
- commandFactory.register({ name: '...', execute: fn }) -> command -> fn edge
- imageEditor.execute('...' | commandNames.X) -> caller -> command edge
- graphics.getComponent(componentNames.X | 'X') -> component -> class edge
- new ClassName(...) -> instantiation edge
- this._prop = new ClassName(...) -> class property -> class edge
- this._map[components.X] = new ClassName(...) -> map entry -> class edge
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tree_sitter import Language, Node, Parser


def _load_js_language() -> Language:
    """Load the tree-sitter JavaScript language."""
    import tree_sitter_javascript

    return Language(tree_sitter_javascript.language())


@dataclass
class SyntheticEdge:
    """A synthetic relationship discovered by JS pattern analysis."""

    source_kind: str
    source_name: str
    target_kind: str
    target_name: str
    relation: str
    file_path: str
    line: int
    column: int = 0


@dataclass
class CommandRegistration:
    """A command registered with commandFactory.register()."""

    name: str
    file_path: str
    execute_symbol: str = ""
    undo_symbol: str = ""


@dataclass
class ComponentMapping:
    """A component resolved via graphics.getComponent() or constructor."""

    component_key: str
    class_name: str
    file_path: str
    line: int


@dataclass
class PropertyMapping:
    """A class property assigned a typed instance, e.g. this._graphics = new Graphics()."""

    class_name: str
    property_name: str
    instance_class: str
    file_path: str
    line: int


class JsPatternIndexer:
    """Scan JS projects for dynamic-dispatch patterns."""

    def __init__(self, project_path: str) -> None:
        self.project_path = Path(project_path).resolve()
        self._language = _load_js_language()
        self._parser = Parser(self._language)
        self.edges: List[SyntheticEdge] = []
        self.commands: Dict[str, CommandRegistration] = {}
        self.components: Dict[str, ComponentMapping] = {}
        self.properties: Dict[str, List[PropertyMapping]] = {}
        self.instantiations: Dict[str, List[Tuple[str, int]]] = {}
        self.command_name_map: Dict[str, str] = {}
        self.component_name_map: Dict[str, str] = {}
        self.object_maps: Dict[str, Dict[str, str]] = {}

    def index(self, paths: Optional[List[Path]] = None) -> "JsPatternIndexer":
        """Index JS files under project_path or the provided paths."""
        # First resolve commandNames/componentNames constants and class maps
        # so later patterns can map `commandNames.APPLY_FILTER` -> `applyFilter`
        # and `new SUB_UI_COMPONENT[name]()` -> all mapped class names.
        self._resolve_constants()

        if paths is None:
            paths = list(self.project_path.rglob("*.js"))

        # Pre-pass: collect class/object maps (e.g. SUB_UI_COMPONENT = { Shape, ... })
        for path in paths:
            if "node_modules" in path.parts:
                continue
            self._index_object_maps(path)

        # First pass: collect command registrations so second pass can resolve
        # dynamic invocations like `this.execute(commands.CLEAR_OBJECTS)`.
        for path in paths:
            if "node_modules" in path.parts:
                continue
            self._index_file(path, registrations_only=True)

        # Second pass: detect executions, instantiations, components, properties.
        for path in paths:
            if "node_modules" in path.parts:
                continue
            self._index_file(path, registrations_only=False)

        return self

    def _index_object_maps(self, path: Path) -> None:
        """Collect object literals that map names to class identifiers.

        Example: const SUB_UI_COMPONENT = { Shape, Crop, Resize, ... };
        """
        try:
            source = path.read_text(encoding="utf-8")
        except Exception:
            return

        tree = self._parser.parse(source.encode("utf-8"))

        def _walk_maps(node: Node) -> None:
            if node.type in (
                "export_statement",
                "variable_declaration",
                "lexical_declaration",
            ):
                decl = node
                if node.type == "export_statement":
                    decl = next(
                        (
                            c
                            for c in node.children
                            if c.type in ("variable_declaration", "lexical_declaration")
                        ),
                        None,
                    )
                if decl:
                    for declarator in decl.children:
                        if declarator.type != "variable_declarator":
                            continue
                        name_node = declarator.child_by_field_name("name")
                        value_node = declarator.child_by_field_name("value")
                        if name_node is None or value_node is None:
                            continue
                        map_name = self._node_text(name_node, source)
                        if value_node.type == "object":
                            entries: Dict[str, str] = {}
                            for child in value_node.children:
                                if child.type == "shorthand_property_identifier":
                                    key = self._node_text(child, source)
                                    entries[key] = key
                                elif child.type == "pair":
                                    key_node = child.child_by_field_name("key")
                                    val_node = child.child_by_field_name("value")
                                    if (
                                        key_node
                                        and val_node
                                        and val_node.type == "identifier"
                                    ):
                                        key = self._node_text(key_node, source).strip(
                                            "\"'"
                                        )
                                        val = self._node_text(val_node, source)
                                        entries[key] = val
                                    elif (
                                        key_node
                                        and val_node
                                        and val_node.type
                                        == "shorthand_property_identifier"
                                    ):
                                        key = self._node_text(key_node, source).strip(
                                            "\"'"
                                        )
                                        entries[key] = key
                            if entries:
                                self.object_maps[map_name] = entries
            for child in node.children:
                _walk_maps(child)

        _walk_maps(tree.root_node)

    def _resolve_constants(self) -> None:
        """Find commandNames / componentMaps declarations and build maps."""
        for path in self.project_path.rglob("*.js"):
            if "node_modules" in path.parts:
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except Exception:
                continue

            tree = self._parser.parse(source.encode("utf-8"))
            self._extract_constant_maps(tree.root_node, source)

    def _extract_constant_maps(self, node: Node, source: str) -> None:
        """Extract export const commandNames = {...} and keyMirror(...) maps."""
        if node.type in (
            "export_statement",
            "variable_declaration",
            "lexical_declaration",
        ):
            decl = None
            if node.type == "export_statement":
                decl = next(
                    (
                        c
                        for c in node.children
                        if c.type in ("variable_declaration", "lexical_declaration")
                    ),
                    None,
                )
            else:
                decl = node

            if decl is None:
                return

            for declarator in decl.children:
                if declarator.type != "variable_declarator":
                    continue
                name_node = declarator.child_by_field_name("name")
                value_node = declarator.child_by_field_name("value")
                if name_node is None or value_node is None:
                    continue

                name = self._node_text(name_node, source)
                if name == "commandNames" and value_node.type == "object":
                    self.command_name_map.update(
                        self._parse_string_object(value_node, source)
                    )
                elif name == "componentNames":
                    self.component_name_map.update(
                        self._parse_key_mirror(value_node, source)
                    )

        for child in node.children:
            self._extract_constant_maps(child, source)

    def _parse_string_object(self, node: Node, source: str) -> Dict[str, str]:
        """Parse { KEY: 'value', ... } into a dict."""
        result: Dict[str, str] = {}
        for pair in node.children:
            if pair.type != "pair":
                continue
            key_node = pair.child_by_field_name("key")
            value_node = pair.child_by_field_name("value")
            if key_node is None or value_node is None:
                continue
            key = self._node_text(key_node, source).strip("\"'")
            value = self._resolve_string_or_constant(value_node, source)
            if value:
                result[key] = value
        return result

    def _parse_key_mirror(self, node: Node, source: str) -> Dict[str, str]:
        """Parse keyMirror('A', 'B') or { A: 'A', B: 'B' } into a dict."""
        result: Dict[str, str] = {}
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn and self._node_text(fn, source) == "keyMirror":
                args = node.child_by_field_name("arguments")
                if args:
                    for arg in args.children:
                        if arg.type in ("(", ",", ")"):
                            continue
                        key = self._resolve_string_or_constant(arg, source)
                        if key:
                            result[key] = key
        elif node.type == "object":
            for pair in node.children:
                if pair.type != "pair":
                    continue
                key_node = pair.child_by_field_name("key")
                value_node = pair.child_by_field_name("value")
                if key_node is None or value_node is None:
                    continue
                key = self._node_text(key_node, source).strip("\"'")
                value = self._resolve_string_or_constant(value_node, source)
                if value:
                    result[key] = value
        return result

    def _index_file(self, path: Path, registrations_only: bool = False) -> None:
        """Parse a single JS file and extract patterns."""
        try:
            source = path.read_text(encoding="utf-8")
        except Exception:
            return

        tree = self._parser.parse(source.encode("utf-8"))
        local_objects = self._build_local_object_map(tree.root_node, source)
        self._walk(
            tree.root_node,
            path,
            source,
            local_objects,
            registrations_only=registrations_only,
        )

    def _build_local_object_map(self, node: Node, source: str) -> Dict[str, Node]:
        """Map variable names to object/subscript literal values in scope."""
        result: Dict[str, Node] = {}
        if node.type in (
            "export_statement",
            "variable_declaration",
            "lexical_declaration",
        ):
            decl = node
            if node.type == "export_statement":
                decl = next(
                    (
                        c
                        for c in node.children
                        if c.type in ("variable_declaration", "lexical_declaration")
                    ),
                    None,
                )
            if decl:
                for declarator in decl.children:
                    if declarator.type != "variable_declarator":
                        continue
                    name_node = declarator.child_by_field_name("name")
                    value_node = declarator.child_by_field_name("value")
                    if name_node and value_node:
                        if value_node.type in ("object", "subscript_expression"):
                            result[self._node_text(name_node, source)] = value_node

        for child in node.children:
            result.update(self._build_local_object_map(child, source))
        return result

    def _walk(
        self,
        node: Node,
        path: Path,
        source: str,
        local_objects: Dict[str, Node],
        registrations_only: bool = False,
    ) -> None:
        """Recursively walk AST and dispatch to pattern detectors."""
        self._detect_command_registration(node, path, source, local_objects)

        if not registrations_only:
            self._detect_command_execution(node, path, source)
            self._detect_component_access(node, path, source)
            self._detect_instantiation(node, path, source, local_objects)
            self._detect_property_assignment(node, path, source)
            self._detect_typed_property_call(node, path, source)
            self._detect_this_method_call(node, path, source)
            self._detect_property_assignment_map(node, path, source)

        for child in node.children:
            self._walk(
                child,
                path,
                source,
                local_objects,
                registrations_only=registrations_only,
            )

    def _node_text(self, node: Node, source: str) -> str:
        """Extract source text for a node."""
        return source[node.start_byte : node.end_byte]

    def _node_location(self, node: Node, path: Path) -> Tuple[int, int]:
        """Return (line, column) for a node."""
        return (node.start_point[0] + 1, node.start_point[1])

    def _is_call(
        self, node: Node, object_name: str, method_name: str
    ) -> Optional[Node]:
        """Check if node is object_name.method_name(args) and return args node."""
        if node.type != "call_expression":
            return None

        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if fn is None or args is None:
            return None

        if fn.type != "member_expression":
            return None

        obj = fn.child_by_field_name("object")
        prop = fn.child_by_field_name("property")
        if obj is None or prop is None:
            return None

        if (
            self._node_text(obj, source="") == object_name
            and self._node_text(prop, source="") == method_name
        ):
            return args

        # For cases where we don't have source cached, compare against raw bytes
        obj_text = fn.child_by_field_name("object").text.decode("utf-8")
        prop_text = fn.child_by_field_name("property").text.decode("utf-8")
        if obj_text == object_name and prop_text == method_name:
            return args

        return None

    def _detect_command_registration(
        self, node: Node, path: Path, source: str, local_objects: Dict[str, Node]
    ) -> None:
        """Detect commandFactory.register({ name: '...', execute: fn })."""
        if node.type != "call_expression":
            return

        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if fn is None or args is None:
            return

        fn_text = self._node_text(fn, source)
        if not fn_text.endswith("commandFactory.register"):
            return

        # First argument is the command object (literal or variable reference)
        first_arg = next(
            (c for c in args.children if c.type not in ("(", ",", ")")), None
        )
        if first_arg is None:
            return

        command_obj = first_arg
        if first_arg.type == "identifier":
            command_obj = local_objects.get(self._node_text(first_arg, source))
        if command_obj is None or command_obj.type != "object":
            return

        name = None
        execute = None
        undo = None

        for child in command_obj.children:
            if child.type == "pair":
                key_node = child.child_by_field_name("key")
                value_node = child.child_by_field_name("value")
                if key_node is None or value_node is None:
                    continue

                key_text = self._node_text(key_node, source).strip("\"'")
                if key_text == "name":
                    name = self._resolve_string_or_constant(value_node, source)
                elif key_text == "execute":
                    execute = self._extract_method_name(value_node, source) or key_text
                elif key_text == "undo":
                    undo = self._extract_method_name(value_node, source) or key_text
            elif child.type == "method_definition":
                method_name = self._extract_method_name(child, source)
                if method_name == "execute":
                    execute = method_name
                elif method_name == "undo":
                    undo = method_name

        if not name:
            return

        line, col = self._node_location(node, path)
        rel_path = path.relative_to(self.project_path).as_posix()
        self.commands[name] = CommandRegistration(
            name=name,
            file_path=rel_path,
            execute_symbol=execute or "",
            undo_symbol=undo or "",
        )
        self.edges.append(
            SyntheticEdge(
                source_kind="command",
                source_name=name,
                target_kind="function",
                target_name=execute or "",
                relation="registered_execute",
                file_path=rel_path,
                line=line,
                column=col,
            )
        )

    def _extract_method_name(self, node: Node, source: str) -> str:
        """Return declared name for method/function/arrow expressions."""
        if node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                return self._node_text(name_node, source)
        if node.type in ("function", "function_expression", "generator_function"):
            name_node = node.child_by_field_name("name")
            if name_node:
                return self._node_text(name_node, source)
        return ""

    def _detect_command_execution(self, node: Node, path: Path, source: str) -> None:
        """Detect execute('commandName' | commandNames.X) calls."""
        if node.type != "call_expression":
            return

        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if fn is None or args is None:
            return

        fn_text = self._node_text(fn, source)
        if not (fn_text.endswith(".execute") or fn_text == "execute"):
            return

        first_arg = next(
            (c for c in args.children if c.type not in ("(", ",", ")")), None
        )
        if first_arg is None:
            return

        command_name = self._resolve_string_or_constant(first_arg, source)
        if command_name not in self.commands:
            return

        line, col = self._node_location(node, path)
        rel_path = path.relative_to(self.project_path).as_posix()
        self.edges.append(
            SyntheticEdge(
                source_kind="caller",
                source_name=f"{rel_path}:{line}",
                target_kind="command",
                target_name=command_name,
                relation="executes",
                file_path=rel_path,
                line=line,
                column=col,
            )
        )

    def _detect_component_access(self, node: Node, path: Path, source: str) -> None:
        """Detect getComponent(componentNames.X | 'X') calls."""
        if node.type != "call_expression":
            return

        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if fn is None or args is None:
            return

        fn_text = self._node_text(fn, source)
        if not fn_text.endswith(".getComponent"):
            return

        first_arg = next(
            (c for c in args.children if c.type not in ("(", ",", ")")), None
        )
        if first_arg is None:
            return

        component_key = self._resolve_component_key(first_arg, source)
        if not component_key:
            return

        # Look up the class name from componentNames mapping if available
        class_name = self._component_key_to_class(component_key)
        line, col = self._node_location(node, path)
        rel_path = path.relative_to(self.project_path).as_posix()
        self.components[component_key] = ComponentMapping(
            component_key=component_key,
            class_name=class_name,
            file_path=rel_path,
            line=line,
        )

    def _detect_instantiation(
        self, node: Node, path: Path, source: str, local_objects: Dict[str, Node]
    ) -> None:
        """Detect new ClassName(...) expressions and dynamic lookups like new SUB_UI_COMPONENT[X]()."""
        if node.type != "new_expression":
            return

        ctor = node.child_by_field_name("constructor")
        if ctor is None:
            return

        class_names: List[str] = []
        if ctor.type == "identifier":
            ctor_name = self._node_text(ctor, source)
            # Resolve local alias back to object-map source
            if (
                ctor_name in local_objects
                and local_objects[ctor_name].type == "subscript_expression"
            ):
                inner = local_objects[ctor_name].child_by_field_name("object")
                if inner:
                    inner_text = self._node_text(inner, source)
                    if inner_text in self.object_maps:
                        class_names.extend(self.object_maps[inner_text].values())
            if not class_names:
                class_names.append(ctor_name)
        elif ctor.type == "subscript_expression":
            obj = ctor.child_by_field_name("object")
            if obj:
                obj_text = self._node_text(obj, source)
                if obj_text in self.object_maps:
                    class_names.extend(self.object_maps[obj_text].keys())
        elif ctor.type == "member_expression":
            obj = ctor.child_by_field_name("object")
            prop = ctor.child_by_field_name("property")
            if obj and prop:
                obj_text = self._node_text(obj, source)
                prop_text = self._node_text(prop, source)
                # Namespace import usage like `new tui.ImageEditor()`
                if obj_text in ("tui",):
                    class_names.append(prop_text)
                elif obj_text in self.object_maps:
                    class_names.extend(self.object_maps[obj_text].values())
                else:
                    return

        if not class_names:
            return

        line, col = self._node_location(node, path)
        rel_path = path.relative_to(self.project_path).as_posix()

        for class_name in class_names:
            self.instantiations.setdefault(class_name, []).append((rel_path, line))
            self.edges.append(
                SyntheticEdge(
                    source_kind="instantiation",
                    source_name=f"{rel_path}:{line}",
                    target_kind="class",
                    target_name=class_name,
                    relation="instantiates",
                    file_path=rel_path,
                    line=line,
                    column=col,
                )
            )

    def _detect_property_assignment(self, node: Node, path: Path, source: str) -> None:
        """Detect this._prop = new ClassName(...) in constructors/methods."""
        if node.type != "assignment_expression":
            return

        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return

        if left.type != "member_expression":
            return

        obj = left.child_by_field_name("object")
        prop = left.child_by_field_name("property")
        if obj is None or prop is None:
            return

        obj_text = self._node_text(obj, source)
        prop_text = self._node_text(prop, source)
        if obj_text != "this":
            return

        if right.type != "new_expression":
            return

        ctor = right.child_by_field_name("constructor")
        if ctor is None or ctor.type != "identifier":
            return

        instance_class = self._node_text(ctor, source)

        # Determine enclosing class name by looking up the syntax tree
        class_name = self._find_enclosing_class(node, source)
        if not class_name:
            return

        line, col = self._node_location(node, path)
        rel_path = path.relative_to(self.project_path).as_posix()
        mapping = PropertyMapping(
            class_name=class_name,
            property_name=prop_text,
            instance_class=instance_class,
            file_path=rel_path,
            line=line,
        )
        self.properties.setdefault(class_name, []).append(mapping)
        self.edges.append(
            SyntheticEdge(
                source_kind="class_property",
                source_name=f"{class_name}.{prop_text}",
                target_kind="class",
                target_name=instance_class,
                relation="typed_property",
                file_path=rel_path,
                line=line,
                column=col,
            )
        )

    def _detect_typed_property_call(self, node: Node, path: Path, source: str) -> None:
        """Detect this._prop.method() where _prop was typed via assignment."""
        if node.type != "call_expression":
            return

        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return

        prop_node = fn.child_by_field_name("property")
        obj_node = fn.child_by_field_name("object")
        if (
            prop_node is None
            or obj_node is None
            or obj_node.type != "member_expression"
        ):
            return

        this_node = obj_node.child_by_field_name("object")
        inst_prop_node = obj_node.child_by_field_name("property")
        if this_node is None or inst_prop_node is None:
            return

        if self._node_text(this_node, source) != "this":
            return

        class_name = self._find_enclosing_class(node, source)
        if not class_name:
            return

        prop_name = self._node_text(inst_prop_node, source)
        mappings = self.properties.get(class_name, [])
        instance_class = None
        for mapping in mappings:
            if mapping.property_name == prop_name:
                instance_class = mapping.instance_class
                break
        if not instance_class:
            return

        method_name = self._node_text(prop_node, source)
        line, col = self._node_location(node, path)
        rel_path = path.relative_to(self.project_path).as_posix()
        self.edges.append(
            SyntheticEdge(
                source_kind="caller",
                source_name=f"{rel_path}:{line}",
                target_kind="method",
                target_name=f"{instance_class}.{method_name}",
                relation="property_call",
                file_path=rel_path,
                line=line,
                column=col,
            )
        )

    def _detect_this_method_call(self, node: Node, path: Path, source: str) -> None:
        """Detect this._methodName() calls within the same class."""
        if node.type != "call_expression":
            return

        fn = node.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return

        obj_node = fn.child_by_field_name("object")
        prop_node = fn.child_by_field_name("property")
        if obj_node is None or prop_node is None:
            return

        if self._node_text(obj_node, source) != "this":
            return

        class_name = self._find_enclosing_class(node, source)
        if not class_name:
            return

        method_name = self._node_text(prop_node, source)
        line, col = self._node_location(node, path)
        rel_path = path.relative_to(self.project_path).as_posix()
        self.edges.append(
            SyntheticEdge(
                source_kind="caller",
                source_name=f"{rel_path}:{line}",
                target_kind="method",
                target_name=f"{class_name}.{method_name}",
                relation="this_method_call",
                file_path=rel_path,
                line=line,
                column=col,
            )
        )

    def _detect_property_assignment_map(
        self, node: Node, path: Path, source: str
    ) -> None:
        """Detect this._map = { Key, ... } assignments to resolve dynamic instantiations."""
        if node.type != "assignment_expression":
            return

        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return

        if left.type != "member_expression":
            return

        obj = left.child_by_field_name("object")
        prop = left.child_by_field_name("property")
        if obj is None or prop is None:
            return

        obj_text = self._node_text(obj, source)
        if obj_text != "this" and obj_text not in self.object_maps:
            return

        map_name = self._node_text(prop, source)
        if right.type != "object":
            return

        entries: Dict[str, str] = {}
        for child in right.children:
            if child.type == "shorthand_property_identifier":
                key = self._node_text(child, source)
                entries[key] = key
            elif child.type == "pair":
                key_node = child.child_by_field_name("key")
                val_node = child.child_by_field_name("value")
                if key_node and val_node and val_node.type == "identifier":
                    key = self._node_text(key_node, source).strip("\"'")
                    val = self._node_text(val_node, source)
                    entries[key] = val
        if entries:
            self.object_maps[map_name] = entries

    def _find_enclosing_class(self, node: Node, source: str) -> Optional[str]:
        """Walk up AST to find enclosing class declaration name."""
        current = node.parent
        while current:
            if current.type in ("class_declaration", "export_statement"):
                name_node = current.child_by_field_name("name")
                if name_node:
                    return self._node_text(name_node, source)
            current = current.parent
        return None

    def _resolve_string_or_constant(self, node: Node, source: str) -> str:
        """Resolve a node to a string value if possible."""
        if node.type == "string":
            return self._node_text(node, source).strip("\"'")
        if node.type in ("identifier", "property_identifier"):
            return self._node_text(node, source)
        if node.type == "member_expression":
            text = self._node_text(node, source)
            # Resolve commandNames.X / commands.X / componentNames.X if we have the map
            for prefix in (
                "commandNames.",
                "commands.",
                "componentNames.",
                "components.",
            ):
                if text.startswith(prefix):
                    key = text.split(".", 1)[1]
                    if prefix in ("commandNames.", "commands."):
                        return self.command_name_map.get(key, text)
                    return self.component_name_map.get(key, text)
            return text
        return ""

    def _resolve_component_key(self, node: Node, source: str) -> str:
        """Resolve componentNames.X or 'X' to a key string."""
        return self._resolve_string_or_constant(node, source)

    def _component_key_to_class(self, key: str) -> str:
        """Heuristic: CROPPER -> Cropper, IMAGE_LOADER -> ImageLoader."""
        return "".join(part.capitalize() for part in key.split("_"))

    def get_command_callers(self, command_name: str) -> List[SyntheticEdge]:
        """Return all synthetic callers of a command by name."""
        return [
            e
            for e in self.edges
            if e.relation == "executes" and e.target_name == command_name
        ]

    def get_component_class(self, component_key: str) -> Optional[str]:
        """Return the class name associated with a component key."""
        mapping = self.components.get(component_key)
        if mapping:
            return mapping.class_name
        return None

    def get_property_class(self, class_name: str, property_name: str) -> Optional[str]:
        """Return the class type assigned to a property, e.g. Graphics._graphics."""
        for mapping in self.properties.get(class_name, []):
            if mapping.property_name == property_name:
                return mapping.instance_class
        return None

    def get_instantiations(self, class_name: str) -> List[Tuple[str, int]]:
        """Return locations where class_name is instantiated with `new`."""
        return self.instantiations.get(class_name, [])

    def is_live(self, class_name: str) -> bool:
        """Return True if class_name is instantiated, registered, or exported."""
        if class_name in self.instantiations:
            return True
        if class_name in {m.class_name for m in self.components.values()}:
            return True
        return False
