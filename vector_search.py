import json
import argparse
import sys

MODEL_NAME = "BAAI/bge-m3"   ## embedding.py랑 반드시 같은 모델 써야함 (다르면 벡터 비교 불가능)


'''
처음에 모델 불러오기 (embedding.py랑 똑같음)
'''
def load_model():
    try:
        from sentence_transformers import SentenceTransformer # type: ignore
    except ImportError:
        print("sentence-transformers가 설치되어 있지 않습니다.")
        print("설치: pip install sentence-transformers --break-system-packages")
        sys.exit(1)

    print(f"임베딩 모델 로딩 중: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    return model


'''
코사인 유사도 계산
embedding.py에서 normalize_embeddings=True로 이미 정규화 해놨기 때문에
그냥 내적(dot product)만 하면 코사인 유사도랑 똑같은 값이 나옴 -> 계산 훨씬 간단하고 빠름
'''
def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    return sum(a * b for a, b in zip(vec_a, vec_b))


'''
질문 벡터랑 저장된 모든 벡터를 비교해서 유사도 top-k개 뽑기 -> 현재는 3개로 설정함
'''
def search_top_k(query_vector, vectors: list[dict], top_k: int = 3) -> list[dict]:
    scored = []
    for item in vectors:
        score = cosine_similarity(query_vector, item["embedding"])
        scored.append({
            "paper_id": item["paper_id"],
            "section_id": item["section_id"],
            "chunk_index": item["chunk_index"],
            "score": score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)   ## 유사도 높은 순으로 정렬
    return scored[:top_k]


'''
(paper_id, section_id, chunk_index) -> chunk_text 로 빠르게 찾을 수 있게 딕셔너리 미리 만들어둠
'''
def build_chunk_lookup(chunks: list[dict]) -> dict:
    lookup = {}
    for c in chunks:
        key = (c["paper_id"], c["section_id"], c["chunk_index"])
        lookup[key] = c["chunk_text"]
    return lookup


'''
top-k 결과에 청크 원문(자식)이랑 섹션 전체 원문(부모)을 붙여줌
'''
def attach_context(top_results: list[dict], chunk_lookup: dict, sections_store: dict) -> list[dict]:
    enriched = []
    for r in top_results:
        chunk_key = (r["paper_id"], r["section_id"], r["chunk_index"])
        section_key = f"{r['paper_id']}::{r['section_id']}"   ## chunk_sections.py에서 이 형태로 저장했음
        section_data = sections_store.get(section_key, {})

        enriched.append({
            **r,
            "chunk_text": chunk_lookup.get(chunk_key, "(청크 원문을 찾지 못함)"),
            "section_head": section_data.get("head", "(제목없음)"),
            "section_text": section_data.get("text", "(섹션 원문을 찾지 못함)"),
            "paper_title": section_data.get("paper_title", ""),
        })
    return enriched


'''
top-k 청크 중에 같은 섹션을 가리키는 게 여러개면 중복 없이 하나로 합침
(LLM한테 같은 섹션 원문을 두 번 보낼 필요 없으니까)
'''
def dedupe_sections(enriched_results: list[dict]) -> list[dict]:
    by_section = {}
    for r in enriched_results:
        key = (r["paper_id"], r["section_id"])
        if key not in by_section:
            by_section[key] = {
                "paper_id": r["paper_id"],
                "paper_title": r["paper_title"],
                "section_id": r["section_id"],
                "section_head": r["section_head"],
                "section_text": r["section_text"],
                "best_score": r["score"],
                "matched_chunks": [],
            }
        by_section[key]["matched_chunks"].append({
            "chunk_index": r["chunk_index"],
            "chunk_text": r["chunk_text"],
            "score": r["score"],
        })
        by_section[key]["best_score"] = max(by_section[key]["best_score"], r["score"])

    sections = list(by_section.values())
    sections.sort(key=lambda x: x["best_score"], reverse=True)
    return sections


'''
args -> 프로그램 시작할때 사용자가 입력한 옵션값들을 담아놓는 보관함
'''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default="results/chunks.json", help="chunk_sections.py가 만든 chunks.json 경로")
    ap.add_argument("--vectors", default="results/vectors.json", help="embedding.py가 만든 vectors.json 경로")
    ap.add_argument("--sections", default="results/sections_store.json", help="chunk_sections.py가 만든 sections_store.json 경로")
    ap.add_argument("--query", required=True, help="사용자 질문")
    ap.add_argument("--top-k", type=int, default=3, help="검색할 청크 개수 (기본값: 3)")
    args = ap.parse_args()

    '''
    실제 임베딩된 데이터를 가져옴
    '''
    with open(args.chunks, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    with open(args.vectors, "r", encoding="utf-8") as f:
        vectors = json.load(f)
    with open(args.sections, "r", encoding="utf-8") as f:
        sections_store = json.load(f)

    '''
    실제 데이터들로 작업 수행
    '''
    model = load_model()
    query_vector = model.encode([args.query], normalize_embeddings=True)[0].tolist()
    top_results = search_top_k(query_vector, vectors, top_k=args.top_k)
    chunk_lookup = build_chunk_lookup(chunks)
    enriched = attach_context(top_results, chunk_lookup, sections_store)
    sections_for_llm = dedupe_sections(enriched)

    # --- 확인용 출력 ---
    print("=" * 70)
    print(f"질문: {args.query}")
    print(f"검색 대상: 전체 {len(vectors)}개 청크")
    print(f"검색된 청크: {len(enriched)}개 -> {len(sections_for_llm)}개 고유 섹션으로 압축")
    print("=" * 70)

    for r in enriched:
        print(f"\n[유사도 {r['score']:.4f}] ({r['paper_id']}) {r['section_head']} (청크 #{r['chunk_index']})")
        print(f"  {r['chunk_text'][:150]}...")

    print("\n" + "=" * 70)
    print("LLM에게 전달할 섹션 원문 (중복 제거됨):")
    print("=" * 70)
    for s in sections_for_llm:
        print(f"\n### [{s['paper_id']}] {s['section_head']} (최고 유사도 {s['best_score']:.4f})")
        print(f"섹션 길이: {len(s['section_text'])}자")


if __name__ == "__main__":
    main()