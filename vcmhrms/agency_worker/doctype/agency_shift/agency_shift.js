// Copyright (c) 2026, Pankaj Sharma and contributors
// For license information, please see license.txt

frappe.ui.form.on('Agency Shift', {
    refresh(frm) { update_hint(frm); },
    mode(frm) { update_hint(frm); }
});
function update_hint(frm) {
    frm.set_intro(frm.doc.mode === 'Flexy'
        ? __('Flexy uses the roster time window and required hours. No late-entry deduction is applied.')
        : __('End time at or before start time defaults to the following day. Maximum OT capture hours allow actual OT to exceed planned OT; overlapping capture windows are split between adjacent duties.'));
}
