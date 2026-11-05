from moni.auth.throttle import LoginThrottle


def test_throttle():
    t = LoginThrottle(max_failures=3, window=900)
    for _ in range(3):
        assert t.retry_after("1.1.1.1", 0) is None
        t.fail("1.1.1.1", 0)
    assert 1 <= t.retry_after("1.1.1.1", 100) <= 800 and t.retry_after("2.2.2.2", 100) is None
    assert t.retry_after("1.1.1.1", 900) is None


def test_throttle_sliding_window():
    t = LoginThrottle(max_failures=2, window=100)
    t.fail("ip", 0)
    t.fail("ip", 50)
    assert t.retry_after("ip", 60) == 40  # erst wenn der Fehlversuch von t=0 abläuft
    assert t.retry_after("ip", 100) is None


def test_throttle_bounded():
    t = LoginThrottle(max_failures=3, window=900)
    for i in range(10_001):
        t.fail(f"10.0.{i // 256}.{i % 256}", 0)
    assert len(t._hits) <= 10_000
    assert "10.0.0.0" not in t._hits and "10.0.39.16" in t._hits  # älteste zuerst verworfen


def test_throttle_overflow_drops_expired_first():
    t = LoginThrottle(max_failures=3, window=900)
    for i in range(10_000):
        t.fail(f"ip{i}", 0)
    t.fail("neu", 1000)
    assert list(t._hits) == ["neu"]
