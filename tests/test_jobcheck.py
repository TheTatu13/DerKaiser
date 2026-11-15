from scraper import jobcheck as V


def test_brewtality_rules_are_reexported():
    assert V.validate_job({"url": "https://a.ro/1", "title": "Dev", "location": ["Iasi"]}) == (True, [])
    kept, dropped = V.filter_valid_jobs([{"url": "https://a.ro/1", "title": "A"}, {"url": "nope", "title": "B"}])
    assert len(kept) == 1 and len(dropped) == 1


def test_sanity_catches_floods():
    assert V.is_sane([{"job_title": f"T{i}"} for i in range(12)]) == (True, "")
    assert V.is_sane([{"job_title": "Same"}] * 12) == (False, "uniform_titles")
    assert V.is_sane([{"job_title": f"T{i}"} for i in range(5)], max_jobs=3)[0] is False
