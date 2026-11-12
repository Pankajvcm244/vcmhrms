// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

frappe.ui.form.on('Agency Shift', {
    refresh(frm) { update_hint(frm); },
    mode(frm) { update_hint(frm); }
});
function update_hint(frm) {
    frm.set_intro(frm.doc.mode === 'Flexy'
        ? __('Flexy: attendance is based only on First In to Last Out duration, Required Hours, and Half Day Hours. The roster date is the punch-matching day.')
        : __('Fixed: Start and End Time control punctuality. End time at or before start time means the duty ends the following day.'));
}


