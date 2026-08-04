#!/bin/bash
# Health Analysis Service 启动脚本
# 使用前请先设置 DEEPSEEK_API_KEY 环境变量，或创建 .env 文件：
#   echo 'DEEPSEEK_API_KEY=your-key-here' > .env

# 从 .env 文件加载（如存在）
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# 检查 API Key 是否已设置
if [ -z "$DEEPSEEK_API_KEY" ]; then
    echo "[ERROR] DEEPSEEK_API_KEY 未设置，请 export 或创建 .env 文件"
    exit 1
fi

# 创建日志目录
mkdir -p logs

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 启动服务..." | tee -a logs/service.log

PYTHON_BIN="$HOME/Setups/miniconda3/envs/smart_home/bin/python"
nohup "$PYTHON_BIN" -u server/health_analysis_service.py \
    >> logs/service.log 2>&1 &

PID=$!
echo "PID: $PID" | tee -a logs/service.log
echo "日志文件: logs/service.log"
echo "查看日志: tail -f logs/service.log"
