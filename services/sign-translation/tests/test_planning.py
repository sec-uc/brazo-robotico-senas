import sys, pathlib, json, importlib, os
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def make_repo(tmp_path):
    from app.repository import SignRepository, Sign
    repo = SignRepository(tmp_path / "signs.db")
    for ch in "ABCM":
        repo.upsert(Sign(gloss=ch, angles=[90] * 15))
    repo.upsert(Sign(gloss="MAMA", angles=[100] * 15, image_path="img/mama.png"))
    return repo


def test_repo_upsert_get_list(tmp_path):
    repo = make_repo(tmp_path)
    assert repo.count() == 5
    assert repo.get("mama").image_path == "img/mama.png"
    assert [s.gloss for s in repo.list(prefix="M")] == ["M", "MAMA"]
    assert repo.delete("C") and repo.get("C") is None


def test_plan_sign_fingerspell_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNS_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("SIGNS_SEED_PATH", str(tmp_path / "none.json"))
    import app.main as m
    importlib.reload(m)
    m.repo = make_repo(tmp_path)
    gestures, unknown = m.plan_for(["MAMA", "CAB", "XYZ"])
    kinds = [(g.gloss, g.kind) for g in gestures]
    assert kinds[0] == ("MAMA", "sign")
    assert kinds[1:] == [("CAB:C", "fingerspell"), ("CAB:A", "fingerspell"), ("CAB:B", "fingerspell")]
    assert unknown == ["XYZ"]
    assert len(gestures[0].targets) == 15


def test_seed_file_is_valid():
    seed = json.loads((ROOT.parents[1] / "data" / "signs_seed.json").read_text(encoding="utf-8"))
    from app.repository import Sign
    signs = [Sign(**s) for s in seed]
    assert len(signs) >= 25 and all(len(s.angles) == 15 for s in signs)  # repositorio público: semilla de ejemplo (data/README.md)
    # la X no está: su seña necesita la punta del índice, un canal del brazo que no responde
    assert {s.gloss for s in signs} >= set("ABCDEFGHIJKLMNOPQRSTUVWYZ")
