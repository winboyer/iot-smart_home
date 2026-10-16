# 极地项目 · 睡眠健康数据分析接口 调用文档

> 本文档面向**调用方**（极地项目后端 / 前端 / 联调同学），讲清"怎么调、怎么传、怎么读返回值、出错怎么办"。
> 接口契约的完整字段定义见同目录 [`API.md`](./API.md) 第 3 节；本文档聚焦**调用方式**。

| 项目 | 内容 |
|------|------|
| 接口名称 | 极地项目睡眠健康数据分析 |
| 请求方法 | `POST` |
| 请求路径 | `/v1/health/polar/analyze` |
| 数据格式 | JSON（`Content-Type: application/json`，UTF-8） |
| 鉴权 | 无（内网部署；如对外暴露请由网关补充鉴权） |
| 幂等性 | 无状态纯计算，同一请求可重复调用 |
| 建议客户端超时 | **≥ 60 秒**（LLM 生成耗时约 10 秒；纯规则引擎 < 10 毫秒） |
| 文档版本 | **v2.0 · 2026-10-16**（多日周期 + 综合评分） |

> **v2.0 变更要点**：一个请求 = **一个统计周期的多日数据**（`records` 数组，每条为一天的监测）；响应新增 **综合评分 `score`**（总分 + 等级 + 三项分项）。旧版"单条 `metrics` / 顶层平铺"写法**已不再支持**。

---

## 一、接口总览

**一个请求 = 一个统计周期**，`records` 中每一条 = 该周期内**一天**的监测数据。
返回该周期的**综合评分**、**结论速览**与**完整分析**。

```
POST http://<服务地址>:8006/v1/health/polar/analyze
```

> **接口数量：极地项目只有一个接口，不按周期拆分。**
> 天 / 周 / 月 / 季 / 年等统计周期**共用这一个接口**，全部由请求字段 `periodType` 区分，
> `records` 承载该周期内的**逐日数据**。调用方想分析"今日"就传 1 天数据 + `periodType=day`，
> 想分析"近一月"就传 30 天数据 + `periodType=month`——**无需为不同周期新增或切换接口**。

分析范围固定为三项核心指标：**睡眠时长**、**夜间心率**、**呼吸**。输出结构：

```
响应
├── 上报元数据回显        projectId / personId / deviceId
├── 周期回显              periodType / periodStart / periodEnd
├── 记录条数              recordCount（收到条数） / validRecordCount（有效条数）
├── reportPeriod          报告周期文案，如 "近一周（2026-10-06 至 2026-10-12）"
├── score                 ★ 综合评分
│   ├── total             总分 0-100；三项全无数据时为 null
│   ├── level             等级：优秀 / 良好 / 一般 / 较差 / 差 / 暂无评分
│   ├── items             三项分项：睡眠 / 心率 / 呼吸
│   └── weights / note    权重说明与算法说明
├── conclusion            结论速览（4 个标签，取用最方便）
└── analysis              完整分析
    ├── 综合分析
    │   ├── 综合评价       {"标题": "正常|需关注|异常", "内容": "..."}
    │   └── 体检建议       {"标题": "无", "内容": "..."}
    ├── 睡眠分析           {"内容": "..."}（标签见 conclusion.睡眠分析）
    └── 心率呼吸分析
        ├── 心率分析       {"标题": "...", "内容": "..."}
        └── 呼吸分析       {"标题": "...", "内容": "..."}
```

---

## 二、快速开始（3 分钟跑通）

### 第 1 步：启动服务

```bash
# 方式一：使用项目启动脚本（需先配置 DEEPSEEK_API_KEY）
cd <项目根目录>
export DEEPSEEK_API_KEY=sk-xxxxxxxx
./start_service.sh

# 方式二：直接运行
cd <项目根目录>
DEEPSEEK_API_KEY=sk-xxxxxxxx python server/health_analysis_service.py

# 方式三：uvicorn 显式指定
cd <项目根目录>
uvicorn health_analysis_service:app --host 0.0.0.0 --port 8006 --app-dir server
```

> 未配置 `DEEPSEEK_API_KEY` 时服务**仍可正常启动并返回 200**，只是分析文本由规则引擎生成（见第七节）。

### 第 2 步：确认服务已就绪

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8006/openapi.json
# 期望输出：200
```

浏览器打开 `http://127.0.0.1:8006/docs` 可看到 Swagger 交互式文档。

### 第 3 步：发起一次调用（近一周）

```bash
curl -s -X POST http://127.0.0.1:8006/v1/health/polar/analyze \
  -H "Content-Type: application/json" \
  -d '{
        "projectId": "SIM_PROJECT_001",
        "personId": "8800000000000001",
        "deviceId": "8001",
        "periodType": "week",
        "periodStart": "2026-10-06",
        "periodEnd": "2026-10-12",
        "records": [
          { "collectTime": "2026-10-06 06:28:00",
            "metrics": [
              {"metricName": "sleepHours",     "metricValue": "7.10"},
              {"metricName": "heartRateValue1","metricValue": "62"},
              {"metricName": "breathValue1",   "metricValue": "15"}
            ]},
          { "collectTime": "2026-10-07 06:35:00",
            "metrics": [
              {"metricName": "sleepHours",     "metricValue": "6.80"},
              {"metricName": "heartRateValue1","metricValue": "66"},
              {"metricName": "breathValue1",   "metricValue": "17"}
            ]}
        ]
      }'
```

> 完整 7 天样例见 `test/polar_week_normal_sample.json`，可直接 `curl -d @test/polar_week_normal_sample.json`。

---

## 三、请求说明

### 3.1 请求行与请求头

```http
POST /v1/health/polar/analyze HTTP/1.1
Host: <服务地址>:8006
Content-Type: application/json
Content-Length: <自动>
```

- 目前**无需任何鉴权头**。
- `Content-Type` **必须为 `application/json`**，否则返回 422。
- 请使用 **UTF-8** 编码，中文内容可直接传入。

### 3.2 请求体结构

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
        { "metricName": "heartRateValue1", "metricValue": "62" },
        { "metricName": "breathValue1", "metricValue": "15" },
        { "metricName": "movementSigns1", "metricValue": "6" }
      ]
    }
    // ... 周期内其余各天，每天一个元素
  ]
}
```

**要点**：
- `records` 是**周期内逐日数据的数组**，顺序不限（服务端按值聚合，不依赖顺序）。
- 每条 record 内部结构与"单日监测"完全一致：`collectTime` + `metrics[{metricName, metricValue}]`。
- 字段同时兼容 **camelCase** 与 **snake_case**（如 `projectId` / `project_id` 等价）。

### 3.3 请求字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `projectId` | string | 否 | 项目编码，原样回显；缺省回显空串 |
| `personId` | string | 否 | 人员编码，原样回显；缺省时可回退取 `person.id` / `person.id_card` |
| `deviceId` | string | 否 | 设备编码，原样回显；缺省时可回退取 `person.device_id` |
| `periodType` | string | 否 | 统计周期类型，决定 `reportPeriod` 前缀文案（见下表） |
| `periodStart` | string | 否 | 统计起始日期 `YYYY-MM-DD`（只取前 10 字符） |
| `periodEnd` | string | 否 | 统计结束日期 `YYYY-MM-DD` |
| `records` | array | 否 | **逐日监测记录数组**，元素为 `{collectTime, metrics}` |
| `person` | object | 否 | 人员基础信息（`id`/`name`/`gender`/`age`），**仅用于个性化文案** |

**`periodType` → 文案对照**（匹配时忽略大小写与分隔符）：

| 上报写法（等价） | 文案 |
|---|---|
| `day` / `daily` | 今日 |
| `week` / `weekly` | 近一周 |
| `month` / `monthly` | 近一个月 |
| `quarter` / `threeMonth` / `threeMonths` | 近三个月 |
| `year` / `yearly` | 近一年 |
| 其他未识别值 | 空（仅用起止日期） |

**`reportPeriod` 生成规则**（服务端自动完成）：

| 情况 | `reportPeriod` 示例 |
|---|---|
| 有 `periodType` + 有起止日期 | `近一周（2026-10-06 至 2026-10-12）` |
| 有 `periodType`，起止为空或同一天 | `今日（2026-10-12）` |
| 无 `periodType`，仅有日期 | `2026-10-06 至 2026-10-12` |
| 都没有 | `本次监测` |

> 注意：`reportPeriod` 文案**不含"数据"二字**，降级模板会自行拼接成"结合{周期}数据"。

### 3.4 `metrics` 指标名对照表

`metricName` 做**归一化匹配**：忽略大小写与分隔符（`-`/`_`/空格），并自动**剥离尾部采集序号**。

| 上报写法（等价） | 归一化后 | 含义 | 单位 | 参与分析 |
|---|---|---|---|---|
| `sleepHours` / `sleep_hours` / `totalSleepHours` | `sleepHours` | 睡眠总时长 | 小时 | 睡眠分析（**优先级最高**） |
| `heartRateValue1` / `heart_rate_value_2` | `heartRateValue` | 夜间心率 | bpm | 心率分析 |
| `breathValue1` / `breathingRateValue` | `breathValue` | 夜间呼吸 | 次/分 | 呼吸分析 |
| `movementSigns1` | `movementSigns` | 运动体征（体动幅度） | — | 辅助 |
| `deepSleepDuration` / `deep_sleep_duration` / `deepSleepHours` | `deepSleepDuration` | 深睡时长 | 分钟 / 小时 | 睡眠结构 |
| `lightSleepDuration` / `light_sleep_duration` / `lightSleepHours` | `lightSleepDuration` | 浅睡时长 | 分钟 / 小时 | 睡眠结构 |
| `awakeDuration` / `awake_duration` / `awakeHours` | `awakeDuration` | 清醒时长 | 分钟 / 小时 | 睡眠结构 |
| `sleepScore` / `sleep_score` | `sleepScore` | 睡眠评分 | 0-100 | 辅助 |
| `sleepStatus` / `sleep_status` | `sleepStatus` | 睡眠状态 | `0`深睡 `1`浅睡 `2`清醒 `3`无 | 辅助 |
| `heartbeat` | `heartbeat` | 设备心跳 | `0`异常 `1`正常 | 辅助 |
| `someoneExists` / `someone_exists` | `someoneExists` | 人体存在 | `0`无人 `1`有人 | 辅助 |
| `getIntoBed` / `get_into_bed` / `inBed` | `getIntoBed` | 入床离床 | `0`离床 `1`入床 | 辅助 |
| `abnormalSleep` / `abnormal_sleep` | `abnormalSleep` | 设备睡眠异常判定 | `0`不足4h `1`大于12h `2`长时间无人 `3`无 | 睡眠分析（**直接判异常**） |
| `breathe_alarm` / `breatheAlarm` / `breathingAlarm` | `breathe_alarm` | 呼吸报警 | `0`正常 `1`异常 | 呼吸分析（至少"需关注"） |
| `bedoff_alarm` / `bedoffAlarm` / `outBedAlarm` | `bedoff_alarm` | 离床报警 | `0`正常 `1`异常 | 睡眠分析（至少"需关注"） |
| `sleepComprehensiveStatus` / `sleep_comprehensive_status` | `sleepComprehensiveStatus` | 睡眠综合状态 | 8 字节数组 | 见下方位序 |
| `sleepQualityAnalysis` / `sleep_quality_analysis` | `sleepQualityAnalysis` | 睡眠质量分析 | 12 字节数组 | 见下方位序 |

**同一条 record 内同一指标多次上报的处理**：

- **数值型**（睡眠时长、心率、呼吸、体动、分项时长）→ **取算术平均**。
  例：`heartRateValue1=60`、`heartRateValue2=70` → 按 **65** 参与该天的分析。
- **状态 / 事件型**（睡眠状态、报警、字节数组等）→ **取最后一次上报值**。

**`metricValue` 类型兼容**：字符串数字（`"7.30"`）与数字（`7.30`）等价；布尔类支持 `0/1`、`true/false`、`正常/异常` 等写法。无法解析的值会被**忽略**，不报错。

**未识别的 `metricName`**：直接忽略，**不会返回 422**。

### 3.5 字节数组位序

以 `metricName` 上报数组时，支持三种写法：

```text
"metricValue": [1, 1, 15, 61, 23, 18, 42, 2]        // 整型数组（推荐）
"metricValue": "01010F3D17122A02"                   // 十六进制串
"metricValue": "1,1,15,61,23,18,42,2"               // 逗号/空格分隔串
```

- **`sleepComprehensiveStatus`（8 字节）**
  `[存在于否, 睡眠状态, 平均呼吸, 平均心跳, 翻身次数, 大幅度体动占比, 小幅度体动占比, 呼吸暂停次数]`
- **`sleepQualityAnalysis`（12 字节）**
  `[睡眠质量评分, 睡眠总时长高字节, 睡眠总时长低字节, 清醒时长占比, 浅睡时长占比, 深睡时长占比, 离床时长, 离床次数, 翻身次数, 平均呼吸, 平均心跳, 呼吸暂停次数]`

> 「睡眠总时长」占 2 字节，按**大端序**还原，单位**分钟**。

### 3.6 服务端的取值优先级与换算（自动完成，调用方无需预处理）

| 分析指标 | 取值来源（按优先级从高到低） | 换算 |
|---|---|---|
| 睡眠总时长 | `sleepHours` → `sleepQualityAnalysis` 第 2、3 字节（大端） → 深睡 + 浅睡 | 统一为小时 |
| 深睡 / 浅睡时长 | `deepSleepDuration` / `lightSleepDuration` | 分钟 ÷ 60 |
| 夜间平均心率 | `sleepComprehensiveStatus[3]` → `sleepQualityAnalysis[10]` → `heartRateValue` | 直接取（bpm） |
| 夜间平均呼吸 | `sleepComprehensiveStatus[2]` → `sleepQualityAnalysis[9]` → `breathValue` | 直接取（次/分） |
| 呼吸暂停次数 | `sleepComprehensiveStatus[7]` → `sleepQualityAnalysis[11]` | 直接取 |
| 离床次数 / 时长 | `sleepQualityAnalysis[7]` / `[6]` | 直接取 |
| 翻身次数 | `sleepComprehensiveStatus[4]` → `sleepQualityAnalysis[8]` | 直接取 |

> **调用方只需上报原始值即可**，无需自行把分钟换成小时、也无需拼装总时长、更无需自己算平均。

### 3.7 多日汇总口径（评分与文案的统一口径）

服务端先把 `records` 汇总为**周期统计量**，再据此算分、生成文案：

| 指标 | 周期统计量 | 说明 |
|---|---|---|
| 睡眠总时长 | 逐日睡眠时长的**均值**、最小/最大值、逐日明细 | 单日均值即当日 `sleepHours`（或由分项求和） |
| 睡眠达标率 | 落在 **7~9 小时**的天数占比 | 用于评分 |
| 深睡 / 浅睡 / 清醒占比 | 周期均值 ÷ 周期总时长 × 100（**百分数**） | 如 `24.9` 表示 24.9% |
| 夜间平均心率 | 逐日心率的**均值**、最小/最大值 | 达标区间 **60~100 bpm** |
| 夜间平均呼吸 | 逐日呼吸的**均值**、最小/最大值 | 达标区间 **12~20 次/分** |
| 呼吸暂停 | 逐日呼吸暂停次数均值 / 合计 | — |

> 「有效记录」= 该天至少能解析出**睡眠时长、夜间心率、呼吸**三者之一。`validRecordCount` 即有效天数。

---

## 四、综合评分（`score`）

### 4.1 算法（确定性，不随大模型变化）

```
单项得分 = 均值得分 × 70% + 达标天数占比 × 100 × 30%
综合评分 = Σ(单项得分 × 权重) ÷ Σ(有效项权重)
权重：睡眠 0.4 / 心率 0.3 / 呼吸 0.3
```

- **均值得分**：把周期均值代入分段表（见 4.2），越靠近理想值分越高。
- **达标天数占比**：周期内落在正常参考区间的天数比例（0~1）。
- **某项无数据**：按 **60 分**计入权重，并在该项标 `"missing": true`——避免"只有一项数据"时评分虚高。
- **三项全无数据**：`total = null`、`level = "暂无评分"`——避免"没有数据"被误读成"健康状况较差"。

### 4.2 均值得分分段表

**睡眠总时长（小时）**

| 区间 | 分数 |
|---|---|
| 7.5 ~ 8.5 | 100 |
| 7.0 ~ 7.5 / 8.5 ~ 9.0 | 92 |
| 6.5 ~ 7.0 / 9.0 ~ 9.5 | 84 |
| 6.0 ~ 6.5 / 9.5 ~ 10.0 | 74 |
| 5.0 ~ 6.0 / 10.0 ~ 11.0 | 62 |
| 4.0 ~ 5.0 / 11.0 ~ 12.0 | 48 |
| 其余（含 < 4 或 > 12） | 35 |

**夜间平均心率（bpm）**

| 区间 | 分数 |
|---|---|
| 60 ~ 80 | 100 |
| 80 ~ 90 | 92 |
| 90 ~ 100 | 84 |
| 55 ~ 60 / 100 ~ 105 | 85 |
| 50 ~ 55 / 105 ~ 110 | 70 |
| 其余 | 40 |

**夜间平均呼吸（次/分）**

| 区间 | 分数 |
|---|---|
| 12 ~ 18 | 100 |
| 18 ~ 20 | 92 |
| 10 ~ 12 / 20 ~ 22 | 85 |
| 9 ~ 10 / 22 ~ 24 | 70 |
| 其余 | 40 |

> 区间按**顺序取首个命中**（左闭右闭）。

### 4.3 等级划分

| 总分 | 等级 |
|---|---|
| ≥ 90 | 优秀 |
| ≥ 80 | 良好 |
| ≥ 70 | 一般 |
| ≥ 60 | 较差 |
| < 60 | 差 |
| 三项全无数据 | 暂无评分（`total = null`） |

### 4.4 `score` 字段结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `score.total` | int \| null | 综合评分 0-100；三项全无数据时为 `null` |
| `score.level` | string | 等级：优秀/良好/一般/较差/差/暂无评分 |
| `score.items` | object | 三项分项，键为 **`睡眠` / `心率` / `呼吸`** |
| `score.items.<项>.score` | float | 该项单项得分（0-100） |
| `score.items.<项>.weight` | float | 该项权重 |
| `score.items.<项>.mean` | float \| null | 该项周期均值；无数据为 `null` |
| `score.items.<项>.compliance_rate` | float \| null | 达标天数占比（0~1）；无数据为 `null` |
| `score.items.<项>.status` | string | 该项结论标签（正常/需关注/异常） |
| `score.items.<项>.missing` | bool | 该项是否**无数据**（按 60 分计入） |
| `score.weights` | object | 权重表 `{睡眠:0.4, 心率:0.3, 呼吸:0.3}` |
| `score.note` | string | 算法说明文字 |

### 4.5 评分详算示例（正常周）

输入为 `test/polar_week_normal_sample.json`（7 天）。周期统计：

| 项 | 周期均值 | 达标率 | 均值得分 | 单项得分 = 均值×0.7 + 达标率×100×0.3 |
|---|---|---|---|---|
| 睡眠 | 7.19 h | 5/7 = 0.7143 | 92 | 92×0.7 + 71.43×0.3 = **85.8** |
| 心率 | 64.0 bpm | 7/7 = 1.0 | 100 | 100×0.7 + 100×0.3 = **100.0** |
| 呼吸 | 15.86 次/分 | 7/7 = 1.0 | 100 | 100×0.7 + 100×0.3 = **100.0** |

```
总分 = (85.8×0.4 + 100×0.3 + 100×0.3) ÷ (0.4+0.3+0.3)
     = (34.32 + 30 + 30) ÷ 1.0
     = 94.32 → 四舍五入 94  → 等级「优秀」
```

**异常周**（`polar_week_abnormal_sample.json`）示例：

| 项 | 均值 | 达标率 | 单项得分 |
|---|---|---|---|
| 睡眠 | 3.83 h | 0 | 24.5 |
| 心率 | 112.57 bpm | 0 | 28.0 |
| 呼吸 | 24.29 次/分 | 0 | 28.0 |

```
总分 = 24.5×0.4 + 28×0.3 + 28×0.3 = 9.8 + 8.4 + 8.4 = 26.6 → 27 → 等级「差」
```

---

## 五、响应说明

### 5.1 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `projectId` / `personId` / `deviceId` | string | 上报元数据**原样回显**；未提供时为空串 |
| `periodType` / `periodStart` / `periodEnd` | string | 周期字段**原样回显** |
| `recordCount` | int | 收到的 `records` 元素个数 |
| `validRecordCount` | int | 有效条数（至少解析出睡眠/心率/呼吸之一） |
| `reportPeriod` | string | 报告周期文案（见 3.3） |
| `score` | object | **综合评分**，结构见 4.4 |
| `conclusion` | object | **结论速览**，固定 4 项：`综合评价` / `睡眠分析` / `心率分析` / `呼吸分析` |
| `analysis` | object | **完整分析**，含 `综合分析` / `睡眠分析` / `心率呼吸分析` |
| `analysis.综合分析.综合评价` | object | `{标题, 内容}`，`标题` ∈ `正常` / `需关注` / `异常` |
| `analysis.综合分析.体检建议` | object | `{标题, 内容}`，`标题` 固定为 `"无"` |
| `analysis.睡眠分析` | object | **仅含 `内容` 字段**；其结论标签取 `conclusion.睡眠分析` |
| `analysis.心率呼吸分析.心率分析` | object | `{标题, 内容}` |
| `analysis.心率呼吸分析.呼吸分析` | object | `{标题, 内容}` |

> 标签口径一致性：`conclusion` 中的 4 个标签、`analysis` 中对应项的 `标题`、`score.items.<项>.status` **三者必然一致**。
> **评分与结论标签由规则引擎确定性地算出**，并注入 Prompt 保证文案里的分数与 `score` 一致。

### 5.2 响应示例 · 正常一周

请求：`test/polar_week_normal_sample.json`（7 天，睡眠 6.4~7.9 h、心率 60~70 bpm、呼吸 14~18 次/分）

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
      "综合评价": {
        "标题": "正常",
        "内容": "结合近一周（2026-10-06 至 2026-10-12）数据，您的整体健康状况正常。睡眠总时长约7.19小时，夜间平均心率约64.0bpm，平均呼吸约15.86次/分。本报告聚焦睡眠时长、心率与呼吸三项核心指标。"
      },
      "体检建议": {
        "标题": "无",
        "内容": "建议定期体检，重点关注睡眠质量、心血管及呼吸系统等基础指标。"
      }
    },
    "睡眠分析": {
      "内容": "正常，近一周（2026-10-06 至 2026-10-12）睡眠总时长约7.19小时，处于成人推荐的7-9小时范围内，深睡约1.79小时（占比24.9%），浅睡约5.4小时，清醒约0.43小时。睡眠时长总体达标，建议保持规律作息。"
    },
    "心率呼吸分析": {
      "心率分析": {
        "标题": "正常",
        "内容": "正常，近一周（2026-10-06 至 2026-10-12）夜间平均心率约64.0bpm，处于正常参考范围（60-100bpm）。建议保持规律作息、适度有氧运动。"
      },
      "呼吸分析": {
        "标题": "正常",
        "内容": "正常，近一周（2026-10-06 至 2026-10-12）夜间平均呼吸约15.86次/分，处于正常参考范围（12-20次/分）。呼吸节律平稳，建议继续保持。"
      }
    }
  }
}
```

### 5.3 响应示例 · 异常一周

请求：`test/polar_week_abnormal_sample.json`（睡眠约 3.8 h、心率约 112.6 bpm、呼吸约 24.3 次/分，含呼吸报警）

```json
{
  "projectId": "SIM_PROJECT_001",
  "personId": "8800000000000002",
  "deviceId": "8002",
  "periodType": "week",
  "periodStart": "2026-10-06",
  "periodEnd": "2026-10-12",
  "recordCount": 7,
  "validRecordCount": 7,
  "reportPeriod": "近一周（2026-10-06 至 2026-10-12）",
  "score": {
    "items": {
      "睡眠": { "score": 24.5, "weight": 0.4, "mean": 3.83, "compliance_rate": 0.0, "status": "异常", "missing": false },
      "心率": { "score": 28.0, "weight": 0.3, "mean": 112.57, "compliance_rate": 0.0, "status": "异常", "missing": false },
      "呼吸": { "score": 28.0, "weight": 0.3, "mean": 24.29, "compliance_rate": 0.0, "status": "异常", "missing": false }
    },
    "weights": { "睡眠": 0.4, "心率": 0.3, "呼吸": 0.3 },
    "note": "综合评分 = 睡眠 40% + 心率 30% + 呼吸 30%；单项得分 = 均值得分 70% + 达标天数占比 30%；某项无数据时按 60 分计入并标记 missing",
    "total": 27,
    "level": "差"
  },
  "conclusion": {
    "综合评价": "异常",
    "睡眠分析": "异常",
    "心率分析": "异常",
    "呼吸分析": "异常"
  },
  "analysis": {
    "综合分析": {
      "综合评价": {
        "标题": "异常",
        "内容": "结合近一周（2026-10-06 至 2026-10-12）数据，您的整体健康状况异常。睡眠总时长约3.83小时，夜间平均心率约112.57bpm，平均呼吸约24.29次/分，设备判定睡眠时长不足4小时。本报告聚焦睡眠时长、心率与呼吸三项核心指标。"
      },
      "体检建议": { "标题": "无", "内容": "建议定期体检，重点关注睡眠质量、心血管及呼吸系统等基础指标。" }
    },
    "睡眠分析": {
      "内容": "异常，近一周（2026-10-06 至 2026-10-12）睡眠总时长约3.83小时，低于成人推荐的7-9小时，深睡约0.81小时（占比21.15%），浅睡约3.01小时，清醒约1.04小时，设备判定睡眠时长不足4小时。睡眠时长明显偏离正常范围，建议关注生活习惯并咨询医生。"
    },
    "心率呼吸分析": {
      "心率分析": { "标题": "异常", "内容": "异常，近一周（2026-10-06 至 2026-10-12）夜间平均心率约112.57bpm，偏离正常参考范围（60-100bpm）。建议持续监测，必要时咨询医生。" },
      "呼吸分析": { "标题": "异常", "内容": "异常，近一周（2026-10-06 至 2026-10-12）夜间平均呼吸约24.29次/分，偏离正常参考范围（12-20次/分）。设备上报呼吸异常报警，建议留意。建议持续监测，必要时咨询医生。" }
    }
  }
}
```

### 5.4 响应示例 · 仅睡眠数据（缺项 missing）

请求：仅上报 1 天、且只有 `sleepHours`。

```json
"recordCount": 1,
"validRecordCount": 1,
"reportPeriod": "今日（2026-10-12）",
"score": {
  "items": {
    "睡眠": { "score": 94.4, "weight": 0.4, "mean": 7.4, "compliance_rate": 1.0, "status": "正常", "missing": false },
    "心率": { "score": 60.0, "weight": 0.3, "mean": null, "compliance_rate": null, "status": "需关注", "missing": true },
    "呼吸": { "score": 60.0, "weight": 0.3, "mean": null, "compliance_rate": null, "status": "需关注", "missing": true }
  },
  "total": 74,
  "level": "一般"
},
"conclusion": { "综合评价": "需关注", "睡眠分析": "正常", "心率分析": "需关注", "呼吸分析": "需关注" }
```

> 心率、呼吸因**无数据**按 60 分计入 → 总分 74「一般」。这正是"数据不足不被误判为优秀"的设计：**分项缺数据时 `missing=true`，前端应提示"该项数据缺失"而非直接展示分数。**

### 5.5 响应示例 · 周期内全无数据

```json
"recordCount": 0,
"validRecordCount": 0,
"reportPeriod": "近一周（2026-10-06 至 2026-10-12）",
"score": {
  "items": {
    "睡眠": { "score": 60.0, "weight": 0.4, "mean": null, "compliance_rate": null, "status": "需关注", "missing": true },
    "心率": { "score": 60.0, "weight": 0.3, "mean": null, "compliance_rate": null, "status": "需关注", "missing": true },
    "呼吸": { "score": 60.0, "weight": 0.3, "mean": null, "compliance_rate": null, "status": "需关注", "missing": true }
  },
  "total": null,
  "level": "暂无评分"
},
"conclusion": { "综合评价": "需关注", "睡眠分析": "需关注", "心率分析": "需关注", "呼吸分析": "需关注" }
```

> `level = "暂无评分"` 且 `total = null`，前端应展示"暂无评分"，**不要**当成 0 分或差评。

### 5.6 结论标签的判定区间

| 指标 | 正常 | 介于两者之间 | 异常 |
|------|------|------|------|
| 睡眠时长（均值） | 7 – 9 小时 | 其余情况 | < 4 小时 或 > 12 小时 |
| 夜间心率（均值） | 60 – 100 bpm | 其余情况 | < 50 bpm 或 > 110 bpm |
| 呼吸率（均值） | 12 – 20 次/分 | 其余情况 | < 9 次/分 或 > 24 次/分 |

附加规则（**按整个周期扫描，命中任意一天即生效**）：

- 周期内任一天 `abnormalSleep = 0`（不足 4 小时）或 `1`（大于 12 小时）→ **睡眠分析直接判「异常」**；`= 2`（长时间无人）→ 至少「需关注」。
- 周期内任一天 `breathe_alarm = 1` → 呼吸分析**至少「需关注」**；`bedoff_alarm = 1` → 睡眠分析**至少「需关注」**。
- **完全没有睡眠数据**时，`综合评价` 为「需关注」，其余项按有无数据各自判定。

> 注意：**结论标签**判定区间（7-9 h 等）与**均值得分**分段表（4.2，7.5-8.5 h 满分）口径不同——前者是"是否正常"的二元判断，后者是"有多好"的连续打分，两者独立。

---

## 六、调用示例

### 6.1 cURL

```bash
curl -s -X POST http://127.0.0.1:8006/v1/health/polar/analyze \
  -H "Content-Type: application/json" \
  -d @test/polar_week_normal_sample.json | python3 -m json.tool
```

### 6.2 Python（requests）

```python
import requests

URL = "http://127.0.0.1:8006/v1/health/polar/analyze"

payload = {
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
                {"metricName": "sleepHours",      "metricValue": "7.10"},
                {"metricName": "heartRateValue1", "metricValue": "62"},
                {"metricName": "breathValue1",    "metricValue": "15"},
            ],
        },
        {
            "collectTime": "2026-10-07 06:35:00",
            "metrics": [
                {"metricName": "sleepHours",      "metricValue": "6.80"},
                {"metricName": "heartRateValue1", "metricValue": "66"},
                {"metricName": "breathValue1",    "metricValue": "17"},
            ],
        },
    ],
}

resp = requests.post(URL, json=payload, timeout=60)   # 超时务必 >= 60s
resp.raise_for_status()
data = resp.json()

# 综合评分
print(data["score"]["total"], data["score"]["level"])          # 94 优秀

# 分项评分
for name, item in data["score"]["items"].items():
    if item["missing"]:
        print(name, "无数据")
    else:
        print(name, item["score"], item["status"], "达标率", item["compliance_rate"])

# 只取标签
print(data["conclusion"])

# 取完整分析文本
print(data["analysis"]["综合分析"]["综合评价"]["内容"])
print(data["analysis"]["睡眠分析"]["内容"])
print(data["analysis"]["心率呼吸分析"]["心率分析"]["内容"])
print(data["analysis"]["心率呼吸分析"]["呼吸分析"]["内容"])
```

### 6.3 Java（OkHttp）

```java
import okhttp3.*;
import java.io.IOException;

public class PolarApiDemo {
    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");

    public static void main(String[] args) throws IOException {
        OkHttpClient client = new OkHttpClient.Builder()
                .connectTimeout(10, java.util.concurrent.TimeUnit.SECONDS)
                .readTimeout(70, java.util.concurrent.TimeUnit.SECONDS)   // 必须 >= 60s
                .build();

        String body = "{"
                + "\"projectId\":\"SIM_PROJECT_001\","
                + "\"personId\":\"8800000000000001\","
                + "\"deviceId\":\"8001\","
                + "\"periodType\":\"week\","
                + "\"periodStart\":\"2026-10-06\","
                + "\"periodEnd\":\"2026-10-12\","
                + "\"records\":["
                +   "{\"collectTime\":\"2026-10-06 06:28:00\",\"metrics\":["
                +       "{\"metricName\":\"sleepHours\",\"metricValue\":\"7.10\"},"
                +       "{\"metricName\":\"heartRateValue1\",\"metricValue\":\"62\"},"
                +       "{\"metricName\":\"breathValue1\",\"metricValue\":\"15\"}]},"
                +   "{\"collectTime\":\"2026-10-07 06:35:00\",\"metrics\":["
                +       "{\"metricName\":\"sleepHours\",\"metricValue\":\"6.80\"},"
                +       "{\"metricName\":\"heartRateValue1\",\"metricValue\":\"66\"},"
                +       "{\"metricName\":\"breathValue1\",\"metricValue\":\"17\"}]}"
                + "]}";

        Request request = new Request.Builder()
                .url("http://127.0.0.1:8006/v1/health/polar/analyze")
                .post(RequestBody.create(body, JSON))
                .build();

        try (Response response = client.newCall(request).execute()) {
            System.out.println(response.code());
            System.out.println(response.body().string());
        }
    }
}
```

### 6.4 JavaScript（fetch）

```javascript
const payload = {
  projectId: "SIM_PROJECT_001",
  personId: "8800000000000001",
  deviceId: "8001",
  periodType: "week",
  periodStart: "2026-10-06",
  periodEnd: "2026-10-12",
  records: [
    { collectTime: "2026-10-06 06:28:00", metrics: [
        { metricName: "sleepHours",      metricValue: "7.10" },
        { metricName: "heartRateValue1", metricValue: "62" },
        { metricName: "breathValue1",    metricValue: "15" } ] },
    { collectTime: "2026-10-07 06:35:00", metrics: [
        { metricName: "sleepHours",      metricValue: "6.80" },
        { metricName: "heartRateValue1", metricValue: "66" },
        { metricName: "breathValue1",    metricValue: "17" } ] },
  ],
};

const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), 60000);   // 60s 超时

const resp = await fetch("http://127.0.0.1:8006/v1/health/polar/analyze", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
  signal: controller.signal,
});
clearTimeout(timer);

if (resp.ok) {
  const data = await resp.json();
  console.log(data.score.total, data.score.level);     // 综合评分
  console.log(data.conclusion);                        // 结论速览
  console.log(data.analysis["睡眠分析"].内容);          // 睡眠分析文本
} else {
  console.error(resp.status, await resp.text());
}
```

### 6.5 Postman / Apifox

- Method：`POST`
- URL：`http://<服务地址>:8006/v1/health/polar/analyze`
- Headers：`Content-Type: application/json`
- Body：选 `raw` → `JSON`，粘贴第三节的请求体。

---

## 七、错误码与容错行为

| HTTP 状态码 | 触发场景 | 响应体 |
|---|---|---|
| **200** | 正常返回（**包括 LLM 不可用时的降级输出**） | 完整分析 JSON |
| **422** | ① `Content-Type` 缺失或不含 `application/json`；② 请求体不是合法 JSON；③ `records` 不是数组（传了字符串/对象） | `{"detail": [...]}`，FastAPI 标准校验错误 |
| **500** | 分析过程出现未预期异常 | `{"detail": "分析失败: <原因>"}` |

**容错设计（以下情况均返回 200，不报错）**：

| 输入情况 | 行为 |
|---|---|
| 请求体为 `{}` 或字段全缺 | 返回完整结构，`score.total=null / level=暂无评分`，结论降级为「需关注」 |
| `records` 为 `null` 或缺失 | 视为空周期，`recordCount=0`，其余同上 |
| `records` 元素不是对象（如字符串/数字） | 跳过该元素，不计入 `recordCount` |
| `records` 元素缺 `metrics` | 该天解析为无数据，仍计入 `recordCount`，不计入 `validRecordCount` |
| `recordCount > validRecordCount` | 说明有条目未解析出任何核心指标，属正常容错 |
| 未识别的 `metricName` | 忽略该项，其余指标照常分析 |
| `metricValue` 无法解析为数字（如 `"abc"`） | 忽略该值 |
| `metricName` 为乱码 / 字节数组长度不足 | 字节数组右侧补 0；无法解析则忽略该指标 |
| `periodType` 为未知值（如 `"fortnight"`） | `reportPeriod` 退化为仅用起止日期，不报错 |
| `periodStart` / `periodEnd` 日期格式非法 | 忽略该日期，`reportPeriod` 按剩余信息生成或退化为「本次监测」 |
| 未配置 `DEEPSEEK_API_KEY` | 分析文本由规则引擎生成，接口仍返回 200 |

> **422 的边界很窄**：只有「`Content-Type` 不合法 / 请求体非法 JSON / `records` 不是数组」这三种才 422；其余脏数据一律 200。

---

## 八、对接注意事项

### 8.1 关于 LLM 与降级

服务优先调用大模型生成分析文本；当模型不可用、超时或返回空时，**自动降级**为内置规则引擎文本。两种模式下：

- 响应**结构完全一致**；`标题`（结论标签）与 **`score`（评分）** 均由规则引擎判定，**不随 LLM 变化**；
- 只有 `内容` 的文字表述可能不同（LLM 为自然语言生成，规则引擎为模板文本）；
- 评分已注入 Prompt，**文案里出现的分数与 `score` 一致**。

因此前端**不要**用 `内容` 文本做逻辑判断，请统一使用 `标题`、`conclusion` 或 `score`。

### 8.2 关于耗时

| 模式 | 耗时参考 |
|---|---|
| 规则引擎（降级） | < 10 毫秒 |
| LLM 并发生成 4 段文本 | 约 10 秒（上游延迟高时可能更长） |

**客户端超时必须设置为 60 秒以上**，否则 LLM 正常工作时会被误判为超时。

### 8.3 关于单位（需要与极地方确认的隐含契约）

| 指标 | 本文档约定 |
|---|---|
| `sleepHours` | **小时**（如 `"7.30"` = 7.3 小时） |
| `heartRateValue` | **bpm**（直接取值，非放大整数） |
| `breathValue` | **次/分**（直接取值） |
| `*Duration` 系列 | **分钟** |
| `*Hours` 系列 | **小时** |
| 字节数组中的总时长 | **分钟**（大端，2 字节） |

若实际上报量纲与此不同，需调整服务端的指标映射表，否则结论与评分都会偏差。

### 8.4 关于数据缺口

以下内容在《睡眠传感器数据》规格中**没有对应字段**，接口**不输出**：

- REM / 快速眼动时长（传感器仅区分深睡、浅睡、清醒三态）
- 心率报警次数（规格只有呼吸报警、离床报警）
- 入睡潜伏期
- 心率最低 / 最高值（`score` 只用均值；逐日极值仅在内部汇总，不出现在响应）

### 8.5 常见问题

**Q1：一个请求要传多久的数据？天/周/月要用不同接口吗？**
**不需要不同接口，就这一个。** 一个请求 = **一个统计周期**，周期多长由你决定（`day` / `week` / `month` / `quarter` / `year`），周期内**逐日**放进 `records`。想分析今日传 1 天、想分析近一月传 30 天，`periodType` 决定报告文案与调用语义。周期越长，评分与文案越有代表性；建议与前端"今日 / 近一周 / 近一月"切换一致。

**Q2：综合评分是怎么算的？会随大模型变化吗？**
不会。`单项得分 = 均值得分×70% + 达标天数占比×100×30%`，`综合 = Σ(单项×权重)`，权重 40/30/30，全部由规则引擎确定性计算（详见第四节）。同一份数据永远得到同一个分数。

**Q3：为什么只有睡眠数据时分数只有 74？**
因为心率、呼吸**无数据**，按 60 分计入权重并标 `missing=true`。这是刻意设计——避免"数据不全却显示优秀"。前端应据 `missing` 提示"该项数据缺失"。

**Q4：`total` 为 `null` 是什么意思？**
周期内三项**全无数据**，`level="暂无评分"`。前端应展示"暂无评分"，不要当成 0 分。

**Q5：响应里没有 `睡眠分析.标题`？**
是设计如此。`睡眠分析` 只返回 `内容`；其标签请读 `conclusion.睡眠分析`（与 `score.items.睡眠.status` 一致）。

**Q6：`体检建议.标题` 为什么是「无」？**
当前版本体检建议不参与等级判定，标题固定为 `"无"`，内容为建议正文。

**Q7：接口幂等吗？可以重试吗？**
接口无状态、不落库，**可安全重试**。仅当使用 LLM 时，`内容` 的措辞可能存在细微差异；`标题`、`score` 与结构不变。

**Q8：能并发调用吗？**
可以，服务端每次请求内部使用 4 路线程并发调用 LLM。建议调用方并发控制在合理范围（如 ≤ 10 QPS），避免上游限流。

---

## 九、对接自测清单

上线前请逐项确认：

- [ ] `Content-Type: application/json` 已正确设置
- [ ] 请求为**周期格式**：`records` 为数组，每元素含 `collectTime` + `metrics`
- [ ] `periodType` 与前端周期文案一致（`day`/`week`/`month`…）
- [ ] `periodStart` / `periodEnd` 格式为 `YYYY-MM-DD`
- [ ] `metricName` 与 3.4 节对照表一致（尾部采集序号可保留，服务端会自动剥离）
- [ ] `metricValue` 单位符合 8.3 节的约定
- [ ] 客户端读取超时 ≥ 60 秒
- [ ] 已核对 `score.total` / `score.level` 与 `conclusion` 是否自洽（如全「正常」却评分「差」需排查）
- [ ] 已针对 `score.items.*.missing == true` 设计前端提示（"该项数据缺失"）
- [ ] 已针对 `score.total == null`（暂无评分）设计前端展示
- [ ] 前端展示逻辑基于 `标题` / `conclusion` / `score`，而非 `内容` 文本
- [ ] 异常场景已验证：`records` 非数组返回 422，其余脏数据返回 200
- [ ] 已按实际业务周期（`day` / `week` / `month`）各真实调通一次，`reportPeriod` 文案与前端展示口径一致

---

## 附：相关文件

| 文件 | 说明 |
|---|---|
| [`API.md`](./API.md) | 服务完整 API 文档（含手环链路 1、2 节） |
| `health_analysis_service.py` | 服务实现（接口 `polar_health_analyze`；评分 `PolarScoreCalculator`；汇总 `PolarMultiDaySummarizer`） |
| `test/polar_week_normal_sample.json` | **正常一周**样例请求体（7 天） |
| `test/polar_week_abnormal_sample.json` | **异常一周**样例请求体（7 天） |
| `test/test_health_analysis_service.py` | 接口自动化测试（极地用例 9–16，含多周期覆盖） |
