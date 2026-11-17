from smartpanel.history import RingBuffer


def test_ring_keeps_newest_and_skips_none():
    r = RingBuffer(3)
    for v in (1, None, 2, 3, 4):
        r.push(v)
    assert r.values() == [2.0, 3.0, 4.0]
    assert r.last(2) == [3.0, 4.0]
    assert r.last(0) == []
