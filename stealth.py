from typing import Any, Dict, List
from config import settings


def get_launch_args() -> List[str]:
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        # --no-sandbox is required to run Chromium as root inside a
        # container without a dedicated seccomp/user-namespace setup. Prefer
        # running the container as a non-root user (see Dockerfile) so the
        # Chromium sandbox itself can stay enabled; only fall back to
        # --no-sandbox if your deployment truly cannot avoid running as root.
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-infobars",
        # NOTE: --ignore-certificate-errors was removed. It silently accepts
        # invalid/self-signed TLS certs on every site, which defeats TLS
        # verification and makes MITM interception undetectable. If a
        # specific internal/self-signed target needs this, opt in per-domain
        # rather than globally.
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
