# Talent Intelligence Scraper

Automated Python + Playwright bot that discovers funded startups ( > $4M ), checks if they are hiring, and collects social media profiles. Outputs Excel + JSON.

## Features
- Scrapes Y Combinator's startup directory via Algolia API for company data
- Extracts funding amounts from YC company detail pages
- Checks each company's website for careers/jobs pages
- Counts open positions and extracts job titles from careers pages
- Finds LinkedIn, Twitter/X, Facebook, Instagram, YouTube links
- Extracts latest funding date from YC news mentions
- Exports to `companies.xlsx` and `companies.json`
- Config-driven (no hardcoded values)
- Async Playwright with retry logic and comprehensive logging

## Tech Stack
- Python (async/await)
- Playwright (browser automation)
- Algolia Search API (company discovery)
- Pandas + OpenPyXL (data export)
- tqdm (progress bars)

## Installation
```bash
pip install -r requirements.txt
playwright install chromium
```

## How to Run
```bash
python main.py
```

## Folder Structure
```
talent-intelligence/
├── scraper/
│   ├── __init__.py
│   ├── company_discovery.py   # YC Algolia API + Playwright funding extraction
│   ├── hiring_checker.py      # Careers detection, job count, role extraction
│   └── social_media_finder.py # Social link extraction
├── config/
│   └── config.py              # All project settings
├── output/
│   ├── companies.xlsx         # Generated Excel output
│   └── companies.json         # Generated JSON output
├── logs/
│   └── scraper.log            # Generated log file
├── main.py                    # Pipeline orchestrator
├── requirements.txt
└── README.md
```

## Output Format
| Field | Description |
|---|---|
| company_name | Startup name |
| industry | Industry category |
| total_funding | Total funding amount raised |
| funding_stage | Funding round (Seed, Series A, Series B, etc.) |
| headquarters | HQ location |
| website | Official website URL |
| description | Short one-line description |
| careers_page | Detected careers/jobs page URL |
| hiring_status | Hiring / Not Hiring / Unknown |
| open_positions | Number of open job listings detected |
| hiring_roles | Comma-separated job titles found |
| latest_funding_date | Date of most recent funding round |
| linkedin_url | LinkedIn company page |
| twitter_url | Twitter/X profile |
| facebook_url | Facebook page |
| instagram_url | Instagram profile |
| youtube_url | YouTube channel |
| source_url | Y Combinator directory page |

## Configuration
Edit `config/config.py` to change:
- `MIN_FUNDING` — minimum funding threshold (default: $4,000,000)
- `HEADLESS` — run browser in background (default: True)
- `TIMEOUT` — page load timeout in ms (default: 30000)
- `MAX_COMPANIES` — max companies to collect (default: 50)
- `HIRING_KEYWORDS` — keywords to detect careers pages
- `SOCIAL_DOMAINS` — social media domain patterns
- `ALGOLIA_APP_ID` / `ALGOLIA_API_KEY` — YC Algolia search credentials

## Error Handling
- Retry logic (3 attempts) for network failures
- try/except on every page visit and API call
- Missing fields default to "N/A"
- Graceful degradation — one failure doesn't stop the pipeline
- Certificate errors and timeouts handled without crashing

## Sample Output
```json
[
  {
    "company_name": "DoorDash",
    "industry": "Consumer",
    "total_funding": "$400.0M",
    "funding_stage": "Series B",
    "headquarters": "San Francisco, CA, USA",
    "website": "http://doordash.com",
    "description": "Restaurant delivery.",
    "careers_page": "http://doordash.com/careers",
    "hiring_status": "Hiring",
    "open_positions": 12,
    "hiring_roles": "Software Engineer, Product Manager, Data Analyst",
    "latest_funding_date": "Feb 21, 2019",
    "linkedin_url": "https://linkedin.com/company/doordash",
    "twitter_url": "https://x.com/doordash",
    "facebook_url": "https://facebook.com/doordash",
    "instagram_url": "https://instagram.com/doordash",
    "youtube_url": "https://youtube.com/@doordash",
    "source_url": "https://www.ycombinator.com/companies/doordash"
  }
]
```
