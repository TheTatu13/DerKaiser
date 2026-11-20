"""Resume of the bridge must survive a docs.jsonl cut in the middle of a line."""
from scraper.bridge import _done_ids


def test_done_ids_skips_cut_and_garbage_lines():
    lines = ['{"id": "1"}\n', "\n", '{"id": "2", "web', 'garbage\n', '{"nope": 1}\n', '{"id": "3"}\n']
    assert _done_ids(lines) == {"1", "3"}
