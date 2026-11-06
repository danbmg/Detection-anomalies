# La nouvelle méthode d'alertes

Les alertes se déclenchent toujours après `--confirm` points consécutifs hors de la bande, avec un seul incident par alerte. Ce qui change, c'est que chaque alerte a maintenant un type : `spike`, `drop` ou `drift`.

- **Saisonnière :** `seasonal` remplace la moyenne de la dernière heure par la médiane de la même heure les jours précédents. Sur le jeu simulé, ça passe de 43 alertes (rolling) à 4, toutes sur de vraies anomalies.
- **CUSUM :** deux sommes cumulées, une par côté, ajoutent le z-score de chaque point moins une marge de 0,5, sans jamais descendre sous 0. Le bruit s'annule. Une dérive s'additionne jusqu'à dépasser 15, et ça lève une alerte `drift`. Un z-score seul ne la voit pas, car la bande suit la dérive.
- **k adaptatif :** si plus de 10 % des points récents sont au-delà de 1,645 écart-type, la bande est trop étroite et k augmente. Sinon k baisse.
