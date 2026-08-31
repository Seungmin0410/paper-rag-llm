## 논문 RAG ##

**논문 or PPT Rag를 만드는 의의: 여러 데이터들 안에서 비교/탐색 -> 예를 들어 우리가 논문 500개를 가지고있고 특정 지식을 얻고싶은데 어디서 얻을지 모르는 상황에서 관련 정보를 얻고 싶을때**

---

**주의사항**     
질문을 할때 질문 안에 논문 고유의 영어 전문용어(약어, 소재명 등)는 영문 그대로 사용하는게 좋다.

---

**저장 파이프라인**  

1) Grobid로 메타데이터 추출 (title, doi, abstract, references)
2) Docling으로 본문 섹션 추출 (정제된 sections)
3) 두 결과 병합 (merge)
4) 병합 결과로 청킹 (chunk_sections.py) -> chunk.json, sections_store.json 자동저장
5) 청크들을 임베딩 (embedding.py) -> vectors.json 자동저장
6) 전부 results/ 폴더에 누적 저장

---

**진행사항 및 개선방향**

진행중: 추가 논문 저장

- 그동안 실험했던 ppt 파일들 파싱해서 사용
- 프롬프트를 좀더 우리상황에 맞게 수정? ex) 한달안에 타임라인 추천 등등 
- 노하우 파일 개선? 
- 더 좋은 모델 사용? 
- top-k 의 개수를 늘린다 -> 근데 이러면 내용이 많아져서 무거워지고 LLM 할루시네이션 나올까 두렵
- 정량적인 표(그간 실험 데이터들)나 그래프 추가?

---

**웹 챗 UI**

내부 연구원들이 터미널 없이 브라우저에서 편하게 질문할 수 있는 간단한 채팅 화면 (`app.py` + `templates/` + `static/`).

```
pip3 install -r requirements_webapp.txt   # flask (최초 1회)
export ANTHROPIC_API_KEY="sk-..."          # 아직 설정 안 했다면
python3 app.py                             # http://localhost:5050 접속
```

- 처음 실행하면 `background.txt`가 없을 경우 빈 템플릿을 자동 생성함 → 우리 프로젝트 배경/노하우로 채워 넣으면 챗봇이 답변할 때 참고함.
- `results/`에 저장된 청크·벡터·BM25 인덱스를 그대로 사용하므로, 새 논문을 추가했다면 `main.py`로 먼저 파이프라인을 돌린 뒤 웹 UI를 써야 최신 데이터가 반영됨.
- 개발용 서버(Flask dev server)이므로 사내망 안에서만 띄우고, 외부에 노출하지 않도록 주의.

---

**중복 논문 감지**

같은 논문이 다른 파일명(다른 판본 등)으로 두 번 들어오는 걸 막기 위해 `main.py`가 논문을 추가하기 전에 자동으로 체크함 (`paper_registry.py`, `results/paper_registry.json`에 기록).

1. **파일 해시 체크** — 완전히 동일한 PDF 재업로드를 Grobid 돌리기 전에 걸러냄 (제일 흔한 실수, 빠르게 체크)
2. **DOI 체크** — Grobid가 메타데이터를 뽑은 직후, 파일명·판본이 달라도 DOI가 같으면 같은 논문으로 판단해서 막음
3. **DOI가 완전하지 않을경우** - 특정 저널 논문들은 DOI가 완전하지 않아서 다른 논문인 경우도 같은 논문이라고 판단했음 -> 이 경우 초록에 유사도를 비교해서 같은 논문인지 판단

둘 중 하나라도 걸리면 파이프라인이 중단되고 기존 `paper_id`를 알려줌. 그래도 강제로 추가하고 싶으면 `--force` 옵션 사용:
```
python3 main.py papers/논문.pdf --force
```

이 기능이 생기기 전에 이미 등록해둔 논문들은 최초 1회 아래 스크립트로 레지스트리를 채워야 함 (이미 실행해둔 상태, 새로 논문을 추가하는 거면 다시 돌릴 필요 없음):
```
python3 backfill_paper_registry.py
```

---

**명령어 정리**

논문들은 paper-rag-llm 파일 안에 papers에 저장 되어 있고 모든 결과 값들은 results 파일 안에 저장 되어 있다.  

python3 main.py papers/'papers_name'.pdf -> 새로운 논문 추가하면 자동으로 저장됨.   
python3 rag_llm.py --query "질문내용" --auto   -> 이렇게 하면 API 호출 (--auto 없으면 그냥 복붙해야함)    

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

**이미지, 표 문제 해결 과정 요약**

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

6. 해결 : 검색된 청크가 속한 섹션의 table/figure를
   검색 순위와 무관하게 무조건 프롬프트에 포함하도록 수정
   
   추후 해결 과제: 관련없는 table/figure들이 답변에 질을 낮출수있음. 그리고 많은 양의 데이터를 LLM이 읽기때문에 토큰 사용량 증가 

7. 이미지 파싱하면서 깨달은 건데 우선 docling 안에 라이브러리 같은곳에 자체적으로 table/figure/caption 등을 자동으로 구별해서 저장해주는 기능이 있었음. 그래서 1차적으로 이 로직으로 변경. 그리고 캡션이랑 그림, 테이블 등을 자동으로 연결해주는 기능도 있어서 이걸 사용 -> 근데 여기서 자동으로 캡션 매칭이 안되는 문제점이 생겨서 매칭이 안된 캡션과 그림, 테이블 등을 나중에 연결해주는 fallback 기능 추가.

8. 이후 논문들 더 추가하는 과정에서 특정 그림들(가로로 길게 figure A,B,C 이런식으로 있는그림) 중 일부만 파싱되는 것을 확인 -> 근데 이 문제는 도클링할때 큰 그림일 경우 큰 박스로 A,B,C 이건 하나의 그림이다 이렇게 잡았어야됬는데 C 하나만 잡아버린 것.
