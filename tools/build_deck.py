#!/usr/bin/env python3
"""Génère le support de soutenance (PPTX) + les notes du présentateur.

    python tools/build_deck.py                 # → docs/PRESENTATION-SOUTENANCE.pptx
    python tools/build_deck.py --group 3

Chaque diapositive porte, dans ses **notes**, le texte exact à dire et le geste
à faire. C'est volontaire : un support qu'on ne sait pas commenter ne sert à
rien. Le jour J, on ouvre le mode Présentateur (PowerPoint/Keynote) et on lit.

Charte du sujet (slide 9 de la présentation fournie) : Montserrat, écriture
#261E48, fond #E4E6F3, rectangles #A1A6D4. Les couleurs d'état (rouge, orange,
vert) en sortent volontairement : elles doivent être lisibles à distance.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = pathlib.Path(__file__).resolve().parent.parent

INK = RGBColor(0x26, 0x1E, 0x48)
PAGE = RGBColor(0xE4, 0xE6, 0xF3)
RECT = RGBColor(0xA1, 0xA6, 0xD4)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREEN = RGBColor(0x0F, 0x9D, 0x63)
RED = RGBColor(0xC8, 0x10, 0x2E)
AMBER = RGBColor(0xD9, 0x77, 0x06)
GREY = RGBColor(0x6B, 0x68, 0x80)
SOFT = RGBColor(0xF4, 0xF5, 0xFB)

FONT = "Montserrat"
W, H = Inches(13.333), Inches(7.5)


# ---------------------------------------------------------------------------
# Briques de mise en page
# ---------------------------------------------------------------------------
def slide(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, H)
    bg.fill.solid()
    bg.fill.fore_color.rgb = PAGE
    bg.line.fill.background()
    bg.shadow.inherit = False
    return s


def textbox(s, x, y, w, h, text, size=18, bold=False, color=INK, align=PP_ALIGN.LEFT,
            italic=False, spacing=1.0, anchor=MSO_ANCHOR.TOP):
    box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = 0
    frame.margin_top = frame.margin_bottom = 0
    paragraphs = text.split("\n")
    for index, line in enumerate(paragraphs):
        p = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        run = p.add_run()
        run.text = line
        run.font.name = FONT
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = italic
        run.font.color.rgb = color
    return box


def bullets(s, items, x=0.9, y=1.75, w=11.6, h=4.6, size=16, color=INK, gap=10):
    box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = 0
    for index, item in enumerate(items):
        p = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        p.space_after = Pt(gap)
        p.line_spacing = 1.12
        run = p.add_run()
        run.text = "—  " + item
        run.font.name = FONT
        run.font.size = Pt(size)
        run.font.color.rgb = color
    return box


def title(s, text, kicker=None):
    if kicker:
        textbox(s, 0.9, 0.5, 11.6, 0.35, kicker.upper(), size=11, bold=True, color=GREY)
        textbox(s, 0.9, 0.82, 11.6, 0.95, text, size=26, bold=True)
    else:
        textbox(s, 0.9, 0.62, 11.6, 0.95, text, size=26, bold=True)
    bar = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.9), Inches(1.55), Inches(1.2), Inches(0.05))
    bar.fill.solid()
    bar.fill.fore_color.rgb = INK
    bar.line.fill.background()
    bar.shadow.inherit = False


def footer(s, number, group):
    textbox(s, 0.9, 7.0, 6.0, 0.3, "RESILIENCE-ALPHA · SHADOW-SETTLE", size=9, color=GREY)
    textbox(s, 10.4, 7.0, 2.0, 0.3, f"G{group} · {number}", size=9, color=GREY, align=PP_ALIGN.RIGHT)


def notes(s, text):
    s.notes_slide.notes_text_frame.text = text.strip()


def card(s, x, y, w, h, head, body, color=INK, fill=WHITE):
    shape = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.adjustments[0] = 0.06
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.color.rgb = color
    shape.line.width = Pt(1.2)
    shape.shadow.inherit = False
    textbox(s, x + 0.22, y + 0.16, w - 0.44, 0.4, head, size=12, bold=True, color=color)
    textbox(s, x + 0.22, y + 0.58, w - 0.44, h - 0.7, body, size=11, color=INK, spacing=1.05)


def big_number(s, x, y, value, label, color=INK):
    textbox(s, x, y, 2.6, 0.7, value, size=34, bold=True, color=color)
    textbox(s, x, y + 0.72, 2.6, 0.5, label, size=10, color=GREY)


def table(s, rows, x, y, w, h, col_widths=None, header=True, size=11):
    shape = s.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w), Inches(h))
    tbl = shape.table
    if col_widths:
        for index, width in enumerate(col_widths):
            tbl.columns[index].width = Inches(width)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cell = tbl.cell(r, c)
            cell.text = ""
            cell.margin_left = Inches(0.08)
            cell.margin_right = Inches(0.06)
            cell.margin_top = Inches(0.03)
            cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = INK if (header and r == 0) else (WHITE if r % 2 else SOFT)
            p = cell.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = str(value)
            run.font.name = FONT
            run.font.size = Pt(size)
            run.font.bold = (header and r == 0) or c == 0
            run.font.color.rgb = WHITE if (header and r == 0) else INK
    return tbl


# ---------------------------------------------------------------------------
# Diapositives
# ---------------------------------------------------------------------------
def build(group: str, out: pathlib.Path) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H

    # 1 — Couverture ------------------------------------------------------
    s = slide(prs)
    band = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(0.32), H)
    band.fill.solid(); band.fill.fore_color.rgb = INK; band.line.fill.background(); band.shadow.inherit = False
    textbox(s, 0.9, 1.15, 11.5, 0.4, "WORKSHOP M2 — 2026", size=13, bold=True, color=GREY)
    textbox(s, 0.9, 1.6, 11.5, 1.2, "RESILIENCE-ALPHA", size=48, bold=True)
    textbox(s, 0.9, 2.75, 11.5, 0.8, "SHADOW-SETTLE", size=30, bold=True, color=RECT)
    textbox(s, 0.9, 3.6, 10.5, 1.2,
            "Un SI de paiement qui encaisse l'attaque,\nse relève seul et le prouve.",
            size=20, color=INK, spacing=1.15)
    for i, (value, label) in enumerate(
        [("19", "services"), ("221", "tests"), ("12", "failles fermées"), ("7 s", "de reprise")]
    ):
        big_number(s, 0.9 + i * 3.0, 5.3, value, label)
    textbox(s, 0.9, 6.55, 11.5, 0.4,
            f"Workshop2026-M2-G{group} · soutenance du 23 octobre 2026", size=11, color=GREY)
    notes(s, """
Bonjour. Nous sommes l'équipe du groupe {g}. Notre sujet : un système de paiement
fictif qui doit encaisser une attaque et se rétablir seul, sans intervention.

Une phrase pour planter le décor : « tout système finit par tomber ; la vraie
question, c'est si le vôtre saura se relever seul. » C'est exactement ce que
nous allons vous montrer, en direct, depuis deux machines distinctes.

Les quatre chiffres en bas sont ceux que nous allons prouver : 19 services,
226 tests, 12 failles fermées, et une reprise en 7 secondes.
""".format(g=group))

    # 2 — Le problème -----------------------------------------------------
    s = slide(prs)
    title(s, "Le problème : on relance à la main, et on ne le prouve pas", "Contexte")
    bullets(s, [
        "Une plateforme de paiement subit des incidents à répétition.",
        "À chaque fois, les équipes redémarrent : lent, non reproductible, non traçable.",
        "Un redémarrage n'est pas une remédiation : il efface la preuve.",
        "Le sujet impose trois choses : attaque réelle, ML (pas de seuils fixes), preuve de reprise.",
    ], y=1.9, size=18, gap=14)
    card(s, 8.5, 4.5, 4.1, 1.9, "Ce que nous mesurons",
         "attaque → détection → playbook →\ntransaction de preuve → déception\n\n6,7 à 7,7 s (limite 90 s)",
         color=GREEN, fill=SOFT)
    footer(s, 2, group)
    notes(s, """
Le point de départ est banal : une plateforme tombe, on la relance à la main.
C'est lent, ce n'est pas reproductible, et surtout ça ne prouve rien : après un
redémarrage, on ne sait pas si le service fonctionne vraiment.

Le sujet interdit deux raccourcis : une attaque « pour de faux », et une
détection par seuils fixes. Il impose une attaque réelle depuis un autre poste,
un modèle entraîné, et une preuve : une nouvelle transaction qui réussit.

C'est cette chaîne complète que nous mesurons en 6,7 à 7,7 secondes, pour une
limite de 90.
""")

    # 3 — La promesse -----------------------------------------------------
    s = slide(prs)
    title(s, "La réponse : une chaîne, quatre preuves", "Promesse")
    steps = [
        ("1", "Attaque", "poste distinct,\nleurres empoisonnés", RED),
        ("2", "Détection", "seuil appris 6,715\n3 fenêtres consécutives", AMBER),
        ("3", "Remédiation", "Ansible réel :\ndéception, quarantaine", INK),
        ("4", "Preuve", "transaction client\nacceptée, key image", GREEN),
    ]
    for i, (number, head, body, color) in enumerate(steps):
        x = 0.75 + i * 3.15
        card(s, x, 2.0, 2.85, 2.25, f"{number} · {head}", body, color=color)
        if i < 3:
            arrow = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x + 2.9), Inches(2.95), Inches(0.22), Inches(0.3))
            arrow.fill.solid(); arrow.fill.fore_color.rgb = RECT; arrow.line.fill.background(); arrow.shadow.inherit = False
    textbox(s, 0.9, 4.75, 11.6, 1.5,
            "Aucune intervention humaine entre l'attaque et la reprise.\n"
            "Le geste final n'est pas administratif : c'est un client qui paie de nouveau.",
            size=17, color=INK, spacing=1.2)
    footer(s, 3, group)
    notes(s, """
Voilà la chaîne en quatre temps. Un poste attaquant distinct empoisonne les
leurres de l'anneau. Le détecteur franchit un seuil qu'il a appris. Le moteur
lance un vrai playbook Ansible : déception, quarantaine, durcissement. Et la
preuve, ce n'est pas un voyant vert : c'est une transaction client qui passe.

Le point important pour la grille : personne ne touche au clavier entre l'attaque
et la reprise. Et le geste de la fin est un paiement de client, pas une action
d'administration.
""")

    # 4 — Architecture ----------------------------------------------------
    s = slide(prs)
    title(s, "Architecture : chaque flèche est un flux réel", "Architecture")
    boxes = [
        (0.75, 1.95, 2.25, 1.0, "PC Attaquant", "attack.py", RED),
        (3.35, 1.95, 2.05, 1.0, "waf :8085", "proxy + déception", INK),
        (5.75, 1.95, 2.35, 1.0, "payment-api :8080", "registre, PIN, sonde", INK),
        (8.45, 1.95, 2.55, 1.0, "monero-crypto-core", "Rust · bLSAG", INK),
        (5.75, 3.55, 2.35, 1.0, "ml-detector :8083", "seuil appris", AMBER),
        (8.45, 3.55, 2.55, 1.0, "remediation-engine", "Ansible · SSE", AMBER),
        (3.35, 3.55, 2.05, 1.0, "shadow-api :8090", "honeypot", GREY),
        (8.45, 5.15, 2.55, 1.0, "front-dashboard", "badge · score · journal", GREEN),
    ]
    for x, y, w, h, head, sub, color in boxes:
        card(s, x, y, w, h, head, sub, color=color, fill=WHITE)
    arrows = [
        (3.0, 2.35, 0.3), (5.4, 2.35, 0.3), (8.1, 2.35, 0.3),
        (6.9, 2.95, 0.55), (8.1, 4.05, 0.3),
        (5.4, 4.05, 0.3),
    ]
    for x, y, w in arrows:
        a = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(0.26))
        a.fill.solid(); a.fill.fore_color.rgb = RECT; a.line.fill.background(); a.shadow.inherit = False
    textbox(s, 0.9, 5.55, 6.9, 1.2,
            "Le WAF est le seul point d'entrée « client ».\n"
            "Le ML observe, il ne filtre pas : s'il tombe, le paiement continue.",
            size=13, color=INK, spacing=1.15)
    footer(s, 4, group)
    notes(s, """
Nous n'allons pas décrire les dix-neuf conteneurs. Regardez les flèches.

L'attaquant entre par le WAF, comme un client. Le paiement appelle le noyau Rust
à chaque transaction. La télémétrie part vers le détecteur, qui appelle le
moteur, qui exécute Ansible : déception vers le honeypot, quarantaine et
durcissement côté paiement, rotation de clé dans Vault.

Deux choix à retenir : le WAF ne déclenche rien, il obéit au playbook. Et le ML
est hors du chemin critique : si on l'arrête, le paiement continue.
""")

    # 5 — Piliers ---------------------------------------------------------
    s = slide(prs)
    title(s, "Les cinq piliers, reliés par des flux", "Piliers")
    rows = [
        ["Pilier", "Ce qui le rend réel", "Preuve"],
        ["DEV", "noyau Rust bLSAG appelé à chaque transaction", "9 tests Rust + 10 HTTP"],
        ["INFRA", "playbook Ansible exécuté dans l'image, 12 cibles Prometheus", "chaos 6/6"],
        ["IA/DATA", "seuil appris sur le trafic du poste, AUC 1,00", "FPR 0 %, rappel 100 %"],
        ["CYBER", "déception active, honeypot, 2FA visage", "4 + 6 familles d'attaques"],
        ["TRANSFORMATION", "EBIOS, PCA/PRA, NIS2, DORA reliés aux composants", "RTO 15 s"],
    ]
    table(s, rows, 0.9, 1.95, 11.5, 3.9, col_widths=[2.4, 6.0, 3.1], size=12)
    textbox(s, 0.9, 6.0, 11.5, 0.7,
            "Un pilier n'est pas « présent » parce qu'un conteneur porte son nom : il l'est quand un flux le traverse.",
            size=13, italic=True, color=GREY)
    footer(s, 5, group)
    notes(s, """
La grille locale demande « les cinq piliers présents et réellement
interconnectés ». Nous avons évité le piège de la diapositive à logos : chaque
ligne dit quel flux rend le pilier réel, et quelle preuve l'accompagne.

Si on vous demande « où est le lien entre IA et INFRA ? » : le détecteur publie
un incident au moteur, qui exécute Ansible. C'est le même verrou et le même
playbook que pour une alerte SIEM.
""")

    # 6 — Crypto ----------------------------------------------------------
    s = slide(prs)
    title(s, "La cryptographie est réelle, et ses limites sont écrites", "Expertise")
    card(s, 0.75, 1.9, 5.6, 2.0, "Ce qui est implémenté",
         "Ristretto255 · adresses one-time par ECDH\n"
         "key image I = x·Hp(P), hash-vers-le-groupe\n"
         "signature d'anneau bLSAG · engagements de Pedersen\n"
         "audit view-only : lire sans pouvoir dépenser", color=GREEN)
    card(s, 6.6, 1.9, 5.9, 2.0, "Les deux écarts assumés",
         "CLSAG → bLSAG : signature ~2× plus grosse, même sécurité\n"
         "pas de Bulletproofs : un montant négatif modulo l\n"
         "passerait l'équilibre — publié dans /healthz", color=AMBER)
    textbox(s, 0.9, 4.25, 11.6, 2.2,
            "Ce n'est pas du Monero de production, et nous ne le prétendons pas.\n"
            "C'est un modèle simplifié mais réel : une signature est produite, vérifiée,\n"
            "et un rejeu de key image est refusé en 409 — y compris sous concurrence.",
            size=15, color=INK, spacing=1.25)
    footer(s, 6, group)
    notes(s, """
Question qui vient toujours : « c'est du vrai Monero ? » Réponse honnête : non,
c'est un modèle simplifié mais réel sur Ristretto255. Les adresses one-time sont
dérivées par ECDH, la key image utilise un vrai hash-vers-le-groupe — donc
personne ne peut la forger — et la signature est une bLSAG, pas une simulation.

Deux écarts : la signature est plus grosse qu'une CLSAG, et il n'y a pas de
Bulletproofs. Conséquence : un montant négatif modulo l passerait la
vérification d'équilibre. Il faut un attaquant authentifié qui forge ses
engagements. C'est écrit dans /healthz et dans le dossier — c'est notre première
tâche après le workshop.
""")

    # 7 — ML --------------------------------------------------------------
    s = slide(prs)
    title(s, "La détection est apprise, pas un seuil déguisé", "IA")
    big_number(s, 0.9, 1.85, "6,715", "seuil appris", color=GREEN)
    big_number(s, 3.6, 1.85, "0 %", "faux positifs", color=GREEN)
    big_number(s, 6.3, 1.85, "100 %", "rappel mesuré", color=GREEN)
    big_number(s, 9.0, 1.85, "1,000", "AUC sur l'âge des leurres")
    textbox(s, 0.9, 3.15, 11.6, 2.9,
            "1 287 observations capturées sur CE poste, puis entraînement\n"
            "(StandardScaler + IsolationForest + autoencodeur PyTorch).\n\n"
            "Gamma réelle : 3,6 j ± 0,2  ·  Attaquant uniforme : 26,9 j ± 2,3\n"
            "Aucun chevauchement : c'est physique, pas un réglage.",
            size=15, color=INK, spacing=1.2)
    card(s, 8.9, 5.35, 3.5, 1.35, "Trois fenêtres",
         "consécutives avant de\ndéclencher : pas de faux\npositif isolé", color=INK, fill=SOFT)
    footer(s, 7, group)
    notes(s, """
C'est le point que le jury teste. Le seuil n'est pas dans le code : il est dans
threshold.json, appris sur une capture du trafic de ce poste, et le runtime
refuse un seuil qui vient d'un autre modèle.

Pourquoi une seule feature porte l'attaque ? Parce qu'un wallet tire ses leurres
selon une gamma biaisée vers les récents — 3,6 jours mesurés — alors qu'un
attaquant qui empoisonne pioche uniformément — 26,9 jours. Il n'y a aucun
chevauchement. Les cinq autres features couvrent les autres familles : rejeu,
rafale, anneau malformé.

Et on exige trois fenêtres consécutives : un pic isolé ne déclenche pas.
""")

    # 8 — Remédiation -----------------------------------------------------
    s = slide(prs)
    title(s, "La remédiation est autonome, et elle ne redémarre rien", "Remédiation")
    bullets(s, [
        "Déception : le WAF déroute l'attaquant vers le honeypot.",
        "Quarantaine : les 25 outputs les plus anciens sont retirés du pool.",
        "Durcissement : l'anneau passe de 11 à 25 leurres.",
        "Rotation : la clé JWT est tournée dans Vault, relue en 30 s.",
        "Preuve : la sonde de transaction doit répondre OK.",
    ], y=1.9, size=17, gap=12)
    card(s, 8.2, 4.55, 4.3, 1.85, "Ce qu'on ne fait pas",
         "redémarrer payment-api :\nun redémarrage efface la preuve.\nCe n'est pas une remédiation.",
         color=RED, fill=SOFT)
    footer(s, 8, group)
    notes(s, """
Le playbook est un vrai ansible-playbook, dans l'image du moteur. Cinq actions :
déception, quarantaine, durcissement, rotation de clé, puis vérification par la
sonde.

Ce que nous ne faisons pas, volontairement : redémarrer payment-api. La première
version du playbook le faisait, protégée par ignore_errors ; elle aurait effacé
le registre d'outputs, c'est-à-dire exactement la preuve que la démo doit
montrer. Un redémarrage destructif n'est pas une remédiation.

Détail d'ingénierie : le moteur répond 202 tout de suite et publie l'état en
SSE. La version synchrone tombait en timeout alors que la remédiation marchait.
""")

    # 9 — Preuves ---------------------------------------------------------
    s = slide(prs)
    title(s, "Trois exécutions consécutives, mêmes résultats", "Preuves")
    rows = [
        ["Exécution", "Détection", "Reprise", "TX de preuve", "Déception", "Ansible"],
        ["1", "3,6 s", "7,2 s", "OK", "active", "rc = 0"],
        ["2", "3,4 s", "7,4 s", "OK", "active", "rc = 0"],
        ["3", "3,1 s", "6,7 s", "OK", "active", "rc = 0"],
    ]
    table(s, rows, 0.9, 1.95, 9.4, 2.3, col_widths=[1.6, 1.8, 1.6, 2.0, 1.5, 1.6], size=13)
    card(s, 10.6, 1.95, 1.9, 2.3, "Limite", "90 s\n\nmarge\n≈ 83 s", color=GREEN, fill=SOFT)
    textbox(s, 0.9, 4.65, 11.6, 1.9,
            "226 tests · 4 familles d'attaques + 6 familles boutique · chaos 6/6\n"
            "La reprise n'est pas un indicateur : c'est une transaction réelle, avec sa key image.",
            size=15, color=INK, spacing=1.25)
    footer(s, 9, group)
    notes(s, """
Ces chiffres sont ceux du matin de la soutenance, pas d'il y a trois semaines.
Trois exécutions de suite, sans redémarrer la pile : la fenêtre du détecteur est
purgée entre deux, sinon la deuxième démo serait contaminée par la première.

La marge est de 83 secondes sur la limite de 90. Elle sert à lire le journal
Ansible et à commenter, pas à attendre.

Et 226 tests, dont un par faille fermée : une correction sans test revient au
premier refactor.
""")

    # 10 — Boutique -------------------------------------------------------
    s = slide(prs)
    title(s, "Le SI est un site de paiement, pas seulement un dashboard", "Commerce")
    bullets(s, [
        "6 articles en PostgreSQL — le front lit GET /products, rien n'est codé en dur.",
        "Panier en base, stock réservé en transaction, jamais négatif.",
        "Payer → signature bLSAG → commande paid avec sa key image (72 ms).",
        "Historique protégé par un capability token de 192 bits : sans lui, 404.",
        "Achat invité assumé : le ticket porte un nom et un e-mail, pas de compte.",
    ], y=1.9, size=16, gap=11)
    card(s, 8.6, 4.6, 3.9, 1.8, "Ce que l'admin voit",
         "client, montant, key image,\névénements de paiement,\nstock temps réel", color=INK, fill=SOFT)
    footer(s, 10, group)
    notes(s, """
Les règles du 20 octobre ont déplacé le sujet : il fallait un site vitrine
connecté à une base, un front, un back, un paiement avec stockage et historique.

Nous avons écrit la boutique plutôt qu'installé Prestashop : le modèle de
données, la surface d'attaque et la preuve sont maîtrisés.

Point à assumer : c'est un achat invité, sans création de compte — c'est un
choix de vitrine. Mais le ticket porte un nom et une adresse, l'historique est
protégé par un jeton de capacité, et le prix vient toujours de la base. Un prix
envoyé par le client est ignoré.
""")

    # 11 — IA -------------------------------------------------------------
    s = slide(prs)
    title(s, "L'IA répond en citant, et agit sur les alertes", "IA")
    card(s, 0.75, 1.9, 5.7, 2.6, "RAG — 18 documents, 709 passages",
         "BM25 construit au build sur le dossier\n"
         "réponse + sources (fichier, section, score)\n"
         "Ollama llama3.1:8b sur le PC hôte\n"
         "sinon extractif — il cite, il n'invente pas", color=GREEN)
    card(s, 6.7, 1.9, 5.8, 2.6, "SIEM — l'alerte déclenche le playbook",
         "Prometheus 24 règles · Loki 4 règles\n"
         "Alertmanager → POST /alert → même moteur\n"
         "corrélation : 3 à 5 alertes = 1 incident\n"
         "les alertes de confort sont ignorées", color=INK)
    textbox(s, 0.9, 4.8, 11.6, 1.6,
            "Le même chemin que le détecteur ML : même verrou, même playbook, même preuve.\n"
            "Deux chemins de remédiation finiraient par diverger.",
            size=15, color=INK, spacing=1.25)
    footer(s, 11, group)
    notes(s, """
Deux briques d'IA, deux rôles.

Le RAG répond sur le dossier technique : 18 documents, 709 passages, et surtout
il cite ses sources. Si Ollama n'est pas joignable, il passe en extractif — les
phrases du dossier — et il le dit dans /healthz. Un assistant qui invente serait
pire qu'un assistant qui cite.

L'ingestion SIEM est la partie « l'IA agit » : une alerte actionnable ouvre un
incident et lance le même playbook que le ML. Une alerte de confort, comme un
bandeau LED muet, est journalisée sans agir. Et trois à cinq alertes en cascade
décrivent un seul incident, pas cinq.
""")

    # 12 — Sécurité -------------------------------------------------------
    s = slide(prs)
    title(s, "12 failles fermées, 4 bugs critiques, 10 limites écrites", "Sécurité")
    rows = [
        ["Famille", "Exemple trouvé", "Correction"],
        ["Authentification", "login acceptait n'importe quel secret", "SHA-256, temps constant, 503 fail-closed"],
        ["Administration", "/admin/* de payment-api ouverts", "jeton de service obligatoire"],
        ["Concurrence", "double dépense par deux curl simultanés", "key image réservée avant le premier await"],
        ["Données", "oracle 404/500 sur l'historique", "format validé, 404 uniforme"],
        ["Chaîne SIEM", "alerte Loki visant le mauvais service", "sélecteur corrigé + contrôle statique"],
    ]
    table(s, rows, 0.9, 1.9, 11.5, 3.3, col_widths=[2.7, 5.1, 3.7], size=11.5)
    textbox(s, 0.9, 5.45, 11.5, 1.3,
            "Nous ne disons pas « invulnérable ». Nous disons : 12 failles fermées,\n"
            "chacune avec son test, et 6 familles d'attaques rejouables en une commande.",
            size=15, color=INK, spacing=1.2)
    footer(s, 12, group)
    notes(s, """
Le jury du 20 octobre a remplacé « pas de faille volontaire » par « SI
invulnérable ». Aucun SI ne l'est. Ce que nous pouvons prouver, c'est le
travail : 12 failles trouvées et fermées, 4 bugs critiques, et un test nommé
pour chaque correction.

Les deux plus parlantes : deux curl simultanés réussissaient une double dépense
— la propriété centrale du projet était contournable. Et la règle Loki qui
devait alerter sur un paiement refusé visait le mauvais service : l'alerte ne
pouvait jamais se déclencher, donc aucune remédiation.

La phrase à retenir : nous ne disons pas invulnérable, nous disons vérifiable.
""")

    # 13 — Gouvernance ----------------------------------------------------
    s = slide(prs)
    title(s, "Gouvernance : les risques sont reliés au démonstrateur", "Gouvernance")
    rows = [
        ["Référentiel", "Ce qui est couvert", "Ce qui ne l'est pas"],
        ["EBIOS RM", "7 scénarios, dont empoisonnement, rejeu, panne silencieuse", "audit externe"],
        ["PCA / PRA", "RTO 15 s sur le paiement ; RPO non borné sur PostgreSQL", "pg_dump non scripté"],
        ["NIS2 art. 21", "risques, incidents, continuité, MFA back-office", "MFA du terminal (PIN seul)"],
        ["DORA art. 19", "audit view-only, journal d'audit, journaux Loki", "revue d'accès périodique"],
        ["RGPD", "minimisation : pas d'image conservée, descripteur seul", "registre, DPO, AIPD"],
    ]
    table(s, rows, 0.9, 1.9, 11.5, 3.4, col_widths=[2.5, 5.7, 3.3], size=11.5)
    textbox(s, 0.9, 5.55, 11.5, 1.2,
            "La limite la plus importante : db-postgres porte les commandes ET le registre d'outputs.\n"
            "Sans sauvegarde scriptée, le RPO n'est pas borné — c'est écrit, pas supposé.",
            size=13, color=INK, spacing=1.15)
    footer(s, 13, group)
    notes(s, """
La gouvernance n'est pas un document à part : chaque ligne renvoie à un
composant du démonstrateur.

EBIOS : sept scénarios, dont celui qui nous intéresse le plus, la panne
silencieuse — c'est le risque le plus probable, et il n'a rien à voir avec un
attaquant.

PCA/PRA : 15 secondes de RTO sur le paiement, contre 7 secondes mesurées. La
marge couvre le cas où la remédiation échoue et où l'exploitant redémarre.

Et la vraie faiblesse de continuité : PostgreSQL porte les commandes et le
registre d'outputs dans la même base, sans pg_dump scripté. Le RPO n'est pas
borné. C'est écrit noir sur blanc.
""")

    # 14 — Limites --------------------------------------------------------
    s = slide(prs)
    title(s, "Ce qui reste ouvert, et pourquoi c'est acceptable", "Limites")
    rows = [
        ["Limite", "Effet", "Pourquoi c'est acceptable"],
        ["Pas de Bulletproofs", "montant négatif modulo l", "exige un attaquant authentifié qui forge"],
        ["Pas de vivacité", "une photo passe le facteur visage", "le mot de passe reste exigé"],
        ["Pas de TLS", "trafic local lisible", "démo sur localhost ; la caméra l'exige"],
        ["AppRole Vault", "le playbook utilise un jeton", "pas de privilège root"],
        ["PostgreSQL mono-nœud", "pas de bascule", "démonstration sur un seul poste"],
        ["WAF non générique", "pas de couverture OWASP", "validation Pydantic par service"],
    ]
    table(s, rows, 0.9, 1.9, 11.5, 3.6, col_widths=[3.3, 4.1, 4.1], size=11.5)
    textbox(s, 0.9, 5.75, 11.5, 0.9,
            "Une limite écrite et datée ne coûte pas de point. Une limite découverte en Q&A en coûte.",
            size=14, italic=True, color=GREY)
    footer(s, 14, group)
    notes(s, """
Cette diapositive est volontairement inconfortable : c'est celle qui nous
protège en Q&A.

Si on nous demande « votre système est-il sûr ? », la réponse est : 12 failles
fermées, et voici les dix limites qui restent. Bulletproofs, vivacité, TLS,
AppRole, mono-nœud. Chacune a une raison et une échéance.

Le pire scénario, c'est que le jury découvre une limite que nous n'avons pas
écrite. Le meilleur, c'est qu'il en cite une et que nous puissions dire : oui,
page 9 du dossier, voilà pourquoi.
""")

    # 15 — Déroulé --------------------------------------------------------
    s = slide(prs)
    title(s, "La démonstration, seconde par seconde", "Live")
    timeline = [
        ("0-18 s", "Auth", "identifiant → visage → mot de passe", GREEN),
        ("18-38 s", "Boutique", "catalogue, panier, paiement, ticket", INK),
        ("38-42 s", "Attaque", "poison depuis le PC Attaquant", RED),
        ("42-52 s", "Remédiation", "badge, score, journal Ansible", AMBER),
        ("52-57 s", "Reprise", "paiement de preuve avec le PIN", GREEN),
        ("57-60 s", "Déception", "l'attaquant tape dans le vide", GREY),
    ]
    for i, (moment, head, body, color) in enumerate(timeline):
        x = 0.55 + i * 2.12
        card(s, x, 1.95, 1.95, 1.75, head, body, color=color, fill=WHITE)
        textbox(s, x, 3.78, 1.95, 0.3, moment, size=10, bold=True, color=color)
    textbox(s, 0.9, 4.45, 11.6, 2.2,
            "Deux règles : personne ne touche au clavier entre 38 et 52 s,\n"
            "et le paiement de 52 s est présenté comme un client — pas comme un administrateur.\n\n"
            "Si un plan tombe : on garde le plan large et on commente le badge, jamais on ne clique.",
            size=14, color=INK, spacing=1.2)
    footer(s, 15, group)
    notes(s, """
Voilà le déroulé. Le chronométreur annonce 18 secondes, 38 secondes, 57.

De 38 à 52, personne ne touche à rien. C'est la règle absolue : une intervention
humaine pendant la remédiation donne zéro sur l'axe 1.

Le geste de 52 secondes est le cœur : on retape un paiement avec le PIN du jour,
et on dit « un client peut de nouveau payer ». Ce n'est pas une action
d'administration.

Si un plan tombe — le badge ne bouge pas, le journal ne défile pas — on garde le
plan large, on commente ce qui est affiché, et on ne clique pas. Le clip de
secours existe pour ça.
""")

    # 16 — Conclusion -----------------------------------------------------
    s = slide(prs)
    band = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, W, H)
    band.fill.solid(); band.fill.fore_color.rgb = INK; band.line.fill.background(); band.shadow.inherit = False
    textbox(s, 0.9, 1.2, 11.5, 0.4, "EN UNE PHRASE", size=12, bold=True, color=RECT)
    textbox(s, 0.9, 1.75, 11.5, 2.0,
            "Un système qui échoue bruyamment\nvaut mieux qu'un système\nqui réussit en silence.",
            size=30, bold=True, color=WHITE, spacing=1.2)
    for i, (value, label) in enumerate(
        [("7 s", "de reprise"), ("221", "tests verts"), ("12", "failles fermées")]
    ):
        textbox(s, 0.9 + i * 3.6, 4.5, 3.2, 0.7, value, size=30, bold=True, color=RECT)
        textbox(s, 0.9 + i * 3.6, 5.2, 3.2, 0.4, label, size=11, color=WHITE)
    textbox(s, 0.9, 6.1, 11.5, 0.5, "Merci — questions ?", size=18, bold=True, color=WHITE)
    notes(s, """
Pour finir : la leçon d'ingénierie du projet n'est pas « nous avons résisté à
l'attaque ». C'est que sur tous les défauts corrigés, aucun n'était une attaque
réussie : c'étaient des pannes silencieuses. Une image sans modèle, un webhook
cassé, un verrou jamais réarmé, une alerte qui visait le mauvais service.

D'où la phrase : un système qui échoue bruyamment vaut mieux qu'un système qui
réussit en silence.

Trois chiffres à retenir : 7 secondes de reprise, 226 tests, 12 failles fermées.
Merci. Nous prenons vos questions.
""")

    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    print(f"écrit : {out} ({len(prs.slides.__iter__.__self__._sldIdLst)} diapositives)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="1")
    parser.add_argument("--out", default=str(ROOT / "docs" / "PRESENTATION-SOUTENANCE.pptx"))
    args = parser.parse_args()
    build(args.group, pathlib.Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
