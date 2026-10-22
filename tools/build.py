"""Rigenera le parti del sito che dipendono dal menu, a partire da data/menu.json.

Scrive solo tra i marcatori <!-- MENU:xxx --> ... <!-- /MENU:xxx --> di menu.html e index.html,
quindi il resto delle pagine si modifica a mano come sempre.
Uso (dalla cartella del sito): python3 tools/build.py
"""
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://www.lagiaraart.it/"  # dominio DA CONFERMARE
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def esc(s):
    return html.escape(str(s or ""), quote=True)


def euro(v):
    return f"{float(v):.2f}".replace(".", ",") + " €"


def visible(cat):
    return [v for v in cat.get("voci", []) if v.get("visibile", True) and str(v.get("nome", "")).strip()]


def dish_name(nome):
    nome = nome.strip()
    if nome.endswith("*"):
        return esc(nome[:-1].rstrip()) + "<sup>*</sup>"
    return esc(nome)


def render_chips(cats):
    return "\n".join(f'<a class="chip" href="#m-{esc(c["id"])}">{esc(c.get("breve") or c["titolo"])}</a>' for c in cats)


def render_blocks(cats):
    out = []
    for c in cats:
        items = visible(c)
        lis = []
        for v in items:
            desc = f'<p class="dish__desc">{esc(v["ingredienti"])}</p>' if str(v.get("ingredienti", "")).strip() else ""
            lis.append(f'<li class="dish"><div class="dish__row"><h3 class="dish__name">{dish_name(v["nome"])}</h3>'
                       f'<span class="dish__dots" aria-hidden="true"></span><span class="dish__price">{euro(v["prezzo"])}</span>'
                       f'</div>{desc}</li>')
        note = f'<p class="menu-cat__note">{esc(c["nota"])}</p>' if str(c.get("nota", "")).strip() else ""
        cid = esc(c["id"])
        out.append(f'<section class="menu-cat" id="m-{cid}" aria-labelledby="m-{cid}-t"><div class="menu-cat__head">'
                   f'<h2 id="m-{cid}-t">{esc(c["titolo"])}</h2><span class="menu-cat__count">{len(items)}</span></div>'
                   f'{note}<ul class="dishes">{"".join(lis)}</ul></section>')
    return "\n".join(out)


def render_cards(cats):
    out = []
    for c in cats:
        if not c.get("vetrina", True):
            continue
        prices = [float(v["prezzo"]) for v in visible(c)]
        if not prices:
            continue
        lo, hi = min(prices), max(prices)
        price = euro(lo) if lo == hi else "da " + euro(lo)
        n = len(prices)
        out.append(f'          <li><a class="cat" href="menu.html#m-{esc(c["id"])}"><span class="cat__name">{esc(c["titolo"])}</span>'
                   f'<span class="cat__price">{price}</span><span class="cat__n">{n} {"proposta" if n == 1 else "proposte"}</span></a></li>')
    return "\n".join(out)


def render_ld(cats):
    menu = {
        "@context": "https://schema.org", "@type": "Menu", "@id": SITE + "menu.html", "url": SITE + "menu.html",
        "inLanguage": "it", "name": "Menu La Giara Art",
        "hasMenuSection": [{
            "@type": "MenuSection", "name": c["titolo"],
            "hasMenuItem": [{
                "@type": "MenuItem", "name": v["nome"].rstrip("*").strip(),
                **({"description": v["ingredienti"].strip()} if str(v.get("ingredienti", "")).strip() else {}),
                "offers": {"@type": "Offer", "price": f'{float(v["prezzo"]):.2f}', "priceCurrency": "EUR"},
            } for v in visible(c)],
        } for c in cats if visible(c)],
    }
    js = json.dumps(menu, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/ld+json">{js}</script>'


def render_date(iso):
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"Prezzi aggiornati al {d.day} {MESI[d.month - 1]} {d.year}."


def euro_breve(v):
    v = float(v)
    return f"{v:.0f} €" if v == int(v) else euro(v)


def fill_prices(text, prices, path):
    """Aggiorna i prezzi citati nel testo: <tag data-prezzo="Nome voce" [data-formato="breve"]>…</tag>."""
    def sub(m):
        nome = html.unescape(m.group(3))
        if nome not in prices:
            print(f"attenzione: {path} cita '{nome}', che non è nel menu visibile: prezzo lasciato com'è")
            return m.group(0)
        val = euro_breve(prices[nome]) if 'data-formato="breve"' in m.group(1) else euro(prices[nome])
        return m.group(1) + val + m.group(5)
    return re.sub(r'(<(\w+)\b[^>]*\bdata-prezzo="([^"]+)"[^>]*>)(.*?)(</\2>)', sub, text)


def replace_block(text, name, content, path):
    pat = re.compile(rf"(<!-- MENU:{name} -->)(.*?)(<!-- /MENU:{name} -->)", re.S)
    if not pat.search(text):
        sys.exit(f"Marcatore MENU:{name} non trovato in {path}")
    return pat.sub(lambda m: m.group(1) + "\n" + content + "\n" + m.group(3), text, count=1)


def check(data):
    ids = set()
    for c in data["categorie"]:
        if not re.fullmatch(r"[a-z0-9-]+", c.get("id", "")):
            sys.exit(f"Id categoria non valido: {c.get('id')!r}")
        if c["id"] in ids:
            sys.exit(f"Id categoria ripetuto: {c['id']}")
        ids.add(c["id"])
        for v in c.get("voci", []):
            float(v.get("prezzo", 0))


def main():
    data = json.loads((ROOT / "data/menu.json").read_text(encoding="utf-8"))
    check(data)
    cats = [c for c in data["categorie"] if visible(c)]
    prices = {v["nome"].rstrip("*").strip(): v["prezzo"] for c in cats for v in visible(c)}
    files = {
        "menu.html": {"chips": render_chips(cats), "categorie": render_blocks(cats),
                      "ld": render_ld(cats), "data": render_date(data.get("aggiornato"))},
        "index.html": {"vetrina": render_cards(cats)},
    }
    for fname, blocks in files.items():
        p = ROOT / fname
        text = p.read_text(encoding="utf-8")
        new = text
        for name, content in blocks.items():
            new = replace_block(new, name, content, fname)
        new = fill_prices(new, prices, fname)
        if new != text:
            p.write_text(new, encoding="utf-8", newline="\n")
            print("aggiornato", fname)
    print(sum(len(visible(c)) for c in cats), "voci visibili in", len(cats), "categorie")


if __name__ == "__main__":
    main()
