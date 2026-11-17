from fastapi import APIRouter, Query

from app.services import settle as svc

router = APIRouter(tags=["归并"])


@router.get("/settle-runs")
def get_runs():
    return svc.list_settle_runs()


@router.get("/settle-runs/{run_id}")
def get_run(run_id: int):
    return svc.get_settle_run(run_id)


@router.post("/settle-runs")
def post_run(simulate_failure: bool = Query(False, description="演练失败：整趟回滚")):
    return svc.run_settle(simulate_failure=simulate_failure)
