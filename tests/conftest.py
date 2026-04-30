"""Shared test fixtures for Trellis test suite."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from engine import TrellisEngine
from store import GraphStore


# Enable no-auth mode for all tests
os.environ["TRELLIS_ALLOW_NO_AUTH"] = "true"


@pytest.fixture
def temp_data_dir():
    """Provide a temporary directory for test data."""
    with tempfile.TemporaryDirectory() as tmp:
        old_data_dir = os.environ.get("TRELLIS_DATA_DIR", "")
        old_db_path = os.environ.get("TRELLIS_DB_PATH", "")
        os.environ["TRELLIS_DATA_DIR"] = tmp
        os.environ["TRELLIS_DB_PATH"] = str(Path(tmp) / "test.db")
        yield tmp
        if old_data_dir:
            os.environ["TRELLIS_DATA_DIR"] = old_data_dir
        else:
            os.environ.pop("TRELLIS_DATA_DIR", None)
        if old_db_path:
            os.environ["TRELLIS_DB_PATH"] = old_db_path
        else:
            os.environ.pop("TRELLIS_DB_PATH", None)


@pytest.fixture
def graph_store(temp_data_dir):
    """Provide a GraphStore backed by a temp directory."""
    store = GraphStore()
    yield store
    store.close()


@pytest.fixture
def trellis_engine(graph_store):
    """Provide a TrellisEngine backed by a temp store."""
    return TrellisEngine(store=graph_store)


@pytest.fixture
def sample_repo(temp_data_dir):
    """Create a sample Python repo with known structure for testing."""
    repo = Path(temp_data_dir) / "sample_repo"
    repo.mkdir()

    # Main feature module
    (repo / "features").mkdir()
    (repo / "features" / "auth.py").write_text(
        '''"""
Authentication feature.
Feature: User Authentication
"""
import hashlib

def authenticate_user(username: str, password: str) -> bool:
    """Authenticate a user."""
    hashed = hash_password(password)
    return check_credentials(username, hashed)

def hash_password(password: str) -> str:
    """Hash a password."""
    return hashlib.sha256(password.encode()).hexdigest()

def check_credentials(username: str, hashed: str) -> bool:
    """Check credentials against database."""
    return True
'''
    )

    # Payment feature module
    (repo / "features" / "payment.py").write_text(
        '''"""
Payment processing feature.
Feature: Payment Processing
"""
from features.auth import authenticate_user

def process_payment(user: str, amount: float) -> dict:
    """Process a payment."""
    if not authenticate_user(user, ""):
        return {"error": "not authenticated"}
    return charge_card(amount)

def charge_card(amount: float) -> dict:
    """Charge a credit card."""
    return {"status": "charged", "amount": amount}

def refund_payment(payment_id: str) -> dict:
    """Refund a payment."""
    return {"status": "refunded"}
'''
    )

    # Reporting feature module
    (repo / "features" / "reports.py").write_text(
        '''"""
Reporting feature.
"""
from features.payment import process_payment

def generate_report(user: str) -> dict:
    """Generate a report."""
    return {"data": "report_data"}

def export_csv(report: dict) -> str:
    """Export report to CSV."""
    return "csv,data"
'''
    )

    return str(repo)


@pytest.fixture
def synced_project(trellis_engine, sample_repo):
    """Sync the sample repo and return the project_id."""
    project_id = "test_project"
    result = trellis_engine.sync_project(
        project_id=project_id,
        repo_path=sample_repo,
        config_path=".trellis/config.yaml",
        incremental=True,
    )
    assert result.indexed_features > 0
    assert result.indexed_functions > 0
    return project_id
