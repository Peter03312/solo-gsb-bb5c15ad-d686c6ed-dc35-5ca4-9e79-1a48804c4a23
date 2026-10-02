#!/usr/bin/env bash
# 调用示例：先启动服务（docker compose up --build 或 uvicorn app.main:app）
# 用法：bash examples/demo.sh  [默认访问 http://localhost:8000，可用 BASE 覆盖]
BASE="${BASE:-http://localhost:8000}"
HERE="$(cd "$(dirname "$0")" && pwd)"

step() { echo; echo "== $* =="; }

step "1. 健康检查"
curl -s "$BASE/health"; echo

step "2. 当前课程目录（首次启动自动初始化为版本 1）"
curl -s "$BASE/catalog/current"; echo

step "3. 请求规划：目标 编译原理 CS401，2026 春开始，每学期≤12 学分，6 个学期"
PLAN_A=$(curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "completed_courses": [],
  "target_courses": ["CS401"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 6
}')
echo "$PLAN_A"
PLAN_A_ID=$(echo "$PLAN_A" | python3 -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")

step "4. 再规划一个方案 B（暂不确认）"
PLAN_B=$(curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "completed_courses": ["CS101", "CS101L", "CS201"],
  "target_courses": ["CS303"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 4
}')
echo "$PLAN_B"
PLAN_B_ID=$(echo "$PLAN_B" | python3 -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")

step "5. 确认方案 A（目录未变，应成功）"
curl -s -X POST "$BASE/plans/$PLAN_A_ID/confirm"; echo

step "6. 修订目录（生成版本 2）"
curl -s -X POST "$BASE/catalog/versions" -H 'Content-Type: application/json' \
  -d @"$HERE/catalog_v2.json"; echo

step "7. 确认方案 B（目录已变，应 409 拒绝且不写入）"
curl -s -w '\nHTTP %{http_code}\n' -X POST "$BASE/plans/$PLAN_B_ID/confirm"
echo "方案 B 状态（仍为 proposed）："
curl -s "$BASE/plans/$PLAN_B_ID"; echo

step "8. 基于新目录重新规划并确认"
PLAN_C=$(curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "completed_courses": ["CS101", "CS101L", "CS201"],
  "target_courses": ["CS303"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 4
}')
echo "$PLAN_C"
PLAN_C_ID=$(echo "$PLAN_C" | python3 -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")
curl -s -X POST "$BASE/plans/$PLAN_C_ID/confirm"; echo

step "9. 回看：方案绑定的目录版本"
curl -s "$BASE/catalog/versions/1"; echo

step "10. 错误示例：课程缺失"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["ABC123"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6
}'; echo

step "11. 错误示例：条件冲突（CS999 与其同修课开课季节互斥）"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS999"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6
}'; echo

step "12. 错误示例：期限内无解"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS401"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 2
}'; echo

step "13. 错误示例：非法输入（空目标 / 非正上限）"
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": [], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12, "num_semesters": 6
}'; echo
curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "target_courses": ["CS101"], "start_semester": "2026-SPRING",
  "max_credits_per_semester": 0, "num_semesters": 6
}'; echo

# ============================================================
# 执行中途滚动重排（原方案必须已确认；以下使用独立方案演示）
# ============================================================

step "14. 新建并确认一个用于演示重排的方案（目标 CS401，6 学期）"
PLAN_D=$(curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "completed_courses": [],
  "target_courses": ["CS401"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 6
}')
PLAN_D_ID=$(echo "$PLAN_D" | python3 -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")
curl -s -X POST "$BASE/plans/$PLAN_D_ID/confirm" >/dev/null
echo "原方案：$PLAN_D_ID"
echo "$PLAN_D" | python3 -c "import sys,json;d=json.load(sys.stdin);print([[c['code'] for c in s['courses']] for s in d['result']['semesters']])"

step "15. 滚动重排：已结束 2 个学期，CS201 未修（偏离原方案），按当前目录求最早完成"
REV=$(curl -s -X POST "$BASE/plans/$PLAN_D_ID/revisions" -H 'Content-Type: application/json' -d '{
  "finished_semesters": 2,
  "completed_courses": ["CS101", "CS101L", "MATH103"]
}')
echo "$REV"
REV_ID=$(echo "$REV" | python3 -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")

step "16. 查看该原方案关联的全部待确认草案"
curl -s "$BASE/plans/$PLAN_D_ID/revisions" | python3 -m json.tool --no-ensure-ascii

step "17. 确认重排草案（沿用既有确认约定；目录若再修订将 409）"
curl -s -X POST "$BASE/plans/$REV_ID/confirm"; echo

step "18. 重排错误示例：原方案未确认 / 已结束学期数越界 / 撤销原已修 / 期限内无解"
PLAN_E=$(curl -s -X POST "$BASE/plans" -H 'Content-Type: application/json' -d '{
  "completed_courses": ["CS101", "CS101L"], "target_courses": ["CS401"],
  "start_semester": "2026-SPRING", "max_credits_per_semester": 12, "num_semesters": 6
}')
PLAN_E_ID=$(echo "$PLAN_E" | python3 -c "import sys,json;print(json.load(sys.stdin)['plan_id'])")
echo "-- 未确认的原方案（422 PLAN_NOT_CONFIRMED）："
curl -s -X POST "$BASE/plans/$PLAN_E_ID/revisions" -H 'Content-Type: application/json' \
  -d '{"finished_semesters": 1, "completed_courses": ["CS101", "CS101L"]}'; echo
# 注：步骤 6 已将目录修订为 v2，本方案基于 v2，确认后再演示“撤销原已修”
curl -s -X POST "$BASE/plans/$PLAN_E_ID/confirm" >/dev/null
echo "-- 已结束学期数不满足 0 < n < 6（400 INVALID_INPUT）："
curl -s -X POST "$BASE/plans/$PLAN_D_ID/revisions" -H 'Content-Type: application/json' \
  -d '{"finished_semesters": 6, "completed_courses": []}'; echo
echo "-- 撤销了原方案创建时声明的已修课程 CS101L（400 INVALID_INPUT）："
curl -s -X POST "$BASE/plans/$PLAN_E_ID/revisions" -H 'Content-Type: application/json' \
  -d '{"finished_semesters": 1, "completed_courses": ["CS101"]}'; echo
