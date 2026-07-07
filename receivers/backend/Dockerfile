FROM  python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg unixodbc \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && . /etc/os-release \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/${VERSION_ID}/prod ${VERSION_CODENAME} main" > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
# Build context is the repo root so the seed data files (_data/) can be
# baked into the image alongside the receivers backend source.
COPY receivers/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY receivers/backend/src/ .
COPY _data/ /app/data/

EXPOSE 5000

CMD ["fastapi", "run", "main.py", "--port", "5000"]