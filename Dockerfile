# syntax=docker/dockerfile:1

# --- base: heavy, rarely-changing layers (apt, uv, Lean toolchain, deps) ---
# Everything expensive lives here so the GitHub Actions layer cache can reuse it
# across runs. Source code is copied in the lighter stages below.
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.elan/bin:/root/.local/bin:${PATH}"

# System packages. BuildKit cache mounts keep the apt archives warm across rebuilds;
# removing docker-clean lets apt actually reuse them.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        build-essential \
        git \
        unzip

# uv (Python package manager) and the Lean toolchain (via elan).
# elan-init is rustup-style: the unattended flag is `-y` (NOT `--yes`). With `--yes`
# it can't recognize the request, tries to prompt, and fails in a non-interactive build.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
RUN curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --default-toolchain leanprover/lean4:stable --no-modify-path

WORKDIR /app

# Install Python dependencies before copying source so this layer caches
# independently of application code changes. The uv cache mount speeds re-resolves.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    /root/.local/bin/uv pip install --system -e .


# --- test: base + dev tooling (coverage) + source. Built by CI (--target test). ---
FROM base AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    /root/.local/bin/uv pip install --system 'coverage[toml]'
COPY . /app
# Default command runs the suite under coverage; CI overrides to also emit reports.
CMD ["coverage", "run", "manage.py", "test", "--verbosity=2"]


# --- runtime (default target): lean production image, no dev tooling ---
FROM base AS runtime

COPY . /app
RUN chmod +x /app/scripts/entrypoint.sh

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
