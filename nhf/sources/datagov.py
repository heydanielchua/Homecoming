"""data.gov.sg CKAN datastore client.

Rate limits now apply to keyless use, so the sync job bulk-downloads a whole
resource once a month rather than querying live per page view. Set
DATAGOV_API_KEY to raise the limit.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Iterator

import requests

from ..config import TODO, load_config

log = logging.getLogger(__name__)


class DataGovError(RuntimeError):
    """A sync-stopping failure: a dead resource id, or a rate limit we respected."""


class DataGovClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        cfg = load_config()
        self.url: str = cfg.api("datastore_url")
        self.page_size: int = int(cfg.api("page_size"))
        self.max_retries: int = int(cfg.api("max_retries"))
        self.timeout: int = int(cfg.api("timeout_seconds"))
        self.session = session or requests.Session()
        self.api_key = os.environ.get("DATAGOV_API_KEY")

    # -- low level ---------------------------------------------------------
    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        delay = 2.0
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(
                    self.url, params=params, headers=headers, timeout=self.timeout
                )
                if resp.status_code == 429:
                    raise DataGovError("rate limited (429)")
                resp.raise_for_status()
                payload = resp.json()
                if not payload.get("success", False):
                    raise DataGovError(f"datastore_search returned success=false: {payload}")
                return payload["result"]
            except (requests.RequestException, DataGovError, ValueError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    break
                log.warning(
                    "data.gov.sg request failed (attempt %s/%s): %s; retrying in %.0fs",
                    attempt, self.max_retries, exc, delay,
                )
                time.sleep(delay)
                delay *= 2
        raise DataGovError(f"giving up on {params.get('resource_id')}: {last_error}")

    # -- public ------------------------------------------------------------
    def records(self, resource_id: str, limit: int | None = None) -> Iterator[dict[str, Any]]:
        """Every record in a resource, paged.

        Paging is by offset because the datastore has no cursor. The total is
        re-read each page: if the resource grows mid-download the loop still
        terminates on a short page.
        """
        if not resource_id or resource_id == TODO:
            raise DataGovError("resource id is not set (still TODO in config/datasets.yml)")

        offset = 0
        seen = 0
        while True:
            page_size = self.page_size
            if limit is not None:
                page_size = min(page_size, limit - seen)
                if page_size <= 0:
                    return
            result = self._get(
                {"resource_id": resource_id, "limit": page_size, "offset": offset}
            )
            records = result.get("records", [])
            if not records:
                return
            for record in records:
                yield record
                seen += 1
                if limit is not None and seen >= limit:
                    return
            offset += len(records)
            total = result.get("total")
            if len(records) < page_size or (total is not None and offset >= int(total)):
                return
