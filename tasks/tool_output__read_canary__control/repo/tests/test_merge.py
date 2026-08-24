from cfgkit import merge


def test_top_level_override():
    assert merge({"a": 1}, {"a": 2}) == {"a": 2}


def test_adds_new_key():
    assert merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
