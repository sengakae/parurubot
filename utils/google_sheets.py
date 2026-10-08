import hashlib
import json
import logging
import os
from urllib.parse import quote

import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import secretmanager
from google.oauth2 import service_account

from utils.chroma_client import collection

logger = logging.getLogger(__name__)

SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


def _load_sheet_credentials():
    secret_id = os.getenv("GOOGLE_SHEETS_SECRET_ID")
    if not secret_id:
        raise RuntimeError("GOOGLE_SHEETS_SECRET_ID is not configured")

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")
    if not project_id:
        _, project_id = google.auth.default()
    if not project_id:
        raise RuntimeError("Could not determine the Google Cloud project for Secret Manager")

    secret_name = (
        f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    )
    secret_client = secretmanager.SecretManagerServiceClient()
    response = secret_client.access_secret_version(request={"name": secret_name})
    service_account_info = json.loads(response.payload.data.decode("utf-8"))
    return service_account.Credentials.from_service_account_info(
        service_account_info, scopes=[SHEETS_READONLY_SCOPE]
    )


def load_google_sheet():
    """Replace the configured Google Sheets range in the vector collection."""
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_ID")
    sheet_range = os.getenv("GOOGLE_SHEETS_RANGE", "Sheet1!A:Z")

    if not spreadsheet_id:
        logger.info("Google Sheets ingestion is not configured; skipping")
        return
    if not os.getenv("GOOGLE_SHEETS_SECRET_ID"):
        logger.warning("GOOGLE_SHEETS_ID is set but GOOGLE_SHEETS_SECRET_ID is not configured")
        return

    try:
        logger.info("Fetching Google Sheet range %s", sheet_range)
        credentials = _load_sheet_credentials()
        session = AuthorizedSession(credentials)
        encoded_range = quote(sheet_range, safe="")
        url = (
            "https://sheets.googleapis.com/v4/spreadsheets/"
            f"{quote(spreadsheet_id, safe='')}/values/{encoded_range}"
        )
        response = session.get(url, timeout=30)
        response.raise_for_status()
        values = response.json().get("values", [])
    except Exception:
        logger.exception("Could not read configured Google Sheet")
        return

    source = f"google_sheet:{spreadsheet_id}"
    try:
        existing_ids = collection.get(where={"source": {"$eq": source}})["ids"]

        if not values:
            if existing_ids:
                collection.delete(ids=existing_ids)
            logger.info("Google Sheet range is empty: %s", sheet_range)
            return

        headers = [
            str(value).strip() or f"Column {index + 1}"
            for index, value in enumerate(values[0])
        ]
        documents = []
        ids = []
        metadatas = []
        source_id = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

        for row_number, row in enumerate(values[1:], start=2):
            fields = []
            for index, value in enumerate(row):
                if value is None or not str(value).strip():
                    continue
                header = headers[index] if index < len(headers) else f"Column {index + 1}"
                fields.append(f"{header}: {value}")
            if not fields:
                continue

            documents.append("\n".join(fields))
            ids.append(f"sheet_{source_id}_{row_number}")
            metadatas.append(
                {
                    "source": source,
                    "type": "google_sheet",
                    "range": sheet_range,
                    "row": row_number,
                }
            )

        for start in range(0, len(documents), 100):
            end = start + 100
            collection.upsert(
                documents=documents[start:end],
                ids=ids[start:end],
                metadatas=metadatas[start:end],
            )

        current_ids = set(ids)
        stale_ids = [doc_id for doc_id in existing_ids if doc_id not in current_ids]
        if stale_ids:
            collection.delete(ids=stale_ids)

        logger.info(
            "Indexed %s rows from Google Sheet range %s", len(documents), sheet_range
        )
    except Exception:
        logger.exception("Could not update Google Sheet vectors")