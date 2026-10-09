/**
 * Keeping a screen honest while a call is still happening.
 *
 * A call row is written when the call starts and rewritten as it progresses —
 * ringing, answered, finished — by webhooks from the carrier and by the media
 * session ending. All of that happens on the server. A screen that loaded its
 * list once keeps showing "ringing" for a call that ended ten minutes ago, and
 * the only way to find out is to reload, which nobody thinks to do because the
 * page does not look stale.
 *
 * So: while anything on screen has not finished, ask again. When everything
 * has settled, stop — a list of last month's calls does not need polling, and
 * this is what keeps it from becoming a permanent background request.
 */
import { useEffect, useRef } from 'react';

/** Statuses that can still change by themselves. */
const UNSETTLED = new Set(['QUEUED', 'RINGING', 'IN_PROGRESS']);

export function isSettled(status: string): boolean {
  return !UNSETTLED.has(status);
}

/**
 * A recording is written after the call ends, so a settled call can still be
 * waiting for its audio. Worth another look, or the row says "waiting for
 * audio" until the page is reloaded.
 */
export function isWaitingForAudio(recordingState: string): boolean {
  return recordingState === 'PENDING';
}

export type Watchable = {
  status: string;
  recording?: { state: string };
};

/** Whether anything here could still change on its own. */
export function anythingInFlight(rows: readonly Watchable[] | null | undefined): boolean {
  return (rows ?? []).some(row =>
    !isSettled(row.status) || isWaitingForAudio(row.recording?.state ?? ''));
}

/**
 * Re-run `refresh` while `inFlight`, and stop when nothing is.
 *
 * `refresh` is held in a ref so a caller can pass a fresh closure each render
 * without restarting the timer — otherwise the interval is torn down and
 * rebuilt on every poll, which is how a 4-second poll quietly becomes a
 * request per render.
 */
export function useSettlingPoll(
  inFlight: boolean,
  refresh: () => void | Promise<unknown>,
  everyMs = 4000,
) {
  const latest = useRef(refresh);
  latest.current = refresh;

  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(() => {
      // A background tab is not watching, and waking a phone's radio every
      // four seconds to update a screen nobody is looking at is rude.
      if (document.visibilityState === 'hidden') return;
      void latest.current();
    }, everyMs);
    return () => clearInterval(timer);
  }, [inFlight, everyMs]);

  // Coming back to the tab is exactly when the screen is most likely to be
  // wrong, and the poll above has been paused the whole time.
  useEffect(() => {
    if (!inFlight) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') void latest.current();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [inFlight]);
}
