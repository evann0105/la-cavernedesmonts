# Documents imprimables — mise à jour du 17 octobre 2026

Le détail de commande du compte client, la confirmation de paiement et le dossier de retour proposent **Bon de commande**, **Facture** et, pour une commande payée ou de démonstration, **Bon de livraison**. La gérante dispose également de « Commandes et factures » et des liens dans les ventes. Chaque document est privé et possède une impression A4, avec enregistrement PDF via le navigateur.

Le bon de commande reprend les données de l’achat : articles, variantes, quantités, prix, remises exactes par ligne, livraison connue et état du paiement. Il ne constitue pas une facture. Les données manquantes restent signalées.

Le bon de livraison se glisse dans le carton : adresse de livraison enregistrée, référence/date, articles, tailles, couleurs, quantités et zone de contrôle manuscrite. Il ne comporte ni prix, ni e-mail client, ni données de facturation et ne dépend pas de la validation fiscale. Une adresse ou des lignes incomplètes sont signalées ; l’adresse de facturation ne remplace pas une livraison manquante. Il récapitule la commande entière : en cas d’envoi partiel, préciser le contenu du colis. Son impression ne marque pas une commande expédiée ou livrée, ne modifie pas le stock et ne crée pas de facture.

**Étiquette transporteur** : aucun transporteur choisi au 17 octobre 2026, aucune intégration sortante et aucun code-barres d’affranchissement inventé. Le bon ne remplace pas l’étiquette officielle obtenue auprès du transporteur avec l’affranchissement, le numéro de suivi et son code-barres. Après choix du prestataire, il faudra déterminer les modalités réelles de génération/import d’étiquette, les poids/dimensions, les tarifs et la transmission des données avant raccordement.

La facture reste un **aperçu non comptable** tant que le profil fiscal historique, les montants exacts ou l’adresse de facturation manquent. Le nom et l’adresse demandés par Stripe sont désormais conservés chiffrés lors de la confirmation du paiement. Les anciennes commandes ne reçoivent pas une identité ou une TVA déduite des données actuelles.

L’émission définitive est manuelle : une personne habilitée vérifie l’aperçu puis confirme explicitement. Aucun numéro n’est créé par consultation, impression ou confirmation de paiement. La série proposée WEB-AAAA-000001 et son départ à 1 doivent être validés avec le comptable. Les verrous empêchent la double émission et les numéros concurrents. Un instantané chiffré conserve le document ; les réimpressions ne le recalculent pas. Les corrections et remboursements nécessitent des avoirs distincts, à organiser avec la comptabilité. Les avoirs historiques du site sont des références de documents déjà émis, pas un générateur fiscal.

## Paramètres restant à confirmer

`SHOP_LEGAL` : identité et adresse professionnelles, SIREN/SIRET/RCS, mention fiscale, numéro TVA actif si applicable.

`SHOP_INVOICING` reste vide. Ses clés sont `vat_mode` (standard ou franchise confirmée), `vat_rate` (taux unique explicite pour articles et livraison), `numbering_confirmed`, `issue_policy_confirmed` et `ready`. Ces confirmations ne doivent être activées qu’après vérification comptable. Aucun taux n’est présumé. Le moment et la nature d’émission, notamment paiement avant livraison/acompte, doivent être confirmés ; les dates de commande et règlement ne prouvent pas une livraison.

Le module actuel n’accepte pas les paniers à plusieurs taux de TVA. En franchise, la mention de l’article 293 B doit être confirmée. Une extension territoriale ou B2B nécessite une adaptation. Les factures et contrats doivent être archivés et sauvegardés selon les durées applicables. Un PDF imprimable ne constitue pas un raccordement à la réforme de facturation électronique.

## Distinguer les trois documents

Les trois onglets indiquent leur usage. Le bon de commande utilise un tableau commercial simple (articles, quantités, montants TTC, avec prix unitaire/remise sur chaque ligne). La facture utilise un tableau détaillé et un bloc dédié au numéro, à la date d’émission, au régime de TVA et au statut comptable. Même en simulation, l’absence de numéro et les montants fiscaux inconnus sont explicites. Aucun numéro ou taux fiscal n’est inventé pour différencier un aperçu. Le bon de livraison conserve uniquement l’adresse et le contenu du colis, avec une case de contrôle manuscrite par article et aucune indication de prix.

Les montants de vente sont identiques sur le bon de commande et la facture d’une même vente : leurs rôles et mentions diffèrent. Une simple consultation reste sans émission, expédition ou mouvement de stock.

Aucune facture réelle n’a été émise. Aucun paiement ni e-mail supplémentaire n’a été déclenché. Les ventes restent fermées.

Sources : [mentions des factures](https://www.economie.gouv.fr/entreprises/gerer-son-entreprise-au-quotidien/gerer-sa-comptabilite-et-ses-demarches/mentions-obligatoires-dune-facture-tout-savoir), [documents EI](https://entreprendre.service-public.gouv.fr/vosdroits/F31808), [conservation](https://entreprendre.service-public.gouv.fr/vosdroits/F10029), [calendrier électronique](https://www.impots.gouv.fr/professionnel/questions/partir-de-quand-suis-je-concerne-par-la-reforme-de-la-facturation).
