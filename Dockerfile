FROM python:3.10-slim

WORKDIR /app

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy packaging related files first
COPY pyproject.toml README.md ./

# Copy the source code
COPY src/ /app/src/

# Install the Python package with all distributed extras
RUN pip install --no-cache-dir -e ".[dev,distributed]"

# Copy other necessary project files
COPY .env /app/.env
COPY config /app/config

# Set environment variables for the cluster
ENV TEMPORAL_HOST="temporal:7233"
ENV RAY_ADDRESS="ray://ray-head:6379"
ENV SEMABRIDGE_ORCHESTRATOR="temporal"

CMD ["python", "-m", "semabridge.orchestration.temporal.worker"]
