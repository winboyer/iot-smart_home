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

conda run -n smart_home python server/health_analysis_service.py
