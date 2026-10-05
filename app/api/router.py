from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import (EvacuationRecord, FloodZone, ForecastRun, ForecastSeries,
                        RainStation, RainfallEvent, Reservoir, ResponseTask,
                        RiverNode, RiverReach, SubBasin, WaterStation, WarningRecord)
from app.schemas import (ResponseComplete, ResponseExecute, ResponseInitiate,
                         ResponseReview)
from app.services.forecast import run_forecast
from app.services.response import (ResponseStateError, complete_response,
                                   execute_response, initiate_response,
                                   review_response)

router = APIRouter(prefix="/api")


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    res = db.query(Reservoir).all()
    stations = db.query(WaterStation).all()
    events = db.query(RainfallEvent).all()
    subs = db.query(SubBasin).all()
    zones = db.query(FloodZone).all()
    return {
        "basin_name": "青岚江流域",
        "sub_basins": len(subs),
        "reaches": db.query(RiverReach).count(),
        "rain_stations": db.query(RainStation).count(),
        "water_stations": len(stations),
        "reservoirs": len(res),
        "events": len(events),
        "zones": len(zones),
        "reservoir_ready": sum(1 for r in res if r.current_level),
        "total_capacity": round(sum(r.storage_at(r.crest_level) for r in res), 1),
        "population_at_risk": sum(z.population for z in zones),
    }


@router.get("/map")
def basin_map(db: Session = Depends(get_db)):
    nodes = [{"id": n.id, "name": n.name, "kind": n.kind, "x": n.x, "y": n.y}
             for n in db.query(RiverNode).all()]
    reaches = [{"id": r.id, "name": r.name, "from": r.from_node_id, "to": r.to_node_id,
                "length": r.length_km}
               for r in db.query(RiverReach).all()]
    subs = [{"id": s.id, "name": s.name, "area_km2": s.area_km2, "cn": s.cn,
             "lag_hr": s.lag_hr, "outlet_node_id": s.outlet_node_id, "x": s.x, "y": s.y}
            for s in db.query(SubBasin).all()]
    reservoirs = [{"id": r.id, "name": r.name, "node_id": r.node_id,
                   "normal_level": r.normal_level, "flood_level": r.flood_level,
                   "crest_level": r.crest_level, "current_level": r.current_level,
                   "gate_max": r.gate_max, "x": r.x, "y": r.y}
                  for r in db.query(Reservoir).all()]
    stations = [{"id": s.id, "name": s.name, "node_id": s.node_id, "x": s.x, "y": s.y,
                 "thresholds": s.thresholds}
                for s in db.query(WaterStation).all()]
    rain_stations = [{"id": s.id, "name": s.name, "node_id": s.node_id, "x": s.x, "y": s.y}
                     for s in db.query(RainStation).all()]
    zones = [{"id": z.id, "name": z.name, "node_id": z.node_id, "risk_level": z.risk_level,
              "population": z.population, "low_level": z.low_level, "high_level": z.high_level,
              "route": z.route, "x": z.x, "y": z.y} for z in db.query(FloodZone).all()]
    return {"nodes": nodes, "reaches": reaches, "sub_basins": subs,
            "reservoirs": reservoirs, "stations": stations,
            "rain_stations": rain_stations, "flood_zones": zones}


@router.get("/rain-events")
def rainfall_events(db: Session = Depends(get_db)):
    return [{"id": e.id, "name": e.name, "return_period": e.return_period,
             "duration_h": e.duration_h, "total_mm": e.total_mm, "note": e.note}
            for e in db.query(RainfallEvent).all()]


@router.get("/rain-events/{eid}")
def rain_event_detail(eid: int, db: Session = Depends(get_db)):
    e = db.query(RainfallEvent).get(eid)
    if not e:
        return {"detail": "not found"}
    return {"id": e.id, "name": e.name, "return_period": e.return_period,
            "duration_h": e.duration_h, "total_mm": e.total_mm,
            "hyetograph": e.hyetograph, "note": e.note}


@router.get("/reservoirs")
def reservoirs(db: Session = Depends(get_db)):
    return [{"id": r.id, "name": r.name, "node_id": r.node_id,
             "normal_level": r.normal_level, "flood_level": r.flood_level,
             "crest_level": r.crest_level, "current_level": r.current_level,
             "current_storage": r.current_storage, "gate_max": r.gate_max}
            for r in db.query(Reservoir).all()]


@router.get("/warnings")
def warnings(db: Session = Depends(get_db)):
    return [{"id": w.id, "run_id": w.run_id, "target_type": w.target_type,
             "target_id": w.target_id,
             "target_name": w.target_name, "level": w.level, "value": w.value,
             "threshold": w.threshold, "message": w.message,
             "created_at": w.created_at.isoformat() if w.created_at else None,
             "status": w.status}
            for w in db.query(WarningRecord).order_by(WarningRecord.id.desc()).all()]


@router.get("/evacuations")
def evacuations(db: Session = Depends(get_db)):
    return [{"id": e.id, "run_id": e.run_id, "zone_id": e.zone_id, "zone_name": e.zone_name,
             "triggered_by": e.triggered_by, "people": e.people, "status": e.status,
             "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in db.query(EvacuationRecord).order_by(EvacuationRecord.id.desc()).all()]


@router.post("/forecast/{eid}/{mode}")
def forecast(eid: int, mode: str, db: Session = Depends(get_db)):
    event = db.query(RainfallEvent).get(eid)
    if not event:
        return {"detail": "event not found"}
    return run_forecast(db, event, reservoir_rule=mode)


@router.get("/forecast/runs")
def forecast_runs(db: Session = Depends(get_db)):
    return [{"id": r.id, "event_id": r.event_id, "mode": r.mode, "status": r.status,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in db.query(ForecastRun).order_by(ForecastRun.id.desc()).limit(20).all()]


@router.get("/forecast/series/{run_id}")
def forecast_series(run_id: int, db: Session = Depends(get_db)):
    rows = db.query(ForecastSeries).filter(ForecastSeries.run_id == run_id).all()
    return [{"id": r.id, "node_id": r.node_id, "kind": r.kind, "name": r.name,
             "values": r.values} for r in rows]


# ---------- 联合防汛处置协同（发起→审核→执行→完成 四态闭环） ----------

def _iso(dt):
    return dt.isoformat() if dt else None


def _task_dict(db: Session, t: ResponseTask) -> dict:
    run = db.get(ForecastRun, t.run_id)
    event = db.get(RainfallEvent, run.event_id) if run else None
    return {
        "id": t.id, "run_id": t.run_id, "title": t.title, "status": t.status,
        "plan": t.plan or {}, "writeback": t.writeback or {},
        "dispatcher": t.dispatcher, "duty_officer": t.duty_officer,
        "evac_lead": t.evac_lead, "review_note": t.review_note,
        "created_at": _iso(t.created_at), "reviewed_at": _iso(t.reviewed_at),
        "executed_at": _iso(t.executed_at), "completed_at": _iso(t.completed_at),
        "run": {"id": run.id, "mode": run.mode, "status": run.status,
                "event_id": run.event_id,
                "event_name": event.name if event else ""} if run else None,
    }


@router.get("/response-tasks")
def response_tasks(db: Session = Depends(get_db)):
    return [_task_dict(db, t)
            for t in db.query(ResponseTask).order_by(ResponseTask.id.desc()).all()]


@router.post("/response-tasks")
def response_initiate(body: ResponseInitiate, db: Session = Depends(get_db)):
    try:
        task = initiate_response(db, body.run_id, body.dispatcher, body.title)
    except ResponseStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_dict(db, task)


@router.post("/response-tasks/{tid}/review")
def response_review(tid: int, body: ResponseReview, db: Session = Depends(get_db)):
    try:
        task = review_response(db, tid, body.duty_officer, body.approve, body.note)
    except ResponseStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_dict(db, task)


@router.post("/response-tasks/{tid}/execute")
def response_execute(tid: int, body: ResponseExecute, db: Session = Depends(get_db)):
    try:
        task = execute_response(db, tid, body.evac_lead)
    except ResponseStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_dict(db, task)


@router.post("/response-tasks/{tid}/complete")
def response_complete(tid: int, body: ResponseComplete, db: Session = Depends(get_db)):
    try:
        task = complete_response(db, tid, body.actor)
    except ResponseStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _task_dict(db, task)