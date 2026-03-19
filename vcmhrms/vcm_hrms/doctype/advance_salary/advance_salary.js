// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

frappe.ui.form.on('Advance Salary', {

    company: function(frm) {
        frm.set_value("employee", null); // reset employee
    },

    refresh: function(frm) {
        frm.set_query("employee", function() {
            return {
                filters: {
                    company: frm.doc.company,
                    status: "Active"
                }
            };
        });
    }

});