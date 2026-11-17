from fastapi import APIRouter, Query

from app.services import settle as svc

router = APIRouter(tags=["配平暂存"])


@router.get("/staging")
def get_staging():
    return svc.list_staging()


@router.get("/balance-merges")
def get_merges():
    return svc.list_balance_merges()


@router.get("/balance-merges/{merge_id}")
def get_merge(merge_id: int):
    return svc.get_balance_merge(merge_id)


@router.post("/balance-merges")
def post_merge(simulate_failure: bool = Query(False, description="演练失败：合并整体回滚")):
    return svc.merge_staging(simulate_failure=simulate_failure)
