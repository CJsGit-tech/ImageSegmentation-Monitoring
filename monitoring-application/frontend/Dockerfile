FROM python:3.13.18-slim-bookworm AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt /build/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir --only-binary=:all: -r /build/requirements.txt

FROM python:3.13.18-slim-bookworm AS runtime
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
RUN useradd --create-home --uid 10001 app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY . /app
USER app
EXPOSE 8501
CMD ["streamlit", "run", "asgi.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
