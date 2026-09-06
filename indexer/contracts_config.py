"""Deployed GenLayer studionet contract addresses and the chain id this indexer targets."""

X402_AUDITOR_ADDRESS = "0xc40f7bADb1E340C78E20CdEf8722114bBEb53e98"
SUPPORTED_PROBE_ADDRESS = "0xb878840aE798078D8ED3CE371f6dC33eD98e0B8F"
DECLARATION_AUDIT_ADDRESS = "0xeC9B3Bb176B22a31F659AB4581a9F22D3522737A"

# Matches genlayer_py.chains.studionet.id, checked here so a future SDK
# version change that alters the chain id is caught loudly instead of
# silently sending transactions to the wrong network.
EXPECTED_CHAIN_ID = 61999
