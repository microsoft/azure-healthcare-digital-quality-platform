FROM  python:3.11-slim

WORKDIR /app
# Build context is the repo root so the seed data files (_data/) can be
# baked into the image alongside the receivers backend source.
COPY receivers/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY receivers/backend/src/ .
COPY _data/ /app/data/

EXPOSE 5000

CMD ["fastapi", "run", "main.py", "--port", "5000"]