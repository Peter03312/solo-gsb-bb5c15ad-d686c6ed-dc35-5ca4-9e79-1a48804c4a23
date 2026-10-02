"""执行中途滚动重排（POST /plans/{id}/revisions）集成测试。"""
import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path):
    os.environ["DB_PATH"] = str(tmp_path / "test.db")
    from app.main import app
    with TestClient(app) as c:
        yield c


def _plan(client, **kw):
    payload = {
        "completed_courses": [],
        "target_courses": ["CS401"],
        "start_semester": "2026-SPRING",
        "max_credits_per_semester": 12,
        "num_semesters": 6,
    }
    payload.update(kw)
    return client.post("/plans", json=payload)


def _confirmed(client, **kw):
    r = _plan(client, **kw)
    assert r.status_code == 201
    pid = r.json()["plan_id"]
    assert client.post(f"/plans/{pid}/confirm").status_code == 200
    return pid


def _sem_codes(body):
    return [[c["code"] for c in s["courses"]] for s in body["result"]["semesters"]]


def test_revision_basic_keeps_original_on_track(client):
    """按原进度修完前两学期：草案顺延到 2027-SPRING，剩余课表与原方案一致。"""
    pid = _confirmed(client)
    r = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 2,
        "completed_courses": ["CS101", "CS101L", "CS201", "MATH103"],
    })
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "revision"
    assert body["status"] == "proposed"
    assert body["catalog_version"] == 1
    assert body["source_plan_id"] == pid
    assert _sem_codes(body) == [["CS301"], ["CS401"]]
    assert body["result"]["completion_semester"] == "2027-FALL"
    # 重排请求：起始顺延、剩余学期数 = 原期限 - 已结束
    assert body["request"]["start_semester"] == "2027-SPRING"
    assert body["request"]["num_semesters"] == 4
    assert body["request"]["completed_courses"] == ["CS101", "CS101L", "CS201", "MATH103"]
    info = body["revision"]
    assert info["finished_semesters"] == 2
    assert info["remaining_semesters"] == 4
    assert info["previous_start_semester"] == "2026-SPRING"
    assert info["source_catalog_version"] == 1
    # 已修课程未重复排入
    done = set(body["request"]["completed_courses"])
    assert not any(c["code"] in done
                   for s in body["result"]["semesters"] for c in s["courses"])


def test_revision_diff_against_original_remaining_semesters(client):
    """实际进度偏离原方案：CS201 未修，草案插入 CS201 并顺延，差异逐学期给出。"""
    pid = _confirmed(client)
    r = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 2,
        "completed_courses": ["CS101", "CS101L", "MATH103"],  # 原计划的 CS201 未修
    })
    assert r.status_code == 201
    body = r.json()
    # 最早完成（3 个学期）：CS201 只能安排在 2027-FALL，故首个剩余学期为空
    assert _sem_codes(body) == [[], ["CS201"], ["CS301"], ["CS401"]]
    diff = body["diff"]
    labels = [s["semester"] for s in diff["semesters"]]
    assert labels == ["2027-SPRING", "2027-FALL", "2028-SPRING", "2028-FALL"]
    spring = diff["semesters"][0]
    assert spring["original_courses"] == ["CS301"]
    assert spring["new_courses"] == []
    assert spring["removed_courses"] == ["CS301"]
    assert spring["added_courses"] == []
    fall = diff["semesters"][1]
    assert fall["original_courses"] == ["CS401"]
    assert fall["new_courses"] == ["CS201"]
    assert fall["removed_courses"] == ["CS401"]
    assert fall["added_courses"] == ["CS201"]
    assert diff["semesters"][2]["added_courses"] == ["CS301"]
    assert diff["semesters"][3]["added_courses"] == ["CS401"]
    assert set(diff["added_courses"]) == {"CS201", "CS301", "CS401"}
    assert set(diff["removed_courses"]) == {"CS301", "CS401"}


def test_revision_saved_as_draft_and_source_untouched(client):
    pid = _confirmed(client)
    rid = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 1, "completed_courses": ["CS101", "CS101L"]}).json()["plan_id"]

    # 原方案与历史目录不变
    src = client.get(f"/plans/{pid}").json()
    assert src["kind"] == "plan"
    assert src["status"] == "confirmed"
    assert src["catalog_version"] == 1
    assert "revision" not in src and "diff" not in src

    # 草案可单独回看
    draft = client.get(f"/plans/{rid}").json()
    assert draft["kind"] == "revision"
    assert draft["source_plan_id"] == pid
    assert draft["status"] == "proposed"

    # 草案不出现在原方案列表；可经子资源列出
    kinds = {p["plan_id"]: p["kind"] for p in client.get("/plans").json()}
    assert kinds[pid] == "plan" and rid not in kinds
    revs = client.get(f"/plans/{pid}/revisions").json()["revisions"]
    assert [r["plan_id"] for r in revs] == [rid]
    assert revs[0]["finished_semesters"] == 1 and revs[0]["source_plan_id"] == pid

    # 反复重排：每次都是独立待确认草案
    rid2 = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 1, "completed_courses": ["CS101", "CS101L"]}).json()["plan_id"]
    assert rid2 != rid
    revs = client.get(f"/plans/{pid}/revisions").json()["revisions"]
    assert [r["plan_id"] for r in revs] == [rid, rid2]


def test_revision_extra_completed_course_shortens_plan(client):
    """多修的 CS202 可替代 CS201 满足 CS301 的先修组；已修课程不重复排入。"""
    pid = _confirmed(client)
    r = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 2,
        "completed_courses": ["CS101", "CS101L", "CS201", "CS202", "MATH103"],
    })
    assert r.status_code == 201
    assert _sem_codes(r.json()) == [["CS301"], ["CS401"]]


def test_revision_rebuilt_against_current_catalog(client):
    """目录修订后重排：草案绑定新版本并按新目录计算（CS401 变为 4 学分）。"""
    pid = _confirmed(client)
    cur = client.get("/catalog/current").json()
    for c in cur["courses"]:
        if c["code"] == "CS401":
            c["credits"] = 4
    assert client.post("/catalog/versions",
                       json={"note": "v2", "courses": cur["courses"]}).status_code == 201

    r = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 2,
        "completed_courses": ["CS101", "CS101L", "CS201", "MATH103"],
    })
    assert r.status_code == 201
    body = r.json()
    assert body["catalog_version"] == 2  # 绑定当前目录，而非原方案的 v1
    cs401 = [c for s in body["result"]["semesters"] for c in s["courses"]
             if c["code"] == "CS401"][0]
    assert cs401["credits"] == 4  # 名称/学分取当前目录
    # 原方案仍绑定 v1，历史目录不变
    assert client.get(f"/plans/{pid}").json()["catalog_version"] == 1
    assert client.get("/catalog/versions/1").status_code == 200


def test_revision_no_solution_within_remaining_horizon(client):
    """学分上限 + 剩余学期不足：NO_SOLUTION，不保存草案。"""
    pid = _confirmed(client, max_credits_per_semester=4)
    # 原方案：CS101+L / CS201 / MATH103 / CS301 / 空 / CS401（6 学期）
    r = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 3,  # 只剩 3 个学期
        "completed_courses": ["CS101", "CS101L"],  # CS201、MATH103 均未修
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NO_SOLUTION"
    assert client.get(f"/plans/{pid}/revisions").json()["revisions"] == []


def test_revision_target_missing_after_catalog_revision(client):
    """当前目录删除了目标课程：COURSE_MISSING，不保存。"""
    pid = _confirmed(client)
    cur = client.get("/catalog/current").json()
    courses = [c for c in cur["courses"] if c["code"] != "CS401"]
    client.post("/catalog/versions", json={"note": "移除 CS401", "courses": courses})
    r = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 1, "completed_courses": ["CS101", "CS101L"]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "COURSE_MISSING"
    assert client.get(f"/plans/{pid}/revisions").json()["revisions"] == []


def test_revision_source_not_found(client):
    r = client.post("/plans/unknown/revisions", json={"finished_semesters": 1})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"
    assert client.get("/plans/unknown/revisions").status_code == 404


def test_revision_requires_confirmed_plan(client):
    pid = _plan(client).json()["plan_id"]  # 未确认
    r = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 1, "completed_courses": []})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "PLAN_NOT_CONFIRMED"


def test_revision_finished_semesters_bounds(client):
    pid = _confirmed(client)
    # pydantic gt=0
    r = client.post(f"/plans/{pid}/revisions", json={"finished_semesters": 0})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_INPUT"
    # 等于/超过原规划期限
    r = client.post(f"/plans/{pid}/revisions", json={"finished_semesters": 6})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "INVALID_INPUT"
    r = client.post(f"/plans/{pid}/revisions", json={"finished_semesters": 7})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "INVALID_INPUT"
    assert client.get(f"/plans/{pid}/revisions").json()["revisions"] == []


def test_revision_cannot_revoke_original_completed(client):
    pid = _confirmed(client, completed_courses=["CS101", "CS101L"])
    r = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 1, "completed_courses": ["CS101"]})  # 撤销 CS101L
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "INVALID_INPUT" and "CS101L" in detail["message"]


def test_revision_after_original_plan_ended_is_conflict(client):
    pid = _confirmed(client)
    r = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 4,
        "completed_courses": ["CS101", "CS101L", "CS201", "MATH103", "CS301", "CS401"]})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "REQUIREMENT_CONFLICT"
    assert client.get(f"/plans/{pid}/revisions").json()["revisions"] == []


def test_revision_confirm_uses_existing_catalog_change_rule(client):
    """草案确认沿用既有约定：落库后目录再修订，确认 409 且不写入。"""
    pid = _confirmed(client)
    rid = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 1, "completed_courses": ["CS101", "CS101L"]}).json()["plan_id"]

    cur = client.get("/catalog/current").json()
    courses = cur["courses"]
    courses[0]["credits"] += 1
    client.post("/catalog/versions", json={"note": "草案后再修订", "courses": courses})

    r = client.post(f"/plans/{rid}/confirm")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "CATALOG_CHANGED"
    assert client.get(f"/plans/{rid}").json()["status"] == "proposed"
    # 原方案状态同样不受影响
    assert client.get(f"/plans/{pid}").json()["status"] == "confirmed"


def test_revision_rejects_stale_result_if_catalog_changes_mid_run(client, monkeypatch):
    """计算至落库之间目录被修订：409 拒绝旧版本结果且不保存。"""
    pid = _confirmed(client)
    from app import db
    original_gcv = db.get_current_version
    calls = {"n": 0}

    def flaky_current(conn):
        calls["n"] += 1
        row = original_gcv(conn)
        if calls["n"] == 2:  # 计算完成、准备落库时的复检
            return {"id": row["id"] + 999}
        return row

    monkeypatch.setattr(db, "get_current_version", flaky_current)
    r = client.post(f"/plans/{pid}/revisions", json={
        "finished_semesters": 1, "completed_courses": ["CS101", "CS101L"]})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "CATALOG_CHANGED"
    monkeypatch.undo()
    assert client.get(f"/plans/{pid}/revisions").json()["revisions"] == []


def test_old_database_is_auto_migrated(tmp_path):
    """仅有旧版 plans 表结构的库，启动时自动增量升级且历史数据可查、可重排。"""
    db_path = tmp_path / "old.db"
    os.environ["DB_PATH"] = str(db_path)
    from app import db as db_mod
    from app.seed_data import SEED_COURSES

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE catalog_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL);
    CREATE TABLE courses (version_id INTEGER NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
        credits INTEGER NOT NULL, seasons TEXT NOT NULL, prereq_groups TEXT NOT NULL DEFAULT '[]',
        coreqs TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (version_id, code));
    CREATE TABLE plans (id TEXT PRIMARY KEY, version_id INTEGER NOT NULL, request_json TEXT NOT NULL,
        result_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed', created_at TEXT NOT NULL);
    """)
    vid = db_mod.create_catalog_version(conn, "初始目录", SEED_COURSES)
    req = {"completed_courses": [], "target_courses": ["CS401"], "start_semester": "2026-SPRING",
           "max_credits_per_semester": 12, "num_semesters": 6}
    # 用真实规划结果，保证含 4 个学期（模拟旧版应用落库的数据）
    from app.main import _to_catalog
    from app.planner import build_plan
    res = build_plan(_to_catalog(db_mod.load_courses(conn, vid)), set(), ["CS401"],
                     "2026-SPRING", 12, 6)
    conn.execute(
        "INSERT INTO plans (id, version_id, request_json, result_json, status, created_at)"
        " VALUES ('oldplan1',?,?,?, 'confirmed', '2026-01-01T00:00:00+00:00')",
        (vid, json.dumps(req), json.dumps(res)))
    conn.commit()
    conn.close()

    from app.main import app
    with TestClient(app) as c:
        row = c.get("/plans/oldplan1").json()
        assert row["kind"] == "plan" and "revision" not in row
        r = c.post("/plans/oldplan1/revisions", json={
            "finished_semesters": 1, "completed_courses": ["CS101", "CS101L"]})
        assert r.status_code == 201
        assert r.json()["source_plan_id"] == "oldplan1"
