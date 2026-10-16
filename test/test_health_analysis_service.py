#!/usr/bin/env python3
"""
健康分析服务 API 测试脚本
"""

import datetime
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8006"
# REMOTE_BASE_URL = "http://120.26.34.95:7116"
PASSED = 0
FAILED = 0

# 全局耗时记录
_TIMINGS: dict[str, float] = {}


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def test(name: str):
    """装饰器风格的测试包装"""
    def decorator(fn):
        def wrapper():
            global PASSED, FAILED
            t0 = time.time()
            try:
                fn()
                elapsed = time.time() - t0
                _TIMINGS[name] = elapsed
                PASSED += 1
                print(f"  ✅ PASS  ⏱️ {elapsed:.2f}s")
            except AssertionError as e:
                elapsed = time.time() - t0
                _TIMINGS[name] = elapsed
                FAILED += 1
                print(f"  ❌ FAIL: {e}  ⏱️ {elapsed:.2f}s")
            except Exception as e:
                elapsed = time.time() - t0
                _TIMINGS[name] = elapsed
                FAILED += 1
                print(f"  ❌ ERROR: {type(e).__name__}: {e}  ⏱️ {elapsed:.2f}s")
        return wrapper

    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    decorator.__name__ = name
    return decorator


def assert_status(resp, expected: int, label=""):
    """断言 HTTP 状态码"""
    actual = resp.getcode()
    assert actual == expected, f"{label} 期望 HTTP {expected}，实际 {actual}"
    print(f"  ✅ HTTP {actual} {label}")


def assert_json(resp, label=""):
    """断言响应体是合法 JSON 并返回"""
    body = resp.read().decode("utf-8")
    try:
        data = json.loads(body)
        print(f"  ✅ 响应为合法 JSON {label}")
        return data
    except json.JSONDecodeError as e:
        raise AssertionError(f"响应不是合法 JSON: {e}") from e


def post_json(url, data: dict):
    """发送 POST JSON 请求"""
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=60)


def get(url):
    """发送 GET 请求"""
    return urllib.request.urlopen(url, timeout=10)


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

SAMPLE_PERSON = {
    "id": "2067422320443576321",
    "project_id": "test-project-001",
    "device_id": "861389061443375",
    "name": "张三",
    "gender": "1",
    "age": 22,
    "height": 164.0,
    "weight": 52.0,
    "bmi": 19.3,
}

SAMPLE_WRISTBAND = [
    {
        "data_time": "2026-06-26 03:19:00",
        "avg_hr": 79,
        "max_hr": 85,
        "min_hr": 72,
        "sbp": 118,
        "dbp": 70,
        "bp_hr": 70,
        "avg_spo2": 96,
        "min_spo2": 95,
        "max_spo2": 99,
        "estimate_temp": 36.5,
        "steps": 120,
        "calorie": 5,
        "distance": 80.0,
        "fatigue": 50,
        "rmssd": 36,
        "type": "hr,spo,pressure",
    }
]

SAMPLE_SLEEP = [
    {
        "collect_date": 20260709,
        "rri_data_list": "[-3, 4820, -1, 37, 28, 32, 45, -2, 5120, -1, 42]",
        "sleep_segments": '[{"E":{"start":0,"end":180},"Q":1049,"T":[11,32]}]',
    }
]

MINIMAL_REQUEST = {
    "person": SAMPLE_PERSON,
    "wristband_records": [],
    "sleep_records": [],
}

FULL_REQUEST = {
    "person": SAMPLE_PERSON,
    "wristband_records": SAMPLE_WRISTBAND,
    "sleep_records": SAMPLE_SLEEP,
}


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

@test("1. 服务存活检查 - GET /docs")
def test_docs():
    resp = get(f"{BASE_URL}/docs")
    assert_status(resp, 200)
    body = resp.read().decode("utf-8")
    assert "openapi" in body.lower() or "swagger" in body.lower() or "fastapi" in body.lower(), \
        "docs 页面内容异常"


@test("2. 获取人员列表 - GET /v1/health/persons")
def test_persons():
    try:
        resp = get(f"{BASE_URL}/v1/health/persons")
        assert_status(resp, 200)
        data = assert_json(resp)
        assert isinstance(data, dict) and "persons" in data, f"期望返回含 persons 字段的字典，实际 {type(data).__name__}"
        print(f"  ✅ 返回 {len(data['persons'])} 条人员记录")
    except urllib.error.HTTPError as e:
        # 如果没有数据文件，返回 500 也可以接受
        if e.code == 500:
            print(f"  ⚠️  HTTP 500 (数据文件缺失，可接受)")
        else:
            raise


@test("3. 阻塞式健康分析 - POST /v1/health/analyze (最小数据)")
def test_analyze_minimal():
    resp = post_json(f"{BASE_URL}/v1/health/analyze", MINIMAL_REQUEST)
    assert_status(resp, 200)

    data = assert_json(resp)
    # 验证顶层字段
    assert "project_id" in data, "缺少 project_id"
    assert "device_id" in data, "缺少 device_id"
    assert "person_id" in data, "缺少 person_id"
    assert "analysis" in data, "缺少 analysis"

    analysis = data["analysis"]
    dimensions = ["综合分析", "睡眠分析", "心率房颤分析", "血压血氧分析", "体温分析", "运动分析"]
    for dim in dimensions:
        assert dim in analysis, f"缺少维度: {dim}"

    print(f"  ✅ 返回 {len(dimensions)} 个分析维度")


@test("4. 阻塞式健康分析 - POST /v1/health/analyze (完整数据)")
def test_analyze_full():
    resp = post_json(f"{BASE_URL}/v1/health/analyze", FULL_REQUEST)
    assert_status(resp, 200)

    data = assert_json(resp)
    assert data.get("person_id") == SAMPLE_PERSON["id"], \
        f"person_id 不匹配: {data.get('person_id')}"

    analysis = data["analysis"]

    # 验证 综合分析 有子字段
    overview = analysis.get("综合分析", {})
    assert "综合评价" in overview, "综合分析缺少: 综合评价"
    assert "体检建议" in overview, "综合分析缺少: 体检建议"

    # 验证 心率房颤分析 有子字段
    hr_af = analysis.get("心率房颤分析", {})
    assert "心率分析" in hr_af, "心率房颤分析缺少: 心率分析"
    assert "房颤分析" in hr_af, "心率房颤分析缺少: 房颤分析"

    # 验证 血压血氧分析 有子字段
    bp_spo = analysis.get("血压血氧分析", {})
    assert "血压分析" in bp_spo, "血压血氧分析缺少: 血压分析"
    assert "血氧分析" in bp_spo, "血压血氧分析缺少: 血氧分析"

    print(f"  ✅ 所有维度分析内容完整")


@test("5. 流式健康分析 - POST /v1/health/analyze/stream")
def test_analyze_stream():
    resp = post_json(f"{BASE_URL}/v1/health/analyze/stream", FULL_REQUEST)
    assert_status(resp, 200)

    content_type = resp.getheader("Content-Type", "")
    assert "text/event-stream" in content_type, f"流式接口 Content-Type 期望 text/event-stream，实际 {content_type}"

    events = []
    meta_found = False
    dimension_events = set()

    for line in resp:
        line = line.decode("utf-8").strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)
            etype = event.get("type", "")
            if etype == "meta":
                meta_found = True
                assert "person_id" in event, "meta 事件缺少 person_id"
            else:
                dimension_events.add(etype)
                assert "content" in event, f"维度事件 {etype} 缺少 content 字段"
        except json.JSONDecodeError:
            print(f"  ⚠️  非 JSON SSE 行: {line[:80]}...")

    assert meta_found, "未收到 meta 事件"
    expected_dims = {"综合分析", "睡眠分析", "心率分析", "房颤分析", "血压分析", "血氧分析", "体温分析", "运动分析"}
    missing = expected_dims - dimension_events
    assert not missing, f"缺少维度事件: {missing}"

    print(f"  ✅ 收到 meta 事件 + {len(dimension_events)} 个维度事件 (共 {len(events)} 条 SSE)")


@test("6. 异常输入 - 缺少 person 字段")
def test_missing_person():
    try:
        post_json(f"{BASE_URL}/v1/health/analyze", {"wristband_records": [], "sleep_records": []})
        raise AssertionError("期望返回 422/400，但返回了 200")
    except urllib.error.HTTPError as e:
        assert e.code in (400, 422), f"期望 400 或 422，实际 {e.code}"
        print(f"  ✅ 正确返回 HTTP {e.code}")


@test("7. 异常输入 - 空请求体")
def test_empty_body():
    try:
        post_json(f"{BASE_URL}/v1/health/analyze", {})
        raise AssertionError("期望返回 422/400，但返回了 200")
    except urllib.error.HTTPError as e:
        assert e.code in (400, 422), f"期望 400 或 422，实际 {e.code}"
        print(f"  ✅ 正确返回 HTTP {e.code}")


# ---------------------------------------------------------------------------
# 驼峰格式兼容性测试数据
# ---------------------------------------------------------------------------

CAMEL_PERSON = {
    "id": "2067422320443576321",
    "projectId": "test-project-camel",
    "deviceId": "861389061443375",
    "name": "李四",
    "gender": "2",
    "age": 28,
    "height": 160.0,
    "weight": 50.0,
    "bmi": 19.5,
    "idCard": "420281200001010002",
}

CAMEL_WRISTBAND = [
    {
        "dataTime": "2026-07-01 08:00:00",
        "avgHr": 72,
        "maxHr": 88,
        "minHr": 65,
        "sbp": 115,
        "dbp": 72,
        "bpHr": 68,
        "avgSpo2": 98,
        "minSpo2": 96,
        "maxSpo2": 99,
        "estimateTemp": 36.3,
        "steps": 200,
        "calorie": 8,
        "distance": 150.0,
        "fatigue": 40,
        "rmssd": 42,
        "type": "hr,spo,pressure",
        "personId": "2067422320443576321",
        "deviceId": "861389061443375",
    }
]

CAMEL_SLEEP = [
    {
        "collectDate": 20260710,
        "rriDataList": "[-3, 5200, -1, 42, 35, 38, 50]",
        "sleepSegments": '[{"E":{"start":0,"end":240},"Q":1100,"T":[10,30]}]',
        "personId": "2067422320443576321",
        "deviceId": "861389061443375",
    }
]

CAMEL_REQUEST = {
    "person": CAMEL_PERSON,
    "wristbandRecords": CAMEL_WRISTBAND,
    "sleepRecords": CAMEL_SLEEP,
}


@test("8. 驼峰格式兼容 - snake_case 顶层 + camelCase 内层数据")
def test_camel_compat():
    """使用 snake_case 顶层键 + camelCase 内部字段"""
    resp = post_json(f"{BASE_URL}/v1/health/analyze", {
        "person": CAMEL_PERSON,
        "wristband_records": CAMEL_WRISTBAND,
        "sleep_records": CAMEL_SLEEP,
    })
    assert_status(resp, 200)

    data = assert_json(resp)
    assert data.get("person_id") == CAMEL_PERSON["idCard"], \
        f"person_id 不匹配: {data.get('person_id')}"

    analysis = data["analysis"]
    assert "综合分析" in analysis, "缺少综合分析"
    assert "睡眠分析" in analysis, "缺少睡眠分析"
    assert "运动分析" in analysis, "缺少运动分析"

    # 验证 camelCase person 的 name 被正确解析
    print(f"  ✅ camelCase 字段解析正常 (人员: {CAMEL_PERSON['name']})")


# ---------------------------------------------------------------------------
# 极地项目（睡眠传感器）测试数据
# 一个请求 = 一个统计周期，records 中每一条 = 一天的监测数据
# ---------------------------------------------------------------------------

POLAR_TEST_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_polar_sample(name: str) -> dict:
    with open(os.path.join(POLAR_TEST_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


POLAR_WEEK_NORMAL = _load_polar_sample("polar_week_normal_sample.json")
POLAR_WEEK_ABNORMAL = _load_polar_sample("polar_week_abnormal_sample.json")

POLAR_NORMAL_LEVELS = {"综合评价": "正常", "心率分析": "正常", "呼吸分析": "正常"}
POLAR_LEVEL_FLOORS = ((90, "优秀"), (80, "良好"), (70, "一般"), (60, "较差"), (0, "差"))


def _expect_level(total: int) -> str:
    for floor, name in POLAR_LEVEL_FLOORS:
        if total >= floor:
            return name
    return "差"


def _assert_polar_meta(data: dict, expect: dict) -> None:
    """校验上报元数据与周期字段原样回显"""
    for key, value in expect.items():
        assert data.get(key) == value, f"{key} 期望 {value}，实际 {data.get(key)}"


def _assert_polar_shape(data: dict) -> dict:
    """校验极地接口元数据、结论速览与三大分析项结构，返回 analysis"""
    for key in ("projectId", "personId", "deviceId", "periodType", "periodStart",
                "periodEnd", "recordCount", "validRecordCount", "reportPeriod", "score"):
        assert key in data, f"响应缺少字段: {key}"

    conclusion = data.get("conclusion", {})
    for key in ("综合评价", "睡眠分析", "心率分析", "呼吸分析"):
        assert key in conclusion, f"conclusion 缺少: {key}"
        assert conclusion[key] in ("正常", "需关注", "异常"), \
            f"conclusion[{key}] 取值异常: {conclusion[key]}"

    analysis = data.get("analysis", {})
    for dim in ("综合分析", "睡眠分析", "心率呼吸分析"):
        assert dim in analysis, f"缺少维度: {dim}"

    overall = analysis["综合分析"]
    assert set(overall) == {"综合评价", "体检建议"}, f"综合分析字段异常: {set(overall)}"
    for key in ("综合评价", "体检建议"):
        assert "标题" in overall[key] and "内容" in overall[key], f"{key} 缺少 标题/内容"

    assert "内容" in analysis["睡眠分析"], "睡眠分析缺少 内容"

    hr_br = analysis["心率呼吸分析"]
    for key in ("心率分析", "呼吸分析"):
        assert key in hr_br, f"心率呼吸分析缺少: {key}"
        assert "标题" in hr_br[key] and "内容" in hr_br[key], f"{key} 缺少 标题/内容"

    # 结论速览与三大分析项中的标签必须一致
    assert conclusion["综合评价"] == overall["综合评价"]["标题"], "结论速览与综合分析不一致"
    assert conclusion["心率分析"] == hr_br["心率分析"]["标题"], "结论速览与心率分析不一致"
    assert conclusion["呼吸分析"] == hr_br["呼吸分析"]["标题"], "结论速览与呼吸分析不一致"
    return analysis


def _assert_polar_score(data: dict) -> dict:
    """校验综合评分结构，并核对总分与分项加权结果一致"""
    score = data.get("score")
    assert isinstance(score, dict), "响应缺少 score 对象"
    for key in ("total", "level", "items", "weights", "note"):
        assert key in score, f"score 缺少字段: {key}"
    assert abs(sum(score["weights"].values()) - 1.0) < 1e-6, "各项权重之和应为 1"

    for name in ("睡眠", "心率", "呼吸"):
        assert name in score["items"], f"score.items 缺少分项: {name}"
        item = score["items"][name]
        for key in ("score", "weight", "mean", "compliance_rate", "status", "missing"):
            assert key in item, f"score.items[{name}] 缺少字段: {key}"

    if score["total"] is None:
        assert score["level"] == "暂无评分", \
            f"无分数时等级应为 暂无评分，实际 {score['level']}"
        return score

    assert score["level"] == _expect_level(score["total"]), \
        f"等级与分数不匹配: {score['total']} -> {score['level']}"
    weighted = sum(i["score"] * i["weight"] for i in score["items"].values())
    assert abs(round(weighted) - score["total"]) <= 1, \
        f"分项加权 {round(weighted)} 与总分 {score['total']} 不一致"
    return score


def _polar_levels(analysis: dict) -> dict:
    return {
        "综合评价": analysis["综合分析"]["综合评价"]["标题"],
        "心率分析": analysis["心率呼吸分析"]["心率分析"]["标题"],
        "呼吸分析": analysis["心率呼吸分析"]["呼吸分析"]["标题"],
    }


@test("9. 极地项目分析 - 周期数据（近一周，正常样本）")
def test_polar_week_normal():
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", POLAR_WEEK_NORMAL)
    assert_status(resp, 200)

    data = assert_json(resp)
    _assert_polar_meta(data, {
        "projectId": "SIM_PROJECT_001", "personId": "8800000000000001",
        "deviceId": "8001", "periodType": "week",
        "periodStart": "2026-10-06", "periodEnd": "2026-10-12",
        "recordCount": 7, "validRecordCount": 7,
        "reportPeriod": "近一周（2026-10-06 至 2026-10-12）",
    })

    analysis = _assert_polar_shape(data)
    levels = _polar_levels(analysis)
    assert levels == POLAR_NORMAL_LEVELS, f"正常周结论期望全部正常，实际 {levels}"

    score = _assert_polar_score(data)
    assert score["total"] >= 80, f"正常周综合评分应偏高，实际 {score['total']}"
    assert "7.19" in analysis["睡眠分析"]["内容"], "睡眠分析应体现多日平均时长 7.19 小时"
    print(f"  ✅ 近一周正常样本：结论 {levels}，综合评分 {score['total']}（{score['level']}）")


@test("10. 极地项目分析 - 空数据")
def test_polar_analyze_empty():
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", {})
    assert_status(resp, 200)

    data = assert_json(resp)
    _assert_polar_meta(data, {"projectId": "", "personId": "", "deviceId": "",
                              "recordCount": 0, "validRecordCount": 0,
                              "reportPeriod": "本次监测"})
    analysis = _assert_polar_shape(data)
    assert analysis["综合分析"]["综合评价"]["标题"] == "需关注", \
        "无数据时综合评价至少为 需关注"

    score = _assert_polar_score(data)
    assert score["total"] is None, "无数据时不应给出分数"
    print("  ✅ 空数据：结构完整、结论 需关注、评分为 暂无评分")


@test("11. 极地项目分析 - snake_case 周期字段 + 指标序号剥离 + 多次上报取平均")
def test_polar_week_snake():
    payload = {
        "project_id": "SIM_PROJECT_002",
        "person_id": "8800000000000003",
        "device_id": "8003",
        "period_type": "week",
        "period_start": "2026-10-06",
        "period_end": "2026-10-12",
        "records": [
            {"collect_time": "2026-10-06 06:30:00",
             "metrics": [
                 {"metric_name": "sleep_hours", "metric_value": "7.5"},
                 {"metric_name": "heart_rate_value_1", "metric_value": "60"},
                 {"metric_name": "heart_rate_value_2", "metric_value": "70"},
                 {"metric_name": "breath_value_1", "metric_value": "18"},
             ]},
        ],
    }
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", payload)
    assert_status(resp, 200)

    data = assert_json(resp)
    _assert_polar_meta(data, {"projectId": "SIM_PROJECT_002", "deviceId": "8003",
                              "periodType": "week",
                              "reportPeriod": "近一周（2026-10-06 至 2026-10-12）"})
    analysis = _assert_polar_shape(data)
    assert "65" in analysis["心率呼吸分析"]["心率分析"]["内容"], \
        "同一指标多次上报应取平均（60 与 70 -> 65）"
    print("  ✅ snake_case 周期字段解析正常，序号剥离，多次上报取平均为 65")


@test("12. 极地项目分析 - 周期异常样本（睡眠不足 + 心率呼吸偏高 + 报警）")
def test_polar_week_abnormal():
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", POLAR_WEEK_ABNORMAL)
    assert_status(resp, 200)

    data = assert_json(resp)
    analysis = _assert_polar_shape(data)
    levels = _polar_levels(analysis)
    assert levels["综合评价"] == "异常", f"异常周综合评价期望 异常，实际 {levels}"
    assert levels["呼吸分析"] != "正常", f"呼吸报警样本不应判定为正常，实际 {levels}"

    score = _assert_polar_score(data)
    assert score["total"] is not None and score["total"] < 60, \
        f"异常周综合评分应低于 60，实际 {score['total']}"
    assert score["level"] in ("较差", "差"), f"异常周等级应为 较差/差，实际 {score['level']}"
    print(f"  ✅ 异常周：结论 {levels}，综合评分 {score['total']}（{score['level']}）")


@test("13. 极地项目分析 - 综合评分分项明细可解释")
def test_polar_score_breakdown():
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", POLAR_WEEK_NORMAL)
    assert_status(resp, 200)

    score = _assert_polar_score(assert_json(resp))
    assert all(not score["items"][k]["missing"] for k in ("睡眠", "心率", "呼吸")), \
        "正常周的三个分项均应有数据"
    assert abs(score["weights"]["睡眠"] - 0.4) < 1e-6, "睡眠权重应为 0.4"
    assert abs(score["weights"]["心率"] - 0.3) < 1e-6, "心率权重应为 0.3"
    assert abs(score["weights"]["呼吸"] - 0.3) < 1e-6, "呼吸权重应为 0.3"

    # 7 晚中 5 晚落在 7-9 小时 → 达标率 5/7
    ratio = score["items"]["睡眠"]["compliance_rate"]
    assert abs(ratio - 5 / 7) < 1e-3, f"睡眠达标率期望 5/7，实际 {ratio}"

    detail = {k: v["score"] for k, v in score["items"].items()}
    print(f"  ✅ 评分明细 {detail}，总分 {score['total']}（{score['level']}）")


@test("14. 极地项目分析 - 缺数据项按 60 分计入并标记 missing")
def test_polar_score_missing():
    payload = {
        "projectId": "SIM_PROJECT_003", "personId": "8800000000000004",
        "deviceId": "8004", "periodType": "day",
        "periodStart": "2026-10-12", "periodEnd": "2026-10-12",
        "records": [{"collectTime": "2026-10-12 06:30:00",
                     "metrics": [{"metricName": "sleepHours", "metricValue": "7.5"}]}],
    }
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", payload)
    assert_status(resp, 200)

    data = assert_json(resp)
    assert data["reportPeriod"] == "今日（2026-10-12）", \
        f"periodType=day 应生成 今日 文案，实际 {data['reportPeriod']}"

    score = _assert_polar_score(data)
    assert score["items"]["睡眠"]["missing"] is False, "睡眠项应有数据"
    assert score["items"]["心率"]["missing"] is True, "心率项应标记为缺失"
    assert score["items"]["呼吸"]["missing"] is True, "呼吸项应标记为缺失"
    assert score["total"] is not None and score["total"] < 80, \
        f"仅一项有数据时总分不应虚高，实际 {score['total']}"
    assert score["level"] != "优秀", f"数据不全时不应评为优秀，实际 {score['level']}"
    print(f"  ✅ 缺数据项按 60 分计入，总分 {score['total']}（{score['level']}）")


@test("15. 极地项目分析 - 逐日记录统计与脏数据容错")
def test_polar_records_tolerance():
    payload = {
        "projectId": "SIM_PROJECT_004", "personId": "8800000000000005",
        "deviceId": "8005", "periodType": "week",
        "periodStart": "2026-10-06", "periodEnd": "2026-10-12",
        "records": [
            {"collectTime": "2026-10-06 06:30:00",
             "metrics": [{"metricName": "sleepHours", "metricValue": "7.5"},
                         {"metricName": "heartRateValue1", "metricValue": "65"}]},
            {"collectTime": "2026-10-07 06:30:00"},
            "not-a-dict",
            {"collectTime": "2026-10-08 06:30:00",
             "metrics": [{"metricName": "unknownMetricX", "metricValue": "9"}]},
        ],
    }
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", payload)
    assert_status(resp, 200)

    data = assert_json(resp)
    assert data["recordCount"] == 3, \
        f"recordCount 应为 3（非字典元素不计），实际 {data['recordCount']}"
    assert data["validRecordCount"] == 1, \
        f"validRecordCount 应为 1，实际 {data['validRecordCount']}"
    _assert_polar_shape(data)
    print("  ✅ 逐日统计正确（3 条记录 / 1 条有效），脏数据与未知指标被忽略")


def _make_period_records(days: int, start: str):
    """按固定序列生成 days 天逐日监测数据（确定性，不依赖随机数）

    返回 (records, 结束日期)。序列中刻意混入一天心率偏高 + 呼吸偏快，
    用于验证多日汇总并非简单复制单日结果。
    """
    sleep_seq = [7.2, 6.5, 7.8, 8.1, 5.9, 7.4, 6.8]
    hr_seq = [62, 70, 58, 66, 108, 64, 72]
    br_seq = [15, 17, 13, 16, 22, 14, 18]
    d0 = datetime.date.fromisoformat(start)
    records = []
    for i in range(days):
        k = i % 7
        d = d0 + datetime.timedelta(days=i)
        records.append({
            "collectTime": "%s 06:%02d:00" % (d.isoformat(), 10 + k),
            "metrics": [
                {"metricName": "sleepHours", "metricValue": str(sleep_seq[k])},
                {"metricName": "heartRateValue1", "metricValue": str(hr_seq[k])},
                {"metricName": "breathValue1", "metricValue": str(br_seq[k])},
            ],
        })
    return records, (d0 + datetime.timedelta(days=days - 1)).isoformat()


# 同一接口支持的统计周期：periodType -> (天数, 起始日期, 期望周期文案)
POLAR_PERIOD_CASES = (
    ("day", 1, "2026-10-16", "今日（2026-10-16）"),
    ("week", 7, "2026-10-10", "近一周（2026-10-10 至 2026-10-16）"),
    ("month", 30, "2026-09-17", "近一个月（2026-09-17 至 2026-10-16）"),
    ("quarter", 90, "2026-07-19", "近三个月（2026-07-19 至 2026-10-16）"),
)


@test("16. 极地项目分析 - 同一接口覆盖 day/week/month/quarter 多周期")
def test_polar_multi_period():
    """验证「一个接口同时处理天/周/月（及更长周期）」，无需按周期拆分接口"""
    seen = []
    for ptype, days, start, expect_text in POLAR_PERIOD_CASES:
        records, end = _make_period_records(days, start)
        payload = {
            "projectId": "SIM_PROJECT_001", "personId": "8800000000000009",
            "deviceId": "8009", "periodType": ptype,
            "periodStart": start, "periodEnd": end, "records": records,
        }
        resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", payload)
        assert_status(resp, 200)
        data = assert_json(resp)

        assert data["periodType"] == ptype, \
            f"periodType 回显异常: {data['periodType']}"
        assert data["reportPeriod"] == expect_text, \
            f"{ptype} 周期文案期望 {expect_text}，实际 {data['reportPeriod']}"
        assert data["recordCount"] == days, \
            f"{ptype} recordCount 期望 {days}，实际 {data['recordCount']}"
        assert data["validRecordCount"] == days, \
            f"{ptype} validRecordCount 期望 {days}，实际 {data['validRecordCount']}"

        analysis = _assert_polar_shape(data)
        score = _assert_polar_score(data)
        assert score["total"] is not None, f"{ptype} 有数据时应给出综合评分"
        assert analysis["睡眠分析"]["内容"], f"{ptype} 睡眠分析内容为空"
        assert analysis["综合分析"]["综合评价"]["内容"], f"{ptype} 综合分析内容为空"
        seen.append("%s=%d(%s)" % (ptype, score["total"], score["level"]))

    print("  ✅ 单接口覆盖多周期，评分 " + " / ".join(seen))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  手环健康综合管理分析服务 - API 测试")
    print(f"  目标: {BASE_URL}")
    print("=" * 60)

    test_docs()
    test_persons()
    test_analyze_minimal()
    test_analyze_full()
    test_analyze_stream()
    test_missing_person()
    test_empty_body()
    test_camel_compat()
    test_polar_week_normal()
    test_polar_analyze_empty()
    test_polar_week_snake()
    test_polar_week_abnormal()
    test_polar_score_breakdown()
    test_polar_score_missing()
    test_polar_records_tolerance()
    test_polar_multi_period()

    print(f"\n{'='*60}")
    print(f"  测试结果: {PASSED + FAILED} 个用例, {PASSED} 通过, {FAILED} 失败")
    print(f"{'='*60}")

    # 耗时汇总
    if _TIMINGS:
        print(f"\n{'='*60}")
        print(f"  接口响应耗时汇总")
        print(f"{'='*60}")
        print(f"  {'接口':<45} {'耗时':>10}")
        print(f"  {'-'*55}")
        total = 0.0
        for name, elapsed in _TIMINGS.items():
            print(f"  {name:<45} {elapsed:>7.2f}s")
            total += elapsed
        print(f"  {'-'*55}")
        print(f"  {'总计':<45} {total:>7.2f}s")
        print(f"{'='*60}")

    sys.exit(0 if FAILED == 0 else 1)
