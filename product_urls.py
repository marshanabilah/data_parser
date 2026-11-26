"""Extract product URLs while leaving original imported text unchanged."""

import re
from urllib.parse import urlsplit

URLS = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


def extract_product_urls(value):
    return tuple(match.group().rstrip(".,;:!?)]}") for match in URLS.finditer(value))


def single_https_host(value):
    urls = extract_product_urls(value)
    if not urls:
        return None
    hosts = set()
    try:
        for link in urls:
            parsed = urlsplit(link)
            if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                return None
            hosts.add(parsed.hostname.lower().rstrip("."))
    except ValueError:
        return None
    return hosts.pop() if len(hosts) == 1 else None
