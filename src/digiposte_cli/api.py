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
        rename_rules: list[tuple[str, str]] | None = None,
    ) -> None:
        self.base_url = base_url
        self.locations = locations or list(_DEFAULT_LOCATIONS)
        self.max_results = max_results
        # [download] rename_rules: regex (pattern, replacement) pairs applied to
        # the file stem of every downloaded document (see file_name_for).
        self.rename_rules = [
            (re.compile(pattern), replacement) for pattern, replacement in (rename_rules or [])
        ]

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

    def list_documents(self) -> list[dict[str, Any]]:
        """List the documents of each configured location through its own
        listing route (e.g. GET /documents/safe, /documents/inbox) and merge
        the results (deduplicated by document id). Each returned document is
        tagged with its canonical `location` (e.g. "INBOX"), which the sync
        command uses to mirror the configured locations in the download dir.

        The listing is **paginated**: the server only returns `max_results`
        documents per page (10 by default) and echoes `{count, documents,
        index, max_results}` where `count` is the location total. Every page
        is fetched by walking `index` until the declared `count` is reached,
        so a vault with more documents than the page size is fully listed
        (before this fix, only the first page was ever seen)."""
        documents: list[dict[str, Any]] = []
        seen: set[str] = set()
        for location in self.locations:
            # The API identifiers are uppercase ("SAFE", "INBOX"…); the REST
            # listing path uses their lowercase slug ("/documents/safe"…).
            index = 0
            while True:
                data = self._get(
                    f"/documents/{location.lower()}?index={index}&max_results={self.max_results}",
                    label=f"List ({location})",
                )
                found = _extract_documents(data) if data is not None else []
                total = data.get("count") if isinstance(data, dict) else None
                for doc in found:
                    doc.setdefault("location", location)
                    doc_id = doc.get("id")
                    key = str(doc_id) if doc_id is not None else repr(doc)
                    if key in seen:
                        continue
                    seen.add(key)
                    documents.append(doc)
                # Stop when the request failed, the page is empty, the
                # declared total is covered, or a short page indicates the
                # end of the list; otherwise fetch the next page.
                if data is None or not found:
                    break
                if total is not None and index + len(found) >= total:
                    break
                if total is None and len(found) < self.max_results:
                    break
                index += self.max_results
        if not documents:
            log.warning(
                "⚠️  No documents found in the configured locations (%s).",
                ", ".join(self.locations),
            )
        else:
            log.info("✅ %d document(s) found in %s", len(documents), ", ".join(self.locations))
        return documents

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
        """Return the on-disk file name for a document: the sanitized title +
        extension, then the configured [download] rename_rules applied to the
        stem. Used both to download and to locate it on disk."""
        return self._apply_rename_rules(self._safe_filename(doc))

    def _apply_rename_rules(self, file_name: str) -> str:
        """Apply the configured rename_rules to a file name, in order.

        The rules rewrite the file *stem* (the part before the last `.`
        extension), so a pattern can be anchored with `$` without having to
        account for the extension. Every occurrence matched by a rule is
        replaced."""
        stem, dot, extension = file_name.rpartition(".")
        if not dot:
            stem, dot, extension = file_name, "", ""
        for pattern, replacement in self.rename_rules:
            stem = pattern.sub(replacement, stem)
        return f"{stem}{dot}{extension}"

    def legacy_file_names_for(self, doc: dict[str, Any]) -> list[str]:
        """File names used for `doc` before the rename_rules existed (the raw
        sanitized title + extension). Older downloads used exactly this name,
        so `rename_legacy` can pick them up instead of downloading twice."""
        return [self._safe_filename(doc)]

    def rename_legacy(self, doc: dict[str, Any], destination: Path) -> Path | None:
        """Rename a file downloaded before the rename_rules to the current name.

        Returns the (new) path if such a file was found and renamed, else None.
        Lets `sync` pick up files written by older versions (before the rules
        existed) instead of downloading the same document again under a second
        name."""
        target = destination / self.file_name_for(doc)
        for legacy in self.legacy_file_names_for(doc):
            source = destination / legacy
            if source.exists():
                source.rename(target)
                log.info("🔁 Renamed: %s → %s", source.name, target.name)
                return target
        return None

    def rename_leftover(self, directory: Path) -> int:
        """Rename the files of `directory` still carrying a pre-rules name.

        Applies the rename_rules to every file name (idempotent: an already
        canonical name is unchanged) and renames the ones that change. This
        catches files whose source document is no longer listed by the vault
        (deleted, moved to trash…), so `rename_legacy` never saw them. A file
        whose target name already exists is left untouched. Returns the number
        of renames performed."""
        renamed = 0
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            target = directory / self._apply_rename_rules(path.name)
            if target == path or target.exists():
                continue
            path.rename(target)
            log.info("🔁 Renamed: %s → %s", path.name, target.name)
            renamed += 1
        return renamed

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
            file_name = self.file_name_for(doc)

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
