# Copyright (c) 2026, Pankaj Sharma and contributors
# For license information, please see license.txt

import frappe


AGENCY_DESK_ROLES = (
    "System Manager",
    "Agency Worker Manager",
    "Agency Roster Manager",
    "Agency Worker Supervisor",
    "ERP Manager",
)

AGENCY_PAGES = (
    "ag-attend-review",
    "ag-monthly-roster",
    "ag-ot-approval",
    "ag-punch-simulator",
)

AGENCY_REPORTS = (
    "Agency Monthly Attendance",
    "Agency Roster vs Attendance",
    "Agency Shift Roster Summary",
)

AGENCY_WORKSPACE = "Agency HR Worker"


def after_migrate():
    """Create required roles/indexes and synchronize Agency Desk access."""
    required_roles = AGENCY_DESK_ROLES + ("Agency Worker Integration",)
    for role in required_roles:
        if not frappe.db.exists("Role", role):
            frappe.get_doc(
                dict(
                    doctype="Role",
                    role_name=role,
                    desk_access=int(role != "Agency Worker Integration"),
                )
            ).insert(ignore_permissions=True)

    frappe.db.add_unique(
        "Agency Roster",
        ["worker", "roster_date"],
        constraint_name="unique_agency_worker_date",
    )
    _sync_agency_page_and_report_roles()
    _backfill_roster_worker_names()
    _sync_agency_workspace()
    _fix_invalid_desk_home_page()


def _replace_allowed_roles(parenttype, parent):
    frappe.db.delete(
        "Has Role",
        {"parent": parent, "parenttype": parenttype, "parentfield": "roles"},
    )
    for index, role in enumerate(AGENCY_DESK_ROLES, start=1):
        frappe.get_doc(
            {
                "doctype": "Has Role",
                "parent": parent,
                "parenttype": parenttype,
                "parentfield": "roles",
                "idx": index,
                "role": role,
            }
        ).db_insert()


def _sync_agency_page_and_report_roles():
    """Keep live Page/Report role rows aligned with the shipped JSON files."""
    for parenttype, names in (("Page", AGENCY_PAGES), ("Report", AGENCY_REPORTS)):
        for name in names:
            if frappe.db.exists(parenttype, name):
                _replace_allowed_roles(parenttype, name)

    frappe.clear_cache()



def _backfill_roster_worker_names():
    """Populate the export-friendly staff name for rosters created before this field."""
    if not frappe.db.has_column("Agency Roster", "worker_name"):
        return
    frappe.db.sql(
        """
        update `tabAgency Roster` roster
        inner join `tabAgency Worker` worker on worker.name = roster.worker
        set roster.worker_name = worker.worker_name
        where coalesce(roster.worker_name, '') != coalesce(worker.worker_name, '')
        """
    )

def _sync_agency_workspace():
    """Expose the shared VCM HRMS workspace through the Agency Worker module."""
    if not frappe.db.exists("Workspace", AGENCY_WORKSPACE):
        return

    frappe.db.set_value(
        "Workspace",
        AGENCY_WORKSPACE,
        {
            "module": "Agency Worker",
            "label": "Agency HR Worker",
            "title": "Agency HR Worker",
            "public": 1,
            "is_hidden": 0,
        },
        update_modified=False,
    )
    _replace_allowed_roles("Workspace", AGENCY_WORKSPACE)

    # Retain the old document for history but remove its conflicting sidebar route.
    if frappe.db.exists("Workspace", "VCM HR"):
        frappe.db.set_value(
            "Workspace", "VCM HR", "is_hidden", 1, update_modified=False
        )

    frappe.clear_cache(doctype="Workspace")


def _fix_invalid_desk_home_page():
    """Prevent `/app/home` from being resolved as the protected Page document."""
    if frappe.db.get_default("desktop:home_page") == "home":
        frappe.db.set_default("desktop:home_page", "workspace")
        frappe.clear_cache()



