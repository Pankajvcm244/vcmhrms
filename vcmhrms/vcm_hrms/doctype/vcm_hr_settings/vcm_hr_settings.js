// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt
// --------------------------------------------------------------
// HIGH-LEVEL PURPOSE
// --------------------------------------------------------------
// Add a manual "Run Auto Attendance Now" button for testers.
// Testers can:
// 1) create Employee Checkin manually
// 2) click this button
// 3) verify Attendance created/updated as expected
// --------------------------------------------------------------
frappe.ui.form.on("VCM HR Settings", {
	refresh(frm) {
        // Show only for System Manager / HR Manager type roles (adjust as needed)
        if (!frappe.user.has_role("System Manager") && !frappe.user.has_role("HR Manager")) {
            return;
        }

        frm.add_custom_button(__("Run Auto Attendance Now"), () => {
            frappe.confirm(
                __("This will run HRMS auto-attendance for all enabled shifts. Continue?"),
                async () => {
                    frappe.dom.freeze(__("Running auto-attendance…"));
                    try {
                        const r = await frappe.call({
                            method: "vcmhrms.vcm_hrms.overrides.VCMShiftType.run_auto_attendance_now",
                            args: {
                            // optional: employee: "HP1671"
                            },
                        });

                        const out = r.message || {};
                        frappe.msgprint({
                            title: __("Auto Attendance Completed"),
                        });

                        frm.reload_doc();
                    } finally {
                        frappe.dom.unfreeze();
                    }
                }
            );
        });
        frm.add_custom_button(__("Run Sandwich Leave"), () => {
            frappe.confirm(
                __("This will run the Sandwich Leave policy. Continue?"),
                async () => {
                    frappe.dom.freeze(__("Running Sandwich Leave…"));
                    try {
                        const r = await frappe.call({
                            method: "vcmhrms.vcm_hrms.overrides.sandwich.run_sandwich_daily",
                            args: {
                            // optional: employee: "HP1671"
                            },
                        });

                        const out = r.message || {};
                        frappe.msgprint({
                            title: __("Sandwich Leave Completed"),
                        });
                        frm.reload_doc();
                    } finally {
                        frappe.dom.unfreeze();
                    }
                }
            );
        });
    },
});