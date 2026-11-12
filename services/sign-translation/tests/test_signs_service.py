"""Traducción a señas: fiabilidad ante glosas desconocidas (QA-12), vocabulario por API sin
redespliegue (QA-04), repositorio y propagación de la traza."""
import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rsp_common.events import GesturePlan, GlossSequence, Subjects
from rsp_testkit import make_token


@pytest.fixture
def st(tmp_path, monkeypatch, fake_bus, auth_env):
    monkeypatch.setenv("SIGNS_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("SIGNS_SEED_PATH", str(tmp_path / "no-existe.json"))
    import app.main as m
    from app.repository import Sign
    for ch in "ACMS":
        m.repo.upsert(Sign(gloss=ch, angles=[90] * 15))
    m.repo.upsert(Sign(gloss="MAMA", angles=[100] * 15, kichwa="mama"))
    m.svc.bus = fake_bus
    return m, TestClient(m.app), {"Authorization": f"Bearer {make_token()}"}


def test_unknown_gloss_without_letters_plan_still_runs_and_gap_is_reported(st):
    """QA-12: palabra sin seña ni letras → se ejecuta lo conocido y la brecha queda registrada."""
    m, _, _ = st
    ev = GlossSequence(session_id="s1", source_text="mamá 123 ñ", glosses=["MAMA", "123", "Ñ"],
                       trace={"captured": 1, "transcribed": 2, "glossed": 3})
    asyncio.run(m.handle_glosses(ev))
    (out,) = m.svc.bus.events(Subjects.GESTURE_PLAN)
    assert isinstance(out, GesturePlan)
    assert [g.gloss for g in out.gestures] == ["MAMA"]
    assert out.unknown_glosses == ["123", "Ñ"]
    assert out.trace == {"captured": 1, "transcribed": 2, "glossed": 3, "planned": out.trace["planned"]}


def test_partial_fingerspelling_reports_missing_letters(st):
    """QA-12: la letra sin seña no se pierde en silencio; se reporta como PALABRA:L."""
    m, _, _ = st
    gestures, unknown = m.plan_for(["CASA", "MAZ"])
    assert [g.gloss for g in gestures] == ["CASA:C", "CASA:A", "CASA:S", "CASA:A", "MAZ:M", "MAZ:A"]
    assert unknown == ["MAZ:Z"] and all(g.hold_ms == m.FINGERSPELL_HOLD_MS for g in gestures)


def test_word_with_enie_falls_back_to_legacy_gloss_but_letter_does_not(st):
    """Vocabularios migrados guardan MANANA; MAÑANA debe encontrarla. La letra Ñ no cae en la N."""
    m, _, _ = st
    m.repo.upsert(m.Sign(gloss="MANANA", angles=[90] * 15))
    assert m.repo.get("MAÑANA").gloss == "MANANA"
    assert m.repo.get("Ñ") is None


def test_fingerspell_option_travels_in_event(st):
    m, _, _ = st
    ev = GlossSequence(session_id="s", source_text="casa", glosses=["CASA"], options={"fingerspell": False})
    asyncio.run(m.handle_glosses(ev))
    (out,) = m.svc.bus.events(Subjects.GESTURE_PLAN)
    assert out.gestures == [] and out.unknown_glosses == ["CASA"] and out.options == {"fingerspell": False}


def test_add_ten_signs_by_api_are_used_immediately(st):
    """QA-04: 10 señas nuevas por API → disponibles en el siguiente plan, sin recompilar ni desplegar."""
    m, c, h = st
    new = [f"PALABRA{i}" for i in range(10)]
    for i, gloss in enumerate(new):
        r = c.put(f"/signs/{gloss.lower()}", json={"gloss": "x", "angles": [80 + i] * 15}, headers=h)
        assert r.status_code == 200 and r.json()["gloss"] == gloss
    gestures, unknown = m.plan_for(new, fingerspell=False)
    assert [g.gloss for g in gestures] == new and unknown == []
    assert gestures[3].targets[0].angle == 83


def test_vocabulary_writes_require_token_reads_do_not(st):
    m, c, h = st
    assert c.put("/signs/X", json={"gloss": "X", "angles": [90] * 15}).status_code == 401
    assert c.delete("/signs/MAMA").status_code == 401
    assert c.get("/signs/MAMA").status_code == 200


def test_sign_crud_and_errors(st):
    m, c, h = st
    assert c.get("/signs/MAMA").json()["kichwa"] == "mama"
    assert c.get("/signs/NADA").status_code == 404
    assert c.get("/signs/plan").status_code == 405
    assert c.put("/signs/X", json={"gloss": "X", "angles": [90] * 14}, headers=h).status_code == 422
    assert c.delete("/signs/mama", headers=h).json() == {"deleted": "MAMA"}
    assert c.delete("/signs/mama", headers=h).status_code == 404
    listed = c.get("/signs?prefix=m").json()
    assert listed["count"] == 4 and [s["gloss"] for s in listed["items"]] == ["M"]


@pytest.mark.parametrize("path", ["/plan", "/signs/plan"])
def test_sync_plan_endpoint(st, path):
    m, c, _ = st
    r = c.post(path, json={"glosses": ["mama", "zz"], "fingerspell": True}).json()
    assert [g["gloss"] for g in r["gestures"]] == ["MAMA"] and r["unknown_glosses"] == ["ZZ"]


# ------------------------------------------------------------------ repositorio
def test_sign_model_requires_15_angles():
    from app.repository import Sign
    with pytest.raises(ValidationError):
        Sign(gloss="A", angles=[90] * 16)


def test_import_only_new_keeps_team_edits_and_fills_kichwa(tmp_path):
    import json
    from app.repository import Sign, SignRepository
    repo = SignRepository(tmp_path / "r.db")
    repo.upsert(Sign(gloss="AGUA", angles=[1] * 15, source="equipo"))
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps([
        {"gloss": "AGUA", "angles": [99] * 15, "kichwa": "yaku"},
        {"gloss": "SOL", "angles": [50] * 15, "kichwa": "inti"},
    ]), encoding="utf-8")
    assert repo.import_json(seed, only_new=True) == 1
    agua = repo.get("agua")
    assert agua.angles == [1] * 15 and agua.source == "equipo" and agua.kichwa == "yaku"
    assert repo.get("SOL").kichwa == "inti" and repo.count() == 2


def test_schema_migration_adds_kichwa_column(tmp_path):
    db = tmp_path / "v1.db"
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE signs (gloss TEXT PRIMARY KEY, angles TEXT NOT NULL, image_path TEXT,
                 hold_ms INTEGER NOT NULL DEFAULT 600, source TEXT NOT NULL DEFAULT 'manual',
                 updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    c.execute("INSERT INTO signs (gloss, angles) VALUES ('A', ?)", ("[" + ",".join(["90"] * 15) + "]",))
    c.commit()
    c.close()
    from app.repository import SignRepository
    repo = SignRepository(db)
    assert repo.get("a").kichwa is None and repo.get("A").angles == [90] * 15


def test_upsert_normalizes_and_overwrites(tmp_path):
    from app.repository import Sign, SignRepository
    repo = SignRepository(tmp_path / "r.db")
    repo.upsert(Sign(gloss="  hola ", angles=[1] * 15))
    repo.upsert(Sign(gloss="HOLA", angles=[2] * 15, image_path="h.png"))
    assert repo.count() == 1 and repo.get("hola").angles == [2] * 15 and repo.get("HOLA").image_path == "h.png"
    assert repo.delete("nada") is False


def test_startup_seeds_only_when_file_exists(tmp_path, monkeypatch, fake_bus):
    import json
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps([{"gloss": "SOL", "angles": [50] * 15}]), encoding="utf-8")
    monkeypatch.setenv("SIGNS_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("SIGNS_SEED_PATH", str(seed))
    import app.main as m
    m.svc.bus = fake_bus
    asyncio.run(m.start())
    assert m.repo.count() == 1 and fake_bus.subscriptions == [Subjects.GLOSS_SEQUENCE]


def test_sign_motion_travels_as_keyframes_and_is_validated(st):
    """Seña con movimiento: las poses de `motion` llegan al plan como keyframes de 15 servos."""
    m, c, h = st
    assert c.put("/signs/HOLA", headers=h, json={"gloss": "HOLA", "angles": [90] * 15,
                                                 "motion": [[100] * 15, [80] * 15]}).status_code == 200
    gestures, _ = m.plan_for(["HOLA"])
    assert [[t.angle for t in pose] for pose in gestures[0].keyframes] == [[100] * 15, [80] * 15]
    bad = c.put("/signs/MAL", headers=h, json={"gloss": "MAL", "angles": [90] * 15, "motion": [[1, 2]]})
    assert bad.status_code == 422
