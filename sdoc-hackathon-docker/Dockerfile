# Cloud Run build for the self-eval server. Unlike docker-compose.yml
# (which mounts data_v2/ and the ground truth as volumes), Cloud Run has
# no volume mounts, so this bakes both into the image instead. That's
# safe ONLY because this service is deployed with --no-allow-unauthenticated
# (IAM-gated) — never deploy this image publicly.
FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/scoring.py server/app.py server/score_cli.py ./
COPY data_v2/ /data/

ENV DATA_DIR=/data
ENV GROUND_TRUTH=/data/ground_truth.json

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
