from __future__ import annotations

import json
from pathlib import Path

from app.db import FIXTURES_DIR, connect


VERIFY_REPORTS_DDL = """
CREATE TABLE IF NOT EXISTS verify_reports (
  id INTEGER PRIMARY KEY,
  settle_run_id INTEGER REFERENCES settle_runs(id),
  ledger_net_cents INTEGER NOT NULL,
  posted_cents INTEGER NOT NULL,
  staging_cents INTEGER NOT NULL,
  total_cents INTEGER NOT NULL,
  quarantine_count INTEGER NOT NULL,
  consistent INTEGER NOT NULL,
  basis TEXT NOT NULL DEFAULT '已对外余额',
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
)
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'CNY',
  posted_cents INTEGER NOT NULL DEFAULT 0,
  staging_cents INTEGER NOT NULL DEFAULT 0,
  last_settle_run_id INTEGER,
  last_merge_id INTEGER
);
CREATE TABLE IF NOT EXISTS ledger_entries (
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
CREATE TABLE IF NOT EXISTS quarantine_items (
  id INTEGER PRIMARY KEY,
  entry_key TEXT NOT NULL,
  account_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  settle_run_id INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settle_runs (
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
CREATE TABLE IF NOT EXISTS balance_merges (
  id INTEGER PRIMARY KEY,
  status TEXT NOT NULL,
  account_count INTEGER NOT NULL DEFAULT 0,
  staged_total_cents INTEGER NOT NULL DEFAULT 0,
  posted_before_cents INTEGER NOT NULL DEFAULT 0,
  posted_after_cents INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS balance_merge_items (
  id INTEGER PRIMARY KEY,
  merge_id INTEGER NOT NULL REFERENCES balance_merges(id),
  account_id INTEGER NOT NULL REFERENCES accounts(id),
  staged_cents INTEGER NOT NULL,
  posted_before_cents INTEGER NOT NULL,
  posted_after_cents INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_balance_merge_items_merge
  ON balance_merge_items(merge_id);
CREATE TABLE IF NOT EXISTS verify_reports (
  id INTEGER PRIMARY KEY,
  settle_run_id INTEGER REFERENCES settle_runs(id),
  ledger_net_cents INTEGER NOT NULL,
  posted_cents INTEGER NOT NULL,
  staging_cents INTEGER NOT NULL,
  total_cents INTEGER NOT NULL,
  quarantine_count INTEGER NOT NULL,
  consistent INTEGER NOT NULL,
  basis TEXT NOT NULL DEFAULT '已对外余额',
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate_accounts(conn) -> None:
    """单余额 → 双余额列：posted_cents（已对外）/ staging_cents（配平暂存）。"""
    cols = _columns(conn, "accounts")
    if not cols:
        return
    if "posted_cents" in cols and "staging_cents" in cols:
        if "last_merge_id" not in cols:
            conn.execute("ALTER TABLE accounts ADD COLUMN last_merge_id INTEGER")
        conn.commit()
        return

    if "posted_cents" in cols:
        posted_expr = "COALESCE(posted_cents, 0)"
    elif "balance_cents" in cols:
        posted_expr = "COALESCE(balance_cents, 0)"
    else:
        posted_expr = "0"
    staging_expr = "COALESCE(staging_cents, 0)" if "staging_cents" in cols else "0"
    settle_expr = "last_settle_run_id" if "last_settle_run_id" in cols else "NULL"

    # accounts 被 ledger_entries / balance_merge_items 外键引用，重建期间必须关闭外键检查。
    if conn.in_transaction:
        conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN")
        conn.execute("DROP TABLE IF EXISTS accounts__dual")
        conn.execute(
            """
            CREATE TABLE accounts__dual (
              id INTEGER PRIMARY KEY,
              code TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              currency TEXT NOT NULL DEFAULT 'CNY',
              posted_cents INTEGER NOT NULL DEFAULT 0,
              staging_cents INTEGER NOT NULL DEFAULT 0,
              last_settle_run_id INTEGER,
              last_merge_id INTEGER
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO accounts__dual(
              id, code, name, currency, posted_cents, staging_cents,
              last_settle_run_id, last_merge_id
            )
            SELECT id, code, name, currency, {posted_expr}, {staging_expr},
                   {settle_expr}, NULL
            FROM accounts
            """
        )
        conn.execute("DROP TABLE accounts")
        conn.execute("ALTER TABLE accounts__dual RENAME TO accounts")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_verify_reports(conn) -> None:
    """互证报告拆成已对外/暂存/合计；旧报告按已对外单列迁移，暂存记 0。"""
    cols = _columns(conn, "verify_reports")
    if not cols or "posted_cents" in cols:
        return

    conn.execute("ALTER TABLE verify_reports RENAME TO verify_reports__legacy")
    conn.executescript(VERIFY_REPORTS_DDL)
    if "balance_sum_cents" in cols:
        conn.execute(
            """
            INSERT INTO verify_reports(
              id, settle_run_id, ledger_net_cents, posted_cents, staging_cents,
              total_cents, quarantine_count, consistent,
              basis, detail_json, created_at
            )
            SELECT id, settle_run_id, ledger_net_cents, balance_sum_cents, 0,
                   balance_sum_cents, quarantine_count, consistent,
                   '已对外余额', detail_json, created_at
            FROM verify_reports__legacy
            """
        )
    conn.execute("DROP TABLE verify_reports__legacy")
    conn.commit()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate_accounts(conn)
        _migrate_verify_reports(conn)

        n = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
        if n:
            return
        seed_path = FIXTURES_DIR / "seed.json"
        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        for a in data["accounts"]:
            conn.execute(
                """
                INSERT INTO accounts(id, code, name, currency, posted_cents, staging_cents)
                VALUES (?, ?, ?, ?, 0, 0)
                """,
                (a["id"], a["code"], a["name"], a["currency"]),
            )
        for e in data["forward_entries"]:
            conn.execute(
                """
                INSERT INTO ledger_entries(
                  id, entry_key, account_id, kind, amount_cents, biz_ref,
                  target_entry_key, booked_at, status, note
                ) VALUES (?, ?, ?, '正向', ?, ?, NULL, ?, '待归并', ?)
                """,
                (
                    e["id"],
                    e["entry_key"],
                    e["account_id"],
                    e["amount_cents"],
                    e.get("biz_ref", ""),
                    e["booked_at"],
                    e.get("note", ""),
                ),
            )
        # 冲正 id 与正向可能重叠，用独立自增：先取 max
        max_id = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM ledger_entries").fetchone()["m"]
        for i, e in enumerate(data["reversal_entries"], start=1):
            conn.execute(
                """
                INSERT INTO ledger_entries(
                  id, entry_key, account_id, kind, amount_cents, biz_ref,
                  target_entry_key, booked_at, status, note
                ) VALUES (?, ?, ?, '冲正', ?, ?, ?, ?, '待归并', ?)
                """,
                (
                    max_id + i,
                    e["entry_key"],
                    e["account_id"],
                    e["amount_cents"],
                    e.get("biz_ref", ""),
                    e.get("target_entry_key"),
                    e["booked_at"],
                    e.get("note", ""),
                ),
            )
        conn.commit()
