import logging
import re

from playwright.async_api import async_playwright

import config.config as cfg

logger = logging.getLogger(__name__)


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

            await page.wait_for_timeout(2000)

            all_links = await page.query_selector_all("a[href]")
            social_found = set()

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
                                result[f"{platform}_url"] = href
                                social_found.add(platform)
                                logger.info("Found %s: %s for %s", platform, href, company["company_name"])
                                break
                except Exception:
                    continue

            await browser.close()
            logger.info("Social media search complete for %s", company["company_name"])
            return result

    except Exception as e:
        logger.error("Social media search failed for %s: %s", company["company_name"], e)
        return result


def _is_valid_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")
