"""
inference_rag_llm.py — 추론 최적화 파이프라인

구조:
[1단] 노하우+배경설명 파일 통째로 주입
[중간] LLM이 서로 다른 관점의 검색 쿼리 여러 개 생성 (가벼운 모델)
[2단] 논문 DB만 검색 (쿼리별로 벡터+BM25 하이브리드 후 전체 RRF로 병합, 아직은 PPT/다른 프로젝트 노하우는 제외)
[충분성 판단] 0건이면 LLM 호출 없이 바로 '부족' / 있으면 LLM이 판단 (가벼운 모델)
부족하면 웹서치 폴백
[최종] 노하우(태그없음) + 논문DB(paper_id 태그, 관련 이미지 첨부) + 웹서치(태그만) + 질문 -> 최종 추론
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
from rag_llm import build_image_manifest, build_image_block

# Haiku : claude-haiku-4-5-20251001 / Sonnet : claude-sonnet-5 / Opus : claude-opus-4-8
MODEL = "claude-sonnet-5"                  # 최종 추론·웹서치 종합용 (품질이 중요한 단계)
FAST_MODEL = "claude-haiku-4-5-20251001"   # 쿼리 생성·충분성 판단용 (단순 분류/생성이라 가벼운 모델로 충분)


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
call_claude()랑 거의 동일하지만, 그림(이미지)을 같이 첨부해야 할 때 씀.
content가 순수 문자열이 아니라 [이미지 블록들 + 텍스트 블록] 리스트로 감
(Anthropic 권장대로 이미지를 텍스트보다 앞에 배치 - rag_llm.py의 call_claude_api()와 동일한 방식).
'''
def call_claude_with_images(prompt: str, image_paths: list = None, model: str = MODEL, max_tokens: int = 4096) -> str:
    try:
        import anthropic  # type: ignore
    except ImportError:
        print("anthropic 패키지가 설치되어 있지 않습니다.")
        print("설치: pip install anthropic --break-system-packages")
        sys.exit(1)

    content = []
    for path in (image_paths or []):
        block = build_image_block(path)
        if block:
            content.append(block)
    content.append({"type": "text", "text": prompt})

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
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
def generate_search_query(project_notes: str, user_question: str, n: int = 2) -> list:
    prompt = f"""아래는 우리 프로젝트의 배경 지식과 노하우입니다.

{project_notes}

사용자 질문: {user_question}

이 질문에 답하려면 논문 데이터베이스에서 어떤 정보를 검색해야 할지,
서로 다른 관점의 검색 쿼리 {n}개를 만들어주세요.
한 줄에 쿼리 문장 하나씩만 출력하고, 번호나 다른 말은 절대 붙이지 마세요."""

    raw = call_claude(prompt, model=FAST_MODEL, max_tokens=200).strip()
    queries = [line.strip("-*0123456789.) ").strip() for line in raw.splitlines() if line.strip()]
    return queries[:n] if queries else [user_question]


'''
2단계: 데이터 베이스에서 추론에 도움될만한 내용들을 가져옴

pinned_paper_id: 사용자가 채팅 UI에서 "이 논문을 참고해줘"라고 지정한 논문 (없으면 None, 기존과 동일하게 동작)
pin_mode:
  - "broad" (우선참고): 전체 풀에서 일반 검색(top_k-2개) + 지정 논문 안에서만 따로 검색(2개)을 순위 무관하게 합침
  - "narrow" (이것만): 전체 풀 검색은 아예 안 하고, 지정 논문 안에서만 검색해서 top_k개를 채움
'''
def hybrid_search_papers(
    queries,
    chunks_path: str = "results/chunks.json",
    vectors_path: str = "results/vectors.json",
    sections_path: str = "results/sections_store.json",
    bm25_path: str = "results/bm25_corpus.json",
    top_k: int = 5,
    candidate_k: int = 15,
    pinned_paper_id: str = None,
    pin_mode: str = "broad",
) -> list:
    # 쿼리를 문자열 하나로 줘도, 리스트로 여러 개 줘도 둘 다 동작하게
    if isinstance(queries, str):
        queries = [queries]

    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    with open(vectors_path, "r", encoding="utf-8") as f:
        vectors = json.load(f)
    with open(sections_path, "r", encoding="utf-8") as f:
        sections_store = json.load(f)
    with open(bm25_path, "r", encoding="utf-8") as f:
        bm25_entries = json.load(f)

    model = get_embedding_model()
    chunk_lookup = build_chunk_lookup(chunks)

    # 주어진 벡터/BM25 풀(vectors_pool, bm25_pool)에서 쿼리별로 검색해서 RRF로 합친 뒤 top-k개 반환
    # (풀을 전체로 주면 기존 방식, 특정 paper_id만 걸러서 주면 그 논문 안에서만 검색하는 셈)
    def search_pool(vectors_pool, bm25_pool, k):
        if not vectors_pool and not bm25_pool:
            return []
        rank_lists = []
        for q in queries:
            if vectors_pool:
                query_vector = model.encode([q], normalize_embeddings=True)[0].tolist()
                rank_lists.append(search_top_k(query_vector, vectors_pool, top_k=candidate_k))
            if bm25_pool:
                rank_lists.append(search_bm25_top_k(q, bm25_pool, top_k=candidate_k))
        if not rank_lists:
            return []
        fused = reciprocal_rank_fusion(*rank_lists, k=60)
        return fused[:k]

    def to_sections(results, is_pinned=False):
        enriched = attach_context(results, chunk_lookup, sections_store)
        sections = dedupe_sections(enriched)
        if is_pinned:
            for s in sections:
                s["is_pinned"] = True
        return sections

    if pinned_paper_id:
        pinned_vectors = [v for v in vectors if v["paper_id"] == pinned_paper_id]
        pinned_bm25 = [b for b in bm25_entries if b["paper_id"] == pinned_paper_id]

        if pin_mode == "narrow":
            # 이것만: 전체 풀 검색 없이, 지정 논문 안에서만 top_k개
            pinned_results = search_pool(pinned_vectors, pinned_bm25, top_k)
            return to_sections(pinned_results, is_pinned=True)

        # broad(우선참고): 일반 검색 자리를 top_k-2로 줄이고, 지정 논문 몫 2개를 강제로 더함
        general_k = max(top_k - 2, 1)
        general_results = search_pool(vectors, bm25_entries, general_k)
        pinned_results = search_pool(pinned_vectors, pinned_bm25, 2)

        sections_general = to_sections(general_results)
        sections_pinned = to_sections(pinned_results, is_pinned=True)

        # 일반 검색에서도 지정 논문의 같은 섹션이 우연히 뽑혔으면 중복 방지 (지정 쪽을 우선시)
        pinned_keys = {(s["paper_id"], s["section_id"]) for s in sections_pinned}
        sections_general = [s for s in sections_general if (s["paper_id"], s["section_id"]) not in pinned_keys]

        return sections_pinned + sections_general

    # 지정 논문 없음 -> 기존과 동일하게 전체 풀에서 top_k개
    final_results = search_pool(vectors, bm25_entries, top_k)
    return to_sections(final_results)  # 논문 결과. PPT/타 프로젝트 노하우는 애초에 이 DB에 안 들어있음


'''
검색된 섹션들을 최종 프롬프트에 넣을 텍스트로 변환
'''
def format_paper_sections_for_prompt(sections_for_llm: list) -> str:
    blocks = []
    for s in sections_for_llm:
        tag = "지정논문" if s.get("is_pinned") else "논문DB"
        block = f"[{tag}: {s['paper_id']}] {s['section_head']}\n{s['section_text']}"
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

    # 프롬프트 순서 원칙(generate_final_answer와 동일): 긴 문서 먼저, 규칙은 질문 바로 앞, 질문은 맨 마지막
    prompt = f"""검색된 자료:
{results_text}

먼저 위 자료가 아래 질문과 실제로 관련이 있는지 판단하세요.
관련이 없다면 무조건 "아니오"로 답하세요.
관련이 있다면, 그 내용만으로 질문에 충분히 답할 수 있는지 판단하세요.

"예" 또는 "아니오"로만 답하세요.

질문: {user_question}"""

    answer = call_claude(prompt, model=FAST_MODEL, max_tokens=10).strip()
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

    # 맨 앞에서 역할/과제를 먼저 규정 -> 뒤에 나오는 긴 자료들을 "종합해서 추천할 재료"로 읽게 하려는 목적
    role_framing = """당신은 BeyondCaptur의 R&D 자문 역할입니다. 지금부터 제공되는 자료(우리 프로젝트의
배경과 노하우, 논문 데이터베이스 검색 결과, 웹서치 결과)는 전부 아래 질문에 대해
우리 상황에 맞는 다음 실험 방향을 추론하기 위한 재료입니다. 단순히 자료를 요약하는
것이 아니라, 이 재료들을 종합해서 우리에게 실제로 도움이 될 구체적인 판단과 추천을
만들어내는 것이 당신의 역할입니다."""

    instructions = """
답변 작성 규칙:
- 논문 데이터베이스에서 가져온 내용은 [논문DB: paper_id] 형식으로 짧게 표시
- [지정논문: paper_id]로 표시된 내용은 사용자가 직접 지정한 논문에서 가져온 것이므로,
  다른 논문DB/웹서치 내용보다 우선적으로 참고하고 답변의 중심 근거로 삼을 것
- 웹서치에서 가져온 내용은 [웹서치] 로만 표시
- 프로젝트 노하우 자체 내용은 출처 표시 없이 자연스럽게 서술
- 자료를 단순히 나열하지 말고, 위 배경설명에 적힌 우리의 목표와 제약조건에 비춰서
  어떤 내용이 우리 상황에 실제로 적용 가능한지, 어떤 내용은 맞지 않는지 판단해서 서술할 것
- 가능하다면 다음에 시도해볼 만한 구체적인 실험 방향이나 접근법을 제안할 것
  (일반적인 조언이 아니라, 우리 상황에 맞춘 구체적인 제안일 것)
- 근거가 얇거나 자료와 우리 상황이 잘 맞지 않으면, 확신에 찬 것처럼 서술하지 말고
  그 한계를 솔직히 밝힐 것

다시 한 번 강조하면: 지금 당신의 역할은 단순 요약이 아니라, 위 자료를 근거로
우리 상황에 맞는 다음 스텝을 추론해서 제안하는 것입니다.
"""

    # paper_results에 딸린 그림(figure)들 중 실제 이미지 파일이 있는 것만 모아서
    # Claude에게 같이 첨부함 (rag_llm.py에 이미 있던 이미지 판독 로직을 그대로 재사용).
    # 캡션 없는 장식용 그림은 build_image_manifest 안에서 이미 걸러짐.
    image_manifest = build_image_manifest(paper_results) if paper_results else []
    image_paths = [m["image_path"] for m in image_manifest]

    if image_manifest:
        manifest_lines = []
        for i, m in enumerate(image_manifest, start=1):
            caption_short = (m["caption"] or "")[:120]
            manifest_lines.append(f"{i}. [{m['paper_id']}] {m['section_head']} — {caption_short}")

        instructions += (
            "\n이 메시지에는 아래 순서대로 관련 논문의 그림(Figure) 이미지가 첨부되어 있습니다:\n"
            + "\n".join(manifest_lines)
            + "\n\n[이미지 판독 규칙 - 반드시 지킬 것]\n"
            "1) 시각적 세부사항(색상, 화살표 방향, 곡선의 기울기와 방향, 축의 좌우/상하 배치, "
            "그림 안에 인쇄된 수치 라벨, 개수, 줄무늬/패턴 등)을 묻는 질문에는, "
            "반드시 첨부된 이미지에서 직접 확인한 것만 근거로 답해라.\n"
            "2) 본문 텍스트나 캡션만 보고 '그림이 이렇게 그려져 있을 것이다'라고 추정해서 서술하는 것은 금지한다.\n"
            "3) 본문 텍스트의 서술과 이미지에서 실제로 보이는 것이 다르면, "
            "이미지에서 관찰한 쪽을 우선하고, 둘이 어긋난다는 사실도 함께 밝혀라.\n"
            "4) 이미지가 흐리거나 해당 부분이 잘려서 확인이 불가능하면, "
            "추측해서 서술하지 말고 '이미지에서 확인 불가'라고 명시해라.\n"
            "5) 이미지를 근거로 답할 때는 논문(paper_id)과 몇 번째 이미지인지 반드시 밝혀라.\n"
        )

    # 프롬프트 순서 원칙(트랙 A 세션 결론과 동일): 긴 문서 먼저, 규칙은 질문 바로 앞, 질문은 맨 마지막
    # + 맨 앞에 역할/과제 프레이밍 추가 (아직 우리 데이터로 검증은 안 된 구조라 실험적으로 적용)
    prompt = f"""{role_framing}

{project_notes}

논문DB 검색 결과:
{paper_section}

웹서치 결과:
{web_section}

{instructions}

질문: {user_question}"""

    if image_paths:
        return call_claude_with_images(prompt, image_paths=image_paths, max_tokens=4096)
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
    top_k: int = 5,
    candidate_k: int = 15,
    pinned_paper_id: str = None,
    pin_mode: str = "broad",
) -> str:
    project_notes = load_project_context(background_path, notes_path)

    queries = generate_search_query(project_notes, user_question)
    paper_results = hybrid_search_papers(
        queries,
        chunks_path=chunks_path,
        vectors_path=vectors_path,
        sections_path=sections_path,
        bm25_path=bm25_path,
        top_k=top_k,
        candidate_k=candidate_k,
        pinned_paper_id=pinned_paper_id,
        pin_mode=pin_mode,
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
    parser.add_argument("--top-k", type=int, default=5)
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