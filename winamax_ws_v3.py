import asyncio, json, re, time, argparse
from pathlib import Path
from typing import Dict, Any, List
from playwright.async_api import async_playwright

# --------- Config par défaut (overridable en CLI) ----------
DEFAULT_SPORTS = [1, 2, 4, 5]             # 1:foot, 2:basket, 4:hockey, 5:tennis
DEFAULT_INITIAL_COLLECT_MS = 25000        
DEFAULT_MONEYLINE_TIMEOUT_MS = 25000
DEFAULT_HEADLESS = False
DEFAULT_PROXY = None                      
DEFAULT_OUTDIR = "."

# --------- Endpoints Socket.IO ----------
BASE_HTTP = "https://sports-eu-west-3.winamax.fr/uof-sports-server/socket.io/"
BASE_WS   = "wss://sports-eu-west-3.winamax.fr/uof-sports-server/socket.io/"
COMMON_Q  = "language=FR&version=3.9.1&embed=false"

# --------- Mapping marchés ----------
MARKET_ID: Dict[int, Dict[str, int]] = {
    1: {"moneyline": 1,   "total_ou": 18,  "handicap": 7016},            
    2: {"moneyline": 219, "total_ou": 225, "handicap": 223},             
    4: {"moneyline": 1,   "total_ou": 412, "handicap": 410},             
    5: {"moneyline": 186, "total_ou": 314, "handicap": 188,               
        "total_games": 189, "handicap_games": 187},
}

# --------- State fusionné ----------
class WinaState:
    def __init__(self):
        self.matches: Dict[int, Dict[str, Any]] = {}
        self.bets: Dict[int, Dict[str, Any]] = {}
        self.odds: Dict[int, Any] = {}
        self.tournaments: Dict[str, Dict[str, Any]] = {}
        self.sports_index: Dict[int, set] = {}

    def merge(self, payload: Dict[str, Any]):
        if not isinstance(payload, dict):
            return
        if "sports" in payload:
            for sid, sdata in payload["sports"].items():
                sid_int = int(sid)
                ids = sdata.get("matches") or []
                self.sports_index.setdefault(sid_int, set()).update(ids)
        if "matches" in payload:
            for mid, obj in payload["matches"].items():
                mid_int = int(mid) if isinstance(mid, str) and str(mid).isdigit() else int(mid)
                self.matches[mid_int] = {**self.matches.get(mid_int, {}), **obj}
        if "bets" in payload:
            for bid, obj in payload["bets"].items():
                bid_int = int(bid) if isinstance(bid, str) and str(bid).isdigit() else int(bid)
                self.bets[bid_int] = {**self.bets.get(bid_int, {}), **obj}
        if "odds" in payload:
            for oid, val in payload["odds"].items():
                oid_int = int(oid) if isinstance(oid, str) and str(oid).isdigit() else int(oid)
                self.odds[oid_int] = val
        if "tournaments" in payload:
            for tid, obj in payload["tournaments"].items():
                self.tournaments[str(tid)] = {**self.tournaments.get(str(tid), {}), **obj}

# --------- Helpers ----------
def is_real_match(m: dict) -> bool:
    return bool(m) and bool(m.get("competitor1Name")) and bool(m.get("competitor2Name"))

def build_listing(state: WinaState, sports: List[int]) -> List[Dict[str, Any]]:
    out = []
    for mid, m in state.matches.items():
        if not is_real_match(m):
            continue
        sport = int(m.get("sportId") or 0)
        if sport not in sports:
            continue
        tid = m.get("tournamentId")
        league = state.tournaments.get(str(tid), {}).get("tournamentName") if tid is not None else None
        out.append({
            "matchId": int(mid),
            "sportId": sport,
            "league": league,
            "home": m.get("competitor1Name"),
            "away": m.get("competitor2Name"),
            "matchStart": m.get("matchStart"),
        })
    out.sort(key=lambda x: (x["matchStart"] is None, x["matchStart"] or 0))
    return out

def moneyline_ready(state: WinaState, match_id: int, sport_id: int) -> bool:
    target = MARKET_ID.get(sport_id, {}).get("moneyline")
    if not target:
        return False
    expected = 3 if sport_id in (1, 4) else 2
    for b in state.bets.values():
        try:
            if int(b.get("matchId") or 0) != match_id:
                continue
        except:
            continue
        if b.get("marketId") != target:
            continue
        outs = b.get("outcomes") or []
        if len(outs) != expected:
            continue
        if all(int(o) in state.odds for o in outs):
            return True
    return False

async def wait_for_moneyline(page, state: WinaState, match_id: int, sport_id: int, timeout_ms: int) -> bool:
    """Attend sportId si nécessaire, puis la moneyline prête."""
    t0 = time.time()
    while sport_id == 0 and (time.time() - t0) * 1000 < timeout_ms:
        mi = state.matches.get(match_id) or {}
        sport_id = int(mi.get("sportId") or 0)
        if sport_id:
            break
        await page.wait_for_timeout(250)
    t1 = time.time()
    while (time.time() - t1) * 1000 < timeout_ms:
        if moneyline_ready(state, match_id, sport_id):
            return True
        await page.wait_for_timeout(350)
    return False

def build_markets_for_match(state: WinaState, match_id: int) -> Dict[str, Any]:
    m = state.matches.get(match_id) or {}
    sport = int(m.get("sportId") or 0)
    mapping = MARKET_ID.get(sport, {})
    markets: Dict[str, Any] = {"moneyline": None, "total_ou": {}, "handicap": {}}
    if sport == 5:
        markets["total_games"] = {}
        markets["handicap_games"] = {}

    for b in state.bets.values():
        try:
            if int(b.get("matchId") or 0) != match_id:
                continue
        except:
            continue
        mid_ = b.get("marketId")
        outs = b.get("outcomes") or []

        if mid_ == mapping.get("moneyline"):
            markets["moneyline"] = [state.odds.get(int(o)) for o in outs]

        elif mid_ == mapping.get("total_ou"):
            try:
                line = float(str(b.get("specialBetValue", "")).split("=")[1])
            except:
                continue
            markets["total_ou"][line] = [state.odds.get(int(o)) for o in outs]

        elif mid_ == mapping.get("handicap"):
            try:
                line = float(str(b.get("specialBetValue", "")).split("=")[1])
            except:
                continue
            markets["handicap"][line] = [state.odds.get(int(o)) for o in outs]

        elif sport == 5 and mid_ == mapping.get("total_games"):
            try:
                line = float(str(b.get("specialBetValue", "")).split("=")[1])
            except:
                continue
            markets["total_games"][line] = [state.odds.get(int(o)) for o in outs]

        elif sport == 5 and mid_ == mapping.get("handicap_games"):
            try:
                line = float(str(b.get("specialBetValue", "")).split("=")[1])
            except:
                continue
            markets["handicap_games"][line] = [state.odds.get(int(o)) for o in outs]

    return {
        "bookmaker": "winamax",
        "matchId": match_id,
        "sportId": sport,
        "league": state.tournaments.get(str(m.get("tournamentId")), {}).get("tournamentName"),
        "home": m.get("competitor1Name"),
        "away": m.get("competitor2Name"),
        "matchStart": m.get("matchStart"),
        "markets": markets,
    }

# --------- Run principal ----------
async def run(headless, proxy, sports, outdir: Path,
              initial_collect_ms, fetch_ids, auto_foot_n, moneyline_timeout_ms, no_auto: bool):
    outdir.mkdir(parents=True, exist_ok=True)
    listing_path = outdir / "winamax_matches.json"
    state = WinaState()

    async with async_playwright() as p:
        launch_kwargs = {"headless": headless, "args": ["--disable-blink-features=AutomationControlled"]}
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}

        browser = await p.chromium.launch(**launch_kwargs)
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/140.0.0.0 Safari/537.36"),
            locale="fr-FR", timezone_id="Europe/Paris",
            extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
            permissions=["geolocation"], geolocation={"latitude": 48.8566, "longitude": 2.3522},
            service_workers="block",
        )
        page = await ctx.new_page()

        async def deliver(event_name: str, payload: str):
            if event_name == "m":
                try:
                    state.merge(json.loads(payload))
                except:
                    pass
            else:
                print(f"📩 {event_name}: {payload}")
        await page.expose_function("deliverToPython", deliver)

        
        await page.goto("https://www.winamax.fr/", wait_until="load", timeout=30000)
        for label in ("Tout accepter", "Accepter tout", "Accept all", "Accepter", "J'accepte"):
            try:
                b = page.get_by_role("button", name=re.compile(label, re.I))
                if await b.is_visible():
                    await b.click()
                    await page.wait_for_timeout(300)
                    break
            except:
                pass

        
        warm = await page.evaluate(
            """async ([baseHttp, q])=>{
                try{
                  const r=await fetch(baseHttp+"?EIO=4&transport=polling&"+q+"&t="+Date.now(),{credentials:"include"});
                  return {status:r.status,ok:r.ok};
                }catch(e){return{status:0,ok:false,err:String(e)}}
            }""", [BASE_HTTP, COMMON_Q]
        )
        print("🔎 Warm-up:", warm)

        
        ok = await page.evaluate(
            """([baseWs,q,sports])=>new Promise((resolve)=>{
                const ws=new WebSocket(baseWs+"?EIO=4&transport=websocket&"+q); window._wina_ws=ws;
                function emit(ev,obj){ws.send('42'+JSON.stringify([ev,obj]));}
                ws.onmessage=async(ev)=>{
                    const d=typeof ev.data==='string'?ev.data:'';
                    if(!d) return;
                    if(d.startsWith('0')){try{const info=JSON.parse(d.slice(1)); ws.send('40'); await window.deliverToPython('open',JSON.stringify(info));}catch{}; return;}
                    if(d==='2'){ws.send('3');return;}
                    if(d.startsWith('40')){
                        await window.deliverToPython('sio_connected','{}');
                        sports.forEach((s,i)=>setTimeout(()=>emit('m',{route:`sport:${s}`,requestId:String(Date.now()+i)}),200*i));
                        resolve(true); return;
                    }
                    if(d.startsWith('42')){try{const arr=JSON.parse(d.slice(2)); await window.deliverToPython(arr[0], JSON.stringify(arr[1]??arr));}catch{}; return;}
                };
            })""", [BASE_WS, COMMON_Q, sports]
        )
        if not ok:
            print("❌ WS KO")
            await ctx.close()
            await browser.close()
            return

        
        await page.wait_for_timeout(initial_collect_ms)

      
        listing = build_listing(state, sports)
        listing_path.write_text(json.dumps(listing, indent=2, ensure_ascii=False), "utf-8")
        print(f"✅ Listing écrit: {listing_path} ({len(listing)} matchs)")

        
        if listing and (sum(1 for x in listing if not x["league"]) / len(listing) > 0.25):
            await page.wait_for_timeout(6000)
            listing = build_listing(state, sports)
            listing_path.write_text(json.dumps(listing, indent=2, ensure_ascii=False), "utf-8")
            print(f"↻ Listing réécrit avec plus de 'league' renseignées ({len(listing)} matchs)")

        
        targets: List[int] = []
        if fetch_ids:
            targets.extend(fetch_ids)
        elif not no_auto and auto_foot_n > 0:
            targets.extend([x["matchId"] for x in listing if x["sportId"] == 1][:auto_foot_n])

        
        for mid in targets:
            rid = f"req_{int(time.time()*1000)}_{mid}"
            await page.evaluate(
                """([mid,rid])=>{window._wina_ws && window._wina_ws.send('42'+JSON.stringify(['m',{route:'match:'+mid,requestId:rid}]));}""",
                [mid, rid]
            )
            print(f"➡️  Subscribed match:{mid}")

            sport_id = int(state.matches.get(mid, {}).get("sportId", 0))
            ok = await wait_for_moneyline(page, state, mid, sport_id, moneyline_timeout_ms)
            if not ok:
                print(f"⚠️  Moneyline incomplète (timeout) pour {mid}")
            
            await page.wait_for_timeout(1200)

            out = build_markets_for_match(state, mid)
            ml = out["markets"].get("moneyline")
            if not ml or (isinstance(ml, list) and all(x is None for x in ml)):
                print(f"⏭️  Moneyline absente pour {mid}, fichier non écrit.")
                continue

            (outdir / f"odds_{mid}.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), "utf-8")
            print(f"✅ Cotes écrites: odds_{mid}.json")

        await page.evaluate("() => { try{window._wina_ws && window._wina_ws.close()}catch(e){} }")
        await ctx.close()
        await browser.close()

# --------- CLI ----------
def parse_args():
    ap = argparse.ArgumentParser(description="Winamax WebSocket collector (V3)")
    ap.add_argument("--headless", default=str(DEFAULT_HEADLESS), type=lambda x: x.lower() in ("1","true","yes","y"))
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    ap.add_argument("--sports", default=",".join(map(str, DEFAULT_SPORTS)))
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("--initial-ms", type=int, default=DEFAULT_INITIAL_COLLECT_MS)
    ap.add_argument("--moneyline-timeout-ms", type=int, default=DEFAULT_MONEYLINE_TIMEOUT_MS)
    ap.add_argument("--fetch-ids", default="")
    ap.add_argument("--auto-foot-n", type=int, default=3)
    ap.add_argument("--no-auto", action="store_true", help="Ne sélectionne pas automatiquement des matchs (seuls --fetch-ids seront pris)")
    return ap.parse_args()

if __name__ == "__main__":
    a = parse_args()
    sports = [int(s.strip()) for s in a.sports.split(",") if s.strip()]
    fetch_ids = [int(x) for x in a.fetch_ids.split(",") if x.strip()] if a.fetch_ids else None
    asyncio.run(run(
        headless=bool(a.headless),
        proxy=a.proxy,
        sports=sports,
        outdir=Path(a.outdir),
        initial_collect_ms=a.initial_ms,
        fetch_ids=fetch_ids,
        auto_foot_n=a.auto_foot_n,
        moneyline_timeout_ms=a.moneyline_timeout_ms,
        no_auto=a.no_auto,
    ))
