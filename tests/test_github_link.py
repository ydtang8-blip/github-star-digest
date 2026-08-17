from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from star_digest.github_link import build_catalog_markdown, public_git_url


def test_catalog_contains_fork_and_source():
    md = build_catalog_markdown(
        "yangjie",
        [
            {
                "full_name": "vercel-labs/eve-software-factory-template",
                "fork_full_name": "yangjie/eve-software-factory-template",
                "language": "TypeScript",
                "stars": 838,
                "status": "downloaded",
                "summary_zh": "软件工厂模板",
            }
        ],
        "github-star-picks",
        "https://github.com/yangjie/github-star-digest",
    )
    assert "yangjie 的高星精选" in md
    assert "vercel-labs/eve-software-factory-template" in md
    assert "yangjie/eve-software-factory-template" in md
    assert public_git_url("yangjie/github-star-picks") == "https://github.com/yangjie/github-star-picks.git"


def test_empty_catalog():
    md = build_catalog_markdown("yangjie", [], "github-star-picks")
    assert "还没有精选项目" in md


if __name__ == "__main__":
    test_catalog_contains_fork_and_source()
    test_empty_catalog()
    print("ok")
