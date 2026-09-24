from typing import Any, Dict, List
from playwright.async_api import Page, Response


class NetworkInterceptor:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.json_payloads: List[Dict[str, Any]] = []
        self._registered = False

    async def start(self) -> None:
        if self._registered:
            return

        async def on_response(response: Response) -> None:
            try:
                ct = response.headers.get("content-type", "").lower()
                if "application/json" in ct:
                    data = await response.json()
                    self.json_payloads.append({"url": response.url, "data": data})
            except Exception:
                pass

        self.page.on("response", on_response)
        self._registered = True

    def best_json(self) -> Dict[str, Any] | None:
        if not self.json_payloads:
            return None
        return max(self.json_payloads, key=lambda x: len(str(x.get("data", ""))))
