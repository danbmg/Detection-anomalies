# Démo : la simulation en direct

## Lancer

```bash
source .venv/bin/activate
python -m anomaly simulate
python -m anomaly watch live data/traffic.csv --method seasonal
```

- `simulate` fabrique 4 semaines de trafic dans `data/traffic.csv`, sans les mettre dans l'historique.
- `watch` relit ce fichier comme si les points arrivaient en direct. Chaque point est enregistré dans l'historique sous le nom `live`, puis jugé. Une alerte s'affiche dès qu'elle est levée, pendant que le flux continue.
- Une journée de données passe en une seconde : les 4 semaines durent 28 secondes. Une ligne par jour montre que le flux avance.
- Les 4 alertes arrivent vers 10 s (pic), 12 s (pic), 17 s (chute) et 23 s (hausse lente).
- Ctrl+C arrête le flux. Les points déjà reçus restent enregistrés.

## Changer la vitesse

| Option | Effet |
|---|---|
| `--speed 86400` | 1 jour de données par seconde (par défaut) : 28 s |
| `--speed 3600` | 1 heure par seconde : environ 11 minutes |
| `--speed 0` | sans attente, tout d'un coup |

## Avant / après en direct

```bash
python -m anomaly watch avant data/traffic.csv
```

Même flux, avec la première méthode (l'heure qui précède) : des alertes tombent presque chaque nuit à 2h, 43 en tout.

## Vérifier que c'est le même résultat que sur tout l'historique

```bash
python -m anomaly alert live --method seasonal
```

Donne les 4 mêmes alertes : le direct et le calcul d'un coup utilisent le même code, point après point.

## Relancer la démo

Relancer `watch live` ne fait rien : ces points sont déjà dans l'historique, il n'y a rien de nouveau. Pour recommencer :

- changer de nom : `python -m anomaly watch live2 data/traffic.csv --method seasonal`
- ou utiliser une base à part, jetable : ajouter `--db /tmp/demo.db`

---

# Démo : tester chaque fonctionnalité

Toutes les commandes ont été lancées et donnent les résultats indiqués (série de la graine 42).
Pour repartir de zéro : `rm data/history.db data/alerts.log`.

## 1. Préparer les données

```bash
source .venv/bin/activate
python -m anomaly simulate --store traffic     # série + 3 anomalies connues, stockée sous "traffic"
python -m anomaly show traffic --from 2026-01-14T13:50 --to 2026-01-14T14:40
```

Les anomalies injectées : un pic le 17/01 à 03:30, une chute le 21/01 à 10:00, une dérive (+50 %) à partir du 27/01.
Le même `--seed` redonne toujours la même série.

## 2. Cycle 1 : bande normale, z-score, alertes

```bash
python -m anomaly show traffic --band --from 2026-01-14T13:45 --to 2026-01-14T14:45   # bande : moyenne ± 3 écarts-types
python -m anomaly detect traffic                  # points hors bande + résumé
python -m anomaly alert traffic                   # 43 alertes (méthode rolling), 6 seulement sur une vraie anomalie
python -m anomaly plot traffic                    # -> data/traffic.png
```

Le problème visible : une alerte chaque nuit à 02:00. C'est le batch nocturne, normal, mais la moyenne de la dernière heure ne le connaît pas.

## 3. Cycle 2 : méthode saisonnière

```bash
python -m anomaly alert traffic --method seasonal      # 4 alertes, les 4 sur une vraie anomalie
python -m anomaly plot traffic --method seasonal --out data/seasonal.png
```

Le pic de 02:00 n'alerte plus, les anomalies injectées alertent toujours.
Compare `data/traffic.png` et `data/seasonal.png`.

## 4. Dérive lente (CUSUM)

Dans la sortie de la commande précédente, la dernière alerte est de type `drift` (le 27/01 vers 10:15, ~10 h après le début).
Elle affiche une somme `cusum` et non un z-score. Le rolling ne la voit jamais : sa moyenne suit la dérive.

## 5. Sensibilité adaptative

```bash
python -m anomaly alert traffic --method seasonal --adaptive   # chaque alerte affiche le k utilisé
```

Résultat : 5 alertes dont 1 fausse, et un `k` qui bouge (3.10, 2.50...).
Pour le voir utile, il faut une série plus bruyante, voir le README, section « Adaptive sensitivity ».

## 6. Seuil fixe (la référence)

```bash
python -m anomaly detect traffic --method fixed
```

Il ne sonne pas sur le batch de 02:00, mais seulement parce qu'il est placé au-dessus.
Il rate le pic de nuit et la chute, et ne voit la dérive que sur ses pics, un jour plus tard.

## 7. Évaluation : faux positifs / faux négatifs

```bash
python -m anomaly evaluate               # 1 série, toutes les méthodes côte à côte
python -m anomaly evaluate --runs 10     # 10 séries additionnées (plus solide)
```

Pour une série : `seasonal` fait 4 alertes, 0 fausse, 0 manquée (précision 100 %, rappel 100 %).
Le seuil fixe garde 100 % de précision mais 50 % de rappel ; `rolling` rattrape le rappel mais avec 19 % de précision.

## 8. Données réelles (NAB)

```bash
make nab                                                # une fois ; les fichiers sont déjà dans data/nab/
python -m anomaly import nyc_taxi data/nab/nyc_taxi.csv --labels data/nab/combined_windows.json
python -m anomaly evaluate --metric nyc_taxi
python -m anomaly plot nyc_taxi --method seasonal
```

À dire à l'oral : sur du réel, beaucoup plus de fausses alertes (97 pour `seasonal` sur les taxis), et aucune méthode ne gagne partout.
Sur la température machine (pas de cycle journalier), c'est le seuil fixe qui s'en sort le mieux.

## 9. Vérifier le code

```bash
make check     # ruff + mypy + pytest
```
