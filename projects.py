"""
projects.py — 프로젝트별 노하우 파일 관리

배경설명(background.txt)은 전체 프로젝트 공통이라 그대로 두고,
노하우(notes)만 프로젝트별로 나눠서 notes/<project_id>.txt에 저장함.
results/projects.json에 어떤 프로젝트가 있는지(id, 이름, 파일 경로) 기록해둠.
"""

import os
import re
import json
import datetime

PROJECTS_PATH = "results/projects.json"
NOTES_DIR = "notes"


def slugify(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9가-힣]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "project"


def load_projects(path: str = PROJECTS_PATH) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_projects(projects: list, path: str = PROJECTS_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)


def get_project(projects: list, project_id: str):
    for p in projects:
        if p["id"] == project_id:
            return p
    return None


'''
새 프로젝트 생성 -> id는 이름에서 자동 생성(중복되면 뒤에 숫자 붙임), 빈 노하우 파일도 같이 만듦
'''
def create_project(name: str, projects_path: str = PROJECTS_PATH, notes_dir: str = NOTES_DIR) -> dict:
    projects = load_projects(projects_path)

    base_id = slugify(name)
    project_id = base_id
    i = 2
    while get_project(projects, project_id):
        project_id = f"{base_id}_{i}"
        i += 1

    notes_path = os.path.join(notes_dir, f"{project_id}.txt")
    os.makedirs(notes_dir, exist_ok=True)
    if not os.path.exists(notes_path):
        with open(notes_path, "w", encoding="utf-8") as f:
            f.write("")

    project = {"id": project_id, "name": name, "notes_path": notes_path}
    projects.append(project)
    save_projects(projects, projects_path)
    return project


def read_notes(notes_path: str) -> str:
    if not os.path.exists(notes_path):
        return ""
    with open(notes_path, "r", encoding="utf-8") as f:
        return f.read()


'''
노하우 항목 하나 추가 -> 오늘 날짜 자동으로 붙여서 파일 맨 아래에 이어붙임
'''
def append_note_entry(notes_path: str, title: str, body: str) -> None:
    today = datetime.date.today()
    date_str = f"{today.month}/{today.day}"
    entry = f"{date_str} · {title}\n{body}\n\n"

    os.makedirs(os.path.dirname(notes_path) or ".", exist_ok=True)
    with open(notes_path, "a", encoding="utf-8") as f:
        f.write(entry)


def ensure_default_projects(projects_path: str = PROJECTS_PATH, notes_dir: str = NOTES_DIR) -> list:
    projects = load_projects(projects_path)
    if projects:
        return projects
    for name in ("ROC", "AL"):
        create_project(name, projects_path, notes_dir)
    return load_projects(projects_path)
