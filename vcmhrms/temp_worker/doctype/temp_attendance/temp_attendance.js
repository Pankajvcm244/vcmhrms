frappe.ui.form.on('Temp Attendance', {
    refresh(frm) {
        if (frappe.user_roles.includes('System Manager') || frappe.user_roles.includes('Temp Worker Manager')) {
            frm.add_custom_button(__('Bulk OT Approval'), () => frappe.set_route('temp-ot-approval'));
            frm.add_custom_button(__('Attendance Review'), () => frappe.set_route('attend-review-page'));
            frm.add_custom_button(__('Monthly Attendance'), () => frappe.set_route('query-report', 'Temp Monthly Attendance'));
        }
        if (!frm.doc.roster) return;
        frm.add_custom_button(__('Open Roster'), () => frappe.set_route('Form', 'Temp Roster', frm.doc.roster));
        frm.add_custom_button(__('Punch Log'), () => {
            frappe.route_options = {worker: frm.doc.worker};
            frappe.set_route('List', 'Temp Punch');
        });
        frm.set_intro(__('IN and OUT are inferred from matched punches. The assigned Supervisor approves OT; System Manager is the backup approver. Approved or rejected values are read-only.'));
        const grid=frm.fields_dict.results.grid;
        grid.grid_rows.forEach(gridRow => {
            const editable=(gridRow.doc.ot_approval_status || 'Pending') === 'Pending';
            gridRow.toggle_editable('approved_before_ot_hours',editable);
            gridRow.toggle_editable('approved_after_ot_hours',editable);
        });
    }
});
