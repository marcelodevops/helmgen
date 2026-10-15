"""Sensitive environment-variable detection."""
import re

from ..presets import SENSITIVE_EXCLUSIONS, SENSITIVE_KEYS

def _tokens(key):
    return [t for t in re.split(r"[^A-Z0-9]+", key) if t]

def _remove_seq(tokens, seq):
    n, out = len(seq), []
    i = 0
    while i < len(tokens):
        if tokens[i:i + n] == seq:
            i += n
            continue
        out.append(tokens[i])
        i += 1
    return out

def is_sensitive_key(name, extra=()):
    """Heuristic secret detection on an env var name.

    Matches per token, exactly or as a suffix (so PGPASSWORD and DB_PASS are
    caught, but KEY inside KEYCLOAK is not), and drops known non-secret
    phrases such as PUBLIC_KEY or KEYSTORE before deciding.
    """
    key = str(name).upper()
    if extra and key in {str(e).strip().upper() for e in extra if str(e).strip()}:
        return True

    tokens = _tokens(key)
    for exclusion in SENSITIVE_EXCLUSIONS:
        tokens = _remove_seq(tokens, _tokens(exclusion))
    return any(tok == s or tok.endswith(s) for tok in tokens for s in SENSITIVE_KEYS)

def detect_sensitive_env(env_dict, extra_sensitive=()):
    sensitive = {}
    normal = {}
    for k, v in env_dict.items():
        if is_sensitive_key(k, extra_sensitive):
            sensitive[k] = "<secret-from-values>"
        else:
            normal[k] = v
    return normal, sensitive

