# iot-smart_home

## `/v1/analyze/overview` 接口请求说明文档

### 1. 接口概述

- **接口地址**：`http://127.0.0.1:26021/v1/analyze/overview`
- **请求方式**：`POST`
- **Content-Type**：`application/json`
- **接口用途**：基于用户上传的睡眠监测数据和健康检测数据，生成统一的综合健康分析结果，返回结构化 `JSON`。

---

### 2. 请求参数

#### 顶层请求体

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `analysis_type` | `string` | 否 | 分析类型，默认值为 `unified_report` |
| `taskId` | `string` | **是** | 任务 ID，调用方必须传入，便于在长耗时分析期间轮询状态 |
| `data` | `object` | **是** | 完整健康数据对象，必须传入 |

#### `analysis_type` 可选值

| 值 | 说明 |
|---|---|
| `sleep_physiology` | 睡眠与生理分析 |
| `blood_analysis` | 血液指标分析 |
| `exam_report` | 综合体检报告 |
| `unified_report` | 统一综合健康报告（默认） |

---

### 3. 请求体示例

```json
{
  "analysis_type": "unified_report",
  "taskId": "health-task-20260410-001",
  "data": {
    "totalDays": 7,
    "sleepSummary": {
      "reportDays": 7,
      "validReportDays": 5,
      "noReportDays": 2,
      "hasSleepRecords": true,
      "sleepRecords": [
        {
          "date": "2026-04-01",
          "hasValidSleepReport": true,
          "bedTime": "23:10",
          "wakeUpTime": "06:40",
          "sleepDurationHours": 7.2,
          "sleepOnsetLatencyMinutes": 18,
          "sleepEfficiency": 88.5,
          "sleepScore": 82.4,
          "physiologicalScore": 80.1,
          "deepSleepHours": 1.5,
          "lightSleepHours": 4.2,
          "remSleepHours": 1.5,
          "awakeDurationMinutes": 12,
          "outBedCount": 1,
          "outBedDurationMinutes": 3,
          "apnoeaCount": 2,
          "averageHeartRate": 61,
          "minHeartRate": 54,
          "maxHeartRate": 76,
          "heartRateAlarmCount": 0,
          "averageBreathingRate": 14,
          "minBreathingRate": 12,
          "maxBreathingRate": 17,
          "breathingAlarmCount": 0
        }
      ]
    },
    "healthCheckRecords": [
      {
        "type": "fasting",
        "upTime": "2026-04-01 08:00:00",
        "createTime": "2026-04-01 08:05:00",
        "bloodGlucose": "5.4",
        "uricAcid": "346",
        "pressureS": "129",
        "pressureD": "82",
        "pressureRate": "68",
        "bloodfatTc": "4.68",
        "bloodfatHdl": "1.32",
        "bloodfatTg": "1.21",
        "bloodfatLdl": "2.43"
      }
    ]
  }
}
```

---

### 4. `data` 内部结构说明

#### `sleepSummary`

| 字段名 | 类型 | 说明 |
|---|---|---|
| `reportDays` | `int` | 报告周期总天数 |
| `validReportDays` | `int` | 有效睡眠记录天数 |
| `noReportDays` | `int` | 无记录天数 |
| `hasSleepRecords` | `bool` | 是否存在睡眠记录 |
| `sleepRecords` | `array<object>` | 睡眠明细列表 |

#### `healthCheckRecords`

| 字段名 | 类型 | 说明 |
|---|---|---|
| `type` | `string` | 检测类型 |
| `upTime` | `string` | 检测时间 |
| `createTime` | `string` | 创建时间 |
| `bloodGlucose` | `string` | 血糖，单位 `mmol/L` |
| `uricAcid` | `string` | 尿酸，单位 `μmol/L` |
| `pressureS` | `string` | 收缩压，单位 `mmHg` |
| `pressureD` | `string` | 舒张压，单位 `mmHg` |
| `pressureRate` | `string` | 心率，单位 `bpm` |
| `bloodfatTc` | `string` | 总胆固醇 |
| `bloodfatHdl` | `string` | 高密度脂蛋白 HDL |
| `bloodfatTg` | `string` | 甘油三酯 TG |
| `bloodfatLdl` | `string` | 低密度脂蛋白 LDL |

---

### 5. 成功返回示例

```json
{
  "taskId": "health-task-20260410-001",
  "睡眠质量分析结果": {
    "advantages": "睡眠时长表现良好，夜间稳定性较好。",
    "improvements_needed": "部分日期入睡潜伏期偏长，呼吸暂停次数偏多。",
    "suggestions": "建议建立固定睡前仪式，并优化睡姿与睡眠环境。",
    "summary": "本周期睡眠质量整体有波动，但存在改善趋势。"
  },
  "血脂分析结果": {
    "suggestions": "建议继续保持规律运动，并优化日常油脂摄入结构。",
    "summary": "血脂整体处于安全范围，心血管风险较低。"
  },
  "尿酸分析结果": {
    "suggestions": "建议增加饮水量，减少高嘌呤饮食摄入。",
    "summary": "尿酸水平总体正常，但接近部分参考上限。"
  },
  "心率分析结果": {
    "suggestions": "建议避免睡前剧烈运动，并持续监测静息心率。",
    "summary": "夜间心率整体正常，仅个别日期出现报警。"
  },
  "呼吸分析结果": {
    "suggestions": "建议进行腹式呼吸训练，并适当调整睡姿。",
    "summary": "呼吸频率整体正常，但个别日期呼吸暂停偏多。"
  },
  "血压分析结果": {
    "suggestions": "建议继续保持低盐饮食与规律运动。",
    "summary": "血压整体稳定，处于理想范围。"
  },
  "空腹血糖分析结果": {
    "suggestions": "建议保持规律饮食，减少含糖饮品摄入。",
    "summary": "空腹血糖总体正常，但需关注长期波动趋势。"
  },
  "健康综合评价": "您整体健康状况良好，各项指标基本在正常范围内。",
  "体检建议": "建议进行多导睡眠监测、尿酸复查及年度常规体检。"
}
```

---

### 6. 错误返回示例

#### 情况一：缺少必填字段 `taskId` 或 `data`

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "taskId"],
      "msg": "Field required"
    },
    {
      "type": "missing",
      "loc": ["body", "data"],
      "msg": "Field required"
    }
  ]
}
```

#### 情况二：`data` 中缺少关键字段

例如缺少 `sleepSummary` 或 `healthCheckRecords` 时，会返回对应异常信息：

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "data", "sleepSummary"],
      "msg": "缺少关键字段 `sleepSummary`"
    },
    {
      "type": "missing",
      "loc": ["body", "data", "healthCheckRecords"],
      "msg": "缺少关键字段 `healthCheckRecords`"
    }
  ]
}
```

#### 情况三：嵌套字段缺失

例如 `data.sleepSummary` 中缺少 `sleepRecords` 时：

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "data", "sleepSummary", "sleepRecords"],
      "msg": "缺少关键字段 `sleepSummary.sleepRecords`"
    }
  ]
}
```

---

### 7. 任务状态查询

当请求体中传入 `taskId` 后，可在等待结果期间调用以下接口查询服务状态：

- **接口地址**：`GET /v1/analyze/overview/status/{taskId}`

返回示例：

```json
{
  "taskId": "health-task-20260410-001",
  "status": "running",
  "message": "参数校验通过，正在生成健康分析结果",
  "analysisType": "unified_report",
  "resultReady": false,
  "createdAt": "2026-04-10T16:30:00+08:00",
  "updatedAt": "2026-04-10T16:30:08+08:00",
  "startedAt": "2026-04-10T16:30:01+08:00"
}
```

状态说明：

| `status` 值 | 含义 |
|---|---|
| `accepted` | 请求已接收 |
| `running` | 正在分析中 |
| `completed` | 已完成 |
| `failed` | 执行失败 |

### 8. 调用示例

#### `curl`

```bash
curl -X POST "http://127.0.0.1:26021/v1/analyze/overview" \
  -H "Content-Type: application/json" \
  -d '{
    "analysis_type": "unified_report",
    "taskId": "health-task-20260410-001",
    "data": {
      "totalDays": 7,
      "sleepSummary": {"reportDays": 7, "validReportDays": 5, "noReportDays": 2, "hasSleepRecords": true, "sleepRecords": []},
      "healthCheckRecords": []
    }
  }'
```

#### 使用本地样例文件测试

```bash
curl -X POST "http://127.0.0.1:26021/v1/analyze/overview" \
  -H "Content-Type: application/json" \
  -d "$(printf '{\"analysis_type\":\"unified_report\",\"data\":%s}' "$(cat guard_ai_upload_one_week_sample.json)")"
```

---

### 9. 说明

- `data` 字段现在为**必填项**，不能再直接将健康数据平铺在请求体顶层。
- 推荐统一按 `analysis_type + data` 的格式调用接口。
- 该接口依赖大模型生成分析内容，响应可能较慢，请耐心等待。
