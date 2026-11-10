from vsniff import common


def test_encode_url_percent_encodes_the_path():
    got = common.encode_url("https://cdn.example.com/動畫/01.mp4/master.m3u8")
    assert got == ("https://cdn.example.com/%E5%8B%95%E7%95%AB/01.mp4/master.m3u8")


def test_fetch_text_percent_encodes_a_non_ascii_referer():
    # hkanime page URLs carry raw Chinese; HTTP headers can't, and the failed
    # request used to be swallowed, silently losing resolution/length/ad-strip.
    seen = {}

    class Resp:
        def text(self):
            return "#EXTM3U"

    class Request:
        def get(self, url, headers):
            seen.update(headers)
            return Resp()

    class Ctx:
        request = Request()

    assert common.fetch_text(Ctx(), "https://cdn/x.m3u8",
                             "https://www.hkanime.com/play/信蜂/304x0") == "#EXTM3U"
    assert seen["Referer"] == "https://www.hkanime.com/play/%E4%BF%A1%E8%9C%82/304x0"
