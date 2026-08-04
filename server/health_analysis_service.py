"""
手环健康数据综合管理分析服务

分析 7 大维度：
  1. 综合分析（综合评价 + 体检建议）
  2. 睡眠分析
  3. 心率房颤分析（心率分析 + 房颤分析）
  4. 血压血氧分析（血压分析 + 血氧分析）
  5. 体温分析
  6. 运动分析

默认使用 DeepSeek API，同时保留本地大模型接口。
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterator, Optional

import httpx
from openai import OpenAI
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import uvicorn


logger = logging.getLogger("health_analysis_service")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
# 数据文件路径
DATA_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "health_data")
PERSON_FILE       = os.path.join(DATA_DIR, "biz_person.json")
SLEEP_CACHE_FILE  = os.path.join(DATA_DIR, "biz_sleep_cache.json")
WRISTBAND_FILE    = os.path.join(DATA_DIR, "biz_wristband_cache.json")

# DeepSeek API 配置（默认）
DEEPSEEK_API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL    = "deepseek-v4-flash"

# LLM 调用超时（秒），超时后降级为规则引擎
# 注意：15s 对 DeepSeek 的较大 prompt（如综合分析 600 tokens）明显偏短，
# 实测会频繁触发 APITimeoutError 并静默降级为规则引擎，导致响应质量不稳定，
# 也让调用方误以为接口"重试/失败"。这里放宽到 60s。
LLM_TIMEOUT_SECONDS = 60

# 本地大模型配置（备用）
LOCAL_API_KEY     = "none"
LOCAL_MODEL       = "/home/jinyfeng/models/Baichuan/Baichuan-M2-32B"
LOCAL_BASE_URL    = "http://127.0.0.1:2602/v1"

# 默认 LLM 提供者: "deepseek" | "local"
DEFAULT_PROVIDER = "deepseek"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class LLMBackend(str, Enum):
    DEEPSEEK = "deepseek"
    LOCAL    = "local"


@dataclass
class PersonInfo:
    """人员基础信息"""
    id: str
    project_id: str = ""
    device_id: str = ""
    name: str = ""
    gender: str = ""           # "1"=男, "2"=女
    age: int = 0
    height: float = 0.0
    weight: float = 0.0
    bmi: float = 0.0
    id_card: str = ""
    phone: str = ""
    status: str = ""


@dataclass
class WristbandRecord:
    """手环实时缓存数据记录"""
    id: str = ""
    history_type: str = ""
    seq: int = 0
    data_time: str = ""        # "2026-06-26 03:19:00"
    avg_hr: Optional[int] = None
    max_hr: Optional[int] = None
    min_hr: Optional[int] = None
    fatigue: Optional[int] = None
    rmssd: Optional[int] = None
    bp_hr: Optional[int] = None
    sbp: Optional[int] = None
    dbp: Optional[int] = None
    avg_spo2: Optional[int] = None
    min_spo2: Optional[int] = None
    max_spo2: Optional[int] = None
    calorie: Optional[int] = None
    steps: Optional[int] = None
    distance: Optional[float] = None
    estimate_temp: Optional[float] = None
    type: str = ""
    person_id: str = ""
    device_id: str = ""


@dataclass
class SleepCacheRecord:
    """睡眠缓存记录"""
    id: str = ""
    person_id: str = ""
    device_id: str = ""
    collect_date: int = 0        # 20260709
    rri_data_list: str = ""      # RR 间期原始数据
    sleep_segments: str = ""     # 睡眠阶段 JSON
    create_time: str = ""


# ---------------------------------------------------------------------------
# 数据加载器
# ---------------------------------------------------------------------------

class DataLoader:
    """从本地 JSON 文件加载手环健康数据"""

    def __init__(self):
        self.persons: dict[str, PersonInfo] = {}
        self.wristband: dict[str, list[WristbandRecord]] = {}  # person_id -> records
        self.sleep_cache: dict[str, list[SleepCacheRecord]] = {}  # person_id -> records

    def load_all(self) -> None:
        """加载全部数据"""
        self._load_persons()
        self._load_wristband()
        self._load_sleep_cache()

    def _load_persons(self) -> None:
        if not os.path.exists(PERSON_FILE):
            return
        with open(PERSON_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for item in raw:
            p = PersonInfo(
                id=item.get("id", ""),
                project_id=item.get("project_id", ""),
                device_id=item.get("device_id", ""),
                name=item.get("name", ""),
                gender=item.get("gender", ""),
                age=item.get("age", 0),
                height=float(item.get("height", 0) or 0),
                weight=float(item.get("weight", 0) or 0),
                bmi=float(item.get("bmi", 0) or 0),
                id_card=item.get("id_card", ""),
                phone=item.get("phone", ""),
                status=item.get("status", ""),
            )
            self.persons[p.id] = p
            # 也按 id_card 索引
            if p.id_card:
                self.persons[p.id_card] = p
            if p.device_id:
                self.persons[p.device_id] = p

    def _load_wristband(self) -> None:
        if not os.path.exists(WRISTBAND_FILE):
            return
        with open(WRISTBAND_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for item in raw:
            r = WristbandRecord(
                id=item.get("id", ""),
                history_type=item.get("history_type", ""),
                seq=item.get("seq", 0),
                data_time=item.get("data_time", ""),
                avg_hr=item.get("avg_hr"),
                max_hr=item.get("max_hr"),
                min_hr=item.get("min_hr"),
                fatigue=item.get("fatigue"),
                rmssd=item.get("rmssd"),
                bp_hr=item.get("bp_hr"),
                sbp=item.get("sbp"),
                dbp=item.get("dbp"),
                avg_spo2=item.get("avg_spo2"),
                min_spo2=item.get("min_spo2"),
                max_spo2=item.get("max_spo2"),
                calorie=item.get("calorie"),
                steps=item.get("steps"),
                distance=float(item["distance"]) if item.get("distance") is not None else None,
                estimate_temp=float(item["estimate_temp"]) if item.get("estimate_temp") is not None else None,
                type=item.get("type", ""),
                person_id=item.get("person_id", ""),
                device_id=item.get("device_id", ""),
            )
            pid = r.person_id or r.device_id
            self.wristband.setdefault(pid, []).append(r)

    def _load_sleep_cache(self) -> None:
        if not os.path.exists(SLEEP_CACHE_FILE):
            return
        with open(SLEEP_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for item in raw:
            r = SleepCacheRecord(
                id=item.get("id", ""),
                person_id=item.get("person_id", ""),
                device_id=item.get("device_id", ""),
                collect_date=item.get("collect_date", 0),
                rri_data_list=item.get("rri_data_list", ""),
                sleep_segments=item.get("sleep_segments", ""),
                create_time=item.get("create_time", ""),
            )
            pid = r.person_id or r.device_id
            self.sleep_cache.setdefault(pid, []).append(r)

    def resolve_person(self, person_id: str) -> Optional[PersonInfo]:
        """按 person_id / id_card / device_id 查找人员"""
        if person_id in self.persons:
            return self.persons[person_id]
        # 遍历查找
        for p in self.persons.values():
            if p.id_card == person_id or p.device_id == person_id:
                return p
        return None

    def get_wristband(self, person_id: str) -> list[WristbandRecord]:
        person = self.resolve_person(person_id)
        if person is None:
            return []
        records = (self.wristband.get(person.id, []) +
                   self.wristband.get(person.id_card, []) +
                   self.wristband.get(person.device_id, []))
        # 去重
        seen = set()
        result = []
        for r in records:
            if r.id and r.id not in seen:
                seen.add(r.id)
                result.append(r)
        # 按时间排序
        result.sort(key=lambda x: x.data_time)
        return result

    def get_sleep_cache(self, person_id: str) -> list[SleepCacheRecord]:
        person = self.resolve_person(person_id)
        if person is None:
            return []
        records = (self.sleep_cache.get(person.id, []) +
                   self.sleep_cache.get(person.id_card, []) +
                   self.sleep_cache.get(person.device_id, []))
        seen = set()
        result = []
        for r in records:
            if r.id and r.id not in seen:
                seen.add(r.id)
                result.append(r)
        result.sort(key=lambda x: x.collect_date)
        return result


# ---------------------------------------------------------------------------
# 数据摘要提取器（将原始数据压缩为 LLM 可处理的文本摘要）
# ---------------------------------------------------------------------------

class DataSummarizer:
    """从原始手环数据提取统计摘要，供 LLM 分析使用"""

    @staticmethod
    def safe_avg(values: list) -> Optional[float]:
        nums = [float(v) for v in values if v is not None]
        return round(sum(nums) / len(nums), 2) if nums else None

    @staticmethod
    def safe_range(values: list) -> str:
        nums = [float(v) for v in values if v is not None]
        if not nums:
            return "暂无"
        if len(nums) == 1:
            return str(round(nums[0], 1))
        return f"{round(min(nums), 1)}~{round(max(nums), 1)}"

    @staticmethod
    def count_above(values: list, threshold: float) -> int:
        return sum(1 for v in values if v is not None and float(v) > threshold)

    @staticmethod
    def count_below(values: list, threshold: float) -> int:
        return sum(1 for v in values if v is not None and float(v) < threshold)

    @classmethod
    def heart_rate_summary(cls, records: list[WristbandRecord]) -> dict:
        """心率数据摘要"""
        hr_records = [r for r in records if r.avg_hr is not None]
        if not hr_records:
            return {"has_data": False, "text": "暂无心率数据"}

        avg_hrs = [r.avg_hr for r in hr_records if r.avg_hr is not None]
        max_hrs = [r.max_hr for r in hr_records if r.max_hr is not None]
        min_hrs = [r.min_hr for r in hr_records if r.min_hr is not None]

        total = len(hr_records)
        date_range = f"{hr_records[0].data_time[:10]} ~ {hr_records[-1].data_time[:10]}"

        return {
            "has_data": True,
            "total_records": total,
            "date_range": date_range,
            "avg_hr": cls.safe_avg(avg_hrs),
            "max_hr": max(max_hrs) if max_hrs else None,
            "min_hr": min(min_hrs) if min_hrs else None,
            "resting_hr": cls.safe_avg([r.avg_hr for r in hr_records if r.avg_hr is not None and r.avg_hr < 80]),
            "above_100_count": cls.count_above(avg_hrs, 100),
            "below_60_count": cls.count_below(avg_hrs, 60),
        }

    @classmethod
    def blood_pressure_summary(cls, records: list[WristbandRecord]) -> dict:
        """血压数据摘要"""
        bp_records = [r for r in records if r.sbp is not None and r.dbp is not None]
        if not bp_records:
            return {"has_data": False, "text": "暂无血压数据"}

        sbps = [r.sbp for r in bp_records if r.sbp is not None]
        dbps = [r.dbp for r in bp_records if r.dbp is not None]

        total = len(bp_records)
        above_130_85 = sum(1 for r in bp_records
                          if r.sbp is not None and r.dbp is not None
                          and (r.sbp >= 130 or r.dbp >= 85))

        return {
            "has_data": True,
            "total_records": total,
            "avg_sbp": cls.safe_avg(sbps),
            "avg_dbp": cls.safe_avg(dbps),
            "sbp_range": cls.safe_range(sbps),
            "dbp_range": cls.safe_range(dbps),
            "above_ideal_count": above_130_85,
            "latest": f"{bp_records[-1].sbp}/{bp_records[-1].dbp} mmHg ({bp_records[-1].data_time[:16]})",
        }

    @classmethod
    def spo2_summary(cls, records: list[WristbandRecord]) -> dict:
        """血氧数据摘要"""
        spo2_records = [r for r in records if r.avg_spo2 is not None]
        if not spo2_records:
            return {"has_data": False, "text": "暂无血氧数据"}

        avg_spo2s = [r.avg_spo2 for r in spo2_records if r.avg_spo2 is not None]
        min_spo2s = [r.min_spo2 for r in spo2_records if r.min_spo2 is not None]

        total = len(spo2_records)
        below_95 = cls.count_below(avg_spo2s, 95)
        below_90 = cls.count_below(avg_spo2s, 90)

        return {
            "has_data": True,
            "total_records": total,
            "avg_spo2": cls.safe_avg(avg_spo2s),
            "min_spo2": min(min_spo2s) if min_spo2s else None,
            "spo2_range": cls.safe_range(avg_spo2s),
            "below_95_count": below_95,
            "below_90_count": below_90,
        }

    @classmethod
    def temperature_summary(cls, records: list[WristbandRecord]) -> dict:
        """体温数据摘要"""
        temp_records = [r for r in records if r.estimate_temp is not None]
        if not temp_records:
            return {"has_data": False, "text": "暂无体温数据"}

        temps = [r.estimate_temp for r in temp_records if r.estimate_temp is not None]

        total = len(temp_records)
        above_373 = sum(1 for t in temps if t > 37.3)
        above_375 = sum(1 for t in temps if t > 37.5)

        return {
            "has_data": True,
            "total_records": total,
            "avg_temp": cls.safe_avg(temps),
            "max_temp": max(temps),
            "min_temp": min(temps),
            "temp_range": cls.safe_range(temps),
            "above_373_count": above_373,
            "above_375_count": above_375,
        }

    @classmethod
    def exercise_summary(cls, records: list[WristbandRecord]) -> dict:
        """运动数据摘要（按天聚合步数、卡路里、距离）"""
        sport_records = [r for r in records if r.steps is not None and r.steps > 0]
        if not sport_records:
            return {"has_data": False, "text": "暂无运动数据"}

        # 按天聚合：步数为每分钟采样值，叠加得到每日总步数
        daily_data: dict[str, dict] = {}
        for r in sport_records:
            day = r.data_time[:10]
            if day not in daily_data:
                daily_data[day] = {"steps": 0, "calorie": 0, "distance": 0}
            if r.steps is not None:
                daily_data[day]["steps"] += r.steps
            if r.calorie is not None:
                daily_data[day]["calorie"] += r.calorie
            if r.distance is not None:
                daily_data[day]["distance"] += r.distance

        total_days = len(daily_data)
        daily_steps = [v["steps"] for v in daily_data.values()]
        daily_calories = [v["calorie"] for v in daily_data.values() if v["calorie"] > 0]
        daily_distances = [v["distance"] for v in daily_data.values() if v["distance"] > 0]

        avg_steps = round(sum(daily_steps) / total_days) if total_days > 0 else 0
        low_activity_days = sum(1 for s in daily_steps if s < 5000)
        high_activity_days = sum(1 for s in daily_steps if s >= 10000)
        total_steps = sum(daily_steps)

        return {
            "has_data": True,
            "total_days": total_days,
            "total_steps": total_steps,
            "avg_daily_steps": avg_steps,
            "max_daily_steps": max(daily_steps) if daily_steps else 0,
            "min_daily_steps": min(daily_steps) if daily_steps else 0,
            "low_activity_days": low_activity_days,
            "high_activity_days": high_activity_days,
            "avg_calorie": cls.safe_avg(daily_calories),
            "total_distance_km": round(sum(daily_distances), 2) if daily_distances else 0,
        }

    @classmethod
    def sleep_summary(cls, sleep_records: list[SleepCacheRecord]) -> dict:
        """睡眠数据摘要"""
        if not sleep_records:
            return {"has_data": False, "text": "暂无睡眠数据"}

        total_nights = len(sleep_records)
        segments_count = []
        hr_values = []
        for sr in sleep_records:
            try:
                segs = json.loads(sr.sleep_segments) if sr.sleep_segments else []
                segments_count.append(len(segs))
                for seg in segs:
                    if "H" in seg and isinstance(seg["H"], dict):
                        hr_values.append(seg["H"].get("a"))
            except (json.JSONDecodeError, Exception):
                pass

        collect_dates = [str(sr.collect_date) for sr in sleep_records]

        return {
            "has_data": True,
            "total_nights": total_nights,
            "collect_dates": collect_dates,
            "avg_segments_per_night": cls.safe_avg(segments_count) if segments_count else None,
            "sleep_hr_avg": cls.safe_avg(hr_values) if hr_values else None,
        }

    @classmethod
    def afib_risk_summary(cls, wristband_records: list[WristbandRecord],
                           sleep_records: list[SleepCacheRecord]) -> dict:
        """房颤风险评估摘要（基于 HRV/RMSSD 和心率变异性）"""
        rmssd_records = [r for r in wristband_records if r.rmssd is not None and r.rmssd > 0]

        # 从睡眠 RRI 数据提取心率变异性
        rri_data = []
        for sr in sleep_records:
            if sr.rri_data_list:
                try:
                    rri_list = json.loads(sr.rri_data_list)
                    if isinstance(rri_list, list):
                        rri_data.extend(rri_list)
                except (json.JSONDecodeError, Exception):
                    pass

        return {
            "has_data": bool(rmssd_records) or bool(rri_data),
            "rmssd_records": len(rmssd_records),
            "rmssd_avg": cls.safe_avg([r.rmssd for r in rmssd_records if r.rmssd]),
            "rmssd_range": cls.safe_range([r.rmssd for r in rmssd_records if r.rmssd]),
            "rri_data_points": len(rri_data),
        }


# ---------------------------------------------------------------------------
# LLM 提供者（支持 DeepSeek 和本地模型）
# ---------------------------------------------------------------------------

class LLMProvider:
    def __init__(self, api_key: str, model: str, base_url: str,
                 timeout: float = LLM_TIMEOUT_SECONDS):
        self.model = model
        self.timeout = timeout
        # 双层禁用重试：OpenAI SDK + 底层 HTTPTransport
        transport = httpx.HTTPTransport(retries=0)
        http_client = httpx.Client(transport=transport, timeout=timeout)
        self.client = OpenAI(
            base_url=base_url, api_key=api_key,
            max_retries=0, timeout=timeout, http_client=http_client,
        )
        self.no_retry_client = self.client.with_options(max_retries=0, timeout=timeout)

    def chat(self, messages: list[dict], system_prompt: Optional[str] = None,
             temperature: float = 0.2, max_tokens: int = 900,
             trace_label: str = "unknown") -> str:
        full = []
        if system_prompt:
            full.append({"role": "system", "content": system_prompt})
        full.extend(messages)
        trace_id = uuid.uuid4().hex[:8]
        start = time.perf_counter()
        logger.info("LLM_START trace=%s label=%s model=%s", trace_id, trace_label, self.model)
        try:
            resp = self.no_retry_client.chat.completions.create(
                model=self.model, messages=full, stream=False,
                temperature=temperature, top_p=0.8, max_tokens=max_tokens,
                timeout=self.timeout,
            )
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.info("LLM_DONE trace=%s label=%s elapsed_ms=%d", trace_id, trace_label, elapsed_ms)
            if resp.choices and resp.choices[0].message:
                return resp.choices[0].message.content or ""
            return ""
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.warning(
                "LLM_FAIL trace=%s label=%s elapsed_ms=%d error=%s",
                trace_id,
                trace_label,
                elapsed_ms,
                repr(e),
            )
            return f"\n LLM 错误: {e}\n"

    def chat_stream(self, messages: list[dict], system_prompt: Optional[str] = None,
                    temperature: float = 0.2, max_tokens: int = 900,
                    trace_label: str = "unknown") -> Iterator[str]:
        full = []
        if system_prompt:
            full.append({"role": "system", "content": system_prompt})
        full.extend(messages)
        trace_id = uuid.uuid4().hex[:8]
        start = time.perf_counter()
        logger.info("LLM_STREAM_START trace=%s label=%s model=%s", trace_id, trace_label, self.model)
        try:
            stream = self.no_retry_client.chat.completions.create(
                model=self.model, messages=full, stream=True,
                temperature=temperature, top_p=0.8, max_tokens=max_tokens,
                timeout=self.timeout,
            )
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.info("LLM_STREAM_DONE trace=%s label=%s elapsed_ms=%d", trace_id, trace_label, elapsed_ms)
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.warning(
                "LLM_STREAM_FAIL trace=%s label=%s elapsed_ms=%d error=%s",
                trace_id,
                trace_label,
                elapsed_ms,
                repr(e),
            )
            yield f"\n LLM 错误: {e}\n"


# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一个专业的家庭健康助手，具备丰富的医学常识和健康管理经验。
你需要基于手环监测数据，为用户提供简洁、专业且通俗易懂的健康分析。

回答风格：
- 直接、清晰、有条理，使用"您"称呼用户
- 用具体数据支撑观点，避免堆砌专业术语
- 语气温和积极，避免制造不必要的焦虑

重要约束：
- 不进行疾病诊断，不替代医生意见
- 如有严重异常指标，建议及时就医
- 聚焦生活方式干预和日常健康管理
- 直接输出最终结果，禁止输出思考过程、推理步骤或 `<think>` 标签"""


# ---------------------------------------------------------------------------
# 规则引擎分析器（LLM 不可用时的降级方案，也用于快速测试）
# ---------------------------------------------------------------------------

class RuleBasedAnalyzer:
    """基于数据摘要规则生成自然语言健康分析，不依赖 LLM"""

    @staticmethod
    def _fmt(val, suffix="", default="暂无"):
        return f"{val}{suffix}" if val is not None else default

    @classmethod
    def comprehensive_analysis(cls, summaries: dict, person: PersonInfo, report_period: str) -> dict:
        """规则生成综合评价 + 体检建议"""
        hr = summaries.get("heart_rate", {})
        bp = summaries.get("blood_pressure", {})
        spo2 = summaries.get("spo2", {})
        temp = summaries.get("temperature", {})
        exercise = summaries.get("exercise", {})
        sleep = summaries.get("sleep", {})

        # 评分计算
        score = 85  # 基础分
        good_points = []
        concerns = []

        if hr.get("has_data"):
            avg_hr = hr.get("avg_hr")
            if avg_hr and 55 <= avg_hr <= 80:
                score += 3; good_points.append("心率平稳")
            elif avg_hr and (avg_hr < 55 or avg_hr > 90):
                score -= 3; concerns.append("心率需关注")

        if bp.get("has_data"):
            above = bp.get("above_ideal_count", 0)
            total = bp.get("total_records", 1)
            if above == 0:
                score += 3; good_points.append("血压理想")
            elif above / max(total, 1) < 0.2:
                score += 1; good_points.append("血压基本平稳")
            else:
                score -= 3; concerns.append("血压波动偏多")

        if spo2.get("has_data"):
            avg_spo2 = spo2.get("avg_spo2")
            if avg_spo2 and avg_spo2 >= 96:
                score += 2; good_points.append("血氧充足")
            elif spo2.get("below_90_count", 0) > 0:
                score -= 3; concerns.append("血氧偏低需关注")

        if temp.get("has_data"):
            above_373 = temp.get("above_373_count", 0)
            if above_373 == 0:
                score += 1; good_points.append("体温正常")
            else:
                score -= 2; concerns.append("体温偶有偏高")

        if exercise.get("has_data"):
            avg_steps = exercise.get("avg_daily_steps", 0)
            if avg_steps >= 10000:
                score += 3; good_points.append("运动量充足")
            elif avg_steps >= 5000:
                score += 1; good_points.append("运动量尚可")
            else:
                score -= 2; concerns.append("运动量偏低")

        if sleep.get("has_data"):
            nights = sleep.get("total_nights", 0)
            if nights >= 3:
                score += 1; good_points.append("有睡眠监测记录")
            else:
                concerns.append("睡眠数据偏少")

        score = max(50, min(100, score))
        if score >= 90: level = "优秀"; percentile = "前25%"
        elif score >= 75: level = "良好"; percentile = "前50%"
        elif score >= 60: level = "一般"; percentile = "前75%"
        else: level = "需关注"; percentile = "需进一步改善"

        good_str = "、".join(good_points[:3]) if good_points else "各项指标整体平稳"
        concern_str = "，需留意" + "、".join(concerns[:2]) if concerns else ""

        overall = (
            f"结合{report_period}的监测数据，您的整体健康状况{level}，在同龄人中处于{percentile}的水平。"
            f"主要优势体现在{good_str}{concern_str}。"
        )

        # 体检建议
        checkup_items = ["血压、心率常规检查"]
        if bp.get("above_ideal_count", 0) > 0:
            checkup_items.insert(0, "颈动脉超声")
        if exercise.get("avg_daily_steps", 10000) < 5000:
            checkup_items.append("糖化血红蛋白")
        checkup_items.extend(["尿微量白蛋白", "高敏C反应蛋白"])
        advice = (
            f"下次体检建议优先与医生沟通，增加几项高性价比的深度检查："
            f"{'、'.join(checkup_items[:4])}。"
            f"这些检查能为您的健康状态提供深层验证，为长期健康管理提供精准导航。"
        )

        return {"综合评价": overall, "体检建议": advice}

    @classmethod
    def sleep_analysis(cls, sleep_data: dict) -> str:
        if not sleep_data.get("has_data"):
            return "暂无睡眠数据，建议佩戴手环持续监测睡眠质量。"

        nights = sleep_data.get("total_nights", 0)
        hr_avg = sleep_data.get("sleep_hr_avg")

        parts = []
        if hr_avg and 60 <= hr_avg <= 80:
            parts.append(f"夜间平均心率约{hr_avg}bpm，整体平稳")
        elif hr_avg and hr_avg > 80:
            parts.append(f"夜间平均心率约{hr_avg}bpm，略偏高")

        if nights >= 3:
            return (
                f"正常，您的睡眠整体达标。共有{nights}晚睡眠监测记录，"
                + ("；".join(parts) + "。" if parts else "")
                + "建议继续保持规律作息，睡前减少电子设备使用，有助于提升深睡占比。"
            )
        return (
            f"需关注，当前仅有{nights}晚睡眠记录，数据偏少。"
            + ("；".join(parts) + "。" if parts else "")
            + "建议持续佩戴手环积累更多睡眠数据以获得更准确的分析。"
        )

    @classmethod
    def heart_rate_analysis(cls, hr_data: dict) -> str:
        if not hr_data.get("has_data"):
            return "暂无心率数据"

        avg_hr = hr_data.get("avg_hr")
        max_hr = hr_data.get("max_hr")
        min_hr = hr_data.get("min_hr")
        above_100 = hr_data.get("above_100_count", 0)
        below_60 = hr_data.get("below_60_count", 0)
        total = hr_data.get("total_records", 0)

        if avg_hr and 55 <= avg_hr <= 90 and above_100 / max(total, 1) < 0.1:
            return (
                f"窦性心律(正常)，心率节律规整，平均心率约{avg_hr}bpm，"
                f"波动范围在{min_hr}~{max_hr}bpm之间，处于正常参考区间。"
                f"未检测到房性早搏等异常心律特征，心脏电活动稳定。"
            )
        elif avg_hr and avg_hr > 90:
            return (
                f"心率偏快，平均心率约{avg_hr}bpm，波动范围{min_hr}~{max_hr}bpm。"
                f"有{above_100}次心率超过100bpm的记录，建议关注静息心率变化，"
                f"避免咖啡因和熬夜，如持续偏快建议咨询医生。"
            )
        return (
            f"心率整体在正常范围，平均约{avg_hr}bpm，"
            f"波动{min_hr}~{max_hr}bpm，建议持续监测。"
        )

    @classmethod
    def afib_analysis(cls, afib_data: dict) -> str:
        if not afib_data.get("has_data"):
            return "暂无房颤相关数据，建议持续佩戴手环积累HRV数据。"

        rmssd_avg = afib_data.get("rmssd_avg")
        rri_points = afib_data.get("rri_data_points", 0)

        if rmssd_avg and 20 <= rmssd_avg <= 70:
            return (
                f"未检测到房颤特征(正常)，心律规整，RMSSD约{rmssd_avg}ms，"
                f"处于正常参考范围内，RR间期波动在正常范围，"
                f"未发现心房颤动相关的异常心律模式。"
            )
        elif rri_points > 0:
            return (
                "未检测到房颤特征(正常)，基于RRI数据分析，"
                "心律规整，RR间期波动在正常范围内，"
                "未发现心房颤动相关的异常心律模式。"
            )
        return "暂无足够HRV数据评估房颤风险，建议持续监测。"

    @classmethod
    def blood_pressure_analysis(cls, bp_data: dict) -> str:
        if not bp_data.get("has_data"):
            return "暂无血压数据"

        avg_sbp = bp_data.get("avg_sbp")
        avg_dbp = bp_data.get("avg_dbp")
        above = bp_data.get("above_ideal_count", 0)
        total = bp_data.get("total_records", 1)
        latest = bp_data.get("latest", "")

        if avg_sbp and avg_dbp and avg_sbp < 130 and avg_dbp < 85:
            if above == 0:
                return (
                    f"血压（正常）。本次监测您的血压整体平稳，"
                    f"平均{avg_sbp}/{avg_dbp}mmHg，全程处于理想血压区间，"
                    f"无持续性异常波动，血管弹性状态良好。"
                )
            else:
                return (
                    f"血压（正常）。本次监测您的血压整体基本平稳，"
                    f"平均{avg_sbp}/{avg_dbp}mmHg，仅出现{above}次一过性小幅偏离健康区间，"
                    f"多与情绪激动、运动、高盐饮食等生理性因素相关，无持续性异常。"
                )
        return (
            f"血压需关注。平均{avg_sbp}/{avg_dbp}mmHg，"
            f"有{above}/{total}次测量超过理想范围。"
            f"建议控制钠盐摄入、避免久坐并固定时间段复测血压。"
        )

    @classmethod
    def spo2_analysis(cls, spo2_data: dict) -> str:
        if not spo2_data.get("has_data"):
            return "暂无血氧数据"

        avg_spo2 = spo2_data.get("avg_spo2")
        min_spo2 = spo2_data.get("min_spo2")
        below_95 = spo2_data.get("below_95_count", 0)

        if avg_spo2 and avg_spo2 >= 96 and below_95 == 0:
            return (
                f"血氧（正常）。本次监测期间，您的血氧饱和度全程处于成人健康推荐区间，"
                f"平均约{avg_spo2}%，静息状态下数值稳定，无异常下降事件，"
                f"昼夜氧合节律正常。当前您的身体供氧充足，心肺氧合机能状态良好。"
            )
        elif avg_spo2 and avg_spo2 >= 95:
            return (
                f"血氧（正常）。平均血氧约{avg_spo2}%，整体在正常范围内，"
                f"最低{min_spo2}%，建议持续关注夜间血氧变化。"
            )
        return (
            f"血氧需关注。平均约{avg_spo2}%，最低{min_spo2}%，"
            f"有{below_95}次低于95%的记录，建议关注夜间呼吸情况。"
        )

    @classmethod
    def temperature_analysis(cls, temp_data: dict) -> str:
        if not temp_data.get("has_data"):
            return "暂无体温数据"

        avg_temp = temp_data.get("avg_temp")
        max_temp = temp_data.get("max_temp")
        above_373 = temp_data.get("above_373_count", 0)

        if avg_temp and max_temp and max_temp <= 37.3 and above_373 == 0:
            return (
                f"正常。本次监测期间，您的体温全程处于成人健康推荐区间，"
                f"平均约{avg_temp}°C，昼夜波动节律正常，无异常升高或降低，"
                f"体温调节机能稳定。当前体温状态反映您的身体基础代谢与免疫状态平稳，"
                f"建议继续保持健康作息，做好日常健康监测即可。"
            )
        elif above_373 > 0:
            return (
                f"需关注。平均体温约{avg_temp}°C，最高{max_temp}°C，"
                f"有{above_373}次超过37.3°C的记录。"
                f"建议多饮水、注意休息，如持续偏高或伴有不适请及时就医。"
            )
        return (
            f"正常。平均体温约{avg_temp}°C，整体在健康区间内，建议持续监测。"
        )

    @classmethod
    def exercise_analysis(cls, exercise_data: dict) -> str:
        if not exercise_data.get("has_data"):
            return "暂无运动数据"

        avg_steps = exercise_data.get("avg_daily_steps", 0)
        total_days = exercise_data.get("total_days", 0)
        low_days = exercise_data.get("low_activity_days", 0)
        high_days = exercise_data.get("high_activity_days", 0)

        if avg_steps >= 10000:
            return (
                f"正常。本次监测周期内，您的每日步数全程处于成人健康推荐区间，"
                f"日均约{avg_steps}步，活动时段分布均匀，无长时间久坐不动，"
                f"日常活动量充足稳定。规律的步行能有效提升心肺耐力、促进循环代谢。"
            )
        elif avg_steps >= 5000:
            return (
                f"基本正常。日均约{avg_steps}步，总活动量尚可，"
                f"但仍有{low_days}天步数低于5000步。"
                f"建议在低活动日增加短时步行，逐步提升至每日8000步以上。"
            )
        return (
            f"运动量不足。日均仅约{avg_steps}步，{total_days}天中有{low_days}天活动量偏低。"
            f"建议每日增加30分钟快走，逐步提升至8000-10000步，"
            f"规律运动有助于改善心血管健康和代谢水平。"
        )

class HealthAnalysisAssistant:
    def __init__(self, provider: LLMProvider, person: PersonInfo):
        self.provider = provider
        self.person = person

    # ------------------------------------------------------------------
    # Prompt 构建
    # ------------------------------------------------------------------

    def _comprehensive_prompt(self, all_summaries: dict, report_period: str) -> str:
        """综合分析提示词（综合评价 + 体检建议）"""
        hr = all_summaries.get("heart_rate", {})
        bp = all_summaries.get("blood_pressure", {})
        spo2 = all_summaries.get("spo2", {})
        temp = all_summaries.get("temperature", {})
        exercise = all_summaries.get("exercise", {})
        sleep = all_summaries.get("sleep", {})

        return f"""你是一名家庭健康助手。请基于以下{report_period}手环监测数据，生成综合健康评价和体检建议。

【用户信息】{self.person.name}，{self.person.age}岁，{'男' if self.person.gender == '1' else '女'}，BMI {self.person.bmi}

【心率数据】{json.dumps(hr, ensure_ascii=False)}
【血压数据】{json.dumps(bp, ensure_ascii=False)}
【血氧数据】{json.dumps(spo2, ensure_ascii=False)}
【体温数据】{json.dumps(temp, ensure_ascii=False)}
【运动数据】{json.dumps(exercise, ensure_ascii=False)}
【睡眠数据】{json.dumps(sleep, ensure_ascii=False)}

请严格按以下格式输出，每个部分用 "##" 分隔：

## 综合评价
仿照示例格式："结合{report_period}的监测数据，您的整体健康状况[优秀/良好/一般/需关注]，在同龄人中处于前[X]%的水平。相比上次报告，您的健康评分[提升/下降]了X分，主要得益于[具体改善点]。"

## 体检建议
仿照示例格式："下次体检建议优先与医生沟通，增加四项高性价比的深度检查：颈动脉超声、糖化血红蛋白、尿微量白蛋白、高敏C反应蛋白。这四项组合能为您的健康提供深层验证。"
根据用户实际数据调整建议内容，优先推荐与当前指标最相关的检查项目。

输出要求：语气温暖积极，不进行疾病诊断，全文不超过300字。"""

    def _sleep_prompt(self, sleep_data: dict, sleep_records: list[SleepCacheRecord]) -> str:
        """睡眠分析提示词"""
        if not sleep_data.get("has_data"):
            return "暂无睡眠数据"

        # 提取睡眠阶段详情
        segments_detail = []
        for sr in sleep_records[:5]:
            try:
                segs = json.loads(sr.sleep_segments) if sr.sleep_segments else []
                date_str = str(sr.collect_date)
                for seg in segs[:3]:
                    detail = {"date": date_str}
                    if "T" in seg:
                        detail["time"] = f"{seg['T'][0]:02d}:{seg['T'][1]:02d}"
                    if "H" in seg and isinstance(seg["H"], dict):
                        detail["hr_avg"] = seg["H"].get("a")
                        detail["hr_min"] = seg["H"].get("n")
                        detail["hr_max"] = seg["H"].get("x")
                    if "V" in seg and isinstance(seg["V"], dict):
                        detail["vitals"] = seg["V"]
                    segments_detail.append(detail)
            except (json.JSONDecodeError, Exception):
                pass

        return f"""你是一名家庭健康助手。请基于以下用户睡眠监测数据，生成简洁专业的睡眠分析。

【睡眠数据摘要】{json.dumps(sleep_data, ensure_ascii=False)}
【睡眠阶段样本】{json.dumps(segments_detail[:15], ensure_ascii=False)}

参考范围：成人建议睡眠时长 7-9 小时；深睡占比 15-25%；入睡潜伏期 < 20 分钟；夜间觉醒 < 2 次。

请仿照以下示例格式输出睡眠分析结果（一段话，200字以内）：
示例："正常，您的睡眠整体达标：总时长符合健康标准，但仍有小幅提升空间。您的入睡用时偏长，深睡占比略低于推荐区间，夜间有短暂觉醒，睡眠稳定性稍有不足。建议睡前减少电子设备使用。"

根据实际数据调整：首先给出结论（正常/需关注），然后具体描述睡眠时长、入睡效率、深睡情况和觉醒水平，最后如有问题给出1条改善建议。

输出要求：使用"您"称呼，自然语言叙述，不进行疾病诊断，不超过200字。"""

    def _heart_rate_afib_prompt(self, hr_data: dict, afib_data: dict) -> tuple[str, str]:
        """心率分析和房颤分析提示词"""
        hr_has = hr_data.get("has_data", False)
        afib_has = afib_data.get("has_data", False)

        if hr_has:
            hr_prompt = f"""你是一名家庭健康助手。请基于以下心率监测数据，生成心率分析结果。

【心率数据】{json.dumps(hr_data, ensure_ascii=False)}

参考范围：成人静息心率 60-100 bpm（理想 55-70 bpm）。

请仿照示例输出心率分析结果（一段话，150字以内）：
示例："窦性心律(正常)，心率节律规整，心率波动在正常范围内，未检测到房性早搏等异常心律特征，心脏电活动稳定。"

根据实际数据调整：首先给出结论（窦性心律(正常)或指出异常），描述心率节律、波动范围及有无异常特征。

输出要求：使用"您"称呼，不进行疾病诊断。"""
        else:
            hr_prompt = "暂无心率数据"

        if afib_has:
            afib_prompt = f"""你是一名家庭健康助手。请基于以下心率变异性(HRV)数据，评估房颤风险。

【HRV数据】{json.dumps(afib_data, ensure_ascii=False)}

参考：RMSSD 正常范围 20-70ms（青年人偏高）；房颤特征：心律绝对不齐、P波消失、RR间期绝对不规则。

请仿照示例输出房颤分析结果（一段话，150字以内）：
示例（正常）："未检测到房颤特征(正常)，心律规整，RR间期波动在正常范围内，未发现心房颤动相关的异常心律模式。"
示例（异常）："心房颤动(异常)，检测到明显的心房颤动特征，心律绝对不齐，心率波动异常增高，P波消失，RR间期绝对不规则，存在心律失常风险。"

根据实际数据判断属于正常还是异常，并给出相应结论。

输出要求：使用"您"称呼，不进行疾病诊断。"""
        else:
            afib_prompt = "暂无房颤相关数据"

        return hr_prompt, afib_prompt

    def _bp_spo2_prompt(self, bp_data: dict, spo2_data: dict) -> tuple[str, str]:
        """血压分析和血氧分析提示词"""
        bp_has = bp_data.get("has_data", False)
        spo2_has = spo2_data.get("has_data", False)

        if bp_has:
            bp_prompt = f"""你是一名家庭健康助手。请基于以下血压监测数据，生成血压分析结果。

【血压数据】{json.dumps(bp_data, ensure_ascii=False)}

参考范围：理想血压 < 130/85 mmHg；正常高值 130-139/85-89 mmHg。

请仿照示例输出血压分析结果（一段话，150字以内）：
示例："血压（正常）。本次监测您的血压整体基本平稳，仅出现一过性小幅偏离健康区间，或处于临界正常高值范围，无持续性异常波动，多与情绪激动、运动、高盐饮食、熬夜等生理性因素相关。"

根据实际数据调整结论（正常/偏高/偏低），描述整体平稳性及可能的生理诱因。

输出要求：使用"您"称呼，不进行疾病诊断。"""
        else:
            bp_prompt = "暂无血压数据"

        if spo2_has:
            spo2_prompt = f"""你是一名家庭健康助手。请基于以下血氧监测数据，生成血氧分析结果。

【血氧数据】{json.dumps(spo2_data, ensure_ascii=False)}

参考范围：成人静息血氧饱和度 ≥ 95%；< 90% 需关注。

请仿照示例输出血氧分析结果（一段话，150字以内）：
示例："血氧（正常）。本次监测期间，您的血氧饱和度全程处于成人健康推荐区间，静息状态下数值稳定，无异常下降事件，昼夜氧合节律正常。当前您的身体供氧充足，心肺氧合机能状态良好。"

根据实际数据调整结论（正常/偏低），描述血氧水平、稳定性及心肺氧合状态。

输出要求：使用"您"称呼，不进行疾病诊断。"""
        else:
            spo2_prompt = "暂无血氧数据"

        return bp_prompt, spo2_prompt

    def _temperature_prompt(self, temp_data: dict) -> str:
        """体温分析提示词"""
        if not temp_data.get("has_data"):
            return "暂无体温数据"

        return f"""你是一名家庭健康助手。请基于以下体温监测数据，生成体温分析结果。

【体温数据】{json.dumps(temp_data, ensure_ascii=False)}

参考范围：成人正常体温 36.0-37.3°C（手环测量值可能略低于腋下温度）。

请仿照示例输出体温分析结果（一段话，150字以内）：
示例："正常。本次监测期间，您的体温全程处于成人健康推荐区间，昼夜波动节律正常，无异常升高或降低，体温调节机能稳定。当前体温状态反映您的身体基础代谢与免疫状态平稳，建议继续保持健康作息，做好日常健康监测即可。"

根据实际数据调整结论（正常/偏高/偏低），描述体温区间、昼夜节律、调节机能和代谢状态。

输出要求：使用"您"称呼，不进行疾病诊断。"""

    def _exercise_prompt(self, exercise_data: dict) -> str:
        """运动分析提示词"""
        if not exercise_data.get("has_data"):
            return "暂无运动数据"

        return f"""你是一名家庭健康助手。请基于以下运动监测数据，生成运动分析结果。

【运动数据】{json.dumps(exercise_data, ensure_ascii=False)}

参考范围：成人每日建议步数 8000-10000 步；每周建议中等强度运动 150 分钟。

请仿照示例输出运动分析结果（一段话，150字以内）：
示例："正常。本次监测周期内，您的每日步数全程处于成人健康推荐区间，活动时段分布均匀，无长时间久坐不动，日常活动量充足稳定。规律的步行能有效提升心肺耐力、促进循环代谢。"

根据实际数据调整结论（正常/运动量不足/需增加活动），描述每日步数水平、活动分布、有无久坐情况，最后提及运动的积极意义。

输出要求：使用"您"称呼，不进行疾病诊断。"""

    # ------------------------------------------------------------------
    # 分析方法
    # ------------------------------------------------------------------

    def analyze_all(self, wristband_records: list[WristbandRecord],
                    sleep_records: list[SleepCacheRecord],
                    report_period: str) -> dict:
        """并发分析全部 7 个维度，LLM 失败时自动降级为规则引擎。"""

        from concurrent.futures import ThreadPoolExecutor, as_completed

        # 1. 提取各维度数据摘要
        summaries = {
            "heart_rate": DataSummarizer.heart_rate_summary(wristband_records),
            "blood_pressure": DataSummarizer.blood_pressure_summary(wristband_records),
            "spo2": DataSummarizer.spo2_summary(wristband_records),
            "temperature": DataSummarizer.temperature_summary(wristband_records),
            "exercise": DataSummarizer.exercise_summary(wristband_records),
            "sleep": DataSummarizer.sleep_summary(sleep_records),
            "afib_risk": DataSummarizer.afib_risk_summary(wristband_records, sleep_records),
        }

        # 2. 构建各维度 prompt
        comprehensive_prompt = self._comprehensive_prompt(summaries, report_period)
        sleep_prompt = self._sleep_prompt(summaries["sleep"], sleep_records)
        hr_prompt, afib_prompt = self._heart_rate_afib_prompt(
            summaries["heart_rate"], summaries["afib_risk"])
        bp_prompt, spo2_prompt = self._bp_spo2_prompt(
            summaries["blood_pressure"], summaries["spo2"])
        temp_prompt = self._temperature_prompt(summaries["temperature"])
        exercise_prompt = self._exercise_prompt(summaries["exercise"])

        # 3. 辅助函数
        def _is_llm_error(text: str) -> bool:
            return not text or "LLM 错误" in text or text.startswith("暂无")

        def _call_llm(label: str, prompt: str, max_tokens: int = 500) -> str:
            if prompt.startswith("暂无"):
                return prompt
            try:
                resp = self.provider.chat(
                    [{"role": "user", "content": prompt}],
                    system_prompt=SYSTEM_PROMPT,
                    max_tokens=max_tokens,
                    trace_label=label,
                )
                return resp.strip()
            except Exception:
                return ""

        def _clean(text: str, fallback_fn) -> str:
            if _is_llm_error(text):
                return fallback_fn()
            clean = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
            clean = re.sub(r"</?think>", "", clean, flags=re.IGNORECASE)
            return clean.strip() or fallback_fn()

        # 4. 定义所有分析任务 (key, prompt, max_tokens, fallback_fn)
        tasks = [
            ("comprehensive", comprehensive_prompt, 600,
             lambda: RuleBasedAnalyzer.comprehensive_analysis(summaries, self.person, report_period)),
            ("sleep", sleep_prompt, 400,
             lambda: RuleBasedAnalyzer.sleep_analysis(summaries["sleep"])),
            ("heart_rate", hr_prompt, 350,
             lambda: RuleBasedAnalyzer.heart_rate_analysis(summaries["heart_rate"])),
            ("afib", afib_prompt, 350,
             lambda: RuleBasedAnalyzer.afib_analysis(summaries["afib_risk"])),
            ("blood_pressure", bp_prompt, 350,
             lambda: RuleBasedAnalyzer.blood_pressure_analysis(summaries["blood_pressure"])),
            ("spo2", spo2_prompt, 350,
             lambda: RuleBasedAnalyzer.spo2_analysis(summaries["spo2"])),
            ("temperature", temp_prompt, 350,
             lambda: RuleBasedAnalyzer.temperature_analysis(summaries["temperature"])),
            ("exercise", exercise_prompt, 350,
             lambda: RuleBasedAnalyzer.exercise_analysis(summaries["exercise"])),
        ]

        # 5. 并发调用 LLM
        results: dict[str, object] = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_map = {
                executor.submit(_call_llm, key, prompt, max_tokens): (key, fallback)
                for key, prompt, max_tokens, fallback in tasks
            }
            for future in as_completed(future_map):
                key, fallback = future_map[future]
                try:
                    raw = future.result()
                except Exception:
                    raw = ""
                results[key] = _clean(raw, fallback)

        # 6. 组装返回结果
        comprehensive = results.get("comprehensive", {})
        if isinstance(comprehensive, str):
            eval_text, advice_text = self._parse_comprehensive(comprehensive)
            comprehensive = {"综合评价": eval_text, "体检建议": advice_text}

        comprehensive["健康指数"] = self._compute_health_index(summaries)

        return {
            "综合分析": comprehensive,
            "睡眠分析": results.get("sleep", ""),
            "心率房颤分析": {
                "心率分析": results.get("heart_rate", ""),
                "房颤分析": results.get("afib", ""),
            },
            "血压血氧分析": {
                "血压分析": results.get("blood_pressure", ""),
                "血氧分析": results.get("spo2", ""),
            },
            "体温分析": results.get("temperature", ""),
            "运动分析": results.get("exercise", ""),
        }

    @staticmethod
    def _parse_comprehensive(text: str) -> tuple[str, str]:
        """从综合分析文本中提取综合评价和体检建议"""
        overall = ""
        advice = ""

        # 尝试按 ## 分割
        parts = re.split(r"##\s*综合评价|##\s*体检建议", text)
        if len(parts) >= 2:
            # 找到综合评价部分
            eval_match = re.search(r"##\s*综合评价\s*\n?(.*?)(?:##\s*体检建议|$)", text, re.DOTALL)
            if eval_match:
                overall = eval_match.group(1).strip()
            advice_match = re.search(r"##\s*体检建议\s*\n?(.*?)$", text, re.DOTALL)
            if advice_match:
                advice = advice_match.group(1).strip()

        if not overall:
            overall = text[:300].strip()
        if not advice:
            advice = "建议定期体检，关注血压、血糖、血脂等基础指标。"

        return overall, advice

    @staticmethod
    def _compute_health_index(summaries: dict) -> dict:
        """计算各维度 1-9 健康指数（上限 9 分表示始终有提升空间），无数据返回 '-'
        评分仅基于指标健康度，不受数据量影响。"""

        def _score(raw: int) -> int:
            """原始 1-10 映射到 1-9，满分从 10→9"""
            return min(raw, 9)

        result = {}

        # === 睡眠（基于夜间心率偏离正常范围的百分比） ===
        sleep = summaries.get("sleep", {})
        if sleep.get("has_data"):
            hr_avg = sleep.get("sleep_hr_avg")
            if hr_avg is None:
                result["睡眠"] = "-"   # 有睡眠记录但无心率数据 → 无法计算
            elif 55 <= hr_avg <= 80:
                result["睡眠"] = 9   # 正常范围
            else:
                # 计算偏离正常范围的百分比
                deviation = (hr_avg - 80) / 80 * 100 if hr_avg > 80 else (55 - hr_avg) / 55 * 100
                if deviation <= 10:
                    result["睡眠"] = 8
                elif deviation <= 20:
                    result["睡眠"] = 7
                elif deviation <= 30:
                    result["睡眠"] = 6
                else:
                    result["睡眠"] = 5
        else:
            result["睡眠"] = "-"

        # === 心率（平均心率 + 异常占比） ===
        hr = summaries.get("heart_rate", {})
        if hr.get("has_data"):
            avg_hr = hr.get("avg_hr", 0) or 0
            total = max(hr.get("total_records", 0), 1)
            above_100 = hr.get("above_100_count", 0)
            ratio = above_100 / total
            if 55 <= avg_hr <= 80 and ratio < 0.05:
                raw = 10
            elif 55 <= avg_hr <= 90 and ratio < 0.1:
                raw = 8
            elif ratio < 0.2:
                raw = 6
            else:
                raw = 4
            result["心率"] = _score(raw)
        else:
            result["心率"] = "-"

        # === 血压（收缩压/舒张压 + 超标占比） ===
        bp = summaries.get("blood_pressure", {})
        if bp.get("has_data"):
            avg_sbp = bp.get("avg_sbp", 0) or 0
            avg_dbp = bp.get("avg_dbp", 0) or 0
            above = bp.get("above_ideal_count", 0)
            total = max(bp.get("total_records", 0), 1)
            ratio = above / total
            if avg_sbp < 120 and avg_dbp < 80 and ratio == 0:
                raw = 10
            elif avg_sbp < 130 and avg_dbp < 85 and ratio < 0.1:
                raw = 8
            elif ratio < 0.3:
                raw = 6
            else:
                raw = 4
            result["血压"] = _score(raw)
        else:
            result["血压"] = "-"

        # === 运动（日均步数） ===
        exercise = summaries.get("exercise", {})
        if exercise.get("has_data"):
            avg_steps = exercise.get("avg_daily_steps", 0) or 0
            if avg_steps >= 10000:
                raw = 10
            elif avg_steps >= 5000:
                raw = 9
            elif avg_steps >= 3000:
                raw = 8
            elif avg_steps >= 2000:
                raw = 7
            elif avg_steps >= 1000:
                raw = 6
            elif avg_steps >= 500:
                raw = 5
            elif avg_steps >= 300:
                raw = 3
            else:
                raw = 1
            result["运动"] = _score(raw)
        else:
            result["运动"] = "-"

        # === 血氧（平均血氧 + 偏低次数） ===
        spo2 = summaries.get("spo2", {})
        if spo2.get("has_data"):
            avg_spo2 = spo2.get("avg_spo2", 0) or 0
            below_95 = spo2.get("below_95_count", 0)
            below_90 = spo2.get("below_90_count", 0)
            if below_90 > 0:
                raw = 3
            elif avg_spo2 >= 98 and below_95 == 0:
                raw = 10
            elif avg_spo2 >= 96 and below_95 <= 1:
                raw = 8
            elif avg_spo2 >= 95:
                raw = 6
            else:
                raw = 4
            result["血氧"] = _score(raw)
        else:
            result["血氧"] = "-"

        # === 体温（最高体温 + 超 37.3 次数） ===
        temp = summaries.get("temperature", {})
        if temp.get("has_data"):
            max_temp = temp.get("max_temp", 0) or 0
            above_373 = temp.get("above_373_count", 0)
            if above_373 == 0 and max_temp <= 37.0:
                raw = 10
            elif above_373 == 0:
                raw = 9
            elif above_373 <= 2:
                raw = 6
            else:
                raw = 4
            result["体温"] = _score(raw)
        else:
            result["体温"] = "-"

        # === 房颤（基于 RMSSD/HRV 值） ===
        afib = summaries.get("afib_risk", {})
        if afib.get("has_data"):
            rmssd = afib.get("rmssd_avg")
            if rmssd is None:
                result["房颤"] = "-"   # 无 RMSSD/HRV 数据 → 无法计算
            elif 20 <= rmssd <= 70:
                result["房颤"] = 9
            else:
                result["房颤"] = 7
        else:
            result["房颤"] = "-"

        return result

    def analyze_all_stream(self, wristband_records: list[WristbandRecord],
                           sleep_records: list[SleepCacheRecord],
                           report_period: str) -> Iterator[str]:
        """流式分析全部维度，以 SSE JSON 事件流输出"""

        summaries = {
            "heart_rate": DataSummarizer.heart_rate_summary(wristband_records),
            "blood_pressure": DataSummarizer.blood_pressure_summary(wristband_records),
            "spo2": DataSummarizer.spo2_summary(wristband_records),
            "temperature": DataSummarizer.temperature_summary(wristband_records),
            "exercise": DataSummarizer.exercise_summary(wristband_records),
            "sleep": DataSummarizer.sleep_summary(sleep_records),
            "afib_risk": DataSummarizer.afib_risk_summary(wristband_records, sleep_records),
        }

        comprehensive_prompt = self._comprehensive_prompt(summaries, report_period)
        sleep_prompt = self._sleep_prompt(summaries["sleep"], sleep_records)
        hr_prompt, afib_prompt = self._heart_rate_afib_prompt(
            summaries["heart_rate"], summaries["afib_risk"])
        bp_prompt, spo2_prompt = self._bp_spo2_prompt(
            summaries["blood_pressure"], summaries["spo2"])
        temp_prompt = self._temperature_prompt(summaries["temperature"])
        exercise_prompt = self._exercise_prompt(summaries["exercise"])

        prompts = [
            ("综合分析", comprehensive_prompt, 600),
            ("睡眠分析", sleep_prompt, 400),
            ("心率分析", hr_prompt, 350),
            ("房颤分析", afib_prompt, 350),
            ("血压分析", bp_prompt, 350),
            ("血氧分析", spo2_prompt, 350),
            ("体温分析", temp_prompt, 350),
            ("运动分析", exercise_prompt, 350),
        ]

        for label, prompt, max_tokens in prompts:
            if prompt.startswith("暂无"):
                yield json.dumps({"type": label, "content": prompt}, ensure_ascii=False) + "\n"
                continue

            full = ""
            for chunk in self.provider.chat_stream(
                [{"role": "user", "content": prompt}],
                system_prompt=SYSTEM_PROMPT,
                max_tokens=max_tokens,
                trace_label=label,
            ):
                full += chunk
            yield json.dumps({"type": label, "content": full.strip()}, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# FastAPI 服务
# ---------------------------------------------------------------------------

app = FastAPI(title="Health Analysis Service - 手环健康综合管理分析")

# 全局数据加载器（启动时加载）
data_loader = DataLoader()
data_loader.load_all()

# LLM 提供者字典
llm_providers: dict[LLMBackend, LLMProvider] = {}


def get_provider() -> LLMProvider:
    """获取 LLM 提供者，模型由服务启动配置 DEFAULT_PROVIDER 决定"""
    backend = LLMBackend(DEFAULT_PROVIDER)
    if backend not in llm_providers:
        if backend == LLMBackend.DEEPSEEK:
            llm_providers[backend] = LLMProvider(
                api_key=DEEPSEEK_API_KEY,
                model=DEEPSEEK_MODEL,
                base_url=DEEPSEEK_BASE_URL,
            )
        else:
            llm_providers[backend] = LLMProvider(
                api_key=LOCAL_API_KEY,
                model=LOCAL_MODEL,
                base_url=LOCAL_BASE_URL,
            )
    return llm_providers[backend]


# ---------------------------------------------------------------------------
# 字段兼容工具：同时支持 snake_case 和 camelCase
# ---------------------------------------------------------------------------

def _get(data: dict, *keys: str, default=None):
    """从字典中按优先级获取字段，兼容 snake_case / camelCase"""
    for k in keys:
        if k in data and data[k] is not None:
            return data[k]
    return default


def _parse_person(raw: dict) -> PersonInfo:
    """解析人员信息，兼容驼峰与下划线字段"""
    return PersonInfo(
        id=_get(raw, "id", default=""),
        project_id=_get(raw, "project_id", "projectId", default=""),
        device_id=_get(raw, "device_id", "deviceId", default=""),
        name=_get(raw, "name", default=""),
        gender=str(_get(raw, "gender", default="")),
        age=int(_get(raw, "age", default=0) or 0),
        height=float(_get(raw, "height", default=0) or 0),
        weight=float(_get(raw, "weight", default=0) or 0),
        bmi=float(_get(raw, "bmi", default=0) or 0),
        id_card=_get(raw, "id_card", "idCard", default=""),
        phone=_get(raw, "phone", default=""),
        status=str(_get(raw, "status", default="")),
    )


def _parse_wristband(raw: dict) -> WristbandRecord:
    """解析手环记录，兼容驼峰与下划线字段"""
    distance_raw = _get(raw, "distance")
    temp_raw = _get(raw, "estimate_temp", "estimateTemp")
    return WristbandRecord(
        id=_get(raw, "id", default=""),
        history_type=_get(raw, "history_type", "historyType", default=""),
        seq=_get(raw, "seq", default=0),
        data_time=_get(raw, "data_time", "dataTime", default=""),
        avg_hr=_get(raw, "avg_hr", "avgHr"),
        max_hr=_get(raw, "max_hr", "maxHr"),
        min_hr=_get(raw, "min_hr", "minHr"),
        fatigue=_get(raw, "fatigue"),
        rmssd=_get(raw, "rmssd"),
        bp_hr=_get(raw, "bp_hr", "bpHr"),
        sbp=_get(raw, "sbp"),
        dbp=_get(raw, "dbp"),
        avg_spo2=_get(raw, "avg_spo2", "avgSpo2"),
        min_spo2=_get(raw, "min_spo2", "minSpo2"),
        max_spo2=_get(raw, "max_spo2", "maxSpo2"),
        calorie=_get(raw, "calorie"),
        steps=_get(raw, "steps"),
        distance=float(distance_raw) if distance_raw is not None else None,
        estimate_temp=float(temp_raw) if temp_raw is not None else None,
        type=_get(raw, "type", default=""),
        person_id=_get(raw, "person_id", "personId", default=""),
        device_id=_get(raw, "device_id", "deviceId", default=""),
    )


def _parse_sleep(raw: dict) -> SleepCacheRecord:
    """解析睡眠记录，兼容驼峰与下划线字段"""
    return SleepCacheRecord(
        id=_get(raw, "id", default=""),
        person_id=_get(raw, "person_id", "personId", default=""),
        device_id=_get(raw, "device_id", "deviceId", default=""),
        collect_date=_get(raw, "collect_date", "collectDate", default=0),
        rri_data_list=_get(raw, "rri_data_list", "rriDataList", default=""),
        sleep_segments=_get(raw, "sleep_segments", "sleepSegments", default=""),
        create_time=_get(raw, "create_time", "createTime", default=""),
    )


class HealthAnalysisRequest(BaseModel):
    """健康分析请求体——直接传入数据库表数据，LLM 模型由服务启动配置决定"""
    person: dict = Field(..., description="biz_person 表数据，单条 JSON 对象")
    wristband_records: list[dict] = Field(default_factory=list, description="biz_wristband_cache 表数据，数组")
    sleep_records: list[dict] = Field(default_factory=list, description="biz_person_sleep_result 表数据，数组")


# ---------------------------------------------------------------------------
# 接口：统一健康分析
# ---------------------------------------------------------------------------

@app.post("/v1/health/analyze")
async def health_analyze(request: HealthAnalysisRequest):
    """
    统一健康分析接口，返回 7 个维度的分析结果。
    请求体直接传入 biz_person / biz_wristband_cache / biz_sleep_cache 三张表的数据。
    """
    # 解析请求数据（兼容 snake_case / camelCase）
    person = _parse_person(request.person)
    wristband_records = sorted(
        [_parse_wristband(item) for item in (request.wristband_records or [])],
        key=lambda x: x.data_time,
    )
    sleep_records = sorted(
        [_parse_sleep(item) for item in (request.sleep_records or [])],
        key=lambda x: x.collect_date,
    )

    # 确定报告周期
    if wristband_records:
        start = wristband_records[0].data_time[:10]
        end = wristband_records[-1].data_time[:10]
        days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days + 1
        report_period = f"近{days}天"
    elif sleep_records:
        report_period = f"{len(sleep_records)}晚睡眠数据"
    else:
        report_period = "暂无监测数据"

    # 获取 LLM 提供者并执行分析
    provider = get_provider()
    assistant = HealthAnalysisAssistant(provider, person)

    try:
        result = await asyncio.to_thread(
            assistant.analyze_all,
            wristband_records,
            sleep_records,
            report_period,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")

    return {
        "project_id": person.project_id,
        "device_id": person.device_id,
        "person_id": person.id_card or person.id,
        "report_period": report_period,
        "analysis": result,
    }


@app.post("/v1/health/analyze/stream")
async def health_analyze_stream(request: HealthAnalysisRequest):
    """流式健康分析接口 (SSE)"""

    # 解析请求数据（兼容 snake_case / camelCase）
    person = _parse_person(request.person)
    wristband_records = sorted(
        [_parse_wristband(item) for item in (request.wristband_records or [])],
        key=lambda x: x.data_time,
    )
    sleep_records = sorted(
        [_parse_sleep(item) for item in (request.sleep_records or [])],
        key=lambda x: x.collect_date,
    )

    if wristband_records:
        start = wristband_records[0].data_time[:10]
        end = wristband_records[-1].data_time[:10]
        days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days + 1
        report_period = f"近{days}天"
    elif sleep_records:
        report_period = f"{len(sleep_records)}晚睡眠数据"
    else:
        report_period = "暂无监测数据"

    provider = get_provider()
    assistant = HealthAnalysisAssistant(provider, person)

    def generate():
        yield json.dumps({
            "type": "meta",
            "project_id": person.project_id,
            "device_id": person.device_id,
            "person_id": person.id_card or person.id,
            "report_period": report_period,
        }, ensure_ascii=False) + "\n"
        yield from assistant.analyze_all_stream(wristband_records, sleep_records, report_period)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/v1/health/persons")
async def list_persons():
    """列出所有已加载的人员信息"""
    seen = set()
    persons = []
    for p in data_loader.persons.values():
        if p.id not in seen and p.name:
            seen.add(p.id)
            persons.append({
                "id": p.id,
                "name": p.name,
                "age": p.age,
                "gender": "男" if p.gender == "1" else "女",
                "device_id": p.device_id,
                "id_card": p.id_card,
                "bmi": p.bmi,
            })
    return {"persons": persons}


if __name__ == "__main__":
    print(f"数据目录: {DATA_DIR}")
    print(f"已加载 {len(data_loader.persons)} 条人员信息")
    print(f"已加载 {sum(len(v) for v in data_loader.wristband.values())} 条手环数据")
    print(f"已加载 {sum(len(v) for v in data_loader.sleep_cache.values())} 条睡眠数据")
    uvicorn.run(app, host="0.0.0.0", port=8006)
