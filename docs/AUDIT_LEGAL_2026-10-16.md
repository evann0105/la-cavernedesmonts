# Audit de préparation légale — La Caverne des Monts

Contrôle du 16 octobre 2026 sur le projet local et sa configuration effective. Périmètre : boutique française vendant des vêtements et accessoires à des consommateurs. Il s’agit d’un audit technique et documentaire de préparation, pas d’une certification de conformité ni d’une validation par un professionnel du droit. Les démarches propres à l’entreprise ne peuvent pas être déduites du code.

## Conclusion

**Le site n’est pas encore prêt pour l’ouverture des ventes.** Les pages essentielles sont maintenant accessibles et les principales incohérences de l’ancien texte ont été corrigées. Les paiements restent fermés. L’ouverture nécessite les renseignements réels manquants et la mise en œuvre des procédures métier décrites ci-dessous.

L’entreprise est identifiée d’après les renseignements communiqués par le propriétaire du projet : Élodie FOLOPPE, EI, SIREN 525 114 914, SIRET 525 114 914 00019, immatriculation 525 114 914 RCS Bourg-en-Bresse. L’adresse de la boutique et des retours utilise le numéro 225 déjà fourni. L’état du registre, l’identité de la directrice de publication et le régime de TVA doivent être confirmés par l’entreprise. Le numéro de TVA proposé comme « à confirmer actif » n’a pas été présenté comme vérifié.

## Corrections réalisées

| Point | Résultat et limites |
| --- | --- |
| Mentions légales | Page dédiée avec identité, coordonnées, SIREN/SIRET/RCS ; les renseignements non confirmés restent signalés. |
| CGV | Remplacement de l’ancien texte CIC/chèque par une version adaptée au parcours Stripe. Suppression des clauses exonérant globalement le vendeur, imposant systématiquement l’emballage d’origine ou réputant conforme tout article après 14 jours. Encadré réglementaire complet intégré ; validation des renseignements de l’entreprise et du texte final encore nécessaire. |
| Confidentialité | Finalités et bases légales, catégories de données, destinataires, droits, contact et réclamation CNIL. Les prestataires et transferts définitifs restent à renseigner. Aucun hébergement France/UE n’est inventé. |
| Cookies | Information dédiée aux cookies session et CSRF ; aucun outil publicitaire ou d’audience tiers trouvé dans les ressources examinées. Pas de bandeau de consentement artificiel sans cookies facultatifs. À revérifier sur l’hébergement final et après tout ajout d’outil. |
| Rétractation | Information, formulaire type lisible et téléchargeable, accès aux retours sans création de compte ; possibilité d’une déclaration claire par courrier/e-mail. La réception en ligne est confirmée par un e-mail contenant la nature, les articles, la date/heure et les précisions. Les e-mails sont mis en file en cas d’échec ; leur surveillance et leur relance en production restent nécessaires. |
| Confirmation de commande | Conservation des conditions applicables et formulaire dans la commande, ajout d’une copie texte à l’e-mail ; montant payé, livraison et récapitulatif. Ne remplace pas une facture fiscale. Les anciennes commandes ne reçoivent pas rétroactivement des CGV inventées. |
| Acceptation | La création des nouvelles sessions Stripe demande l’acceptation des CGV. Une acceptation n’est horodatée que si l’objet Stripe récupéré côté serveur la confirme. Le tableau de bord Stripe doit aussi avoir les URL CGV/confidentialité correctes ; vérifier ce parcours réel en environnement de test avant ouverture. |
| Paiement | Un contrôle refuse l’ouverture si les informations/validations légales sont incomplètes, si les e-mails ne sont pas activés ou si la boîte locale de test est encore sélectionnée. L’inventaire n’est pas réservé dans ce cas. Les validations manuelles sont des confirmations de préparation, pas une preuve automatique de conformité. |
| Abonnements | Aucun abonnement, paiement récurrent, offre à reconduction tacite ni essai payant trouvé. Le code Stripe est en mode achat ponctuel. Les obligations spécifiques de résiliation/reconduction seront à implémenter si cette activité est ajoutée. |
| Avis | Information sur la sélection éditoriale et l’absence de vérification d’achat ; distinction entre mois de visite et date de publication. Les dates de publication originales, références individuelles et droits de reproduction restent à vérifier. |
| Accessibilité | Page de contact et état réel de préparation ; aucune fausse déclaration de conformité ou d’exemption. |
| Gestion | Suivi privé « Préparer l’ouverture des ventes » accessible depuis le compte de gestion, et commande de contrôle `check_legal`. |

## Points à terminer avant d’ouvrir les paiements

1. **Entreprise / publication / TVA** : confirmer la directrice de publication, le régime réel de TVA et les mentions de facturation. Un numéro théoriquement calculable ne prouve pas un assujettissement ou une activation VIES.
2. **Hébergement et domaine** : choisir l’hébergeur, publier ses nom/adresse/téléphone, déterminer les lieux de traitement et sous-traitants. Confirmer l’accès au nom de domaine et son échéance réelle auprès du titulaire/registrar.
3. **Médiateur** : adhérer au dispositif correspondant à l’activité et publier le nom, l’adresse et le site de ce médiateur. Ne pas inventer une adhésion. L’ancienne plateforme européenne de règlement en ligne des litiges n’est pas ajoutée comme solution actuelle.
4. **Livraison et frais de retour** : valider zone (le code actuel collecte une adresse en France), transporteur, tarif, restrictions territoriales et délai maximal ; les aligner sur le tarif Stripe et la logistique réelle. Confirmer les frais de retour et la prise en charge sans frais en cas de non-conformité. Le réglage d’instructions de retour doit correspondre aux CGV.
5. **Garanties** : le modèle complet A de D. 211-2 identifié dans sa version de 2022 est intégré aux CGV et à leur copie contractuelle. Son actualité complète reste à confirmer : l’évolution européenne vers douze mois d’extension après réparation et sa transposition française ne sont pas présentées uniformément par les sources officielles accessibles au 16 octobre 2026. Une réserve est affichée dans le texte de préparation ; voir CGV_FINALISATION_2026-10-16.md. Faire confirmer la version normative française applicable par le conseil, ainsi que les coordonnées et la procédure réelle de prise en charge avec la gérante, avant d’approuver les CGV.
6. **Fiches et sécurité des produits** : l’audit initial a signalé 26 descriptions sans composition exprimée en pourcentage, dont « test », sur 111 fiches publiées. La [recherche documentaire des 26 références](VERIFICATION_26_FICHES_2026-10-16.md) a ensuite permis de renseigner 9 compositions avec pourcentages documentés, 5 compositions partielles et les coordonnées officielles de 23 responsables de marque. Des champs dédiés, les sources et des notes privées de contrôle sont maintenant disponibles. Ces coordonnées de marque ne suffisent pas à attester le fabricant ou le responsable UE du lot effectivement détenu. Les 17 compositions encore incomplètes ou non confirmées, l’identification des opérateurs applicables, la traçabilité, les exigences bébé et les avertissements en français restent à vérifier sur les étiquettes et documents fournisseurs. Tout équipement de protection exige aussi les contrôles propres à sa catégorie. Le signal initial ne constitue pas à lui seul une détermination de non-conformité ; les pourcentages manquants n’ont pas été inventés.
7. **Factures et comptabilité** : un module de bons imprimables et de factures à émission manuelle est préparé (voir FACTURATION.md). Sans profil fiscal historique, adresse complète et validation comptable, seule une prévisualisation non comptable est proposée. Le processus d’avoirs reste à organiser. Le mail de paiement et le tableau de bord ne constituent pas ce système. Choisir une solution et une procédure, archiver les documents, traiter les remises et remboursements, vérifier avec le comptable les obligations de réception des factures électroniques et le calendrier d’émission/e-reporting selon la situation de l’EI.
8. **RGPD effectif** : un registre et des procédures sont préparés, un export personnel protégé et un audit de conservation en lecture seule sont disponibles. Identifier les prestataires réels, formaliser leurs contrats/transferts et valider les durées ainsi que les opérations de suppression/archivage (voir RGPD_REGISTRE_ET_PROCEDURES_2026-10-16.md). Pas de purge automatique générale trouvée. Prévoir une revue des comptes inactifs, journaux, pièces de retour, e-mails et sauvegardes. Tester l’accès, la rectification et l’effacement avec conservation des seules pièces justifiées ; traiter les demandes dans les délais applicables. Aucune suppression automatique de l’historique n’a été lancée pendant cet audit.
9. **Accessibilité** : vérifier l’effectif et les données de l’entreprise pour déterminer les éventuelles exemptions ; auditer réellement clavier, lecteur d’écran, contrastes, erreurs, zoom, mobile et paiement. La présence d’une page ne remplace pas l’audit.
10. **Prix, allégations et avis** : vérifier les preuves des caractéristiques techniques, les droits d’utilisation des photos/textes et les références d’avis Google. Toute annonce de réduction nécessite un prix de référence justifié selon les règles applicables (prix le plus bas des 30 jours, sous réserve d’exceptions légales). Le classement « Promotions » seul ne gère pas cet historique.
11. **REP et environnement** : déterminer avec la gérante si elle est concernée comme metteur sur le marché/importateur pour les emballages/textiles, obtenir/publier les identifiants uniques lorsqu’ils sont requis et vérifier les obligations d’information applicables. Le seul statut de revendeur ne permet pas de conclure sans connaître les achats et emballages utilisés.
12. **Exploitation et renouvellements** : HTTPS final avec renouvellement et alerte d’échec, domaine et messagerie avec échéances connues, certificats techniques, mises à jour de sécurité, sauvegardes chiffrées et restauration testée, accès de gestion et double authentification, gestion des incidents et notification de violation quand requise, surveillance des paiements/retours/mails en échec. Aucun accès au registrar ou contrat d’hébergement n’a été fourni ; les renouvellements ne sont donc pas certifiés actifs.

## État technique observé et contrôles

- Version locale en développement ; paiements désactivés, boutique non marquée prête, messagerie locale de test. Le HTTP local ne constitue pas la configuration HTTPS publique.
- Authentification et accès de gestion limités déjà en place ; preuves de retour privées ; plusieurs données sensibles chiffrées en base. Cela n’atteste pas de l’ensemble de la sécurité de l’hébergeur final.
- Migration `paiement.0008_order_contract` appliquée. Pas de modification de stock ni d’opération bancaire réalisée par cet audit.
- Suite complète : **265 tests réussis** après les changements de paiement/pages. Après les derniers ajustements de texte, transparence des avis, validation des paramètres et détail des remises dans les confirmations : **57 tests ciblés réussis**. Vérifications Django, cohérence des migrations et espaces du diff sans erreur.
- Vérification visuelle des mentions légales sur le serveur local.

## Configuration de préparation

Les informations sont centralisées dans `espace/legal.py` et peuvent être complétées par le dictionnaire JSON `SHOP_LEGAL` dans la configuration privée. Ne jamais publier les secrets Stripe ou de messagerie dans ce dictionnaire : il contient uniquement des renseignements publics.

Les validations `LEGAL_DOCUMENTS_APPROVED`, `LEGAL_PRIVACY_OPERATIONS_READY`, `LEGAL_PRODUCT_INFORMATION_READY`, `LEGAL_INVOICING_READY` et `LEGAL_ACCESSIBILITY_REVIEWED` restent à `False`. Elles ne doivent être confirmées qu’après réalisation et contrôle des points correspondants, pas pour contourner le blocage. Les conditions antérieures déjà associées à une tentative de paiement ne sont pas réécrites lors d’un nouvel essai.

## Sources officielles consultées

- [Mentions obligatoires et informations précontractuelles — ministère de l’Économie](https://www.economie.gouv.fr/entreprises/developper-son-entreprise/innover-et-numeriser-son-entreprise/mentions-sur-votre-site-internet-les-obligations-respecter)
- [E-commerce : règles entre professionnels et consommateurs — DGCCRF](https://www.economie.gouv.fr/dgccrf/les-fiches-pratiques/e-commerce-les-regles-entre-professionnels-et-consommateurs)
- [Rétractation et délais — DGCCRF](https://www.economie.gouv.fr/dgccrf/les-fiches-pratiques/delais-de-reflexion-ou-de-retractation-comment-sappliquent-t-ils)
- [Ordonnance du 5 janvier 2026 : rétractation en ligne — Légifrance](https://www.legifrance.gouv.fr/jorf/article_jo/JORFARTI000053298872)
- [Garanties légales, encadrés — décret du 29 juin 2022, à confronter à la version applicable](https://www.legifrance.gouv.fr/jorf/id/JORFTEXT000045978303)
- [Médiation de la consommation — Service Public Entreprendre](https://entreprendre.service-public.gouv.fr/vosdroits/F33338)
- [Cookies : règles — CNIL](https://www.cnil.fr/fr/cookies-et-autres-traceurs/regles/cookies)
- [Référentiels de gestion commerciale — CNIL](https://www.cnil.fr/fr/questions-reponses-sur-les-referentiels-relatifs-la-gestion-des-activites-commerciales-et-des)
- [Mise en conformité RGPD — CNIL](https://www.cnil.fr/fr/assurer-votre-conformite-en-4-etapes)
- [Accessibilité des services — DGCCRF](https://www.economie.gouv.fr/dgccrf/les-fiches-pratiques/professionnels-vos-produits-et-services-doivent-etre-conformes-la-directive-accessibilite)
- [Sécurité générale des produits, notamment article 19 — règlement UE 2023/988](https://eur-lex.europa.eu/legal-content/FR/TXT/PDF/?uri=CELEX%3A02023R0988-20260529)
- [Étiquetage des vêtements — DGCCRF](https://www.economie.gouv.fr/dgccrf/les-fiches-pratiques/letiquetage-des-vetements)
- [Factures électroniques — ministère de l’Économie](https://www.economie.gouv.fr/tout-savoir-sur-la-facturation-electronique-pour-les-entreprises)
- [Avis en ligne — DGCCRF](https://www.economie.gouv.fr/dgccrf/les-fiches-pratiques/avis-en-ligne-attention-aux-faux-commentaires)
- [Création d’une session de paiement — documentation Stripe](https://docs.stripe.com/api/checkout/sessions/create)

Certaines pages Légifrance ont renvoyé une erreur d’accès lors de l’ouverture ; leurs références ont été retrouvées dans l’index officiel. L’encadré réglementaire n’a donc pas été recopié de mémoire comme s’il avait été intégralement vérifié.

## Complément : documents et droits personnels

Au terme de cette étape : 315 tests réussis, dont accès privés, calculs des remises, figement et concurrence de numérotation, export personnel, conservation des déclarations et CGV. Migrations paiement.0009, retours.0006 et facturation.0001 appliquées. Aucune facture réelle, aucun paiement ni purge de données réelles. La série fiscale et les validations demeurent désactivées.

La collecte du nom/prénom de déclaration est ajoutée et chiffrée. Le 16 octobre, le contrôle automatique avait refusé l’ajout de cette identité dans les futurs accusés sans autorisation explicite. **Le 17 octobre, le propriétaire a expressément autorisé cet ajout uniquement à l’adresse liée à la commande : il est maintenant implémenté.** Le nom confirmé vient de la déclaration figée ; le destinataire unique vient de la commande et est fixé lors de la mise en file. Si cette adresse change avant envoi, l’accusé reste en attente de vérification au lieu d’être redirigé. Aucun envoi réel ni relance rétroactive n’a été déclenché pour cette modification. Les nouveaux liens documentaires dans les e-mails ne sont pas ajoutés. L’émission automatique de factures reste exclue ; le parcours retenu est un aperçu suivi d’une émission manuelle explicite après vérification du profil.

Un bon de livraison privé et sans prix a été ajouté le 17 octobre. Il ne vaut ni étiquette d’affranchissement ni preuve de livraison. Le transporteur reste à choisir ; aucun raccordement ni code-barres transporteur n’est annoncé comme opérationnel.

La [liste précise à remettre à Élodie](INFORMATIONS_A_DEMANDER_A_ELODIE.md) rassemble les renseignements, documents et démarches restant à confirmer.
