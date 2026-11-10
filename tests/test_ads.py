import pytest

from vsniff import ads


# Shapes below are trimmed from real chinaq playlists (Nov 2026).
def _playlist(*blocks, lead_disc=False):
    """Build a media playlist; each block is [(duration, uri), ...]."""
    out = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:8",
           "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-MEDIA-SEQUENCE:0"]
    for i, block in enumerate(blocks):
        if i or lead_disc:
            out.append("#EXT-X-DISCONTINUITY")
        for d, uri in block:
            out += [f"#EXTINF:{d},", uri]
    return "\n".join(out + ["#EXT-X-ENDLIST"]) + "\n"


def _main(n, prefix, durs=(3.16, 3.12, 3.36, 4.0, 2.52, 1.92)):
    return [(durs[i % len(durs)], f"{prefix}m{i}.ts") for i in range(n)]


DY_MAIN = "https://bf.modujx17.com/20261019/7cbiw8z4/3506kb/hls/"


DY_AD = [(3, "/20261102/8EzXUOn8/10134kb/hls/a1.ts"),
         (3, "/20261102/8EzXUOn8/10134kb/hls/a2.ts"),
         (5, "/20261102/8EzXUOn8/10134kb/hls/a3.ts"),
         (0.64, "/20261102/8EzXUOn8/10134kb/hls/a4.ts")]


DY_URL = "https://play.modujx17.com/20261019/7cbiw8z4/3506kb/hls/index.m3u8"


def test_strip_ads_drops_blocks_from_a_foreign_directory():
    text = _playlist(_main(60, DY_MAIN), DY_AD, _main(60, DY_MAIN), DY_AD,
                     _main(60, DY_MAIN))
    clean, removed, n = ads.strip_ads(text, DY_URL)
    assert n == 2 and removed == pytest.approx(2 * 11.64)
    assert "8EzXUOn8" not in clean
    assert clean.count(DY_MAIN) == 180
    assert clean.count("#EXT-X-DISCONTINUITY") == 2  # between kept blocks only
    assert clean.rstrip().endswith("#EXT-X-ENDLIST")


UY_URL = "https://cdn.yzzy31-play.com/20261102/28527_b6e8c545/3000k/hls/mixed.m3u8"


UY_AD = [(6.633333, "ad1.ts"), (3.333333, "ad2.ts"), (4.8, "ad3.ts"),
         (1.7, "ad4.ts")]  # 30fps clip inside a 25fps episode, same directory


UY_MAIN = (6.72, 2.4, 4.0, 3.08, 5.88, 5.04)


def test_strip_ads_drops_blocks_at_a_different_frame_rate():
    text = _playlist(_main(60, "", UY_MAIN), UY_AD, _main(60, "", UY_MAIN),
                     [(5.933333, "t1.ts"), (3.333333, "t2.ts"), (0.3, "t3.ts")],
                     lead_disc=True)
    clean, removed, n = ads.strip_ads(text, UY_URL)
    assert n == 2
    assert "ad1.ts" not in clean and "t1.ts" not in clean
    # relative segment names become absolute so a local copy still resolves
    assert ("https://cdn.yzzy31-play.com/20261102/28527_b6e8c545/3000k/hls/m0.ts"
            in clean)


def test_strip_ads_ignores_scene_cut_discontinuities():
    # Some sources split every scene into its own block: same directory, same
    # 2s cadence, many tiny blocks. None of them are ads.
    d = "https://yyv13.qwe132456.cc/yyv13/202610/19/x/video/2000k_1080/hls/"
    blocks = [[(2.0, f"{d}s{b}_{i}.ts") for i in range(1 + b % 7)]
              for b in range(80)]
    assert ads.strip_ads(_playlist(*blocks), d + "index.m3u8") is None


def test_strip_ads_leaves_a_clean_playlist_alone():
    assert ads.strip_ads(_playlist(_main(100, DY_MAIN)), DY_URL) is None


def test_strip_ads_refuses_to_strip_too_much():
    # a "foreign" block that would be a large share of the video is not an ad
    text = _playlist(_main(10, DY_MAIN), DY_AD * 3)
    assert ads.strip_ads(text, DY_URL) is None


def test_strip_ads_keeps_the_encryption_key_of_a_dropped_preroll():
    key = '#EXT-X-KEY:METHOD=AES-128,URI="key.key"'
    text = _playlist(DY_AD, _main(60, DY_MAIN)).replace(
        "#EXTINF:3,\n/20261102/8EzXUOn8/10134kb/hls/a1.ts",
        key + "\n#EXTINF:3,\n/20261102/8EzXUOn8/10134kb/hls/a1.ts")
    clean, _removed, n = ads.strip_ads(text, DY_URL)
    assert n == 1
    assert ('#EXT-X-KEY:METHOD=AES-128,URI="https://play.modujx17.com/'
            '20261019/7cbiw8z4/3506kb/hls/key.key"') in clean
