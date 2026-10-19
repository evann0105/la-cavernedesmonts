# Contrôle du catalogue — 19 octobre 2026

Contrôle en lecture seule des 111 produits publiés dans la base locale, avec simulation de la configuration publique `https://la-cavernedesmonts.fr`. Ce contrôle ne constitue pas une indexation effective par Google.

- 111 / 111 : titre et description préparés automatiquement.
- 111 / 111 : réponse HTTP 200, une seule description, URL canonique correcte et données Product ou ProductGroup valides en JSON.
- 111 / 111 : inclus dans le sitemap public simulé.
- 0 description commerciale manquante ; 0 photo principale manquante.
- 88 fiches ne renseignent pas le champ marque. Leur nom peut déjà contenir la marque : ce texte est conservé pour le référencement, mais aucune marque structurée n’est devinée. Les alertes de l’administration invitent à vérifier l’étiquette et à renseigner le champ.
- Deux paires portent le même nom réel : produits 16 / 46 (« Veste polaire douce cocooning Canadian PEAK Bleu Marine ») et 56 / 60 (« Veste polaire sans manche blanche LHOTSE »). Vérifier s’il s’agit de doublons ou de références à distinguer. Aucune fusion ni suppression automatique n’a été effectuée.
- Les coloris distincts des noms longs restent différenciés dans les titres et descriptions générés.

La rubrique **Mon compte → Catalogue → Référencement Google** présente ces alertes et se recalcule avec les changements du catalogue. Un avertissement de contenu ne signifie pas l’absence de métadonnées.

627 tests applicatifs automatisés réussis, puis 73 vérifications ciblées après les derniers ajustements et 8 tests JavaScript réussis lors de cette livraison, dont les contrôles SEO, les variantes, la confidentialité, les prix, le stock et les parcours existants. Les vérifications de fiches n’ont ni créé de commande ni envoyé d’e-mail.

La publication HTTPS, l’autorisation d’indexation en production et la validation Search Console restent à réaliser. Voir [le guide de mise en ligne](REFERENCEMENT_GOOGLE.md).
