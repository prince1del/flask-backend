import argparse
import json
import sys
from pathlib import Path

from app.document_analysis import analyze_documents

from .db import CentralizedDB
from .firebase_smoke import run_smoke_test
from .firebase_sync import FirebaseSync
from .sync import apply_pending_changes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the centralized database")
    parser.add_argument(
        "command",
        choices=[
            "init",
            "add",
            "list",
            "get",
            "update",
            "delete",
            "count",
            "sync",
            "firebase-sync",
            "firebase-check",
            "add-distributor",
            "add-retailer",
            "list-distributors",
            "list-retailers",
            "backup",
            "restore",
            "audit-log",
            "cleanup-temp",
            "analyze-documents",
            "build-article-master",
        ],
        help="Action to perform",
    )
    parser.add_argument("args", nargs="*", help="Command arguments")
    parser.add_argument("--db", dest="db_path", help="Optional database path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    db_path = Path(args.db_path or "centralized_db.sqlite3")
    db = CentralizedDB(str(db_path))

    if args.command == "init":
        print(f"Initialized database at {db_path}")
        return 0

    if args.command == "add":
        if len(args.args) < 1:
            print("Usage: add <name> [email] [department]", file=sys.stderr)
            return 2
        name = args.args[0]
        email = args.args[1] if len(args.args) > 1 else None
        department = args.args[2] if len(args.args) > 2 else None
        record_id = db.add_record(name, email, department)
        print(f"Added record {record_id}")
        return 0

    if args.command == "list":
        records = db.list_records()
        if not records:
            print("No records found")
            return 0
        for record in records:
            print(
                f"{record['id']}: {record['name']} | {record['email'] or '-'} | {record['department'] or '-'}"
            )
        return 0

    if args.command == "get":
        if len(args.args) != 1:
            print("Usage: get <id>", file=sys.stderr)
            return 2
        record = db.get_record(int(args.args[0]))
        if record is None:
            print("Record not found")
            return 1
        print(record)
        return 0

    if args.command == "update":
        if len(args.args) < 2:
            print("Usage: update <id> <field=value> [field=value ...]", file=sys.stderr)
            return 2
        record_id = int(args.args[0])
        updates: dict[str, str] = {}
        for item in args.args[1:]:
            if "=" not in item:
                print(f"Invalid update value: {item}", file=sys.stderr)
                return 2
            key, value = item.split("=", 1)
            updates[key] = value
        success = db.update_record(record_id, **updates)
        print("Updated record" if success else "Record not found")
        return 0 if success else 1

    if args.command == "delete":
        if len(args.args) != 1:
            print("Usage: delete <id>", file=sys.stderr)
            return 2
        success = db.delete_record(int(args.args[0]))
        print("Deleted record" if success else "Record not found")
        return 0 if success else 1

    if args.command == "count":
        print(db.count_records())
        return 0

    if args.command == "backup":
        if len(args.args) != 1:
            print("Usage: backup <destination>", file=sys.stderr)
            return 2
        backup_path = db.backup_database(args.args[0])
        print(f"Backup created at {backup_path}")
        return 0

    if args.command == "restore":
        if len(args.args) != 1:
            print("Usage: restore <source>", file=sys.stderr)
            return 2
        restored_path = db.restore_database(args.args[0], overwrite=True)
        print(f"Restored database to {restored_path}")
        return 0

    if args.command == "audit-log":
        limit = 50
        if args.args:
            try:
                limit = int(args.args[0])
            except ValueError:
                print("Usage: audit-log [limit]", file=sys.stderr)
                return 2
        logs = db.list_audit_logs(limit=limit)
        if not logs:
            print("No audit logs found")
            return 0
        for item in logs:
            print(
                f"{item['id']} | {item['created_at']} | {item['action']} | {item['table_name']} | {item['record_id']} | {item['details']}"
            )
        return 0

    if args.command == "cleanup-temp":
        if len(args.args) != 1:
            print("Usage: cleanup-temp <directory>", file=sys.stderr)
            return 2
        removed = db.cleanup_temp_uploads(args.args[0])
        print(f"Removed {removed} stale files")
        return 0

    if args.command == "sync":
        pending = db.sync_store.peek()
        applied = apply_pending_changes(str(db_path), db.sync_store)
        firebase_sync = FirebaseSync()
        for item in pending:
            if item.get("action") == "firebase-add":
                firebase_sync.push_record(item.get("payload", {}))
        print(f"Applied {applied} pending changes")
        return 0

    if args.command == "firebase-sync":
        firebase_sync = FirebaseSync()
        synced = firebase_sync.sync_pending()
        print(f"Synced {synced} pending Firebase changes")
        return 0

    if args.command == "firebase-check":
        print(run_smoke_test())
        return 0

    if args.command == "add-distributor":
        if len(args.args) < 7:
            print(
                "Usage: add-distributor <name> <contact_person> <phone> <email> <address> <city> <state> [gst_number] [credit_limit]",
                file=sys.stderr,
            )
            return 2
        record_id = db.add_distributor(
            args.args[0],
            args.args[1],
            args.args[2],
            args.args[3] if len(args.args) > 3 else None,
            args.args[4] if len(args.args) > 4 else None,
            args.args[5] if len(args.args) > 5 else None,
            args.args[6] if len(args.args) > 6 else None,
            args.args[7] if len(args.args) > 7 else None,
            float(args.args[8]) if len(args.args) > 8 and args.args[8] else None,
        )
        print(f"Added distributor {record_id}")
        return 0

    if args.command == "add-retailer":
        if len(args.args) < 7:
            print(
                "Usage: add-retailer <name> <contact_person> <phone> <email> <address> <city> <state> [gst_number] [credit_limit]",
                file=sys.stderr,
            )
            return 2
        record_id = db.add_retailer(
            args.args[0],
            args.args[1],
            args.args[2],
            args.args[3] if len(args.args) > 3 else None,
            args.args[4] if len(args.args) > 4 else None,
            args.args[5] if len(args.args) > 5 else None,
            args.args[6] if len(args.args) > 6 else None,
            args.args[7] if len(args.args) > 7 else None,
            float(args.args[8]) if len(args.args) > 8 and args.args[8] else None,
        )
        print(f"Added retailer {record_id}")
        return 0

    if args.command == "list-distributors":
        distributors = db.list_distributors()
        if not distributors:
            print("No distributors found")
            return 0
        for distributor in distributors:
            print(
                f"{distributor['id']}: {distributor['name']} | {distributor['contact_person']} | {distributor['phone']}"
            )
        return 0

    if args.command == "list-retailers":
        retailers = db.list_retailers()
        if not retailers:
            print("No retailers found")
            return 0
        for retailer in retailers:
            print(
                f"{retailer['id']}: {retailer['name']} | {retailer['contact_person']} | {retailer['phone']}"
            )
        return 0

    if args.command == "analyze-documents":
        if not args.args:
            print(
                "Usage: analyze-documents <file1> <file2> [file3 ...]", file=sys.stderr
            )
            return 2
        report = analyze_documents(args.args)
        print(json.dumps(report["summary"], indent=2))
        if report["mismatches"]:
            print("\nMismatches:")
            for item in report["mismatches"]:
                print(
                    f"- {item['field']}: {item['message']} | source={item['source']} | target={item['target']}"
                )
        return 0

    if args.command == "build-article-master":
        if len(args.args) != 1:
            print("Usage: build-article-master <order-sheet-path>", file=sys.stderr)
            return 2
        result = db.build_article_master_from_order_sheet(args.args[0])
        print(json.dumps(result, indent=2))
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
