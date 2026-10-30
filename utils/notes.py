import hashlib
import io
import json
import logging
import re
import string
from pathlib import Path

import pandas as pd
import requests

from utils.chroma_client import collection

logger = logging.getLogger(__name__)


def file_hash(path: Path) -> str:
    """Compute md5 hash of a file (for change detection)."""
    hasher = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    return hasher.hexdigest()


def already_ingested(file_path: Path, file_hash_val: str) -> bool:
    """Check if a file with this hash is already in the DB."""
    results = collection.get(
        where={"file_hash": {"$eq": file_hash_val}},
        limit=1,
    )
    return len(results["ids"]) > 0


def cleanup_deleted_files():
    """Remove chunks from vector DB for files that no longer exist in the notes folder"""
    notes_folder = Path("./notes")
    if not notes_folder.exists():
        logger.info("Notes folder doesn't exist, skipping cleanup")
        return
    
    try:
        results = collection.get()
        
        if not results["metadatas"]:
            logger.info("No documents in vector DB to clean up")
            return
        
        current_files = set()
        for file_path in notes_folder.rglob("*"):
            if file_path.is_file() and file_path.suffix in ['.txt', '.csv', '.json']:
                relative_path = file_path.relative_to(notes_folder)
                current_files.add(str(relative_path))
        
        chunks_to_remove = []
        deleted_sources = set()
        
        for doc_id, metadata in zip(results["ids"], results["metadatas"]):
            if metadata.get("type") == "sheet":
                continue  # synced from a Google Sheet, not a file in notes/
            source = metadata.get("source", "")
            if source and source not in current_files:
                chunks_to_remove.append(doc_id)
                deleted_sources.add(source)
        
        if chunks_to_remove:
            collection.delete(ids=chunks_to_remove)
            logger.info(f"Cleanup: Removed {len(chunks_to_remove)} chunks from {len(deleted_sources)} deleted files:")
            for source in sorted(deleted_sources):
                logger.info(f"   - {source}")
        else:
            logger.info("No orphaned chunks found during cleanup")
            
    except Exception as e:
        logger.exception(f"Error during cleanup: {e}")


TEMPLATES_FOLDER = Path("templates")
TEMPLATE_KEYS = ("url", "columns", "required", "row", "fields")


def short_maps_url(url: str) -> str:
    """Drop the long /data= tail and query string; the place path and coordinates still open the place."""
    return url.split("/data=", 1)[0].split("?", 1)[0]


# named transforms a template can apply to a column, e.g. {"link": "shorten_maps_url"}
TRANSFORMS = {"shorten_maps_url": short_maps_url}

_SHEET_EDIT_LINK = re.compile(r"^(https://docs\.google\.com/spreadsheets/d/[^/]+)/edit.*$")


def csv_export_url(url: str) -> str:
    """Turn a Google Sheets share link (.../edit?usp=sharing) into its CSV export link."""
    return _SHEET_EDIT_LINK.sub(r"\1/export?format=csv", url)


def normalize_header(header: str) -> str:
    return header.lstrip("\ufeff").strip().lower().rstrip(":").strip()


def _placeholders(part: str) -> list[str]:
    return [name for _, name, _, _ in string.Formatter().parse(part) if name]


def load_template(path: Path) -> dict:
    """Read and validate one sheet template. Raises ValueError with the reason."""
    try:
        template = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"can't read {path.name}: {e}") from e
    if not isinstance(template, dict):
        raise ValueError(f"{path.name}: expected a JSON object")

    missing = [key for key in TEMPLATE_KEYS if key not in template]
    if missing:
        raise ValueError(f"{path.name}: missing {missing}")
    if not isinstance(template["url"], str) or not template["url"]:
        raise ValueError(f"{path.name}: url must be a non-empty string")
    if not template["required"]:
        raise ValueError(f"{path.name}: required must list at least one column")

    template.setdefault("transforms", {})
    keys = set(template["columns"].values())
    referenced = (
        [name for part in template["row"] for name in _placeholders(part)]
        + list(template["required"])
        + list(template["fields"])
        + list(template["transforms"])
    )
    unknown = sorted(set(referenced) - keys)
    if unknown:
        raise ValueError(f"{path.name}: {unknown} not in columns")
    bad_transforms = sorted(set(template["transforms"].values()) - set(TRANSFORMS))
    if bad_transforms:
        raise ValueError(f"{path.name}: unknown transforms {bad_transforms}")

    template["url"] = csv_export_url(template["url"])
    return template


def load_templates(folder: Path = TEMPLATES_FOLDER) -> dict[str, dict | None]:
    """All *.json templates in folder by name (file stem); None marks a file that failed to load."""
    if not folder.is_dir():
        return {}
    templates = {}
    for path in sorted(folder.glob("*.json")):
        try:
            templates[path.stem] = load_template(path)
        except ValueError:
            logger.exception("Skipping sheet template %s", path.name)
            templates[path.stem] = None
    return templates


def sheet_rows(csv_text: str, template: dict) -> list[dict[str, str]]:
    """Parse a sheet CSV into rows keyed by the template's column keys, with transforms applied.
    Rows missing a required value are dropped."""
    try:
        df = pd.read_csv(io.StringIO(csv_text.lstrip("\ufeff")), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise ValueError(f"not a CSV: {e}") from e

    columns = {}
    for col in df.columns:
        key = template["columns"].get(normalize_header(col))
        if key:
            columns[col] = key
    absent = [key for key in template["required"] if key not in columns.values()]
    if absent:
        raise ValueError(f"required columns {absent} not in {list(df.columns)}")

    rows = []
    for _, record in df.iterrows():
        row = {key: "" for key in template["columns"].values()}
        for col, key in columns.items():
            row[key] = record[col].strip()
        if not all(row[key] for key in template["required"]):
            continue
        for key, name in template.get("transforms", {}).items():
            if row[key]:
                row[key] = TRANSFORMS[name](row[key])
        rows.append(row)
    return rows


def render_row(row: dict[str, str], template: dict) -> str:
    """Fill the template's row parts; a part whose placeholders are all empty is dropped."""
    parts = []
    for part in template["row"]:
        names = _placeholders(part)
        if names and not any(row.get(name) for name in names):
            continue
        for name in names:
            # replace, not str.format, so braces inside cell values are never re-parsed
            part = part.replace("{" + name + "}", row.get(name, ""))
        parts.append(part)
    return " ".join("".join(parts).split())


def sheet_source(name: str) -> str:
    return f"sheet:{name}"


def sync_all_sheets(folder: Path = TEMPLATES_FOLDER) -> None:
    """Sync every template in folder, then drop entries of sheets whose template file is gone.

    Blocking. Never raises: an escaping exception would end the daily tasks.loop until the bot restarts.
    """
    try:
        if not folder.is_dir():
            logger.warning("Sheet templates folder %s not found, skipping sheet sync", folder)
            return
        templates = load_templates(folder)
        for name, template in templates.items():
            if template is not None:
                sync_sheet(name, template)

        # invalid-but-present templates count as present, so their old entries are kept
        present = {sheet_source(name) for name in templates}
        sheets = collection.get(where={"type": {"$eq": "sheet"}})
        removed = [
            doc_id
            for doc_id, metadata in zip(sheets["ids"], sheets["metadatas"])
            if metadata.get("source") not in present
        ]
        if removed:
            collection.delete(ids=removed)
            logger.info("Removed %s entries of deleted sheet templates", len(removed))
    except Exception:
        logger.exception("Sheet sync failed")


def sync_sheet(name: str, template: dict) -> None:
    """Reload one sheet into the vector DB if it changed. Blocking; any failure keeps the old entries.

    Never raises, so one sheet can't stop the others or the daily loop.
    """
    try:
        _sync_sheet(name, template)
    except Exception:
        logger.exception("Sheet %s sync failed, keeping existing data", name)


def _sync_sheet(name: str, template: dict) -> None:
    source = sheet_source(name)
    try:
        response = requests.get(template["url"], timeout=30)
    except requests.RequestException:
        logger.exception("Sheet %s download failed, keeping existing data", name)
        return
    if response.status_code != 200:
        logger.warning("Sheet %s download returned %s, keeping existing data", name, response.status_code)
        return

    try:
        # Google omits the charset sometimes, and requests then guesses latin-1; decode ourselves
        rows = sheet_rows(response.content.decode("utf-8-sig"), template)
    except (UnicodeDecodeError, ValueError):
        logger.exception("Sheet %s isn't a usable CSV, keeping existing data", name)
        return
    if not rows:
        logger.warning("Sheet %s has no usable rows, keeping existing data", name)
        return

    fhash = hashlib.md5(response.content).hexdigest()
    unchanged = collection.get(
        where={"$and": [{"source": {"$eq": source}}, {"file_hash": {"$eq": fhash}}]},
        limit=1,
    )
    if unchanged["ids"]:
        logger.info("Sheet %s unchanged (%s rows)", name, len(rows))
        return

    # upsert before deleting leftovers, so searches never see an empty sheet and a failed
    # upsert leaves the old rows in place
    old_ids = set(collection.get(where={"source": {"$eq": source}})["ids"])
    new_ids = [f"{name}_sheet_{i}" for i in range(len(rows))]
    collection.upsert(
        documents=[render_row(row, template) for row in rows],
        ids=new_ids,
        metadatas=[
            {
                "source": source,
                "type": "sheet",
                "file_hash": fhash,
                "chunk": i,
                **{field: row[field].lower() for field in template["fields"]},
            }
            for i, row in enumerate(rows)
        ],
    )
    stale_ids = old_ids - set(new_ids)
    if stale_ids:
        collection.delete(ids=list(stale_ids))
    logger.info("Loaded sheet %s (%s rows, removed %s stale)", name, len(rows), len(stale_ids))


def load_personal_notes():
    """Load all .txt files from a 'notes' folder into the vector database"""
    notes_folder = Path("./notes")
    if not notes_folder.exists():
        logger.info("No notes folder found - create ./notes/ and add .txt files")
        return
    
    logger.info("Checking for deleted files...")
    cleanup_deleted_files()

    logger.info("Looking for .txt files...")
    for note_file in notes_folder.rglob("*.txt"):
        logger.info("Found .txt: %s", note_file)
        try:
            fhash = file_hash(note_file)
            if already_ingested(note_file, fhash):
                logger.info(f"Skipping {note_file} (no changes)")
                continue

            with open(note_file, "r", encoding="utf-8") as file:
                content = file.read()

            chunks = [content[i : i + 1000] for i in range(0, len(content), 800)]

            relative_path = note_file.relative_to(notes_folder)
            for i, chunk in enumerate(chunks):
                doc_id = f"{note_file.stem}_{i}"
                collection.upsert(
                    documents=[chunk],
                    ids=[doc_id],
                    metadatas=[{
                        "source": str(relative_path),
                        "chunk": i,
                        "file_hash": fhash,
                        "type": "txt"
                    }],
                )

            logger.info(f"Loaded {relative_path} ({len(chunks)} chunks)")
        except Exception as e:
            logger.exception(f"Error loading {note_file.name}: {e}")

    logger.info("Looking for .csv files...")
    for csv_file in notes_folder.rglob("*.csv"):
        logger.info("Found .csv: %s", csv_file)
        try:
            fhash = file_hash(csv_file)
            if already_ingested(csv_file, fhash):
                logger.info(f"Skipping {csv_file} (no changes)")
                continue

            df = pd.read_csv(csv_file)
            content_chunks = []

            summary = f"CSV Summary - File: {csv_file.name}, Columns: {', '.join(df.columns)}, Total rows: {len(df)}"
            content_chunks.append(summary)

            for idx, row in df.iterrows():
                row_items = []
                for col, val in row.items():
                    if pd.notna(val) and str(val).strip():
                        row_items.append(f"{col}: {val}")

                if row_items:
                    row_text = f"Entry {idx + 1} - " + ", ".join(row_items)
                    content_chunks.append(row_text)

            grouped_chunks = [
                "\n".join(content_chunks[i:i+8])
                for i in range(0, len(content_chunks), 8)
            ]

            relative_path = csv_file.relative_to(notes_folder)
            for i, chunk in enumerate(grouped_chunks):
                doc_id = f"{relative_path.stem}_csv_{i}"
                collection.upsert(
                    documents=[chunk],
                    ids=[doc_id],
                    metadatas=[{
                        "source": str(relative_path),
                        "chunk": i,
                        "file_hash": fhash,
                        "type": "csv",
                        "total_rows": len(df),
                    }],
                )

            logger.info(
                f"Loaded CSV {relative_path} ({len(df)} rows → {len(grouped_chunks)} chunks)"
            )

        except Exception as e:
            logger.exception(f"Error loading CSV {csv_file}: {e}")

    logger.info("Looking for .json files...")
    for json_file in notes_folder.rglob("*.json"):
        logger.info("Found .json: %s", json_file)
        try:
            fhash = file_hash(json_file)
            if already_ingested(json_file, fhash):
                logger.info(f"Skipping {json_file} (no changes)")
                continue

            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            content_chunks = []

            if isinstance(data, dict):
                for k, v in data.items():
                    content_chunks.append(f"{k}: {v}")

            elif isinstance(data, list):
                for idx, item in enumerate(data):
                    if isinstance(item, dict):
                        row_items = [f"{k}: {v}" for k, v in item.items()]
                        content_chunks.append(f"Entry {idx+1} - " + ", ".join(row_items))
                    else:
                        content_chunks.append(f"Entry {idx+1}: {item}")

            else:
                content_chunks.append(str(data))

            grouped_chunks = [
                "\n".join(content_chunks[i:i+8])
                for i in range(0, len(content_chunks), 8)
            ]

            relative_path = json_file.relative_to(notes_folder)
            for i, chunk in enumerate(grouped_chunks):
                doc_id = f"{relative_path.stem}_json_{i}"
                collection.upsert(
                    documents=[chunk],
                    ids=[doc_id],
                    metadatas=[{
                        "source": str(relative_path),
                        "chunk": i,
                        "file_hash": fhash,
                        "type": "json",
                    }],
                )

            logger.info(f"Loaded JSON {relative_path} ({len(content_chunks)} entries → {len(grouped_chunks)} chunks)")
        except Exception as e:
            logger.exception(f"Error loading JSON {json_file}: {e}")


def search_personal_notes(query, n_results=3):
    """Search personal notes for relevant information"""
    try:
        results = collection.query(query_texts=[query], n_results=n_results)
        documents = (results.get("documents") or [[]])[0] or []
        metadatas = (results.get("metadatas") or [[]])[0] or []

        if documents:
            logger.info(
                "Vector search returned %s results",
                len(documents),
            )
            relevant_info = []
            for doc in documents:
                preview = doc[:400] + "..." if len(doc) > 400 else doc
                relevant_info.append(preview)
            return "\n\n".join(relevant_info)
        logger.info("Vector search returned no results")
        return None
    except Exception:
        logger.exception("Vector search failed")
        return None
