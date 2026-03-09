FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libglib2.0-0 \
        libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "livekit-agents[google,silero,turn-detector]~=1.4" \
    "livekit-plugins-noise-cancellation~=0.2" \
    "google-genai==1.64.0" \
    "python-dotenv>=1.2.1"

COPY agent.py secret_agent.py script_scheduler.py pyproject.toml uv.lock README.md LICENSE ./
COPY config/defaults.toml ./config/defaults.toml

RUN useradd -m -u 10001 appuser \
    && mkdir -p /app/config /app/user \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "secret_agent.py", "start"]
