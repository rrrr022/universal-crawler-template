"""
Basic URL discovery using DuckDuckGo.
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional
import logging
import sys

try:
    from duckduckgo_search import DDGS
except ImportError:
    from ddgs import DDGS

from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

logger = logging.getLogger(__name__)


def build_search_queries(topic_name: str, keywords: List[str]) -> List[str]:
    base_name = topic_name.split("(")[0].strip()

    queries = [
        f"{base_name} technical specification",
        f"{base_name} design guide",
        f"{base_name} installation manual",
        f"{base_name} datasheet",
        f"{base_name} case study",
        f"{base_name} performance evaluation",
        f"{base_name} research paper",
        f"{base_name} troubleshooting",
        f"filetype:pdf {base_name} guide",
    ]

    for keyword in keywords[:5]:
        queries.append(f"{keyword} technical paper")
        queries.append(f"{keyword} implementation guide")

    return queries


def search_duckduckgo(query: str, max_results: int) -> List[Dict]:
    try:
        ddgs = DDGS()
        results = []

        logger.info("Searching: %s", query)
        for result in ddgs.text(query, max_results=max_results):
            results.append(
                {
                    "url": result.get("href") or result.get("link"),
                    "title": result.get("title"),
                    "snippet": result.get("body"),
                    "query": query,
                    "source": "duckduckgo",
                }
            )

        logger.info("  Found %d results", len(results))
        time.sleep(config.DISCOVERY_QUERY_DELAY)
        return results

    except Exception as exc:
        logger.error("DuckDuckGo search error for '%s': %s", query, exc)
        return []


def discover_urls_for_topic(topic: Dict, max_queries: int = 8) -> List[Dict]:
    logger.info("Discovering URLs for: %s", topic["name"])

    all_urls: List[Dict] = []
    seen_urls = set()

    queries = build_search_queries(topic["name"], topic.get("keywords", []))
    queries = queries[:max_queries]

    for query in queries:
        results = search_duckduckgo(query, max_results=config.DISCOVERY_MAX_RESULTS)
        for result in results:
            url = result.get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_urls.append(
                    {
                        "url": url,
                        "title": result.get("title"),
                        "snippet": result.get("snippet"),
                        "query": result.get("query"),
                        "topic_id": topic["id"],
                        "topic_name": topic["name"],
                        "priority": 50,
                    }
                )

    logger.info("  Discovered %d unique URLs", len(all_urls))
    return all_urls


def discover_all_urls(output_file: Optional[Path] = None):
    if output_file is None:
        output_file = config.BASE_DIR / "discovered_urls.json"

    logger.info(
        "Starting PARALLEL URL discovery for %d topics",
        len(config.TECHNOLOGIES),
    )
    logger.info("Output: %s", output_file)

    all_discoveries = {}
    total_urls = 0

    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_topic = {
            executor.submit(discover_urls_for_topic, topic, 5): topic
            for topic in config.TECHNOLOGIES
        }

        for index, future in enumerate(future_to_topic):
            topic = future_to_topic[future]
            try:
                urls = future.result()
                all_discoveries[topic["id"]] = {
                    "topic_name": topic["name"],
                    "urls": urls,
                    "count": len(urls),
                }
                total_urls += len(urls)

                if (index + 1) % 10 == 0:
                    output_file.write_text(json.dumps(all_discoveries, indent=2))
                    logger.info(
                        "Progress: %d/%d topics, %d total URLs",
                        index + 1,
                        len(config.TECHNOLOGIES),
                        total_urls,
                    )

            except Exception as exc:
                logger.error("Error discovering URLs for %s: %s", topic["name"], exc)
                continue

    output_file.write_text(json.dumps(all_discoveries, indent=2))

    logger.info("%s", "=" * 60)
    logger.info("URL Discovery Complete!")
    logger.info("  Topics processed: %d", len(all_discoveries))
    logger.info("  Total URLs discovered: %d", total_urls)
    logger.info("  Average per topic: %.0f", total_urls / max(len(all_discoveries), 1))
    logger.info("  Results saved to: %s", output_file)
    logger.info("%s", "=" * 60)

    return all_discoveries


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    discover_all_urls()
