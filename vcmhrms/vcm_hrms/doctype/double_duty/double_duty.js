// Copyright (c) 2026, Aman Soni and contributors
// For license information, please see license.txt


frappe.ui.form.on('Double Duty', {

    setup: function(frm) {
        // Filter Employee based on Company + Active
        frm.set_query("employee", "employee_details", function() {
            return {
                filters: {
                    company: frm.doc.company,
                    status: "Active"
                }
            };
        });
    },

    // Month → Auto Payroll Date (25)
    month: function(frm) {
        if (!frm.doc.month) return;

        const month_map = {
            "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
            "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
            "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"
        };

        let year = frappe.datetime.get_today().split("-")[0];
        let month = month_map[frm.doc.month];

        let date = `${year}-${month}-25`;

        frm.set_value("payroll_date", date);
        frm.set_df_property("payroll_date", "read_only", 1);
    },

    company: function(frm) {
        frm.refresh_field("employee_details");
    },

    payroll_date: function(frm) {
        if (frm.doc.employee_details) {
            frm.doc.employee_details.forEach(row => {
                calculate_amount(frm, row.doctype, row.name);
            });
        }
    }
});


// ================= CHILD TABLE EVENTS =================
frappe.ui.form.on('Double Duty Employee', {

    //  Employee select → fetch salary
    employee: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (!row.employee) return;

        // Step 1: Get latest Salary Structure Assignment
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Salary Structure Assignment",
                filters: {
                    employee: row.employee,
                    docstatus: 1
                },
                fields: ["salary_structure"],
                order_by: "from_date desc",
                limit_page_length: 1
            },
            callback: function(r) {
                if (r.message && r.message.length > 0) {

                    let salary_structure = r.message[0].salary_structure;

                    // Step 2: Get Gross Salary
                    frappe.db.get_value("Salary Structure", salary_structure, "custom_gross_salary")
                        .then(res => {
                            if (res.message) {

                                row.gross_salary = res.message.custom_gross_salary || 0;

                                frm.refresh_field("employee_details");

                                calculate_amount(frm, cdt, cdn);
                            }
                        });
                }
            }
        });
    },

    days: function(frm, cdt, cdn) {
        calculate_amount(frm, cdt, cdn);
    },

    gross_salary: function(frm, cdt, cdn) {
        calculate_amount(frm, cdt, cdn);
    }
});


// ================= MAIN FUNCTION =================
function calculate_amount(frm, cdt, cdn) {
    let row = locals[cdt][cdn];

    if (!row.days || !row.gross_salary || !frm.doc.payroll_date) return;

    let payroll_date = frappe.datetime.str_to_obj(frm.doc.payroll_date);

    let year = payroll_date.getFullYear();
    let month = payroll_date.getMonth();

    // END DATE = 25 current month
    let end_date = new Date(year, month, 25);

    // START DATE = 26 previous month
    let start_date = new Date(year, month - 1, 26);

    let total_days = frappe.datetime.get_day_diff(end_date, start_date) + 1;

    if (total_days <= 0) return;

    let per_day = row.gross_salary / total_days;

    row.amount = per_day * row.days;

    frm.refresh_field("employee_details");
}