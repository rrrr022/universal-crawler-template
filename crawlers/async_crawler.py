"""
High-performance async crawler using aiohttp.
"""
import asyncio
import aiohttp
import json
import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import logging
from datetime import datetime
from typing import Dict, List, Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

log_handlers = [logging.StreamHandler()]
try:
    config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_handlers.append(logging.FileHandler(config.LOG_FILE))
except Exception:
    pass

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=log_handlers,
)
logger = logging.getLogger(__name__)


class SmartCache:
    """Content-addressable cache to avoid duplicate downloads."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.url_to_hash: Dict[str, str] = {}
        self.hits = 0
        self.misses = 0

    def _get_cache_path(self, content_hash: str) -> Path:
        return self.cache_dir / content_hash[:2] / content_hash[2:4] / content_hash

    def _hash_content(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get(self, url: str) -> Optional[str]:
        if url in self.url_to_hash:
            content_hash = self.url_to_hash[url]
            cache_path = self._get_cache_path(content_hash)
            if cache_path.exists():
                self.hits += 1
                logger.debug("Cache HIT: %s", url)
                return cache_path.read_text(encoding="utf-8", errors="ignore")
        self.misses += 1
        return None

    def put(self, url: str, content: str) -> str:
        content_hash = self._hash_content(content)
        cache_path = self._get_cache_path(content_hash)

        if not cache_path.exists():
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(content, encoding="utf-8")
            logger.debug("Cached: %s -> %s", url, content_hash[:8])
        else:
            logger.debug("Content already cached (duplicate): %s", content_hash[:8])

        self.url_to_hash[url] = content_hash
        return content_hash

    def stats(self) -> Dict[str, float]:
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "total_requests": total,
            "hit_rate_percent": hit_rate,
        }


class AsyncCrawler:
    """High-performance async web crawler."""

    def __init__(self):
        self.cache = SmartCache(config.CACHE_DIR)
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore = asyncio.Semaphore(config.CONCURRENCY)
        self.domain_delays: Dict[str, float] = {}

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(limit=config.CONCURRENCY, limit_per_host=10)
        timeout = aiohttp.ClientTimeout(total=config.TIMEOUT)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"User-Agent": config.USER_AGENT},
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _rate_limit(self, domain: str):
        now = time.time()
        if domain in self.domain_delays:
            elapsed = now - self.domain_delays[domain]
            if elapsed < config.RATE_LIMIT:
                await asyncio.sleep(config.RATE_LIMIT - elapsed)
        self.domain_delays[domain] = time.time()

    async def fetch_url(self, url: str, topic_name: str) -> Optional[Dict]:
        cached = self.cache.get(url)
        if cached:
            return {
                "url": url,
                "content": cached,
                "topic_name": topic_name,
                "timestamp": datetime.now().isoformat(),
                "from_cache": True,
            }

        async with self.semaphore:
            domain = urlparse(url).netloc
            await self._rate_limit(domain)

            for attempt in range(config.MAX_RETRIES):
                try:
                    async with self.session.get(url, allow_redirects=True) as response:
                        if response.status == 200:
                            content = await response.text()
                            content_hash = self.cache.put(url, content)

                            logger.info("[OK] Fetched: %s (%d bytes)", url, len(content))

                            return {
                                "url": url,
                                "final_url": str(response.url),
                                "content": content,
                                "content_hash": content_hash,
                                "topic_name": topic_name,
                                "timestamp": datetime.now().isoformat(),
                                "status_code": response.status,
                                "from_cache": False,
                            }

                        logger.warning("[FAIL] HTTP %s: %s", response.status, url)

                except asyncio.TimeoutError:
                    logger.warning(
                        "[TIMEOUT] Attempt %d/%d: %s",
                        attempt + 1,
                        config.MAX_RETRIES,
                        url,
                    )
                    if attempt < config.MAX_RETRIES - 1:
                        await asyncio.sleep(config.BACKOFF_FACTOR**attempt)

                except Exception as exc:
                    logger.error("[ERROR] Fetching %s: %s", url, exc)
                    if attempt < config.MAX_RETRIES - 1:
                        await asyncio.sleep(config.BACKOFF_FACTOR**attempt)

        return None

    async def crawl_batch(self, urls: List[Dict]) -> List[Dict]:
        logger.info("Starting batch crawl: %d URLs", len(urls))
        tasks = [self.fetch_url(url_data["url"], url_data["topic_name"]) for url_data in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid_results = [r for r in results if r is not None and not isinstance(r, Exception)]
        logger.info("Completed batch: %d/%d successful", len(valid_results), len(urls))
        return valid_results

    def extract_links(self, html: str, base_url: str) -> List[str]:
        try:
            soup = BeautifulSoup(html, "lxml")
            links = []
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                absolute_url = urljoin(base_url, href)
                if absolute_url.startswith("http"):
                    links.append(absolute_url)
            return links
        except Exception as exc:
            logger.error("Error extracting links: %s", exc)
            return []

    def calculate_relevance(self, html: str, keywords: List[str]) -> float:
        try:
            text = BeautifulSoup(html, "lxml").get_text().lower()
            score = 0.0

            for keyword in keywords:
                count = text.count(keyword.lower())
                score += min(count * 0.05, 0.3)

            indicators = [
                "design",
                "sizing",
                "calculation",
                "specification",
                "installation",
                "performance",
                "case study",
                "manual",
            ]
            for term in indicators:
                if term in text:
                    score += 0.05

            return min(score, 1.0)
        except Exception as exc:
            logger.error("Error calculating relevance: %s", exc)
            return 0.0

    def save_result(self, result: Dict, topic_id: int):
        try:
            topic_dir = config.RAW_DATA_DIR / f"topic_{topic_id:03d}"
            topic_dir.mkdir(exist_ok=True)

            filename = f"{result['content_hash'][:16]}.json"
            filepath = topic_dir / filename

            metadata = {
                "url": result["url"],
                "final_url": result.get("final_url", result["url"]),
                "content_hash": result["content_hash"],
                "topic_name": result["topic_name"],
                "timestamp": result["timestamp"],
                "content_length": len(result["content"]),
                "from_cache": result.get("from_cache", False),
            }

            filepath.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            logger.debug("Saved result: %s", filepath)

        except Exception as exc:
            logger.error("Error saving result: %s", exc)


async def crawl_topic(topic: Dict, discovered_urls: List[Dict], max_urls: int = 100):
    logger.info("%s", "=" * 60)
    logger.info("Starting crawl for: %s (ID: %s)", topic["name"], topic["id"])
    logger.info("%s", "=" * 60)

    seed_urls = [
        {"url": url_data["url"], "topic_name": topic["name"]}
        for url_data in discovered_urls[:max_urls]
    ]

    if not seed_urls:
        logger.warning("No URLs found for %s, skipping", topic["name"])
        return []

    logger.info("Crawling %d discovered URLs", len(seed_urls))

    async with AsyncCrawler() as crawler:
        results = await crawler.crawl_batch(seed_urls)
        for result in results:
            crawler.save_result(result, topic["id"])

        cache_stats = crawler.cache.stats()
        logger.info("Topic: %s", topic["name"])
        logger.info("  URLs crawled: %d", len(results))
        logger.info("  Cache hit rate: %.1f%%", cache_stats["hit_rate_percent"])
        logger.info("  Data saved to: %s", config.RAW_DATA_DIR / f"topic_{topic['id']:03d}")

    return results


async def crawl_all_topics(max_urls_per_topic: int = 50):
    logger.info("Starting crawl for %d topics", len(config.TECHNOLOGIES))
    logger.info("Max URLs per topic: %d", max_urls_per_topic)
    logger.info("Storage location: %s", config.BASE_DIR)

    discovered_file = config.BASE_DIR / "discovered_urls_enhanced.json"
    if not discovered_file.exists():
        discovered_file = config.BASE_DIR / "discovered_urls.json"
    if not discovered_file.exists():
        logger.error("No discovered URLs file found at %s", discovered_file)
        logger.error("Please run discovery/url_discovery.py first!")
        return

    logger.info("Loading discovered URLs from %s", discovered_file)
    discovered_data = json.loads(discovered_file.read_text())

    start_time = time.time()
    total_crawled = 0

    for topic in config.TECHNOLOGIES:
        try:
            topic_id = str(topic["id"])
            if topic_id in discovered_data:
                urls = discovered_data[topic_id]["urls"]
                logger.info("Found %d URLs for %s", len(urls), topic["name"])
                await crawl_topic(topic, urls, max_urls=max_urls_per_topic)
                total_crawled += min(len(urls), max_urls_per_topic)
            else:
                logger.warning("No discovered URLs for %s", topic["name"])
        except Exception as exc:
            logger.error("Failed to crawl %s: %s", topic["name"], exc)
            continue

    elapsed = time.time() - start_time
    logger.info("%s", "=" * 60)
    logger.info("Crawl complete!")
    logger.info("Total URLs crawled: %d", total_crawled)
    logger.info("Total time: %.1f minutes", elapsed / 60)
    logger.info("Results saved to: %s", config.RAW_DATA_DIR)
    logger.info("%s", "=" * 60)


if __name__ == "__main__":
    asyncio.run(crawl_all_topics(max_urls_per_topic=50))
