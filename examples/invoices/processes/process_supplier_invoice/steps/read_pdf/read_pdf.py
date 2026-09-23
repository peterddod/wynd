"""Wynd step read_pdf (deterministic)."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pypdf import PdfReader

from wynd.runtime import DeterministicStep


class ReadPdf(DeterministicStep):
    """Read a PDF file and return its text and page count."""

    class Input(BaseModel):
        pdf_path: Path

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        text: str
        pages: int

    def run(self, input: Input) -> Output:
        reader = PdfReader(input.pdf_path)
        texts = [(page.extract_text() or "").rstrip("\n") for page in reader.pages]
        return self.Output(text="\n".join(texts), pages=len(reader.pages))
