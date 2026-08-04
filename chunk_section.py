import json
import re
import argparse
import os

'''
설정값 - 청크사이즈나 겹침 사이즈는 청킹해보고 결정
'''
TARGET_CHUNK_SIZE = 700
MIN_CHUNK_SIZE = 50
OVERLAP_SIZE = 80


'''
섹션 원문을 문단 별로 쪼개기
'''
def split_into_paragraphs(text: str) -> list[str]:
    paras = []
    for p in text.split("\n\n"):
        if p.strip():
            paras.append(p.strip())
    return paras


'''
화학식 정규화: 유니코드 아래첨자(₀₁₂...)를 일반 숫자로 통일.
예) "Cu₂O" -> "Cu2O", "CO₂" -> "CO2"
이걸 안 하면 원문엔 아래첨자로 써있는데 질문은 일반 숫자로 쓰는 경우
글자 자체가 달라서 벡터 유사도가 미묘하게 떨어질 수 있음.
청킹하기 전에 제일 먼저 이 정규화 작업 꼭필요함
'''
SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
def normalize_chemical_formula(text: str) -> str:
    return text.translate(SUBSCRIPT_MAP)


'''
표 감지
Docling이 표를 문단이랑 구분 안 해주고 그냥 텍스트로 섞어서 줄 때가 있어서,
패턴으로 표를 추정해서 따로 처리함 (표는 안 자르고 통째로 한 조각 유지하기 위함).

판단 기준 (둘 중 하나라도 해당하면 표로 간주):
  1) "Table 1", "Table 2." 처럼 표 캡션으로 시작하는 경우
  2) 숫자/구분자(탭, 파이프, 여러 칸 공백)가 촘촘하게 반복되는 줄이 많은 경우
     (표를 텍스트로 뽑으면 보통 이런 모양이 됨)
'''
TABLE_CAPTION_RE = re.compile(r'^\s*(Table|표)\s*S?\d+', re.IGNORECASE)

def is_table_like(paragraph: str) -> bool:
    if TABLE_CAPTION_RE.match(paragraph):
        return True

    lines = [ln for ln in paragraph.split("\n") if ln.strip()]
    if len(lines) < 2:
        return False

    # 숫자나 탭/파이프 구분자가 많이 섞인 줄의 비율을 셈
    numeric_like_lines = 0
    for ln in lines:
        digit_count = sum(ch.isdigit() for ch in ln)
        has_delimiter = ("\t" in ln) or ("|" in ln) or ("  " in ln)  # 연속 공백 2칸 이상도 표 흔적
        if has_delimiter and digit_count >= 2:
            numeric_like_lines += 1

    return (numeric_like_lines / len(lines)) >= 0.5


'''
너무 긴 문단은 미리 잘라두기
'''
def split_oversized_paragraph(paragraph: str, max_size: int) -> list[str]:
    if len(paragraph) <= max_size:
        return [paragraph]

    sentences = re.split(r'(?<=[.!?])\s+', paragraph)

    pieces = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) > max_size and current:
            pieces.append(current.strip())
            current = ""
        current = (current + " " + sent).strip()
    if current:
        pieces.append(current.strip())
    return pieces if pieces else [paragraph]


'''
Greddy Packing - 하나의 큰 청크를 여러 개의 작은 청크로 나눔
표(table)로 판단되는 문단은 예외적으로 안 자르고 그 자체로 하나의 청크가 됨.
'''
def chunk_section_text(text: str, target_size: int = TARGET_CHUNK_SIZE, overlap_size: int = OVERLAP_SIZE) -> list[str]:

    '''
    논문 청킹하기전 text 정규화작업 및 문단 별로 text 분리
    '''
    text = normalize_chemical_formula(text)  
    paragraphs = split_into_paragraphs(text)

    if not paragraphs:
        return[]
    
    normalized = []
    for p in paragraphs:
        if is_table_like(p):
            normalized.append((p, True))     ## 표는 그대로, 안 쪼갬
        else:
            for piece in split_oversized_paragraph(p, target_size * 2):
                normalized.append((piece, False))

    '''
    최종 정보가 모아지는 장소
    '''
    chunks = []
    current_parts = []
    current_len = 0

    for para, is_table in normalized:
        '''
        표는 크기와 상관없이 하나로 간주
        '''
        if is_table:
            if current_parts:
                chunks.append("\n\n".join(current_parts).strip())
                current_parts = []
                current_len = 0
            chunks.append(para)   
            continue
        '''
        일반 문단인 경우 target_size 만큼 자르고 overlap 적용해주기
        '''
        if current_len + len(para) > target_size and current_parts:
            chunks.append("\n\n".join(current_parts).strip())
            overlap_text = current_parts[-1][-overlap_size:] if overlap_size > 0 else ""
            current_parts = [overlap_text] if overlap_text else []
            current_len = len(overlap_text)
 
        current_parts.append(para)
        current_len += len(para)

    if current_parts:
        last_chunk = "\n\n".join(current_parts).strip() 
 
        if len(last_chunk) < MIN_CHUNK_SIZE and chunks:
            chunks[-1] = (chunks[-1] + "\n\n" + last_chunk).strip()
        else:
            chunks.append(last_chunk)
 
    return chunks


'''
grobid + docling 이  완료된 doc 파일을 받아와서 청킹 시작.

본격적인 청킹을 시작하기전에 각 논문마다 paper_id 를 부여해서 저장할때 비교가능하게 준비 -> chunck_section_text 로 청킹 시작
'''
def build_chunks(doc: dict, paper_id: str, target_size: int = TARGET_CHUNK_SIZE, overlap_size: int = OVERLAP_SIZE) -> tuple[dict, list[dict]]:
    '''
    sections_store -> 나중에 llm 참고용으로 섹션 전체를 저장 (부모)
    all_chunks -> 청킹을 해서 모아둘 딕션어리 (자식)
    '''
    sections_store = {}
    all_chunks = []

    paper_title = doc.get("title", "")
    paper_doi = doc.get("doi", "")

    '''
    우선은 한 파일에 .json 형태로 모든 논문의 정보를 저장하는데 이때 논문별로 구별해주기 위해서 paper_id로 각각 논문을 구별
    '''
    for sec_idx, sec in enumerate(doc.get("sections", [])):
        head = sec.get("head") or "(제목없음)"
        text = sec.get("text", "")
        section_id = f"sec_{sec_idx}"
        store_key = f"{paper_id}::{section_id}"

        sections_store[store_key] = {
            "paper_id": paper_id,     
            "section_id": section_id,
            "head": head,
            "text": text,
            "paper_title": paper_title,
            "paper_doi": paper_doi,
        }

        '''
        chunk_section_text로 논문 청킹해오고 아래 형태로 저장
        '''
        pieces = chunk_section_text(text, target_size=target_size, overlap_size=overlap_size)

        for chunk_idx, piece in enumerate(pieces):
            all_chunks.append({
                "paper_id": paper_id,       
                "section_id": section_id,    
                "chunk_index": chunk_idx,      
                "chunk_total": len(pieces),   
                "chunk_text": piece,           
                "chunk_char_len": len(piece),
            })
    return sections_store, all_chunks


'''
누적 저장(upsert) 관련 함수들
------------------------------
목표: chunks.json / sections_store.json 파일 하나에 여러 논문을 계속 쌓기.
방법: 기존 파일을 읽고 -> 이번에 처리하는 paper_id의 옛날 데이터는 지우고
      -> 새로 만든 데이터를 합쳐서 -> 다시 저장.
(같은 논문을 다시 돌리면 그 논문 부분만 교체되고, 다른 논문은 그대로 남음)
'''
## 파일이 없으면 그냥 빈 리스트로 시작 -> chunks.json 처음 만들 때 대비
def load_existing_list(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

## 파일이 없으면 그냥 빈 딕셔너리로 시작 (sections_store.json 처음 만들 때 대비)
def load_existing_dict(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

## chunks.json에 이번 논문 청크들을 누적 저장
def upsert_chunks_store(path: str, paper_id: str, new_chunks: list[dict]) -> list[dict]:
    existing = load_existing_list(path)
    kept = [c for c in existing if c.get("paper_id") != paper_id]  ## 이 논문의 옛날 데이터는 버림
    merged = kept + new_chunks
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged

## sections_store.json에 이번 논문 섹션들을 누적 저장
def upsert_sections_store(path: str, paper_id: str, new_sections: dict) -> dict:
    existing = load_existing_dict(path)
    prefix = f"{paper_id}::"
    kept = {k: v for k, v in existing.items() if not k.startswith(prefix)}  ## 이 논문 것만 골라서 버림
    kept.update(new_sections)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
    return kept


'''
결과 확인 및 테스팅용~
'''
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="merged JSON 경로 (parse_grobid+parse_docling 병합 결과)")
    ap.add_argument("--paper-id", required=True, help="이 논문을 구분할 고유 id, 예: 2019_faradaic")
    ap.add_argument("--out-chunks", help="청크(자식) 결과를 누적 저장할 JSON 경로")
    ap.add_argument("--out-sections", help="섹션 원문 저장소(부모) 결과를 누적 저장할 JSON 경로")
    ap.add_argument("--target-size", type=int, default=TARGET_CHUNK_SIZE,
                     help=f"청크 목표 글자 수 (기본값: {TARGET_CHUNK_SIZE})")
    ap.add_argument("--overlap", type=int, default=OVERLAP_SIZE,
                     help=f"청크 간 오버랩 글자 수 (기본값: {OVERLAP_SIZE})")
    args = ap.parse_args()
 
    with open(args.input, "r", encoding="utf-8") as f:
        doc = json.load(f)
 
    sections_store, chunks = build_chunks(
        doc, args.paper_id, target_size=args.target_size, overlap_size=args.overlap
    )
 
    # --- 눈으로 확인할 수 있게 요약 출력 (이번 논문 것만) ---
    print(f"논문: {doc.get('title', '')[:60]}  (paper_id: {args.paper_id})")
    print(f"이번 논문 섹션 수: {len(sections_store)}")
    print(f"이번 논문 청크 수: {len(chunks)}")
    print("=" * 70)
    for c in chunks[:10]:
        store_key = f"{args.paper_id}::{c['section_id']}"
        head = sections_store[store_key]["head"]
        print(f"  [{head[:40]}] #{c['chunk_index']+1}/{c['chunk_total']} "
              f"— {c['chunk_char_len']}자")
    if len(chunks) > 10:
        print(f"  ... 외 {len(chunks) - 10}개")
 
    # --- 두 개의 파일에 "누적" 저장 (기존 다른 논문 데이터는 그대로 유지됨) ---
    if args.out_chunks:
        all_chunks = upsert_chunks_store(args.out_chunks, args.paper_id, chunks)
        print(f"\n누적 저장됨 (청크/자식): {args.out_chunks}  (전체 {len(all_chunks)}개)")
 
    if args.out_sections:
        all_sections = upsert_sections_store(args.out_sections, args.paper_id, sections_store)
        print(f"누적 저장됨 (섹션 원문/부모): {args.out_sections}  (전체 {len(all_sections)}개)")
 
 
if __name__ == "__main__":
    main()