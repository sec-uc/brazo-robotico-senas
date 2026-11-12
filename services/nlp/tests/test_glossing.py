import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from app.glossing import to_glosses, normalize


def test_normalize_removes_accents_and_punctuation():
    assert normalize("¡Hola, Mamá!") == "hola mama"


def test_stopwords_removed():
    assert to_glosses("la mamá de la casa") == ["MAMA", "CASA"]


def test_numbers_to_words():
    assert to_glosses("tengo 4 manzanas") == ["TENGO", "CUATRO", "MANZANAS"]


def test_known_phrase_kept_as_single_gloss():
    assert to_glosses("estoy más o menos bien") == ["ESTOY", "MAS O MENOS", "BIEN"]


def test_empty():
    assert to_glosses("   ") == []


def test_enie_is_a_letter_not_an_accent():
    """QA-12: "año" no puede glosarse como "ano"; las tildes sí se quitan."""
    from app.glossing import strip_accents, to_glosses
    assert strip_accents("año pingüino café ÑANDÚ") == "año pinguino cafe ÑANDU"
    assert to_glosses("año") == ["AÑO"]


def test_single_letter_is_the_alphabet_letter_not_a_stopword():
    from app.glossing import to_glosses
    assert [to_glosses(l) for l in ("a", "E", "o", "y", "ñ")] == [["A"], ["E"], ["O"], ["Y"], ["Ñ"]]
    assert to_glosses("a casa") == ["CASA"]  # dentro de una frase sigue siendo palabra vacía
