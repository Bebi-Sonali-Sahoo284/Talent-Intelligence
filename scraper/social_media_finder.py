import logging
import re
from urllib.parse import urlparse, urlunparse

from playwright.async_api import async_playwright

import config.config as cfg

logger = logging.getLogger(__name__)

# Pages to try if the homepage yields no social links
_FALLBACK_PATHS = ["/about", "/about-us", "/company", "/contact"]


async def find_social_media(company: dict) -> dict:
    result = dict(company)
    for platform in cfg.SOCIAL_DOMAINS:
        result[f"{platform}_url"] = "N/A"

    website = company.get("website", "")
    if not website or website == "N/A":
        logger.info("No website for %s — skipping social media search", company.get("company_name"))
        return result

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=cfg.HEADLESS)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()
            page.set_default_timeout(cfg.TIMEOUT)

            logger.info("Finding social media for %s at %s", company["company_name"], website)

            try:
                await page.goto(website, wait_until="domcontentloaded", timeout=cfg.TIMEOUT)
            except Exception as e:
                logger.warning("Could not load %s: %s", website, e)
                await browser.close()
                return result

            # Initial wait for JS to settle
            await page.wait_for_timeout(2000)

            # Scroll to bottom to trigger lazy-loaded / JS-rendered footer links
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

            social_found = await _scan_links(page, result, set(), company["company_name"])

            # Fallback: try /about, /about-us, /company, /contact pages if links still missing
            missing = set(cfg.SOCIAL_DOMAINS.keys()) - social_found
            if missing:
                logger.info(
                    "%s: still missing %s — trying fallback pages",
                    company["company_name"], missing
                )
                for path in _FALLBACK_PATHS:
                    if not missing:
                        break
                    fallback_url = website.rstrip("/") + path
                    try:
                        resp = await page.goto(
                            fallback_url, wait_until="domcontentloaded", timeout=10000
                        )
                        if resp and resp.ok:
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await page.wait_for_timeout(1000)
                            social_found = await _scan_links(
                                page, result, social_found, company["company_name"]
                            )
                            missing = set(cfg.SOCIAL_DOMAINS.keys()) - social_found
                    except Exception:
                        continue

            await browser.close()
            logger.info(
                "Social media search complete for %s — found: %s",
                company["company_name"], social_found or "none"
            )
            return result

    except Exception as e:
        logger.error("Social media search failed for %s: %s", company["company_name"], e)
        return result


async def _scan_links(page, result: dict, already_found: set, company_name: str) -> set:
    """
    Scan all <a href> links on the current page, match against SOCIAL_DOMAINS,
    and write found URLs into result. Skips platforms already in already_found.
    Returns the updated set of found platform names.
    """
    social_found = set(already_found)
    all_links = await page.query_selector_all("a[href]")

    for link in all_links:
        try:
            href = await link.get_attribute("href")
            if not href:
                continue
            href = href.strip()
            if not _is_valid_url(href):
                continue

            href_lower = href.lower()
            for platform, domains in cfg.SOCIAL_DOMAINS.items():
                if platform in social_found:
                    continue
                domain_list = domains if isinstance(domains, list) else [domains]
                for domain in domain_list:
                    if domain in href_lower:
                        normalized = _normalize_social_url(href)
                        result[f"{platform}_url"] = normalized
                        social_found.add(platform)
                        logger.info(
                            "Found %s: %s for %s", platform, normalized, company_name
                        )
                        break
        except Exception:
            continue

    return social_found


def _normalize_social_url(url: str) -> str:
    """
    Normalise a social media URL:
      - Lowercase the scheme and hostname  (linkedIn.com → linkedin.com)
      - Strip trailing hyphens from the path  (/company/heap-inc- → /company/heap-inc)
      - Strip a lone trailing slash only when the path has content
    """
    try:
        parsed = urlparse(url)
        clean_path = parsed.path.rstrip("-")
        # Only strip trailing slash if not root path
        if clean_path != "/" and clean_path.endswith("/"):
            clean_path = clean_path.rstrip("/")
        normalized = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            clean_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        ))
        return normalized
    except Exception:
        return url


def _is_valid_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")
