"""engine/screens.py - the screen classifier: ONE place that answers "which screen is this?".

The bot asks it which screen it sees, and the handlers (handlers.py) act on the answer. The Troubleshooting menu's "Screen check" logs the
answer whenever it changes ("[screen] main_map"), so every guess can be compared with reality.

How it works
  * RULES is an ordered table: the FIRST rule that matches names the screen, so put the most specific / most covering rules first
    (popups on top of a screen win over the screen below them; a dialogue wins over the battle behind it).
  * A rule is ("screen_name", ("any" | "all", [template names])) or ("screen_name", ("call", "afk_method_name")).
    "any" = at least one of the templates is on the screen, "all" = every one of them. "call" = a check the bot already has
    (dialogue, news window, ...). Several rules may share a name (then either one makes it that screen).
  * classify(afk, frame, full=True) also lists the OTHER rules that matched (useful when two screens look alike).

How to test it
  python bhbot.py screen file.png [more.png]     what screen does the bot think a saved screenshot is?
  python bhbot.py screen --corpus                check every screenshot in BHPyBot/tests/screens/<expected_name>/*.png
  Wrong or unknown? Drop the screenshot (an 800x520 one from BHPyBot/out/unknown) into tests/screens/<the name it SHOULD have>/ and
  `screen --corpus` will keep it right from then on.
"""
from collections import namedtuple

Screen = namedtuple("Screen", "name why also")      # name, templates/checks that matched, other screens that matched too (full=True only)

# Screens that look the same for every event (the team roster with ACCEPT, the screen after a battle) get ONE name: which event it
# belongs to is something the bot knows from what it just clicked, not from the picture.
TEAM_ACCEPT = ["dungeons.accept", "raid.accept", "pvp-arena.accept", "trials.accept", "gauntlet.accept", "gvg.accept", "invasion.accept",
               "expedition.accept"]
RESULTS = ["dungeons.rerun", "dungeons.collect", "pvp-arena.town-win", "pvp-arena.town-defeat", "raid.town", "trials.town", "gauntlet.town",
           "gvg.town", "invasion.town", "expedition.town"]


RULES = (
    # ---- the connection and the whole-screen messages ------------------------------------------------------------------ #
    [("disconnected", ("any", ["globally.reconnect"])),
     ("maintenance", ("any", ["globally.maintenance"])),
     ("direct_message", ("any", ["globally.send-message"])),
     # ---- popups and confirmations (they sit on top of whatever screen is behind) --------------------------------------- #
     ("quit_battle_confirm", ("any", ["globally.confirm-quit-battle"])),
     ("idle_check", ("any", ["globally.are-you-still-there"])),
     ("exit_confirm", ("any", ["globally.are-you-sure-want-to-exit"])),
     ("leave_dungeon_confirm", ("any", ["globally.leave-this-dungeon"])),
     ("not_full_team_confirm", ("any", ["globally.confirm-start-not-full-team"])),
     ("out_of_resource", ("any", ["dungeons.not-enough-energy", "raid.not-enough-shards", "pvp-arena.not-enough-ticket",
                                  "trials.not-enough-tokens", "invasion.not-enough-badges", "world-boss.not-enough-xeals"])),
     ("daily_rewards", ("any", ["daily.claim", "globally.dailyRewards"])),
     ("news", ("call", "news_open")),
     ("fishing_ranks", ("any", ["globally.fishing-ranks"])),
     ("tier_picker", ("any", ["popup.tier-title"])),
     ("bounties_window", ("any", ["popup.bounties-title"])),
     ("summon_window", ("any", ["popup.summon-title"])),
     ("terms_of_service", ("all", ["popup.tos-title", "popup.tos-decline"])),         # the chat's Terms of Service page (the bot declines)
     ("chat_window", ("any", ["popup.chat-title"])),                                    # the chat window (world / guild); the bot closes it
     ("player_profile", ("all", ["popup.player-duel", "popup.player-ignore"])),          # another player's stat page (a click landed on a player): just close it
     ("items_popup", ("any", ["globally.items-popup"])),
     ("gvg_guild_confirm", ("any", ["gvg.guild-confirm"])),                             # once a week: "You cannot leave or be removed from your guild ..." (also has a YES button)
     ("resume_prompt", ("any", ["resume.text", "resume.yes"])),
     ("persuade_prompt", ("any", ["globally.persuade", "globally.persuade-bribe"])),
     ("familiars_page", ("any", ["globally.familiars-title"])),
     ("heroes_page", ("any", ["heroes.title", "heroes.confirm"])),
     # ---- a dialogue beats the battle behind it (the AUTO button stays on screen) -------------------------------------- #
     ("dialogue", ("call", "dialogue_open")),
     ("black_screen", ("call", "black_frame")),                                         # the whole picture is black: a scene change / game restarting
     ("loading", ("any", ["loading.title"])),                                           # black screen, white LOADING + percent: nothing to do but wait
     ("results", ("any", RESULTS)),                                                     # DEFEAT / VICTORY window: the AUTO tab is still on screen behind it
     ("battle", ("call", "battle_open")),
     # ---- event screens -------------------------------------------------------------------------------------------------- #
     ("world_boss_lobby", ("any", ["world-boss.lobby-ready", "world-boss.lobby-unready"])),
     ("world_boss_lobby", ("all", ["world-boss.start-boss", "world-boss.invite"])),
     ("world_boss_lobby", ("all", ["world-boss.start-boss", "world-boss.private"])),       # with the party full there is no INVITE button left       # either alone also "matches" in sand and clouds
     ("world_boss_menu", ("any", ["world-boss.world-boss", "world-boss.summon-boss", "world-boss.summon-party", "world-boss.regroup",
                                  "world-boss.ready", "world-boss.unready"])),
     ("quest_zone_map", ("any", ["dungeons.zones"])),
     ("quest_special_popup", ("any", ["dungeons.bonuses"])),                            # a special dungeon's page: bonuses + ENTER (no difficulty picker)
    ("quest_difficulty", ("call", "difficulty_screen")),
     ("team_accept", ("any", TEAM_ACCEPT)),
     ("raid_menu", ("any", ["raid.raid", "raid.summon"])),
     ("raid_menu", ("all", ["raid.normal", "raid.hard", "raid.heroic"])),               # one of the three alone "matches" in sand and clouds
       ("pvp_menu", ("any", ["pvp-arena.play", "pvp-arena.fight"])),
       ("difficulty_popup", ("any", ["trials.difficulty-title"])),                   # the DIFFICULTY list (Trials / Gauntlet / quest tiers)
       ("trials_menu", ("any", ["trials.play"])),
       ("gauntlet_menu", ("any", ["gauntlet.play"])),
       ("expedition_menu", ("any", ["expedition.title"])),                              # before GVG: both menus have the same PLAY button
       ("gvg_menu", ("any", ["gvg.play", "gvg.fight"])),
       ("invasion_menu", ("any", ["invasion.play", "invasion.title"])),
       ("expedition_portal", ("any", ["expedition.portal-googarum", "expedition.portal-twimbo", "expedition.portal-svord",
                                      "expedition.portal-x5", "expedition.enter"])),      # a portal's page (progress, rewards, cost, difficulty, ENTER)
       ("expedition_map", ("call", "expedition_map_open")),                              # the dimension map with its portals (green bars under each)
       ("expedition_menu", ("any", ["expedition.play"])),
       ("fishing", ("any", ["fishing.cast", "fishing.catch", "fishing.start", "fishing.menu-play", "fishing.fishing"])),
       # ---- finally the town itself ---------------------------------------------------------------------------------------- #
       ("main_map", ("call", "on_main_screen"))]
)

# Screens that only exist once the game has finished loading and that the bot has a handler for: seeing one of them ends the 'loading' wait
# (not team_accept / results: those are also on screen while a battle is being loaded, the chain deals with them itself).
MENU_SCREENS = ("pvp_menu", "trials_menu", "gauntlet_menu", "gvg_menu", "invasion_menu", "expedition_menu", "raid_menu", "world_boss_menu")
# Left out on purpose: screens whose own button starts the loading (ACCEPT, ENTER, FIGHT, difficulty pages): they stay visible for a moment
# after the click, so seeing them must NOT end the wait. A menu that matched through a FIGHT button is skipped by the caller too.

# what to do about a popup that only has to be closed / answered. screen name -> action. Run by Afk.popup_table().
#   do: esc (times, gap) | click_template | x_then_esc (x = the red X) | claim_then_esc | confirm_team ;  every = seconds between tries
POPUP_ACTIONS = {
    "items_popup": dict(do="esc", say="'Items' popup -> ESC"),
    "leave_dungeon_confirm": dict(do="esc", say="'Leave this dungeon?' popup -> ESC (staying)", every=10),
    "familiars_page": dict(do="esc", times=4, gap=1.0, say="Familiars page is open -> ESC x4, 1 s apart", every=10),
    "not_full_team_confirm": dict(do="confirm_team", say="Start with a non-full team? -> confirming", every=3),
    "gvg_guild_confirm": dict(do="click_template", template="resume.yes", say="GVG: 'You cannot leave your guild during this event' (once a week) -> YES", every=3),
    "news": dict(do="x_then_esc", x=(621, 98), say="News popup -> closing it (red X, then ESC)", every=3),
    "daily_rewards": dict(do="claim_then_esc", template="globally.dailyRewards", say="Daily rewards popup -> claiming and closing", every=10, cooldown=230),
    "dialogue": dict(do="dialogue"),                           # NPC / boss chat: click the yellow arrow, every 3rd time SPACE (Afk.npc_dialogue)
    "player_profile": dict(do="esc", say="Another player's profile page is open (a click landed on a player) -> ESC", every=3),
    "chat_window": dict(do="esc", say="Chat window is open -> ESC", every=3),
    "terms_of_service": dict(do="click_template", template="popup.tos-decline", say="Terms of Service page (chat / messaging) -> DECLINE", every=3),
}

NAMES = tuple(dict.fromkeys(name for name, _ in RULES)) + ("unknown",)


def _check(afk, frame, spec, afk_margin):
    """-> list of what matched (empty = this rule does not match)."""
    kind, arg = spec
    if kind in ("any", "all"):
        hits = []
        for t in arg:
            try:
                tpl = afk.tpl.get(t)
            except KeyError:
                continue                                    # a template this install does not have: the rule simply cannot use it
            # a template with a usual spot is only believed near that spot (small white-text templates "match" in snow, sand and clouds
            # when the whole screen is searched); one without a spot is searched everywhere
            if tpl.find(frame) if tpl.at else tpl.find(frame, None):
                hits.append(t)
        return hits if kind == "any" or len(hits) == len(arg) else []
    return [arg] if getattr(afk, "_screen_call")(arg, frame, afk_margin) else []


def classify(afk, frame, afk_margin=80, full=False):
    """The screen the bot is looking at: Screen(name, why, also). `afk` supplies .tpl (the templates) and the checks named in "call" rules."""
    first, also = None, []
    for name, spec in RULES:
        why = _check(afk, frame, spec, afk_margin)
        if not why:
            continue
        if first is None:
            first = Screen(name, why, [])
            if not full:
                return first
        else:
            also.append(name)
    return Screen(first.name, first.why, also) if first else Screen("unknown", [], [])
