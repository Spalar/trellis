"""Tests for Django / DRF route tracing."""

from __future__ import annotations

import ast
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.trellis.django_routes import DjangoRoutes, match_routes, split_route
from src.trellis.python_call_indexer import PythonCallGraphIndexer
from src.trellis.utils import get_code_graph_path

SOURCES = {
    "site/settings.py": 'ROOT_URLCONF = "site.urls"\n',
    "site/urls.py": """
from django.urls import include, path, re_path
from shop import views

urlpatterns = [
    path("api/", include("shop.urls")),
    re_path(r"^legacy/(?P<slug>[-\\w]+)/$", views.legacy),
    path("users/<int:pk>/", views.user_detail),
    path("users/me/", views.me),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
urlpatterns = format_suffix_patterns(urlpatterns)
""",
    "shop/urls.py": """
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from shop import views
from shop.views import OrderViewSet

router = DefaultRouter()
router.register(r"orders", OrderViewSet, basename="order")

LIST = {"get": "list", "post": "create"}
NESTED = [(views.ItemViewSet, "items")]

urlpatterns = [
    path("", include(router.urls)),
    path("health/", views.health),
    path("report/", views.ReportView.as_view()),
]

for viewset, prefix in NESTED:
    urlpatterns += [path(f"stores/<int:store_pk>/{prefix}/", viewset.as_view(LIST))]
""",
    "shop/views.py": """
from rest_framework.decorators import action, api_view
from rest_framework.viewsets import ModelViewSet, ViewSet

def helper():
    return 1

@api_view(["GET"])
def health(request):
    return helper()

def legacy(request, slug):
    return slug

def user_detail(request, pk):
    return pk

def me(request):
    return request

class ReportView:
    def get(self, request):
        return helper()

class OrderViewSet(ModelViewSet):
    def create(self, request):
        return helper()

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel_order(self, request, pk=None):
        return pk

class ItemViewSet(ViewSet):
    def list(self, request):
        return []
""",
}


@pytest.fixture
def routes(monkeypatch):
    data_dir = tempfile.mkdtemp(prefix="trellis-data-")
    project_dir = tempfile.mkdtemp(prefix="trellis-project-")
    monkeypatch.setenv("TRELLIS_DATA_DIR", data_dir)
    root = Path(project_dir)
    db_path = get_code_graph_path(root) / "index.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT UNIQUE);
        CREATE TABLE nodes (id INTEGER PRIMARY KEY, file_id INTEGER, type TEXT, name TEXT,
            qualified_name TEXT, start_line INTEGER, end_line INTEGER);
        CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER,
            relation TEXT, metadata TEXT);
        """
    )
    nodes = {}
    for fid, (rel, text) in enumerate(SOURCES.items(), start=1):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
        conn.execute("INSERT INTO files VALUES (?, ?)", (fid, rel))
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
                cur = conn.execute(
                    "INSERT INTO nodes (file_id, type, name, qualified_name, start_line, end_line)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (fid, "class" if isinstance(node, ast.ClassDef) else "function",
                     node.name, node.name, line, node.end_lineno),
                )
                nodes[(rel, line)] = {"id": cur.lastrowid, "qualified_name": node.name,
                                      "path": rel, "line": line}
    conn.commit()
    conn.close()

    indexer = PythonCallGraphIndexer(str(root))
    indexer.load()
    yield DjangoRoutes(indexer, nodes).build()

    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(project_dir, ignore_errors=True)


def _handlers(routes, spec):
    found = match_routes(routes, spec)
    assert found, spec
    return found[0].path, [(h["method"], h["handler"]) for h in found[0].handlers]


def test_include_and_api_view_function(routes):
    assert _handlers(routes, "GET /api/health/") == (
        "api/health/",
        [("GET", "shop/views.py:health")],
    )


def test_router_list_detail_and_action(routes):
    path, handlers = _handlers(routes, "POST /api/orders/")
    assert path == "api/orders/"
    assert handlers == [("POST", "shop/views.py:create")]

    path, handlers = _handlers(routes, "GET /api/orders/12/")
    assert path == "api/orders/<pk>/"
    assert handlers == [("GET", "shop/views.py:OrderViewSet")]  # inherited from DRF

    assert _handlers(routes, "POST /api/orders/12/cancel/")[1] == [
        ("POST", "shop/views.py:cancel_order")
    ]


def test_class_view_and_loop_generated_route(routes):
    assert _handlers(routes, "/api/report/")[1] == [("GET", "shop/views.py:get")]
    path, handlers = _handlers(routes, "GET /api/stores/3/items/")
    assert path == "api/stores/<store_pk>/items/"
    assert handlers == [("GET", "shop/views.py:list")]


def test_re_path_and_pattern_text(routes):
    assert _handlers(routes, "/legacy/blue-shoes/")[1] == [("ANY", "shop/views.py:legacy")]
    assert _handlers(routes, "/api/stores/<int:store_pk>/items/")[0] == "api/stores/<store_pk>/items/"


def test_unknown_route(routes):
    assert match_routes(routes, "GET /nope/") == []


def test_split_route():
    assert split_route("GET /api/x/?q=1") == ("get", "api/x/")
    assert split_route("/api/x/") == ("", "api/x/")


def test_unknown_wrappers_keep_routes(routes):
    # urlpatterns += static(...) and format_suffix_patterns(urlpatterns)
    assert _handlers(routes, "/api/health/")[0] == "api/health/"


def test_typed_converters_follow_django_order(routes):
    assert _handlers(routes, "/users/me/")[1] == [("ANY", "shop/views.py:me")]
    assert _handlers(routes, "/users/12/")[1] == [("ANY", "shop/views.py:user_detail")]


def test_loose_match_for_placeholder_ids(routes):
    found = match_routes(routes, "GET /users/abc/")  # 'abc' is not an int
    assert found[0].loose and found[0].path == "users/<pk>/"
    assert not match_routes(routes, "GET /users/12/")[0].loose
    found = match_routes(routes, "GET /api/stores/x/items/")
    assert found and found[0].loose and found[0].path == "api/stores/<store_pk>/items/"


def test_roots_without_root_urlconf(routes, tmp_path):
    from src.trellis.django_routes import DjangoRoutes

    class FakeIndexer:
        project_path = tmp_path

        def paths(self):
            return ["proj/urls.py", "app/urls.py"]

        def module_path(self, dotted):
            return {"app.urls": "app/urls.py"}.get(dotted)

    (tmp_path / "proj").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "proj/urls.py").write_text(
        "urlpatterns = [path('api/', include('app.urls'))]\n", encoding="utf-8"
    )
    (tmp_path / "app/urls.py").write_text("urlpatterns = []\n", encoding="utf-8")
    assert DjangoRoutes(FakeIndexer(), {}).root_urlconfs() == ["proj/urls.py"]
