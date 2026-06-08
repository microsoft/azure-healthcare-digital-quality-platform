ARG PY_BASE=python:3.11-slim
FROM ${PY_BASE}

WORKDIR /app
COPY providers/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY providers/backend/src/ .
COPY providers/_data/ ./_data/
ENV SAMPLE_DATA_DIR=/app/_data
ENV CONSUMERS_SAMPLE_PATIENTS_DIR=/app/_data/sample_patients

EXPOSE 5000

CMD ["fastapi", "run", "main.py", "--port", "5000"]