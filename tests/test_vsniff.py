import vsniff


def test_module_imports():
    assert hasattr(vsniff, "ChinaqSite")
    assert hasattr(vsniff, "build_filename")
