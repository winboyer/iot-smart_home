"""规则引擎耗时对比测试"""
import time
from server.health_analysis_service import DataLoader, DataSummarizer, RuleBasedAnalyzer

loader = DataLoader()
loader.load_all()

wb = loader.get_wristband('42028120010818001X')
sl = loader.get_sleep_cache('42028120010818001X')
p = loader.resolve_person('42028120010818001X')

summaries = {
    'heart_rate': DataSummarizer.heart_rate_summary(wb),
    'blood_pressure': DataSummarizer.blood_pressure_summary(wb),
    'spo2': DataSummarizer.spo2_summary(wb),
    'temperature': DataSummarizer.temperature_summary(wb),
    'exercise': DataSummarizer.exercise_summary(wb),
    'sleep': DataSummarizer.sleep_summary(sl),
    'afib_risk': DataSummarizer.afib_risk_summary(wb, sl),
}

t0 = time.time()
result = {
    '综合分析': RuleBasedAnalyzer.comprehensive_analysis(summaries, p, '近一个月'),
    '睡眠分析': RuleBasedAnalyzer.sleep_analysis(summaries['sleep']),
    '心率房颤分析': {
        '心率分析': RuleBasedAnalyzer.heart_rate_analysis(summaries['heart_rate']),
        '房颤分析': RuleBasedAnalyzer.afib_analysis(summaries['afib_risk']),
    },
    '血压血氧分析': {
        '血压分析': RuleBasedAnalyzer.blood_pressure_analysis(summaries['blood_pressure']),
        '血氧分析': RuleBasedAnalyzer.spo2_analysis(summaries['spo2']),
    },
    '体温分析': RuleBasedAnalyzer.temperature_analysis(summaries['temperature']),
    '运动分析': RuleBasedAnalyzer.exercise_analysis(summaries['exercise']),
}
elapsed = round((time.time() - t0) * 1000)

print(f'===== 规则引擎分析耗时: {elapsed}ms =====')
print()
for key, val in result.items():
    print(f'--- {key} ---')
    if isinstance(val, dict):
        for k, v in val.items():
            print(f'  [{k}]: {v}')
    else:
        print(f'  {v}')
    print()
