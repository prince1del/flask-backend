import os

from cryptography.fernet import Fernet


class CredentialEncryption:
    def __init__(self):
        self.key = os.getenv('CREDENTIAL_ENCRYPTION_KEY')
        if not self.key:
            raise ValueError('CREDENTIAL_ENCRYPTION_KEY not set in .env!')
        self.cipher = Fernet(self.key.encode() if isinstance(self.key, str) else self.key)

    def encrypt(self, token: str) -> str:
        """Encrypt OAuth token before storing in DB."""
        return self.cipher.encrypt(token.encode()).decode()

    def decrypt(self, encrypted_token: str) -> str:
        """Decrypt OAuth token from DB."""
        return self.cipher.decrypt(encrypted_token.encode()).decode()
