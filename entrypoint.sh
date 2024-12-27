#!/bin/bash
set -e

export GITHUB_TOKEN="$INPUT_GITHUB_TOKEN"
export OPENAI_API_KEY="$INPUT_OPENAI_API_KEY"
export PR_NUMBER="$INPUT_PR_NUMBER"
export SYSTEM_PROMPT="$INPUT_SYSTEM_PROMPT"

python /app/ai_code_review.py
