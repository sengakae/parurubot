import hashlib
import json
import logging
from pathlib import Path

import pandas as pd

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

        if results["documents"] and results["documents"][0]:
            relevant_info = []
            for doc, _ in zip(results["documents"][0], results["metadatas"][0]):
                preview = doc[:400] + "..." if len(doc) > 400 else doc
                relevant_info.append(preview)
            return "\n\n".join(relevant_info)
        return None
    except:
        return None
