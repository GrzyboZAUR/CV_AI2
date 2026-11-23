#!/usr/bin/env python3
"""
Generator PDF z CV (polski i angielski w jednym pliku).

W tym pliku są TYLKO dane stałe: imię, nazwisko, kontakt, nagłówki sekcji i klauzula zgody.
Wszystko, co zależy od oferty (profil, stanowiska, umiejętności, projekty, edukacja,
certyfikaty), jest czytane z pliku JSON.

Użycie:
    python generuj_pdf.py cv_danePL.json
    python generuj_pdf.py cv_dane.json --jezyk en --output pdf
    python generuj_pdf.py logi/*/cv.json

Język bierze z meta.jezyk w JSON-ie, a gdy go nie ma, z opcji --jezyk (domyślnie pl).
Jeśli CV nie mieści się na jednej stronie, skrypt zmniejsza czcionkę (do 85%),
a gdy to nie pomoże, wypisuje ostrzeżenie.

Wymagania:
    pip install reportlab
    Czcionka Carlito (.ttf) w ~/.fonts, w folderze skryptu lub w folderze czcionek systemu.
"""

import argparse
import glob
import io
import json
import os
import re
import sys
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ============================================================
# DANE STAŁE - uzupełnij swoimi prawdziwymi danymi
# ============================================================
IMIE_NAZWISKO = "Jan Kowalski"
EMAIL = "jan.kowalski@example.com"
TELEFON = "(+48) 111 111 111"
LINKEDIN = "https://www.linkedin.com/in//"
LOKALIZACJA = {"pl": "", "en": ""}   # pusty tekst = nie pokazuj

ETYKIETY = {
    "pl": {
        "profile": "Profil",
        "experience": "Doświadczenie zawodowe",
        "skills": "Umiejętności",
        "certs": "Certyfikaty",
        "courses": "Kursy",
        "education": "Edukacja",
        "projects": "Projekty",
        "footer": (
            "Wyrażam zgodę na przetwarzanie moich danych osobowych dla potrzeb rekrutacji "
            "(zgodnie z art. 6 ust. 1 lit. a Rozporządzenia Parlamentu Europejskiego i Rady (UE) "
            "2016/679 z dnia 27 kwietnia 2016 r. w sprawie ochrony osób fizycznych w związku "
            "z przetwarzaniem danych osobowych i w sprawie swobodnego przepływu takich danych "
            "oraz uchylenia dyrektywy 95/46/WE (ogólne rozporządzenie o ochronie danych)). "
            "Wyrażam zgodę na przetwarzanie moich danych osobowych w zakresie przyszłych "
            "procesów rekrutacyjnych."
        ),
    },
    "en": {
        "profile": "Profile",
        "experience": "Experience",
        "skills": "Skills",
        "certs": "Certifications",
        "courses": "Courses",
        "education": "Education",
        "projects": "Projects",
        "footer": (
            "I agree to the processing of personal data provided in this document for recruitment "
            "purposes pursuant to the Personal Data Protection Act of 10 May 2018 and GDPR "
            "(EU) 2016/679."
        ),
    },
}

# Folder wyjściowy (None = obok pliku JSON)
DEFAULT_OUTPUT_DIR = None

# Foldery, w których szukamy czcionki Carlito
FONT_DIR = os.path.expanduser("~/.fonts")
SKALE = [1.0, 0.95, 0.9, 0.85]     # kolejne próby zmniejszenia czcionki, gdy CV nie mieści się na stronie

# ============================================================
# Czcionki
# ============================================================
def setup_fonts():
    """Rejestruje Carlito. Gdy go nie ma, używa Helvetica (bez polskich znaków)."""
    pliki = {
        "Carlito": "Carlito-Regular.ttf",
        "Carlito-Bold": "Carlito-Bold.ttf",
        "Carlito-Italic": "Carlito-Italic.ttf",
        "Carlito-BoldItalic": "Carlito-BoldItalic.ttf",
    }
    szukaj = [
        FONT_DIR,
        os.path.dirname(os.path.abspath(__file__)),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts"),
        r"C:\Windows\Fonts",
        os.path.expanduser("~/Library/Fonts"),
        "/Library/Fonts",
        "/usr/share/fonts/truetype/crosextra",
        "fonts/"
    ]
    folder = next((s for s in szukaj if os.path.exists(os.path.join(s, "Carlito-Regular.ttf"))), None)
    if folder:
        for nazwa, plik in pliki.items():
            if nazwa not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(nazwa, os.path.join(folder, plik)))
        registerFontFamily("Carlito", normal="Carlito", bold="Carlito-Bold",
                           italic="Carlito-Italic", boldItalic="Carlito-BoldItalic")
        return "Carlito", "Carlito-Bold", "Carlito-Italic"
    print("⚠️  Nie znaleziono czcionki Carlito, używam Helvetica (BEZ polskich znaków).")
    print("   Pobierz: https://fonts.google.com/specimen/Carlito i wrzuć pliki .ttf do folderu skryptu.")
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


# ============================================================
# Style i elementy układu
# ============================================================
DARK = HexColor("#000000")
GRAY = HexColor("#555555")
LIGHT_LINE = HexColor("#999999")

BODY_SIZE, LEAD, NAME_SIZE, SECTION_SIZE, JOB_SIZE, CONTACT_SIZE = 8.5, 11, 16, 9.5, 9, 8


def e(tekst):
    """Zabezpiecza tekst z JSON-a przed znakami, które psują znaczniki (np. & < >)."""
    return escape(str(tekst))


def make_styles(font, font_b, font_i, s=1.0):
    body, lead, job = BODY_SIZE * s, LEAD * s, JOB_SIZE * s
    return {
        "name": ParagraphStyle("Name", fontSize=NAME_SIZE * s, leading=NAME_SIZE * s + 3, fontName=font_b,
                               textColor=DARK, alignment=TA_CENTER, spaceAfter=1 * mm),
        "contact": ParagraphStyle("Contact", fontSize=CONTACT_SIZE * s, leading=CONTACT_SIZE * s + 3,
                                  fontName=font, textColor=GRAY, alignment=TA_CENTER, spaceAfter=0),
        "section": ParagraphStyle("Section", fontSize=SECTION_SIZE * s, leading=SECTION_SIZE * s + 3,
                                  fontName=font_b, textColor=DARK, spaceBefore=4 * mm, spaceAfter=1 * mm),
        "job_title": ParagraphStyle("JobTitle", fontSize=job, leading=job + 3, fontName=font_b,
                                    textColor=DARK, spaceBefore=2.5 * mm, spaceAfter=0),
        "job_date": ParagraphStyle("JobDate", fontSize=job, leading=job + 3, fontName=font,
                                   textColor=GRAY, alignment=TA_RIGHT, spaceBefore=2.5 * mm),
        "job_context": ParagraphStyle("JobContext", fontSize=body, leading=lead, fontName=font_i,
                                      textColor=GRAY, spaceAfter=0.5 * mm),
        "bullet": ParagraphStyle("Bullet", fontSize=body, leading=lead, fontName=font, textColor=DARK,
                                 leftIndent=8 * mm, bulletIndent=3 * mm, spaceAfter=0.3 * mm),
        "profile": ParagraphStyle("Profile", fontSize=body, leading=lead, fontName=font,
                                  textColor=DARK, spaceAfter=0),
        "skill_label": ParagraphStyle("SkillLabel", fontSize=body, leading=lead, fontName=font_b,
                                      textColor=DARK),
        "skill_value": ParagraphStyle("SkillValue", fontSize=body, leading=lead, fontName=font,
                                      textColor=DARK),
        "edu_detail": ParagraphStyle("EduDetail", fontSize=body, leading=lead, fontName=font_i, textColor=GRAY,
                                     leftIndent=3 * mm, spaceAfter=0.3 * mm),
        "cert_detail": ParagraphStyle("CertDetail", fontSize=body, leading=lead, fontName=font, textColor=DARK,
                                      leftIndent=13 * mm, bulletIndent=8 * mm, spaceAfter=0.2 * mm),
        "cert": ParagraphStyle("Cert", fontSize=body, leading=lead, fontName=font, textColor=DARK,
                               leftIndent=8 * mm, bulletIndent=3 * mm, spaceAfter=0.3 * mm),
        "edu": ParagraphStyle("Edu", fontSize=body, leading=lead, fontName=font,
                              textColor=DARK, spaceAfter=0),
        "edu_date": ParagraphStyle("EduDate", fontSize=body, leading=lead, fontName=font,
                                   textColor=GRAY, alignment=TA_RIGHT),
        "footer": ParagraphStyle("Footer", fontSize=6.5 * s, leading=8 * s, fontName=font,
                                 textColor=GRAY, alignment=TA_CENTER, spaceBefore=3 * mm),
        "name_left": ParagraphStyle("NameLeft", fontSize=NAME_SIZE * s, leading=NAME_SIZE * s + 3,
                                    fontName=font_b, textColor=DARK, alignment=0, spaceAfter=1 * mm),
        "contact_left": ParagraphStyle("ContactLeft", fontSize=CONTACT_SIZE * s,
                                       leading=CONTACT_SIZE * s + 3, fontName=font, textColor=GRAY,
                                       alignment=0, spaceAfter=0),
    }


def section_header(tekst, styles):
    return [
        Spacer(1, 1 * mm),
        HRFlowable(width="100%", thickness=0.4, color=LIGHT_LINE, spaceAfter=0.5 * mm),
        Paragraph(e(tekst).upper(), styles["section"]),
    ]


def dwie_kolumny(lewa, prawa, usable):
    """Tabela: tekst po lewej, data po prawej (70% / 30%)."""
    t = Table([[lewa, prawa]], colWidths=[usable * 0.7, usable * 0.3])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def tekst_edukacji(wpis):
    tytul = wpis.get("field", "")
    if wpis.get("degree"):
        tytul = f"{tytul}, {wpis['degree']}" if tytul else wpis["degree"]
    return f"<b>{e(tytul)}</b> | {e(wpis.get('school', ''))}"


def tekst_projektu(p):
    nazwa = e(p.get("name", ""))
    link = p.get("link", "")
    if link:
        nazwa = f"<a href='{e(link)}' color='#0000FF'>{nazwa}</a>"
    tekst = f"<b>{nazwa}</b>"
    if p.get("description"):
        tekst += f" – {e(p['description'])}"
    if p.get("technologies"):
        tekst += f" | <i>{e(p['technologies'])}</i>"
    return tekst


# ============================================================
# Budowanie dokumentu
# ============================================================
def _photo_flowable(sciezka, max_w_mm=25, max_h_mm=30):
    """Zdjęcie z zachowaniem proporcji. Zwraca None, gdy nie da się go wczytać."""
    if not sciezka or not os.path.exists(sciezka):
        return None
    try:
        from PIL import Image as PILImage
        with PILImage.open(sciezka) as im:
            w, h = im.size
        ratio = w / h if h else 1
    except Exception:
        return RLImage(sciezka, width=max_w_mm * mm, height=max_h_mm * mm)
    target_h = max_h_mm * mm
    target_w = target_h * ratio
    if target_w > max_w_mm * mm:
        target_w = max_w_mm * mm
        target_h = target_w / ratio
    try:
        return RLImage(sciezka, width=target_w, height=target_h)
    except Exception as ex:
        print(f"⚠️  Nie mogę wstawić zdjęcia {sciezka}: {ex}")
        return None

def dane_naglowka(osoba, jezyk):
    """Imię i linie kontaktowe. W trybie wieloosobowym tylko dane tej osoby, bez danych domyślnych."""
    if osoba:
        lok = osoba.get("location_en" if jezyk == "en" else "location_pl", "")
        return osoba.get("name", ""), [osoba.get("email", ""), osoba.get("phone", ""), lok, osoba.get("linkedin", "")]
    return IMIE_NAZWISKO, [EMAIL, TELEFON, LOKALIZACJA.get(jezyk, ""), LINKEDIN]


def dodaj_wpisy(story, wpisy, st):
    """Certyfikaty i kursy: zwykły tekst albo {'text': ..., 'details': [...]} z punktami pod spodem."""
    for w in wpisy:
        if isinstance(w, dict):
            story.append(Paragraph(f"•  {e(w.get('text', ''))}", st["cert"]))
            for d in w.get("details", []):
                story.append(Paragraph(f"–  {e(d)}", st["cert_detail"]))
        else:
            story.append(Paragraph(f"•  {e(w)}", st["cert"]))


def zbuduj(dest, cv, jezyk, fonty, skala, osoba=None, naglowki=None, zdjecie=None):
    """Buduje PDF do dest (ścieżka lub bufor). Zwraca liczbę stron."""
    font, font_b, font_i = fonty
    et = {**ETYKIETY[jezyk], **(naglowki or {})}   # nagłówki mogą być nadpisane w bazie osoby
    st = make_styles(font, font_b, font_i, skala)
    usable = A4[0] - 28 * mm

    doc = SimpleDocTemplate(dest, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=10 * mm, bottomMargin=6 * mm)
    story = []

    # Nagłówek
    imie, kontakt = dane_naglowka(osoba, jezyk)
    kontakt_tekst = "  •  ".join(e(k) for k in kontakt if k)
    foto = _photo_flowable(zdjecie) if zdjecie else None
    if foto is not None:
        lewa_c = usable - foto.drawWidth - 4 * mm
        lewa = Table(
            [[Paragraph(e(imie), st["name_left"])],
             [Paragraph(kontakt_tekst, st["contact_left"])]],
            colWidths=[lewa_c],
        )
        lewa.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        naglowek = Table([[lewa, foto]], colWidths=[lewa_c, foto.drawWidth + 4 * mm])
        naglowek.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ]))
        story.append(naglowek)
    else:
        story.append(Paragraph(e(imie), st["name"]))
        story.append(Paragraph(kontakt_tekst, st["contact"]))

    # Profil
    story.extend(section_header(et["profile"], st))
    story.append(Paragraph(e(cv.get("profile", "")), st["profile"]))

    # Doświadczenie
    story.extend(section_header(et["experience"], st))
    for job in cv.get("jobs", []):
        lewa = Paragraph(f"<b>{e(job['title'])}</b> | {e(job['company'])}", st["job_title"])
        prawa = Paragraph(e(job["dates"]), st["job_date"])
        story.append(dwie_kolumny(lewa, prawa, usable))
        story.append(Paragraph(e(job.get("location", "")), st["job_context"]))
        for b in job.get("bullets", []):
            story.append(Paragraph(f"•  {e(b)}", st["bullet"]))

    # Umiejętności
    if cv.get("skills"):
        story.extend(section_header(et["skills"], st))
        for skill in cv["skills"]:
            wiersz = Table(
                [[Paragraph(f"<b>{e(skill['label'])}:</b>", st["skill_label"]),
                  Paragraph(e(skill["value"]), st["skill_value"])]],
                colWidths=[40 * mm, usable - 40 * mm])
            wiersz.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]))
            story.append(wiersz)

    # Certyfikaty
    if cv.get("certificates"):
        story.extend(section_header(et["certs"], st))
        dodaj_wpisy(story, cv["certificates"], st)

    # Kursy (bez certyfikatu)
    if cv.get("courses"):
        story.extend(section_header(et["courses"], st))
        dodaj_wpisy(story, cv["courses"], st)

    # Edukacja
    if cv.get("education"):
        story.extend(section_header(et["education"], st))
        for wpis in cv["education"]:
            lewa = Paragraph(tekst_edukacji(wpis), st["edu"])
            prawa = Paragraph(e(wpis.get("years", "")), st["edu_date"])
            story.append(dwie_kolumny(lewa, prawa, usable))
            if wpis.get("details"):
                story.append(Paragraph(e(", ".join(wpis["details"])), st["edu_detail"]))

    # Projekty
    if cv.get("projects"):
        story.extend(section_header(et["projects"], st))
        for p in cv["projects"]:
            story.append(Paragraph(tekst_projektu(p), st["edu"]))
            story.append(Spacer(1, 2))

    # Klauzula zgody
    story.append(Paragraph(e(et["footer"]), st["footer"]))

    doc.build(story)
    return doc.page


def build_cv(output_path, cv, jezyk, osoba=None, naglowki=None, zdjecie=None):
    fonty = setup_fonts()
    wybrana = SKALE[-1]
    for skala in SKALE:
        if zbuduj(io.BytesIO(), cv, jezyk, fonty, skala, osoba, naglowki, zdjecie) == 1:
            wybrana = skala
            break
    strony = zbuduj(output_path, cv, jezyk, fonty, wybrana, osoba, naglowki, zdjecie)
    if wybrana != 1.0 and strony == 1:
        print(f"ℹ️  Zmniejszono czcionkę do {int(wybrana * 100)}%, żeby CV zmieściło się na 1 stronie.")
    if strony > 1:
        print(f"⚠️  CV ma {strony} strony mimo zmniejszenia czcionki. "
              "Ogranicz liczbę punktów lub projektów (MAX_PUNKTOW, MAX_PROJEKTY).")
    return strony


def bezpieczna_nazwa(tekst):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", tekst).strip("_") or "cv"


def process_file(json_path, output_dir=None, jezyk=None):
    """Przetwarza jeden plik JSON na PDF. Zwraca ścieżkę PDF."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cv = data.get("cv", data)               # obsługa formatu {cv: {...}} i płaskiego
    meta = data.get("meta", {})
    jezyk = meta.get("jezyk") or jezyk or "pl"
    zdjecie = meta.get("zdjecie") or ""
    if jezyk not in ETYKIETY:
        sys.exit(f"❌ Nieznany język: {jezyk} (dostępne: {', '.join(ETYKIETY)})")

    osoba = meta.get("osoba") or {}
    nazwa = (f"{bezpieczna_nazwa(osoba.get('name') or IMIE_NAZWISKO)}_"
             f"{bezpieczna_nazwa(meta.get('firma', 'unknown'))}_"
             f"{bezpieczna_nazwa(meta.get('tytul', 'cv'))}_{jezyk.upper()}.pdf")
    folder = output_dir or os.path.dirname(os.path.abspath(json_path))
    os.makedirs(folder, exist_ok=True)
    sciezka = os.path.join(folder, nazwa)

    build_cv(sciezka, cv, jezyk, osoba, meta.get("naglowki"), zdjecie)
    print(f"✅ PDF: {sciezka}")
    return sciezka


def main():
    ap = argparse.ArgumentParser(description="Generator PDF z CV (PL/EN)")
    ap.add_argument("pliki", nargs="+", help="plik(i) JSON, można użyć wzorca *.json")
    ap.add_argument("--output", default=DEFAULT_OUTPUT_DIR, help="folder na PDF")
    ap.add_argument("--jezyk", choices=ETYKIETY.keys(), help="język, gdy JSON nie ma meta.jezyk")
    args = ap.parse_args()

    pliki = []
    for wzor in args.pliki:
        znalezione = glob.glob(wzor)
        pliki.extend(znalezione if znalezione else [wzor])
    pliki = [p for p in pliki if p.endswith(".json")]
    if not pliki:
        sys.exit("❌ Nie znaleziono plików JSON")

    for p in pliki:
        try:
            process_file(p, args.output, args.jezyk)
        except Exception as ex:
            print(f"❌ Błąd przy {p}: {ex}")


if __name__ == "__main__":
    main()