# syntax=docker/dockerfile:1.7
# ------------------------------------------------------------------------
# imapsync web UI - full container with FastAPI backend, modern UI,
# and the imapsync Perl tool with every required CPAN dependency.
# ------------------------------------------------------------------------

ARG PY_VERSION=3.12-slim-bookworm

# ---- stage 1: build imapsync binary ------------------------------------
# A separate stage purely to run `perl -c` smoke check before we ship it.
FROM debian:bookworm-slim AS perl-build

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PERL_MM_USE_DEFAULT=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        build-essential make \
        perl perl-modules \
        libio-socket-ssl-perl libnet-ssleay-perl \
        libmail-imapclient-perl libencode-imaputf7-perl \
        libdigest-hmac-perl libdigest-md5-perl libdigest-sha-perl \
        libfile-copy-recursive-perl libfile-tail-perl \
        libterm-readkey-perl \
        libsys-meminfo-perl libregexp-common-perl \
        libunicode-string-perl \
        libcompress-zlib-perl \
        libreadonly-perl \
        cpanminus \
        wget \
    && rm -rf /var/lib/apt/lists/* \
    && cpanm --notest --no-man-pages --quiet \
        IO::Tee \
        File::Tail \
    || true

RUN wget -O /usr/local/bin/imapsync https://imapsync.lamiral.info/dist/imapsync \
    && chmod +x /usr/local/bin/imapsync \
    && ln -sf /usr/local/bin/imapsync /usr/bin/imapsync \
    && imapsync --version

# ---- stage 2: python runtime + UI --------------------------------------
FROM python:${PY_VERSION} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    IMAPSYNC_LOG_DIR=/var/log/imapsync-ui

# Pull imapsync binary from the previous stage.
COPY --from=perl-build /usr/local/bin/imapsync /usr/local/bin/imapsync
COPY --from=perl-build /usr/bin/imapsync /usr/bin/imapsync

# Install the same Perl + CPAN libs at runtime. Same arch, so direct copy
# would also work, but installing once is cleaner and survives base bumps.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        cpanminus \
        build-essential make \
        perl perl-modules \
        libio-socket-ssl-perl libnet-ssleay-perl \
        libmail-imapclient-perl libencode-imaputf7-perl \
        libdigest-hmac-perl libdigest-md5-perl libdigest-sha-perl \
        libfile-copy-recursive-perl libfile-tail-perl \
        libterm-readkey-perl \
        libsys-meminfo-perl libregexp-common-perl \
        libunicode-string-perl \
        libcompress-zlib-perl \
        libreadonly-perl \
        procps \
        tini \
    && cpanm --notest --no-man-pages --quiet \
        IO::Tee \
        File::Tail \
    && rm -rf /var/lib/apt/lists/* /root/.cpanm \
    && mkdir -p /var/log/imapsync-ui /app \
    && useradd --system --create-home --shell /usr/sbin/nologin imapui

# Smoke-check that imapsync can run in the runtime image.
RUN imapsync --version

WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend /app/backend
COPY frontend /app/frontend
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV IMAPSYNC_BIN=/usr/local/bin/imapsync

EXPOSE 8000
VOLUME ["/var/log/imapsync-ui"]
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
