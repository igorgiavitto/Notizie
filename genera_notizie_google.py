#!/usr/bin/env python3
"""
Versione "solo Google News".
Genera una pagina HTML con le notizie principali in tre sezioni:
Mondo, Italia, Friuli Venezia Giulia.

Scarica i feed RSS direttamente dal tuo computer (nessun proxy),
scrive "notizie_google.html" nella stessa cartella dello script e la apre nel browser.

Uso:
    python3 genera_notizie_google.py              # genera una volta e apre la pagina
    python3 genera_notizie_google.py --ogni 15    # rigenera ogni 15 minuti (Ctrl+C per fermare)
    python3 genera_notizie_google.py --ore 48     # finestra temporale (default 24 ore)

Richiede solo Python 3.8+ (nessuna libreria da installare).
"""

import argparse
import html
import re
import shutil
import ssl
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ===== CONFIGURAZIONE: aggiungi o togli feed qui =====
FEEDS = [
    # Mondo
    ("mondo",  "Esteri",          "https://news.google.com/rss/headlines/section/topic/WORLD?hl=it&gl=IT&ceid=IT:it"),
    ("mondo",  "Europa",          "https://news.google.com/rss/search?q=Unione+europea+when:1d&hl=it&gl=IT&ceid=IT:it"),
    # Italia
    ("italia", "Italia",          "https://news.google.com/rss/headlines/section/topic/NATION?hl=it&gl=IT&ceid=IT:it"),
    ("italia", "Economia",        "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=it&gl=IT&ceid=IT:it"),
    # Friuli Venezia Giulia
    ("fvg",    "Friuli V.G.",     "https://news.google.com/rss/search?q=%22Friuli+Venezia+Giulia%22+when:1d&hl=it&gl=IT&ceid=IT:it"),
    ("fvg",    "Udine",           "https://news.google.com/rss/search?q=Udine+when:1d&hl=it&gl=IT&ceid=IT:it"),
    ("fvg",    "Trieste",         "https://news.google.com/rss/search?q=Trieste+when:1d&hl=it&gl=IT&ceid=IT:it"),
    ("fvg",    "Pordenone",       "https://news.google.com/rss/search?q=Pordenone+when:1d&hl=it&gl=IT&ceid=IT:it"),
    ("fvg",    "Gorizia",         "https://news.google.com/rss/search?q=Gorizia+when:1d&hl=it&gl=IT&ceid=IT:it"),
]
NOTIZIE_PER_SEZIONE = 20
TIMEOUT = 20
# =====================================================

SEZIONI = [
    ("mondo",  "Mondo",                 "Le notizie internazionali più recenti."),
    ("italia", "Italia",                "Politica, cronaca ed economia nazionale."),
    ("fvg",    "Friuli Venezia Giulia", "Trieste, Udine, Pordenone, Gorizia e il resto della regione."),
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def pulisci(testo, maxlen=280):
    testo = re.sub(r"<[^>]+>", " ", testo or "")
    testo = html.unescape(re.sub(r"\s+", " ", testo)).strip()
    if len(testo) > maxlen:
        testo = testo[:maxlen].rsplit(" ", 1)[0] + "…"
    return testo


def leggi_data(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def tag(el):
    return el.tag.split("}")[-1].lower()


def figlio(el, *nomi):
    for c in el:
        if tag(c) in nomi:
            return c
    return None


def _contesti_ssl():
    """Contesti SSL da provare, sempre con verifica dei certificati attiva."""
    yield "sistema", ssl.create_default_context()
    try:
        import certifi  # pip install certifi
        yield "certifi", ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass


def _scarica_bytes(url):
    headers = {"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    ultimo = None
    for _, ctx in _contesti_ssl():
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                return r.read()
        except urllib.error.URLError as e:
            ultimo = e
            if not isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                raise
    # Ultima strada: curl di sistema (usa i certificati del sistema operativo)
    curl = shutil.which("curl")
    if curl:
        p = subprocess.run(
            [curl, "-sSL", "--fail", "--max-time", str(TIMEOUT), "-A", UA, url],
            capture_output=True,
        )
        if p.returncode == 0 and p.stdout:
            return p.stdout
        raise RuntimeError("curl: " + (p.stderr.decode(errors="replace").strip() or f"codice {p.returncode}"))
    raise ultimo


def scarica(feed):
    sezione, nome, url = feed
    dati = _scarica_bytes(url)
    root = ET.fromstring(dati)
    notizie = []
    for it in root.iter():
        if tag(it) not in ("item", "entry"):
            continue
        t = figlio(it, "title")
        l = figlio(it, "link")
        link = (l.text or "").strip() if l is not None else ""
        if not link and l is not None:
            link = l.get("href", "")
        d = figlio(it, "pubdate", "published", "updated", "date")
        desc = figlio(it, "description", "summary", "content")
        titolo = pulisci(t.text if t is not None else "", 300)
        # Google News: testata originale nel tag <source>, titolo "Titolo - Testata"
        src = figlio(it, "source")
        testata = (src.text or "").strip() if src is not None else ""
        if testata and titolo.endswith(" - " + testata):
            titolo = titolo[: -len(testata) - 3].strip()
        elif not testata and " - " in titolo:
            titolo, testata = titolo.rsplit(" - ", 1)
        testo_desc = "" if "news.google." in url else pulisci(desc.text if desc is not None else "")
        if titolo and link:
            notizie.append({
                "sezione": sezione, "fonte": testata or nome, "titolo": titolo[:200], "link": link,
                "ricerca": nome,
                "desc": testo_desc,
                "data": leggi_data(d.text if d is not None else ""),
                "anche": [],
            })
    return notizie


def parole(t):
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", t).split() if len(w) > 3}


def simili(a, b):
    A, B = parole(a), parole(b)
    return bool(A and B) and len(A & B) / min(len(A), len(B)) >= 0.6


def togli_doppioni(items):
    out = []
    for it in items:
        dup = next((o for o in out if o["link"] == it["link"] or simili(o["titolo"], it["titolo"])), None)
        if dup:
            if it["fonte"] != dup["fonte"] and it["fonte"] not in dup["anche"]:
                dup["anche"].append(it["fonte"])
        else:
            out.append(it)
    return out


def genera(ore, ogni):
    print(f"[{datetime.now():%H:%M}] Scarico {len(FEEDS)} feed…")
    stato, tutte = [], []
    with ThreadPoolExecutor(max_workers=8) as ex:
        risultati = list(ex.map(lambda f: (f, _prova(f)), FEEDS))
    for f, (items, err) in risultati:
        if err:
            stato.append(f'<li class="ko">✗ {html.escape(f[1])} — {html.escape(err)}</li>')
            print(f"  ✗ {f[1]}: {err}")
        else:
            stato.append(f'<li class="ok">✓ {html.escape(f[1])} — {len(items)} notizie</li>')
            print(f"  ✓ {f[1]}: {len(items)}")
            tutte.extend(items)

    limite = datetime.now(timezone.utc) - timedelta(hours=ore)
    ora = datetime.now()
    sezioni_html = []
    for key, titolo, lead in SEZIONI:
        items = [i for i in tutte if i["sezione"] == key and (i["data"] is None or i["data"] >= limite)]
        items.sort(key=lambda i: i["data"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        items = togli_doppioni(items)[:NOTIZIE_PER_SEZIONE]
        if items:
            li = []
            for i in items:
                quando = i["data"].astimezone().strftime("%d/%m %H:%M") if i["data"] else ""
                anche = (" · anche " + ", ".join(i["anche"][:4])) if i["anche"] else ""
                desc = f'<p class="desc">{html.escape(i["desc"])}</p>' if i["desc"] and i["desc"] != i["titolo"] else ""
                li.append(
                    f'<li><a class="title" href="{html.escape(i["link"])}" target="_blank" rel="noopener">{html.escape(i["titolo"])}</a>'
                    f'{desc}<div class="info"><span class="badge">{html.escape(i["fonte"] + anche)}</span><span>{quando}</span></div></li>'
                )
            lista = "\n".join(li)
        else:
            lista = f'<li class="empty">Nessuna notizia disponibile nelle ultime {ore} ore.</li>'
        sezioni_html.append(f'<section id="{key}"><h2>{titolo}</h2><p class="lead">{lead}</p><ul class="news">{lista}</ul></section>')

    giorni = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    mesi = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
    data_it = f"{giorni[ora.weekday()]} {ora.day} {mesi[ora.month - 1]} {ora.year}, ore {ora:%H:%M}"
    refresh = f'<meta http-equiv="refresh" content="{ogni * 60}">' if ogni else ""

    pagina = PAGINA.format(
        refresh=refresh, titolo_data=f"{ora.day} {mesi[ora.month - 1]} {ora.year}",
        data=data_it, ore=ore, sezioni="\n".join(sezioni_html), stato="\n".join(stato),
    )
    out = Path(__file__).with_name("notizie_google.html")
    out.write_text(pagina, encoding="utf-8")
    print(f"  → scritto {out}")
    return out


def _prova(f):
    try:
        return scarica(f), None
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


PAGINA = """<!DOCTYPE html>
<html lang="it"><head><meta charset="UTF-8">{refresh}
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Notizie principali – {titolo_data}</title>
<style>
:root{{--bg:#faf9f6;--card:#fff;--text:#1f1e1c;--muted:#6b6a66;--border:#e4e2dc;--accent:#2f5d8a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1a18;--card:#24231f;--text:#ecebe6;--muted:#a3a19a;--border:#3a3934;--accent:#8db6e0}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}}
.wrap{{max-width:880px;margin:0 auto;padding:32px 20px 64px}}h1{{font-size:28px;font-weight:600;margin:0 0 4px}}
.meta{{color:var(--muted);font-size:14px}}nav{{display:flex;gap:14px;margin:16px 0 8px;font-size:14px}}nav a{{color:var(--accent);text-decoration:none}}
section{{margin-top:36px}}h2{{font-size:22px;font-weight:600;margin:0 0 4px;padding-left:12px;border-left:4px solid var(--sec)}}
#mondo{{--sec:#2f5d8a}}#italia{{--sec:#3b7a3b}}#fvg{{--sec:#a3521f}}
.lead{{color:var(--muted);font-size:14px;margin:0 0 14px 16px}}ul.news{{list-style:none;margin:0;padding:0}}
ul.news li{{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:10px}}
a.title{{color:var(--text);font-weight:600;text-decoration:none}}a.title:hover{{color:var(--accent);text-decoration:underline}}
.desc{{margin:4px 0 0;font-size:14.5px}}.info{{font-size:12.5px;color:var(--muted);margin-top:6px;display:flex;gap:10px}}
.badge{{color:var(--sec);font-weight:600}}.empty{{color:var(--muted);font-style:italic}}
details{{margin-top:48px;font-size:13px;color:var(--muted)}}.ok{{color:#3b7a3b}}.ko{{color:#b23b3b}}
@media print{{nav,details{{display:none}}ul.news li{{break-inside:avoid}}}}
</style></head><body><div class="wrap">
<h1>Notizie principali</h1><div class="meta">Generato {data} · ultime {ore} ore</div>
<nav><a href="#mondo">Mondo</a><a href="#italia">Italia</a><a href="#fvg">Friuli Venezia Giulia</a></nav>
{sezioni}
<details><summary>Stato delle fonti</summary><ul>{stato}</ul></details>
</div></body></html>"""


def main():
    p = argparse.ArgumentParser(description="Genera il report delle notizie in HTML")
    p.add_argument("--ore", type=int, default=24, help="finestra temporale in ore (default 24)")
    p.add_argument("--ogni", type=int, default=0, help="rigenera ogni N minuti (0 = una volta sola)")
    a = p.parse_args()
    out = genera(a.ore, a.ogni)
    webbrowser.open(out.resolve().as_uri())
    if a.ogni:
        print(f"Aggiornamento ogni {a.ogni} minuti. Ctrl+C per fermare.")
        try:
            while True:
                time.sleep(a.ogni * 60)
                genera(a.ore, a.ogni)
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()
