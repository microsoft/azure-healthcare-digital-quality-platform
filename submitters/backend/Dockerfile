ARG PY_BASE=python:3.11-slim
FROM ${PY_BASE}

WORKDIR /app
# Build context is the repo root so the seed data files (data/) can be
# baked into the image alongside the backend source.
COPY submitters/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY submitters/backend/src/ .
COPY _data/ /app/data/

EXPOSE 5000

CMD ["fastapi", "run", "main.py", "--port", "5000"]