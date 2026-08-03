# 手环健康综合管理分析服务 API 文档

## 服务信息

| 项目 | 说明 |
|------|------|
| 服务名称 | Health Analysis Service |
| 端口 | `26022` |
| 默认 LLM | DeepSeek API (`deepseek-v4-flash`)，由启动配置决定 |
| 备用 LLM | 本地 Baichuan-M2-32B，设置 `DEFAULT_PROVIDER=local` 切换 |
| 数据来源 | 请求体中直接传入数据库表数据 |

## 请求数据格式

请求体对应数据库三张表的数据结构：

| 请求字段 | 对应数据库表 | 类型 | 说明 |
|---------|-------------|------|------|
| `person` | `biz_person` | object | 人员基础信息，单条记录 |
| `wristband_records` | `biz_wristband_cache` | array | 手环实时缓存数据 |
| `sleep_records` | `biz_person_sleep_result` | array | 睡眠分析结果数据 |

---

## 接口列表

### 1. 统一健康分析（阻塞式）

**`POST /v1/health/analyze`**

一次请求返回全部 7 大维度的分析结果。

#### 请求

```http
POST /v1/health/analyze
Content-Type: application/json
```

```json
{
  "person": {
    "id": "2067422320443576321",
    "project_id": "2069727459418341377",
    "device_id": "861389061443375",
    "name": "张三",
    "gender": "1",
    "age": 22,
    "height": 164.0,
    "weight": 52.0,
    "bmi": 19.3
  },
  "wristband_records": [
    {
      "data_time": "2026-06-26 03:19:00",
      "avg_hr": 79, "max_hr": 79, "min_hr": 79,
      "sbp": 118, "dbp": 70, "bp_hr": 70,
      "avg_spo2": 96, "min_spo2": 95, "max_spo2": 99,
      "estimate_temp": 36.5,
      "steps": 120, "calorie": 5, "distance": 80,
      "fatigue": 50, "rmssd": 36,
      "type": "hr,spo,pressure"
    }
  ],
  "sleep_records": [
    {
      "collect_date": 20260709,
      "rri_data_list": "[-3, 4820, -1, 37, ...]",
      "sleep_segments": "[{\"E\":{...},\"Q\":1049,\"T\":[11,32]}]"
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `person` | object | 是 | `biz_person` 表的一条记录 |
| `wristband_records` | array | 否 | `biz_wristband_cache` 表的记录数组，可为空数组 |
| `sleep_records` | array | 否 | `biz_person_sleep_result` 表的记录数组，可为空数组 |

> LLM 模型由服务启动时的 `DEFAULT_PROVIDER` 配置决定（`deepseek` 或 `local`），请求中无需指定。

#### 响应

```json
{
  "project_id": "2069727459418341377",
  "device_id": "861389061443375",
  "person_id": "42028120010818001X",
  "report_period": "近一个月",
  "analysis": {
    "综合分析": {
      "综合评价": "结合近一个月的监测数据，您的整体健康状况良好...",
      "体检建议": "下次体检建议优先与医生沟通，增加几项高性价比的深度检查..."
    },
    "睡眠分析": "正常，您的睡眠整体达标。共有4晚睡眠监测记录...",
    "心率房颤分析": {
      "心率分析": "窦性心律(正常)，心率节律规整...",
      "房颤分析": "未检测到房颤特征(正常)..."
    },
    "血压血氧分析": {
      "血压分析": "血压（正常）。本次监测您的血压整体基本平稳...",
      "血氧分析": "血氧（正常）。本次监测期间，您的血氧饱和度全程处于成人健康推荐区间..."
    },
    "体温分析": "正常。本次监测期间，您的体温全程处于成人健康推荐区间...",
    "运动分析": "正常。本次监测周期内，您的每日步数全程处于成人健康推荐区间..."
  }
}
```

#### 7 大分析维度说明

| 维度 | 子项 | 说明 | 示例输出 |
|------|------|------|---------|
| **综合分析** | 综合评价 | 整体健康等级、同龄人对比、评分变化 | `"结合近一月的监测数据，您的整体健康状况优秀，在同龄人中处于前25%的水平..."` |
| | 体检建议 | 推荐体检项目和理由 | `"下次体检建议优先与医生沟通，增加颈动脉超声、糖化血红蛋白、尿微量白蛋白、高敏C反应蛋白..."` |
| **睡眠分析** | — | 睡眠质量、夜间心率、改善建议 | `"正常，您的睡眠整体达标：总时长符合健康标准..."` |
| **心率房颤分析** | 心率分析 | 心律类型、节律、波动范围 | `"窦性心律(正常)，心率节律规整，心率波动在正常范围内..."` |
| | 房颤分析 | 心房颤动风险评估 | `"未检测到房颤特征(正常)，心律规整，RR间期波动在正常范围内..."` |
| **血压血氧分析** | 血压分析 | 血压水平、稳定性、生理诱因 | `"血压（正常）。本次监测您的血压整体基本平稳..."` |
| | 血氧分析 | 血氧水平、氧合状态 | `"血氧（正常）。您的血氧饱和度全程处于成人健康推荐区间..."` |
| **体温分析** | — | 体温区间、昼夜节律、代谢状态 | `"正常。您的体温全程处于成人健康推荐区间，昼夜波动节律正常..."` |
| **运动分析** | — | 步数水平、活动分布、改善建议 | `"正常。您的每日步数全程处于成人健康推荐区间，活动时段分布均匀..."` |

---

### 2. 统一健康分析（流式）

**`POST /v1/health/analyze/stream`**

以 SSE (Server-Sent Events) 流式输出各维度分析结果。

#### 请求

```http
POST /v1/health/analyze/stream
Content-Type: application/json
```

```json
{
  "person": { ... },
  "wristband_records": [ ... ],
  "sleep_records": [ ... ]
}
```

请求参数同阻塞式接口。

#### 响应（SSE 事件流）

```
{"type": "meta", "project_id": "...", "device_id": "...", "person_id": "...", "report_period": "近一个月"}
{"type": "综合分析", "content": "结合近一个月的监测数据..."}
{"type": "睡眠分析", "content": "正常，您的睡眠整体达标..."}
{"type": "心率分析", "content": "窦性心律(正常)..."}
{"type": "房颤分析", "content": "未检测到房颤特征(正常)..."}
{"type": "血压分析", "content": "血压（正常）..."}
{"type": "血氧分析", "content": "血氧（正常）..."}
{"type": "体温分析", "content": "正常..."}
{"type": "运动分析", "content": "正常..."}
```

每条 SSE 事件为一行 JSON：

```json
{"type": "<维度名称>", "content": "<分析文本>"}
```

第一条为 `"meta"` 类型，包含人员信息和报告周期。

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEEPSEEK_API_KEY` | `sk-655f2d...` | DeepSeek API 密钥 |
| `DEFAULT_PROVIDER` | `deepseek` | LLM 后端：`deepseek` 或 `local` |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | DeepSeek 模型名称 |

## 测试示例

```bash
# 1. 阻塞式分析（传入数据库表数据）
curl -s -X POST http://localhost:26022/v1/health/analyze \
  -H "Content-Type: application/json" \
  -d '{"person":{...}, "wristband_records":[...], "sleep_records":[...]}' | python3 -m json.tool

# 2. 流式分析
curl -s -X POST http://localhost:26022/v1/health/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{"person":{...}, "wristband_records":[...], "sleep_records":[...]}'
```

## 性能参考

| 模式 | 模型 | 调用方式 | 耗时 |
|------|------|---------|------|
| LLM 并发 | `deepseek-v4-flash` | 8 路并行 | ~10s |
| LLM 顺序 | `deepseek-chat` | 8 次串行 | ~19s |
| 规则引擎 | — | 纯 Python | <10ms |
