# Use Python 3.12 slim image (ms-cql-sdk requires Python >=3.12)
# Build context is the digital-quality project root; orchestrator code lives
# under `submitters/orchestrator/src/` and measure definitions live under
# `measures/`.
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file (azure-agents-learning-sdk is installed from PyPI via requirements.txt)
COPY submitters/orchestrator/src/requirements.txt ./requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all Python application modules (prevents missing renamed executor modules)
COPY submitters/orchestrator/src/*.py ./

# Copy memory module
COPY submitters/orchestrator/src/memory/ ./memory/

# Copy measure definitions (CQL + markdown) from the project-root _measures/ folder
COPY _measures/ ./measures/

# Create non-root user
RUN useradd -m -u 1000 mcpuser && \
    chown -R mcpuser:mcpuser /app

# Switch to non-root user
USER mcpuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "digital_quality_orchestrator:app", "--host", "0.0.0.0", "--port", "8000"]
