"""
Universal Crawler Template configuration loader.
Loads config/config.yaml-style settings and exposes normalized constants.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "crawler_config.yaml"
CONFIG_PATH = Path(os.getenv("CRAWLER_CONFIG", DEFAULT_CONFIG_PATH))


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


_config = _load_yaml(CONFIG_PATH)
if not _config:
    raise ValueError(
        "Missing crawler configuration. Create config/crawler_config.yaml or set CRAWLER_CONFIG."
    )

_output_dir = _config.get("output_dir", "crawl_data")
_output_dir_path = Path(_output_dir)
if not _output_dir_path.is_absolute():
    _output_dir_path = PROJECT_ROOT / _output_dir_path

BASE_DIR = _output_dir_path.resolve()
RAW_DATA_DIR = BASE_DIR / "raw"
PROCESSED_DIR = BASE_DIR / "processed"
CACHE_DIR = BASE_DIR / "cache"
QUEUE_FILE = BASE_DIR / "queue.json"
CHECKPOINT_FILE = BASE_DIR / "checkpoint.json"
RESULTS_DB = BASE_DIR / "results.db"

for _dir in (RAW_DATA_DIR, PROCESSED_DIR, CACHE_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

LOG_LEVEL = _config.get("log_level", "INFO")
LOG_FILE = BASE_DIR / "crawler.log"

CONCURRENCY = int(_config.get("concurrency", 100))
RATE_LIMIT = float(_config.get("rate_limit", 0.5))
TIMEOUT = int(_config.get("timeout", 30))
MAX_RETRIES = int(_config.get("max_retries", 3))
BACKOFF_FACTOR = int(_config.get("backoff_factor", 2))
USER_AGENT = _config.get(
    "user_agent",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
)

_discovery_cfg = _config.get("discovery", {})
DISCOVERY_MAX_RESULTS = int(_discovery_cfg.get("max_results_per_query", 20))
DISCOVERY_QUERIES_PER_TOPIC = int(_discovery_cfg.get("max_queries_per_topic", 8))
DISCOVERY_ENABLE_BING = bool(_discovery_cfg.get("enable_bing", True))
DISCOVERY_ENABLE_SCHOLAR = bool(_discovery_cfg.get("enable_scholar", False))
DISCOVERY_CONCURRENCY = int(_discovery_cfg.get("max_concurrent", 10))
DISCOVERY_QUERY_DELAY = float(_discovery_cfg.get("per_query_delay_seconds", 2))

_domains_cfg = _load_yaml(PROJECT_ROOT / "config" / "domains_priority.yaml")
PRIORITY_DOMAINS: List[str] = _domains_cfg.get("priority_domains", [])
EXCLUDE_DOMAINS: List[str] = _domains_cfg.get("exclude_domains", [])

_topics = _config.get("topics", [])
if not _topics:
    raise ValueError(
        "No topics found in config/crawler_config.yaml. Add topics to proceed."
    )

TECHNOLOGIES: List[Dict[str, Any]] = []
for index, topic in enumerate(_topics, start=1):
    raw_id = topic.get("id", index)
    try:
        tech_id = int(raw_id)
    except (TypeError, ValueError):
        tech_id = index

    TECHNOLOGIES.append(
        {
            "id": tech_id,
            "name": topic.get("name", f"Topic {index}"),
            "keywords": topic.get("keywords", []),
            "vendors": topic.get("vendors", []),
            "category": topic.get("category", ""),
        }
    )
