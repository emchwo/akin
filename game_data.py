"""Static game content: axes, the 8 types, and fallback scenarios.

Nothing in here is written by a model. The Planner and the end screen read from it directly.
"""

APP_NAME = "Akin"

# Each axis has two sides. Scores count +1 for the first side and -1 for the second.
AXES = {
    "one_many": {
        "label": "One vs. Many",
        "sides": ["one", "many"],
        "side_labels": {"one": "One", "many": "Many"},
        "color": "slate",
        "question": "Is there one soulmate out there, or could you build a great life with many different people?",
    },
    "found_built": {
        "label": "Found vs. Built",
        "sides": ["found", "built"],
        "side_labels": {"found": "Found", "built": "Built"},
        "color": "clay",
        "question": "Is a good relationship something you find, or something you build?",
    },
    "home_engine": {
        "label": "Home vs. Engine",
        "sides": ["home", "engine"],
        "side_labels": {"home": "Home", "engine": "Engine"},
        "color": "sage",
        "question": "Does the right person feel like home, or like an engine propelling you somewhere new?",
    },
}
AXIS_ORDER = ["one_many", "found_built", "home_engine"]

# One plain line per side, shown as the 3-bullet summary under each player's type on the end screen.
SIDE_SUMMARY = {
    "one": "There's one right person out there for you.",
    "many": "You could build a great life with more than one person.",
    "found": "A good relationship is discovered. When it's right, you know.",
    "built": "A good relationship is made through effort over time, not found ready-made.",
    "home": "The right person feels calm and settled, not like a push toward somewhere new.",
    "engine": "The right person pushes you somewhere new, not just somewhere safe.",
}

SETTINGS = {
    "first_date": "first date: two people who just met, talking about how each of them sees romantic relationships",
    "new_friends": "friends: two friends (not dating each other) comparing how each of them sees romantic relationships",
}

# Keyed by (one_many side, found_built side, home_engine side).
TYPES = {
    ("one", "found", "home"): {
        "key": "soulmate", "name": "The Soulmate",
        "tagline": "There's one person out there, and when I meet them, I'll be home.",
    },
    ("one", "found", "engine"): {
        "key": "stargazer", "name": "The Stargazer",
        "tagline": "We're written in the stars, and the stars are where we're going.",
    },
    ("one", "built", "home"): {
        "key": "anchor", "name": "The Anchor",
        "tagline": "One person, one home, built to last.",
    },
    ("one", "built", "engine"): {
        "key": "ride_or_die", "name": "The Ride-or-Die",
        "tagline": "You and me, for life, always building toward what's next.",
    },
    ("many", "found", "home"): {
        "key": "hearthseeker", "name": "The Hearthseeker",
        "tagline": "There's more than one right person, and I'll know mine when it feels like home.",
    },
    ("many", "found", "engine"): {
        "key": "wayfarer", "name": "The Wayfarer",
        "tagline": "If the spark is there, let's see where the road goes.",
    },
    ("many", "built", "home"): {
        "key": "craftsman", "name": "The Craftsman",
        "tagline": "There's no single right person, just the home we make together, one day at a time.",
    },
    ("many", "built", "engine"): {
        "key": "co_founder", "name": "The Co-Founder",
        "tagline": "Pick a great partner and build something ambitious together.",
    },
}

# Hand-written, pre-checked scenarios. Used when the Checker rejects the writer 3 times,
# or when the writer fails outright. "a_side" is the axis side option A maps to.
FALLBACK_SCENARIOS = {
    "one_many": [
        {
            "scenario": "A fortune teller at a street fair offers to tell you the exact name of the person you'll end up with. The reading costs five dollars.",
            "option_a": "Pay the five dollars. I want to know the name.",
            "option_b": "Keep walking. There's no single name to find.",
            "a_side": "one",
        },
        {
            "scenario": "You move to a new city for a year. A friend asks whether you think the love of your life could be living there right now.",
            "option_a": "Could be. Lots of people could be right for me.",
            "option_b": "Unlikely. There's one person, and they're wherever they are.",
            "a_side": "many",
        },
        {
            "scenario": "Your grandparents met at 19 and say no one else could ever have worked. Your aunt says she could have been happy with three different people she dated.",
            "option_a": "My grandparents are right.",
            "option_b": "My aunt is right.",
            "a_side": "one",
        },
    ],
    "found_built": [
        {
            "scenario": "A couple you admire tells you their first year together was rocky and they almost broke up twice. Now they're the happiest pair you know.",
            "option_a": "That's encouraging. The rocky part is how they got good.",
            "option_b": "That's surprising. I'd expect the right person to feel easier.",
            "a_side": "built",
        },
        {
            "scenario": "A new app promises a 98% compatibility score with anyone you meet. You get a 61% with someone you really like.",
            "option_a": "Ignore it. We can make 61% work.",
            "option_b": "Take it seriously. Some fits are just there or not.",
            "a_side": "built",
        },
        {
            "scenario": "You're assembling flat-pack furniture with someone new, and the instructions are missing. Halfway through, it's going badly.",
            "option_a": "This is a good test. Watch how we figure it out.",
            "option_b": "It's furniture. Chemistry shows up in other ways.",
            "a_side": "built",
        },
    ],
    "home_engine": [
        {
            "scenario": "It's Friday night after a long week. Your person texts with two plans for tonight.",
            "option_a": "Takeout, a blanket, and the movie we've seen ten times.",
            "option_b": "A last-minute train to a city neither of us knows.",
            "a_side": "home",
        },
        {
            "scenario": "A friend describes their partner as 'the reason I finally applied for the job I was scared of.'",
            "option_a": "That's what I'd want someone to be for me.",
            "option_b": "Sweet, but I'd want someone who makes the scary stuff feel less necessary.",
            "a_side": "engine",
        },
        {
            "scenario": "You're picking a spot to live together for the next five years.",
            "option_a": "A quiet place near family that feels settled.",
            "option_b": "A city where we both have to start over.",
            "a_side": "home",
        },
    ],
}
