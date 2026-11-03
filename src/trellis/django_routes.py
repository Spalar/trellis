"""Django and Django REST Framework route tracing.

Builds the route table by evaluating the module-level statements of the
project's URLconfs: path()/re_path()/url(), include(), View.as_view() with
or without an action mapping, DRF routers (register, router.urls, @action),
module constants and simple for-loops over constant lists. Each view is
resolved through the urls file's imports, so the handler is the function or
method that actually serves the route.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .python_call_indexer import FileInfo, PythonCallGraphIndexer

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_MAX_LOOP = 500
_MAX_INCLUDE_DEPTH = 8

# DRF router actions: (detail route?, http method, action)
_ROUTER_ACTIONS = (
    (False, "get", "list"),
    (False, "post", "create"),
    (True, "get", "retrieve"),
    (True, "put", "update"),
    (True, "patch", "partial_update"),
    (True, "delete", "destroy"),
)
# Actions a third-party DRF base class provides
_DRF_BASE_ACTIONS = {
    "ModelViewSet": {"list", "create", "retrieve", "update", "partial_update", "destroy"},
    "ReadOnlyModelViewSet": {"list", "retrieve"},
    "ListModelMixin": {"list"},
    "CreateModelMixin": {"create"},
    "RetrieveModelMixin": {"retrieve"},
    "UpdateModelMixin": {"update", "partial_update"},
    "DestroyModelMixin": {"destroy"},
}


# -- values produced by evaluating urls.py --------------------------------


@dataclass
class Ref:
    """A name written in urls.py that refers to code (`views.users`)."""

    dotted: Tuple[str, ...]


@dataclass
class AsView:
    view: object
    actions: Optional[Dict[str, str]]


@dataclass
class Include:
    target: object


@dataclass
class Pattern:
    route: object
    view: object
    regex: bool


@dataclass
class Router:
    registrations: List[Tuple[object, object]] = field(default_factory=list)


@dataclass
class RouterUrls:
    router: Router


_UNKNOWN = object()


class _UrlConfEvaluator:
    """Evaluate the module-level statements of one urls.py."""

    def __init__(self) -> None:
        self.env: Dict[str, object] = {}

    def run(self, statements: List[ast.stmt]) -> None:
        for stmt in statements:
            try:
                self._exec(stmt)
            except (RecursionError, TypeError, ValueError):
                continue

    def _exec(self, stmt: ast.stmt) -> None:
        if isinstance(stmt, ast.Assign):
            value = self.evaluate(stmt.value)
            for target in stmt.targets:
                self._bind(target, value)
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            self._bind(stmt.target, self.evaluate(stmt.value))
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, ast.Add):
            if isinstance(stmt.target, ast.Name):
                current = self.env.get(stmt.target.id)
                self.env[stmt.target.id] = self._add(current, self.evaluate(stmt.value))
        elif isinstance(stmt, ast.Expr):
            self.evaluate(stmt.value)
        elif isinstance(stmt, ast.For):
            items = self.evaluate(stmt.iter)
            if isinstance(items, (list, tuple)):
                for item in items[:_MAX_LOOP]:
                    self._bind(stmt.target, item)
                    self.run(stmt.body)
        elif isinstance(stmt, ast.If):  # keep routes from both branches
            self.run(stmt.body)
            self.run(stmt.orelse)
        elif isinstance(stmt, ast.Try):
            self.run(stmt.body)

    def _bind(self, target: ast.AST, value: object) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = value
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (list, tuple)):
            for sub, item in zip(target.elts, value):
                self._bind(sub, item)

    @staticmethod
    def _add(left: object, right: object) -> object:
        if isinstance(right, RouterUrls):
            right = [right]
        if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            return list(left) + list(right)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        # `urlpatterns += static(...)`: keep the routes we do know
        return list(left) if isinstance(left, (list, tuple)) else _UNKNOWN

    def evaluate(self, node: ast.AST) -> object:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                part = self.evaluate(value.value if isinstance(value, ast.FormattedValue) else value)
                parts.append(part if isinstance(part, (str, int)) else "*")
            return "".join(str(p) for p in parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._add(self.evaluate(node.left), self.evaluate(node.right))
        if isinstance(node, (ast.List, ast.Tuple)):
            items = [self.evaluate(e) for e in node.elts if not isinstance(e, ast.Starred)]
            return items if isinstance(node, ast.List) else tuple(items)
        if isinstance(node, ast.Dict):
            return {
                self.evaluate(k): self.evaluate(v)
                for k, v in zip(node.keys, node.values)
                if k is not None
            }
        if isinstance(node, ast.Name):
            return self.env.get(node.id, Ref((node.id,)))
        if isinstance(node, ast.Attribute):
            base = self.evaluate(node.value)
            if isinstance(base, Router) and node.attr == "urls":
                return RouterUrls(base)
            if isinstance(base, Ref):
                return Ref(base.dotted + (node.attr,))
            return _UNKNOWN
        if isinstance(node, ast.Call):
            return self._call(node)
        return _UNKNOWN

    def _call(self, node: ast.Call) -> object:
        func = node.func
        args = [self.evaluate(a) for a in node.args]
        kwargs = {k.arg: self.evaluate(k.value) for k in node.keywords if k.arg}
        if isinstance(func, ast.Attribute):
            owner = self.evaluate(func.value)
            if func.attr == "as_view":
                actions = args[0] if args and isinstance(args[0], dict) else None
                return AsView(owner, actions)
            if func.attr == "register" and isinstance(owner, Router) and len(args) >= 2:
                owner.registrations.append((args[0], args[1]))
                return None
            if isinstance(owner, list) and func.attr in ("append", "extend"):
                owner.extend(args[0] if func.attr == "extend" and isinstance(args[0], list) else args[:1])
                return None
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in ("path", "re_path", "url") and len(args) + len(kwargs) >= 2:
            route = args[0] if args else kwargs.get("route")
            view = args[1] if len(args) > 1 else kwargs.get("view")
            return Pattern(route, view, regex=name != "path")
        if name == "include" and args:
            target = args[0]
            return Include(target[0] if isinstance(target, tuple) and target else target)
        if name.endswith("Router"):
            return Router()
        # Wrappers such as format_suffix_patterns(urlpatterns) keep their input.
        if args and isinstance(args[0], list):
            return args[0]
        return _UNKNOWN


# -- route table ----------------------------------------------------------


_PATH_PARAM = re.compile(r"<(?:(\w+):)?(\w+)>")
# Django's built-in path converters; custom ones fall back to one segment.
_CONVERTERS = {
    "str": "[^/]+",
    "int": "[0-9]+",
    "slug": "[-a-zA-Z0-9_]+",
    "uuid": "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    "path": ".+",
}
_REGEX_GROUP = re.compile(r"\(\?P<(\w+)>[^)]*\)")


def _segment(route: str, regex: bool) -> Tuple[str, str]:
    """(display text, regex fragment) for one route string."""
    if not regex:
        display = _PATH_PARAM.sub(lambda m: f"<{m.group(2)}>", route)
        parts, last = [], 0
        for m in _PATH_PARAM.finditer(route):
            parts.append(re.escape(route[last : m.start()]))
            parts.append(_CONVERTERS.get(m.group(1) or "str", "[^/]+"))
            last = m.end()
        parts.append(re.escape(route[last:]))
        return display, "".join(parts)
    body = route[1:] if route.startswith("^") else route
    body = body[:-1] if body.endswith("$") else body
    display = _REGEX_GROUP.sub(lambda m: f"<{m.group(1)}>", body)
    # Unnamed groups, so names repeated across include() levels still compile.
    return display, re.sub(r"\(\?P<\w+>", "(?:", body)


@dataclass
class Route:
    path: str  # display form, e.g. api/stores/<store_pk>/items/
    regex: str
    urls_file: str
    view: object  # Ref | AsView | ("router", viewset Ref, {method: action})
    name: str = ""
    handlers: List[Dict] = field(default_factory=list)
    loose: bool = False  # matched only after widening typed converters


class DjangoRoutes:
    """Django/DRF route table for one project."""

    def __init__(self, indexer: PythonCallGraphIndexer, node_ids: Dict[Tuple[str, int], Dict]):
        self.indexer = indexer
        self.node_ids = node_ids  # (path, start line) -> {id, qualified_name, type}
        self._decorators: Dict[str, Dict[int, List[ast.expr]]] = {}

    # ---- discovery ----------------------------------------------------

    def root_urlconfs(self) -> List[str]:
        """urls files Django starts from: ROOT_URLCONF, else urls.py nobody includes."""
        roots: List[str] = []
        setting = re.compile(r"""^\s*ROOT_URLCONF\s*=\s*['"]([\w.]+)['"]""", re.M)
        for path in self.indexer.paths():
            lowered = path.lower()
            if "settings" in lowered or "config" in lowered or "conf/" in lowered:
                for module in setting.findall(self._read(path) or ""):
                    target = self.indexer.module_path(module)
                    if target and target not in roots:
                        roots.append(target)
        if roots:
            return roots
        candidates = sorted(p for p in self.indexer.paths() if p.endswith("urls.py"))
        included = set()
        for path in candidates:
            included.update(self._included_modules(self._patterns(path)))
        return [p for p in candidates if p not in included] or candidates

    def _included_modules(self, patterns: list) -> List[str]:
        found = []
        for item in patterns:
            if isinstance(item, list):
                found += self._included_modules(item)
            elif isinstance(item, Pattern) and isinstance(item.view, Include):
                target = item.view.target
                if isinstance(target, str) and self.indexer.module_path(target):
                    found.append(self.indexer.module_path(target))
                elif isinstance(target, list):
                    found += self._included_modules(target)
        return found

    def build(self) -> List[Route]:
        routes: List[Route] = []
        for root in self.root_urlconfs():
            self._expand(root, self._patterns(root), ("", ""), routes, 0)
        for route in routes:
            route.handlers = self._handlers(route)
        return routes

    def _read(self, path: str) -> Optional[str]:
        try:
            return (self.indexer.project_path / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _patterns(self, path: str) -> list:
        text = self._read(path)
        if text is None:
            return []
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return []
        evaluator = _UrlConfEvaluator()
        evaluator.run(tree.body)
        patterns = evaluator.env.get("urlpatterns")
        return patterns if isinstance(patterns, list) else []

    def _expand(self, path: str, patterns: list, prefix: Tuple[str, str], out: List[Route], depth: int) -> None:
        if depth > _MAX_INCLUDE_DEPTH:
            return
        for item in patterns:
            if isinstance(item, RouterUrls):
                self._expand_router(path, item.router, prefix, out)
            elif isinstance(item, list):
                self._expand(path, item, prefix, out, depth + 1)
            elif isinstance(item, Pattern) and isinstance(item.route, str):
                display, regex = _segment(item.route, item.regex)
                full = (prefix[0] + display, prefix[1] + regex)
                view = item.view
                if isinstance(view, Include):
                    target = view.target
                    if isinstance(target, str):
                        module = self.indexer.module_path(target)
                        if module:
                            self._expand(module, self._patterns(module), full, out, depth + 1)
                    elif isinstance(target, list):
                        self._expand(path, target, full, out, depth + 1)
                    elif isinstance(target, RouterUrls):
                        self._expand_router(path, target.router, full, out)
                elif isinstance(view, (Ref, AsView)):
                    out.append(Route(full[0], full[1], path, view))

    def _expand_router(self, path: str, router: Router, prefix: Tuple[str, str], out: List[Route]) -> None:
        for raw_prefix, viewset in router.registrations:
            if not isinstance(raw_prefix, str) or not isinstance(viewset, Ref):
                continue
            base = raw_prefix.strip("^$/")
            cls = self._class(path, viewset)
            available = self._viewset_actions(cls) if cls else {a for _, _, a in _ROUTER_ACTIONS}
            for detail in (False, True):
                actions = {m: a for d, m, a in _ROUTER_ACTIONS if d == detail and a in available}
                if actions:
                    sub = f"{base}/<pk>/" if detail else f"{base}/"
                    rx = re.escape(base) + ("/[^/.]+/" if detail else "/")
                    out.append(Route(prefix[0] + sub, prefix[1] + rx, path, ("router", viewset, actions)))
            for detail, methods, url_path, action in self._extra_actions(cls) if cls else []:
                sub = f"{base}/<pk>/{url_path}/" if detail else f"{base}/{url_path}/"
                rx = re.escape(base) + ("/[^/.]+/" if detail else "/") + re.escape(url_path) + "/"
                out.append(
                    Route(prefix[0] + sub, prefix[1] + rx, path, ("router", viewset, {m: action for m in methods}))
                )

    # ---- view resolution ---------------------------------------------

    def _class(self, path: str, ref: Ref) -> Optional[Tuple[FileInfo, str]]:
        symbol = self.indexer.resolve(path, ref.dotted)
        return (symbol[1], symbol[2]) if symbol and symbol[0] == "class" else None

    def _viewset_actions(self, cls: Tuple[FileInfo, str]) -> set:
        """Router actions the viewset provides: its own methods plus DRF bases."""
        found = {a for _, _, a in _ROUTER_ACTIONS if self.indexer.find_method(cls[0], cls[1], a)}
        stack, seen = [cls], set()
        while stack:
            info, name = stack.pop()
            if (info.path, name) in seen or name not in info.classes:
                continue
            seen.add((info.path, name))
            for base in info.classes[name].bases:
                symbol = self.indexer.resolve(info.path, base)
                if symbol and symbol[0] == "class":
                    stack.append((symbol[1], symbol[2]))
                else:
                    found |= _DRF_BASE_ACTIONS.get(base[-1], set())
        return found

    def _decorators_at(self, path: str, line: int) -> List[ast.expr]:
        if path not in self._decorators:
            table: Dict[int, List[ast.expr]] = {}
            try:
                tree = ast.parse(self._read(path) or "")
            except (SyntaxError, ValueError):
                tree = ast.Module(body=[], type_ignores=[])
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
                    table[node.decorator_list[0].lineno] = node.decorator_list
            self._decorators[path] = table
        return self._decorators[path].get(line, [])

    def _extra_actions(self, cls: Tuple[FileInfo, str]) -> List[Tuple[bool, List[str], str, str]]:
        """DRF @action methods: (detail, http methods, url path, method name)."""
        info, name = cls
        actions = []
        for method, line in info.classes[name].methods.items():
            for deco in self._decorators_at(info.path, line):
                if isinstance(deco, ast.Call) and getattr(deco.func, "id", getattr(deco.func, "attr", "")) == "action":
                    kw = {k.arg: k.value for k in deco.keywords if k.arg}
                    detail = isinstance(kw.get("detail"), ast.Constant) and bool(kw["detail"].value)
                    methods = _string_list(kw.get("methods")) or ["get"]
                    url_path = kw.get("url_path")
                    url = url_path.value if isinstance(url_path, ast.Constant) else method
                    actions.append((detail, [m.lower() for m in methods], str(url), method))
        return actions

    def _function_methods(self, path: str, line: int) -> List[str]:
        """HTTP methods from @api_view([...]) / @require_http_methods([...])."""
        for deco in self._decorators_at(path, line):
            if isinstance(deco, ast.Call) and getattr(deco.func, "id", getattr(deco.func, "attr", "")) in (
                "api_view",
                "require_http_methods",
            ):
                methods = _string_list(deco.args[0] if deco.args else None)
                if methods:
                    return [m.lower() for m in methods]
        return ["any"]

    def _node(self, path: str, line: int) -> Optional[Dict]:
        return self.node_ids.get((path, line))

    def _handler(self, method: str, action: str, info: FileInfo, cls: str) -> Dict:
        hit = self.indexer.find_method(info, cls, action)
        node = self._node(*hit) if hit else self._node(info.path, info.classes[cls].line)
        entry = {"method": method.upper(), "action": action}
        if not hit:
            entry["inherited"] = True  # served by a base class outside the project
        if node:
            entry.update(handler=f"{node['path']}:{node['qualified_name']}", node_id=node["id"], line=node["line"])
        else:
            entry["handler"] = f"{info.path}:{cls}.{action}"
        return entry

    def _handlers(self, route: Route) -> List[Dict]:
        view = route.view
        if isinstance(view, tuple):  # router route
            _, ref, actions = view
            cls = self._class(route.urls_file, ref)
            if not cls:
                return [{"method": m.upper(), "action": a, "handler": ".".join(ref.dotted) + " (external)"} for m, a in actions.items()]
            return [self._handler(m, a, cls[0], cls[1]) for m, a in actions.items()]
        if isinstance(view, AsView) and isinstance(view.view, Ref):
            cls = self._class(route.urls_file, view.view)
            if not cls:
                return [{"method": "ANY", "handler": ".".join(view.view.dotted) + " (external)"}]
            if isinstance(view.actions, dict):
                return [
                    self._handler(m, a, cls[0], cls[1])
                    for m, a in view.actions.items()
                    if isinstance(m, str) and isinstance(a, str)
                ]
            methods = [m for m in HTTP_METHODS if self.indexer.find_method(cls[0], cls[1], m)]
            return [self._handler(m, m, cls[0], cls[1]) for m in methods] or [
                self._handler("any", "dispatch", cls[0], cls[1])
            ]
        if isinstance(view, Ref):
            symbol = self.indexer.resolve(route.urls_file, view.dotted)
            if symbol and symbol[0] == "function":
                info, name = symbol[1], symbol[2]
                node = self._node(info.path, info.functions[name])
                methods = self._function_methods(info.path, info.functions[name])
                entry = {"handler": f"{info.path}:{name}"}
                if node:
                    entry.update(node_id=node["id"], line=node["line"])
                return [dict(entry, method=m.upper()) for m in methods]
            return [{"method": "ANY", "handler": ".".join(view.dotted) + " (external)"}]
        return []


def _string_list(node: Optional[ast.AST]) -> List[str]:
    if isinstance(node, (ast.List, ast.Tuple)):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def split_route(spec: str) -> Tuple[str, str]:
    """'GET /api/users/?q=1' -> ('get', 'api/users/')."""
    spec = spec.strip()
    method, _, rest = spec.partition(" ")
    if rest and method.lower() in HTTP_METHODS:
        spec = rest.strip()
        method = method.lower()
    else:
        method = ""
    path = spec.split("?", 1)[0].split("#", 1)[0]
    return method, path.lstrip("/")


def _loose(regex: str) -> str:
    """The route regex with every typed converter widened to one path segment."""
    for name, rx in _CONVERTERS.items():
        if name != "path":
            regex = regex.replace(rx, "[^/]+")
    return regex


def _is_catch_all(route: Route) -> bool:
    """A route that accepts any URL, such as an SPA fallback `^(?P<path>.*)$`."""
    try:
        return re.fullmatch(route.regex, "trellis-probe/x/y/") is not None
    except re.error:
        return False


def _first_match(routes: List[Route], path: str, loose: bool) -> Optional[Route]:
    for route in routes:
        try:
            rx = re.compile(_loose(route.regex) if loose else route.regex)
        except re.error:
            continue
        if rx.fullmatch(path) or rx.fullmatch(path.rstrip("/") + "/"):
            return route
    return None


def match_routes(routes: List[Route], spec: str) -> List[Route]:
    """The route Django would dispatch `spec` to (first match in URLconf order).

    A route pattern ('/api/users/<pk>/') matches its own text exactly. A URL
    is matched with Django's converters (int, slug, uuid, ...); if nothing
    matches, or only a catch-all does, again with any segment accepted on the
    specific routes, so placeholder ids still work (marked `loose`). With a method ('POST /api/x/'), only
    that method's handlers are kept.
    """
    method, path = split_route(spec)
    loose = False
    if "<" in path:
        wanted = _PATH_PARAM.sub(lambda m: f"<{m.group(2)}>", path).strip("/")
        found = next((r for r in routes if r.path.strip("/") == wanted), None)
    else:
        found = _first_match(routes, path, loose=False)
        if found is None or _is_catch_all(found):
            specific = [r for r in routes if not _is_catch_all(r)]
            widened = _first_match(specific, path, loose=True)
            if widened is not None:
                found, loose = widened, True
    if found is None:
        return []
    handlers = found.handlers
    if method:
        handlers = [h for h in handlers if h["method"] in (method.upper(), "ANY")] or handlers
    return [Route(found.path, found.regex, found.urls_file, found.view, found.name, handlers, loose)]
