import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import pandas as pd
from tqdm.asyncio import tqdm

import config.config as cfg
from scraper.company_discovery import discover_companies
from scraper.hiring_checker import check_hiring_status
from scraper.social_media_finder import find_social_media


def setup_logging():
    os.makedirs(cfg.LOG_DIR, exist_ok=True)
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)

    # Force UTF-8 on the Windows console so box-drawing chars from
    # Playwright error messages don't crash the cp1252 StreamHandler.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    log_file = os.path.join(cfg.LOG_DIR, cfg.LOG_FILENAME)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    file_handler.setFormatter(file_formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter("%(levelname)-8s | %(message)s")
    console_handler.setFormatter(console_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


async def process_company(company: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            company = await check_hiring_status(company)
        except Exception as e:
            logging.getLogger(__name__).error("Hiring check failed for %s: %s", company.get("company_name", "?"), e)
            company["hiring_status"] = "Unknown"
            company["careers_page"] = "N/A"

        try:
            company = await find_social_media(company)
        except Exception as e:
            logging.getLogger(__name__).error("Social media find failed for %s: %s", company.get("company_name", "?"), e)

        return company


async def main():
    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("  Starting Talent Intelligence Scraper...")
    logger.info("=" * 60)

    logger.info("STEP 1: Discovering companies (funding > $%s)...", f"{cfg.MIN_FUNDING:,}")
    companies = await discover_companies()
    logger.info("Found %d companies with funding > $%s", len(companies), f"{cfg.MIN_FUNDING:,}")

    if not companies:
        logger.warning("No companies found. Exiting.")
        return

    logger.info("STEP 2: Checking hiring status & finding social media...")
    sem = asyncio.Semaphore(3)
    tasks = [process_company(c, sem) for c in companies]
    results = []
    for coro in tqdm.as_completed(tasks, total=len(tasks), desc="Processing companies"):
        result = await coro
        results.append(result)

    logger.info("STEP 3: Cleaning data with Pandas...")
    df = pd.DataFrame(results)

    df = df.drop_duplicates(subset=["company_name"], keep="first")
    df = df.fillna("N/A")

    # open_positions: convert floats (e.g. 4.0) to int strings; keep "N/A" as-is.
    # Without this, Excel round-trips lose "N/A" because pandas infers a float column.
    def _clean_positions(val):
        try:
            if str(val) in ("N/A", "nan", ""):
                return "N/A"
            return str(int(float(val)))
        except (ValueError, TypeError):
            return "N/A"
    df["open_positions"] = df["open_positions"].apply(_clean_positions)

    expected_cols = [
        "company_name", "industry", "total_funding", "funding_stage",
        "headquarters", "website", "description", "careers_page", "hiring_status",
        "open_positions", "hiring_roles", "latest_funding_date",
        "linkedin_url", "twitter_url", "facebook_url", "instagram_url",
        "youtube_url", "source_url"
    ]
    for col in expected_cols:
        if col not in df.columns:
            df[col] = "N/A"
    df = df[expected_cols]

    df["company_name"] = df["company_name"].str.strip()

    xlsx_path = os.path.join(cfg.OUTPUT_DIR, cfg.EXCEL_FILENAME)
    json_path = os.path.join(cfg.OUTPUT_DIR, cfg.JSON_FILENAME)

    logger.info("STEP 4: Exporting Excel -> %s", xlsx_path)
    df.to_excel(xlsx_path, index=False, engine="openpyxl")

    logger.info("STEP 5: Exporting JSON -> %s", json_path)
    # Explicit UTF-8 write — pandas to_json() uses the system encoding on
    # Windows (cp1252) which crashes on Unicode chars in company names/descriptions.
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(df.to_json(orient="records", indent=2, force_ascii=False))

    hiring_count = len(df[df["hiring_status"] == "Hiring"])
    not_hiring_count = len(df[df["hiring_status"] == "Not Hiring"])
    unknown_count = len(df[df["hiring_status"] == "Unknown"])

    logger.info("=" * 60)
    logger.info("  TALENT INTELLIGENCE SCRAPER — COMPLETE")
    logger.info("=" * 60)
    logger.info("  Total Companies Found : %d", len(df))
    logger.info("  Currently Hiring      : %d", hiring_count)
    logger.info("  Not Hiring            : %d", not_hiring_count)
    logger.info("  Unknown               : %d", unknown_count)
    logger.info("-" * 60)
    logger.info("  Output Files:")
    logger.info("  -> %s", xlsx_path)
    logger.info("  -> %s", json_path)
    log_file_path = os.path.join(cfg.LOG_DIR, cfg.LOG_FILENAME)
    logger.info("  -> %s", log_file_path)
    logger.info("=" * 60)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
