# Bibliothèque de photos

La vendeuse retrouve ses images dans **Tous les outils → Site & contenus → Bibliothèque de photos**, sous `/gestion/photos/`. Les droits de gestion du catalogue et la politique de confirmation d’adresse existante s’appliquent. Aucun compte externe n’est nécessaire.

## Utilisation

La grille comporte des aperçus, une recherche par nom et des filtres par dossier. Une photo peut être ouverte en grand, renommée et déplacée. Les dossiers sont plats : leur création et leur renommage n’affectent aucun chemin de fichier. Le rangement groupé porte sur les photos cochées dans la page courante. Une photo sans dossier reste accessible parmi les photos non classées.

La page d’ajout accepte jusqu’à douze fichiers JPEG, PNG ou WebP par envoi, de 10 Mo et 64 mégapixels au maximum chacun. Le serveur vérifie les vrais formats, corrige l’orientation, optimise les images et crée les miniatures. Les noms originaux sont conservés pour la recherche. La reconnaissance du contenu évite de créer plusieurs entrées pour une image identique.

Les formulaires de produits (photo principale, galerie et couleurs), les photos de l’accueil, les deux diaporamas et les pages de présentation disposent d’un bouton **Choisir dans la bibliothèque**. Le sélecteur conserve les autres champs du formulaire ; il ne publie aucun changement. Un choix simple ou multiple doit encore être enregistré dans le formulaire métier. Chaque utilisation reçoit sa propre copie et sa propre description accessible.

## Conservation

Une photo de bibliothèque est immuable : son contenu n’est pas remplacé au renommage ou au rangement. L’archivage retire seulement cette photo des nouveaux choix. Les copies déjà présentes sur les produits, l’accueil, les pages et leurs versions historiques restent conservées. Une photo archivée peut être réactivée. Aucune fonction de suppression physique n’est proposée.

La bibliothèque ne modifie pas les stocks, les commandes, les prix, les slugs, les textes publics ou les configurations d’accueil existantes. La description d’une image est conservée dans son contexte d’utilisation ; elle n’est pas remplacée globalement par le nom de la photo.

## Alimentation automatique et installation

L’application `phototheque` possède ses propres modèles, migration, vues, ressources et tests. Après migration et mise à jour des droits du rôle applicatif PostgreSQL, la commande `sync_phototheque` récupère les images existantes. L’exécuter séparément dans les données habituelles et l’environnement de paiement fictif pour conserver leur isolation.

L’inventaire lit uniquement les champs d’images connus des produits, de l’accueil et des pages, ainsi que les photos statiques référencées ou les galeries importées connues. Il ne parcourt ni les documents clients, ni les pièces jointes, ni les archives historiques privées. Il n’effectue aucune récupération réseau. Les chemins absolus, les traversées de dossiers et les liens symboliques hors des sources autorisées sont rejetés. Les images manquantes ou invalides sont signalées sans modifier leurs références métier.

Les nouveaux envois sauvegardés dans les modèles métier sont récupérés après validation de leur transaction. Une transaction annulée n’ajoute pas d’entrée de bibliothèque. Le middleware `PhotoActorMiddleware`, placé après l’authentification, conserve uniquement le contexte de l’auteur pendant la requête ; il ne crée pas de données lors d’une consultation. Un échec de capture est journalisé sans invalider rétroactivement un enregistrement métier déjà confirmé ; un nouvel inventaire peut le rattraper.

Toutes les consultations et la liste destinée au sélecteur sont en lecture seule. Les opérations de rangement, d’ajout, de renommage et d’archivage passent par des requêtes POST protégées. Le sélecteur transmet des identifiants de photos de la bibliothèque, jamais des chemins locaux ou des URL à télécharger.
