"""Wynd step escalate_to_human (deterministic)."""
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class InvoiceFields(BaseModel):
    supplier: str
    invoice_number: str
    total: float
    currency: str
    due_date: date


class FieldError(BaseModel):
    field: str
    code: str
    message: str
    fixable: bool


class EscalateToHuman(DeterministicStep):
    """Write an escalation ticket for a human reviewer into the queue directory."""

    class Input(BaseModel):
        fields: InvoiceFields
        errors: list[FieldError]
        queue_dir: Path
        run_id: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        ticket_path: Path

    def run(self, input: Input) -> Output:
        queue = Path(input.queue_dir)
        queue.mkdir(parents=True, exist_ok=True)
        path = queue / (re.sub(r"[^A-Za-z0-9._-]", "_", input.run_id) + ".json")
        ticket = {
            "run_id": input.run_id,
            "fields": input.fields.model_dump(mode="json"),
            "errors": [e.model_dump() for e in input.errors],
            "reasons": [e.message for e in input.errors]
                       or [f"routed to review by the process; see `wynd trace {input.run_id}`"],
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(ticket, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return self.Output(ticket_path=path)
