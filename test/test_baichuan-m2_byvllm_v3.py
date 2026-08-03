import requests
import json
from dataclasses import dataclass
from typing import Iterator, Optional
from openai import OpenAI


@dataclass
class UserBasicInfo:
    """用户基础信息"""
    name: str
    age: int
    gender: str          # "男" 或 "女"
    height: str          # e.g. "175cm"
    weight: str          # e.g. "70kg"


@dataclass
class PhysiologyData:
    """基础生理数据"""
    heart_rate: str      # 心率数据描述，e.g. "平均75bpm，范围60-90bpm"
    breath_rate: str     # 呼吸率数据描述，e.g. "平均16次/分钟"
    blood_pressure: str  # 血压数据描述，e.g. "120/80 mmHg，波动正常"
    body_temp: str       # 体温数据描述，e.g. "36.5°C，稳定"
    time_range: str      # 时间区间，e.g. "2025-03-01 至 2025-03-31"


@dataclass
class BloodData:
    """血液指标数据"""
    blood_glucose: str   # 血糖值及趋势，e.g. "空腹5.2 mmol/L，餐后7.8 mmol/L，趋势平稳"
    uric_acid: str       # 尿酸值及变化，e.g. "380 μmol/L，较上月下降10"
    blood_lipids: str    # 血脂各项数值，e.g. "总胆固醇5.1、甘油三酯1.8、HDL1.2、LDL3.0 mmol/L"
    time_range: str      # 时间区间


@dataclass
class ExaminReport:
    """体检报告数据"""
    total_score: int           # 体检总分（0-100）
    score_description: str     # 健康评分描述
    excellent_count: int       # 优秀项数
    good_count: int            # 良好项数
    abnormal_count: int        # 异常项数
    key_summary: str           # 关键指标摘要，e.g. "睡眠良好、血压偏高、心率正常"


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
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
    
        except Exception as e:
            yield f"\n LLM 错误: {e}\n"


class HealthAssistant:
    def __init__(self, provider: LLMProvider, system_prompt: Optional[str] = None):
        self.provider = provider
        self.system_prompt = system_prompt or self._default_system_prompt()
        self.conversation_history: list[dict] = []
    def _default_system_prompt(self) -> str:
        """默认系统提示词"""
        return """你是一个专业的家庭健康助手，具备丰富的医学常识和健康管理经验。当用户提供家庭成员健康信息时，你需要：
        1. 快速理解问题核心，识别关键健康指标
        2. 提供简洁、专业且通俗易懂的分析建议
        3. 突出关键健康风险点或积极改善迹象
        4. 保持回答简洁，适合口头沟通（2-3分钟口述长度）

        回答风格：
        - 直接、清晰、有条理，使用"您"称呼用户
        - 用具体数据和例子支撑观点
        - 避免堆砌专业术语，优先使用通俗表达
        - 语气温和积极，避免制造不必要的焦虑

        重要约束：
        - 不进行疾病诊断，不替代医生意见
        - 如有严重异常指标，建议及时就医
        - 聚焦生活方式干预和日常健康管理"""

    def basic_physiology_prompt(self, user: UserBasicInfo, data: PhysiologyData) -> str:
        """基础生理总结分析提示词"""
        return f"""你是一名家庭健康助手，请基于以下用户基础生理数据，生成简洁专业的健康总结。

【用户信息】
- 姓名：{user.name}，年龄：{user.age}岁，性别：{user.gender}
- 身高：{user.height}，体重：{user.weight}

【数据时间范围】{data.time_range}

【生理指标数据】
- 心率：{data.heart_rate}
- 呼吸率：{data.breath_rate}
- 血压：{data.blood_pressure}
- 体温：{data.body_temp}

请严格按以下结构输出健康总结：

**1. 整体评价**
用1句话概括本周期生理指标的整体状态（良好 / 一般 / 需关注）。

**2. 亮点发现**
列举1-2项表现良好的指标，说明其意义及可能反映的积极生活习惯。

**3. 关注提醒**
如有异常波动，指出具体指标、时间规律及可能的诱因（如压力、睡眠、饮食），不做疾病诊断。若无异常则标注"本周期各项指标平稳"。

**4. 生活建议**
给出2条具体、可执行的日常改善建议，与当前指标直接相关。

输出要求：语言亲切易懂，用"您"称呼，避免专业术语堆砌，不进行疾病诊断，全文不超过300字。"""
    
    def blood_analys_prompt(self, user: UserBasicInfo, data: BloodData) -> str:
        """血液指标总结分析提示词"""
        gender_note = "\n注意：女性用户血液指标可能受生理周期影响，请综合考量。" if user.gender == "女" else ""
        return f"""你是一名家庭健康助手，请基于以下用户血液指标数据，生成通俗易懂的健康解读。{gender_note}

【用户信息】
- 姓名：{user.name}，年龄：{user.age}岁，性别：{user.gender}

【数据时间范围】{data.time_range}

【血液指标数据】
- 血糖：{data.blood_glucose}
- 尿酸：{data.uric_acid}
- 血脂：{data.blood_lipids}

请严格按以下结构输出健康解读：

**1. 指标概览**
用"正常 / 偏高 / 偏低"对各项指标进行简明分类，并标注各自的参考范围。

**2. 趋势分析**
对比数据变化趋势，说明改善方向或需持续关注的项目，指出变化幅度是否显著。

**3. 饮食建议**
针对偏高或偏低的指标，给出2条具体的食物调整建议（明确"建议多吃/少吃XX"及理由）。

**4. 生活提示**
给出1条与当前指标最相关的日常习惯提醒（如饮水量、运动类型、作息规律等）。

输出要求：不使用"疾病""治疗"等医疗表述，聚焦生活方式干预；如有指标显著偏离正常范围，建议咨询医生。全文不超过350字。"""

    def examin_report_analys_prompt(self, user: UserBasicInfo, report: ExaminReport) -> str:
        """体检报告总结分析提示词"""
        return f"""你是一名家庭健康助手，请整合用户近期体检数据，生成一份温暖、有激励性的月度健康报告总结。

【用户信息】
- 姓名：{user.name}，年龄：{user.age}岁

【体检核心数据】
- 体检总分：{report.total_score}分（满分100分）
- 健康评分说明：{report.score_description}
- 指标评价：优秀 {report.excellent_count} 项、良好 {report.good_count} 项、需关注 {report.abnormal_count} 项
- 关键指标摘要：{report.key_summary}

请严格按以下结构输出月度健康报告：

**1. 鼓励开场**
以积极正向的语气肯定用户整体健康表现，结合总分给出同龄人横向对比参考（如"在同龄人中属于良好水平"）。

**2. 优势亮点**
列举2项具体表现良好的健康指标或生活习惯，说明其对长期健康的积极意义。

**3. 温和提醒**
用"可进一步优化"的正向表述，指出 {report.abnormal_count} 项需关注指标中最重要的1项，说明其潜在影响及改善方向，避免制造焦虑。

**4. 下月小目标**
设定1个具体、易达成的健康行动目标（量化描述，如"每周增加2次20分钟快走"），与需关注指标直接相关。

**5. 温馨结语**
用1句鼓励性话语收尾，强调健康是长期积累的过程。

输出要求：语气积极温暖；用具体行为建议替代抽象提醒；全文不超过400字。"""
    
    def add_user_message(self, content: str) -> None:
        self.conversation_history.append({
            "role": "user",
            "content": content
        })

    def add_assistant_message(self, content: str) -> None:
        self.conversation_history.append({
            "role": "assistant",
            "content": content
        })

    def analyze_physiology_stream(self, user: UserBasicInfo, data: PhysiologyData) -> Iterator[str]:
        """流式分析基础生理数据"""
        prompt = self.basic_physiology_prompt(user, data)
        messages = [{"role": "user", "content": prompt}]
        full_response = ""
        for chunk in self.provider.chat_stream(messages):
            full_response += chunk
            yield chunk
        self.add_assistant_message(full_response)

    def analyze_blood_stream(self, user: UserBasicInfo, data: BloodData) -> Iterator[str]:
        """流式分析血液指标数据"""
        prompt = self.blood_analys_prompt(user, data)
        messages = [{"role": "user", "content": prompt}]
        full_response = ""
        for chunk in self.provider.chat_stream(messages):
            full_response += chunk
            yield chunk
        self.add_assistant_message(full_response)

    def analyze_examin_report_stream(self, user: UserBasicInfo, report: ExaminReport) -> Iterator[str]:
        """流式分析体检报告"""
        prompt = self.examin_report_analys_prompt(user, report)
        messages = [{"role": "user", "content": prompt}]
        full_response = ""
        for chunk in self.provider.chat_stream(messages):
            full_response += chunk
            yield chunk
        self.add_assistant_message(full_response)
    
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