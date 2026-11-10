from vsniff import sites


def test_supports_batch_flags():
    assert sites.ChinaqSite().supports_batch is True
    assert sites.HKAnimeSite().supports_batch is True
    assert sites.DramasqSite().supports_batch is True
    assert sites.GenericSite().supports_batch is False


def test_every_site_is_routed_and_generic_is_the_catch_all():
    assert [a.name for a in sites.ADAPTERS][-1] == "generic"
    assert sites.pick_adapter("https://chinaq.net/video/1-1.html").name == "chinaq"
    assert sites.pick_adapter("https://www.hkanime.com/play/x/1x0").name == "hkanime"
    assert sites.pick_adapter("https://example.com/watch/1").name == "generic"
