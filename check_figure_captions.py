import json, sys

def inspect_figures(docling_json_path: str):
    with open(docling_json_path, encoding="utf-8") as f:
        data = json.load(f)

    total = with_cap = no_cap = 0
    for sec in data["sections"]:
        for fig in sec.get("figures", []):
            total += 1
            if fig.get("caption"):
                with_cap += 1
            else:
                no_cap += 1
                print(f"[캡션없음] 섹션='{sec['head'][:40]}' page={fig.get('page')} bbox={fig.get('bbox')}")

    print(f"\n총 그림 {total}개 / 캡션 있음 {with_cap}개 / 캡션 없음 {no_cap}개")

if __name__ == "__main__":
    inspect_figures(sys.argv[1])