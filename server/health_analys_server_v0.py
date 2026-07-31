from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Iterator, Optional
from openai import OpenAI
import uvicorn

# --- 核心逻辑部分 (与 test 文件中逻辑对应) ---
class LLMProvider:
    def __init__(self, api_key: str, model: str, base_url: str):
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def chat_stream(self, messages: list[dict], system_prompt: Optional[str]=None) -> Iterator[str]:
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                stream=True,
                temperature=0.7,
                top_p=0.9
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            yield f"\n LLM 错误: {e}\n"

class HealthAssistant:
    def __init__(self, provider: LLMProvider, system_prompt: Optional[str]=None):
        self.provider = provider
        # 简化版 Prompt，可根据原文件扩展
        self.system_prompt = system_prompt or "你是一个专业的家庭健康助手..."
        self.conversation_history = []
    
    def add_user_message(self, content: str):
        self.conversation_history.append({"role": "user", "content": content})
    
    def add_assistant_message(self, content: str):
        self.conversation_history.append({"role": "assistant", "content": content})
    
    def chat_stream(self, question: str) -> Iterator[str]:
        self.add_user_message(f"用户问题：{question}")
        full_response = ""
        for chunk in self.provider.chat_stream(self.conversation_history, self.system_prompt):
            full_response += chunk
            yield chunk
        self.add_assistant_message(full_response)

# --- FastAPI 接口封装 ---
app = FastAPI(title="Health Analysis LLM Service")

# 全局初始化
API_KEY = "none"
MODEL_PATH = "/home/jinyfeng/models/Baichuan/Baichuan-M2-32B"
BASE_URL = "http://127.0.0.1:2602/v1"

provider = LLMProvider(api_key=API_KEY, model=MODEL_PATH, base_url=BASE_URL)
assistant = HealthAssistant(provider=provider)

class ChatRequest(BaseModel):
    question: str

@app.post("/v1/chat")
async def chat(request: ChatRequest):
    """阻塞式普通接口"""
    try:
        response_text = ""
        for chunk in assistant.chat_stream(request.question):
            response_text += chunk
        return {"response": response_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/chat/stream")
async def chat_stream_api(request: ChatRequest):
    """流式生成接口 (SSE)"""
    return StreamingResponse(
        assistant.chat_stream(request.question), 
        media_type="text/event-stream"
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=26021)