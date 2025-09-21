# Winamax – Collecteur WebSocket (V3)

Scraper "propre" des données temps réel de Winamax via leur Socket.IO intégré (WebSocket brut, sans API privée).

Ce projet génère :
- `winamax_matches.json` → listing de tous les matchs visibles (foot, basket, hockey, tennis)
- `odds_<matchId>.json` → cotes et marchés d'un match précis

✅ **La V3 n'utilise que Playwright** (navigateur headless). Pas besoin de python-socketio ni d'aiohttp.

## 1) Prérequis

- Windows / macOS / Linux
- Python 3.10+ (3.11–3.13 OK)
- Accès réseau normal vers winamax.fr et sports-eu-west-3.winamax.fr

## 2) Installation rapide

```bash
# 1) Aller dans votre dossier Exemple
cd D:\ScriptWinamax

# 2) Créer un venv
py -3 -m venv .venv
.\.venv\Scripts\Activate

# 3) Installer les dépendances
pip install --upgrade pip
pip install playwright

# 4) Installer le navigateur Playwright
python -m playwright install chromium
```

### Fichiers importants dans le dossier

- `winamax_ws_v3.py` ← script principal (la V3)
- `winamax_socket_rawws.py`, `winamax_sniffer.py` (anciens/outils)

Conservez-les si vous voulez, mais la V3 suffit pour l'usage final.

## 3) Lancer le collecteur

### 3.1. Faire un snapshot des matchs disponibles

```bash
python winamax_ws_v3.py
```

**Sortie attendue :**
- `winamax_matches.json` (dans le dossier courant)
- Log : ✅ `Listing écrit: winamax_matches.json (xxx matchs)`

**Astuce :** La liste peut légèrement varier d'un run à l'autre (flux temps réel). Pour capturer "plein" : utilisez un delai initial plus grand :

```bash
python winamax_ws_v3.py --initial-ms 45000
```

### 3.2. Récupérer les cotes pour un match particulier

Ouvrez `winamax_matches.json` et repérez le matchId souhaité.

Lancez la V3 en ciblant cet ID :

```bash
# Un seul match
python winamax_ws_v3.py --fetch-ids 61513672

# Plusieurs matchs
python winamax_ws_v3.py --fetch-ids 61513672,61060757
```

Le script s'abonne aux flux du/des match(s) et crée un fichier par match :
- `odds_61513672.json`
- `odds_61060757.json`

**Note "Moneyline absente" :** Si Winamax n'a pas encore publié la 1X2 (foot/hockey) ou la 2-way (basket/tennis), le fichier n'est pas écrit par défaut. Rallongez le délai :

```bash
python winamax_ws_v3.py --fetch-ids 61513672 --initial-ms 45000 --moneyline-timeout-ms 45000
```

### 3.3. Récupérer automatiquement les N premiers matchs de foot

```bash
python winamax_ws_v3.py --auto-foot-n 3
```

## 4) Options (CLI)

| Option | Défaut | Description |
|--------|---------|-------------|
| `--sports` | 1,2,4,5 | Sports à abonner (1=foot, 2=basket, 4=hockey, 5=tennis) |
| `--outdir` | . | Dossier de sortie des JSON |
| `--initial-ms` | 25000 | Temps initial pour laisser arriver les payloads "tournaments/matches" |
| `--moneyline-timeout-ms` | 25000 | Attente max pour des cotes "moneyline" complètes avant d'écrire le fichier |
| `--fetch-ids` | vide | Liste d'IDs matchs à coter (61513672,61060757) |
| `--auto-foot-n` | 3 | Si --fetch-ids est vide, coter n premiers matchs de foot |
| `--headless` | False | 1/true pour exécuter sans fenêtre |
| `--proxy` | vide | Proxy Playwright, ex. http://user:pass@host:port |

### Exemples

```bash
# Headless + sortie dans _captures
python winamax_ws_v3.py --fetch-ids 61513672 --headless 1 --outdir _captures

# Avec proxy et délais plus larges
python winamax_ws_v3.py --fetch-ids 61513672 --proxy http://user:pass@ip:3128 --initial-ms 60000 --moneyline-timeout-ms 60000
```

## 5) Fichiers générés

### 5.1. winamax_matches.json

Liste des matchs visibles au moment du snapshot :

```json
[
  {
    "matchId": 61513672,
    "sportId": 1,
    "league": "Bundesliga",
    "home": "VfB Stuttgart",
    "away": "St. Pauli",
    "matchStart": 1758306600
  }
]
```

- `sportId` : 1=foot, 2=basket, 4=hockey, 5=tennis
- `matchStart` : timestamp UNIX (secondes)

### 5.2. odds_<matchId>.json

Cotes et marchés d'un match précis :

```json
{
  "bookmaker": "winamax",
  "matchId": 61060757,
  "sportId": 1,
  "league": "Serie A",
  "home": "Lecce",
  "away": "Cagliari",
  "matchStart": 1758307800,
  "markets": {
    "moneyline": [2.8, 2.8, 2.6],
    "total_ou": {
      "0.5": [1.10, 6.00],
      "1.5": [1.46, 2.30],
      "2.5": [2.40, 1.43],
      "3.5": [4.60, 1.15]
    },
    "handicap": {}
  }
}
```

### Interprétation des champs markets

**moneyline**
- Foot / Hockey : `[1, N, 2]` → domicile, nul, extérieur
- Basket / Tennis : `[Home, Away]`

**total_ou** (Over/Under)
- Dictionnaire `{ "ligne": [Over, Under], ... }`
- Ex. `"2.5": [2.40, 1.43]`

**handicap**
- Dictionnaire `{ "handicap": [Home, Away], ... }`
- Ex. `"-1.0": [1.90, 1.90]`

**(Tennis) total_games & handicap_games :** Même structure que ci-dessus (lignes en jeux, pas en buts).

**Champs manquants / listes vides :** Winamax ne pousse pas toujours toutes les lignes immédiatement. Il est normal d'avoir des marchés vides pour certains matchs/moments.

## 6) Chercher un match rapidement

### PowerShell (par nom d'équipe)
```powershell
$matches = Get-Content winamax_matches.json | ConvertFrom-Json
$matches | ? { $_.home -match "Stuttgart" -or $_.away -match "Stuttgart" } |
  Select-Object matchId,league,home,away,matchStart
```

### Python "one-liner"
```python
python - << "PY"
import json,re
d=json.load(open("winamax_matches.json",encoding="utf-8"))
for m in d:
    if re.search("Stuttgart",m["home"] or "",re.I) or re.search("Stuttgart",m["away"] or "",re.I):
        print(m["matchId"], m["league"], "-", m["home"], "vs", m["away"])
PY
```

## 7) Pourquoi le nombre de matchs varie ?

1. **Flux temps réel :** l'index arrive avant toutes les fiches matches
   → Allongez `--initial-ms` (45–60 s) pour un snapshot plus "plein"

2. **Filtre sur la qualité des données :** on n'affiche que les fiches avec home & away
   → Certaines entrées "placeholder" sont ignorées

3. **Changements côté Winamax :** ajout/suppression en continu

Si vous souhaitez forcer le même nombre que l'index, on peut bâtir le listing depuis sports_index (IDs connus) et remplir les noms dès qu'ils arrivent. Dites-moi si vous voulez cette variante.

## 8) Limitations & bonnes pratiques

- Respectez les CGU de Winamax. Ce projet est pour usage interne/technique
- Ne marteler ni l'HTTP ni le WS (la V3 est légère, évitez les runs en boucle très serrée)
- Les IDs/mappings de marchés proviennent de l'observation ; Winamax peut les faire évoluer (V3 est faite pour être facilement ajustable)

## 9) Dépannage

**"Moneyline absente, fichier non écrit"**
- Allongez `--initial-ms` et `--moneyline-timeout-ms`, ou relancez plus tard

**403 / handshake**
- Lançez la V3 (elle fait un "warm-up" HTTP côté navigateur)
- Un proxy FR peut aider (`--proxy http://...`)

**Page noircie / bannière cookies**
- La V3 clique automatiquement sur "Tout accepter". Si ça change, regardez dans le code la section "Home + cookies"

**Pas de odds_*.json**
- Vérifiez que l'ID est correct et présent dans `winamax_matches.json`
- Essayez de coter un autre match pour confirmer que tout fonctionne

## 10) Résumé de ce que l'on récupère actuellement

**Listing multi-sports** (foot, basket, hockey, tennis) avec :
- matchId, sportId, league, home, away, matchStart

**Cotes par match :**
- moneyline, total_ou, handicap, et (tennis) total_games, handicap_games

**Fichiers JSON** prêts à consommer par votre bot (ou à versionner)

## 11) Commandes "prêtes à copier"

```bash
# Snapshot complet avec délais confortables
python winamax_ws_v3.py --initial-ms 60000

# Cotes d'un match précis, headless, sortie dans _captures
python winamax_ws_v3.py --fetch-ids 61513672 --headless 1 --outdir _captures

# Cotes de plusieurs matchs & délais plus longs
python winamax_ws_v3.py --fetch-ids 61513672,61060757 --initial-ms 45000 --moneyline-timeout-ms 45000
```
