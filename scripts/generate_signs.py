#!/usr/bin/env python3
"""Ajusta el vocabulario del brazo a las imágenes de referencia y rellena lo que falta.

1. Señas con imagen (alfabeto dibujado en assets/images/*.jpeg del cliente y fotos de
   assets/gestures/): se describen dedo a dedo con `hand()` y se escriben así, o se derivan
   de la letra calibrada más parecida cambiando solo la orientación del brazo.
2. Glosas sin seña o con la seña vacía (todo en 0): mano de su primera letra, brazo
   elegido de forma estable entre las posiciones de las señas reales, y la imagen de
   esa letra para que la pantalla muestre lo mismo que hace el brazo.

Articulaciones de las señas (punta/base): meñique 1/6, anular 2/7, medio 3/8, índice 4/9,
pulgar 5/10/11; 12 brazo lateral, 13 muñeca lateral, 14 muñeca adelante/atrás, 15 fijo. El
cableado real del brazo lo traduce actuator-control (hardware.CHANNEL), no estas señas.

Nada de esto está validado por un intérprete de LSEC: se marca con source
"generado-sin-validar" / "derivado-sin-validar". Señas editadas por el equipo desde la
API (otro source) no se tocan.

  python scripts/generate_signs.py            # muestra lo que haría
  python scripts/generate_signs.py --apply    # lo escribe vía gateway (PUT /v1/signs/{glosa})
  python scripts/generate_signs.py --remove   # lo deshace (antes de run_evaluation.sh: con estas
                                              # señas, palabras que se deletreaban pasan a 1 gesto)
"""
from __future__ import annotations

import argparse
import json
import os
import unicodedata
import urllib.request
from pathlib import Path

API = "http://localhost:8080"
SOURCE = "generado-sin-validar"
LETTER_SOURCE = "derivado-sin-validar"
SEED_SOURCE = "abecedario.xlsx"
ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "signs_seed.json"
DICT = ROOT / "data" / "kichwa_dictionary.json"

# Mano, verificada canal a canal en el brazo (2026-10-29). Cada dedo tiene base (B: posiciones
# 6-9 de la seña = meñique, anular, medio, índice; menor = hacia la palma) y punta (A: 1-4); el
# pulgar, punta (5) y base (10); el 11 no responde. Las puntas del medio y del índice tampoco
# responden por ahora: el actuador las ignora, pero la seña las describe igual.
# Niveles por dedo (orden meñique, anular, medio, índice); el actuador recorta a los límites.
FINGER_B = {                     # base
    "E": [135, 145, 120, 130],   # extendido
    "F": [50, 40, 47, 50],       # cerrado (mínimos del actuador; medio en calibración)
    "C": [90] * 4,               # curvo amplio (C)
    "O": [65] * 4,               # curvo cerrado (O, D, F)
    "D": [90] * 4,               # doblado hacia delante (K, P, G)
    "H": [100] * 4,              # base casi recta (gancho de la E)
}
FINGER_A = {                     # punta
    "E": [130, 144, 144, 142],   # recta
    "F": [65, 24, 24, 32],       # cerrada
    "C": [100] * 4,
    "O": [80] * 4,
    "D": [130, 144, 144, 142],   # recta: el dedo apunta hacia delante
    "H": [65, 30, 30, 40],       # doblada: gancho
}
THUMB = {                      # punta (5), base (10), 11 (no responde); menor = hacia la palma
    "lado": (120, 110, 50),    # pegado al costado del índice
    "fuera": (135, 125, 50),   # separado (A, L)
    "abierto": (135, 130, 50),   # muy separado (Y, 5)
    "curvo": (100, 105, 50),   # enfrentado a los dedos (C)
    "pinza": (80, 90, 50),     # hacia las yemas (O, D, F)
    "cruzado": (60, 70, 50),     # doblado sobre la palma (B, S)
}
# Brazo, probado servo a servo: 12 lateral (mayor = hacia el pulgar), 13 muñeca lateral
# (menor = hacia el meñique), 14 muñeca adelante/atrás (menor = hacia el dorso). No hay
# articulación que suba la mano ni que gire el antebrazo.
NEUTRAL = (90, 90, 100)        # 12, 13, 14
HORIZONTAL = (90, 64, 100)     # muñeca inclinada al lado del meñique
GIRO = (90, 112, 100)          # muñeca inclinada al lado del pulgar
MUNECA_ARRIBA = (90, 90, 75)   # muñeca hacia el dorso
ABAJO = (90, 90, 126)          # muñeca doblada hacia la palma: los dedos apuntan abajo
ELEVADO = (110, 90, 75)


def hand(fingers: str, thumb: str, arm: tuple[int, int, int] = NEUTRAL) -> list[int]:
    """fingers: nivel de meñique, anular, medio e índice (claves de FINGER_B)."""
    a = [FINGER_A[f][i] for i, f in enumerate(fingers)]
    b = [FINGER_B[f][i] for i, f in enumerate(fingers)]
    return a + [THUMB[thumb][0]] + b + list(THUMB[thumb][1:]) + list(arm) + [90]


# Referencia: Glosario Básico de Lengua de Señas Ecuatoriana (Vicepresidencia de la República
# del Ecuador y FENASEC, 2013), extraído del diccionario oficial de LSEC: alfabeto
# dactilográfico (p. 22), números naturales (p. 87) y fichas de palabras.
# https://www.vicepresidencia.gob.ec/wp-content/uploads/downloads/2013/04/Compilacion-Final-Interactivo.pdf
# (letra, brazo) = mano de esa letra calibrada con otra orientación. El brazo no separa ni
# cruza dedos (U/V/R/H solo difieren en orientación) y no hace trazos (J, Z, LL, Ñ, RR) ni
# toca el cuerpo: de cada seña queda la configuración de la mano y una posición del brazo.
CODO_CARA = NEUTRAL  # el brazo no sube la mano: HOLA se reconoce por abrir y cerrar
FROM_IMAGE: dict[str, tuple[list[int] | tuple[str, tuple[int, int, int]], str | None]] = {
    # alfabeto LSEC, todo con el modelo de mano (las letras del equipo usaban servos sin efecto)
    "A": (hand("FFFF", "fuera"), None),                # puño, pulgar separado al costado
    "B": (hand("EEEE", "cruzado"), None),              # cuatro dedos rectos, pulgar sobre la palma
    "C": (hand("CCCC", "curvo"), None),
    "D": (hand("OOOE", "pinza"), None),                # índice arriba, resto en círculo con el pulgar
    "E": (hand("HHHH", "cruzado"), None),              # dedos en gancho sobre el pulgar
    "F": (hand("EEEO", "pinza"), None),                # índice y pulgar en círculo, tres dedos rectos
    # índice y pulgar juntos apuntando al frente: el pulgar va hacia la palma para tocar el
    # índice (punta 100, base 85); con el pulgar "lado" quedaba separado (probado)
    "G": ([{4: 100, 8: 80, 9: 85}.get(i, a) for i, a in enumerate(hand("FFFD", "pinza"))], None),
    "H": (hand("FFEE", "cruzado", HORIZONTAL), None),  # índice y medio de lado
    "I": (hand("EFFF", "cruzado"), None),              # meñique arriba
    "J": (hand("EFFF", "cruzado", GIRO), None),        # I con el trazo (ver MOTION)
    "K": (hand("FFDE", "pinza"), None),                # índice arriba, medio doblado hacia delante
    "L": (hand("FFFE", "fuera"), None),                # índice arriba, pulgar separado
    # tres dedos caídos sobre el pulgar; base a 70 (con 90 se veían poco bajados, probado)
    "M": ([70 if i in (6, 7, 8) else a for i, a in enumerate(hand("FDDD", "cruzado", ABAJO))], None),
    "N": (hand("FFDD", "cruzado", ABAJO), None),       # dos dedos caídos sobre el pulgar
    "O": (hand("OOOO", "pinza"), None),
    # la K inclinada al máximo de la muñeca; sin giro de antebrazo el índice no queda horizontal.
    # El medio (servo B, posición 8) más doblado que en la K para que apunte hacia abajo
    "P": ([70 if i == 7 else a for i, a in enumerate(hand("FFDE", "pinza", (90, 64, 126)))], None),  # 55 se veía peor (probado)
    "Q": (hand("FFFD", "pinza", ABAJO), None),         # la G apuntando abajo
    "R": (hand("FFEE", "cruzado", MUNECA_ARRIBA), None),  # índice y medio (no se pueden cruzar)
    "S": (hand("FFFF", "cruzado"), None),              # puño, pulgar delante de los dedos
    "T": (hand("EEEO", "cruzado"), None),              # medio, anular y meñique arriba; índice doblado sobre el pulgar cruzado
    "U": (hand("FFEE", "cruzado"), None),              # índice y medio juntos arriba
    "V": (hand("FFEE", "cruzado", GIRO), None),        # (no se pueden separar los dedos)
    "W": (hand("FEEE", "cruzado"), None),              # índice, medio y anular
    "X": (hand("FFFH", "lado"), None),                 # índice en gancho
    "Y": (hand("EFFF", "abierto"), None),              # meñique y pulgar
    "Z": (hand("FFFE", "cruzado", (90, 64, 120)), None),  # índice que traza la Z
    # números naturales (variante de una mano)
    "DOS": (hand("FFEE", "cruzado", GIRO), None),      # como la V
    "TRES": (hand("FFEE", "fuera"), None),             # índice, medio y pulgar
    "CUATRO": (hand("EEEE", "cruzado"), None),
    "CINCO": (hand("EEEE", "abierto"), None),
    "DIEZ": (hand("FFFF", "fuera", GIRO), None),       # pulgar arriba que gira
    # palabras del glosario
    "HOLA": (hand("EEEE", "fuera", CODO_CARA), "assets/gestures/hola.jpeg"),       # dedos juntos junto a la cabeza
    "ADIOS": (hand("EEEE", "cruzado", (90, 90, 75)), "assets/gestures/adios.jpeg"),  # mano plana, muñeca atrás
    "GRACIAS": (hand("EEEE", "cruzado", (90, 90, 90)), "assets/gestures/gracias.jpeg"),  # mano plana que baja
    # sin ficha en el glosario: según las fotos del cliente
    "SI": (hand("EEEE", "fuera", MUNECA_ARRIBA), "assets/gestures/si.jpeg"),
    "AYUDA": (hand("FFEE", "cruzado"), "assets/gestures/ayuda.jpg"),
    "NO": (hand("FFFE", "cruzado"), "assets/gestures/no.jpeg"),
}
NO_LETTER_IMAGE: set[str] = set()  # el glosario LSEC trae imagen de todas las letras

# Movimiento de cada seña (glosario LSEC): poses que siguen a la principal, como cambios
# sobre ella {servo: ángulo}. 12 codo, 13 rotación, 14 muñeca; los dedos con hand().
CERRADA_CARA = {i + 1: a for i, a in enumerate(hand("CCCC", "pinza", CODO_CARA))}
ABIERTA_CARA = {i + 1: a for i, a in enumerate(FROM_IMAGE["HOLA"][0])}
MOTION: dict[str, list[dict[int, int]]] = {
    "J": [{14: 120}, {13: 64, 14: 120}],                          # baja y se curva
    "Z": [{13: 112}, {13: 64, 14: 120}, {13: 112, 14: 120}],      # trazo de la Z
    "HOLA": [CERRADA_CARA, ABIERTA_CARA, CERRADA_CARA, ABIERTA_CARA],  # se cierra y se abre
    "ADIOS": [{14: 115}],                                         # la mano va hacia delante
    "GRACIAS": [{14: 122}],                                       # la mano baja
    "DIEZ": [{13: 64}, {13: 112}],                                 # el pulgar gira
}


def word_beat(angles: list[int]) -> list[dict[int, int]]:
    """Palabras sin trazo conocido: el brazo avanza un poco y se asienta, para no quedar estático."""
    return [{14: min(angles[13] + 12, 126)}]  # la muñeca se inclina hacia delante y vuelve


def poses(angles: list[int], changes: list[dict[int, int]]) -> list[list[int]]:
    return [[c.get(i + 1, a) for i, a in enumerate(angles)] for c in changes]


def call(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"content-type": "application/json",
                                          **({"authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")


def is_empty(angles: list[int]) -> bool:
    return not any(angles[:14])


def resolve(spec: list[int] | tuple[str, tuple[int, int, int]], letters: dict[str, list[int]]) -> list[int] | None:
    if isinstance(spec, list):
        return spec
    base, arm = spec
    return letters[base][:11] + list(arm) + [letters[base][14]] if base in letters else None


def first_letter(gloss: str, letters: dict[str, list[int]]) -> str | None:
    for ch in unicodedata.normalize("NFD", gloss):
        if ch in letters:
            return ch
    return None


def generate(gloss: str, letters: dict[str, list[int]], arms: list[tuple[int, int, int]]) -> tuple[list[int], str] | None:
    letter = first_letter(gloss, letters)
    if letter is None or not arms:
        return None
    arm = arms[sum(map(ord, gloss)) % len(arms)]  # estable entre ejecuciones (hash() no lo es)
    return letters[letter][:11] + list(arm) + [letters[letter][14]], letter


def _selfcheck() -> None:
    h = hand("FFFE", "lado")
    assert h[:4] == [65, 24, 24, 142] and h[4] == 120 and h[5:9] == [50, 40, 47, 130] and h[9:] == [110, 50, 90, 90, 100, 90]
    assert hand("OOOO", "pinza")[5:9] == [65] * 4 and hand("OOOO", "pinza")[:4] == [80] * 4
    e = hand("HHHH", "cruzado")  # E: puntas en gancho (40 en el índice), base casi recta (100)
    assert compensate(e, set()) == e
    c = compensate(e, {3})       # punta del índice muerta: su base se dobla 0,6·(142-40) ≈ 61° más
    assert c[8] == max(FINGER_B["F"][3], 100 - 61) and c[:8] == e[:8] and c[9:] == e[9:]
    assert compensate(hand("EEEE", "cruzado"), {3}) == hand("EEEE", "cruzado")  # punta recta: nada
    assert resolve(("U", HORIZONTAL), {"U": list(range(1, 16))}) == list(range(1, 12)) + [90, 64, 100, 15]
    assert resolve(("K", ABAJO), {}) is None
    letters = {"C": list(range(1, 16)), "A": [7] * 15}
    arms = [(60, 90, 80), (120, 90, 100)]
    a, l = generate("CASA", letters, arms)
    assert l == "C" and a[:11] == list(range(1, 12)) and tuple(a[11:14]) in arms and len(a) == 15
    assert generate("CASA", letters, arms)[0] == a and generate("PADRE", letters, arms)[1] == "A"
    assert generate("123", letters, arms) is None and is_empty([0] * 14 + [90])
    p = poses(list(range(1, 16)), [{13: 99}])
    assert p == [list(range(1, 13)) + [99, 14, 15]] and poses([1] * 15, []) == []
    assert word_beat([0] * 11 + [120, 90, 120, 90]) == [{14: 126}]  # dentro de los límites
    assert all(len(pose) == 15 for g in MOTION for pose in poses([90] * 15, MOTION[g]))


def credentials() -> tuple[str, str]:
    """RSP_USER / RSP_PASS, o el usuario con rol operator de AUTH_USERS en el .env de la plataforma."""
    if os.getenv("RSP_PASS"):
        return os.getenv("RSP_USER", "operator"), os.environ["RSP_PASS"]
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("AUTH_USERS="):
            for entry in line.split("=", 1)[1].split(","):
                name, pwd, roles = entry.strip().split(":", 2)
                if "operator" in roles.split("|"):
                    return name, pwd
    raise SystemExit("define RSP_USER y RSP_PASS (usuario con rol operator)")


# Dedos (0 = meñique … 3 = índice) cuya punta no responde en el brazo; lo rellena main() con
# los canales inactivos del actuador, así la compensación desaparece sola al repararlos.
DEAD_TIPS: set[int] = set()
TIP_TO_BASE = 0.6  # cuánto del doblez pedido a la punta pasa a la base


def compensate(pose: list[int], dead: set[int]) -> list[int]:
    """Si la punta de un dedo no responde, la seña dobla más su base: la punta baja lo que
    puede y la forma se acerca a la referencia (E, C, G…). Sin puntas muertas, no cambia nada."""
    out = pose[:]
    for f in dead:
        wanted = FINGER_A["E"][f] - pose[f]          # cuánto debía doblarse la punta
        if wanted > 0:
            out[5 + f] = max(FINGER_B["F"][f], pose[5 + f] - round(TIP_TO_BASE * wanted))
    return out


def put(g: str, sign: dict, token: str, apply: bool) -> None:
    if DEAD_TIPS:
        sign = {**sign, "angles": compensate(sign["angles"], DEAD_TIPS),
                "motion": [compensate(p, DEAD_TIPS) for p in sign.get("motion", [])]}
    if apply:
        call("PUT", f"/v1/signs/{urllib.request.quote(g)}", sign, token)


def main() -> None:
    _selfcheck()
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--remove", action="store_true", help="deshace los cambios (antes de evaluar)")
    args = ap.parse_args()

    user, password = credentials()
    token = call("POST", "/auth/token", {"username": user, "password": password})["access_token"]
    signs = {s["gloss"]: s for s in call("GET", "/v1/signs?limit=1000", token=token)["items"]}
    seed = {s["gloss"]: s for s in json.loads(SEED.read_text(encoding="utf-8"))}
    ours = (SOURCE, LETTER_SOURCE)
    if args.remove:
        # lo que estaba en la semilla vuelve a ella (borrar una letra rompería el deletreo); lo nuevo se borra
        restored = deleted = 0
        for g, s in signs.items():
            if s.get("source") not in ours:
                continue
            if g in seed:
                call("PUT", f"/v1/signs/{urllib.request.quote(g)}", seed[g], token); restored += 1
            else:
                call("DELETE", f"/v1/signs/{urllib.request.quote(g)}", token=token); deleted += 1
        print(f"{restored} señas restauradas a la semilla, {deleted} borradas")
        return

    hw = call("GET", "/v1/actuator/servos", token=token)
    channel = {int(k): v for k, v in hw.get("channels", {}).items()}
    DEAD_TIPS.update(f for f in range(4) if channel.get(f + 1, f + 1) in set(hw.get("inactive", [])))
    if DEAD_TIPS:
        names = ["meñique", "anular", "medio", "índice"]
        print("puntas sin respuesta, compensadas con la base:", ", ".join(names[f] for f in sorted(DEAD_TIPS)))
    editable = lambda g: g not in signs or signs[g].get("source") in (*ours, SEED_SOURCE)  # noqa: E731
    letters = {g: s["angles"] for g, s in signs.items() if len(g) == 1 and any(s["angles"][:10])}

    # 1. señas con imagen de referencia (letras primero: las palabras se derivan de ellas)
    for g, (spec, image) in sorted(FROM_IMAGE.items(), key=lambda kv: len(kv[0])):
        angles = resolve(spec, letters)
        if angles is None or not editable(g):
            print(f"  {g}: se deja ({'base sin calibrar' if angles is None else 'editada por el equipo'})")
            continue
        base = signs.get(g, {})
        print(f"  {g:8} {angles}  (imagen)")
        # sin imagen propia: la de la semilla si la hay; nunca la de letra que puso el paso 2 en otra ejecución
        keep = base.get("image_path") if base.get("source") == SEED_SOURCE else None
        put(g, {"gloss": g, "angles": angles, "image_path": image or keep, "motion": poses(angles, MOTION.get(g, [])),
                "hold_ms": base.get("hold_ms", 600), "source": LETTER_SOURCE, "kichwa": base.get("kichwa")}, token, args.apply)
        signs[g] = {**base, "angles": angles, "source": LETTER_SOURCE}
        if len(g) == 1:
            letters[g] = angles

    # 2. el resto del vocabulario: glosas que la NLP produce para el español del diccionario kichwa
    arms = sorted({tuple(s["angles"][11:14]) for g, s in seed.items()
                   if len(g) > 1 and not is_empty(s["angles"])} - {NEUTRAL})
    raw = json.loads(DICT.read_text(encoding="utf-8"))
    spanish = sorted({v for e in raw.values() if isinstance(e, dict) for v in e.values() if isinstance(v, str)})
    wanted = {g for w in spanish for g in call("POST", "/v1/nlp/gloss", {"text": w, "language": "es"}, token)["glosses"]}
    todo = sorted(g for g in wanted | set(signs) if len(g) > 1 and g not in FROM_IMAGE and editable(g)
                  and (g not in signs or is_empty(signs[g]["angles"]) or signs[g].get("source") == SOURCE))
    print(f"{len(letters)} letras, {len(arms)} posiciones de brazo; {len(todo)} palabras con la mano de su inicial")
    for g in todo:
        got = generate(g, letters, arms)
        if got is None:
            print(f"  {g}: sin letra inicial conocida, se deja")
            continue
        angles, letter = got
        base = signs.get(g, {})
        image = None if letter in NO_LETTER_IMAGE else f"assets/images/{letter.lower()}.jpeg"
        put(g, {"gloss": g, "angles": angles, "image_path": image, "motion": poses(angles, word_beat(angles)),
                "hold_ms": base.get("hold_ms", 600),
                "source": SOURCE, "kichwa": base.get("kichwa")}, token, args.apply)
    if not args.apply:
        print("(simulación: añade --apply para escribirlas)")


if __name__ == "__main__":
    main()
