FROM python:3.13.18-slim-bookworm AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt /build/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir --only-binary=:all: -r /build/requirements.txt

FROM builder AS test
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements-dev.txt /build/requirements-dev.txt
RUN pip install --no-cache-dir --only-binary=:all: -r /build/requirements-dev.txt
COPY . /app
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM python:3.13.18-slim-bookworm AS runtime
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 10001 app
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic
COPY app /app/app
USER app
EXPOSE 8000
CMD ["python", "-m", "app.prestart"]
