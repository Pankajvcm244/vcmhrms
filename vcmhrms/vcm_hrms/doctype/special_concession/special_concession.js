// // Copyright (c) 2026, Aman Soni and contributors
// // For license information, please see license.txt

frappe.ui.form.on('Special Concession', {

    employee: function(frm) {
        fetch_employee_details(frm);
    },

    date: function(frm) {
        fetch_checkin(frm);
        fetch_attendance_status(frm);
    }
});


// 🔹 FETCH APPROVER + SHIFT
function fetch_employee_details(frm) {

    if (!frm.doc.employee) return;

    // Fetch latest Shift Assignment
    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "Shift Assignment",
            filters: {
                employee: frm.doc.employee,
                docstatus: 1
            },
            fields: ["shift_type"],
            order_by: "start_date desc",
            limit_page_length: 1
        },
        callback: function(r) {

            if (r.message && r.message.length > 0) {
                frm.set_value("employee_shift", r.message[0].shift_type);
            } else {
                frm.set_value("employee_shift", "");
            }
        }
    });
}


// 🔹 FETCH CHECKIN → FORCE 24 HOUR FORMAT
function fetch_checkin(frm) {

    if (!frm.doc.employee || !frm.doc.date) return;

    let from_datetime = frm.doc.date + " 00:00:00";
    let to_datetime   = frm.doc.date + " 23:59:59";

    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "Employee Checkin",
            filters: [
                ["employee", "=", frm.doc.employee],
                ["time", ">=", from_datetime],
                ["time", "<=", to_datetime]
            ],
            fields: ["time", "log_type"],
            order_by: "time asc",
            limit_page_length: 100
        },
        callback: function(r) {

            if (r.message && r.message.length > 0) {

                let checkins = r.message;

                let in_logs = checkins.filter(d => d.log_type === "IN");
                let out_logs = checkins.filter(d => d.log_type === "OUT");

                let first_in = in_logs.length ? in_logs[0].time : checkins[0].time;
                let last_out = out_logs.length ? out_logs[out_logs.length - 1].time : checkins[checkins.length - 1].time;

                //  Convert to strict HH:MM:SS
                let in_time  = first_in.split(" ")[1];
                let out_time = last_out.split(" ")[1];

                frm.set_value("original_in_time", in_time);
                frm.set_value("original_out_time", out_time);

            } else {

                frm.set_value("original_in_time", "");
                frm.set_value("original_out_time", "");
            }
        }
    });
}


// 🔹 FETCH ATTENDANCE STATUS
function fetch_attendance_status(frm) {

    if (!frm.doc.employee || !frm.doc.date) return;

    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "Attendance",
            filters: {
                employee: frm.doc.employee,
                attendance_date: frm.doc.date,
                docstatus: 1
            },
            fields: ["status"],
            limit_page_length: 1
        },
        callback: function(r) {

            if (r.message && r.message.length > 0) {
                frm.set_value("attendance_status", r.message[0].status);
            } else {
                frm.set_value("attendance_status", "");
            }
        }
    });
}