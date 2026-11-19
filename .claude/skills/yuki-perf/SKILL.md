---
name: yuki-perf
description: Mesurer et optimiser le coût CPU de Yuki (simulation, rendu Qt, et charge induite dans KWin). À utiliser avant de toucher à TILE, au rendu partiel, aux flocons ou à la boucle d'animation, ou quand Yuki ou kwin_wayland consomme trop.
---

# Performance de Yuki

## Mesurer

- Banc headless, sans toucher à la session : `python3 tools/bench.py [--season printemps] [--profile] [--dump /chemin/prefixe] [--extras rain,aurora,…|all]`.
  Il reproduit les deux écrans de Roland (2560x1440 à 100 %, 1707x1067 à 150 %) et le rendu partiel de `SnowView`.
  `--dump` écrit les images finales : comparer deux versions en **prémultiplié**
  (en ARGB non prémultiplié, un pixel d'alpha 1 contre 0 donne un faux écart de 255).
- cProfile gonfle les temps d'environ 2x : ne s'en servir que pour classer, et chronométrer sans.
- Process réel : `perf record -F 499 -t <pid> -- sleep 6` puis `perf report --sort dso,sym`.
  `perf_event_paranoid=2` : pas de piles d'appels (`-g` refusé), seulement les symboles à plat.
- CPU réel de Yuki et de KWin : différence de `/proc/<pid>/stat` (champs 14+15, en centièmes de s) sur 10 s.
  Pour la part de KWin due à Yuki : `kill -STOP` Yuki pendant la mesure (la neige reste figée), puis `kill -CONT`.
  Les réglages du menu (`~/.config/yuki/yuki.conf`) changent la densité réelle : en tenir compte.
- Relancer pour mesurer le code en cours : `~/.local/bin/yuki-restart`, attendre ~15 s avant de mesurer.

## Ce qu'on sait (novembre 2026, Plasma 6, Qt 6.11, 60 fps, density 1)

- Le clip ne réduit pas le coût du rasteriseur de Qt : un chemin pleine largeur coûte le même prix même si 2 tuiles sont visibles
  (`gray_record_cell` à 68 % du CPU avant `_pile_spans`). D'où les tas construits seulement sur les colonnes abîmées,
  avec 2 colonnes de marge pour que le contour soit identique au pixel près dans la zone clippée.
- KWin paie surtout **par rectangle** abîmé, pas par pixel : TILE=16 a fait monter KWin de 32 à 44 % malgré 2,7x moins de pixels.
  TILE=48/64 : KWin à peine moins cher, Yuki plus cher. Garder 32.
  Les ~220 rectangles par vue sont surtout des flocons isolés : impossible de les fusionner sans gaspiller beaucoup de surface.
- Yuki induit environ 20 à 25 points de CPU dans KWin, qui varient avec le nombre d'images par seconde. D'où `Sim.lively()` + `--fps-calm` :
  on ne garde toutes les images par seconde que si quelque chose de rapide bouge. N'y ajouter que ce qui saccade vraiment à 30 images/s.
- Ce que coûtent les nouveautés (banc, density 1) : aurore +1,1 ms, givre +1,3, lucioles +0,8, luge et oiseau ~0, averse +4,5
  (les éclaboussures font redessiner les tas : d'où leur plafond, et la pluie volontairement absente de `lively()`).
- Grand et lent (aurore, givre) : pré-rendre dans une QImage en cache, la recopier clippée. La rasteriser à chaque image coûterait cher, même clippée.
- Ce qui reste dans Yuki, c'est surtout l'interpréteur Python (boucles par flocon dans `step`, `_collect_item_tiles`, `_paint_round_flakes`).
  Prochain levier : vectoriser les flocons avec numpy (nouvelle dépendance sur les trois plateformes, à décider avec Roland).
- Un rendu GPU (QOpenGLWindow) annoncerait toute la surface comme abîmée à chaque image : KWin recomposerait les deux écrans en entier.
  Il faudrait aussi régler le plantage à la recréation de surface (voir `view.py`) et la transparence sous Windows. Ce n'est pas rentable ici.
