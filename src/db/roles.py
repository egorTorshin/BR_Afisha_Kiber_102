"""Provision narrow PostgreSQL roles after each schema preparation."""

import os

from src.db.connection import Database


ROLE_TABLES = {
    "afisha_api": {
        "SELECT": ("cities", "categories", "venues", "events", "event_sources", "raw_events",
                   "import_reports", "feedback", "audit_log", "admin_sessions", "request_limits",
                   "subscriptions", "agent_dialogue_states", "agent_traces", "agent_sessions",
                   "agent_action_receipts"),
        "INSERT": ("events", "event_sources", "raw_events", "import_reports", "feedback",
                   "audit_log", "admin_sessions", "request_limits", "subscriptions",
                   "agent_dialogue_states", "agent_traces", "agent_sessions",
                   "agent_action_receipts"),
        "UPDATE": ("events", "event_sources", "raw_events", "feedback", "admin_sessions",
                   "request_limits", "subscriptions", "agent_dialogue_states", "agent_sessions"),
        "DELETE": ("event_sources", "agent_dialogue_states", "agent_sessions",
                   "agent_action_receipts"),
    },
    "afisha_bot": {
        "SELECT": ("cities", "categories", "venues", "events", "event_sources", "raw_events",
                   "import_reports", "subscriptions", "audit_log", "feedback", "bot_profiles"),
        "INSERT": ("events", "event_sources", "raw_events", "import_reports", "subscriptions",
                   "audit_log", "bot_profiles"),
        "UPDATE": ("events", "event_sources", "raw_events", "subscriptions", "feedback", "bot_profiles"),
        "DELETE": ("event_sources", "subscriptions", "bot_profiles"),
    },
    "afisha_maintenance": {
        "SELECT": ("events", "raw_events", "subscriptions", "feedback", "audit_log",
                   "import_reports", "admin_sessions", "request_limits", "agent_dialogue_states",
                   "agent_sessions", "agent_action_receipts"),
        "UPDATE": ("events",),
        "DELETE": ("raw_events", "subscriptions", "feedback", "audit_log", "import_reports",
                   "admin_sessions", "request_limits", "agent_dialogue_states", "agent_sessions",
                   "agent_action_receipts"),
    },
}


def provision_roles(db: Database) -> None:
    if not db.is_postgres:
        return
    from psycopg2 import sql

    credentials = {
        "afisha_api": os.getenv("API_DB_PASSWORD", ""),
        "afisha_bot": os.getenv("BOT_DB_PASSWORD", ""),
        "afisha_maintenance": os.getenv("MAINTENANCE_DB_PASSWORD", ""),
    }
    if any(len(password) < 16 for password in credentials.values()):
        raise ValueError("Задайте API_DB_PASSWORD, BOT_DB_PASSWORD и MAINTENANCE_DB_PASSWORD длиной от 16 символов")

    with db.transaction():
        with db.connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                for name, permissions in ROLE_TABLES.items():
                    role = sql.Identifier(name)
                    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
                    if cursor.fetchone() is None:
                        cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(role))
                    cursor.execute(sql.SQL("ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %s").format(role),
                                   (credentials[name],))
                    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
                    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(role))
                    for privileges, tables in permissions.items():
                        table_list = sql.SQL(", ").join(sql.Identifier(table) for table in tables)
                        cursor.execute(sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                            sql.SQL(privileges), table_list, role,
                        ))
