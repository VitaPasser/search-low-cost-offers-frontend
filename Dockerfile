FROM astral/uv:0.12-python3.14-trixie
LABEL authors="vitapasser"

RUN apt-get update \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r dev \
    && useradd -r -g dev -m -d /home/dev dev \
    && mkdir -p /home/dev/app \
    && chown -R dev:dev /home/dev

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY --chown=dev:dev ./uv.lock /home/dev/app/uv.lock
COPY --chown=dev:dev ./pyproject.toml /home/dev/app/pyproject.toml
COPY --chown=dev:dev ./src /home/dev/app/src

WORKDIR /home/dev/app

RUN uv sync

USER dev

ENTRYPOINT ["uv","run","streamlit","run","./src/main.py"]