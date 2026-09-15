# -*- coding: utf-8 -*-
"""Tests for `db_writer.BufferedWriter` over a fake synchronous write
api — no database is contacted. Runs under pytest.
"""

import threading
import time

import pytest

from db_writer import BufferedWriter


class FakeApi:
    """The library's synchronous write api: records every write call,
    fails the next `fail_next` calls."""

    def __init__(self):
        self.writes = []
        self.fail_next = 0
        self.lock = threading.Lock()

    def write(self, bucket, org, record):
        with self.lock:
            if self.fail_next:
                self.fail_next -= 1
                raise ConnectionError('write refused')
            self.writes.append(list(record))


def _rec(i):
    return {'measurement': 'm', 'tags': {'sensor': 's'},
            'fields': {'v': i}, 'time': i}


def _wait(predicate, timeout=2.0):
    t0 = time.monotonic()
    while not predicate():
        if time.monotonic() - t0 > timeout:
            raise AssertionError('timed out')
        time.sleep(0.005)


@pytest.fixture
def writer():
    """A BufferedWriter flushing every 20 ms, 100 ms retry backoff."""
    api = FakeApi()
    errors = []
    w = BufferedWriter(api, 'b', 'o', flush_interval_ms=20,
                       close_wait_ms=2_000, on_error=errors.append,
                       retry_backoff_s=0.1)
    w.api = api
    w.errors = errors
    yield w
    w.close()


def test_queued_records_go_out_as_one_request(writer):
    writer.write([_rec(1), _rec(2)])
    writer.write([_rec(3)])
    _wait(lambda: writer.api.writes)
    assert writer.api.writes == [[_rec(1), _rec(2), _rec(3)]]
    assert writer.errors == []
    assert writer.n_written == 3 and writer.n_dropped == 0


def test_failed_request_is_reported_and_retried_in_order(writer):
    writer.api.fail_next = 1
    writer.write([_rec(1)])
    _wait(lambda: writer.errors)
    assert 'write refused' in str(writer.errors[0])
    writer.write([_rec(2)])
    _wait(lambda: writer.api.writes)
    assert writer.api.writes == [[_rec(1), _rec(2)]]
    assert len(writer.errors) == 1
    assert writer.n_written == 2


def test_backoff_holds_the_retry(writer):
    writer._retry_backoff_s = 0.3
    writer.api.fail_next = 1
    writer.write([_rec(1)])
    _wait(lambda: writer.errors)
    t_fail = time.monotonic()
    _wait(lambda: writer.api.writes, timeout=3.0)
    assert time.monotonic() - t_fail >= 0.2


def test_cap_drops_the_oldest_and_counts():
    api = FakeApi()
    errors = []
    w = BufferedWriter(api, 'b', 'o', flush_interval_ms=10_000,
                       close_wait_ms=2_000, on_error=errors.append,
                       max_pending=3)
    try:
        time.sleep(0.05)              # the thread is in its long wait
        w.write([_rec(1), _rec(2), _rec(3), _rec(4), _rec(5)])
        assert w.n_dropped == 2
        api.fail_next = 1
    finally:
        w.close()
    # The close's final flush failed: those records are given up on
    assert api.writes == []
    assert w.n_dropped == 5 and w.n_written == 0
    assert len(errors) == 1


def test_close_drains_pending_records():
    api = FakeApi()
    w = BufferedWriter(api, 'b', 'o', flush_interval_ms=10_000,
                       close_wait_ms=2_000, on_error=lambda e: None)
    time.sleep(0.05)
    w.write([_rec(1)])
    t0 = time.monotonic()
    w.close()
    assert time.monotonic() - t0 < 2.0
    assert api.writes == [[_rec(1)]]
    w.close()                         # idempotent


def test_survives_a_raising_error_callback():
    api = FakeApi()

    def bad_on_error(exc):
        raise RuntimeError('callback broken')

    w = BufferedWriter(api, 'b', 'o', flush_interval_ms=20,
                       close_wait_ms=2_000, on_error=bad_on_error,
                       retry_backoff_s=0.05)
    try:
        api.fail_next = 1
        w.write([_rec(1)])
        _wait(lambda: api.writes)
        assert api.writes == [[_rec(1)]]
        assert w._thread.is_alive()
    finally:
        w.close()


class RejectedError(Exception):
    """The library's ApiException shape: a `status` attribute."""

    def __init__(self, status):
        super().__init__(f'({status}) rejected')
        self.status = status


def test_rejected_request_is_dropped_not_retried(writer):
    api = writer.api
    original_write = api.write

    def rejecting_write(bucket, org, record):
        if any(r['fields']['v'] == 1 for r in record):
            raise RejectedError(400)
        return original_write(bucket, org, record)

    api.write = rejecting_write
    writer.write([_rec(1), _rec(2)])
    _wait(lambda: writer.errors)
    # The poison batch is given up on; what follows is not held behind
    # it (after the backoff)
    writer.write([_rec(3)])
    _wait(lambda: api.writes)
    assert api.writes == [[_rec(3)]]
    assert writer.n_dropped == 2 and writer.n_written == 1
    assert len(writer.errors) == 1


@pytest.mark.parametrize('status, rejected', [
    (400, True), (401, True), (422, True), (428, True),
    (429, False), (500, False), (503, False), (None, False)])
def test_rejection_rule(status, rejected):
    exc = RejectedError(status) if status is not None else OSError('x')
    assert BufferedWriter.is_rejected(exc) is rejected


def test_flushes_in_chunks():
    api = FakeApi()
    w = BufferedWriter(api, 'b', 'o', flush_interval_ms=10_000,
                       close_wait_ms=2_000, on_error=lambda e: None,
                       max_request=3)
    time.sleep(0.05)
    w.write([_rec(i) for i in range(7)])
    w.close()
    # One flush drains the backlog in consecutive bounded requests
    assert [len(x) for x in api.writes] == [3, 3, 1]
    assert [r['fields']['v'] for x in api.writes for r in x] == list(range(7))
    assert w.n_written == 7


def test_never_loses_records_under_load(writer):
    # The regression for the library's batching mode, which dropped
    # records at its window boundaries: every record written while
    # flushes run lands exactly once, in order
    n = 3000

    def produce():
        for i in range(n):
            writer.write([_rec(i)])
            if i % 7 == 0:
                time.sleep(0.0005)

    t = threading.Thread(target=produce)
    t.start()
    t.join()
    writer.close()
    flat = [r['fields']['v'] for batch in writer.api.writes for r in batch]
    assert flat == list(range(n))
    assert len(writer.api.writes) > 1
    assert writer.errors == []
