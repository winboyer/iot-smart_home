"""测试阻塞接口——多场景覆盖：驼峰/下划线、空数据"""
import json, os, re, random, requests, copy, time

API_URL = "http://120.26.34.95:7116/v1/health/analyze"

# 可重试的瞬时错误 HTTP 状态码（服务端/网关层面临时故障）
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 180  # 秒；服务端 8 路 LLM 并发，需留足时间

# camelCase → snake_case 转换
def to_snake(name: str) -> str:
    return re.sub(r'(?<=[a-z])([A-Z])', r'_\1', name).lower()

def convert_keys(obj):
    if isinstance(obj, dict):
        return {to_snake(k): convert_keys(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_keys(v) for v in obj]
    return obj

def _retry_wait(attempt: int) -> float:
    """指数退避 + 抖动：5s / 10s / 20s"""
    return min(2 ** (attempt - 1) * 5, 20) + random.uniform(0, 2)


def _preview(v, limit: int = 100) -> str:
    """安全截断任意类型的值为字符串预览"""
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)[:limit]
    return str(v)[:limit]


def call_api(label, req):
    """
    带重试的 API 调用。
    只对「瞬时错误」重试：连接异常 / 读超时 / 429/5xx；
    对 4xx、200 但 JSON 解析失败等确定性错误直接结束，避免重复打爆服务端。
    """
    from requests.exceptions import ConnectionError as ReqConnErr
    from requests.exceptions import Timeout as ReqTimeout

    print(f"  wristband: {len(req.get('wristband_records', []))} 条")
    print(f"  sleep: {len(req.get('sleep_records', []))} 条")
    print(f"  请求中 ...")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        # 1) 发送请求：仅网络层瞬时异常触发重试
        try:
            resp = requests.post(API_URL, json=req, timeout=REQUEST_TIMEOUT)
        except (ReqConnErr, ReqTimeout) as e:
            print(f"  网络异常（瞬时，可重试）: {type(e).__name__}: {e}")
            if attempt < MAX_ATTEMPTS:
                wait = _retry_wait(attempt)
                print(f"  重试 {attempt+1}/{MAX_ATTEMPTS}（等待 {wait:.0f}s）...")
                time.sleep(wait)
            else:
                print(f"  ❌ 失败: {type(e).__name__}: {e}")
            continue

        # 2) 先检查状态码，再解析 JSON
        if resp.status_code == 200:
            try:
                result = resp.json()
            except Exception as e:
                print(f"  ⚠️ HTTP 200 但响应 JSON 解析失败: {type(e).__name__}: {e}")
                return False
            print(f"  Status: {resp.status_code}")
            print(f"  project_id: {result.get('project_id')}")
            print(f"  report_period: {result.get('report_period')}")
            analysis = result.get('analysis', {})
            for key, val in analysis.items():
                if isinstance(val, dict):
                    for k, v in val.items():
                        print(f"  ▸ {k}: {_preview(v)}...")
                else:
                    print(f"  ▸ {key}: {_preview(val)}...")
            return True

        # 3) 瞬时 HTTP 错误：退避重试
        if resp.status_code in RETRYABLE_STATUS:
            print(f"  Status: {resp.status_code}（可重试）")
            if attempt < MAX_ATTEMPTS:
                wait = _retry_wait(attempt)
                print(f"  重试 {attempt+1}/{MAX_ATTEMPTS}（等待 {wait:.0f}s）...")
                time.sleep(wait)
            else:
                print(f"  ❌ 失败: HTTP {resp.status_code}")
            continue

        # 4) 其余（4xx 等）为确定性错误：不重试
        try:
            result = resp.json()
        except Exception:
            result = resp.text[:200]
        print(f"  Error: HTTP {resp.status_code}: {result}")
        return False

    return False


def _find_data_file() -> str:
    """定位 result.json：优先环境变量，其次常见路径（兼容 Mac/Linux）"""
    candidates = [
        os.environ.get("HEALTH_TEST_DATA", ""),
        "/Users/jinyfeng/Downloads/result.json",        # 原 Mac 路径
        os.path.expanduser("~/Downloads/result.json"),  # 本机 Linux 路径
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "result.json"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError(
        "找不到 result.json 数据文件。\n"
        "请通过环境变量指定: export HEALTH_TEST_DATA=/path/to/result.json"
    )

# 读取原始数据（自动定位文件，兼容 Mac/Linux 路径）
raw_data = json.load(open(_find_data_file(), encoding="utf-8"))

# 准备测试用例
test_cases = []

# 用例1: 原始驼峰格式
test_cases.append(("驼峰格式（原始数据）", raw_data))

# 用例2: 下划线格式
test_cases.append(("下划线格式（转换后）", convert_keys(copy.deepcopy(raw_data))))

# 用例3: wristband_records 为空
data_no_wristband = convert_keys(copy.deepcopy(raw_data))
data_no_wristband["wristband_records"] = []
test_cases.append(("wristband_records 为空", data_no_wristband))

# 用例4: sleep_records 为空
data_no_sleep = convert_keys(copy.deepcopy(raw_data))
data_no_sleep["sleep_records"] = []
test_cases.append(("sleep_records 为空", data_no_sleep))

# 用例5: 两者都为空
data_both_empty = convert_keys(copy.deepcopy(raw_data))
data_both_empty["wristband_records"] = []
data_both_empty["sleep_records"] = []
test_cases.append(("两者均为空", data_both_empty))

# 执行测试（每次请求间等待 5s，避免服务端单 worker 冲突）
for idx, (label, req) in enumerate(test_cases, 1):
    print(f"{'='*60}")
    print(f"  用例 {idx}: {label}")
    print(f"{'='*60}")
    ok = call_api(label, req)
    print()
    if idx < len(test_cases):
        print(f"  ⏳ 等待 5s 再进行下一个测试 ...")
        time.sleep(5)

