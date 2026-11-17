from pathlib import Path
import os
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DATA_DIR", str(ROOT / "backend" / "data_test"))
os.environ.setdefault("FIXTURES_DIR", str(ROOT / "fixtures"))

from app import seed  # noqa: E402
from app.db import DB_PATH  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    """每个测试模块用干净库，避免归并/合并状态跨模块串联。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    seed.init_db()
    yield
