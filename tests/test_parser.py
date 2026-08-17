from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from star_digest.collector import parse_trending_fallback, parse_trending_html


def test_parse_trending_html():
    html = (Path(__file__).parent / "fixtures" / "trending.html").read_text(encoding="utf-8")
    items = parse_trending_html(html)
    assert [i["full_name"] for i in items] == ["cordiverse/cordis", "unslothai/unsloth"]
    assert items[0]["stars"] == 4935
    assert items[0]["stars_today"] == 720
    assert items[0]["language"] == "TypeScript"
    assert items[1]["language"] == "Python"


def test_parse_trending_fallback():
    text = """
[Star](/login)

## [cordiverse / cordis](/cordiverse/cordis)

Meta-Framework of Spatiotemporal Composability

 TypeScript [4,935](/cordiverse/cordis/stargazers) [270](/cordiverse/cordis/forks) 720 stars today

[Star](/login)

## [basecamp / omarchy](/basecamp/omarchy)

Beautiful Linux

 Shell [25,485](/basecamp/omarchy/stargazers) [2,597](/basecamp/omarchy/forks) 270 stars today
"""
    items = parse_trending_fallback(text)
    assert len(items) == 2
    assert items[0]["full_name"] == "cordiverse/cordis"
    assert items[0]["stars_today"] == 720
    assert items[1]["name"] == "omarchy"


if __name__ == "__main__":
    test_parse_trending_html()
    test_parse_trending_fallback()
    print("ok")
