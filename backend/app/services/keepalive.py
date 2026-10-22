"""
Keeping the instance awake.

Render's free plan stops a service after roughly fifteen minutes without
inbound traffic and takes about a minute to start it again. That minute is the
problem: a call webhook arrives, Meta waits a few seconds, and gives up long
before the process is listening. The call is simply lost, and nothing in our
own logs records it, because we were not running to log it.

So the service calls its own public URL on a timer. The request goes out to
the internet and back in through Render's router, which is what makes it count
as traffic -- a request to localhost would never leave the instance and would
keep nothing awake.

Two things this deliberately does not pretend to do:

* **It cannot wake a sleeping instance.** The timer sleeps with the process.
  This prevents the instance from going idle; it cannot rescue one that
  already has. If the service is stopped by a deploy, a crash, or a spell with
  no traffic before this loop's first tick, only an outside request starts it.
* **It is not a substitute for a paid plan.** A paid instance never idles, so
  `KEEPALIVE_SECONDS=0` turns this off and it should be off there. Keeping a
  free instance awake around the clock also consumes free instance hours,
  which are finite and shared across every free service in the account.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import settings

log = logging.getLogger(__name__)

#: Generous: the point is to prove the instance is up, and a slow reply still
#: proves it. Shorter than the interval, so a hung request cannot stack up.
TIMEOUT_SECONDS = 30


def target() -> str:
    """The URL to call, or an empty string when there is nothing to call.

    `/health` and not `/`: it is the cheapest route in the service, it touches
    no database, and Render is already calling it for its own health checks.
    """
    base = settings.public_base_url.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return ""
    return f"{base}/health"


def should_run() -> bool:
    """Whether this deployment wants a keepalive at all.

    Off without a public URL, because there is then nothing meaningful to
    call: a laptop has no router to come back in through. Off when the
    interval is zero, which is how a paid plan says it does not need this.
    """
    return settings.keepalive_seconds > 0 and bool(target())


async def ping(client: httpx.AsyncClient) -> bool:
    """One request. Never raises: a failed ping is not worth a traceback."""
    url = target()
    try:
        response = await client.get(url, timeout=TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        log.warning("keepalive: could not reach %s (%s)", url, exc)
        return False
    if response.status_code != 200:
        log.warning("keepalive: %s answered %s", url, response.status_code)
        return False
    return True


async def run_forever() -> None:
    """Call our own health endpoint, for as long as the service runs."""
    if not should_run():
        log.info(
            "keepalive is off (set KEEPALIVE_SECONDS and PUBLIC_BASE_URL to "
            "enable it; it is only needed on a plan that sleeps)"
        )
        return

    every = settings.keepalive_seconds
    log.info("keepalive: calling %s every %d seconds", target(), every)

    async with httpx.AsyncClient() as client:
        while True:
            # Sleeping first means a restart does not add a request to the one
            # that just started the process.
            await asyncio.sleep(every)
            await ping(client)
