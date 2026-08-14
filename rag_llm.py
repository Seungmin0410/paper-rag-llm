import json
import argparse
import sys
import os
import base64

from vector_search import (
    load_model,
    search_top_k,
    build_chunk_lookup,
    attach_context,
    dedupe_sections,
    format_tables_and_figures,   # 추가: vector_search.py에 이미 있는 함수를 가져다 씀
)


'''
추가: 검색된 섹션들에서 이미지 경로 + 출처 정보(paper_id, section_head, caption)를
      순서를 유지한 채 하나의 매니페스트로 만든다.

이 순서가 매우 중요함: call_claude_api()에서 실제로 Claude에게 보내는 이미지 블록 순서와
반드시 동일해야, 프롬프트에 적힌 "1번 이미지 = ○○ 논문의 Figure 3" 같은 매핑이
실제로 Claude가 받는 이미지 순서와 어긋나지 않는다.
'''
def build_image_manifest(sections: list[dict]) -> list[dict]:
    seen_paths = set()
    manifest = []
    for s in sections:
        for fig in s.get("section_figures", []):
            path = fig.get("image_path")
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            manifest.append({
                "image_path": path,
                "paper_id": s.get("paper_id", ""),
                "section_head": s.get("section_head", ""),
                "caption": fig.get("caption", "") or "",
            })
    return manifest


'''
검색된 섹션들 + 질문을 하나의 프롬프트 텍스트로 조합.
이 프롬프트 텍스트는 자동 모드(API)든 수동 모드(복붙)든 완전히 동일하게 씀.
(환각 방지 문구 포함: 자료에 없으면 모른다고 말하게 지시)

변경: image_manifest를 받아서, 프롬프트 맨 앞에 "첨부된 이미지 목록"을 번호 매겨 명시.
      이렇게 해야 Claude가 몇 번째 이미지가 어느 논문/섹션/캡션에 해당하는지 정확히 알고,
      텍스트 설명과 실제 이미지를 짝지어서 답변에 활용할 수 있다.
'''
def build_prompt(query: str, sections: list[dict], image_manifest: list[dict] = None) -> str:
    context_parts = []
    for s in sections:
        section_block = f"### [{s['paper_id']}] {s['section_head']}\n{s['section_text']}"

        # 추가: 표/그림도 프롬프트에 같이 넣기 (안 넣으면 LLM이 표 데이터를 아예 못 봄)
        tf_text = format_tables_and_figures(s.get("section_tables", []), s.get("section_figures", []))
        if tf_text:
            section_block += f"\n\n{tf_text}"

        context_parts.append(section_block)
    context_text = "\n\n".join(context_parts)

    # 추가: 이미지 매니페스트를 텍스트로 변환 (프롬프트 맨 앞에 들어감)
    image_manifest_text = ""
    image_instruction = ""
    if image_manifest:
        lines = []
        for i, m in enumerate(image_manifest, start=1):
            caption_short = m["caption"][:120]
            lines.append(f"{i}. [{m['paper_id']}] {m['section_head']} — {caption_short}")
        image_manifest_text = (
            "이 메시지에는 아래 순서대로 이미지가 첨부되어 있습니다 (몇 번째 이미지인지, "
            "어느 논문의 어느 섹션/캡션에 해당하는지 표시):\n"
            + "\n".join(lines)
            + "\n\n"
        )
        image_instruction = (
            "\n첨부된 이미지가 있다면, 캡션 텍스트만으로는 알 수 없는 시각적 세부사항"
            "(색상, 개수, 배치, 정확한 수치 라벨, 화살표 방향 등)을 답할 때는 "
            "해당 이미지를 직접 참고해서 답변해줘. 이때 몇 번째 이미지를 근거로 했는지도 같이 밝혀줘."
        )

    '''
    -------------------------------------
    여기 prompt 안에서 내용을 바꿔가면서 질문하면됨 
    -------------------------------------
    '''

    
    prompt = f"""{image_manifest_text}다음은 논문에서 검색된 관련 내용입니다.

{context_text}

---

위 내용을 바탕으로 다음 질문에 답변해줘.
만약 위 내용만으로 답하기에 정보가 부족하면, 부족한 부분이 무엇인지 솔직히 말해줘.
답변할 때 어느 논문(paper_id)의 어느 섹션을 근거로 했는지도 같이 밝혀줘.
추측하지 말고, 자료에 명시적으로 없는 내용은 답변에 포함하지 마.{image_instruction}

질문: {query}
"""
    return prompt


'''
추가: 이미지 파일 하나를 Claude API의 image content block 형식으로 변환.
지금 저장되는 이미지가 전부 .png(parse_docling.py에서 pil_img.save(...png))라서
media_type을 image/png로 고정해도 됨.
'''
def build_image_block(image_path: str) -> dict | None:
    try:
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        img_b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": img_b64,
            },
        }
    except Exception as e:
        print(f"  !! 이미지 로드 실패 ({image_path}): {e}")
        return None


'''
자동 모드에서만 사용: Claude API 호출.
(수동 모드에서는 이 함수 자체가 호출되지 않음 -> API 키 없어도 수동 모드는 그냥 작동함)

변경: image_paths를 받아서, 텍스트 프롬프트와 함께 이미지도 같이 실어 보냄.
      Anthropic 권장대로 이미지 블록을 텍스트보다 앞에 배치.
      이 순서는 build_prompt()의 image_manifest 순서와 반드시 동일해야 함
      (main()에서 같은 리스트를 기반으로 만들기 때문에 자동으로 일치함).
'''
def call_claude_api(prompt: str, image_paths: list[str] = None, model: str = "claude-sonnet-4-6") -> str:
    try:
        import anthropic #type: ignore
    except ImportError:
        print("anthropic 패키지가 설치되어 있지 않습니다.")
        print("설치: pip install anthropic --break-system-packages")
        sys.exit(1)

    content = []

    if image_paths:
        for path in image_paths:
            block = build_image_block(path)
            if block:
                content.append(block)

    content.append({"type": "text", "text": prompt})

    client = anthropic.Anthropic()  # 환경변수 ANTHROPIC_API_KEY 사용
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text


'''
사용방법 

python3 rag_llm.py --query "전극 재료로 뭘 썼어?"                 => 기본값: 수동 모드 (프롬프트만 출력, 무료)
python3 rag_llm.py --query "전극 재료로 뭘 썼어?" --auto          => 자동 모드 (Claude API 호출, 과금)
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

    # 추가: 이미지 매니페스트(경로+출처+캡션)를 먼저 만들고,
    #         프롬프트와 실제 API 호출 둘 다 이 하나의 매니페스트에서 파생시켜서
    #         "프롬프트에 적힌 번호"와 "실제 전달되는 이미지 순서"가 항상 일치하게 함.
    image_manifest = build_image_manifest(sections_for_llm)
    image_paths = [m["image_path"] for m in image_manifest]

    prompt = build_prompt(args.query, sections_for_llm, image_manifest=image_manifest)

    if args.auto:
        # --- 자동 모드: API 호출해서 답변까지 바로 받기 (과금 발생) ---
        print(f"Claude API 호출 중... (이미지 {len(image_paths)}장 포함)\n")
        answer = call_claude_api(prompt, image_paths=image_paths)
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

        # 변경: 수동 모드에서는 이미지를 텍스트로 못 실어 보내니, 매니페스트 정보를 그대로 안내.
        #         Claude.ai에 직접 첨부하고 싶으면 이 파일들을 프롬프트에 적힌 순서 그대로 같이 올리면 됨.
        if image_manifest:
            print(f"\n(참고: 관련 이미지 {len(image_manifest)}장이 있습니다. "
                  f"--auto 모드를 쓰면 자동으로 같이 전달되고, "
                  f"수동 모드에서는 아래 파일들을 이 순서 그대로 Claude.ai에 첨부해야 "
                  f"프롬프트에 적힌 번호와 실제 이미지가 일치합니다.)")
            for i, m in enumerate(image_manifest, start=1):
                print(f"  {i}. [{m['paper_id']}] {m['image_path']}")


if __name__ == "__main__":
    main()