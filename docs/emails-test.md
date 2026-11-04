# E-mails de test

Le mode local est activé par `EMAIL_TEST_MODE=True` dans `.env`, uniquement lorsque `DEBUG=True`. Il remplace le transport SMTP par une capture sur disque : même une adresse réelle ne reçoit rien. Aucun compte Gmail, mot de passe d’application ou compte professionnel n’est nécessaire. Redémarrer `./start` après tout changement de configuration.

Dans Espace vendeuse → Tous les outils → Boîte e-mail de test
(`/gestion/emails-test/`), les gestionnaires autorisés voient les 50 derniers
messages et leur aperçu HTML. L’aperçu reste isolé par une politique de sécurité.
Pour les liens de confirmation appartenant au site, la boîte propose aussi
« Ouvrir le lien de confirmation » : se connecter au compte destinataire, ouvrir
ce lien, puis confirmer son adresse. Pour la récupération d’accès, le bouton
« Choisir un nouveau mot de passe » ouvre le formulaire protégé. Une simple
visite ne confirme aucune adresse et ne change aucun mot de passe. Seules ces
deux routes de sécurité de la même origine sont autorisées comme actions ; les
liens externes restent du texte. La boîte est inaccessible aux clients et
désactivée hors développement.

Les messages sont stockés dans `.emails-test/`, exclu de Git, avec des permissions privées. Ils peuvent contenir des liens de confirmation : ne pas publier ce dossier ni le déployer. Supprimer les fichiers de test lorsqu’ils ne sont plus utiles.

Pour recréer les exemples de modèles :

```sh
.venv/bin/python manage.py test_emails
```

Cette commande impose une capture locale indépendamment du backend configuré. Les exemples utilisent `example.test`, sont marqués TEST et ne créent ni clients ni commandes. Leurs liens sont volontairement fictifs. Les fichiers `*-apercu.html` permettent aussi de consulter les modèles hors du site.

## Ce qui est relié au site

La confirmation d’adresse utilise le modèle HTML et sa version texte. Son expiration, sa limitation d’envoi et son lien à usage unique sont conservés. Le contournement local de la vérification des comptes reste indépendant : s’il est activé, créer un compte ne déclenche pas automatiquement de message de confirmation.
En revanche, demander volontairement une confirmation depuis Mes avoirs ou la
page de vérification génère bien le message, même avec ce raccourci local.
Les pages précisent qu’une capture locale n’arrive pas dans une boîte
personnelle. Le transport reste commun aux autres messages du site ; aucun
SMTP professionnel n’est activé par cette modification.

Le parcours « Mot de passe oublié ? » utilise aussi cette capture. Les demandes
sont préparées par le worker `reconcile_reservations`, sans attendre SMTP pendant
le formulaire. Attendre environ une minute, puis consulter la boîte de test avec
un gestionnaire autorisé. La réponse publique reste générique même si aucune
adresse ne correspond ; une capture n’est créée que pour un compte récupérable.
Voir [le guide de récupération](MOT_DE_PASSE_OUBLIE.md).

## Autres modèles et parcours

Les confirmations de commande, expéditions et remboursements suivent leurs
événements confirmés dans les parcours métier. Le modèle de contact reste à
raccorder si ce formulaire doit produire un accusé de réception. Ne jamais
envoyer une confirmation de paiement simplement après un retour navigateur.

Les modèles sont actuellement en français, en HTML et texte brut. Ils n’ont pas été testés dans Gmail/Outlook réels : la boîte locale vérifie la génération et la capture, pas la délivrabilité ni le rendu propre à chaque logiciel.

### Essai visuel du 17 octobre 2026

Un unique exemple HTML/texte de rétractation a été envoyé, à la demande explicite du propriétaire, depuis son compte Gmail connecté vers sa propre adresse. Il contient son nom/prénom et un article fictif, sans détail ni montant de commande, adresse de retour ou lien signé. Le contrôle automatique avait refusé la première version contenant ces données et le lien d’accès privé ; cette version complète n’a pas été envoyée. L’exemple visuel a été accepté par Gmail. Cela ne configure ni ne valide le transport de production du site : la configuration locale reste en capture. Le rendu dans Gmail et les autres clients doit encore être apprécié par le destinataire.

## Mise en production

Désactiver `EMAIL_TEST_MODE`, choisir le fournisseur SMTP/API, configurer l’expéditeur et les secrets hors du dépôt, activer `EMAIL_DELIVERY_ENABLED` et vérifier le domaine d’envoi et les liens HTTPS. Tester alors la réception sur de vraies boîtes. Une adresse professionnelle seule ne configure pas l’envoi.
