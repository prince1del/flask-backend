# NEXORA — naye server pe shift (DigitalOcean Bangalore)

Fresh start: Render ka purana data nahi aayega. Software wahi rahega.

## Step 1 — Tum karo (account + payment)

1. https://cloud.digitalocean.com pe account banao, card add karo.
2. **Create → Droplets**:
   - Region: **Bangalore (BLR1)**
   - Image: **Ubuntu 24.04 LTS**
   - Size: **Basic → Regular → 2 GB / 1 CPU (~$12/mo)**
   - Authentication: **Password** (strong password) ya SSH key
   - Backups: optional (~$2.4/mo) — server pe roz ka backup script already lagta hai
3. Droplet ka **IP address** note karo.
4. (Recommended) Domain lo, aur uska **A record** → droplet IP pe point karo
   (jaise `api.tumhara-domain.in`).

## Step 2 — Server setup (ek command)

Server pe login karke (DigitalOcean ka "Console" button bhi chalega):

```bash
git clone https://github.com/prince1del/flask-backend.git /tmp/nexora-setup
sudo bash /tmp/nexora-setup/deploy/setup_server.sh https://github.com/prince1del/flask-backend.git api.tumhara-domain.in
```

Repo private hai to clone ke liye GitHub token/deploy key chahiye hoga.

## Step 3 — Keys bharo

`/etc/nexora.env` kholo (`sudo nano /etc/nexora.env`) aur yeh bharo:

| Setting | Kahan se milegi |
|---|---|
| `GEMINI_API_KEY`, `GEMINI_FLASH_MODEL` | Render → flask-backend → Environment (👁 button) ya aistudio.google.com |
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` | Render Environment ya Google Cloud Console |
| Redirect URIs | Auto bhar jaate hain; Google Cloud Console mein bhi yahi naye URLs add karne hain |

Phir: `sudo systemctl restart nexora`

## Step 4 — Apps ko naya address

- Android: `android/app/build.gradle.kts` mein URL badlo → naya APK.
- Desktop/web: naya URL.

## Step 5 — Laptop pe backup (data kabhi na khoye)

Server roz raat 2:30 baje backup banata hai. Yeh script use **laptop pe copy** karti hai,
taaki server/company/account kuch bhi band ho, data tumhare paas rahe.

PowerShell mein (ek baar):

```powershell
ssh-keygen -t ed25519 -f $HOME\.ssh\nexora_backup -N '""'
```

`$HOME\.ssh\nexora_backup.pub` ka content server pe `/home/nexora/.ssh/authorized_keys` mein daalo, phir:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\laptop_backup.ps1 -Server <IP-ya-domain> -Register
```

- Roz subah 10 baje chalega; laptop band tha to on hote hi chal jaayega.
- Backups: `Documents\NEXORA-Backups` — roz wali copy 60 din tak, aur **har mahine ki pehli copy hamesha ke liye**.
- Har run ka hisaab: `Documents\NEXORA-Backups\backup-log.txt`.

## Roz ka kaam

| Kaam | Command |
|---|---|
| Logs dekho | `journalctl -u nexora -f` |
| Naya code lagao | `cd /opt/nexora && sudo -u nexora git pull && sudo systemctl restart nexora` |
| Backups | `/var/backups/nexora/` (roz raat 2:30, 14 din tak) |
