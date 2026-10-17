# RGPD — registre et procédures de la boutique

Document interne préparé le 16 octobre 2026 à partir du code local de La Caverne des Monts. À tenir à jour par la gérante avec le prestataire de maintenance. Ce dossier décrit des mesures disponibles et des mesures à terminer ; il ne certifie ni l’exploitation réelle ni la conformité globale.

## Responsable et périmètre

Responsable de traitement : Élodie FOLOPPE, entrepreneur individuel, enseigne La Caverne des Monts, SIREN 525 114 914. Coordonnées publiques reprises des mentions légales. Le contact pour les droits est `contact@la-cavernedesmonts.fr` ; **Élodie doit confirmer qui surveille cette adresse et assure le remplacement en cas d’absence**. Aucun DPO n’est déclaré ici sans désignation réelle.

Périmètre : site e-commerce, clients et visiteurs, comptes de gestion habilités. La vente de vêtements pour bébés n’implique pas à elle seule un traitement de données de nourrissons : aucun compte bébé ni date de naissance n’est nécessaire au parcours actuel. Les champs libres peuvent néanmoins contenir accidentellement des informations inutiles ; les minimiser lors du traitement des demandes.

## Registre des activités

Les lignes suivantes forment un registre initial de traitements récurrents, à compléter avec les contrats et les lieux réels de traitement. Le responsable mentionné ci-dessus est commun à chaque activité. **Destinataires, transferts et sous-traitants restent à confirmer avant ouverture** : une marque de prestataire ne prouve ni le pays d’hébergement ni sa qualification juridique.

| Activité / base juridique | Personnes et données | Accès et destinataires | Conservation et points d’attention |
| --- | --- | --- | --- |
| Compte client et carnet d’adresses / service demandé, art. 6(1)(b) | Clients : identifiant, nom, e-mail, hachage du mot de passe, adresses, dates de connexion | Client lui-même, personnel habilité, hébergeur et mainteneur selon leurs missions | Pendant le service ; revue à deux ans d’inactivité identifiée. Préavis proposé de 30 jours avant fermeture, contrôle des opérations encore ouvertes. La fermeture du compte ne supprime pas les archives légales. |
| Achat, paiement, livraison / contrat et obligations légales, art. 6(1)(b) et (c) | Clients y compris invités : adresse, e-mail, articles et variantes, montants, références du paiement, conditions applicables | Gestion, Stripe, transporteur, hébergeur, messagerie ; comptable pour les seules pièces utiles | Durée du service et des garanties ; pièces utiles ensuite en archive limitée. Factures/pièces comptables : dix ans depuis clôture de l’exercice. Contrats électroniques ≥ 120 € : règles particulières de dix ans, point de départ à vérifier depuis livraison/prestation. |
| Retours, garanties, remboursements / contrat, obligations légales, défense des droits art. 6(1)(b), (c), (f) | Clients et déclarants : nom et adresse électronique de déclaration, motif, demande datée, suivi colis, justificatif privé, articles/quantités, montants et historique | Client concerné, gestion du SAV, Stripe, transporteur pour ses opérations, hébergeur, messagerie | Dossier actif jusqu’à résolution ; archive des preuves nécessaires selon garantie/prescription/litige. Revue des documents superflus 90 jours après clôture. Ne pas purger une preuve d’expédition utile simplement parce que le dossier a 90 jours. |
| E-mails transactionnels / contrat, obligation légale et intérêt légitime de fiabilité | Clients : destinataire, copie du message, liens d’accès privés et données de commande/retour | Personnes habilitées et fournisseur d’envoi | Revoir les copies techniques envoyées après 90 jours ; conserver ailleurs les confirmations contractuelles requises. Préserver et traiter la file non envoyée. Aucune prospection automatique. |
| Sécurité des accès / intérêt légitime, art. 6(1)(f) | Visiteurs, clients, gestionnaires : IP, clés de limitation pseudonymisées, tentatives, sessions, authentification renforcée | Personnel de sécurité/maintenance autorisé, hébergeur, pas les gestionnaires sans besoin | Sessions une heure sans mise à jour. Compteurs limités à leur fenêtre, revue après expiration. Objectif journaux de sécurité : six mois, incident justifiant une conservation supplémentaire documenté. Distinguer journaux applicatifs et serveur. |
| Contact et SAV par messagerie / mesures précontractuelles, contrat ou intérêt légitime selon la demande | Demandeurs : coordonnées et contenu strictement utile | Personnel habilité, messagerie, mainteneur si incident | Pendant le traitement ; messages utiles à un contrat/SAV intégrés au dossier et à sa durée. Proposition : supprimer les échanges ordinaires sans suite après un an ; confirmer cette organisation dans la boîte réelle. |
| Droits RGPD / obligation légale, art. 6(1)(c) ; preuve minimale des suites art. 6(1)(f) | Demandeurs : référence interne, périmètre, vérification proportionnée, dates, décision et preuve de réponse | Responsable des droits et remplaçant habilité, prestataires concernés par une demande | Traiter sous un mois ; prolongation motivée possible de deux mois annoncée dans le premier mois. Preuve minimale proposée cinq ans pour défense des droits ; pièce d’identité éventuelle détruite après vérification, sauf justification particulière. |
| Sauvegardes et restauration / intérêt légitime de continuité et sécurité, art. 6(1)(f) | Données présentes dans les systèmes sauvegardés | Hébergeur et personnes expressément autorisées | Cycle proposé de 30 jours à confirmer avec l’hébergeur ; chiffrer, tester la restauration, limiter l’accès. Réappliquer les suppressions validées après restauration avant remise en service. Ne pas promettre cette rotation tant qu’elle n’est pas configurée. |

### Mesures observées dans le code

- Hachage des mots de passe ; limitation persistante des tentatives ; vérification de l’adresse e-mail en production ; accès de gestion limités aux habilitations prévues. La double authentification n’est pas actuellement active ; son éventuelle activation doit être organisée et testée.
- Plusieurs champs de coordonnées, motifs, adresses et pièces privées sont chiffrés en base. **Les clés et sauvegardes doivent être protégées séparément.** Toutes les données ne sont pas chiffrées champ par champ ; ne pas prétendre le contraire.
- Accès aux commandes et retours limités au compte propriétaire ou à un lien privé vérifié pour les invités. Les preuves de retour sont téléchargées via une route privée.
- Export du compte disponible avec confirmation du mot de passe, limitation des tentatives, protection CSRF et en-têtes empêchant la mise en cache. Aucun mot de passe, secret de session, jeton d’accès ou graine OTP n’est inclus.
- Le catalogue ne charge pas d’outil d’audience ou publicitaire tiers identifié. Refaire l’inventaire réseau sur le domaine définitif, notamment après ajout d’un widget ou d’un outil d’analyse.
- HTTPS public, droits de l’hébergeur, journaux, sauvegardes, rotation des clés et procédure d’astreinte **ne sont pas attestés par un test local**.

## Outils disponibles et limites

`python manage.py privacy_audit` affiche uniquement des nombres et des consignes de revue. `--json` fournit le même rapport structuré. Cette commande est **strictement en lecture seule**, sans option de suppression. Elle ne divulgue ni noms, ni e-mails, ni références de commandes.

Le rapport couvre sessions expirées, compteurs temporaires expirés, comptes apparemment inactifs, commandes anciennes non payées, simulations, justificatifs de dossiers clos, copies techniques des courriels envoyés et commandes payées anciennes. Les dates de profil ne sont pas toutes tracées : un compte signalé inactif n’est **pas** une preuve qu’aucun contact récent n’a eu lieu. Une commande ancienne de dix ans n’est **pas** automatiquement arrivée au terme d’une obligation comptable.

La rubrique « Mes données personnelles » permet l’export JSON des données **rattachées au compte**. Une simple concordance d’e-mail ne permet jamais d’y inclure une autre commande. L’export ne contient pas les blobs des pièces jointes, les journaux techniques ni la boîte de messagerie externe ; ces données restent à rechercher si le périmètre d’une demande le nécessite. Les justificatifs privés sont téléchargeables depuis leur dossier. Les documents fiscaux et leurs archives doivent aussi être examinés lors d’une demande, sans réécrire une facture émise.

**Aucune suppression de données réelles n’a été effectuée.** La purge et l’archivage à accès restreint restent à organiser et à tester avant de valider `LEGAL_PRIVACY_OPERATIONS_READY`.

## Procédure d’exercice des droits

1. **Réception** : enregistrer la date, le droit demandé, le périmètre, le contact déjà connu et le responsable de traitement du dossier. Vérifier chaque jour ouvré la boîte prévue ; fixer une échéance d’un mois calendaire, pas 30 jours par approximation. Accuser réception sans envoyer de données.
2. **Identité** : privilégier le compte authentifié ou un contrôle proportionné par les coordonnées déjà détenues. Ne pas demander systématiquement une pièce d’identité. En cas de doute raisonnable, expliquer la vérification strictement nécessaire ; pour un mandataire, vérifier le mandat. Ne rien divulguer à une simple adresse ressemblante.
3. **Inventaire** : vérifier compte, commandes rattachées, achats invités dont la propriété a été établie, retours et identité du déclarant, pièces jointes, bons/avoirs/factures, boîte de support, prestataires et archives. Écarter les données de tiers. Les paiements ne sont pas retrouvés en devinant un lien privé dans un e-mail.
4. **Accès/portabilité** : proposer le téléchargement privé disponible ; le compléter si nécessaire avec une copie intelligible des données non couvertes et les informations de l’article 15. Utiliser un canal sûr, avec destinataire vérifié, et une durée d’accès limitée ; aucun export partagé publiquement ou laissé dans les médias du site. L’export n’est pas une raison pour refuser une demande formulée par e-mail.
5. **Rectification** : modifier le profil et le carnet d’adresses quand demandé. Une correction comptable se traite par le document correctif requis, sans écraser la facture originale. Corriger les prestataires concernés lorsque nécessaire et applicable.
6. **Effacement** : décider donnée par donnée. Retirer le profil et les adresses devenus inutiles ; préserver uniquement les données nécessaires à une obligation ou à la défense d’un droit, dans une archive distincte à accès limité. Vérifier commandes et remboursements en cours, garanties et litiges. Ne pas supprimer une relation protégée ou un historique de remboursement par une suppression en cascade improvisée. Révoquer les sessions/liens d’accès et moyens d’authentification du compte fermé. Une suppression en base active doit être réappliquée lors de toute restauration de sauvegarde.
7. **Limitation/opposition** : marquer et faire respecter la restriction dans chaque outil concerné ; écarter les données de tout usage non autorisé. La comptabilité obligatoire ne devient pas une liste de prospection. Aucun module de prospection n’est actif actuellement.
8. **Réponse** : répondre dans le mois ; si une prolongation de deux mois est nécessaire, annoncer dans le premier mois ses raisons. Expliquer les actions réalisées, les exceptions motivées, les pièces conservées et leur échéance/critère, ainsi que le recours CNIL. Informer les destinataires de rectification/effacement/limitation lorsque le RGPD le requiert.
9. **Clôture** : conserver une trace minimale de la décision, des dates et de la réponse, sans conserver inutilement les copies de données ou la pièce d’identité. Révoquer le partage temporaire de l’export. Contrôler la réalisation des demandes envoyées aux prestataires.

### Journal de droits à tenir dans un emplacement privé

| Référence interne | Réception | Droit et périmètre | Vérification proportionnée | Échéance | Responsable | Données traitées / exception et motif | Réponse / canal | Clôture et échéance d’archive |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| À renseigner pour chaque demande réelle | | | | | | | | |

Ce tableau est un modèle de procédure, pas un journal qui contiendrait déjà des demandes. Ne pas consigner des données client dans le dépôt de code.

## Revue de conservation et archivage

Fréquence proposée : contrôle mensuel par la personne nommée, plus lors d’une demande d’effacement. Exécuter l’audit, puis documenter pour chaque catégorie la décision et la justification. Réconcilier les commandes abandonnées avec Stripe avant toute purge. Vérifier les dates de livraison, de garantie, de clôture fiscale et les contentieux. Archiver les pièces exigées dans un espace distinct et restreint avant de retirer les copies superflues des outils courants. Faire tester une suppression/restauration sur des données fictives. La commande d’audit ne remplace ni cette procédure ni la tâche d’exploitation qui doit être réellement planifiée.

Ne pas annoncer une conservation de « toutes les données pendant dix ans ». Les factures et contrats requis n’autorisent pas à garder indéfiniment un compte actif, tous ses justificatifs ou tous les journaux. Inversement, ne pas annoncer que fermer le compte efface les factures obligatoires.

## Incident et violation de données

Désigner un contact principal et un remplaçant. Dès découverte : consigner l’heure de connaissance, contenir l’incident sans détruire les preuves, révoquer les accès compromis et faire intervenir l’hébergeur/mainteneur. Décrire nature, catégories de données et personnes, volume estimé, conséquences, mesures et décisions. Toute violation doit être documentée. Si elle présente un risque pour les droits/libertés, notifier la CNIL dans les meilleurs délais et si possible sous 72 heures ; documenter un retard. En cas de risque élevé, informer aussi les personnes selon les règles applicables. Une notification initiale peut être complétée. Faire un bilan correctif ; ne pas envoyer d’alerte non vérifiée à toute la clientèle.

## À confirmer avec Élodie et les prestataires avant ouverture

- Responsable et remplaçant pour les demandes RGPD ; adresse effectivement surveillée ; personne habilitée à accepter une suppression et à contrôler les archives.
- Liste **réelle** de tous les intervenants : hébergeur, stockage, sauvegarde, messagerie transactionnelle et boîte de contact, Stripe/entité contractante, transporteurs, comptable/logiciel fiscal, mainteneur, éventuels widgets. Pour chacun : nom juridique, rôle, données utiles, pays de traitement et de sauvegarde, sous-traitants ultérieurs, contrat art. 28 si applicable, garanties des transferts hors EEE, suppression/restitution de fin de contrat.
- Validation des durées proposées, de l’archivage et des accès ; méthode de preuve des dates de livraison et de clôture fiscale ; traitement des garanties et litiges.
- Choix et configuration des journaux de sécurité et de leur rotation ; configuration réelle de la sauvegarde, durée et restauration testée.
- Confirmation des canaux de contact hors site, des anciennes bases clients importées ou conservées et de l’absence de prospection actuelle ; tout ajout demande un traitement distinct et une information adaptée.
- Désignation d’une personne chargée de la revue mensuelle ; exécution d’un exercice d’accès/rectification/effacement sur données fictives avec l’hébergeur et le comptable ; preuve de traitement des copies/sauvegardes et des données chez les prestataires.

## Sources officielles consultées le 16 octobre 2026

- [CNIL — registre des activités](https://www.cnil.fr/fr/RGPD-le-registre-des-activites-de-traitement) : contenu et caractère évolutif du registre.
- [CNIL — questions-réponses sur la gestion commerciale](https://www.cnil.fr/fr/questions-reponses-sur-les-referentiels-relatifs-la-gestion-des-activites-commerciales-et-des) : référentiel adaptable, durées et comptes inactifs ; les recommandations ne remplacent pas la justification du responsable.
- [CNIL — durées de conservation](https://www.cnil.fr/fr/passer-laction/les-durees-de-conservation-des-donnees) : séparation entre base active et archive intermédiaire.
- [CNIL — répondre à une demande de droit d’accès](https://www.cnil.fr/fr/repondre-une-demande-de-droit-dacces) : identité proportionnée, droits des tiers, réponse et prolongation.
- [CNIL — tracer les opérations](https://www.cnil.fr/fr/securite-tracer-les-operations) : minimisation et durée des journaux.
- [CNIL — notifier une violation](https://www.cnil.fr/fr/services-en-ligne/notifier-une-violation-de-donnees-personnelles) : documentation, analyse des risques, délais et personnes concernées.

Les durées proposées de 90 jours pour la revue des copies techniques, d’un an pour les contacts sans suite et le cycle de sauvegarde de 30 jours sont des choix d’organisation **à valider**, pas des délais légaux universels. La conservation des preuves ne peut pas être calculée correctement avec la seule date de création d’une commande.
