"""测试阻塞接口——从 result.json 读取数据并调用 /v1/health/analyze"""
import json, re, requests

# camelCase → snake_case 转换
def to_snake(name: str) -> str:
    return re.sub(r'(?<=[a-z])([A-Z])', r'_\1', name).lower()

def convert_keys(obj):
    if isinstance(obj, dict):
        return {to_snake(k): convert_keys(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_keys(v) for v in obj]
    return obj

# 读取数据
with open('/Users/jinyfeng/Downloads/result.json') as f:
    data = json.load(f)

# 转换 key 格式
req = convert_keys(data)

print(f"Request: person={req['person'].get('name', 'N/A')}, "
      f"wristband={len(req.get('wristband_records', []))} records, "
      f"sleep={len(req.get('sleep_records', []))} records")

# 调用接口
API_URL = "http://120.26.34.95:7116/v1/health/analyze"
resp = requests.post(API_URL, json=req, timeout=120)
result = resp.json()

print(f"\nStatus: {resp.status_code}")
print(f"project_id: {result.get('project_id')}")
print(f"device_id: {result.get('device_id')}")
print(f"person_id: {result.get('person_id')}")
print(f"report_period: {result.get('report_period')}")
print()

analysis = result.get('analysis', {})
for key, val in analysis.items():
    print(f"━━━ {key} ━━━")
    if isinstance(val, dict):
        for k, v in val.items():
            print(f"  [{k}]: {v[:200]}")
    else:
        print(f"  {val[:200]}")
    print()
