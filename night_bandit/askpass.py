"""Git asks for credentials through a pipe; never emit them to application logs."""
import os
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from night_bandit.auth import CredentialVault
try:
    prompt=" ".join(sys.argv[1:])
    if not re.search(r"https://(?:[^/@\s]+@)?github\.com(?=[:/'\s])",prompt):
        raise ValueError("unrecognized credential destination")
    if prompt.lower().startswith("username"):
        print("x-access-token")
    elif prompt.lower().startswith("password"):
        print(CredentialVault(os.environ["NIGHT_BANDIT_VAULT"]).load())
    else: raise ValueError("unknown credential prompt")
except Exception:
    sys.exit(1)
