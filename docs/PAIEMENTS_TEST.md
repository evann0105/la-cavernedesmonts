# Paiements fictifs Stripe

Le site d’essai est indépendant de la boutique habituelle. Il utilise sa propre base PostgreSQL, ses comptes, ses stocks, ses codes, ses factures et sa boîte e-mail locale. Aucun client ni commande de la boutique n’est recopié.

## Démarrer

Depuis le dossier du site, avec le cluster PostgreSQL local déjà démarré :

```sh
./test-payments
```

Ouvrir **http://127.0.0.1:8010/paiement/essais/**. La bande jaune « MODE TEST » identifie toutes les pages. Le site habituel reste sur le port 8000. Fermer le lanceur avec Ctrl+C arrête aussi l’écoute Stripe et le traitement des commandes de test.

Options : `--no-browser`, `--port 8010`, `--prepare-only`. Les relances conservent les essais précédents ; elles ne remettent pas les stocks ou les codes à zéro.

Les identifiants générés pour **evann-test** (administration) et **client-test** (client) se trouvent dans le fichier privé `accounts.json`, dans le dossier voisin `<nom-du-projet>-payments-test-data`. Ce dossier est extérieur au dépôt et réservé à l’utilisateur du Mac.

## Cartes et parcours

Utiliser exclusivement les cartes de test Stripe, une date future, par exemple 12/34, et le CVC 123.

| Cas | Carte |
| --- | --- |
| Paiement accepté | 4242 4242 4242 4242 |
| Paiement refusé | 4000 0000 0000 0002 |
| Authentification 3D Secure | 4000 0025 0000 3155 |

Les trois produits d’essai coûtent 80 €, 35 € et 110 €. La livraison Colissimo fictive coûte 6,90 €, offerte dès 99 € d’articles. Les codes personnels de **client-test** sont **TEST15** (15 %, minimum 70 €) et **TEST10** (10 €), chacun à usage unique. Le stock et les codes ne sont définitivement consommés qu’après validation du paiement auprès de Stripe. Une session abandonnée peut conserver temporairement sa réservation jusqu’à l’expiration confirmée par Stripe.

Vérifier successivement le montant du panier, la page Stripe, la commande payée, la facture fictive et les e-mails dans la boîte de test. Les pages de préparation, d’expédition et de remboursement utilisent les mêmes parcours que la boutique, avec les données séparées. Un paiement refusé ne doit pas devenir une commande payée. Les retours de navigateur ne remplacent jamais les confirmations signées de Stripe.

## Raccordement Stripe

L’environnement demande une clé Stripe de **test**, dans le fichier privé `stripe.json` : propriétés `secret_key` et `webhook_secret`. Ne pas les placer dans Git ni remplacer la configuration de la boutique. Les clés réelles sont refusées. Les réponses Stripe reçues pendant les essais doivent explicitement être fictives avant toute modification des commandes ou stocks.

Installer la CLI officielle Stripe puis indiquer son chemin avec `CAVERNE_STRIPE_CLI` lors du premier lancement. Le lanceur mémorise ce chemin dans sa configuration privée et transmet la clé par variable d’environnement, sans la mettre dans les arguments affichés. Il démarre l’écoute des notifications sur `/paiement/webhook/` et conserve son journal dans le dossier privé.

Un environnement temporaire créé par `stripe sandbox create` expire après sept jours : le revendiquer depuis le lien fourni par Stripe permet de le conserver. Ce lien et les clés restent privés. Le passage au compte de la propriétaire se fera séparément ; aucune clé ni vente réelle n’est activée par ce lanceur.

Documentation officielle : [environnements de test](https://docs.stripe.com/sandboxes), [cartes de test](https://docs.stripe.com/testing), [CLI et environnement temporaire](https://docs.stripe.com/cli/sandbox).
