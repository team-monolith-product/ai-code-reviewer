# Dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Checkout Action이 1001로 사용자를 줍니다
USER 1001

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ai_code_review.py .
COPY entrypoint.sh .

RUN chmod +x entrypoint.sh ai_code_review.py

ENTRYPOINT ["/app/entrypoint.sh"]
