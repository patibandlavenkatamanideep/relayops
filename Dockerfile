FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

# Single source of truth for dependencies: pyproject.toml.
# Install with the dev extra so the image can also run the test/eval suites.
COPY . .
RUN python -m pip install --upgrade pip && python -m pip install ".[dev]"

EXPOSE 8501

CMD ["sh", "scripts/start_streamlit.sh"]
