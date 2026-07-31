import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterator, Optional
from openai import OpenAI
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import uvicorn


# ---------------------------------------------------------------------------
# 请求类型枚举
# ---------------------------------------------------------------------------

class AnalysisType(str, Enum):
    SLEEP_PHYSIOLOGY = "sleep_physiology"   # 睡眠与生理分析
    BLOOD_ANALYSIS   = "blood_analysis"     # 血液指标分析
    EXAM_REPORT      = "exam_report"        # 综合体检报告
    UNIFIED_REPORT   = "unified_report"     # 统一综合健康报告


class ChatRequest(BaseModel):
    question: str

# ---------------------------------------------------------------------------
# JSON 数据结构模型
# ---------------------------------------------------------------------------

@dataclass
class SleepRecord:
    """单日睡眠记录，对应 sleepSummary.sleepRecords[] 中的一条"""
    date: str
    hasValidSleepReport: bool
    bedTime: Optional[str] = None
    wakeUpTime: Optional[str] = None
    sleepDurationHours: Optional[float] = None
    sleepOnsetLatencyMinutes: Optional[int] = None
    sleepEfficiency: Optional[float] = None
    sleepScore: Optional[float] = None
    physiologicalScore: Optional[float] = None
    deepSleepHours: Optional[float] = None
    lightSleepHours: Optional[float] = None
    remSleepHours: Optional[float] = None
    awakeDurationMinutes: Optional[int] = None
    outBedCount: Optional[float] = None
    outBedDurationMinutes: Optional[float] = None
    apnoeaCount: Optional[int] = None
    averageHeartRate: Optional[float] = None
    minHeartRate: Optional[float] = None
    maxHeartRate: Optional[float] = None
    heartRateAlarmCount: Optional[int] = None
    averageBreathingRate: Optional[float] = None
    minBreathingRate: Optional[float] = None
    maxBreathingRate: Optional[float] = None
    breathingAlarmCount: Optional[int] = None


@dataclass
class SleepSummary:
    """sleepSummary 对象"""
    reportDays: int
    validReportDays: int
    noReportDays: int
    hasSleepRecords: bool
    sleepRecords: list[SleepRecord] = field(default_factory=list)


@dataclass
class HealthCheckRecord:
    """单次体检记录，对应 healthCheckRecords[] 中的一条"""
    type: str
    upTime: str
    createTime: str
    bloodGlucose: Optional[str] = None   # 血糖 mmol/L
    uricAcid: Optional[str] = None       # 尿酸 μmol/L
    pressureS: Optional[str] = None      # 收缩压 mmHg
    pressureD: Optional[str] = None      # 舒张压 mmHg
    pressureRate: Optional[str] = None   # 心率 bpm
    bloodfatTc: Optional[str] = None     # 总胆固醇 mmol/L
    bloodfatHdl: Optional[str] = None    # HDL mmol/L
    bloodfatTg: Optional[str] = None     # 甘油三酯 mmol/L
    bloodfatLdl: Optional[str] = None    # LDL mmol/L


@dataclass
class AnalysisRequest:
    """统一分析请求，包含请求类型和 JSON 原始数据"""
    analysis_type: AnalysisType
    healthCheckRecords: list[HealthCheckRecord] = field(default_factory=list)
    sleepSummary: Optional[SleepSummary] = None

    @classmethod
    def from_json(cls, analysis_type: AnalysisType, data: dict) -> "AnalysisRequest":
        """从接口传入的 JSON dict 构建请求对象"""
        sleep_summary = None
        if "sleepSummary" in data:
            ss = data["sleepSummary"]
            sleep_records = [SleepRecord(**r) for r in ss.get("sleepRecords", [])]
            sleep_summary = SleepSummary(
                reportDays=ss.get("reportDays", 0),
                validReportDays=ss.get("validReportDays", 0),
                noReportDays=ss.get("noReportDays", 0),
                hasSleepRecords=ss.get("hasSleepRecords", False),
                sleepRecords=sleep_records,
            )
        health_records = [HealthCheckRecord(**r) for r in data.get("healthCheckRecords", [])]
        return cls(
            analysis_type=analysis_type,
            healthCheckRecords=health_records,
            sleepSummary=sleep_summary,
        )


# ---------------------------------------------------------------------------
# LLM 提供者
# ---------------------------------------------------------------------------

class LLMProvider:
    def __init__(self, api_key: str, model: str, base_url: str):
        self.model = model
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=0,
        )

    def _build_messages(self, messages: list[dict], system_prompt: Optional[str] = None) -> list[dict]:
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        return full_messages

    def chat(self, messages: list[dict], system_prompt: Optional[str] = None) -> str:
        full_messages = self._build_messages(messages, system_prompt)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                stream=False,
                temperature=0.2,
                top_p=0.8,
                max_tokens=900,
            )
            if response.choices and response.choices[0].message:
                return response.choices[0].message.content or ""
            return ""
        except Exception as e:
            return f"\n LLM 错误: {e}\n"

    def chat_stream(self, messages: list[dict], 
                    system_prompt: Optional[str]=None) -> Iterator[str]:
        full_messages = self._build_messages(messages, system_prompt)

        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                stream=True,
                temperature=0.2,
                top_p=0.8,
                max_tokens=900,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            yield f"\n LLM 错误: {e}\n"


# ---------------------------------------------------------------------------
# 健康助手
# ---------------------------------------------------------------------------

class HealthAssistant:
    def __init__(self, provider: LLMProvider, system_prompt: Optional[str] = None):
        self.provider = provider
        self.system_prompt = system_prompt or self._default_system_prompt()
        self.conversation_history: list[dict] = []

    # ------------------------------------------------------------------
    # 系统提示词
    # ------------------------------------------------------------------

    def _default_system_prompt(self) -> str:
        """默认系统提示词"""
        return """你是一个专业的家庭健康助手，具备丰富的医学常识和健康管理经验。当用户提供家庭成员健康数据时，你需要：
        1. 快速理解数据核心，识别关键健康指标
        2. 提供简洁、专业且通俗易懂的分析建议
        3. 突出关键健康风险点或积极改善迹象
        4. 保持回答简洁，适合口头沟通（2-3分钟口述长度）

        回答风格：
        - 直接、清晰、有条理，使用"您"称呼用户
        - 用具体数据支撑观点，避免堆砌专业术语
        - 语气温和积极，避免制造不必要的焦虑

        重要约束：
        - 不进行疾病诊断，不替代医生意见
        - 如有严重异常指标，建议及时就医
        - 聚焦生活方式干预和日常健康管理
        - 直接输出最终结果，禁止输出思考过程、推理步骤或 `<think>` 标签"""

    # ------------------------------------------------------------------
    # Prompt 构建（按请求类型）
    # ------------------------------------------------------------------

    def _sleep_physiology_prompt(self, request: AnalysisRequest) -> str:
        """睡眠与生理分析提示词，使用 sleepSummary.sleepRecords 数据"""
        ss = request.sleepSummary
        if ss is None or not ss.sleepRecords:
            return "暂无睡眠数据，无法生成睡眠与生理分析报告。"

        valid = [r for r in ss.sleepRecords if r.hasValidSleepReport]
        records_text = ""
        for r in valid:
            records_text += (
                f"\n  [{r.date}] 入睡{r.bedTime}→起床{r.wakeUpTime}，"
                f"睡眠{r.sleepDurationHours}h（深睡{r.deepSleepHours}h / 浅睡{r.lightSleepHours}h / REM{r.remSleepHours}h），"
                f"入睡潜伏{r.sleepOnsetLatencyMinutes}min，效率{r.sleepEfficiency}%，评分{r.sleepScore}，"
                f"觉醒{r.awakeDurationMinutes}min，离床{int(r.outBedCount or 0)}次，呼吸暂停{r.apnoeaCount}次，"
                f"心率均值/最小/最大={r.averageHeartRate}/{r.minHeartRate}/{r.maxHeartRate} bpm"
                f"（报警{r.heartRateAlarmCount}次），"
                f"呼吸率均值/最小/最大={r.averageBreathingRate}/{r.minBreathingRate}/{r.maxBreathingRate} 次/min"
                f"（报警{r.breathingAlarmCount}次）"
            )

        return f"""你是一名家庭健康助手，请基于以下用户近期睡眠与夜间生理数据，生成简洁专业的健康总结。

【数据概况】统计周期共 {ss.reportDays} 天，有效睡眠记录 {ss.validReportDays} 天，缺失 {ss.noReportDays} 天

【逐日有效睡眠记录】{records_text}

请严格按以下结构输出健康总结：

**1. 整体评价**
用1句话概括本周期睡眠质量的整体状态（优良 / 一般 / 需关注）。

**2. 亮点发现**
列举1-2项表现良好的睡眠或生理指标，说明其对健康的积极意义。

**3. 关注提醒**
针对异常项（如呼吸暂停次数偏多、睡眠效率偏低、心率报警等），指出具体日期规律及可能诱因，
不做疾病诊断。若无异常则标注"本周期各项指标平稳"。

**4. 生活建议**
给出2条具体、可执行的日常改善建议，与当前睡眠指标直接相关。

输出要求：语言亲切易懂，用"您"称呼，不进行疾病诊断，全文不超过350字。"""

    def _blood_analysis_prompt(self, request: AnalysisRequest) -> str:
        """血液指标分析提示词，使用 healthCheckRecords 数据"""
        records = request.healthCheckRecords
        if not records:
            return "暂无血液检测数据，无法生成血液指标分析报告。"

        records_text = ""
        for r in records:
            records_text += (
                f"\n  [{r.upTime}] "
                f"血糖{r.bloodGlucose} mmol/L，尿酸{r.uricAcid} μmol/L，"
                f"血压{r.pressureS}/{r.pressureD} mmHg（心率{r.pressureRate} bpm），"
                f"总胆固醇{r.bloodfatTc} mmol/L，HDL{r.bloodfatHdl}，甘油三酯{r.bloodfatTg}，LDL{r.bloodfatLdl}"
            )

        return f"""你是一名家庭健康助手，请基于以下用户近期血液及体征检测数据，生成通俗易懂的健康解读。

【检测记录（共 {len(records)} 次）】{records_text}

参考范围（成人）：
- 空腹血糖：3.9-6.1 mmol/L
- 尿酸：男性 < 420 μmol/L，女性 < 360 μmol/L
- 血压：< 130/85 mmHg（理想值）
- 总胆固醇：< 5.2 mmol/L；HDL > 1.0 mmol/L；甘油三酯 < 1.7 mmol/L；LDL < 3.4 mmol/L

请严格按以下结构输出健康解读：

**1. 指标概览**
用"正常 / 偏高 / 偏低"对各项指标逐一分类，并标注偏离正常范围的程度。

**2. 趋势分析**
对比多次检测数据的变化趋势，指出改善方向或需持续关注的项目。

**3. 饮食建议**
针对偏高或偏低的指标，给出2条具体的食物调整建议（明确"建议多吃/少吃XX"及理由）。

**4. 生活提示**
给出1条与当前指标最相关的日常习惯提醒（如饮水量、运动类型、作息规律等）。

输出要求：不使用"疾病""治疗"等医疗表述；如有指标显著偏离正常范围，建议咨询医生；全文不超过400字。"""

    def _exam_report_prompt(self, request: AnalysisRequest) -> str:
        """综合体检报告提示词，整合睡眠与血液两类数据"""
        ss = request.sleepSummary
        records = request.healthCheckRecords

        # 睡眠摘要
        if ss and ss.validReportDays > 0:
            valid = [r for r in ss.sleepRecords if r.hasValidSleepReport]
            avg_score = round(sum(r.sleepScore for r in valid if r.sleepScore) / len(valid), 1) if valid else None
            avg_efficiency = round(sum(r.sleepEfficiency for r in valid if r.sleepEfficiency) / len(valid), 1) if valid else None
            total_apnoea = sum(r.apnoeaCount for r in valid if r.apnoeaCount)
            sleep_text = (
                f"有效记录 {ss.validReportDays}/{ss.reportDays} 天，"
                f"平均睡眠评分 {avg_score}，平均睡眠效率 {avg_efficiency}%，"
                f"累计呼吸暂停 {total_apnoea} 次"
            )
        else:
            sleep_text = "无有效睡眠数据"

        # 血液指标摘要（取最新一条）
        if records:
            latest = records[-1]
            blood_text = (
                f"最近检测（{latest.upTime}）："
                f"血糖{latest.bloodGlucose} mmol/L，尿酸{latest.uricAcid} μmol/L，"
                f"血压{latest.pressureS}/{latest.pressureD} mmHg，"
                f"总胆固醇{latest.bloodfatTc} mmol/L，LDL{latest.bloodfatLdl} mmol/L"
            )
        else:
            blood_text = "无血液检测数据"

        return f"""你是一名家庭健康助手，请整合用户近期健康数据，生成一份温暖、有激励性的综合健康报告。

【睡眠与生理摘要】{sleep_text}

【血液与体征摘要】{blood_text}

请严格按以下结构输出综合健康报告：

**1. 鼓励开场**
以积极正向的语气概括用户整体健康状态（1-2句话）。

**2. 优势亮点**
列举2项具体表现良好的健康指标或生活习惯，说明其对长期健康的积极意义。

**3. 温和提醒**
用"可进一步优化"的正向表述，指出最需关注的1项指标，说明其潜在影响及改善方向，避免制造焦虑。

**4. 下阶段小目标**
设定1个具体、易达成的健康行动目标（量化描述，如"每周增加2次20分钟快走"），与需关注指标直接相关。

**5. 温馨结语**
用1句鼓励性话语收尾，强调健康是长期积累的过程。

输出要求：语气积极温暖；用具体行为建议替代抽象提醒；全文不超过450字。"""

    def _unified_health_report_prompt(self, request: AnalysisRequest) -> str:
        """统一综合健康报告提示词：改为压缩摘要输入，减少生成耗时"""
        ss = request.sleepSummary
        records = request.healthCheckRecords

        def _avg(values: list[Optional[float]]) -> str:
            nums = [float(v) for v in values if v is not None]
            return str(round(sum(nums) / len(nums), 1)) if nums else "暂无"

        def _range_text(values: list[Optional[str]]) -> str:
            nums = []
            for value in values:
                if value in (None, ""):
                    continue
                try:
                    nums.append(float(value))
                except (TypeError, ValueError):
                    continue
            if not nums:
                return "暂无"
            if len(nums) == 1:
                return str(round(nums[0], 2))
            return f"{round(min(nums), 2)}~{round(max(nums), 2)}"

        if ss and ss.sleepRecords:
            valid = [r for r in ss.sleepRecords if r.hasValidSleepReport]
            if valid:
                abnormal_days = []
                for r in valid:
                    issues = []
                    if r.sleepOnsetLatencyMinutes is not None and r.sleepOnsetLatencyMinutes > 20:
                        issues.append(f"入睡慢{r.sleepOnsetLatencyMinutes}min")
                    if r.sleepEfficiency is not None and r.sleepEfficiency < 85:
                        issues.append(f"效率{r.sleepEfficiency}%")
                    if r.awakeDurationMinutes is not None and r.awakeDurationMinutes > 30:
                        issues.append(f"觉醒{r.awakeDurationMinutes}min")
                    if r.apnoeaCount is not None and r.apnoeaCount > 2:
                        issues.append(f"呼吸暂停{r.apnoeaCount}次")
                    if issues:
                        abnormal_days.append(f"{r.date}({';'.join(issues)})")

                sleep_section = (
                    f"周期{ss.reportDays}天，有效{ss.validReportDays}天，缺失{ss.noReportDays}天。\n"
                    f"平均睡眠时长{_avg([r.sleepDurationHours for r in valid])}h，平均入睡潜伏{_avg([r.sleepOnsetLatencyMinutes for r in valid])}min，"
                    f"平均效率{_avg([r.sleepEfficiency for r in valid])}%，平均评分{_avg([r.sleepScore for r in valid])}。\n"
                    f"平均深睡{_avg([r.deepSleepHours for r in valid])}h，平均REM{_avg([r.remSleepHours for r in valid])}h，累计呼吸暂停{sum((r.apnoeaCount or 0) for r in valid)}次。\n"
                    f"夜间平均心率{_avg([r.averageHeartRate for r in valid])}bpm，夜间平均呼吸率{_avg([r.averageBreathingRate for r in valid])}次/分。\n"
                    f"异常日期：{'；'.join(abnormal_days[:5]) if abnormal_days else '无明显异常'}。"
                )
            else:
                sleep_section = "暂无有效睡眠记录"
        else:
            sleep_section = "暂无睡眠数据"

        if records:
            latest = records[-1]
            blood_section = (
                f"共{len(records)}次检测。血糖范围{_range_text([r.bloodGlucose for r in records])}mmol/L，"
                f"尿酸范围{_range_text([r.uricAcid for r in records])}μmol/L，"
                f"收缩压范围{_range_text([r.pressureS for r in records])}mmHg，舒张压范围{_range_text([r.pressureD for r in records])}mmHg，"
                f"总胆固醇范围{_range_text([r.bloodfatTc for r in records])}mmol/L，LDL范围{_range_text([r.bloodfatLdl for r in records])}mmol/L，"
                f"甘油三酯范围{_range_text([r.bloodfatTg for r in records])}mmol/L。\n"
                f"最近一次({latest.upTime})：血糖{latest.bloodGlucose}，尿酸{latest.uricAcid}，血压{latest.pressureS}/{latest.pressureD}，"
                f"心率{latest.pressureRate}，TC{latest.bloodfatTc}，HDL{latest.bloodfatHdl}，TG{latest.bloodfatTg}，LDL{latest.bloodfatLdl}。"
            )
        else:
            blood_section = "暂无血液检测数据"

        return f"""你是一名专业的家庭健康助手。请根据下列摘要输出一份简洁、专业、可执行的健康分析，并且只输出 JSON 对象。

睡眠摘要：
{sleep_section}

血液与体征摘要：
{blood_section}

参考范围：血糖3.9-6.1；尿酸男<420/女<360；血压<130/85；总胆固醇<5.2；甘油三酯<1.7；LDL<3.4；睡眠效率≥85%；睡眠7-9h；入睡潜伏<20min；静息心率60-100；呼吸率12-20。

请严格输出以下 JSON 结构，所有字段值必须是单行字符串，不要 Markdown，不要额外解释：
{{
  "sleep_quality": {{"advantages": "", "improvements_needed": "", "suggestions": "", "summary": ""}},
  "blood_lipid": {{"suggestions": "", "summary": ""}},
  "uric_acid": {{"suggestions": "", "summary": ""}},
  "heart_rate": {{"suggestions": "", "summary": ""}},
  "breathing": {{"suggestions": "", "summary": ""}},
  "blood_pressure": {{"suggestions": "", "summary": ""}},
  "fasting_glucose": {{"suggestions": "", "summary": ""}},
  "health_overview": "",
  "checkup_recommendations": ""
}}

要求：
1. 每个字段尽量控制在1-2句内，避免重复和空话；
2. 缺失数据写“暂无相关数据，建议持续监测”；
3. 不做疾病诊断，如明显异常仅建议复查或就医；
4. 严禁输出 `<think>` 标签、推理过程或分析步骤。"""

    def _health_overview_prompt(
        self,
        current_score: float,
        previous_score: Optional[float],
        report_period: str,
        sleep_text: str,
        blood_text: str,
    ) -> str:
        """健康综合评价提示词——仿照参考回复模式生成口播式健康总结"""
        if current_score >= 90:
            level = "优秀"
        elif current_score >= 75:
            level = "良好"
        elif current_score >= 60:
            level = "一般"
        else:
            level = "需关注"

        if previous_score is not None:
            delta = round(current_score - previous_score, 1)
            if delta > 0:
                score_change = f"相比上次报告，您的健康评分提升了 {delta} 分"
            elif delta < 0:
                score_change = f"相比上次报告，您的健康评分下降了 {abs(delta)} 分"
            else:
                score_change = "相比上次报告，您的健康评分保持稳定"
        else:
            score_change = "本次为首次综合评价"

        return f"""你是一名专业的家庭健康助手。请基于以下用户{report_period}综合健康数据，生成一段温暖、有激励性的健康综合评价播报。

【综合健康分】当前评分：{current_score} 分（满分100分），整体状况：{level}
【评分变化】{score_change}
【睡眠与生理摘要】{sleep_text}
【血液与体征摘要】{blood_text}

请严格按以下语言风格和结构输出健康综合评价：

**第一句（整体定调）**
仿照示例格式输出：
"结合{report_period}的监测数据，您的整体健康状况[等级]，在同龄人中处于前[X]%的水平。"
- 等级用"优秀 / 良好 / 一般 / 需关注"之一，直接填入实际等级
- 若无同龄人百分位数据则省略同龄人对比部分

**第二句（评分变化与主要原因）**
仿照示例格式输出：
"[评分变化描述]，主要得益于[1-2个具体改善点]。" 或
"[评分变化描述]，主要原因是[1-2个待改善点]。"
- 改善点或原因须结合实际数据（如睡眠效率、呼吸暂停次数、血糖趋势、血脂变化等）
- 用通俗表达，不堆砌专业术语

**第三句（关注提醒，仅在有异常指标时输出）**
用一句话温和提示：
"需要留意的是，[具体指标] 较参考值[偏高/偏低]，建议[1条简洁可执行的生活改善建议]。"
若所有指标均在正常范围内则跳过此句。

**结语（鼓励）**
用1句温暖鼓励的话收尾，强调坚持健康习惯的长期价值。

输出要求：
- 全程使用"您"称呼，语气温暖自然，适合语音播报
- 每句话独立成行，总字数控制在150字以内
- 只输出评价正文，不加标题、编号或 Markdown 格式
- 不进行疾病诊断，不替代医生意见"""

    @staticmethod
    def _compute_health_score(
        sleep_summary: Optional["SleepSummary"],
        health_records: list["HealthCheckRecord"],
    ) -> float:
        """根据睡眠与血液数据自动计算综合健康评分（0-100）"""

        def _safe_float(val):
            try:
                return float(val) if val is not None else None
            except (ValueError, TypeError):
                return None

        components: list[tuple[float, float]] = []  # (score, weight)

        # ---- 睡眠评分（权重 0.5）----
        if sleep_summary and sleep_summary.validReportDays > 0:
            valid = [r for r in sleep_summary.sleepRecords if r.hasValidSleepReport]
            if valid:
                device_scores = [r.sleepScore for r in valid if r.sleepScore is not None]
                if device_scores:
                    # 直接使用设备计算的睡眠评分
                    components.append((sum(device_scores) / len(device_scores), 0.5))
                else:
                    sub: list[float] = []
                    # 睡眠时长（7-9h 最优）
                    durations = [r.sleepDurationHours for r in valid if r.sleepDurationHours]
                    if durations:
                        avg_dur = sum(durations) / len(durations)
                        sub.append(100 if 7 <= avg_dur <= 9 else (75 if 6 <= avg_dur < 7 or 9 < avg_dur <= 10 else 50))
                    # 睡眠效率
                    effs = [r.sleepEfficiency for r in valid if r.sleepEfficiency]
                    if effs:
                        avg_eff = sum(effs) / len(effs)
                        sub.append(100 if avg_eff >= 85 else (75 if avg_eff >= 75 else 50))
                    # 呼吸暂停（越少越好）
                    apnoeas = [r.apnoeaCount for r in valid if r.apnoeaCount is not None]
                    if apnoeas:
                        avg_ap = sum(apnoeas) / len(apnoeas)
                        sub.append(100 if avg_ap <= 2 else (75 if avg_ap <= 5 else 50))
                    # 心率报警（越少越好）
                    hr_alarms = [r.heartRateAlarmCount for r in valid if r.heartRateAlarmCount is not None]
                    if hr_alarms:
                        avg_hral = sum(hr_alarms) / len(hr_alarms)
                        sub.append(100 if avg_hral == 0 else (75 if avg_hral <= 2 else 50))
                    if sub:
                        components.append((sum(sub) / len(sub), 0.5))

        # ---- 血液评分（权重 0.5）----
        if health_records:
            latest = health_records[-1]
            sub = []
            # 空腹血糖
            bg = _safe_float(latest.bloodGlucose)
            if bg is not None:
                sub.append(100 if 3.9 <= bg <= 6.1 else (70 if bg <= 7.0 else 40))
            # 血压
            ps = _safe_float(latest.pressureS)
            pd = _safe_float(latest.pressureD)
            if ps is not None and pd is not None:
                sub.append(100 if ps < 130 and pd < 85 else (70 if ps < 140 and pd < 90 else 40))
            # 总胆固醇
            tc = _safe_float(latest.bloodfatTc)
            if tc is not None:
                sub.append(100 if tc < 4.5 else (75 if tc < 5.2 else 40))
            # LDL
            ldl = _safe_float(latest.bloodfatLdl)
            if ldl is not None:
                sub.append(100 if ldl < 2.6 else (75 if ldl < 3.4 else 40))
            # 甘油三酯
            tg = _safe_float(latest.bloodfatTg)
            if tg is not None:
                sub.append(100 if tg < 1.7 else (70 if tg < 2.3 else 40))
            # 尿酸
            ua = _safe_float(latest.uricAcid)
            if ua is not None:
                sub.append(100 if ua < 360 else (70 if ua < 420 else 40))
            if sub:
                components.append((sum(sub) / len(sub), 0.5))

        if not components:
            return 75.0  # 数据不足时返回默认分

        total_weight = sum(w for _, w in components)
        weighted_sum = sum(s * w for s, w in components)
        return round(weighted_sum / total_weight, 1)

    def analyze_overview_stream(
        self,
        current_score: Optional[float],
        previous_score: Optional[float],
        report_period: str,
        health_data: dict,
    ) -> Iterator[str]:
        """流式生成健康综合评价（current_score 为 None 时自动从数据计算）"""
        ss_raw = health_data.get("sleepSummary")
        records_raw = health_data.get("healthCheckRecords", [])

        # 解析睡眠数据
        sleep_summary_obj: Optional[SleepSummary] = None
        if ss_raw and ss_raw.get("validReportDays", 0) > 0:
            sleep_records = [SleepRecord(**r) for r in ss_raw.get("sleepRecords", [])]
            sleep_summary_obj = SleepSummary(
                reportDays=ss_raw.get("reportDays", 0),
                validReportDays=ss_raw.get("validReportDays", 0),
                noReportDays=ss_raw.get("noReportDays", 0),
                hasSleepRecords=ss_raw.get("hasSleepRecords", False),
                sleepRecords=sleep_records,
            )
            valid = [r for r in sleep_records if r.hasValidSleepReport]
            avg_score = round(sum(r.sleepScore for r in valid if r.sleepScore) / len(valid), 1) if valid else None
            avg_eff = round(sum(r.sleepEfficiency for r in valid if r.sleepEfficiency) / len(valid), 1) if valid else None
            total_ap = sum(r.apnoeaCount for r in valid if r.apnoeaCount)
            sleep_text = (
                f"有效记录 {ss_raw['validReportDays']}/{ss_raw['reportDays']} 天，"
                f"平均睡眠评分 {avg_score}，平均睡眠效率 {avg_eff}%，"
                f"累计呼吸暂停 {total_ap} 次"
            )
        else:
            sleep_text = "无有效睡眠数据"

        # 解析血液数据
        health_record_objs = [HealthCheckRecord(**r) for r in records_raw] if records_raw else []
        if health_record_objs:
            r = health_record_objs[-1]
            blood_text = (
                f"最近检测（{r.upTime}）："
                f"血糖{r.bloodGlucose} mmol/L，尿酸{r.uricAcid} μmol/L，"
                f"血压{r.pressureS}/{r.pressureD} mmHg，"
                f"总胆固醇{r.bloodfatTc} mmol/L，LDL{r.bloodfatLdl} mmol/L"
            )
        else:
            blood_text = "无血液检测数据"

        # 自动计算健康评分（如未由调用方提供）
        if current_score is None:
            current_score = self._compute_health_score(sleep_summary_obj, health_record_objs)

        prompt = self._health_overview_prompt(
            current_score, previous_score,
            report_period, sleep_text, blood_text
        )
        messages = [{"role": "user", "content": prompt}]
        full_response = ""
        for chunk in self.provider.chat_stream(messages, self.system_prompt):
            full_response += chunk
            yield chunk
        self.add_assistant_message(full_response)

    # ------------------------------------------------------------------
    # 分析入口（按 AnalysisRequest 分发）
    # ------------------------------------------------------------------

    def analyze_stream(self, request: AnalysisRequest) -> Iterator[str]:
        """根据 analysis_type 分发到对应分析方法，返回流式生成器"""
        dispatch = {
            AnalysisType.SLEEP_PHYSIOLOGY: self._sleep_physiology_prompt,
            AnalysisType.BLOOD_ANALYSIS:   self._blood_analysis_prompt,
            AnalysisType.EXAM_REPORT:      self._exam_report_prompt,
            AnalysisType.UNIFIED_REPORT:   self._unified_health_report_prompt,
        }
        prompt = dispatch[request.analysis_type](request)
        messages = [{"role": "user", "content": prompt}]

        full_response = ""
        for chunk in self.provider.chat_stream(messages, self.system_prompt):
            full_response += chunk
            yield chunk
        self.add_assistant_message(full_response)

    def analyze_once(self, request: AnalysisRequest) -> str:
        """阻塞式分析接口使用非流式生成，减少逐块传输开销"""
        dispatch = {
            AnalysisType.SLEEP_PHYSIOLOGY: self._sleep_physiology_prompt,
            AnalysisType.BLOOD_ANALYSIS: self._blood_analysis_prompt,
            AnalysisType.EXAM_REPORT: self._exam_report_prompt,
            AnalysisType.UNIFIED_REPORT: self._unified_health_report_prompt,
        }
        prompt = dispatch[request.analysis_type](request)
        messages = [{"role": "user", "content": prompt}]
        full_response = self.provider.chat(messages, self.system_prompt)
        self.add_assistant_message(full_response)
        return full_response

    # ------------------------------------------------------------------
    # 对话管理 & 通用问答
    # ------------------------------------------------------------------

    def add_user_message(self, content: str) -> None:
        self.conversation_history.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        self.conversation_history.append({"role": "assistant", "content": content})

    def chat_stream(self, question: str) -> Iterator[str]:
        """通用问答流式接口"""
        self.add_user_message(f"用户问题：{question}")
        full_response = ""
        for chunk in self.provider.chat_stream(self.conversation_history, self.system_prompt):
            full_response += chunk
            yield chunk
        self.add_assistant_message(full_response)


# ---------------------------------------------------------------------------
# FastAPI 服务
# ---------------------------------------------------------------------------

# 全局初始化
API_KEY    = "none"
MODEL_PATH = "/home/jinyfeng/models/Baichuan/Baichuan-M2-32B"
BASE_URL   = "http://127.0.0.1:2602/v1"

provider  = LLMProvider(api_key=API_KEY, model=MODEL_PATH, base_url=BASE_URL)
assistant = HealthAssistant(provider=provider)

app = FastAPI(title="Health Analysis LLM Service")

TASK_STATUS_STORE: dict[str, dict] = {}


def now_iso() -> str:
    """返回当前时间的 ISO 字符串"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def resolve_task_id(task_id: str) -> str:
    """taskId 为必填项，且不能为空白字符串"""
    cleaned = task_id.strip()
    if not cleaned:
        raise HTTPException(
            status_code=422,
            detail=[{
                "type": "value_error",
                "loc": ["body", "taskId"],
                "msg": "`taskId` 不能为空字符串"
            }]
        )
    return cleaned


def update_task_status(
    task_id: str,
    status: str,
    message: str,
    *,
    analysis_type: Optional[str] = None,
    result_ready: Optional[bool] = None,
    error: Optional[object] = None,
) -> dict:
    """记录任务状态，便于轮询查询"""
    task = TASK_STATUS_STORE.get(task_id, {
        "taskId": task_id,
        "status": "pending",
        "message": "任务已创建",
        "analysisType": analysis_type,
        "resultReady": False,
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
    })

    task["status"] = status
    task["message"] = message
    task["updatedAt"] = now_iso()
    if analysis_type is not None:
        task["analysisType"] = analysis_type
    if result_ready is not None:
        task["resultReady"] = result_ready
    if status == "running" and "startedAt" not in task:
        task["startedAt"] = now_iso()
    if status in {"completed", "failed"}:
        task["completedAt"] = now_iso()
    if error is not None:
        task["error"] = error
    elif "error" in task and status != "failed":
        task.pop("error", None)

    TASK_STATUS_STORE[task_id] = task
    return task


def resolve_report_period(total_days: Optional[int], fallback: str = "近一周") -> str:
    """根据 totalDays 自动映射报告周期描述"""
    if total_days is None:
        return fallback
    if total_days == 7:
        return "近1周"
    if 28 <= total_days <= 31:
        return "近一个月"
    if 80 <= total_days <= 93:
        return "3个月"
    return fallback or f"近{total_days}天"


def get_sleep_records_from_summary(sleep_summary: Optional[dict]) -> list[dict]:
    """从 request.sleepSummary 字典中安全获取 sleepRecords 数据"""
    if not isinstance(sleep_summary, dict):
        return []
    sleep_records = sleep_summary.get("sleepRecords", [])
    return sleep_records if isinstance(sleep_records, list) else []


def collect_stream_text(stream: Iterator[str]) -> str:
    """收集流式输出为完整字符串"""
    return "".join(chunk for chunk in stream)


def safe_float(value: object) -> Optional[float]:
    """安全转换数值"""
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def build_rule_based_unified_report(request: Optional[AnalysisRequest]) -> dict:
    """当大模型返回不稳定内容时，使用规则生成一份稳定的结构化结果"""
    fallback = "暂无相关数据，建议持续监测"
    result = {
        "睡眠质量分析结果": {"advantages": fallback, "improvements_needed": fallback, "suggestions": fallback, "summary": fallback},
        "血脂分析结果": {"suggestions": fallback, "summary": fallback},
        "尿酸分析结果": {"suggestions": fallback, "summary": fallback},
        "心率分析结果": {"suggestions": fallback, "summary": fallback},
        "呼吸分析结果": {"suggestions": fallback, "summary": fallback},
        "血压分析结果": {"suggestions": fallback, "summary": fallback},
        "空腹血糖分析结果": {"suggestions": fallback, "summary": fallback},
        "健康综合评价": fallback,
        "体检建议": fallback,
    }

    if request is None:
        return result

    ss = request.sleepSummary
    records = request.healthCheckRecords

    def fmt_num(value: Optional[float], digits: int = 1) -> str:
        return str(round(value, digits)) if value is not None else "暂无"

    if ss and ss.sleepRecords:
        valid = [r for r in ss.sleepRecords if r.hasValidSleepReport]
        if valid:
            durations = [r.sleepDurationHours for r in valid if r.sleepDurationHours is not None]
            efficiencies = [r.sleepEfficiency for r in valid if r.sleepEfficiency is not None]
            latencies = [r.sleepOnsetLatencyMinutes for r in valid if r.sleepOnsetLatencyMinutes is not None]
            awake_minutes = [r.awakeDurationMinutes for r in valid if r.awakeDurationMinutes is not None]
            apnea_counts = [r.apnoeaCount or 0 for r in valid]
            heart_rates = [r.averageHeartRate for r in valid if r.averageHeartRate is not None]
            breathing_rates = [r.averageBreathingRate for r in valid if r.averageBreathingRate is not None]

            avg_duration = sum(durations) / len(durations) if durations else None
            avg_eff = sum(efficiencies) / len(efficiencies) if efficiencies else None
            avg_latency = sum(latencies) / len(latencies) if latencies else None
            avg_hr = sum(heart_rates) / len(heart_rates) if heart_rates else None
            avg_br = sum(breathing_rates) / len(breathing_rates) if breathing_rates else None
            total_apnea = sum(apnea_counts)
            max_awake = max(awake_minutes) if awake_minutes else None

            advantages = []
            if avg_duration is not None and 7 <= avg_duration <= 9:
                advantages.append(f"平均睡眠时长约{fmt_num(avg_duration)}小时，达到建议范围")
            if avg_eff is not None and avg_eff >= 85:
                advantages.append(f"平均睡眠效率约{fmt_num(avg_eff)}%，整体睡眠连续性较好")
            if avg_hr is not None and 55 <= avg_hr <= 70:
                advantages.append(f"夜间平均心率约{fmt_num(avg_hr)}bpm，整体较平稳")
            if not advantages:
                advantages.append("睡眠时长与夜间生理指标整体可用于持续观察")

            improvements = []
            if avg_latency is not None and avg_latency > 20:
                improvements.append(f"平均入睡潜伏约{fmt_num(avg_latency)}分钟，入睡稍慢")
            if avg_eff is not None and avg_eff < 85:
                improvements.append(f"平均睡眠效率约{fmt_num(avg_eff)}%，低于85%的建议值")
            if total_apnea > max(2, len(valid) * 2):
                improvements.append(f"累计呼吸暂停约{total_apnea}次，建议持续关注夜间呼吸情况")
            if max_awake is not None and max_awake > 30:
                improvements.append(f"部分夜晚觉醒时长可达{fmt_num(max_awake, 0)}分钟")
            if not improvements:
                improvements.append("各项睡眠指标整体平稳")

            suggestions = []
            if avg_latency is not None and avg_latency > 20:
                suggestions.append("建议固定上床时间，睡前1小时减少电子屏幕刺激")
            if avg_eff is not None and avg_eff < 85:
                suggestions.append("建议保持卧室安静黑暗，晚间避免咖啡因和过饱饮食")
            if total_apnea > max(2, len(valid) * 2):
                suggestions.append("建议优先采取侧卧睡姿，如持续打鼾或憋醒可做睡眠呼吸监测")
            if not suggestions:
                suggestions.append("建议继续保持规律作息并持续监测睡眠趋势")

            level = "良好" if (avg_eff or 0) >= 85 and total_apnea <= len(valid) else ("一般" if (avg_duration or 0) >= 6 else "需关注")
            result["睡眠质量分析结果"] = {
                "advantages": "；".join(advantages[:2]) + "。",
                "improvements_needed": "；".join(improvements[:3]) + "。",
                "suggestions": "；".join(suggestions[:2]) + "。",
                "summary": f"近{ss.reportDays}天睡眠整体{level}，平均睡眠时长约{fmt_num(avg_duration)}小时，平均效率约{fmt_num(avg_eff)}%，建议继续关注睡眠连续性与呼吸暂停情况。",
            }

    if records:
        latest = records[-1]
        tc = safe_float(latest.bloodfatTc)
        ldl = safe_float(latest.bloodfatLdl)
        tg = safe_float(latest.bloodfatTg)
        hdl = safe_float(latest.bloodfatHdl)
        ua = safe_float(latest.uricAcid)
        bg = safe_float(latest.bloodGlucose)
        ps = safe_float(latest.pressureS)
        pd = safe_float(latest.pressureD)
        pr = safe_float(latest.pressureRate)

        lipid_issues = []
        if tc is not None and tc >= 5.2:
            lipid_issues.append("总胆固醇偏高")
        if ldl is not None and ldl >= 3.4:
            lipid_issues.append("LDL偏高")
        if tg is not None and tg >= 1.7:
            lipid_issues.append("甘油三酯偏高")
        result["血脂分析结果"] = {
            "suggestions": "建议保持少油少糖饮食并坚持每周中等强度运动150分钟。" if not lipid_issues else "建议减少油炸和高饱和脂肪食物摄入，并增加快走、骑行等有氧运动。",
            "summary": f"最近一次血脂结果为TC {fmt_num(tc, 2)}、LDL {fmt_num(ldl, 2)}、TG {fmt_num(tg, 2)}、HDL {fmt_num(hdl, 2)}，" + ("整体处于参考范围内。" if not lipid_issues else f"需关注{'、'.join(lipid_issues)}。"),
        }

        result["尿酸分析结果"] = {
            "suggestions": "建议每日足量饮水，控制高嘌呤食物与酒精摄入。" if ua is None or ua < 360 else "建议增加饮水量并减少海鲜、浓汤和动物内脏摄入，必要时复查尿酸。",
            "summary": f"最近一次尿酸约{fmt_num(ua, 0)}μmol/L，" + ("总体处于参考范围内。" if ua is None or ua < 360 else "已接近或达到参考上限，需持续观察。"),
        }

        hr_flag = pr is not None and (pr < 55 or pr > 100)
        result["心率分析结果"] = {
            "suggestions": "建议保持规律作息与适度有氧运动，并结合晨起静息心率持续观察。" if not hr_flag else "建议减少熬夜和刺激性饮品，如心率持续异常可进一步复查心电情况。",
            "summary": f"最近一次心率约{fmt_num(pr, 0)}bpm，" + ("整体处于常见参考范围内。" if not hr_flag else "已偏离常见参考范围，建议持续监测。"),
        }

        bp_flag = ps is not None and pd is not None and (ps >= 130 or pd >= 85)
        result["血压分析结果"] = {
            "suggestions": "建议继续保持低盐饮食、规律运动和家庭血压记录。" if not bp_flag else "建议控制钠盐摄入、避免久坐并固定时间段复测血压。",
            "summary": f"最近一次血压约{fmt_num(ps, 0)}/{fmt_num(pd, 0)}mmHg，" + ("整体较平稳。" if not bp_flag else "已接近或超过理想值，需重点随访。"),
        }

        glucose_flag = bg is not None and not (3.9 <= bg <= 6.1)
        result["空腹血糖分析结果"] = {
            "suggestions": "建议继续保持规律三餐与餐后活动。" if not glucose_flag else "建议控制精制糖和夜宵摄入，并结合空腹复测观察波动。",
            "summary": f"最近一次空腹血糖约{fmt_num(bg, 1)}mmol/L，" + ("总体在参考范围内。" if not glucose_flag else "已偏离参考范围，建议持续监测。"),
        }

    sleep_summary = result["睡眠质量分析结果"]["summary"]
    lipid_summary = result["血脂分析结果"]["summary"]
    score = HealthAssistant._compute_health_score(ss, records)
    level = "良好" if score >= 75 else ("一般" if score >= 60 else "需关注")
    result["呼吸分析结果"] = result["呼吸分析结果"] if result["呼吸分析结果"]["summary"] != fallback else {
        "suggestions": result["睡眠质量分析结果"]["suggestions"],
        "summary": "若夜间存在打鼾、憋醒或呼吸暂停增多，建议持续关注并优化睡姿。",
    }
    if ss and ss.sleepRecords:
        valid = [r for r in ss.sleepRecords if r.hasValidSleepReport]
        apnea_total = sum((r.apnoeaCount or 0) for r in valid)
        avg_br = sum([r.averageBreathingRate for r in valid if r.averageBreathingRate is not None]) / max(1, len([r for r in valid if r.averageBreathingRate is not None])) if valid else None
        result["呼吸分析结果"] = {
            "suggestions": "建议优先侧卧睡眠、保持鼻腔通畅，并在持续打鼾时考虑睡眠呼吸监测。" if apnea_total > max(2, len(valid) * 2) else "建议继续保持良好睡姿和稳定作息。",
            "summary": f"夜间平均呼吸率约{fmt_num(avg_br)}次/分，累计呼吸暂停约{apnea_total}次，" + ("提示需进一步关注夜间呼吸稳定性。" if apnea_total > max(2, len(valid) * 2) else "整体较平稳。"),
        }

    result["健康综合评价"] = f"本次综合健康评估约为{fmt_num(score)}分，整体状态{level}。从现有数据看，血压、血糖及大部分血脂指标较平稳；当前更需要关注睡眠效率、入睡速度及夜间呼吸稳定性。"

    checkups = ["建议按年度体检节奏复查血压、血脂和血糖"]
    if ss and ss.sleepRecords:
        valid = [r for r in ss.sleepRecords if r.hasValidSleepReport]
        if sum((r.apnoeaCount or 0) for r in valid) > max(2, len(valid) * 2):
            checkups.insert(0, "建议增加睡眠呼吸监测，评估夜间呼吸暂停风险")
    if records:
        latest_ua = safe_float(records[-1].uricAcid)
        if latest_ua is not None and latest_ua >= 360:
            checkups.append("建议结合尿酸和肾功能做一次复查")
    result["体检建议"] = "；".join(checkups[:3]) + "。"

    return result


def build_unified_report_response(response_text: str, request: Optional[AnalysisRequest] = None) -> dict:
    """将统一分析返回文本组装成结构化结果，并补齐缺失字段"""

    default_result = build_rule_based_unified_report(request)

    def sanitize_llm_text(value: object, fallback: str = "暂无相关数据，建议持续监测") -> str:
        if not isinstance(value, str):
            return fallback
        text = value.strip()
        if not text:
            return fallback

        text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE)

        meta_markers = ["用户要求我", "只输出json", "json对象", "思考过程", "推理过程", "数据包括睡眠摘要", "json结构是", "现在，分析数据"]
        if any(marker in text.lower() for marker in meta_markers):
            return fallback

        for marker in ["JSON结构是：", "要求：", "现在，分析数据：", "请严格输出以下 JSON 结构"]:
            if marker in text:
                text = text.split(marker, 1)[0].strip()

        text = re.sub(r"\s+", " ", text).strip()
        return text or fallback

    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(cleaned) if cleaned else {}
    except json.JSONDecodeError:
        parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    sleep_quality = parsed.get("sleep_quality") if isinstance(parsed.get("sleep_quality"), dict) else {}
    blood_lipid = parsed.get("blood_lipid") if isinstance(parsed.get("blood_lipid"), dict) else {}
    uric_acid = parsed.get("uric_acid") if isinstance(parsed.get("uric_acid"), dict) else {}
    heart_rate = parsed.get("heart_rate") if isinstance(parsed.get("heart_rate"), dict) else {}
    breathing = parsed.get("breathing") if isinstance(parsed.get("breathing"), dict) else {}
    blood_pressure = parsed.get("blood_pressure") if isinstance(parsed.get("blood_pressure"), dict) else {}
    fasting_glucose = parsed.get("fasting_glucose") if isinstance(parsed.get("fasting_glucose"), dict) else {}

    fallback_text = sanitize_llm_text(cleaned, "") if (not parsed and cleaned) else "暂无相关数据，建议持续监测"

    result = {
        "睡眠质量分析结果": {
            "advantages": sanitize_llm_text(sleep_quality.get("advantages"), default_result["睡眠质量分析结果"]["advantages"]),
            "improvements_needed": sanitize_llm_text(sleep_quality.get("improvements_needed"), default_result["睡眠质量分析结果"]["improvements_needed"]),
            "suggestions": sanitize_llm_text(sleep_quality.get("suggestions"), default_result["睡眠质量分析结果"]["suggestions"]),
            "summary": sanitize_llm_text(sleep_quality.get("summary"), default_result["睡眠质量分析结果"]["summary"]),
        },
        "血脂分析结果": {
            "suggestions": sanitize_llm_text(blood_lipid.get("suggestions"), default_result["血脂分析结果"]["suggestions"]),
            "summary": sanitize_llm_text(blood_lipid.get("summary"), default_result["血脂分析结果"]["summary"]),
        },
        "尿酸分析结果": {
            "suggestions": sanitize_llm_text(uric_acid.get("suggestions"), default_result["尿酸分析结果"]["suggestions"]),
            "summary": sanitize_llm_text(uric_acid.get("summary"), default_result["尿酸分析结果"]["summary"]),
        },
        "心率分析结果": {
            "suggestions": sanitize_llm_text(heart_rate.get("suggestions"), default_result["心率分析结果"]["suggestions"]),
            "summary": sanitize_llm_text(heart_rate.get("summary"), default_result["心率分析结果"]["summary"]),
        },
        "呼吸分析结果": {
            "suggestions": sanitize_llm_text(breathing.get("suggestions"), default_result["呼吸分析结果"]["suggestions"]),
            "summary": sanitize_llm_text(breathing.get("summary"), default_result["呼吸分析结果"]["summary"]),
        },
        "血压分析结果": {
            "suggestions": sanitize_llm_text(blood_pressure.get("suggestions"), default_result["血压分析结果"]["suggestions"]),
            "summary": sanitize_llm_text(blood_pressure.get("summary"), default_result["血压分析结果"]["summary"]),
        },
        "空腹血糖分析结果": {
            "suggestions": sanitize_llm_text(fasting_glucose.get("suggestions"), default_result["空腹血糖分析结果"]["suggestions"]),
            "summary": sanitize_llm_text(fasting_glucose.get("summary"), default_result["空腹血糖分析结果"]["summary"]),
        },
        "健康综合评价": sanitize_llm_text(parsed.get("health_overview"), default_result["健康综合评价"]),
        "体检建议": sanitize_llm_text(parsed.get("checkup_recommendations"), default_result["体检建议"]),
    }

    if not parsed and fallback_text:
        result["原始结果"] = response_text

    return result


def validate_health_data_payload(data: dict) -> dict:
    """校验 data 中的关键字段，缺失时返回明确的 422 异常信息"""
    errors: list[dict] = []

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=422,
            detail=[{
                "type": "type_error.dict",
                "loc": ["body", "data"],
                "msg": "`data` 必须为 JSON 对象"
            }]
        )

    top_level_required = ["totalDays", "sleepSummary", "healthCheckRecords"]
    for field_name in top_level_required:
        if field_name not in data:
            errors.append({
                "type": "missing",
                "loc": ["body", "data", field_name],
                "msg": f"缺少关键字段 `{field_name}`"
            })

    sleep_summary = data.get("sleepSummary")
    if "sleepSummary" in data:
        if not isinstance(sleep_summary, dict):
            errors.append({
                "type": "type_error.dict",
                "loc": ["body", "data", "sleepSummary"],
                "msg": "`sleepSummary` 必须为 JSON 对象"
            })
        else:
            sleep_summary_required = ["reportDays", "validReportDays", "noReportDays", "hasSleepRecords", "sleepRecords"]
            for field_name in sleep_summary_required:
                if field_name not in sleep_summary:
                    errors.append({
                        "type": "missing",
                        "loc": ["body", "data", "sleepSummary", field_name],
                        "msg": f"缺少关键字段 `sleepSummary.{field_name}`"
                    })

            sleep_records = sleep_summary.get("sleepRecords")
            if "sleepRecords" in sleep_summary:
                if not isinstance(sleep_records, list):
                    errors.append({
                        "type": "type_error.list",
                        "loc": ["body", "data", "sleepSummary", "sleepRecords"],
                        "msg": "`sleepSummary.sleepRecords` 必须为数组"
                    })
                else:
                    for index, record in enumerate(sleep_records):
                        if not isinstance(record, dict):
                            errors.append({
                                "type": "type_error.dict",
                                "loc": ["body", "data", "sleepSummary", "sleepRecords", index],
                                "msg": "睡眠记录项必须为 JSON 对象"
                            })
                            continue
                        for field_name in ["date", "hasValidSleepReport"]:
                            if field_name not in record:
                                errors.append({
                                    "type": "missing",
                                    "loc": ["body", "data", "sleepSummary", "sleepRecords", index, field_name],
                                    "msg": f"缺少关键字段 `sleepRecords[{index}].{field_name}`"
                                })

    health_records = data.get("healthCheckRecords")
    if "healthCheckRecords" in data:
        if not isinstance(health_records, list):
            errors.append({
                "type": "type_error.list",
                "loc": ["body", "data", "healthCheckRecords"],
                "msg": "`healthCheckRecords` 必须为数组"
            })
        else:
            for index, record in enumerate(health_records):
                if not isinstance(record, dict):
                    errors.append({
                        "type": "type_error.dict",
                        "loc": ["body", "data", "healthCheckRecords", index],
                        "msg": "体检记录项必须为 JSON 对象"
                    })
                    continue
                for field_name in ["type", "upTime", "createTime"]:
                    if field_name not in record:
                        errors.append({
                            "type": "missing",
                            "loc": ["body", "data", "healthCheckRecords", index, field_name],
                            "msg": f"缺少关键字段 `healthCheckRecords[{index}].{field_name}`"
                        })

    if errors:
        raise HTTPException(status_code=422, detail=errors)

    return data


class HealthDataRequest(BaseModel):
    """接口请求体：taskId 与 data 都为必填项，便于查询长耗时任务状态"""
    analysis_type: AnalysisType = AnalysisType.UNIFIED_REPORT
    taskId: str = Field(..., min_length=1, description="任务唯一标识")
    data: dict

    def get_task_id(self) -> str:
        """获取必填的 taskId"""
        return resolve_task_id(self.taskId)

    def get_payload(self) -> dict:
        """获取并校验必填的整体健康 JSON 数据"""
        return validate_health_data_payload(self.data)


@app.post("/v1/analyze/overview")
async def analyze(request: HealthDataRequest):
    """阻塞式分析接口，返回完整分析结果"""
    task_id = request.get_task_id()
    update_task_status(task_id, "accepted", "请求已接收，正在校验参数", analysis_type=request.analysis_type.value)

    try:
        raw_data = request.get_payload()
        update_task_status(task_id, "running", "参数校验通过，正在生成健康分析结果", analysis_type=request.analysis_type.value)

        req = AnalysisRequest.from_json(request.analysis_type, raw_data)
        response_text = await asyncio.to_thread(
            assistant.analyze_once,
            req,
        )

        # UNIFIED_REPORT 返回结构化分析结果
        if request.analysis_type == AnalysisType.UNIFIED_REPORT:
            structured_response = build_unified_report_response(response_text, req)
            update_task_status(task_id, "completed", "分析已完成，可直接读取最终结果", analysis_type=request.analysis_type.value, result_ready=True)
            return {
                "taskId": task_id,
                **structured_response,
            }

        update_task_status(task_id, "completed", "分析已完成", analysis_type=request.analysis_type.value, result_ready=True)
        return {
            "taskId": task_id,
            "response": response_text,
        }
    except HTTPException as e:
        update_task_status(task_id, "failed", "参数校验失败", analysis_type=request.analysis_type.value, result_ready=False, error=e.detail)
        raise
    except Exception as e:
        update_task_status(task_id, "failed", f"任务执行失败: {e}", analysis_type=request.analysis_type.value, result_ready=False, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/analyze/overview/status/{task_id}")
async def analyze_status(task_id: str):
    """根据 taskId 查询当前分析任务状态"""
    task = TASK_STATUS_STORE.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"未找到 taskId={task_id} 对应的任务状态")
    return task


@app.post("/v1/analyze/overview/stream")
async def analyze_stream(request: HealthDataRequest):
    """流式分析接口 (SSE)"""
    task_id = request.get_task_id()
    update_task_status(task_id, "accepted", "流式请求已接收，正在校验参数", analysis_type=request.analysis_type.value)

    try:
        raw_data = request.get_payload()
        update_task_status(task_id, "running", "流式分析进行中", analysis_type=request.analysis_type.value)
        req = AnalysisRequest.from_json(request.analysis_type, raw_data)
    except HTTPException as e:
        update_task_status(task_id, "failed", "流式请求参数校验失败", analysis_type=request.analysis_type.value, result_ready=False, error=e.detail)
        raise

    def tracked_stream() -> Iterator[str]:
        try:
            for chunk in assistant.analyze_stream(req):
                yield chunk
            update_task_status(task_id, "completed", "流式分析已完成", analysis_type=request.analysis_type.value, result_ready=True)
        except Exception as e:
            update_task_status(task_id, "failed", f"流式分析失败: {e}", analysis_type=request.analysis_type.value, result_ready=False, error=str(e))
            raise

    return StreamingResponse(
        tracked_stream(),
        media_type="text/event-stream",
        headers={"X-Task-Id": task_id},
    )


class HealthOverviewRequest(BaseModel):
    """健康综合评价请求体"""
    current_score: Optional[float] = None   # 当前健康评分（0-100），不传则自动从数据计算
    previous_score: Optional[float] = None  # 上次报告评分，用于计算变化量
    totalDays: Optional[int] = None         # 原始 JSON 中的统计天数
    report_period: str = "近一周"            # 数据统计周期描述（未传 totalDays 时使用）
    healthCheckRecords: list[dict] = []
    sleepSummary: Optional[dict] = None


@app.post("/v1/health_analyze/overview")
async def health_analyze_overview(request: HealthOverviewRequest):
    """阻塞式健康综合评价接口"""
    try:
        health_data = {"healthCheckRecords": request.healthCheckRecords}
        if request.sleepSummary is not None:
            health_data["sleepSummary"] = request.sleepSummary

        sleep_records = get_sleep_records_from_summary(request.sleepSummary)
        total_days = request.totalDays
        if total_days is None and request.sleepSummary is not None:
            total_days = request.sleepSummary.get("totalDays") or request.sleepSummary.get("reportDays")
        if total_days is None and sleep_records:
            total_days = len(sleep_records)
        report_period = resolve_report_period(total_days, request.report_period)

        response_text = ""
        for chunk in assistant.analyze_overview_stream(
            request.current_score, request.previous_score,
            report_period, health_data
        ):
            response_text += chunk
        return {
            "report_period": report_period,
            "sleep_records_count": len(sleep_records),
            "response": response_text,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/health_analyze/overview/stream")
async def analyze_overview_stream_api(request: HealthOverviewRequest):
    """流式健康综合评价接口 (SSE)"""
    health_data = {"healthCheckRecords": request.healthCheckRecords}
    if request.sleepSummary is not None:
        health_data["sleepSummary"] = request.sleepSummary

    sleep_records = get_sleep_records_from_summary(request.sleepSummary)
    total_days = request.totalDays
    if total_days is None and request.sleepSummary is not None:
        total_days = request.sleepSummary.get("totalDays") or request.sleepSummary.get("reportDays")
    if total_days is None and sleep_records:
        total_days = len(sleep_records)
    report_period = resolve_report_period(total_days, request.report_period)

    return StreamingResponse(
        assistant.analyze_overview_stream(
            request.current_score, request.previous_score,
            report_period, health_data
        ),
        media_type="text/event-stream"
    )

@app.post("/v1/chat")
async def chat(request: ChatRequest):
    """通用问答阻塞式接口"""
    try:
        response_text = ""
        for chunk in assistant.chat_stream(request.question):
            response_text += chunk
        return {"response": response_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    """通用问答流式接口 (SSE)"""
    return StreamingResponse(
        assistant.chat_stream(request.question),
        media_type="text/event-stream"
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=26021)

