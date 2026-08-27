'''
backfill_paper_registry.py — 기존에 이미 등록된 논문들을 paper_registry.json에 채워넣기

중복 논문 감지 기능(paper_registry.py)이 생기기 전에 이미 파이프라인을 돌려놓은 논문들은
레지스트리에 없는 상태라, 이 논문들을 다른 파일명으로 다시 올려도 중복으로 안 잡힘.
이 스크립트를 한 번 돌리면 papers/ 안의 PDF와 results/*_grobid.json에 저장된 DOI를 이용해서
레지스트리를 채워줌. (이미 파이프라인 안 돌린 papers/ 파일은 grobid 결과가 없어서 건너뜀)

사용법:
    python3 backfill_paper_registry.py
'''

import os
import json

from main import make_paper_id_from_filename
from paper_registry import compute_file_hash, load_registry, save_registry, register_paper

PAPERS_DIR = "papers"
RESULTS_DIR = "results"


def main():
    registry_path = os.path.join(RESULTS_DIR, "paper_registry.json")
    registry = load_registry(registry_path)

    pdf_files = [f for f in os.listdir(PAPERS_DIR) if f.lower().endswith(".pdf")]
    added, skipped = 0, 0

    for filename in pdf_files:
        pdf_path = os.path.join(PAPERS_DIR, filename)
        pdf_filename = os.path.splitext(filename)[0]
        paper_id = make_paper_id_from_filename(pdf_path)

        grobid_json_path = os.path.join(RESULTS_DIR, f"{pdf_filename}_grobid.json")
        if not os.path.exists(grobid_json_path):
            print(f"  건너뜀 (grobid 결과 없음, 아직 파이프라인 안 돌린 논문): {filename}")
            skipped += 1
            continue

        with open(grobid_json_path, "r", encoding="utf-8") as f:
            grobid_doc = json.load(f)
        doi = grobid_doc.get("doi", "")

        file_hash = compute_file_hash(pdf_path)
        register_paper(registry, paper_id, file_hash=file_hash, doi=doi, source_filename=filename)
        print(f"  등록됨: {paper_id} (doi: {doi or '없음'})")
        added += 1

    save_registry(registry, registry_path)
    print(f"\n완료: {added}개 등록, {skipped}개 건너뜀 -> {registry_path}")


if __name__ == "__main__":
    main()
