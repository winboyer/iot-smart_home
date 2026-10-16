# 健康综合管理分析服务 API 文档

> 服务包含两条相互独立的数据链路：**手环健康分析**（1、2 节）与**极地项目睡眠传感器分析**（3 节）。

## 服务信息

| 项目 | 说明 |
|------|------|
| 服务名称 | Health Analysis Service |
| 端口 | `8006` |
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

> 极地项目接口（第 3 节）使用另一套请求体：上报元数据与统计周期（`projectId` /
> `personId` / `deviceId` / `periodType` / `periodStart` / `periodEnd`）+
> `records` 逐日记录数组（一个请求 = 一个统计周期），并额外返回综合评分 `score`，详见该节。

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

### 3. 极地项目健康体检数据分析（阻塞式）

**`POST /v1/health/polar/analyze`**

> 本节为接口契约说明。**面向调用方的调用方式文档（含 cURL / Python / Java / JavaScript 示例、
> 错误码、自测清单）见 [`POLAR_API_GUIDE.md`](./POLAR_API_GUIDE.md)。**

面向极地项目的睡眠传感器上报数据。**一个请求 = 一个统计周期**，周期内含多条按天记录
（`records` 数组，每一条 = 一天的监测数据）。

> **极地项目仅此一个接口**：`day` / `week` / `month` / `quarter` / `year` 等周期**共用本接口**，
> 由 `periodType` 区分、`records` 承载该周期内的逐日数据，**不按周期拆分接口**。

聚焦 **睡眠时长、夜间心率、呼吸** 三项核心指标，响应包含四部分：

- **元数据与周期回显**：`projectId` / `personId` / `deviceId` / `periodType` / `periodStart` / `periodEnd`
- **综合评分 `score`**：0–100 分 + 等级 + 分项明细（由规则引擎确定性计算，见下文）
- **结论速览 `conclusion`**：四项标签
- **完整分析 `analysis`**：综合分析、睡眠分析、心率呼吸分析

与手环接口（1、2 节）相互独立，互不影响。

#### 请求

```http
POST /v1/health/polar/analyze
Content-Type: application/json
```

```json
{
  "projectId": "SIM_PROJECT_001",
  "personId": "8800000000000001",
  "deviceId": "8001",
  "periodType": "week",
  "periodStart": "2026-10-06",
  "periodEnd": "2026-10-12",
  "records": [
    {
      "collectTime": "2026-10-06 06:28:00",
      "metrics": [
        { "metricName": "sleepHours", "metricValue": "7.10" },
        { "metricName": "deepSleepHours", "metricValue": "1.80" },
        { "metricName": "lightSleepHours", "metricValue": "5.30" },
        { "metricName": "awakeHours", "metricValue": "0.35" },
        { "metricName": "heartRateValue1", "metricValue": "62" },
        { "metricName": "breathValue1", "metricValue": "15" },
        { "metricName": "movementSigns1", "metricValue": "6" }
      ]
    }
  ]
}
```

#### 请求字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `projectId` | string | 否 | 项目编码，原样回显 |
| `personId` | string | 否 | 人员编码，原样回显；缺省时可回退取 `person.id` / `person.id_card` |
| `deviceId` | string | 否 | 设备编码，原样回显；缺省时可回退取 `person.device_id` |
| `periodType` | string | 否 | 统计周期类型：`day` / `week` / `month` / `quarter` / `year` |
| `periodStart` | string | 否 | 统计起始日期 `YYYY-MM-DD` |
| `periodEnd` | string | 否 | 统计结束日期 `YYYY-MM-DD` |
| `records` | array | 否 | 逐日监测记录数组，元素为 `{collectTime, metrics}` |
| `person` | object | 否 | 人员基础信息（`id`/`name`/`gender`/`age`），仅用于个性化文案 |

**周期文案生成规则**（`reportPeriod`）：

| 输入 | `reportPeriod` |
|------|----------------|
| `periodType=week` + 起止日期 | `近一周（2026-10-06 至 2026-10-12）` |
| `periodType=day` + 同一天 | `今日（2026-10-12）` |
| 仅起止日期 | `2026-10-06 至 2026-10-12` |
| 都没有 / 类型未识别 | `本次监测` |

`periodType` 取值为：`day`→今日、`week`→近一周、`month`→近一个月、`quarter`→近三个月、`year`→近一年。

#### `records` 元素结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `collectTime` | string | 该条数据的采集时间，`YYYY-MM-DD HH:mm:ss` |
| `metrics` | array | 指标数组，元素为 `{metricName, metricValue}` |

`recordCount` = 数组中**字典元素**的个数（非字典元素忽略）；`validRecordCount` = 至少解析出
睡眠时长、心率或呼吸之一的记录数。

#### `metrics` 指标名对照表

`metricName` 忽略大小写与分隔符差异，并自动剥离**尾部采集序号**
（`heartRateValue1`、`heart_rate_value_2` 均等价于 `heartRateValue`）。
**同一条记录内**同一指标多次上报时：数值型**取平均**，状态 / 事件型取最后一次。

| `metricName`（归一化后） | 含义 | 单位 | 参与分析 |
|------|------|------|------|
| `sleepHours` | 睡眠总时长 | 小时 | 睡眠分析（**优先级最高**） |
| `heartRateValue` | 夜间心率 | bpm | 心率分析 |
| `breathValue` | 夜间呼吸 | 次/分 | 呼吸分析 |
| `movementSigns` | 运动体征（体动幅度） | — | 辅助指标 |
| `deepSleepDuration` / `deepSleepHours` | 深睡时长 | 分钟 / 小时 | 睡眠结构 |
| `lightSleepDuration` / `lightSleepHours` | 浅睡时长 | 分钟 / 小时 | 睡眠结构 |
| `awakeDuration` / `awakeHours` | 清醒时长 | 分钟 / 小时 | 睡眠结构 |
| `sleepScore` | 睡眠评分 | 0-100 | 辅助指标 |
| `sleepStatus` | 睡眠状态 | `0`深睡 / `1`浅睡 / `2`清醒 / `3`无 | 辅助指标 |
| `heartbeat` | 设备心跳 | `0`异常 / `1`正常 | 辅助指标 |
| `someoneExists` | 人体存在 | `0`无人 / `1`有人 | 辅助指标 |
| `getIntoBed` | 入床离床 | `0`离床 / `1`入床 | 辅助指标 |
| `abnormalSleep` | 设备睡眠异常判定 | `0`不足4h / `1`大于12h / `2`长时间无人 / `3`无 | 睡眠分析（**直接判异常**） |
| `breathe_alarm` | 呼吸报警 | `0`正常 / `1`异常 | 呼吸分析（至少"需关注"） |
| `bedoff_alarm` | 离床报警 | `0`正常 / `1`异常 | 睡眠分析（至少"需关注"） |
| `sleepComprehensiveStatus` | 睡眠综合状态 | 8 字节数组 | 见下方位序 |
| `sleepQualityAnalysis` | 睡眠质量分析 | 12 字节数组 | 见下方位序 |

**兼容性**：`metricValue` 兼容字符串数字（`"7.10"` 与 `7.10` 等价）；布尔类支持 `0/1`、`true/false`；
未识别的 `metricName` 会被忽略，不返回 422。

**字节数组位序**

- `sleepComprehensiveStatus`（8 字节）：`[存在、睡眠状态、平均呼吸、平均心跳、翻身次数、大幅度体动占比、小幅度体动占比、呼吸暂停次数]`
- `sleepQualityAnalysis`（12 字节）：`[睡眠质量评分、睡眠总时长(2B)、清醒时长占比、浅睡时长占比、深睡时长占比、离床时长、离床次数、翻身次数、平均呼吸、平均心跳、呼吸暂停次数]`
- 支持三种写法：整型数组 `[1,1,15,...]`、十六进制串 `"01010F3D17122A02"`、逗号或空格分隔串 `"1,1,15,..."`

#### 多日汇总：取值与换算规则（服务端自动完成）

单条记录先按下表解析，随后**按天汇总为周期统计量**（均值、极值、达标率、逐日明细）。

| 分析指标 | 取值来源（按优先级） | 换算 |
|------|------|------|
| 睡眠总时长 | `sleepHours` → `sleepQualityAnalysis` 第 2、3 字节（大端）→ `deepSleepDuration` + `lightSleepDuration` | 统一换算为小时 |
| 深睡 / 浅睡时长 | `deepSleepDuration` / `lightSleepDuration` | 分钟 ÷ 60 |
| 夜间平均心率 | `sleepComprehensiveStatus[3]` → `sleepQualityAnalysis[10]` → `heartRateValue` | 直接取值（bpm） |
| 夜间平均呼吸 | `sleepComprehensiveStatus[2]` → `sleepQualityAnalysis[9]` → `breathValue` | 直接取值（次/分） |
| 呼吸暂停次数 | `sleepComprehensiveStatus[7]` → `sleepQualityAnalysis[11]` | 直接取值 |

多日汇总口径：

| 统计量 | 口径 |
|------|------|
| 均值（`total_sleep_hours` / `avg_heart_rate` / `avg_breathing_rate`） | 各有效日**算术平均**，用于结论判定与评分 |
| 极值 | 各日最小 / 最大 |
| 达标率 | 落在正常参考区间内的**天数占比** |
| 设备报警 | 任一日报警即视为有报警 |
| 设备睡眠异常 | 按严重程度取最严重的一天（`0/1` > `2` > `3`） |

#### 综合评分（`score`）

**由规则引擎确定性计算**，同一份数据永远得到同一个分数，不随大模型输出波动。

```
单项得分 = 均值得分 × 70% + 达标天数占比 × 100 × 30%
综合评分 = Σ(单项得分 × 权重) ÷ Σ(有效项权重)          ← 四舍五入取整
```

| 维度 | 权重 |
|------|:----:|
| 睡眠 | 40% |
| 心率 | 30% |
| 呼吸 | 30% |

**均值得分分段表**（按顺序取首个命中区间；都不命中取下界分）

| 睡眠（小时） | 得分 | 心率（bpm） | 得分 | 呼吸（次/分） | 得分 |
|:----:|:----:|:----:|:----:|:----:|:----:|
| 7.5 – 8.5 | 100 | 60 – 80 | 100 | 12 – 18 | 100 |
| 7.0 – 7.5 / 8.5 – 9.0 | 92 | 80 – 90 | 92 | 18 – 20 | 92 |
| 6.5 – 7.0 / 9.0 – 9.5 | 84 | 90 – 100 | 84 | — | — |
| 6.0 – 6.5 / 9.5 – 10.0 | 74 | 55 – 60 / 100 – 105 | 85 | 10 – 12 / 20 – 22 | 85 |
| 5.0 – 6.0 / 10.0 – 11.0 | 62 | 50 – 55 / 105 – 110 | 70 | 9 – 10 / 22 – 24 | 70 |
| 4.0 – 5.0 / 11.0 – 12.0 | 48 | 其余 | 40 | 其余 | 40 |
| 其余（< 4 或 > 12） | 35 | | | | |

**等级划分**

| 综合评分 | 等级 |
|:----:|:----:|
| ≥ 90 | 优秀 |
| 80 – 89 | 良好 |
| 70 – 79 | 一般 |
| 60 – 69 | 较差 |
| < 60 | 差 |

**缺数据项的处理**：某项无数据时，该项按 **60 分**计入并标记 `"missing": true`
（避免数据不足时评分虚高）；三项全部无数据时 `score.total` 为 `null`、`score.level` 为 `暂无评分`。

#### 正常 / 异常参考区间（结论标签判定依据）

| 指标 | 正常 | 异常 |
|------|------|------|
| 睡眠时长 | 7-9 小时 | < 4 小时 或 > 12 小时（与 `abnormalSleep` 定义一致） |
| 夜间心率 | 60-100 bpm | < 50 bpm 或 > 110 bpm |
| 呼吸率 | 12-20 次/分 | < 9 次/分 或 > 24 次/分 |

介于正常与异常之间判定为「需关注」。此外：

- `abnormalSleep` 为 `0`（不足 4 小时）或 `1`（大于 12 小时）时，睡眠分析直接判为「异常」；
  为 `2`（长时间异常无人）时至少「需关注」。
- `breathe_alarm = 1` 时呼吸分析至少「需关注」；`bedoff_alarm = 1` 时睡眠分析至少「需关注」。
- 某维度完全没有数据时，该维度为「需关注」。

#### 响应

```json
{
  "projectId": "SIM_PROJECT_001",
  "personId": "8800000000000001",
  "deviceId": "8001",
  "periodType": "week",
  "periodStart": "2026-10-06",
  "periodEnd": "2026-10-12",
  "recordCount": 7,
  "validRecordCount": 7,
  "reportPeriod": "近一周（2026-10-06 至 2026-10-12）",
  "score": {
    "items": {
      "睡眠": { "score": 85.8, "weight": 0.4, "mean": 7.19, "compliance_rate": 0.7143, "status": "正常", "missing": false },
      "心率": { "score": 100.0, "weight": 0.3, "mean": 64.0, "compliance_rate": 1.0, "status": "正常", "missing": false },
      "呼吸": { "score": 100.0, "weight": 0.3, "mean": 15.86, "compliance_rate": 1.0, "status": "正常", "missing": false }
    },
    "weights": { "睡眠": 0.4, "心率": 0.3, "呼吸": 0.3 },
    "note": "综合评分 = 睡眠 40% + 心率 30% + 呼吸 30%；单项得分 = 均值得分 70% + 达标天数占比 30%；某项无数据时按 60 分计入并标记 missing",
    "total": 94,
    "level": "优秀"
  },
  "conclusion": {
    "综合评价": "正常",
    "睡眠分析": "正常",
    "心率分析": "正常",
    "呼吸分析": "正常"
  },
  "analysis": {
    "综合分析": {
      "综合评价": { "标题": "正常", "内容": "结合近一周（2026-10-06 至 2026-10-12）数据，您的整体健康状况正常。睡眠总时长约7.19小时……" },
      "体检建议": { "标题": "无", "内容": "建议定期体检，重点关注睡眠质量、心血管及呼吸系统等基础指标。" }
    },
    "睡眠分析": { "内容": "正常，近一周（2026-10-06 至 2026-10-12）睡眠总时长约7.19小时，处于成人推荐的7-9小时范围内……" },
    "心率呼吸分析": {
      "心率分析": { "标题": "正常", "内容": "正常，近一周（2026-10-06 至 2026-10-12）夜间平均心率约64.0bpm……" },
      "呼吸分析": { "标题": "正常", "内容": "正常，近一周（2026-10-06 至 2026-10-12）夜间平均呼吸约15.86次/分……" }
    }
  }
}
```

#### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `projectId` / `personId` / `deviceId` | string | 上报元数据原样回显；未提供时为空串 |
| `periodType` / `periodStart` / `periodEnd` | string | 周期字段原样回显 |
| `recordCount` | int | `records` 中字典元素的个数 |
| `validRecordCount` | int | 至少解析出睡眠时长 / 心率 / 呼吸之一的记录数 |
| `reportPeriod` | string | 由周期类型与起止日期生成的周期文案 |
| `score.total` | int \| null | **综合评分**（0-100）；三项全部无数据时为 `null` |
| `score.level` | string | 等级：优秀 / 良好 / 一般 / 较差 / 差 / 暂无评分 |
| `score.items` | object | 分项明细：`睡眠` / `心率` / `呼吸`，各含 `score` / `weight` / `mean` / `compliance_rate` / `status` / `missing` |
| `score.weights` | object | 各维度权重（和为 1） |
| `score.note` | string | 评分口径说明 |
| `conclusion` | object | 结论速览：四项标签，与 `analysis` 中对应项的 `标题` 一致 |
| `analysis` | object | 综合分析（综合评价 + 体检建议）、睡眠分析、心率呼吸分析（心率分析 + 呼吸分析） |
| `分析项.标题` | string | `正常` / `需关注` / `异常`（体检建议固定为 `无`） |
| `分析项.内容` | string | 综合评分由规则算出后注入 Prompt，故文案中的分数与实际分数一致 |

`睡眠分析` 仅含 `内容` 字段，其标签取 `conclusion.睡眠分析`。

LLM 调用失败或超时时自动降级为规则引擎，接口始终返回 200；`score` 与 `conclusion` **不受** LLM 影响。

#### 已知数据来源缺口（需极地项目方确认）

| 缺失项 | 影响 |
|------|------|
| REM / 快速眼动时长 | 传感器仅区分深睡、浅睡、清醒三态，睡眠结构分析不含 REM |
| 心率报警次数 | 规格仅有 `breathe_alarm`、`bedoff_alarm` 两种报警，无心率报警字段 |
| 入睡潜伏期 | 单条监测数据无法推算，已从输出中移除 |
| 心率最低 / 最高值 | 规格未上报逐日心率极值，仅输出夜间平均值 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEEPSEEK_API_KEY` | `sk-655f2d...` | DeepSeek API 密钥 |
| `DEFAULT_PROVIDER` | `deepseek` | LLM 后端：`deepseek` 或 `local` |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | DeepSeek 模型名称 |

## 测试示例

```bash
# 1. 阻塞式分析（传入数据库表数据）
curl -s -X POST http://localhost:8006/v1/health/analyze \
  -H "Content-Type: application/json" \
  -d '{"person":{...}, "wristband_records":[...], "sleep_records":[...]}' | python3 -m json.tool

# 2. 流式分析
curl -s -X POST http://localhost:8006/v1/health/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{"person":{...}, "wristband_records":[...], "sleep_records":[...]}'
```

## 性能参考

| 模式 | 模型 | 调用方式 | 耗时 |
|------|------|---------|------|
| LLM 并发 | `deepseek-v4-flash` | 8 路并行 | ~10s |
| LLM 顺序 | `deepseek-chat` | 8 次串行 | ~19s |
| 规则引擎 | — | 纯 Python | <10ms |
