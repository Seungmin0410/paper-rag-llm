'''
Docling 에서는 본문 섹션에 집중
'''
import os
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from docling.document_converter import DocumentConverter, PdfFormatOption # type: ignore
from docling.datamodel.pipeline_options import PdfPipelineOptions # type: ignore
from docling.datamodel.base_models import InputFormat # type: ignore
from docling_core.types.doc import PictureItem, TableItem # type: ignore


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

# ===== 추가: 캡션-그림/표 연결 설정값 =====
# scan_caption_linking.py 로 15개 논문 전체를 스캔하고, 매칭된 이미지 5개를 직접
# 눈으로 대조 검증까지 마친 값들을 그대로 가져온 것.
#
# 학술논문에서 그림/표/반응식 캡션이 시작하는 전형적인 접두어.
# 이 패턴에 안 맞는 텍스트(예: 그래프 축 라벨 조각 등)는 매칭 후보에서 제외해서
# 엉뚱한 텍스트를 그림에 억지로 붙이는 사고를 방지한다.
CAPTION_PREFIXES = ("Fig.", "Figure", "Table", "Scheme")

# 캡션-그림/표 사이 수직 거리가 이 값(pt)을 넘으면 매칭을 포기한다.
# 실측값(9.9~14.9pt)에 여유를 크게 둔 값.
MAX_MATCH_GAP = 40

# ===== 추가: 그림 원본 이미지 저장 설정값 =====
# 캡션이 있는 그림만 실제 PNG 파일로 저장한다 (캡션 없는 장식/로고 이미지는
# 어차피 build_figure_chunks에서 검색 대상으로도 안 쓰이므로 저장할 필요가 없음).
# scan_caption_linking.py / verify_fallback_images.py 검증 때 썼던 것과 동일한 해상도.
IMAGES_BASE_DIR = os.path.join("results", "images")
IMAGES_SCALE = 2.0


## 너무 짧아서 정크일때
def short_junk (text: str) -> bool:
    return len(text.strip()) < MIN_TEXT_LEN

## 두 섹션의 내용이 같이서 중복일때
def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()

def confirmed_junk (text: str) -> bool:
    text = text.strip()
    return bool(_confirmed_junk_re.match(text))


# ===== 추가: 캡션-그림/표 연결 헬퍼 함수 =====

def _looks_like_caption(text: str) -> bool:
    return text.strip().startswith(CAPTION_PREFIXES)


def _vertical_gap(fig_bbox, cap_bbox) -> float:
    """그림/표와 캡션 사이 수직 거리 계산. 어느 쪽이 위/아래든 대응."""
    gap_fig_above = abs(fig_bbox.b - cap_bbox.t)
    gap_fig_below = abs(cap_bbox.b - fig_bbox.t)
    return min(gap_fig_above, gap_fig_below)


def build_caption_map(doc) -> dict:
    """
    문서 전체를 한 번 훑어서, 모든 picture/table에 대한 캡션 텍스트를
    미리 계산해두는 매핑표를 만든다. (id(item) -> caption_text)

    1순위: Docling 내장 기능 item.caption_text(doc)
    2순위(fallback): 1순위가 실패한 것에 한해, 같은 페이지 안에서
                     'Fig./Figure/Table/Scheme'로 시작하는 caption 라벨 텍스트와
                     picture/table 사이 수직 거리를 계산해 그리디 매칭.
                     (scan_caption_linking.py 로 15개 논문 전체 검증 완료:
                      원래 고아 23개 중 20개 구제, 이미지 5개 육안 대조 확인)
    """
    caption_map = {}

    # 1) 문서 전체에서 caption 라벨 텍스트 수집
    all_caption_items = []  # (text_item, page, bbox)
    for text_item in doc.texts:
        if getattr(text_item, "label", None) == "caption" and text_item.prov:
            all_caption_items.append((text_item, text_item.prov[0].page_no, text_item.prov[0].bbox))

    linked_texts = set()
    unlinked_items = []  # (item, kind, page, bbox)

    # 2) picture/table: Docling 내장 caption_text() 먼저 시도
    for item, _level in doc.iterate_items():
        if isinstance(item, PictureItem):
            cap = item.caption_text(doc)
            if cap:
                caption_map[id(item)] = cap
                linked_texts.add(cap.strip())
            elif item.prov:
                unlinked_items.append((item, "picture", item.prov[0].page_no, item.prov[0].bbox))
        elif isinstance(item, TableItem):
            cap = item.caption_text(doc)
            if cap:
                caption_map[id(item)] = cap
                linked_texts.add(cap.strip())
            elif item.prov:
                unlinked_items.append((item, "table", item.prov[0].page_no, item.prov[0].bbox))

    # 3) 아직 안 붙은 caption 텍스트만 추림 (fallback 매칭 후보)
    orphan_captions = [
        (text_item, page, bbox)
        for text_item, page, bbox in all_caption_items
        if text_item.text.strip() not in linked_texts
    ]

    # 4) fallback: 페이지별 좌표 기반 그리디 매칭
    caps_by_page = defaultdict(list)
    for text_item, page, bbox in orphan_captions:
        if _looks_like_caption(text_item.text):
            caps_by_page[page].append((text_item, bbox))

    items_by_page = defaultdict(list)
    for item, kind, page, bbox in unlinked_items:
        items_by_page[page].append((item, kind, bbox))

    matched_caption_ids, matched_item_ids = set(), set()
    for page, caps in caps_by_page.items():
        items = items_by_page.get(page, [])
        if not items:
            continue
        candidates = []
        for cap_item, cap_bbox in caps:
            for item, kind, item_bbox in items:
                gap = _vertical_gap(item_bbox, cap_bbox)
                candidates.append((gap, id(cap_item), id(item), cap_item, item))
        candidates.sort(key=lambda x: x[0])
        for gap, cap_id, item_id, cap_item, item in candidates:
            if gap > MAX_MATCH_GAP:
                break  # 정렬되어 있으므로 이후는 다 더 멀다 -> 조기 종료
            if cap_id in matched_caption_ids or item_id in matched_item_ids:
                continue
            caption_map[item_id] = cap_item.text
            matched_caption_ids.add(cap_id)
            matched_item_ids.add(item_id)

    return caption_map


'''
docling 메인 함수로, 받아온 파일을 각각 함수로 보내서 정크제거, 세분화 작업 진행하고 최종 형태로 return

변경: paper_id 파라미터 추가. 그림을 실제 파일로 저장할 때 논문별로 폴더를 나누기 위해 필요함.
      main.py에서 이미 paper_id를 만들어두고 있으니 그대로 넘겨받으면 되고,
      단독 실행(예: 진단용)처럼 안 넘어오는 경우엔 pdf 파일명에서 자동으로 만든다.
'''
def parse_docling(pdf_path: str, min_repeat: int = JUNK_HEAD_REPEAT_THRESHOLD, paper_id: str = None) -> dict:
    if not paper_id:
        raw_name = os.path.splitext(os.path.basename(pdf_path))[0]
        paper_id = re.sub(r"[^a-zA-Z0-9가-힣]+", "_", raw_name).strip("_").lower()

    # 변경: 그림 원본을 실제로 뽑아내려면 Docling한테 "그림 이미지도 렌더링해라"라고
    #         옵션을 켜줘야 함 (기본값은 꺼져있어서 지금까지는 캡션 텍스트만 뽑고 있었음).
    #         scan_caption_linking.py 등 진단 스크립트에서 이미 검증한 것과 동일한 설정.
    pipeline_options = PdfPipelineOptions(
        generate_picture_images=True,
        images_scale=IMAGES_SCALE,
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )
    result = converter.convert(pdf_path)
    doc = result.document
 
    raw = build_raw_section(doc, paper_id)
 
    junk_heads, head_stats = detect_junk_header(raw, min_repeat=min_repeat)
    sections, rem_junk = filter_junk_headers(raw, junk_heads)
    sections, rem_inner = clean_sections_inner(sections)
    sections, rem_sec = dedupe_sections(sections)
 
    final = []
    for sec in sections:
        # 추가: 텍스트가 짧아도 표(tables)나 그림(figures)이 있으면 최종 결과에서 안 빠지게 조건 추가
        tables = sec.get("tables", [])
        figures = sec.get("figures", [])
        has_enough_text = len(sec["text"]) >= MIN_TEXT_LEN
        has_content = has_enough_text or tables or figures

        # 변경: 기존엔 "sec['head']가 있어야만" 살아남는 조건이라, 문서 맨 앞(첫 section_header가
        #         나오기 전에 등장하는 graphical abstract 등)에 있는 그림/표가 캡션까지 다 붙어놓고도
        #         head가 None이라는 이유만으로 통째로 버려지고 있었음.
        #         이제는 head 유무와 무관하게 "내용이 있으면" 살린다. head가 없으면 placeholder를 채워서
        #         하위 단계(main() 출력, chunk_section.py 등)에서 None 때문에 에러 나지 않게 함.
        #         (캡션 없는 진짜 장식/로고 이미지는 build_figure_chunks에서 여전히 자동으로 걸러짐)
        if has_content:
            head = sec["head"] if sec["head"] else "(머리글 없음 - 문서 상단)"
            final.append({
                "n": None,
                "head": head,
                "text": sec["text"],
                "tables": tables,      # 추가: 표 데이터도 최종 결과물에 포함
                "figures": figures,    # 추가: 그림 메타데이터도 최종 결과물에 포함
            })

    # 추가: 캡션 연결 결과를 한눈에 볼 수 있는 통계 (검증/모니터링용)
    total_figures = sum(len(s.get("figures", [])) for s in final)
    figures_with_caption = sum(
        1 for s in final for f in s.get("figures", []) if f.get("caption")
    )
    total_tables = sum(len(s.get("tables", [])) for s in final)
    tables_with_caption = sum(
        1 for s in final for t in s.get("tables", []) if t.get("caption")
    )
 
    return {
        "sections": final,
        "_removed": rem_junk + rem_inner + rem_sec,
        "_stats": {
            "raw_sections": len(raw),
            "after_junk_filter": len(sections) + len(rem_junk),
            "final_sections": len(final),
            "head_frequencies": head_stats,
            "junk_heads_detected": list(junk_heads),
            # 추가: 캡션 연결 성공률 통계
            "total_figures": total_figures,
            "figures_with_caption": figures_with_caption,
            "total_tables": total_tables,
            "tables_with_caption": tables_with_caption,
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

변경: 캡션 붙이는 방식을 "직전/직후 항목에 즉석으로 붙이기"(순서 기반)에서
      "문서 전체를 미리 훑어서 만든 caption_map을 조회하는 방식"(caption_text() + 좌표 기반 fallback)으로 교체.
      이유: 순서 기반 방식은 그림-캡션 사이에 다른 항목이 끼어들면(대형 멀티패널 그림, 2단 컬럼 레이아웃 등)
      last_time_ref가 리셋되며 캡션을 놓치는 문제가 있었고, 이는 캡션-그림 연결 진단 스캔에서
      실제로 확인된 Docling reading order의 알려진 실패 패턴과 동일한 취약점을 공유하기 때문.
'''
def build_raw_section(doc, paper_id: str) -> list[dict]:
    # 추가: 문서 전체를 한 번 미리 훑어서 캡션 매핑표를 만들어둔다.
    caption_map = build_caption_map(doc)

    # 추가: 이 논문의 그림을 저장할 폴더 (캡션 있는 그림만 실제로 저장됨).
    #         fig_counter는 문서 전체에서 저장 순서를 매기기 위한 카운터 (섹션 바뀌어도 리셋 안 함).
    images_dir = os.path.join(IMAGES_BASE_DIR, paper_id)
    fig_counter = 0

    sections = []
    current = {"head": None, "text_parts": [], "tables": [], "figures" : []}

    # 추가: 텍스트 없이도(표/그림만 있어도) 섹션을 버리지 않도록 flush 조건에 tables/figures 추가
    def flush():
        if current["head"] is not None or current["text_parts"] or current["tables"] or current["figures"]:
            sections.append({
                "head": current["head"],
                "text": "\n\n".join(current["text_parts"]).strip(),
                "tables": current["tables"],
                "figures": current["figures"],
            })

    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)
        text = (getattr(item, "text", "") or "").strip()

        if label == "section_header":
            if not text:
                continue
            flush()
            current = {"head": text, "text_parts": [], "tables": [], "figures": []}
            continue

        # 변경: caption 라벨은 이제 build_caption_map()에서 이미 처리했으므로,
        #         여기서는 text_parts에 안 섞이도록 건너뛰기만 하면 됨 (pending_caption 로직 제거)
        if label == "caption":
            continue

        if label == "table":
            try:
                md_table = item.export_to_markdown(doc)  # 표 구조를 살려서 마크다운으로 저장
            except Exception:
                md_table = text  # 변환 실패 시 원본 텍스트라도 저장 (fallback)

            table_entry = {
                "caption": caption_map.get(id(item)),   # 변경: 미리 계산해둔 매핑표에서 조회
                "markdown": md_table,
            }
            current["tables"].append(table_entry)
            continue

        if label == "picture":
            caption = caption_map.get(id(item))   # 변경: 미리 계산해둔 매핑표에서 조회
            fig_entry = {
                "caption": caption,
            }

            # 추가: 캡션이 있는 그림만 실제 파일로 저장 (캡션 없는 건 장식/로고로 추정되어
            #         build_figure_chunks에서도 어차피 검색 대상에서 빠지므로 저장할 필요가 없음).
            if caption:
                try:
                    pil_img = item.get_image(doc)
                    if pil_img is not None:
                        os.makedirs(images_dir, exist_ok=True)
                        fig_counter += 1
                        img_filename = f"fig_{fig_counter:03d}.png"
                        img_path = os.path.join(images_dir, img_filename)
                        pil_img.save(img_path)
                        fig_entry["image_path"] = img_path
                except Exception:
                    # 이미지 저장이 실패해도 캡션 텍스트는 살아있어야 하므로 조용히 넘어감
                    pass

            current["figures"].append(fig_entry)
            continue

        if not text:
            continue

        if label in ("text", "formula"):
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
        # 추가: junk 섹션이 표/그림을 들고 있을 수 있으니 미리 꺼내둠
        tables = sec.get("tables", [])
        figures = sec.get("figures", [])
 
        is_junk = head in junk_heads or confirmed_junk(head)
 
        if is_junk:
            # 변경: 텍스트가 짧아도 표/그림이 있으면 "내용 있음"으로 인정 (원래는 text 길이만 봄)
            has_content = len(text) >= MIN_TEXT_LEN or tables or figures
            if has_content and kept:
                kept[-1]["text"] = (kept[-1]["text"] + "\n\n" + text).strip()
                # 추가: junk 섹션이 갖고 있던 표/그림을 사라지지 않게 앞 섹션으로 이관
                kept[-1].setdefault("tables", []).extend(tables)
                kept[-1].setdefault("figures", []).extend(figures)
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
    # text 필드만 다루는 함수라 tables/figures는 건드릴 필요 없이 그대로 유지됨
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
                # 추가: prev가 버려지기 전에 갖고 있던 표/그림을 sec 쪽으로 이관 (안 그러면 유실됨)
                sec.setdefault("tables", []).extend(prev.get("tables", []))
                sec.setdefault("figures", []).extend(prev.get("figures", []))
                kept[-1] = sec
            else:
                # 추가: 반대로 sec가 버려지는 경우엔 sec의 표/그림을 prev 쪽으로 이관
                kept[-1].setdefault("tables", []).extend(sec.get("tables", []))
                kept[-1].setdefault("figures", []).extend(sec.get("figures", []))
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
 
    ap = argparse.ArgumentParser(description="Docling 본문 섹션 추출 (빈도 기반 junk 탐지)")
    ap.add_argument("pdf", help="파싱할 PDF 경로")
    ap.add_argument("--min-repeat", type=int, default=JUNK_HEAD_REPEAT_THRESHOLD,
                   help=f"junk header 탐지 threshold (기본값: {JUNK_HEAD_REPEAT_THRESHOLD})")
    ap.add_argument("--show-removed", action="store_true", help="걸러낸/의심 항목 로그 출력")
    ap.add_argument("--show-stats", action="store_true", help="분석 통계 출력")
    args = ap.parse_args()
 
    print(f"Parsing {args.pdf} with Docling ...")
    # 변경: 단독 실행 시에도 이미지가 저장될 폴더명을 알 수 있도록 paper_id를 미리 만들어서 넘김
    pdf_filename = os.path.splitext(os.path.basename(args.pdf))[0]
    auto_paper_id = re.sub(r"[^a-zA-Z0-9가-힣]+", "_", pdf_filename).strip("_").lower()
    out = parse_docling(args.pdf, min_repeat=args.min_repeat, paper_id=auto_paper_id)
    sections = out["sections"]
    removed = out["_removed"]
    stats = out["_stats"]
 
    print("=" * 70)
    print(f"[본문 섹션] {len(sections)}개")
    for i, s in enumerate(sections):
        # 추가: 표/그림 개수도 같이 출력해서 눈으로 확인 가능하게
        n_tables = len(s.get("tables", []))
        n_figures = len(s.get("figures", []))
        print(f"  {i+1:2d}) {s['head'][:60]}  — {len(s['text'])}자, 표 {n_tables}개, 그림 {n_figures}개")
    print("=" * 70)
 
    if args.show_stats:
        print(f"\n[통계]")
        print(f"  원본 섹션: {stats['raw_sections']}")
        print(f"  최종 섹션: {stats['final_sections']}")
        if stats.get('junk_heads_detected'):
            print(f"  자동 탐지된 junk 헤더: {stats['junk_heads_detected']}")
        print(f"  헤더 빈도: {stats.get('head_frequencies', {})}")
        # 추가: 캡션 연결 성공률 출력
        print(f"  그림: 전체 {stats['total_figures']}개 중 캡션 연결 {stats['figures_with_caption']}개")
        print(f"  표:   전체 {stats['total_tables']}개 중 캡션 연결 {stats['tables_with_caption']}개")
 
    if args.show_removed:
        print(f"\n[검수용] 제거/의심 항목 {len(removed)}건")
        for r in removed:
            print(f"  - {r}")
    
    # 파일 저장
    os.makedirs("results", exist_ok=True)
    output_file = f"results/{pdf_filename}_docling.json"
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 결과 저장: {output_file}")
 
 
if __name__ == "__main__":
    main()