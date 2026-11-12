"""Normalización y glosado: de texto libre a secuencia de glosas.

Una *glosa* es la etiqueta en mayúsculas con la que se nombra una seña
(convención habitual en lingüística de lenguas de señas). El servicio de
traducción a señas busca cada glosa en su diccionario; lo que no exista se
deletrea con el alfabeto dactilológico.

Reglas del prototipo (deliberadamente simples y trazables; el modelo
lingüístico completo de la LSEC es trabajo futuro y NO parte de la tesis):
  1. minúsculas + quitar tildes (el diccionario heredado las mezcla)
  2. números → palabras (5 → CINCO) cuando existen en el diccionario
  3. eliminar artículos, preposiciones y muletillas (las lenguas de señas
     tienden a omitirlos)
  4. unir expresiones multi-palabra conocidas ("mas o menos", "un ratito")
  5. mayúsculas
"""
from __future__ import annotations

import re
import unicodedata

STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "a",
    "en", "por", "para", "con", "y", "e", "o", "u", "que", "se", "lo", "es", "esta", "este",
    "eh", "em", "este", "pues",
}

NUMBER_WORDS = {
    "0": "cero", "1": "uno", "2": "dos", "3": "tres", "4": "cuatro", "5": "cinco",
    "6": "seis", "7": "siete", "8": "ocho", "9": "nueve", "10": "diez",
}

# expresiones compuestas que existen como una sola seña en el diccionario heredado
PHRASES = [
    "muy rapido", "mas o menos", "un ratito", "manos libres", "mala suerte",
    "mal pensado", "maquina de afeitar", "microfono de solapa",
]


def strip_accents(text: str) -> str:
    """Quita tildes y diéresis (á→a, ü→u) pero conserva la ñ: en español es una
    letra distinta con su propia seña dactilológica, no una n acentuada
    ("año" ≠ "ano")."""
    out = []
    for c in unicodedata.normalize("NFD", text):
        if unicodedata.category(c) != "Mn":
            out.append(c)
        elif c == "\u0303" and out and out[-1] in "nN":  # tilde de la ñ
            out[-1] = "ñ" if out[-1] == "n" else "Ñ"
    return "".join(out)


def normalize(text: str) -> str:
    text = strip_accents(text.lower())
    text = re.sub(r"[^a-z0-9ñ\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def to_glosses(text: str, keep_stopwords: bool = False) -> list[str]:
    norm = normalize(text)
    if not norm:
        return []
    if len(norm) == 1 and norm.isalpha():
        return [norm.upper()]  # una letra sola es la letra del alfabeto, no la palabra vacía «a»/«e»/«o»/«y»
    for phrase in PHRASES:
        if phrase in norm:
            norm = norm.replace(phrase, phrase.replace(" ", "_"))
    glosses: list[str] = []
    for tok in norm.split():
        tok = NUMBER_WORDS.get(tok, tok)
        if not keep_stopwords and tok in STOPWORDS:
            continue
        glosses.append(tok.replace("_", " ").upper())
    return glosses
