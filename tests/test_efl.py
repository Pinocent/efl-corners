"""
Reading the EFL's referee appointments, and what the board infers from them:
the referee for each match, and which fixtures have been called off (the
fixture list kept ten postponed 26 Sep matches on their old date).

  python3 -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tracker import efl  # noqa: E402

# the two layouts the EFL has used: dates as h3 with divisions as h4 (3-6 Oct),
# and divisions as bold paragraphs (26 Sep); cup sections must be left out
BODY_HEADINGS = """<h3>Saturday, 3 October 2026</h3>
<h4>Sky Bet League One</h4>
<p>Burton Albion v Huddersfield Town (15:00)<br />NEIL HAIR<br />Marc Wilson and Aaron Hallam<br />Fourth Official : Ryan McILravey</p>
<p>Reading v Bradford City (15:00)<br />PAUL HOWARD<br />Lee Venamore and Rob Smith<br />Fourth Official : Harrison Blair</p>
<p><em>READ MORE: <a href="https://www.efl.com/x/">When does Championship football return?</a></em></p>
<h4>Sky Bet League Two</h4>
<p>Accrington Stanley v Cheltenham Town (15:00)<br />JAMIE O'CONNOR<br />Danny Guest and Kevin Mulraine<br />Fourth Official : Matt Archibald</p>
<p>Chesterfield v Tranmere Rovers (12:30)<br />RYAN MCILRAVEY<br />Andrew Ellis and Scott Chalkley<br />Fourth Official : Oliver Mackey</p>
<h3>Jersey Mike's Trophy</h3>
<h4>Tuesday, 6 October 2026</h4>
<p>Rotherham United v Bradford City (19:00)<br />JAMIE ROBINSON<br />Karl Buckley and Tyler Dutton<br />Fourth Official : Alex Gray</p>"""

BODY_BOLD = """<h3>Saturday, 26 September 2026</h3>
<p><strong>Sky Bet League One</strong></p>
<p>Cambridge United v AFC Wimbledon (15:00)<br />BEN TONER<br />Conor Farrell and Robert Evans<br />Fourth Official : Neil Hair</p>
<p><strong>Sky Bet League Two</strong></p>
<p>Oldham Athletic v Salford City (17:30)<br />ISAAC SEARLE<br />Martin Parker and Joshua Bramall<br />Fourth Official : S Wood</p>"""


def page(body, title="Referee appointments: 3-6 October", published="2026-09-29T09:07:06Z"):
    """A server-rendered efl.com article, with the CSS `content:""` strings that surround the real one."""
    js = json.dumps(body)[1:-1].replace("<", "\\u003C")
    return ('<style>.a:before{content:""}.b:after{content:"\\f101"}</style><script>window.__NUXT__=(function(){return '
            f'{{postTitle:"{title}",publishedDateTime:"{published}",content:[{{rowData:{{widgetName:"Text Block",'
            f'widgetType:"TextBlockWidget",widgetData:{{mediaLibraryID:e,contentDouble:e,content:"{js}",imageKey:e}}}}}}]}}'
            '}())</script>')


def fixture(d, league, home, away):
    return {"date": d, "league": league, "home": home, "away": away}


class Parse(unittest.TestCase):
    def test_headings_layout(self):
        rows = efl.parse(efl.article_body(page(BODY_HEADINGS)))
        got = [(r["date"], r["time"], r["league"], r["home"], r["away"], r["referee"]) for r in rows]
        self.assertEqual(got, [
            (date(2026, 10, 3), "15:00", "League 1", "Burton", "Huddersfield", "Neil Hair"),
            (date(2026, 10, 3), "15:00", "League 1", "Reading", "Bradford", "Paul Howard"),
            (date(2026, 10, 3), "15:00", "League 2", "Accrington", "Cheltenham", "Jamie O'Connor"),
            (date(2026, 10, 3), "12:30", "League 2", "Chesterfield", "Tranmere", "Ryan Mcilravey"),
        ])                                   # the Trophy tie is left out
        self.assertTrue(all(r["known"] for r in rows))

    def test_bold_layout(self):
        rows = efl.parse(efl.article_body(page(BODY_BOLD)))
        self.assertEqual([(r["league"], r["home"], r["referee"]) for r in rows],
                         [("League 1", "Cambridge Utd", "Ben Toner"), ("League 2", "Oldham", "Isaac Searle")])

    def test_unrecognised_club_is_marked(self):
        rows = efl.parse(BODY_BOLD.replace("Oldham Athletic", "Nowhere Rangers"))
        self.assertEqual([r["known"] for r in rows], [True, False])


class Referees(unittest.TestCase):
    def test_spelt_like_the_rest_of_the_board(self):
        rows = efl.parse(BODY_HEADINGS)
        for r in rows:
            r["published"] = ""
        refs = efl.referees(rows, date(2026, 9, 30), known={"R McIlravey", "N Hair"})
        self.assertEqual(refs[("Burton", "Huddersfield")], "N Hair")
        self.assertEqual(refs[("Accrington", "Cheltenham")], "J O'Connor")
        self.assertEqual(refs[("Chesterfield", "Tranmere")], "R McIlravey")
        self.assertEqual(efl.referees(rows, date(2026, 12, 1)), {})      # long gone


class Postponed(unittest.TestCase):
    def setUp(self):
        self.apps = efl.parse(BODY_HEADINGS)
        d = date(2026, 10, 3)
        self.fixtures = [fixture(d, "League 1", "Burton", "Huddersfield"),
                         fixture(d, "League 1", "Barnsley", "MK Dons"),        # not listed: off
                         fixture(d, "League 2", "Colchester", "Port Vale"),    # not listed: off
                         fixture(d, "Championship", "Derby", "Wrexham"),       # division not covered
                         fixture(date(2026, 10, 4), "League 1", "Luton", "Doncaster")]  # date not covered

    def test_left_out_fixtures_are_off(self):
        self.assertEqual(efl.postponed(self.apps, self.fixtures),
                         {("Barnsley", "MK Dons", date(2026, 10, 3)), ("Colchester", "Port Vale", date(2026, 10, 3))})

    def test_string_dates_and_played_matches(self):
        stored = [dict(f, date=f["date"].isoformat()) for f in self.fixtures]
        self.assertEqual(efl.postponed(self.apps, stored, played={("Barnsley", "MK Dons")}),
                         {("Colchester", "Port Vale", date(2026, 10, 3))})

    def test_the_rearranged_date_is_not_off(self):
        later = self.fixtures + [fixture(date(2026, 11, 24), "League 1", "Barnsley", "MK Dons")]
        off = efl.postponed(self.apps, later)
        self.assertIn(("Barnsley", "MK Dons", date(2026, 10, 3)), off)
        self.assertNotIn(("Barnsley", "MK Dons", date(2026, 11, 24)), off)

    def test_moved_to_another_listed_date_is_not_off(self):
        moved = efl.parse(BODY_HEADINGS.replace("</p>\n<h3>Jersey",
                          '</p>\n<h3>Sunday, 4 October 2026</h3>\n<h4>Sky Bet League One</h4>\n'
                          "<p>Barnsley v MK Dons (12:00)<br />TOM REEVES<br />A and B</p>\n<h3>Jersey"))
        self.assertNotIn(("Barnsley", "MK Dons", date(2026, 10, 3)), efl.postponed(moved, self.fixtures))

    def test_a_section_with_an_unknown_club_is_not_trusted(self):
        apps = efl.parse(BODY_HEADINGS.replace("Reading v", "Nowhere Rangers v"))
        off = efl.postponed(apps, self.fixtures)
        self.assertNotIn(("Barnsley", "MK Dons", date(2026, 10, 3)), off)   # League 1 section doubtful
        self.assertIn(("Colchester", "Port Vale", date(2026, 10, 3)), off)    # League 2 still fine


class SavedCopy(unittest.TestCase):
    URL = "https://www.efl.com/news/2026/september/29/referee-appointments--3-6-october/"

    def setUp(self):
        self.cache = os.path.join(tempfile.mkdtemp(), efl.FILE)
        self.pages = {efl.NEWS_URL: f'<a href="{self.URL[len(efl.SITE):]}">Referee appointments</a>',
                      self.URL: page(BODY_HEADINGS)}

    def fetch(self, url):
        if url not in self.pages:
            raise OSError("HTTP Error 403: Forbidden")
        return self.pages[url]

    def test_read_saved_and_reused_when_blocked(self):
        st = {}
        rows = efl.get_appointments(self.cache, log=lambda *_: None, status=st,
                                    today=date(2026, 9, 30), fetch=self.fetch)
        self.assertEqual(len(rows), 4)
        self.assertEqual(st["source"], "efl.com")
        self.assertEqual(st["articles"][0]["title"], "Referee appointments: 3-6 October")
        self.assertTrue(os.path.exists(self.cache))
        self.pages = {}                                                  # efl.com refuses
        st = {}
        rows = efl.get_appointments(self.cache, log=lambda *_: None, status=st,
                                    today=date(2026, 10, 1), fetch=self.fetch)
        self.assertEqual(len(rows), 4)
        self.assertEqual(st["source"], "saved copy")
        self.assertEqual(rows[0]["date"], date(2026, 10, 3))

    def test_an_edited_article_replaces_what_was_saved(self):
        efl.get_appointments(self.cache, log=lambda *_: None, today=date(2026, 9, 30), fetch=self.fetch)
        # the EFL drops a match (postponed) and changes a referee
        self.pages[self.URL] = page(BODY_HEADINGS.replace("PAUL HOWARD", "TOM REEVES")
                                    .replace("<p>Burton Albion v Huddersfield Town (15:00)<br />NEIL HAIR<br />"
                                             "Marc Wilson and Aaron Hallam<br />Fourth Official : Ryan McILravey</p>\n", ""))
        rows = efl.get_appointments(self.cache, log=lambda *_: None, today=date(2026, 10, 1), fetch=self.fetch)
        self.assertEqual(sorted((r["home"], r["referee"]) for r in rows),
                         [("Accrington", "Jamie O'Connor"), ("Chesterfield", "Ryan Mcilravey"), ("Reading", "Tom Reeves")])

    def test_finished_articles_are_not_fetched_again(self):
        efl.get_appointments(self.cache, log=lambda *_: None, today=date(2026, 9, 30), fetch=self.fetch)
        asked = []
        efl.get_appointments(self.cache, log=lambda *_: None, today=date(2026, 10, 10),
                             fetch=lambda url: asked.append(url) or self.fetch(url))
        self.assertEqual(asked, [efl.NEWS_URL])


if __name__ == "__main__":
    unittest.main()
