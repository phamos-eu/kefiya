const KEFIYA_IMPORT_STATUS_COLORS = {
	"Not Started": "gray",
	Queued: "blue",
	"In Progress": "blue",
	Success: "green",
	"Partial Success": "orange",
	Error: "red",
};

frappe.listview_settings["Kefiya Bank Statement Import"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const status = doc.status || "Not Started";
		return [__(status), KEFIYA_IMPORT_STATUS_COLORS[status] || "gray", `status,=,${status}`];
	},
};
