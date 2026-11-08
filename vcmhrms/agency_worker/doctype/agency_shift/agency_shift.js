// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

frappe.ui.form.on('Agency Shift', {
    refresh(frm) { update_hint(frm); },
    mode(frm) { update_hint(frm); }
});
function update_hint(frm) {
    frm.set_intro(frm.doc.mode === 'Flexy'
        ? __('Flexy: attendance is based on worked duration. Start and End Time anchor punches to the correct duty; Late Entry and Early Exit grace are not applicable.')
        : __('Fixed: Start and End Time control punctuality. End time at or before start time means the duty ends the following day.'));
}


