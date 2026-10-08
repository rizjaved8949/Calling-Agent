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
# The maintenance commands travel with the image, so a migration can be run
# from the Render shell rather than from a laptop that would otherwise need a
# copy of the production credentials to do it.
COPY scripts ./scripts

# Recordings fall back to this when no company has connected Drive and no
# bucket is configured. It does not survive a redeploy — that is the point of
# the remote backends, not a flaw in this line.
RUN mkdir -p data/recordings

EXPOSE 8000

# Shell form on purpose: Render (and Cloud Run, and Heroku) inject the port to
# listen on as $PORT, and a container that ignores it binds somewhere the load
# balancer is not looking — which presents as a deploy that builds, starts, and
# then fails its health check with nothing in the logs.
#
# No --reload, and one worker: the Google Sheet sync debounces in process
# memory, so a second worker would mean two uploads racing for the same file.
# Scale with instances behind the load balancer, not workers inside this.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
