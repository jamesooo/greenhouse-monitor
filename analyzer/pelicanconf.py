from __future__ import annotations

import os


AUTHOR = "Greenhouse Monitor"
SITENAME = os.environ.get("GREENHOUSE_SITE_NAME", "Greenhouse Daily")
SITEURL = os.environ.get("GREENHOUSE_SITE_URL", "").rstrip("/")
TIMEZONE = os.environ.get("GREENHOUSE_SITE_TIMEZONE", "America/Los_Angeles")
DEFAULT_LANG = "en"

THEME = os.environ.get(
    "GREENHOUSE_PELICAN_THEME", "/opt/greenhouse-analyzer/site/theme"
)
STATIC_PATHS = ["images"]
ARTICLE_URL = "{date:%Y}/{date:%m}/{slug}.html"
ARTICLE_SAVE_AS = ARTICLE_URL
ARCHIVES_SAVE_AS = "archive.html"
DIRECT_TEMPLATES = ["index", "archives"]
DEFAULT_PAGINATION = False
RELATIVE_URLS = not bool(SITEURL)
DELETE_OUTPUT_DIRECTORY = True

FEED_ALL_ATOM = None
CATEGORY_FEED_ATOM = None
TRANSLATION_FEED_ATOM = None
AUTHOR_FEED_ATOM = None
AUTHOR_FEED_RSS = None