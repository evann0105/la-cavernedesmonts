# Pause des commandes

L’espace vendeuse comporte une page dédiée : `/gestion/boutique/pause-commandes/`, accessible depuis Tous les outils et Réglages de la boutique. Elle nécessite les droits de gestion du catalogue et la confirmation de l’adresse du compte, selon la politique d’accès existante.

## Fonctionnement

Une période possède un nom privé, un message public, un début et une reprise obligatoires. Les calendriers et les dates affichées utilisent l’heure de Paris ; les instants sont stockés avec fuseau. Les heures ambiguës ou inexistantes lors d’un changement d’heure sont refusées par le formulaire. Le début immédiat utilise l’horloge du serveur, jamais une date fournie par le navigateur.

La suspension est active sur l’intervalle `[début, reprise[`. Aucune tâche planifiée, connexion au compte de la vendeuse ou intervention manuelle n’est nécessaire pour le début ou la fin. Deux périodes adjacentes forment une fermeture continue : le bandeau indique la vraie reprise après la dernière. Les chevauchements sont refusés sous un verrou partagé par tous les réglages.

Une pause future peut être modifiée ou annulée. Une pause en cours peut être prolongée ou terminée plus tôt ; son début effectif ne peut pas être réécrit. Une période terminée ou annulée reste consultable et conserve son journal d’actions et les états avant/après. Les vieux onglets sont détectés par une révision globale ; leur saisie reste affichée pour comparaison avant une nouvelle confirmation.

## Suivi quotidien et réutilisation

L’accueil vendeuse affiche l’état de la suspension des commandes et la prochaine période, avec ses dates à l’heure de Paris et un accès aux réglages. Cet indicateur concerne la suspension des achats ; les autres conditions nécessaires au paiement restent indépendantes. Il se synchronise à la frontière prévue et au retour dans l’onglet, sans recharger les autres tâches affichées.

Le bouton **Dupliquer** d’une période ouvre un formulaire reprenant uniquement son nom et son message. Les dates restent vides et le mode programmé est présélectionné : rien n’est créé par cette consultation. La vendeuse choisit les nouvelles dates et confirme l’enregistrement, avec les mêmes contrôles de droits, de chevauchement et de révision que pour une création habituelle. La période d’origine et son journal ne sont pas modifiés.

Le détail d’une pause présente les réglages initiaux puis les changements de nom, de message et de dates sous forme avant/après. L’annulation et la reprise anticipée indiquent leur instant effectif. Le journal reste paginé, sans limite globale à douze événements ; les valeurs anciennes absentes ou illisibles sont signalées sans empêcher la consultation. Les dates sont présentées à l’heure de Paris, avec la précision nécessaire pour distinguer les instants.

## Ce que voit le client

Pendant la pause, le catalogue, les photos, les couleurs et les tailles restent consultables. Un bandeau distinct des annonces éditoriales affiche le message et la reprise. Les nouvelles actions d’ajout au panier et d’achat sont bloquées ; le panier existant est conservé et les quantités/retraits restent modifiables.

Un onglet déjà ouvert vérifie les frontières prévues avec l’heure du serveur, actualise ses contrôles à la reprise et se resynchronise lorsqu’il redevient visible ou retrouve le réseau. Il n’effectue pas d’interrogation permanente. Une création/modification imprévue de pause est donc visible à la prochaine lecture, au réveil de l’onglet ou lors de la soumission ; le contrôle serveur bloque immédiatement toute nouvelle tentative concernée. En cas d’échec de lecture à une frontière, les contrôles restent bloqués jusqu’à vérification, avec des reprises limitées et un message d’actualisation.

Le catalogue conserve ses URL, son indexation et sa disponibilité physique dans les données structurées. La pause ne change ni le stock ni les prix ni la configuration technique du paiement. Les aperçus privés de contenu restent indépendants de ce bandeau public.

## Paiements et commandes existants

Les commandes déjà payées restent à préparer. Aucun remboursement, annulation ou relâchement de réservation n’est provoqué par la seule suspension. Les horaires du magasin et les délais d’expédition conservent leurs réglages distincts.

Les contrôles serveur interviennent à l’ajout au panier, au POST de paiement, avant toute nouvelle réservation, puis avant l’admission d’une nouvelle tentative Stripe. La réservation prend une garde partagée avant les verrous de commande/stock ; l’admission déjà sous verrou de commande utilise une garde sans attente pour éviter une inversion avec une suspension simultanée. Les mutations vendeuse prennent la garde exclusive et ne verrouillent jamais les commandes.

Une admission de paiement est enregistrée durablement avec le récapitulatif, avant l’appel réseau. La garde n’est jamais conservée pendant un appel Stripe. Une tentative admise avant la pause peut donc encore aboutir : ses réponses, reprises idempotentes, confirmations, webhooks et rapprochements restent traités normalement. La suspension n’est pas une révocation des pages Stripe déjà ouvertes.

La migration `0002_existing_payment_admissions` reconnaît les anciennes tentatives possédant un récapitulatif de paiement ou un identifiant Stripe non vide comme potentiellement engagées. Elle ne change aucun statut de commande, prix, stock ou code client et n’appelle aucun service externe.

## Installation

Appliquer les migrations habituelles puis renouveler les droits du rôle applicatif PostgreSQL selon la procédure du projet. Aucune pause n’est créée par l’installation : les achats restent dans leur état antérieur tant que la vendeuse n’a rien programmé. Aucune clé Stripe ni configuration d’e-mail supplémentaire n’est nécessaire pour cette fonctionnalité.
