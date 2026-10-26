# syntax=docker/dockerfile:1
#
# Ailurus Memory (ai-memory) -- container image.
#
#   docker build -t ailurus-memory .                 the image
#   docker build --target test -t ailurus-test .     the same image + the test suite run
#
# Stages:
#   base     the pinned Python base everything else starts from
#   model    downloads the embedding model, pinned by commit and checked by SHA-256
#   build    a venv with hash-locked dependencies + the project itself
#   runtime  what ships: venv, sources, model; non-root; no compiler, no pip cache
#   test     runtime + tests/, runs ./run-tests.sh at build time (CI smoke test)
#
# PYTHON 3.14: the same version as the development venv and the CI jobs, so a test that
# passes on a laptop is the test that passes here. Every native wheel the image needs is
# published for cp314 on BOTH linux/amd64 and linux/arm64 (manylinux, glibc):
# onnxruntime 1.31 (cp314 manylinux_2_28), numpy 2.5 (cp314), pydantic-core (cp314),
# tokenizers and cryptography (abi3) -- checked on PyPI 2026-10-26. That is why the base
# is Debian (glibc) and not Alpine: onnxruntime ships no musl wheels at all.
#
# The base is pinned by the digest of its multi-arch INDEX (not of one platform's
# manifest), so the amd64 and the arm64 builds both resolve to the same release.
# Dependabot (.github/dependabot.yml) proposes digest bumps; refresh the lock after one.
FROM docker.io/library/python:3.14.9-slim-trixie@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8 AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1


# ---- the embedding model ------------------------------------------------------------
# Its own stage so a code change never re-downloads 470 MB, and so the network access it
# needs is confined to one step whose output is verified.
FROM base AS model
COPY docker/fetch-model.py /tmp/fetch-model.py
RUN python /tmp/fetch-model.py /models/paraphrase-multilingual-MiniLM-L12-v2


# ---- dependencies + the project -----------------------------------------------------
FROM base AS build
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# The lock pins every package, transitive ones included, by version AND hash, for both
# architectures (all wheel hashes of each pinned release are listed). --require-hashes
# makes pip refuse anything not in it; --no-deps makes it refuse to resolve anything on
# its own. Regenerate it with the command at the top of the file.
COPY docker/requirements.lock /tmp/requirements.lock
RUN pip install --require-hashes --no-deps --only-binary=:all: -r /tmp/requirements.lock
# The sources, by name: tests, docs and packaging files for other platforms stay out.
# pyproject needs README.md (readme=) to build; the version comes from the package.
COPY pyproject.toml README.md LICENSE /app/
COPY ai_memory/ /app/ai_memory/
COPY docker/healthcheck.py /app/docker/healthcheck.py
# EDITABLE on purpose: the code finds its assets (the admin web's SVG, the icons)
# relative to its own __file__, and a regular install would put the modules in
# site-packages without them. Editable keeps the modules in /app, next to assets/, and
# still installs the console scripts (ai-memory, ai-memory-manage, ...) into the venv.
# --no-build-isolation: setuptools comes from the lock, not from an unpinned download.
# Then the build tools leave: the editable finder setuptools writes is self-contained,
# and nothing in the running image installs packages.
RUN pip install --no-deps --no-build-isolation -e /app \
 && python -m compileall -q /app \
 && pip uninstall -q -y setuptools \
 && python -m pip uninstall -q -y pip


# ---- what ships -----------------------------------------------------------------------
FROM base AS runtime

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Ailurus Memory" \
      org.opencontainers.image.description="Self-hosted, encrypted, shared memory for AI agents over MCP, with an admin web." \
      org.opencontainers.image.source="https://github.com/TursiThePanda/ailurus-memory" \
      org.opencontainers.image.url="https://github.com/TursiThePanda/ailurus-memory" \
      org.opencontainers.image.documentation="https://github.com/TursiThePanda/ailurus-memory/blob/master/docs/install/docker.md" \
      org.opencontainers.image.licenses="LicenseRef-PolyForm-Noncommercial-1.0.0" \
      org.opencontainers.image.authors="David Urban" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.base.name="docker.io/library/python:3.14.9-slim-trixie"

# A fixed, unprivileged uid/gid, so a bind-mounted /data can be prepared on the host with
# `chown 10001:10001`. No login shell, no sudo.
RUN groupadd --gid 10001 aimemory \
 && useradd --uid 10001 --gid 10001 --home-dir /home/aimemory --create-home \
            --shell /usr/sbin/nologin aimemory \
 && mkdir -p /data/config /data/attachments /backup \
 && chown -R 10001:10001 /data /backup \
 && chmod 0700 /data

COPY --from=build /opt/venv /opt/venv
COPY --from=build /app /app
COPY --from=model /models /models

# Paths are fixed by the image. Everything on /data survives a recreate: the database,
# the config dir (incl. the generated TLS certificate, so it does not change on every
# recreate) and the attachment read-out dir (the default would be a cache dir outside
# the volume).
#
# AI_MEMORY_HOST / TLS / PORT and the allow-lists are FIRST-START SEEDS: they fill the
# network settings once, after which the admin web owns them.
#
# The keyring backend is the "fail" one: there is no Secret Service in a container, and
# the passphrase must come from the secret file or the tty -- never from a keyring.
ENV PATH=/opt/venv/bin:$PATH \
    HOME=/home/aimemory \
    AI_MEMORY_DATA_DIR=/data \
    AI_MEMORY_CONFIG_DIR=/data/config \
    AI_MEMORY_ATTACHMENT_DIR=/data/attachments \
    AI_MEMORY_MODEL_DIR=/models/paraphrase-multilingual-MiniLM-L12-v2 \
    AI_MEMORY_BACKUP_DEST=/backup \
    AI_MEMORY_HOST=0.0.0.0 \
    AI_MEMORY_PORT=8080 \
    AI_MEMORY_TLS=self-signed \
    PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring

USER 10001:10001
WORKDIR /app
EXPOSE 8080
# chown above happens BEFORE this line: a fresh named volume is initialised from the
# image's /data, so it inherits the non-root owner. (Changes after VOLUME are discarded.)
VOLUME ["/data"]

# 200 only once the store is unlocked. The long start period covers an instance that is
# waiting for its passphrase on the tty (`docker attach`); see docker/healthcheck.py for
# why it tries HTTPS and then HTTP.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10m --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]

# No ENTRYPOINT: `docker run <image> ai-memory-manage ...` must work as typed.
# Exec form, so `ai-memory` is PID 1 and receives SIGTERM from `docker stop` directly.
CMD ["ai-memory", "run"]


# ---- the test suite, against the image's own environment ------------------------------
FROM runtime AS test
USER root
COPY . /app/
RUN chown -R 10001:10001 /app/tests
USER 10001:10001
# run-tests.sh has no venv here and falls back to python3, which is the venv's (PATH).
# The tests start real servers on localhost and feed the passphrase through the env.
RUN AI_MEMORY_PASSPHRASE=ci-test-passphrase HOME=/tmp ./run-tests.sh
