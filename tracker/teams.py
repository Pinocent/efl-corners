"""
One spelling per club, whatever the source calls it.

football-data.co.uk, fixturedownload.com and Flashscore (manual_results.csv)
all spell clubs differently. Every name goes through canon() before it is
stored, so "Sheffield Weds", "Sheffield Wednesday" and "Sheffield Wed" are
one team - the old script let those through as separate clubs and double
counted the matches.
"""

LEAGUES = {"E1": "Championship", "E2": "League 1", "E3": "League 2"}

# canonical name -> other spellings seen in the wild
TEAMS = {
    # Championship
    "Birmingham": ["Birmingham City"],
    "Blackburn": ["Blackburn Rovers"],
    "Bolton": ["Bolton Wanderers"],
    "Bristol City": [],
    "Burnley": [],
    "Cardiff": ["Cardiff City"],
    "Charlton": ["Charlton Athletic"],
    "Derby": ["Derby County"],
    "Lincoln": ["Lincoln City"],
    "Middlesbrough": ["Boro"],
    "Millwall": [],
    "Norwich": ["Norwich City"],
    "Portsmouth": [],
    "Preston": ["Preston North End", "Preston NE"],
    "QPR": ["Queens Park Rangers"],
    "Sheffield Utd": ["Sheffield United", "Sheff Utd"],
    "Southampton": [],
    "Stoke": ["Stoke City"],
    "Swansea": ["Swansea City"],
    "Watford": [],
    "West Brom": ["West Bromwich Albion", "West Bromwich"],
    "West Ham": ["West Ham United"],
    "Wolves": ["Wolverhampton Wanderers", "Wolverhampton"],
    "Wrexham": [],
    # League 1
    "AFC Wimbledon": ["Wimbledon"],
    "Barnsley": [],
    "Blackpool": [],
    "Bradford": ["Bradford City"],
    "Bromley": [],
    "Burton": ["Burton Albion"],
    "Cambridge Utd": ["Cambridge", "Cambridge United"],
    "Doncaster": ["Doncaster Rovers"],
    "Huddersfield": ["Huddersfield Town"],
    "Leicester": ["Leicester City"],
    "Leyton Orient": ["Leyton"],
    "Luton": ["Luton Town"],
    "Mansfield": ["Mansfield Town"],
    "MK Dons": ["Milton Keynes Dons", "Milton Keynes"],
    "Notts County": ["Notts Co"],
    "Oxford Utd": ["Oxford", "Oxford United"],
    "Peterborough": ["Peterboro", "Peterborough United", "Peterborough Utd"],
    "Plymouth": ["Plymouth Argyle"],
    "Reading": [],
    "Sheffield Wed": ["Sheffield Weds", "Sheffield Wednesday", "Sheff Wed"],
    "Stevenage": [],
    "Stockport": ["Stockport County"],
    "Wigan": ["Wigan Athletic"],
    "Wycombe": ["Wycombe Wanderers", "Wycombe Wands"],
    # League 2
    "Accrington": ["Accrington Stanley"],
    "Barnet": [],
    "Bristol Rovers": ["Bristol Rvs"],
    "Cheltenham": ["Cheltenham Town"],
    "Chesterfield": [],
    "Colchester": ["Colchester United", "Colchester Utd"],
    "Crawley": ["Crawley Town"],
    "Crewe": ["Crewe Alexandra"],
    "Exeter": ["Exeter City"],
    "Fleetwood": ["Fleetwood Town"],
    "Gillingham": [],
    "Grimsby": ["Grimsby Town"],
    "Newport": ["Newport County"],
    "Northampton": ["Northampton Town"],
    "Oldham": ["Oldham Athletic"],
    "Port Vale": [],
    "Rochdale": [],
    "Rotherham": ["Rotherham United", "Rotherham Utd"],
    "Salford": ["Salford City"],
    "Shrewsbury": ["Shrewsbury Town"],
    "Swindon": ["Swindon Town"],
    "Tranmere": ["Tranmere Rovers"],
    "Walsall": [],
    "York": ["York City"],
    # clubs that move in and out of these divisions
    "Coventry": ["Coventry City"],
    "Hull": ["Hull City"],
    "Ipswich": ["Ipswich Town"],
    "Leeds": ["Leeds United"],
    "Sunderland": [],
    "Nottm Forest": ["Nottingham Forest", "Nott'm Forest"],
    "Forest Green": ["Forest Green Rovers"],
    "Harrogate": ["Harrogate Town"],
    "Carlisle": ["Carlisle United"],
    "Morecambe": [],
    "Sutton": ["Sutton United"],
    "Hartlepool": ["Hartlepool United"],
    "Wealdstone": [],
    "Barrow": ["Barrow AFC"],
}

_FILLER = {"fc", "afc", "utd", "united", "town", "city", "rovers", "athletic",
           "albion", "county", "wanderers", "the", "and"}


def _words(name):
    return "".join(ch.lower() if ch.isalnum() else " " for ch in str(name or "")).split()


def _strict(name):
    return "".join(w for w in _words(name) if w not in ("fc",))


def _loose(name):
    w = _words(name)
    core = [x for x in w if x not in _FILLER]
    return "".join(core or w)


_EXACT = {}
for _c, _alts in TEAMS.items():
    for _n in [_c] + _alts:
        _EXACT[_strict(_n)] = _c

# "AFC Wimbledon" and "Wimbledon" share a loose key, which is fine; "Bristol
# City" and "Bristol Rovers" both reduce to "bristol", so loose keys that
# point at more than one club are dropped rather than guessed.
_LOOSE = {}
for _c, _alts in TEAMS.items():
    for _n in [_c] + _alts:
        _LOOSE.setdefault(_loose(_n), set()).add(_c)
_LOOSE = {k: next(iter(v)) for k, v in _LOOSE.items() if len(v) == 1}

_unknown = set()


def canon(name):
    """Canonical club name. Unknown clubs pass through unchanged (and are noted)."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    hit = _EXACT.get(_strict(raw)) or _LOOSE.get(_loose(raw))
    if hit:
        return hit
    _unknown.add(raw)
    return raw


def unknown_names():
    return sorted(_unknown)
