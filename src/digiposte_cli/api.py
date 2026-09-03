"""Digiposte API client (api.digiposte.fr/api/v3), Bearer-authenticated.

The token is obtained by `auth.manual_login()` (secure.digiposte.fr
`/rest/security/token` endpoint). Every call uses
`Authorization: Bearer <access_token>` + `X-API-VERSION-MINOR: 2`.
"""

import logging
import re
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("digiposte")

_DEFAULT_BASE_URL = "https://api.digiposte.fr/api/v3"
_DEFAULT_LOCATIONS = ["SAFE", "INBOX"]
_DEFAULT_MAX_RESULTS = 1000


def _extract_documents(payload: Any) -> list[dict[str, Any]]:
    """Extract the document list from the JSON response, whatever its shape:
    a direct list, or an object like {results: [...]}, {documents: [...]}…"""
    if isinstance(payload, list):
        return [d for d in payload if isinstance(d, dict)]
    if isinstance(payload, dict):
        for key in ("results", "documents", "items", "hits", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                return [d for d in value if isinstance(d, dict)]
    return []


class DigiposteAPI:
    """HTTP client authenticated for the Digiposte v3 API."""

    session: requests.Session

    def __init__(
        self,
        access_token: str,
        base_url: str = _DEFAULT_BASE_URL,
        locations: list[str] | None = None,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> None:
        self.base_url = base_url
        self.locations = locations or list(_DEFAULT_LOCATIONS)
        self.max_results = max_results

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "X-API-VERSION-MINOR": "2",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                ),
            }
        )

    # ── Documents ─────────────────────────────────────────────────────

    def _post(self, path: str, payload: dict[str, Any], label: str) -> object:
        """POST JSON; log the endpoint and return the JSON (or None on error)."""
        url = f"{self.base_url}{path}"
        log.info("🔎 %s → POST %s", label, url)
        resp = self.session.post(url, json=payload)
        if resp.status_code == 401:
            log.error(
                "   🔑 HTTP 401 on %s — token rejected. Run `digiposte-cli login` to refresh it.",
                url,
            )
            return None
        if resp.status_code != 200:
            log.warning("   ⚠️  HTTP %s on %s", resp.status_code, url)
            return None
        return resp.json()

    def _get(self, path: str, label: str) -> object:
        url = f"{self.base_url}{path}"
        log.info("🔎 %s → GET %s", label, url)
        resp = self.session.get(url)
        if resp.status_code == 401:
            log.error(
                "   🔑 HTTP 401 on %s — token rejected. Run `digiposte-cli login` to refresh it.",
                url,
            )
            return None
        if resp.status_code != 200:
            log.warning("   ⚠️  HTTP %s on %s", resp.status_code, url)
            return None
        return resp.json()

    def list_documents(self, folder_id: str = "") -> list[dict[str, Any]]:
        """Try to list the documents through several candidate endpoints,
        until one answers 200."""
        m = self.max_results
        locations = self.locations
        candidates: list[tuple[str, dict[str, Any]]] = [
            (
                f"/documents/search?max_results={m}&sort=CREATION_DATE&direction=DESC",
                {
                    "locations": locations,
                    **({"folder_id": folder_id} if folder_id else {}),
                },
            ),
            (
                f"/documents?max_results={m}&sort=CREATION_DATE&direction=DESC",
                {},
            ),
            (
                "/documents/facet",
                {"locations": locations},
            ),
        ]

        for path, body in candidates:
            if body:
                data = self._post(path, body, label="Search documents")
            else:
                data = self._get(path, label="Search documents")

            documents = _extract_documents(data) if data is not None else []
            if documents:
                log.info("✅ Endpoint kept: %s", path)
                log.info("📄 %s document(s) found", len(documents))
                return documents
            if data is None:
                continue

            # 200 but empty → try other GET endpoints
            if not documents:
                log.info("   (200 response but empty list on %s)", path)

        # Last resort: a few simple GETs
        for path in ("/folders", "/documents/safe", "/documents/inbox"):
            data = self._get(path, label="List (fallback)")
            documents = _extract_documents(data) if data is not None else []
            if documents:
                log.info("✅ Endpoint kept: %s", path)
                log.info("📄 %s document(s) found", len(documents))
                return documents

        log.warning(
            "⚠️  No listing endpoint worked. Inspect the browser DevTools "
            "(Network tab) after login to find the real routes."
        )
        return []

    def search_documents(
        self,
        locations: list[str] | None = None,
        folder_id: str = "",
        max_results: int | None = None,
        sort: str = "CREATION_DATE",
        direction: str = "DESC",
    ) -> list[dict[str, Any]]:
        """Targeted search (POST /documents/search)."""
        loc = locations or self.locations
        m = max_results or self.max_results
        payload: dict[str, Any] = {"locations": loc}
        if folder_id:
            payload["folder_id"] = folder_id
        url = f"{self.base_url}/documents/search?max_results={m}&sort={sort}&direction={direction}"
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        return _extract_documents(resp.json())

    @staticmethod
    def _safe_filename(doc: dict[str, Any]) -> str:
        """Build a safe file name from the document metadata."""
        title = doc.get("title") or doc.get("name") or doc.get("subtitle")
        if not isinstance(title, str) or not title:
            title = str(doc.get("id") or "document")

        extension = doc.get("extension")
        if not isinstance(extension, str):
            extension = ""
        extension = extension.lstrip(".")

        name = re.sub(r'[\\/:*?"<>|]+', "_", title).strip()
        name = re.sub(r"\s+", " ", name)
        if extension and not name.lower().endswith(f".{extension.lower()}"):
            name = f"{name}.{extension}"
        return name

    def file_name_for(self, doc: dict[str, Any]) -> str:
        """Return the on-disk file name for a document (sanitized title +
        extension). Used both to download and to locate it on disk."""
        return self._safe_filename(doc)

    def download_document(self, doc: dict[str, Any] | str, destination: Path) -> Path | None:
        """Download a document's content via
        GET /document/{id}/content (returns the raw bytes)."""
        if isinstance(doc, str):
            document_id = doc
            file_name = doc
        else:
            identifier = doc.get("id")
            document_id = str(identifier) if identifier is not None else ""
            if not document_id:
                log.error("❌ Document without id: %s", doc)
                return None
            file_name = self._safe_filename(doc)

        url = f"{self.base_url}/document/{document_id}/content"
        log.info("⬇️  Downloading “%s”…", file_name)

        try:
            resp = self.session.get(url, stream=True, timeout=120)
            resp.raise_for_status()

            destination.mkdir(parents=True, exist_ok=True)
            path = destination / file_name

            with open(path, "wb") as f:
                f.writelines(resp.iter_content(chunk_size=65536))

            log.info("✅ Saved: %s", path)
            return path

        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            log.error(
                "❌ Failed to download document %s (HTTP %s)",
                document_id,
                status,
            )
            return None

    def close(self) -> None:
        self.session.close()
