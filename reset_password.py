import sqlite3
from werkzeug.security import generate_password_hash

new_password = 'Nexora@123'
new_hash = generate_password_hash(new_password)

conn = sqlite3.connect('centralized_db.sqlite3')
c = conn.cursor()
c.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'", (new_hash,))
conn.commit()
print("Password updated. Rows affected:", c.rowcount)
conn.close()
