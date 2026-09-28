"""Wynd step save_record (deterministic)."""
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class InvoiceRecord(BaseModel):
    key: str
    supplier: str
    invoice_number: str
    total: float
    currency: str
    due_date: date


class SaveRecord(DeterministicStep):
    """Write an invoice record as JSON into the destination directory, named after its key."""

    class Input(BaseModel):
        record: InvoiceRecord
        dest: Path

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        record: InvoiceRecord
        path: Path

    def run(self, input: Input) -> Output:
        dest = Path(input.dest)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / (re.sub(r"[^A-Za-z0-9._-]", "_", input.record.key) + ".json")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(input.record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return self.Output(record=input.record, path=path)
