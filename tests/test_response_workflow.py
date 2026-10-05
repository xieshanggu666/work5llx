"""联合防汛处置协同（发起→审核→执行→完成）四态闭环测试。

覆盖：发起幂等归并 / 并发发起归并 / 四态闭环推进与方案回写（水库工况、
预警销号、转移台账）/ 非法迁移拦截 / 迁移重试幂等 / 历史遗留记录（run_id=NULL）
不受回写影响 / 历史运行（缺水位过程线）水量平衡兼容发起。
"""
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import (EvacuationRecord, FloodZone, ForecastRun, ForecastSeries,
                        RainfallEvent, Reservoir, ResponseTask, RiverNode,
                        RiverReach, SubBasin, WaterStation, WarningRecord)
from app.services.forecast import run_forecast
from app.services.response import (ResponseStateError, complete_response,
                                   execute_response, initiate_response,
                                   review_response)


def _seed(db):
    """最小自洽流域：子流域→水库→出口站（必触发红色预警与强制转移）。"""
    res = Reservoir(id=1, name="青峰水库", node_id=2, normal_level=74.0,
                    flood_level=80.0, crest_level=100.0,
                    storage_curve=[[60, 3000], [70, 6200], [80, 11000],
                                   [90, 17200], [100, 24000]],
                    discharge_curve=[[80, 0], [90, 1600], [100, 5000]],
                    gate_max=2500.0, current_level=78.0, active=1)
    res.current_storage = res.storage_at(res.current_level)
    db.add_all([
        RiverNode(id=1, name="源头", kind="headwater"),
        RiverNode(id=2, name="水库", kind="reservoir"),
        RiverNode(id=3, name="出口", kind="outlet"),
        RiverReach(id=1, name="入库段", from_node_id=1, to_node_id=2,
                   k_hr=1.0, x_coef=0.2),
        RiverReach(id=2, name="出库段", from_node_id=2, to_node_id=3,
                   k_hr=1.0, x_coef=0.2),
        SubBasin(id=1, name="子流域", area_km2=120.0, cn=88.0, lag_hr=1.0,
                 outlet_node_id=1),
        res,
        WaterStation(id=1, name="出口水位站", node_id=3,
                     thresholds={"base_level": 10.0, "blue": 11.0, "yellow": 12.0,
                                 "orange": 13.0, "red": 14.0,
                                 "rating": [[0, 10.0], [10, 11.0], [50, 13.0], [100, 15.0]]}),
        FloodZone(id=1, name="沿岸村", node_id=3, population=500,
                  low_level=11.0, high_level=13.0),
        RainfallEvent(id=1, name="测试暴雨", duration_h=6, total_mm=300.0,
                      hyetograph=[50.0] * 6),
    ])
    db.commit()


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db",
                           connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    _seed(db)
    db.close()
    yield factory
    engine.dispose()


def _run_done(db, mode="optimized"):
    r = run_forecast(db, db.get(RainfallEvent, 1), mode)
    assert r["run_status"] == "done"
    return r["run_id"]


def test_initiate_merges_repeat_for_same_run(session_factory):
    db = session_factory()
    run_id = _run_done(db)
    t1 = initiate_response(db, run_id, "调度员甲")
    assert t1.status == "initiated"
    assert t1.plan["mode"] == "optimized"
    assert len(t1.plan["reservoirs"]) == 1
    assert t1.plan["reservoirs"][0]["level_source"] == "series"
    assert t1.plan["warning_count"] == 1
    assert t1.plan["evacuation_count"] == 1
    assert t1.plan["evacuation_people"] == 500

    # 重复发起（即使换调度员）归并到同一张协同单，不覆盖既有内容
    t2 = initiate_response(db, run_id, "调度员乙")
    assert t2.id == t1.id
    assert db.query(ResponseTask).count() == 1
    assert t2.dispatcher == "调度员甲"
    db.close()


def test_concurrent_initiate_merges_into_single_task(session_factory):
    db = session_factory()
    run_id = _run_done(db)
    db.close()
    errors, task_ids = [], []

    def worker():
        s = session_factory()
        try:
            task_ids.append(initiate_response(s, run_id, "调度员").id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(set(task_ids)) == 1
    db = session_factory()
    assert db.query(ResponseTask).count() == 1
    db.close()


def test_initiate_rejects_run_not_done_or_missing(session_factory):
    db = session_factory()
    db.add(ForecastRun(id=99, event_id=1, mode="natural", status="failed"))
    db.commit()
    with pytest.raises(ResponseStateError):
        initiate_response(db, 99, "调度员")
    with pytest.raises(ResponseStateError):
        initiate_response(db, 12345, "调度员")
    db.close()


def test_four_state_closed_loop_writes_back(session_factory):
    db = session_factory()
    run_id = _run_done(db)
    res_before = db.get(Reservoir, 1)
    old_level, old_storage = res_before.current_level, res_before.current_storage

    task = initiate_response(db, run_id, "调度员甲")
    plan_end_level = task.plan["reservoirs"][0]["end_level"]
    plan_end_storage = task.plan["reservoirs"][0]["end_storage"]
    assert (plan_end_level, plan_end_storage) != (old_level, old_storage)

    # 审核
    task = review_response(db, task.id, "值守乙", approve=True, note="同意方案")
    assert task.status == "approved"
    assert task.duty_officer == "值守乙" and task.review_note == "同意方案"
    assert task.reviewed_at is not None

    # 执行：回写水库工况 + 转移台账启动；预警保持 active 待闭环销号
    task = execute_response(db, task.id, "负责人丙")
    assert task.status == "executed"
    assert task.evac_lead == "负责人丙" and task.executed_at is not None
    db.expire_all()
    res = db.get(Reservoir, 1)
    assert res.current_level == pytest.approx(plan_end_level)
    assert res.current_storage == pytest.approx(plan_end_storage)
    evac = db.query(EvacuationRecord).filter(EvacuationRecord.run_id == run_id).one()
    assert evac.status == "moving"
    warn = db.query(WarningRecord).filter(WarningRecord.run_id == run_id).one()
    assert warn.status == "active"
    wb = task.writeback
    assert wb["reservoirs"][0]["old_level"] == pytest.approx(old_level)
    assert wb["reservoirs"][0]["new_level"] == pytest.approx(plan_end_level)
    assert wb["evacuations_moving"] == 1

    # 完成：转移安置确认 + 预警销号，闭环结束
    task = complete_response(db, task.id)
    assert task.status == "completed" and task.completed_at is not None
    db.expire_all()
    assert db.query(EvacuationRecord).filter(EvacuationRecord.run_id == run_id).one().status == "safe"
    assert db.query(WarningRecord).filter(WarningRecord.run_id == run_id).one().status == "cleared"
    assert task.writeback["evacuations_safe"] == 1
    assert task.writeback["warnings_cleared"] == 1
    db.close()


def test_invalid_transitions_rejected(session_factory):
    db = session_factory()
    run_id = _run_done(db)
    task = initiate_response(db, run_id, "调度员")

    with pytest.raises(ResponseStateError):
        execute_response(db, task.id, "负责人")       # 未审核不能执行
    with pytest.raises(ResponseStateError):
        complete_response(db, task.id)                # 未执行不能完成

    review_response(db, task.id, "值守")
    with pytest.raises(ResponseStateError):
        complete_response(db, task.id)                # 未执行不能完成
    with pytest.raises(ResponseStateError):
        review_response(db, task.id, "值守", approve=False)  # 已审核不能退回

    execute_response(db, task.id, "负责人")
    with pytest.raises(ResponseStateError):
        review_response(db, task.id, "值守")          # 已执行不能回退审核

    complete_response(db, task.id)
    with pytest.raises(ResponseStateError):
        execute_response(db, task.id, "负责人")       # 已完成不能回退执行
    with pytest.raises(ResponseStateError):
        review_response(db, 999, "值守")              # 协同单不存在
    db.close()


def test_transition_retry_is_idempotent_noop(session_factory):
    db = session_factory()
    run_id = _run_done(db)
    task = initiate_response(db, run_id, "调度员")
    review_response(db, task.id, "值守")

    t1 = execute_response(db, task.id, "负责人")
    wb1 = dict(t1.writeback)
    t2 = execute_response(db, task.id, "负责人")      # 重复执行：幂等空操作
    assert t2.status == "executed" and t2.writeback == wb1
    assert db.query(EvacuationRecord).filter_by(run_id=run_id, status="moving").count() == 1

    c1 = complete_response(db, task.id)
    c2 = complete_response(db, task.id)               # 重复完成：幂等空操作
    assert c2.status == "completed" and c2.writeback == c1.writeback
    db.close()


def test_review_reject_returns_to_initiated(session_factory):
    db = session_factory()
    run_id = _run_done(db)
    task = initiate_response(db, run_id, "调度员")
    task = review_response(db, task.id, "值守", approve=False, note="方案需修订")
    assert task.status == "initiated"
    assert task.review_note == "方案需修订"
    assert task.reviewed_at is None
    # 修订后可再次提交审核
    task = review_response(db, task.id, "值守", approve=True)
    assert task.status == "approved"
    db.close()


def test_legacy_records_untouched_by_writeback(session_factory):
    db = session_factory()
    # 历史遗留台账：无 run_id，含人工处置状态
    db.add(WarningRecord(run_id=None, target_type="station", target_id=1,
                         target_name="出口水位站", kind="water_level", level="red",
                         value=15.0, threshold=14.0, message="历史遗留预警",
                         status="active"))
    db.add(EvacuationRecord(run_id=None, zone_id=1, zone_name="沿岸村",
                            triggered_by="强制转移", people=500, status="pending"))
    db.commit()

    run_id = _run_done(db)
    task = initiate_response(db, run_id, "调度员")
    review_response(db, task.id, "值守")
    execute_response(db, task.id, "负责人")
    complete_response(db, task.id)
    db.expire_all()

    # 历史遗留记录原样保留，本运行记录完成闭环
    legacy_warn = db.query(WarningRecord).filter(WarningRecord.run_id.is_(None)).one()
    legacy_evac = db.query(EvacuationRecord).filter(EvacuationRecord.run_id.is_(None)).one()
    assert legacy_warn.status == "active"
    assert legacy_evac.status == "pending"
    assert db.query(WarningRecord).filter(WarningRecord.run_id == run_id).one().status == "cleared"
    assert db.query(EvacuationRecord).filter(EvacuationRecord.run_id == run_id).one().status == "safe"
    db.close()


def test_historical_run_without_level_series_uses_balance_fallback(session_factory):
    """历史运行（旧版本无 reslevel 过程线）仍可发起协同：末态按水量平衡推算。"""
    db = session_factory()
    run_id = _run_done(db)
    # 模拟历史库：删掉本次运行的水库水位过程线
    (db.query(ForecastSeries)
     .filter(ForecastSeries.run_id == run_id, ForecastSeries.kind == "reslevel")
     .delete(synchronize_session=False))
    db.commit()

    res = db.get(Reservoir, 1)
    base_storage = res.current_storage
    series = db.query(ForecastSeries).filter(ForecastSeries.run_id == run_id).all()
    by_kind = {s.kind: s.values for s in series}
    infl, outf = by_kind["inflow"], by_kind["resout"]
    expect_storage = max(0.0, base_storage
                         + sum(i - o for i, o in zip(infl, outf)) * 3600.0 / 1e4)

    task = initiate_response(db, run_id, "调度员")
    snap = task.plan["reservoirs"][0]
    assert snap["level_source"] == "balance"
    assert snap["end_storage"] == pytest.approx(expect_storage, abs=0.2)
    assert snap["end_level"] == pytest.approx(res.level_at(expect_storage), abs=0.01)

    # 回写同样生效
    review_response(db, task.id, "值守")
    execute_response(db, task.id, "负责人")
    db.expire_all()
    assert db.get(Reservoir, 1).current_storage == pytest.approx(expect_storage, abs=0.2)
    db.close()


def test_writeback_uses_approved_snapshot_not_live_state(session_factory):
    """执行回写以审核时的方案快照为准，而非执行当下的水库状态。"""
    db = session_factory()
    run_id = _run_done(db)
    task = initiate_response(db, run_id, "调度员")
    plan_level = task.plan["reservoirs"][0]["end_level"]
    review_response(db, task.id, "值守")

    # 审核后水库工况被外部改动：执行仍按快照回写
    res = db.get(Reservoir, 1)
    res.current_level = 65.0
    res.current_storage = res.storage_at(65.0)
    db.commit()

    execute_response(db, task.id, "负责人")
    db.expire_all()
    assert db.get(Reservoir, 1).current_level == pytest.approx(plan_level)
    db.close()
