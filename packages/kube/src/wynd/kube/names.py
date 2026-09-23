"""Kubernetes object names and label values (`$DRAFTS/08 §5.4`)."""

from __future__ import annotations


def k8s_name(*parts: str, max_len: int = 63) -> str:
    """DNS-1123 label of `parts` joined by '-': lowercase; [^a-z0-9-] -> '-'; collapse '-'; strip '-'.
    If longer than max_len: s[:max_len-9].rstrip('-') + '-' + sha1(s)[:8]."""
    raise NotImplementedError("PLAN §11")


def label_value(s: str) -> str:
    """[A-Za-z0-9_.-], <= 63 chars, alphanumeric at both ends; same truncation+hash rule as k8s_name."""
    raise NotImplementedError("PLAN §11")


def job_name(kind: str, job_id: str) -> str:
    """k8s_name("wynd", kind.replace("_", "-"), job_id)."""
    raise NotImplementedError("PLAN §11")


def cronjob_name(release_id: str) -> str:
    """k8s_name("wynd-trigger", release_id)."""
    raise NotImplementedError("PLAN §11")


def release_name(release_id: str) -> str:
    """k8s_name("wynd-rel", release_id)."""
    raise NotImplementedError("PLAN §11")


def secret_name_for(process_id: str) -> str:
    """k8s_name("wynd-p", process_id): the per-process Secret, shared by its releases."""
    raise NotImplementedError("PLAN §11")
