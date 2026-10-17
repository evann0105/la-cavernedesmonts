# PostgreSQL et données personnelles

Cette évolution nécessite la migration contrôlée `./migrate-postgres`. Ne jamais appliquer le changement de modèle utilisateur directement sur l’ancienne SQLite : le transfert s’effectue vers une base PostgreSQL vide et conserve les identifiants et les mots de passe hachés.

## Protection mise en place après migration réussie

- PostgreSQL dédié sur `127.0.0.1:55432`, hors Git. Le partage du site avec `--lan` ne partage pas PostgreSQL.
- Django utilise un rôle sans droits de création de bases, tables ou rôles ; maintenance par socket Unix privée avec authentification locale.
- TCP exige TLS, et Django vérifie le certificat et l’adresse du serveur (`verify-full`). Le certificat local expire après 825 jours : renouveler avant expiration.
- Chiffrement authentifié Fernet via la bibliothèque `cryptography`, au niveau des champs Django : nom, prénom, e-mail, adresses sauvegardées, e-mail vérifié, coordonnées de livraison et e-mail de commande, motif des avoirs.
- Index HMAC de l’e-mail pour permettre la connexion et la détection de doublons. Cet index révèle l’égalité des adresses, pas leur texte. Les recherches partielles dans les champs chiffrés sont retirées.
- Les mots de passe restent hachés avec le mécanisme Django. Les données publiques du catalogue, identifiants techniques, noms de connexion, autorisations, montants et statuts ne sont pas chiffrés par les champs applicatifs. Les tables de sessions/journaux et extensions ne bénéficient pas d’un chiffrement intégral par ce mécanisme.
- Sauvegarde `pg_dump` chiffrée avant chaque démarrage ; première restauration testée dans une base temporaire avant bascule.

## Clés et limites

Les fichiers privés résident dans le dossier voisin `la-cavernedesmonts-data/postgres-private`, accessible uniquement au compte macOS : `data.key` pour les champs, `backup.key` pour les archives, certificat et identifiants locaux. Aucun n’est publié dans Git. Conserver une copie indépendante des deux clés dans un gestionnaire de secrets et des sauvegardes chiffrées sur un autre support. Perdre les clés rend les données irrécupérables. Ne pas les remplacer pour corriger une erreur de lecture ; organiser une rotation par déchiffrement/réchiffrement contrôlé.

Le chiffrement applicatif ne protège pas contre un attaquant contrôlant l’application ou le compte macOS qui peut lire les clés. Il ne chiffre pas tous les fichiers PostgreSQL, WAL, swap, photos ni anciens fichiers SQLite. FileVault (ou un volume chiffré) reste requis pour la protection complète du disque. L’ancienne SQLite et ses sauvegardes sont conservées pour retour arrière ; ne les supprimer qu’après validation et archivage chiffré.

L’accès navigateur au serveur de développement reste HTTP : TLS PostgreSQL ne chiffre pas navigateur → Django. En production, utiliser HTTPS, un hébergement et des sauvegardes chiffrés, un gestionnaire de secrets, un rôle de migration séparé, les mises à jour de sécurité PostgreSQL, puis vérifier la restauration. PostgreSQL 15.13 était installé sur ce Mac lors de la préparation : mettre la branche à jour avant exposition en production.

## Commandes après bascule

- `./start` : démarre PostgreSQL si nécessaire, sauvegarde, migre avec le rôle de maintenance et lance le site avec le rôle limité.
- `./start --lan` : même protection de base ; seul le site est accessible sur le réseau local.
- `.venv/bin/python scripts/postgres_local.py` : nouvelle sauvegarde chiffrée.
- Tests PostgreSQL : `DB_MAINTENANCE=1 .venv/bin/python manage.py test --noinput` (base de test dédiée créée puis supprimée).

La maintenance locale utilise le socket Unix protégé, sans transmission TCP. Pour un hébergement externe, `DATABASE_URL` et `DATABASE_SSL_ROOT_CERT` sont pris en charge par Django, mais le lanceur local n’est pas un outil de déploiement de production.


## Réservations et paiements

Les variantes comptées sont obligatoires pour payer. Le stock physique (`quantity`)
comprend les quantités réservées (`reserved`). Un panier ne réserve rien ; le départ
vers Stripe réserve pour une session de 35 minutes. La confirmation signée puis
vérifiée auprès de Stripe décompte le stock une fois. Seule une expiration confirmée
libère une réservation ; le retour au panier ne suffit pas. Les erreurs réseau
ambiguës gardent la réservation, avec reprise par la même clé d’idempotence.

`./start` démarre également la vérification des réservations échues chaque minute.
En hébergement, planifier `python manage.py reconcile_reservations` chaque minute
avec les mêmes secrets et surveiller ses échecs. Configurer les événements Stripe
`checkout.session.completed`, `checkout.session.async_payment_succeeded` et
`checkout.session.expired`. Ne pas activer la récupération automatique des sessions
expirées côté Stripe : elle contournerait la nouvelle réservation nécessaire.

Si une tentative sans identifiant Stripe reste indéterminée, le stock est conservé.
Après 23 heures, aucune nouvelle création Stripe n’est tentée avec cette clé :
vérifier manuellement la tentative chez Stripe avant toute correction de stock.
Les anciennes commandes sans variantes sont conservées ; les anciens paniers
doivent choisir une variante pour payer. Les ventes en magasin et les retours
restent à saisir manuellement. Les clés Stripe sont absentes en local : les tests
automatisés simulent le prestataire ; un essai Stripe en mode test reste obligatoire
avant d’activer les paiements réels.
