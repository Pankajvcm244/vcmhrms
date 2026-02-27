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
                        message: __(
                        "Shifts processed: {0}<br>Errors: {1}<br><br>Details:<br><pre>{2}</pre>",
                        [
                            out.processed || 0,
                            out.errors || 0,
                            frappe.utils.escape_html(JSON.stringify(out.details || {}, null, 2)),
                        ]
                        ),
                        indicator: (out.errors || 0) > 0 ? "orange" : "green",
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