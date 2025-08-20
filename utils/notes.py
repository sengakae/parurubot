import hashlib
import json
from pathlib import Path

import pandas as pd

from utils.chroma_client import collection


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


def load_personal_notes():
    """Load all .txt files from a 'notes' folder into the vector database"""
    notes_folder = Path("./notes")
    if not notes_folder.exists():
        print("No notes folder found - create ./notes/ and add .txt files")
        return

    print("Looking for .txt files...")
    for note_file in notes_folder.rglob("*.txt"):
        print("Found .txt:", note_file)
        try:
            fhash = file_hash(note_file)
            if already_ingested(note_file, fhash):
                print(f"Skipping {note_file} (no changes)")
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

            print(f"Loaded {relative_path} ({len(chunks)} chunks)")
        except Exception as e:
            print(f"Error loading {note_file.name}: {e}")

    print("Looking for .csv files...")
    for csv_file in notes_folder.rglob("*.csv"):
        print("Found .csv:", csv_file)
        try:
            fhash = file_hash(csv_file)
            if already_ingested(csv_file, fhash):
                print(f"Skipping {csv_file} (no changes)")
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

            print(
                f"Loaded CSV {relative_path} ({len(df)} rows → {len(grouped_chunks)} chunks)"
            )

        except Exception as e:
            print(f"Error loading CSV {csv_file}: {e}")

    print("Looking for .json files...")
    for json_file in notes_folder.rglob("*.json"):
        print("Found .json:", json_file)
        try:
            fhash = file_hash(json_file)
            if already_ingested(json_file, fhash):
                print(f"Skipping {json_file} (no changes)")
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

            print(f"Loaded JSON {relative_path} ({len(content_chunks)} entries → {len(grouped_chunks)} chunks)")
        except Exception as e:
            print(f"Error loading JSON {json_file}: {e}")


def search_personal_notes(query, n_results=3):
    """Search personal notes for relevant information"""
    try:
        results = collection.query(query_texts=[query], n_results=n_results)

        if results["documents"] and results["documents"][0]:
            relevant_info = []
            for doc, metadata in zip(results["documents"][0], results["metadatas"][0]):
                source_file = metadata["source"]
                file_type = metadata.get("type", "unknown")

                preview = doc[:400] + "..." if len(doc) > 400 else doc
                if file_type == "csv":
                    relevant_info.append(f"From CSV {source_file}:\n{preview}")
                elif file_type == "json":
                    relevant_info.append(f"From JSON {source_file}:\n{preview}")
                else:
                    relevant_info.append(f"From {source_file}: {preview}")
            return "\n\n".join(relevant_info)
        return None
    except:
        return None
