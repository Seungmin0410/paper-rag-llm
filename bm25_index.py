import json
import re
import os


'''
BM25 검색용 전처리 - 소문자 통일, 영문/숫자/한글만 남기고 나머지는 공백 처리
'''
def tokenize_for_bm25(text: str) -> list[list]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9가-힣\s]", " ", text)
    tokens = text.split()
    return tokens


'''
청크들을 받아서 BM25용 토큰 리스트로 변환
'''
def build_bm25_entries(chunks: list[dict]) -> list[dict]:
    entries = []
    for c in chunks:
        entries.append({
            "paper_id": c["paper_id"],
            "section_id": c["section_id"],
            "chunk_index": c["chunk_index"],
            "tokens": tokenize_for_bm25(c["chunk_text"]),
        })
    return entries


'''
embedding.py의 upsert_vectors_store와 완전히 동일한 패턴으로 새로운 파일 파싱하면 기존것 덮어쓰기
'''
def load_existing_bm25(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def upsert_bm25_store(path: str, paper_id: str, new_entries: list[dict]) -> list[dict]:
    existing = load_existing_bm25(path)
    kept = [e for e in existing if e.get("paper_id") != paper_id]
    merged = kept + new_entries
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False)
    return merged
