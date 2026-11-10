from vsniff import common


def test_encode_url_percent_encodes_the_path():
    got = common.encode_url("https://cdn.example.com/動畫/01.mp4/master.m3u8")
    assert got == ("https://cdn.example.com/%E5%8B%95%E7%95%AB/01.mp4/master.m3u8")
