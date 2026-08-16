"""Le token bucket doit réellement plafonner le débit."""

import asyncio
import time

import pytest

from mercari_sniper.ratelimit import TokenBucket


class TestTokenBucket:
    async def test_burst_up_to_capacity_is_immediate(self):
        bucket = TokenBucket(rate=10, capacity=5)
        started = time.monotonic()
        for _ in range(5):
            await bucket.acquire()
        assert time.monotonic() - started < 0.1

    async def test_throttles_beyond_capacity(self):
        bucket = TokenBucket(rate=20, capacity=2)
        started = time.monotonic()
        for _ in range(6):
            await bucket.acquire()
        # 2 immédiats + 4 à 20/s => ~0.2 s incompressibles.
        assert time.monotonic() - started >= 0.18

    async def test_concurrent_consumers_share_the_budget(self):
        bucket = TokenBucket(rate=20, capacity=1)
        started = time.monotonic()
        await asyncio.gather(*(bucket.acquire() for _ in range(5)))
        assert time.monotonic() - started >= 0.18

    async def test_refills_over_time(self):
        bucket = TokenBucket(rate=50, capacity=1)
        await bucket.acquire()
        await asyncio.sleep(0.1)
        started = time.monotonic()
        await bucket.acquire()
        assert time.monotonic() - started < 0.05

    def test_rejects_non_positive_rate(self):
        with pytest.raises(ValueError):
            TokenBucket(rate=0)
        with pytest.raises(ValueError):
            TokenBucket(rate=-1)

    async def test_set_rate_takes_effect(self):
        bucket = TokenBucket(rate=100, capacity=1)
        bucket.set_rate(10)
        assert bucket.rate == 10
        await bucket.acquire()
        started = time.monotonic()
        await bucket.acquire()
        assert time.monotonic() - started >= 0.08
