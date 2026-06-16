FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# Public deploy is template-only. The image installs ".[dev]" (NOT ".[llm]", so no
# anthropic SDK is present), and these defaults keep the reply composer
# deterministic even if a key were ever injected. The LLM arm is local-only and
# triple-gated: the [llm] extra + RELAYOPS_COMPOSER=llm + RELAYOPS_ALLOW_LLM=true +
# ANTHROPIC_API_KEY.
ENV RELAYOPS_COMPOSER=template
ENV RELAYOPS_ALLOW_LLM=false

WORKDIR /app

# chromadb pulls native extensions (e.g. hnswlib) that ship no prebuilt wheel and
# must be compiled, so the slim image needs a C/C++ toolchain at build time.
# Placed before COPY so this layer caches across application code changes.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

# Single source of truth for dependencies: pyproject.toml.
# Install with the dev extra so the image can also run the test/eval suites.
COPY . .
RUN python -m pip install --upgrade pip && python -m pip install ".[dev]"

EXPOSE 8501

CMD ["sh", "scripts/start_streamlit.sh"]
