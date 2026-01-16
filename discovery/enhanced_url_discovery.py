"""
Enhanced URL Discovery - multi-source search (DuckDuckGo, optional Bing/Scholar).
Generic, topic-driven discovery.
"""

import asyncio
import aiohttp
import json
from pathlib import Path
from typing import Dict, List, Set, Optional
from datetime import datetime
import logging
import re
import sys
from urllib.parse import quote_plus

try:
    from duckduckgo_search import DDGS
except ImportError:  # duckduckgo-search rename fallback
    from ddgs import DDGS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class EnhancedURLDiscovery:
    def __init__(self):
        self.discovered_urls: Dict[str, Dict] = {}
        self.output_file = config.BASE_DIR / "discovered_urls_enhanced.json"
        self.session: Optional[aiohttp.ClientSession] = None
        self.enable_bing = config.DISCOVERY_ENABLE_BING
        self.enable_scholar = config.DISCOVERY_ENABLE_SCHOLAR
        self.priority_domains = config.PRIORITY_DOMAINS
        self.exclude_domains = config.EXCLUDE_DOMAINS

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def create_search_queries(self, tech: Dict) -> List[str]:
        name = tech["name"]
        keywords = tech.get("keywords", [])
        vendors = tech.get("vendors", [])

        queries = [
            f"{name} technical specification",
            f"{name} design guide",
            f"{name} installation manual",
            f"{name} datasheet",
            f"{name} case study",
            f"{name} performance evaluation",
            f"{name} research paper",
            f"{name} troubleshooting",
        ]

        for keyword in keywords[:3]:
            queries.append(f"{keyword} technical paper")
            queries.append(f"{keyword} implementation guide")

        for vendor in vendors[:2]:
            queries.append(f"{name} site:{vendor}")

        return queries[: config.DISCOVERY_QUERIES_PER_TOPIC]

    async def search_duckduckgo(self, query: str, max_results: int) -> Set[str]:
        urls: Set[str] = set()
        try:
            with DDGS() as ddgs:
                results = ddgs.text(query, max_results=max_results)
                for result in results:
                    url = result.get("href") or result.get("link")
                    if url and self.is_relevant_url(url):
                        urls.add(url)
            await asyncio.sleep(config.DISCOVERY_QUERY_DELAY)
        except Exception as exc:
            logger.warning("DuckDuckGo search failed for '%s': %s", query, exc)
        return urls

    async def search_bing(self, query: str, max_results: int) -> Set[str]:
        urls: Set[str] = set()
        try:
            if not self.session:
                return urls
            search_url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results}"
            headers = {"User-Agent": config.USER_AGENT}
            async with self.session.get(search_url, headers=headers, timeout=15) as response:
                if response.status == 200:
                    html = await response.text()
                    url_pattern = r'<a href="(https?://[^"]+)"'
                    for url in re.findall(url_pattern, html):
                        if self.is_relevant_url(url):
                            urls.add(url)
            await asyncio.sleep(config.DISCOVERY_QUERY_DELAY)
        except Exception as exc:
            logger.warning("Bing search failed for '%s': %s", query, exc)
        return urls

    async def search_scholar_proxy(self, query: str) -> Set[str]:
        urls: Set[str] = set()
        try:
            if not self.session:
                return urls
            search_url = f"https://scholar.google.com/scholar?q={quote_plus(query)}&hl=en&num=10"
            headers = {"User-Agent": config.USER_AGENT}
            async with self.session.get(search_url, headers=headers, timeout=15) as response:
                if response.status == 200:
                    html = await response.text()
                    url_pattern = r'<a href="(https?://[^"]+\.(?:pdf|htm|html)[^"]*)"'
                    for url in re.findall(url_pattern, html):
                        if self.is_relevant_url(url):
                            urls.add(url)
            await asyncio.sleep(max(3, config.DISCOVERY_QUERY_DELAY))
        except Exception as exc:
            logger.warning("Scholar search failed for '%s': %s", query, exc)
        return urls

    def is_relevant_url(self, url: str) -> bool:
        url_lower = url.lower()

        if any(domain in url_lower for domain in self.exclude_domains):
            return False

        exclude_patterns = [
            "/login",
            "/signin",
            "/register",
            "/cart",
            "/checkout",
            ".css",
            ".js",
            ".jpg",
            ".png",
            ".gif",
            ".svg",
            ".ico",
            "ad.doubleclick",
            "googleads",
            "facebook.net",
        ]
        if any(pattern in url_lower for pattern in exclude_patterns):
            return False

        prefer_patterns = [
            ".pdf",
            "technical",
            "engineering",
            "design",
            "specification",
            "manual",
            "guide",
            "datasheet",
            "journal",
            "research",
            "article",
            "paper",
            "study",
        ]

        is_priority = any(domain in url_lower for domain in self.priority_domains)
        has_preferred = any(pattern in url_lower for pattern in prefer_patterns)

        return is_priority or has_preferred or len(url) < 200

    async def discover_urls_for_tech(self, tech: Dict) -> Dict:
        tech_id = tech["id"]
        tech_name = tech["name"]
        logger.info("[Topic %03d] Starting discovery: %s", tech_id, tech_name)

        all_urls: Set[str] = set()
        queries = self.create_search_queries(tech)

        for query in queries:
            ddg_urls = await self.search_duckduckgo(
                query, max_results=config.DISCOVERY_MAX_RESULTS
            )
            all_urls.update(ddg_urls)

            if self.enable_bing:
                bing_urls = await self.search_bing(query, max_results=10)
                all_urls.update(bing_urls)

            if self.enable_scholar and len(all_urls) < 15:
                scholar_urls = await self.search_scholar_proxy(query)
                all_urls.update(scholar_urls)

            if len(all_urls) >= 30:
                break

        sorted_urls = sorted(
            all_urls,
            key=lambda url: (
                -1 if any(d in url.lower() for d in self.priority_domains) else 0,
                url,
            ),
        )

        result = {
            "tech_id": tech_id,
            "tech_name": tech_name,
            "category": tech.get("category", ""),
            "count": len(sorted_urls),
            "urls": sorted_urls,
            "discovered_at": datetime.now().isoformat(),
        }

        logger.info("[Topic %03d] Found %d URLs", tech_id, len(sorted_urls))
        return result

    async def discover_all(self) -> None:
        logger.info(
            "Starting enhanced URL discovery for %d topics",
            len(config.TECHNOLOGIES),
        )
        logger.info(
            "Concurrency: %d parallel searches",
            config.DISCOVERY_CONCURRENCY,
        )

        semaphore = asyncio.Semaphore(config.DISCOVERY_CONCURRENCY)

        async def discover_with_semaphore(tech):
            async with semaphore:
                return await self.discover_urls_for_tech(tech)

        tasks = [discover_with_semaphore(tech) for tech in config.TECHNOLOGIES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error("Task failed: %s", result)
            elif result:
                self.discovered_urls[str(result["tech_id"])] = result

        self.save_results()

        total_urls = sum(r["count"] for r in self.discovered_urls.values())
        avg_urls = total_urls / len(self.discovered_urls) if self.discovered_urls else 0

        logger.info("\n%s", "=" * 60)
        logger.info("ENHANCED DISCOVERY COMPLETED")
        logger.info("%s", "=" * 60)
        logger.info("Topics: %d / %d", len(self.discovered_urls), len(config.TECHNOLOGIES))
        logger.info("Total URLs: %d", total_urls)
        logger.info("Average per topic: %.1f", avg_urls)
        logger.info("Output: %s", self.output_file)

    def save_results(self) -> None:
        with self.output_file.open("w", encoding="utf-8") as handle:
            json.dump(self.discovered_urls, handle, indent=2, ensure_ascii=False)
        logger.info(
            "Saved %d topic results to %s",
            len(self.discovered_urls),
            self.output_file,
        )


async def main():
    async with EnhancedURLDiscovery() as discovery:
        await discovery.discover_all()


if __name__ == "__main__":
    asyncio.run(main())
