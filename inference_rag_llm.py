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


'''
우선은 파일명 수동을 쳐야하지만 나중에 --"파일이름" 이런식으로 바꿔도 되고 이건 한번 생각해봐야할 문제
'''
BACKGROUND_PATH = "background.txt"
NOTES_PATH = "notes.txt"

# vector_search.py의 load_model()이 SentenceTransformer를 매번 새로 로딩하면 느림
# -> 여기서 한 번만 로딩해서 재사용 (모듈 레벨 캐싱)
_embedding_model = None
def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = load_model()
    return _embedding_model


'''
클로드 API 호출
ThinkingBlock이 먼저 올 수 있어서 type == "text"인 블록만 골라 이어붙임 -> LLM이 반환할때 바로 text가 안오는 경우 대비. 
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

'''
전체 파이프라인 시작

1단계: 우리가 가지고 있는 배경설명 파일 + 노하우 파일 로드 -> LLM에 전달
배경설명은 필수(비어있거나 파일이 없으면 종료) / 노하우는 선택(첫 실험 전엔 없어도 진행)
'''
def load_project_context(background_path: str = BACKGROUND_PATH, notes_path: str = NOTES_PATH) -> str:
    if not os.path.exists(background_path):
        print(f"!! 배경설명 파일을 찾을 수 없습니다: {background_path}")
        sys.exit(1)

    with open(background_path, "r", encoding="utf-8") as f:
        background = f.read()

    if not background.strip():
        print(f"!! 배경설명 파일이 비어 있습니다: {background_path}")
        sys.exit(1)

    notes = ""
    if notes_path and os.path.exists(notes_path):
        with open(notes_path, "r", encoding="utf-8") as f:
            notes = f.read()
    if not notes.strip():
        print("(참고: 아직 기록된 실험 노하우가 없습니다. 배경설명만으로 진행합니다.)")
        notes = "(아직 기록된 실험 노하우 없음)"

    return f"[프로젝트 배경]\n{background}\n\n[실험 노하우]\n{notes}"


'''
중간 단계: 배경설명 + 노하우 + 질문 -> LLM에 전달하고 이에 맞는 쿼리 뽑기
'''
def generate_search_query(project_notes: str, user_question: str) -> str:
    # TODO: 쿼리 하나만 뽑을지, 여러 개 뽑을지 (지금은 하나로 시작)
    prompt = f"""아래는 우리 프로젝트의 배경 지식과 노하우입니다.

{project_notes}

사용자 질문: {user_question}

이 질문에 답하려면 논문 데이터베이스에서 어떤 정보를 검색해야 할지,
검색에 사용할 쿼리 문장 1개만 만들어주세요. 쿼리만 출력하고 다른 말은 하지 마세요."""

    return call_claude(prompt, max_tokens=200).strip()


'''
2단계: 데이터 베이스에서 추론에 도움될만한 내용들을 가져옴
'''
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


'''
검색된 섹션들을 최종 프롬프트에 넣을 텍스트로 변환
'''
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


'''
데이터 베이스에서 가져온 정보들이 충분한지 판단
-> 아직은 LLM 자체 판단에 맞기지만 차후 특정 유사도 이하면 트리거 하거나 다른 방법을 생각
'''
def is_sufficient(search_results: list, user_question: str) -> bool:
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


'''
내용이 부족할시 웹서치
'''
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


'''
최종 추론 형성
'''
def generate_final_answer(project_notes: str, paper_results: list, web_results: str, user_question: str) -> str:
    # 출처 태그 규칙: 논문DB는 [논문DB: paper_id], 웹서치는 [웹서치], 노하우는 태그 없음

    paper_section = format_paper_sections_for_prompt(paper_results) if paper_results else "(없음)"
    web_section = web_results if web_results else "(없음)"

    # TODO: 출처 태그 규칙을 규칙 문장으로 명시 
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

    return call_claude(prompt, max_tokens=4096)  


# ---------------------------------------------------------------------------
# 전체 파이프라인 (1홉)
# ---------------------------------------------------------------------------
def run_reasoning(
    user_question: str,
    background_path: str = BACKGROUND_PATH,
    notes_path: str = NOTES_PATH,
    chunks_path: str = "results/chunks.json",
    vectors_path: str = "results/vectors.json",
    sections_path: str = "results/sections_store.json",
    bm25_path: str = "results/bm25_corpus.json",
    top_k: int = 3,
    candidate_k: int = 15,
) -> str:
    project_notes = load_project_context(background_path, notes_path)

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


'''
추론 실행할땐 --auto 없이 명령어 사용
-> python3 inference_rag_llm.py --query "질문 내용"

main 부분
'''
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True, help="질문")
    # 배경설명/노하우는 기본적으로 BACKGROUND_PATH/NOTES_PATH 고정 파일을 읽음.
    # 다른 파일로 테스트하고 싶을 때만 아래 두 인자로 덮어쓰면 됨
    parser.add_argument("--background", default=BACKGROUND_PATH, help=f"배경설명 파일 경로 (기본값: {BACKGROUND_PATH})")
    parser.add_argument("--notes", default=NOTES_PATH, help=f"노하우 파일 경로 (기본값: {NOTES_PATH})")
    parser.add_argument("--chunks", default="results/chunks.json")
    parser.add_argument("--vectors", default="results/vectors.json")
    parser.add_argument("--sections", default="results/sections_store.json")
    parser.add_argument("--bm25", default="results/bm25_corpus.json")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--candidate-k", type=int, default=15)
    args = parser.parse_args()

    result = run_reasoning(
        args.query,
        background_path=args.background,
        notes_path=args.notes,
        chunks_path=args.chunks,
        vectors_path=args.vectors,
        sections_path=args.sections,
        bm25_path=args.bm25,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
    )
    print(result)