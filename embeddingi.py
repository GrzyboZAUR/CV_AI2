"""Podobieństwo semantyczne (po znaczeniu) wymagań z oferty do linii bazy, przez embeddingi w Ollamie.

Wymaga modelu embeddingów na serwerze Ollamy:   ollama pull bge-m3
Domyślnie używa tego samego adresu co generator (OLLAMA_URL) i modelu z EMB_MODEL (domyślnie bge-m3).

Embeddingi linii bazy są zapamiętywane w folderze cache_embeddingow/ (klucz: model + treść linii),
więc przy kolejnych ofertach liczą się tylko wymagania z oferty.
"""
import hashlib
import json
import math
import os
from pathlib import Path

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL = os.getenv("EMB_MODEL", "bge-m3")
KATALOG_CACHE = Path(__file__).resolve().parent / "cache_embeddingow"
TIMEOUT_S = 180
PACZKA = 32


def _adres():
    return OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/embed"


def _normalizuj(v):
    dl = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / dl for x in v]


def podobienstwo(a, b):
    """Cosinus wektorów (po normalizacji to iloczyn skalarny)."""
    return sum(x * y for x, y in zip(a, b))


def embed(teksty, model=None):
    """Lista wektorów (znormalizowanych) dla listy tekstów. Błąd sieci lub brak modelu: wyjątek."""
    model = model or MODEL
    wynik = []
    for i in range(0, len(teksty), PACZKA):
        r = requests.post(_adres(), json={"model": model, "input": teksty[i:i + PACZKA]}, timeout=TIMEOUT_S)
        r.raise_for_status()
        wynik += r.json()["embeddings"]
    return [_normalizuj(v) for v in wynik]


def wektory_linii(teksty_linii, model=None):
    """Wektory linii bazy z cache na dysku."""
    model = model or MODEL
    klucz = hashlib.sha1((model + "\n" + "\n".join(teksty_linii)).encode("utf-8")).hexdigest()[:20]
    plik = KATALOG_CACHE / f"{model.replace('/', '_').replace(':', '_')}_{klucz}.json"
    if plik.exists():
        try:
            return json.loads(plik.read_text(encoding="utf-8"))["wektory"]
        except (ValueError, KeyError):
            pass                                   # uszkodzony cache: liczymy od nowa
    wektory = embed(teksty_linii, model)
    try:
        KATALOG_CACHE.mkdir(exist_ok=True)
        plik.write_text(json.dumps({"wektory": [[round(x, 5) for x in v] for v in wektory]}), encoding="utf-8")
    except OSError:
        pass                                       # cache jest tylko przyspieszeniem
    return wektory


def najblizsze(teksty, teksty_linii, wektory_bazy, model=None):
    """Dla każdego tekstu: (podobieństwo, najbliższa linia bazy)."""
    wynik = []
    for w in embed(teksty, model):
        podobienstwa = [podobienstwo(w, v) for v in wektory_bazy]
        j = max(range(len(podobienstwa)), key=podobienstwa.__getitem__)
        wynik.append((podobienstwa[j], teksty_linii[j]))
    return wynik
