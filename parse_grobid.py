'''
Grobid에서는 본문을 제외한 제목 / 초록 / 참고문헌 등을 참고
'''
import sys
import json
import argparse
from lxml import etree
from parse_summary import print_summary


'''
_text 함수는 XML 파일에서 조각을 하나 받아서, 그 안에있는 순수 글자만 깔끔하게 뽑아 하나의 문자열로 반환 ex) 안 녕 하 세 요 => 안녕하세요
'''
def _text(el):
    if el is None:
        return ""
    
    return " ".join("".join(el.itertext()).split())


'''
call_grobid 가 Grobid 서버에 pdf 문서를 보내고 Grobid 에서 보기좋게 잘라주면 다시가져와서 저장

pdf_path: str -> pdf 파일이 글자 형식이여야함을 보여줌 / -> bytes 결과값 XML 파일이 이진수 형태임을 알려줌 => 둘다 편의를 위해서 써둠
/api/processFulltextDocument -> GROBID 서버안에서 논문을 파싱해주는 구체적인 요청 주소
open(pdf_path, "rb") -> pdf 파일을 여는데 rb(read binary) 형태로 열기
request.post() -> 데이터를 서버에 전송 / files, data 등은 어떤 파일일지, 어떤 형식으로 반환을 원하는지 
.raise_for_status() -> 에러가 있나 확인 200 번호대면 성공
'''
def call_grobid(pdf_path: str, server: str) -> bytes:
    import requests
    url = f"{server.rstrip('/')}/api/processFulltextDocument"

    with open(pdf_path, "rb") as f:
        resp = requests.post(
            url,
            files = {"input": f},
            data = {"consolidateHeader": "1",
                    "consolidateCitation": "0",
                    "segmentSentences": "0",
                    },
            timeout = 300,
        )
    resp.raise_for_status()
    return resp.content


'''
GROBID에 PDF 파일을 넣으면 나오는 결과가 TEI 형식의 XML 파일
TEI에 주소는 그냥 고유 ID 
'''
TEI = "http://www.tei-c.org/ns/1.0"
NS  = {"t": TEI}


'''
parse_tei 가 Grobid에서 call_grobid 으로 가져온 XML 파일을 -> 제목 / 초록 / 본문 섹션들 / 참고문헌 등의 목록으로 나누어 파이썬 딕셔너리 하나로 정리해서 반환

doi -> 논문 고유 식별 번호
'''
def parse_tei(tei_bytes: bytes) -> dict:
    '''
    제목, 초록, 개요 찾기
    '''
    root = etree.fromstring(tei_bytes)
    title_el = root.find(".//t:teiHeader//t:titleStmt/t:title", NS)
    doi_el = root.find('.//t:sourceDesc//t:idno[@type="DOI"]', NS)
    abstract_el = root.find(".//t:profileDesc/t:abstract", NS)
    
    '''
    진짜 중요한 바디 내용부분들 하나씩 가져와서 번호붙이고 저장해두기

    body 안에서 div 를 하나씩 가져와서 고유 번호(n) 붙여줌
    div 안에 제목이나 본문내용이있으면 sections 딕션어리에 추가
    '''
    sections = []
    for div in root.findall(".//t:text/t:body/t:div", NS):
        head_el = div.find("t:head", NS)
        head = _text(head_el)
        n = head_el.get("n") if head_el is not None else None

        paras = []
        for p in div.findall("t:p", NS):
            paras.append(_text(p))

        body = "\n\n".join(p for p in paras if p)
        if head or body:
            sections.append({"n": n, "head": head, "text": body})


    '''
    참고문헌 추출하기 => 나중에 검색 대상에서 제외를 위해 따로보관(참고문헌을 검색에 포함시키면 나중에 오류많이생김)
    bib -> bibliography 의 약자로 참고문헌을 나타냄
    '''
    references = []
    for bib in root.findall(".//t:text/t:back//t:listBibl/t:biblStruct", NS):
        ref_title = _text(bib.find(".//t:title", NS))
        ref_doi_el = bib.find('.//t:idno[@type="DOI"]', NS)
        surnames = [_text(s) for s in bib.findall(".//t:author//t:surname", NS)]

        references.append({
            "title": ref_title,
            "authors": surnames,
            "doi": ref_doi_el.text if ref_doi_el is not None else None,
        })

        
    ''' 
    제목 / 초록 / 개요/ 본문 내용정리 / 참고문헌 추출 등에 파일 정리를 다하고 아래와 같은 형태로 모든 정보를 저장
    {
        "title": "논문 제목",
        "doi": "10.1234/example",
        "abstract": "요약 텍스트",
        "sections": [
            {"n": "1", "head": "소개", "text": "..."},
            {"n": "2", "head": "방법", "text": "..."}
        ],
        "references": [
            {"title": "참고1", "authors": ["Kim"], "doi": "..."}
        ]
    }
    '''
    return {
        "title": _text(title_el),
        "doi": doi_el.text if doi_el is not None else None,
        "abstract": _text(abstract_el),
        "sections": sections,
        "references": references,
    }



