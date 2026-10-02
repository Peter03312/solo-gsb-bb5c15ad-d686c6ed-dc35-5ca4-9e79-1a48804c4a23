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
| GET | `/plans` | 方案列表 |
| GET | `/plans/{id}` | 回看方案（含绑定的目录版本号） |
| POST | `/plans/{id}/confirm` | 确认方案；目录已修订则 409 拒绝且不写入 |

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
| `CATALOG_CHANGED` | 409 | 确认时目录已修订，拒绝且不写入 |
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
