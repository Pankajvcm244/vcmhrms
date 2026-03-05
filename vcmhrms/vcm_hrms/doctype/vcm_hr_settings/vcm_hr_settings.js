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
        frm.clear_custom_buttons();
        // Show only for System Manager / HR Manager type roles (adjust as needed)
        const allowed =
            frappe.user.has_role("System Manager") ||
            frappe.user.has_role("HR Manager");
        if (!allowed) return;
        // Only show buttons in Draft
        if (frm.doc.docstatus !== 0) return;

        if (!frappe.user.has_role("System Manager")) {
            frm.set_df_property("attendance_locked_upto", "read_only", 1);
        }
        update_repair_days_limit(frm);

        frm.add_custom_button(__("Run Auto Attendance Now"), () => {
            frappe.confirm(
                __("This will run HRMS auto-attendance for all enabled shifts. Continue?"),
                async () => {
                    frappe.dom.freeze(__("Running auto-attendance…"));
                    try {
                        const r = await frappe.call({
                            method: "vcmhrms.vcm_hrms.overrides.VCMShiftType.run_auto_attendance_now",
                            args: {
                            // employee: "HP1671"
                            },
                        });
                        console.log("Auto Attendance Response:", r);
                        if (!r.message || !r.message.ok) {
                            frappe.msgprint({
                                title: __("Auto Attendance Failed"),
                                message: __("No attendance processed."),
                                indicator: "red"
                            });
                            return;
                        }
                        frappe.msgprint({
                            title: __("Auto Attendance Completed now"),
                            message: __(
                                "Processed: {0} shifts",
                                [r.message.processed || 0]
                            ),
                            indicator: "green"
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

                        if (!r.message || !r.message.ok) {
                            frappe.msgprint({
                                title: __("Sandwich Leave Failed"),
                                message: __("No sandwich leave processed."),
                                indicator: "red"
                            });
                            return;
                        }
                        frappe.msgprint({
                            title: __("Sandwich Leave Completed"),
                            message: __(
                                "Processed: {0} sandwich leaves",
                                [r.message.processed || 0]
                            ),
                            indicator: "green"
                        });
                        frm.reload_doc();
                    } finally {
                        frappe.dom.unfreeze();
                    }
                }
            );
        });
    },
    attendance_locked_upto(frm) {
        update_repair_days_limit(frm);
    },
    repair_window_days(frm) {
        enforce_repair_days_limit(frm);
    }
});

function update_repair_days_limit(frm) {
    // compute max allowed window
    if (!frm.doc.attendance_locked_upto) {
        return;
    }
    const today = frappe.datetime.get_today();
    const lock_date = frm.doc.attendance_locked_upto;
    const lock_plus_one = frappe.datetime.add_days(lock_date, 1);
    const max_days = Math.max(
        0,
        frappe.datetime.get_diff(today, lock_plus_one)
    );
    frm._max_repair_days = max_days;
    frm.set_df_property("repair_window_days","max_value",max_days);
    enforce_repair_days_limit(frm);
}

function enforce_repair_days_limit(frm) {
    // clamp user input
    if (frm._max_repair_days === undefined) {
        return;
    }
    let val = frm.doc.repair_window_days || 0;
    if (val > frm._max_repair_days) {
        frappe.msgprint({
            title: __("Repair Window Limited"),
            message: __(
                "Repair days cannot exceed <b>{0}</b> because attendance is locked upto {1}.",
                [frm._max_repair_days, frm.doc.attendance_locked_upto]
            ),
            indicator: "orange"
        });
        frm.set_value("repair_window_days", frm._max_repair_days);
    }
}