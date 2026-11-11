# TVPilot HDMI3 使用时长数据对接单

> 交付方：TVPilot（电视端控制服务，容器 `tvpilot`，端口 8090）
> 接收方：memory-agent（记忆管理服务，容器 `memory-agent`，端口 8086）
> 日期：2026-09-09
> 版本：v1.0
> 优先级：P2

---

## 1. 背景与目标

小甜菜正在开发**电视游戏机防沉迷系统**，需要统计孩子在 HDMI3（游戏机接口）的使用时长。

TVPilot 已实现 HDMI3 状态检测和时长累计（每5分钟轮询电视，本地 SQLite 存储7天）。现需将历史数据同步到 memory-agent 长期存储（90天），供用户画像、行为分析、防沉迷报告等功能使用。

---

## 2. 对接模式：memory-agent 拉取（推荐）

### 2.1 为什么用拉模式

| 维度 | 拉模式（memory-agent 主动拉） | 推模式（TVPilot 主动推） |
|------|-------------------------------|--------------------------|
| 架构一致性 | ✅ 符合现有 collect 框架（从 HA 拉数据） | ❌ 需要新增推送 API |
| 失败处理 | ✅ memory-agent 控制重试、断点续传 | ❌ TVPilot 需处理推送失败队列 |
| 耦合度 | ✅ 低，TVPilot 只提供只读 API | ❌ 高，TVPilot 依赖 memory-agent 可用性 |
| 开发量 | TVPilot 侧 0（API 已就绪），memory-agent 侧新增采集任务 | 双方都需开发 |

### 2.2 采集频率建议

- **每日一次**：每日 00:10 拉取前一天（00:00~24:00）的汇总数据
- **可选增量**：每小时拉取当日实时数据（如需实时统计）
- **历史回填**：首次对接时，拉取最近7天数据（TVPilot 本地保留7天）

---

## 3. TVPilot API 详细说明

### 3.1 基础信息
- **Base URL**：`http://192.168.2.200:8090`（内网，无需认证）
- **格式**：JSON
- **超时建议**：5秒

### 3.2 查询指定日期使用时长（主要接口）

**GET** `/api/tv/hdmi3/usage?date=YYYY-MM-DD`

**请求参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| date | string | 否 | 日期 YYYY-MM-DD，默认今天 |

**响应示例**：
```json
{
  "code": 0,
  "data": {
    "date": "2026-09-09",
    "seconds": 4520,
    "minutes": 75,
    "hours": 1.25,
    "sessions": [
      {
        "start": "2026-09-09T09:00:00.123456",
        "end": "2026-09-09T10:30:00.654321",
        "seconds": 5400
      },
      {
        "start": "2026-09-09T14:00:00.111111",
        "end": null,
        "seconds": 3600
      }
    ]
  }
}
```

**字段说明**：
| 字段 | 类型 | 说明 |
|------|------|------|
| date | string | 日期 |
| seconds | int | 当日累计秒数 |
| minutes | int | 当日累计分钟（取整） |
| hours | float | 当日累计小时 |
| sessions | array | 使用时段列表 |
| sessions[].start | string | 开始时间（ISO8601，含微秒） |
| sessions[].end | string/null | 结束时间，null 表示进行中 |
| sessions[].seconds | int | 该时段秒数 |

### 3.3 查询最近 N 天统计

**GET** `/api/tv/hdmi3/history?days=7`

**响应示例**：
```json
{
  "code": 0,
  "data": {
    "days": 7,
    "total_seconds": 28800,
    "total_hours": 8.0,
    "avg_minutes_per_day": 68,
    "active_days": 5,
    "daily": [
      {"date": "2026-09-03", "seconds": 3600, "minutes": 60},
      {"date": "2026-09-04", "seconds": 5400, "minutes": 90}
    ]
  }
}
```

### 3.4 当前状态（实时查询，非采集用）

**GET** `/api/tv/hdmi3/status` — 返回当前是否在 HDMI3 + 今日累计
**GET** `/api/tv/hdmi3/today` — 简化版今日时长
**GET** `/api/tv/state` — 电视通用状态

### 3.5 错误码

| code | 说明 |
|------|------|
| 0 | 成功 |
| 非0 | 失败（电视未连接/日期格式错误等） |

**注意**：日期无数据时返回 `seconds=0, sessions=[]`，不报错。

---

## 4. 数据存储建议

### 4.1 存储位置选择

memory-agent 现有表分析：

| 表 | 用途 | 适合存 HDMI3 时长吗 |
|----|------|---------------------|
| `events` | HA 设备状态变更事件 | ❌ 是 HA 实体事件 |
| `behavior_events` | 视觉事件（人脸/场景） | ❌ 表结构围绕 persons_json、snapshot 设计 |
| `agent_memories` | LLM 语义记忆 | ❌ 自然语言记忆，非结构化统计 |

**建议：新建 `tv_usage` 表**，专门存储电视使用时长统计。

### 4.2 建议表结构

```sql
CREATE TABLE IF NOT EXISTS tv_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,              -- 'YYYY-MM-DD'
  source TEXT NOT NULL DEFAULT 'hdmi3',  -- 输入源：hdmi3 / hdmi1 / app 等
  total_seconds INTEGER NOT NULL DEFAULT 0,
  sessions_json TEXT NOT NULL DEFAULT '[]',  -- 使用时段 JSON
  collected_at TEXT NOT NULL,       -- memory-agent 采集时间
  source_api TEXT NOT NULL DEFAULT 'tvpilot',  -- 数据来源
  UNIQUE(date, source)
);
CREATE INDEX IF NOT EXISTS idx_tv_usage_date ON tv_usage(date);
```

### 4.3 数据保留策略
- TVPilot 本地保留 **7 天**（短期缓存，供实时查询）
- memory-agent 长期保留 **90 天**（供用户画像、趋势分析、防沉迷报告）
- 超过 90 天的数据可归档或删除（根据 memory-agent 现有清理策略）

---

## 5. 采集实现建议

### 5.1 接入 collect 框架

memory-agent 已有 `/api/collect/*` 系列接口和定时采集任务（从 HA 拉取数据）。建议：

1. 在 `collect_routes.py` 或新增 `tv_collect.py` 中添加 TVPilot 数据源
2. 新增采集任务类型：`tv_hdmi3_usage`
3. 每日定时执行：调用 TVPilot `/api/tv/hdmi3/usage?date=昨天` 拉取数据
4. 写入 `tv_usage` 表

### 5.2 伪代码

```python
async def collect_tv_hdmi3_usage(date: str) -> dict:
    """从 TVPilot 拉取指定日期 HDMI3 使用时长。"""
    url = f"http://192.168.2.200:8090/api/tv/hdmi3/usage?date={date}"
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(url)
        data = resp.json()
    if data.get("code") != 0:
        return {"ok": False, "error": data.get("msg", "unknown")}
    
    usage = data["data"]
    # 写入 tv_usage 表（INSERT OR REPLACE，支持重复采集更新）
    store.upsert_tv_usage(
        date=usage["date"],
        source="hdmi3",
        total_seconds=usage["seconds"],
        sessions_json=json.dumps(usage["sessions"], ensure_ascii=False),
        collected_at=now_local().isoformat(),
    )
    return {"ok": True, "date": date, "seconds": usage["seconds"]}
```

### 5.3 历史回填

首次对接时，循环拉取最近7天：
```python
for i in range(1, 8):  # 昨天到前7天
    date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
    await collect_tv_hdmi3_usage(date)
```

---

## 6. 与其他系统的关系

```
┌─────────────┐     实时查询      ┌─────────────┐
│   小甜菜     │ ◄─────────────── │   TVPilot   │
│  (防沉迷)    │   /api/tv/hdmi3/* │  (8090)     │
└─────────────┘                   └──────┬──────┘
                                          │ 每日拉取
                                          ▼
                                   ┌─────────────┐
                                   │ memory-agent│
                                   │  (8086)     │
                                   │ tv_usage 表 │
                                   │ (90天)      │
                                   └─────────────┘
```

- **小甜菜**：实时查询走 TVPilot API（低延迟，当前状态）
- **memory-agent**：历史统计、用户画像、长期趋势（90天数据）
- **TVPilot**：数据采集源头，本地7天缓存

---

## 7. 验收标准

1. ✅ memory-agent 可成功调用 TVPilot `/api/tv/hdmi3/usage?date=YYYY-MM-DD` 并解析返回
2. ✅ 每日定时采集任务正常执行，数据写入 `tv_usage` 表
3. ✅ 历史回填可拉取最近7天数据
4. ✅ 重复采集同一天数据不产生重复记录（UPSERT）
5. ✅ TVPilot 不可用时采集任务优雅失败，不影响其他采集任务
6. ✅ 数据保留90天，超期自动清理
7. ✅ memory-agent 可基于 `tv_usage` 表查询指定日期/时间范围的 HDMI3 使用时长

---

## 8. 联系方式

- TVPilot 代码：`E:\NAS\TVPilot`（容器内 `/app/tvpilot_server.py` + `/app/hdmi3_monitor.py`）
- TVPilot API 文档：`E:\NAS\xiaotiancai\docs\TVPilot_HDMI3时长统计接口标准_v1.0.md`
- TVPilot 容器：`tvpilot`，端口 `8090`
- 电视：192.168.2.238:5555（ADB）

---

## 9. 变更记录

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-09-09 | 初始版本，定义 HDMI3 时长数据对接方案 |
