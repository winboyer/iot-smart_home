"""测试阻塞接口——多场景覆盖：驼峰/下划线、空数据"""
import json, re, requests, copy, time

API_URL = "http://120.26.34.95:7116/v1/health/analyze"

# camelCase → snake_case 转换
def to_snake(name: str) -> str:
    return re.sub(r'(?<=[a-z])([A-Z])', r'_\1', name).lower()

def convert_keys(obj):
    if isinstance(obj, dict):
        return {to_snake(k): convert_keys(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_keys(v) for v in obj]
    return obj

def call_api(label, req):
    """带重试的 API 调用"""
    print(f"  wristband: {len(req.get('wristband_records', []))} 条")
    print(f"  sleep: {len(req.get('sleep_records', []))} 条")
    print(f"  请求中 ...")

    for attempt in range(3):
        try:
            resp = requests.post(API_URL, json=req, timeout=180)
            result = resp.json()
            print(f"  Status: {resp.status_code}")
            if resp.status_code == 200:
                print(f"  project_id: {result.get('project_id')}")
                print(f"  report_period: {result.get('report_period')}")
                analysis = result.get('analysis', {})
                for key, val in analysis.items():
                    if isinstance(val, dict):
                        for k, v in val.items():
                            print(f"  ▸ {k}: {v[:100]}...")
                    else:
                        print(f"  ▸ {key}: {val[:100]}...")
                return True
            else:
                print(f"  Error: {result}")
                return False
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 10
                print(f"  重试 {attempt+2}/3（等待 {wait}s）...")
                time.sleep(wait)
            else:
                print(f"  ❌ 失败: {type(e).__name__}: {e}")
    return False

# 读取原始数据
with open('/Users/jinyfeng/Downloads/result.json') as f:
    raw_data = json.load(f)

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

