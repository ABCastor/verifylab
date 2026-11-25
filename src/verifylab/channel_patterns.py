"""Source indicators used by the five-channel field reading."""

ESCAPES = [  # (name, regex) on masked text
    ("sorry", r"\bsorry\b"), ("axiom", r"^\s*(?:private\s+|protected\s+)?axiom\b"),
    ("native_decide", r"\bnative_decide\b"), ("decide +kernel", r"decide\s*\+kernel"),
    ("implemented_by", r"\bimplemented_by\b"), ("extern", r"@\[\s*extern\b"), ("unsafe", r"\bunsafe\b"),
    ("opaque", r"^\s*(?:private\s+|protected\s+)?opaque\b"), ("skipKernelTC", r"debug\.skipKernelTC"),
    ("#exit", r"^\s*#exit\b"), ("prelude", r"^\s*prelude\b"), ("allowUnsafeReducibility", r"allowUnsafeReducibility"),
    ("maxHeartbeats 0", r"maxHeartbeats\s+0\b"), ("set_option debug.*", r"set_option\s+debug\."),
]
BUILD = [
    ("import Lean", r"^\s*(?:public\s+)?import\s+Lean(?:\.|\s|$)"), ("elab", r"(?<![\w.])elab(?:_rules)?\b"),
    ("macro", r"(?<![\w.])macro(?:_rules)?\b"), ("syntax", r"^\s*(?:scoped\s+|local\s+)?syntax\b"),
    ("run_cmd/run_elab/run_meta", r"\brun_(?:cmd|elab|meta)\b"), ("initialize", r"^\s*(?:builtin_)?initialize\b"),
    ("addDecl", r"\baddDecl\b"), ("#eval", r"^\s*#eval\b"), ("@[command_elab/tactic/term_elab]",
                                                                  r"@\[\s*(?:command_elab|tactic|term_elab)\b"),
    ("notation", r"^\s*(?:scoped\s+|local\s+)?(?:notation|infixl?|infixr|prefix|postfix)\b"),
]
