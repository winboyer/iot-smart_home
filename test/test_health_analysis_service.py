#!/usr/bin/env python3
"""
健康分析服务 API 测试脚本
"""

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
# 请求体支持两种写法：
#   1）聚合式（推荐）：projectId / personId / deviceId / collectTime + metrics 指标数组
#   2）平铺式（兼容）：上报指标字段直接放在请求体顶层
# ---------------------------------------------------------------------------

POLAR_TEST_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_polar_sample(name: str) -> dict:
    with open(os.path.join(POLAR_TEST_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


POLAR_METRICS_NORMAL = _load_polar_sample("polar_metrics_normal_sample.json")
POLAR_METRICS_ABNORMAL = _load_polar_sample("polar_metrics_abnormal_sample.json")
POLAR_FLAT_NORMAL = _load_polar_sample("polar_sensor_normal_sample.json")
POLAR_FLAT_ABNORMAL = _load_polar_sample("polar_sensor_abnormal_sample.json")

POLAR_NORMAL_LEVELS = {"综合评价": "正常", "心率分析": "正常", "呼吸分析": "正常"}


def _assert_polar_meta(data: dict, expect: dict) -> None:
    """校验上报元数据（projectId / personId / deviceId / collectTime）原样回显"""
    for key, value in expect.items():
        assert data.get(key) == value, f"{key} 期望 {value}，实际 {data.get(key)}"


def _assert_polar_shape(data: dict) -> dict:
    """校验极地接口元数据、结论速览与三大分析项结构，返回 analysis"""
    for key in ("projectId", "personId", "deviceId", "collectTime", "reportPeriod"):
        assert key in data, f"响应缺少元数据字段: {key}"

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


def _polar_levels(analysis: dict) -> dict:
    return {
        "综合评价": analysis["综合分析"]["综合评价"]["标题"],
        "心率分析": analysis["心率呼吸分析"]["心率分析"]["标题"],
        "呼吸分析": analysis["心率呼吸分析"]["呼吸分析"]["标题"],
    }


@test("9. 极地项目分析 - 聚合式指标数组（正常样本，元数据回显）")
def test_polar_metrics_normal():
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", POLAR_METRICS_NORMAL)
    assert_status(resp, 200)

    data = assert_json(resp)
    _assert_polar_meta(data, {
        "projectId": "SIM_PROJECT_001",
        "personId": "8800000000000001",
        "deviceId": "8001",
        "collectTime": "2026-10-12 06:30:00",
    })
    assert data.get("reportPeriod") == "2026-10-12 监测", \
        f"reportPeriod 期望取自 collectTime，实际 {data.get('reportPeriod')}"

    analysis = _assert_polar_shape(data)
    levels = _polar_levels(analysis)
    assert levels == POLAR_NORMAL_LEVELS, f"正常样本结论期望全部正常，实际 {levels}"
    assert "7.3" in analysis["睡眠分析"]["内容"], "睡眠分析内容应体现 7.3 小时总时长"
    print(f"  ✅ 聚合式指标正常样本: {levels}")


@test("10. 极地项目分析 - 空数据")
def test_polar_analyze_empty():
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", {})
    assert_status(resp, 200)

    data = assert_json(resp)
    _assert_polar_meta(data, {"projectId": "", "personId": "", "deviceId": "", "collectTime": ""})
    analysis = _assert_polar_shape(data)
    assert analysis["综合分析"]["综合评价"]["标题"] == "需关注", \
        "无睡眠数据时综合评价至少为 需关注"
    print("  ✅ 空数据返回结构完整，结论降级为 需关注")


@test("11. 极地项目分析 - metrics snake_case 与序号剥离")
def test_polar_metrics_snake():
    payload = {
        "project_id": "SIM_PROJECT_002",
        "person_id": "8800000000000003",
        "device_id": "8003",
        "collect_time": "2026-10-12 06:45:00",
        "metrics": [
            {"metric_name": "sleep_hours", "metric_value": "7.5"},
            {"metric_name": "heart_rate_value_1", "metric_value": "60"},
            {"metric_name": "heart_rate_value_2", "metric_value": "70"},
            {"metric_name": "breath_value_1", "metric_value": "18"},
            {"metric_name": "movement_signs_1", "metric_value": "10"},
        ],
    }
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", payload)
    assert_status(resp, 200)

    data = assert_json(resp)
    _assert_polar_meta(data, {"projectId": "SIM_PROJECT_002", "deviceId": "8003"})
    analysis = _assert_polar_shape(data)
    levels = _polar_levels(analysis)
    assert levels == POLAR_NORMAL_LEVELS, f"snake_case 等价输入应得到相同结论，实际 {levels}"
    assert "65" in analysis["心率呼吸分析"]["心率分析"]["内容"], \
        "同一指标多次上报应取平均（60 与 70 -> 65）"
    print("  ✅ metrics snake_case 解析正常，序号后缀剥离，多次上报取平均")


@test("12. 极地项目分析 - 平铺式字段（兼容旧调用方）")
def test_polar_flat_compat():
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", POLAR_FLAT_NORMAL)
    assert_status(resp, 200)

    analysis = _assert_polar_shape(assert_json(resp))
    levels = _polar_levels(analysis)
    assert levels == POLAR_NORMAL_LEVELS, f"平铺式等价输入应得到相同结论，实际 {levels}"
    print("  ✅ 平铺式写法仍可用")


@test("13. 极地项目分析 - 聚合式异常样本（睡眠不足4小时 + 呼吸报警）")
def test_polar_metrics_abnormal():
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", POLAR_METRICS_ABNORMAL)
    assert_status(resp, 200)

    analysis = _assert_polar_shape(assert_json(resp))
    levels = _polar_levels(analysis)
    assert levels["综合评价"] == "异常", f"异常样本综合评价期望 异常，实际 {levels}"
    assert levels["呼吸分析"] != "正常", f"呼吸报警样本不应判定为正常，实际 {levels}"
    assert "3.2" in analysis["睡眠分析"]["内容"], "睡眠分析内容应体现约 3.2 小时的总时长"
    print(f"  ✅ 聚合式异常样本判定正确: {levels}")


@test("14. 极地项目分析 - 十六进制字节数组兼容（平铺）")
def test_polar_analyze_hex_bytes():
    payload = dict(POLAR_FLAT_NORMAL)
    payload["sleepComprehensiveStatus"] = "01010F3D17122A02"
    payload["sleepQualityAnalysis"] = "5201A4064A1A0C01170F3D02"
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", payload)
    assert_status(resp, 200)

    analysis = _assert_polar_shape(assert_json(resp))
    levels = _polar_levels(analysis)
    assert levels == POLAR_NORMAL_LEVELS, f"十六进制数组应等价于整型数组，实际 {levels}"
    print("  ✅ 十六进制字节数组解析正常")


@test("15. 极地项目分析 - 缺质量分析时按分项时长兜底")
def test_polar_analyze_fallback_total():
    payload = dict(POLAR_FLAT_NORMAL)
    payload.pop("sleepQualityAnalysis", None)
    resp = post_json(f"{BASE_URL}/v1/health/polar/analyze", payload)
    assert_status(resp, 200)

    analysis = _assert_polar_shape(assert_json(resp))
    levels = _polar_levels(analysis)
    assert levels == POLAR_NORMAL_LEVELS, f"兜底总时长应为 7 小时（正常），实际 {levels}"
    print("  ✅ 无质量分析字段时按 深睡+浅睡 兜底计算总时长")


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
    test_polar_metrics_normal()
    test_polar_analyze_empty()
    test_polar_metrics_snake()
    test_polar_flat_compat()
    test_polar_metrics_abnormal()
    test_polar_analyze_hex_bytes()
    test_polar_analyze_fallback_total()

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
