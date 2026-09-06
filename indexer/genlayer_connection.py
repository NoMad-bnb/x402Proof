"""Build a signing GenLayer studionet client from GENLAYER_PRIVATE_KEY in the local .env."""

import os

from dotenv import load_dotenv

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet

from contracts_config import EXPECTED_CHAIN_ID

PRIVATE_KEY_ENV_VAR = "GENLAYER_PRIVATE_KEY"

# Loads a .env file that sits in this same folder, and ONLY this folder.
# This is deliberately scoped to the project: it does not touch the user's
# system-wide or account-wide environment variables (no
# [Environment]::SetEnvironmentVariable, no persistent PowerShell profile
# edit), so nothing outside this indexer/ folder is affected. The .env file
# itself is a plain text file, not committed to source control, holding
# only GENLAYER_PRIVATE_KEY=0x....
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_FILE)


def get_client():
    """Build a signing GenLayer client for studionet.

    Raises RuntimeError with a clear message if the key is missing from
    both the .env file and the environment, rather than letting a cryptic
    SDK error surface three layers deep.
    """
    private_key = os.environ.get(PRIVATE_KEY_ENV_VAR)
    if not private_key:
        raise RuntimeError(
            "GENLAYER_PRIVATE_KEY was not found. Create a file named "
            ".env in this same folder (indexer/.env) containing exactly "
            "one line:\n"
            "  GENLAYER_PRIVATE_KEY=0x....\n"
            "This is read automatically every time a script in this "
            "folder runs, no need to set it manually per terminal "
            "session."
        )

    account = create_account(private_key)

    if studionet.id != EXPECTED_CHAIN_ID:
        raise RuntimeError(
            "genlayer_py's studionet chain id changed from what this "
            "indexer expects ("
            + str(EXPECTED_CHAIN_ID)
            + ", now "
            + str(studionet.id)
            + "). Do not proceed without checking whether the deployed "
            "contract addresses still refer to the network you think they "
            "do."
        )

    return create_client(chain=studionet, account=account)
