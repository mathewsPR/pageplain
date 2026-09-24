from typing import Any, Dict, List
from config import settings


def get_launch_args() -> List[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-infobars",
        "--ignore-certificate-errors",
        "--disable-features=IsolateOrigins,site-per-process",
    ]


def get_context_options() -> Dict[str, Any]:
    return {
        "viewport": {
            "width": settings.viewport_width,
            "height": settings.viewport_height,
        },
        "locale": settings.locale,
        "timezone_id": settings.timezone_id,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36"
        ),
        "java_script_enabled": True,
        "accept_downloads": False,
        "has_touch": False,
        "is_mobile": False,
        "color_scheme": "light",
        "extra_http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
        },
    }
