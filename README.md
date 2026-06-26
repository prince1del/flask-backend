# Centralized DB System

A lightweight Python and SQLite project for managing centralized records through a small CLI.

## Features

- Create and manage a local SQLite-backed store
- Add, list, update, delete, and count records
- Simple command-line workflow for day-to-day use
- CRUD test coverage with pytest

## Quick start

```bash
python -m centralized_db_system.cli init
python -m centralized_db_system.cli add "Ada Lovelace" "ada@example.com" "Research"
python -m centralized_db_system.cli list
python -m centralized_db_system.cli count
```

## Cloud-backed storage

You can point the app at a cloud-friendly SQLite location by setting an environment variable before running commands:

```bash
set CLOUD_DATABASE_URL=sqlite:///C:/path/to/shared/centralized_db.sqlite3
```

On Linux or macOS, use:

```bash
export CLOUD_DATABASE_URL="sqlite:////path/to/shared/centralized_db.sqlite3"
```

## Offline-first sync

The app now queues local write operations and replays them later when you run the sync command. This is useful for offline editing followed by a reconnect:

```bash
python -m centralized_db_system.cli add "Offline User" "offline@example.com" "Ops"
python -m centralized_db_system.cli sync
```

## Firebase setup

To use Firebase, follow these steps:

1. Create a Firebase project in the Firebase console.
2. Enable the Realtime Database.
3. Download the service account JSON file for your project.
4. Set the environment variables before running the app:

```bash
set FIREBASE_PROJECT_ID=your-project-id
set FIREBASE_DATABASE_URL=https://your-project-id.firebaseio.com
set FIREBASE_CREDENTIALS_JSON=C:/path/to/serviceAccount.json
```

Then verify the setup:

```bash
python -m centralized_db_system.cli firebase-check
```

And run the sync command when you want to push queued changes:

```bash
python -m centralized_db_system.cli firebase-sync
```

The Firebase sync layer will push records to the cloud when the credentials are available and will fall back to the offline queue if not.

## Testing

```bash
pytest
```

## Project structure

- centralized_db_system/db.py: database layer
- centralized_db_system/cli.py: command-line interface
- tests/test_db.py: CRUD regression tests
