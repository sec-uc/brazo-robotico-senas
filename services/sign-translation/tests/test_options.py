import sys, pathlib, importlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_fingerspell_off_reports_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNS_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("SIGNS_SEED_PATH", str(ROOT.parents[1] / "data" / "signs_seed.json"))
    import app.main as m
    importlib.reload(m)
    m.repo.import_json(m.SEED_PATH)
    g, u = m.plan_for(["AGUA", "ZAPATO"], fingerspell=False)
    assert [x.gloss for x in g] == ["AGUA"] and u == ["ZAPATO"]
    assert g[0].kichwa == "yaku"
    g2, u2 = m.plan_for(["ZAPATO"], fingerspell=True)
    assert len(g2) == 6 and u2 == []
