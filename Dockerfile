FROM python:3.12-slim

WORKDIR /app

# deps first for layer caching
COPY solution/requirements.txt solution/requirements.txt
RUN pip install --no-cache-dir -r solution/requirements.txt

# app code + the dataset it reads (sdoc-hackathon-bundle has no ground
# truth in it — that only lives in sdoc-hackathon-docker, which is
# excluded via .dockerignore and must never end up in this image)
COPY solution/ solution/
COPY sdoc-hackathon-bundle/ sdoc-hackathon-bundle/

WORKDIR /app/solution/frontend

# Cloud Run sets PORT itself (default 8080) and expects the process to
# read it — gmail_dashboard.py already does via os.environ.get('PORT').
ENV PORT=8080
EXPOSE 8080

CMD ["python", "gmail_dashboard.py"]
