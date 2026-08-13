# Cloudflare Access — Setup projects.grooveos.app

> **Domaine corrigé 2026-08-13.** Ce runbook Phase 5 était écrit pour
> `projects.dejavu.cat` ; le domaine a migré vers `grooveos.app` le 2026-05-07
> (voir `.planning/STATE.md`). Les cinq occurrences ont été remplacées ici. La
> procédure Cloudflare Access elle-même n'a pas changé. Le contenu est en français,
> antérieur à la règle « docs produit en anglais ».

## Prérequis

- Domaine grooveos.app déjà sur Cloudflare (DNS géré par Cloudflare)
- Compte Cloudflare Zero Trust (plan Free — jusqu'à 50 users)
- Firebase Hosting initialisé (`firebase init hosting --project xbrain-495115`)
- DNS CNAME Firebase vérifié (TXT record de vérification ajouté)

## Étape 1 — Firebase Hosting custom domain

1. Firebase Console > Hosting > Add custom domain
2. Entrer : `projects.grooveos.app`
3. Copier le TXT record de vérification fourni par Firebase
4. Dans Cloudflare DNS : ajouter le TXT record (mode DNS-only, pas Proxied)
5. Attendre la vérification (peut prendre jusqu'à 24h)
6. Firebase fournit ensuite des enregistrements A (deux IPs Firebase Hosting)
7. Dans Cloudflare DNS : créer deux enregistrements A :
   - Name: `projects` | Content: `<IP Firebase 1>` | Proxied: OUI (orange cloud)
   - Name: `projects` | Content: `<IP Firebase 2>` | Proxied: OUI (orange cloud)

> **Important :** Les enregistrements A DOIVENT être en mode Proxied (orange cloud) pour que
> Cloudflare Access intercepte le trafic. Mode DNS-only (gris) = bypass Cloudflare Access.

## Étape 2 — Identity Provider Google dans Cloudflare Zero Trust

1. Aller sur https://one.dash.cloudflare.com/ > Settings > Authentication > Add new > Google
2. Dans Google Cloud Console (console.cloud.google.com) :
   - APIs & Services > Credentials > Create OAuth 2.0 Client ID
   - Application type: Web application
   - Name: Cloudflare Access
   - Authorized redirect URI: `https://<team>.cloudflareaccess.com/cdn-cgi/access/callback`
     (remplacer `<team>` par votre team slug Cloudflare Zero Trust — actuellement `cortxos`)
3. Copier Client ID et Client Secret dans Cloudflare

## Étape 3 — Application Access

1. Cloudflare Zero Trust > Access > Applications > Add an application > Self-hosted
2. Application name: `xbrain Projects Dashboard`
3. Application domain: `projects.grooveos.app`
4. Session Duration: 24h
5. Identity providers: Google (configuré étape 2)
6. Skip pages: laisser vide (toutes les URLs sont protégées)

## Étape 4 — Policy

1. Dans l'application créée > Add a policy
2. Policy name: `xbrain Team`
3. Action: Allow
4. Rule type: Emails
5. Values: `team@example.com`, `<team-member-1@example.com>`, `<team-member-2@example.com>`

## Résultat attendu

Tout accès à https://projects.grooveos.app est intercepté par Cloudflare Access.
L'écran de login Google apparaît. Seuls les emails whitelistés passent.
Firebase Hosting sert le HTML généré sans modification.

## Troubleshooting

- **Site non protégé** : vérifier que les records DNS sont en mode Proxied (orange cloud).
- **Erreur 523** : Firebase Hosting TLS incompatible avec Cloudflare proxy.
  Fix : Cloudflare SSL/TLS > Overview > passer de "Flexible" à "Full (Strict)".
- **Login loop** : vérifier que le redirect URI dans Google Cloud Console correspond exactement
  au format `https://<team>.cloudflareaccess.com/cdn-cgi/access/callback`.
- **URL directe Firebase bypass Cloudflare** : l'URL `xbrain-495115.web.app` est publique
  par nature. Risque accepté pour une petite équipe (T-05-06-01).
