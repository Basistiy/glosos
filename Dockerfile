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
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "livekit-agents[google,silero,turn-detector]~=1.4" \
    "livekit-plugins-noise-cancellation~=0.2" \
    "google-genai==1.64.0" \
    "python-dotenv>=1.2.1" \
    "pypdf" \
    "pdfplumber" \
    "pandas" \
    "matplotlib" \
    "openpyxl" \
    "xlsxwriter" \
    "orjson" \
    "ruamel.yaml" \
    "Pillow" \
    "python-docx" \
    "python-pptx" \
    "reportlab" \
    "httpx" \
    "beautifulsoup4" \
    "lxml" \
    "trafilatura" \
    "feedparser" \
    "duckduckgo-search"

RUN npm install --no-save --omit=dev \
    @firebase/app \
    @firebase/auth \
    @firebase/firestore \
    && npm cache clean --force \
    && rm -rf /root/.npm

RUN useradd -m -u 10001 appuser \
    && mkdir -p /app/config /app/user

COPY --chown=10001:10001 agent.py secret_agent.py token_agent.py run_token_agent.js script_scheduler.py sounds.py pyproject.toml uv.lock README.md LICENSE ./
COPY --chown=10001:10001 config/defaults.toml ./config/defaults.toml

USER appuser

CMD ["node", "run_token_agent.js"]
