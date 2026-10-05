"""联合防汛处置协同：围绕一次预报运行的 发起→审核→执行→完成 四态闭环。

角色分工：调度员发起（挂接预报运行并生成调度方案快照）→ 预警值守审核 →
转移负责人执行（审核后的方案回写水库工况、启动转移台账）→ 完成闭环
（转移安置确认、本运行预警统一销号）。

幂等与兼容约定：
- 同一预报运行只存在一张协同单（run_id 唯一约束），重复发起幂等复用，
  并发争抢由唯一约束兜底；
- 状态机严格按 initiated→approved→executed→completed 推进，重复提交同一
  迁移是幂等空操作，跨状态跳转报错；
- 回写只触碰本运行（run_id）挂接的预警/转移台账，run_id 为 NULL 的历史
  遗留记录原样保留；
- 历史运行（缺少水库水位过程线）发起时按水量平衡由当前库容推算末态，
  无需迁移即可纳入协同闭环。
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (EvacuationRecord, ForecastRun, ForecastSeries,
                        Reservoir, ResponseTask, WarningRecord)

# 状态迁移表：动作 → (前置状态, 目标状态)
_TRANSITIONS = {
    "review": ("initiated", "approved"),
    "execute": ("approved", "executed"),
    "complete": ("executed", "completed"),
}

_LEVEL_ORDER = {"": 0, "blue": 1, "yellow": 2, "orange": 3, "red": 4}


class ResponseStateError(ValueError):
    """协同单状态机非法迁移。"""


def _get_or_create_task(db: Session, run_id: int, dispatcher: str, title: str,
                        plan: dict) -> ResponseTask:
    """按业务幂等键 run_id 获取或创建协同单；并发冲突由唯一约束兜底。"""
    task = db.query(ResponseTask).filter(ResponseTask.run_id == run_id).first()
    if task:
        return task
    task = ResponseTask(run_id=run_id, title=title, dispatcher=dispatcher,
                        plan=plan, status="initiated")
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        task = db.query(ResponseTask).filter(ResponseTask.run_id == run_id).one()
    return task


def _build_plan_snapshot(db: Session, run: ForecastRun) -> dict:
    """从预报运行的过程线生成调度方案快照（审核与回写都以该快照为准）。

    水库末态优先取运行持久化的水位过程线（reslevel）；历史运行没有该序列时
    退化为水量平衡推算（当前库容 + Σ(入库−出库)·dt），保证老记录可兼容发起。
    """
    series = db.query(ForecastSeries).filter(ForecastSeries.run_id == run.id).all()
    by_key: Dict[tuple, List[float]] = {(s.node_id, s.kind): s.values for s in series}

    reservoirs = []
    for res in db.query(Reservoir).filter(Reservoir.active == 1).all():
        inflow = by_key.get((res.node_id, "inflow"))
        outflow = by_key.get((res.node_id, "resout"))
        levels = by_key.get((res.node_id, "reslevel"))
        if inflow is None and outflow is None:
            continue  # 该水库未参与本次运行（如无水库工况的历史运行）
        if levels:
            end_level = round(levels[-1], 2)
            end_storage = round(res.storage_at(levels[-1]), 1)
            source = "series"
        else:
            infl = inflow or []
            outf = outflow or []
            n = max(len(infl), len(outf))
            delta = sum((infl[i] if i < len(infl) else 0.0)
                        - (outf[i] if i < len(outf) else 0.0)
                        for i in range(n)) * 3600.0 / 1e4  # m³/s·h → 万m³
            base = res.current_storage
            if base is None:
                base = res.storage_at(res.current_level or res.normal_level)
            end_storage = round(max(0.0, base + delta), 1)
            end_level = round(res.level_at(end_storage), 2)
            source = "balance"
        reservoirs.append({
            "reservoir_id": res.id, "name": res.name,
            "peak_outflow": round(max(outflow), 2) if outflow else 0.0,
            "end_level": end_level, "end_storage": end_storage,
            "level_source": source,
        })

    warnings = db.query(WarningRecord).filter(WarningRecord.run_id == run.id).all()
    evacuations = (db.query(EvacuationRecord)
                   .filter(EvacuationRecord.run_id == run.id).all())
    max_level = ""
    for w in warnings:
        if _LEVEL_ORDER.get(w.level, 0) > _LEVEL_ORDER.get(max_level, 0):
            max_level = w.level
    return {
        "mode": run.mode,
        "reservoirs": reservoirs,
        "warning_count": len(warnings),
        "max_warning_level": max_level,
        "evacuation_count": len(evacuations),
        "evacuation_people": sum(e.people or 0 for e in evacuations),
    }


def initiate_response(db: Session, run_id: int, dispatcher: str,
                      title: str = "") -> ResponseTask:
    """发起处置协同（幂等）：同一预报运行重复发起归并到同一张协同单。"""
    run = db.get(ForecastRun, run_id)
    if run is None:
        raise ResponseStateError(f"预报运行 {run_id} 不存在")
    if run.status != "done":
        raise ResponseStateError(f"预报运行 {run_id} 状态为 {run.status}，推演完成后才能发起处置协同")
    plan = _build_plan_snapshot(db, run)
    if not title:
        title = f"预报运行#{run_id}（{run.mode}）联合防汛处置"
    return _get_or_create_task(db, run_id, dispatcher or "", title, plan)


def _transition(task: ResponseTask, action: str) -> bool:
    """推进状态机。已在目标状态时幂等空操作返回 False，其余错配抛错。"""
    required, target = _TRANSITIONS[action]
    if task.status == target:
        return False
    if task.status != required:
        raise ResponseStateError(
            f"协同单当前状态为 {task.status}，不能执行 {action}（要求前置状态 {required}）")
    task.status = target
    return True


def review_response(db: Session, task_id: int, duty_officer: str,
                    approve: bool = True, note: str = "") -> ResponseTask:
    """审核：initiated→approved；不通过则退回 initiated 待重新发起审核。"""
    task = _get_task(db, task_id)
    if task.status == "approved" and approve:
        return task
    if task.status != "initiated":
        raise ResponseStateError(f"协同单当前状态为 {task.status}，不能审核")
    task.duty_officer = duty_officer or ""
    task.review_note = note or ""
    if approve:
        task.status = "approved"
        task.reviewed_at = datetime.now()
    else:
        task.status = "initiated"  # 退回：调度员修订后可再次提交审核
        task.reviewed_at = None
    db.commit()
    return task


def execute_response(db: Session, task_id: int, evac_lead: str) -> ResponseTask:
    """执行：approved→executed，并把审核后的调度方案回写水库工况与转移台账。

    - 水库工况：按方案快照回写 current_level / current_storage；
    - 转移台账：本运行 pending 的转移记录启动为 moving；
    - 回写结果摘要写入 writeback，重复执行是幂等空操作。
    """
    task = _get_task(db, task_id)
    if not _transition(task, "execute"):
        return task
    task.evac_lead = evac_lead or ""
    task.executed_at = datetime.now()

    applied = []
    for item in (task.plan or {}).get("reservoirs", []):
        res = db.get(Reservoir, item.get("reservoir_id") or 0)
        if res is None:
            continue
        old_level, old_storage = res.current_level, res.current_storage
        res.current_level = item["end_level"]
        res.current_storage = item["end_storage"]
        applied.append({"reservoir_id": res.id, "name": res.name,
                        "old_level": old_level, "new_level": item["end_level"],
                        "old_storage": old_storage, "new_storage": item["end_storage"]})

    # 只动本运行挂接的转移台账；run_id 为 NULL 的历史遗留记录原样保留
    moving = (db.query(EvacuationRecord)
              .filter(EvacuationRecord.run_id == task.run_id,
                      EvacuationRecord.status == "pending")
              .update({"status": "moving"}, synchronize_session=False))

    task.writeback = {**(task.writeback or {}),
                      "reservoirs": applied, "evacuations_moving": moving}
    db.commit()
    return task


def complete_response(db: Session, task_id: int, actor: str = "") -> ResponseTask:
    """完成：executed→completed，转移安置确认（moving→safe）并销号本运行预警。"""
    task = _get_task(db, task_id)
    if not _transition(task, "complete"):
        return task
    task.completed_at = datetime.now()

    safe = (db.query(EvacuationRecord)
            .filter(EvacuationRecord.run_id == task.run_id,
                    EvacuationRecord.status == "moving")
            .update({"status": "safe"}, synchronize_session=False))
    cleared = (db.query(WarningRecord)
               .filter(WarningRecord.run_id == task.run_id,
                       WarningRecord.status == "active")
               .update({"status": "cleared"}, synchronize_session=False))

    task.writeback = {**(task.writeback or {}),
                      "evacuations_safe": safe, "warnings_cleared": cleared}
    db.commit()
    return task


def _get_task(db: Session, task_id: int) -> ResponseTask:
    task = db.get(ResponseTask, task_id)
    if task is None:
        raise ResponseStateError(f"处置协同单 {task_id} 不存在")
    return task
