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
let auto_attendance_running = false;
let sandwich_running = false;

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
        if (!frappe.user.has_role("System Manager")) {
            frm.set_df_property("last_repair_run_at", "read_only", 1);
        }
        update_repair_days_limit(frm);
        // -----------------------------
        // STATE
        // -----------------------------
        let auto_attendance_running = false;
        // -----------------------------
        // AUTO ATTENDANCE BUTTON
        // -----------------------------
        const btn_auto = frm.add_custom_button(__("Run Auto Attendance Now in Background"), () => {
            if (auto_attendance_running) {
                frappe.msgprint(__("Auto Attendance is already running."));
                return;
            }
            frappe.confirm(
                    __("This will run HRMS auto-attendance for all active employee with enabled shifts in background. Continue?"),
                    async () => {
                        auto_attendance_running = true;
                        btn_auto.prop("disabled", true);
                        frappe.show_alert({
                            message: __("Starting Auto Attendance..."),
                            indicator: "blue"
                        });
                        // frappe.dom.freeze(__("Running auto-attendance…"));
                        try {
                            const r = await frappe.call({
                                method: "vcmhrms.vcm_hrms.overrides.VCMShiftType.run_auto_attendance_now",
                                freeze: false,
                                args: {
                                // employee: "HP1671"
                                },
                            });
                            if (!r.message) throw new Error("Invalid response");
                            // -----------------------------
                            // ALREADY RUNNING (backend guard)
                            // -----------------------------
                            if (r.message.already_running) {
                                frappe.msgprint({
                                    title: __("Already Running"),
                                    message: __("Auto Attendance is already in progress."),
                                    indicator: "orange"
                                });
                                // ✅ Start polling job status
                                monitor_auto_attendance(frm, btn_auto, () => {
                                    auto_attendance_running = false;
                                });
                                return;
                            }                    
                            // -----------------------------
                            // QUEUED
                            // -----------------------------
                            if (r.message.queued) {
                                frappe.show_alert({
                                    message: __("Auto Attendance started in background"),
                                    indicator: "green"
                                });
                                monitor_auto_attendance(frm, btn_auto, () => {
                                    auto_attendance_running = false;
                                });
                            } else {
                                throw new Error("Queue failed");
                            }

                        } catch (e) {
                            console.error(e);

                            frappe.msgprint({
                                title: __("Error"),
                                message: __("Failed to start auto attendance."),
                                indicator: "red"
                            });

                            auto_attendance_running = false;
                            btn_auto.prop("disabled", false);
                        }
                            
                    } // end of on confirm
                ); // end of confirm
            } // end of button
        ); // end of add_custom_button
        // -----------------------------
        // LATE GRACE QUOTA RECALC BUTTON
        // -----------------------------
        frm.add_custom_button(__('Recalculate Late Grace Quota'), () => {
            frappe.confirm(
                __('Recalculate Late Grace Quota in background?'),
                async () => {
                    frappe.show_alert({
                        message: __('Starting Late Grace recalculation...'),
                        indicator: 'blue'
                    });
                    try {
                        const r = await frappe.call({
                            method: 'vcmhrms.vcm_hrms.overrides.VCMShiftType.run_late_grace_quota_now',
                            freeze: false   // ❗ important: no UI freeze
                        });
                        // -----------------------------
                        // Already Running
                        // -----------------------------
                        if (r.message?.already_running) {
                            frappe.msgprint({
                                title: __('Already Running'),
                                message: __('Late Grace calculation is already in progress.'),
                                indicator: 'orange'
                            });
                            monitor_late_grace(frm);
                            return;
                        }
                        // -----------------------------
                        // Queued
                        // -----------------------------
                        if (!r.message?.queued) {
                            throw new Error("Queue failed");
                        }
                        frappe.show_alert({
                            message: __('Late Grace recalculation started'),
                            indicator: 'green'
                        });
                        // ✅ Start polling
                        monitor_late_grace(frm);
                    } catch (e) {
                        console.error(e);
                        frappe.msgprint({
                            title: __('Error'),
                            message: __('Failed to start Late Grace recalculation'),
                            indicator: 'red'
                        });
                    }
                }
            );
        });
        // -----------------------------
        // SANDWICH LEAVE BUTTON
        // -----------------------------
       const btn_sandwich = frm.add_custom_button(__('Run Sandwich Leave'), () => {
            if (sandwich_running) {
                frappe.msgprint(__("Sandwich Leave process is already running."));
                return;
            }
            frappe.confirm(
                __("Run Sandwich Leave policy in background?"),
                async () => {
                    sandwich_running = true;
                    btn_sandwich.prop("disabled", true);
                    frappe.show_alert({
                        message: __("Starting Sandwich Leave..."),
                        indicator: "blue"
                    });
                    try {
                        const r = await frappe.call({
                            method: "vcmhrms.vcm_hrms.overrides.sandwich.run_sandwich_now",
                            freeze: false   // ❗ remove freeze
                        });
                        // -----------------------------
                        // Already Running
                        // -----------------------------
                        if (r.message?.already_running) {
                            frappe.msgprint({
                                title: __("Already Running"),
                                message: __("Sandwich process is already running."),
                                indicator: "orange"
                            });
                            monitor_sandwich(frm, btn_sandwich, () => {
                                sandwich_running = false;
                            });
                            return;
                        }
                        // -----------------------------
                        // Queued
                        // -----------------------------
                        if (!r.message?.queued) {
                            throw new Error("Queue failed");
                        }
                        frappe.show_alert({
                            message: __("Sandwich Leave started"),
                            indicator: "green"
                        });
                        // ✅ Start polling
                        monitor_sandwich(frm, btn_sandwich, () => {
                            sandwich_running = false;
                        });
                    } catch (e) {
                        console.error(e);
                        frappe.msgprint({
                            title: __("Error"),
                            message: __("Failed to start Sandwich Leave."),
                            indicator: "red"
                        });
                        sandwich_running = false;
                        btn_sandwich.prop("disabled", false);
                    }
                }
            );
        });
        frm.add_custom_button(__("Delete All Attendance"), function () {
            frappe.confirm(
                __(
                    "This will delete ALL Attendance records and unlink Employee Checkins.<br><br>" +
                    "<b>Use only in testing.</b><br>" +
                    "This action cannot be undone.<br><br>" +
                    "Do you want to continue?"
                ),
                function () {
                    frappe.call({
                        method: "vcmhrms.vcm_hrms.doctype.vcm_hr_settings.vcm_hr_settings.enqueue_delete_all_attendance",
                        freeze: true,
                        freeze_message: __("Starting attendance cleanup..."),
                        callback: function (r) {
                            if (!r.exc) {
                                frappe.show_alert({
                                    message: __("Attendance deletion started in background."),
                                    indicator: "orange"
                                });

                                poll_delete_all_attendance_status(frm);
                            }
                        }
                    });
                }
            );
        }).addClass("btn-danger");

        poll_delete_all_attendance_status(frm);
    }, // end of refresh
    attendance_locked_upto(frm) {
        update_repair_days_limit(frm);
    },
    repair_window_days(frm) {
        enforce_repair_days_limit(frm);
    }
}); // end of frappe.ui.form.on

let AUTO_ATTENDANCE_INTERVAL = null;
async function monitor_auto_attendance(frm, btn_auto, on_finish) {
    // ❗ prevent duplicate intervals
    if (AUTO_ATTENDANCE_INTERVAL) {
        clearInterval(AUTO_ATTENDANCE_INTERVAL);
    }
    let attempts = 0;
    const max_attempts = 240; // ~20 minutes (5 sec interval)
    const interval = setInterval(async () => {
        try {
            const r = await frappe.call({
                method: "vcmhrms.vcm_hrms.overrides.VCMShiftType.get_auto_attendance_status"
            });
            const status = r.message || {};
            // ------------------------
            // RUNNING STATE
            // ------------------------
            if (status.status === "running") {
                const shift = status.current_shift || "-";
                const processed = status.processed || 0;
                const total = status.total || "?";
                frappe.show_alert({
                    message: __(
                        "Processing {0}/{1} • Shift: {2}",
                        [processed, total, shift]
                    ),
                    indicator: "orange"
                });
            }
            // ------------------------
            // COMPLETED
            // ------------------------
            if (status.status === "completed") {
                clearInterval(interval);
                frappe.msgprint({
                    title: __("Auto Attendance Completed"),
                    message: __(
                        "Processed: {0} shifts",
                        [status.processed || 0]
                    ),
                    indicator: "green"
                });
                frm.reload_doc();
                btn_auto.prop("disabled", false);
                on_finish && on_finish();
            }
            // ------------------------
            // FAILED
            // ------------------------
            if (status.status === "failed") {
                clearInterval(interval);
                frappe.msgprint({
                    title: __("Auto Attendance Failed"),
                    message: status.error || __("Unknown error"),
                    indicator: "red"
                });
                btn_auto.prop("disabled", false);
                on_finish && on_finish();
            }
        } catch (err) {
            console.error("Polling error", err);
        }
        // ------------------------
        // TIMEOUT PROTECTION
        // ------------------------
        attempts++;
        if (attempts > max_attempts) {
            clearInterval(interval);

            frappe.msgprint({
                title: __("Timeout"),
                message: __("Auto Attendance is taking longer than expected."),
                indicator: "orange"
            });

            btn_auto.prop("disabled", false);
            on_finish && on_finish();
        }
    }, 5000); // poll every 5 sec
}


let LATE_GRACE_INTERVAL = null;
function monitor_late_grace(frm) {
    // ❗ prevent duplicate polling
    if (LATE_GRACE_INTERVAL) {
        clearInterval(LATE_GRACE_INTERVAL);
    }
    let attempts = 0;
    const max_attempts = 120; // ~10 min
    LATE_GRACE_INTERVAL = setInterval(async () => {
        try {
            const r = await frappe.call({
                method: "vcmhrms.vcm_hrms.overrides.VCMShiftType.get_late_grace_status"
            });
            const status = r.message || {};
            // ------------------------
            // RUNNING
            // ------------------------
            if (status.status === "running") {
                frappe.show_alert({
                    message: __(
                        "Processing {0}/{1} • Employee: {2}",
                        [
                            status.processed || 0,
                            status.total || "?",
                            status.current_employee || "-"
                        ]
                    ),
                    indicator: "orange"
                });
            }
            // ------------------------
            // COMPLETED
            // ------------------------
            if (status.status === "completed") {
                clearInterval(LATE_GRACE_INTERVAL);
                frappe.msgprint({
                    title: __("Completed"),
                    message: __(
                        "Late Grace recalculation completed.<br>Processed: {0}<br>Updated: {1}",
                        [
                            status.processed || 0,
                            status.updated || 0
                        ]
                    ),
                    indicator: "green"
                });

                frm.reload_doc();
            }
            // ------------------------
            // FAILED
            // ------------------------
            if (status.status === "failed") {
                clearInterval(LATE_GRACE_INTERVAL);
                frappe.msgprint({
                    title: __("Failed"),
                    message: __(
                        "Error:<br>{0}",
                        [status.error || __("Unknown error")]
                    ),
                    indicator: "red"
                });
            }
        } catch (err) {
            console.error("Late Grace Polling error", err);
        }
        // ------------------------
        // TIMEOUT
        // ------------------------
        attempts++;
        if (attempts > max_attempts) {
            clearInterval(LATE_GRACE_INTERVAL);
            frappe.msgprint({
                title: __("Timeout"),
                message: __("Late Grace recalculation is taking too long."),
                indicator: "orange"
            });
        }
    }, 5000);
}

//  Sandwich Monitoring
let SANDWICH_INTERVAL = null;
function monitor_sandwich(frm, btn_sandwich, on_finish) {
    if (SANDWICH_INTERVAL) {
        clearInterval(SANDWICH_INTERVAL);
    }
    let attempts = 0;
    const max_attempts = 120;
    SANDWICH_INTERVAL = setInterval(async () => {
        try {
            const r = await frappe.call({
                method: "vcmhrms.vcm_hrms.overrides.sandwich.get_sandwich_status"
            });
            const status = r.message || {};
            // ------------------------
            // RUNNING
            // ------------------------
            if (status.status === "running") {
                frappe.show_alert({
                    message: __(
                        "Processing {0}/{1} • Employee: {2}",
                        [
                            status.processed || 0,
                            status.total || "?",
                            status.current_employee || "-"
                        ]
                    ),
                    indicator: "orange"
                });
            }
            // ------------------------
            // COMPLETED
            // ------------------------
            if (status.status === "completed") {
                clearInterval(SANDWICH_INTERVAL);
                frappe.msgprint({
                    title: __("Sandwich Leave Completed"),
                    message: __(
                        "Processed: {0} | Created: {1} | Cleared: {2}",
                        [
                            status.processed || 0,
                            status.created || 0,
                            status.cleared || 0
                        ]
                    ),
                    indicator: "green"
                });
                frm.reload_doc();
                btn_sandwich.prop("disabled", false);
                on_finish && on_finish();
            }
            // ------------------------
            // FAILED
            // ------------------------
            if (status.status === "failed") {
                clearInterval(SANDWICH_INTERVAL);
                frappe.msgprint({
                    title: __("Sandwich Leave Failed"),
                    message: status.error || __("Unknown error"),
                    indicator: "red"
                });
                btn_sandwich.prop("disabled", false);
                on_finish && on_finish();
            }
        } catch (err) {
            console.error("Sandwich polling error", err);
        }
        attempts++;
        if (attempts > max_attempts) {
            clearInterval(SANDWICH_INTERVAL);
            frappe.msgprint({
                title: __("Timeout"),
                message: __("Sandwich process is taking too long."),
                indicator: "orange"
            });
            btn_sandwich.prop("disabled", false);
            on_finish && on_finish();
        }
    }, 5000);
}

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

function poll_delete_all_attendance_status(frm) {
    if (frm.__delete_all_attendance_polling) {
        return;
    }

    frm.__delete_all_attendance_polling = true;

    const poll = function () {
        frappe.call({
            method: "vcmhrms.vcm_hrms.doctype.vcm_hr_settings.vcm_hr_settings.get_delete_all_attendance_status",
            callback: function (r) {
                const status = r.message || {};

                if (status.status) {
                    let indicator = "blue";

                    if (status.status === "Completed") {
                        indicator = "green";
                    } else if (status.status === "Failed") {
                        indicator = "red";
                    } else if (status.status === "Running") {
                        indicator = "orange";
                    }

                    frm.dashboard.clear_headline();

                    frm.dashboard.set_headline_alert(
                        `<b>Delete All Attendance:</b> ${frappe.utils.escape_html(status.message || status.status)}`,
                        indicator
                    );
                }

                if (status.status === "Queued" || status.status === "Running") {
                    setTimeout(poll, 3000);
                } else {
                    frm.__delete_all_attendance_polling = false;
                }
            },
            error: function () {
                frm.__delete_all_attendance_polling = false;
            }
        });
    };

    poll();
}