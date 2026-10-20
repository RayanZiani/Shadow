// =====================================================================
//  socle_terminal.scad — Socle de scène pour terminal de paiement
//  RESILIENCE-ALPHA / SHADOW-SETTLE
//
//  Rôle : poser le terminal incliné vers la caméra et porter un bandeau de
//  LEDs visible à distance pour le Clip Chrono.
//
//  Ce fichier n'imprime PAS le terminal : il imprime le décor autour.
//  Voir docs/hardware/CONCEPT-PHYSIQUE.md §7.
//
//  UTILISATION
//    openscad socle_terminal.scad -D 'part="socle"'      -o socle.stl
//    openscad socle_terminal.scad -D 'part="bandeau"'    -o bandeau.stl
//    openscad socle_terminal.scad -D 'part="diffuseur"'  -o diffuseur.stl
//    openscad socle_terminal.scad -D 'part="plaque"'     -o plaque.stl
//    openscad socle_terminal.scad                        (assemblage, ne pas imprimer)
//
//  ÉTAT : v1 paramétrique, NON imprimée. Vérifier l'aperçu 3D et les cotes
//  du terminal avant de lancer une impression de 4 heures.
//
//  À MESURER AVANT TOUT (pied à coulisse) :
//    terminal_l, terminal_w, terminal_h
//
//  TOLÉRANCES : 0,25 mm sur les assemblages FDM. Ne pas descendre en dessous.
// =====================================================================

part = "assemblage";   // "socle" | "bandeau" | "diffuseur" | "plaque" | "assemblage"

// ---------- 1. LE TERMINAL (à mesurer) ----------
terminal_l       = 150;   // longueur (côté clavier)
terminal_w       = 95;    // profondeur
terminal_h       = 30;    // épaisseur au point le plus épais
terminal_incline = 15;    // degrés — au-delà de 25° le terminal glisse

// ---------- 2. LE SOCLE ----------
base_margin    = 14;   // débord du socle autour du terminal
front_lip_h    = 20;   // hauteur de la butée avant
front_lip_w    = 16;   // largeur de la butée avant
wall           = 4;
cable_notch_w  = 30;   // passage de câbles à l'arrière
cable_notch_h  = 18;

// ---------- 3. LE BANDEAU LED ----------
led_count    = 24;
led_strip_w  = 12;   // ruban WS2812 nu : 12 mm
led_strip_t  = 3;    // ruban + adhésif
band_h       = 34;
band_len     = 170;  // doit rester <= base_l
mast_h       = 100;  // hauteur du montant au-dessus du socle
pocket_depth = wall; // profondeur du logement du bandeau dans le montant

// ---------- 4. TOLÉRANCES ----------
fit = 0.25;

// ---------- dérivés ----------
base_l     = terminal_l + 2 * base_margin;
base_w     = terminal_w + 2 * base_margin;
band_len_eff = min(band_len, base_l - 2 * wall);
band_depth   = led_strip_w + 2 * wall;
slope_h      = tan(terminal_incline) * terminal_w;
rear_h       = front_lip_h + slope_h;

$fn = 64;

// =====================================================================
//  SOCLE : coin incliné + montant arrière percé d'un logement
// =====================================================================
module socle() {
    difference() {
        union() {
            // butée avant (empêche le terminal de glisser vers la caméra)
            translate([0, -base_w / 2 + front_lip_w / 2, 0])
                rounded_box(base_l, front_lip_w, front_lip_h, r = 3);

            // coin incliné : hull de la butée avant vers le bloc arrière
            hull() {
                translate([0, -base_w / 2 + front_lip_w / 2, 0])
                    rounded_box(base_l, front_lip_w, front_lip_h, r = 3);
                translate([0, base_w / 2 - wall, 0])
                    rounded_box(base_l, 2 * wall, rear_h, r = 3);
            }

            // montant qui porte le bandeau
            translate([0, base_w / 2 - wall / 2, 0])
                rounded_box(band_len_eff + 2 * wall, wall, rear_h + mast_h, r = 3);
        }

        // passage de câbles à l'arrière, au niveau du plan incliné
        translate([0, base_w / 2, rear_h - cable_notch_h / 2 - 4])
            cube([cable_notch_w, wall + 2, cable_notch_h], center = true);

        // logement du bandeau dans la face avant du montant
        translate([
            0,
            base_w / 2 - wall + pocket_depth / 2,
            rear_h + mast_h - band_h / 2
        ])
            cube([band_len_eff + fit, pocket_depth + 1, band_h + fit], center = true);

        // deux avant-trous M3 pour vis à bois (pas d'insert thermique)
        for (sx = [-1, 1])
            translate([
                sx * band_len_eff * 0.38,
                base_w / 2 - wall + 1,
                rear_h + mast_h - band_h / 2
            ])
                rotate([90, 0, 0])
                    cylinder(h = wall, d = 2.5, center = true);
    }
}

// =====================================================================
//  BANDEAU : support du ruban, glissé dans le logement du montant
// =====================================================================
module bandeau() {
    difference() {
        // corps : profondeur = 3 mm dans le logement + la gorge + la paroi
        rounded_box(band_len_eff, band_depth, band_h, r = 3);

        // gorge du ruban, ouverte côté terminal
        translate([0, 0, 3])
            cube([band_len_eff - 2 * wall, led_strip_w + fit, band_h], center = true);

        // fentes de diffusion : une par LED, évite les points chauds
        for (i = [0 : led_count - 1]) {
            x = -band_len_eff / 2 + (i + 0.5) * band_len_eff / led_count;
            translate([x, 0, band_h / 2])
                cube([band_len_eff / led_count * 0.5, led_strip_w + 4, band_h * 0.55],
                     center = true);
        }
    }
}

// =====================================================================
//  DIFFUSEUR : PETG TRANSLUCIDE, 2 périmètres, 0 % de remplissage.
//  En PLA, les LEDs font des points chauds visibles à la caméra.
// =====================================================================
module diffuseur() {
    union() {
        rounded_box(band_len_eff, 2.0, band_h, r = 2);
        // nervures horizontales : homogénéisent la lumière sans coût d'impression
        for (i = [1 : 6])
            translate([0, -1.2, i * band_h / 7])
                cube([band_len_eff - 6, 0.8, 0.8], center = true);
    }
}

// =====================================================================
//  PLAQUE : signalétique, à coller sur le socle
// =====================================================================
module plaque() {
    difference() {
        rounded_box(base_l * 0.5, 22, 3, r = 3);
        translate([0, 0, 1.5])
            linear_extrude(2)
                text("RESILIENCE-ALPHA", size = 8,
                     font = "Montserrat:style=Bold",
                     halign = "center", valign = "center");
    }
}

// =====================================================================
//  ASSEMBLAGE : contrôle visuel uniquement
// =====================================================================
module assemblage() {
    color("#A1A6D4") socle();

    translate([0, base_w / 2 - wall + pocket_depth / 2 - 0.5,
               rear_h + mast_h - band_h / 2])
        color("#261E48") bandeau();

    translate([0, base_w / 2 - wall + pocket_depth / 2 - 1.6,
               rear_h + mast_h - band_h / 2])
        color("#E4E6F3", 0.6) diffuseur();

    translate([0, -base_w / 2 + front_lip_w + 4, 0.1])
        color("#261E48") plaque();

    // volume du terminal : vérifie que l'inclinaison et la butée sont cohérentes
    translate([0, (front_lip_w / 2) + (terminal_w / 2) * cos(terminal_incline) - base_w / 2,
               front_lip_h])
        rotate([-terminal_incline, 0, 0])
            translate([0, 0, terminal_h / 2])
                color("#ffffff", 0.35)
                    cube([terminal_l, terminal_w, terminal_h], center = true);
}

// ---------- utilitaire ----------
module rounded_box(l, w, h, r = 2) {
    rr = min(r, min(l, w) / 2 - 0.01);
    hull()
        for (x = [-1, 1], y = [-1, 1])
            translate([x * (l / 2 - rr), y * (w / 2 - rr), 0])
                cylinder(h = h, r = rr);
}

// ---------- dispatch ----------
if (part == "socle") socle();
else if (part == "bandeau") bandeau();
else if (part == "diffuseur") diffuseur();
else if (part == "plaque") plaque();
else assemblage();
