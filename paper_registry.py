"""
paper_registry.py — 중복 논문 감지

같은 논문이 다른 파일명으로 두 번 들어오는 걸 막기 위해,
results/paper_registry.json에 논문마다 (파일 해시, DOI)를 기록해두고
새 논문이 들어올 때마다 대조함.

1차 체크(파일 해시): 완전히 동일한 PDF 파일을 다시 올리는 흔한 실수를 빠르게 잡음.
2차 체크(DOI): 파일명/판본이 달라도 실제로는 같은 논문인 경우를 잡음
              (Grobid가 메타데이터를 파싱한 뒤에만 알 수 있음).
"""

import os
import json
import hashlib

REGISTRY_PATH_DEFAULT = "results/paper_registry.json"


class DuplicatePaperError(Exception):
    """이미 등록된 논문(파일 해시 또는 DOI 일치)을 다시 추가하려고 할 때 발생"""

    def __init__(self, message: str, existing_paper_id: str, match_type: str):
        super().__init__(message)
        self.existing_paper_id = existing_paper_id
        self.match_type = match_type  # "file_hash" 또는 "doi"


def compute_file_hash(pdf_path: str) -> str:
    """PDF 파일 내용을 통째로 해싱 (완전히 동일한 파일 재업로드 감지용)"""
    hasher = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def normalize_doi(doi: str) -> str:
    """대소문자, 공백, 'https://doi.org/' 같은 접두어 차이로 다른 논문처럼 보이는 것 방지"""
    if not doi:
        return ""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi.strip()


def load_registry(registry_path: str = REGISTRY_PATH_DEFAULT) -> dict:
    if not os.path.exists(registry_path):
        return {}
    with open(registry_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_registry(registry: dict, registry_path: str = REGISTRY_PATH_DEFAULT) -> None:
    os.makedirs(os.path.dirname(registry_path) or ".", exist_ok=True)
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)


def find_by_file_hash(registry: dict, file_hash: str):
    """이미 등록된 논문 중 파일 해시가 완전히 같은 게 있으면 그 paper_id, 없으면 None"""
    for paper_id, info in registry.items():
        if info.get("file_hash") == file_hash:
            return paper_id
    return None


def find_by_doi(registry: dict, doi: str):
    """이미 등록된 논문 중 DOI가 같은 게 있으면 그 paper_id, 없으면 None (DOI 없으면 항상 None)"""
    norm = normalize_doi(doi)
    if not norm:
        return None
    for paper_id, info in registry.items():
        if normalize_doi(info.get("doi", "")) == norm:
            return paper_id
    return None


def register_paper(registry: dict, paper_id: str, file_hash: str, doi: str, source_filename: str) -> None:
    registry[paper_id] = {
        "file_hash": file_hash,
        "doi": doi,
        "source_filename": source_filename,
    }
