"""engine/handlers.py - event handlers. A handler says which classified screens it owns and what to do on them ("when the screen is X, do Y").

The classifier (screens.py) names the screen, this table finds the owner, the owner acts. The handler code calls the proven methods of the
bot (quest_screen, quest_difficulty, try_chain). Adding an event = one class here + one rule in screens.py.
"""


class Handler:
    task = ""                      # key in bhbot.TASKS
    screens = ()                   # classifier names this handler owns
    when_busy = False              # may it act while a battle / loading wait is in progress?

    def act(self, afk, frame, screen):          # -> True when something was done (the loop then starts over)
        raise NotImplementedError


class QuestHandler(Handler):
    task = "quest"
    screens = ("quest_zone_map", "quest_difficulty")

    def act(self, afk, frame, screen):
        if screen == "quest_zone_map":
            afk.quest_screen(frame, True)       # zone map: pick / walk zones, special dungeons, give-up rules
            return True
        if screen == "quest_difficulty":
            return afk.quest_difficulty(frame)  # NORMAL / HARD / HEROIC picker
        return False


class PvpHandler(Handler):
    task = "pvp"
    screens = ("pvp_menu",)

    def act(self, afk, frame, screen):
        if afk.activity == "gvg":                    # GVG's 'Select opponent' list looks exactly like PVP's: GVG owns it while a GVG run is on
            return False
        return afk.try_chain("pvp", frame, False)    # PLAY / FIGHT (opponent row from the settings)


class RaidHandler(Handler):
    task = "raid"
    screens = ("raid_menu",)

    def act(self, afk, frame, screen):
        if afk.raid_screens(frame):             # raid level / difficulty pages and the lobby
            afk.acted()
            return True
        return afk.try_chain("raid", frame, False)   # SUMMON / PLAY and the rest of its button chain


class TrialsHandler(Handler):
    task = "trials"
    screens = ("trials_menu", "difficulty_popup")

    def act(self, afk, frame, screen):
        if screen == "difficulty_popup":
            if afk.activity != "trials":        # the same DIFFICULTY list also belongs to Gauntlet / Expedition: only when Trials opened it
                return False
            if afk.trials_difficulty(frame):    # tier row -> exact number (V2.0 "Difficulty number" box)
                afk.acted()
                return True
            return False
        return afk.try_chain("trials", frame, False)  # PLAY / TOKENS chain


class WorldBossHandler(Handler):
    task = "world-boss"
    screens = ("world_boss_menu", "world_boss_lobby")

    def act(self, afk, frame, screen):
        if afk.boss_screens(frame):             # boss list / SUMMON, tier + difficulty, lobby (wait for players, START)
            afk.acted()
            return True
        return afk.try_chain("world-boss", frame, False)    # REGROUP and the rest of the chain


class GauntletHandler(Handler):
    task = "gauntlet"
    screens = ("gauntlet_menu", "difficulty_popup")

    def act(self, afk, frame, screen):
        if screen == "difficulty_popup":
            if afk.activity != "gauntlet":      # shared DIFFICULTY list: only when Gauntlet opened it
                return False
            if afk.trials_difficulty(frame, "gauntlet"):
                afk.acted()
                return True
            return False
        return afk.try_chain("gauntlet", frame, False)


class GvgHandler(Handler):
    task = "gvg"
    screens = ("gvg_menu", "pvp_menu")               # pvp_menu: the shared 'Select opponent' list, only while a GVG run is on

    def act(self, afk, frame, screen):
        if screen == "pvp_menu" and afk.activity != "gvg":
            return False
        return afk.try_chain("gvg", frame, False)    # PLAY / FIGHT / ACCEPT chain


class InvasionHandler(Handler):
    task = "invasion"
    screens = ("invasion_menu",)

    def act(self, afk, frame, screen):
        if afk.invasion_wave(frame):                  # the wave number picker
            afk.acted()
            return True
        return afk.try_chain("invasion", frame, False)


class ExpeditionHandler(Handler):
    task = "expedition"
    screens = ("expedition_menu", "expedition_map", "expedition_portal", "difficulty_popup")

    def act(self, afk, frame, screen):
        if screen == "difficulty_popup" and afk.activity in ("trials", "gauntlet"):    # that list belongs to the other events
            return False
        if afk.expedition_pages(frame):               # portal map, portal page, difficulty number list
            afk.acted()
            return True
        return screen == "expedition_menu" and afk.try_chain("expedition", frame, False)


HANDLERS = [QuestHandler(), PvpHandler(), RaidHandler(), ExpeditionHandler(), TrialsHandler(), WorldBossHandler(), GauntletHandler(),
            GvgHandler(), InvasionHandler()]
BY_SCREEN = {}
for _h in HANDLERS:
    for _s in _h.screens:
        BY_SCREEN.setdefault(_s, []).append(_h)


def dispatch(afk, frame, ready, busy, classify):
    """Ask the classifier which screen this is; if a handler owns it and its event is ready, let it act. Returns True if it acted."""
    h = None
    try:
        sc = classify(afk, frame)
    except Exception:
        return False
    for h in BY_SCREEN.get(sc.name, ()):          # several events can share a screen (the DIFFICULTY list): first one that acts wins
        if h.task not in ready or (busy and not h.when_busy):
            continue
        if h.act(afk, frame, sc.name):
            if afk.ts_due("handler"):
                afk.say("[handler] %s handled the %s screen" % (h.task, sc.name), every=0)
            return True
    return False
