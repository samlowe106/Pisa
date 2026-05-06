FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.elan/bin:/root/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        build-essential \
        git \
        unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- --default-toolchain leanprover/lean4:stable --yes --no-modify-path

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN python -m pip install --upgrade pip setuptools wheel
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
RUN /root/.local/bin/uv pip install --system -e .

COPY . /app
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
