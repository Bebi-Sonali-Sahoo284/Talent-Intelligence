MIN_FUNDING = 4_000_000
HEADLESS = True
TIMEOUT = 30000
RETRY_ATTEMPTS = 3
DELAY_BETWEEN_REQUESTS = 2
MAX_COMPANIES = 50
MAX_JOB_AGE_DAYS = 90  # JSON-LD postings older than this are treated as stale

OUTPUT_DIR = "./output"
LOG_DIR = "./logs"
EXCEL_FILENAME = "companies.xlsx"
JSON_FILENAME = "companies.json"
LOG_FILENAME = "scraper.log"

WELLFOUND_URL = "https://wellfound.com/companies"
YC_URL = "https://www.ycombinator.com/companies"

SOCIAL_DOMAINS = {
    "linkedin": "linkedin.com",
    "twitter": ["twitter.com", "x.com"],
    "facebook": "facebook.com",
    "instagram": "instagram.com",
    "youtube": "youtube.com"
}

HIRING_KEYWORDS = [
    "careers", "jobs", "hiring",
    "join-us", "work-with-us", "open-roles", "join-our-team"
]

CAREERS_PATHS = [
    "/careers",
    "/jobs",
    "/hiring",
    "/join-us",
    "/work-with-us",
    "/about/careers",
    "/company/careers"
]

ALGOLIA_APP_ID = "45BWZJ1SGC"
ALGOLIA_API_KEY = "NzllNTY5MzJiZGM2OTY2ZTQwMDEzOTNhYWZiZGRjODlhYzVkNjBmOGRjNzJiMWM4ZTU0ZDlhYTZjOTJiMjlhMWFuYWx5dGljc1RhZ3M9eWNkYyZyZXN0cmljdEluZGljZXM9WUNDb21wYW55X3Byb2R1Y3Rpb24lMkNZQ0NvbXBhbnlfQnlfTGF1bmNoX0RhdGVfcHJvZHVjdGlvbiZ0YWdGaWx0ZXJzPSU1QiUyMnljZGNfcHVibGljJTIyJTVE"
ALGOLIA_INDEX = "YCCompany_production"
