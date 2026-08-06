## 논문 RAG ##

**파이프라인**  

1) Grobid로 메타데이터 추출 (title, doi, abstract, references)
2) Docling으로 본문 섹션 추출 (정제된 sections)
3) 두 결과 병합 (merge)
4) 병합 결과로 청킹 (chunk_sections.py) -> chunk.json, sections_store.json 자동저장
5) 청크들을 임베딩 (embedding.py) -> vectors.json 자동저장
6) 전부 results/ 폴더에 누적 저장

---

**진행사항 및 개선방향**

- 검색결과 LLM에 넘겨서 답변 정확도 확인 (현재 진행중)
- 이미지, 그래프 해석 부분 추가
- LangGraph 검증 루프 추가 (LangGraph 쓸거면 Tavily 같은 검색전용 API 가져오는것도 방법)
- 내용이 부족하다면 인터넷 검색 추가(검색 유사도가 낮을때, LLM 자체적으로 답을 못찾겠다고 판단했을때, 질문 자체가 논문 범위를 넘어설 경우)

---

**명령어 정리**

논문들은 paper-rag-llm 파일 안에 papers에 저장 되어 있고 모든 결과 값들은 results 파일 안에 저장 되어 있다.  

python3 main.py papers/'papers_name'.pdf -> 새로운 논문 추가하면 자동으로 저장됨.
python3 vector_search.py --query "묻고싶은 질문" --top-k 5 -> 이 질문을 벡터로 바꿨을 때, 저장된 청크중 어떤 게 유사도가 가장높은지 보여줌.

---

**주요 알고리즘**

parse_grobid + parse_docling -> 논문 파싱   
chunk_section -> greedy packing, 화학식 정규화, 표 감지.  
embedding -> bge-m3 임베딩 모델
bm25_index -> bm25 검색을 위한 텍스트 전처리
vector_search -> bge-m3 + BM25 하이브리드 벡터 유사도 검색, RRF. 
