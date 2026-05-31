import asyncio
import logging
import re

import requests
from playwright.async_api import async_playwright

import config.config as cfg

logger = logging.getLogger(__name__)


def _parse_funding_amount(text: str) -> int | None:
    text = text.replace(",", "").replace(" ", "").upper()
    patterns = [
        r"RAISES?\s*\$?([\d.]+)\s*(M|MILLION|B|BILLION|K)",
        r"RAISED\s*\$?([\d.]+)\s*(M|MILLION|B|BILLION|K)",
        r"\$?([\d.]+)\s*(M|MILLION)\s*(?:ROUND|IN\s+FUNDING|SERIES)",
        r"\$?([\d.]+)\s*B\s*(?:ROUND|IN\s+FUNDING|SERIES)",
    ]
    multipliers = {"M": 1_000_000, "MILLION": 1_000_000,
                   "B": 1_000_000_000, "BILLION": 1_000_000_000,
                   "K": 1_000}
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            amount = float(match.group(1))
            suffix = match.group(2).upper() if match.lastindex >= 2 else "M"
            multiplier = multipliers.get(suffix, 1_000_000)
            return int(amount * multiplier)
    return None


async def _extract_funding_batch(companies: list) -> list:
    if not companies:
        return companies

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=cfg.HEADLESS)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()
            page.set_default_timeout(15000)

            for company in companies:
                slug = company.get("slug", "")
                if not slug:
                    company["total_funding"] = "N/A"
                    company["total_funding_value"] = 0
                    continue

                try:
                    url = f"https://www.ycombinator.com/companies/{slug}"
                    await page.goto(url, wait_until="domcontentloaded")
                    await asyncio.sleep(1.5)

                    text = await page.evaluate("document.body.innerText")
                    lines = [l.strip() for l in text.split("\n") if l.strip()]

                    max_funding = 0
                    latest_funding_date = "N/A"
                    date_pattern = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}")

                    for i, line in enumerate(lines):
                        amount = _parse_funding_amount(line)
                        if amount and amount > max_funding:
                            max_funding = amount
                            for j in range(i, min(i + 3, len(lines))):
                                date_match = date_pattern.search(lines[j])
                                if date_match:
                                    latest_funding_date = date_match.group(0)
                                    break

                    if max_funding > 0:
                        company["total_funding_value"] = max_funding
                        if max_funding >= 1_000_000_000:
                            company["total_funding"] = f"${max_funding / 1_000_000_000:.1f}B"
                        elif max_funding >= 1_000_000:
                            company["total_funding"] = f"${max_funding / 1_000_000:.1f}M"
                        else:
                            company["total_funding"] = f"${max_funding:,}"
                        company["latest_funding_date"] = latest_funding_date
                        logger.info("Funding for %s: %s (date: %s)", company["company_name"], company["total_funding"], latest_funding_date)
                    else:
                        company["total_funding"] = "N/A"
                        company["total_funding_value"] = 0
                        company["latest_funding_date"] = "N/A"

                except Exception as e:
                    logger.warning("Failed funding extraction for %s: %s", slug, e)
                    company["total_funding"] = "N/A"
                    company["total_funding_value"] = 0

            await browser.close()
            return companies

    except Exception as e:
        logger.error("Batch funding extraction failed: %s", e)
        for c in companies:
            c["total_funding"] = "N/A"
            c["total_funding_value"] = 0
        return companies


def _fetch_from_algolia(page: int = 0, hits_per_page: int = 50) -> list:
    url = f"https://{cfg.ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{cfg.ALGOLIA_INDEX}/query"
    headers = {
        "X-Algolia-API-Key": cfg.ALGOLIA_API_KEY,
        "X-Algolia-Application-Id": cfg.ALGOLIA_APP_ID,
        "Content-Type": "application/json"
    }
    payload = {
        "params": f"hitsPerPage={hits_per_page}&page={page}"
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json().get("hits", [])


def _convert_hit_to_company(hit: dict) -> dict:
    industry = hit.get("industry") or hit.get("subindustry", "").split(" -> ")[-1] or "N/A"

    stage_map = {
        "Seed": "Seed", "Early": "Seed",
        "Series A": "Series A", "Series B": "Series B",
        "Series C": "Series C", "Growth": "Series B",
        "Public": "Public"
    }
    funding_stage = stage_map.get(hit.get("stage", ""), hit.get("stage", "Unknown"))

    locations = hit.get("all_locations", "N/A")
    if isinstance(locations, str) and locations:
        hq = locations.split(";")[0].strip()
    else:
        hq = "N/A"

    return {
        "company_name": hit.get("name", "Unknown"),
        "industry": industry if industry else "N/A",
        "total_funding": "N/A",
        "total_funding_value": 0,
        "funding_stage": funding_stage,
        "headquarters": hq,
        "website": hit.get("website", "N/A"),
        "description": hit.get("one_liner", "N/A"),
        "latest_funding_date": "N/A",
        "slug": hit.get("slug", ""),
        "stage": hit.get("stage", ""),
        "batch": hit.get("batch", ""),
        "source_url": f"https://www.ycombinator.com/companies/{hit.get('slug', '')}",
    }


async def discover_companies() -> list:
    companies = []
    page = 0
    needed = cfg.MAX_COMPANIES

    logger.info("Fetching companies from YC Algolia index...")

    all_candidates = []
    while len(all_candidates) < needed * 2:
        try:
            hits = _fetch_from_algolia(page=page, hits_per_page=50)
            if not hits:
                break
            logger.info("Fetched page %d with %d hits", page, len(hits))
            for hit in hits:
                all_candidates.append(_convert_hit_to_company(hit))
            page += 1
        except Exception as e:
            logger.error("Failed to fetch from Algolia: %s", e)
            break

    candidates = all_candidates[:needed * 2]
    logger.info("Processing %d candidates for funding extraction...", len(candidates))

    candidates = await _extract_funding_batch(candidates)

    for company in candidates:
        if len(companies) >= needed:
            break
        if company.get("total_funding_value", 0) >= cfg.MIN_FUNDING:
            company.pop("total_funding_value", None)
            company.pop("slug", None)
            company.pop("stage", None)
            company.pop("batch", None)
            companies.append(company)
            logger.info("Collected: %s — %s — %s — Date: %s",
                        company["company_name"],
                        company["total_funding"],
                        company["funding_stage"],
                        company.get("latest_funding_date", "N/A"))

    logger.info("Company discovery complete. Found %d companies with funding > $%s",
                len(companies), f"{cfg.MIN_FUNDING:,}")
    return companies
