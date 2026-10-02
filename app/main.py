"""高校课程学期规划服务 —— FastAPI 入口。"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import db
from .planner import Course, PlanError, build_plan, parse_semester, semester_label
from .schemas import CatalogIn, PlanIn, RevisionIn


@asynccontextmanager
async def lifespan(_):
    db.init_db()  # 建表；首次启动写入种子目录（版本 1）
    yield


app = FastAPI(
    title="高校课程学期规划服务",
    version="1.0.0",
    description="基于课程目录版本的学期排课规划：先修组、同修、开课季节、学分上限约束下的最早完成方案。",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def invalid_input_handler(_, exc):
    errors = [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": {"code": "INVALID_INPUT", "message": "非法输入", "errors": errors}},
    )


def _to_catalog(rows):
    return {
        r["code"]: Course(
            code=r["code"], name=r["name"], credits=r["credits"],
            seasons=frozenset(r["semesters"]),
            prereq_groups=tuple(tuple(g) for g in r["prereq_groups"]),
            coreqs=tuple(r["coreqs"]),
        )
        for r in rows
    }


def _plan_out(row):
    out = {
        "plan_id": row["id"],
        "status": row["status"],
        "catalog_version": row["version_id"],
        "kind": row["kind"],
        "request": json.loads(row["request_json"]),
        "result": json.loads(row["result_json"]),
        "created_at": row["created_at"],
    }
    if row["kind"] == "revision":
        out["source_plan_id"] = row["source_plan_id"]
        out["revision"] = json.loads(row["revision_info"])
        out["diff"] = json.loads(row["diff_json"])
    return out


def _summary_out(row):
    """方案列表/重排草案列表中的概要行。"""
    out = {
        "plan_id": row["id"],
        "status": row["status"],
        "kind": row["kind"],
        "catalog_version": row["version_id"],
        "completion_semester": json.loads(row["result_json"]).get("completion_semester"),
        "created_at": row["created_at"],
    }
    if row["kind"] == "revision":
        info = json.loads(row["revision_info"])
        out["source_plan_id"] = row["source_plan_id"]
        out["finished_semesters"] = info["finished_semesters"]
    return out


# ---------------------------------------------------------------- 滚动重排

def _semester_index_key(label):
    """绝对学期标签的时序键，如 2026-FALL < 2027-SPRING；无标签排最前。"""
    if not label:
        return (-1, -1)
    year, season = parse_semester(label)
    return (year, 0 if season == "SPRING" else 1)


def _revision_diff(source_result, revision_result, finished):
    """重排课表相对原方案剩余学期（已结束学期之后）的课程差异，按绝对学期对齐。"""
    remaining_original = source_result.get("semesters", [])[finished:]
    orig = {s["semester"]: {c["code"] for c in s["courses"]}
            for s in remaining_original}
    new = {s["semester"]: {c["code"] for c in s["courses"]}
           for s in revision_result.get("semesters", [])}
    semesters = []
    removed_total, added_total, kept_total = set(), set(), set()
    for label in sorted(set(orig) | set(new), key=_semester_index_key):
        old_codes, new_codes = orig.get(label, set()), new.get(label, set())
        removed, added, kept = old_codes - new_codes, new_codes - old_codes, old_codes & new_codes
        removed_total |= removed
        added_total |= added
        kept_total |= kept
        semesters.append({
            "semester": label,
            "original_courses": sorted(old_codes),
            "new_courses": sorted(new_codes),
            "kept_courses": sorted(kept),
            "removed_courses": sorted(removed),
            "added_courses": sorted(added),
        })
    return {
        "semesters": semesters,
        "removed_courses": sorted(removed_total),
        "added_courses": sorted(added_total),
        "kept_courses": sorted(kept_total),
    }


# ---------------------------------------------------------------- 基础

@app.get("/")
def root():
    return {"service": "高校课程学期规划服务", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------- 课程目录

@app.post("/catalog/versions", status_code=201)
def create_catalog(payload: CatalogIn):
    """目录修订：全量提交课程集合，生成新版本并成为当前版本。"""
    courses = [
        {
            "code": c.code, "name": c.name, "credits": c.credits,
            "semesters": c.semesters, "prereq_groups": c.prereq_groups, "coreqs": c.coreqs,
        }
        for c in payload.courses
    ]
    with db.session() as conn:
        vid = db.create_catalog_version(conn, payload.note, courses)
        version = db.get_version(conn, vid)
    return {
        "version_id": vid,
        "note": version["note"],
        "course_count": len(courses),
        "created_at": version["created_at"],
    }


@app.get("/catalog/current")
def current_catalog():
    with db.session() as conn:
        version = db.get_current_version(conn)
        courses = db.load_courses(conn, version["id"])
    return {
        "version_id": version["id"],
        "note": version["note"],
        "created_at": version["created_at"],
        "courses": courses,
    }


@app.get("/catalog/versions/{version_id}")
def catalog_version(version_id: int):
    """查看历史目录版本（供回看方案所绑定的目录）。"""
    with db.session() as conn:
        version = db.get_version(conn, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail={
                "code": "NOT_FOUND", "message": f"目录版本 {version_id} 不存在"})
        courses = db.load_courses(conn, version_id)
    return {
        "version_id": version["id"],
        "note": version["note"],
        "created_at": version["created_at"],
        "courses": courses,
    }


# ---------------------------------------------------------------- 规划方案

@app.post("/plans", status_code=201)
def create_plan(payload: PlanIn):
    """生成规划方案并绑定当前目录版本；失败时说明原因，不返回部分课表。"""
    with db.session() as conn:
        version = db.get_current_version(conn)
        catalog = _to_catalog(db.load_courses(conn, version["id"]))
        try:
            result = build_plan(
                catalog,
                completed=set(payload.completed_courses),
                targets=payload.target_courses,
                start_semester=payload.start_semester,
                max_credits=payload.max_credits_per_semester,
                num_semesters=payload.num_semesters,
            )
        except PlanError as exc:
            status = 400 if exc.code == "INVALID_INPUT" else 422
            raise HTTPException(status_code=status,
                                detail={"code": exc.code, "message": exc.message})
        plan_id = uuid.uuid4().hex[:12]
        db.insert_plan(conn, plan_id, version["id"], payload.model_dump(), result)
        row = db.get_plan(conn, plan_id)
    return _plan_out(row)


@app.get("/plans")
def list_plans():
    with db.session() as conn:
        rows = db.list_plans(conn)
    return [_summary_out(r) for r in rows]


@app.get("/plans/{plan_id}")
def get_plan(plan_id: str):
    """回看方案（含其绑定的目录版本号）。"""
    with db.session() as conn:
        row = db.get_plan(conn, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail={
                "code": "NOT_FOUND", "message": f"方案 {plan_id} 不存在"})
    return _plan_out(row)


@app.post("/plans/{plan_id}/confirm")
def confirm_plan(plan_id: str):
    """确认方案：若目录自规划后已修订，则拒绝且不写入。"""
    with db.session() as conn:
        row = db.get_plan(conn, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail={
                "code": "NOT_FOUND", "message": f"方案 {plan_id} 不存在"})
        if row["status"] == "confirmed":
            return _plan_out(row)  # 幂等：已确认的方案直接返回
        current = db.get_current_version(conn)
        if row["version_id"] != current["id"]:
            raise HTTPException(status_code=409, detail={
                "code": "CATALOG_CHANGED",
                "message": f"目录已修订（当前版本 {current['id']}，方案基于版本 "
                           f"{row['version_id']}），确认被拒绝，请重新规划",
            })
        db.set_plan_status(conn, plan_id, "confirmed")
        row = db.get_plan(conn, plan_id)
    return _plan_out(row)


# ---------------------------------------------------------------- 执行中途滚动重排

@app.post("/plans/{plan_id}/revisions", status_code=201)
def create_revision(plan_id: str, payload: RevisionIn):
    """针对已确认方案执行中途的课程完成情况 / 目录修订滚动重排。

    凭原方案编号、已结束学期数与截至该时点的累计已修课程：从原起始学期顺延至下一
    学期，沿用原目标课程、每学期学分上限与剩余学期数，按当前目录求最早完成的完整
    未来课表，已修课程不重复排入。结果作为待确认草案关联保存，原方案与其历史目录
    不变。计算至落库之间目录被修订时，拒绝旧版本结果（409）且不保存。
    """
    with db.session() as conn:
        row = db.get_plan(conn, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail={
                "code": "NOT_FOUND", "message": f"方案 {plan_id} 不存在"})
        if row["kind"] != "plan":
            raise HTTPException(status_code=400, detail={
                "code": "INVALID_INPUT",
                "message": "滚动重排只能针对原始方案发起，草案请按其编号直接回看或确认"})
        if row["status"] != "confirmed":
            raise HTTPException(status_code=422, detail={
                "code": "PLAN_NOT_CONFIRMED",
                "message": f"方案 {plan_id} 尚未确认（当前状态 {row['status']}），"
                           "只有已确认方案才能在执行中途滚动重排"})

        request = json.loads(row["request_json"])
        source_result = json.loads(row["result_json"])
        original_completed = set(request["completed_courses"])
        cumulative_completed = set(payload.completed_courses)

        finished = payload.finished_semesters
        num_semesters = request["num_semesters"]
        if finished >= num_semesters:
            raise HTTPException(status_code=400, detail={
                "code": "INVALID_INPUT",
                "message": f"已结束学期数 {finished} 须大于 0 且小于原规划学期数 "
                           f"{num_semesters}"})
        revoked = sorted(original_completed - cumulative_completed)
        if revoked:
            raise HTTPException(status_code=400, detail={
                "code": "INVALID_INPUT",
                "message": "累计已修课程撤销了原方案创建时声明的已修课程："
                           + "、".join(revoked)})
        original_k = len(source_result.get("semesters", []))
        if finished >= original_k:
            end_sem = source_result.get("completion_semester")
            tail = f"（已于 {end_sem} 完成全部目标）" if end_sem else "，目标课程均已完成"
            raise HTTPException(status_code=422, detail={
                "code": "REQUIREMENT_CONFLICT",
                "message": f"原方案仅安排 {original_k} 个学期{tail}，"
                           f"已结束 {finished} 个学期后无剩余学期可重排"})

        start_year, start_season = parse_semester(request["start_semester"])
        new_start = semester_label(start_year, start_season, finished + 1)
        remaining = num_semesters - finished

        # 基于当前目录（非原方案绑定目录）计算完整未来课表
        current = db.get_current_version(conn)
        catalog = _to_catalog(db.load_courses(conn, current["id"]))
        try:
            result = build_plan(
                catalog,
                completed=cumulative_completed,
                targets=request["target_courses"],
                start_semester=new_start,
                max_credits=request["max_credits_per_semester"],
                num_semesters=remaining,
            )
        except PlanError as exc:
            status = 400 if exc.code == "INVALID_INPUT" else 422
            raise HTTPException(status_code=status,
                                detail={"code": exc.code, "message": exc.message})

        # 计算至落库之间目录若被修订，拒绝旧版本结果且不保存
        current_now = db.get_current_version(conn)
        if current_now["id"] != current["id"]:
            raise HTTPException(status_code=409, detail={
                "code": "CATALOG_CHANGED",
                "message": f"目录在计算期间被修订（{current['id']} -> {current_now['id']}），"
                           "旧版本计算结果已拒绝，请按最新目录重新发起重排"})

        diff = _revision_diff(source_result, result, finished)
        effective_request = {
            "completed_courses": sorted(cumulative_completed),
            "target_courses": request["target_courses"],
            "start_semester": new_start,
            "max_credits_per_semester": request["max_credits_per_semester"],
            "num_semesters": remaining,
        }
        revision_info = {
            "source_plan_id": plan_id,
            "source_catalog_version": row["version_id"],
            "finished_semesters": finished,
            "previous_start_semester": request["start_semester"],
            "remaining_semesters": remaining,
        }
        revision_id = uuid.uuid4().hex[:12]
        db.insert_revision(conn, revision_id, current["id"], effective_request,
                           result, plan_id, revision_info, diff)
        saved = db.get_plan(conn, revision_id)
    return _plan_out(saved)


@app.get("/plans/{plan_id}/revisions")
def list_plan_revisions(plan_id: str):
    """查看某已确认方案关联保存的全部滚动重排草案（按创建先后）。"""
    with db.session() as conn:
        row = db.get_plan(conn, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail={
                "code": "NOT_FOUND", "message": f"方案 {plan_id} 不存在"})
        rows = db.list_revisions(conn, plan_id)
    return {"plan_id": plan_id, "revisions": [_summary_out(r) for r in rows]}
