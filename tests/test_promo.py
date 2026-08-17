from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from star_digest.collector import clip_readme, strip_promo


def test_strip_apilayer_ad():
    raw = """
# APILayer Unified Suite in now Live!

[APILayer unified suite](https://apilayer.com/?utm_source=Github) allows you to integrate APIs.

[Sign up](https://app.apilayer.com?utm_source=Github) and start building today!

# Try Public APIs for free
The Public APIs repository is manually curated by community members like you and folks working at [APILayer](https://apilayer.com/?utm_source=Github).
It includes an extensive list of public APIs from many domains.
"""
    cleaned = clip_readme(raw)
    assert "APILayer Unified Suite" not in cleaned
    assert "utm_source" not in cleaned
    assert "manually curated" in cleaned
    assert "extensive list of public APIs" in cleaned


if __name__ == "__main__":
    test_strip_apilayer_ad()
    print("ok")
