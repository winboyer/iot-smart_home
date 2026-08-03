import os, sys
import time
# import pytest
from fastapi.testclient import TestClient
import requests
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# from server.health_analys_server import app, assistant
# client = TestClient(app)

# def mock_chat_stream(question: str):
#     """模拟流式生成，阻断真实的大模型网络请求"""
#     yield "这"
#     yield "是"
#     yield "健康"
#     yield "建议。"

# def test_chat_endpoint(monkeypatch):
#     # 替换 HealthAssistant 的实际请求逻辑
#     monkeypatch.setattr(assistant, "chat_stream", mock_chat_stream)
    
#     response = client.post("/v1/chat", json={"question": "我最近总是头痛怎么办？"})
#     assert response.status_code == 200
#     assert response.json() == {"response": "这是健康建议。"}

# def test_chat_stream_endpoint(monkeypatch):
#     monkeypatch.setattr(assistant, "chat_stream", mock_chat_stream)
    
#     with client.stream("POST", "/v1/chat/stream", json={"question": "心率异常怎么办？"}) as response:
#         assert response.status_code == 200
#         text = response.read().decode('utf-8')
#         assert text == "这是健康建议。"



# 假设你的 FastAPI 服务运行在 26021 端口 (根据你 health_analys_server.py 中的定义)
BASE_URL = "http://127.0.0.1:26021"

def test_chat_api():
    """测试普通阻塞式接口"""
    print("--- 测试普通接口 (/v1/chat) ---")
    url = f"{BASE_URL}/v1/chat"
    payload = {"question": "你好，请简短地介绍一下你能做什么？"}
    
    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        print("接口返回JSON:", result)
        print("回答内容:", result.get("response"))
    except Exception as e:
        print(f"普通接口测试失败: {e}")

def test_chat_stream_api():
    """测试流式生成接口"""
    print("\n--- 测试流式接口 (/v1/chat/stream) ---")
    url = f"{BASE_URL}/v1/chat/stream"
    payload = {"question": "请简单讲一下保持健康睡眠的3个建议。"}
    
    try:
        # 启用 stream=True 进行流式读取
        with requests.post(url, json=payload, stream=True, timeout=60) as response:
            response.raise_for_status()
            print("流式输出结果: ", end="")
            
            # 持续迭代读取流内容
            for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                
                print(chunk, end="", flush=True)
            print("\n[流式接收完成]")
    except Exception as e:
        print(f"流式接口测试失败: {e}")




sample_file = os.path.join(os.path.dirname(__file__), "guard_ai_upload_one_week_sample.json")
if not os.path.exists(sample_file):
    sample_file = os.path.join(os.path.dirname(__file__), "..", "guard_ai_upload_one_week_sample.json")

with open(sample_file, "r", encoding="utf-8") as f:
    payload = json.load(f)

def request_overview_blocking():
    url = f"{BASE_URL}/v1/analyze/overview"
    status_url_template = f"{BASE_URL}/v1/analyze/overview/status/{{task_id}}"
    headers = {"Content-Type": "application/json"}

    task_id = f"health-task-test-{int(time.time())}"
    request_body = {
        "analysis_type": "unified_report",
        "taskId": task_id,
        "data": payload,
    }

    print("正在发送阻塞式请求，请稍候...")
    print("taskId:", task_id)
    print("请求体:")
    print(json.dumps(request_body, ensure_ascii=False, indent=2))

    start_time = time.time()
    response = requests.post(url, json=request_body, headers=headers)
    elapsed = round(time.time() - start_time, 2)

    print(f"\n阻塞式请求耗时: {elapsed} 秒")

    try:
        status_response = requests.get(status_url_template.format(task_id=task_id), timeout=10)
        print("\n=== 当前任务状态 ===")
        print(json.dumps(status_response.json(), ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"状态查询失败: {e}")

    if response.status_code == 200:
        result = response.json()
        print("\n=== 请求成功，完整分析结果如下 ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"请求失败，状态码: {response.status_code}")
        print("错误详情:", response.text)


def request_overview_stream():
    url = f"{BASE_URL}/v1/analyze/overview/stream"
    headers = {"Content-Type": "application/json"}
    task_id = f"health-task-stream-{int(time.time())}"
    request_body = {
        "analysis_type": "unified_report",
        "taskId": task_id,
        "data": payload,
    }

    print("正在发送流式请求，接收中...\n")
    print("taskId:", task_id)
    print("=== 实时分析结果 ===")

    # 关键点：开启 stream=True
    with requests.post(url, json=request_body, headers=headers, stream=True) as response:
        if response.status_code == 200:
            print("响应头 X-Task-Id:", response.headers.get("X-Task-Id"))
            for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
                if chunk:
                    print(chunk, end="", flush=True)
            print("\n\n=== 接收完毕 ===")
        else:
            print(f"请求失败，状态码: {response.status_code}")
            print("错误详情:", response.text)


if __name__ == "__main__":
    print("开始测试 HTTP 接口服务...\n")
    # test_chat_api()
    # test_chat_stream_api()

    request_overview_blocking()
    request_overview_stream()