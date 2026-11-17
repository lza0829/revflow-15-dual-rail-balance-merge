# 07-revflow（冲流台）

冲流台 — 正向与冲正流水净额归并：正向装载 → 冲正关联入账 → 净额归并落余额 → 未平/超额隔离 → 流水净额与余额互证。

**不是** 多源 Feed 归并，**不是** 作业依赖编排，**不是** CDC 变更流，**不是** 质检签发，**不是** 数仓分层建设，**不是** 交班过户。  
**也不是** 收银/退款 CRUD 后台、电商订单、报表 CSV 看板——主链是**流水管道与净额状态**，不是业务单据编辑台。

## 启动

```bash
# 后端
cd backend
pip install -r requirements.txt
set DATA_DIR=%CD%\data
set FIXTURES_DIR=%CD%\..\fixtures
uvicorn app.main:app --host 127.0.0.1 --port 10700

# 前端
cd frontend
npm install
npm run dev
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://127.0.0.1:5700 |
| 接口 | http://127.0.0.1:10700 |

## 主链

账户登记 → 装载正向流水 → 装载冲正并关联原单 → 跑净额归并（已配平落暂存，未平/超额进隔离）→ 显式合并暂存到已对外 → 互证报告核对「流水净额 vs 已对外 + 暂存」

## 双余额（已对外 / 配平暂存）

归并不直接改唯一账户余额：半趟失败只需回滚本趟，不必整库回滚。

| 概念 | 字段 | 含义 |
| --- | --- | --- |
| 已对外余额 | `accounts.posted_cents` | 对外展示、对账、接口一律以此为准 |
| 配平暂存余额 | `accounts.staging_cents` | 归并匹配出的净额先落这里，本趟结束前绝不并入已对外 |
| 合计 | `posted + staging` | 仅供内部核对，不对外展示 |

落库形态择一：**本仓取「账户双列」**（`posted_cents` / `staging_cents`），不另起暂存表；
`balance_merges` / `balance_merge_items` 只是合并审计日志，不是第二套余额。

**显式合并**（归并台「合并暂存到已对外」）：同一事务、同一条 UPDATE 完成
`posted = posted + staging; staging = 0`，失败整体回滚——不会出现已对外已变而暂存未清，
也不会把暂存直接当已对外展示。归并失败（含 `?simulate_failure=true` 演练）同样整体回滚，
两套余额逐户回到本趟前。

接口：

- `GET /api/accounts`、`GET /api/staging` — 分列两套余额（含合计）
- `POST /api/settle-runs[?simulate_failure=true]` — 跑归并，净额只写暂存
- `POST /api/balance-merges[?simulate_failure=true]` — 显式合并暂存到已对外
- `POST /api/verify-reports` — 互证报告，分别给出已对外 / 暂存 / 合计并声明对外口径

旧库（单列 `balance_cents`）启动时自动迁移为双列：旧余额落 `posted_cents`，暂存置 0。

## 界面约定

页面文案全部中文；入口：总览 / 账户 / 正向流水 / 冲正流水 / 归并台 / 未平隔离 / 净额互证。  
视觉：**左轨编号导航 + 右侧账册横线工作面**（Literata / JetBrains Mono，靛蓝强调）；**不是**交班台那种顶栏品牌+横向流程条。

## 与已有仓差异

| 仓 | 骨架 |
| --- | --- |
| Feedstitch | 多源解析→冲突隔离→水位 |
| Batchgate | 作业依赖→重试→水位 |
| Changeflow | 事件序→缺口→Sink 游标 |
| Checklane | 入检→规则→隔离签发 |
| Tiercraft | 贴源→明细→汇总→层间对账 |
| Shiftboard | 班次归属→交班互斥→批次过户 |
| **本仓** | **正向装载→冲正关联→净额归并→未平隔离→净额互证** |
