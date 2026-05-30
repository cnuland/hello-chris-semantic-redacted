#!/usr/bin/env python3
"""Load the sensitive RAG document into Qdrant for demo scenarios.

Reads src/demo/sensitive_rag_doc.md, splits it into chunks by section
headers (##), embeds each chunk using sentence-transformers, and upserts
into a Qdrant collection named "sensitive_docs".

Usage:
    python load_rag_doc.py
    python load_rag_doc.py --qdrant-url http://qdrant.example.com:6333
    python load_rag_doc.py --collection my_docs --recreate
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------


def _check_sentence_transformers() -> None:
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        print(
            "ERROR: sentence-transformers is not installed.\n"
            "Install it with:\n"
            "  pip install sentence-transformers\n"
            "or:\n"
            "  pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)


def _check_qdrant_client() -> None:
    try:
        import qdrant_client  # noqa: F401
    except ImportError:
        print(
            "ERROR: qdrant-client is not installed.\n"
            "Install it with:\n"
            "  pip install qdrant-client\n"
            "or:\n"
            "  pip install -r requirements.txt",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Markdown chunking
# ---------------------------------------------------------------------------

_RAG_DOC_PATH = Path(__file__).parent / "sensitive_rag_doc.md"

# Embedding model -- same one used by the sensitivity classifier
_MODEL_NAME = "all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 output dimension


def _read_document(path: Path) -> str:
    """Read the raw markdown document."""
    if not path.exists():
        print(f"ERROR: RAG document not found at {path}", file=sys.stderr)
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def _split_into_chunks(text: str) -> list[dict[str, str]]:
    """Split the document by ## headers into named chunks.

    Returns a list of dicts with keys: section, text.
    The document title (# heading) is included as metadata but
    not as a separate chunk.
    """
    lines = text.split("\n")
    chunks: list[dict[str, str]] = []
    current_section = "Preamble"
    current_lines: list[str] = []

    for line in lines:
        # Match ## headers (level 2) as chunk boundaries
        match = re.match(r"^##\s+(.+)$", line)
        if match:
            # Save previous chunk if it has content
            content = "\n".join(current_lines).strip()
            if content:
                chunks.append({"section": current_section, "text": content})
            current_section = match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Don't forget the last chunk
    content = "\n".join(current_lines).strip()
    if content:
        chunks.append({"section": current_section, "text": content})

    return chunks


def _chunk_id(section: str, idx: int) -> str:
    """Generate a deterministic chunk ID from section name and index."""
    raw = f"{section}:{idx}"
    return hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


def _embed_chunks(
    chunks: list[dict[str, str]], model_name: str = _MODEL_NAME
) -> list[list[float]]:
    """Embed chunk texts using sentence-transformers."""
    from sentence_transformers import SentenceTransformer

    print(f"Loading embedding model: {model_name}")
    model = SentenceTransformer(model_name)

    texts = [c["text"] for c in chunks]
    print(f"Embedding {len(texts)} chunks...")
    embeddings = model.encode(texts, show_progress_bar=True)

    return [emb.tolist() for emb in embeddings]


# ---------------------------------------------------------------------------
# Qdrant upsert
# ---------------------------------------------------------------------------


def _upsert_to_qdrant(
    chunks: list[dict[str, str]],
    embeddings: list[list[float]],
    qdrant_url: str,
    collection_name: str,
    recreate: bool = False,
    source: str = "sensitive_rag_doc.md",
) -> None:
    """Upsert embedded chunks into a Qdrant collection."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    print(f"Connecting to Qdrant at {qdrant_url}")
    client = QdrantClient(url=qdrant_url)

    # Check if collection exists
    collections = [c.name for c in client.get_collections().collections]

    if collection_name in collections:
        if recreate:
            print(f"Recreating collection '{collection_name}'")
            client.delete_collection(collection_name)
        else:
            print(f"Collection '{collection_name}' already exists (use --recreate to overwrite)")

    if collection_name not in collections or recreate:
        print(f"Creating collection '{collection_name}' (dim={_EMBEDDING_DIM})")
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=_EMBEDDING_DIM, distance=Distance.COSINE
            ),
        )

    # Build points
    points: list[PointStruct] = []
    for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        chunk_id = _chunk_id(chunk["section"], idx)
        point = PointStruct(
            id=chunk_id,
            vector=embedding,
            payload={
                "text": chunk["text"],
                "source": source,
                "section": chunk["section"],
                "sensitivity": "NEVER_EGRESS",
                "chunk_id": chunk_id,
            },
        )
        points.append(point)

    print(f"Upserting {len(points)} points into '{collection_name}'")
    client.upsert(collection_name=collection_name, points=points)
    print("Done. All chunks loaded with sensitivity=NEVER_EGRESS")

    # Print summary
    info = client.get_collection(collection_name)
    print(f"\nCollection '{collection_name}': {info.points_count} points")
    print("\nChunks loaded:")
    for idx, chunk in enumerate(chunks):
        print(f"  [{idx}] {chunk['section']}: {len(chunk['text'])} chars")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load sensitive RAG document into Qdrant"
    )
    parser.add_argument(
        "--qdrant-url",
        default="http://localhost:6333",
        help="Qdrant server URL (default: http://localhost:6333)",
    )
    parser.add_argument(
        "--collection",
        default="sensitive_docs",
        help="Qdrant collection name (default: sensitive_docs)",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and recreate the collection if it already exists",
    )
    parser.add_argument(
        "--doc-path",
        default=str(_RAG_DOC_PATH),
        help=f"Path to the RAG document (default: {_RAG_DOC_PATH})",
    )
    args = parser.parse_args()

    # Pre-flight dependency checks
    _check_sentence_transformers()
    _check_qdrant_client()

    # Read and chunk the document
    doc_path = Path(args.doc_path)
    raw_text = _read_document(doc_path)
    chunks = _split_into_chunks(raw_text)
    print(f"Split document into {len(chunks)} chunks")

    # Embed chunks
    embeddings = _embed_chunks(chunks)

    # Upsert to Qdrant
    _upsert_to_qdrant(
        chunks=chunks,
        embeddings=embeddings,
        qdrant_url=args.qdrant_url,
        collection_name=args.collection,
        recreate=args.recreate,
        source=doc_path.name,
    )


if __name__ == "__main__":
    main()
