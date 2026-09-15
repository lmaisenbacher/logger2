# -*- coding: utf-8 -*-
"""Client-side buffering of InfluxDB writes over the SYNCHRONOUS write
api — the `write_mode = batching` transport of logger2.

Replaces influxdb-client's batching WriteApi (`WriteType.batching`):
its RxPY operator `window_with_time_or_count` (reactivex 5.1.0) closes
a window from its timer thread without a lock, so every record pushed
while the old window completes is silently discarded — neither the
error nor the retry callback fires (RxPY issue 694, fixed upstream on
2026-07-28 but in no released version as of 2026-09-15). Measured
against the lab database: whole `write()` calls lost at timer-closed
windows, most reliably at the first flush after the api is created,
with a SINGLE producer (ion-detection GUI, 2026-09-14/15; the
unitrap-pydase-apps servers carry the same class in
`unitrap_services.py`). Re-enabling the library's mode needs
reactivex >= 5.1.1 pinned in the shared venv, and even then the fix
turns the drop into producer backpressure at window boundaries.
"""

import math
import threading
import time


class BufferedWriter:
    """Queue records in memory; an own daemon thread posts the queue
    every `flush_interval_ms` through `write_api`, the library's
    synchronous api, in requests of at most `max_request` records (no
    library retries — a request is bounded by the client's timeout), so
    the caller never waits on the database.

    A failed request is reported through `on_error(exception)` on the
    drain thread. A request the database REJECTED (a 4xx status below
    429: malformed line, field type conflict, bad token — see
    `is_rejected`) is dropped and counted, since retrying cannot help
    and it would block every record behind it; anything else
    (connection errors, timeouts, 429, 5xx) goes back to the head of
    the queue and the next attempt waits `retry_backoff_s` (a dead host
    costs one bounded request per backoff, not one per flush). The
    queue is capped at `max_pending` records — the OLDEST are dropped
    beyond it (and counted in `n_dropped`), so an unreachable database
    bounds memory, not the process. `close()` drains what is pending,
    bounded by `close_wait_ms`, and stops the thread; it does not close
    `write_api`. `n_written` counts the records the database accepted.
    """

    def __init__(self, write_api, bucket, org, flush_interval_ms,
                 close_wait_ms, on_error, max_pending=20_000,
                 retry_backoff_s=5.0, max_request=5_000,
                 name='influxdb-writer'):
        self._api = write_api
        self._bucket = bucket
        self._org = org
        self._flush_interval_s = flush_interval_ms / 1000
        self._close_wait_s = close_wait_ms / 1000
        self._on_error = on_error
        self._max_pending = max_pending
        self._retry_backoff_s = retry_backoff_s
        self._max_request = max_request
        self._pending = []
        self.n_written = 0
        self.n_dropped = 0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closing = False
        self._retry_after = -math.inf
        self._thread = threading.Thread(target=self._drain, name=name,
                                        daemon=True)
        self._thread.start()

    def write(self, records):
        """Queue `records` (point dicts) for the next flush; returns at
        once and never raises for a database problem."""
        with self._lock:
            self._pending.extend(records)
            self._trim_locked()

    def close(self):
        """Drain the queue (bounded by the close wait) and stop the
        thread. Idempotent."""
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._closing = True
        self._wake.set()
        thread.join(self._close_wait_s)

    def _trim_locked(self):
        excess = len(self._pending) - self._max_pending
        if excess > 0:
            del self._pending[:excess]
            self.n_dropped += excess

    def _drain(self):
        while True:
            self._wake.wait(self._flush_interval_s)
            self._wake.clear()
            closing = self._closing
            try:
                if closing or time.monotonic() >= self._retry_after:
                    self._flush(final=closing)
            except Exception as exc:
                # Nothing above should raise; the thread must outlive
                # whatever did
                self._report(exc)
            if closing:
                return

    def _report(self, exc):
        try:
            self._on_error(exc)
        except Exception:
            pass

    @staticmethod
    def is_rejected(exc):
        """True for a request the database REJECTED (a 4xx status below
        429) — retrying cannot help. Duck-typed on the library's
        `ApiException.status`; the library's own retry rule likewise
        retries only 429 and 5xx."""
        status = getattr(exc, 'status', None)
        return isinstance(status, int) and 400 <= status < 429

    def _flush(self, final):
        """Post the queue in requests of `max_request` records until it
        is empty or a request fails."""
        while True:
            with self._lock:
                batch = self._pending[:self._max_request]
                del self._pending[:len(batch)]
            if not batch:
                return
            try:
                self._api.write(self._bucket, self._org, batch)
            except Exception as exc:
                self._report(exc)
                with self._lock:
                    if final or self.is_rejected(exc):
                        self.n_dropped += len(batch)
                    else:
                        # Back at the head: the records stay in time
                        # order
                        self._pending[:0] = batch
                        self._trim_locked()
                self._retry_after = time.monotonic() + self._retry_backoff_s
                return
            with self._lock:
                self.n_written += len(batch)
