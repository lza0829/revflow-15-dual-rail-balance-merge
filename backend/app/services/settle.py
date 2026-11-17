from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect, rows_to_dicts
from app.modules import dual_rail

# 余额落库形态：账户双列
#   posted_cents  —— 已对外余额（对账、展示、接口一律以此为准）
#   staging_cents —— 配平暂存余额（本趟匹配净额先落这里，合并前绝不并入已对外）
# 合并日志 balance_merges / balance_merge_items 只做审计，不是第二套余额。


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _account_rows(conn) -> list[dict]:
    rows = rows_to_dicts(conn.execute("SELECT * FROM accounts ORDER BY id").fetchall())
    for r in rows:
        r["total_cents"] = r["posted_cents"] + r["staging_cents"]
    return rows


def _balance_totals(conn) -> dict:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(posted_cents), 0) AS posted,
               COALESCE(SUM(staging_cents), 0) AS staging,
               COALESCE(SUM(posted_cents + staging_cents), 0) AS total,
               COALESCE(SUM(CASE WHEN staging_cents != 0 THEN 1 ELSE 0 END), 0) AS staged_accounts
        FROM accounts
        """
    ).fetchone()
    return {
        "posted_cents": row["posted"],
        "staging_cents": row["staging"],
        "total_cents": row["total"],
        "staged_accounts": row["staged_accounts"],
    }


def dashboard() -> dict:
    with connect() as conn:
        accounts = _account_rows(conn)
        fwd = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_entries WHERE kind = '正向'"
        ).fetchone()["c"]
        rev = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_entries WHERE kind = '冲正'"
        ).fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM ledger_entries WHERE status = '待归并'"
        ).fetchone()["c"]
        q = conn.execute("SELECT COUNT(*) AS c FROM quarantine_items").fetchone()["c"]
        totals = _balance_totals(conn)
        last = conn.execute("SELECT * FROM settle_runs ORDER BY id DESC LIMIT 1").fetchone()
        last_merge = conn.execute(
            "SELECT * FROM balance_merges ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "账户数": len(accounts),
            "正向笔数": fwd,
            "冲正笔数": rev,
            "待归并": pending,
            "未平隔离": q,
            "已对外余额合计分": totals["posted_cents"],
            "暂存余额合计分": totals["staging_cents"],
            "余额合计分": totals["total_cents"],
            "待合并账户数": totals["staged_accounts"],
            "对外口径": "已对外余额",
            "最近归并": dict(last) if last else None,
            "最近合并": dict(last_merge) if last_merge else None,
            "账户": accounts,
        }


def list_accounts() -> list[dict]:
    with connect() as conn:
        return _account_rows(conn)


def list_staging() -> dict:
    """配平暂存分户视图：两套余额分列只读，不把暂存当已对外。"""
    with connect() as conn:
        return {
            "合计": _balance_totals(conn),
            "对外口径": "已对外余额",
            "账户": _account_rows(conn),
        }


def list_entries(kind: str | None = None) -> list[dict]:
    with connect() as conn:
        if kind:
            rows = conn.execute(
                """
                SELECT e.*, a.code AS account_code, a.name AS account_name
                FROM ledger_entries e
                JOIN accounts a ON a.id = e.account_id
                WHERE e.kind = ?
                ORDER BY e.id
                """,
                (kind,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT e.*, a.code AS account_code, a.name AS account_name
                FROM ledger_entries e
                JOIN accounts a ON a.id = e.account_id
                ORDER BY e.id
                """
            ).fetchall()
        return rows_to_dicts(rows)


def list_quarantine() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT q.*, a.code AS account_code
            FROM quarantine_items q
            JOIN accounts a ON a.id = q.account_id
            ORDER BY q.id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


def list_settle_runs() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(
            conn.execute("SELECT * FROM settle_runs ORDER BY id DESC").fetchall()
        )


def list_verify_reports() -> list[dict]:
    with connect() as conn:
        rows = rows_to_dicts(
            conn.execute("SELECT * FROM verify_reports ORDER BY id DESC").fetchall()
        )
        return [_report_out(r) for r in rows]


def _report_out(row: dict) -> dict:
    out = dict(row)
    out["detail"] = json.loads(out.pop("detail_json"))
    return out


def _insert_quarantine(conn, run_id: int, now: str, entry: dict, reason: str) -> None:
    conn.execute(
        """
        INSERT INTO quarantine_items(
          entry_key, account_id, reason, amount_cents, settle_run_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (entry["entry_key"], entry["account_id"], reason, entry["amount_cents"], run_id, now),
    )
    conn.execute(
        "UPDATE ledger_entries SET status = '已隔离', settle_run_id = ? WHERE id = ?",
        (run_id, entry["id"]),
    )


def run_settle(simulate_failure: bool = False) -> dict:
    """跑一轮净额归并：匹配净额只写入配平暂存，绝不改已对外余额。

    整趟写入（流水状态、隔离项、暂存余额、运行记录）在一个事务内；
    任何失败（含演练失败）都回滚，暂存回到本趟前，已对外余额保持原值。
    """
    conn = connect()
    run_id: int | None = None
    try:
        now = _now()
        # 先拿写锁再读候选流水：本趟匹配与写暂存基于同一份快照，且与合并互斥。
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT INTO settle_runs(status, started_at) VALUES ('进行中', ?)", (now,)
        )
        run_id = cur.lastrowid

        forwards = rows_to_dicts(
            conn.execute(
                "SELECT * FROM ledger_entries WHERE kind = '正向' AND status = '待归并' ORDER BY id"
            ).fetchall()
        )
        reversals = rows_to_dicts(
            conn.execute(
                "SELECT * FROM ledger_entries WHERE kind = '冲正' AND status = '待归并' ORDER BY id"
            ).fetchall()
        )
        fwd_by_key = {f["entry_key"]: f for f in forwards}

        used: dict[str, int] = {f["entry_key"]: 0 for f in forwards}
        settled = 0
        quarantined = 0
        account_net: dict[int, int] = {}

        for f in forwards:
            account_net[f["account_id"]] = account_net.get(f["account_id"], 0) + f["amount_cents"]

        for r in reversals:
            target = r.get("target_entry_key")
            if not target or target not in fwd_by_key:
                _insert_quarantine(conn, run_id, now, r, "冲正无原单")
                quarantined += 1
                continue

            fwd = fwd_by_key[target]
            if fwd["account_id"] != r["account_id"]:
                _insert_quarantine(conn, run_id, now, r, "冲正账户与原单不一致")
                quarantined += 1
                continue

            remain = fwd["amount_cents"] - used[target]
            if r["amount_cents"] > remain:
                _insert_quarantine(conn, run_id, now, r, "冲正超额")
                quarantined += 1
                continue

            used[target] += r["amount_cents"]
            account_net[r["account_id"]] = account_net.get(r["account_id"], 0) - r["amount_cents"]
            conn.execute(
                "UPDATE ledger_entries SET status = '已归并', settle_run_id = ? WHERE id = ?",
                (run_id, r["id"]),
            )
            settled += 1

        for f in forwards:
            conn.execute(
                "UPDATE ledger_entries SET status = '已归并', settle_run_id = ? WHERE id = ?",
                (run_id, f["id"]),
            )
            settled += 1

        # 关键：净额写入配平暂存（累加未合并的暂存），已对外余额本趟不动。
        staging_total = 0
        for acc_id, net in account_net.items():
            conn.execute(
                """
                UPDATE accounts
                SET staging_cents = staging_cents + ?, last_settle_run_id = ?
                WHERE id = ?
                """,
                (net, run_id, acc_id),
            )
            staging_total += net

        finished = _now()
        conn.execute(
            """
            UPDATE settle_runs
            SET status = '完成',
                forward_count = ?,
                reversal_count = ?,
                settled_count = ?,
                quarantine_count = ?,
                net_cents = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (
                len(forwards),
                len(reversals),
                settled,
                quarantined,
                staging_total,
                finished,
                run_id,
            ),
        )

        if simulate_failure:
            # 演练：提交前失败，本趟暂存/流水状态/隔离/运行记录一起回滚。
            raise HTTPException(500, "演练：归并本趟失败，配平暂存已回滚到本趟前")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return get_settle_run(run_id)


def get_settle_run(run_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM settle_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "归并运行不存在")
        return dict(row)


def list_balance_merges() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(
            conn.execute("SELECT * FROM balance_merges ORDER BY id DESC").fetchall()
        )


def get_balance_merge(merge_id: int) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM balance_merges WHERE id = ?", (merge_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "合并批次不存在")
        items = rows_to_dicts(
            conn.execute(
                """
                SELECT i.*, a.code AS account_code
                FROM balance_merge_items i
                JOIN accounts a ON a.id = i.account_id
                WHERE i.merge_id = ?
                ORDER BY i.id
                """,
                (merge_id,),
            ).fetchall()
        )
        out = dict(row)
        out["items"] = items
        return out


def merge_staging(simulate_failure: bool = False) -> dict:
    """显式合并：配平暂存并入已对外余额并清零暂存。

    同一事务、同一条 UPDATE 同时完成「并入」与「清零」：
    不会出现已对外已变而暂存未清，也不会出现暂存被直接当做已对外展示。
    任一步失败整体回滚，暂存与已对外都回到合并前。
    """
    conn = connect()
    merge_id: int | None = None
    try:
        now = _now()
        # 先拿写锁再读快照：并发归并无法在本合并读与写之间插入暂存变动。
        conn.execute("BEGIN IMMEDIATE")
        accounts = rows_to_dicts(
            conn.execute(
                "SELECT id, posted_cents, staging_cents FROM accounts ORDER BY id"
            ).fetchall()
        )
        plan = dual_rail.merge_plan(accounts)
        posted_before_total = sum(r["posted_cents"] for r in accounts)

        cur = conn.execute(
            "INSERT INTO balance_merges(status, started_at) VALUES ('进行中', ?)", (now,)
        )
        merge_id = cur.lastrowid

        staged_total = 0
        for idx, line in enumerate(plan):
            updated = conn.execute(
                """
                UPDATE accounts
                SET posted_cents = posted_cents + staging_cents,
                    staging_cents = 0,
                    last_merge_id = ?
                WHERE id = ? AND staging_cents = ?
                """,
                (merge_id, line.account_id, line.staged_cents),
            )
            if updated.rowcount != 1:
                raise RuntimeError("合并期间暂存余额发生变化，已中止")
            conn.execute(
                """
                INSERT INTO balance_merge_items(
                  merge_id, account_id, staged_cents, posted_before_cents, posted_after_cents
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    merge_id,
                    line.account_id,
                    line.staged_cents,
                    line.posted_before_cents,
                    line.posted_after_cents,
                ),
            )
            staged_total += line.staged_cents
            if simulate_failure and idx == 0:
                raise HTTPException(500, "演练：合并失败，已对外余额与配平暂存均已回滚")

        posted_after_total = posted_before_total + staged_total
        conn.execute(
            """
            UPDATE balance_merges
            SET status = '完成',
                account_count = ?,
                staged_total_cents = ?,
                posted_before_cents = ?,
                posted_after_cents = ?,
                finished_at = ?,
                note = ?
            WHERE id = ?
            """,
            (
                len(plan),
                staged_total,
                posted_before_total,
                posted_after_total,
                _now(),
                "无待合并暂存" if not plan else "",
                merge_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return get_balance_merge(merge_id)


def run_verify(settle_run_id: int | None = None) -> dict:
    with connect() as conn:
        if settle_run_id is None:
            last = conn.execute("SELECT id FROM settle_runs ORDER BY id DESC LIMIT 1").fetchone()
            if not last:
                raise HTTPException(400, "尚无归并运行，无法互证")
            settle_run_id = last["id"]
        run = conn.execute("SELECT * FROM settle_runs WHERE id = ?", (settle_run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "归并运行不存在")

        fwd_sum = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries
            WHERE kind = '正向' AND status = '已归并' AND settle_run_id = ?
            """,
            (settle_run_id,),
        ).fetchone()["s"]
        rev_sum = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries
            WHERE kind = '冲正' AND status = '已归并' AND settle_run_id = ?
            """,
            (settle_run_id,),
        ).fetchone()["s"]
        ledger_net = fwd_sum - rev_sum

        totals = _balance_totals(conn)
        posted = totals["posted_cents"]
        staging = totals["staging_cents"]
        total = totals["total_cents"]

        q_count = conn.execute(
            "SELECT COUNT(*) AS c FROM quarantine_items WHERE settle_run_id = ?",
            (settle_run_id,),
        ).fetchone()["c"]

        # 已归并流水净额（全量）——与账户双列合计同源互证
        all_fwd = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries WHERE kind = '正向' AND status = '已归并'"
        ).fetchone()["s"]
        all_rev = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) AS s FROM ledger_entries WHERE kind = '冲正' AND status = '已归并'"
        ).fetchone()["s"]
        all_ledger_net = all_fwd - all_rev

        # 分户互证：每户 已对外+暂存 应等于该户已归并流水净额
        account_rows = _account_rows(conn)
        ledger_by_account = {
            r["account_id"]: r["s"]
            for r in conn.execute(
                """
                SELECT account_id,
                       COALESCE(SUM(CASE WHEN kind = '正向' THEN amount_cents ELSE -amount_cents END), 0) AS s
                FROM ledger_entries
                WHERE status = '已归并'
                GROUP BY account_id
                """
            ).fetchall()
        }
        mismatched_accounts = dual_rail.reconcile(account_rows, ledger_by_account)

        consistent = 1 if (all_ledger_net == total and not mismatched_accounts) else 0
        detail = {
            "对外口径": dual_rail.BASIS_POSTED,
            "说明": "对外展示/对账以已对外余额为准；暂存仅作配平中间态；合计=已对外+暂存。",
            "本轮正向分": fwd_sum,
            "本轮冲正分": rev_sum,
            "本轮流水净额分": ledger_net,
            "已对外余额分": posted,
            "配平暂存分": staging,
            "双余额合计分": total,
            "全量流水净额分": all_ledger_net,
            "待合并账户数": totals["staged_accounts"],
            "分户不一致数": len(mismatched_accounts),
            "分户不一致": mismatched_accounts,
            "本轮隔离数": q_count,
        }
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO verify_reports(
              settle_run_id, ledger_net_cents, posted_cents, staging_cents,
              total_cents, quarantine_count, consistent, basis, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                settle_run_id,
                all_ledger_net,
                posted,
                staging,
                total,
                q_count,
                consistent,
                dual_rail.BASIS_POSTED,
                json.dumps(detail, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
        rid = cur.lastrowid
        row = conn.execute("SELECT * FROM verify_reports WHERE id = ?", (rid,)).fetchone()
        return _report_out(dict(row))
