FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

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
    && chmod 0440 /etc/sudoers.d/$USERNAME

RUN python -m venv $VIRTUAL_ENV \
    && chown -R $USERNAME:$USERNAME $VIRTUAL_ENV

WORKDIR /workspace

# Copia de requerimientos e instalación de dependencias
COPY kit/requirements.txt /workspace/requirements.txt

USER $USERNAME

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /workspace/requirements.txt

# Copia el contenido de kit directamente en la raíz de /workspace
COPY kit/ /workspace/

EXPOSE 8000

CMD ["make", "start"]