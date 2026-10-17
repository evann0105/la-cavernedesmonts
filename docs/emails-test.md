# E-mails de test

Le mode local est activé par `EMAIL_TEST_MODE=True` dans `.env`, uniquement lorsque `DEBUG=True`. Il remplace le transport SMTP par une capture sur disque : même une adresse réelle ne reçoit rien. Aucun compte Gmail, mot de passe d’application ou compte professionnel n’est nécessaire. Redémarrer `./start` après tout changement de configuration.

Dans Mon compte → Administration → Boîte e-mail de test (`/gestion/emails-test/`), les gestionnaires autorisés voient les 50 derniers messages et leur aperçu HTML. L’aperçu est isolé par une politique de sécurité ; pour essayer un lien de confirmation réel, copier le lien depuis le texte de la boîte, puis l’ouvrir avec le compte destinataire. La boîte est inaccessible aux clients et désactivée hors développement.

Les messages sont stockés dans `.emails-test/`, exclu de Git, avec des permissions privées. Ils peuvent contenir des liens de confirmation : ne pas publier ce dossier ni le déployer. Supprimer les fichiers de test lorsqu’ils ne sont plus utiles.

Pour recréer les six exemples :

```sh
.venv/bin/python manage.py test_emails
```

Cette commande impose une capture locale indépendamment du backend configuré. Les exemples utilisent `example.test`, sont marqués TEST et ne créent ni clients ni commandes. Leurs liens sont volontairement fictifs. Les fichiers `*-apercu.html` permettent aussi de consulter les modèles hors du site.

## Ce qui est relié au site

La confirmation d’adresse utilise le modèle HTML et sa version texte. Son expiration, sa limitation d’envoi et son lien à usage unique sont conservés. Le contournement local de la vérification des comptes reste indépendant : s’il est activé, créer un compte ne déclenche pas automatiquement de message de confirmation.

## Modèles préparés, sans automatisation ajoutée

Réinitialisation du mot de passe, confirmation de commande, expédition, remboursement et accusé de réception du formulaire de contact. Leur contenu doit être alimenté par les événements réellement confirmés, avec les vrais montants, références et liens. Ne jamais envoyer une confirmation de paiement simplement après un retour navigateur ; gérer aussi les nouvelles tentatives et doublons avant activation.

Les modèles sont actuellement en français, en HTML et texte brut. Ils n’ont pas été testés dans Gmail/Outlook réels : la boîte locale vérifie la génération et la capture, pas la délivrabilité ni le rendu propre à chaque logiciel.

### Essai visuel du 17 octobre 2026

Un unique exemple HTML/texte de rétractation a été envoyé, à la demande explicite du propriétaire, depuis son compte Gmail connecté vers sa propre adresse. Il contient son nom/prénom et un article fictif, sans détail ni montant de commande, adresse de retour ou lien signé. Le contrôle automatique avait refusé la première version contenant ces données et le lien d’accès privé ; cette version complète n’a pas été envoyée. L’exemple visuel a été accepté par Gmail. Cela ne configure ni ne valide le transport de production du site : la configuration locale reste en capture. Le rendu dans Gmail et les autres clients doit encore être apprécié par le destinataire.

## Mise en production

Désactiver `EMAIL_TEST_MODE`, choisir le fournisseur SMTP/API, configurer l’expéditeur et les secrets hors du dépôt, activer `EMAIL_DELIVERY_ENABLED` et vérifier le domaine d’envoi et les liens HTTPS. Tester alors la réception sur de vraies boîtes. Une adresse professionnelle seule ne configure pas l’envoi.
