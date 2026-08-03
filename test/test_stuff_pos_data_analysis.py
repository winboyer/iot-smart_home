"""
智能安全帽 - 在场工人列表接口 请求测试与分析

接口说明:
  GET https://smarthat.lanjiansuzhou.com/dashboard/proj/safety/present_worker/list
  查询参数: project_id, build_id, worker_name, token, areaLvl, orgLvl

返回数据包含:
  - 各班组的工人信息（姓名、手机号、工种、安全帽颜色、电量、位置坐标、运动状态等）
"""

import json
import ssl
from typing import Any, Dict, List, Optional
from urllib import error, request
from urllib.parse import urlencode


# ---------------------------------------------------------------------------
# 配置区
# ---------------------------------------------------------------------------
API_BASE = "https://smarthat.lanjiansuzhou.com/dashboard/proj/safety/present_worker/list"

DEFAULT_PARAMS = {
    "project_id": "61455993-9f17-47b2-87c1-2d08c9745359",
    "build_id": "",
    "worker_name": "",
    "token": "ZDhiMTBlM2ItNDMxNS00NjAwLWFlMTktNzEwOGUwMjNiNmZm",
    "areaLvl": "100000",
    "orgLvl": "",
}


# ---------------------------------------------------------------------------
# HTTP 请求
# ---------------------------------------------------------------------------
def fetch_present_workers(
    project_id: Optional[str] = None,
    build_id: Optional[str] = None,
    worker_name: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """通过 HTTP GET 获取项目在场工人列表数据。

    Args:
        project_id: 项目 ID，为 None 时使用默认值。
        build_id:   楼栋 ID，空字符串表示不限。
        worker_name:工人姓名模糊搜索，空字符串表示不限。
        timeout:    请求超时时间（秒）。

    Returns:
        接口返回的 JSON 字典，结构为:
        {
            "code": 0,
            "msg": "",
            "count": int,
            "data": [
                {
                    "team_name": "...",
                    "worker_list": [ {...}, ... ]
                },
                ...
            ]
        }

    Raises:
        RuntimeError: 网络或 HTTP 错误。
    """
    params = dict(DEFAULT_PARAMS)
    if project_id is not None:
        params["project_id"] = project_id
    if build_id is not None:
        params["build_id"] = build_id
    if worker_name is not None:
        params["worker_name"] = worker_name

    url = f"{API_BASE}?{urlencode(params)}"

    req = request.Request(url, method="GET")

    # 创建不验证 SSL 证书的 context（测试环境使用）
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    try:
        with request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP 错误 {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"网络错误: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# 数据解析 / 分析
# ---------------------------------------------------------------------------
def parse_workers(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """将原始响应拍平为一维的工人列表，并附上班组名。"""
    workers: List[Dict[str, Any]] = []
    for team in raw.get("data", []):
        team_name = team.get("team_name", "未知班组")
        for w in team.get("worker_list", []):
            w["_team_name"] = team_name
            workers.append(w)
    return workers


def analyze(raw: Dict[str, Any]) -> Dict[str, Any]:
    """对返回数据进行统计分析。"""
    workers = parse_workers(raw)

    if not workers:
        return {
            "total_workers": 0,
            "total_teams": 0,
            "teams": [],
            "alarms": [],
            "low_battery": [],
            "by_motion": {},
            "by_color": {},
            "by_worker_type": {},
            "by_area": {},
        }

    # 班组统计
    teams: Dict[str, int] = {}
    for w in workers:
        tn = w.get("_team_name", "未知")
        teams[tn] = teams.get(tn, 0) + 1

    # 告警
    alarms = [w for w in workers if w.get("alarm") != "正常"]

    # 低电量（< 20%）
    low_battery = [
        w for w in workers if int(w.get("battery_level", 100)) < 20
    ]

    # 按运动状态
    by_motion: Dict[str, int] = {}
    for w in workers:
        m = w.get("motion", "未知")
        by_motion[m] = by_motion.get(m, 0) + 1

    # 按安全帽颜色
    by_color: Dict[str, int] = {}
    for w in workers:
        c = w.get("color", "未知")
        by_color[c] = by_color.get(c, 0) + 1

    # 按工种
    by_type: Dict[str, int] = {}
    for w in workers:
        t = w.get("worker_type_name", "未知")
        by_type[t] = by_type.get(t, 0) + 1

    # 按区域
    by_area: Dict[str, int] = {}
    for w in workers:
        a = f"{w.get('build_name', '')} {w.get('area_name', '')}".strip()
        by_area[a] = by_area.get(a, 0) + 1

    return {
        "total_workers": len(workers),
        "total_teams": len(teams),
        "teams": [{"name": k, "count": v} for k, v in sorted(teams.items())],
        "alarms": [
            {
                "name": w.get("name"),
                "alarm": w.get("alarm"),
                "team": w.get("_team_name"),
            }
            for w in alarms
        ],
        "low_battery": [
            {
                "name": w.get("name"),
                "battery": w.get("battery_level"),
                "team": w.get("_team_name"),
            }
            for w in low_battery
        ],
        "by_motion": by_motion,
        "by_color": by_color,
        "by_worker_type": by_type,
        "by_area": by_area,
    }


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------
def print_worker_table(workers: List[Dict[str, Any]]) -> None:
    """打印工人信息表格。"""
    header = f"{'班组':<20} {'姓名':<10} {'手机号':<15} {'工种':<10} {'颜色':<6} {'电量':<6} {'位置':<20} {'状态':<6}"
    print(header)
    print("-" * len(header))
    for w in workers:
        line = (
            f"{w.get('_team_name', ''):<20} "
            f"{w.get('name', ''):<10} "
            f"{w.get('mobile', ''):<15} "
            f"{w.get('worker_type_name', ''):<10} "
            f"{w.get('color', ''):<6} "
            f"{w.get('battery_level', '') + '%':<6} "
            f"{w.get('area_name', ''):<20} "
            f"{w.get('motion', ''):<6}"
        )
        print(line)


def print_analysis(stats: Dict[str, Any]) -> None:
    """打印统计分析结果。"""
    print(f"\n{'='*60}")
    print("📊 在场工人统计分析")
    print(f"{'='*60}")
    print(f"工人总数:  {stats['total_workers']}")
    print(f"班组数量:  {stats['total_teams']}")
    print(f"\n班组分布:")
    for t in stats["teams"]:
        print(f"  · {t['name']}: {t['count']} 人")

    print(f"\n运动状态:")
    for k, v in stats["by_motion"].items():
        print(f"  · {k}: {v} 人")

    print(f"\n安全帽颜色:")
    for k, v in stats["by_color"].items():
        print(f"  · {k}: {v} 人")

    print(f"\n工种分布:")
    for k, v in stats["by_worker_type"].items():
        print(f"  · {k}: {v} 人")

    print(f"\n区域分布:")
    for k, v in stats["by_area"].items():
        print(f"  · {k}: {v} 人")

    if stats["alarms"]:
        print(f"\n⚠️  告警人员:")
        for a in stats["alarms"]:
            print(f"  · {a['name']} ({a['team']}): {a['alarm']}")

    if stats["low_battery"]:
        print(f"\n🔋 低电量人员 (< 20%):")
        for b in stats["low_battery"]:
            print(f"  · {b['name']} ({b['team']}): {b['battery']}%")


# ---------------------------------------------------------------------------
# 基本校验
# ---------------------------------------------------------------------------
def validate_response(raw: Dict[str, Any]) -> List[str]:
    """校验返回数据结构，返回所有发现的问题。"""
    issues: List[str] = []

    if raw.get("code") != 0:
        issues.append(f"接口返回非 0 状态码: code={raw.get('code')}, msg={raw.get('msg')}")

    if not isinstance(raw.get("data"), list):
        issues.append("data 字段不是列表")
        return issues

    for i, team in enumerate(raw["data"]):
        if "team_name" not in team:
            issues.append(f"data[{i}] 缺少 team_name")
        if "worker_list" not in team:
            issues.append(f"data[{i}] 缺少 worker_list")
            continue
        for j, w in enumerate(team.get("worker_list", [])):
            for field in ["name", "mobile", "worker_id", "hat_mac"]:
                if field not in w:
                    issues.append(f"data[{i}].worker_list[{j}] 缺少 {field}")

    return issues


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        raw_data = fetch_present_workers()
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        exit(1)

    # 1. 校验
    print("🔍 数据校验...")
    validation_issues = validate_response(raw_data)
    if validation_issues:
        print(f"⚠️  发现 {len(validation_issues)} 个问题:")
        for issue in validation_issues:
            print(f"  · {issue}")
    else:
        print("✅ 数据结构校验通过")

    # 2. 原始 JSON（可选）
    # print("\n原始返回数据:")
    # print(json.dumps(raw_data, ensure_ascii=False, indent=2))

    # 3. 工人表格
    workers = parse_workers(raw_data)
    print(f"\n{'='*60}")
    print(f"📋 在场工人列表（共 {len(workers)} 人）")
    print(f"{'='*60}")
    print_worker_table(workers)

    # 4. 统计分析
    stats = analyze(raw_data)
    print_analysis(stats)