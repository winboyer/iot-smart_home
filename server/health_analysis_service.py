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
from typing import Any, Iterator, Optional

import httpx
from openai import OpenAI
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
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
# 极地项目（睡眠传感器）健康体检数据分析
# ---------------------------------------------------------------------------
# 输入契约：极地项目睡眠传感器上报的「一条汇总数据」——即《睡眠传感器数据》
# 规格中 16 个字段合并成的单个 JSON 对象，一个请求 = 一条汇总数据。
# 聚焦睡眠时长、夜间心率、呼吸三项核心指标，输出
# 综合分析（综合评价 + 体检建议）/ 睡眠分析 / 心率呼吸分析（心率分析 + 呼吸分析）。
# 与手环分析链路相互独立：本段不引用、不修改任何手环相关逻辑。

# 8 字节「睡眠综合状态」字段位序
POLAR_COMPREHENSIVE_LAYOUT = (
    "存在于否", "睡眠状态", "平均呼吸", "平均心跳",
    "翻身次数", "大幅度体动占比", "小幅度体动占比", "呼吸暂停次数",
)

# 12 字节「睡眠质量分析」字段位序（睡眠总时长占第 2、3 字节，大端序）
POLAR_QUALITY_LAYOUT = (
    "睡眠质量评分", "睡眠总时长高字节", "睡眠总时长低字节",
    "清醒时长占比", "浅睡时长占比", "深睡时长占比",
    "离床时长", "离床次数", "翻身次数", "平均呼吸", "平均心跳", "呼吸暂停次数",
)
POLAR_QUALITY_TOTAL_MINUTES_SLICE = (1, 3)

# 报告周期文案：上报数据为单条汇总，不含统计起止日期
POLAR_PERIOD_TEXT = "本次监测"

# 统计周期类型（periodType）-> 中文文案；键名统一按「小写、去分隔符」匹配
POLAR_PERIOD_TYPE_LABELS = {
    "day": "今日",
    "daily": "今日",
    "week": "近一周",
    "weekly": "近一周",
    "month": "近一个月",
    "monthly": "近一个月",
    "quarter": "近三个月",
    "threemonth": "近三个月",
    "threemonths": "近三个月",
    "year": "近一年",
    "yearly": "近一年",
}

# 规格 abnormalSleep 定义：0=睡眠时长不足 4 小时，1=睡眠时长大于 12 小时
POLAR_SLEEP_ABNORMAL_HOURS = (4.0, 12.0)
POLAR_ABNORMAL_SLEEP_LABELS = {
    0: "睡眠时长不足4小时",
    1: "睡眠时长大于12小时",
    2: "长时间异常无人",
    3: "无",
}
POLAR_SLEEP_STATUS_LABELS = {0: "深睡", 1: "浅睡", 2: "清醒", 3: "无"}


def _polar_int_array(raw, length: int) -> Optional[list]:
    """解析上报的字节数组，兼容 整型列表 / 十六进制串 / 逗号或空格分隔串 / 单个整数。
    不足 length 位在右侧补 0，超出则截断；无法解析返回 None。"""
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    if isinstance(raw, (list, tuple)):
        try:
            vals = [int(v) for v in raw]
        except (TypeError, ValueError):
            return None
    elif isinstance(raw, int):
        vals = [(raw >> (8 * (length - 1 - idx))) & 0xFF for idx in range(length)]
    elif isinstance(raw, str):
        text = raw.strip()
        if "," in text or " " in text:
            try:
                vals = [int(part) for part in re.split(r"[,\s]+", text) if part != ""]
            except ValueError:
                return None
        else:
            body = re.sub(r"^0x", "", text, flags=re.IGNORECASE).replace(":", "")
            if not re.fullmatch(r"[0-9A-Fa-f]+", body):
                return None
            if len(body) % 2:
                body = "0" + body
            vals = [int(body[i:i + 2], 16) for i in range(0, len(body), 2)]
    else:
        return None
    if not vals:
        return None
    if len(vals) < length:
        vals = vals + [0] * (length - len(vals))
    return vals[:length]


def _polar_flag(raw) -> Optional[bool]:
    """把 0/1、bool、"true"/"false" 等写法归一到 bool"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "正常", "有", "有人"}:
        return True
    if text in {"0", "false", "no", "n", "off", "异常", "无", "无人"}:
        return False
    try:
        return bool(int(float(text)))
    except (TypeError, ValueError):
        return None


# --- 上报指标（metrics）归一化 ------------------------------------------------
# 极地上报的 metrics 为 [{metricName, metricValue}] 数组，同一指标可能多次上报
# （如 heartRateValue1 / heartRateValue2，尾部数字为采集序号），
# 归一化时统一剥离序号后缀、忽略大小写与分隔符差异。
POLAR_METRIC_ALIASES = {
    # 睡眠总时长（直接以「小时」给出）
    "sleephours": "sleep_hours",
    "sleepdurationhours": "sleep_hours",
    "totalsleephours": "sleep_hours",
    # 夜间心率 / 呼吸 / 体动（多次上报取平均）
    "heartratevalue": "heart_rate_value",
    "heartrate": "heart_rate_value",
    "avgheartrate": "heart_rate_value",
    "breathvalue": "breath_value",
    "breathingvalue": "breath_value",
    "breathingratevalue": "breath_value",
    "breath": "breath_value",
    "movementsigns": "movement_signs",
    "movement": "movement_signs",
    # 状态 / 事件（取最后一次上报）
    "heartbeat": "heartbeat",
    "someoneexists": "someone_exists",
    "sleepstatus": "sleep_status",
    "getintobed": "get_into_bed",
    "inbed": "get_into_bed",
    "abnormalsleep": "abnormal_sleep",
    "sleepscore": "sleep_score",
    "breathealarm": "breathe_alarm",
    "breathingalarm": "breathe_alarm",
    "bedoffalarm": "bedoff_alarm",
    "outbedalarm": "bedoff_alarm",
    "sleepcomprehensivestatus": "comprehensive_status",
    "sleepqualityanalysis": "quality_analysis",
    # 分项时长：Duration 后缀视为分钟，Hours 后缀视为小时
    "awakeduration": "awake_duration",
    "awakehours": "awake_duration_hours",
    "lightsleepduration": "light_sleep_duration",
    "lightsleephours": "light_sleep_duration_hours",
    "deepsleepduration": "deep_sleep_duration",
    "deepsleephours": "deep_sleep_duration_hours",
}

# 多次上报需取平均的数值型指标；其余指标取最后一次上报值
POLAR_METRIC_AVERAGED = frozenset({
    "sleep_hours", "heart_rate_value", "breath_value", "movement_signs",
    "awake_duration", "awake_duration_hours",
    "light_sleep_duration", "light_sleep_duration_hours",
    "deep_sleep_duration", "deep_sleep_duration_hours",
})

# 内部指标键 -> 请求 raw 字段名（供 _parse_polar_sensor 消费）
POLAR_METRIC_PASSTHROUGH = {
    "heartbeat": "heartbeat",
    "someone_exists": "someoneExists",
    "sleep_status": "sleepStatus",
    "get_into_bed": "getIntoBed",
    "abnormal_sleep": "abnormalSleep",
    "sleep_score": "sleepScore",
    "breathe_alarm": "breathe_alarm",
    "bedoff_alarm": "bedoff_alarm",
    "comprehensive_status": "sleepComprehensiveStatus",
    "quality_analysis": "sleepQualityAnalysis",
    "heart_rate_value": "heartRateValue",
    "breath_value": "breathValue",
    "movement_signs": "movementSigns",
}
# 分钟量纲的分项时长直接透传；小时量纲的换算成分钟后透传
POLAR_MINUTES_PASSTHROUGH = {
    "awake_duration": "awakeDuration",
    "light_sleep_duration": "lightSleepDuration",
    "deep_sleep_duration": "deepSleepDuration",
}
POLAR_HOURS_TO_MINUTES = {
    "awake_duration_hours": "awakeDuration",
    "light_sleep_duration_hours": "lightSleepDuration",
    "deep_sleep_duration_hours": "deepSleepDuration",
}


def _polar_to_float(value) -> Optional[float]:
    """把上报的字符串数字 / 数字统一转成 float，无法转换返回 None"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _polar_metric_key(name) -> Optional[str]:
    """把上报的 metricName 归一化成内部指标键。

    忽略大小写与非字母数字差异，并剥离尾部采集序号：
    `heartRateValue1` / `heart_rate_value_1` -> `heart_rate_value`。
    """
    if name is None:
        return None
    token = re.sub(r"[^0-9a-z]", "", str(name).lower())
    token = re.sub(r"\d+$", "", token)
    if not token:
        return None
    return POLAR_METRIC_ALIASES.get(token)


def _polar_metrics_to_raw(metrics) -> dict:
    """把 metrics 数组 [{metricName, metricValue}] 归并成内部 raw 字段字典。

    - 数值型指标多次上报取平均，状态 / 事件型取最后一次；
    - 小时量纲的分项时长统一换算为分钟；
    - 无法识别的 metricName 直接忽略，不报错。
    """
    numeric: dict = {}
    latest: dict = {}

    for item in metrics or []:
        if isinstance(item, dict):
            name = item.get("metricName", item.get("metric_name"))
            value = item.get("metricValue", item.get("metric_value"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            name, value = item[0], item[1]
        else:
            continue

        key = _polar_metric_key(name)
        if key is None:
            continue
        if key in POLAR_METRIC_AVERAGED:
            val = _polar_to_float(value)
            if val is not None:
                numeric.setdefault(key, []).append(val)
        else:
            latest[key] = value

    averaged = {k: sum(v) / len(v) for k, v in numeric.items() if v}

    raw: dict = {}
    # 睡眠总时长（小时，直接给出）
    if "sleep_hours" in averaged:
        raw["sleepHours"] = averaged["sleep_hours"]
    # 分项时长
    for src, dst in POLAR_MINUTES_PASSTHROUGH.items():
        if src in averaged:
            raw[dst] = averaged[src]
    for src, dst in POLAR_HOURS_TO_MINUTES.items():
        if src in averaged and dst not in raw:
            raw[dst] = round(averaged[src] * 60.0, 4)
    # 心率 / 呼吸 / 体动
    for src in ("heart_rate_value", "breath_value", "movement_signs"):
        if src in averaged:
            raw[POLAR_METRIC_PASSTHROUGH[src]] = averaged[src]
    # 状态 / 事件型：取最后一次上报，且不覆盖上面已算出的数值
    for src, dst in POLAR_METRIC_PASSTHROUGH.items():
        if src in latest and dst not in raw:
            raw[dst] = latest[src]

    return raw


@dataclass
class PolarSensorRecord:
    """极地项目睡眠传感器的一条汇总数据（对应规格中的 16 个字段）"""

    heartbeat: Optional[bool] = None
    someone_exists: Optional[float] = None
    movement_signs: Optional[float] = None
    breath_value: Optional[float] = None
    heart_rate_value: Optional[float] = None
    sleep_status: Optional[float] = None
    get_into_bed: Optional[bool] = None
    awake_duration_minutes: Optional[float] = None
    light_sleep_minutes: Optional[float] = None
    deep_sleep_minutes: Optional[float] = None
    sleep_score: Optional[float] = None
    comprehensive_status: Optional[list] = None
    quality_analysis: Optional[list] = None
    abnormal_sleep: Optional[float] = None
    breathe_alarm: Optional[bool] = None
    bedoff_alarm: Optional[bool] = None
    # 上报直接给出的睡眠总时长（单位：小时），优先级高于字节数组与分项时长
    sleep_hours_direct: Optional[float] = None

    # -- 字节数组取值 ----------------------------------------------------
    def _cq(self, index: int):
        """取 8 字节「睡眠综合状态」中的第 index 个字节"""
        if not self.comprehensive_status or index >= len(self.comprehensive_status):
            return None
        return self.comprehensive_status[index]

    def _ql(self, index: int):
        """取 12 字节「睡眠质量分析」中的第 index 个字节"""
        if not self.quality_analysis or index >= len(self.quality_analysis):
            return None
        return self.quality_analysis[index]

    # -- 睡眠时长 --------------------------------------------------------
    @property
    def quality_total_minutes(self) -> Optional[float]:
        """「睡眠质量分析」上报的 2 字节睡眠总时长（大端，单位分钟）"""
        if not self.quality_analysis:
            return None
        lo, hi = POLAR_QUALITY_TOTAL_MINUTES_SLICE
        if len(self.quality_analysis) < hi:
            return None
        value = self.quality_analysis[lo] * 256 + self.quality_analysis[hi - 1]
        return float(value) if value > 0 else None

    @property
    def summary_total_minutes(self) -> Optional[float]:
        """分项时长合计（深睡 + 浅睡），单位分钟"""
        parts = [v for v in (self.deep_sleep_minutes, self.light_sleep_minutes) if v is not None]
        return float(sum(parts)) if parts else None

    @property
    def sleep_total_minutes(self) -> Optional[float]:
        """睡眠总时长（分钟）：上报直接给出的小时值 > 质量分析字节 > 深睡 + 浅睡"""
        if self.sleep_hours_direct is not None:
            return float(self.sleep_hours_direct) * 60.0
        return self.quality_total_minutes or self.summary_total_minutes

    @property
    def sleep_total_hours(self) -> Optional[float]:
        total = self.sleep_total_minutes
        return round(total / 60.0, 2) if total is not None else None

    @property
    def deep_sleep_hours(self) -> Optional[float]:
        return round(self.deep_sleep_minutes / 60.0, 2) if self.deep_sleep_minutes is not None else None

    @property
    def light_sleep_hours(self) -> Optional[float]:
        return round(self.light_sleep_minutes / 60.0, 2) if self.light_sleep_minutes is not None else None

    @property
    def awake_hours(self) -> Optional[float]:
        return round(self.awake_duration_minutes / 60.0, 2) if self.awake_duration_minutes is not None else None

    @property
    def in_bed_hours(self) -> Optional[float]:
        """卧床时长 = 睡眠总时长 + 清醒时长"""
        total, awake = self.sleep_total_minutes, self.awake_duration_minutes
        if total is None and awake is None:
            return None
        return round(((total or 0.0) + (awake or 0.0)) / 60.0, 2)

    # -- 心率 / 呼吸 -----------------------------------------------------
    @property
    def avg_heart_rate(self) -> Optional[float]:
        """夜间平均心率：综合状态字节 > 质量分析字节 > 实时心率值"""
        for val in (self._cq(3), self._ql(10), self.heart_rate_value):
            if val:
                return float(val)
        return None

    @property
    def avg_breathing_rate(self) -> Optional[float]:
        """夜间平均呼吸：综合状态字节 > 质量分析字节 > 实时呼吸值"""
        for val in (self._cq(2), self._ql(9), self.breath_value):
            if val:
                return float(val)
        return None

    @property
    def apnoea_count(self) -> Optional[float]:
        for val in (self._cq(7), self._ql(11)):
            if val is not None:
                return float(val)
        return None

    @property
    def turn_over_count(self) -> Optional[float]:
        for val in (self._cq(4), self._ql(8)):
            if val is not None:
                return float(val)
        return None

    @property
    def out_bed_count(self) -> Optional[float]:
        val = self._ql(7)
        return float(val) if val is not None else None

    @property
    def out_bed_minutes(self) -> Optional[float]:
        val = self._ql(6)
        return float(val) if val is not None else None

    @property
    def deep_sleep_ratio(self) -> Optional[float]:
        val = self._ql(5)
        return float(val) if val is not None else None

    @property
    def light_sleep_ratio(self) -> Optional[float]:
        val = self._ql(4)
        return float(val) if val is not None else None

    @property
    def awake_ratio(self) -> Optional[float]:
        val = self._ql(3)
        return float(val) if val is not None else None

    @property
    def sleep_status_text(self) -> str:
        if self.sleep_status is None:
            return "未知"
        return POLAR_SLEEP_STATUS_LABELS.get(int(self.sleep_status), "未知")

    @property
    def abnormal_sleep_text(self) -> str:
        if self.abnormal_sleep is None:
            return ""
        return POLAR_ABNORMAL_SLEEP_LABELS.get(int(self.abnormal_sleep), "")

    @property
    def has_valid_sleep_report(self) -> bool:
        """存在可用睡眠数据：有总时长，或有深/浅睡分项"""
        return self.sleep_total_minutes is not None


def _parse_polar_sensor(raw: dict) -> PolarSensorRecord:
    """把极地上报的一条汇总数据解析成 PolarSensorRecord，兼容 camelCase / snake_case"""

    def num(*keys: str) -> Optional[float]:
        val = _get(raw, *keys)
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    return PolarSensorRecord(
        heartbeat=_polar_flag(_get(raw, "heartbeat", "heart_beat")),
        someone_exists=num("someoneExists", "someone_exists"),
        movement_signs=num("movementSigns", "movement_signs"),
        breath_value=num("breathValue", "breath_value"),
        heart_rate_value=num("heartRateValue", "heart_rate_value"),
        sleep_status=num("sleepStatus", "sleep_status"),
        get_into_bed=_polar_flag(_get(raw, "getIntoBed", "get_into_bed")),
        awake_duration_minutes=num("awakeDuration", "awake_duration"),
        light_sleep_minutes=num("lightSleepDuration", "light_sleep_duration"),
        deep_sleep_minutes=num("deepSleepDuration", "deep_sleep_duration"),
        sleep_score=num("sleepScore", "sleep_score"),
        comprehensive_status=_polar_int_array(
            _get(raw, "sleepComprehensiveStatus", "sleep_comprehensive_status"), 8),
        quality_analysis=_polar_int_array(
            _get(raw, "sleepQualityAnalysis", "sleep_quality_analysis"), 12),
        abnormal_sleep=num("abnormalSleep", "abnormal_sleep"),
        breathe_alarm=_polar_flag(_get(raw, "breathe_alarm", "breatheAlarm")),
        bedoff_alarm=_polar_flag(_get(raw, "bedoff_alarm", "bedoffAlarm")),
        sleep_hours_direct=num("sleepHours", "sleep_hours", "totalSleepHours",
                               "sleepDurationHours", "total_sleep_hours"),
    )


class PolarDataSummarizer:
    """极地项目睡眠数据摘要：聚焦睡眠时长、夜间心率、呼吸三项核心指标"""

    # 成人正常参考区间
    SLEEP_DURATION_NORMAL = (7.0, 9.0)
    SLEEP_DURATION_ABNORMAL = POLAR_SLEEP_ABNORMAL_HOURS
    HEART_RATE_NORMAL = (60.0, 100.0)
    BREATHING_NORMAL = (12.0, 20.0)

    @classmethod
    def sleep_duration_summary(cls, record: PolarSensorRecord) -> dict:
        """睡眠时长摘要（含睡眠结构、评分、离床与翻身等辅助信息）"""
        total = record.sleep_total_hours
        lo, hi = cls.SLEEP_DURATION_NORMAL
        summary = {
            "has_data": total is not None,
            "total_sleep_hours": total,
            "normal_range": "%.0f~%.0f" % (lo, hi),
            "abnormal_sleep": record.abnormal_sleep,
            "abnormal_sleep_text": record.abnormal_sleep_text,
            "bedoff_alarm": record.bedoff_alarm,
        }
        if total is None:
            return summary
        return {
            **summary,
            "deep_sleep_hours": record.deep_sleep_hours,
            "light_sleep_hours": record.light_sleep_hours,
            "awake_hours": record.awake_hours,
            "in_bed_hours": record.in_bed_hours,
            "deep_sleep_ratio": record.deep_sleep_ratio,
            "light_sleep_ratio": record.light_sleep_ratio,
            "awake_ratio": record.awake_ratio,
            "sleep_score": record.sleep_score,
            "turn_over_count": record.turn_over_count,
            "out_bed_count": record.out_bed_count,
            "out_bed_minutes": record.out_bed_minutes,
            "below_normal": total < lo,
            "above_normal": total > hi,
        }

    @classmethod
    def heart_rate_summary(cls, record: PolarSensorRecord) -> dict:
        """夜间心率摘要"""
        avg = record.avg_heart_rate
        lo, hi = cls.HEART_RATE_NORMAL
        summary = {
            "has_data": avg is not None,
            "avg_heart_rate": avg,
            "normal_range": "%.0f~%.0f" % (lo, hi),
            "alarm": None,
        }
        if avg is None:
            return summary
        return {**summary, "below_normal": avg < lo, "above_normal": avg > hi}

    @classmethod
    def breathing_summary(cls, record: PolarSensorRecord) -> dict:
        """呼吸摘要（含呼吸暂停与设备呼吸报警）"""
        avg = record.avg_breathing_rate
        lo, hi = cls.BREATHING_NORMAL
        summary = {
            "has_data": avg is not None,
            "avg_breathing_rate": avg,
            "normal_range": "%.0f~%.0f" % (lo, hi),
            "apnoea_count": record.apnoea_count,
            "alarm": record.breathe_alarm,
        }
        if avg is None:
            return summary
        return {**summary, "below_normal": avg < lo, "above_normal": avg > hi}

    @classmethod
    def summarize(cls, record: PolarSensorRecord) -> dict:
        return {
            "sleep_duration": cls.sleep_duration_summary(record),
            "heart_rate": cls.heart_rate_summary(record),
            "breathing": cls.breathing_summary(record),
        }


class PolarMultiDaySummarizer:
    """多日（周期）睡眠数据汇总

    把 records（多天上报）汇总成与 PolarDataSummarizer.summarize 同形的摘要结构，
    因此规则引擎与各分析 Prompt 可直接复用。区别在于数值含义为「多日统计」：
    均值、极值、达标天数占比、逐日明细。仅一天输入时与单条汇总等价。
    """

    @staticmethod
    def _stats(values):
        vals = [v for v in values if v is not None]
        if not vals:
            return {"has_data": False, "avg": None, "min": None, "max": None,
                    "days": 0, "daily": []}
        return {
            "has_data": True,
            "avg": round(sum(vals) / len(vals), 2),
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "days": len(vals),
            "daily": [round(v, 2) for v in vals],
        }

    @staticmethod
    def _compliance(values, lo, hi) -> float:
        """达标率：落在正常参考区间内的天数占比（0~1）"""
        vals = [v for v in values if v is not None]
        if not vals:
            return 0.0
        return round(sum(1 for v in vals if lo <= v <= hi) / len(vals), 4)

    @classmethod
    def sleep_duration_summary(cls, records) -> dict:
        lo, hi = PolarDataSummarizer.SLEEP_DURATION_NORMAL
        totals = [r.sleep_total_hours for r in records]
        st = cls._stats(totals)

        # 设备主动上报的睡眠异常：按严重程度取最严重的一天（0/1 > 2 > 3）
        abnormal = None
        for cand in (0, 1, 2, 3):
            if any(getattr(r, "abnormal_sleep", None) == cand for r in records):
                abnormal = cand
                break
        abnormal_text = ""
        for r in records:
            if getattr(r, "abnormal_sleep", None) == abnormal:
                abnormal_text = getattr(r, "abnormal_sleep_text", "") or ""
                break

        summary = {
            "has_data": st["has_data"],
            "days": st["days"],
            "total_sleep_hours": st["avg"],
            "min_sleep_hours": st["min"],
            "max_sleep_hours": st["max"],
            "daily_sleep_hours": st["daily"],
            "compliance_rate": cls._compliance(totals, lo, hi),
            "short_sleep_days": sum(1 for v in totals if v is not None and v < lo),
            "normal_range": "%.0f~%.0f" % (lo, hi),
            "abnormal_sleep": abnormal,
            "abnormal_sleep_text": abnormal_text,
            "bedoff_alarm": any(getattr(r, "bedoff_alarm", None) for r in records),
        }
        if not st["has_data"]:
            return summary

        deep = cls._stats([r.deep_sleep_hours for r in records])
        light = cls._stats([r.light_sleep_hours for r in records])
        awake = cls._stats([r.awake_hours for r in records])
        score = cls._stats([r.sleep_score for r in records])
        total = st["avg"]
        return {
            **summary,
            "deep_sleep_hours": deep["avg"],
            "light_sleep_hours": light["avg"],
            "awake_hours": awake["avg"],
            # 占比统一用「百分数」（24.9 表示 24.9%），与单日字节数组口径一致
            "deep_sleep_ratio": round(deep["avg"] / total * 100, 2) if deep["avg"] and total else None,
            "light_sleep_ratio": round(light["avg"] / total * 100, 2) if light["avg"] and total else None,
            "awake_ratio": round(awake["avg"] / total * 100, 2) if awake["avg"] and total else None,
            "sleep_score": score["avg"],
            "turn_over_count": cls._stats([r.turn_over_count for r in records])["avg"],
            "out_bed_count": cls._stats([r.out_bed_count for r in records])["avg"],
            "out_bed_minutes": cls._stats([r.out_bed_minutes for r in records])["avg"],
            "below_normal": total < lo,
            "above_normal": total > hi,
        }

    @classmethod
    def heart_rate_summary(cls, records) -> dict:
        lo, hi = PolarDataSummarizer.HEART_RATE_NORMAL
        vals = [r.avg_heart_rate for r in records]
        st = cls._stats(vals)
        summary = {
            "has_data": st["has_data"],
            "days": st["days"],
            "avg_heart_rate": st["avg"],
            "min_heart_rate": st["min"],
            "max_heart_rate": st["max"],
            "daily_heart_rate": st["daily"],
            "compliance_rate": cls._compliance(vals, lo, hi),
            "normal_range": "%.0f~%.0f" % (lo, hi),
            "alarm": None,
        }
        if not st["has_data"]:
            return summary
        return {**summary,
                "below_normal": st["avg"] < lo,
                "above_normal": st["avg"] > hi}

    @classmethod
    def breathing_summary(cls, records) -> dict:
        lo, hi = PolarDataSummarizer.BREATHING_NORMAL
        vals = [r.avg_breathing_rate for r in records]
        st = cls._stats(vals)
        apnoea = [r.apnoea_count for r in records if r.apnoea_count is not None]
        summary = {
            "has_data": st["has_data"],
            "days": st["days"],
            "avg_breathing_rate": st["avg"],
            "min_breathing_rate": st["min"],
            "max_breathing_rate": st["max"],
            "daily_breathing_rate": st["daily"],
            "compliance_rate": cls._compliance(vals, lo, hi),
            "normal_range": "%.0f~%.0f" % (lo, hi),
            "apnoea_count": round(sum(apnoea) / len(apnoea), 1) if apnoea else None,
            "apnoea_total": round(sum(apnoea), 1) if apnoea else None,
            "alarm": any(getattr(r, "breathe_alarm", None) for r in records),
        }
        if not st["has_data"]:
            return summary
        return {**summary,
                "below_normal": st["avg"] < lo,
                "above_normal": st["avg"] > hi}

    @classmethod
    def summarize(cls, records) -> dict:
        records = list(records or [])
        return {
            "sleep_duration": cls.sleep_duration_summary(records),
            "heart_rate": cls.heart_rate_summary(records),
            "breathing": cls.breathing_summary(records),
        }


class PolarScoreCalculator:
    """综合评分计算器（确定性）

      单项得分 = 均值得分 × 70% + 达标天数占比 × 100 × 30%
      综合评分 = Σ(单项得分 × 权重) ÷ Σ(有效项权重)

    缺数据的项不计入，剩余项权重归一化。同一份数据永远得到同一个分数，
    不随大模型输出波动；评分与结论标签一并注入 Prompt，保证文案与分数一致。
    """

    SLEEP = "睡眠"
    HEART_RATE = "心率"
    BREATHING = "呼吸"

    WEIGHTS = {SLEEP: 0.4, HEART_RATE: 0.3, BREATHING: 0.3}
    MEAN_WEIGHT = 0.7
    COMPLIANCE_WEIGHT = 0.3

    # 某项无数据时的计分：按「中等」计入并保留权重，避免数据不足时评分虚高
    MISSING_SCORE = 60.0

    # (分数下界, 等级名)，自上而下首个命中
    LEVELS = ((90, "优秀"), (80, "良好"), (70, "一般"), (60, "较差"), (0, "差"))

    # 均值得分分段：(下界, 上界, 分数)，按顺序取首个命中；均不命中取 FLOOR
    # 区间越靠近理想值分数越高，避免「只要落在正常区间就给满分」导致区分度不足
    SLEEP_BANDS = (
        (7.5, 8.5, 100.0),
        (7.0, 7.5, 92.0), (8.5, 9.0, 92.0),
        (6.5, 7.0, 84.0), (9.0, 9.5, 84.0),
        (6.0, 6.5, 74.0), (9.5, 10.0, 74.0),
        (5.0, 6.0, 62.0), (10.0, 11.0, 62.0),
        (4.0, 5.0, 48.0), (11.0, 12.0, 48.0),
    )
    SLEEP_FLOOR = 35.0
    HEART_RATE_BANDS = (
        (60.0, 80.0, 100.0),
        (80.0, 90.0, 92.0),
        (90.0, 100.0, 84.0),
        (55.0, 60.0, 85.0), (100.0, 105.0, 85.0),
        (50.0, 55.0, 70.0), (105.0, 110.0, 70.0),
    )
    HEART_RATE_FLOOR = 40.0
    BREATHING_BANDS = (
        (12.0, 18.0, 100.0),
        (18.0, 20.0, 92.0),
        (10.0, 12.0, 85.0), (20.0, 22.0, 85.0),
        (9.0, 10.0, 70.0), (22.0, 24.0, 70.0),
    )
    BREATHING_FLOOR = 40.0

    @classmethod
    def _band_score(cls, value, bands, floor):
        if value is None:
            return None
        for lo, hi, score in bands:
            if lo <= value <= hi:
                return score
        return floor

    @classmethod
    def _level(cls, total: int) -> str:
        for floor, name in cls.LEVELS:
            if total >= floor:
                return name
        return cls.LEVELS[-1][1]

    @classmethod
    def _note(cls) -> str:
        return ("综合评分 = 睡眠 %.0f%% + 心率 %.0f%% + 呼吸 %.0f%%；"
                "单项得分 = 均值得分 %.0f%% + 达标天数占比 %.0f%%；"
                "某项无数据时按 %.0f 分计入并标记 missing"
                % (cls.WEIGHTS[cls.SLEEP] * 100,
                   cls.WEIGHTS[cls.HEART_RATE] * 100,
                   cls.WEIGHTS[cls.BREATHING] * 100,
                   cls.MEAN_WEIGHT * 100,
                   cls.COMPLIANCE_WEIGHT * 100,
                   cls.MISSING_SCORE))

    @classmethod
    def compute(cls, summaries: dict, statuses: dict) -> dict:
        specs = (
            (cls.SLEEP, "sleep_duration", "睡眠分析", "total_sleep_hours",
             cls.SLEEP_BANDS, cls.SLEEP_FLOOR),
            (cls.HEART_RATE, "heart_rate", "心率分析", "avg_heart_rate",
             cls.HEART_RATE_BANDS, cls.HEART_RATE_FLOOR),
            (cls.BREATHING, "breathing", "呼吸分析", "avg_breathing_rate",
             cls.BREATHING_BANDS, cls.BREATHING_FLOOR),
        )

        items, weighted, weight_sum = {}, 0.0, 0.0
        for label, key, status_key, value_key, bands, floor in specs:
            data = summaries.get(key) or {}
            mean = data.get(value_key)
            weight = cls.WEIGHTS[label]
            mean_score = cls._band_score(mean, bands, floor)

            if mean_score is None:
                # 该项无数据：按 MISSING_SCORE 计入权重，标注 missing 供调用方识别
                items[label] = {
                    "score": cls.MISSING_SCORE,
                    "weight": weight,
                    "mean": None,
                    "compliance_rate": None,
                    "status": statuses.get(status_key, ""),
                    "missing": True,
                }
                weighted += cls.MISSING_SCORE * weight
                weight_sum += weight
                continue

            rate = data.get("compliance_rate") or 0.0
            score = mean_score * cls.MEAN_WEIGHT + rate * 100.0 * cls.COMPLIANCE_WEIGHT
            items[label] = {
                "score": round(score, 1),
                "weight": weight,
                "mean": mean,
                "compliance_rate": rate,
                "status": statuses.get(status_key, ""),
                "missing": False,
            }
            weighted += score * weight
            weight_sum += weight

        result = {"items": items, "weights": dict(cls.WEIGHTS), "note": cls._note()}
        # 三项全部无数据：不给分数（避免"没有数据"被误读成"健康状况较差"）
        if weight_sum <= 0 or all(v.get("missing") for v in items.values()):
            result.update({"total": None, "level": "暂无评分"})
            return result
        total = int(round(weighted / weight_sum))
        result.update({"total": total, "level": cls._level(total)})
        return result


class PolarRuleBasedAnalyzer:
    """极地项目规则引擎：生成结论标签（正常/需关注/异常）与 LLM 不可用时的降级文本"""

    NORMAL = "正常"
    ATTENTION = "需关注"
    ABNORMAL = "异常"

    @classmethod
    def _sleep_level(cls, data: dict) -> str:
        """睡眠时长等级：优先采信设备 abnormalSleep 判定，其次按总时长区间"""
        if not data.get("has_data"):
            return cls.ATTENTION
        abnormal = data.get("abnormal_sleep")
        if abnormal in (0, 1):
            return cls.ABNORMAL
        total = data.get("total_sleep_hours")
        if total is None:
            return cls.ATTENTION
        ab_lo, ab_hi = PolarDataSummarizer.SLEEP_DURATION_ABNORMAL
        lo, hi = PolarDataSummarizer.SLEEP_DURATION_NORMAL
        if total < ab_lo or total > ab_hi:
            return cls.ABNORMAL
        return cls.NORMAL if lo <= total <= hi else cls.ATTENTION

    @classmethod
    def _heart_rate_level(cls, data: dict) -> str:
        if not data.get("has_data"):
            return cls.ATTENTION
        avg = data.get("avg_heart_rate")
        if avg is None:
            return cls.ATTENTION
        lo, hi = PolarDataSummarizer.HEART_RATE_NORMAL
        if avg < 50 or avg > 110:
            return cls.ABNORMAL
        return cls.NORMAL if lo <= avg <= hi else cls.ATTENTION

    @classmethod
    def _breathing_level(cls, data: dict) -> str:
        if not data.get("has_data"):
            return cls.ATTENTION
        avg = data.get("avg_breathing_rate")
        if avg is None:
            return cls.ATTENTION
        lo, hi = PolarDataSummarizer.BREATHING_NORMAL
        if avg < 9 or avg > 24:
            return cls.ABNORMAL
        return cls.NORMAL if lo <= avg <= hi else cls.ATTENTION

    @classmethod
    def evaluate_status(cls, summaries: dict) -> dict:
        """基于数据摘要计算各分析项的结论标签"""
        sleep = summaries.get("sleep_duration", {})
        hr = summaries.get("heart_rate", {})
        br = summaries.get("breathing", {})

        levels = {
            "睡眠分析": cls._sleep_level(sleep),
            "心率分析": cls._heart_rate_level(hr),
            "呼吸分析": cls._breathing_level(br),
        }

        # 设备主动上报的报警：该维度至少「需关注」
        if br.get("alarm") and levels["呼吸分析"] == cls.NORMAL:
            levels["呼吸分析"] = cls.ATTENTION
        if sleep.get("bedoff_alarm") and levels["睡眠分析"] == cls.NORMAL:
            levels["睡眠分析"] = cls.ATTENTION
        # 设备判定「长时间异常无人」：睡眠维度至少「需关注」
        if sleep.get("abnormal_sleep") == 2 and levels["睡眠分析"] == cls.NORMAL:
            levels["睡眠分析"] = cls.ATTENTION

        if cls.ABNORMAL in levels.values():
            overall = cls.ABNORMAL
        elif cls.ATTENTION in levels.values():
            overall = cls.ATTENTION
        else:
            overall = cls.NORMAL

        levels["综合评价"] = overall
        return levels

    # ------------------------------------------------------------------
    # 降级文本（LLM 不可用时使用）
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt(val, suffix="", digits=None):
        if val is None:
            return None
        if digits is not None and isinstance(val, (int, float)):
            val = round(val, digits)
        return f"{val}{suffix}"

    @classmethod
    def comprehensive_analysis(cls, summaries: dict, report_period: str) -> dict:
        """规则生成综合评价 + 体检建议"""
        sleep = summaries.get("sleep_duration", {})
        hr = summaries.get("heart_rate", {})
        br = summaries.get("breathing", {})
        status = cls.evaluate_status(summaries)["综合评价"]

        if not sleep.get("has_data"):
            return {
                "综合评价": f"{report_period}内暂无有效睡眠监测数据，无法生成综合评价。建议持续使用睡眠传感器采集数据后再查看分析。",
                "体检建议": "建议定期体检，重点关注睡眠质量、心血管及呼吸系统等基础指标。",
            }

        details = []
        if sleep.get("total_sleep_hours") is not None:
            details.append(f"睡眠总时长约{sleep.get('total_sleep_hours')}小时")
        if hr.get("avg_heart_rate") is not None:
            details.append(f"夜间平均心率约{hr.get('avg_heart_rate')}bpm")
        if br.get("avg_breathing_rate") is not None:
            details.append(f"平均呼吸约{br.get('avg_breathing_rate')}次/分")
        if sleep.get("abnormal_sleep_text") and sleep.get("abnormal_sleep") in (0, 1, 2):
            details.append(f"设备判定{sleep.get('abnormal_sleep_text')}")

        overall = (
            f"结合{report_period}数据，您的整体健康状况{status}。"
            + "，".join(details)
            + "。本报告聚焦睡眠时长、心率与呼吸三项核心指标。"
        )
        advice = "建议定期体检，重点关注睡眠质量、心血管及呼吸系统等基础指标。"
        return {"综合评价": overall, "体检建议": advice}

    @classmethod
    def sleep_analysis(cls, sleep: dict, report_period: str) -> str:
        if not sleep.get("has_data"):
            return f"{report_period}内暂无可用的睡眠数据，无法评估睡眠时长，建议持续使用睡眠传感器采集数据。"
        level = cls._sleep_level(sleep)
        lo, hi = PolarDataSummarizer.SLEEP_DURATION_NORMAL
        total = sleep.get("total_sleep_hours")

        parts = [f"{report_period}睡眠总时长约{total}小时"]
        if total is not None and total < lo:
            parts.append(f"低于成人推荐的{lo:.0f}-{hi:.0f}小时")
        elif total is not None and total > hi:
            parts.append(f"高于成人推荐的{lo:.0f}-{hi:.0f}小时")
        else:
            parts.append(f"处于成人推荐的{lo:.0f}-{hi:.0f}小时范围内")
        if sleep.get("deep_sleep_hours") is not None:
            deep = f"深睡约{sleep.get('deep_sleep_hours')}小时"
            if sleep.get("deep_sleep_ratio") is not None:
                deep += f"（占比{sleep.get('deep_sleep_ratio')}%）"
            parts.append(deep)
        if sleep.get("light_sleep_hours") is not None:
            parts.append(f"浅睡约{sleep.get('light_sleep_hours')}小时")
        if sleep.get("awake_hours") is not None:
            parts.append(f"清醒约{sleep.get('awake_hours')}小时")
        if sleep.get("sleep_score") is not None:
            parts.append(f"睡眠评分{sleep.get('sleep_score')}分")
        if sleep.get("turn_over_count") is not None:
            parts.append(f"翻身约{sleep.get('turn_over_count')}次")
        if sleep.get("out_bed_count") is not None:
            text = f"离床{sleep.get('out_bed_count')}次"
            if sleep.get("out_bed_minutes") is not None:
                text += f"（共{sleep.get('out_bed_minutes')}分钟）"
            parts.append(text)
        if sleep.get("abnormal_sleep_text") and sleep.get("abnormal_sleep") in (0, 1, 2):
            parts.append(f"设备判定{sleep.get('abnormal_sleep_text')}")
        if sleep.get("bedoff_alarm"):
            parts.append("监测到离床报警")

        if level == cls.NORMAL:
            tip = "睡眠时长总体达标，建议保持规律作息。"
        elif level == cls.ABNORMAL:
            tip = "睡眠时长明显偏离正常范围，建议关注生活习惯并咨询医生。"
        else:
            tip = "建议适当提前就寝、减少睡前电子设备使用，以延长有效睡眠时长。"

        return f"{level}，" + "，".join(parts) + "。" + tip

    @classmethod
    def heart_rate_analysis(cls, hr: dict, report_period: str) -> str:
        if not hr.get("has_data"):
            return f"{report_period}内暂无有效的夜间心率数据，无法评估心率水平。"
        level = cls._heart_rate_level(hr)
        lo, hi = PolarDataSummarizer.HEART_RATE_NORMAL
        avg = hr.get("avg_heart_rate")

        parts = [f"{report_period}夜间平均心率约{avg}bpm"]
        if avg is not None and lo <= avg <= hi:
            parts.append(f"处于正常参考范围（{lo:.0f}-{hi:.0f}bpm）")
        else:
            parts.append(f"偏离正常参考范围（{lo:.0f}-{hi:.0f}bpm）")

        tip = "建议保持规律作息、适度有氧运动。" if level == cls.NORMAL else "建议持续监测，必要时咨询医生。"
        return f"{level}，" + "，".join(parts) + "。" + tip

    @classmethod
    def breathing_analysis(cls, br: dict, report_period: str) -> str:
        if not br.get("has_data"):
            return f"{report_period}内暂无有效的呼吸数据，无法评估呼吸水平。"
        level = cls._breathing_level(br)
        lo, hi = PolarDataSummarizer.BREATHING_NORMAL
        avg = br.get("avg_breathing_rate")

        parts = [f"{report_period}夜间平均呼吸约{avg}次/分"]
        if avg is not None and lo <= avg <= hi:
            parts.append(f"处于正常参考范围（{lo:.0f}-{hi:.0f}次/分）")
        else:
            parts.append(f"偏离正常参考范围（{lo:.0f}-{hi:.0f}次/分）")
        if br.get("apnoea_count") is not None:
            parts.append(f"呼吸暂停约{int(br.get('apnoea_count'))}次")

        body = "，".join(parts) + "。"
        if br.get("alarm"):
            body += "设备上报呼吸异常报警，建议留意。"
        tip = "呼吸节律平稳，建议继续保持。" if level == cls.NORMAL else "建议持续监测，必要时咨询医生。"
        return f"{level}，{body}{tip}"


class PolarAnalysisAssistant:
    """极地项目健康体检数据分析助手：优先调用 LLM，失败时降级为规则引擎"""

    def __init__(self, provider: LLMProvider, person: Optional[PersonInfo] = None):
        self.provider = provider
        self.person = person

    def _person_desc(self) -> str:
        if self.person is None:
            return "未知"
        gender = {"1": "男", "2": "女"}.get(str(self.person.gender), "未知")
        parts = [p for p in (self.person.name, f"{self.person.age}岁" if self.person.age else "", gender) if p]
        return "，".join(parts) or "未知"

    @staticmethod
    def _score_text(score: dict, label: Optional[str] = None) -> str:
        """把评分结果转成 Prompt 可用的一句话（评分由规则引擎算出，模型照述即可）"""
        total = score.get("total")
        if total is None:
            return "综合评分：暂无评分（数据不足，不计入评分）"
        items = score.get("items") or {}
        if label and label in items:
            it = items[label]
            weight = it.get("weight") or 0.0
            return (f"本项评分 {it.get('score')} 分（在综合评分中占 {weight * 100:.0f}%）；"
                    f"综合评分 {total} 分（{score.get('level')}）")
        detail = "，".join(f"{k} {v.get('score')} 分" for k, v in items.items())
        return f"综合评分 {total} 分（{score.get('level')}），分项：{detail}"

    # ------------------------------------------------------------------
    # Prompt 构建
    # ------------------------------------------------------------------

    def _comprehensive_prompt(self, summaries: dict, report_period: str,
                              status: str, score: dict) -> str:
        """综合分析提示词（综合评价 + 体检建议）"""
        if not summaries.get("sleep_duration", {}).get("has_data"):
            return "暂无睡眠数据"
        return f"""你是一名家庭健康助手。请基于以下睡眠传感器监测数据，生成综合健康评价和体检建议。
本报告聚焦睡眠时长、心率、呼吸三项指标。

【用户信息】{self._person_desc()}
【评分】{self._score_text(score)}
【睡眠时长摘要】{json.dumps(summaries.get("sleep_duration", {}), ensure_ascii=False)}
【夜间心率摘要】{json.dumps(summaries.get("heart_rate", {}), ensure_ascii=False)}
【呼吸摘要】{json.dumps(summaries.get("breathing", {}), ensure_ascii=False)}

参考范围：成人睡眠时长 7-9 小时；夜间心率 60-100 bpm；呼吸率 12-20 次/分。

请严格按以下格式输出，每个部分用 "##" 分隔：

## 综合评价
必须以"结合{report_period}数据，您的整体健康状况{status}。"开头，随后用具体数据说明睡眠时长、夜间心率与呼吸的表现，80-150 字。

## 体检建议
给出 1-2 句体检建议，优先推荐与当前指标最相关的检查项目。

输出要求：必须在综合评价中自然提及【评分】给出的综合评分（原样引用该分数，不得自行改动或重算）；语气温暖积极，使用"您"称呼用户，不进行疾病诊断，禁止输出思考过程或 `<think>` 标签。"""

    def _sleep_prompt(self, summaries: dict, report_period: str,
                      status: str, score: dict) -> str:
        sleep = summaries.get("sleep_duration", {})
        if not sleep.get("has_data"):
            return "暂无睡眠数据"
        lo, hi = PolarDataSummarizer.SLEEP_DURATION_NORMAL
        return f"""你是一名家庭健康助手。请基于以下睡眠监测数据，生成睡眠时长分析。

【睡眠时长摘要】{json.dumps(sleep, ensure_ascii=False)}
【评分】{self._score_text(score, "睡眠")}

参考范围：成人建议睡眠时长 {lo:.0f}-{hi:.0f} 小时；深睡占比 15-25%；异常边界为低于 4 小时或高于 12 小时。

请仿照以下示例格式输出睡眠分析（一段话，200 字以内）：
示例："正常，您的睡眠整体达标：总时长符合健康标准，但仍有小幅提升空间。深睡占比略低于推荐区间，夜间有短暂觉醒。建议睡前减少电子设备使用。"

要求：先给出结论（{status}），再具体描述睡眠总时长、深睡与浅睡情况、睡眠评分与觉醒水平，最后给出 1 条改善建议。
使用"您"称呼用户，自然语言叙述，不进行疾病诊断。"""

    def _heart_rate_prompt(self, summaries: dict, report_period: str,
                           status: str, score: dict) -> str:
        hr = summaries.get("heart_rate", {})
        if not hr.get("has_data"):
            return "暂无心率数据"
        lo, hi = PolarDataSummarizer.HEART_RATE_NORMAL
        return f"""你是一名家庭健康助手。请基于以下夜间心率监测数据，生成心率分析。

【夜间心率摘要】{json.dumps(hr, ensure_ascii=False)}
【评分】{self._score_text(score, "心率")}

参考范围：成人夜间/静息心率 {lo:.0f}-{hi:.0f} bpm。

请仿照示例输出心率分析（一段话，150 字以内）：
示例："正常，您的心率节律规整，夜间平均心率约 61bpm，处于正常范围内，心脏电活动稳定。"

要求：先给出结论（{status}），再描述夜间平均心率及其与参考范围的关系，最后给出 1 条建议。
使用"您"称呼用户，不进行疾病诊断。"""

    def _breathing_prompt(self, summaries: dict, report_period: str,
                          status: str, score: dict) -> str:
        br = summaries.get("breathing", {})
        if not br.get("has_data"):
            return "暂无呼吸数据"
        lo, hi = PolarDataSummarizer.BREATHING_NORMAL
        return f"""你是一名家庭健康助手。请基于以下夜间呼吸监测数据，生成呼吸分析。

【呼吸摘要】{json.dumps(br, ensure_ascii=False)}
【评分】{self._score_text(score, "呼吸")}

参考范围：成人呼吸率 {lo:.0f}-{hi:.0f} 次/分。

请仿照示例输出呼吸分析（一段话，150 字以内）：
示例："正常，您的呼吸节律平稳，夜间平均呼吸约 14 次/分，处于正常范围内，呼吸暂停事件少，夜间通气状态良好。"

要求：先给出结论（{status}），再描述夜间平均呼吸率、呼吸暂停次数及有无设备报警，最后给出 1 条建议。
使用"您"称呼用户，不进行疾病诊断。"""

    # ------------------------------------------------------------------
    # 分析入口
    # ------------------------------------------------------------------

    def analyze_all(self, records, report_period: str) -> dict:
        """并发分析极地项目多日睡眠传感器数据，LLM 失败时自动降级为规则引擎

        返回 {"analysis": 三大分析项, "score": 综合评分, "statuses": 结论标签,
              "summaries": 多日汇总}；评分由规则引擎确定性算出后注入 Prompt，
        保证生成的文案与分数一致。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        records = list(records or [])
        summaries = PolarMultiDaySummarizer.summarize(records)
        statuses = PolarRuleBasedAnalyzer.evaluate_status(summaries)
        score = PolarScoreCalculator.compute(summaries, statuses)

        prompts = {
            "comprehensive": (self._comprehensive_prompt(summaries, report_period,
                                                         statuses["综合评价"], score), 600),
            "sleep": (self._sleep_prompt(summaries, report_period,
                                         statuses["睡眠分析"], score), 400),
            "heart_rate": (self._heart_rate_prompt(summaries, report_period,
                                                   statuses["心率分析"], score), 350),
            "breathing": (self._breathing_prompt(summaries, report_period,
                                                 statuses["呼吸分析"], score), 350),
        }

        def _call_llm(label: str, prompt: str, max_tokens: int) -> str:
            if prompt.startswith("暂无"):
                return ""
            try:
                return self.provider.chat(
                    [{"role": "user", "content": prompt}],
                    system_prompt=SYSTEM_PROMPT,
                    max_tokens=max_tokens,
                    trace_label=label,
                ).strip()
            except Exception:
                return ""

        def _clean_text(text: str) -> str:
            if not text or "LLM 错误" in text:
                return ""
            clean = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
            clean = re.sub(r"</?think>", "", clean, flags=re.IGNORECASE)
            return clean.strip()

        results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_map = {
                executor.submit(_call_llm, key, prompt, max_tokens): key
                for key, (prompt, max_tokens) in prompts.items()
            }
            for future in as_completed(future_map):
                key = future_map[future]
                try:
                    raw = future.result()
                except Exception:
                    raw = ""
                results[key] = _clean_text(raw)

        # 综合分析（综合评价 + 体检建议）
        comp_text = results.get("comprehensive", "")
        if comp_text:
            overall, advice = HealthAnalysisAssistant._parse_comprehensive(comp_text)
        else:
            fallback = PolarRuleBasedAnalyzer.comprehensive_analysis(summaries, report_period)
            overall, advice = fallback["综合评价"], fallback["体检建议"]

        sleep_text = results.get("sleep", "") or PolarRuleBasedAnalyzer.sleep_analysis(
            summaries.get("sleep_duration", {}), report_period)
        hr_text = results.get("heart_rate", "") or PolarRuleBasedAnalyzer.heart_rate_analysis(
            summaries.get("heart_rate", {}), report_period)
        br_text = results.get("breathing", "") or PolarRuleBasedAnalyzer.breathing_analysis(
            summaries.get("breathing", {}), report_period)

        return {
            "analysis": {
                "综合分析": {
                    "综合评价": {"标题": statuses["综合评价"], "内容": overall},
                    "体检建议": {"标题": "无", "内容": advice},
                },
                "睡眠分析": {"内容": sleep_text},
                "心率呼吸分析": {
                    "心率分析": {"标题": statuses["心率分析"], "内容": hr_text},
                    "呼吸分析": {"标题": statuses["呼吸分析"], "内容": br_text},
                },
            },
            "score": score,
            "statuses": statuses,
            "summaries": summaries,
        }


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
# 极地项目请求模型（一个统计周期的多日睡眠传感器上报数据）
# ---------------------------------------------------------------------------
# 一个请求 = 一个统计周期；records 数组中每一条 = 一天的监测数据。
# 键名兼容 camelCase 与 snake_case；metricValue 兼容字符串数字。

POLAR_FIELD_DESCRIPTIONS = {
    "heartbeat": "设备心跳包：0异常 / 1正常",
    "someoneExists": "人体存在状态：0无人 / 1有人",
    "movementSigns": "运动体征参数，上报间隔 10s，数值越大动作幅度越大",
    "breathValue": "呼吸数值，范围 0-30，状态变化上报，30 秒一次",
    "heartRateValue": "心率数值，范围 60-100，状态变化上报，30 秒一次",
    "sleepStatus": "睡眠状态：0深睡 / 1浅睡 / 2清醒 / 3无",
    "getIntoBed": "入床离床状态：0离床 / 1入床",
    "awakeDuration": "清醒时长，单位分钟",
    "lightSleepDuration": "浅睡时长，单位分钟",
    "deepSleepDuration": "深睡时长，单位分钟",
    "sleepScore": "睡眠评分，范围 0-100",
    "sleepComprehensiveStatus": "睡眠综合状态，8 个整型（0-255）：[存在、睡眠状态、平均呼吸、平均心跳、翻身次数、大幅度体动占比、小幅度体动占比、呼吸暂停次数]",
    "sleepQualityAnalysis": "睡眠质量分析，12 个整型（0-255）：[睡眠质量评分、睡眠总时长(2B)、清醒时长占比、浅睡时长占比、深睡时长占比、离床时长、离床次数、翻身次数、平均呼吸、平均心跳、呼吸暂停次数]",
    "abnormalSleep": "睡眠异常：0睡眠时长不足4小时 / 1睡眠时长大于12小时 / 2长时间异常无人 / 3无",
    "breathe_alarm": "呼吸异常报警：0正常 / 1异常",
    "bedoff_alarm": "离床报警：0正常 / 1异常",
}


class PolarAnalysisRequest(BaseModel):
    """极地项目健康体检数据分析请求体——一个统计周期的多日睡眠传感器上报数据

    一个请求 = 一个统计周期；records 中每一条 = 一天的监测数据。例如
       {"projectId": "SIM_PROJECT_001", "personId": "8800000000000001",
        "deviceId": "8001", "periodType": "week",
        "periodStart": "2026-10-06", "periodEnd": "2026-10-12",
        "records": [
          {"collectTime": "2026-10-06 06:28:00",
           "metrics": [{"metricName": "sleepHours", "metricValue": "7.10"},
                       {"metricName": "deepSleepHours", "metricValue": "1.80"},
                       {"metricName": "heartRateValue1", "metricValue": "62"},
                       {"metricName": "breathValue1", "metricValue": "15"}]},
          ...]}
    """

    model_config = ConfigDict(populate_by_name=True)

    # —— 上报元数据（原样回显到响应）——
    project_id: Optional[Any] = Field(default=None, alias="projectId",
                                      description="项目编码，如 SIM_PROJECT_001")
    person_id: Optional[Any] = Field(default=None, alias="personId",
                                     description="人员编码")
    device_id: Optional[Any] = Field(default=None, alias="deviceId",
                                     description="设备编码")

    # —— 统计周期 ——
    period_type: Optional[Any] = Field(
        default=None, alias="periodType",
        description="统计周期类型：day / week / month / quarter / year")
    period_start: Optional[Any] = Field(default=None, alias="periodStart",
                                        description="统计起始日期，YYYY-MM-DD")
    period_end: Optional[Any] = Field(default=None, alias="periodEnd",
                                      description="统计结束日期，YYYY-MM-DD")

    # —— 逐日监测记录 ——
    records: Optional[list] = Field(
        default=None, alias="records",
        description="逐日监测记录数组，元素为 {collectTime, metrics}。"
                    "metrics 为 [{metricName, metricValue}]；同一指标多次上报取平均，"
                    "尾部采集序号（如 heartRateValue1 / heartRateValue2）自动剥离；"
                    "metricName 支持 sleepHours / deepSleepHours / lightSleepHours / "
                    "awakeHours / heartRateValue / breathValue / movementSigns / "
                    "sleepScore / abnormalSleep / breathe_alarm / bedoff_alarm 等")

    person: Optional[dict] = Field(default=None, description="人员基础信息，可选，单条 JSON 对象")


def _polar_text(value) -> str:
    """把需要回显的元数据统一转成字符串（None / 空值 -> 空串）"""
    if value is None:
        return ""
    return str(value).strip()


def _polar_collect_date(collect_time: str) -> str:
    """从采集时间中取出日期部分（YYYY-MM-DD），取不到返回空串"""
    if not collect_time:
        return ""
    head = collect_time.strip()[:10]
    return head if re.fullmatch(r"\d{4}-\d{2}-\d{2}", head) else ""


def _polar_parse_records(items):
    """把 records 数组解析成 (逐日记录列表, 总条数, 有效条数)

    每条 record 形如 {collectTime, metrics:[{metricName, metricValue}]}；
    有效 = 该条至少解析出睡眠时长、夜间心率或呼吸之一。
    """
    records, total = [], 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        total += 1
        raw = _polar_metrics_to_raw(_get(item, "metrics", "Metrics"))
        records.append(_parse_polar_sensor(raw))

    valid = sum(
        1 for r in records
        if r.sleep_total_hours is not None
        or r.avg_heart_rate is not None
        or r.avg_breathing_rate is not None
    )
    return records, total, valid


def _polar_report_text(period_type, period_start, period_end) -> str:
    """生成报告周期文案：周期类型为主，起止日期为辅

    例：periodType=week + 2026-10-06~2026-10-12 -> "近一周（2026-10-06 至 2026-10-12）"
    注意：文案不要自带"数据"二字，降级模板会拼接成"结合{周期}数据"。
    """
    label = ""
    if period_type is not None and str(period_type).strip() != "":
        token = re.sub(r"[^0-9a-z]", "", str(period_type).lower())
        label = POLAR_PERIOD_TYPE_LABELS.get(token, "")

    start = _polar_collect_date(_polar_text(period_start))
    end = _polar_collect_date(_polar_text(period_end))
    if start and end and start != end:
        span = f"{start} 至 {end}"
    else:
        span = start or end

    if label and span:
        return f"{label}（{span}）"
    return label or span or POLAR_PERIOD_TEXT


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


# 请求体中的元数据字段：不参与指标解析，只用于原样回显


@app.post("/v1/health/polar/analyze")
async def polar_health_analyze(request: PolarAnalysisRequest):
    """
    极地项目健康体检数据 AI 分析接口（阻塞式）。

    请求体 = 一个统计周期的多日睡眠传感器上报数据：
      projectId / personId / deviceId / periodType / periodStart / periodEnd /
      records[{collectTime, metrics[{metricName, metricValue}]}]

    聚焦睡眠时长、夜间心率、呼吸三项核心指标，返回：
      上报元数据与周期回显、综合评分（score）、结论速览（conclusion）、
      完整分析（综合分析 / 睡眠分析 / 心率呼吸分析）。
    """
    person = _parse_person(request.person) if request.person else None

    records, record_count, valid_count = _polar_parse_records(request.records)
    report_period = _polar_report_text(request.period_type,
                                       request.period_start, request.period_end)

    provider = get_provider()
    assistant = PolarAnalysisAssistant(provider, person)

    try:
        result = await asyncio.to_thread(assistant.analyze_all, records, report_period)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")

    statuses = result.get("statuses") or {}
    score = result.get("score") or {}

    return {
        "projectId": _polar_text(request.project_id),
        "personId": _polar_text(request.person_id) or (
            (person.id_card or person.id) if person else ""),
        "deviceId": _polar_text(request.device_id) or (person.device_id if person else ""),
        "periodType": _polar_text(request.period_type),
        "periodStart": _polar_text(request.period_start),
        "periodEnd": _polar_text(request.period_end),
        "recordCount": record_count,
        "validRecordCount": valid_count,
        "reportPeriod": report_period,
        "score": score,
        "conclusion": {
            "综合评价": statuses.get("综合评价", ""),
            "睡眠分析": statuses.get("睡眠分析", ""),
            "心率分析": statuses.get("心率分析", ""),
            "呼吸分析": statuses.get("呼吸分析", ""),
        },
        "analysis": result.get("analysis") or {},
    }


if __name__ == "__main__":
    print(f"数据目录: {DATA_DIR}")
    print(f"已加载 {len(data_loader.persons)} 条人员信息")
    print(f"已加载 {sum(len(v) for v in data_loader.wristband.values())} 条手环数据")
    print(f"已加载 {sum(len(v) for v in data_loader.sleep_cache.values())} 条睡眠数据")
    uvicorn.run(app, host="0.0.0.0", port=8006)
