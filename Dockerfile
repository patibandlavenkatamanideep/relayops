FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# Public deploy is template-only. The image installs core deps only (NOT ".[llm]"
# and NOT ".[dev]", so no anthropic SDK is present), and these defaults keep the
# reply composer deterministic even if a key were ever injected. The LLM arm is
# local-only and triple-gated: the [llm] extra + RELAYOPS_COMPOSER=llm +
# RELAYOPS_ALLOW_LLM=true + ANTHROPIC_API_KEY.
ENV RELAYOPS_COMPOSER=template
ENV RELAYOPS_ALLOW_LLM=false

WORKDIR /app

# chromadb pulls native extensions (e.g. hnswlib) that ship no prebuilt wheel and
# must be compiled, so the slim image needs a C/C++ toolchain at build time.
# Placed before COPY so this layer caches across application code changes.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# Install CORE deps only, pinned to the reproducible production set so a fresh
# build can't drift onto a newly-released (and possibly broken) version. The
# constraints file is generated from pyproject.toml's core dependencies for
# Python 3.12 (see constraints/railway.txt header to regenerate). No [dev]/[llm].
COPY . .
RUN python -m pip install --upgrade pip \
 && python -m pip install -c constraints/railway.txt .

EXPOSE 8501

CMD ["sh", "scripts/start_streamlit.sh"]
