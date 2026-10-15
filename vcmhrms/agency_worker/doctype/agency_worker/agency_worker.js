frappe.ui.form.on('Agency Worker', {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__('Punch Log'), () => {
            frappe.route_options = {worker: frm.doc.name};
            frappe.set_route('List', 'Agency Punch');
        });
        frm.add_custom_button(__('Monthly Roster'), () => frappe.set_route('ag-monthly-roster'));
    }
});
