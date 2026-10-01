"""プロジェクトのディスク永続(slide-studio/projects/<pid>/)。単一真実源=project.json。"""
from __future__ import annotations
import json, os, pathlib, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECTS = ROOT / "projects"
PROJECTS.mkdir(exist_ok=True)


def _pid() -> str:
    return "p_" + os.urandom(5).hex()


def pdir(pid: str) -> pathlib.Path:
    return PROJECTS / pid


def create(title: str = "無題のスライド") -> dict:
    pid = _pid()
    d = pdir(pid)
    (d / "sources").mkdir(parents=True, exist_ok=True)
    (d / "out").mkdir(parents=True, exist_ok=True)
    proj = {
        "pid": pid, "title": title, "created": int(time.time()),
        "sources": [], "deck": None, "recommend": None, "render": None,
    }
    save(proj)
    return proj


def load(pid: str) -> dict | None:
    f = pdir(pid) / "project.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save(proj: dict) -> None:
    f = pdir(proj["pid"]) / "project.json"
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)   # 原子的更新(並行writeでの破損防止)


FAV_FILE = ROOT / "favorites.json"


def get_favorites() -> list:
    if FAV_FILE.exists():
        try:
            v = json.loads(FAV_FILE.read_text(encoding="utf-8"))
            return v if isinstance(v, list) else []
        except (json.JSONDecodeError, OSError):
            return []
    return []


def toggle_favorite(name: str) -> list:
    favs = get_favorites()
    if name in favs:
        favs.remove(name)
    else:
        favs.append(name)
    tmp = FAV_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(favs, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, FAV_FILE)
    return favs


def list_all() -> list[dict]:
    out = []
    for d in sorted(PROJECTS.glob("p_*"), key=lambda p: p.stat().st_mtime, reverse=True):
        p = load(d.name)
        if not p:   # 壊れた/欠損 project.json は一覧全体を落とさずスキップ
            continue
        out.append({"pid": p.get("pid", d.name), "title": p.get("title", "無題"),
                    "created": p.get("created", 0),
                    "slides": len((p.get("deck") or {}).get("slides", [])),
                    "has_render": bool(p.get("render"))})
    return out
