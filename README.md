# 高校课程学期规划服务

基于 **Python + FastAPI + SQLite** 的课程学期规划服务：依据课程目录（学分、春秋开课学期、
先修条件、同修课程），为学生生成满足全部约束、**期限内最早完成**的完整排课方案；
目录支持版本化修订，方案绑定目录版本，可回看、可确认。

## 功能与规则

- **课程目录**：每门课记录学分、开课季节（`SPRING`/`FALL`，可两者）、先修条件与同修课程。
  - 先修条件按“组”声明：**每组任选一门，各组均须满足**；先修课必须安排在**更早学期**。
  - 同修课程（coreq）尚未修过时，必须与该课**同学期**安排。
- **规划输入**：已修课程、目标课程（非空）、起始学期（如 `2026-SPRING`）、
  每学期学分上限、规划学期数。学分与两个上限必须为正，非法输入一律拒绝（422/400）。
- **规划输出**：
  - 已修课程不重复排入；方案只包含目标课程及满足条件所需的课程。
  - 返回期限内**最早完成**的完整方案；完成学期相同者，按“各学期课程代码序列”
    （每学期内代码升序、再按学期先后组成序列）的**字典序**取最小者。
  - 课程缺失、条件冲突（先修循环、同修课无共同开课学期等）或期限内无解时，
    返回具体原因，**不返回部分课表**。
- **目录版本**：每次目录修订生成新版本；方案创建时绑定当前版本，可随时回看；
  确认方案时若目录已修订，则拒绝（409）且不写入。
- **执行中途滚动重排**：已确认方案执行过程中，若实际课程完成情况偏离原方案，或目录发生
  修订，可凭「原方案编号 + 已结束学期数 + 截至该时点的累计已修课程」发起重排：
  - 已结束学期数须 **大于 0 且小于原规划期限**；累计已修**不得撤销**原方案创建时声明的
    已修课程；原方案不存在、未确认或原方案已执行完毕时拒绝。
  - 从原起始学期**顺延至下一学期**，沿用**原目标课程、每学期学分上限与剩余学期数**
    （原期限 − 已结束学期数），按**当前目录**求期限内最早完成的完整未来课表；已修课程
    不重复排入。
  - 返回新方案、它与**原方案剩余学期**逐学期对齐的课程差异（保留/移除/新增），并关联
    保存为**待确认草案**（`kind=revision`、`proposed`）；**原方案及其历史目录不变**。
  - 草案确认、回看沿用既有约定（草案绑定重排时的当前目录，其后目录再修订则确认 409）。
  - 目录在「计算 → 落库」之间被修订时，拒绝旧版本计算结果（409）且不保存。

## 快速开始

### 方式一：Docker Compose（推荐，一键启动）

```bash
docker compose up --build
```

### 方式二：本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 访问地址

| 内容 | 地址 |
| --- | --- |
| 服务地址 | http://localhost:8000 |
| 交互式 API 文档 | http://localhost:8000/docs |
| 健康检查 | http://localhost:8000/health |

端口：容器内外均为 **8000**（Compose 映射 `8000:8000`，可在 `docker-compose.yml` 中修改）。

### 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_PATH` | 容器内 `/data/app.db`；本地 `data/app.db` | SQLite 数据库文件路径 |

数据库在首次启动时自动初始化：建表，并写入种子课程目录（版本 1）。
Compose 通过命名卷 `planner-data` 持久化数据。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/catalog/versions` | 目录修订：全量提交课程集合，生成新版本 |
| GET | `/catalog/current` | 当前目录版本及全部课程 |
| GET | `/catalog/versions/{id}` | 查看历史目录版本（供回看） |
| POST | `/plans` | 生成规划方案（绑定当前目录版本） |
| GET | `/plans` | 原始方案列表（不含重排草案） |
| GET | `/plans/{id}` | 回看方案（含绑定的目录版本号） |
| POST | `/plans/{id}/confirm` | 确认方案；目录已修订则 409 拒绝且不写入 |
| POST | `/plans/{id}/revisions` | 已确认方案执行中途滚动重排，保存为待确认草案 |
| GET | `/plans/{id}/revisions` | 查看某方案关联的全部重排草案（按创建先后） |

### 目录课程字段

```json
{
  "code": "CS301",
  "name": "算法设计与分析",
  "credits": 3,
  "semesters": ["SPRING", "FALL"],
  "prereq_groups": [["CS201", "CS202"], ["MATH103"]],
  "coreqs": []
}
```

- `credits`：正整数；`semesters`：`SPRING`/`FALL` 至少其一。
- `prereq_groups`：每个内层数组为一组，组内任选一门，各组均须满足。
- `coreqs`：同修课程代码列表。

### 调用示例

生成规划：

```bash
curl -X POST http://localhost:8000/plans -H 'Content-Type: application/json' -d '{
  "completed_courses": [],
  "target_courses": ["CS401"],
  "start_semester": "2026-SPRING",
  "max_credits_per_semester": 12,
  "num_semesters": 6
}'
```

响应（201，节选）：

```json
{
  "plan_id": "64adff786f4c",
  "status": "proposed",
  "catalog_version": 1,
  "result": {
    "completion_semester": "2027-FALL",
    "total_credits": 16,
    "semesters": [
      {"semester": "2026-SPRING", "courses": [{"code": "CS101", ...}, {"code": "CS101L", ...}], "total_credits": 4},
      {"semester": "2026-FALL",   "courses": [{"code": "CS201", ...}, {"code": "MATH103", ...}], "total_credits": 6},
      {"semester": "2027-SPRING", "courses": [{"code": "CS301", ...}], "total_credits": 3},
      {"semester": "2027-FALL",   "courses": [{"code": "CS401", ...}], "total_credits": 3}
    ]
  }
}
```

确认 / 修订 / 回看：

```bash
curl -X POST http://localhost:8000/plans/<plan_id>/confirm      # 确认方案
curl -X POST http://localhost:8000/catalog/versions \
  -H 'Content-Type: application/json' -d @examples/catalog_v2.json  # 目录修订 -> 新版本
curl http://localhost:8000/plans/<plan_id>                       # 回看方案（含绑定版本）
curl http://localhost:8000/catalog/versions/1                    # 回看历史目录
```

完整可运行示例脚本（含错误演示）：

```bash
bash examples/demo.sh
```

### 执行中途滚动重排（本轮新增）

前提：原方案已通过 `POST /plans/{id}/confirm` 确认。原计划 CS401 于 2026-SPRING 起、
6 学期、每学期 ≤12 学分；执行 2 个学期后 CS201 未按计划修读，凭累计已修重排：

```bash
curl -X POST http://localhost:8000/plans/<plan_id>/revisions \
  -H 'Content-Type: application/json' -d '{
    "finished_semesters": 2,
    "completed_courses": ["CS101", "CS101L", "MATH103"]
  }'
```

- 起始学期自动顺延为 `2027-SPRING`，剩余学期 `6-2=4`，目标课程、学分上限沿用原方案，
  课程名称/学分/先修规则取**当前目录**。
- 响应 `201`（节选）：`kind` 为 `revision`、`status` 为 `proposed`，附带重排参数与
  与原方案剩余学期的逐学期差异：

```json
{
  "plan_id": "c6c1895a9258",
  "status": "proposed",
  "kind": "revision",
  "catalog_version": 1,
  "source_plan_id": "1ba6d4e2ad9b",
  "request": {
    "completed_courses": ["CS101", "CS101L", "MATH103"],
    "target_courses": ["CS401"],
    "start_semester": "2027-SPRING",
    "max_credits_per_semester": 12,
    "num_semesters": 4
  },
  "result": {
    "completion_semester": "2028-FALL",
    "semesters": [
      {"semester": "2027-SPRING", "courses": [], "total_credits": 0},
      {"semester": "2027-FALL", "courses": [{"code": "CS201", "credits": 3, "...": "..."}], "total_credits": 3},
      {"semester": "2028-SPRING", "courses": [{"code": "CS301", "...": "..."}], "total_credits": 3},
      {"semester": "2028-FALL", "courses": [{"code": "CS401", "...": "..."}], "total_credits": 3}
    ]
  },
  "revision": {
    "source_plan_id": "1ba6d4e2ad9b",
    "source_catalog_version": 1,
    "finished_semesters": 2,
    "previous_start_semester": "2026-SPRING",
    "remaining_semesters": 4
  },
  "diff": {
    "semesters": [
      {"semester": "2027-SPRING", "original_courses": ["CS301"], "new_courses": [],
       "kept_courses": [], "removed_courses": ["CS301"], "added_courses": []},
      {"semester": "2027-FALL", "original_courses": ["CS401"], "new_courses": ["CS201"],
       "kept_courses": [], "removed_courses": ["CS401"], "added_courses": ["CS201"]},
      {"semester": "2028-SPRING", "original_courses": [], "new_courses": ["CS301"],
       "kept_courses": [], "removed_courses": [], "added_courses": ["CS301"]},
      {"semester": "2028-FALL", "original_courses": [], "new_courses": ["CS401"],
       "kept_courses": [], "removed_courses": [], "added_courses": ["CS401"]}
    ],
    "removed_courses": ["CS301", "CS401"],
    "added_courses": ["CS201", "CS301", "CS401"],
    "kept_courses": []
  }
}
```

随后可沿用既有约定确认草案；列出某方案的全部草案：

```bash
curl -X POST http://localhost:8000/plans/<revision_id>/confirm   # 目录再修订则 409
curl http://localhost:8000/plans/<plan_id>/revisions              # 草案概要列表
```

说明：

- 草案是**新记录**（`GET /plans` 原始方案列表不包含草案，草案仅经
  `GET /plans/{id}/revisions` 与 `GET /plans/{revision_id}` 查看）；原方案记录与其绑定的
  历史目录在任何情况下均不被修改。
- 可对同一原方案反复重排，每次生成一个相互独立的待确认草案。
- 已修课程中包含当前目录已不存在的课程是允许的（视为转学分/历史修读），仅用于满足先修；
  目标课程必须仍在当前目录中。
- 重排计算完成到写库之间若发生目录修订，整体拒绝（`CATALOG_CHANGED`，409），不保存任何
  草案，请按最新目录重新发起。

### 错误响应

失败时返回相应 HTTP 状态码与原因，不返回部分课表：

```json
{"detail": {"code": "NO_SOLUTION", "message": "受开课学期、先修顺序或每学期学分上限 12 限制，2 个学期内无法排完所需课程"}}
```

| code | HTTP | 含义 |
| --- | --- | --- |
| `INVALID_INPUT` | 400/422 | 非法输入：空目标、非正学分/上限、学期格式错误等 |
| `COURSE_MISSING` | 422 | 课程缺失：目标课程不在当前目录版本中 |
| `REQUIREMENT_CONFLICT` | 422 | 条件冲突：先修循环、同修课无共同开课学期、同修课不在目录等 |
| `NO_SOLUTION` | 422 | 无解：期限内无法排完（学分上限/开课季节/先修顺序限制） |
| `TOO_COMPLEX` | 422 | 组合或搜索超出内置复杂度上限 |
| `PLAN_NOT_CONFIRMED` | 422 | 滚动重排前提不满足：原方案不存在（404）或尚未确认（422） |
| `CATALOG_CHANGED` | 409 | 确认时目录已修订；或重排计算至落库之间目录被修订（拒绝旧版本结果），均不写入 |
| `NOT_FOUND` | 404 | 方案或目录版本不存在 |

## 项目结构

```
app/
  main.py       # FastAPI 路由与错误处理
  planner.py    # 规划引擎（先修组枚举、同修合并、回溯排课、字典序选优）
  schemas.py    # 请求模型与校验
  db.py         # SQLite 持久化（目录版本、方案）
  seed_data.py  # 种子课程目录（版本 1）
tests/          # pytest 单元与 API 集成测试
examples/       # demo.sh 调用示例、catalog_v2.json 目录修订示例
Dockerfile / docker-compose.yml
```

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```
