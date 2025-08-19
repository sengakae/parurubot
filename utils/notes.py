from pathlib import Path

import pandas as pd

from utils.chroma_client import collection


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
            with open(note_file, "r", encoding="utf-8") as file:
                content = file.read()

            chunks = [content[i : i + 1000] for i in range(0, len(content), 800)]

            for i, chunk in enumerate(chunks):
                relative_path = note_file.relative_to(notes_folder)
                doc_id = f"{note_file.stem}_{i}"
                collection.upsert(
                    documents=[chunk],
                    ids=[doc_id],
                    metadatas=[{"source": str(relative_path), "chunk": i}],
                )
            print(f"Loaded {relative_path} ({len(chunks)} chunks)")
        except Exception as e:
            print(f"Error loading {note_file.name}: {e}")

    print("Looking for .csv files...")
    for csv_file in notes_folder.rglob("*.csv"):
        print("Found .csv:", csv_file)
        try:
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

            grouped_chunks = []
            for i in range(0, len(content_chunks), 8):
                chunk = "\n".join(content_chunks[i : i + 8])
                grouped_chunks.append(chunk)

            relative_path = csv_file.relative_to(notes_folder)
            for i, chunk in enumerate(grouped_chunks):
                doc_id = f"{relative_path.stem}_csv_{i}"
                collection.upsert(
                    documents=[chunk],
                    ids=[doc_id],
                    metadatas=[
                        {
                            "source": str(relative_path),
                            "type": "csv",
                            "chunk": i,
                            "total_rows": len(df),
                        }
                    ],
                )

            print(
                f"Loaded CSV {relative_path} ({len(df)} rows → {len(grouped_chunks)} chunks)"
            )

        except Exception as e:
            print(f"Error loading CSV {csv_file}: {e}")


def search_personal_notes(query, n_results=3):
    """Search personal notes for relevant information"""
    try:
        results = collection.query(query_texts=[query], n_results=n_results)

        if results["documents"] and results["documents"][0]:
            relevant_info = []
            for doc, metadata in zip(results["documents"][0], results["metadatas"][0]):
                source_file = metadata["source"]
                file_type = metadata.get("type", "unknown")

                if file_type == "csv":
                    preview = doc[:400] + "..." if len(doc) > 400 else doc
                    relevant_info.append(f"From CSV {source_file}:\n{preview}")
                else:
                    preview = doc[:300] + "..." if len(doc) > 300 else doc
                    relevant_info.append(f"From {source_file}: {preview}")
            return "\n\n".join(relevant_info)
        return None
    except:
        return None
