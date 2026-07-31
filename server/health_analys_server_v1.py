import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Optional
from openai import OpenAI
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn


# ---------------------------------------------------------------------------
# 请求类型枚举
# ---------------------------------------------------------------------------

class AnalysisType(str, Enum):
    SLEEP_PHYSIOLOGY = "sleep_physiology"   # 睡眠与生理分析
    BLOOD_ANALYSIS   = "blood_analysis"     # 血液指标分析
    EXAM_REPORT      = "exam_report"        # 综合体检报告


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
        - 聚焦生活方式干预和日常健康管理"""

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

    def _health_overview_prompt(
        self,
        current_score: float,
        previous_score: Optional[float],
        peer_percentile: Optional[int],
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

        peer_text = f"，在同龄人中处于前 {peer_percentile}% 的水平" if peer_percentile else ""

        return f"""你是一名专业的家庭健康助手。请基于以下用户{report_period}综合健康数据，生成一段温暖、有激励性的健康综合评价播报。

【综合健康分】当前评分：{current_score} 分（满分100分），整体状况：{level}{peer_text}
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
        peer_percentile: Optional[int],
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
            current_score, previous_score, peer_percentile,
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
        }
        prompt = dispatch[request.analysis_type](request)
        messages = [{"role": "user", "content": prompt}]

        full_response = ""
        for chunk in self.provider.chat_stream(messages, self.system_prompt):
            full_response += chunk
            yield chunk
        self.add_assistant_message(full_response)

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


class HealthDataRequest(BaseModel):
    """接口请求体：分析类型 + 原始健康 JSON 数据"""
    analysis_type: AnalysisType
    healthCheckRecords: list[dict] = []
    sleepSummary: Optional[dict] = None


@app.post("/v1/analyze")
async def analyze(request: HealthDataRequest):
    """阻塞式分析接口，返回完整分析结果"""
    try:
        data = {"healthCheckRecords": request.healthCheckRecords}
        if request.sleepSummary is not None:
            data["sleepSummary"] = request.sleepSummary
        req = AnalysisRequest.from_json(request.analysis_type, data)
        response_text = ""
        for chunk in assistant.analyze_stream(req):
            response_text += chunk
        return {"analysis_type": request.analysis_type, "response": response_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/analyze/stream")
async def analyze_stream(request: HealthDataRequest):
    """流式分析接口 (SSE)"""
    data = {"healthCheckRecords": request.healthCheckRecords}
    if request.sleepSummary is not None:
        data["sleepSummary"] = request.sleepSummary
    req = AnalysisRequest.from_json(request.analysis_type, data)
    return StreamingResponse(
        assistant.analyze_stream(req),
        media_type="text/event-stream"
    )


class HealthOverviewRequest(BaseModel):
    """健康综合评价请求体"""
    current_score: Optional[float] = None   # 当前健康评分（0-100），不传则自动从数据计算
    previous_score: Optional[float] = None  # 上次报告评分，用于计算变化量
    peer_percentile: Optional[int] = None   # 同龄人百分位，如 25 表示"前25%"
    report_period: str = "近一月"            # 数据统计周期描述
    healthCheckRecords: list[dict] = []
    sleepSummary: Optional[dict] = None


@app.post("/v1/analyze/overview")
async def analyze_overview(request: HealthOverviewRequest):
    """阻塞式健康综合评价接口"""
    try:
        health_data = {"healthCheckRecords": request.healthCheckRecords}
        if request.sleepSummary is not None:
            health_data["sleepSummary"] = request.sleepSummary
        response_text = ""
        for chunk in assistant.analyze_overview_stream(
            request.current_score, request.previous_score,
            request.peer_percentile, request.report_period, health_data
        ):
            response_text += chunk
        return {"response": response_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/analyze/overview/stream")
async def analyze_overview_stream_api(request: HealthOverviewRequest):
    """流式健康综合评价接口 (SSE)"""
    health_data = {"healthCheckRecords": request.healthCheckRecords}
    if request.sleepSummary is not None:
        health_data["sleepSummary"] = request.sleepSummary
    return StreamingResponse(
        assistant.analyze_overview_stream(
            request.current_score, request.previous_score,
            request.peer_percentile, request.report_period, health_data
        ),
        media_type="text/event-stream"
    )


@app.post("/v1/chat")
async def chat(question: str):
    """通用问答阻塞式接口"""
    try:
        response_text = ""
        for chunk in assistant.chat_stream(question):
            response_text += chunk
        return {"response": response_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/chat/stream")
async def chat_stream(question: str):
    """通用问答流式接口 (SSE)"""
    return StreamingResponse(
        assistant.chat_stream(question),
        media_type="text/event-stream"
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=26021)
