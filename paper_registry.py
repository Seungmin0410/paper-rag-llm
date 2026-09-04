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
from difflib import SequenceMatcher

REGISTRY_PATH_DEFAULT = "results/paper_registry.json"


class DuplicatePaperError(Exception):
    """이미 등록된 논문(파일 해시, DOI, 또는 초록 내용 일치)을 다시 추가하려고 할 때 발생"""

    def __init__(self, message: str, existing_paper_id: str, match_type: str):
        super().__init__(message)
        self.existing_paper_id = existing_paper_id
        self.match_type = match_type  # "file_hash", "doi", 또는 "abstract"


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


def _looks_like_full_doi(doi: str) -> bool:
    """Grobid가 가끔 저널 접두사만 뽑고 논문 고유 번호를 놓치는 경우가 있음
    (예: '10.1016/j.isci' — 원래는 뒤에 '.2021.102384'처럼 논문별 번호가 더 붙어야 함).
    같은 저널의 서로 다른 논문이 전부 이 잘린 접두사로만 뽑히면 DOI가 겹쳐서
    다른 논문인데도 중복으로 오인하게 됨. 진짜 DOI의 suffix(첫 '/' 뒤)에는
    거의 항상 숫자가 있으므로, 숫자가 하나도 없으면 잘린 것으로 보고 신뢰하지 않는다."""
    if "/" not in doi:
        return False
    suffix = doi.split("/", 1)[1]
    return any(ch.isdigit() for ch in suffix)


def find_by_doi(registry: dict, doi: str):
    """이미 등록된 논문 중 DOI가 같은 게 있으면 그 paper_id, 없으면 None
    (DOI가 없거나, 저널 접두사만 잘려서 뽑힌 것으로 보이면 항상 None)"""
    norm = normalize_doi(doi)
    if not norm or not _looks_like_full_doi(norm):
        return None
    for paper_id, info in registry.items():
        if normalize_doi(info.get("doi", "")) == norm:
            return paper_id
    return None


def normalize_text_for_compare(text: str) -> str:
    """공백/대소문자 차이로 다른 글처럼 보이는 것 방지 (초록 비교용)"""
    return " ".join((text or "").lower().split())


def find_by_abstract(registry: dict, abstract: str, threshold: float = 0.8):
    """DOI 체크로 못 잡는 경우(잘린 DOI, DOI 자체가 없는 논문 등)를 대비한 2차 안전장치.
    초록 본문을 정규화해서 유사도를 비교하고, 임계값 이상이면 같은 논문으로 판단한다.
    (서로 다른 두 논문이 초록까지 거의 동일할 확률은 사실상 없어서, DOI/제목보다 훨씬 믿을만한 지표)
    초록이 너무 짧으면(추출 실패 등 신뢰 못 할 상태) 비교 자체를 건너뜀."""
    norm = normalize_text_for_compare(abstract)
    if len(norm) < 50:
        return None

    best_id, best_ratio = None, 0.0
    for paper_id, info in registry.items():
        existing = normalize_text_for_compare(info.get("abstract", ""))
        if len(existing) < 50:
            continue
        ratio = SequenceMatcher(None, norm, existing).ratio()
        if ratio > best_ratio:
            best_ratio, best_id = ratio, paper_id

    return best_id if best_ratio >= threshold else None


def register_paper(registry: dict, paper_id: str, file_hash: str, doi: str, source_filename: str, abstract: str = "") -> None:
    registry[paper_id] = {
        "file_hash": file_hash,
        "doi": doi,
        "source_filename": source_filename,
        "abstract": abstract,
    }
