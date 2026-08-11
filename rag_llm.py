import json
import argparse
import sys

from vector_search import (
    load_model,
    search_top_k,
    build_chunk_lookup,
    attach_context,
    dedupe_sections,
    format_tables_and_figures,   # 추가: vector_search.py에 이미 있는 함수를 가져다 씀
)


'''
검색된 섹션들 + 질문을 하나의 프롬프트 텍스트로 조합.
이 프롬프트 텍스트는 자동 모드(API)든 수동 모드(복붙)든 완전히 동일하게 씀.
(환각 방지 문구 포함: 자료에 없으면 모른다고 말하게 지시)
'''
def build_prompt(query: str, sections: list[dict]) -> str:
    context_parts = []
    for s in sections:
        section_block = f"### [{s['paper_id']}] {s['section_head']}\n{s['section_text']}"

        # 추가: 표/그림도 프롬프트에 같이 넣기 (안 넣으면 LLM이 표 데이터를 아예 못 봄)
        tf_text = format_tables_and_figures(s.get("section_tables", []), s.get("section_figures", []))
        if tf_text:
            section_block += f"\n\n{tf_text}"

        context_parts.append(section_block)
    context_text = "\n\n".join(context_parts)

    '''
    -------------------------------------
    여기 prompt 안에서 내용을 바꿔가면서 질문하면됨 
    -------------------------------------
    '''
    prompt = f"""다음은 논문에서 검색된 관련 내용입니다.

{context_text}

---

위 내용을 바탕으로 다음 질문에 답변해줘.
만약 위 내용만으로 답하기에 정보가 부족하면, 부족한 부분이 무엇인지 솔직히 말해줘.
답변할 때 어느 논문(paper_id)의 어느 섹션을 근거로 했는지도 같이 밝혀줘.
추측하지 말고, 자료에 명시적으로 없는 내용은 답변에 포함하지 마.

질문: {query}
"""
    return prompt


'''
자동 모드에서만 사용: Claude API 호출.
(수동 모드에서는 이 함수 자체가 호출되지 않음 -> API 키 없어도 수동 모드는 그냥 작동함)

아직 이 부분 API 세팅 안했음
'''
def call_claude_api(prompt: str, model: str = "claude-sonnet-4-6") -> str:
    try:
        import anthropic #type: ignore
    except ImportError:
        print("anthropic 패키지가 설치되어 있지 않습니다.")
        print("설치: pip install anthropic --break-system-packages")
        sys.exit(1)

    client = anthropic.Anthropic()  # 환경변수 ANTHROPIC_API_KEY 사용
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


'''
사용방법 

python3 llm_rag.py --query "전극 재료로 뭘 썼어?"                  => 기본값: 수동 모드 (프롬프트만 출력, 무료)
python3 llm_rag.py --query "전극 재료로 뭘 썼어?" --auto          => 자동 모드 (Claude API 호출, 과금)
'''
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", default="results/chunks.json")
    ap.add_argument("--vectors", default="results/vectors.json")
    ap.add_argument("--sections", default="results/sections_store.json")
    ap.add_argument("--query", required=True, help="사용자 질문")
    ap.add_argument("--top-k", type=int, default=5, help="검색할 청크 개수 (기본값: 5)")
    ap.add_argument("--auto", action="store_true",
                     help="이 옵션을 주면 Claude API를 자동 호출함 (과금 발생). "
                          "안 주면 프롬프트만 출력하고 끝남 (무료, 수동으로 Claude.ai에 붙여넣기)")
    args = ap.parse_args()

    with open(args.chunks, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    with open(args.vectors, "r", encoding="utf-8") as f:
        vectors = json.load(f)
    with open(args.sections, "r", encoding="utf-8") as f:
        sections_store = json.load(f)

    model = load_model()
    query_vector = model.encode([args.query], normalize_embeddings=True)[0].tolist()

    top_results = search_top_k(query_vector, vectors, top_k=args.top_k)
    chunk_lookup = build_chunk_lookup(chunks)
    enriched = attach_context(top_results, chunk_lookup, sections_store)
    sections_for_llm = dedupe_sections(enriched)

    prompt = build_prompt(args.query, sections_for_llm)

    if args.auto:
        # --- 자동 모드: API 호출해서 답변까지 바로 받기 (과금 발생) ---
        print("Claude API 호출 중...\n")
        answer = call_claude_api(prompt)
        print("=" * 70)
        print("답변:")
        print("=" * 70)
        print(answer)
    else:
        # --- 수동 모드: 프롬프트만 출력 (무료) ---
        print("=" * 70)
        print("아래 내용을 복사해서 Claude.ai 채팅창에 붙여넣으세요:")
        print("=" * 70)
        print(prompt)
        print("=" * 70)
        print(f"(검색된 섹션 {len(sections_for_llm)}개가 포함되어 있습니다)")


if __name__ == "__main__":
    main()