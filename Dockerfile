# Shared base contains only what the unprivileged application needs at runtime.
FROM python:3.14-slim AS runtime-base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Development is selected explicitly by compose/devcontainer. It includes
# interactive tooling and sudo for the disposable developer account.
FROM runtime-base AS development

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    make \
    git \
    curl \
    sudo \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

ARG USERNAME=adminuser
ARG USER_UID=1000
ARG USER_GID=1000

RUN (getent group $USER_GID || groupadd --gid $USER_GID $USERNAME) \
    && (getent passwd $USER_UID || useradd --uid $USER_UID --gid $USER_GID -m $USERNAME) \
    && echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME \
    && python -m venv $VIRTUAL_ENV \
    && chown -R $USERNAME:$USERNAME $VIRTUAL_ENV

COPY app/requirements.txt /workspace/app/requirements.txt

USER $USERNAME

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /workspace/app/requirements.txt

COPY . /workspace/

CMD ["make", "-C", "app", "start"]

# The default image is the production runtime. It has no compiler, sudo, or
# development account and runs as an unprivileged user. ``make`` remains for
# the release migration and gunicorn entrypoints.
FROM runtime-base AS production

ARG USERNAME=appuser
ARG USER_UID=10001
ARG USER_GID=10001

RUN apt-get update && apt-get install -y --no-install-recommends \
    make \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID --create-home --shell /usr/sbin/nologin $USERNAME \
    && python -m venv $VIRTUAL_ENV \
    && chown -R $USERNAME:$USERNAME $VIRTUAL_ENV

COPY app/requirements.txt /workspace/app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /workspace/app/requirements.txt

COPY . /workspace/

USER $USERNAME

EXPOSE 8000

CMD ["make", "-C", "app", "gunicorn"]
