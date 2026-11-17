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

# 双余额的纯逻辑单测，不起 HTTP。
from app.modules import dual_rail  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    """每个测试模块用干净库，避免归并/合并状态跨模块串联。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    seed.init_db()
    yield


def test_merge_plan_skips_zero_staging():
    rows = [
        {"id": 1, "posted_cents": 100, "staging_cents": 0},
        {"id": 2, "posted_cents": 50, "staging_cents": 30},
        {"id": 3, "posted_cents": -20, "staging_cents": -5},
    ]
    plan = dual_rail.merge_plan(rows)
    assert [(p.account_id, p.staged_cents, p.posted_after_cents) for p in plan] == [
        (2, 30, 80),
        (3, -5, -25),
    ]


def test_totals_are_three_columns():
    rows = [
        {"posted_cents": 100, "staging_cents": 10},
        {"posted_cents": 0, "staging_cents": 0},
    ]
    assert dual_rail.totals(rows) == {
        "posted_cents": 100,
        "staging_cents": 10,
        "total_cents": 110,
        "staged_accounts": 1,
    }


def test_reconcile_reports_per_account_gap():
    rows = [
        {"id": 1, "code": "A", "posted_cents": 80, "staging_cents": 20},
        {"id": 2, "code": "B", "posted_cents": 10, "staging_cents": 0},
    ]
    assert dual_rail.reconcile(rows, {1: 100, 2: 10}) == []
    bad = dual_rail.reconcile(rows, {1: 100, 2: 11})
    assert bad == [
        {
            "account_id": 2,
            "account_code": "B",
            "流水净额分": 11,
            "已对外分": 10,
            "暂存分": 0,
            "合计分": 10,
        }
    ]
