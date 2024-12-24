#!/bin/bash
set -e

export GITHUB_TOKEN="$INPUT_GITHUB_TOKEN"
export OPENAI_API_KEY="$INPUT_OPENAI_API_KEY"

# 2) 실제 Python 스크립트 실행
python /app/ai_code_review.py
