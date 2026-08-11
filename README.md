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

python3 chunk_section.py results/논문이름_merged.json --paper-id 논문이름 --out-chunks results/chunks.json --out-sections results/sections_store.json -> 청킹만 따로 다시하고싶을때

python3 bm25_index.py results/chunks.json --out results/bm25_corpus.json -> 각 청크의 텍스트를 BM25 검색용으로 전처리  
python3 embedding.py results/chunks.json --paper-id 논문이름 --out results/vectors.json -> 임베딩만 다로 다시하고 싶을때  
python3 embedding.py results/chunks.json --all --out results/vectors.json -> 전체 논문 임베딩 다시 하고싶을때  

python3 vector_search.py --query "묻고싶은 질문" --top-k 5 -> 이 질문을 벡터로 바꿨을 때, 저장된 청크중 어떤 게 유사도가 가장높은지 보여줌.

---

**주요 알고리즘**

parse_grobid + parse_docling -> 논문 파싱     
chunk_section -> greedy packing, 화학식 정규화, 표 감지.    
embedding -> bge-m3 임베딩 모델  
bm25_index -> bm25 검색을 위한 텍스트 전처리  
vector_search -> bge-m3 + BM25 하이브리드 벡터 유사도 검색, RRF. 

---

**문제 해결 과정 요약**

1. Grobid로 파싱 → 본문 내용이 잘 안 파싱됨

2. Docling 도입 → 본문은 잘 파싱되지만, table/figure는 파싱 로직 자체가 없어 통째로 버려짐

3. parse_docling.py 수정 → table/figure/caption 라벨 수집 로직 추가
   → 표는 caption 유무 상관없이 다 살리고, 그림은 caption 있는 것만 살리기로 결정
   (caption 없는 그림 = 로고 등 장식 이미지로 판단)

4. chunk_section.py 수정 → 저장된 tables/figures를 실제 청크로 변환

5. 그런데도 표 안에 답이 있는 질문에 답변을 못 함
   → 원인: 파싱/청킹은 정상, 문제는 검색 단계
   → 표는 마크다운(파이프 기호+숫자) 형태라 자연어 질문과 벡터 유사도가
     낮게 나와서, caption이 정상이어도 검색 순위에서 자주 밀려남

6. 해결 (방향 A): 검색된 청크가 속한 섹션의 table/figure를
   검색 순위와 무관하게 무조건 프롬프트에 포함하도록 수정
   
   추후 해결 과제: 관련없는 table/figure들이 답변에 질을 낮출수있음. 그리고 많은 양의 데이터를 LLM이 읽기때문에 토큰 사용량 증가 