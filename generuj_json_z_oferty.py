"""
Generator danych do CV (JSON) na podstawie bazy doświadczenia (.md) i oferty pracy.
Model (Bielik) działa w Ollamie na Twoim serwerze.

Jak to działa (2 zapytania do modelu):
  1. ANALIZA OFERTY  - model wypisuje wymagania, kod porównuje je z bazą, model pisze CV.
                        Wynik: lista wymagań, których NIE MASZ.
  2. GENERACJA CV    - model pisze profil, wybiera punkty, umiejętności, projekty
                       i certyfikaty pod tę ofertę.

Każde uruchomienie zapisuje komplet plików w folderze logi/<data>_<firma>_<stanowisko>/
Gotowe PDF-y trafiają do folderu pdf/ (kopia także do folderu logu).

Przykłady:
    python generuj_json_z_oferty.py --firma "Media Expert" --stanowisko "Specjalista ds. administracji danych"
    python generuj_json_z_oferty.py --firma Sollers --stanowisko "Business Analyst" --jezyk en --oferta sollers.txt --pdf
"""
import argparse
import json
import math
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
import os
from dotenv import load_dotenv
import requests

# Wymuś UTF-8 na konsoli Windows, żeby emoji w logach nie wywalały UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

load_dotenv()
# ============================================================
# KONFIGURACJA – dopasuj do siebie
# ============================================================
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL      = os.getenv("MODEL", "bielik-11b")

NUM_CTX = 8192           # okno kontekstu; baza + oferta + prompt muszą się zmieścić
# Dopasowanie po znaczeniu (embeddingi w Ollamie, np. "ollama pull bge-m3"). Domyślnie WYŁĄCZONE do czasu dobrania
# progu na własnych danych (python kalibruj_embeddingi.py). Włączasz: EMB_WLACZONE=1 w .env albo --embeddingi.
EMBEDDINGI = os.getenv("EMB_WLACZONE", "0").strip().lower() in ("1", "tak", "true", "yes")
EMB_PROG = float(os.getenv("EMB_PROG", "0.65") or 0.65)   # minimalne podobieństwo, żeby uznać wymaganie za "prawdopodobne"
TEMP_ANALIZA = 0.0        # analiza oferty: zero kreatywności
TEMP_CV = 0.3             # pisanie CV: odrobina swobody, żeby profil nie był kopią bazy
TIMEOUT_S = 1800          # większy model (11B) częściowo na procesorze potrafi liczyć kilkanaście minut

MAX_PROJEKTY = 3          # ile projektów maksymalnie trafia do CV
MAX_CERTYFIKATY = 4       # ile szkoleń z certyfikatem maksymalnie trafia do CV
MAX_KURSY = 3             # ile kursów (bez certyfikatu) maksymalnie trafia do CV
MAX_PUNKTOW = 6           # ile punktów na stanowisko
MAX_PUNKTY_STUDIA = 4     # ile punktów pod każdą uczelnią (wybieranych według oferty)
PLEC_KANDYDATA = "m"      # "m" = formy męskie, "k" = żeńskie (dotyczy opisu w CV po polsku)
POMIJAJ_STANOWISKA = []   # numery stanowisk z bazy, których NIE pokazywać w CV (np. [5]); domyślnie wszystkie

KATALOG = Path(__file__).resolve().parent
BAZA_MD = KATALOG / "baza-doswiadczenia-v3.md"     # stary tryb jednoosobowy (gdy nie ma baz osób)
BAZA_JEZYK = "pl"         # w jakim języku jest napisana baza
FOLDER_BAZ = KATALOG / "bazy"                      # tu leżą pliki "<Imię> - baza zatrudnienia.md"
KONCOWKA_BAZY = " - baza zatrudnienia.md"
FOLDER_WYNIKOW = KATALOG / "wyniki"                # wyniki/<Imię>/cv_pl.json
FOLDER_LOGOW = KATALOG / "logi"
FOLDER_PDF = KATALOG / "pdf"                       # pdf/<Imię>/...
PLIK_TLUMACZEN = KATALOG / "tlumaczenia_en.json"   # tłumaczenia stałych danych na angielski

ZAINTERESOWANIA = False   # czy pokazywać kategorię zainteresowań (włącza opcja --zainteresowania)
USTAWIENIA = {}           # sekcja "# Ustawienia CV" bazy wybranej osoby
OSOBA = None              # wybrana osoba (ustawiane w main); None = stary tryb jednoosobowy
DANE_OSOBOWE = {}         # dane kontaktowe z sekcji "# Dane osobowe" bazy wybranej osoby

JEZYKI = {
    "pl": {
        "nazwa": "polski",
        "json": "cv_danePL.json",
        "kategorie": "Umiejętności twarde, Narzędzia, Umiejętności miękkie, Języki, Uprawnienia, Zainteresowania",
    },
    "en": {
        "nazwa": "angielski",
        "json": "cv_dane.json",
        "kategorie": "Hard skills, Tools, Soft skills, Languages, Licenses, Interests",
    },
}
# ============================================================

SLOWA_POMIJANE = {"i", "oraz", "w", "z", "na", "do", "a", "lub", "of", "and", "the"}
# Cechy osobiste z ofert: nie da się ich "udowodnić" cytatem z bazy, więc nie są traktowane jak braki.
# Dopisz własne frazy, jeśli jakaś cecha trafia do czerwonej listy.
CECHY_MIEKKIE = [
    "odporność na stres", "nastawienie na współpracę", "współpraca", "samodzielność", "komunikatywność",
    "zdolności komunikacyjne", "otwartość", "rzetelność", "dbałość o szczegóły", "zdolności analityczne",
    "organizacja czasu", "organizacja pracy", "zaangażowanie", "motywacja", "chęć rozwoju", "proaktywność",
    "kreatywność", "logicznego myślenia", "elastyczność", "sumienność", "praca w zespole", "pracy w zespole",
    "dyskrecja", "poufność", "lojalność", "uczciwość", "asertywność", "pozytywne nastawienie",
    "cierpliwość", "empatia", "odpowiedzialność", "opiekuńczość", "życzliwość", "determinacja",
    "przedsiębiorczość", "nastawienie na wynik", "entuzjazm",
]
# Słowa zbyt ogólne, żeby świadczyły o dopasowaniu wymagania do bazy
SLOWA_OGOLNE = {"znajomość", "doświadczenie", "umiejętność", "umiejętności", "biegła", "bardzo",
                "dobra", "dobry", "dobre", "praktyczna", "praktyczne", "wiedza", "obsługa", "mile", "widziane",
                "najlepiej", "minimum", "min", "lata", "lat", "rok", "roku", "przynajmniej", "jednego", "jednej",
                "zainteresowanie", "podstaw", "podstawy", "podstawowa", "podstawowe", "solidne", "własne",
                "pokrewnym", "pokrewne", "obszarze", "obszarach", "obszar", "codziennej", "swobodne",
                "swobodnego", "korzystanie", "umożliwiający", "umożliwiającego", "faktycznie", "które",
                "zakresu", "zakres", "dziedzinie", "dziedziny", "obszaru"}
# Kolejne słowa bez znaczenia dla dopasowania (dopiski "mile widziane", spójniki, ogólniki). Dopisuj własne.
SLOWA_OGOLNE |= {
    "widziana", "widziany", "widziane", "atut", "atutem", "niekonieczne", "niekonieczny", "niekonieczna",
    "preferowane", "preferowana", "preferowany", "wymagane", "wymagana", "wymagany", "pożądane", "pożądana",
    "praca", "pracy", "pracę", "pracą", "pracach", "nowych", "nowe", "nowy", "nowa", "nowoczesnych", "nowoczesne",
    "poziomie", "poziom", "poziomu", "wysoki", "wysoka", "wysokie", "wysokim", "język", "języka", "języku",
    "języki", "języków", "pakiet", "pakietu", "pakietem", "pakiecie", "innych", "inne", "inny", "klasy", "klasa",
    "zawodowe", "zawodowego", "zawodowym", "zawodowa", "podobnym", "podobne", "podobnego", "podobny",
    "stanowisku", "stanowisko", "stanowiska", "orientacja", "orientacji", "orientację", "metody", "metoda",
    "metodę", "metodami", "metod", "ukończony", "ukończone", "ukończona", "ukończenie", "posiadanie",
    "jako", "dla", "przy", "ich", "jak", "nad", "pod", "najmniej", "własnej", "własnym", "firmowej", "firmowe",
    "umiejętnością", "znajomością", "znajomości", "doświadczenia", "doświadczeniem", "doświadczeniu",
    "półroczne", "półroczny", "półroczna", "miesięcy", "miesiące", "wykształcenie", "wykształcenia",
    "wykształceniem", "wyższe", "wyższym", "wyższego", "średnie", "średnim", "kierunkowe", "kierunkowy",
    "kierunki", "kierunek", "kierunku", "kierunków",
}
# Wymogi formalne i osobiste z ofert (fakty o Tobie, nie umiejętności): nie dopasowuję ich słowami,
# tylko pokazuję osobno. Dopisz własne frazy, jeśli jakiś wymóg trafia do zwykłych wymagań.
FORMALNE_FRAZY = [
    "prawo jazdy", "prawa jazdy", "status studenta", "studenta", "niepełnosprawn", "inwalid", "orzeczeni",
    "książeczk", "sanepid", "niekarał", "karalno", "zezwolenie na pracę", "obywatelstw", "własny samochód",
    "samochód", "relokac", "podróże służbowe", "podróży służbowych", "dyspozycyjn", "zmianow", "weekend",
    "badania lekarskie", "badań lekarskich", "uprawnienia",
]
# Grupy bliskoznacznych słów (rdzenie 4-literowe); dzięki nim "sporządzanie" pasuje do "tworzenia".
# Dopisuj własne grupy, jeśli widzisz fałszywe braki.
SYNONIMY = [
    {"twor", "spor", "przy", "gene", "budo", "opra"},   # tworzenie, sporządzanie, przygotowywanie, generowanie
    {"weryf", "kont", "walid"},                         # weryfikacja, kontrola
    {"grom", "zbie", "pozy"},                           # gromadzenie, zbieranie, pozyskiwanie
    {"zarz", "kier", "nadz"},                           # zarządzanie, kierowanie, nadzór
    {"anal", "bada"},                                   # analiza, badanie
    {"baz", "baza", "bazy", "bazi", "bazą", "bazę"},   # baza, bazy, bazie (krótkie słowa z końcówkami)
    {"sieć", "siec"},
    {"ml", "mach", "lear"},                             # ML, machine learning
    {"ekon", "mikr", "makr"},                           # ekonomia, mikroekonomia, makroekonomia                                   # sieć, sieci
    {"prog", "pyth", "flas"},                           # programowanie, Python, Flask (przykład, rozbuduj)
    {"ms", "micr"},                                     # MS Office = Microsoft Office
    {"sale", "sprzd"},                                  # Sales = sprzedaż
]
SYNONIMY_OSOBY = []       # własne synonimy z sekcji "# Słowa kluczowe" bazy (ustawiane przy porównaniu)

# Rdzeń słowa to jego 4 pierwsze litery. Poniższe przedrostki mają własny rdzeń, bo po skróceniu
# do 4 liter wyglądałyby tak samo jak inne słowo (sprzedaż / sprzęt, program / prognoza, system / systematyczność).
# Dłuższe przedrostki wygrywają z krótszymi. Dopisuj własne, jeśli widzisz fałszywe dopasowania.
WYJATKI_RDZENI = sorted([
    ("sprzed", "sprzd"), ("sprzęt", "sprzt"), ("sprzet", "sprzt"), ("sprzęc", "sprzt"),
    ("prognoz", "prgn"), ("programist", "prgs"), ("systemat", "sstm"), ("przyj", "przj"),
    ("kontakt", "kntk"), ("kontrakt", "kntr"), ("technik", "tchk"), ("salesforce", "slsf"),
    ("dane", "dane"), ("danyc", "dane"), ("danym", "dane"), ("danej", "dane"),
], key=lambda x: -len(x[0]))


def rdzen_slowa(slowo):
    s = slowo.lower()
    for przedrostek, rdzen in WYJATKI_RDZENI:
        if s.startswith(przedrostek):
            return rdzen
    return s[:4]
KATALOG_LOGU = None       # ustawiany w main()


# ------------------------------------------------------------
# Log: wszystko, co idzie na ekran, trafia też do pliku
# ------------------------------------------------------------
class Tee:
    def __init__(self, *strumienie):
        self.strumienie = strumienie

    def write(self, tekst):
        for s in self.strumienie:
            try:
                s.write(tekst)
                s.flush()
            except UnicodeEncodeError:
                s.write(tekst.encode("ascii", "replace").decode("ascii"))
                s.flush()

    def flush(self):
        for s in self.strumienie:
            s.flush()


def zapisz_w_logu(nazwa, tekst):
    if KATALOG_LOGU is not None:
        (KATALOG_LOGU / nazwa).write_text(tekst, encoding="utf-8")


def slug(tekst):
    return re.sub(r"[^\w-]+", "-", tekst.lower()).strip("-")[:30] or "x"

ROZSZERZENIA_ZDJEC = (".jpg", ".jpeg", ".png")

def znajdz_zdjecie(folder, imie):
    """Szuka zdjęcia obok bazy: '<Imię> - zdjęcie.jpg' albo '<Imię>.jpg'."""
    if not imie or not folder.exists():
        return None
    wzorce = [
        f"{imie} - zdjęcie", f"{imie} - zdjecie",
        f"{imie.lower()} - zdjęcie", f"{imie.lower()} - zdjecie",
        imie, imie.lower(),
    ]
    for bazowy in wzorce:
        for r in ROZSZERZENIA_ZDJEC:
            for wariant in (r, r.upper()):
                p = folder / (bazowy + wariant)
                if p.exists():
                    return p
    return None
# ------------------------------------------------------------
# Czyszczenie i parsowanie bazy doświadczenia (.md)
# ------------------------------------------------------------
def czysc_md(t):
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    return t.replace("**", "").replace("`", "").strip()


def norm(t):
    """Małe litery, bez znaków specjalnych i markdownu - do porównań tekstu."""
    t = czysc_md(t).lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def znajdz_osoby():
    """Szuka plików '<Imię> - baza zatrudnienia.md' w folderze bazy/ i obok skryptu."""
    osoby = {}
    for folder in (FOLDER_BAZ, KATALOG):
        if folder.exists():
            for plik in sorted(folder.glob(f"*{KONCOWKA_BAZY}")):
                osoby.setdefault(plik.name[: -len(KONCOWKA_BAZY)].strip(), plik)
    return osoby


def wybierz_osobe(podana):
    """Zwraca (imię, ścieżka bazy). Bez --osoba pokazuje menu."""
    osoby = znajdz_osoby()
    if not osoby:
        if BAZA_MD.exists():
            return None, BAZA_MD                      # stary tryb jednoosobowy
        sys.exit(f"❌ Nie znaleziono baz. Utwórz plik '<Imię>{KONCOWKA_BAZY}' w folderze {FOLDER_BAZ}")
    if podana:
        for imie, sciezka in osoby.items():
            if imie.lower() == podana.lower():
                return imie, sciezka
        sys.exit(f"❌ Nie ma bazy dla '{podana}'. Dostępne osoby: {', '.join(osoby)}")
    if len(osoby) == 1:
        return next(iter(osoby.items()))
    if not sys.stdin.isatty():
        sys.exit(f"❌ Podaj --osoba. Dostępne: {', '.join(osoby)}")
    imiona = list(osoby)
    print("Dla kogo analizujemy ofertę?")
    for i, imie in enumerate(imiona, 1):
        print(f"  [{i}] {imie}")
    while True:
        wybor = input("Wybór: ").strip()
        if wybor.isdigit() and 1 <= int(wybor) <= len(imiona):
            return imiona[int(wybor) - 1], osoby[imiona[int(wybor) - 1]]
        for imie in imiona:
            if wybor and imie.lower() == wybor.lower():
                return imie, osoby[imie]
        print("   Wpisz numer z listy albo imię.")


ETYKIETY_OSOBY = {
    "imię i nazwisko": "name", "e-mail": "email", "email": "email", "telefon": "phone",
    "linkedin": "linkedin", "lokalizacja": "location_pl", "lokalizacja (en)": "location_en",
    "płeć": "gender",
}


def parse_pola_sekcji(baza, naglowek):
    """Pola '- **Etykieta:** wartość' z sekcji '# <naglowek>'. Zwraca {etykieta małymi literami: wartość}."""
    wynik, w_sekcji = {}, False
    for l in baza.splitlines():
        if l.startswith("# "):
            w_sekcji = (l[2:].strip() == naglowek)
        elif l.strip() == "---":
            w_sekcji = False
        elif w_sekcji:
            m = re.match(r"^\s*-\s*\*\*(.+?):\*\*\s*(.*)$", l)
            if m:
                wynik[m.group(1).strip().lower()] = re.sub(
                    r"\[([^\]]*)\]\(([^)]*)\)", r"\2", m.group(2)).strip()   # link -> sam adres
    return wynik


def parse_dane_osobowe(baza):
    pola = parse_pola_sekcji(baza, "Dane osobowe")
    return {ETYKIETY_OSOBY[k]: v for k, v in pola.items() if k in ETYKIETY_OSOBY}


ETYKIETY_USTAWIEN = {
    "kategorie umiejętności": "kategorie_pl", "kategorie umiejętności (en)": "kategorie_en",
    "nagłówek umiejętności": "h_skills_pl", "nagłówek umiejętności (en)": "h_skills_en",
    "nagłówek projektów": "h_projects_pl", "nagłówek projektów (en)": "h_projects_en",
    "nagłówek certyfikatów": "h_certs_pl", "nagłówek certyfikatów (en)": "h_certs_en",
    "nagłówek kursów": "h_courses_pl", "nagłówek kursów (en)": "h_courses_en",
    "maks. projektów": "max_projekty", "maks. certyfikatów": "max_certyfikaty",
    "maks. kursów": "max_kursy", "maks. punktów": "max_punktow", "maks. punktów studiów": "max_studia",
}


def parse_ustawienia(baza):
    """Opcjonalna sekcja '# Ustawienia CV': kategorie umiejętności, nagłówki sekcji, limity."""
    pola = parse_pola_sekcji(baza, "Ustawienia CV")
    return {ETYKIETY_USTAWIEN[k]: v for k, v in pola.items() if k in ETYKIETY_USTAWIEN and v}


def zastosuj_ustawienia(ust):
    """Liczbowe ustawienia nadpisują domyślne limity z konfiguracji."""
    global MAX_PROJEKTY, MAX_CERTYFIKATY, MAX_KURSY, MAX_PUNKTOW, MAX_PUNKTY_STUDIA
    if ust.get("max_studia", "").isdigit():
        MAX_PUNKTY_STUDIA = int(ust["max_studia"])
    if ust.get("max_projekty", "").isdigit():
        MAX_PROJEKTY = int(ust["max_projekty"])
    if ust.get("max_certyfikaty", "").isdigit():
        MAX_CERTYFIKATY = int(ust["max_certyfikaty"])
    if ust.get("max_kursy", "").isdigit():
        MAX_KURSY = int(ust["max_kursy"])
    if ust.get("max_punktow", "").isdigit():
        MAX_PUNKTOW = int(ust["max_punktow"])


def kategorie_umiejetnosci(jezyk):
    kat = USTAWIENIA.get(f"kategorie_{jezyk}") or JEZYKI[jezyk]["kategorie"]
    if not ZAINTERESOWANIA:
        kat = ", ".join(k.strip() for k in kat.split(",")
                        if k.strip() and k.strip().lower() not in ("zainteresowania", "interests"))
    return kat


def naglowki_pdf(jezyk):
    """Własne nagłówki sekcji CV (np. 'Osiągnięcia' zamiast 'Projekty')."""
    mapa = {"skills": "h_skills", "projects": "h_projects", "certs": "h_certs", "courses": "h_courses"}
    return {k: USTAWIENIA[f"{v}_{jezyk}"] for k, v in mapa.items() if f"{v}_{jezyk}" in USTAWIENIA}


SEKCJE_POMOCNICZE = ("Dane osobowe", "Ustawienia CV", "Słowa kluczowe")   # nie są dowodem umiejętności i nie idą do modelu


def usun_sekcje(baza, nazwy):
    wynik, pomijaj = [], False
    for l in baza.splitlines():
        if l.startswith("# "):
            pomijaj = (l[2:].strip() in nazwy)
        if not pomijaj:
            wynik.append(l)
    return "\n".join(wynik)


def sprawdz_baze(baza, dane, stanowiska):
    """Wypisuje ostrzeżenia o brakach w bazie. Brak stanowisk przerywa pracę."""
    uwagi = []
    for klucz, nazwa in (("name", "Imię i nazwisko"), ("email", "E-mail"), ("phone", "Telefon")):
        if not dane.get(klucz):
            uwagi.append(f'brak w sekcji "# Dane osobowe": {nazwa}')
    if not dane.get("gender"):
        uwagi.append('brak pola "Płeć" w sekcji "# Dane osobowe" (przyjmuję ustawienie domyślne)')
    if not stanowiska:
        sys.exit("❌ W bazie nie znaleziono żadnego stanowiska. Sprawdź nagłówki: ## Stanowisko 1: Nazwa")
    zostaly = [l.strip() for l in baza.splitlines() if "UZUPEŁNIJ" in l]
    if zostaly:
        print(f"❌ W bazie zostało {len(zostaly)} niewypełnionych pól (słowo UZUPEŁNIJ), np.:")
        for l in zostaly[:5]:
            print(f"   {l[:90]}")
        sys.exit("   Uzupełnij je albo usuń puste wpisy, żeby nie trafiły do CV.")
    for u in uwagi:
        print(f"⚠️  Baza: {u}")


def sekcje(baza, prefix):
    """Zwraca listę (nagłówek, linie) dla sekcji '## <prefix>...'."""
    wynik, aktualna = [], None
    for linia in baza.splitlines():
        if linia.startswith("## "):
            aktualna = None
            if linia[3:].startswith(prefix):
                aktualna = (linia[3:].strip(), [])
                wynik.append(aktualna)
        elif linia.startswith("# ") or linia.strip() == "---":
            aktualna = None
        elif aktualna is not None:
            aktualna[1].append(linia)
    return wynik


def pole(linie, nazwa):
    for l in linie:
        m = re.match(rf"\s*-\s*\*\*{re.escape(nazwa)}:\*\*\s*(.*)", l)
        if m:
            return m.group(1).strip()
    return ""


def parse_stanowiska(baza):
    """Stanowiska z bazy: tytuł, firma, daty, lokalizacja kopiowane dosłownie."""
    wynik = []
    for i, (naglowek, linie) in enumerate(sekcje(baza, "Stanowisko"), 1):
        if i in POMIJAJ_STANOWISKA:
            continue
        wynik.append({
            "id": f"J{i}",
            "title": re.sub(r"^Stanowisko\s+\d+\s*:\s*", "", naglowek),
            "company": czysc_md(pole(linie, "Firma")),
            "dates": czysc_md(pole(linie, "Okres pracy")),
            "location": czysc_md(pole(linie, "Lokalizacja")),
            "base_bullets": [czysc_md(l.strip()[2:]) for l in linie if re.match(r"^\s{2,}-\s", l)],
        })
    return wynik


PREFIKSY_PROJEKTOW = ("Projekt", "Osiągnięcie", "Realizacj", "Sukces")


def parse_projekty(baza):
    """Projekty (albo osiągnięcia/realizacje): nagłówki '## Projekt N:', '## Osiągnięcie N:' itd."""
    wynik = []
    for i, (naglowek, linie) in enumerate(sekcje(baza, PREFIKSY_PROJEKTOW), 1):
        nazwa = naglowek.split(":", 1)[1].strip() if ":" in naglowek else naglowek
        linki = re.findall(r"\((https?://[^)\s]+)\)", "\n".join(linie))
        opis = czysc_md(pole(linie, "Opis"))
        tech = czysc_md(pole(linie, "Technologie") or pole(linie, "Metody") or pole(linie, "Narzędzia")).rstrip(". ")
        if not opis:  # np. projekt z kilku części
            czesci = [czysc_md(l.strip()[2:]) for l in linie
                      if l.strip().startswith("- ") and "**Link:**" not in l]
            opis = " ".join(czesci)
        wynik.append({
            "id": f"P{i}", "name": nazwa, "link": linki[0] if linki else "",
            "links": linki, "description": opis, "technologies": tech,
        })
    return wynik


def parse_edukacja(baza):
    """Uczelnie z polami Kierunek, Tytuł, Lata oraz opcjonalnymi punktami (przedmioty, umiejętności)."""
    wynik = []
    for naglowek, linie in sekcje(baza, "Uczelnia"):
        punkty = [czysc_md(l.strip()[2:]) for l in linie
                  if l.strip().startswith("- ") and not l.strip().startswith("- **")]
        wynik.append({
            "school": re.sub(r"^Uczelnia\s+\d+\s*:\s*", "", naglowek),
            "field": czysc_md(pole(linie, "Kierunek / Program") or pole(linie, "Kierunek")),
            "degree": czysc_md(pole(linie, "Tytuł")),
            "years": czysc_md(pole(linie, "Lata")),
            "details": [p for p in punkty if p],
        })
    return wynik


def punkty_sekcji(baza, naglowek):
    wynik, w_sekcji = [], False
    for l in baza.splitlines():
        if l.startswith("# "):
            w_sekcji = (l[2:].strip() == naglowek)
        elif l.strip() == "---":
            w_sekcji = False
        elif w_sekcji and l.strip().startswith("- "):
            wynik.append(czysc_md(l.strip()[2:]))
    return wynik


def sklej_certyfikat(punkt):
    """'**Tytuł:** Udemy, Autor (07.2025)' -> 'Tytuł – Udemy, Autor (07.2025)'."""
    m = re.match(r"^\*\*(.+?):\*\*\s*(.*)$", punkt) or re.match(r"^\*\*(.+?)\*\*\s*[–-]?\s*(.*)$", punkt)
    if not m:
        return czysc_md(punkt)
    tytul, reszta = czysc_md(m.group(1)), czysc_md(m.group(2))
    reszta = reszta.lstrip("–- ").strip()
    return f"{tytul} – {reszta}" if reszta else tytul


NAGLOWKI_CERTYFIKATOW = ("Szkolenia i Certyfikaty", "Certyfikaty", "Szkolenia")
NAGLOWKI_KURSOW = ("Kursy",)


def parse_wpisy(baza, naglowki, prefix):
    """Wpisy z sekcji o jednym z podanych nagłówków. Wcięte punkty pod wpisem to jego szczegóły."""
    wpisy, w_sekcji = [], False
    for l in baza.splitlines():
        if l.startswith("# "):
            w_sekcji = (l[2:].strip() in naglowki)
        elif l.strip() == "---":
            w_sekcji = False
        elif w_sekcji and l.strip().startswith("- "):
            tekst = l.strip()[2:]
            if l.startswith("- "):
                wpisy.append({"text": sklej_certyfikat(tekst), "details": []})
            elif wpisy:
                wpisy[-1]["details"].append(czysc_md(tekst))
    return [{"id": f"{prefix}{i}", **w} for i, w in enumerate(wpisy, 1)]


def parse_certyfikaty(baza):
    return parse_wpisy(baza, NAGLOWKI_CERTYFIKATOW, "C")


def parse_kursy(baza):
    """Kursy bez certyfikatu: sekcja '# Kursy' w bazie."""
    return parse_wpisy(baza, NAGLOWKI_KURSOW, "K")


def wpis_do_cv(w):
    """Wpis bez szczegółów to zwykły tekst, ze szczegółami: {'text': ..., 'details': [...]}."""
    return {"text": w["text"], "details": w["details"]} if w["details"] else w["text"]


def opis_wpisu(w):
    return f"{w['id']}: {w['text']}" + (f" | szczegóły: {'; '.join(w['details'])}" if w["details"] else "")


# ------------------------------------------------------------
# Tłumaczenia stałych danych (edukacja, certyfikaty, opisy projektów) na angielski
# ------------------------------------------------------------
def wczytaj_tlumaczenia():
    if PLIK_TLUMACZEN.exists():
        return json.loads(PLIK_TLUMACZEN.read_text(encoding="utf-8"))
    return {}


MIESIACE_EN = {
    "styczeń": "January", "luty": "February", "marzec": "March", "kwiecień": "April", "maj": "May",
    "czerwiec": "June", "lipiec": "July", "sierpień": "August", "wrzesień": "September",
    "październik": "October", "listopad": "November", "grudzień": "December", "obecnie": "Present",
}


def tlumacz_daty(tekst):
    return re.sub(r"[^\W\d_]+", lambda m: MIESIACE_EN.get(m.group(0).lower(), m.group(0)), tekst)


def przetlumacz_dane(cv, jezyk, brakujace):
    """Dla CV w innym języku niż baza podmienia stałe teksty na tłumaczenia z pliku.
    Czego brakuje w słowniku, zostaje po polsku i trafia na listę ostrzeżeń."""
    if jezyk == BAZA_JEZYK:
        return cv
    t = wczytaj_tlumaczenia()

    def tl(sekcja, tekst):
        if not tekst:
            return tekst
        wynik = t.get(sekcja, {}).get(tekst)
        if wynik is None:
            brakujace.append(f"{sekcja}: {tekst}")
            return tekst
        return wynik

    for job in cv["jobs"]:
        job["title"] = tl("job_title", job["title"])
        job["dates"] = tlumacz_daty(job["dates"])
        job["location"] = t.get("location", {}).get(job["location"], job["location"])
    for wpis in cv["education"]:
        wpis["school"] = tl("school", wpis["school"])
        wpis["field"] = tl("field", wpis["field"])
        wpis["degree"] = tl("degree", wpis["degree"])
        if wpis.get("details"):
            tlumaczone = []
            for d in wpis["details"]:
                en = t.get("details", {}).get(d)
                if en is None:
                    brakujace.append(f"details: {d}")
                    en = d
                tlumaczone.append(en)
            wpis["details"] = tlumaczone
    def tl_wpis(sekcja, w):
        if not isinstance(w, dict):
            return tl(sekcja, w)
        szczegoly = []
        for d in w["details"]:
            wynik = t.get("details", {}).get(d)
            if wynik is None:
                brakujace.append(f"details: {d}")
                wynik = d
            szczegoly.append(wynik)
        return {"text": tl(sekcja, w["text"]), "details": szczegoly}

    cv["certificates"] = [tl_wpis("certificates", c) for c in cv["certificates"]]
    cv["courses"] = [tl_wpis("courses", c) for c in cv["courses"]]
    for p in cv["projects"]:
        opis = t.get("projects", {}).get(p["name"])
        if opis is None:
            brakujace.append(f"projects: {p['name']}")
        else:
            p["description"] = opis
    return cv


# ------------------------------------------------------------
# Schematy JSON (wymuszają format odpowiedzi modelu)
# ------------------------------------------------------------
def schemat_wymagan():
    return {
        "type": "object",
        "properties": {
            "wymagania": {"type": "array", "maxItems": 25, "items": {"type": "string"}},
            "wymogi_formalne": {"type": "array", "maxItems": 15, "items": {"type": "string"}},
        },
        "required": ["wymagania", "wymogi_formalne"],
    }


def schemat_cv(stanowiska, projekty, certyfikaty, kursy):
    props = {
        "profile": {"type": "string"},
        "jobs_bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": [j["id"] for j in stanowiska]},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "bullets"],
            },
        },
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
                "required": ["label", "value"],
            },
        },
    }
    wymagane = ["profile", "jobs_bullets", "skills"]
    return {"type": "object", "properties": props, "required": wymagane}


def dobierz_kontekst(system, user):
    """Szacuje wymagany kontekst na podstawie długości promptu."""
    # Heurystyka: w języku polskim 1 token to średnio ok. 3 znaki
    dlugosc_znakow = len(system) + len(user)
    tokeny_promptu = dlugosc_znakow / 3.0

    # Dodajemy zapas 2500 tokenów na wygenerowanie odpowiedzi przez model
    wymagane = tokeny_promptu + 2500

    # Wybór progów (możesz je modyfikować według własnych potrzeb)
    if wymagane <= 4096:
        return 4096
    elif wymagane <= 6144:
        return 6144
    elif wymagane <= 8192:
        return 8192
    elif wymagane <= 12288:
        return 12288
    else:
        return 16384

# ------------------------------------------------------------
# Rozmowa z Ollamą
# ------------------------------------------------------------
def zapytaj_ollame(system, user, schema, temperatura, etykieta, wymuszony_ctx=None):
    # Dynamiczne dobieranie kontekstu
    ctx = wymuszony_ctx if wymuszony_ctx else dobierz_kontekst(system, user)
    print(f"   [{etykieta}] Użyty kontekst: {ctx} tokenów.")

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": schema,
        "options": {"temperature": temperatura, "num_ctx": ctx},
        "stream": False,
    }
    start = time.time()
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_S)
        r.raise_for_status()
    except requests.exceptions.ConnectionError:
        sys.exit(
            "❌ Nie mogę połączyć się z Ollamą. Sprawdź adres w OLLAMA_URL, "
            "czy stacja jest włączona, czy działa Ollama i sieć."
        )
    except requests.exceptions.RequestException as e:
        sys.exit(f"❌ Błąd zapytania do Ollamy: {e}")

    dane = r.json()
    zuzyte = dane.get("prompt_eval_count")
    czas = time.time() - start
    szybkosc = ""
    if dane.get("eval_count") and dane.get("eval_duration"):
        szybkosc = f", generowanie ok. {dane['eval_count'] / (dane['eval_duration'] / 1e9):.1f} tok/s"
    print(f"   [{etykieta}] tokeny promptu: {zuzyte} z {ctx}, czas: {czas:.0f} s{szybkosc}")
    if zuzyte and zuzyte >= ctx * 0.95:
        print("⚠️  Kontekst prawie pełny - początek bazy mógł zostać ucięty. Zwiększ NUM_CTX.")
    if dane.get("done_reason") == "length":
        print("⚠️  Odpowiedź modelu została ucięta (limit długości).")

    tresc = dane["message"]["content"]
    zapisz_w_logu(f"surowa_odpowiedz_{etykieta}.txt", tresc)
    try:
        return json.loads(tresc)
    except json.JSONDecodeError:
        sys.exit("❌ Model zwrócił niepełny JSON (możliwe, że zabrakło kontekstu). "
                 f"Surowa odpowiedź jest w logu: {KATALOG_LOGU}")


# ------------------------------------------------------------
# KROK 1: analiza oferty - czego wymaga, co z tego masz
# ------------------------------------------------------------
def prompty_analizy(oferta, firma, stanowisko):
    """Model dostaje TYLKO ofertę. Porównanie z bazą robi kod, a nie model."""
    system = (
        "Jesteś rekruterem. Z ogłoszenia o pracę wypisz listę KONKRETNYCH wymagań i kluczowych "
        "obowiązków: narzędzia, technologie, programy, metody, uprawnienia, doświadczenie, języki, "
        "wykształcenie oraz cechy osobiste.\n\n"
        "ZASADY:\n"
        "1. Każda pozycja to krótka fraza (maksymalnie 6 słów), po polsku. Nazwy narzędzi "
        "i technologii zostaw bez zmian (np. Power BI, SQL, SAP).\n"
        "2. Nie przepisuj całych zdań z ogłoszenia i nie dodawaj etykiet typu 'Zakres obowiązków:' "
        "ani 'Dla nas ważne są:'.\n"
        "3. Pomiń opis firmy, benefity i oferowane warunki. Nie wymyślaj pozycji, których nie ma "
        "w ogłoszeniu.\n"
        "4. Maksymalnie 25 pozycji w 'wymagania'. Przejrzyj po kolei WSZYSTKIE sekcje ogłoszenia: "
        "wymagania, mile widziane i zakres obowiązków, i z każdej wypisz konkretne pozycje. Pozycje z sekcji "
        "'mile widziane' też wpisuj, tylko bez słów 'mile widziane', 'atut' i bez dopisków w nawiasach.\n"
        "5. WYMOGI FORMALNE I OSOBISTE wpisz do osobnego pola 'wymogi_formalne', a NIE do "
        "'wymagania'. Należą tu: status studenta, orzeczenie o niepełnosprawności lub grupa "
        "inwalidzka, prawo jazdy (zawsze z kategorią, np. 'Prawo jazdy kat. C'), książeczka "
        "sanepidowska, niekaralność, obywatelstwo lub zezwolenie na pracę, dyspozycyjność, praca "
        "zmianowa lub w weekendy, własny samochód, gotowość do relokacji lub podróży służbowych, "
        "badania lekarskie, specjalne uprawnienia, forma zatrudnienia (umowa o pracę, B2B, zlecenie) i tryb pracy "
        "(stacjonarnie, zdalnie, hybrydowo), jeśli ogłoszenie je podaje. Przepisuj je dokładnie, razem z kategorią. "
        "Umiejętności, doświadczenie, certyfikaty i narzędzia (także te 'mile widziane') idą do 'wymagania', "
        "NIE do 'wymogi_formalne'. Godziny pracy, wynagrodzenie i benefity pomiń.\n"
        "Odpowiedz wyłącznie obiektem JSON zgodnym ze schematem."
    )
    user = (
        f"OGŁOSZENIE O PRACĘ\nFirma: {firma}\nStanowisko: {stanowisko}\n"
        f"Treść oferty:\n{oferta}\n"
    )
    return system, user


def znaczace_slowa(tekst):
    """Lista (rdzeń 4-literowy, słowo) dla znaczących słów. Zachowuje akronimy (AI, BI, IT)."""
    wynik = []
    for w in re.findall(r"\w+", czysc_md(tekst)):
        nw = w.lower()
        if nw in SLOWA_POMIJANE or nw in SLOWA_OGOLNE or re.fullmatch(r"\d+[a-zA-Z]{0,2}", w):
            continue
        if len(nw) > 2 or (len(nw) == 2 and w.isupper()):
            wynik.append((rdzen_slowa(nw), w))
    return wynik


def rdzenie_slow(tekst):
    """Rdzenie (4 pierwsze litery) znaczących słów, bez słów ogólnych i krótkich spójników."""
    return [r for r, _ in znaczace_slowa(tekst)]


POLA_STRUKTURALNE = ("Firma", "Okres pracy", "Lokalizacja", "Zakres obowiązków", "Lata", "Link")


SEKCJE_BEZ_PREFIKSU = ("Kariera", "Edukacja", "Projekty", "Mój opis", "Dane formalne",
                       "Szkolenia i Certyfikaty", "Certyfikaty", "Szkolenia", "Kursy")


def linie_bazy(baza):
    """Lista (tekst linii, zbiór rdzeni słów) do szukania dopasowań w bazie.
    Pomija linie strukturalne (np. 'Firma: Ampak') i sekcje pomocnicze. Do linii z własnych sekcji
    (np. 'Metody wychowawcze', 'Narzędzia') dokleja nazwę sekcji, bo jest częścią znaczenia."""
    wynik = []
    wzor_pola = re.compile(r"^-\s*\*\*(?:" + "|".join(POLA_STRUKTURALNE) + r"):\*\*")
    h1, h2 = "", ""
    for l in baza.splitlines():
        t = l.strip()
        if l.startswith("# "):
            h1, h2 = l[2:].strip(), ""
        elif l.startswith("## "):
            h2 = l[3:].strip()
        if h1 in SEKCJE_POMOCNICZE:
            continue
        if not t or t.startswith("#") or t == "---" or wzor_pola.match(t):
            continue
        t = czysc_md(t.lstrip("- ").strip())
        prefiks = "" if h1 in SEKCJE_BEZ_PREFIKSU else (h2 or h1)
        if prefiks and not t.lower().startswith(prefiks.lower()):
            t = f"{prefiks}: {t}"
        wynik.append((t, {rdzen_slowa(w) for w in norm(t).split()}))
    return wynik


def pokrywa(rdzen, rdz_linii):
    """Czy rdzeń słowa (lub jego bliskoznacznik) występuje wśród rdzeni linii."""
    if rdzen in rdz_linii:
        return True
    return any(rdzen in grupa and grupa & rdz_linii for grupa in SYNONIMY + SYNONIMY_OSOBY)


def najblizsza_linia(rdzenie, linie):
    """Linia bazy pokrywająca najwięcej słów wymagania. Przy remisie wygrywa linia ze słowem
    głównym (pierwszym w wymaganiu), a potem krótsza (bardziej szczegółowa)."""
    najlepsza, klucz_najlepszy = "", None
    for tekst, rdz in linie:
        trafione = sum(pokrywa(r, rdz) for r in rdzenie)
        if not trafione:
            continue
        klucz = (trafione, pokrywa(rdzenie[0], rdz), -len(rdz))
        if klucz_najlepszy is None or klucz > klucz_najlepszy:
            najlepsza, klucz_najlepszy = tekst, klucz
    return najlepsza, (klucz_najlepszy[0] if klucz_najlepszy else 0)


def skroc(tekst, ile=100):
    return tekst if len(tekst) <= ile else tekst[: ile - 1].rstrip() + "…"


# --- czyszczenie wymagań z oferty ---
PREFIKS_MILE = re.compile(
    r"^\s*(?:mile\s+widzian\w+(?:\s+jest)?|dodatkowym\s+atutem\s+(?:będzie|jest)|atutem\s+(?:będzie|jest))\s*[:\-–]?\s*",
    re.I)
NAWIAS_ATUT = re.compile(r"\s*\((?=[^)]*(?:atut|niekonieczn|mile widzian|preferowan|opcjonaln))[^)]*\)", re.I)
SUFIKS_MILE = re.compile(r"\s*[,;–-]?\s*(?:mile\s+widzian\w+|jako\s+atut|atut)\s*$", re.I)


def oczysc_wymaganie(tekst):
    """Usuwa dopiski 'mile widziane', '(atut, niekonieczne)' itp., które model przepisuje z oferty."""
    t = str(tekst).strip()
    t = NAWIAS_ATUT.sub("", t)
    t = PREFIKS_MILE.sub("", t)
    t = SUFIKS_MILE.sub("", t)
    t = " ".join(t.split()).strip(" ,;.:–-")
    return (t[:1].upper() + t[1:]) if t else str(tekst).strip()


# --- cechy osobiste ---
CECHY_WZORZEC = re.compile(
    r"cierpliw|empati|zaangaż|inicjatyw|systematyczn|sumienn|rzetelno|odpowiedzialno|samodzielno|samodzielny\b|"
    r"komunikatywn|kreatywn|otwarto|elastyczn|punktualn|uczciwo|lojalno|dyskrecj|asertywn|motywac|entuzjazm|"
    r"determinac|życzliw|opiekuńczo|ciepł|lubieni|proaktywn|nastawieni\w* na|organizacj\w* (?:własnej )?pracy|"
    r"łatwo\w* w kontakc|profesjonaln\w* komunikac|gotowo\w* do nauk|dbało\w* o szczegół|"
    r"pozytywn\w* nastawieni|odporno\w* na stres|chęć|współprac")


def czy_cecha(nazwa):
    n = norm(nazwa)
    # "współpraca z rodzicami / interesariuszami" to obowiązek, a nie cecha ("współpraca z innymi" nią jest)
    n = re.sub(r"współprac\w*\s+z\s+(?!innymi\b|ludźmi\b|zespołem\b)\w+", " ", n)
    return any(norm(f) in n for f in CECHY_MIEKKIE) or bool(CECHY_WZORZEC.search(n))


# --- wymogi formalne: (nazwa, wzorzec w ofercie, etykiety pola w sekcji "# Dane formalne") ---
KATEGORIE_FORMALNE = [
    ("status studenta", r"studen|uczennic|uczeń", ("status studenta",)),
    ("niepełnosprawność", r"niepełnosprawn|inwalid|orzeczeni", ("orzeczenie o niepełnosprawności", "niepełnosprawn")),
    ("książeczka sanepidowska", r"książeczk|sanepid|sanitarno", ("książeczka", "sanepid")),
    ("niekaralność", r"niekaral|karalno", ("niekaralnoś",)),
    ("praca zmianowa", r"zmianow|weekend|nocn", ("praca zmianowa", "zmianow")),
    ("forma zatrudnienia",
     r"umow\w* o prac|umow\w* zlecen|umow\w* o dzie|kontrakt\w* b2b|b2b kontrakt|forma zatrudnienia|"
     r"samozatrudnien|współprac\w* b2b",
     ("forma zatrudnienia", "forma współpracy", "b2b")),
    ("tryb pracy", r"stacjonarn|zdaln|hybrydow|\bremote\b|home office|w biurze",
     ("tryb pracy", "praca zdalna", "preferowany tryb")),
    ("podróże służbowe", r"podróż|delegac", ("podróż",)),
    ("relokacja", r"relokac|przeprowadzk", ("relokac",)),
    ("własny samochód", r"samochód|pojazd", ("samochód",)),
    ("badania lekarskie", r"badań lekarsk|badania lekarsk|medycyny pracy", ("badania lekarskie",)),
    ("godziny pracy", r"godzin\w* pracy|\d{1,2}[:.]\d{2}\s*[-–]\s*\d{1,2}[:.]\d{2}|\bpn\s*[-–]\s*pt\b", ()),
    ("wymiar etatu", r"pełn\w* etat|część etatu|niepełn\w* wymiar|wymiar etatu", ()),
]
# Pozycje, które model wpisał do "wymogi_formalne", ale nie są żadną znaną kategorią: zostają formalne tylko z tymi słowami.
CECHY_UMIEJETNOSCI = re.compile(
    r"doświadcz|znajomo|umiejętno|certyfikat|język|kurs|szkolen|wykształc|obsług|zarządz|prowadz|tworz|analiz")
FORMALNE_DODATKOWE = re.compile(r"wymog|ustawow|zaświadcz|orzeczen|badani|uprawnien|zezwole|obywatel|dyspozycyjn")


def czy_formalne(nazwa):
    n = norm(nazwa)
    return any(norm(f) in n for f in FORMALNE_FRAZY) or any(re.search(k[1], n) for k in KATEGORIE_FORMALNE)


def rozdziel_wymogi(analiza):
    """Zwraca (wymagania, wymogi_formalne). Czyści dopiski 'mile widziane'. Przenosi wymogi formalne ze zwykłych
    wymagań, a umiejętności wpisane przez model do wymogów formalnych (np. certyfikaty) wraca do wymagań."""
    formalne_modelu = [oczysc_wymaganie(x) for x in analiza.get("wymogi_formalne", []) if str(x).strip()]
    zwykle = [oczysc_wymaganie(x) for x in analiza.get("wymagania", []) if str(x).strip()]
    wymagania, wymogi = [], []
    for w in formalne_modelu + zwykle:
        n = norm(w)
        if re.match(r"(?:brak|nie dotyczy|nie podano|nie wymaga)\b", n):
            continue
        formalne = czy_formalne(w) or (w in formalne_modelu and bool(FORMALNE_DODATKOWE.search(n)))
        # Pozycja, którą model wpisał do wymogów formalnych, a która nie jest ani wymogiem formalnym, ani umiejętnością
        # (np. benefity: "premia frekwencyjna", "praca w nowoczesnej placówce"), jest pomijana.
        if w in formalne_modelu and not formalne and not (CECHY_UMIEJETNOSCI.search(n) or nazwy_wlasne(w)):
            continue
        cel = wymogi if formalne else wymagania
        if w.lower() not in {x.lower() for x in cel}:
            cel.append(w)
    # Krótkie pozycje, które są tylko fragmentem dłuższego wymagania ("MS Excel" przy "Bardzo dobra znajomość MS Excel"),
    # nie są osobnym wymaganiem: model często powtarza tak nazwy narzędzi z nagłówka oferty.
    n_wym = [norm(x) for x in wymagania]
    wymagania = [x for i, x in enumerate(wymagania)
                 if not (len(n_wym[i].split()) <= 3 and any(
                     j != i and re.search(rf"\b{re.escape(n_wym[i])}\b", n_wym[j]) for j in range(len(n_wym))))]
    return wymagania, wymogi


def kategorie_prawa_jazdy(tekst):
    """Z tekstu 'Prawo jazdy kat. B lub C' zwraca ({'B','C'}, czy_wystarczy_dowolna)."""
    m = re.search(r"(?i:kat(?:egori[ai]|\.)?)\s*((?:[A-E]\d?(?:\+E)?)(?:\s*(?:,|/|\+|i|oraz|lub)\s*(?:[A-E]\d?(?:\+E)?))*)", tekst)
    if not m:
        return set(), False
    ciag = m.group(1)
    return set(re.findall(r"[A-E]", ciag)), bool(re.search(r"\blub\b|/", ciag))


def sprawdz_formalne(wymogi, baza):
    """Każdy wymóg dostaje status: ok / info (w bazie jest wpis, oceń sam) / brak_kat (inna kategoria prawa jazdy) /
    brak_info (w bazie nie ma pola) / reczne (nie ma odpowiednika w bazie, oceń sam)."""
    linie = linie_bazy(baza)
    pozycje = [(norm(p.partition(":")[0]), p) for p in punkty_sekcji(baza, "Dane formalne")]
    wynik = []
    for nazwa in wymogi:
        n = norm(nazwa)
        if "prawo jazdy" in n or "prawa jazdy" in n:
            maja = set()
            for t, _ in linie:
                if "prawo jazdy" in norm(t):
                    maja |= kategorie_prawa_jazdy(t)[0]
            wym, dowolna = kategorie_prawa_jazdy(nazwa)
            posiadane = ", ".join(sorted(maja))
            if not maja:
                wynik.append({"nazwa": nazwa, "status": "brak_info",
                              "uwaga": "w bazie nie ma informacji o prawie jazdy"})
            elif not wym:
                wynik.append({"nazwa": nazwa, "status": "info", "uwaga": f"w bazie masz kategorie: {posiadane}"})
            elif (wym & maja) if dowolna else wym <= maja:
                wynik.append({"nazwa": nazwa, "status": "ok", "uwaga": f"w bazie masz kategorie: {posiadane}"})
            else:
                wynik.append({"nazwa": nazwa, "status": "brak_kat",
                              "uwaga": f"wymagane: {', '.join(sorted(wym))}, w bazie masz: {posiadane}"})
            continue
        kat = next((k for k in KATEGORIE_FORMALNE if re.search(k[1], n)), None)
        if kat is None or not kat[2]:
            wynik.append({"nazwa": nazwa, "status": "reczne", "uwaga": "oceń sam, w bazie nie ma na to pola"})
            continue
        znaleziona = next((p for et, p in pozycje if any(norm(e) in et for e in kat[2])), None)
        if znaleziona is None:
            wynik.append({"nazwa": nazwa, "status": "brak_info",
                          "uwaga": f'brak w bazie (dopisz do sekcji "# Dane formalne" pole "{kat[0]}")'})
        else:
            wynik.append({"nazwa": nazwa, "status": "info", "uwaga": f'w bazie: "{skroc(znaleziona, 80)}"'})
    return wynik


SEPARATORY_WYMAGAN = re.compile(
    r"\s*(?:,|;|/|\(|\)|\bnp\.|\blub\b|\balbo\b|\boraz\b|\bi\b|\bczy\b|\s[—–-]\s)\s*", re.I)
SYGNALY_ALTERNATYWY = re.compile(
    r"\blub\b|\balbo\b|\bnp\.|/|przynajmniej jednego|dowoln|pokrewn", re.I)


def rozbij_wymaganie(nazwa):
    """Dzieli wymaganie na człony. Zwraca (człony, czy_wystarczy_jeden_człon)."""
    alternatywa = bool(SYGNALY_ALTERNATYWY.search(nazwa))
    czlony = [c.strip() for c in SEPARATORY_WYMAGAN.split(nazwa) if c and c.strip()]
    return czlony, alternatywa


def ocen_wymaganie(nazwa, linie, wszystkie):
    """Ocenia wymaganie człon po członie. Zwraca (status, wpis); status: mam / prawdopodobnie / brak / cecha.

    Przy alternatywie ("lub", "np.", "przynajmniej jednego") wystarczy jeden człon.
    Przy wyliczeniu ("i", przecinki) znalezione człony są pokazywane osobno od brakujących.
    """
    czlony, alternatywa = rozbij_wymaganie(nazwa)
    oceny = []
    for c in czlony:
        rdz = rdzenie_slow(c)
        if not rdz:
            continue
        linia, trafione = najblizsza_linia(rdz, linie)
        mocny = trafione == len(rdz)
        slaby = (not mocny) and (
            all(pokrywa(r, wszystkie) for r in rdz)
            or (len(rdz) >= 3 and trafione >= 2 and 3 * trafione >= 2 * len(rdz)))
        oceny.append({"czlon": c, "linia": linia if trafione else "", "mocny": mocny, "slaby": slaby})
    if not oceny:
        return "cecha", {"nazwa": nazwa, "linia": ""}
    mocne = [o for o in oceny if o["mocny"]]
    slabe = [o for o in oceny if o["slaby"]]
    znalezione = mocne + slabe
    if mocne:
        linie_dow = []
        for o in (mocne[:1] if alternatywa else mocne):
            if o["linia"] and o["linia"] not in linie_dow:
                linie_dow.append(o["linia"])
        linia = " / ".join(linie_dow)
    else:
        linia = (slabe or oceny)[0]["linia"] if slabe else ""
    wpis = {"nazwa": nazwa, "linia": linia,
            "masz": [o["czlon"] for o in znalezione],
            "brak": [o["czlon"] for o in oceny if o not in znalezione],
            "czlony": [o["czlon"] for o in oceny],
            "brak_czlony": [o["czlon"] for o in oceny if o not in znalezione]}
    if alternatywa:
        return ("mam" if mocne else "prawdopodobnie" if slabe else "brak"), wpis
    if len(mocne) == len(oceny):
        if (re.search(r"bardzo dobr|zaawansowan|biegł|ekspert|wysok\w* poziom|zaawansowan", norm(nazwa))
                and re.search(r"podstaw|początkując|basic", norm(wpis["linia"]))):
            wpis["brak"] = [f"zaawansowana znajomość (w bazie tylko podstawy): {wpis['linia'][:60]}"]
            wpis["masz"] = [o["czlon"] for o in znalezione]
            return "prawdopodobnie", wpis
        return "mam", wpis
    return ("prawdopodobnie" if znalezione else "brak"), wpis


# --- wymagania, które wymagają osobnej logiki: języki, lata doświadczenia, wykształcenie ---
POZIOMY_CEFR = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}
NAZWY_POZIOMOW = {v: k for k, v in POZIOMY_CEFR.items()}
JEZYKI_OBCE = {   # nazwa -> początki słów w ofercie i bazie
    "angielski": ("angielsk", "english"), "niemiecki": ("niemieck", "german", "deutsch"),
    "francuski": ("francusk", "french"), "hiszpański": ("hiszpańsk", "spanish"), "rosyjski": ("rosyjsk", "russian"),
    "włoski": ("włosk", "italian"), "ukraiński": ("ukraińsk",), "czeski": ("czesk",), "norweski": ("norwesk",),
    "szwedzki": ("szwedzk",), "duński": ("duńsk",), "holenderski": ("holendersk", "niderlandzk"),
    "portugalski": ("portugalsk",), "chiński": ("chińsk",), "japoński": ("japońsk",), "słowacki": ("słowack",),
}
JEZYK_SYGNAL = r"j[eę]zyk|poziom|\b[abc][12]\b|w mowie|piśmie|biegł|swobodn|fluent|komunikatywn"


def poziomy_cefr(tekst):
    return [POZIOMY_CEFR[x.upper()] for x in re.findall(r"\b([abc][12])\b", norm(tekst))]


def poziom_slowny(tekst):
    t = norm(tekst)
    if re.search(r"ojczyst|native", t):
        return 6
    if re.search(r"biegł|płynn|swobodn|fluent|zaawansowan", t):
        return 5
    if re.search(r"komunikatyw|średniozaawans|średnio zaawans", t):
        return 3
    if re.search(r"podstaw|początk|basic", t):
        return 2
    return 0


def poziom_w_bazie(kanon, linie):
    """(czy język jest w bazie, najwyższy znaleziony poziom 0-6, fragment bazy)."""
    prefiksy = JEZYKI_OBCE[kanon]
    znaleziony, najlepszy, dowod = False, -1, ""
    for tekst, _ in linie:
        for seg in rozdziel_po_przecinkach(tekst):
            if any(w.startswith(p) for w in norm(seg).split() for p in prefiksy):
                znaleziony = True
                poziom = max(poziomy_cefr(seg) or [0]) or poziom_slowny(seg)
                if poziom > najlepszy:
                    najlepszy, dowod = poziom, seg.strip()
    return znaleziony, max(najlepszy, 0), dowod


def ocen_jezyki(nazwa, linie):
    n = norm(nazwa)
    slowa = n.split()
    jezyki = []
    for kanon, prefiksy in JEZYKI_OBCE.items():
        pozycje = [i for i, w in enumerate(slowa) if any(w.startswith(p) for p in prefiksy)]
        if pozycje:
            jezyki.append((pozycje[0], kanon))
    if not jezyki or not re.search(JEZYK_SYGNAL, n):
        return None
    jezyki.sort()
    cefr_calosc = poziomy_cefr(nazwa)
    wyniki = []
    for k, (poz, kanon) in enumerate(jezyki):
        koniec = jezyki[k + 1][0] if k + 1 < len(jezyki) else len(slowa)
        okno = " ".join(slowa[poz:koniec])
        cefr = poziomy_cefr(okno) or cefr_calosc
        wymagany = min(cefr) if cefr else (poziom_slowny(okno) or poziom_slowny(nazwa))
        jest, poziom, dowod = poziom_w_bazie(kanon, linie)
        if not jest:
            status = "brak"
        elif wymagany == 0 or poziom >= wymagany:
            status = "mam"
        else:
            status = "czesc"          # język jest, ale poziom niższy albo niepodany w bazie
        wyniki.append((kanon, status, wymagany, dowod))
    alternatywa = bool(re.search(r"\blub\b|\balbo\b|\bor\b", n))
    if alternatywa:
        status = "mam" if any(w[1] == "mam" for w in wyniki) else \
            "prawdopodobnie" if any(w[1] == "czesc" for w in wyniki) else "brak"
    else:
        status = "mam" if all(w[1] == "mam" for w in wyniki) else \
            "prawdopodobnie" if any(w[1] != "brak" for w in wyniki) else "brak"
    return status, {
        "nazwa": nazwa, "linia": " / ".join(w[3] for w in wyniki if w[3]),
        "masz": [f"język {w[0]}" for w in wyniki if w[1] != "brak"],
        "brak": [f"język {w[0]}" + (f" na poziomie {NAZWY_POZIOMOW.get(w[2])}" if w[2] else "")
                 for w in wyniki if w[1] != "mam"],
        "czlony": [],
    }


MIESIACE_PL = {
    "styczeń": 1, "stycznia": 1, "luty": 2, "lutego": 2, "marzec": 3, "marca": 3, "kwiecień": 4, "kwietnia": 4,
    "maj": 5, "maja": 5, "czerwiec": 6, "czerwca": 6, "lipiec": 7, "lipca": 7, "sierpień": 8, "sierpnia": 8,
    "wrzesień": 9, "września": 9, "październik": 10, "października": 10, "listopad": 11, "listopada": 11,
    "grudzień": 12, "grudnia": 12,
}


def okres_stanowiska(daty):
    """'Styczeń 2022 – Obecnie' -> (miesiąc początku, miesiąc końca) liczone od roku 0, albo None."""
    t = daty.lower()
    traf = list(re.finditer(r"(?:([^\W\d_]+)\s+)?(?<!\d)(\d{4})(?!\d)", t))
    if not traf:
        return None

    def miesiac(tr, domyslny):
        return (tr.group(1) and MIESIACE_PL.get(tr.group(1))) or domyslny

    start = int(traf[0].group(2)) * 12 + miesiac(traf[0], 1) - 1
    if len(traf) > 1:
        koniec = int(traf[1].group(2)) * 12 + miesiac(traf[1], 12) - 1
    elif re.search(r"obecnie|obecny|teraz|present|dziś", t):
        teraz = datetime.now()
        koniec = teraz.year * 12 + teraz.month - 1
    else:
        koniec = start
    return start, max(start, koniec)


def lata_stazu(stanowiska, filtr=None):
    """Łączny czas pracy na stanowiskach (okresy nakładające się liczone raz), w latach."""
    okresy = sorted(o for s in stanowiska if (not filtr or filtr(s)) for o in [okres_stanowiska(s["dates"])] if o)
    suma, koniec = 0, None
    for a, b in okresy:
        if koniec is None or a > koniec:
            suma += b - a + 1
            koniec = b
        elif b > koniec:
            suma += b - koniec
            koniec = b
    return suma / 12


def ocen_staz(nazwa, ctx):
    """Wymagania typu 'min. 5 lat doświadczenia (w sprzedaży)': porównuje z czasem pracy na pasujących stanowiskach."""
    n = norm(nazwa)
    if not re.search(r"doświadcz|staż", n):
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:letni\w*|lat\w*|rok\w*)", n)
    if m:
        wymagane = float(m.group(1).replace(",", "."))
    elif re.search(r"półroczn|pół roku|6 miesi", n):
        wymagane = 0.5
    else:
        return None
    reszta = re.sub(r"(?i)\d+(?:[.,]\d+)?[\s-]*(?:letni\w*|lat\w*|rok\w*)", " ", nazwa)
    tematyka = rdzenie_slow(reszta)

    def pasuje(s):
        rdz = {rdzen_slowa(w) for w in norm(" ".join([s["title"], s.get("company", ""), *s["base_bullets"]])).split()}
        return sum(pokrywa(r, rdz) for r in tematyka) >= max(1, math.ceil(0.6 * len(tematyka)))

    lat = lata_stazu(ctx["stanowiska"], pasuje if tematyka else None)
    nazwy = [s["title"] for s in ctx["stanowiska"] if not tematyka or pasuje(s)]
    opis = f"w bazie ok. {lat:.1f} lat" + (f" ({'; '.join(nazwy)[:70]})" if nazwy else "")
    wpis = {"nazwa": nazwa, "linia": opis if lat else "", "masz": [], "brak": [], "czlony": []}
    if lat >= wymagane:
        wpis["masz"] = [f"ok. {lat:.0f} lat doświadczenia"]
        return "mam", wpis
    if lat >= 0.7 * wymagane:
        wpis["masz"], wpis["brak"] = [f"ok. {lat:.1f} lat doświadczenia"], [f"min. {wymagane:g} lat"]
        return "prawdopodobnie", wpis
    return "brak", wpis


def ocen_wyksztalcenie(nazwa, ctx):
    """Poziom wykształcenia (wyższe / średnie) i kierunek porównywane z sekcją '# Edukacja' bazy."""
    n = norm(nazwa)
    edu = ctx["edukacja"]
    if "wykształcen" not in n or not edu:
        return None
    wymaga_wyzszego = bool(re.search(r"wyższ|magister|licencjat|inżynier|studi", n))
    wymaga_sredniego = bool(re.search(r"średni", n))
    ma_wyzsze = any(re.search(r"magister|inżynier|licencjat|doktor|studia", e["degree"].lower()) for e in edu)
    ma_srednie = ma_wyzsze or any(
        re.search(r"matura|średni|technikum|liceum", " ".join([e["degree"], e["school"], e["field"]]).lower())
        for e in edu)
    poziom_ok = ma_wyzsze if wymaga_wyzszego else ma_srednie if wymaga_sredniego else True
    pola = rdzenie_slow(nazwa)
    slowa_pol = [w for _, w in znaczace_slowa(nazwa)]
    tekst_edu = " ".join(" ".join([e["field"], e["degree"], e["school"], *e.get("details", [])]) for e in edu)
    rdz_edu = {rdzen_slowa(w) for w in norm(tekst_edu).split()}
    opis_edu = " / ".join(", ".join(x for x in (e["field"], e["degree"]) if x) for e in edu)
    if not poziom_ok:
        return "brak", {"nazwa": nazwa, "linia": "", "masz": [], "brak": [nazwa], "czlony": []}
    if not pola or any(pokrywa(r, rdz_edu) for r in pola):
        return "mam", {"nazwa": nazwa, "linia": opis_edu, "masz": [], "brak": [], "czlony": []}
    alt = [k for k in ctx["kwalifikacje"]
           if any(pokrywa(r, {rdzen_slowa(w) for w in norm(k).split()}) for r in pola)]
    poziom_txt = "wyższe" if ma_wyzsze else "średnie"
    if alt:
        return "prawdopodobnie", {"nazwa": nazwa, "linia": alt[0], "czlony": [],
                                   "masz": [f"kwalifikacje lub kurs: {alt[0]}"],
                                   "brak": [f"wykształcenie kierunkowe: {', '.join(slowa_pol)}"]}
    return "prawdopodobnie", {"nazwa": nazwa, "linia": opis_edu, "czlony": [],
                               "masz": [f"wykształcenie {poziom_txt} ({opis_edu})"],
                               "brak": [f"kierunek: {', '.join(slowa_pol)}"]}


def ocen_specjalne(nazwa, ctx):
    return (ocen_jezyki(nazwa, ctx["linie"]) or ocen_staz(nazwa, ctx) or ocen_wyksztalcenie(nazwa, ctx))


def parse_slownik(baza):
    """Sekcja '# Słowa kluczowe': linie '- Salesforce = CRM' to synonimy (po jednym słowie na pozycję)."""
    grupy, w_sekcji = [], False
    for l in baza.splitlines():
        if l.startswith("# "):
            w_sekcji = (l[2:].strip() == "Słowa kluczowe")
        elif w_sekcji and l.strip().startswith("- ") and "=" in l:
            grupa = set()
            for termin in re.split(r"[=,;]", czysc_md(l.strip()[2:])):
                slowa = re.findall(r"\w+", termin)
                if slowa:
                    grupa.add(rdzen_slowa(slowa[0]))
            if len(grupa) > 1:
                grupy.append(grupa)
    return grupy


def nazwy_wlasne(tekst):
    """Nazwy narzędzi i technologii z wymagania: akronimy (SAP, BPMN), słowa z cyfrą lub wielką literą w środku,
    wielkie litery w środku frazy (Jira, Docker). Takie słowo albo jest w bazie, albo go nie ma."""
    slowa = re.findall(r"[\wąćęłńóśźż.+#]+", czysc_md(tekst), re.I)
    wynik = []
    for i, w in enumerate(slowa):
        w = w.strip(".")
        if len(w) < 2 or w.lower() in SLOWA_POMIJANE:
            continue
        if (w.isupper() or re.search(r"[a-ząćęłńóśźż][A-ZĄĆĘŁŃÓŚŹŻ]", w)
                or (re.search(r"\d", w) and re.search(r"[A-Za-z]", w)) or (i > 0 and w[0].isupper())):
            wynik.append(w)
    return wynik


def brakuje_nazw_wlasnych(wymaganie, baza):
    """True, jeśli wymaganie zawiera nazwę narzędzia, której nie ma nigdzie w bazie (wtedy podobieństwo semantyczne
    nie może go uratować: 'Docker' nie jest 'czymś podobnym do SQL')."""
    tekst_bazy = norm(baza)
    return any(norm(w)[:5] not in tekst_bazy for w in nazwy_wlasne(wymaganie) if norm(w))


def rozszerz_semantycznie(kandydaci, linie, baza, prawdopodobne, braki):
    """Wymagania uznane za braki, ale znaczeniowo bliskie jakiejś linii bazy, awansują do 'prawdopodobnych'.
    Nigdy do 'znalezionych'. Pominięte: wymagania z nazwą własną, której nie ma w bazie, oraz języki, lata
    i wykształcenie (mają własną logikę, a podobieństwo pomyliłoby np. niemiecki z angielskim)."""
    do_sprawdzenia = [(w, c) for w, c in kandydaci if not brakuje_nazw_wlasnych(c, baza)]
    if not do_sprawdzenia or not linie:
        return
    try:
        import embeddingi
        teksty_linii = [t for t, _ in linie]
        wynik = embeddingi.najblizsze([c for _, c in do_sprawdzenia], teksty_linii,
                                      embeddingi.wektory_linii(teksty_linii))
    except Exception as e:                       # brak modelu, brak sieci, brak pliku embeddingi.py
        print(f"⚠️  Embeddingi niedostępne ({type(e).__name__}), dopasowanie tylko po słowach "
              f"(sprawdź: ollama pull {os.getenv('EMB_MODEL', 'bge-m3')}).")
        return
    for (wpis, _), (sim, linia) in zip(do_sprawdzenia, wynik):
        wpis["podobienstwo"], wpis["najblizsza"] = round(sim, 2), linia
        if sim >= EMB_PROG:
            braki.remove(wpis)
            wpis.update(linia=linia, semantyczne=True, masz=[], brak=[], czlony=[])
            prawdopodobne.append(wpis)


def podziel_wymagania(wymagania, baza):
    """Porównuje wymagania z ofert z bazą. Zwraca: znalezione, prawdopodobne, cechy, braki.

    Znalezione      - wszystkie człony wymagania (albo jeden przy alternatywie "lub") mają linię w bazie,
    Prawdopodobne   - część członów jest w bazie (pokazuję, które) albo słowa są rozproszone,
    Cechy           - cechy osobiste (nie da się ich udowodnić linią z bazy),
    Braki           - brak śladu w bazie.
    Języki, lata doświadczenia i wykształcenie mają osobną logikę (poziom, staż z dat stanowisk, kierunek).
    """
    SYNONIMY_OSOBY[:] = parse_slownik(baza)
    linie = linie_bazy(baza)
    wszystkie = set().union(*(r for _, r in linie)) if linie else set()
    ctx = {"stanowiska": parse_stanowiska(baza), "edukacja": parse_edukacja(baza), "linie": linie,
           "kwalifikacje": [w["text"] for w in parse_certyfikaty(baza) + parse_kursy(baza)]}
    mam, prawdopodobne, cechy, braki, widziane, kandydaci = [], [], [], [], set(), []
    for nazwa in wymagania:
        nazwa = str(nazwa).strip()
        if not nazwa or nazwa.lower() in widziane:
            continue
        widziane.add(nazwa.lower())
        czysta = oczysc_wymaganie(nazwa)           # dopasowuję bez dopisków "mile widziane", nazwa zostaje oryginalna
        if czy_cecha(czysta):
            rdz = rdzenie_slow(czysta)
            linia, trafione = najblizsza_linia(rdz, linie) if rdz else ("", 0)
            cechy.append({"nazwa": nazwa, "linia": linia if trafione else ""})
            continue
        specjalne = ocen_specjalne(czysta, ctx)
        status, wpis = specjalne or ocen_wymaganie(czysta, linie, wszystkie)
        wpis["nazwa"], wpis["specjalne"] = nazwa, bool(specjalne)
        {"cecha": cechy, "mam": mam, "prawdopodobnie": prawdopodobne, "brak": braki}[status].append(wpis)
        if status == "brak" and not specjalne:
            kandydaci.append((wpis, czysta))
    if EMBEDDINGI and kandydaci:
        rozszerz_semantycznie(kandydaci, linie, baza, prawdopodobne, braki)
    return mam, prawdopodobne, cechy, braki


def wypisz_raport_wymagan(mam, prawdopodobne, cechy, braki, formalne=()):
    linie = []
    if formalne:
        znaki = {"ok": "✓", "info": "~", "brak_kat": "✗", "brak_info": "?", "reczne": "·"}
        linie.append("")
        linie.append(f"📋 WYMOGI FORMALNE I OSOBISTE Z OFERTY (sprawdź, czy je spełniasz) ({len(formalne)}):")
        for f in formalne:
            linie.append(f"   {znaki[f['status']]} {f['nazwa']}   [{f['uwaga']}]")
    if braki:
        linie.append("")
        linie.append("🔴" * 3 + f" W OFERCIE SĄ WYMAGANIA, KTÓRYCH NIE MA W TWOJEJ BAZIE ({len(braki)}):")
        for b in braki:
            linie.append(f"   ✗ {b['nazwa']}")
        linie.append("   → Jeśli to masz, dopisz do bazy. Jeśli nie, to są Twoje braki względem oferty.")
        linie.append("     (szukam po słowach, więc przy innych sformułowaniach może być fałszywy alarm)")
    else:
        linie.append("\n✅ Wszystkie konkretne wymagania z oferty mają ślad w Twojej bazie.")
    if prawdopodobne:
        linie.append(f"\n🟡 CZĘŚCIOWO LUB PRAWDOPODOBNIE MASZ, SPRAWDŹ ({len(prawdopodobne)}):")
        for p in prawdopodobne:
            linie.append(f"   ~ {p['nazwa']}" + (f"   (podobne po znaczeniu, {p['podobienstwo']})" if p.get("semantyczne") else ""))
            if p.get("masz") and p.get("brak"):
                linie.append(f"       masz:  {'; '.join(p['masz'])}")
                linie.append(f"       brak:  {'; '.join(p['brak'])}")
            if p["linia"]:
                linie.append(f"       najbliższy fragment bazy: \"{skroc(p['linia'])}\"")
    if cechy:
        linie.append(f"\n💬 CECHY OSOBISTE Z OFERTY (nie da się ich sprawdzić w bazie, oceń sam) ({len(cechy)}):")
        for c in cechy:
            slad = f"w bazie: \"{skroc(c['linia'], 80)}\"" if c["linia"] else "w bazie: brak śladu"
            linie.append(f"   · {c['nazwa']}  ({slad})")
    if mam:
        linie.append(f"\n✅ ZNALEZIONE W BAZIE ({len(mam)}):")
        for m in mam:
            linie.append(f"   ✓ {m['nazwa']}  ←  \"{skroc(m['linia'])}\"")
    tekst = "\n".join(linie)
    print(tekst)
    return tekst


def zapytaj_o_braki(braki):
    """Zwraca 'k' (kontynuuj), 'u' (uzupełnij bazę) albo 'p' (przerwij)."""
    if not braki or not sys.stdin.isatty():
        return "k"
    print("\nCo robisz?")
    print("  [Enter] kontynuuj mimo braków")
    print("  [u]     uzupełnij bazę (otworzę plik, po zapisaniu od razu porównam ponownie)")
    print("  [p]     przerwij")
    while True:
        wybor = input("Wybór: ").strip().lower()
        if wybor in ("", "k"):
            return "k"
        if wybor in ("u", "p"):
            return wybor
        print("   Wpisz Enter, u albo p.")


def otworz_baze():
    print(f"📝 Otwieram bazę: {BAZA_MD}")
    try:
        os.startfile(str(BAZA_MD))        # Windows: domyślny edytor dla plików .md
    except AttributeError:
        print("   (ten system nie otwiera plików automatycznie, otwórz bazę ręcznie)")
    input("   Uzupełnij bazę, ZAPISZ plik i naciśnij Enter, żeby porównać ponownie... ")


# ------------------------------------------------------------
# KROK 2: generowanie CV pod ofertę
# ------------------------------------------------------------
def zbuduj_prompty_cv(baza, oferta, firma, stanowisko, jezyk, mam, braki, stanowiska):
    cfg = JEZYKI[jezyk]
    potw_l, brak_l = [], [f"- {b['nazwa']}" for b in braki]
    for m in mam:
        if m.get("masz") and m.get("brak"):
            potw_l += [f"- {x}" for x in m["masz"]]     # tylko ta część wymagania, którą kandydat naprawdę ma
            brak_l += [f"- {x}" for x in m["brak"]]     # reszty nie wolno przypisywać kandydatowi
        else:
            potw_l.append(f"- {m['nazwa']} (tylko jeśli potwierdza to fragment bazy: \"{skroc(m['linia'], 120)}\")"
                          if m.get("semantyczne") else f"- {m['nazwa']}")
    potw = "\n".join(potw_l) or "(brak)"
    brak = "\n".join(brak_l) or "(brak)"
    stan = "\n".join(f"{j['id']}: {j['title']} | {j['company']} | {j['dates']}" for j in stanowiska) or "(brak)"
    kat = kategorie_umiejetnosci(jezyk)
    nazwy_kat = [k.strip().lower() for k in kat.split(",")]
    dodatki_kat = ""
    if any(k in ("umiejętności twarde", "hard skills") for k in nazwy_kat):
        dodatki_kat += (" Kategoria 'Umiejętności twarde' (Hard skills) ma zawierać umiejętności "
                        "z sekcji 'Umiejętności Twarde' bazy przydatne w tej ofercie.")
    if any(k in ("uprawnienia", "licenses") for k in nazwy_kat):
        dodatki_kat += (" Kategoria 'Uprawnienia' (Licenses): tylko jeśli oferta ich wymaga "
                        "i kandydat ma je w bazie (np. prawo jazdy), inaczej pomiń.")
    if any(k in ("zainteresowania", "interests") for k in nazwy_kat):
        dodatki_kat += " Kategorię 'Zainteresowania' wypełnij krótko (1-3 pozycje)."

    plec = ""
    if jezyk == "pl":
        plec = (" Kandydat jest mężczyzną: używaj WYŁĄCZNIE form męskich (np. 'doświadczony', "
                "'zorganizowany'), żadnych form żeńskich." if PLEC_KANDYDATA == "m" else
                " Kandydatka jest kobietą: używaj WYŁĄCZNIE form żeńskich.")
    system = (
        "Jesteś doradcą zawodowym. Na podstawie bazy doświadczenia kandydata i ogłoszenia "
        "o pracę przygotowujesz dane JSON do CV dopasowanego do TEJ konkretnej oferty. "
        "Dwie różne oferty muszą dać wyraźnie różne CV.\n\n"
        "ZASADY:\n"
        "1. Używaj WYŁĄCZNIE faktów z bazy. Nie wymyślaj firm, stanowisk, dat, liczb, "
        "technologii, narzędzi ani certyfikatów. Nie wspominaj o wymaganiach z listy "
        "BRAKÓW, nawet jeśli oferta ich żąda.\n"
        "1a. CAŁĄ TREŚĆ (zarówno 'profile', jak i 'bullets') pisz w PIERWSZEJ OSOBIE LICZBY POJEDYNCZEJ "
        "(np. 'Jestem...', 'Mam...', 'Zarządzałem...', 'Tworzę...'). Unikaj form bezosobowych "
        "('zarządzanie', 'tworzenie') oraz trzeciej osoby ('kandydat posiada').\n"
        "2. 'jobs_bullets': zwróć wpis dla KAŻDEGO identyfikatora stanowiska z listy STANOWISKA. "
        "Firmy, tytuły i daty są dodawane automatycznie, więc ich nie przepisuj, podajesz tylko punkty.\n"
        f"3. 'bullets': wybierz z bazy tylko punkty przydatne w TEJ ofercie i przeformułuj "
        f"je słownictwem z oferty (bez dodawania nowych faktów), najważniejsze na początku, "
        f"maksymalnie {MAX_PUNKTOW} na stanowisko. Punkty niezwiązane z ofertą POMIŃ. "
        "Stanowisko niezwiązane z ofertą ma 0-1 punkt.\n"
         f"4. 'profile': 2-3 zdania napisane POD TĘ OFERTĘ. NIE kopiuj i nie przepisuj opisu "
        f"profilu z bazy. Zdanie 1: kim JESTEM w kontekście stanowiska "
        f"'{stanowisko}' (nie obliczaj lat doświadczenia, jeśli nie ma ich wprost w bazie). "
        "Zdanie 2: dwie-trzy najważniejsze MOJE kompetencje z listy POTWIERDZONYCH WYMAGAŃ, "
        "każda poparta faktem z bazy. Zdanie 3: czym BĘDĘ użyteczny na tym "
        "stanowisku, konkretnie, bez ogólników typu 'otwarty i zaradny'. "
        "Nie opisuj MOICH cech charakteru (np. skrupulatny, komunikatywny, zorganizowany), chyba że "
        "wprost stoją w bazie w sekcji 'Umiejętności Miękkie'.\n"
        f"5. 'skills': użyj kategorii: {kat}. Do każdej kategorii bierz pozycje z odpowiadającej jej "
        "sekcji bazy (według znaczenia nazwy), krótkie (maksymalnie 4 słowa), oddzielone PRZECINKAMI. "
        "Wpisuj tylko umiejętności z bazy powiązane z ofertą. Kategorię bez treści w bazie albo "
        "niezwiązaną z ofertą pomiń. Nie powtarzaj tej samej umiejętności w kilku kategoriach."
        f"{dodatki_kat}\n"
        "6. NIE umieszczaj w 'skills' edukacji, certyfikatów ani projektów.\n"
        f"7. Język całej odpowiedzi: {cfg['nazwa']}.{plec}\n"
        "Odpowiedz wyłącznie obiektem JSON zgodnym ze schematem."
    )
    user = (
        f"BAZA DOŚWIADCZENIA KANDYDATA:\n{usun_sekcje(baza, SEKCJE_POMOCNICZE)}\n\n"
        f"OGŁOSZENIE O PRACĘ\nFirma: {firma}\nStanowisko: {stanowisko}\n"
        f"Treść oferty:\n{oferta}\n\n"
        f"POTWIERDZONE WYMAGANIA (kandydat to ma, podkreśl to):\n{potw}\n\n"
        f"BRAKI (kandydat tego nie ma, NIE wspominaj):\n{brak}\n\n"
        f"STANOWISKA (dla każdego zwróć punkty):\n{stan}\n"
    )
    return system, user


def slowa_wymagan(teksty):
    """{rdzeń: przykładowe słowo} dla znaczących słów z wymagań oferty."""
    wynik = {}
    for t in teksty:
        for r, w in znaczace_slowa(t):
            wynik.setdefault(r, w)
    return wynik


def slowa_dowodow(wpisy_mam):
    """Słowa z krótkich linii bazy, które potwierdziły wymagania (wymaganie -> baza -> wpis)."""
    wynik = {}
    for w in wpisy_mam:
        for linia in w.get("linia", "").split(" / "):
            if 0 < len(linia) <= 100:
                for r, sl in znaczace_slowa(linia.rsplit(": ", 1)[-1]):
                    wynik.setdefault(r, sl)
    return wynik


def wybierz_wpisy(wpisy, tekst_wpisu, slowa, max_n, slowa_dow=None):
    """Wybiera wpisy (projekty, szkolenia, kursy). Słowa z oferty liczą się za 1, słowa z linii bazy,
    które potwierdziły wymaganie, za 0,5 (np. oferta: 'metody wychowawcze' -> baza: 'Pedagogika Montessori')."""
    slowa_dow = {r: w for r, w in (slowa_dow or {}).items() if r not in slowa}
    ocenione = []
    for kolejnosc, w in enumerate(wpisy):
        rdz = {rdzen_slowa(x) for x in norm(tekst_wpisu(w)).split()}
        bezposrednie = [slowa[r] for r in slowa if pokrywa(r, rdz)]
        posrednie = [slowa_dow[r] for r in slowa_dow if pokrywa(r, rdz)]
        wynik = len(bezposrednie) + 0.5 * len(posrednie)
        if wynik >= 1:
            ocenione.append((-wynik, kolejnosc, w, bezposrednie + [f"({x})" for x in posrednie]))
    ocenione.sort(key=lambda x: (x[0], x[1]))
    return [(w, tr) for _, _, w, tr in ocenione[:max_n]]


def wybierz_do_cv(teksty, projekty, certyfikaty, kursy, wpisy_mam=(), edukacja=()):
    """Zwraca ({'projects': [...], 'certificates': [...], 'courses': [...]}, opisy do wypisania)."""
    slowa = slowa_wymagan(teksty)
    dowody = slowa_dowodow(wpisy_mam)
    grupy = (
        ("projects", "Projekty", projekty, lambda p: f"{p['name']} {p['description']} {p['technologies']}",
         MAX_PROJEKTY, lambda p: p["name"]),
        ("certificates", "Szkolenia z certyfikatem", certyfikaty,
         lambda c: c["text"] + " " + " ".join(c["details"]), MAX_CERTYFIKATY, lambda c: c["text"][:70]),
        ("courses", "Kursy", kursy,
         lambda k: k["text"] + " " + " ".join(k["details"]), MAX_KURSY, lambda k: k["text"][:70]),
    )
    wybor, opisy = {}, []
    for klucz, etykieta, wpisy, tekst, ile, nazwa in grupy:
        wybrane = wybierz_wpisy(wpisy, tekst, slowa, ile, dowody)
        wybor[klucz] = [w for w, _ in wybrane]
        opisy.append((etykieta, [(nazwa(w), tr) for w, tr in wybrane], bool(wpisy)))
    wybor["education_details"] = []
    for uczelnia in edukacja:
        punkty = [{"text": d, "details": []} for d in uczelnia.get("details", [])]
        wybrane = wybierz_wpisy(punkty, lambda x: x["text"], slowa, MAX_PUNKTY_STUDIA, dowody)
        wybor["education_details"].append([w["text"] for w, _ in wybrane])
        if punkty:
            opisy.append((f"Studia ({uczelnia['school'][:28]})", [(w["text"], tr) for w, tr in wybrane], True))
    return wybor, opisy


STOP_RAPORTU = {"się", "ich", "jest", "oraz", "dla", "przy", "jak", "nie", "aby", "tego", "tych", "były", "będą"}


def tekst_cv_calosc(cv):
    """Cały tekst CV, który zobaczy rekruter albo system rekrutacyjny (ATS)."""
    czesci = [cv["profile"]]
    for j in cv["jobs"]:
        czesci += j["bullets"] + [j["title"]]
    czesci += [k["value"] for k in cv["skills"]]
    for u in cv["education"]:
        czesci += [u["field"], u["school"]] + u.get("details", [])
    for c in cv["certificates"] + cv["courses"]:
        czesci.append(c["text"] if isinstance(c, dict) else c)
    for p in cv["projects"]:
        czesci += [p["name"], p["description"], p["technologies"]]
    return " ".join(czesci)


def raport_slow_kluczowych(cv, baza, teksty):
    """Które słowa z oferty są w CV, które masz w bazie, ale nie weszły do CV, a których nie masz."""
    rdz_cv = {rdzen_slowa(w) for w in norm(tekst_cv_calosc(cv)).split()}
    rdz_baza = set().union(*(r for _, r in linie_bazy(baza))) if baza else set()
    w_cv, w_bazie, brak = [], [], []
    for r, slowo in slowa_wymagan(teksty).items():
        if slowo.lower() in STOP_RAPORTU or (len(slowo) < 4 and not slowo.isupper()):
            continue
        (w_cv if pokrywa(r, rdz_cv) else w_bazie if pokrywa(r, rdz_baza) else brak).append(slowo)
    linie = ["", "🔑 SŁOWA KLUCZOWE Z OFERTY",
             f"   ✅ w CV ({len(w_cv)}): {', '.join(w_cv) or '-'}"]
    if w_bazie:
        linie.append(f"   🟠 masz w bazie, ale nie ma ich w CV ({len(w_bazie)}): {', '.join(w_bazie)}")
        linie.append("      → jeśli któreś jest ważne w ofercie, dopisz je do bazy w punktach lub umiejętnościach")
        linie.append("        (np. pod studiami albo w narzędziach), a skrypt je dobierze")
    linie.append(f"   ⚪ nie ma ich w Twojej bazie ({len(brak)}): {', '.join(brak) or '-'}")
    tekst = "\n".join(linie)
    print(tekst)
    return tekst


def rozdziel_po_przecinkach(tekst):
    """Dzieli listę po przecinkach, ale nie wewnątrz nawiasów."""
    return [x.strip() for x in re.split(r",\s*(?![^()]*\))", tekst) if x.strip()]


def popraw_umiejetnosci(skills, oferta):
    """Usuwa powtórzenia między kategoriami oraz kategorie, których oferta nie dotyczy."""
    n_oferty = norm(oferta)
    uprawnienia_ok = "prawo jazdy" in n_oferty or "prawa jazdy" in n_oferty or "uprawnien" in n_oferty
    widziane, wynik = set(), []
    for kat in skills:
        etykieta = kat["label"].strip().lower()
        if etykieta in ("uprawnienia", "licenses") and not uprawnienia_ok:
            continue
        if etykieta in ("zainteresowania", "interests") and not ZAINTERESOWANIA:
            continue
        pozycje = []
        for poz in rozdziel_po_przecinkach(kat["value"]):
            klucz = norm(re.sub(r"\([^)]*\)", "", poz))     # "Python (Pandas)" i "Python" to to samo
            if klucz and klucz not in widziane:
                widziane.add(klucz)
                pozycje.append(poz)
        if pozycje:
            wynik.append({"label": kat["label"], "value": ", ".join(pozycje)})
    return wynik


def edukacja_do_cv(edukacja, wybrane):
    """Uczelnie z bazy; pole 'details' tylko z punktami wybranymi pod ofertę."""
    wynik = []
    for i, uczelnia in enumerate(edukacja):
        wpis = {k: v for k, v in uczelnia.items() if k != "details"}
        punkty = wybrane[i] if i < len(wybrane) else []
        if punkty:
            wpis["details"] = punkty
        wynik.append(wpis)
    return wynik


def zloz_cv(wynik, stanowiska, wybor, edukacja, oferta):
    """Składa CV: stanowiska i wybrane wpisy pochodzą z bazy, model dostarcza tylko teksty."""
    punkty = {x.get("id"): [b for b in x.get("bullets", []) if isinstance(b, str) and b.strip()]
              for x in wynik.get("jobs_bullets", [])}
    jobs = [
        {"title": j["title"], "company": j["company"], "dates": j["dates"],
         "location": j["location"], "bullets": punkty.get(j["id"], [])[:MAX_PUNKTOW]}
        for j in stanowiska
    ]
    return {
        "profile": wynik["profile"],
        "jobs": jobs,
        "skills": popraw_umiejetnosci(wynik["skills"], oferta),
        "projects": [{k: v for k, v in p.items() if k != "id"} for p in wybor["projects"]],
        "education": edukacja_do_cv(edukacja, wybor.get("education_details", [])),
        "certificates": [wpis_do_cv(c) for c in wybor["certificates"]],
        "courses": [wpis_do_cv(k) for k in wybor["courses"]],
    }


# ------------------------------------------------------------
# Kontrola zgodności wyniku z bazą
# ------------------------------------------------------------
def jest_w_bazie(liczba, baza):
    return re.search(rf"(?<!\d){re.escape(liczba)}(?!\d)", baza) is not None


def sprawdz_z_baza(cv, baza, jezyk, braki=()):
    """Szuka rzeczy, które model mógł dopisać od siebie."""
    uwagi = []
    baza_low = baza.lower()
    for job in cv["jobs"]:
        if job["company"].lower() not in baza_low:
            uwagi.append(f"Firma nie występuje w bazie: {job['company']}")
        for rok in re.findall(r"\d{4}", job["dates"]):
            if not jest_w_bazie(rok, baza):
                uwagi.append(f"Rok {rok} ({job['company']}) nie występuje w bazie")
        for b in job["bullets"]:
            for liczba in re.findall(r"\d+", b):
                if not jest_w_bazie(liczba, baza):
                    uwagi.append(f"Liczba {liczba} nie występuje w bazie: \"{b[:70]}\"")
    # Porównanie słów ma sens tylko, gdy CV jest w tym samym języku co baza.
    # Każda pozycja jest dzielona na słowa i sprawdzana osobno (porównanie rdzeni słów).
    if jezyk == BAZA_JEZYK:
        rdzenie = {rdzen_slowa(s) for s in norm(baza).split()}
        for kat in cv["skills"]:
            for poz in re.split(r"[,;.]\s*", kat["value"]):
                poz = poz.strip()
                if not poz:
                    continue
                nieznane = [w for w in norm(poz).split()
                            if len(w) > 2 and w not in SLOWA_POMIJANE and rdzen_slowa(w) not in rdzenie]
                if nieznane:
                    uwagi.append(
                        f"Umiejętność niepotwierdzona w bazie ({kat['label']}): {poz} "
                        f"(nieznane słowa: {', '.join(nieznane)})"
                    )
        m = re.search(r"\*\*Profil zawodowy:\*\*\s*(.+)", baza)
        if m and len(norm(m.group(1))) > 40 and norm(m.group(1)) in norm(cv["profile"]):
            uwagi.append("Profil zawiera dosłownie opis z bazy (zamiast tekstu pisanego pod ofertę)")
        tekst_cv = " ".join([cv["profile"]] + [b for j in cv["jobs"] for b in j["bullets"]]
                            + [k["value"] for k in cv["skills"]])
        rdz_cv = {rdzen_slowa(w) for w in norm(tekst_cv).split()}
        for b in braki:
            for czlon in b.get("czlony", []):
                rdz = rdzenie_slow(czlon)
                trafione = [r for r in rdz if r in rdz_cv]
                if len(rdz) >= 2 and len(trafione) >= 2 and len(trafione) / len(rdz) >= 0.6:
                    uwagi.append(f"CV wspomina o wymaganiu, którego nie ma w bazie: „{czlon}”")
    return uwagi


def folder_pdf():
    return FOLDER_PDF / slug(OSOBA) if OSOBA else FOLDER_PDF


def zbuduj_pdf(jezyk, json_path):
    """Buduje PDF generatorem generuj_pdf.py i zapisuje kopię w folderze logu."""
    try:
        import generuj_pdf
    except ImportError as e:
        print(f"❌ Nie mogę wczytać generuj_pdf.py ({e}). Czy jest w tym samym folderze i czy "
              "zainstalowano reportlab (pip install reportlab)?")
        return
    try:
        sciezka = generuj_pdf.process_file(str(json_path), str(folder_pdf()), jezyk)
        shutil.copy(sciezka, KATALOG_LOGU / Path(sciezka).name)
    except Exception as e:
        print(f"❌ Generator PDF zakończył się błędem: {e}")


def wczytaj_oferte(sciezka):
    p = Path(sciezka)
    if not p.is_absolute():
        p = KATALOG / p
    if not p.exists():
        sys.exit(f"❌ Nie ma pliku z ofertą: {p}\n   Wklej treść ogłoszenia do tego pliku i zapisz.")
    try:
        tekst = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        tekst = p.read_text(encoding="cp1250")
    if len(tekst.strip()) < 200:
        sys.exit(f"❌ Oferta w {p.name} ma tylko {len(tekst.strip())} znaków. "
                 "Wygląda na pustą lub niepełną.")
    return p, tekst


def wczytaj_baze():
    baza = BAZA_MD.read_text(encoding="utf-8")
    return (baza, parse_stanowiska(baza), parse_projekty(baza), parse_certyfikaty(baza),
            parse_kursy(baza), parse_edukacja(baza))


def uruchom(args):
    sciezka_oferty, oferta = wczytaj_oferte(args.oferta)
    shutil.copy(sciezka_oferty, KATALOG_LOGU / "oferta.txt")
    podglad = " ".join(oferta.split())[:110]
    print(f"📄 Oferta: {sciezka_oferty.name}, {len(oferta)} znaków, początek: \"{podglad}...\"")

    _baza, _stan, *_ = wczytaj_baze()
    sprawdz_baze(_baza, DANE_OSOBOWE, _stan)
    # --- zdjęcie (opcjonalne) ---
    zdjecie_path = None
    if not args.bez_zdjecia:
        if args.zdjecie:
            p = Path(args.zdjecie)
            if not p.is_absolute():
                p = KATALOG / p
            if p.exists():
                zdjecie_path = p
            else:
                print(f"⚠️  Nie znaleziono zdjęcia: {p} — generuję CV bez zdjęcia.")
        elif OSOBA:
            zdjecie_path = znajdz_zdjecie(BAZA_MD.parent, OSOBA)
        if zdjecie_path:
            print(f"📷 Zdjęcie do CV: {zdjecie_path}")
        elif OSOBA:
            print(f"ℹ️  Brak zdjęcia obok bazy (szukam '{OSOBA} - zdjęcie.jpg') — CV bez zdjęcia.")
    # --- KROK 1: analiza oferty (z opcją wczytania z pliku) ---
    if args.wczytaj_analize:
        analiza = json.loads(Path(args.wczytaj_analize).read_text(encoding="utf-8"))
        print(f"📂 Wczytano analizę z pliku: {args.wczytaj_analize}")
        args.bez_pytan = True  # Automatycznie pomijamy pytanie o uzupełnienie bazy
    else:
        print("\n⏳ Krok 1/2: wyciągam wymagania z oferty...")
        sys_a, usr_a = prompty_analizy(oferta, args.firma, args.stanowisko)
        analiza = zapytaj_ollame(sys_a, usr_a, schemat_wymagan(), TEMP_ANALIZA, "analiza", wymuszony_ctx=args.ctx)
        zapisz_w_logu("wymagania_z_oferty.json", json.dumps(analiza, ensure_ascii=False, indent=2))

    # --- porównanie z bazą + ewentualny zapis analizy (faza 1) ---
    while True:
        baza, stanowiska, projekty, certyfikaty, kursy, edukacja = wczytaj_baze()
        print(f"\n🗂  Baza: {len(stanowiska)} stanowisk, {len(projekty)} projektów, "
              f"{len(certyfikaty)} szkoleń, {len(kursy)} kursów, {len(edukacja)} uczelnie")
        wymagania, wymogi = rozdziel_wymogi(analiza)
        mam, prawdopodobne, cechy, braki = podziel_wymagania(wymagania, baza)
        formalne = sprawdz_formalne(wymogi, baza)
        zapisz_w_logu("wymagania.json", json.dumps(
            {"znalezione": mam, "prawdopodobne": prawdopodobne,
             "cechy": cechy, "braki": braki, "formalne": formalne},
            ensure_ascii=False, indent=2))
        raport_wymagan = wypisz_raport_wymagan(mam, prawdopodobne, cechy, braki, formalne)
        zapisz_w_logu("raport.txt", raport_wymagan)

        if args.zapisz_analize:
            Path(args.zapisz_analize).write_text(
                json.dumps(analiza, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"💾 Analiza zapisana do: {args.zapisz_analize}")
            return

        problemy = braki + [f for f in formalne if f["status"] in ("brak_kat", "brak_info")]
        decyzja = "k" if args.bez_pytan else zapytaj_o_braki(problemy)
        if decyzja == "p":
            print("\n⏹  Przerwano. Uzupełnij bazę i uruchom skrypt ponownie.")
            zapisz_w_logu("raport.txt", raport_wymagan + "\n\nPrzerwano na prośbę użytkownika.")
            return
        if decyzja == "u":
            otworz_baze()
            continue
        break

    # --- KROK 2: generowanie CV (znalezione i prawdopodobne wymagania są dozwolone) ---
    wybor, opisy = wybierz_do_cv(wymagania + [args.stanowisko], projekty, certyfikaty, kursy, mam, edukacja)
    print("\n📎 Dobór projektów, szkoleń i kursów (kod, według słów z oferty):")
    for etykieta, lista, czy_sa in opisy:
        if not czy_sa:
            continue
        if not lista:
            print(f"   {etykieta}: żadne nie pasują do oferty")
        for nazwa_wpisu, trafione in lista:
            print(f"   {etykieta}: {nazwa_wpisu}  [pasuje: {', '.join(trafione[:6])}]")

    print("\n⏳ Krok 2/2: generuję CV pod tę ofertę...")
    sys_c, usr_c = zbuduj_prompty_cv(
        baza, oferta, args.firma, args.stanowisko, args.jezyk,
        mam + prawdopodobne,
        braki + [c for c in cechy if not c["linia"]]
        + [f for f in formalne if f["status"] in ("brak_kat", "brak_info")],
        stanowiska)
    wynik = zapytaj_ollame(sys_c, usr_c, schemat_cv(stanowiska, projekty, certyfikaty, kursy), TEMP_CV, "cv", wymuszony_ctx=args.ctx)

    cv_data = {
        "meta": {
            "firma": args.firma,
            "tytul": args.stanowisko,
            "creation_date": datetime.now().strftime("%Y-%m-%d"),
            "jezyk": args.jezyk,
            "model": MODEL,
            **({"kandydat": OSOBA, "osoba": DANE_OSOBOWE} if OSOBA else {}),
            **({"naglowki": naglowki_pdf(args.jezyk)} if naglowki_pdf(args.jezyk) else {}),
            **({"zdjecie": str(zdjecie_path)} if zdjecie_path else {}),
        },
        "cv": zloz_cv(wynik, stanowiska, wybor, edukacja, oferta),
    }
    brak_tlumaczen = []
    cv_data["cv"] = przetlumacz_dane(cv_data["cv"], args.jezyk, brak_tlumaczen)

    tekst_json = json.dumps(cv_data, ensure_ascii=False, indent=2)
    if args.wyjscie:
        json_path = KATALOG / args.wyjscie
    elif OSOBA:
        json_path = FOLDER_WYNIKOW / slug(OSOBA) / f"cv_{args.jezyk}.json"
    else:
        json_path = KATALOG / JEZYKI[args.jezyk]["json"]
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(tekst_json, encoding="utf-8")
    zapisz_w_logu("cv.json", tekst_json)
    print(f"\n✅ Zapisano: {json_path}")

    # Kopia w folderze głównym skryptu — do ręcznej edycji i bezpośredniego generuj_pdf.py
    kopia_root = KATALOG / JEZYKI[args.jezyk]["json"]  # cv_danePL.json / cv_dane.json
    if kopia_root.resolve() != json_path.resolve():
        kopia_root.write_text(tekst_json, encoding="utf-8")
        print(f"   📝 Kopia do ręcznej edycji: {kopia_root}")
        print(f"      → Edytuj ją i uruchom: python generuj_pdf.py {kopia_root.name} --output pdf")
    #     uruchomienie: python generuj_pdf.py cv_danePL.json --output pdf

    print(f"   Stanowiska: {len(cv_data['cv']['jobs'])} (wszystkie z bazy, z punktami od modelu)")
    print(f"   Wybrane projekty: {[p['name'] for p in cv_data['cv']['projects']] or 'żadne'}")
    print(f"   Wybrane szkolenia z certyfikatem: {len(cv_data['cv']['certificates'])}")
    print(f"   Wybrane kursy: {[k[:45] for k in cv_data['cv']['courses']] or 'żadne'}")

    kontrola = braki + [{"czlony": p["brak_czlony"]} for p in prawdopodobne if p.get("brak_czlony")]
    uwagi = sprawdz_z_baza(cv_data["cv"], baza, args.jezyk, kontrola)
    for b in brak_tlumaczen:
        uwagi.append(f"Brak tłumaczenia w {PLIK_TLUMACZEN.name}, zostało po polsku: {b}")
    raport_uwag = ""
    if uwagi:
        raport_uwag = "⚠️  Sprawdź przed wysłaniem CV (może to wymysł modelu):\n" + \
                      "\n".join(f"   - {u}" for u in uwagi)
        print("\n" + raport_uwag)
    else:
        print("\n✅ Automatyczna kontrola zgodności z bazą: bez uwag.")
    raport_slow = raport_slow_kluczowych(cv_data["cv"], baza, wymagania + [args.stanowisko])
    print("👀 Mimo to przeczytaj JSON przed wysłaniem CV.")
    zapisz_w_logu("raport.txt", raport_wymagan + "\n\n" + (raport_uwag or "Kontrola zgodności: bez uwag.")
                  + "\n" + raport_slow)

    if args.pdf:
        zbuduj_pdf(args.jezyk, json_path)


def main():
    global EMBEDDINGI, KATALOG_LOGU, MODEL, NUM_CTX, BAZA_MD, OSOBA, DANE_OSOBOWE, PLEC_KANDYDATA, PLIK_TLUMACZEN, USTAWIENIA, ZAINTERESOWANIA
    ap = argparse.ArgumentParser(description="Generator JSON do CV (Bielik + Ollama)")
    ap.add_argument("--firma", required=True, help="firma, do której aplikujesz")
    ap.add_argument("--stanowisko", required=True, help="nazwa stanowiska z oferty")
    ap.add_argument("--oferta", default="oferta.txt", help="plik z treścią ogłoszenia")
    ap.add_argument("--jezyk", choices=JEZYKI.keys(), default="pl")
    ap.add_argument("--wyjscie", help="nazwa pliku JSON (domyślnie z konfiguracji)")
    ap.add_argument("--zainteresowania", action="store_true",
                    help="dodaj do CV kategorię zainteresowań (domyślnie pomijana)")
    ap.add_argument("--zdjecie", metavar="PLIK",
                    help="ścieżka do zdjęcia (domyślnie szukam '<Imię> - zdjęcie.jpg' obok bazy)")
    ap.add_argument("--bez-zdjecia", action="store_true",
                    help="nie dodawaj zdjęcia do CV")
    ap.add_argument("--osoba", help="dla kogo robimy CV (imię z nazwy pliku bazy); bez tego pokażę menu")
    ap.add_argument("--pdf", action="store_true", help="po wygenerowaniu JSON-a zbuduj PDF")
    ap.add_argument("--model", help="nazwa modelu w Ollamie (domyślnie z konfiguracji), do porównań")
    ap.add_argument("--ctx", type=int, help="długość kontekstu (domyślnie z konfiguracji)")
    ap.add_argument("--zapisz-analize", metavar="PLIK",
                    help="zapisz analizę oferty do pliku i zakończ po raporcie (faza 1)")
    ap.add_argument("--wczytaj-analize", metavar="PLIK",
                    help="użyj zapisanej analizy, pomiń Krok 1 (faza 2)")
    ap.add_argument("--embeddingi", action="store_true", help="dopasowanie po znaczeniu (wymaga: ollama pull bge-m3)")
    ap.add_argument("--bez-embeddingow", action="store_true", help="wyłącz embeddingi nawet jeśli włączone w .env")
    ap.add_argument("--bez-pytan", action="store_true",
                    help="nie zatrzymuj się po analizie oferty, nawet gdy są braki")
    ap.add_argument("--folder-log", help="ręcznie wskaż folder na logi (opcjonalnie)")
    args = ap.parse_args()
    ZAINTERESOWANIA = args.zainteresowania
    EMBEDDINGI = (EMBEDDINGI or args.embeddingi) and not args.bez_embeddingow
    if args.model:
        MODEL = args.model
    if args.ctx:
        NUM_CTX = args.ctx

    # --- wybór osoby, jej baza, dane kontaktowe, płeć i tłumaczenia ---
    OSOBA, BAZA_MD = wybierz_osobe(args.osoba)
    USTAWIENIA = parse_ustawienia(BAZA_MD.read_text(encoding="utf-8"))
    zastosuj_ustawienia(USTAWIENIA)
    if OSOBA:
        DANE_OSOBOWE = parse_dane_osobowe(BAZA_MD.read_text(encoding="utf-8"))
        if DANE_OSOBOWE.get("gender"):
            PLEC_KANDYDATA = "k" if DANE_OSOBOWE["gender"].strip().lower().startswith("k") else "m"
        wlasne = BAZA_MD.parent / f"{OSOBA} - tlumaczenia EN.json"
        if wlasne.exists():
            PLIK_TLUMACZEN = wlasne

    # --- folder logu: deterministyczny, żeby retry nadpisywał pliki ---
    if args.folder_log:
        KATALOG_LOGU = Path(args.folder_log).resolve()
    else:
        czesc_osoby = slug(OSOBA)[:16] if OSOBA else "wspolne"
        KATALOG_LOGU = FOLDER_LOGOW / czesc_osoby / f"{slug(args.firma)}_{slug(args.stanowisko)}"
    KATALOG_LOGU.mkdir(parents=True, exist_ok=True)

    # log per faza (faza1 = analiza, faza2 = generacja CV) - nie nadpisują się nawzajem
    sufiks = "faza2" if args.wczytaj_analize else "faza1"
    plik_logu = open(KATALOG_LOGU / f"log_{sufiks}.txt", "w", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, plik_logu)
    print(f"📁 Log tego uruchomienia: {KATALOG_LOGU}")
    print(f"🤖 Model: {MODEL}, kontekst: {NUM_CTX}")
    print(f"👤 Osoba: {OSOBA or '(tryb jednoosobowy)'}, baza: {BAZA_MD.name}")
    if args.jezyk == "en" and USTAWIENIA.get("kategorie_pl") and not USTAWIENIA.get("kategorie_en"):
        print("ℹ️  Baza ma własne kategorie umiejętności po polsku, ale nie ma wersji (EN): "
              "w angielskim CV użyję domyślnych kategorii.")
    try:
        uruchom(args)
    finally:
        sys.stdout = sys.__stdout__
        plik_logu.close()


if __name__ == "__main__":
    main()