# Livraison standard offerte dès 99 €

Consigne du 18 octobre 2026 : le seuil inclusif est **99 € d’articles**, hors livraison et avant un code promotionnel saisi chez Stripe. Les prix de vente et quantités du panier déterminent ce montant. Le panier et la page de commande indiquent la gratuité ou le montant manquant ; les CGV reprennent la même règle.

`FREE_SHIPPING_THRESHOLD_EUR=99.00` est le réglage commun en euros, précis au centime. Le serveur choisit un tarif Stripe à zéro dès le seuil, sinon le tarif `STRIPE_SHIPPING_RATE` configuré. Aucun tarif sous le seuil, transporteur ni délai n’est inventé. Ces éléments restent à valider avant l’ouverture des ventes.

La règle et les CGV sont figées dans la tentative de paiement. Réessayer après une erreur réseau réutilise ce même contenu et la même clé d’idempotence. Une tentative déjà ouverte conserve ses conditions. Après confirmation du paiement, les frais effectivement payés sont conservés dans la commande et réutilisés pour les documents et retours ; une livraison offerte reste à zéro, même pour un retour partiel.

Les tests simulent Stripe ; aucun paiement réel ni création de tarif dans le compte marchand n’est effectué par la vérification locale. Les ventes restent désactivées tant que les réglages et validations d’ouverture sont incomplets.
