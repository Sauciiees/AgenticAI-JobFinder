"""Turns an uploaded CV (PDF or DOCX bytes) into text chunks and stores them for retrieval."""

from __future__ import annotations

import io
import zipfile

from agent import vector_store

PDF = "pdf"
DOCX = "docx"
CONTENT_TYPES = {
    PDF: "application/pdf",
    DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def detect_kind(data: bytes) -> str | None:
    """Identifies the file by its content, never by its name: PDFs start with %PDF-, DOCX files are
    ZIP archives that contain word/document.xml."""
    if data.startswith(b"%PDF-"):
        return PDF
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if "word/document.xml" in zf.namelist():
                    return DOCX
        except zipfile.BadZipFile:
            return None
    return None


def extract_text(data: bytes, kind: str) -> str:
    if kind == PDF:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page_num, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                pages.append(f"--- Page {page_num + 1} ---\n{text}")
        return "\n\n".join(pages)
    if kind == DOCX:
        import docx

        document = docx.Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells if cell.text.strip()))
        return "\n\n".join(parts)
    raise ValueError(f"Unsupported document type: {kind}")


def chunk_text(text: str, min_len: int = 20, max_len: int = 1500) -> list[str]:
    chunks: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip()
        while len(block) > max_len:
            chunks.append(block[:max_len])
            block = block[max_len:]
        if len(block) > min_len:
            chunks.append(block)
    if not chunks and text.strip():
        chunks = [text.strip()[:max_len]]
    return chunks


def parse_and_store_document(data: bytes, kind: str, user_id: int, doc_type: str = "resume") -> list[str]:
    """Extracts, chunks, embeds and stores a user's document. Returns the chunks."""
    chunks = chunk_text(extract_text(data, kind))
    vector_store.replace_user_documents(user_id, chunks, doc_type=doc_type)
    return chunks
