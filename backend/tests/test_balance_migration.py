"""旧库（单余额 balance_cents）升级到双余额列的迁移。"""

from __future__ import annotations

import sqlite3

import pytest

from app import db as appdb
from app import seed

LEGACY_SCHEMA = """
CREATE TABLE accounts (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'CNY',
  balance_cents INTEGER NOT NULL DEFAULT 0,
  last_settle_run_id INTEGER
);
CREATE TABLE ledger_entries (
  id INTEGER PRIMARY KEY,
  entry_key TEXT NOT NULL UNIQUE,
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  kind TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  biz_ref TEXT NOT NULL DEFAULT '',
  target_entry_key TEXT,
  booked_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '待归并',
  note TEXT NOT NULL DEFAULT '',
  settle_run_id INTEGER
);
CREATE TABLE quarantine_items (
  id INTEGER PRIMARY KEY,
  entry_key TEXT NOT NULL,
  account_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  settle_run_id INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE settle_runs (
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL,
  forward_count INTEGER NOT NULL DEFAULT 0,
  reversal_count INTEGER NOT NULL DEFAULT 0,
  settled_count INTEGER NOT NULL DEFAULT 0,
  quarantine_count INTEGER NOT NULL DEFAULT 0,
  net_cents INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE verify_reports (
  id INTEGER PRIMARY KEY,
  settle_run_id INTEGER REFERENCES settle_runs(id),
  ledger_net_cents INTEGER NOT NULL,
  balance_sum_cents INTEGER NOT NULL,
  quarantine_count INTEGER NOT NULL,
  consistent INTEGER NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


@pytest.fixture()
def legacy_db(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO accounts VALUES (1, 'ACC-A', '旧账户', 'CNY', 12345, 1)"
    )
    conn.execute(
        """
        INSERT INTO ledger_entries VALUES (
          1, 'FWD-X', 1, '正向', 12345, '', '', '2026-11-16T00:00:00Z', '已归并', '', 1
        )
        """
    )
    conn.execute(
        "INSERT INTO settle_runs VALUES (1, '完成', 1, 0, 1, 0, 12345, '2026-11-16T00:00:00Z', NULL)"
    )
    conn.execute(
        "INSERT INTO verify_reports VALUES (1, 1, 12345, 12345, 0, 1, '{}', '2026-11-16T00:00:02Z')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(appdb, "DB_PATH", path)
    return path


def test_legacy_balance_migrates_to_posted(legacy_db):
    seed.init_db()
    conn = sqlite3.connect(legacy_db)
    conn.row_factory = sqlite3.Row
    acct = dict(conn.execute("SELECT * FROM accounts").fetchone())
    assert acct["posted_cents"] == 12345
    assert acct["staging_cents"] == 0
    assert acct["last_settle_run_id"] == 1

    report = dict(conn.execute("SELECT * FROM verify_reports").fetchone())
    assert report["posted_cents"] == 12345
    assert report["staging_cents"] == 0
    assert report["total_cents"] == 12345
    assert report["basis"] == "已对外余额"

    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # 旧列已消失，只留双列
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(accounts)")}
    assert "balance_cents" not in cols
    assert {"posted_cents", "staging_cents"} <= cols
    conn.close()


def test_migration_is_idempotent(legacy_db):
    seed.init_db()
    seed.init_db()
    conn = sqlite3.connect(legacy_db)
    rows = conn.execute("SELECT posted_cents, staging_cents FROM accounts").fetchall()
    assert rows == [(12345, 0)]
    conn.close()
