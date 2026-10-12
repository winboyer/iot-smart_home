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
| 文档版本 | v1.0 · 2026-10-12 |

---

## 一、接口总览

一个请求 = **一条监测数据**，返回该条数据的**结论速览**与**完整分析**。

```
POST http://<服务地址>:8006/v1/health/polar/analyze
```

分析范围固定为三项核心指标：**睡眠时长**、**夜间心率**、**呼吸**。输出结构：

```
响应
├── 上报元数据回显        projectId / personId / deviceId / collectTime
├── reportPeriod          报告周期文案，如 "2026-10-12 监测"
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

### 第 3 步：发起一次调用

```bash
curl -s -X POST http://127.0.0.1:8006/v1/health/polar/analyze \
  -H "Content-Type: application/json" \
  -d '{
        "projectId": "SIM_PROJECT_001",
        "personId": "8800000000000001",
        "deviceId": "8001",
        "collectTime": "2026-10-12 06:30:00",
        "metrics": [
          {"metricName": "sleepHours",     "metricValue": "7.30"},
          {"metricName": "heartRateValue1","metricValue": "64"},
          {"metricName": "breathValue1",   "metricValue": "16"},
          {"metricName": "movementSigns1", "metricValue": "8"}
        ]
      }'
```

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

支持两种写法，**可混用**：

**① 聚合式（推荐）** —— 元数据 + `metrics` 指标数组：

```json
{
  "projectId": "SIM_PROJECT_001",
  "personId": "8800000000000001",
  "deviceId": "8001",
  "collectTime": "2026-10-12 06:30:00",
  "metrics": [
    { "metricName": "sleepHours", "metricValue": "7.30" },
    { "metricName": "heartRateValue1", "metricValue": "64" },
    { "metricName": "breathValue1", "metricValue": "16" },
    { "metricName": "movementSigns1", "metricValue": "8" }
  ]
}
```

**② 平铺式（兼容）** —— 指标字段直接放在顶层：

```json
{
  "projectId": "SIM_PROJECT_001",
  "personId": "8800000000000001",
  "deviceId": "8001",
  "collectTime": "2026-10-12 06:30:00",
  "sleepHours": 7.3,
  "heartRateValue": 64,
  "breathValue": 16,
  "movementSigns": 8
}
```

> 两种写法同时存在时，**顶层平铺字段优先**。
> 建议新调用方统一使用**聚合式**，便于后续扩展指标而不改动的字段结构。

### 3.3 请求字段说明

**上报元数据**（均可选，响应原样回显）

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `projectId` | string | 否 | 项目编码，原样回显；缺省回显空串 |
| `personId` | string | 否 | 人员编码，原样回显；缺省时可回退取 `person.id` / `person.id_card` |
| `deviceId` | string | 否 | 设备编码，原样回显；缺省时可回退取 `person.device_id` |
| `collectTime` | string | 否 | 采集时间，格式 `YYYY-MM-DD HH:mm:ss`；用于生成 `reportPeriod` |
| `metrics` | array | 否 | 指标数组，元素为 `{metricName, metricValue}` |
| `person` | object | 否 | 人员基础信息（`id`/`name`/`gender`/`age`），**仅用于个性化文案** |

**`collectTime` 的解析规则**：只取前 10 个字符，若形如 `YYYY-MM-DD` 则生成 `reportPeriod = "<该日期> 监测"`；否则回退为 `"本次监测"`。

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

**同一指标多次上报的处理**：

- **数值型**（睡眠时长、心率、呼吸、体动、分项时长）→ **取算术平均**。
  例：`heartRateValue1=60`、`heartRateValue2=70` → 按 **65** 参与分析。
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

> **调用方只需上报原始值即可**，无需自行把分钟换成小时、也无需拼装总时长。

---

## 四、响应说明

### 4.1 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `projectId` / `personId` / `deviceId` / `collectTime` | string | 上报元数据**原样回显**；未提供时为空串 |
| `reportPeriod` | string | 有 `collectTime` 时为 `"{日期} 监测"`，否则为 `"本次监测"` |
| `conclusion` | object | **结论速览**，固定 4 项：`综合评价` / `睡眠分析` / `心率分析` / `呼吸分析` |
| `analysis` | object | **完整分析**，含 `综合分析` / `睡眠分析` / `心率呼吸分析` |
| `analysis.综合分析.综合评价` | object | `{标题, 内容}`，`标题` ∈ `正常` / `需关注` / `异常` |
| `analysis.综合分析.体检建议` | object | `{标题, 内容}`，`标题` 固定为 `"无"` |
| `analysis.睡眠分析` | object | **仅含 `内容` 字段**；其结论标签取 `conclusion.睡眠分析` |
| `analysis.心率呼吸分析.心率分析` | object | `{标题, 内容}` |
| `analysis.心率呼吸分析.呼吸分析` | object | `{标题, 内容}` |

> 标签口径一致性：`conclusion` 中的 4 个标签与 `analysis` 中对应项的 `标题` **必然一致**。
> 只需标签时读 `conclusion` 即可，避免解析嵌套结构。

### 4.2 响应示例 · 正常数据

请求：`sleepHours=7.30`、`heartRateValue1=64`、`breathValue1=16`、`movementSigns1=8`

```json
{
  "projectId": "SIM_PROJECT_001",
  "personId": "8800000000000001",
  "deviceId": "8001",
  "collectTime": "2026-10-12 06:30:00",
  "reportPeriod": "2026-10-12 监测",
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
        "内容": "结合2026-10-12 监测数据，您的整体健康状况正常。睡眠总时长约7.3小时，夜间平均心率约64.0bpm，平均呼吸约16.0次/分。本报告聚焦睡眠时长、心率与呼吸三项核心指标。"
      },
      "体检建议": {
        "标题": "无",
        "内容": "建议定期体检，重点关注睡眠质量、心血管及呼吸系统等基础指标。"
      }
    },
    "睡眠分析": {
      "内容": "正常，2026-10-12 监测睡眠总时长约7.3小时，处于成人推荐的7-9小时范围内。睡眠时长总体达标，建议保持规律作息。"
    },
    "心率呼吸分析": {
      "心率分析": {
        "标题": "正常",
        "内容": "正常，2026-10-12 监测夜间平均心率约64.0bpm，处于正常参考范围（60-100bpm）。建议保持规律作息、适度有氧运动。"
      },
      "呼吸分析": {
        "标题": "正常",
        "内容": "正常，2026-10-12 监测夜间平均呼吸约16.0次/分，处于正常参考范围（12-20次/分）。呼吸节律平稳，建议继续保持。"
      }
    }
  }
}
```

### 4.3 响应示例 · 异常数据

请求：`sleepHours=3.20`、`heartRateValue1=118`、`breathValue1=23`、`breathe_alarm=1`、`abnormalSleep=0`

```json
{
  "projectId": "SIM_PROJECT_001",
  "personId": "8800000000000002",
  "deviceId": "8002",
  "collectTime": "2026-10-12 07:10:00",
  "reportPeriod": "2026-10-12 监测",
  "conclusion": {
    "综合评价": "异常",
    "睡眠分析": "异常",
    "心率分析": "异常",
    "呼吸分析": "需关注"
  },
  "analysis": {
    "综合分析": {
      "综合评价": {
        "标题": "异常",
        "内容": "结合2026-10-12 监测数据，您的整体健康状况异常。睡眠总时长约3.2小时，夜间平均心率约118.0bpm，平均呼吸约23.0次/分，设备判定睡眠时长不足4小时。本报告聚焦睡眠时长、心率与呼吸三项核心指标。"
      },
      "体检建议": {
        "标题": "无",
        "内容": "建议定期体检，重点关注睡眠质量、心血管及呼吸系统等基础指标。"
      }
    },
    "睡眠分析": {
      "内容": "异常，2026-10-12 监测睡眠总时长约3.2小时，低于成人推荐的7-9小时，设备判定睡眠时长不足4小时。睡眠时长明显偏离正常范围，建议关注生活习惯并咨询医生。"
    },
    "心率呼吸分析": {
      "心率分析": {
        "标题": "异常",
        "内容": "异常，2026-10-12 监测夜间平均心率约118.0bpm，偏离正常参考范围（60-100bpm）。建议持续监测，必要时咨询医生。"
      },
      "呼吸分析": {
        "标题": "需关注",
        "内容": "需关注，2026-10-12 监测夜间平均呼吸约23.0次/分，偏离正常参考范围（12-20次/分）。设备上报呼吸异常报警，建议留意。建议持续监测，必要时咨询医生。"
      }
    }
  }
}
```

### 4.4 结论标签的判定区间

| 指标 | 正常 | 介于两者之间 | 异常 |
|------|------|------|------|
| 睡眠时长 | 7 – 9 小时 | 其余情况 | < 4 小时 或 > 12 小时 |
| 夜间心率 | 60 – 100 bpm | 其余情况 | < 50 bpm 或 > 110 bpm |
| 呼吸率 | 12 – 20 次/分 | 其余情况 | < 9 次/分 或 > 24 次/分 |

附加规则：

- `abnormalSleep = 0`（不足 4 小时）或 `1`（大于 12 小时）→ **睡眠分析直接判「异常」**；`= 2`（长时间无人）→ 至少「需关注」。
- `breathe_alarm = 1` → 呼吸分析**至少「需关注」**；`bedoff_alarm = 1` → 睡眠分析**至少「需关注」**。
- **完全没有睡眠数据**时，`综合评价` 为「需关注」，其余项按有无数据各自判定。

---

## 五、调用示例

### 5.1 cURL

```bash
curl -s -X POST http://127.0.0.1:8006/v1/health/polar/analyze \
  -H "Content-Type: application/json" \
  -d @polar_request.json | python3 -m json.tool
```

其中 `polar_request.json` 即第三节的请求体。

### 5.2 Python（requests）

```python
import requests

URL = "http://127.0.0.1:8006/v1/health/polar/analyze"

payload = {
    "projectId": "SIM_PROJECT_001",
    "personId": "8800000000000001",
    "deviceId": "8001",
    "collectTime": "2026-10-12 06:30:00",
    "metrics": [
        {"metricName": "sleepHours",      "metricValue": "7.30"},
        {"metricName": "heartRateValue1", "metricValue": "64"},
        {"metricName": "breathValue1",    "metricValue": "16"},
        {"metricName": "movementSigns1",  "metricValue": "8"},
    ],
}

resp = requests.post(URL, json=payload, timeout=60)   # 超时务必 >= 60s
resp.raise_for_status()
data = resp.json()

# 只取标签
print(data["conclusion"])          # {'综合评价': '正常', '睡眠分析': '正常', ...}

# 取完整分析文本
print(data["analysis"]["综合分析"]["综合评价"]["内容"])
print(data["analysis"]["睡眠分析"]["内容"])
print(data["analysis"]["心率呼吸分析"]["心率分析"]["内容"])
print(data["analysis"]["心率呼吸分析"]["呼吸分析"]["内容"])
```

### 5.3 Java（OkHttp）

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
                + "\"collectTime\":\"2026-10-12 06:30:00\","
                + "\"metrics\":["
                +   "{\"metricName\":\"sleepHours\",\"metricValue\":\"7.30\"},"
                +   "{\"metricName\":\"heartRateValue1\",\"metricValue\":\"64\"},"
                +   "{\"metricName\":\"breathValue1\",\"metricValue\":\"16\"}"
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

### 5.4 JavaScript（fetch）

```javascript
const payload = {
  projectId: "SIM_PROJECT_001",
  personId: "8800000000000001",
  deviceId: "8001",
  collectTime: "2026-10-12 06:30:00",
  metrics: [
    { metricName: "sleepHours",      metricValue: "7.30" },
    { metricName: "heartRateValue1", metricValue: "64" },
    { metricName: "breathValue1",    metricValue: "16" },
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
  console.log(data.conclusion);                       // 结论速览
  console.log(data.analysis["睡眠分析"].内容);          // 睡眠分析文本
} else {
  console.error(resp.status, await resp.text());
}
```

### 5.5 Postman / Apifox

- Method：`POST`
- URL：`http://<服务地址>:8006/v1/health/polar/analyze`
- Headers：`Content-Type: application/json`
- Body：选 `raw` → `JSON`，粘贴第三节的请求体。

---

## 六、错误码与容错行为

| HTTP 状态码 | 触发场景 | 响应体 |
|---|---|---|
| **200** | 正常返回（**包括 LLM 不可用时的降级输出**） | 完整分析 JSON |
| **422** | ① `Content-Type` 缺失或不含 `application/json`；② 请求体不是合法 JSON；③ `metrics` 不是数组（传了字符串/对象） | `{"detail": [...]}`，FastAPI 标准校验错误 |
| **500** | 分析过程出现未预期异常 | `{"detail": "分析失败: <原因>"}` |

**容错设计（以下情况均返回 200，不报错）**：

| 输入情况 | 行为 |
|---|---|
| 请求体为 `{}` 或字段全缺 | 返回完整结构，结论降级为「需关注」 |
| 未识别的 `metricName` | 忽略该项，其余指标照常分析 |
| `metricValue` 无法解析为数字（如 `"abc"`） | 忽略该值 |
| `metrics` 元素缺字段 / 不是对象 | 跳过该元素 |
| 字节数组长度不足 | 右侧补 0；无法解析则忽略该指标 |
| 未配置 `DEEPSEEK_API_KEY` | 分析文本由规则引擎生成，接口仍返回 200 |

---

## 七、对接注意事项

### 7.1 关于 LLM 与降级

服务优先调用大模型生成分析文本；当模型不可用、超时或返回空时，**自动降级**为内置规则引擎文本。两种模式下：

- 响应**结构完全一致**，`标题`（结论标签）由规则引擎判定，**不随 LLM 变化**；
- 只有 `内容` 的文字表述可能不同（LLM 为自然语言生成，规则引擎为模板文本）。

因此前端**不要**用 `内容` 文本做逻辑判断，请统一使用 `标题` 或 `conclusion`。

### 7.2 关于耗时

| 模式 | 耗时参考 |
|---|---|
| 规则引擎（降级） | < 10 毫秒 |
| LLM 并发生成 4 段文本 | 约 10 秒（上游延迟高时可能更长） |

**客户端超时必须设置为 60 秒以上**，否则 LLM 正常工作时会被误判为超时。

### 7.3 关于单位（需要与极地方确认的隐含契约）

| 指标 | 本文档约定 |
|---|---|
| `sleepHours` | **小时**（如 `"7.30"` = 7.3 小时） |
| `heartRateValue` | **bpm**（直接取值，非放大整数） |
| `breathValue` | **次/分**（直接取值） |
| `*Duration` 系列 | **分钟** |
| `*Hours` 系列 | **小时** |
| 字节数组中的总时长 | **分钟**（大端，2 字节） |

若实际上报量纲与此不同，需调整服务端的指标映射表，否则结论会偏差。

### 7.4 关于数据缺口

以下内容在《睡眠传感器数据》规格中**没有对应字段**，接口**不输出**：

- REM / 快速眼动时长（传感器仅区分深睡、浅睡、清醒三态）
- 心率报警次数（规格只有呼吸报警、离床报警）
- 入睡潜伏期（单条监测数据无法推算）
- 心率最低 / 最高值（仅输出夜间平均值）

### 7.5 常见问题

**Q1：需要传一个周期（近一周/近三月）的数据吗？**
不需要。本接口**一个请求 = 一条监测数据**。多日分析请按日多次调用，由调用方自行聚合。

**Q2：响应里没有 `睡眠分析.标题`？**
是设计如此。`睡眠分析` 只返回 `内容`；其标签请读 `conclusion.睡眠分析`。

**Q3：`体检建议.标题` 为什么是「无」？**
当前版本体检建议不参与等级判定，标题固定为 `"无"`，内容为建议正文。

**Q4：接口幂等吗？可以重试吗？**
接口无状态、不落库，**可安全重试**。仅当使用 LLM 时，`内容` 的措辞可能存在细微差异，`标题` 与结构不变。

**Q5：能并发调用吗？**
可以，服务端每次请求内部使用 4 路线程并发调用 LLM。建议调用方并发控制在合理范围（如 ≤ 10 QPS），避免上游限流。

---

## 八、对接自测清单

上线前请逐项确认：

- [ ] `Content-Type: application/json` 已正确设置
- [ ] `collectTime` 格式为 `YYYY-MM-DD HH:mm:ss`（否则 `reportPeriod` 会退化为"本次监测"）
- [ ] `metricName` 与第三节对照表一致（尾部采集序号可保留，服务端会自动剥离）
- [ ] `metricValue` 单位符合 7.3 节的约定
- [ ] 客户端读取超时 ≥ 60 秒
- [ ] 已覆盖 3 类返回：正常（全「正常」）、异常（含「异常」标签）、空数据（全「需关注」）
- [ ] 前端展示逻辑基于 `标题` / `conclusion`，而非 `内容` 文本
- [ ] 异常场景已验证：`metrics` 非数组返回 422，其余脏数据返回 200

---

## 附：相关文件

| 文件 | 说明 |
|---|---|
| [`API.md`](./API.md) | 服务完整 API 文档（含手环链路 1、2 节） |
| `health_analysis_service.py` | 服务实现（接口定义见 `polar_health_analyze`） |
| `test/polar_metrics_normal_sample.json` | 正常样本请求体 |
| `test/polar_metrics_abnormal_sample.json` | 异常样本请求体 |
| `test/test_health_analysis_service.py` | 接口自动化测试（极地用例 9–15） |
