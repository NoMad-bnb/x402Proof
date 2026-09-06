"""One-shot CLI to generate a fresh GenLayer account and print the address and private key."""

from genlayer_py import generate_private_key, create_account

if __name__ == "__main__":
    private_key = generate_private_key()
    account = create_account(private_key)

    key_hex = private_key.hex() if hasattr(private_key, "hex") else str(private_key)
    if not key_hex.startswith("0x"):
        key_hex = "0x" + key_hex

    print("Generated a new GenLayer account.\n")
    print("Address (safe to share):")
    print("  " + account.address)
    print()
    print("Private key (KEEP SECRET, do not paste anywhere but your own")
    print("environment variable):")
    print("  " + key_hex)
    print()
    print("Next step: set it as an environment variable, then fund this")
    print("address with a small amount of GEN via the Studio, using your")
    print("existing funded account (0x741BE24C0387A56501ab39a79DAc8FB02B244337).")
