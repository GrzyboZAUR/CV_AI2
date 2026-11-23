# AI CV – generator CV pod ofertę pracy (lokalny LLM)

Generator, który z **Twojej bazy zatrudnienia** (plik Markdown) i **treści ogłoszenia** tworzy CV dopasowane
do tej konkretnej oferty: jedna strona A4, PDF. Model językowy działa **lokalnie w Ollamie** (domyślnie
[Bielik 11B](https://huggingface.co/speakleash)), więc Twoje dane nie trafiają do żadnego chmurowego LLM.
Obsługa z terminala albo przez bota Telegram (z telefonu).

> ⚠️ Projekt hobbystyczny, bez gwarancji. Model może się pomylić. **Zawsze przeczytaj gotowe CV przed wysłaniem.**
> Interfejs, prompty i baza są po polsku (CV można wygenerować także po angielsku).

## Jak to działa

1. **Analiza oferty** – model wypisuje wymagania z ogłoszenia (osobno wymogi formalne, np. prawo jazdy).
2. **Porównanie z bazą** – robi to *kod*, nie model: każde wymaganie dostaje status
   (znalezione / częściowo / brak / cecha osobista) razem z dowodem, czyli linią z Twojej bazy.
   Dzięki temu widzisz, czego Ci brakuje, i model nie dopisuje umiejętności, których nie masz.
3. **Dobór treści** – kod wybiera projekty, certyfikaty, kursy i punkty przy studiach według słów z oferty.
4. **Generowanie CV** – model pisze profil, punkty przy stanowiskach i umiejętności, korzystając wyłącznie z faktów z bazy.
5. **Kontrola** – skrypt ostrzega o liczbach, firmach i umiejętnościach, których nie ma w bazie, oraz raportuje
   pokrycie słów kluczowych z oferty.
6. **PDF** – 1 strona A4 (czcionka zmniejsza się automatycznie, do 85%).

## Wymagania

| Element | Wersja / uwagi |
|---|---|
| Python | 3.10 lub nowszy |
| [Ollama](https://ollama.com) | uruchomiona lokalnie albo na innym komputerze w sieci |
| Model | Bielik 11B Instruct v3, kwantyzacja Q4_K_M (plik ok. 7 GB) |
| Sprzęt | karta z ok. 8–12 GB VRAM albo 16 GB+ RAM (na procesorze działa, ale wolno – to normalne, generowanie trwa minuty) |
| Czcionka | Carlito (w repo, folder `fonts/`) – potrzebna do polskich znaków w PDF |
| Telegram (opcjonalnie) | token bota z [@BotFather](https://t.me/BotFather) i Twoje ID |
| Embeddingi (opcjonalnie) | `ollama pull bge-m3` (ok. 1,2 GB), włączane flagą `--embeddingi` |

## Instalacja

```bash
git clone https://github.com/<twoj-login>/<nazwa-repo>.git
cd <nazwa-repo>
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Model w Ollamie:

```bash
ollama pull huggingface.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M
ollama cp   huggingface.co/speakleash/Bielik-11B-v3.0-Instruct-GGUF:Q4_K_M bielik-11b
ollama run bielik-11b "Cześć"      # rozgrzewka, potem /bye
```

Jeśli Ollama stoi na innym komputerze, ustaw w `.env` `OLLAMA_URL=http://ADRES:11434/api/chat`.
Nie wystawiaj Ollamy do internetu bez uwierzytelniania (do dostępu z zewnątrz użyj np. VPN / Tailscale).

## Przygotowanie bazy zatrudnienia

1. Skopiuj `examples/szablon - baza zatrudnienia.md` do folderu `bazy/`.
2. Zmień nazwę na `<Imię> - baza zatrudnienia.md` (np. `Anna - baza zatrudnienia.md`).
3. Uzupełnij wszystkie pola `UZUPEŁNIJ` (skrypt odmówi pracy, jeśli któreś zostanie).
4. Opcjonalnie dodaj zdjęcie `bazy/<Imię> - zdjęcie.jpg`.

Zasada: **baza ma zawierać tylko prawdziwe fakty o Tobie.** Im więcej konkretnych punktów
(narzędzia, metody, przedmioty na studiach), tym lepiej dopasowanie do słów kluczowych z ofert.
Pełny przykład: `examples/Jan Kowalski - baza zatrudnienia.md`.

## Użycie z terminala

```bash
# treść ogłoszenia wklej do pliku oferta.txt
python generuj_json_z_oferty.py --osoba Anna --firma "Nazwa firmy" --stanowisko "Analityk danych" --pdf
```

Przydatne opcje: `--jezyk en`, `--model nazwa`, `--oferta plik.txt`, `--zdjecie plik.jpg`, `--bez-zdjecia`,
`--zainteresowania`, `--embeddingi`, `--bez-pytan`. Pełna lista: `--help`.

Wyniki: `pdf/<Imię>/` (PDF), `wyniki/<Imię>/` (JSON), `logi/<Imię>/<firma_stanowisko>/` (raport braków, słów kluczowych, logi).
Raport (`raport.txt`) pokazuje, których wymagań z oferty nie ma w bazie, a które słowa kluczowe weszły do CV.

## Bot Telegram - w trakcie budowy

1. Utwórz bota w @BotFather i skopiuj token. Swoje ID sprawdzisz np. w @userinfobot.
2. W `.env` ustaw `TELEGRAM_TOKEN` i `TELEGRAM_ALLOWED_USER_ID`.
   Kolejne osoby: `TELEGRAM_USERS=222:Anna,333:Marek` (każda widzi tylko swoją bazę).
3. Uruchom `python bot.py`, w Telegramie wpisz `/cv`, podaj firmę, stanowisko i wklej ofertę.
   Bot najpierw pokaże analizę, a po Twoim potwierdzeniu wygeneruje i odeśle PDF.

Uwaga o prywatności: model liczy lokalnie, ale treść rozmowy z botem (oferta, gotowy PDF) przechodzi
przez serwery Telegrama. Bot odpowiada tylko użytkownikom z listy ID.

## Konfiguracja (`.env`)

Zobacz `.env.example`. Najważniejsze: `OLLAMA_URL`, `MODEL`, `TELEGRAM_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`,
`TELEGRAM_USERS`, `CV_DEFAULT_JEZYK`, `CV_TIMEOUT_S`, `EMB_WLACZONE`.

## Struktura repozytorium

```
generuj_json_z_oferty.py   główny skrypt (analiza, dopasowanie, generowanie JSON)
generuj_pdf.py             JSON -> PDF (1 strona A4)
bot.py                     bot Telegram
fonts/                     czcionka Carlito (licencja OFL)
examples/                  szablon bazy, przykładowa baza i oferta
bazy/  logi/  pdf/  wyniki/   tworzone lokalnie, w .gitignore (Twoje dane)
```

## Ograniczenia

- Dopasowanie wymagań działa po słowach i rdzeniach (plus opcjonalnie embeddingi), więc zdarzają się fałszywe braki
  przy innych sformułowaniach. Synonimy możesz dopisać w sekcji `# Słowa kluczowe` swojej bazy.
- Klauzula zgody RODO w stopce PDF jest wpisana na stałe w `generuj_pdf.py`; dostosuj ją do siebie.
- Baza i prompty są po polsku; Bielik jest modelem polskim. Inne języki bazy nie są wspierane.

## Licencja

Kod:  Apache 2.0. (plik `LICENSE`). Czcionka Carlito: SIL OFL. Model Bielik ma własną licencję, sprawdź ją na stronie SpeakLeash.
