import json
import argparse
import sys
from bm25_index import tokenize_for_bm25

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
BM25 검색 -> bm25_corpus.json에 저장된 토큰들로 BM250kapi 인덱스를 즉석에서 만들고, 질문도 똑같이 토큰화해서 점수 매김
'''
def search_bm25_top_k(query: str, bm25_entries: list[dict], top_k: int = 3) -> list[dict]:
    try:
        from rank_bm25 import BM25Okapi #type: ignore
    except ImportError:
        print("rank_bm25가 설치되어 있지 않습니다.")
        print("설치: pip install rank_bm25 --break-system-packages")
        sys.exit(1)

    corpus_tokens = [e["tokens"] for e in bm25_entries]
    bm25 = BM25Okapi(corpus_tokens)

    query_tokens = tokenize_for_bm25(query)   ## bm25_index.py와 동일한 전처리 (소문자 통일 등)
    scores = bm25.get_scores(query_tokens)

    scored = []
    for entry, score in zip(bm25_entries, scores):
        scored.append({
            "paper_id": entry["paper_id"],
            "section_id": entry["section_id"],
            "chunk_index": entry["chunk_index"],
            "score": float(score),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


'''
RRF(Reciprocal Rank Funsion) -> 여러 개의 순위 리스트(벡터 검색, BM25 검색, 쿼리별로 각각 등)를 합침,
점수를 산정하는 방식이 벡터 유사도와 BM25 점수가 다르기 때문에 순위를 기반으로 산정.
*rank_lists로 받기 때문에 예전처럼 reciprocal_rank_fusion(vector_results, bm25_results, k=60)로
2개만 넘겨도 그대로 동작하고, 쿼리가 여러 개라 순위 리스트가 더 많아져도 그대로 넘기면 됨.
'''
def reciprocal_rank_fusion(*rank_lists: list[dict], k: int = 60) -> list[dict]:
    rrf_scores = {}   ## key: (paper_id, section_id, chunk_index) -> 누적 RRF 점수

    for rank_list in rank_lists:
        for rank, item in enumerate(rank_list):
            key = (item["paper_id"], item["section_id"], item["chunk_index"])
            rrf_scores[key] = rrf_scores.get(key, 0) + 1 / (k + rank + 1)

    fused = []
    for (paper_id, section_id, chunk_index), score in rrf_scores.items():
        fused.append({
            "paper_id": paper_id,
            "section_id": section_id,
            "chunk_index": chunk_index,
            "score": score,
        })

    fused.sort(key=lambda x: x["score"], reverse=True)
    return fused


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
            "section_tables": section_data.get("tables", []),      # 내가 추가: sections_store에 이미 저장돼있던 표 데이터를 여기서도 꺼내오기
            "section_figures": section_data.get("figures", []),    # 내가 추가: 그림 데이터도 동일하게 꺼내오기 (안 꺼내면 dedupe_sections에서 사라짐)
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
                "section_tables": r["section_tables"],      # 내가 추가: attach_context에서 넣어준 표 데이터를 여기서도 이어받기
                "section_figures": r["section_figures"],    # 내가 추가: 그림 데이터도 이어받기
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
내가 추가: 섹션에 딸린 표/그림을 LLM 프롬프트에 넣을 수 있는 문자열로 변환
표는 caption+markdown 그대로, 그림은 caption 있는 것만 (build_figure_chunks랑 동일한 원칙)
'''
def format_tables_and_figures(tables: list[dict], figures: list[dict]) -> str:
    parts = []

    for table in tables:
        caption = table.get("caption") or ""
        md = table.get("markdown", "")
        if caption and md.strip().startswith(caption.strip()):
            parts.append(md.strip())
        else:
            parts.append(f"{caption}\n\n{md}".strip())

    for fig in figures:
        caption = fig.get("caption")
        if caption:   ## caption 없는 그림(로고 등 추정)은 프롬프트에 안 넣음
            parts.append(caption)

    return "\n\n".join(p for p in parts if p)


'''
추가: 그림 리스트에서 실제 이미지 파일 경로만 뽑아온다.
parse_docling.py가 캡션 있는 그림에 한해 image_path를 채워뒀으므로,
그 필드가 있는 것만 모으면 됨 (캡션 없어서 애초에 저장 안 된 그림은 자연히 빠짐).
이 리스트가 최종적으로 rag_llm.py에서 Claude Vision API에 첨부할 이미지가 된다.
'''
def collect_image_paths(figures: list[dict]) -> list[str]:
    paths = []
    for fig in figures:
        path = fig.get("image_path")
        if path:
            paths.append(path)
    return paths


'''
args -> 프로그램 시작할때 사용자가 입력한 옵션값들을 담아놓는 보관함
'''
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default="results/chunks.json", help="chunk_sections.py가 만든 chunks.json 경로")
    ap.add_argument("--vectors", default="results/vectors.json", help="embedding.py가 만든 vectors.json 경로")
    ap.add_argument("--sections", default="results/sections_store.json", help="chunk_sections.py가 만든 sections_store.json 경로")
    ap.add_argument("--bm25", default="results/bm25_corpus.json", help="bm25_index.py가 만든 bm25_corpus.json 경로")   ## ★ 추가
    ap.add_argument("--query", required=True, help="사용자 질문")
    ap.add_argument("--top-k", type=int, default=3, help="검색할 청크 개수 (기본값: 3)")
    ap.add_argument("--candidate-k", type=int, default=15,   
                     help="벡터/BM25 각각에서 후보로 뽑을 개수 (기본값: 15) -> 이후 RRF로 합쳐서 --top-k개로 압축")
    ap.add_argument("--no-hybrid", action="store_true",   
                     help="벡터 검색만 쓰고 BM25는 끄기 (기존 방식과 비교용)")
    
    args = ap.parse_args()

    with open(args.chunks, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    with open(args.vectors, "r", encoding="utf-8") as f:
        vectors = json.load(f)
    with open(args.sections, "r", encoding="utf-8") as f:
        sections_store = json.load(f)

    model = load_model()
    query_vector = model.encode([args.query], normalize_embeddings=True)[0].tolist()
    vector_results = search_top_k(query_vector, vectors, top_k=args.candidate_k)

    if args.no_hybrid:
        final_results = vector_results[:args.top_k]
        print("(--no-hybrid 지정됨: 벡터 검색만 사용)")
    else:
        with open(args.bm25, "r", encoding="utf-8") as f:
            bm25_entries = json.load(f)
        bm25_results = search_bm25_top_k(args.query, bm25_entries, top_k=args.candidate_k)
        fused = reciprocal_rank_fusion(vector_results, bm25_results, k=60)
        final_results = fused[:args.top_k]

    chunk_lookup = build_chunk_lookup(chunks)
    enriched = attach_context(final_results, chunk_lookup, sections_store)
    sections_for_llm = dedupe_sections(enriched)

    # --- 확인용 출력 ---
    print("=" * 70)
    print(f"질문: {args.query}")
    print(f"검색 대상: 전체 {len(vectors)}개 청크")
    print(f"검색된 청크: {len(enriched)}개 -> {len(sections_for_llm)}개 고유 섹션으로 압축")
    print("=" * 70)

    for r in enriched:
        print(f"\n[점수 {r['score']:.4f}] ({r['paper_id']}) {r['section_head']} (청크 #{r['chunk_index']})")
        print(f"  {r['chunk_text'][:150]}...")

    print("\n" + "=" * 70)
    print("LLM에게 전달할 섹션 원문 (중복 제거됨, 아래를 복사해서 사용):")
    print("=" * 70)
    for s in sections_for_llm:
        print(f"\n### [{s['paper_id']}] {s['section_head']}")
        print(s['section_text'])
        # 내가 추가: 표/그림도 잘리지 않고 전체 내용 그대로 출력 (복붙용)
        tf_text = format_tables_and_figures(s["section_tables"], s["section_figures"])
        if tf_text:
            print(f"\n{tf_text}")


if __name__ == "__main__":
    main()