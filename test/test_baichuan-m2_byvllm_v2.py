import requests
import json
from typing import Iterator, Optional
from openai import OpenAI


class LLMProvider:
    def __init__(self, api_key: str, model: str, base_url: str):
        self.model = model
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key
        )

    def chat_stream(self, messages: list[dict], 
                    system_prompt: Optional[str]=None) -> Iterator[str]:
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        try:
            stream = self.client.chat.completions.create(
                model = self.model,
                messages = full_messages,
                stream = True,
                temperature = 0.7,
                top_p = 0.9
            )
            # payload = {
            #     "model": model,
            #     "prompt": prompt,
            #     "max_tokens": max_tokens,
            #     "temperature": 0.7,
            #     "top_p": 0.9
            # }
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    # print(chunk.choices[0].delta.content, end="", flush=True)
                    yield chunk.choices[0].delta.content
    
        except Exception as e:
            yield f"\n LLM 错误: {e}\n"


class HealthAssistant:
    def __init__(self, provider: LLMProvider, system_prompt: Optional[str]=None):
        self.provider = provider
        self.system_prompt = system_prompt or self._default_system_prompt()
        self.conversation_history = []
    def _default_system_prompt(self) -> str:
        """默认系统提示词"""
        return """你是一个专业的家庭健康助手。当用户提供家庭成员健康信息时，你需要：
        1. 快速理解问题核心
        2. 提供简洁、专业的回答建议
        3. 突出关键点
        4. 保持回答在2-3分钟的口述长度
        
        回答风格：
        - 直接、清晰、有条理
        - 用具体例子支撑观点
        - 避免过于冗余的理论
        
        记住：你是在帮助用户分析家庭成员的健康状况，并给出有效建议，不是在写论文。"""

    def basic_physiology_prompt(self) -> str:
        """基础生理总结分析提示词"""
        return """你是一名家庭健康助手，请基于以下用户基础生理数据（时间范围：{时间区间}），生成简洁专业的健康总结：

        【用户信息】姓名：{姓名}，年龄：{年龄}岁，性别：{性别}，身高：{身高}，体重：{体重}
        【数据内容】
        - 心率：{心率数据}
        - 呼吸率：{呼吸率数据}
        - 血压：{血压数据}
        - 体温：{体温数据}

        请按以下结构输出：
        1. 整体评价（1句话概括生理指标整体状态）
        2. 亮点发现（1-2项表现良好的指标及原因）
        3. 关注提醒（如有异常波动，说明时间规律及可能诱因，避免医疗诊断）
        4. 生活建议（2条具体、可执行的日常改善建议）

        要求：语言亲切易懂，用“您”称呼，避免专业术语堆砌，不进行疾病诊断。"""
    
    def blood_analys_prompt(self) -> str:
        """血液指标总结分析提示词"""
        return """你是一名家庭健康助手，请基于以下用户血液指标数据（时间范围：{时间区间}），生成通俗易懂的健康解读：

        【用户信息】姓名：{姓名}，年龄：{年龄}岁，性别：{性别}
        【指标数据】
        - 血糖：{血糖值及趋势}
        - 尿酸：{尿酸值及变化}
        - 血脂：{血脂各项数值}

        请按以下结构输出：
        1. 指标概览（用“正常/偏高/偏低”简明分类各项指标）
        2. 趋势分析（对比历史数据，说明改善或需关注的变化）
        3. 饮食建议（针对异常指标，提供2条具体食物调整建议）
        4. 生活提示（1条与指标相关的日常习惯提醒，如饮水、运动等）

        要求：不提及“疾病”“治疗”等医疗表述，聚焦生活方式干预；女性用户需考虑生理周期对指标的影响。"""

    def examin_report_analys_prompt(self) -> str:
        """体检报告总结分析提示词"""
        return """你是一名家庭健康助手，请整合用户近期健康数据，生成一份温暖、有激励性的月度健康报告总结：

        【用户信息】姓名：{姓名}，年龄：{年龄}岁
        【核心数据】
        - 体检总分：{分数}分
        - 健康评分分析：{评分描述}
        - 指标评价：优秀{X}项、良好{Y}项、异常{Z}项
        - 关键总结：{睡眠/血压/心率/血脂等关键点}

        请按以下结构输出：
        1. 鼓励开场（肯定用户整体健康表现，提及同龄人排名）
        2. 优势亮点（列举2项做得好的健康行为，如睡眠、活动量）
        3. 温和提醒（用“可进一步优化”代替“问题”，指出1项待改善指标）
        4. 下月小目标（设定1个具体、易达成的健康行动，如“每周增加2次快走”）

        要求：语气积极正向，避免制造焦虑；用具体行为建议替代抽象提醒；结尾给予温暖鼓励。"""
    
    def add_user_message(self, content: str):
        self.conversation_history.append({
            "role": "user",
            "content": content
        })
    
    def add_assistant_message(self, content: str):
        self.conversation_history.append({
            "role": "assistant",
            "content": content
        })
    
    def chat_stream(self, question: str) -> Iterator[str]:
        self.add_user_message(f"用户问题：{question}")

        full_response = ""
        for chunk in self.provider.chat_stream(
            self.conversation_history,
            self.system_prompt
        ):
            full_response += chunk
            yield chunk

        self.add_assistant_message(full_response)


# Test example
if __name__ == "__main__":
    
    api_key = "none"
    model_path = "/home/jinyfeng/models/Baichuan/Baichuan-M2-32B"
    url = "http://127.0.0.1:2602/v1"

    provider = LLMProvider(api_key=api_key, model=model_path, base_url=url)
    llm_assistant = HealthAssistant(provider=provider)

    question = "你好, 你能诊断哪些疾病?"

    for chunk in llm_assistant.chat_stream(question=question):
        # print(chunk)
        print(chunk, end="", flush=True)

    
    # response = query_vllm_service(prompt)
    # print(response)

    
            
    

def query_vllm_service(prompt: str, 
                       model: str = "/home/jinyfeng/models/Baichuan/Baichuan-M2-32B", 
                       max_tokens: int = 1024):
    """
    Query a vLLM deployed service
    
    Args:
        prompt: Input prompt text
        model: Model name
        max_tokens: Maximum tokens to generate
    
    Returns:
        Generated text response
    """

    url = "http://127.0.0.1:2602/v1"
    # url = "http://127.0.0.1:2602/v1/completions"

    client = OpenAI(base_url=url, api_key="none")
    stream = client.chat.completions.create(
        model = model,
        messages = [{"role": "user", "content": prompt}],
        stream = True,

    )    
    return stream
    
    
    # try:
    #     response = requests.post(url, json=payload, headers=headers)
    #     response.raise_for_status()
    #     result = response.json()
    #     print(result["choices"][0]["text"])
    #     return result["choices"][0]["text"]
    # except requests.exceptions.RequestException as e:
    #     print(f"Error: {e}")
    #     return None