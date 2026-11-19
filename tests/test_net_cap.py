from scraper.net import Net


def _opener(size):
    def op(req, timeout):
        return 200, {}, b"x" * size
    return op


def test_default_cap_truncates_big_feeds_and_raised_cap_keeps_them():
    small = Net(opener=_opener(3_000_000), interval=0, respect_robots=False)
    assert len(small.get("https://a.test/feed")) == 2_000_000  # the silent cut that broke Lever boards
    big = Net(opener=_opener(3_000_000), interval=0, respect_robots=False, max_bytes=60_000_000)
    assert len(big.get("https://a.test/feed")) == 3_000_000
