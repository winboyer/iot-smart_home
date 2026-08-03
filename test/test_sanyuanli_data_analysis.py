import json
import ssl
from datetime import datetime
from typing import Any, Dict
from urllib import error, request


API_URL = "https://dmap.cscec3bxjy.cn/api/dibang/report/workerstatus"


def fetch_worker_status(
	project_id: str,
	query_type: str,
	end_time: str,
	timeout: int = 30,
) -> Dict[str, Any]:
	"""通过 HTTP POST 获取项目工人状态数据。"""
	if query_type not in {"week", "day"}:
		raise ValueError("query_type 只能是 'week' 或 'day'")

	try:
		# 校验时间格式: 2026-04-10 10:00:00
		datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
	except ValueError as exc:
		raise ValueError("end_time 格式必须是 YYYY-MM-DD HH:MM:SS") from exc

	payload = {
		"project_id": project_id,
		"query_type": query_type,
		"end_time": end_time,
	}

	data = json.dumps(payload).encode("utf-8")
	req = request.Request(
		API_URL,
		data=data,
		method="POST",
		headers={"Content-Type": "application/json"},
	)

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


if __name__ == "__main__":
	# 将这里替换为你的真实项目 ID
	project_id = "sanyuanli"

	# 可选值: day / week
	query_type = "week"

	# 时间格式必须为: YYYY-MM-DD HH:MM:SS
	end_time = "2026-04-10 10:00:00"
	# end_time = "2026-06-02 23:59:59"

	try:
		result = fetch_worker_status(
			project_id=project_id,
			query_type=query_type,
			end_time=end_time,
		)
		print("请求成功，返回数据:")
		print(result)
	except Exception as e:
		print(f"请求失败: {e}")
