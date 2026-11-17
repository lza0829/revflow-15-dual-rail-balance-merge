from pathlib import Path
import os
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DATA_DIR"] = str(ROOT / "backend" / "data_test")
os.environ["FIXTURES_DIR"] = str(ROOT / "fixtures")

db_path = Path(os.environ["DATA_DIR"]) / "app.db"
if db_path.exists():
    db_path.unlink()

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _accounts(client):
    return {a["code"]: a for a in client.get("/api/accounts").json()}


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["项目"] == "冲流台"


def test_accounts_have_dual_balances(client):
    rows = client.get("/api/accounts").json()
    assert rows
    for a in rows:
        assert "posted_cents" in a and "staging_cents" in a
        assert a["posted_cents"] == 0 and a["staging_cents"] == 0
        assert a["total_cents"] == 0


def test_settle_failure_rolls_back_staging(client):
    """归并失败：暂存回到本趟前，已对外不变。"""
    before = _accounts(client)
    r = client.post("/api/settle-runs?simulate_failure=true")
    assert r.status_code == 500
    after = _accounts(client)
    assert {k: (v["posted_cents"], v["staging_cents"]) for k, v in after.items()} == {
        k: (v["posted_cents"], v["staging_cents"]) for k, v in before.items()
    }
    # 运行记录也应回滚
    assert client.get("/api/settle-runs").json() == []


def test_settle_writes_staging_only(client):
    d = client.get("/api/dashboard").json()
    assert d["账户数"] >= 3
    assert d["正向笔数"] >= 5
    assert d["冲正笔数"] >= 4

    run = client.post("/api/settle-runs")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "完成"
    assert body["quarantine_count"] >= 1
    assert body["net_cents"] != 0

    accounts = _accounts(client)
    # 已对外余额未被本趟触碰
    for a in accounts.values():
        assert a["posted_cents"] == 0
        assert a["staging_cents"] == a["total_cents"]

    # 暂存净额 = 已归并流水净额（逐户）
    ledger = client.get("/api/entries").json()
    per_account = {}
    for e in ledger:
        if e["status"] != "已归并":
            continue
        sign = 1 if e["kind"] == "正向" else -1
        per_account[e["account_id"]] = per_account.get(e["account_id"], 0) + sign * e["amount_cents"]
    for aid, expected in per_account.items():
        row = next(a for a in accounts.values() if a["id"] == aid)
        assert row["staging_cents"] == expected

    q = client.get("/api/quarantine").json()
    assert any(x["reason"] == "冲正无原单" for x in q)


def test_verify_reports_posted_staging_total(client):
    report = client.post("/api/verify-reports")
    assert report.status_code == 200, report.text
    rj = report.json()
    assert rj["basis"] == "已对外余额"
    assert rj["posted_cents"] == 0
    assert rj["staging_cents"] != 0
    assert rj["total_cents"] == rj["posted_cents"] + rj["staging_cents"]
    assert rj["ledger_net_cents"] == rj["total_cents"]
    assert rj["consistent"] == 1
    assert rj["detail"]["对外口径"] == "已对外余额"
    assert rj["detail"]["配平暂存分"] == rj["staging_cents"]


def test_merge_failure_keeps_both_balances(client):
    before = _accounts(client)
    r = client.post("/api/balance-merges?simulate_failure=true")
    assert r.status_code == 500
    after = _accounts(client)
    assert {k: (v["posted_cents"], v["staging_cents"]) for k, v in after.items()} == {
        k: (v["posted_cents"], v["staging_cents"]) for k, v in before.items()
    }
    assert client.get("/api/balance-merges").json() == []


def test_merge_moves_staging_to_posted_atomically(client):
    before = _accounts(client)
    staged = {k: v["staging_cents"] for k, v in before.items() if v["staging_cents"]}

    merge = client.post("/api/balance-merges")
    assert merge.status_code == 200, merge.text
    body = merge.json()
    assert body["status"] == "完成"
    assert body["staged_total_cents"] == sum(staged.values())
    assert body["posted_after_cents"] == body["posted_before_cents"] + body["staged_total_cents"]

    after = _accounts(client)
    for code, a in after.items():
        assert a["staging_cents"] == 0
        assert a["posted_cents"] == before[code]["posted_cents"] + before[code]["staging_cents"]
        if before[code]["staging_cents"]:
            assert a["last_merge_id"] == body["id"]

    # 合并后再互证：已对外 = 流水净额，暂存 = 0
    report = client.post("/api/verify-reports").json()
    assert report["staging_cents"] == 0
    assert report["posted_cents"] == report["ledger_net_cents"]
    assert report["consistent"] == 1


def test_staging_reads_separately_from_posted(client):
    body = client.get("/api/staging").json()
    assert body["对外口径"] == "已对外余额"
    assert body["合计"]["staging_cents"] == 0
    for a in body["账户"]:
        assert a["total_cents"] == a["posted_cents"] + a["staging_cents"]


def test_merge_with_empty_staging_is_noop(client):
    before = _accounts(client)
    merge = client.post("/api/balance-merges")
    assert merge.status_code == 200
    body = merge.json()
    assert body["account_count"] == 0
    assert body["staged_total_cents"] == 0
    after = _accounts(client)
    assert {k: v["posted_cents"] for k, v in after.items()} == {
        k: v["posted_cents"] for k, v in before.items()
    }


def test_settle_after_merge_accumulates_in_staging(client):
    """二次归并：新净额进暂存，已对外保持上次合并结果。"""
    before = _accounts(client)
    run = client.post("/api/settle-runs").json()
    after = _accounts(client)
    for code, a in after.items():
        assert a["posted_cents"] == before[code]["posted_cents"]
    if run["net_cents"]:
        moved = {c: v["staging_cents"] for c, v in after.items() if before[c]["staging_cents"] == 0}
        assert moved
