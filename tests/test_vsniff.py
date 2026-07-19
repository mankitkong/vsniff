import vsniff


def test_module_imports():
    assert hasattr(vsniff, "ChinaqSite")
    assert hasattr(vsniff, "build_filename")


def test_filter_from_none_returns_all():
    assert vsniff.filter_from([1, 2, 3], None) == [1, 2, 3]


def test_filter_from_is_inclusive():
    assert vsniff.filter_from([1, 2, 3, 4, 5], 3) == [3, 4, 5]


def test_filter_from_above_max_is_empty():
    assert vsniff.filter_from([1, 2, 3], 99) == []
