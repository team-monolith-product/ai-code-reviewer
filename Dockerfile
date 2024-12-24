# Dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ai_code_review.py .
COPY entrypoint.sh .

RUN chmod +x entrypoint.sh ai_code_review.py

ENTRYPOINT ["/app/entrypoint.sh"]
