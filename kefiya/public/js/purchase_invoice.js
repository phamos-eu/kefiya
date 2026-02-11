frappe.ui.form.on('Purchase Invoice', {
    refresh: function(frm) {
        if (frm.is_new() && frm.fields_dict.originalbeleg_hochladen) {
            frm.set_value('originalbeleg_hochladen', null);
        }
    }
});
