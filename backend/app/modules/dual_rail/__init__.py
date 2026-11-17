"""双轨余额：已对外余额（posted）与配平暂存余额（staging）分离，显式合并。

本模块只管「两套余额怎么算、怎么合、怎么核」，不碰流水匹配、不碰 HTTP：

* 归并一趟产出的净额先落到 `staging_cents`，`posted_cents` 本趟不动；
* 只有显式合并才把暂存并入已对外，并在同一条更新里清零暂存；
* 对外展示/对账一律取 `posted_cents`，暂存与合计仅供内部核对。

不是规则影子轨，也不是左右源水位：两套余额都是同一账户的真金白银口径，
区别只在「已对外」与「已配平待合并」。
"""

from __future__ import annotations

from dataclasses import dataclass

BASIS_POSTED = "已对外余额"


@dataclass(frozen=True)
class MergeLine:
    """合并的一行：暂存并入已对外，合并后暂存必为 0。"""

    account_id: int
    staged_cents: int
    posted_before_cents: int
    posted_after_cents: int


def merge_plan(rows: list[dict]) -> list[MergeLine]:
    plan: list[MergeLine] = []
    for r in rows:
        staged = int(r["staging_cents"])
        if staged == 0:
            continue
        before = int(r["posted_cents"])
        plan.append(
            MergeLine(
                account_id=int(r["id"]),
                staged_cents=staged,
                posted_before_cents=before,
                posted_after_cents=before + staged,
            )
        )
    return plan


def totals(rows: list[dict]) -> dict:
    posted = sum(int(r["posted_cents"]) for r in rows)
    staging = sum(int(r["staging_cents"]) for r in rows)
    return {
        "posted_cents": posted,
        "staging_cents": staging,
        "total_cents": posted + staging,
        "staged_accounts": sum(1 for r in rows if int(r["staging_cents"]) != 0),
    }


def reconcile(rows: list[dict], ledger_by_account: dict[int, int]) -> list[dict]:
    """分户互证：每户「已对外 + 暂存」应等于该户已归并流水净额。"""
    mismatched: list[dict] = []
    for r in rows:
        account_id = int(r["id"])
        posted = int(r["posted_cents"])
        staging = int(r["staging_cents"])
        expected = int(ledger_by_account.get(account_id, 0))
        if expected != posted + staging:
            mismatched.append(
                {
                    "account_id": account_id,
                    "account_code": r["code"],
                    "流水净额分": expected,
                    "已对外分": posted,
                    "暂存分": staging,
                    "合计分": posted + staging,
                }
            )
    return mismatched
