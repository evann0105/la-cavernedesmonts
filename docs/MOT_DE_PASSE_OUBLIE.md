# Retrouver un accès par e-mail

Le lien **« Mot de passe oublié ? »** est disponible sur la page de connexion,
pour un client comme pour la vendeuse. Il ne nécessite pas d’être connecté et
n’oblige pas à contacter le développeur lorsque la messagerie fonctionne.

## Parcours

1. Saisir l’adresse e-mail enregistrée dans le compte.
2. Lire le message le plus récent, puis ouvrir son lien privé.
3. Choisir et confirmer un nouveau mot de passe conforme aux validations du site.
4. Se reconnecter. La récupération ne connecte pas automatiquement la personne.

Le lien est valable une heure et ne peut être utilisé qu’une fois. Une nouvelle
demande acceptée invalide le lien précédent. Un changement d’adresse, de mot de
passe ou l’inactivation du compte empêche l’utilisation du lien devenu obsolète.
Ouvrir un lien ne change pas le mot de passe : les scanners de messagerie ne
consomment donc pas la récupération. Une page expirée permet de redemander un
lien.

Le site ne révèle jamais si l’adresse correspond à un compte actif. Une demande
ne confirme pas l’adresse du compte, n’accorde aucun droit administratif et ne
modifie pas son mot de passe. Après une modification réussie, les anciennes
connexions doivent se reconnecter et un avis de sécurité est préparé sans jamais
contenir le mot de passe.

## Envoi et mode test

Le transport est le même que pour les autres e-mails. La demande est mise en
file sans attendre SMTP, ce qui évite de distinguer une adresse existante au
temps de réponse de son fournisseur. Le worker `reconcile_reservations` traite
aussi cette file, après les tâches de commandes, remboursements et notifications.
Il doit rester actif ; un message peut prendre environ une minute à être préparé.

Avec `DEBUG=True` et `EMAIL_TEST_MODE=True`, aucun message n’arrive dans une boîte
personnelle : il est capturé dans la boîte e-mail de test du site. Celle-ci reste
réservée aux gestionnaires autorisés. Un bouton explicite ouvre uniquement les
liens de récupération appartenant à ce site ; l’aperçu HTML reste isolé.

Un client n’obtient jamais accès à cette boîte administrative. Pour les essais,
utiliser un compte fictif et une session de gestion autorisée, ou les fichiers
privés de test accessibles à l’opérateur. La messagerie professionnelle sera
nécessaire pour la récupération réelle en production. Aucun secret SMTP n’est
ajouté au code et le parcours ne force pas le mode test.

## Protection et exploitation

- Les demandes sont limitées en base par réseau et adresse, avec délai entre
  deux demandes. L’adresse saisie et les contenus d’envoi conservés en attente
  sont chiffrés ; les identifiants de quotas sont pseudonymisés.
- Le jeton n’est pas conservé en clair dans l’état de récupération. Les contenus
  sensibles en attente d’envoi restent chiffrés et sont retirés après traitement.
- La validation finale reprend les contrôles sous verrou de base de données :
  deux validations simultanées ne peuvent pas appliquer deux mots de passe.
- Les formulaires exigent POST et CSRF. Les pages sont privées, non indexables
  et non mises en cache. L’URL portant le jeton utilise `no-referrer`, puis le
  formulaire s’ouvre à une URL nettoyée. Cette page utilise `same-origin` pour
  préserver le contrôle d’origine des POST, y compris en HTTP local.
- Les liens sont construits depuis l’origine configurée du site, jamais depuis
  un domaine fourni par le formulaire ou un en-tête Host arbitraire.
- Les limitations de connexion existantes restent actives. Si de mauvais essais
  avaient bloqué la connexion, attendre la fin de ce blocage, au plus quinze
  minutes, même après avoir choisi un nouveau mot de passe.

Le filtre applicatif masque les chemins de récupération dans les journaux Django.
À l’hébergement, masquer aussi ces chemins dans les journaux du proxy et du
serveur HTTP : ces journaux ne passent pas par le filtre Python. Ne jamais
publier la boîte de test, les sessions, les sauvegardes ou les liens d’e-mails.

Le suivi couvre les deux parcours client et gestionnaire, les liens invalides,
expirés ou réutilisés, les demandes concurrentes, les validations simultanées,
les protections de formulaire, les envois en échec et l’absence d’énumération.

Références : [récupération Django](https://docs.djangoproject.com/en/5.2/topics/auth/default/#django.contrib.auth.views.PasswordResetView),
[recommandations OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html).
