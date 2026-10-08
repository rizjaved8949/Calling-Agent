# ffmpeg is the reason this is not python:*-alpine: the musl builds of ffmpeg
# are a separate package dance, and the recording path depends on it being
# present. Without ffmpeg the service still runs — it just stores audio exactly
# as it arrived, which means larger files and no seeking.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app

# Recordings fall back to this when no company has connected Drive and no
# bucket is configured. It does not survive a redeploy — that is the point of
# the remote backends, not a flaw in this line.
RUN mkdir -p data/recordings

EXPOSE 8000

# No --reload, and one worker by default: the Sheet-sync debounce keeps state
# in the process, so a second worker would mean two uploads racing. Scale with
# replicas behind the load balancer rather than workers inside the container.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
