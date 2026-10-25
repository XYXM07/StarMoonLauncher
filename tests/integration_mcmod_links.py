"""Opt-in public MCMOD search/link probe. No model credentials, JARs or game installs."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from launcher.mcmod_links import MCModLinks
from launcher.providers import ProviderHub


def main():
    source = MCModLinks(ProviderHub())
    query = "地毯"
    articles = source.search(query)
    assert any(item["id"] == "2361" for item in articles), "Carpet article was not found in MCMOD search"
    links = source.detail("2361")
    assert any(item["provider"] == "github" and item["project"] == "gnembon/fabric-carpet" for item in links)
    assert any(item["provider"] == "modrinth" and item["project"] == "carpet" for item in links)
    result = {"query": query, "articles": len(articles), "source": "https://www.mcmod.cn/class/2361.html", "links": links}
    output = ROOT / "test-results/mcmod-links-network.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("MCMOD_PUBLIC_SEARCH_AND_RELATED_LINKS_OK", len(links))


if __name__ == "__main__":
    main()
