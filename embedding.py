import json
import argparse
import sys
import os


'''
현재는 bge-m3 이 모델만쓰지만 나중에 BM25 추가해서 하이브리드로 정확성 높이기
'''
MODEL_NAME = "BAAI/bge-m3" 
BATCH_SIZE = 16


'''
처음에 모델 불러오기
'''
def load_model():
    try:
        from sentence_transformers import SentenceTransformer # type: ignore
    except ImportError:
        print("sentence-transformers가 설치되어 있지 않습니다.")
        print("설치: pip install sentence-transformers --break-system-packages")
        sys.exit(1)

    print(f"임베딩 모델 로딩 중: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    return model

'''
청크들 벡터화
'''
def embed_chunks(model, chunks: list[dict], batch_size: int = BATCH_SIZE, include_text: bool = False) -> list[dict]:
    texts = [c["chunk_text"] for c in chunks]
    print(f"총 {len(texts)}개 청크를 임베딩합니다 (배치 크기: {batch_size})...")

    embeddings = model.encode(texts, batch_size = batch_size, show_progress_bar = True, normalize_embeddings = True,)

    results = []
    for chunk, vector in zip(chunks, embeddings):
        ## paper_id도 같이 넣어줌 -> 나중에 vectors.json에 여러 논문이 누적됐을 때
        ## 어느 논문 벡터인지 구분하기 위해 필요함 (chunks.json에 이미 paper_id가 있으니 그대로 가져옴)
        item = {"paper_id": chunk["paper_id"], "section_id": chunk["section_id"], "chunk_index": chunk["chunk_index"], "embedding": vector.tolist(),}

        if include_text:
            item["chunk_text"] = chunk["chunk_text"]
        results.append(item)
    return results


'''
누적 저장(upsert) 관련 함수들
------------------------------
chunk_sections.py에서 한 것과 똑같은 원리.
vectors.json 파일 하나에 여러 논문 벡터를 계속 쌓기 위함.
기존 파일 읽고 -> 이번 paper_id의 옛날 벡터는 지우고 -> 새 벡터 합쳐서 -> 다시 저장.
'''

## 파일이 없으면 그냥 빈 리스트로 시작 (vectors.json 처음 만들 때 대비)
def load_existing_vectors(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

## vectors.json에 이번 논문 벡터들을 누적 저장
def upsert_vectors_store(path: str, paper_id: str, new_vectors: list[dict]) -> list[dict]:
    existing = load_existing_vectors(path)
    kept = [v for v in existing if v.get("paper_id") != paper_id]  ## 이 논문의 옛날 벡터는 버림
    merged = kept + new_vectors
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged


'''
python3 embedding.py results/chunks.json \
    --paper-id 2019_faradaic \
    --out results/vectors.json \
    --with-text => 이부분은 나중에 텍스트 확인하고싶을때만 사용하면됨. 그냥 확인용임

## paper_id를 지정하면 chunks.json(누적된 전체 청크) 중에서
## 그 논문 것만 골라서 임베딩하고, vectors.json에 그 논문 부분만 갱신함.
## 여러 논문을 전부 (다시) 임베딩하고 싶으면 --all 사용:
python3 embedding.py results/chunks.json --all --out results/vectors.json
'''

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="chunk_sections.py가 (누적) 저장한 chunks.json 경로")
    ap.add_argument("--paper-id", help="이 논문의 청크만 골라서 임베딩 (예: 2019_faradaic)")
    ap.add_argument("--all", action="store_true",
                     help="input 파일 안의 모든 paper_id를 전부 임베딩 (한 번에 다 처리하고 싶을 때)")
    ap.add_argument("--out", required=True, help="벡터 결과를 누적 저장할 JSON 경로")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE,
                     help=f"임베딩 배치 크기 (기본값: {BATCH_SIZE})")
    ap.add_argument("--with-text", action="store_true",
                     help="결과에 chunk_text 원문도 같이 저장 (디버깅용, 파일 크기 커짐). "
                          "기본값은 저장 안 함 (가벼움)")
    args = ap.parse_args()

    ## paper_id도 --all도 안 주면 어떤 청크를 임베딩할지 알 수 없으니 에러 처리
    if not args.paper_id and not args.all:
        print("--paper-id 또는 --all 중 하나는 반드시 지정해야 합니다.")
        sys.exit(1)
 
    with open(args.input, "r", encoding="utf-8") as f:
        all_chunks = json.load(f)
 
    if not all_chunks:
        print("입력 파일에 청크가 없습니다. chunk_sections.py를 먼저 실행했는지 확인하세요.")
        sys.exit(1)
 
    model = load_model()

    if args.all:
        ## chunks.json 안에 섞여있는 모든 paper_id를 찾아서 하나씩 순서대로 임베딩
        paper_ids = sorted(set(c["paper_id"] for c in all_chunks))
        print(f"발견된 논문: {paper_ids}")
        for pid in paper_ids:
            target_chunks = [c for c in all_chunks if c["paper_id"] == pid]
            print(f"\n[{pid}] {len(target_chunks)}개 청크 임베딩 중...")
            results = embed_chunks(model, target_chunks, batch_size=args.batch_size,
                                    include_text=args.with_text)
            all_vectors = upsert_vectors_store(args.out, pid, results)
            print(f"  -> 누적 저장됨 (전체 {len(all_vectors)}개 벡터)")
    else:
        ## 지정한 paper_id의 청크만 chunks.json에서 골라냄
        target_chunks = [c for c in all_chunks if c["paper_id"] == args.paper_id]
        if not target_chunks:
            print(f"paper_id '{args.paper_id}'에 해당하는 청크가 없습니다.")
            sys.exit(1)

        results = embed_chunks(model, target_chunks, batch_size=args.batch_size,
                                include_text=args.with_text)

        ## 기존 vectors.json에 이번 논문 벡터만 갱신해서 누적 저장
        all_vectors = upsert_vectors_store(args.out, args.paper_id, results)

        # --- 확인용 요약 ---
        dim = len(results[0]["embedding"]) if results else 0
        print("=" * 60)
        print(f"임베딩 완료: {len(results)}개 청크 (이번 논문)")
        print(f"벡터 차원: {dim}차원")
        print(f"누적 저장됨: {args.out}  (전체 {len(all_vectors)}개 벡터)")
 
 
if __name__ == "__main__":
    main()