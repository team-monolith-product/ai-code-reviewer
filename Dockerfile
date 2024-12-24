# Dockerfile
FROM python:3.11-slim

# 1) OS 업데이트
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# 2) 작업 디렉토리 생성
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ai_code_review.py .

RUN chmod +x ai_code_review.py
CMD ["python", "ai_code_review.py"]
