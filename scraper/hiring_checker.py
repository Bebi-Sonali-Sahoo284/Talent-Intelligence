import json
import logging
import re
from datetime import datetime, timezone

import requests
from playwright.async_api import async_playwright

import config.config as cfg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ATS domain patterns — maps a substring found in a careers URL to a platform
# ---------------------------------------------------------------------------
ATS_PATTERNS = {
    "greenhouse": "boards.greenhouse.io",
    "lever":      "jobs.lever.co",
    "ashby":      "jobs.ashbyhq.com",
    "workable":   "apply.workable.com",
}

# ---------------------------------------------------------------------------
# Phrases that signal a role or the entire hiring page is closed / filled.
# Checked against the full lowercased page text.
# ---------------------------------------------------------------------------
_CLOSED_SIGNALS = [
    "position filled",
    "role has been filled",
    "this position has been filled",
    "no longer accepting applications",
    "position is no longer available",
    "this job is closed",
    "job is no longer available",
    "application closed",
    "applications are closed",
    "hiring is paused",
    "position closed",
    "we are not currently hiring",
    "not accepting applications",
    "this role is closed",
    "posting has been closed",
    "unfortunately this role",
    "this opportunity is no longer",
    "role is no longer open",
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def check_hiring_status(company: dict) -> dict:
    result = dict(company)
    result["hiring_status"] = "Not Hiring"
    result["careers_page"] = "N/A"
    result["open_positions"] = "N/A"
    result["hiring_roles"] = "N/A"

    website = company.get("website", "")
    if not website or website == "N/A":
        logger.info("No website for %s — marking Unknown", company.get("company_name"))
        result["hiring_status"] = "Unknown"
        return result

    attempt = 0
    while attempt < cfg.RETRY_ATTEMPTS:
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

                logger.info("Checking hiring for %s at %s", company["company_name"], website)

                try:
                    await page.goto(website, wait_until="domcontentloaded", timeout=cfg.TIMEOUT)
                except Exception as e:
                    logger.warning("Could not load %s: %s", website, e)
                    result["hiring_status"] = "Unknown"
                    await browser.close()
                    return result

                all_links = await page.query_selector_all("a[href]")
                found_careers = False
                careers_url = None

                for link in all_links:
                    href = await link.get_attribute("href")
                    if not href:
                        continue
                    href_lower = href.lower()
                    for keyword in cfg.HIRING_KEYWORDS:
                        if keyword in href_lower:
                            full_url = _resolve_url(website, href)
                            if full_url:
                                careers_url = full_url
                                result["careers_page"] = full_url
                                result["hiring_status"] = "Hiring"
                                found_careers = True
                                logger.info("Found careers link: %s for %s", full_url, company["company_name"])
                                break
                    if found_careers:
                        break

                if not found_careers:
                    for path in cfg.CAREERS_PATHS:
                        try_url = website.rstrip("/") + path
                        try:
                            resp = await page.goto(try_url, wait_until="domcontentloaded", timeout=10000)
                            if resp and resp.ok:
                                careers_url = try_url
                                result["careers_page"] = try_url
                                result["hiring_status"] = "Hiring"
                                logger.info("Found careers page at %s for %s", try_url, company["company_name"])
                                found_careers = True
                                break
                        except Exception:
                            continue

                if found_careers and careers_url:
                    await _extract_jobs(page, careers_url, result)

                if not found_careers:
                    logger.info("No careers page found for %s", company["company_name"])

                await browser.close()
                return result

        except Exception as e:
            attempt += 1
            logger.error("Attempt %d/%d for %s failed: %s", attempt, cfg.RETRY_ATTEMPTS, company["company_name"], e)
            if attempt >= cfg.RETRY_ATTEMPTS:
                result["hiring_status"] = "Unknown"
                return result

    return result


# ---------------------------------------------------------------------------
# Orchestrator: tries each extraction strategy in priority order
# ---------------------------------------------------------------------------

async def _extract_jobs(page, careers_url: str, result: dict) -> None:
    """
    Tries three strategies in order, stopping as soon as one yields titles:
      1. JSON-LD structured data  (schema.org/JobPosting)
      2. ATS platform API / page  (Greenhouse, Lever, Ashby, Workable)
      3. Generic page scraping    (improved CSS selectors + text heuristics)
    """
    try:
        await page.goto(careers_url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2000)

        # Bail early if the page explicitly signals hiring is closed or the role is filled
        if await _page_signals_closed(page):
            logger.info("Closed/filled signals detected on %s — overriding to Not Hiring", careers_url)
            result["hiring_status"] = "Not Hiring"
            return

        # --- Strategy 1: JSON-LD ---
        titles = await _extract_from_jsonld(page)
        if titles:
            logger.info("JSON-LD extraction succeeded (%d roles)", len(titles))
            _apply_job_results(result, titles)
            return

        # --- Strategy 2: ATS platform ---
        ats = _detect_ats(careers_url)
        if ats:
            logger.info("Detected ATS platform: %s", ats)
            titles = await _extract_from_ats(page, careers_url, ats)
            if titles:
                logger.info("ATS extraction succeeded (%d roles)", len(titles))
                _apply_job_results(result, titles)
                return

        # --- Strategy 3: Generic scraping ---
        titles = await _extract_generic(page)
        if titles:
            logger.info("Generic extraction succeeded (%d roles)", len(titles))
            _apply_job_results(result, titles)

    except Exception as e:
        logger.warning("Could not extract jobs from %s: %s", careers_url, e)


# ---------------------------------------------------------------------------
# Strategy 1 — JSON-LD structured data (schema.org/JobPosting)
# ---------------------------------------------------------------------------

async def _extract_from_jsonld(page) -> list[str]:
    """
    Parses every <script type="application/ld+json"> block on the page.
    Collects job titles from any object whose @type is JobPosting.
    Returns a deduplicated list of title strings, or [] if none found.
    """
    try:
        raw_blocks: list[str] = await page.evaluate("""
            () => [...document.querySelectorAll('script[type="application/ld+json"]')]
                  .map(s => s.textContent)
        """)
    except Exception as e:
        logger.debug("JSON-LD evaluate failed: %s", e)
        return []

    titles: list[str] = []
    for block in raw_blocks:
        try:
            data = json.loads(block)
        except (json.JSONDecodeError, TypeError):
            continue

        # data can be a single object or a list
        items = data if isinstance(data, list) else [data]
        for item in items:
            # Handle @graph wrapper (common in schema.org)
            if item.get("@type") == "ItemList" and "@graph" in item:
                items.extend(item["@graph"])
                continue
            graph = item.get("@graph", [])
            if graph:
                items.extend(graph)

            if item.get("@type") == "JobPosting":
                date_posted = item.get("datePosted", "")
                if _is_stale_by_date(date_posted):
                    logger.debug("Skipping stale JSON-LD posting (datePosted: %s)", date_posted)
                    continue
                title = item.get("title") or item.get("name", "")
                if title and isinstance(title, str):
                    titles.append(title.strip())

    return _dedup(titles)


# ---------------------------------------------------------------------------
# Strategy 2 — ATS platform detection and extraction
# ---------------------------------------------------------------------------

def _detect_ats(careers_url: str) -> str | None:
    """Returns the ATS platform name if the URL matches a known pattern."""
    url_lower = careers_url.lower()
    for platform, domain in ATS_PATTERNS.items():
        if domain in url_lower:
            return platform
    return None


async def _extract_from_ats(page, careers_url: str, ats: str) -> list[str]:
    """Dispatches to the right ATS extractor."""
    try:
        if ats == "greenhouse":
            return await _extract_greenhouse(careers_url)
        if ats == "lever":
            return await _extract_lever(careers_url)
        if ats == "ashby":
            return await _extract_ashby(page, careers_url)
        if ats == "workable":
            return await _extract_workable(page)
    except Exception as e:
        logger.warning("ATS extraction (%s) failed: %s", ats, e)
    return []


async def _extract_greenhouse(careers_url: str) -> list[str]:
    """
    Greenhouse exposes a public JSON API at:
      https://boards.greenhouse.io/v1/boards/<company-slug>/jobs
    The company slug is the path segment after boards.greenhouse.io/
    """
    match = re.search(r"boards\.greenhouse\.io/(?:embed/job_board\?for=)?([^/?#]+)", careers_url, re.I)
    if not match:
        return []
    slug = match.group(1)
    api_url = f"https://boards.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        resp = requests.get(api_url, timeout=10)
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        titles = [j.get("title", "").strip() for j in jobs if j.get("title")]
        logger.info("Greenhouse API: %d jobs for slug '%s'", len(titles), slug)
        return _dedup(titles)
    except Exception as e:
        logger.warning("Greenhouse API call failed for %s: %s", slug, e)
        return []


async def _extract_lever(careers_url: str) -> list[str]:
    """
    Lever exposes a public JSON API at:
      https://api.lever.co/v0/postings/<company-slug>
    The company slug is the path segment after jobs.lever.co/
    """
    match = re.search(r"jobs\.lever\.co/([^/?#]+)", careers_url, re.I)
    if not match:
        return []
    slug = match.group(1)
    api_url = f"https://api.lever.co/v0/postings/{slug}"
    try:
        resp = requests.get(api_url, timeout=10)
        resp.raise_for_status()
        jobs = resp.json()
        titles = [j.get("text", "").strip() for j in jobs if j.get("text")]
        logger.info("Lever API: %d jobs for slug '%s'", len(titles), slug)
        return _dedup(titles)
    except Exception as e:
        logger.warning("Lever API call failed for %s: %s", slug, e)
        return []


async def _extract_ashby(page, careers_url: str) -> list[str]:
    """
    Ashby job boards are JS-rendered. We navigate to the page and
    target the specific CSS class Ashby uses for job listing rows.
    """
    try:
        await page.goto(careers_url, wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(3000)
        elements = await page.query_selector_all(
            "a[href*='/jobs/'], "
            "[class*='JobListing'], [class*='job-listing'], "
            "[data-testid*='job'], [class*='PostingTitle']"
        )
        titles = []
        for el in elements:
            text = (await el.inner_text()).strip()
            if text and _is_likely_job_title(text):
                titles.append(text)
        logger.info("Ashby page scrape: %d job titles", len(titles))
        return _dedup(titles)
    except Exception as e:
        logger.warning("Ashby scrape failed: %s", e)
        return []


async def _extract_workable(page) -> list[str]:
    """
    Workable job boards render job rows in <li> elements inside
    a predictable container. We wait for them and extract the title.
    """
    try:
        await page.wait_for_selector("li[data-ui='job']", timeout=8000)
        elements = await page.query_selector_all("li[data-ui='job'] h3, li[data-ui='job'] [class*='title']")
        titles = []
        for el in elements:
            text = (await el.inner_text()).strip()
            if text and _is_likely_job_title(text):
                titles.append(text)
        logger.info("Workable scrape: %d job titles", len(titles))
        return _dedup(titles)
    except Exception as e:
        logger.warning("Workable scrape failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Strategy 3 — Generic page scraping (improved)
# ---------------------------------------------------------------------------

# Keywords whose presence in a short line suggests it is a job title
_ROLE_KEYWORDS = [
    "engineer", "manager", "developer", "designer", "analyst", "scientist",
    "intern", "associate", "director", "lead", "architect", "specialist",
    "product", "sales", "marketing", "recruiter", "operations", "counsel",
    "officer", "researcher", "writer", "coordinator", "strategist",
]


async def _extract_generic(page) -> list[str]:
    """
    Two-pass generic approach:
      Pass 1 — targeted CSS selectors for common job-board patterns.
      Pass 2 — full text scan with improved title heuristics as fallback.
    """
    # Pass 1: targeted CSS
    elements = await page.query_selector_all(
        "h2[class*='job'], h3[class*='job'], h4[class*='job'], "
        "li[class*='job-title'], span[class*='job-title'], "
        "[data-job-title], [data-role], "
        "td[class*='title'], .job-title, .role-title, .position-title"
    )
    titles = []
    for el in elements:
        text = (await el.inner_text()).strip()
        if text and _is_likely_job_title(text):
            titles.append(text)

    if titles:
        return _dedup(titles)

    # Pass 2: full text fallback with stricter filtering
    try:
        all_text: str = await page.evaluate("document.body.innerText")
    except Exception:
        return []

    lines = [l.strip() for l in all_text.split("\n") if l.strip()]
    for line in lines:
        line_lower = line.lower()
        if any(kw in line_lower for kw in _ROLE_KEYWORDS):
            if _is_likely_job_title(line):
                titles.append(line)

    return _dedup(titles)


# ---------------------------------------------------------------------------
# Stale / closed-posting detection helpers
# ---------------------------------------------------------------------------

def _is_stale_by_date(date_str: str) -> bool:
    """
    Returns True if the ISO-8601 datePosted string is older than
    cfg.MAX_JOB_AGE_DAYS. Returns False when the date is missing or
    unparseable (safe default: keep the job).
    """
    if not date_str:
        return False
    try:
        normalized = date_str.replace("Z", "+00:00")
        posted = datetime.fromisoformat(normalized)
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - posted).days
        return age_days > cfg.MAX_JOB_AGE_DAYS
    except (ValueError, TypeError):
        return False


async def _page_signals_closed(page) -> bool:
    """
    Returns True if the lowercased full-page text contains any phrase from
    _CLOSED_SIGNALS — indicating the role or the entire hiring page is no
    longer active.
    """
    try:
        text_lower = (await page.evaluate("document.body.innerText")).lower()
        for signal in _CLOSED_SIGNALS:
            if signal in text_lower:
                logger.debug("Closed signal matched: '%s'", signal)
                return True
        return False
    except Exception:
        return False


async def _has_apply_button(page) -> bool:
    """
    Returns True if the page contains an active apply CTA (button, link, or
    form).  Most useful on single-job-posting pages: if a company forgot to
    unpublish but disabled/removed the apply mechanism, this returns False.

    Note: on multi-listing pages (job boards) apply buttons live on
    individual job detail pages, so a False result here is not conclusive.
    """
    try:
        found: bool = await page.evaluate("""
            () => {
                const applyText = [
                    'apply now', 'apply for this job', 'apply for this role',
                    'submit application', 'apply today', 'apply here', 'apply'
                ];
                const candidates = [
                    ...document.querySelectorAll('a, button, input[type="submit"]')
                ];
                return candidates.some(el => {
                    const text = (el.innerText || el.value || '').toLowerCase().trim();
                    return applyText.some(t => text === t || text.startsWith(t));
                });
            }
        """)
        return bool(found)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _is_likely_job_title(text: str) -> bool:
    """
    Heuristic filter that accepts short, clean lines likely to be job titles
    and rejects sentences, URLs, nav items, and other noise.
    """
    if not text:
        return False
    # Length guard: real titles are 4–60 characters
    if len(text) < 4 or len(text) > 60:
        return False
    # Not a URL or path
    if text.startswith(("http", "/", "#")):
        return False
    # Not a sentence (ends with terminal punctuation)
    if text.endswith((".", "!", "?")):
        return False
    # Not obviously a nav/button label (all-lower single word)
    words = text.split()
    if len(words) == 1 and text.islower():
        return False
    # Not a paragraph (too many words)
    if len(words) > 8:
        return False
    # Must not be a number-only string
    if text.replace(",", "").replace(".", "").isdigit():
        return False
    return True


def _dedup(titles: list[str]) -> list[str]:
    """Remove duplicates while preserving insertion order."""
    return list(dict.fromkeys(t for t in titles if t))


def _apply_job_results(result: dict, titles: list[str]) -> None:
    """Write open_positions and hiring_roles into the result dict."""
    result["open_positions"] = len(titles)
    result["hiring_roles"] = ", ".join(titles[:20])


def _resolve_url(base: str, href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        base_domain = re.match(r"(https?://[^/]+)", base)
        if base_domain:
            return base_domain.group(1) + href
    if href.startswith("#"):
        return None
    return base.rstrip("/") + "/" + href.lstrip("/")
