# Réglages de la boutique

Depuis **Mon compte → Boutique → Réglages de la boutique**, choisir la rubrique
concernée. Chaque rubrique possède sa propre page et son bouton d’enregistrement.

- **Coordonnées** : téléphone et e-mail publics, repris dans le pied de page,
  les pages de contact et du magasin, les fiches produits, le panier, les
  mentions légales et les nouveaux e-mails. Les liens d’appel et d’e-mail sont
  mis à jour avec leurs libellés.
- **Adresse du magasin** : rue, complément, code postal, ville et pays.
  L’adresse publiée et l’itinéraire suivent ces réglages. Les données destinées
  aux moteurs de recherche utilisent aussi cette adresse physique.
- **Adresse des retours** : une adresse d’expédition distincte, ou celle du
  magasin. Elle est reprise dans les instructions de retour, les nouveaux
  accusés et les conditions de vente publiées.
- **Informations de l’entreprise** : identité juridique, forme, SIREN, SIRET,
  immatriculation, responsable de publication et adresse professionnelle.
  L’adresse professionnelle peut suivre celle du magasin ou rester distincte.

Les horaires habituels et les périodes exceptionnelles gardent leur page
**Horaires du magasin**. La sélection de la page d’accueil reste dans
**Personnaliser l’accueil**. Ces deux pages sont également accessibles depuis
les réglages.

L’enregistrement agit immédiatement, sans redémarrage. En cas de modification
simultanée dans deux onglets, le second enregistrement obsolète est refusé :
recharger la page avant de continuer. Les changements sont journalisés avec
leur auteur, sans rendre ce journal public.

Les coordonnées publiques ne changent pas le compte de messagerie qui envoie
les messages, ses identifiants SMTP, ni les destinataires privés des commandes.
Les anciennes factures, copies des conditions et profils de facturation déjà
enregistrés avec une commande conservent leurs données historiques. Les futurs
documents prennent les réglages en vigueur au moment de la nouvelle commande.
Les instructions de retour affichées aujourd’hui utilisent l’adresse actuelle.

La TVA, l’hébergement, le médiateur et les validations avant ouverture des ventes
conservent leur configuration et leur état de validation existants. Modifier
l’identité de l’entreprise ne les valide pas automatiquement.

## Installation

Appliquer les migrations habituelles de `boutique`. Aucune donnée de commande
n’est modifiée par cette migration. Avant le premier enregistrement, les valeurs
existantes du site et de sa configuration sont conservées. La première
modification enregistre une configuration complète en base, puis chaque page
ne modifie que ses propres champs.
