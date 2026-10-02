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
