# syntax=docker/dockerfile:1

# --- base: what every stage shares — the interpreter and the *runtime* system deps only.
# Build tooling (compilers, curl, git) lives in the build stage below and never reaches the
# runtime image.
FROM python:3.14-slim AS base

# uv installs the locked deps into this venv. It lives outside /app so the docker-compose
# `.:/app` volume mount can't shadow it; only-system stops uv from downloading a second
# Python (it reuses the image's interpreter).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_PREFERENCE=only-system \
    PATH="/opt/venv/bin:/root/.elan/bin:/root/.local/bin:${PATH}"

# Runtime system packages: bubblewrap for the Lean sandbox, ca-certificates so manage.py
# fetch_commons_thumbnail can talk HTTPS. BuildKit cache mounts keep the apt archives warm
# across rebuilds; removing docker-clean lets apt actually reuse them.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        bubblewrap


# --- build: heavy, rarely-changing work (build tools, uv, Lean toolchain, deps). ---
# Everything expensive lives here so the GitHub Actions layer cache can reuse it across
# runs; the runtime stage copies out only the artifacts (/opt/venv, /root/.elan).
FROM base AS build

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        curl \
        build-essential \
        git \
        unzip

# uv (Python package manager) and the Lean toolchain (via elan).
# elan-init is rustup-style: the unattended flag is `-y` (NOT `--yes`). With `--yes`
# it can't recognize the request, tries to prompt, and fails in a non-interactive build.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
# elan only *records* the default toolchain; `lean --version` forces the actual toolchain
# download so Lean is baked into this layer (otherwise `lean --server` would download
# hundreds of MB on first use in every container, breaking live feedback).
# Pisa only elaborates (`lean file.lean`, `lean --server`) — it never links native
# executables — so the toolchain's native-compilation half (~500 MB of static *.a libs,
# LLVM/clang, leanc) is deleted in the same layer to keep it out of the image. Trimming
# must happen here: removing files in a later layer would not shrink the image.
RUN curl -sSf https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --default-toolchain leanprover/lean4:stable --no-modify-path \
    && lean --version \
    && TOOLCHAIN="$(dirname "$(dirname "$(elan which lean)")")" \
    && find "$TOOLCHAIN" -name '*.a' -delete \
    && rm -rf "$TOOLCHAIN"/lib/libLLVM* "$TOOLCHAIN"/lib/libclang* "$TOOLCHAIN"/lib/clang \
        "$TOOLCHAIN"/bin/llvm* "$TOOLCHAIN"/bin/clang* "$TOOLCHAIN"/bin/ld.lld* \
        "$TOOLCHAIN"/bin/leanc

WORKDIR /app

# Install only the locked *dependencies* before copying source, so this layer caches
# independently of application code. `--no-install-project` skips building the `pisa` app
# itself (it's run via manage.py from /app, and its source isn't present at this layer);
# `--no-dev` excludes the dev group. The uv cache mount speeds re-resolves.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --- build-dev: build + the dev dependency group (coverage), for the test image. ---
FROM build AS build-dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project


# --- runtime (default target): lean production image — no compilers, no build tooling ---
FROM base AS runtime

COPY --from=build /opt/venv /opt/venv
COPY --from=build /root/.elan /root/.elan

# Bypass the elan shim at runtime: inside the bubblewrap sandbox (read-only filesystem, no
# network) the shim re-resolves the `stable` channel on every `lean` invocation and prints
# "warning: failed to query latest release" on stderr — which the editor then shows in its
# Messages panel on every run (ELAN_OFFLINE doesn't prevent it there). `elan which` resolves
# the shim to the actual toolchain binary once, at build time, when the network is up.
RUN ln -s "$(elan which lean)" /usr/local/bin/lean-direct
ENV ELAN_OFFLINE=1 \
    LEAN_EXECUTABLE=/usr/local/bin/lean-direct \
    LEAN_LSP_CMD="/usr/local/bin/lean-direct --server"

WORKDIR /app
COPY . /app
RUN chmod +x /app/scripts/entrypoint.sh

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
# Production default: serve over ASGI (WebSockets + HTTP) with daphne. The dev compose
# overrides this with `runserver` for autoreload.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "pisa.asgi:application"]


# --- test: runtime + dev tooling (coverage). Built by CI (--target test). ---
FROM runtime AS test

COPY --from=build-dev /opt/venv /opt/venv
# Default command runs the suite under coverage; CI overrides to also emit reports.
CMD ["coverage", "run", "manage.py", "test", "--verbosity=2"]
