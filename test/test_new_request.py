"""测试新请求格式"""
import json, requests

with open('/Users/jinyfeng/Downloads/health_data/biz_person.json') as f:
    persons = json.load(f)
with open('/Users/jinyfeng/Downloads/health_data/biz_wristband_cache.json') as f:
    wristband = json.load(f)
with open('/Users/jinyfeng/Downloads/health_data/biz_sleep_cache.json') as f:
    sleep = json.load(f)

req = {
    'backend': 'deepseek',
    'person': persons[0],
    'wristband_records': wristband[:50],
    'sleep_records': sleep[:1]
}

print(f"Sending: person={req['person']['name']}, wristband={len(req['wristband_records'])} records, sleep={len(req['sleep_records'])} records")
resp = requests.post('http://localhost:26022/v1/health/analyze', json=req, timeout=120)
data = resp.json()
print(f"Status: {resp.status_code}")
print(f"project_id: {data.get('project_id')}")
print(f"device_id: {data.get('device_id')}")
print(f"person_id: {data.get('person_id')}")
print(f"report_period: {data.get('report_period')}")
print(f"analysis keys: {list(data.get('analysis', {}).keys())}")
