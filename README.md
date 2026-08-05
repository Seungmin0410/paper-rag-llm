## 논문 RAG ##

**파이프라인**  

1) Grobid로 메타데이터 추출 (title, doi, abstract, references)
2) Docling으로 본문 섹션 추출 (정제된 sections)
3) 두 결과 병합 (merge)
4) 병합 결과로 청킹 (chunk_sections.py) -> chunk.json, sections_store.json 자동저장
5) 청크들을 임베딩 (embedding.py) -> vectors.json 자동저장
6) 전부 results/ 폴더에 누적 저장

python3 main.py papers/'papers_name'.pdf -> 새로운 논문 추가하면 자동으로 저장됨.

**진행사항 및 개선방향**

- 검색결과 LLM에 넘겨서 답변 정확도 확인
- 이미지, 표, 그래프 해석 부분 추가
- BM25 하이브리드 검색 추가
- LangGraph 검증 루프 추가

