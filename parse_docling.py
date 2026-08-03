'''
Docling 에서는 본문 섹션에 집중
'''
import re
from collections import Counter
from difflib import SequenceMatcher
from docling.document_converter import DocumentConverter # type: ignore


'''
프로토 타입 논문들 중에 확실한 정크들 제거, 수기로 작성하기 때문에 너무 안걸러지는 것 들만 나중에 추가

r 이건 정규펴현식 백슬래시가 있으면 붙여주면 좋음
'''
CONFIRMED_JUNK_PATTERNS = [
    r"^PAPER$",
    r"^Review$",
    r"^Energy\s*&?\s*Environmental Science$",  
    r"^Energies\s*,?\s*\d{4}",
]

_confirmed_junk_re = re.compile("|".join(CONFIRMED_JUNK_PATTERNS), re.IGNORECASE)


'''
정크 텍스트, 제목들이 되는 기준 설정 + 정크 부분 없애기(수기)
'''
MIN_TEXT_LEN = 10
JUNK_HEAD_REPEAT_THRESHOLD = 4
INNER_DUP_THRESHOLD = 0.8
SECTION_DUP_THRESHOLD = 0.8

## 너무 짧아서 정크일때
def short_junk (text: str) -> bool:
    return len(text.strip()) < MIN_TEXT_LEN

## 두 섹션의 내용이 같이서 중복일때
def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def confirmed_junk (text: str) -> bool:
    text = text.strip()
    return bool(_confirmed_junk_re.match(text))

'''
docling 메인 함수로, 받아온 파일을 각각 함수로 보내서 정크제거, 세분화 작업 진행하고 최종 형태로 return
'''
def parse_docling(pdf_path: str, min_repeat: int = JUNK_HEAD_REPEAT_THRESHOLD) -> dict:
    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    doc = result.document
 
    raw = build_raw_section(doc)
 
    junk_heads, head_stats = detect_junk_header(raw, min_repeat=min_repeat)
    sections, rem_junk = filter_junk_headers(raw, junk_heads)
    sections, rem_inner = clean_sections_inner(sections)
    sections, rem_sec = dedupe_sections(sections)
 
    final = []
    for sec in sections:
        if sec["head"] and len(sec["text"]) >= MIN_TEXT_LEN:
            final.append({"n": None, "head": sec["head"], "text": sec["text"]})
 
    return {
        "sections": final,
        "_removed": rem_junk + rem_inner + rem_sec,
        "_stats": {
            "raw_sections": len(raw),
            "after_junk_filter": len(sections) + len(rem_junk),
            "final_sections": len(final),
            "head_frequencies": head_stats,
            "junk_heads_detected": list(junk_heads),
        }
    }


'''
Docling을 진행하여 받아온 raw 파일을 우리가 원하는 형태로 좀더 세분화 하는 작업 

# Docling의 판단 로직 (대략)
if 폰트_크기 > 14pt and 굵기 == bold:
    label = "section_header"  # ← 섹션 헤더다!
elif 일반_텍스트:
    label = "text"  # ← 일반 텍스트다

도클링 자체에서 이런 방식으로 section_header, text 같은걸 구별하는데 이걸 이용하여 우리가 보기 좋게 리스트 형식으로 만든다.
'''
def build_raw_section(doc) -> list[dict]:
    sections = []
    current = {"head": None, "text_parts": []}

    def flush():
        if current["head"] is not None or current["text_parts"]:
            sections.append({
                "head": current["head"],
                "text": "\n\n".join(current["text_parts"]).strip(),
            })

    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue

        if label =="section_header":
            flush()
            current = {"head": text, "text_parts": []}
        elif label in ("text", "caption", "formula"):
            current["text_parts"].append(text)

    flush()
    return sections


'''
빈도기반 정크헤더 필터링
'''
def detect_junk_header(sections: list[dict], min_repeat: int = JUNK_HEAD_REPEAT_THRESHOLD) -> tuple[set[str], dict]:
    head_counter = Counter()
    for sec in sections:
        h = (sec["head"] or "").strip()
        if h:
            head_counter[h] += 1

    junk_heads = {h for h, count in head_counter.items() if count >= min_repeat}
    return junk_heads, dict(head_counter)


'''
확실한 정크 + 빈도기반 정크헤더 없애기
'''
def filter_junk_headers (sections: list[dict], junk_heads: set[str]) -> tuple[list[dict], list[dict]]:
    kept = []
    removed = []

    for sec in sections:
        head = (sec["head"] or "").strip()
        text = sec["text"]
 
        is_junk = head in junk_heads or confirmed_junk(head)
 
        if is_junk:
            if len(text) >= MIN_TEXT_LEN and kept:
                kept[-1]["text"] = (kept[-1]["text"] + "\n\n" + text).strip()
                removed.append({"reason": "junk_merged", "head": head, "text_len": len(text)})
            else:
                removed.append({"reason": "junk_dropped", "head": head, "text_len": len(text)})
            continue
 
        kept.append(sec)
 
    return kept, removed


'''
섹션 내부 문단 중복 제거
'''
def dedupe_within_section(text: str) -> tuple[str, int]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        return text, 0
 
    kept = []
    dropped = 0
    for p in paragraphs:
        if short_junk(p):
            dropped += 1
            continue
 
        if kept:
            sim = similarity(kept[-1][:300], p[:300])
            if sim >= INNER_DUP_THRESHOLD:
                if len(p) > len(kept[-1]):
                    kept[-1] = p 
                dropped += 1
                continue
 
        kept.append(p)
 
    return "\n\n".join(kept).strip(), dropped

def clean_sections_inner(sections: list[dict]) -> tuple[list[dict], list[dict]]:
    removed = []
    for sec in sections:
        new_text, dropped = dedupe_within_section(sec["text"])
        sec["text"] = new_text
        if dropped > 0:
            removed.append({"reason": "inner_dup_removed", "head": sec["head"], "count": dropped})
    return sections, removed


'''
섹션 헤드간 중복제거
'''
def _head_related(h1: str, h2: str) -> bool:
    """헤더가 같거나 포함관계인지 판정."""
    a = (h1 or "").strip().lower()
    b = (h2 or "").strip().lower()
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)

def dedupe_sections(sections: list[dict]) -> tuple[list[dict], list[dict]]:
    kept = []
    removed = []
 
    for sec in sections:
        if not kept:
            kept.append(sec)
            continue
 
        prev = kept[-1]
        text_sim = (similarity(prev["text"][:300], sec["text"][:300])
                    if prev["text"] and sec["text"] else 0.0)
 
        if _head_related(prev["head"], sec["head"]) and text_sim >= SECTION_DUP_THRESHOLD:
            if len(sec["text"]) > len(prev["text"]):
                kept[-1] = sec
            removed.append({"reason": "section_dup_removed", "head": sec["head"], 
                           "similarity": round(text_sim, 2)})
            continue
 
        if text_sim >= SECTION_DUP_THRESHOLD:
            removed.append({"reason": "section_dup_suspected", "head": sec["head"],
                           "similarity": round(text_sim, 2)})
            kept.append(sec)
            continue
 
        kept.append(sec)
 
    return kept, removed


'''
결과 확인용
'''
def main():
    import argparse
    import json
    import os
 
    ap = argparse.ArgumentParser(description="Docling 본문 섹션 추출 (빈도 기반 junk 탐지)")
    ap.add_argument("pdf", help="파싱할 PDF 경로")
    ap.add_argument("--min-repeat", type=int, default=JUNK_HEAD_REPEAT_THRESHOLD,
                   help=f"junk header 탐지 threshold (기본값: {JUNK_HEAD_REPEAT_THRESHOLD})")
    ap.add_argument("--show-removed", action="store_true", help="걸러낸/의심 항목 로그 출력")
    ap.add_argument("--show-stats", action="store_true", help="분석 통계 출력")
    args = ap.parse_args()
 
    print(f"Parsing {args.pdf} with Docling ...")
    out = parse_docling(args.pdf, min_repeat=args.min_repeat)
    sections = out["sections"]
    removed = out["_removed"]
    stats = out["_stats"]
 
    print("=" * 70)
    print(f"[본문 섹션] {len(sections)}개")
    for i, s in enumerate(sections):
        print(f"  {i+1:2d}) {s['head'][:60]}  — {len(s['text'])}자")
    print("=" * 70)
 
    if args.show_stats:
        print(f"\n[통계]")
        print(f"  원본 섹션: {stats['raw_sections']}")
        print(f"  최종 섹션: {stats['final_sections']}")
        if stats.get('junk_heads_detected'):
            print(f"  자동 탐지된 junk 헤더: {stats['junk_heads_detected']}")
        print(f"  헤더 빈도: {stats.get('head_frequencies', {})}")
 
    if args.show_removed:
        print(f"\n[검수용] 제거/의심 항목 {len(removed)}건")
        for r in removed:
            print(f"  - {r}")
    
    # 파일 저장
    os.makedirs("results", exist_ok=True)
    
    # PDF 파일명에서 확장자 제외하고 가져오기
    pdf_filename = os.path.splitext(os.path.basename(args.pdf))[0]
    output_file = f"results/{pdf_filename}_docling.json"
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 결과 저장: {output_file}")
 
 
if __name__ == "__main__":
    main()