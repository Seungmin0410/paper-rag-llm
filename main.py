'''
main.py (전체 자동화 버전)

PDF 파일 하나를 넣으면:
  1) Grobid로 메타데이터 추출 (title, doi, abstract, references)
  2) Docling으로 본문 섹션 추출 (정제된 sections)
  3) 두 결과 병합 (merge)
  4) 병합 결과로 청킹 (chunk_sections.py)
  5) 청크들을 임베딩 (embedding.py)
  6) 전부 results/ 폴더에 누적 저장

전제 조건
  - Grobid 서버가 실행 중이어야 함 (기본: http://localhost:8070)

=== 사용법 ===
    # --paper-id 생략하면 파일명에서 자동 생성됨 (2023_From.pdf -> 2023_from)
    python3 main.py papers/2023_From.pdf

    # 직접 이름 지정하고 싶으면
    python3 main.py papers/2023_From.pdf --paper-id my_custom_name

    # Grobid 서버 주소 바꾸고 싶으면
    python3 main.py papers/2023_From.pdf --server http://localhost:8070

    # 임베딩까지 말고 청킹까지만 하고 싶으면
    python3 main.py papers/2023_From.pdf --skip-embedding

    # 이미 등록된 논문(파일 해시 또는 DOI 일치)이어도 강제로 추가하고 싶으면
    python3 main.py papers/2023_From.pdf --force
'''


import os
import re
import sys
import json
import argparse
from parse_grobid import call_grobid, parse_tei
from parse_docling import parse_docling
from chunk_section import build_chunks, upsert_chunks_store, upsert_sections_store
from embedding import load_model, embed_chunks, upsert_vectors_store
from bm25_index import build_bm25_entries, upsert_bm25_store
from paper_registry import (
    DuplicatePaperError,
    compute_file_hash,
    load_registry,
    save_registry,
    find_by_file_hash,
    find_by_doi,
    register_paper,
)

'''
paper_id를 --paper-id 없이 넣었을 때, PDF 파일명에서 자동으로 만들어주는 함수
예) "2023_From.pdf" -> "2023_from"
    "2022 Electrochemically Final(v2).pdf" -> "2022_electrochemically_final_v2"

규칙: 확장자 제거 -> 소문자로 -> 영문/숫자/한글 아닌 것들은 전부 "_"로 치환
     -> 연속된 "_"는 하나로 압축 -> 앞뒤 "_" 제거
'''
def make_paper_id_from_filename(pdf_path: str) -> str:
    filename = os.path.splitext(os.path.basename(pdf_path))[0]
    pid = filename.lower()
    pid = re.sub(r"[^a-z0-9가-힣]+", "_", pid)
    pid = re.sub(r"_+", "_", pid).strip("_")
    return pid


'''
grobid + docling 필요한 부분들만 가져와서 합치기
'''
def merge_results(grobid_doc: dict, docling_out: dict) -> dict:
    '''
    title/doi/abstract -> Grobid
    sections           -> Docling
    references         -> Grobid 개수만 저장
    '''
    return {
        "title": grobid_doc.get("title", ""),
        "doi": grobid_doc.get("doi", ""),
        "abstract": grobid_doc.get("abstract", ""),
        "sections": docling_out.get("sections", []),
        "references": len(grobid_doc.get("references", [])),
    }

'''
메인 함수
'''
def run_pipeline(pdf_path: str, paper_id: str, server: str,
                  results_dir: str = "results",
                  target_size: int = 700, overlap: int = 80,
                  batch_size: int = 16, skip_embedding: bool = False,
                  force: bool = False, on_progress=None):
    # on_progress(message): 웹 업로드 화면처럼 단계별 진행상황을 실시간으로 보여주고 싶을 때 쓰는 훅.
    # CLI에서는 안 넘기면 그냥 아무 일도 안 함 (print()로 충분하니까).
    if on_progress is None:
        on_progress = lambda message: None

    os.makedirs(results_dir, exist_ok=True)
    pdf_filename = os.path.splitext(os.path.basename(pdf_path))[0]

    registry_path = os.path.join(results_dir, "paper_registry.json")
    registry = load_registry(registry_path)

    # === 0) 중복 체크 1차: 파일 해시 ===
    # 완전히 같은 PDF를 다른 이름으로 다시 넣는 흔한 실수를 Grobid/Docling 돌리기 전에 빠르게 걸러냄
    print("=" * 70)
    print(f"[0/5] 중복 논문 체크 중... (파일 해시)")
    print("=" * 70)
    file_hash = compute_file_hash(pdf_path)
    existing_by_hash = find_by_file_hash(registry, file_hash)
    if existing_by_hash and not force:
        raise DuplicatePaperError(
            f"이미 등록된 논문과 완전히 동일한 파일입니다 (기존 paper_id: '{existing_by_hash}'). "
            f"그래도 추가하려면 --force 옵션을 사용하세요.",
            existing_paper_id=existing_by_hash,
            match_type="file_hash",
        )
    print("  중복 아님 (새 파일)")
    on_progress("중복 논문 체크 완료 (파일 해시)")

    # === 1) Grobid ===
    print("\n" + "=" * 70)
    print(f"[1/5] Grobid 파싱 중... ({pdf_path})")
    print("=" * 70)
    tei_bytes = call_grobid(pdf_path, server)
    grobid_doc = parse_tei(tei_bytes)

    # === 중복 체크 2차: DOI ===
    # 파일명/판본이 달라 해시로는 못 잡아도, Grobid가 뽑은 DOI가 같으면 같은 논문으로 판단
    doi = grobid_doc.get("doi", "")
    existing_by_doi = find_by_doi(registry, doi)
    if existing_by_doi and not force:
        raise DuplicatePaperError(
            f"DOI가 이미 등록된 논문과 같습니다 (DOI: {doi}, 기존 paper_id: '{existing_by_doi}'). "
            f"파일명이나 판본이 다를 뿐 같은 논문으로 보입니다. "
            f"그래도 추가하려면 --force 옵션을 사용하세요.",
            existing_paper_id=existing_by_doi,
            match_type="doi",
        )

    grobid_out_path = os.path.join(results_dir, f"{pdf_filename}_grobid.json")
    with open(grobid_out_path, "w", encoding="utf-8") as f:
        json.dump(grobid_doc, f, ensure_ascii=False, indent=2)
    print(f"  제목: {grobid_doc.get('title', '')[:60]}")
    print(f"  섹션(원본): {len(grobid_doc.get('sections', []))}개")
    print(f"  참고문헌: {len(grobid_doc.get('references', []))}건")
    print(f"  저장됨: {grobid_out_path}")
    on_progress("Grobid 파싱 완료")

    # === 2) Docling ===
    print("\n" + "=" * 70)
    print(f"[2/5] Docling 파싱 중... ({pdf_path})")
    print("=" * 70)
    docling_out = parse_docling(pdf_path, paper_id=paper_id)  # 변경: paper_id 추가 (results/images/{paper_id}/에 그림 저장하기 위해 필요)

    docling_out_path = os.path.join(results_dir, f"{pdf_filename}_docling.json")
    with open(docling_out_path, "w", encoding="utf-8") as f:
        json.dump(docling_out, f, ensure_ascii=False, indent=2)
    print(f"  정제된 섹션: {len(docling_out.get('sections', []))}개")
    print(f"  저장됨: {docling_out_path}")
    on_progress("Docling 파싱 완료")

    # === 3) 병합 ===
    print("\n" + "=" * 70)
    print(f"[3/5] Grobid + Docling 병합 중...")
    print("=" * 70)
    merged = merge_results(grobid_doc, docling_out)

    merged_out_path = os.path.join(results_dir, f"{pdf_filename}_merged.json")
    with open(merged_out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"  최종 섹션 수: {len(merged['sections'])}개 (Docling 본문 사용)")
    print(f"  저장됨: {merged_out_path}")

    # === 4) 청킹 ===
    print("\n" + "=" * 70)
    print(f"[4/5] 청킹 중... (paper_id: {paper_id})")
    print("=" * 70)
    sections_store, chunks = build_chunks(
        merged, paper_id, target_size=target_size, overlap_size=overlap
    )

    chunks_path = os.path.join(results_dir, "chunks.json")
    sections_path = os.path.join(results_dir, "sections_store.json")

    all_chunks = upsert_chunks_store(chunks_path, paper_id, chunks)
    all_sections = upsert_sections_store(sections_path, paper_id, sections_store)

    print(f"  이번 논문 섹션 수: {len(sections_store)}")
    print(f"  이번 논문 청크 수: {len(chunks)}")
    print(f"  누적 저장됨 (청크): {chunks_path} (전체 {len(all_chunks)}개)")
    print(f"  누적 저장됨 (섹션): {sections_path} (전체 {len(all_sections)}개)")

    '''
    bm25 부분 추가
    '''
    print("\n" + "=" * 70)
    print(f"[BM25] 키워드 검색 인덱스 생성 중...")
    print("=" * 70)
    bm25_path = os.path.join(results_dir, "bm25_corpus.json")
    bm25_entries = build_bm25_entries(chunks)
    all_bm25 = upsert_bm25_store(bm25_path, paper_id, bm25_entries)
    print(f"  이번 논문 BM25 엔트리: {len(bm25_entries)}개")
    print(f"  누적 저장됨 (BM25): {bm25_path} (전체 {len(all_bm25)}개)")
    on_progress("청킹 + BM25 인덱스 생성 완료")

    # 여기까지 왔으면 메타데이터 파싱+청킹은 끝난 상태 -> 중복 체크용 레지스트리에 등록
    # (임베딩 완료 여부와 무관하게, 이후 같은 논문이 다시 들어오는 걸 잡아내는 게 목적)
    register_paper(registry, paper_id, file_hash=file_hash, doi=doi, source_filename=os.path.basename(pdf_path))
    save_registry(registry, registry_path)

    # === 5) 임베딩 ===
    if skip_embedding:
        print("\n[5/5] --skip-embedding 지정됨 → 임베딩 건너뜀")
        on_progress("완료 (임베딩 건너뜀)")
        return

    print("\n" + "=" * 70)
    print(f"[5/5] 임베딩 중...")
    print("=" * 70)
    model = load_model()
    vectors = embed_chunks(model, chunks, batch_size=batch_size, include_text=False)

    vectors_path = os.path.join(results_dir, "vectors.json")
    all_vectors = upsert_vectors_store(vectors_path, paper_id, vectors)

    dim = len(vectors[0]["embedding"]) if vectors else 0
    print(f"  임베딩 완료: {len(vectors)}개 청크 (벡터 차원: {dim})")
    print(f"  누적 저장됨 (벡터): {vectors_path} (전체 {len(all_vectors)}개)")
    on_progress("임베딩 완료")

    print("\n" + "=" * 70)
    print(f"✅ 전체 파이프라인 완료! (paper_id: {paper_id})")
    print("=" * 70)
    on_progress("완료")


def main():
    ap = argparse.ArgumentParser(
        description="PDF 하나 -> Grobid + Docling + 병합 + 청킹 + 임베딩까지 한 번에"
    )
    ap.add_argument("pdf", help="파싱할 PDF 경로 (예: papers/2023_From.pdf)")
    ap.add_argument("--paper-id", default=None,
                   help="논문 고유 ID. 생략하면 파일명에서 자동 생성됨 (예: 2023_From.pdf -> 2023_from)")
    ap.add_argument("--server", default="http://localhost:8070", help="Grobid 서버 주소")
    ap.add_argument("--results-dir", default="results", help="결과 저장 폴더 (기본값: results)")
    ap.add_argument("--target-size", type=int, default=700, help="청크 목표 크기")
    ap.add_argument("--overlap", type=int, default=80, help="청크 오버랩 크기")
    ap.add_argument("--batch-size", type=int, default=16, help="임베딩 배치 크기")
    ap.add_argument("--skip-embedding", action="store_true", help="임베딩 단계 건너뛰기")
    ap.add_argument("--force", action="store_true",
                     help="이미 등록된 논문(파일 해시 또는 DOI 일치)이어도 강제로 추가")
    args = ap.parse_args()

    # --paper-id 안 줬으면 파일명에서 자동 생성
    paper_id = args.paper_id if args.paper_id else make_paper_id_from_filename(args.pdf)
    if not args.paper_id:
        print(f"ℹ️  --paper-id 생략됨 → 파일명에서 자동 생성: '{paper_id}'\n")

    try:
        run_pipeline(
            pdf_path=args.pdf,
            paper_id=paper_id,
            server=args.server,
            results_dir=args.results_dir,
            target_size=args.target_size,
            overlap=args.overlap,
            batch_size=args.batch_size,
            skip_embedding=args.skip_embedding,
            force=args.force,
        )
    except Exception as e:
        print(f"\n❌ 파이프라인 중단: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()