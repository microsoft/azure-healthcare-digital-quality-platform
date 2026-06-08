FROM  python:3.11-slim

WORKDIR /app
COPY consumers/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY consumers/backend/src/ .
COPY consumers/_data/ ./_data/
ENV SAMPLE_DATA_DIR=/app/_data
ENV CONSUMERS_SAMPLE_PATIENTS_DIR=/app/_data/sample_patients

EXPOSE 5000

CMD ["fastapi", "run", "main.py", "--port", "5000"]