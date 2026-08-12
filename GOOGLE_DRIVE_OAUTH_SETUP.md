# GOOGLE DRIVE OAUTH SETUP & TROUBLESHOOTING

## Desktop App Configuration Guide

**Error:** `Google Drive OAuth is not configured`

**Solution:** Configure OAuth credentials

---

# ERROR EXPLANATION

```
Error Message:
"Google Drive OAuth is not configured. Set the following 
environment variables: GOOGLE_OAUTH_CLIENT_ID, 
GOOGLE_OAUTH_CLIENT_SECRET, or provide a valid 
client_secrets.json file."
```

**What this means:**

- Desktop app is trying to connect to Google Drive
- OAuth credentials are missing
- App doesn't know how to authenticate with Google

**Why it happens:**

- First-time setup
- OAuth credentials not configured
- Environment variables not set
- client_secrets.json file missing

---

# SOLUTION: 3 Methods

## METHOD 1: Google Cloud Console Setup (Recommended)

### Step 1: Create Google Cloud Project

```
1. Go to Google Cloud Console
   https://console.cloud.google.com/

2. Sign in with your Google account

3. Click "Select a Project" (top left)

4. Click "NEW PROJECT"

5. Name: "NEXORA-GDrive"
   Organization: Your organization (or skip)
   
6. Click "CREATE"

7. Wait for project creation (~1 minute)
```

### Step 2: Enable Google Drive API

```
1. In Google Cloud Console
   Search: "Google Drive API"

2. Click "Google Drive API"

3. Click "ENABLE"

4. Wait for API to enable (~30 seconds)
```

### Step 3: Create OAuth Credentials

```
1. In left sidebar, go to "Credentials"
   https://console.cloud.google.com/apis/credentials

2. Click "CREATE CREDENTIALS"

3. Choose "OAuth 2.0 Client IDs"

4. Select "Desktop application"

5. Click "CREATE"

6. A dialog shows:
   - Client ID (long string)
   - Client Secret (long string)
   
7. Copy both values
   (You'll need these in next step)
```

### Step 4: Download credentials.json

```
1. In Credentials page, find your OAuth app

2. Click the download icon (⬇️) next to it

3. File "client_secrets.json" downloads

4. Save to NEXORA folder:
   /nexora/config/client_secrets.json
```

---

## METHOD 2: Environment Variables Setup

### For Windows

```
# Open Command Prompt as Administrator
# Set environment variables:

setx GOOGLE_OAUTH_CLIENT_ID "YOUR_CLIENT_ID_HERE"
setx GOOGLE_OAUTH_CLIENT_SECRET "YOUR_CLIENT_SECRET_HERE"

# Restart NEXORA app
```

### For Mac/Linux

```
# Open Terminal
# Add to ~/.bash_profile or ~/.zshrc

export GOOGLE_OAUTH_CLIENT_ID="YOUR_CLIENT_ID_HERE"
export GOOGLE_OAUTH_CLIENT_SECRET="YOUR_CLIENT_SECRET_HERE"

# Reload shell
source ~/.bash_profile

# Or for zsh:
source ~/.zshrc
```

### For Docker/Production

```
# In Dockerfile or docker-compose.yml

ENV GOOGLE_OAUTH_CLIENT_ID=your_client_id_here
ENV GOOGLE_OAUTH_CLIENT_SECRET=your_client_secret_here

# Or in docker-compose.yml
environment:
  GOOGLE_OAUTH_CLIENT_ID: ${GOOGLE_OAUTH_CLIENT_ID}
  GOOGLE_OAUTH_CLIENT_SECRET: ${GOOGLE_OAUTH_CLIENT_SECRET}
```

---

## METHOD 3: .env File Setup (Easiest)

### Step 1: Create .env file

```
# In NEXORA root directory:
/nexora/.env

Content:
GOOGLE_OAUTH_CLIENT_ID=YOUR_CLIENT_ID_HERE
GOOGLE_OAUTH_CLIENT_SECRET=YOUR_CLIENT_SECRET_HERE
```

### Step 2: Backend reads .env

```
# In backend/config.py or main.py

from dotenv import load_dotenv
import os

load_dotenv()  # Load .env file

GOOGLE_OAUTH_CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')

if not GOOGLE_OAUTH_CLIENT_ID or not GOOGLE_OAUTH_CLIENT_SECRET:
    raise ValueError("Google OAuth credentials not configured!")
```

### Step 3: Restart App

```
1. Stop NEXORA app (if running)
2. Restart NEXORA app
3. Try to open Google Drive again
```

---

# STEP-BY-STEP TUTORIAL

## Complete Setup in 10 Minutes

### 1. Google Cloud Console (3 minutes)

```
Go to: https://console.cloud.google.com/
Sign in → Create Project "NEXORA-GDrive"
Enable Google Drive API
Create Desktop OAuth credentials
Download client_secrets.json
```

### 2. Save Credentials to NEXORA (2 minutes)

```
Save downloaded file:
/nexora/config/client_secrets.json

OR set environment variables:
GOOGLE_OAUTH_CLIENT_ID = "..."
GOOGLE_OAUTH_CLIENT_SECRET = "..."
```

### 3. Restart Desktop App (2 minutes)

```
Quit NEXORA
Restart NEXORA
Go to Settings → Google Drive
Try to connect again
```

### 4. Grant Permission (3 minutes)

```
Click "Connect Google Drive"
Browser opens Google OAuth login
Sign in with your account
Grant NEXORA access
Browser redirects back to app
✅ Connected!
```

---

# TROUBLESHOOTING

## Issue 1: "Invalid Client ID/Secret"

```
Problem: Credentials are wrong or expired

Solution:
  1. Delete old credentials in Google Cloud Console
  2. Create new OAuth credentials
  3. Update GOOGLE_OAUTH_CLIENT_ID and CLIENT_SECRET
  4. Restart app
```

## Issue 2: "Redirect URI mismatch"

```
Problem: OAuth redirect URI not configured

Solution:
  1. In Google Cloud Console
  2. Go to Credentials
  3. Edit the OAuth app
  4. Add Authorized redirect URIs:
     - http://localhost:5000/api/gdrive/callback
     - http://localhost:3000/api/gdrive/callback
     - https://your-domain.com/api/gdrive/callback
  5. Click Save
  6. Restart app
```

## Issue 3: "Permission denied" after clicking Connect

```
Problem: Google account doesn't have Drive access

Solution:
  1. Make sure Google account is active
  2. Check that Google Drive is not disabled
  3. Try with different Google account
  4. Check browser cookies/cache (clear if needed)
  5. Restart app and try again
```

## Issue 4: "Failed to exchange code for credentials"

```
Problem: OAuth code is invalid or expired

Solution:
  1. Restart app
  2. Try connecting again
  3. If still fails, check:
     - Internet connection
     - Firewall blocking Google API
     - Correct credentials in .env or env vars
```

## Issue 5: Desktop app can't reach backend

```
Problem: Desktop → Backend API connection failed

Solution:
  1. Check backend is running
  2. Check URL in desktop app settings
  3. Default: http://localhost:5000
  4. If different, update in Settings
  5. Check firewall
```

---

# CONFIGURATION CHECKLIST

## Before Opening Google Drive in Desktop App

```
☐ Google Cloud Project created
☐ Google Drive API enabled
☐ OAuth credentials created
☐ credentials.json downloaded OR environment variables set
☐ Backend has .env file configured
☐ GOOGLE_OAUTH_CLIENT_ID set
☐ GOOGLE_OAUTH_CLIENT_SECRET set
☐ Redirect URI configured in Google Cloud Console
☐ Backend is running (http://localhost:5000)
☐ Desktop app can reach backend
```

## After Setup

```
☐ Click "Google Drive" in sidebar
☐ Click "Connect Google Drive"
☐ Browser opens Google login
☐ User signs in
☐ User grants permission
☐ Browser redirects back to app
☐ Status shows "Connected"
☐ Folder structure created in Drive
```

---

# CREDENTIALS SECURITY

## Important Security Notes

```
❌ DO NOT:
  • Commit credentials to Git
  • Share credentials in chat/email
  • Hardcode credentials in code
  • Use same credentials for multiple environments

✅ DO:
  • Store in .env file
  • Add .env to .gitignore
  • Use different credentials per environment
  • Rotate credentials regularly
  • Use environment variables in production
```

## .gitignore Configuration

```
# In /nexora/.gitignore

# Google OAuth credentials
.env
.env.local
config/client_secrets.json
config/credentials.json

# Never commit these!
```

---

# ENVIRONMENT SETUP BY DEPLOYMENT

## Development (Local Machine)

```
Method: .env file
Location: /nexora/.env

.env content:
GOOGLE_OAUTH_CLIENT_ID=your_dev_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_dev_client_secret
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:5000/api/gdrive/callback
```

## Staging (Server)

```
Method: Environment variables
Set via:
  • docker-compose.yml
  • systemctl service file
  • Shell profile

Values:
GOOGLE_OAUTH_CLIENT_ID=your_staging_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_staging_client_secret
GOOGLE_OAUTH_REDIRECT_URI=https://staging.nexora.com/api/gdrive/callback
```

## Production (Live)

```
Method: Secrets management
Set via:
  • Kubernetes Secrets
  • AWS Secrets Manager
  • HashiCorp Vault
  • Environment variables (with encryption)

Values:
GOOGLE_OAUTH_CLIENT_ID=your_production_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_production_client_secret
GOOGLE_OAUTH_REDIRECT_URI=https://nexora.your-company.com/api/gdrive/callback
```

---

# VERIFICATION SCRIPT

## Test if OAuth is Configured

```
# Save as test_gdrive_config.py
# Run: python test_gdrive_config.py

import os
from dotenv import load_dotenv

load_dotenv()

GOOGLE_OAUTH_CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')

print("Google Drive OAuth Configuration Check")
print("=" * 50)

if GOOGLE_OAUTH_CLIENT_ID:
    print("✅ GOOGLE_OAUTH_CLIENT_ID: Set")
    print(f"   First 10 chars: {GOOGLE_OAUTH_CLIENT_ID[:10]}...")
else:
    print("❌ GOOGLE_OAUTH_CLIENT_ID: NOT SET")

if GOOGLE_OAUTH_CLIENT_SECRET:
    print("✅ GOOGLE_OAUTH_CLIENT_SECRET: Set")
    print(f"   First 10 chars: {GOOGLE_OAUTH_CLIENT_SECRET[:10]}...")
else:
    print("❌ GOOGLE_OAUTH_CLIENT_SECRET: NOT SET")

# Check if credentials.json exists
if os.path.exists('config/client_secrets.json'):
    print("✅ config/client_secrets.json: Found")
else:
    print("❌ config/client_secrets.json: NOT FOUND")

print("=" * 50)

if GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET:
    print("✅ All credentials configured!")
    print("You can now use Google Drive integration.")
else:
    print("❌ Credentials missing!")
    print("Follow setup guide above.")
```

---

# QUICK FIX CHECKLIST

**If Google Drive still doesn't work:**

```
1. Stop NEXORA app completely
   
2. Check .env file exists:
   /nexora/.env

3. Verify .env contains:
   GOOGLE_OAUTH_CLIENT_ID=<value>
   GOOGLE_OAUTH_CLIENT_SECRET=<value>

4. Restart NEXORA app

5. Check backend logs for errors:
   tail -f /nexora/logs/backend.log

6. Look for "OAuth configured" message

7. Try Google Drive again

If still failing:
   a. Delete old credentials from Google Cloud
   b. Create new credentials
   c. Update .env
   d. Restart app
   e. Try again
```

---

# SUPPORT CONTACT

**If setup still doesn't work:**

1. Check Google Cloud Console credentials are correct
2. Verify redirect URI matches exactly
3. Clear browser cache and cookies
4. Try different browser
5. Check backend logs for error messages
6. Contact support with:

- OS (Windows/Mac/Linux)
- NEXORA version
- Error message (screenshot)
- Backend logs

---

**Status: Complete Setup Guide ✅**

Expected time to fix: 10-15 minutes

Once configured, Google Drive integration works seamlessly!
