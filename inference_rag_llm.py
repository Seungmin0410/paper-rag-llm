"""
inference_rag_llm.py — 추론 최적화 파이프라인

구조:
[1단] 노하우+배경설명 파일 통째로 주입 
[중간] LLM이 쿼리 생성
[2단] 논문 DB만 검색 (벡터+BM25 하이브리드, 아직은 PPT/다른 프로젝트 노하우는 제외)
[충분성 판단] 0건이면 LLM 호출 없이 바로 '부족' / 있으면 LLM이 판단
부족하면 웹서치 폴백
[최종] 노하우(태그없음) + 논문DB(paper_id 태그) + 웹서치(태그만) + 질문 -> 최종 추론
"""

import os
import sys
import json

from vector_search import (
    load_model,
    search_top_k,
    search_bm25_top_k,
    reciprocal_rank_fusion,
    build_chunk_lookup,
    attach_context,
    dedupe_sections,
    format_tables_and_figures,
)

# Haiku : claude-haiku-4-5-20251001 / Sonnet : claude-sonnet-5 / Opus : claude-opus-4-8
MODEL = "claude-sonnet-5"

# vector_search.py의 load_model()이 SentenceTransformer를 매번 새로 로딩하면 느림
# -> 여기서 한 번만 로딩해서 재사용 (모듈 레벨 캐싱)
_embedding_model = None
def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = load_model()
    return _embedding_model


'''
rag_llm.py의 call_claude_api()와 동일한 패턴.
client를 함수 호출마다 새로 만듦 (rag_llm.py 방식 그대로 따름).
ThinkingBlock이 먼저 올 수 있어서 type == "text"인 블록만 골라 이어붙임 (rag_llm.py에서 겪은 버그 그대로 반영).
'''
def call_claude(prompt: str, model: str = MODEL, max_tokens: int = 4096) -> str:
    try:
        import anthropic  # type: ignore
    except ImportError:
        print("anthropic 패키지가 설치되어 있지 않습니다.")
        print("설치: pip install anthropic --break-system-packages")
        sys.exit(1)

    client = anthropic.Anthropic()  # 환경변수 ANTHROPIC_API_KEY 사용
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text_parts = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_parts)


# ---------------------------------------------------------------------------
# 1단: 노하우 파일 로드
# ---------------------------------------------------------------------------
def load_project_notes(notes_path: str) -> str:
    # TODO: 파일 없을 때 에러 메시지 어떻게 보여줄지
    with open(notes_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 중간 단계: 노하우 보고 검색 쿼리 생성
# ---------------------------------------------------------------------------
def generate_search_query(project_notes: str, user_question: str) -> str:
    # TODO: 쿼리 하나만 뽑을지, 여러 개 뽑을지 (지금은 하나로 시작)
    prompt = f"""아래는 우리 프로젝트의 배경 지식과 노하우입니다.

{project_notes}

사용자 질문: {user_question}

이 질문에 답하려면 논문 데이터베이스에서 어떤 정보를 검색해야 할지,
검색에 사용할 쿼리 문장 1개만 만들어주세요. 쿼리만 출력하고 다른 말은 하지 마세요."""

    return call_claude(prompt, max_tokens=200).strip()


# ---------------------------------------------------------------------------
# 2단: 논문 DB 검색 (벡터+BM25 하이브리드) - PPT/타 프로젝트 노하우 제외
# vector_search.py의 main() 로직을 함수 하나로 재사용
# ---------------------------------------------------------------------------
def hybrid_search_papers(
    query: str,
    chunks_path: str = "results/chunks.json",
    vectors_path: str = "results/vectors.json",
    sections_path: str = "results/sections_store.json",
    bm25_path: str = "results/bm25_corpus.json",
    top_k: int = 3,
    candidate_k: int = 15,
) -> list:
    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    with open(vectors_path, "r", encoding="utf-8") as f:
        vectors = json.load(f)
    with open(sections_path, "r", encoding="utf-8") as f:
        sections_store = json.load(f)
    with open(bm25_path, "r", encoding="utf-8") as f:
        bm25_entries = json.load(f)

    model = get_embedding_model()
    query_vector = model.encode([query], normalize_embeddings=True)[0].tolist()

    vector_results = search_top_k(query_vector, vectors, top_k=candidate_k)
    bm25_results = search_bm25_top_k(query, bm25_entries, top_k=candidate_k)
    fused = reciprocal_rank_fusion(vector_results, bm25_results, k=60)
    final_results = fused[:top_k]

    chunk_lookup = build_chunk_lookup(chunks)
    enriched = attach_context(final_results, chunk_lookup, sections_store)
    sections_for_llm = dedupe_sections(enriched)  # 논문 결과. PPT/타 프로젝트 노하우는 애초에 이 DB에 안 들어있음
    return sections_for_llm


# ---------------------------------------------------------------------------
# 검색된 섹션들을 최종 프롬프트에 넣을 텍스트로 변환
# 출처 태그 규칙: [논문DB: paper_id] 형식으로 각 섹션 앞에 표시
# ---------------------------------------------------------------------------
def format_paper_sections_for_prompt(sections_for_llm: list) -> str:
    blocks = []
    for s in sections_for_llm:
        block = f"[논문DB: {s['paper_id']}] {s['section_head']}\n{s['section_text']}"
        # 텍스트만 가기로 했으므로(우선은), 표/그림 캡션 텍스트는 넣되 이미지 첨부는 안 함
        tf_text = format_tables_and_figures(s["section_tables"], s["section_figures"])
        if tf_text:
            block += f"\n{tf_text}"
        blocks.append(block)
    return "\n\n---\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 충분성 판단
# ---------------------------------------------------------------------------
def is_sufficient(search_results: list, user_question: str) -> bool:
    # search_top_k / search_bm25_top_k는 항상 top_k개를 채워서 반환하므로
    # "0건"은 사실상 발생하지 않음 -> 진짜 판단은 전적으로 LLM 몫
    # (유사도가 낮아 무관한 청크가 섞여도 top_k만큼은 채워져서 나오기 때문에,
    #  "충분한가"뿐 아니라 "애초에 질문과 관련이 있는가"부터 판단하게 프롬프트에 명시)
    if len(search_results) == 0:
        return False

    results_text = format_paper_sections_for_prompt(search_results)

    prompt = f"""사용자 질문: {user_question}

검색된 자료:
{results_text}

먼저 위 자료가 질문과 실제로 관련이 있는지 판단하세요.
관련이 없다면 무조건 "아니오"로 답하세요.
관련이 있다면, 그 내용만으로 질문에 충분히 답할 수 있는지 판단하세요.

"예" 또는 "아니오"로만 답하세요."""

    answer = call_claude(prompt, max_tokens=10).strip()
    return answer.startswith("예")


# ---------------------------------------------------------------------------
# 웹서치 폴백
# ---------------------------------------------------------------------------
def web_search_fallback(user_question: str, model: str = MODEL, max_tokens: int = 2048) -> str:
    try:
        import anthropic  # type: ignore
    except ImportError:
        print("anthropic 패키지가 설치되어 있지 않습니다.")
        print("설치: pip install anthropic --break-system-packages")
        sys.exit(1)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": user_question}],
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
    )
    # 웹서치 결과도 여러 content block(텍스트+tool_use+tool_result)으로 나뉘어 올 수 있음
    # -> rag_llm.py 방식대로 type == "text"인 블록만 이어붙임
    text_parts = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_parts)


# ---------------------------------------------------------------------------
# 최종 추론 생성
# ---------------------------------------------------------------------------
def generate_final_answer(project_notes: str, paper_results: list, web_results: str, user_question: str) -> str:
    # 출처 태그 규칙: 논문DB는 [논문DB: paper_id], 웹서치는 [웹서치], 노하우는 태그 없음

    paper_section = format_paper_sections_for_prompt(paper_results) if paper_results else "(없음)"
    web_section = web_results if web_results else "(없음)"

    # TODO: 출처 태그 규칙을 규칙 문장으로 명시 (트랙 A 이미지 판독 규칙처럼)
    instructions = """
답변 작성 규칙:
- 논문 데이터베이스에서 가져온 내용은 [논문DB: paper_id] 형식으로 짧게 표시
- 웹서치에서 가져온 내용은 [웹서치] 로만 표시
- 프로젝트 노하우 자체 내용은 출처 표시 없이 자연스럽게 서술
"""

    # 프롬프트 순서 원칙(트랙 A 세션 결론과 동일): 긴 문서 먼저, 규칙은 질문 바로 앞, 질문은 맨 마지막
    prompt = f"""{project_notes}

논문DB 검색 결과:
{paper_section}

웹서치 결과:
{web_section}

{instructions}

질문: {user_question}"""

    return call_claude(prompt, max_tokens=4096)  # thinking 고려해서 rag_llm.py와 동일하게


# ---------------------------------------------------------------------------
# 전체 파이프라인 (1홉)
# ---------------------------------------------------------------------------
def run_reasoning(
    notes_path: str,
    user_question: str,
    chunks_path: str = "results/chunks.json",
    vectors_path: str = "results/vectors.json",
    sections_path: str = "results/sections_store.json",
    bm25_path: str = "results/bm25_corpus.json",
    top_k: int = 3,
    candidate_k: int = 15,
) -> str:
    project_notes = load_project_notes(notes_path)

    query = generate_search_query(project_notes, user_question)
    paper_results = hybrid_search_papers(
        query,
        chunks_path=chunks_path,
        vectors_path=vectors_path,
        sections_path=sections_path,
        bm25_path=bm25_path,
        top_k=top_k,
        candidate_k=candidate_k,
    )

    web_results = ""
    if not is_sufficient(paper_results, user_question):
        web_results = web_search_fallback(user_question)

    answer = generate_final_answer(project_notes, paper_results, web_results, user_question)
    return answer


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--notes", required=True, help="프로젝트 노하우 파일 경로")
    parser.add_argument("--query", required=True, help="질문")
    # 아래 경로 인자들은 vector_search.py 기본값과 동일하게 맞춤
    parser.add_argument("--chunks", default="results/chunks.json")
    parser.add_argument("--vectors", default="results/vectors.json")
    parser.add_argument("--sections", default="results/sections_store.json")
    parser.add_argument("--bm25", default="results/bm25_corpus.json")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--candidate-k", type=int, default=15)
    args = parser.parse_args()

    result = run_reasoning(
        args.notes,
        args.query,
        chunks_path=args.chunks,
        vectors_path=args.vectors,
        sections_path=args.sections,
        bm25_path=args.bm25,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
    )
    print(result)