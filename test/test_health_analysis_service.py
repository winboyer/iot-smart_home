#!/usr/bin/env python3
"""
健康分析服务 API 测试脚本
"""

import json
import sys
import time
import urllib.request
import urllib.error

# BASE_URL = "http://127.0.0.1:26022"
BASE_URL = "http://120.26.34.95:7116"
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
